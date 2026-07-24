import {
  context as otelContext,
  SpanKind,
  SpanStatusCode,
  trace,
  type Span,
} from "@opentelemetry/api";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import type {
  CallHandler,
  ExecutionContext,
} from "@nestjs/common";
import type { FastifyReply, FastifyRequest } from "fastify";
import type { Observable } from "rxjs";
import { finalize, tap } from "rxjs";

let telemetryInitialized = false;

export function initializeTelemetry(): void {
  if (telemetryInitialized) return;
  new NodeTracerProvider().register();
  telemetryInitialized = true;
}

export function traceHttpRequest(
  executionContext: ExecutionContext,
  next: CallHandler,
): Observable<unknown> {
  const http = executionContext.switchToHttp();
  const request = http.getRequest<FastifyRequest>();
  const reply = http.getResponse<FastifyReply>();
  const correlationId = request.id;
  const route =
    request.routeOptions?.url ??
    request.url.split("?")[0] ??
    "unknown-route";
  const tracer = trace.getTracer("vv.student-api", "0.1.0");
  const span = tracer.startSpan(`${request.method} ${route}`, {
    kind: SpanKind.SERVER,
    attributes: {
      "http.request.method": request.method,
      "url.path": route,
      "vv.correlation_id": correlationId,
    },
  });

  setResponseCorrelationHeaders(reply, correlationId, span);
  const activeContext = trace.setSpan(otelContext.active(), span);
  return otelContext.with(activeContext, () =>
    next.handle().pipe(
      tap({
        error: (error: unknown) => {
          span.recordException(normalizeException(error));
          span.setStatus({
            code: SpanStatusCode.ERROR,
            message:
              error instanceof Error ? error.message : "Unhandled request error",
          });
        },
        complete: () => {
          span.setStatus({ code: SpanStatusCode.OK });
        },
      }),
      finalize(() => span.end()),
    ),
  );
}

function setResponseCorrelationHeaders(
  reply: FastifyReply,
  correlationId: string,
  span: Span,
): void {
  void reply.header("x-request-id", correlationId);
  void reply.header("x-correlation-id", correlationId);
  void reply.header("x-trace-id", span.spanContext().traceId);
}

function normalizeException(error: unknown): Error {
  if (error instanceof Error) return error;
  return new Error(typeof error === "string" ? error : "Unknown request error");
}
