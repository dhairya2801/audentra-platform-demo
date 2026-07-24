import type {
  ClaimedOutboxEvent,
  DomainEventActor,
  OutboxEventRow,
} from "./types.js";

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Invalid outbox event: ${field} must be a non-empty string`);
  }
  return value;
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function dateValue(value: unknown, field: string): Date {
  const result = value instanceof Date ? value : new Date(requiredString(value, field));
  if (Number.isNaN(result.getTime())) {
    throw new Error(`Invalid outbox event: ${field} is not a valid timestamp`);
  }
  return result;
}

function nonNegativeInteger(value: unknown, field: string): number {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isSafeInteger(numeric) || numeric < 0) {
    throw new Error(`Invalid outbox event: ${field} must be a non-negative integer`);
  }
  return numeric;
}

function objectValue(
  value: unknown,
  field: string,
): Record<string, unknown> {
  let parsed = value;
  if (typeof parsed === "string") {
    try {
      parsed = JSON.parse(parsed) as unknown;
    } catch {
      throw new Error(`Invalid outbox event: ${field} contains malformed JSON`);
    }
  }
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    Array.isArray(parsed)
  ) {
    throw new Error(`Invalid outbox event: ${field} must be a JSON object`);
  }
  return parsed as Record<string, unknown>;
}

function actorValue(value: unknown): DomainEventActor | null {
  if (value === undefined || value === null) {
    return null;
  }
  const actor = objectValue(value, "actor");
  return {
    type: requiredString(actor.type, "actor.type"),
    id: nullableString(actor.id),
  };
}

export function parseOutboxEvent(row: OutboxEventRow): ClaimedOutboxEvent {
  const payload = objectValue(row.payload ?? row.data ?? {}, "payload");
  const embeddedData =
    payload.data === undefined
      ? payload
      : objectValue(payload.data, "payload.data");
  const occurredAt =
    row.occurred_at ?? payload.occurredAt ?? payload.occurred_at ?? row.created_at;

  return {
    outboxId: requiredString(row.id, "id"),
    eventId: requiredString(
      row.event_id ?? payload.eventId ?? payload.event_id ?? row.id,
      "event_id",
    ),
    eventName: requiredString(
      row.event_name ??
        row.event_type ??
        payload.eventName ??
        payload.event_name,
      "event_name",
    ),
    occurredAt: dateValue(occurredAt, "occurred_at"),
    tenantId: requiredString(
      row.tenant_id ?? payload.tenantId ?? payload.tenant_id,
      "tenant_id",
    ),
    aggregateType: requiredString(
      row.aggregate_type ??
        payload.aggregateType ??
        payload.aggregate_type,
      "aggregate_type",
    ),
    aggregateId: requiredString(
      row.aggregate_id ?? payload.aggregateId ?? payload.aggregate_id,
      "aggregate_id",
    ),
    aggregateVersion:
      row.aggregate_version === undefined &&
      payload.aggregateVersion === undefined &&
      payload.aggregate_version === undefined
        ? null
        : nonNegativeInteger(
            row.aggregate_version ??
              payload.aggregateVersion ??
              payload.aggregate_version,
            "aggregate_version",
          ),
    actor:
      row.actor_type === undefined
        ? actorValue(row.actor ?? payload.actor)
        : {
            type: requiredString(row.actor_type, "actor_type"),
            id: nullableString(row.actor_id),
          },
    correlationId: nullableString(
      row.correlation_id ?? payload.correlationId ?? payload.correlation_id,
    ),
    causationId: nullableString(
      row.causation_id ?? payload.causationId ?? payload.causation_id,
    ),
    data: embeddedData,
    attempts: nonNegativeInteger(
      row.attempts ?? row.attempt_count ?? 0,
      "attempts",
    ),
  };
}

export function getEventString(
  data: Record<string, unknown>,
  ...keys: string[]
): string | null {
  for (const key of keys) {
    const value = data[key];
    if (typeof value === "string" && value.trim() !== "") {
      return value;
    }
  }
  return null;
}
