import { createHash } from "node:crypto";
import { Inject, Injectable } from "@nestjs/common";
import type {
  AskEdwardInput,
  AskEdwardResponse,
  ConfirmStudentDocumentExtractionInput,
  EdwardContextReceipt,
  StudentDocument,
  StudentDocumentCategory,
  StudentDocumentExtraction,
  StudentDocumentExtractionFailureCode,
} from "@vv/contracts";
import { extractStudentDocumentImageRegion } from "@vv/document-preprocessing";
import type { AuthContext } from "../auth/auth-context";
import { ApiError, BadRequestError } from "../common/api-error";
import {
  DOCUMENT_STORAGE,
  type DocumentStorage,
} from "../documents/document-storage";
import {
  PLATFORM_STORE,
  type PlatformStore,
} from "../platform/platform-store";
import {
  STUDENT_AI_GATEWAY,
  type StudentAiGateway,
} from "./student-ai.gateway";
import {
  guardedEdwardResponse,
  normalizeEdwardPageContext,
  normalizeEdwardResponse,
} from "./edward-safety";

const maximumDocumentBytes = 10_485_760;
const allowedCategories = new Set<StudentDocumentCategory>([
  "identity",
  "residency",
  "transcript",
  "financial_aid",
  "health",
  "consent",
  "other",
]);
const allowedMimeTypes = new Set<StudentDocument["mimeType"]>([
  "application/pdf",
  "image/jpeg",
  "image/png",
]);

@Injectable()
export class StudentAgentService {
  constructor(
    @Inject(PLATFORM_STORE)
    private readonly store: PlatformStore,
    @Inject(DOCUMENT_STORAGE)
    private readonly storage: DocumentStorage,
    @Inject(STUDENT_AI_GATEWAY)
    private readonly ai: StudentAiGateway,
  ) {}

  async uploadDocument(input: {
    auth: AuthContext;
    fileName: string;
    mimeType: string;
    category?: string;
    requirementId?: string;
    bytes: Buffer;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    const fileName = safeFileName(input.fileName);
    const mimeType = validateMimeType(input.mimeType);
    const category = input.category
      ? validateCategory(input.category)
      : "other";
    validateFileBytes(input.bytes, mimeType);
    const sha256 = createHash("sha256").update(input.bytes).digest("hex");
    const reserved = await this.store.reserveStudentDocumentUpload({
      auth: input.auth,
      document: {
        fileName,
        mimeType,
        sizeBytes: input.bytes.length,
        category,
        sha256,
      },
      ...(input.requirementId
        ? { requirementId: input.requirementId }
        : {}),
      idempotencyKey: input.idempotencyKey,
      requestId: input.requestId,
    });
    const reference = await this.store.getStudentDocumentContentReference({
      auth: input.auth,
      documentId: reserved.id,
    });
    try {
      await this.storage.put({
        key: reference.storageKey,
        body: input.bytes,
        contentType: mimeType,
        sha256,
      });
    } catch {
      throw new ApiError(
        503,
        "DOCUMENT_STORAGE_UNAVAILABLE",
        "Your document record was saved, but the original could not be stored yet. Please retry this upload.",
      );
    }

    // This transaction moves the already-stored original to processing and
    // writes the extraction-request outbox event. The worker—not the HTTP
    // request—owns the expensive parser call and can recover after a restart.
    await this.store.claimStudentDocumentProcessing({
      auth: input.auth,
      documentId: reserved.id,
      requestId: input.requestId,
    });
    return this.store.getStudentDocument({
      auth: input.auth,
      documentId: reserved.id,
    });
  }

  /**
   * Retry parsing from the opaque stored object; the student never needs to
   * upload the same file again. The processing claim serializes the expensive
   * model call. If a client replays its idempotent request after a terminal
   * result, returning that current document is stable and does not re-parse.
   */
  async retryDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    const current = await this.store.getStudentDocument({
      auth: input.auth,
      documentId: input.documentId,
    });
    if (!canRetryDocumentExtraction(current.extraction)) {
      return current;
    }
    const claimed = await this.store.claimStudentDocumentProcessing({
      auth: input.auth,
      documentId: input.documentId,
      retry: true,
      requestId: input.requestId,
      retryIdempotencyKey: input.idempotencyKey,
    });
    if (!claimed) {
      return this.store.getStudentDocument({
        auth: input.auth,
        documentId: input.documentId,
      });
    }
    return this.store.getStudentDocument({
      auth: input.auth,
      documentId: input.documentId,
    });
  }

