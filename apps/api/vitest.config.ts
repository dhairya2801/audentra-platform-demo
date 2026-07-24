import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.spec.ts"],
    restoreMocks: true,
    clearMocks: true,
    coverage: {
      reporter: ["text", "json-summary"],
      include: ["src/**/*.ts"],
      exclude: ["src/main.ts", "src/database/migrate.ts", "src/database/seed.ts"],
    },
  },
});
