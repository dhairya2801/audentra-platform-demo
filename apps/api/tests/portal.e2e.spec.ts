import type { NestFastifyApplication } from "@nestjs/platform-fastify";
import type {
  AskEdwardResponse,
  OnboardingStep,
  StudentDocumentExtraction,
  StudentOnboarding,
} from "@vv/contracts";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApiApplication } from "../src/create-app";
import {
  DEMO_IDS,
  type AppConfig,
} from "../src/config/app-config";
import type { StudentAiGateway } from "../src/agentic/student-ai.gateway";
import type { DocumentStorage } from "../src/documents/document-storage";
import { InMemoryPlatformStore } from "./support/in-memory-platform.store";

const config: AppConfig = {
  environment: "test",
  port: 4000,
  databaseUrl: "postgresql://unused/unused",
  webOrigins: ["http://localhost:3000"],
  authMode: "demo",
  documentWorkerToken: "test-document-worker-token",
  demoIds: {
    tenantId: DEMO_IDS.tenantId,
    studentId: DEMO_IDS.studentId,
    actorId: DEMO_IDS.personId,
  },
};

describe("functional student portal API", () => {
  let app: NestFastifyApplication;
  const storedObjects = new Map<string, Buffer>();
  let extractionCalls = 0;
  let storageFailuresRemaining = 0;
  const queuedExtractionOutcomes: Array<StudentDocumentExtraction | Error> = [];
  const documentStorage: DocumentStorage = {
    async put(input) {
      if (storageFailuresRemaining > 0) {
        storageFailuresRemaining -= 1;
        throw new Error("Simulated object storage outage");
      }
      storedObjects.set(input.key, Buffer.from(input.body));
    },
    async get(key) {
      const value = storedObjects.get(key);
      if (!value) throw new Error("Missing test object");
      return Buffer.from(value);
    },
  };
  const studentAiGateway: StudentAiGateway = {
    async evaluateCourseExemptions() {
      throw new Error("No tenant exemption context is configured in this test");
    },
    async evaluateImmunizationCompliance() {
      throw new Error(
        "No tenant immunization context is configured in this test",
      );
    },
    async extractStudentDocument(): Promise<StudentDocumentExtraction> {
      extractionCalls += 1;
      const queued = queuedExtractionOutcomes.shift();
      if (queued instanceof Error) throw queued;
      if (queued) return structuredClone(queued);
      return {
        status: "completed",
        documentType: "ferpa",
        summary: "A student records release authorization.",
        studentName: "Alex Morgan",
        institutionName: "Aster University",
        issueDate: null,
        academicTerm: null,
        fields: [
          {
            key: "student_name",
            label: "Student name",
            value: "Alex Morgan",
            confidence: 0.98,
          },
        ],
        warnings: [],
        model: "test/document-model",
        provider: "openrouter",
        processedAt: "2026-07-24T12:00:00.000Z",
        verifiedAt: null,
      };
    },
    async askEdward(): Promise<AskEdwardResponse> {
      return {
        message: "Open Documents to review your upload.",
        provider: "openrouter",
        model: "test/edward",
        usage: {
          promptTokens: 50,
          completionTokens: 8,
          totalTokens: 58,
        },
        suggestedActions: [
          { label: "Open documents", href: "/documents" },
        ],
        contextReceipts: [{ source: "dashboard" }],
        widgets: [],
      };
    },
  };

  beforeAll(async () => {
    app = await createApiApplication({
      config,
      platformStoreOverride: new InMemoryPlatformStore(),
      documentStorageOverride: documentStorage,
      studentAiGatewayOverride: studentAiGateway,
      logger: false,
    });
  });

  const processQueuedDocument = (documentId: string) =>
    app.inject({
      method: "POST",
      url: `/v1/student/internal/document-extractions/${documentId}`,
      headers: { "x-vv-worker-token": "test-document-worker-token" },
    });

  afterAll(async () => {
    await app.close();
  });

  it("bootstraps an authenticated student into server-owned onboarding", async () => {
    const response = await app.inject({
      method: "GET",
      url: "/v1/student/bootstrap",
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      authenticated: true,
      student: { id: DEMO_IDS.studentId, preferredName: "Alex" },
      onboarding: {
        required: true,
        status: "in_progress",
        currentStep: "offer",
        version: 1,
      },
      initialRoute: "/onboarding",
    });
  });

  it("requires offer acceptance before advancing and rejects step skipping", async () => {
    const deferred = await app.inject({
      method: "PUT",
      url: "/v1/student/onboarding",
      payload: {
        expectedVersion: 1,
        currentStep: "offer",
        data: {},
        skip: true,
      },
    });
    expect(deferred.statusCode).toBe(400);
    expect(deferred.json()).toMatchObject({
      error: { code: "ONBOARDING_STEP_REQUIRED" },
    });

    const blocked = await app.inject({
      method: "PUT",
      url: "/v1/student/onboarding",
      payload: {
        expectedVersion: 1,
        currentStep: "offer",
        data: {},
      },
    });
    expect(blocked.statusCode).toBe(409);
    expect(blocked.json()).toMatchObject({
      error: { code: "ACCEPTED_OFFER_REQUIRED" },
    });

    const acceptance = await app.inject({
      method: "POST",
      url: `/v1/admission-offers/${DEMO_IDS.offerId}/accept`,
      headers: { "idempotency-key": "portal.offer.accept.0001" },
    });
    expect(acceptance.statusCode).toBe(200);

    const offerStep = await app.inject({
      method: "PUT",
      url: "/v1/student/onboarding",
      payload: {
        expectedVersion: 1,
        currentStep: "offer",
        data: {},
      },
    });
    expect(offerStep.statusCode).toBe(200);
    expect(offerStep.json()).toMatchObject({
      currentStep: "about_you",
      completedSteps: ["offer"],
      version: 2,
    });

    const skipped = await app.inject({
      method: "PUT",
      url: "/v1/student/onboarding",
      payload: {
        expectedVersion: 2,
        currentStep: "housing",
        data: { housingPreference: "on_campus" },
      },
    });
    expect(skipped.statusCode).toBe(409);
    expect(skipped.json()).toMatchObject({
      error: { code: "ONBOARDING_STEP_OUT_OF_ORDER" },
    });
  });

  it("persists and resumes every ordered onboarding step", async () => {
    const invalidAboutYou = await app.inject({
      method: "PUT",
      url: "/v1/student/onboarding",
      payload: {
        expectedVersion: 2,
        currentStep: "about_you",
        data: { firstName: "Alex" },
      },
    });
    expect(invalidAboutYou.statusCode).toBe(400);
    expect(invalidAboutYou.json()).toMatchObject({
      error: { code: "ONBOARDING_STEP_INVALID" },
    });

    const steps: Array<{
      step: OnboardingStep;
      data: Record<string, unknown>;
    }> = [
      {
        step: "about_you",
        data: {
          firstName: "Alex",
          lastName: "Morgan",
          preferredName: "Alex",
          personalEmail: "alex.morgan@example.com",
          mobilePhone: "+15550102027",
          citizenshipStatus: "us_citizen",
          communicationPreference: "email",
          residencyStatus: "domestic",
          residencyVerificationPath: "home_address_review",
          streetAddress: "18 Willow Street",
          city: "Cambridge",
          stateOrProvince: "MA",
          postalCode: "02139",
          country: "United States",
        },
      },
      {
        step: "housing",
        data: { housingPreference: "undecided" },
      },
      {
        step: "campus_life",
        data: { campusInterests: ["student_clubs"] },
      },
      {
        step: "emergency_contacts",
        data: {
          emergencyContacts: [
            {
              fullName: "Jordan Morgan",
              relationship: "parent",
              mobilePhone: "+15550100300",
            },
          ],
        },
      },
      {
        step: "family_permissions",
        data: { familyPermissions: [] },
      },
      {
        step: "review_and_sign",
        data: {
          signatureFullName: "Alex Morgan",
          signatureMethod: "typed",
          signatureConsent: true,
          signedDocumentIds: [
            "ferpa_release",
            "enrollment_acknowledgment",
          ],
        },
      },
    ];
    let version = 2;
    for (const entry of steps) {
      const response = await app.inject({
        method: "PUT",
        url: "/v1/student/onboarding",
        payload: {
          expectedVersion: version,
          currentStep: entry.step,
          data: entry.data,
        },
      });
      expect(response.statusCode).toBe(200);
      version = (response.json() as StudentOnboarding).version;
    }

    const resumed = await app.inject({
      method: "GET",
      url: "/v1/student/onboarding",
    });
    expect(resumed.statusCode).toBe(200);
    expect(resumed.json()).toMatchObject({
      currentStep: "deposit",
      version: 8,
    });
    expect(resumed.json().completedSteps).toHaveLength(7);
  });

  it("records a server-priced dummy deposit idempotently", async () => {
    const request = {
      method: "POST" as const,
      url: "/v1/student/payments/deposit",
      headers: { "idempotency-key": "portal.deposit.0001" },
      payload: { offerId: DEMO_IDS.offerId },
    };
    const first = await app.inject(request);
    const replay = await app.inject(request);
    const list = await app.inject({
      method: "GET",
      url: "/v1/student/payments",
    });

    expect(first.statusCode).toBe(200);
    expect(replay.json()).toEqual(first.json());
    expect(first.json()).toMatchObject({
      offerId: DEMO_IDS.offerId,
      amountCents: 50_000,
      status: "succeeded",
      processor: "dummy",
    });
    expect(list.json()).toMatchObject({ total: 1 });
  });

  it("completes onboarding once and routes subsequent bootstrap to dashboard", async () => {
    const depositStep = await app.inject({
      method: "PUT",
      url: "/v1/student/onboarding",
      payload: {
        expectedVersion: 8,
        currentStep: "deposit",
        data: { depositChoice: "pay_now" },
      },
    });
    expect(depositStep.statusCode).toBe(200);
    expect(depositStep.json()).toMatchObject({
      completedSteps: expect.arrayContaining(["deposit"]),
      version: 9,
    });

    const request = {
      method: "POST" as const,
      url: "/v1/student/onboarding/complete",
      headers: { "idempotency-key": "portal.onboarding.complete.0001" },
      payload: { expectedVersion: 9 },
    };
    const completed = await app.inject(request);
    const replay = await app.inject(request);
    const bootstrap = await app.inject({
      method: "GET",
      url: "/v1/student/bootstrap",
    });
    const documents = await app.inject({
      method: "GET",
      url: "/v1/student/documents",
    });
    const signedDocuments = documents
      .json()
      .items.filter(
        (document: { signature?: unknown }) => document.signature != null,
      );
    const signedContent = await app.inject({
      method: "GET",
      url: signedDocuments[0].contentUrl,
    });

    expect(completed.statusCode).toBe(200);
    expect(replay.json()).toEqual(completed.json());
    expect(completed.json()).toMatchObject({
      status: "completed",
      version: 10,
    });
    expect(bootstrap.json()).toMatchObject({
      onboarding: { required: false, status: "completed" },
      initialRoute: "/dashboard",
    });
    expect(signedDocuments).toHaveLength(2);
    expect(signedDocuments[0]).toMatchObject({
      mimeType: "application/pdf",
      category: "other",
      processingMode: "generated",
      status: "accepted",
      signature: {
        signerName: "Alex Morgan",
        method: "typed",
        onboardingVersion: 10,
      },
    });
    expect(signedContent.statusCode).toBe(200);
    expect(signedContent.headers["content-type"]).toContain(
      "application/pdf",
    );
    expect(signedContent.rawPayload.subarray(0, 5).toString("ascii")).toBe(
      "%PDF-",
    );
  });

  it("lists tenant-scoped requirements and returns tenant-safe detail errors", async () => {
    const list = await app.inject({
      method: "GET",
      url: "/v1/student/requirements",
    });
    const requirementId = list.json().items[0].id as string;
    const detail = await app.inject({
      method: "GET",
      url: `/v1/student/requirements/${requirementId}`,
    });
    const detailBySlug = await app.inject({
      method: "GET",
      url: "/v1/student/requirements/profile-verification",
    });
    const missing = await app.inject({
      method: "GET",
      url: "/v1/student/requirements/00000000-0000-7000-8000-000000009999",
    });

    expect(list.statusCode).toBe(200);
    expect(list.json()).toMatchObject({ total: 1 });
    expect(detail.statusCode).toBe(200);
    expect(detail.json()).toMatchObject({
      code: "profile_verification",
      slug: "profile-verification",
      submissionType: "form",
      documentCategory: null,
    });
    expect(detailBySlug.statusCode).toBe(200);
    expect(detailBySlug.json()).toEqual(detail.json());
    expect(missing.statusCode).toBe(404);
  });

  it("lists messages and marks a message read naturally idempotently", async () => {
    const list = await app.inject({
      method: "GET",
      url: "/v1/student/messages",
    });
    const messageId = list.json().items[0].id as string;
    const first = await app.inject({
      method: "POST",
      url: `/v1/student/messages/${messageId}/read`,
    });
    const replay = await app.inject({
      method: "POST",
      url: `/v1/student/messages/${messageId}/read`,
    });

    expect(list.json()).toMatchObject({ unreadCount: 2 });
    expect(first.statusCode).toBe(200);
    expect(first.json().readAt).not.toBeNull();
    expect(replay.json()).toEqual(first.json());
  });

  it("validates and creates metadata-only document placeholders idempotently", async () => {
    const request = {
      method: "POST" as const,
      url: "/v1/student/documents",
      headers: { "idempotency-key": "portal.document.0001" },
      payload: {
        fileName: "passport.pdf",
        mimeType: "application/pdf",
        sizeBytes: 4096,
        category: "identity",
      },
    };
    const first = await app.inject(request);
    const replay = await app.inject(request);
    const invalid = await app.inject({
      ...request,
      headers: { "idempotency-key": "portal.document.0002" },
      payload: { ...request.payload, mimeType: "application/octet-stream" },
    });
    const list = await app.inject({
      method: "GET",
      url: "/v1/student/documents",
    });

    expect(first.statusCode).toBe(201);
    expect(first.json()).toMatchObject({ status: "placeholder" });
    expect(replay.json()).toEqual(first.json());
    expect(invalid.statusCode).toBe(400);
    expect(list.json()).toMatchObject({ total: 4 });
  });

  it("uploads, parses, downloads, and confirms a real document idempotently", async () => {
    const boundary = "vv-test-boundary";
    const fileBytes = Buffer.from("%PDF-1.7\nAster FERPA test\n%%EOF\n");
    const payload = Buffer.concat([
      Buffer.from(
        `--${boundary}\r\nContent-Disposition: form-data; name="category"\r\n\r\nconsent\r\n`,
      ),
      Buffer.from(
        `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="release.pdf"\r\nContent-Type: application/pdf\r\n\r\n`,
      ),
      fileBytes,
      Buffer.from(`\r\n--${boundary}--\r\n`),
    ]);
    const request = {
      method: "POST" as const,
      url: "/v1/student/documents/upload",
      headers: {
        "content-type": `multipart/form-data; boundary=${boundary}`,
        "idempotency-key": "portal.document.upload.0001",
      },
      payload,
    };
    const uploaded = await app.inject(request);
    const replay = await app.inject(request);

    expect(uploaded.statusCode).toBe(201);
    expect(replay.statusCode).toBe(201);
    expect(replay.json()).toEqual(uploaded.json());
    expect(uploaded.json()).toMatchObject({
      fileName: "release.pdf",
      category: "consent",
      status: "processing",
      extraction: { status: "processing" },
    });
    expect(uploaded.json().sha256).toMatch(/^[0-9a-f]{64}$/);
    const processed = await processQueuedDocument(uploaded.json().id);
    expect(processed.statusCode).toBe(200);
    expect(processed.json()).toMatchObject({
      status: "needs_review",
      extraction: { status: "completed", documentType: "ferpa" },
    });
    expect(extractionCalls).toBe(1);

    const content = await app.inject({
      method: "GET",
      url: processed.json().contentUrl,
    });
    expect(content.statusCode).toBe(200);
    expect(content.headers["content-type"]).toMatch(/^application\/pdf/);
    expect(content.rawPayload).toEqual(fileBytes);

    const confirmed = await app.inject({
      method: "POST",
      url: `/v1/student/documents/${processed.json().id}/confirm-extraction`,
      headers: { "idempotency-key": "portal.document.confirm.0001" },
      payload: { acceptedFieldKeys: ["student_name"] },
    });
    expect(confirmed.statusCode).toBe(200);
    expect(confirmed.json()).toMatchObject({
      status: "under_review",
      extraction: {
        acceptedFieldKeys: ["student_name"],
      },
    });
  });

  it("releases the processing claim when storage fails so the same upload can retry safely", async () => {
    const boundary = "vv-storage-retry-boundary";
    const payload = Buffer.from(
      [
        `--${boundary}`,
        'Content-Disposition: form-data; name="category"',
        "",
        "transcript",
        `--${boundary}`,
        'Content-Disposition: form-data; name="file"; filename="retry.pdf"',
        "Content-Type: application/pdf",
        "",
        "%PDF-1.7",
        "retryable upload",
        "%%EOF",
        `--${boundary}--`,
        "",
      ].join("\r\n"),
    );
    const request = {
      method: "POST" as const,
      url: "/v1/student/documents/upload",
      headers: {
        "content-type": `multipart/form-data; boundary=${boundary}`,
        "idempotency-key": "portal.document.upload.retry.0001",
      },
      payload,
    };
    const previousExtractionCalls = extractionCalls;
    storageFailuresRemaining = 1;

    const failed = await app.inject(request);
    const retried = await app.inject(request);

    expect(failed.statusCode).toBe(503);
    expect(failed.json()).toMatchObject({
      error: { code: "DOCUMENT_STORAGE_UNAVAILABLE" },
    });
    expect(retried.statusCode).toBe(201);
    expect(retried.json()).toMatchObject({
      fileName: "retry.pdf",
      status: "processing",
      extraction: { status: "processing" },
    });
    const processed = await processQueuedDocument(retried.json().id);
    expect(processed.json()).toMatchObject({
      status: "needs_review",
      extraction: { status: "completed" },
    });
    expect(extractionCalls).toBe(previousExtractionCalls + 1);
  });

  it("retries transient parsing once and supports stored failed or pending retries without re-uploading", async () => {
    const failedBoundary = "vv-extraction-retry-boundary";
    const failedPayload = Buffer.from(
      [
        `--${failedBoundary}`,
        'Content-Disposition: form-data; name="category"',
        "",
        "consent",
        `--${failedBoundary}`,
        'Content-Disposition: form-data; name="file"; filename="retry-safe.pdf"',
        "Content-Type: application/pdf",
        "",
        "%PDF-1.7",
        "stored original used for retry",
        "%%EOF",
        `--${failedBoundary}--`,
        "",
      ].join("\r\n"),
    );
    const callsBeforeAutomaticRetry = extractionCalls;
    queuedExtractionOutcomes.push(
      new Error("OpenRouter returned an empty completion"),
    );
    const automaticallyRetriedUpload = await app.inject({
      method: "POST",
      url: "/v1/student/documents/upload",
      headers: {
        "content-type": `multipart/form-data; boundary=${failedBoundary}`,
        "idempotency-key": "portal.document.automatic-retry.0001",
      },
      payload: failedPayload,
    });
    expect(automaticallyRetriedUpload.statusCode).toBe(201);
    expect(automaticallyRetriedUpload.json()).toMatchObject({
      status: "processing",
      extraction: { status: "processing" },
    });
    const automaticallyRetriedProcessed = await processQueuedDocument(
      automaticallyRetriedUpload.json().id,
    );
    expect(automaticallyRetriedProcessed.json()).toMatchObject({
      status: "needs_review",
      extraction: { status: "completed" },
    });
    expect(extractionCalls).toBe(callsBeforeAutomaticRetry + 2);

    const callsBeforeFailure = extractionCalls;
    queuedExtractionOutcomes.push(
      new Error(
        "OpenRouter returned HTTP 503: bearer sk-this-must-never-reach-a-student",
      ),
    );
    queuedExtractionOutcomes.push(
      new Error(
        "OpenRouter returned HTTP 503: bearer sk-this-must-never-reach-a-student",
      ),
    );
    const failedUpload = await app.inject({
      method: "POST",
      url: "/v1/student/documents/upload",
      headers: {
        "content-type": `multipart/form-data; boundary=${failedBoundary}`,
        "idempotency-key": "portal.document.extraction-failure.0001",
      },
      payload: failedPayload,
    });

    expect(failedUpload.statusCode).toBe(201);
    expect(failedUpload.json()).toMatchObject({
      status: "processing",
      extraction: { status: "processing" },
    });
    const failedProcessed = await processQueuedDocument(failedUpload.json().id);
    expect(failedProcessed.json()).toMatchObject({
      status: "uploaded",
      extraction: {
        status: "failed",
        failureCode: "provider_unavailable",
        retryable: true,
      },
    });
    expect(JSON.stringify(failedProcessed.json())).not.toContain(
      "sk-this-must-never-reach-a-student",
    );
    expect(extractionCalls).toBe(callsBeforeFailure + 2);

    const missingRetryKey = await app.inject({
      method: "POST",
      url: `/v1/student/documents/${failedUpload.json().id}/retry-extraction`,
    });
    expect(missingRetryKey.statusCode).toBe(400);
    expect(missingRetryKey.json()).toMatchObject({
      error: { code: "IDEMPOTENCY_KEY_REQUIRED" },
    });
    const retryWithBody = await app.inject({
      method: "POST",
      url: `/v1/student/documents/${failedUpload.json().id}/retry-extraction`,
      headers: { "idempotency-key": "portal.document.retry-body.0001" },
      payload: { unexpected: true },
    });
    expect(retryWithBody.statusCode).toBe(400);
    expect(retryWithBody.json()).toMatchObject({
      error: { code: "RETRY_EXTRACTION_BODY_NOT_ALLOWED" },
    });

    const retryRequest = {
      method: "POST" as const,
      url: `/v1/student/documents/${failedUpload.json().id}/retry-extraction`,
      headers: { "idempotency-key": "portal.document.retry-extraction.0001" },
    };
    queuedExtractionOutcomes.push(
      new Error("OpenRouter returned HTTP 503: retry still unavailable"),
      new Error("OpenRouter returned HTTP 503: retry still unavailable"),
    );
    const failedRetry = await app.inject(retryRequest);
    const failedRetryReplay = await app.inject(retryRequest);

    expect(failedRetry.statusCode).toBe(200);
    expect(failedRetry.json()).toMatchObject({
      status: "processing",
      extraction: { status: "processing" },
    });
    expect(failedRetryReplay.json()).toEqual(failedRetry.json());
    const failedRetryProcessed = await processQueuedDocument(
      failedUpload.json().id,
    );
    expect(failedRetryProcessed.json()).toMatchObject({
      status: "uploaded",
      extraction: { status: "failed", failureCode: "provider_unavailable" },
    });
    expect(extractionCalls).toBe(callsBeforeFailure + 4);

    const recoveredRetryRequest = {
      ...retryRequest,
      headers: { "idempotency-key": "portal.document.retry-extraction.0002" },
    };
    const retried = await app.inject(recoveredRetryRequest);
    const replay = await app.inject(recoveredRetryRequest);

    expect(retried.statusCode).toBe(200);
    expect(retried.json()).toMatchObject({
      status: "processing",
      extraction: { status: "processing" },
    });
    expect(replay.json()).toEqual(retried.json());
    const recoveredProcessed = await processQueuedDocument(
      failedUpload.json().id,
    );
    expect(recoveredProcessed.json()).toMatchObject({
      status: "needs_review",
      extraction: { status: "completed" },
    });
    // The same key is durable even after a retry fails; a new key is an
    // explicit subsequent attempt. Terminal replays do not parse again.
    const terminalReplay = await app.inject(recoveredRetryRequest);
    expect(terminalReplay.json()).toEqual(recoveredProcessed.json());
    expect(extractionCalls).toBe(callsBeforeFailure + 5);

    const pendingBoundary = "vv-pending-extraction-retry-boundary";
    const pendingPayload = Buffer.from(
      [
        `--${pendingBoundary}`,
        'Content-Disposition: form-data; name="category"',
        "",
        "identity",
        `--${pendingBoundary}`,
        'Content-Disposition: form-data; name="file"; filename="identity-pending.pdf"',
        "Content-Type: application/pdf",
        "",
        "%PDF-1.7",
        "configuration becomes available before retry",
        "%%EOF",
        `--${pendingBoundary}--`,
        "",
      ].join("\r\n"),
    );
    queuedExtractionOutcomes.push({
      status: "pending_configuration",
      documentType: "identity",
      summary: "File stored securely. Parsing is awaiting configuration.",
      studentName: null,
      institutionName: null,
      issueDate: null,
      academicTerm: null,
      fields: [],
      courses: [],
      warnings: ["Parsing is awaiting configuration."],
      model: null,
      provider: "local",
      processedAt: null,
      verifiedAt: null,
      retryable: true,
    });
    const pendingUpload = await app.inject({
      method: "POST",
      url: "/v1/student/documents/upload",
      headers: {
        "content-type": `multipart/form-data; boundary=${pendingBoundary}`,
        "idempotency-key": "portal.document.pending-extraction.0001",
      },
      payload: pendingPayload,
    });
    const pendingProcessed = await processQueuedDocument(pendingUpload.json().id);
    const pendingRetry = await app.inject({
      method: "POST",
      url: `/v1/student/documents/${pendingUpload.json().id}/retry-extraction`,
      headers: { "idempotency-key": "portal.document.pending-retry.0001" },
    });

    expect(pendingProcessed.json()).toMatchObject({
      status: "uploaded",
      extraction: { status: "pending_configuration", retryable: true },
    });
    expect(pendingRetry.statusCode).toBe(200);
    expect(pendingRetry.json()).toMatchObject({
      status: "processing",
      extraction: { status: "processing" },
    });
    const pendingRetryProcessed = await processQueuedDocument(
      pendingUpload.json().id,
    );
    expect(pendingRetryProcessed.json()).toMatchObject({
      status: "needs_review",
      extraction: { status: "completed" },
    });

    const unsupportedBoundary = "vv-unsupported-extraction-boundary";
    const unsupportedPayload = Buffer.from(
      [
        `--${unsupportedBoundary}`,
        'Content-Disposition: form-data; name="category"',
        "",
        "identity",
        `--${unsupportedBoundary}`,
        'Content-Disposition: form-data; name="file"; filename="unsupported.pdf"',
        "Content-Type: application/pdf",
        "",
        "%PDF-1.7",
        "requires staff review",
        "%%EOF",
        `--${unsupportedBoundary}--`,
        "",
      ].join("\r\n"),
    );
    const callsBeforeUnsupported = extractionCalls;
    queuedExtractionOutcomes.push(
      new Error("OpenRouter returned HTTP 415 unsupported parser capability"),
    );
    const unsupportedUpload = await app.inject({
      method: "POST",
      url: "/v1/student/documents/upload",
      headers: {
        "content-type": `multipart/form-data; boundary=${unsupportedBoundary}`,
        "idempotency-key": "portal.document.unsupported-extraction.0001",
      },
      payload: unsupportedPayload,
    });
    const unsupportedProcessed = await processQueuedDocument(
      unsupportedUpload.json().id,
    );
    const unsupportedRetry = await app.inject({
      method: "POST",
      url: `/v1/student/documents/${unsupportedUpload.json().id}/retry-extraction`,
      headers: {
        "idempotency-key": "portal.document.unsupported-retry.0001",
      },
    });

    expect(unsupportedProcessed.json()).toMatchObject({
      status: "uploaded",
      extraction: {
        status: "failed",
        failureCode: "unsupported_capability",
        retryable: false,
      },
    });
    expect(unsupportedRetry.json()).toEqual(unsupportedProcessed.json());
    expect(extractionCalls).toBe(callsBeforeUnsupported + 1);
  });

  it("serves Edward through the same bounded AI adapter", async () => {
    const response = await app.inject({
      method: "POST",
      url: "/v1/student/assistant/messages",
      payload: {
        message: "Where is my document?",
        pageContext: "/dashboard",
        history: [],
      },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      provider: "openrouter",
      model: "test/edward",
      usage: { totalTokens: 58 },
      suggestedActions: [
        { label: "Open documents", href: "/documents" },
      ],
      contextReceipts: [
        { source: "dashboard" },
        { source: "profile" },
        { source: "documents" },
      ],
    });
  });

  it("schedules appointments idempotently and rejects past times", async () => {
    const request = {
      method: "POST" as const,
      url: "/v1/student/appointments",
      headers: { "idempotency-key": "portal.appointment.0001" },
      payload: {
        type: "financial_aid",
        startsAt: "2030-08-10T14:00:00.000Z",
        notes: "Deposit questions",
      },
    };
    const first = await app.inject(request);
    const replay = await app.inject(request);
    const past = await app.inject({
      ...request,
      headers: { "idempotency-key": "portal.appointment.0002" },
      payload: { ...request.payload, startsAt: "2020-01-01T00:00:00.000Z" },
    });

    expect(first.statusCode).toBe(201);
    expect(first.json()).toMatchObject({ status: "scheduled" });
    expect(replay.json()).toEqual(first.json());
    expect(past.statusCode).toBe(400);
  });

  it("updates a versioned profile and returns help content", async () => {
    const profile = await app.inject({
      method: "GET",
      url: "/v1/student/profile",
    });
    const updated = await app.inject({
      method: "PATCH",
      url: "/v1/student/profile",
      payload: {
        expectedVersion: profile.json().version,
        preferredName: "Alexis",
        mobilePhone: "+1 555 010 9911",
        communicationPreference: "sms",
      },
    });
    const stale = await app.inject({
      method: "PATCH",
      url: "/v1/student/profile",
      payload: {
        expectedVersion: profile.json().version,
        preferredName: "Stale",
      },
    });
    const help = await app.inject({
      method: "GET",
      url: "/v1/student/help",
    });

    expect(updated.statusCode).toBe(200);
    expect(updated.json()).toMatchObject({
      preferredName: "Alexis",
      communicationPreference: "sms",
      version: 2,
    });
    expect(stale.statusCode).toBe(409);
    expect(help.statusCode).toBe(200);
    expect(help.json().articles.length).toBeGreaterThan(0);
    expect(help.json().support.email).toContain("@");
  });

  it("keeps every portal resource tenant isolated", async () => {
    const response = await app.inject({
      method: "GET",
      url: "/v1/student/bootstrap",
      headers: {
        "x-demo-tenant-id": "00000000-0000-7000-8000-000000009999",
      },
    });

    expect(response.statusCode).toBe(404);
    expect(response.json()).toMatchObject({
      error: { code: "STUDENT_NOT_FOUND" },
    });
  });

  it("allows versioned portal mutations through configured CORS", async () => {
    const response = await app.inject({
      method: "OPTIONS",
      url: "/v1/student/profile",
      headers: {
        origin: "http://localhost:3000",
        "access-control-request-method": "PATCH",
        "access-control-request-headers": "content-type",
      },
    });

    expect(response.statusCode).toBe(204);
    expect(response.headers["access-control-allow-methods"]).toContain("PATCH");
  });
});
