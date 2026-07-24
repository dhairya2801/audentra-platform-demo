import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { Pool, type PoolClient } from "pg";
import { loadAppConfig } from "../config/app-config";

interface AppliedMigration {
  name: string;
  checksum: string;
}

async function ensureMigrationTable(client: PoolClient): Promise<void> {
  await client.query(`
    CREATE TABLE IF NOT EXISTS vv_schema_migration (
      name text PRIMARY KEY,
      checksum varchar(64) NOT NULL,
      applied_at timestamptz NOT NULL DEFAULT now()
    )
  `);
}

async function main(): Promise<void> {
  const config = loadAppConfig();
  const migrationsDirectory = resolve(__dirname, "../../migrations");
  const migrationNames = (await readdir(migrationsDirectory))
    .filter((name) => /^\d+.*\.sql$/.test(name))
    .sort();
  const pool = new Pool({
    connectionString: config.databaseUrl,
    application_name: "vv-api-migrator",
  });
  const client = await pool.connect();

  try {
    await client.query(
      "SELECT pg_advisory_lock(hashtext('vv-api-schema-migrations'))",
    );
    await ensureMigrationTable(client);
    const appliedResult = await client.query<AppliedMigration>(
      "SELECT name, checksum FROM vv_schema_migration",
    );
    const applied = new Map(
      appliedResult.rows.map((migration) => [
        migration.name,
        migration.checksum,
      ]),
    );

    for (const name of migrationNames) {
      const migrationSql = await readFile(
        resolve(migrationsDirectory, name),
        "utf8",
      );
      const checksum = createHash("sha256")
        .update(migrationSql)
        .digest("hex");
      const previousChecksum = applied.get(name);
      if (previousChecksum) {
        if (previousChecksum !== checksum) {
          throw new Error(
            `Applied migration ${name} has changed; create a new migration instead`,
          );
        }
        continue;
      }

      await client.query("BEGIN");
      try {
        await client.query(migrationSql);
        await client.query(
          "INSERT INTO vv_schema_migration (name, checksum) VALUES ($1, $2)",
          [name, checksum],
        );
        await client.query("COMMIT");
        process.stdout.write(`Applied ${name}\n`);
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      }
    }
  } finally {
    await client
      .query("SELECT pg_advisory_unlock(hashtext('vv-api-schema-migrations'))")
      .catch(() => undefined);
    client.release();
    await pool.end();
  }
}

void main();
