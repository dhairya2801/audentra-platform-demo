import { randomUUID } from "node:crypto";

export interface WorkerConfig {
  databaseUrl: string;
  apiInternalUrl: string;
  documentWorkerToken: string;
  workerId: string;
  consumerName: string;
  batchSize: number;
  pollIntervalMs: number;
  leaseSeconds: number;
  maxAttempts: number;
  baseRetryMs: number;
  maxRetryMs: number;
  statementTimeoutMs: number;
  shutdownTimeoutMs: number;
  healthPort: number;
  heartbeatIntervalMs: number;
  logLevel: "debug" | "info" | "warn" | "error";
}

function readInteger(
  environment: NodeJS.ProcessEnv,
  name: string,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const raw = environment[name];
  if (raw === undefined || raw.trim() === "") {
    return fallback;
  }

  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(
      `${name} must be an integer between ${minimum} and ${maximum}`,
    );
  }
  return value;
}

export function loadConfig(
  environment: NodeJS.ProcessEnv = process.env,
): WorkerConfig {
  const databaseUrl = environment.DATABASE_URL?.trim();
  if (!databaseUrl) {
    throw new Error("DATABASE_URL is required");
  }

  const logLevel = environment.LOG_LEVEL?.trim().toLowerCase() ?? "info";
  if (!["debug", "info", "warn", "error"].includes(logLevel)) {
    throw new Error("LOG_LEVEL must be debug, info, warn, or error");
  }

  const config: WorkerConfig = {
    databaseUrl,
    apiInternalUrl:
      environment.API_INTERNAL_URL?.trim() || "http://localhost:4000",
    documentWorkerToken:
      environment.DOCUMENT_WORKER_TOKEN?.trim() ||
      "local-development-document-worker-token",
    workerId:
      environment.WORKER_ID?.trim() ||
      `${process.env.HOSTNAME ?? "local"}-${process.pid}-${randomUUID().slice(0, 8)}`,
    consumerName:
      environment.OUTBOX_CONSUMER_NAME?.trim() || "student-dashboard-projector",
    batchSize: readInteger(environment, "WORKER_BATCH_SIZE", 20, 1, 500),
    pollIntervalMs: readInteger(
      environment,
      "WORKER_POLL_INTERVAL_MS",
      1_000,
      50,
      60_000,
    ),
    leaseSeconds: readInteger(
      environment,
      "WORKER_LEASE_SECONDS",
      60,
      5,
      3_600,
    ),
    maxAttempts: readInteger(
      environment,
      "WORKER_MAX_ATTEMPTS",
      10,
      1,
      100,
    ),
    baseRetryMs: readInteger(
      environment,
      "WORKER_BASE_RETRY_MS",
      1_000,
      100,
      3_600_000,
    ),
    maxRetryMs: readInteger(
      environment,
      "WORKER_MAX_RETRY_MS",
      300_000,
      1_000,
      86_400_000,
    ),
    statementTimeoutMs: readInteger(
      environment,
      "DATABASE_STATEMENT_TIMEOUT_MS",
      15_000,
      1_000,
      300_000,
    ),
    shutdownTimeoutMs: readInteger(
      environment,
      "WORKER_SHUTDOWN_TIMEOUT_MS",
      25_000,
      1_000,
      300_000,
    ),
    healthPort: readInteger(
      environment,
      "WORKER_HEALTH_PORT",
      3_002,
      1,
      65_535,
    ),
    heartbeatIntervalMs: readInteger(
      environment,
      "WORKER_HEARTBEAT_INTERVAL_MS",
      30_000,
      1_000,
      3_600_000,
    ),
    logLevel: logLevel as WorkerConfig["logLevel"],
  };
  if (config.baseRetryMs > config.maxRetryMs) {
    throw new Error("WORKER_BASE_RETRY_MS cannot exceed WORKER_MAX_RETRY_MS");
  }
  return config;
}
