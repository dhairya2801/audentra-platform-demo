import type { EventDispatcher } from "./dispatcher.js";
import type { OutboxRepository } from "./outbox-repository.js";
import type { ClaimedOutboxEvent, Logger } from "./types.js";

export interface WorkerStatus {
  startedAt: Date;
  stopping: boolean;
  polling: boolean;
  inFlight: number;
  processed: number;
  failed: number;
  lastSuccessfulPollAt: Date | null;
  lastPollError: string | null;
}

export class WorkerService {
  private readonly abortController = new AbortController();
  private readonly status: WorkerStatus = {
    startedAt: new Date(),
    stopping: false,
    polling: false,
    inFlight: 0,
    processed: 0,
    failed: 0,
    lastSuccessfulPollAt: null,
    lastPollError: null,
  };

  public constructor(
    private readonly repository: OutboxRepository,
    private readonly dispatcher: EventDispatcher,
    private readonly pollIntervalMs: number,
    private readonly logger: Logger,
  ) {}

  public snapshot(): Readonly<WorkerStatus> {
    return { ...this.status };
  }

  public async run(): Promise<void> {
    this.logger.info("worker_poll_loop_started", {
      pollIntervalMs: this.pollIntervalMs,
    });

    while (!this.status.stopping) {
      this.status.polling = true;
      try {
        const batch = await this.repository.claimBatch();
        this.status.lastSuccessfulPollAt = new Date();
        this.status.lastPollError = null;
        this.status.polling = false;

        for (const rejected of batch.rejected) {
          this.status.failed += 1;
          const failure = await this.repository.rejectMalformed(rejected);
          this.logger.error("malformed_outbox_event_rejected", {
            outboxId: rejected.outboxId,
            attempts: failure.attempts,
            retryAt: failure.nextAttemptAt.toISOString(),
            deadLettered: failure.deadLettered,
            failureRecorded: failure.recorded,
            error: rejected.error,
          });
        }

        if (batch.events.length === 0) {
          await interruptibleDelay(
            this.pollIntervalMs,
            this.abortController.signal,
          );
          continue;
        }

        this.logger.debug("outbox_batch_claimed", {
          count: batch.events.length,
          malformed: batch.rejected.length,
        });
        for (const event of batch.events) {
          if (this.status.stopping) {
            break;
          }
          await this.process(event);
        }
      } catch (error) {
        this.status.polling = false;
        this.status.lastPollError =
          error instanceof Error ? error.message : String(error);
        this.logger.error("worker_poll_failed", { error });
        await interruptibleDelay(
          this.pollIntervalMs,
          this.abortController.signal,
        );
      }
    }

    this.logger.info("worker_poll_loop_stopped");
  }

  public stop(): void {
    if (this.status.stopping) {
      return;
    }
    this.status.stopping = true;
    this.abortController.abort();
    this.logger.info("worker_stop_requested", {
      inFlight: this.status.inFlight,
    });
  }

  private async process(event: ClaimedOutboxEvent): Promise<void> {
    this.status.inFlight += 1;
    const startedAt = performance.now();
    try {
      const outcome = await this.dispatcher.dispatch(event);
      const completed = await this.repository.complete(event);
      if (!completed) {
        this.logger.warn("outbox_event_acknowledgement_lost", {
          eventId: event.eventId,
          outboxId: event.outboxId,
        });
        return;
      }
      this.status.processed += 1;
      this.logger.info("outbox_event_processed", {
        eventId: event.eventId,
        eventName: event.eventName,
        outcome,
        durationMs: Math.round(performance.now() - startedAt),
      });
    } catch (error) {
      this.status.failed += 1;
      try {
        const failure = await this.repository.fail(event, error);
        this.logger.error("outbox_event_failed", {
          eventId: event.eventId,
          eventName: event.eventName,
          attempts: failure.attempts,
          retryAt: failure.nextAttemptAt.toISOString(),
          deadLettered: failure.deadLettered,
          failureRecorded: failure.recorded,
          error,
        });
      } catch (recordingError) {
        this.logger.error("outbox_failure_recording_failed", {
          eventId: event.eventId,
          originalError: error,
          recordingError,
        });
      }
    } finally {
      this.status.inFlight -= 1;
    }
  }
}

async function interruptibleDelay(
  milliseconds: number,
  signal: AbortSignal,
): Promise<void> {
  if (signal.aborted) {
    return;
  }

  await new Promise<void>((resolve) => {
    const timeout = setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timeout);
        resolve();
      },
      { once: true },
    );
  });
}
