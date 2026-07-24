import { createDemoApi } from "./http-api.js";
import { JsonStateStore, defaultDataFile } from "./store.js";

const port = readPort(process.env.DEMO_API_PORT ?? process.env.PORT);
const dataFile = process.env.DEMO_API_DATA_FILE ?? defaultDataFile;
const origins = (
  process.env.DEMO_API_ORIGINS ??
  "http://localhost:3000,http://127.0.0.1:3000"
)
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);
const store = new JsonStateStore(dataFile);
const { server } = await createDemoApi({
  store,
  allowedOrigins: origins,
});

await new Promise((resolve, reject) => {
  server.once("error", reject);
  server.listen(port, "0.0.0.0", resolve);
});
process.stdout.write(
  `${JSON.stringify({
    timestamp: new Date().toISOString(),
    level: "info",
    service: "vv-demo-api",
    message: "development_preview_started",
    port,
    dataFile: store.filePath,
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
