import type { Logger } from "./types.js";

type LogLevel = "debug" | "info" | "warn" | "error";

const severity: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

function serializeError(value: unknown): unknown {
  if (!(value instanceof Error)) {
    return value;
  }

  return {
    name: value.name,
    message: value.message,
    stack: value.stack,
    cause: value.cause,
  };
}

export function createLogger(
  minimumLevel: LogLevel = "info",
  baseFields: Record<string, unknown> = {},
): Logger {
  function write(
    level: LogLevel,
    message: string,
    fields: Record<string, unknown> = {},
  ): void {
    if (severity[level] < severity[minimumLevel]) {
      return;
    }

    const normalizedFields = Object.fromEntries(
      Object.entries(fields).map(([key, value]) => [key, serializeError(value)]),
    );
    const record = {
      timestamp: new Date().toISOString(),
      level,
      service: "vv-worker",
      ...baseFields,
      message,
      ...normalizedFields,
    };
    const output = JSON.stringify(record);

    if (level === "error") {
      process.stderr.write(`${output}\n`);
    } else {
      process.stdout.write(`${output}\n`);
    }
  }

  return {
    debug: (message, fields) => write("debug", message, fields),
    info: (message, fields) => write("info", message, fields),
    warn: (message, fields) => write("warn", message, fields),
    error: (message, fields) => write("error", message, fields),
  };
}
