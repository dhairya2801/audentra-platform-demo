import { cli, ServerOptions } from "@livekit/agents";
import { fileURLToPath } from "node:url";
import { loadConfig } from "./config.js";
import { createLogger } from "./logger.js";

function main(): void {
  const config = loadConfig();
  const agentFile = fileURLToPath(
    new URL(import.meta.url.endsWith(".ts") ? "./agent.ts" : "./agent.js", import.meta.url),
  );
  cli.runApp(
    new ServerOptions({
      agent: agentFile,
      agentName: config.agentName,
      wsURL: config.livekitUrl,
      apiKey: config.livekitApiKey,
      apiSecret: config.livekitApiSecret,
      drainTimeout: config.shutdownTimeoutMs,
      shutdownProcessTimeout: config.shutdownTimeoutMs,
      logLevel: config.logLevel,
    }),
  );
}

try {
  main();
} catch (error) {
  createLogger("info").error("voice_agent_startup_failed", { error });
  process.exitCode = 1;
}
