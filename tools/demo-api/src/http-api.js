import { createHash, randomUUID } from "node:crypto";
import { extractStudentDocumentImageRegion } from "@vv/document-preprocessing";
import { createServer } from "node:http";
import { dirname, extname, join } from "node:path";
import {
  acceptOffer,
  autoProjectCompletedTranscripts,
  buildBootstrap,
  buildCampusLife,
  buildDashboard,
  buildOnboarding,
  buildStudentAcademics,
  buildStudentFinancials,
  completeOnboarding,
  completeDocumentExtractionRetry,
  createAppointment,
  createDepositPayment,
  createDocumentMetadata,
  confirmDocumentExtraction,
  createHelpRequest,
  findDocumentForDownload,
  fixtureSummary,
  getHelpTopics,
  housingPlanResponse,
  idempotentMutation,
  ingestActivities,
  listMessages,
  listDocuments,
  listCatalogCourses,
  listRequirements,
  markMessageRead,
  patchProfile,
  profileResponse,
  queueDocumentExtraction,
  queueDocumentExtractionRetry,
  reserveDocumentUpload,
  requirementDetail,
  selectFinancialPaymentPlan,
  updateHousingPlan,
  updateOnboarding,
} from "./domain.js";
import {
  HttpError,
  badRequest,
  conflict,
  notFound,
  unauthorized,
} from "./errors.js";
import { JsonStateStore } from "./store.js";
import { CredentialAuthStore } from "./credential-auth-store.js";
import { StudentStoreRegistry } from "./student-store-registry.js";
import { createOpenRouterGatewayFromEnv } from "./openrouter.js";
import {
  guardedEdwardResponse,
  normalizeEdwardPageContext,
  normalizeEdwardResponse,
} from "./edward-safety.js";
import {
  exactKeys,
  objectBody,
  requireIdempotencyKey,
} from "./validation.js";

const requestIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const maximumBodyBytes = 262_144;
const maximumUploadBytes = 10_485_760;
const maximumMultipartBytes = maximumUploadBytes + 65_536;
// Version the development fixture cookie so a browser session created before
// credential authentication was introduced cannot silently keep entering the
// shared Alex fixture.
const demoSessionToken = "demo-session-v2";

