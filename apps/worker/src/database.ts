import pg, { type PoolClient } from "pg";
import type { WorkerConfig } from "./config.js";

const { Pool } = pg;

export type DatabasePool = InstanceType<typeof Pool>;
export type DatabaseClient = PoolClient;

export function createDatabasePool(config: WorkerConfig): DatabasePool {
  return new Pool({
    connectionString: config.databaseUrl,
    application_name: `vv-worker:${config.workerId}`,
    max: Math.max(2, Math.min(config.batchSize, 10)),
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
    statement_timeout: config.statementTimeoutMs,
    query_timeout: config.statementTimeoutMs + 1_000,
    allowExitOnIdle: false,
  });
}

export async function inTransaction<T>(
  pool: DatabasePool,
  work: (client: DatabaseClient) => Promise<T>,
): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const result = await work(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}
