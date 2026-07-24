export class ApiError extends Error {
  constructor(
    readonly statusCode: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class BadRequestError extends ApiError {
  constructor(code: string, message: string) {
    super(400, code, message);
  }
}

export class UnauthorizedError extends ApiError {
  constructor(message = "Authentication is required") {
    super(401, "UNAUTHORIZED", message);
  }
}

export class NotFoundError extends ApiError {
  constructor(code: string, message: string) {
    super(404, code, message);
  }
}

export class ConflictError extends ApiError {
  constructor(code: string, message: string) {
    super(409, code, message);
  }
}
