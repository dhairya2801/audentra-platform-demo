import { createHash, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import {
  createSignedOnboardingPdf,
  extractStudentDocumentImageRegion,
} from "@vv/document-preprocessing";
import { createServer } from "node:http";
import { dirname, extname, join } from "node:path";
import {
  acceptOffer,
  autoProjectCompletedTranscripts,
  buildStaffActionCenter,
  buildStaffOperationsWorkspace,
  buildStaffStudentRecord,
  buildBootstrap,
  buildCampusLife,
  buildDashboard,
  buildOnboarding,
  buildStudentAcademics,
  buildStudentFinancials,
  completeOnboarding,
  completeDocumentExtractionRetry,
  createStaffClub,
  createStaffCorePlay,
  createStaffKnowledgeCard,
  createAppointment,
  createDepositPayment,
  createDocumentMetadata,
  confirmDocumentExtraction,
  createHelpRequest,
  ensureStaffDocumentWorkItems,
  expireStaleDocumentExtractions,
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
  previewStaffEdward,
  queueDocumentExtraction,
  queueDocumentExtractionRetry,
  reconcileAuthoritativeRewards,
  reviewStaffDocument,
  reserveDocumentUpload,
  requirementDetail,
  selectFinancialPaymentPlan,
  simulateStaffOutreach,
  updateHousingPlan,
  updateOnboarding,
  updateStaffClub,
  updateStaffCorePlay,
  updateStaffInquiry,
  updateStaffKnowledgeCard,
  updateStaffStudentPreferences,
  updateStaffWorkItem,
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
import { StaffCredentialAuthStore } from "./staff-credential-auth-store.js";
import { StudentStoreRegistry } from "./student-store-registry.js";
import { TenantStoreRegistry } from "./tenant-store-registry.js";
import {
  demoTenants,
  publicTenantContext,
  tenantConfigForSlug,
} from "./tenant-config.js";
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
import {
  draftManagedConfigurationWithEdward,
  getManagedConfiguration,
  updateManagedConfiguration,
} from "./managed-config.js";

const requestIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const maximumBodyBytes = 262_144;
const maximumUploadBytes = 10_485_760;
const maximumMultipartBytes = maximumUploadBytes + 65_536;
// Version the development fixture cookie so a browser session created before
// credential authentication was introduced cannot silently keep entering the
// shared Alex fixture.
const demoSessionToken = "demo-session-v2";
const onboardingDocumentTemplates = [
  {
    code: "ferpa_release",
    title: "FERPA Information Release",
    fileName: "ferpa-information-release-signed.pdf",
    sourceSuffix: "ferpa-release.pdf",
    signatureBox: { x: 0.098, y: 0.488, width: 0.53, height: 0.054 },
  },
  {
    code: "enrollment_acknowledgment",
    title: "Enrollment Information Acknowledgment",
    fileName: "enrollment-information-acknowledgment-signed.pdf",
    sourceSuffix: "enrollment-acknowledgment.pdf",
    signatureBox: { x: 0.098, y: 0.447, width: 0.53, height: 0.054 },
  },
];

export async function createDemoApi(options = {}) {
  const clock = options.clock ?? (() => new Date());
  const store =
    options.store ??
    new JsonStateStore(
      options.dataFile,
      clock,
    );
  await store.initialize();
  const tenantStores =
    options.tenantStores ??
    new TenantStoreRegistry(store, clock, options.tenantSeedStateOptions);
  await tenantStores.initialize();
  for (const [, tenantStore] of tenantStores.entries()) {
    await reconcileCompletedTranscripts(tenantStore, clock);
  }
  const logger = options.logger === undefined ? console : options.logger;
  const authStore =
    options.authStore ??
    new CredentialAuthStore(join(dirname(store.filePath), "auth.json"), clock);
  await authStore.initialize();
  const staffAuthStore =
    options.staffAuthStore ??
    new StaffCredentialAuthStore(
      join(dirname(store.filePath), "staff-auth.json"),
      clock,
      options.staffBootstrapPassword,
    );
  await staffAuthStore.initialize(
    tenantStores.entries().map(([tenantSlug, tenantStore]) => ({
      tenantSlug,
      members: tenantStore.snapshot().staff.members,
    })),
  );
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
  const recoverDocumentJobs = async (studentStore) => {
    await studentStore.transact((draft, transaction) => {
      const expired = expireStaleDocumentExtractions(draft, clock());
      if (expired === 0) transaction.skipWrite();
      return expired;
    });
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
  for (const [, tenantStore] of tenantStores.entries()) {
    await recoverDocumentJobs(tenantStore);
  }
  for (const studentStore of studentStores.cachedStores()) {
    await recoverDocumentJobs(studentStore);
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

      const tenant = resolveRequestTenant(request);
      const requestStore = tenantStores.get(tenant.slug);
      if (!requestStore) {
        throw badRequest("UNKNOWN_TENANT", "The requested university is not configured");
      }
      const result = await route({
        request,
        store: requestStore,
        tenant,
        clock,
        ai,
        logger,
        requestId,
        enqueueDocumentExtraction,
        authStore,
        staffAuthStore,
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
          tenant: request.headers["x-tenant-slug"] ?? demoTenants.aster.slug,
          status: response.statusCode,
          durationMs: Math.round(performance.now() - startedAt),
        }),
      );
    }
  });

  return {
    server,
    store,
    authStore,
    staffAuthStore,
    studentStores,
    tenantStores,
  };
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
  tenant,
  clock,
  ai,
  logger,
  requestId,
  enqueueDocumentExtraction,
  authStore,
  staffAuthStore,
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
  if (method === "GET" && path === "/v1/tenant/context") {
    return { body: publicTenantContext(tenant) };
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
    const credentialSession = credentialSessionFor(
      request,
      authStore,
      tenant.slug,
    );
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
  if (method === "GET" && path === "/v1/auth/staff/session") {
    const staffSession = staffCredentialSessionFor(
      request,
      staffAuthStore,
      tenant.slug,
    );
    return {
      body: staffSession
        ? staffSessionResponse(staffSession.account)
        : {
            authenticated: false,
            mode: "credentials",
            actorType: "staff",
          },
    };
  }
  if (method === "POST" && path === "/v1/auth/sign-up") {
    const body = await readJson(request);
    const created = await authStore.signUp(body, tenant.slug);
    const studentStore = await studentStores.get(created.account);
    await recoverDocumentJobs(studentStore);
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
    const signedIn = await authStore.signIn(body, tenant.slug);
    const studentStore = await studentStores.get(signedIn.account);
    await recoverDocumentJobs(studentStore);
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
  if (method === "POST" && path === "/v1/auth/staff/sign-in") {
    const body = await readJson(request);
    const signedIn = await staffAuthStore.signIn(body, tenant.slug);
    return {
      body: staffSessionResponse(signedIn.account),
      headers: {
        "set-cookie": staffCredentialSessionCookie(
          signedIn.sessionToken,
          signedIn.expiresAt,
        ),
      },
    };
  }
  if (method === "POST" && path === "/v1/auth/staff/sign-out") {
    const cookies = parseCookies(request.headers.cookie);
    await staffAuthStore.signOut(cookies.vv_staff_session);
    return {
      body: {
        authenticated: false,
        mode: "credentials",
        actorType: "staff",
      },
      headers: {
        "set-cookie":
          `vv_staff_session=signed-out; Path=/; HttpOnly; SameSite=Lax; Max-Age=0${cookieSecuritySuffix()}`,
      },
    };
  }

  if (path === "/v1/staff" || path.startsWith("/v1/staff/")) {
    const staffSession = requireStaffCredentialSession(
      request,
      staffAuthStore,
      tenant.slug,
    );
    const staff = store
      .snapshot()
      .staff.members.find(
        (member) => member.id === staffSession.account.staffId,
      );
    if (!staff) {
      throw notFound(
        "STAFF_PREVIEW_NOT_CONFIGURED",
        "No staff preview identity is configured",
      );
    }
    if (method === "GET" && path === "/v1/staff/action-center") {
      const body = await store.transact((draft, transaction) => {
        const created = ensureStaffDocumentWorkItems(draft, clock());
        if (created === 0) transaction.skipWrite();
        return buildStaffActionCenter(draft);
      });
      return { body };
    }
    if (method === "GET" && path === "/v1/staff/workspace") {
      const body = await store.transact((draft, transaction) => {
        const created = ensureStaffDocumentWorkItems(draft, clock());
        if (created === 0) transaction.skipWrite();
        return buildStaffOperationsWorkspace(draft, staff.id);
      });
      return { body };
    }
    const configurationMatch = path.match(
      /^\/v1\/staff\/configurations\/(journeys|campus_life|academics)$/,
    );
    if (method === "GET" && configurationMatch) {
      return {
        body: getManagedConfiguration(
          store.snapshot(),
          configurationMatch[1],
        ),
      };
    }
    if (method === "PUT" && configurationMatch) {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        updateManagedConfiguration(
          draft,
          configurationMatch[1],
          body,
          staff.name,
          clock(),
        ),
      );
      return { body: result };
    }
    if (
      method === "POST" &&
      path === "/v1/staff/edward/configuration-draft"
    ) {
      const body = await readJson(request);
      return {
        body: draftManagedConfigurationWithEdward(
          store.snapshot(),
          body.kind,
          body,
        ),
      };
    }
    const knowledgeMatch = path.match(
      /^\/v1\/staff\/knowledge-base\/([^/]+)$/,
    );
    if (method === "POST" && path === "/v1/staff/knowledge-base") {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        createStaffKnowledgeCard(draft, body, staff.id, clock()),
      );
      return { status: 201, body: result };
    }
    if (method === "PATCH" && knowledgeMatch) {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        updateStaffKnowledgeCard(
          draft,
          decodeURIComponent(knowledgeMatch[1]),
          body,
          clock(),
        ),
      );
      return { body: result };
    }
    const corePlayMatch = path.match(/^\/v1\/staff\/core-plays\/([^/]+)$/);
    if (method === "POST" && path === "/v1/staff/core-plays") {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        createStaffCorePlay(draft, body, staff.id, clock()),
      );
      return { status: 201, body: result };
    }
    if (method === "PATCH" && corePlayMatch) {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        updateStaffCorePlay(
          draft,
          decodeURIComponent(corePlayMatch[1]),
          body,
          clock(),
        ),
      );
      return { body: result };
    }
    const inquiryMatch = path.match(/^\/v1\/staff\/inquiries\/([^/]+)$/);
    if (method === "PATCH" && inquiryMatch) {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        updateStaffInquiry(
          draft,
          decodeURIComponent(inquiryMatch[1]),
          body,
          staff.id,
          clock(),
        ),
      );
      return { body: result };
    }
    const clubMatch = path.match(
      /^\/v1\/staff\/campus-life\/clubs\/([^/]+)$/,
    );
    if (method === "POST" && path === "/v1/staff/campus-life/clubs") {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        createStaffClub(draft, body, clock()),
      );
      return { status: 201, body: result };
    }
    if (method === "PATCH" && clubMatch) {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        updateStaffClub(
          draft,
          decodeURIComponent(clubMatch[1]),
          body,
          clock(),
        ),
      );
      return { body: result };
    }
    if (method === "POST" && path === "/v1/staff/outreach/simulate") {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        simulateStaffOutreach(draft, body, staff.id, clock()),
      );
      return { status: 201, body: result };
    }
    if (method === "POST" && path === "/v1/staff/edward/preview") {
      return { body: previewStaffEdward(await readJson(request)) };
    }
    const workItemMatch = path.match(/^\/v1\/staff\/work-items\/([^/]+)$/);
    if (method === "PATCH" && workItemMatch) {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        updateStaffWorkItem(
          draft,
          decodeURIComponent(workItemMatch[1]),
          body,
          staff.id,
          clock(),
        ),
      );
      return { body: result };
    }
    const preferenceMatch = path.match(
      /^\/v1\/staff\/students\/([^/]+)\/preferences$/,
    );
    if (method === "PATCH" && preferenceMatch) {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        updateStaffStudentPreferences(
          draft,
          decodeURIComponent(preferenceMatch[1]),
          body,
          staff.id,
          clock(),
        ),
      );
      return { body: result };
    }
    const studentMatch = path.match(/^\/v1\/staff\/students\/([^/]+)$/);
    if (method === "GET" && studentMatch) {
      return {
        body: buildStaffStudentRecord(
          store.snapshot(),
          decodeURIComponent(studentMatch[1]),
        ),
      };
    }
    const decisionMatch = path.match(
      /^\/v1\/staff\/documents\/([^/]+)\/decision$/,
    );
    if (method === "POST" && decisionMatch) {
      const body = await readJson(request);
      const result = await store.transact((draft) =>
        reviewStaffDocument(
          draft,
          decodeURIComponent(decisionMatch[1]),
          body,
          staff.id,
          clock(),
        ),
      );
      return { body: result };
    }
    throw notFound("STAFF_ROUTE_NOT_FOUND", "The staff route was not found");
  }

  const credentialSession = credentialSessionFor(
    request,
    authStore,
    tenant.slug,
  );
  if (credentialSession) {
    store = await studentStores.get(credentialSession.account);
  } else {
    requireDemoSession(request);
  }

  if (
    method === "GET" &&
    (path === "/v1/bootstrap" || path === "/v1/student/bootstrap")
  ) {
    const body = await store.transact((draft, transaction) => {
      const awarded = reconcileAuthoritativeRewards(draft, clock());
      if (awarded === 0) transaction.skipWrite();
      return buildBootstrap(draft, clock);
    });
    return { body };
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
    const result = await materialWrite({
      request,
      store,
      operation: "onboarding.complete",
      body,
      mutate: (draft) => completeOnboarding(draft, body, clock()),
    });
    await ensureOnboardingSignedDocuments(store, tenant, clock);
    return result;
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
    await store.transact((draft, transaction) => {
      const expired = expireStaleDocumentExtractions(draft, clock());
      if (expired === 0) transaction.skipWrite();
      return expired;
    });
    await ensureOnboardingSignedDocuments(store, tenant, clock);
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
        uploadBundleId: upload.uploadBundleId,
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
            uploadBundleId: upload.uploadBundleId,
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
    const state = store.snapshot();
    const studentContext = buildAssistantContext(
      state,
      body.message,
      pageContext,
    );
    const guarded = guardedEdwardResponse(body.message, studentContext);
    if (guarded) return { body: guarded };
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
  const allowedFields = new Set([
    "file",
    "category",
    "requirementId",
    "uploadBundleId",
  ]);
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
  const uploadBundleIds = form.getAll("uploadBundleId");
  if (files.length !== 1) {
    throw badRequest(
      "ONE_FILE_PER_UPLOAD",
      "Upload exactly one document per request",
    );
  }
  if (
    categories.length > 1 ||
    requirementIds.length > 1 ||
    uploadBundleIds.length > 1
  ) {
    throw badRequest(
      "DUPLICATE_MULTIPART_FIELD",
      "Category, requirement, and upload bundle context may only be provided once",
    );
  }
  const [file] = files;
  const category = categories.length === 1 ? categories[0] : null;
  const requirementId =
    requirementIds.length === 1 ? requirementIds[0] : null;
  const uploadBundleId =
    uploadBundleIds.length === 1 ? uploadBundleIds[0] : null;
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
  if (
    uploadBundleId !== null &&
    (typeof uploadBundleId !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
        uploadBundleId,
      ))
  ) {
    throw badRequest("INVALID_UPLOAD_BUNDLE_ID", "The upload bundle is invalid");
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
    uploadBundleId:
      typeof uploadBundleId === "string" ? uploadBundleId : undefined,
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

function credentialSessionFor(request, authStore, tenantSlug) {
  const cookies = parseCookies(request.headers.cookie);
  return authStore.getSession(cookies.vv_session, tenantSlug);
}

function staffCredentialSessionFor(request, authStore, tenantSlug) {
  const cookies = parseCookies(request.headers.cookie);
  return authStore.getSession(cookies.vv_staff_session, tenantSlug);
}

function requireStaffCredentialSession(request, authStore, tenantSlug) {
  const session = staffCredentialSessionFor(
    request,
    authStore,
    tenantSlug,
  );
  if (!session) {
    throw unauthorized("Sign in with a staff account to continue");
  }
  return session;
}

async function ensureOnboardingSignedDocuments(store, tenant, clock) {
  const snapshot = store.snapshot();
  const onboarding = snapshot.onboarding;
  const data = onboarding.data;
  if (
    onboarding.status !== "completed" ||
    !onboarding.completedAt ||
    data.signatureConsent !== true ||
    !data.signatureFullName ||
    !data.signatureMethod ||
    !Array.isArray(data.signedDocumentIds)
  ) {
    return 0;
  }
  const existing = new Set(
    snapshot.documents
      .filter(
        (document) =>
          document.signature?.onboardingVersion === onboarding.version,
      )
      .map((document) => document.signature?.templateCode),
  );
  const requested = new Set(data.signedDocumentIds);
  let created = 0;
  for (const template of onboardingDocumentTemplates) {
    if (!requested.has(template.code) || existing.has(template.code)) continue;
    const id = deterministicSignedDocumentId(
      `${tenant.id}:${snapshot.profile.studentId}:${template.code}:${onboarding.version}`,
    );
    const bytes = await createSignedOnboardingPdf({
      templateBytes: await readOnboardingTemplate(
        `${tenant.slug}-${template.sourceSuffix}`,
      ),
      signerName: data.signatureFullName,
      signatureMethod: data.signatureMethod,
      ...(data.signatureMethod === "drawn"
        ? { signatureImageData: data.signatureImageData }
        : {}),
      signedAt: onboarding.completedAt,
      auditReceipt:
        `onboarding.${onboarding.version}.${template.code}.${snapshot.profile.studentId}`,
      signatureBox: template.signatureBox,
    });
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    const storageKey = `${id}.pdf`;
    await store.writeUpload(storageKey, bytes);
    await store.transact((draft, transaction) => {
      if (
        draft.documents.some(
          (document) =>
            document.signature?.templateCode === template.code &&
            document.signature?.onboardingVersion === onboarding.version,
        )
      ) {
        transaction.skipWrite();
        return false;
      }
      draft.documents.push({
        id,
        fileName: template.fileName,
        mimeType: "application/pdf",
        sizeBytes: bytes.length,
        category: "other",
        processingMode: "generated",
        status: "accepted",
        storageKey,
        contentStored: true,
        sha256,
        signature: {
          templateCode: template.code,
          title: template.title,
          signerName: data.signatureFullName,
          method: data.signatureMethod,
          signedAt: onboarding.completedAt,
          onboardingVersion: onboarding.version,
        },
        createdAt: onboarding.completedAt ?? clock().toISOString(),
      });
      return true;
    });
    existing.add(template.code);
    created += 1;
  }
  return created;
}

async function readOnboardingTemplate(fileName) {
  const roots = [
    process.env.ONBOARDING_DOCUMENT_TEMPLATE_DIR,
    join(process.cwd(), "apps", "web", "public", "documents", "onboarding"),
    join(process.cwd(), "..", "web", "public", "documents", "onboarding"),
    join(
      process.cwd(),
      "..",
      "..",
      "apps",
      "web",
      "public",
      "documents",
      "onboarding",
    ),
  ].filter(Boolean);
  let missing;
  for (const root of roots) {
    try {
      return await readFile(join(root, fileName));
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      missing = error;
    }
  }
  throw missing ?? new Error(`Missing onboarding template ${fileName}`);
}

function deterministicSignedDocumentId(value) {
  const bytes = createHash("sha256").update(value).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 0x0f) | 0x50;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join("-");
}

