export class HttpError extends Error {
  constructor(status, code, message, details = undefined) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function badRequest(code, message, details) {
  return new HttpError(400, code, message, details);
}

export function unauthorized(message = "Demo sign-in is required") {
  return new HttpError(401, "UNAUTHORIZED", message);
}

export function notFound(code, message) {
  return new HttpError(404, code, message);
}

export function conflict(code, message) {
  return new HttpError(409, code, message);
}
