export interface DomainEventActor {
  type: string;
  id: string | null;
}

export interface DomainEventEnvelope {
  eventId: string;
  eventName: string;
  occurredAt: Date;
  tenantId: string;
  aggregateType: string;
  aggregateId: string;
  aggregateVersion: number | null;
  actor: DomainEventActor | null;
  correlationId: string | null;
  causationId: string | null;
  data: Record<string, unknown>;
}

export interface ClaimedOutboxEvent extends DomainEventEnvelope {
  outboxId: string;
  attempts: number;
}

export interface OutboxEventRow {
  id: unknown;
  event_id?: unknown;
  event_name?: unknown;
  event_type?: unknown;
  occurred_at?: unknown;
  created_at?: unknown;
  tenant_id?: unknown;
  aggregate_type?: unknown;
  aggregate_id?: unknown;
  aggregate_version?: unknown;
  actor?: unknown;
  actor_type?: unknown;
  actor_id?: unknown;
  correlation_id?: unknown;
  causation_id?: unknown;
  payload?: unknown;
  data?: unknown;
  attempts?: unknown;
  attempt_count?: unknown;
}

export type EventHandler = (event: DomainEventEnvelope) => Promise<void>;

export interface Logger {
  debug(message: string, fields?: Record<string, unknown>): void;
  info(message: string, fields?: Record<string, unknown>): void;
  warn(message: string, fields?: Record<string, unknown>): void;
  error(message: string, fields?: Record<string, unknown>): void;
}
