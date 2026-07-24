import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/database/schema.ts",
  out: "./migrations",
  dbCredentials: {
    url:
      process.env.DATABASE_URL ??
      "postgresql://vv:vv_local_password@localhost:5432/vv_enrollment",
  },
  strict: true,
  verbose: true,
});
