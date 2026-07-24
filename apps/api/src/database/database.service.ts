import { Inject, Injectable, OnModuleDestroy } from "@nestjs/common";
import { drizzle, type NodePgDatabase } from "drizzle-orm/node-postgres";
import { Pool } from "pg";
import { APP_CONFIG, type AppConfig } from "../config/app-config";
import * as schema from "./schema";

@Injectable()
export class DatabaseService implements OnModuleDestroy {
  readonly pool: Pool;
  readonly db: NodePgDatabase<typeof schema>;

  constructor(@Inject(APP_CONFIG) config: AppConfig) {
    this.pool = new Pool({
      connectionString: config.databaseUrl,
      max: 10,
      connectionTimeoutMillis: 5_000,
      idleTimeoutMillis: 30_000,
      application_name: "vv-api",
    });
    this.db = drizzle(this.pool, { schema });
  }

  async onModuleDestroy(): Promise<void> {
    await this.pool.end();
  }
}
