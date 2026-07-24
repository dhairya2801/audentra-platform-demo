import { BadRequestError } from "./api-error";

export function requireIdempotencyKey(value: string | undefined): string {
  if (value === undefined) {
    throw new BadRequestError(
      "IDEMPOTENCY_KEY_REQUIRED",
      "The Idempotency-Key header is required",
    );
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(value)) {
    throw new BadRequestError(
      "INVALID_IDEMPOTENCY_KEY",
      "Idempotency-Key must be 8-128 safe characters",
    );
  }
  return value;
}
