import { createServer, type Server } from "node:http";
import type { OutboxRepository } from "./outbox-repository.js";
import type { Logger } from "./types.js";
import type { WorkerService } from "./worker-service.js";

export class HealthServer {
  private server: Server | null = null;

  public constructor(
    private readonly port: number,
    private readonly repository: OutboxRepository,
    private readonly worker: WorkerService,
    private readonly logger: Logger,
  ) {}

  public async start(): Promise<void> {
    if (this.server) {
      return;
    }

    this.server = createServer(async (request, response) => {
      response.setHeader("content-type", "application/json; charset=utf-8");
      response.setHeader("cache-control", "no-store");

      if (request.url === "/health/live") {
        response.statusCode = 200;
        response.end(
          JSON.stringify({
            status: "ok",
            service: "vv-worker",
            uptimeSeconds: Math.floor(process.uptime()),
          }),
        );
        return;
      }

      if (request.url === "/health/ready") {
        const status = this.worker.snapshot();
        if (status.stopping) {
          response.statusCode = 503;
          response.end(JSON.stringify({ status: "stopping" }));
          return;
        }
        try {
          await this.repository.ping();
          response.statusCode = 200;
          response.end(
            JSON.stringify({
              status: "ready",
              inFlight: status.inFlight,
              lastSuccessfulPollAt:
                status.lastSuccessfulPollAt?.toISOString() ?? null,
            }),
          );
        } catch (error) {
          response.statusCode = 503;
          response.end(
            JSON.stringify({
              status: "not_ready",
              reason: error instanceof Error ? error.message : "database_error",
            }),
          );
        }
        return;
      }

      response.statusCode = 404;
      response.end(JSON.stringify({ error: "not_found" }));
    });

    await new Promise<void>((resolve, reject) => {
      this.server?.once("error", reject);
      this.server?.listen(this.port, "0.0.0.0", () => {
        this.server?.off("error", reject);
        resolve();
      });
    });
    this.logger.info("health_server_started", { port: this.port });
  }

  public async close(): Promise<void> {
    const server = this.server;
    if (!server) {
      return;
    }
    this.server = null;
    await new Promise<void>((resolve, reject) => {
      server.close((error) => {
        if (error) {
          reject(error);
        } else {
          resolve();
        }
      });
    });
  }
}
