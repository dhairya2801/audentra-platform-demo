"""Application errors with stable public HTTP semantics."""


class ApiError(Exception):
    """A deliberately public error safe to return to an API caller."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class BadRequestError(ApiError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(400, code, message)


class UnauthorizedError(ApiError):
    def __init__(self, message: str = "Authentication is required") -> None:
        super().__init__(401, "UNAUTHORIZED", message)


class NotFoundError(ApiError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(404, code, message)


class ConflictError(ApiError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(409, code, message)
