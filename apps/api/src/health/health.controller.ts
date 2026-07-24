import { Controller, Get, Inject, Optional } from "@nestjs/common";
import { sql } from "drizzle-orm";
import { ApiError } from "../common/api-error";
import { DatabaseService } from "../database/database.service";

@Controller("health")
export class HealthController {
  constructor(
    @Optional()
    @Inject(DatabaseService)
    private readonly database: DatabaseService | undefined,
  ) {}

  @Get()
  getHealth(): { status: "ok"; service: "vv-api"; timestamp: string } {
    return {
      status: "ok",
      service: "vv-api",
      timestamp: new Date().toISOString(),
    };
  }

  @Get("ready")
  async getReadiness(): Promise<{
    status: "ready";
    service: "vv-api";
    timestamp: string;
  }> {
    if (this.database) {
      try {
        await this.database.db.execute(sql`SELECT 1`);
      } catch {
        throw new ApiError(
          503,
          "DATABASE_UNAVAILABLE",
          "The API database is not ready",
        );
      }
    }
    return {
      status: "ready",
      service: "vv-api",
      timestamp: new Date().toISOString(),
    };
  }
}