  /**
   * Invoked only by the outbox worker after the upload transaction has
   * committed. Re-delivery is safe: a terminal document is returned without
   * issuing a second model request.
   */
  async processQueuedDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    requestId: string;
  }): Promise<StudentDocument> {
    const document = await this.store.getStudentDocument({
      auth: input.auth,
      documentId: input.documentId,
    });
    if (
      document.status !== "processing" ||
      document.extraction?.status !== "processing"
    ) {
      return document;
    }
    try {
      const content = await this.getDocumentContent({
        auth: input.auth,
        documentId: input.documentId,
      });
      return this.processDocumentExtraction({
        auth: input.auth,
        documentId: input.documentId,
        fileName: content.fileName,
        mimeType: content.mimeType,
        category: document.category,
        bytes: content.bytes,
        requestId: input.requestId,
      });
    } catch (error) {
      return this.store.completeStudentDocumentExtraction({
        auth: input.auth,
        documentId: input.documentId,
        extraction: failedExtraction(
          document.fileName,
          documentTypeForCategory(document.category),
          error,
        ),
        requestId: input.requestId,
      });
    }
  }

  /**
   * Reconciles a reserved upload when an API process stopped after object
   * storage succeeded but before it could commit the processing outbox event.
   * It deliberately verifies the opaque original first. A missing object is
   * retried by the outbox worker rather than ever sent to the parser.
   */
  async recoverReservedDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    requestId: string;
  }): Promise<StudentDocument> {
    const document = await this.store.getStudentDocument({
      auth: input.auth,
      documentId: input.documentId,
    });
    if (document.status !== "uploaded" || document.extraction) {
      return document;
    }
    await this.getDocumentContent({
      auth: input.auth,
      documentId: input.documentId,
    });
    await this.store.claimStudentDocumentProcessing({
      auth: input.auth,
      documentId: input.documentId,
      requestId: input.requestId,
    });
    return this.store.getStudentDocument({
      auth: input.auth,
      documentId: input.documentId,
    });
  }

  async getDocumentContent(input: {
    auth: AuthContext;
    documentId: string;
  }): Promise<{
    bytes: Buffer;
    fileName: string;
    mimeType: StudentDocument["mimeType"];
  }> {
    const reference = await this.store.getStudentDocumentContentReference(input);
    return {
      bytes: await this.storage.get(reference.storageKey),
      fileName: reference.fileName,
      mimeType: reference.mimeType,
    };
  }

  async getDocumentProfilePhoto(input: {
    auth: AuthContext;
    documentId: string;
  }): Promise<Buffer> {
    const document = await this.store.getStudentDocument(input);
    const region = document.extraction?.visualRegions?.find(
      (candidate) => candidate.kind === "profile_photo",
    );
    if (
      document.category !== "identity" ||
      document.extraction?.status !== "completed" ||
      !region
    ) {
      throw new ApiError(
        404,
        "DOCUMENT_PROFILE_PHOTO_NOT_FOUND",
        "No profile photo was identified in this document",
      );
    }
    const content = await this.getDocumentContent(input);
    return extractStudentDocumentImageRegion({
      bytes: content.bytes,
      mimeType: content.mimeType,
      region,
    });
  }

  confirmDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    confirmation: ConfirmStudentDocumentExtractionInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    return this.store.confirmStudentDocumentExtraction(input);
  }

  private async processDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    fileName: string;
    mimeType: StudentDocument["mimeType"];
    category: StudentDocumentCategory;
    bytes: Buffer;
    requestId: string;
    retryIdempotencyKey?: string;
  }): Promise<StudentDocument> {
    const expectedDocumentType =
      input.category === "other"
        ? undefined
        : documentTypeForCategory(input.category);
    let extraction: StudentDocumentExtraction;
    try {
      extraction = await this.extractDocumentWithSingleRetry({
        tenantId: input.auth.tenantId,
        studentId: input.auth.studentId,
        documentId: input.documentId,
        requestId: input.requestId,
        fileName: input.fileName,
        mimeType: input.mimeType,
        bytes: input.bytes,
        ...(expectedDocumentType ? { expectedDocumentType } : {}),
      });
      if (
        input.category === "financial_aid" &&
        extraction.status === "completed"
      ) {
        extraction = classificationOnlyExtraction(extraction);
      }
      if (
        expectedDocumentType &&
        extraction.status === "completed" &&
        extraction.documentType !== expectedDocumentType
      ) {
        extraction = {
          ...extraction,
          warnings: [
            `This file was uploaded for a ${expectedDocumentType.replaceAll("_", " ")} requirement, but its contents look like ${extraction.documentType.replaceAll("_", " ")}. The requirement was not advanced automatically.`,
            ...extraction.warnings,
          ].slice(0, 12),
        };
      }
    } catch (error) {
      extraction = failedExtraction(
        input.fileName,
        expectedDocumentType,
        error,
      );
    }
    return this.store.completeStudentDocumentExtraction({
      auth: input.auth,
      documentId: input.documentId,
      extraction,
      requestId: input.requestId,
      ...(input.retryIdempotencyKey
        ? { retryIdempotencyKey: input.retryIdempotencyKey }
        : {}),
    });
  }

  /**
   * A transient provider error should not immediately become a student-facing
   * failure. Retry once, immediately, and only for errors classified as a
   * temporary service condition; malformed and unsupported responses never
   * get a blind second model call.
   */
  private async extractDocumentWithSingleRetry(input: {
    tenantId: string;
    studentId: string;
    documentId: string;
    requestId: string;
    fileName: string;
    mimeType: StudentDocument["mimeType"];
    bytes: Buffer;
    expectedDocumentType?: StudentDocumentExtraction["documentType"];
  }): Promise<StudentDocumentExtraction> {
    const extract = (attempt: number) =>
      this.ai.extractStudentDocument({ ...input, attempt });
    try {
      return await extract(1);
    } catch (error) {
      if (!classifyExtractionFailure(error).automaticRetryable) {
        throw error;
      }
      return extract(2);
    }
  }

  async askEdward(input: {
    auth: AuthContext;
    question: AskEdwardInput;
  }): Promise<AskEdwardResponse> {
    const guarded = guardedEdwardResponse(input.question.message);
    if (guarded) return guarded;
    const normalizedQuestion = input.question.message.toLowerCase();
    const [dashboard, profile, documents, onboarding, payments] = await Promise.all([
      this.store.getStudentDashboard(input.auth),
      this.store.getStudentProfile(input.auth),
      this.store.getStudentDocuments(input.auth),
      this.store.getStudentOnboarding(input.auth),
      this.store.getStudentPayments(input.auth),
    ]);
    const studentContext = {
      preferredName: profile.preferredName,
      programName: dashboard.offer.programName,
      termName: dashboard.offer.termName,
      onboardingStatus: onboarding.status,
      enrollmentCompletion: dashboard.journey.completionPercent,
      nextAction: dashboard.journey.nextAction,
      unreadMessages: dashboard.unreadMessageCount,
      documentStatuses: documents.items.map((document) => ({
        category: document.category,
        status: document.status,
      })),
      offerId: dashboard.offer.id,
      depositAmountCents: dashboard.offer.depositAmountCents,
      depositPaid: payments.items.some(
        (payment) =>
          payment.type === "enrollment_deposit" &&
          payment.status === "succeeded",
      ),
    };
    const response = await this.ai.askEdward({
      ...input.question,
      pageContext: normalizeEdwardPageContext(input.question.pageContext),
      studentContext,
    });
    // These receipts are assembled only after all deterministic reads above
    // succeed. They deliberately describe data supplied to Edward, rather
    // than inferring imaginary tool calls from the student's wording.
    return {
      ...normalizeEdwardResponse(response, {
        offerId: studentContext.offerId,
        depositAmountCents: studentContext.depositAmountCents,
        depositPaid: studentContext.depositPaid,
        allowDepositPayment:
          /(?:pay|make|complete).{0,24}deposit|deposit.{0,24}(?:pay|payment)/i.test(
            input.question.message,
          ),
        documentUploadCategory:
          /upload|transcript|fafsa|verification/.test(normalizedQuestion)
            ? normalizedQuestion.includes("transcript")
              ? "transcript"
              : "financial_aid"
            : null,
        appointmentType:
          /appointment|advisor|counselor|human/.test(normalizedQuestion)
            ? /financial|aid|fafsa|loan/.test(normalizedQuestion)
              ? "financial_aid"
              : "enrollment_support"
            : null,
      }),
      contextReceipts: collectedEdwardContextReceipts,
    };
  }
}

