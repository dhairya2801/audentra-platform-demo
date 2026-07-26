import type { WorkerConfig } from "./config.js";
import type { Logger, DomainEventEnvelope } from "./types.js";

/**
 * Calls the API's private command after an extraction-request outbox event is
 * committed. The document ID is the aggregate ID, so duplicate deliveries are
 * harmless: the API returns the terminal document without a second LLM call.
 */
export class DocumentExtractionRunner {
  constructor(
    private readonly config: WorkerConfig,
    private readonly logger: Logger,
    private readonly fetchImplementation: typeof fetch = fetch,
  ) {}

  async handle(event: DomainEventEnvelope): Promise<void> {
    const studentId = stringValue(event.data.studentId, "studentId");
    const actorId = event.actor?.id;
    if (!actorId) {
      throw new Error("document extraction event is missing an actor id");
    }
    const commandPath =
      event.eventName === "document.upload_reserved.v1"
        ? "document-extraction-reservations"
        : "document-extractions";
    const url = new URL(
      `/v1/student/internal/${commandPath}/${event.aggregateId}`,
      this.config.apiInternalUrl,
    );
    const response = await this.fetchImplementation(url, {
      method: "POST",
      headers: {
        "x-vv-worker-token": this.config.documentWorkerToken,
        "x-demo-tenant-id": event.tenantId,
        "x-demo-student-id": studentId,
        "x-demo-actor-id": actorId,
        "x-correlation-id": event.correlationId ?? event.eventId,
      },
    });
    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new Error(
        `document extraction command failed with ${response.status}: ${body.slice(0, 400)}`,
      );
    }
    this.logger.info("document_extraction_command_completed", {
      eventId: event.eventId,
      documentId: event.aggregateId,
      status: response.status,
    });
  }
}

function stringValue(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`document extraction event is missing ${field}`);
  }
  return value;
}
