import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import {
  acceptOffer,
  buildBootstrap,
  buildDashboard,
  buildOnboarding,
  completeOnboarding,
  createAppointment,
  createDepositPayment,
  createDocumentMetadata,
  createHelpRequest,
  fixtureSummary,
  getHelpTopics,
  idempotentMutation,
  ingestActivities,
  listMessages,
  listRequirements,
  markMessageRead,
  patchProfile,
  profileResponse,
  requirementDetail,
  updateOnboarding,
} from "./domain.js";
import {
  HttpError,
  badRequest,
  notFound,
  unauthorized,
} from "./errors.js";
import { JsonStateStore } from "./store.js";
import {
  exactKeys,
  objectBody,
  requireIdempotencyKey,
} from "./validation.js";

const requestIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const maximumBodyBytes = 262_144;

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
      });
      if (result.headers) {
        for (const [name, value] of Object.entries(result.headers)) {
          response.setHeader(name, value);
        }
      }
      sendJson(response, result.status ?? 200, result.body);
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

async function route({ request, store, clock }) {
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
    const items = store
      .snapshot()
      .documents.toSorted(
        (left, right) =>
          right.createdAt.localeCompare(left.createdAt) ||
          left.id.localeCompare(right.id),
      );
    return { body: { items, total: items.length } };
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
  const contentLength = Number(request.headers["content-length"] ?? 0);
  if (contentLength > maximumBodyBytes) {
    throw new HttpError(
      413,
      "REQUEST_TOO_LARGE",
      `Request bodies are limited to ${maximumBodyBytes} bytes`,
    );
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > maximumBodyBytes) {
      throw new HttpError(
        413,
        "REQUEST_TOO_LARGE",
        `Request bodies are limited to ${maximumBodyBytes} bytes`,
      );
    }
    chunks.push(chunk);
  }
  if (size === 0) return {};
  const contentType = request.headers["content-type"] ?? "";
  if (!String(contentType).toLowerCase().startsWith("application/json")) {
    throw new HttpError(
      415,
      "JSON_REQUIRED",
      "Content-Type must be application/json",
    );
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw badRequest("INVALID_JSON", "The request body is not valid JSON");
  }
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