const collectedEdwardContextReceipts: EdwardContextReceipt[] = [
  { source: "dashboard" },
  { source: "profile" },
  { source: "documents" },
  { source: "onboarding" },
  { source: "payments" },
];

function safeFileName(fileName: string): string {
  const name = fileName
    .replaceAll("\\", "_")
    .replaceAll("/", "_")
    .replace(/[\u0000-\u001f\u007f"]/g, "")
    .trim()
    .slice(0, 255);
  if (!name) {
    throw new BadRequestError(
      "INVALID_FILE_NAME",
      "The document needs a file name",
    );
  }
  return name;
}

function validateMimeType(value: string): StudentDocument["mimeType"] {
  if (!allowedMimeTypes.has(value as StudentDocument["mimeType"])) {
    throw new ApiError(
      415,
      "UNSUPPORTED_FILE_TYPE",
      "Use a PDF, JPEG, or PNG document",
    );
  }
  return value as StudentDocument["mimeType"];
}

function validateCategory(value: string): StudentDocumentCategory {
  if (!allowedCategories.has(value as StudentDocumentCategory)) {
    throw new BadRequestError(
      "INVALID_DOCUMENT_CATEGORY",
      "Choose a valid document category",
    );
  }
  return value as StudentDocumentCategory;
}

function documentTypeForCategory(
  category: StudentDocumentCategory,
): StudentDocumentExtraction["documentType"] {
  return {
    identity: "identity",
    residency: "residency",
    transcript: "transcript",
    financial_aid: "financial_aid",
    health: "immunization",
    consent: "ferpa",
    other: "other",
  }[category] as StudentDocumentExtraction["documentType"];
}

function classificationOnlyExtraction(
  extraction: StudentDocumentExtraction,
): StudentDocumentExtraction {
  return {
    ...extraction,
    studentName: null,
    institutionName: null,
    issueDate: null,
    academicTerm: null,
    fields: [],
    courses: [],
    visualRegions: [],
    verifiedAt: null,
  };
}

function validateFileBytes(
  bytes: Buffer,
  mimeType: StudentDocument["mimeType"],
): void {
  if (bytes.length < 1 || bytes.length > maximumDocumentBytes) {
    throw new ApiError(
      413,
      "DOCUMENT_TOO_LARGE",
      "Documents must be no larger than 10 MB",
    );
  }
  const valid =
    mimeType === "application/pdf"
      ? bytes.length >= 5 &&
        bytes.subarray(0, 5).toString("ascii") === "%PDF-"
      : mimeType === "image/jpeg"
        ? bytes.length >= 3 &&
          bytes[0] === 0xff &&
          bytes[1] === 0xd8 &&
          bytes[2] === 0xff
        : bytes.length >= 8 &&
          bytes
            .subarray(0, 8)
            .equals(
              Buffer.from([
                0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
              ]),
            );
  if (!valid) {
    throw new ApiError(
      415,
      "FILE_SIGNATURE_MISMATCH",
      "The file contents do not match the selected PDF, JPEG, or PNG type",
    );
  }
}

function canRetryDocumentExtraction(
  extraction: StudentDocumentExtraction | undefined,
): boolean {
  return (
    extraction?.status === "pending_configuration" ||
    (extraction?.status === "failed" && extraction.retryable !== false)
  );
}

interface ExtractionFailureClassification {
  failureCode: StudentDocumentExtractionFailureCode;
  retryable: boolean;
  automaticRetryable: boolean;
  warning: string;
}

/**
 * Provider errors can contain implementation details or even configuration
 * fragments. Inspect them only to choose a deliberately small public code;
 * never persist or return the raw message.
 */
function classifyExtractionFailure(
  error: unknown,
): ExtractionFailureClassification {
  const candidate =
    error && typeof error === "object"
      ? (error as {
          name?: unknown;
          code?: unknown;
          status?: unknown;
          message?: unknown;
        })
      : {};
  const detail = [
    candidate.name,
    candidate.code,
    candidate.status,
    candidate.message,
  ]
    .filter((part): part is string => typeof part === "string")
    .join(" ")
    .toLowerCase();
  if (
    /(?:timeout|timed out|abort|etimedout|deadline)/.test(detail)
  ) {
    return {
      failureCode: "timeout",
      retryable: true,
      automaticRetryable: true,
      warning:
        "Parsing took too long. You can retry without uploading the file again.",
    };
  }
  if (
    /(?:\b429\b|\b5\d\d\b|network|fetch failed|econn|enotfound|temporar(?:y|ily)|unavailable)/.test(
      detail,
    )
  ) {
    return {
      failureCode: "provider_unavailable",
      retryable: true,
      automaticRetryable: true,
      warning:
        "The parsing service is temporarily unavailable. You can retry without uploading the file again.",
    };
  }
  if (/empty (?:completion|structured extraction)|no readable content/.test(detail)) {
    return {
      failureCode: "invalid_response",
      retryable: true,
      automaticRetryable: true,
      warning:
        "The parsing service returned an unusable result. You can retry without uploading the file again.",
    };
  }
  if (
    error instanceof SyntaxError ||
    /(?:invalid json|unexpected token|invalid response)/.test(detail)
  ) {
    return {
      failureCode: "invalid_response",
      retryable: true,
      automaticRetryable: false,
      warning:
        "The parsing service returned an unusable result. You can retry without uploading the file again.",
    };
  }
  if (
    /(?:\b400\b|\b404\b|\b413\b|\b415\b|\b422\b|unsupported|not supported|capability|file-parser)/.test(
      detail,
    )
  ) {
    return {
      failureCode: "unsupported_capability",
      retryable: false,
      automaticRetryable: false,
      warning:
        "The current parsing setup cannot process this file. The original file remains available for staff review.",
    };
  }
  return {
    failureCode: "unknown",
    retryable: true,
    automaticRetryable: false,
    warning:
      "The parsing attempt could not be completed. You can retry without uploading the file again.",
  };
}

function failedExtraction(
  fileName: string,
  expectedDocumentType?: StudentDocumentExtraction["documentType"],
  error?: unknown,
): StudentDocumentExtraction {
  const failure = classifyExtractionFailure(error);
  return {
    status: "failed",
    documentType:
      expectedDocumentType ??
      (fileName.toLowerCase().includes("transcript") ? "transcript" : "other"),
    summary:
      "The original file was stored, but structured extraction could not be completed.",
    studentName: null,
    institutionName: null,
    issueDate: null,
    academicTerm: null,
    fields: [],
    warnings: [failure.warning],
    model: null,
    provider: "local",
    processedAt: new Date().toISOString(),
    verifiedAt: null,
    failureCode: failure.failureCode,
    retryable: failure.retryable,
  };
}
