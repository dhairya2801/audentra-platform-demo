"""Stable exception-to-HTTP translation."""

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from audentra.core.errors import ApiError


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown-request")


def _body(request: Request, code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "requestId": _request_id(request)}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content=_body(request, error.code, error.message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        messages = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'][1:])}: {item['msg']}"
            for item in error.errors()
        )
        return JSONResponse(
            status_code=400,
            content=_body(request, "VALIDATION_ERROR", messages or "Request validation failed"),
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        message = error.detail if isinstance(error.detail, str) else str(error.detail)
        return JSONResponse(
            status_code=error.status_code,
            content=_body(
                request,
                "VALIDATION_ERROR" if error.status_code == 400 else "HTTP_ERROR",
                message,
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_body(request, "INTERNAL_ERROR", "An unexpected error occurred"),
        )