export async function createDemoApi(options = {}) {
  const clock = options.clock ?? (() => new Date());
  const store =
    options.store ??
    new JsonStateStore(
      options.dataFile,
      clock,
    );
  await store.initialize();
  await reconcileCompletedTranscripts(store, clock);
  const logger = options.logger === undefined ? console : options.logger;
  const authStore =
    options.authStore ??
    new CredentialAuthStore(join(dirname(store.filePath), "auth.json"), clock);
  await authStore.initialize();
  const studentStores =
    options.studentStores ??
    new StudentStoreRegistry(join(dirname(store.filePath), "students"), clock);
  await studentStores.initialize();
  for (const account of authStore.listAccounts()) {
    const studentStore = await studentStores.get(account);
    await reconcileCompletedTranscripts(studentStore, clock);
  }
  const storeOpenRouterResponses =
    options.storeOpenRouterResponses ??
    developmentResponseStorageEnabled(process.env);
  const ai =
    options.ai ??
    createOpenRouterGatewayFromEnv({
      apiKey: options.openRouterApiKey,
      model: options.openRouterModel,
      fetch: options.fetch,
      responseRecorder:
        options.openRouterResponseRecorder ??
        (storeOpenRouterResponses
          ? (response) =>
              studentStores.recordAiProviderResponse(
                response,
                authStore.listAccounts(),
                store,
              )
          : undefined),
    });
  const allowedOrigins = new Set(
    options.allowedOrigins ?? [
      "http://localhost:3000",
      "http://127.0.0.1:3000",
    ],
  );
  // This API is a local development fixture. Keep its state-resetting entry
  // point unavailable in a production process even if this module is reused.
  const guidedOnboardingResetEnabled =
    options.enableGuidedOnboardingReset ?? process.env.NODE_ENV !== "production";

  /**
   * The preview intentionally models production's outbox/worker boundary:
   * HTTP persists metadata and immutable bytes, then this in-process worker
   * consumes the durable queued state.  It is a Set rather than a promise
   * chain so duplicate/replayed requests cannot launch parallel model calls.
   */
  const scheduledDocumentJobs = new Set();
  const enqueueDocumentExtraction = (
    studentStore,
    documentId,
    requestId = randomUUID(),
  ) => {
    const jobKey = `${studentStore.filePath}:${documentId}`;
    if (scheduledDocumentJobs.has(jobKey)) return;
    scheduledDocumentJobs.add(jobKey);
    // A microtask is sufficient to release the request stack before beginning
    // extraction and avoids Node 26's native async-id assertion around
    // setImmediate callbacks created from an HTTP request callback.
    queueMicrotask(() => {
      void runQueuedDocumentExtraction({
        studentStore,
        documentId,
        requestId,
      }).finally(() => {
        scheduledDocumentJobs.delete(jobKey);
      });
    });
  };

  const runQueuedDocumentExtraction = async ({
    studentStore,
    documentId,
    requestId,
  }) => {
    const startedAt = performance.now();
    const document = studentStore
      .snapshot()
      .documents.find((candidate) => candidate.id === documentId);
    if (
      !document ||
      document.status !== "processing" ||
      document.extraction?.status !== "processing" ||
      !document.storageKey ||
      document.contentStored === false
    ) {
      return;
    }

    logger?.info?.(
      JSON.stringify({
        timestamp: clock().toISOString(),
        level: "info",
        service: "vv-demo-api",
        event: "document_extraction_started",
        documentId,
        requestId,
        category: document.category,
        fileName: document.fileName,
      }),
    );

    let extraction;
    try {
      const bytes = await studentStore.readUpload(document.storageKey);
      extraction = await extractStudentDocumentSafely({
        ai,
        documentId,
        fileName: document.fileName,
        mimeType: document.mimeType,
        bytes,
        category: document.category,
        requirementId: document.requirementId,
        clock,
        logger,
        requestId,
      });
    } catch (error) {
      extraction = failedExtraction(
        classifyExtractionFailure(error),
        document.fileName,
        clock(),
      );
    }

    const completed = await studentStore.transact((draft) => {
      const latest = draft.documents.find(
        (candidate) => candidate.id === documentId,
      );
      if (
        !latest ||
        latest.status !== "processing" ||
        latest.extraction?.status !== "processing"
      ) {
        return latest ? structuredClone(latest) : null;
      }
      return completeDocumentExtractionRetry(draft, documentId, extraction, clock());
    });
    logger?.info?.(
      JSON.stringify({
        timestamp: clock().toISOString(),
        level: "info",
        service: "vv-demo-api",
        event: "document_extraction_completed",
        documentId,
        requestId,
        status: completed?.extraction?.status ?? extraction.status,
        provider: completed?.extraction?.provider ?? extraction.provider,
        model: completed?.extraction?.model ?? extraction.model,
        courseCount:
          completed?.extraction?.courses?.length ??
          extraction.courses?.length ??
          0,
        warningCount:
          completed?.extraction?.warnings?.length ??
          extraction.warnings?.length ??
          0,
        failureCode:
          completed?.extraction?.failureCode ??
          extraction.failureCode ??
          null,
        durationMs: Math.round(performance.now() - startedAt),
      }),
    );
  };

  // Recovery covers a process stop after object storage succeeds but before
  // the queued-state transaction or local worker scheduling has run.
  const recoverDocumentJobs = (studentStore) => {
    for (const document of studentStore.snapshot().documents) {
      if (document.status === "processing" && document.extraction?.status === "processing") {
        enqueueDocumentExtraction(
          studentStore,
          document.id,
          `recovery-${document.id}`,
        );
        continue;
      }
      if (
        document.status === "placeholder" &&
        document.storageKey &&
        document.contentStored === false
      ) {
        void studentStore.readUpload(document.storageKey).then(
          () =>
            studentStore
              .transact((draft) => queueDocumentExtraction(draft, document.id, clock()))
              .then(() =>
                enqueueDocumentExtraction(
                  studentStore,
                  document.id,
                  `recovery-${document.id}`,
                ),
              ),
          () => undefined,
        );
      }
    }
  };
  // Inspect durable queued state before accepting traffic. Recovery itself
  // schedules only the jobs that actually exist; avoiding a wrapper immediate
  // also prevents a server from closing while an empty startup callback is
  // still pending.
  recoverDocumentJobs(store);
  for (const studentStore of studentStores.cachedStores()) {
    recoverDocumentJobs(studentStore);
  }

  const server = createServer(async (request, response) => {
    const startedAt = performance.now();
    const requestId = createRequestId(request.headers["x-request-id"]);
    response.setHeader("x-request-id", requestId);
    response.setHeader("x-vv-preview-mode", "development-only");
    response.setHeader("cache-control", "no-store");
    applyCors(request, response, allowedOrigins);

    try {
      if (request.method === "OPTIONS") {
        response.statusCode = 204;
        response.end();
        return;
      }

      const result = await route({
        request,
        store,
        clock,
        ai,
        logger,
        requestId,
        enqueueDocumentExtraction,
        authStore,
        studentStores,
        recoverDocumentJobs,
        guidedOnboardingResetEnabled,
      });
      if (result.headers) {
        for (const [name, value] of Object.entries(result.headers)) {
          response.setHeader(name, value);
        }
      }
      if (result.bytes) {
        sendBytes(response, result.status ?? 200, result.bytes, result.headers);
      } else {
        sendJson(response, result.status ?? 200, result.body);
      }
    } catch (error) {
      sendError(response, error, requestId);
    } finally {
      logger?.info?.(
        JSON.stringify({
          timestamp: clock().toISOString(),
          level: "info",
          service: "vv-demo-api",
          requestId,
          method: request.method,
          path: new URL(request.url ?? "/", "http://localhost").pathname,
          status: response.statusCode,
          durationMs: Math.round(performance.now() - startedAt),
        }),
      );
    }
  });

  return { server, store, authStore, studentStores };
}

async function reconcileCompletedTranscripts(store, clock) {
  await store.transact((draft, transaction) => {
    const projected = autoProjectCompletedTranscripts(draft, clock());
    if (projected === 0) transaction.skipWrite();
    return projected;
  });
}

