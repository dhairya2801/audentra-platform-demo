import type { WorkerConfig } from "./config.js";
import type { DatabasePool } from "./database.js";
import { inTransaction } from "./database.js";
import { parseOutboxEvent } from "./event-parser.js";
import type { ClaimedOutboxEvent, OutboxEventRow } from "./types.js";

const CLAIM_SQL = `
  WITH candidates AS (
    SELECT id
    FROM public.outbox_event
    WHERE published_at IS NULL
      AND next_attempt_at <= NOW()
      AND attempts < $4
      AND (
        locked_at IS NULL
        OR locked_at < NOW() - ($2::integer * INTERVAL '1 second')
      )
    ORDER BY occurred_at ASC, id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT $3
  )
  UPDATE public.outbox_event AS event
  SET locked_at = NOW(),
      locked_by = $1
  FROM candidates
  WHERE event.id = candidates.id
  RETURNING event.*
`;

export interface OutboxStats {
  pending: number;
  retrying: number;
  deadLettered: number;
  oldestPendingAt: Date | null;
}

export interface FailureResult {
  recorded: boolean;
  deadLettered: boolean;
  attempts: number;
  nextAttemptAt: Date;
}

export interface RejectedOutboxEvent {
  outboxId: string;
  attempts: number;
  error: unknown;
}

export interface ClaimedBatch {
  events: ClaimedOutboxEvent[];
  rejected: RejectedOutboxEvent[];
}

export class OutboxRepository {
  public constructor(
    private readonly pool: DatabasePool,
    private readonly config: WorkerConfig,
  ) {}

  public async claimBatch(): Promise<ClaimedBatch> {
    const rows = await inTransaction(this.pool, async (client) => {
      const result = await client.query<OutboxEventRow>(CLAIM_SQL, [
        this.config.workerId,
        this.config.leaseSeconds,
        this.config.batchSize,
        this.config.maxAttempts,
      ]);
      return result.rows;
    });

    const events: ClaimedOutboxEvent[] = [];
    const rejected: RejectedOutboxEvent[] = [];
    for (const row of rows) {
      try {
        events.push(parseOutboxEvent(row));
      } catch (error) {
        rejected.push({
          outboxId: String(row.id),
          attempts: safeAttemptCount(row.attempts ?? row.attempt_count),
          error,
        });
      }
    }
    return { events, rejected };
  }

  public async complete(event: ClaimedOutboxEvent): Promise<boolean> {
    const result = await this.pool.query(
      `
        UPDATE public.outbox_event
        SET published_at = NOW(),
            locked_at = NULL,
            locked_by = NULL,
            last_error = NULL
        WHERE id = $1
          AND published_at IS NULL
          AND locked_by = $2
      `,
      [event.outboxId, this.config.workerId],
    );
    return result.rowCount === 1;
  }

  public async fail(
    event: ClaimedOutboxEvent,
    error: unknown,
  ): Promise<FailureResult> {
    return this.recordFailure(
      event.outboxId,
      event.attempts,
      event.eventId,
      error,
    );
  }

  public async rejectMalformed(
    event: RejectedOutboxEvent,
  ): Promise<FailureResult> {
    return this.recordFailure(
      event.outboxId,
      event.attempts,
      event.outboxId,
      event.error,
    );
  }

  private async recordFailure(
    outboxId: string,
    previousAttempts: number,
    stableSeed: string,
    error: unknown,
  ): Promise<FailureResult> {
    const attempts = previousAttempts + 1;
    const deadLettered = attempts >= this.config.maxAttempts;
    const delayMs = deadLettered
      ? 100 * 365 * 24 * 60 * 60 * 1_000
      : calculateRetryDelay(
          attempts,
          this.config.baseRetryMs,
          this.config.maxRetryMs,
          stableSeed,
        );
    const nextAttemptAt = new Date(Date.now() + delayMs);
    const message = errorMessage(error).slice(0, 8_000);
    const result = await this.pool.query(
      `
        UPDATE public.outbox_event
        SET attempts = attempts + 1,
            next_attempt_at = $3,
            last_error = $4,
            locked_at = NULL,
            locked_by = NULL
        WHERE id = $1
          AND published_at IS NULL
          AND locked_by = $2
      `,
      [
        outboxId,
        this.config.workerId,
        nextAttemptAt.toISOString(),
        message,
      ],
    );

    return {
      recorded: result.rowCount === 1,
      deadLettered,
      attempts,
      nextAttemptAt,
    };
  }

  public async releaseClaims(): Promise<number> {
    const result = await this.pool.query(
      `
        UPDATE public.outbox_event
        SET locked_at = NULL,
            locked_by = NULL
        WHERE published_at IS NULL
          AND locked_by = $1
      `,
      [this.config.workerId],
    );
    return result.rowCount ?? 0;
  }

  public async ping(): Promise<void> {
    await this.pool.query("SELECT 1 FROM public.outbox_event LIMIT 0");
  }

  public async stats(): Promise<OutboxStats> {
    const result = await this.pool.query<{
      pending: string;
      retrying: string;
      dead_lettered: string;
      oldest_pending_at: Date | null;
    }>(
      `
        SELECT
          COUNT(*) FILTER (
            WHERE published_at IS NULL AND attempts < $1
          )::text AS pending,
          COUNT(*) FILTER (
            WHERE published_at IS NULL AND attempts > 0 AND attempts < $1
          )::text AS retrying,
          COUNT(*) FILTER (
            WHERE published_at IS NULL AND attempts >= $1
          )::text AS dead_lettered,
          MIN(occurred_at) FILTER (
            WHERE published_at IS NULL AND attempts < $1
          ) AS oldest_pending_at
        FROM public.outbox_event
      `,
      [this.config.maxAttempts],
    );
    const row = result.rows[0];
    if (!row) {
      return {
        pending: 0,
        retrying: 0,
        deadLettered: 0,
        oldestPendingAt: null,
      };
    }
    return {
      pending: Number(row.pending),
      retrying: Number(row.retrying),
      deadLettered: Number(row.dead_lettered),
      oldestPendingAt: row.oldest_pending_at,
    };
  }
}

function safeAttemptCount(value: unknown): number {
  const numeric = typeof value === "number" ? value : Number(value ?? 0);
  return Number.isSafeInteger(numeric) && numeric >= 0 ? numeric : 0;
}

export function calculateRetryDelay(
  attempts: number,
  baseRetryMs: number,
  maxRetryMs: number,
  stableSeed: string,
): number {
  const exponent = Math.max(0, attempts - 1);
  const bounded = Math.min(maxRetryMs, baseRetryMs * 2 ** exponent);
  let hash = 0;
  for (const character of stableSeed) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  }
  const jitterFactor = 0.8 + (hash % 401) / 1_000;
  return Math.min(
    maxRetryMs,
    Math.max(baseRetryMs, Math.round(bounded * jitterFactor)),
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return `${error.name}: ${error.message}`;
  }
  if (typeof error === "string") {
    return error;
  }
  try {
    return JSON.stringify(error);
  } catch {
    return "Unknown worker error";
  }
}
