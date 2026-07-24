import { badRequest } from "./errors.js";

const safeIdPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const idempotencyPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;

export function objectBody(value) {
  if (
    value === null ||
    typeof value !== "object" ||
    Array.isArray(value)
  ) {
    throw badRequest("INVALID_BODY", "The request body must be a JSON object");
  }
  return value;
}

export function exactKeys(object, allowed) {
  const unknown = Object.keys(object).filter((key) => !allowed.includes(key));
  if (unknown.length > 0) {
    throw badRequest(
      "UNKNOWN_FIELDS",
      `Unknown request field${unknown.length === 1 ? "" : "s"}: ${unknown.join(", ")}`,
    );
  }
}

export function requiredString(value, name, { min = 1, max = 200 } = {}) {
  if (typeof value !== "string") {
    throw badRequest("INVALID_FIELD", `${name} must be a string`);
  }
  const normalized = value.trim();
  if (normalized.length < min || normalized.length > max) {
    throw badRequest(
      "INVALID_FIELD",
      `${name} must contain ${min}-${max} characters`,
    );
  }
  return normalized;
}

export function optionalString(value, name, options) {
  if (value === undefined) return undefined;
  return requiredString(value, name, options);
}

export function enumValue(value, name, values) {
  if (!values.includes(value)) {
    throw badRequest(
      "INVALID_FIELD",
      `${name} must be one of: ${values.join(", ")}`,
    );
  }
  return value;
}

export function integerValue(value, name, minimum, maximum) {
  if (
    !Number.isSafeInteger(value) ||
    value < minimum ||
    value > maximum
  ) {
    throw badRequest(
      "INVALID_FIELD",
      `${name} must be an integer between ${minimum} and ${maximum}`,
    );
  }
  return value;
}

export function uuidValue(value, name) {
  if (typeof value !== "string" || !safeIdPattern.test(value)) {
    throw badRequest("INVALID_FIELD", `${name} must be a valid UUID`);
  }
  return value.toLowerCase();
}

export function isoTimestamp(value, name) {
  const text = requiredString(value, name, { min: 20, max: 40 });
  const timestamp = Date.parse(text);
  if (!Number.isFinite(timestamp) || !text.includes("T")) {
    throw badRequest("INVALID_FIELD", `${name} must be an ISO timestamp`);
  }
  return new Date(timestamp).toISOString();
}

export function requireIdempotencyKey(headers) {
  const value = headers["idempotency-key"];
  if (value === undefined) {
    throw badRequest(
      "IDEMPOTENCY_KEY_REQUIRED",
      "The Idempotency-Key header is required",
    );
  }
  if (typeof value !== "string" || !idempotencyPattern.test(value)) {
    throw badRequest(
      "INVALID_IDEMPOTENCY_KEY",
      "Idempotency-Key must be 8-128 safe characters",
    );
  }
  return value;
}

export function booleanValue(value, name) {
  if (typeof value !== "boolean") {
    throw badRequest("INVALID_FIELD", `${name} must be a boolean`);
  }
  return value;
}