async function route({
  request,
  store,
  clock,
  ai,
  logger,
  requestId,
  enqueueDocumentExtraction,
  authStore,
  studentStores,
  recoverDocumentJobs,
  guidedOnboardingResetEnabled,
}) {
  const method = request.method ?? "GET";
  const url = new URL(request.url ?? "/", "http://localhost");
  const path = url.pathname.replace(/\/+$/, "") || "/";

  if (method === "GET" && (path === "/health" || path === "/health/live")) {
    return {
      body: {
        status: "ok",
        service: "vv-demo-api",
        environment: "development",
        timestamp: clock().toISOString(),
      },
    };
  }
  if (method === "GET" && path === "/health/ready") {
    const state = store.snapshot();
    return {
      body: {
        status: "ready",
        service: "vv-demo-api",
        fixtureVersion: state.fixture.version,
        revision: state.fixture.revision,
        timestamp: clock().toISOString(),
      },
    };
  }
  if (method === "GET" && path === "/v1/demo/fixture-state") {
    return { body: fixtureSummary(store.snapshot()) };
  }
  if (method === "GET" && path === "/v1/auth/session") {
    const credentialSession = credentialSessionFor(request, authStore);
    if (credentialSession) {
      const studentStore = await studentStores.get(credentialSession.account);
      return {
        body: credentialSessionResponse(
          credentialSession.account,
          studentStore.snapshot(),
        ),
      };
    }
    const authenticated = hasDemoSession(request);
    return {
      body: authenticated
        ? sessionResponse(store.snapshot())
        : { authenticated: false, mode: "demo" },
    };
  }
  if (method === "POST" && path === "/v1/auth/sign-up") {
    const body = await readJson(request);
    const created = await authStore.signUp(body);
    const studentStore = await studentStores.get(created.account);
    recoverDocumentJobs(studentStore);
    return {
      status: 201,
      body: credentialSessionResponse(
        created.account,
        studentStore.snapshot(),
      ),
      headers: {
        "set-cookie": credentialSessionCookie(
          created.sessionToken,
          created.expiresAt,
        ),
      },
    };
  }
  if (method === "POST" && path === "/v1/auth/sign-in") {
    const body = await readJson(request);
    const signedIn = await authStore.signIn(body);
    const studentStore = await studentStores.get(signedIn.account);
    recoverDocumentJobs(studentStore);
    return {
      body: credentialSessionResponse(
        signedIn.account,
        studentStore.snapshot(),
      ),
      headers: {
        "set-cookie": credentialSessionCookie(
          signedIn.sessionToken,
          signedIn.expiresAt,
        ),
      },
    };
  }
  if (method === "POST" && path === "/v1/auth/sign-out") {
    const cookies = parseCookies(request.headers.cookie);
    await authStore.signOut(cookies.vv_session);
    return {
      body: { authenticated: false, mode: "credentials" },
      headers: {
        "set-cookie":
          `vv_session=signed-out; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${cookieSecuritySuffix()}`,
      },
    };
  }
  if (method === "POST" && path === "/v1/auth/demo/sign-in") {
    const body = await readJson(request);
    exactKeys(objectBody(body), []);
    return {
      body: sessionResponse(store.snapshot()),
      headers: {
        "set-cookie":
          `vv_demo_session=${demoSessionToken}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400${cookieSecuritySuffix()}`,
      },
    };
  }
  if (method === "POST" && path === "/v1/auth/demo/start-guided-onboarding") {
    if (!guidedOnboardingResetEnabled) {
      throw notFound(
        "DEMO_GUIDED_ONBOARDING_DISABLED",
        "Guided demo onboarding is only available in the development preview",
      );
    }
    const body = await readJson(request);
    exactKeys(objectBody(body), []);
    const freshState = await store.reset();
    return {
      body: sessionResponse(freshState),
      headers: {
        "set-cookie":
          `vv_demo_session=${demoSessionToken}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400${cookieSecuritySuffix()}`,
      },
    };
  }
  if (method === "POST" && path === "/v1/auth/demo/sign-out") {
    return {
      body: { authenticated: false, mode: "demo" },
      headers: {
        "set-cookie":
          `vv_demo_session=signed-out; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400${cookieSecuritySuffix()}`,
      },
    };
  }

  const credentialSession = credentialSessionFor(request, authStore);
  if (credentialSession) {
    store = await studentStores.get(credentialSession.account);
  } else {
    requireDemoSession(request);
  }

  if (
    method === "GET" &&
    (path === "/v1/bootstrap" || path === "/v1/student/bootstrap")
  ) {
    return { body: buildBootstrap(store.snapshot(), clock) };
  }
  if (method === "GET" && path === "/v1/student/dashboard") {
    return { body: buildDashboard(store.snapshot(), clock) };
  }
  if (method === "GET" && path === "/v1/student/academics") {
    return { body: buildStudentAcademics(store.snapshot(), clock) };
  }
  if (method === "GET" && path === "/v1/catalog/courses") {
    return {
      body: listCatalogCourses(
        store.snapshot(),
        url.searchParams.get("query") ?? "",
      ),
    };
  }
  if (method === "GET" && path === "/v1/student/financials") {
    return { body: buildStudentFinancials(store.snapshot(), clock) };
  }
  if (
    method === "POST" &&
    path === "/v1/student/financials/payment-plan"
  ) {
    const body = await readJson(request);
    return materialWrite({
      request,
      store,
      operation: "financial.payment_plan.select",
      body,
      mutate: (draft) => selectFinancialPaymentPlan(draft, body),
    });
  }
  if (method === "GET" && path === "/v1/student/campus-life") {
    return { body: buildCampusLife(store.snapshot(), clock) };
  }
  if (method === "GET" && path === "/v1/student/onboarding") {
    return { body: buildOnboarding(store.snapshot()) };
  }
  if (
    (method === "PUT" || method === "PATCH") &&
    path === "/v1/student/onboarding"
  ) {
    const body = await readJson(request);
    const result = await store.transact((draft) =>
      updateOnboarding(draft, body, clock()),
    );
    return { body: result };
  }
  if (method === "POST" && path === "/v1/student/onboarding/complete") {
    const body = await readJson(request);
    return materialWrite({
      request,
      store,
      operation: "onboarding.complete",
      body,
      mutate: (draft) => completeOnboarding(draft, body, clock()),
    });
  }
  if (method === "GET" && path === "/v1/student/housing-plan") {
    return { body: housingPlanResponse(store.snapshot()) };
  }
  if (method === "PATCH" && path === "/v1/student/housing-plan") {
    const body = await readJson(request);
    const result = await store.transact((draft) =>
      updateHousingPlan(draft, body, clock()),
    );
    return { body: result };
  }

  const offerMatch = path.match(
    /^\/v1\/admission-offers\/([^/]+)\/accept$/,
  );
  if (method === "POST" && offerMatch) {
    const body = await readJson(request);
    exactKeys(objectBody(body), []);
    const offerId = decodeURIComponent(offerMatch[1]);
    return materialWrite({
      request,
      store,
      operation: `admission_offer.accept:${offerId}`,
      body,
      mutate: (draft) => acceptOffer(draft, offerId, clock()),
    });
  }

  if (method === "POST" && path === "/v1/activity-events/batch") {
    const body = await readJson(request);
    const result = await store.transact((draft) =>
      ingestActivities(draft, body, clock()),
    );
    return { status: 202, body: result };
  }

  if (method === "GET" && path === "/v1/student/requirements") {
    return { body: listRequirements(store.snapshot()) };
  }
  const requirementMatch = path.match(
    /^\/v1\/student\/requirements\/([^/]+)$/,
  );
  if (method === "GET" && requirementMatch) {
    return {
      body: requirementDetail(store.snapshot(), requirementMatch[1]),
    };
  }

  if (method === "GET" && path === "/v1/student/messages") {
    return { body: listMessages(store.snapshot()) };
  }
  const messageMatch = path.match(
    /^\/v1\/student\/messages\/([^/]+)\/read$/,
  );
  if ((method === "POST" || method === "PATCH") && messageMatch) {
    const body = await readJson(request);
    exactKeys(objectBody(body), []);
    const messageId = decodeURIComponent(messageMatch[1]);
    const result = await store.transact((draft, transaction) =>
      markMessageRead(draft, messageId, clock(), transaction),
    );
    return { body: result };
  }

  if (method === "GET" && path === "/v1/student/profile") {
    return { body: profileResponse(store.snapshot()) };
  }
  if (method === "PATCH" && path === "/v1/student/profile") {
    const body = await readJson(request);
    const result = await store.transact((draft) =>
      patchProfile(draft, body, clock()),
    );
    return { body: result };
  }

  if (method === "GET" && path === "/v1/student/documents") {
    return { body: listDocuments(store.snapshot()) };
  }
  if (method === "POST" && path === "/v1/student/documents") {
    const body = await readJson(request);
    const result = await materialWrite({
      request,
      store,
      operation: "document.metadata.create",
      body,
      mutate: (draft) => createDocumentMetadata(draft, body, clock()),
    });
    return { ...result, status: 201 };
  }
  if (method === "POST" && path === "/v1/student/documents/upload") {
    const upload = await readDocumentUpload(request);
    const digest = createHash("sha256").update(upload.bytes).digest("hex");
    // Phase 1: commit the student/document metadata before touching either
    // object storage or an AI provider. A client retry with this idempotency
    // key reuses the same durable record and storage key.
    const reservation = await materialWrite({
      request,
      store,
      operation: "document.upload.reserve",
      body: {
        fileName: upload.fileName,
        mimeType: upload.mimeType,
        sizeBytes: upload.bytes.length,
        category: upload.category,
        requirementId: upload.requirementId,
        sha256: digest,
      },
      mutate: (draft) => {
        if (upload.requirementId) {
          const requirement = draft.requirements.find(
            (candidate) =>
              candidate.id === upload.requirementId &&
              candidate.submissionType === "document",
          );
          if (!requirement) {
            throw badRequest(
              "DOCUMENT_REQUIREMENT_NOT_FOUND",
              "The document requirement was not found",
            );
          }
          if (requirement.status === "blocked") {
            throw conflict(
              "DOCUMENT_REQUIREMENT_BLOCKED",
              "Complete the prerequisite enrollment tasks before uploading this document.",
            );
          }
        }
        const id = randomUUID();
        const storageKey = `${id}${safeExtension(upload.fileName, upload.mimeType)}`;
        return reserveDocumentUpload(
          draft,
          {
            id,
            fileName: upload.fileName,
            mimeType: upload.mimeType,
            sizeBytes: upload.bytes.length,
            category: upload.category,
            requirementId: upload.requirementId,
            sha256: digest,
            storageKey,
          },
          clock(),
        );
      },
    });

    const persisted = store
      .snapshot()
      .documents.find((document) => document.id === reservation.body.id);
    if (!persisted?.storageKey) {
      throw new HttpError(
        500,
        "DOCUMENT_RESERVATION_INCOMPLETE",
        "The document record could not be prepared for storage.",
      );
    }

    // Phase 2: atomically place the immutable original. If this fails, the
    // placeholder remains in the database and a replay safely resumes from
    // this exact point; no parser has seen the file.
    try {
      await store.writeUpload(persisted.storageKey, upload.bytes);
    } catch {
      throw new HttpError(
        503,
        "DOCUMENT_STORAGE_UNAVAILABLE",
        "Your document record was saved, but the original could not be stored yet. Please retry this upload.",
      );
    }

    // Phase 3: durable state transition, then asynchronous extraction. The
    // HTTP response intentionally represents the saved/processing state.
    const queued = await store.transact((draft) =>
      queueDocumentExtraction(draft, persisted.id, clock()),
    );
    if (
      queued.status === "processing" &&
      queued.extraction?.status === "processing"
    ) {
      enqueueDocumentExtraction(store, persisted.id, requestId);
    }
    return { ...reservation, body: queued, status: 201 };
  }
  const retryExtractionMatch = path.match(
    /^\/v1\/student\/documents\/([^/]+)\/retry-extraction$/,
  );
  if (method === "POST" && retryExtractionMatch) {
    const body = await readJson(request);
    exactKeys(objectBody(body), []);
    const documentId = decodeURIComponent(retryExtractionMatch[1]);
    const queued = await materialWrite({
      request,
      store,
      operation: `document.extraction.retry:${documentId}`,
      body,
      mutate: (draft) => queueDocumentExtractionRetry(draft, documentId, clock()).document,
    });
    if (
      queued.body.status === "processing" &&
      queued.body.extraction?.status === "processing"
    ) {
      enqueueDocumentExtraction(store, documentId, requestId);
    }
    return queued;
  }
  const documentContentMatch = path.match(
    /^\/v1\/student\/documents\/([^/]+)\/content$/,
  );
  if (method === "GET" && documentContentMatch) {
    const document = findDocumentForDownload(
      store.snapshot(),
      decodeURIComponent(documentContentMatch[1]),
    );
    return {
      bytes: await store.readUpload(document.storageKey),
      headers: {
        "content-type": document.mimeType,
        "content-disposition": `inline; filename="${safeDownloadName(document.fileName)}"`,
      },
    };
  }
  const documentProfilePhotoMatch = path.match(
    /^\/v1\/student\/documents\/([^/]+)\/profile-photo$/,
  );
  if (method === "GET" && documentProfilePhotoMatch) {
    const document = findDocumentForDownload(
      store.snapshot(),
      decodeURIComponent(documentProfilePhotoMatch[1]),
    );
    const region = document.extraction?.visualRegions?.find(
      (candidate) => candidate.kind === "profile_photo",
    );
    if (
      document.category !== "identity" ||
      document.extraction?.status !== "completed" ||
      !region
    ) {
      throw new HttpError(
        404,
        "DOCUMENT_PROFILE_PHOTO_NOT_FOUND",
        "No profile photo was identified in this document",
      );
    }
    return {
      bytes: await extractStudentDocumentImageRegion({
        bytes: await store.readUpload(document.storageKey),
        mimeType: document.mimeType,
        region,
      }),
      headers: {
        "content-type": "image/jpeg",
        "content-disposition": `inline; filename="profile-photo-${document.id}.jpg"`,
        "cache-control": "private, max-age=300",
      },
    };
  }
  const confirmExtractionMatch = path.match(
    /^\/v1\/student\/documents\/([^/]+)\/confirm-extraction$/,
  );
  if (method === "POST" && confirmExtractionMatch) {
    const body = await readJson(request);
    const documentId = decodeURIComponent(confirmExtractionMatch[1]);
    return materialWrite({
      request,
      store,
      operation: `document.extraction.confirm:${documentId}`,
      body,
      mutate: (draft) =>
        confirmDocumentExtraction(draft, documentId, body, clock()),
    });
  }

  if (method === "POST" && path === "/v1/student/assistant/messages") {
    const body = await readJson(request);
    const pageContext = validateEdwardInput(body);
    const guarded = guardedEdwardResponse(body.message);
    if (guarded) return { body: guarded };
    const state = store.snapshot();
    const studentContext = buildAssistantContext(state);
    const response = await ai.askEdward({
      message: body.message,
      pageContext,
      history: body.history,
      studentContext,
    });
    return {
      // The preview mirrors the production API: the orchestration layer—not
      // an intent regex—attaches receipts for projections it just collected.
      body: {
        ...normalizeEdwardResponse(response, {
          offerId: studentContext.offerId,
          depositAmountCents: studentContext.depositAmountCents,
          depositPaid: studentContext.depositPaid,
          allowDepositPayment:
            /(?:pay|make|complete).{0,24}deposit|deposit.{0,24}(?:pay|payment)/i.test(
              body.message,
            ),
          documentUploadCategory:
            /upload|transcript|fafsa|verification/i.test(body.message)
              ? /transcript/i.test(body.message)
                ? "transcript"
                : "financial_aid"
              : null,
          appointmentType:
            /appointment|advisor|counselor|human/i.test(body.message)
              ? /financial|aid|fafsa|loan/i.test(body.message)
                ? "financial_aid"
                : "enrollment_support"
              : null,
        }),
        contextReceipts: studentContext.contextReceipts,
      },
    };
  }

  if (method === "GET" && path === "/v1/student/appointments") {
    const items = store
      .snapshot()
      .appointments.toSorted(
        (left, right) =>
          left.startsAt.localeCompare(right.startsAt) ||
          left.id.localeCompare(right.id),
      );
    return { body: { items, total: items.length } };
  }
  if (method === "POST" && path === "/v1/student/appointments") {
    const body = await readJson(request);
    const result = await materialWrite({
      request,
      store,
      operation: "appointment.create",
      body,
      mutate: (draft) => createAppointment(draft, body, clock()),
    });
    return { ...result, status: 201 };
  }

  if (method === "GET" && path === "/v1/student/payments") {
    const items = store
      .snapshot()
      .payments.toSorted(
        (left, right) =>
          right.createdAt.localeCompare(left.createdAt) ||
          left.id.localeCompare(right.id),
      );
    return { body: { items, total: items.length } };
  }
  if (
    method === "POST" &&
    (path === "/v1/student/payments/deposit" ||
      path === "/v1/payments/deposit")
  ) {
    const body = await readJson(request);
    return materialWrite({
      request,
      store,
      operation: "payment.deposit",
      body,
      mutate: (draft) => createDepositPayment(draft, body, clock()),
    });
  }

  if (
    method === "GET" &&
    (path === "/v1/student/help" || path === "/v1/help")
  ) {
    return { body: getHelpTopics() };
  }
  if (
    method === "POST" &&
    path === "/v1/demo/help-requests"
  ) {
    const body = await readJson(request);
    return materialWrite({
      request,
      store,
      operation: "help.request.create",
      body,
      mutate: (draft) => createHelpRequest(draft, body, clock()),
    });
  }

  throw notFound(
    "ENDPOINT_NOT_FOUND",
    `No preview endpoint matches ${method} ${path}`,
  );
}

