import type { LogLevel } from "./config.js";
import type { Logger } from "./types.js";

const severity: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

function serializeValue(value: unknown): unknown {
  if (!(value instanceof Error)) return value;
  return { errorType: value.name || "Error" };
}

export function createLogger(
  minimumLevel: LogLevel = "info",
  baseFields: Record<string, unknown> = {},
): Logger {
  const write = (
    level: LogLevel,
    message: string,
    fields: Record<string, unknown> = {},
  ): void => {
    if (severity[level] < severity[minimumLevel]) return;
    const normalizedFields = Object.fromEntries(
      Object.entries(fields).map(([key, value]) => [key, serializeValue(value)]),
    );
    const output = JSON.stringify({
      timestamp: new Date().toISOString(),
      level,
      service: "vv-voice-agent",
      ...baseFields,
      message,
      ...normalizedFields,
    });
    (level === "error" ? process.stderr : process.stdout).write(`${output}\n`);
  };

  return {
    debug: (message, fields) => write("debug", message, fields),
    info: (message, fields) => write("info", message, fields),
    warn: (message, fields) => write("warn", message, fields),
    error: (message, fields) => write("error", message, fields),
  };
}
