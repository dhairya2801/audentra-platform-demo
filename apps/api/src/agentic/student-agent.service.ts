import { createHash } from "node:crypto";
import { Inject, Injectable } from "@nestjs/common";
import type {
  AskEdwardInput,
  AskEdwardResponse,
  ConfirmStudentDocumentExtractionInput,
  StudentDocument,
  StudentDocumentCategory,
  StudentDocumentExtraction,
} from "@vv/contracts";
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
    const claimed = await this.store.claimStudentDocumentProcessing({
      auth: input.auth,
      documentId: reserved.id,
    });
    if (!claimed) {
      return this.store.getStudentDocument({
        auth: input.auth,
        documentId: reserved.id,
      });
    }
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
      await this.store.releaseStudentDocumentProcessing({
        auth: input.auth,
        documentId: reserved.id,
        requestId: input.requestId,
      });
      throw new ApiError(
        503,
        "DOCUMENT_STORAGE_UNAVAILABLE",
        "The document could not be stored. Please try the upload again.",
      );
    }
    let extraction: StudentDocumentExtraction;
    try {
      const expectedDocumentType =
        category === "other" ? undefined : documentTypeForCategory(category);
      extraction = await this.ai.extractStudentDocument({
        fileName,
        mimeType,
        bytes: input.bytes,
        ...(expectedDocumentType ? { expectedDocumentType } : {}),
      });
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
    } catch {
      extraction = failedExtraction(fileName);
    }
    return this.store.completeStudentDocumentExtraction({
      auth: input.auth,
      documentId: reserved.id,
      extraction,
      requestId: input.requestId,
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

  confirmDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    confirmation: ConfirmStudentDocumentExtractionInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    return this.store.confirmStudentDocumentExtraction(input);
  }

  async askEdward(input: {
    auth: AuthContext;
    question: AskEdwardInput;
  }): Promise<AskEdwardResponse> {
    const [dashboard, profile, documents, onboarding, payments] = await Promise.all([
      this.store.getStudentDashboard(input.auth),
      this.store.getStudentProfile(input.auth),
      this.store.getStudentDocuments(input.auth),
      this.store.getStudentOnboarding(input.auth),
      this.store.getStudentPayments(input.auth),
    ]);
    return this.ai.askEdward({
      ...input.question,
      studentContext: {
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
      },
    });
  }
}

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

function failedExtraction(fileName: string): StudentDocumentExtraction {
  return {
    status: "failed",
    documentType: fileName.toLowerCase().includes("transcript")
      ? "transcript"
      : "other",
    summary:
      "The original file was stored, but structured extraction could not be completed.",
    studentName: null,
    institutionName: null,
    issueDate: null,
    academicTerm: null,
    fields: [],
    warnings: [
      "The parsing provider could not complete this attempt. The original file is still available.",
    ],
    model: null,
    provider: "local",
    processedAt: new Date().toISOString(),
    verifiedAt: null,
  };
}