async function materialWrite({
  request,
  store,
  operation,
  body,
  mutate,
}) {
  const key = requireIdempotencyKey(request.headers);
  const result = await idempotentMutation({
    store,
    operation,
    key,
    body,
    mutate,
  });
  return {
    body: result.response,
    headers: {
      "idempotency-replayed": result.replayed ? "true" : "false",
    },
  };
}

async function readJson(request) {
  const bytes = await readBody(request, maximumBodyBytes);
  if (bytes.length === 0) return {};
  const contentType = request.headers["content-type"] ?? "";
  if (!String(contentType).toLowerCase().startsWith("application/json")) {
    throw new HttpError(
      415,
      "JSON_REQUIRED",
      "Content-Type must be application/json",
    );
  }
  try {
    return JSON.parse(bytes.toString("utf8"));
  } catch {
    throw badRequest("INVALID_JSON", "The request body is not valid JSON");
  }
}

async function readDocumentUpload(request) {
  const contentType = String(request.headers["content-type"] ?? "");
  if (!contentType.toLowerCase().startsWith("multipart/form-data")) {
    throw new HttpError(
      415,
      "MULTIPART_REQUIRED",
      "Content-Type must be multipart/form-data",
    );
  }
  const bytes = await readBody(request, maximumMultipartBytes);
  const webRequest = new Request("http://localhost/v1/student/documents/upload", {
    method: "POST",
    headers: { "content-type": contentType },
    body: bytes,
  });
  const form = await webRequest.formData().catch(() => null);
  if (!form) {
    throw badRequest("INVALID_MULTIPART", "The upload form could not be read");
  }
  const allowedFields = new Set(["file", "category", "requirementId"]);
  for (const field of form.keys()) {
    if (!allowedFields.has(field)) {
      throw badRequest(
        "INVALID_MULTIPART_FIELD",
        `Unexpected multipart field: ${field}`,
      );
    }
  }
  const files = form.getAll("file");
  const categories = form.getAll("category");
  const requirementIds = form.getAll("requirementId");
  if (files.length !== 1) {
    throw badRequest(
      "ONE_FILE_PER_UPLOAD",
      "Upload exactly one document per request",
    );
  }
  if (categories.length > 1 || requirementIds.length > 1) {
    throw badRequest(
      "DUPLICATE_MULTIPART_FIELD",
      "Category and requirement context may only be provided once",
    );
  }
  const [file] = files;
  const category = categories.length === 1 ? categories[0] : null;
  const requirementId =
    requirementIds.length === 1 ? requirementIds[0] : null;
  if (!(file instanceof File)) {
    throw badRequest("FILE_REQUIRED", "Choose a document file to upload");
  }
  if (!["application/pdf", "image/jpeg", "image/png"].includes(file.type)) {
    throw new HttpError(
      415,
      "UNSUPPORTED_FILE_TYPE",
      "Use a PDF, JPEG, or PNG document",
    );
  }
  if (file.size < 1 || file.size > maximumUploadBytes) {
    throw new HttpError(
      413,
      "DOCUMENT_TOO_LARGE",
      "Documents must be no larger than 10 MB",
    );
  }
  const fileBytes = Buffer.from(await file.arrayBuffer());
  if (!matchesDeclaredFileType(fileBytes, file.type)) {
    throw new HttpError(
      415,
      "FILE_SIGNATURE_MISMATCH",
      "The file contents do not match the selected PDF, JPEG, or PNG type",
    );
  }
  if (
    category !== null &&
    (typeof category !== "string" ||
    ![
      "identity",
      "residency",
      "transcript",
      "financial_aid",
      "health",
      "consent",
      "other",
    ].includes(category))
  ) {
    throw badRequest("INVALID_DOCUMENT_CATEGORY", "Choose a valid category");
  }
  if (
    requirementId !== null &&
    (typeof requirementId !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        requirementId,
      ))
  ) {
    throw badRequest("INVALID_REQUIREMENT_ID", "The document requirement is invalid");
  }
  const fileName = safeDownloadName(file.name);
  const requestedCategory =
    typeof requirementId === "string"
      ? categoryForRequirementId(requirementId)
      : null;
  if (typeof requirementId === "string" && !requestedCategory) {
    throw badRequest(
      "DOCUMENT_REQUIREMENT_NOT_FOUND",
      "The document requirement was not found",
    );
  }
  return {
    fileName,
    mimeType: file.type,
    category:
      requestedCategory ??
      (typeof category === "string" ? category : "other"),
    requirementId:
      typeof requirementId === "string" ? requirementId : undefined,
    bytes: fileBytes,
  };
}

