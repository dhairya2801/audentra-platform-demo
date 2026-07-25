import { createHash, randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { extname } from "node:path";
import {
  acceptOffer,
  buildBootstrap,
  buildCampusLife,
  buildDashboard,
  buildOnboarding,
  buildStudentAcademics,
  buildStudentFinancials,
  completeOnboarding,
  createAppointment,
  createDepositPayment,
  createDocumentMetadata,
  createUploadedDocument,
  confirmDocumentExtraction,
  createHelpRequest,
  findDocumentForDownload,
  fixtureSummary,
  getHelpTopics,
  idempotentMutation,
  ingestActivities,
  listMessages,
  listDocuments,
  listCatalogCourses,
  listRequirements,
  markMessageRead,
  patchProfile,
  profileResponse,
  requirementDetail,
  selectFinancialPaymentPlan,
  updateOnboarding,
} from "./domain.js";
import {
  HttpError,
  badRequest,
  notFound,
  unauthorized,
} from "./errors.js";
import { JsonStateStore } from "./store.js";
import { createOpenRouterGatewayFromEnv } from "./openrouter.js";
import {
  exactKeys,
  objectBody,
  requireIdempotencyKey,
} from "./validation.js";

const requestIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const maximumBodyBytes = 262_144;
const maximumUploadBytes = 10_485_760;
const maximumMultipartBytes = maximumUploadBytes + 65_536;

export async function createDemoApi(options = {}) {
  const store =
    options.store ??
    new JsonStateStore(
      options.dataFile,
      options.clock ?? (() => new Date()),
    );
  await store.initialize();
  const clock = options.clock ?? (() => new Date());
  const logger = options.logger === undefined ? console : options.logger;
  const ai =
    options.ai ??
    createOpenRouterGatewayFromEnv({
      apiKey: options.openRouterApiKey,
      model: options.openRouterModel,
      fetch: options.fetch,
    });
  const allowedOrigins = new Set(
    options.allowedOrigins ?? [
      "http://localhost:3000",
      "http://127.0.0.1:3000",
    ],
  );

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

  return { server, store };
}

async function route({ request, store, clock, ai }) {
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
    const authenticated = hasDemoSession(request);
    return {
      body: authenticated
        ? sessionResponse(store.snapshot())
        : { authenticated: false, mode: "demo" },
    };
  }
  if (method === "POST" && path === "/v1/auth/demo/sign-in") {
    const body = await readJson(request);
    exactKeys(objectBody(body), []);
    return {
      body: sessionResponse(store.snapshot()),
      headers: {
        "set-cookie":
          "vv_demo_session=demo-session; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400",
      },
    };
  }
  if (method === "POST" && path === "/v1/auth/demo/sign-out") {
    return {
      body: { authenticated: false, mode: "demo" },
      headers: {
        "set-cookie":
          "vv_demo_session=signed-out; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400",
      },
    };
  }

  requireDemoSession(request);

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
    const result = await materialWrite({
      request,
      store,
      operation: "document.upload",
      body: {
        fileName: upload.fileName,
        mimeType: upload.mimeType,
        sizeBytes: upload.bytes.length,
        category: upload.category,
        requirementId: upload.requirementId,
        sha256: digest,
      },
      mutate: async (draft) => {
        if (
          upload.requirementId &&
          !draft.requirements.some(
            (requirement) =>
              requirement.id === upload.requirementId &&
              requirement.submissionType === "document",
          )
        ) {
          throw badRequest(
            "DOCUMENT_REQUIREMENT_NOT_FOUND",
            "The document requirement was not found",
          );
        }
        const id = randomUUID();
        const storageKey = `${id}${safeExtension(upload.fileName, upload.mimeType)}`;
        await store.writeUpload(storageKey, upload.bytes);
        let extraction;
        try {
          extraction = await ai.extractStudentDocument({
            fileName: upload.fileName,
            mimeType: upload.mimeType,
            bytes: upload.bytes,
            expectedDocumentType: documentTypeForCategory(upload.category),
          });
          const expectedDocumentType = documentTypeForCategory(upload.category);
          if (
            upload.requirementId &&
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
          extraction = failedExtraction(error, upload.fileName, clock());
        }
        return createUploadedDocument(
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
            extraction,
          },
          clock(),
        );
      },
    });
    return { ...result, status: 201 };
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
    validateEdwardInput(body);
    const state = store.snapshot();
    return {
      body: await ai.askEdward({
        message: body.message,
        pageContext: body.pageContext,
        history: body.history,
        studentContext: buildAssistantContext(state),
      }),
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
  const file = form.get("file");
  const category = form.get("category");
  const requirementId = form.get("requirementId");
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
  return cookies.vv_demo_session === "demo-session";
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
}

function buildAssistantContext(state) {
  const academics = buildStudentAcademics(state);
  const financials = buildStudentFinancials(state);
  return {
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

function failedExtraction(error, fileName, now) {
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
    warnings: [
      "The parsing provider could not complete this attempt. The original file is still available.",
    ],
    model: null,
    provider: "local",
    processedAt: now.toISOString(),
    verifiedAt: null,
  };
}
