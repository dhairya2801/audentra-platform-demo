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
    async extractStudentDocument(): Promise<StudentDocumentExtraction> {
      extractionCalls += 1;
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
        toolsUsed: ["get_enrollment_status"],
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
        data: { legalNameConfirmed: true },
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
          legalNameConfirmed: true,
          contactInformationConfirmed: true,
          homeAddressConfirmed: true,
          communicationPreference: "email",
          residencyStatus: "domestic",
        },
      },
      {
        step: "housing",
        data: { housingPreference: "on_campus" },
      },
      {
        step: "campus_life",
        data: { campusInterests: ["student_clubs"] },
      },
      {
        step: "emergency_contacts",
        data: { emergencyContactConfirmed: true },
      },
      {
        step: "other_records",
        data: { recordsConfirmed: true },
      },
      {
        step: "family_permissions",
        data: { familyPermissionsReviewed: true },
      },
      {
        step: "review_and_sign",
        data: { signatureConfirmed: true },
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
      version: 9,
    });
    expect(resumed.json().completedSteps).toHaveLength(8);
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
        expectedVersion: 9,
        currentStep: "deposit",
        data: { depositAcknowledged: true },
      },
    });
    expect(depositStep.statusCode).toBe(200);
    expect(depositStep.json()).toMatchObject({
      completedSteps: expect.arrayContaining(["deposit"]),
      version: 10,
    });

    const request = {
      method: "POST" as const,
      url: "/v1/student/onboarding/complete",
      headers: { "idempotency-key": "portal.onboarding.complete.0001" },
      payload: { expectedVersion: 10 },
    };
    const completed = await app.inject(request);
    const replay = await app.inject(request);
    const bootstrap = await app.inject({
      method: "GET",
      url: "/v1/student/bootstrap",
    });

    expect(completed.statusCode).toBe(200);
    expect(replay.json()).toEqual(completed.json());
    expect(completed.json()).toMatchObject({
      status: "completed",
      version: 11,
    });
    expect(bootstrap.json()).toMatchObject({
      onboarding: { required: false, status: "completed" },
      initialRoute: "/dashboard",
    });
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
    const missing = await app.inject({
      method: "GET",
      url: "/v1/student/requirements/00000000-0000-7000-8000-000000009999",
    });

    expect(list.statusCode).toBe(200);
    expect(list.json()).toMatchObject({ total: 1 });
    expect(detail.statusCode).toBe(200);
    expect(detail.json()).toMatchObject({
      code: "profile_verification",
      submissionType: "form",
    });
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
    expect(list.json()).toMatchObject({ total: 2 });
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
      status: "needs_review",
      extraction: {
        status: "completed",
        documentType: "ferpa",
      },
    });
    expect(uploaded.json().sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(extractionCalls).toBe(1);

    const content = await app.inject({
      method: "GET",
      url: uploaded.json().contentUrl,
    });
    expect(content.statusCode).toBe(200);
    expect(content.headers["content-type"]).toMatch(/^application\/pdf/);
    expect(content.rawPayload).toEqual(fileBytes);

    const confirmed = await app.inject({
      method: "POST",
      url: `/v1/student/documents/${uploaded.json().id}/confirm-extraction`,
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
      status: "needs_review",
      extraction: { status: "completed" },
    });
    expect(extractionCalls).toBe(previousExtractionCalls + 1);
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