function categoryForRequirementId(requirementId) {
  const categoryByRequirementId = {
    "00000000-0000-7000-8000-000000000602": "identity",
    "00000000-0000-7000-8000-000000000604": "transcript",
    "00000000-0000-7000-8000-000000000605": "financial_aid",
    "00000000-0000-7000-8000-000000000606": "health",
  };
  return categoryByRequirementId[requirementId] ?? null;
}

function documentTypeForCategory(category) {
  return {
    identity: "identity",
    residency: "residency",
    transcript: "transcript",
    financial_aid: "financial_aid",
    health: "immunization",
    consent: "ferpa",
  }[category];
}

async function readBody(request, maximumBytes) {
  const contentLength = Number(request.headers["content-length"] ?? 0);
  if (contentLength > maximumBytes) {
    throw new HttpError(
      413,
      "REQUEST_TOO_LARGE",
      `Request bodies are limited to ${maximumBytes} bytes`,
    );
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > maximumBytes) {
      throw new HttpError(
        413,
        "REQUEST_TOO_LARGE",
        `Request bodies are limited to ${maximumBytes} bytes`,
      );
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function requireDemoSession(request) {
  if (!hasDemoSession(request)) throw unauthorized();
}

function hasDemoSession(request) {
  const cookies = parseCookies(request.headers.cookie);
  return cookies.vv_demo_session === demoSessionToken;
}

function credentialSessionFor(request, authStore) {
  const cookies = parseCookies(request.headers.cookie);
  return authStore.getSession(cookies.vv_session);
}

function credentialSessionCookie(token, expiresAt) {
  const maxAge = Math.max(
    0,
    Math.floor((Date.parse(expiresAt) - Date.now()) / 1_000),
  );
  return `vv_session=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}; Priority=High${cookieSecuritySuffix()}`;
}

function cookieSecuritySuffix() {
  return process.env.NODE_ENV === "production" ? "; Secure" : "";
}

function parseCookies(header) {
  if (typeof header !== "string") return {};
  return Object.fromEntries(
    header.split(";").map((part) => {
      const separator = part.indexOf("=");
      if (separator < 0) return [part.trim(), ""];
      return [
        part.slice(0, separator).trim(),
        decodeURIComponent(part.slice(separator + 1).trim()),
      ];
    }),
  );
}

function sessionResponse(state) {
  return {
    authenticated: true,
    mode: "demo",
    actorType: "student",
    student: {
      id: state.profile.studentId,
      preferredName: state.profile.preferredName,
    },
    notice:
      "Development fixture only. This is not an institutional authentication session.",
  };
}

function credentialSessionResponse(account, state) {
  return {
    authenticated: true,
    mode: "credentials",
    actorType: "student",
    student: {
      id: state.profile.studentId,
      preferredName:
        state.onboarding.status === "completed"
          ? state.profile.preferredName
          : null,
      email: account.email,
      phone: account.phone,
      emailVerified: account.emailVerified,
      phoneVerified: account.phoneVerified,
    },
    notice:
      "Email and phone verification are pending until delivery providers are configured.",
  };
}

function applyCors(request, response, allowedOrigins) {
  const origin = request.headers.origin;
  if (typeof origin === "string" && allowedOrigins.has(origin)) {
    response.setHeader("access-control-allow-origin", origin);
    response.setHeader("access-control-allow-credentials", "true");
    response.setHeader("vary", "Origin");
  }
  response.setHeader(
    "access-control-allow-methods",
    "GET, POST, PUT, PATCH, OPTIONS",
  );
  response.setHeader(
    "access-control-allow-headers",
    "Content-Type, Idempotency-Key, X-Request-Id",
  );
  response.setHeader(
    "access-control-expose-headers",
    "X-Request-Id, Idempotency-Replayed, X-VV-Preview-Mode",
  );
  response.setHeader("access-control-max-age", "600");
}

function sendJson(response, status, body) {
  response.statusCode = status;
  response.setHeader("content-type", "application/json; charset=utf-8");
  response.end(`${JSON.stringify(body)}\n`);
}

function sendBytes(response, status, bytes, headers = {}) {
  response.statusCode = status;
  for (const [name, value] of Object.entries(headers)) {
    response.setHeader(name, value);
  }
  response.setHeader("content-length", bytes.length);
  response.end(bytes);
}

function sendError(response, error, requestId) {
  const known = error instanceof HttpError;
  const status = known ? error.status : 500;
  const code = known ? error.code : "INTERNAL_ERROR";
  const message = known
    ? error.message
    : "The development preview could not complete the request";
  sendJson(response, status, {
    error: {
      code,
      message,
      requestId,
      ...(known && error.details !== undefined
        ? { details: error.details }
        : {}),
    },
  });
}

function createRequestId(candidate) {
  return typeof candidate === "string" && requestIdPattern.test(candidate)
    ? candidate
    : randomUUID();
}

function validateEdwardInput(input) {
  const body = objectBody(input);
  exactKeys(body, ["message", "pageContext", "history"]);
  if (
    typeof body.message !== "string" ||
    body.message.trim().length < 1 ||
    body.message.length > 2_000
  ) {
    throw badRequest("INVALID_MESSAGE", "message must contain 1-2000 characters");
  }
  if (
    typeof body.pageContext !== "string" ||
    body.pageContext.length > 120
  ) {
    throw badRequest(
      "INVALID_PAGE_CONTEXT",
      "pageContext must be a string up to 120 characters",
    );
  }
  if (
    body.history !== undefined &&
    (!Array.isArray(body.history) ||
      body.history.length > 8 ||
      body.history.some(
        (entry) =>
          !entry ||
          !["user", "assistant"].includes(entry.role) ||
          typeof entry.content !== "string" ||
          entry.content.length > 1_200,
      ))
  ) {
    throw badRequest(
      "INVALID_HISTORY",
      "history must contain up to 8 short user or assistant messages",
    );
  }
  return normalizeEdwardPageContext(body.pageContext);
}

function buildAssistantContext(state) {
  const academics = buildStudentAcademics(state);
  const financials = buildStudentFinancials(state);
  return {
    contextReceipts: [
      { source: "dashboard" },
      { source: "profile" },
      { source: "documents" },
      { source: "onboarding" },
      { source: "payments" },
      { source: "academics" },
      { source: "financials" },
      { source: "messages" },
    ],
    preferredName: state.profile.preferredName,
    programName: state.offer.programName,
    termName: state.offer.termName,
    onboardingStatus: state.onboarding.status,
    enrollmentCompletion: buildDashboard(state).journey.completionPercent,
    nextAction: buildDashboard(state).journey.nextAction,
    unreadMessages: state.messages.filter((message) => message.readAt === null)
      .length,
    documentStatuses: state.documents.map((document) => ({
      category: document.category,
      status: document.status,
    })),
    offerId: state.offer.id,
    depositAmountCents: state.offer.depositAmountCents,
    depositPaid: state.payments.some(
      (payment) =>
        payment.type === "enrollment_deposit" &&
        payment.status === "succeeded",
    ),
    academicSummary: {
      selectedProgram: academics.selectedProgram.name,
      suggestedExemptions: academics.exemptionRecommendations.map(
        (recommendation) => recommendation.targetCourseCode,
      ),
      eligibleCourses: academics.plan
        .filter((item) => item.status === "eligible")
        .map((item) => item.course.code),
    },
    financialSummary: {
      remainingBalanceCents: financials.remainingBalanceCents,
      acceptedAidCents: financials.acceptedAidCents,
      actionRequiredDocuments: financials.requiredDocuments
        .filter((document) => document.status === "action_required")
        .map((document) => document.code),
      sapStatus: financials.sap.status,
    },
  };
}

function safeExtension(fileName, mimeType) {
  const extension = extname(fileName).toLowerCase();
  const allowed = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
  };
  return extension === ".jpeg" && mimeType === "image/jpeg"
    ? ".jpg"
    : extension === allowed[mimeType]
      ? extension
      : allowed[mimeType];
}

function safeDownloadName(fileName) {
  const name = String(fileName ?? "")
    .replaceAll("\\", "_")
    .replaceAll("/", "_")
    .replace(/[\u0000-\u001f\u007f"]/g, "")
    .trim()
    .slice(0, 255);
  if (!name) {
    throw badRequest("INVALID_FILE_NAME", "The document needs a file name");
  }
  return name;
}

function matchesDeclaredFileType(bytes, mimeType) {
  if (mimeType === "application/pdf") {
    return bytes.length >= 5 && bytes.subarray(0, 5).toString("ascii") === "%PDF-";
  }
  if (mimeType === "image/jpeg") {
    return (
      bytes.length >= 3 &&
      bytes[0] === 0xff &&
      bytes[1] === 0xd8 &&
      bytes[2] === 0xff
    );
  }
  if (mimeType === "image/png") {
    return (
      bytes.length >= 8 &&
      bytes.subarray(0, 8).equals(
        Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
      )
    );
  }
  return false;
}

/**
 * A stored upload is always retained when a parser call fails. We make one
 * bounded retry only for provider-shaped/transient failures (such as the
 * observed HTTP 200 response without a completion), then persist a safe
 * diagnostic rather than an opaque success-looking record.
 */
async function extractStudentDocumentSafely({
  ai,
  documentId,
  fileName,
  mimeType,
  bytes,
  category,
  requirementId,
  clock,
  logger,
  requestId,
}) {
  const expectedDocumentType = documentTypeForCategory(category);
  let firstFailure = null;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      let extraction = await ai.extractStudentDocument({
        documentId,
        requestId,
        attempt: attempt + 1,
        fileName,
        mimeType,
        bytes,
        expectedDocumentType,
      });
      extraction = failedExtractionForNoUsefulOutput(
        extraction,
        clock(),
        expectedDocumentType,
      );
      if (
        category === "financial_aid" &&
        extraction.status === "completed"
      ) {
        extraction = classificationOnlyExtraction(extraction);
      }
      if (
        requirementId &&
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
      return extraction;
    } catch (error) {
      const failure = classifyExtractionFailure(error);
      if (attempt === 0 && shouldAutomaticallyRetryExtraction(error, failure)) {
        firstFailure = failure;
        logDocumentExtractionEvent(logger, {
          requestId,
          event: "document_extraction_retrying",
          failureCode: failure.failureCode,
        });
        continue;
      }
      const extraction = failedExtraction(failure, fileName, clock());
      logDocumentExtractionEvent(logger, {
        requestId,
        event: "document_extraction_failed",
        failureCode: extraction.failureCode,
        retryable: extraction.retryable,
        ...(firstFailure
          ? { initialFailureCode: firstFailure.failureCode }
          : {}),
      });
      return extraction;
    }
  }
  // The loop always returns. This guard makes the state safe if it is changed.
  return failedExtraction(
    { failureCode: "unknown", retryable: true },
    fileName,
    clock(),
  );
}

function developmentResponseStorageEnabled(environment) {
  const configured = environment.OPENROUTER_STORE_RESPONSES?.trim().toLowerCase();
  if (configured === "true") return true;
  if (configured === "false") return false;
  return environment.NODE_ENV !== "production";
}

function failedExtractionForNoUsefulOutput(
  extraction,
  now,
  expectedDocumentType,
) {
  if (
    extraction?.status !== "completed" ||
    hasUsefulStructuredOutput(extraction) ||
    (expectedDocumentType &&
      extraction?.documentType &&
      extraction.documentType !== expectedDocumentType)
  ) {
    return extraction;
  }
  return {
    ...extraction,
    status: "failed",
    summary:
      "The file was stored, but the parser did not find usable student-record information.",
    fields: [],
    courses: [],
    warnings: [
      "The parser could not produce usable structured information from this file.",
      "You can retry parsing without uploading the file again.",
    ],
    failureCode: "invalid_response",
    retryable: true,
    processedAt: extraction.processedAt ?? now.toISOString(),
    verifiedAt: null,
  };
}

function hasUsefulStructuredOutput(extraction) {
  if (extraction.documentType && extraction.documentType !== "other") {
    // A confident document classification is useful for a review queue even
    // when intentionally redacted identity documents have no safe fields.
    return true;
  }
  if (
    Array.isArray(extraction.fields) &&
    extraction.fields.some(
      (field) => typeof field?.value === "string" && field.value.trim(),
    )
  ) {
    return true;
  }
  if (
    Array.isArray(extraction.courses) &&
    extraction.courses.some(
      (course) => typeof course?.title === "string" && course.title.trim(),
    )
  ) {
    return true;
  }
  return [
    extraction.studentName,
    extraction.institutionName,
    extraction.issueDate,
    extraction.academicTerm,
  ].some((value) => typeof value === "string" && value.trim());
}

function classificationOnlyExtraction(extraction) {
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

function shouldAutomaticallyRetryExtraction(error, failure) {
  if (!failure.retryable) return false;
  const status = Number(error?.status);
  const message = String(error?.message ?? "").toLowerCase();
  return (
    failure.failureCode === "provider_unavailable" ||
    failure.failureCode === "timeout" ||
    /empty completion|no readable content/.test(message) ||
    status === 429 ||
    status >= 500
  );
}

function classifyExtractionFailure(error) {
  const status = Number(error?.status);
  const name = String(error?.name ?? "").toLowerCase();
  const message = String(error?.message ?? "").toLowerCase();
  if (
    name.includes("timeout") ||
    /timed? out|timeout|aborted due to timeout/.test(message) ||
    status === 408 ||
    status === 504
  ) {
    return { failureCode: "timeout", retryable: true };
  }
  if (
    /unsupported|not support|no compatible|invalid.*(?:schema|response|file)|unavailable for free/.test(
      message,
    ) ||
    status === 400 ||
    status === 404 ||
    status === 422
  ) {
    return { failureCode: "unsupported_capability", retryable: false };
  }
  if (
    status === 429 ||
    status >= 500 ||
    /rate limit|provider returned error|temporar(?:y|ily)|unavailable|fetch failed|econn|enotfound/.test(
      message,
    )
  ) {
    return { failureCode: "provider_unavailable", retryable: true };
  }
  if (/empty completion|no readable content|json|schema/.test(message)) {
    return { failureCode: "invalid_response", retryable: true };
  }
  return { failureCode: "unknown", retryable: true };
}

function logDocumentExtractionEvent(logger, event) {
  logger?.warn?.(
    JSON.stringify({
      timestamp: new Date().toISOString(),
      level: "warn",
      service: "vv-demo-api",
      ...event,
    }),
  );
}

function failedExtraction(error, fileName, now) {
  const { failureCode, retryable } = error;
  const message = {
    provider_unavailable:
      "The document parser is temporarily unavailable. You can retry parsing without uploading the file again.",
    unsupported_capability:
      "This document cannot be parsed with the current parser configuration. The original file is still available.",
    invalid_response:
      "The document parser did not return usable structured information. You can retry parsing without uploading the file again.",
    timeout:
      "Parsing took too long to finish. You can retry parsing without uploading the file again.",
    unknown:
      "The parsing attempt did not finish. You can retry parsing without uploading the file again.",
  }[failureCode] ??
    "The parsing attempt did not finish. You can retry parsing without uploading the file again.";
  return {
    status: "failed",
    documentType: fileName.toLowerCase().includes("transcript")
      ? "transcript"
      : "other",
    summary:
      "The file was stored, but structured extraction could not be completed.",
    studentName: null,
    institutionName: null,
    issueDate: null,
    academicTerm: null,
    fields: [],
    courses: [],
    warnings: [message],
    model: null,
    provider: "local",
    processedAt: now.toISOString(),
    verifiedAt: null,
    failureCode,
    retryable,
  };
}
