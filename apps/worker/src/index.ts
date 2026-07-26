import { loadConfig } from "./config.js";
import { StudentDashboardProjector } from "./dashboard-projector.js";
import { createDatabasePool } from "./database.js";
import { DocumentExtractionRunner } from "./document-extraction-runner.js";
import {
  EventDispatcher,
  portalEventsWithoutWorkerProjection,
} from "./dispatcher.js";
import { HealthServer } from "./health-server.js";
import { createLogger } from "./logger.js";
import { OutboxRepository } from "./outbox-repository.js";
import { WorkerService } from "./worker-service.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const logger = createLogger(config.logLevel, { workerId: config.workerId });
  const pool = createDatabasePool(config);
  pool.on("error", (error) => {
    logger.error("database_pool_error", { error });
  });

  const repository = new OutboxRepository(pool, config);
  const projector = new StudentDashboardProjector(
    pool,
    config.consumerName,
    logger,
  );
  const extractionRunner = new DocumentExtractionRunner(config, logger);
  const dispatcher = new EventDispatcher(logger)
    .register("enrollment.journey_created.v1", (event) => projector.handle(event))
    .register("document.upload_reserved.v1", (event) =>
      extractionRunner.handle(event),
    )
    .register("document.extraction_requested.v1", (event) =>
      extractionRunner.handle(event),
    );
  for (const eventName of portalEventsWithoutWorkerProjection) {
    dispatcher.registerIgnored(eventName);
  }
  const worker = new WorkerService(
    repository,
    dispatcher,
    config.pollIntervalMs,
    logger,
  );
  const healthServer = new HealthServer(
    config.healthPort,
    repository,
    worker,
    logger,
  );
  let heartbeat: NodeJS.Timeout | null = null;
  let shuttingDown = false;
  let runPromise: Promise<void> | null = null;

  const shutdown = async (signal: string): Promise<void> => {
    if (shuttingDown) {
      return;
    }
    shuttingDown = true;
    logger.info("worker_shutdown_started", { signal });
    worker.stop();
    if (heartbeat) {
      clearInterval(heartbeat);
    }

    const gracefulShutdown = (async () => {
      await runPromise;
      await healthServer.close();
      const released = await repository.releaseClaims();
      await pool.end();
      logger.info("worker_shutdown_completed", { releasedClaims: released });
    })();

    const timedOut = await Promise.race([
      gracefulShutdown.then(() => false),
      new Promise<boolean>((resolve) =>
        setTimeout(() => resolve(true), config.shutdownTimeoutMs),
      ),
    ]);
    if (timedOut) {
      logger.error("worker_shutdown_timed_out", {
        timeoutMs: config.shutdownTimeoutMs,
      });
      process.exitCode = 1;
    }
  };

  process.once("SIGTERM", () => void shutdown("SIGTERM"));
  process.once("SIGINT", () => void shutdown("SIGINT"));

  await repository.ping();
  await healthServer.start();
  logger.info("worker_started", {
    consumerName: config.consumerName,
    batchSize: config.batchSize,
    leaseSeconds: config.leaseSeconds,
  });

  heartbeat = setInterval(() => {
    void repository
      .stats()
      .then((stats) => {
        const status = worker.snapshot();
        logger.info("worker_heartbeat", {
          inFlight: status.inFlight,
          processed: status.processed,
          failed: status.failed,
          pending: stats.pending,
          retrying: stats.retrying,
          deadLettered: stats.deadLettered,
          oldestPendingAt: stats.oldestPendingAt?.toISOString() ?? null,
          lastSuccessfulPollAt:
            status.lastSuccessfulPollAt?.toISOString() ?? null,
        });
      })
      .catch((error: unknown) => {
        logger.warn("worker_heartbeat_failed", { error });
      });
  }, config.heartbeatIntervalMs);
  heartbeat.unref();

  try {
    runPromise = worker.run();
    await runPromise;
  } finally {
    if (!shuttingDown) {
      await shutdown("poll_loop_completed");
    }
  }
}

void main().catch((error: unknown) => {
  const logger = createLogger("info");
  logger.error("worker_startup_failed", { error });
  process.exitCode = 1;
});