function resolveRequestTenant(request) {
  const rawSlug = request.headers["x-tenant-slug"];
  const headerSlug = Array.isArray(rawSlug) ? rawSlug[0] : rawSlug;
  const url = new URL(request.url ?? "/", "http://localhost");
  const isDocumentMediaRequest =
    /^\/v1\/student\/documents\/[0-9a-f-]+\/(?:content|profile-photo)$/i.test(
      url.pathname,
    );
  const querySlug =
    headerSlug === undefined && isDocumentMediaRequest
      ? url.searchParams.get("tenant") ?? undefined
      : undefined;
  const slug = headerSlug ?? querySlug;
  if (slug === undefined || slug === "") return demoTenants.aster;
  const tenant = tenantConfigForSlug(slug);
  if (!tenant) {
    throw badRequest(
      "UNKNOWN_TENANT",
      "The requested university is not configured",
    );
  }
  return tenant;
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

function staffCredentialSessionCookie(token, expiresAt) {
  const maxAge = Math.max(
    0,
    Math.floor((Date.parse(expiresAt) - Date.now()) / 1_000),
  );
  return `vv_staff_session=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}; Priority=High${cookieSecuritySuffix()}`;
}

function staffSessionResponse(account) {
  return {
    authenticated: true,
    mode: "credentials",
    actorType: "staff",
    staff: {
      id: account.staffId,
      name: account.displayName,
      email: account.email,
      component: account.component,
    },
    notice:
      "Authenticated local staff session. Institutional deployments should replace this adapter with university SSO while preserving the same role boundary.",
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
    "Content-Type, Idempotency-Key, X-Request-Id, X-Tenant-Slug, X-Demo-Actor-Type",
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

function buildAssistantContext(state, message = "", pageContext = "/dashboard") {
  const normalized = `${message} ${pageContext}`.toLowerCase();
  const wantsDocuments =
    /document|upload|transcript|fafsa|ferpa|verification/.test(normalized);
  const wantsOnboarding =
    /onboarding|offer|housing|roommate|emergency contact|sign/.test(normalized);
  const wantsPayments =
    /payment|deposit|pay|balance|billing|financial|aid|loan/.test(normalized);
  const wantsAcademics =
    /academic|class|classroom|course|catalog|major|program|prerequisite|exempt|credit/.test(
      normalized,
    );
  const wantsFinancials =
    /financial|aid|fafsa|loan|award|balance|billing|payment plan|sap/.test(
      normalized,
    );
  const wantsMessages = /message|inbox|notification|unread/.test(normalized);
  const wantsCampus =
    /campus|club|event|activity|activities|organization|community|social life/.test(
      normalized,
    );
  const dashboard = buildDashboard(state);
  const contextReceipts = [{ source: "dashboard" }, { source: "profile" }];
  const context = {
    universityName: state.tenant?.name ?? "the university",
    universityShortName: state.tenant?.shortName ?? "the university",
    contextReceipts,
    preferredName: state.profile.preferredName,
    programName: state.offer.programName,
    termName: state.offer.termName,
    enrollmentChecklistCompletionPercent: dashboard.journey.completionPercent,
    nextAction: dashboard.journey.nextAction,
    offerId: state.offer.id,
    depositAmountCents: state.offer.depositAmountCents,
    depositPaid: state.payments.some(
      (payment) =>
        payment.type === "enrollment_deposit" &&
        payment.status === "succeeded",
    ),
  };
  if (wantsDocuments) {
    contextReceipts.push({ source: "documents" });
    context.documentStatuses = state.documents.slice(0, 24).map((document) => ({
      category: document.category,
      status: document.status,
    }));
  }
  if (wantsOnboarding) {
    contextReceipts.push({ source: "onboarding" });
    context.onboardingStatus = state.onboarding.status;
    context.housingPreference =
      state.onboarding.data?.housingPreference ?? null;
  }
  if (wantsPayments) {
    contextReceipts.push({ source: "payments" });
  }
  if (wantsMessages) {
    contextReceipts.push({ source: "messages" });
    context.unreadMessages = state.messages.filter(
      (studentMessage) => studentMessage.readAt === null,
    ).length;
  }
  if (wantsAcademics) {
    const academics = buildStudentAcademics(state);
    contextReceipts.push({ source: "academics" });
    context.academicSummary = {
      selectedProgram: academics.selectedProgram.name,
      degree: academics.selectedProgram.degree,
      catalogVersion: academics.catalogVersion,
      suggestedExemptions: academics.exemptionRecommendations.map(
        (recommendation) => recommendation.targetCourseCode,
      ),
      plan: academics.plan.slice(0, 16).map((item) => ({
        code: item.course.code,
        title: item.course.title,
        recommendedTerm: item.recommendedTerm,
        status: item.status,
        missingPrerequisites: item.missingPrerequisiteCodes,
      })),
    };
  }
  if (wantsFinancials) {
    const financials = buildStudentFinancials(state);
    contextReceipts.push({ source: "financials" });
    context.financialSummary = {
      remainingBalanceCents: financials.remainingBalanceCents,
      acceptedAidCents: financials.acceptedAidCents,
      actionRequiredDocuments: financials.requiredDocuments
        .filter((document) => document.status === "action_required")
        .map((document) => document.code),
      sapStatus: financials.sap.status,
    };
  }
  if (wantsCampus) {
    const campusLife = buildCampusLife(state);
    contextReceipts.push({ source: "campus_life" });
    context.campusLifeSummary = {
      upcomingEvents: campusLife.events.slice(0, 8).map((event) => ({
        title: event.title,
        startsAt: event.startsAt,
        location: event.location,
        category: event.category,
      })),
      clubs: campusLife.clubs.slice(0, 16).map((club) => ({
        name: club.name,
        category: club.category,
        description: club.description,
        nextActivity: club.nextActivity,
      })),
    };
  }
  return context;
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
  const message = String(error?.message ?? "").toLowerCase();
  return (
    failure.failureCode === "provider_unavailable" &&
    Number(error?.status) !== 413 &&
    !/tokens per minute|request too large|rate_limit_exceeded/.test(message) &&
    !/invalid json|incomplete json|no valid json/.test(message)
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
    status === 413 ||
    status >= 500 ||
    /rate limit|rate_limit_exceeded|tokens per minute|request too large|provider returned error|temporar(?:y|ily)|unavailable|fetch failed|econn|enotfound/.test(
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
