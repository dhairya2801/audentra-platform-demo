import { createDemoApi } from "./http-api.js";
import { createDeterministicE2eAi } from "./deterministic-e2e-ai.js";
import { createSeedState } from "./seed.js";
import { JsonStateStore, defaultDataFile } from "./store.js";

const port = readPort(process.env.DEMO_API_PORT ?? process.env.PORT);
const host = process.env.DEMO_API_HOST?.trim() || "0.0.0.0";
const e2eDocumentAi = process.env.VV_E2E_DOCUMENT_AI?.trim().toLowerCase();
const completedE2eStudent =
  process.env.VV_E2E_COMPLETED_STUDENT?.trim().toLowerCase() === "true";
if (e2eDocumentAi && process.env.NODE_ENV === "production") {
  throw new Error("VV_E2E_DOCUMENT_AI cannot be enabled in production");
}
if (completedE2eStudent && process.env.NODE_ENV === "production") {
  throw new Error("VV_E2E_COMPLETED_STUDENT cannot be enabled in production");
}
if (completedE2eStudent && e2eDocumentAi !== "deterministic") {
  throw new Error(
    "VV_E2E_COMPLETED_STUDENT requires the deterministic browser-test mode",
  );
}
if (e2eDocumentAi && e2eDocumentAi !== "deterministic") {
  throw new Error("VV_E2E_DOCUMENT_AI must be 'deterministic' when configured");
}
const dataFile = process.env.DEMO_API_DATA_FILE ?? defaultDataFile;
const origins = (
  process.env.DEMO_API_ORIGINS ??
  "http://localhost:3000,http://127.0.0.1:3000"
)
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);
const store = new JsonStateStore(
  dataFile,
  undefined,
  undefined,
  completedE2eStudent
    ? () => createSeedState({ completedOnboarding: true })
    : createSeedState,
);
const { server } = await createDemoApi({
  store,
  allowedOrigins: origins,
  ...(completedE2eStudent
    ? { tenantSeedStateOptions: { completedOnboarding: true } }
    : {}),
  ...(e2eDocumentAi === "deterministic"
    ? { ai: createDeterministicE2eAi() }
    : {}),
});

await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(port, host, resolve);
});
process.stdout.write(
  `${JSON.stringify({
    timestamp: new Date().toISOString(),
    level: "info",
    service: "vv-demo-api",
    message: "development_preview_started",
    port,
    host,
    dataFile: store.filePath,
    documentAiMode:
      e2eDocumentAi === "deterministic" ? "deterministic-e2e" : "configured",
    completedE2eStudent,
  })}\n`,
);

let closing = false;
async function shutdown(signal) {
  if (closing) return;
  closing = true;
  process.stdout.write(
    `${JSON.stringify({
      timestamp: new Date().toISOString(),
      level: "info",
      service: "vv-demo-api",
      message: "development_preview_stopping",
      signal,
    })}\n`,
  );
  await new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
}

process.once("SIGINT", () => void shutdown("SIGINT"));
process.once("SIGTERM", () => void shutdown("SIGTERM"));

function readPort(candidate) {
  const port = Number(candidate ?? 4000);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error("DEMO_API_PORT must be an integer between 1 and 65535");
  }
  return port;
}
