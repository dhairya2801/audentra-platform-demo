import type {
  DomainEventEnvelope,
  EventHandler,
  Logger,
} from "./types.js";

export type DispatchOutcome = "handled" | "ignored";

export const portalEventsWithoutWorkerProjection = [
  "admission.offer_accepted.v1",
  "student.onboarding_completed.v1",
  "document.placeholder_created.v1",
  "document.storage_failed.v1",
  "document.extraction_retry_started.v1",
  "document.extraction_completed.v1",
  "student.appointment_scheduled.v1",
  "payment.deposit_succeeded.v1",
  "student.profile_updated.v1",
] as const;

export class EventDispatcher {
  private readonly handlers = new Map<string, EventHandler | null>();

  public constructor(private readonly logger: Logger) {}

  public register(eventName: string, handler: EventHandler): this {
    if (this.handlers.has(eventName)) {
      throw new Error(`Handler already registered for ${eventName}`);
    }
    this.handlers.set(eventName, handler);
    return this;
  }

  public registerIgnored(eventName: string): this {
    if (this.handlers.has(eventName)) {
      throw new Error(`Handler already registered for ${eventName}`);
    }
    this.handlers.set(eventName, null);
    return this;
  }

  public async dispatch(event: DomainEventEnvelope): Promise<DispatchOutcome> {
    if (!this.handlers.has(event.eventName)) {
      throw new Error(`No outbox handler registered for ${event.eventName}`);
    }

    const handler = this.handlers.get(event.eventName);
    if (handler === null || handler === undefined) {
      this.logger.info("outbox_event_ignored", {
        eventId: event.eventId,
        eventName: event.eventName,
      });
      return "ignored";
    }

    await handler(event);
    return "handled";
  }
}
