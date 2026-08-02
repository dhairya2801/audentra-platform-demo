"""Request correlation compatible with the original Fastify adapter."""

import re
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        candidate = request.headers.get("x-correlation-id") or request.headers.get("x-request-id")
        request_id = (
            candidate if candidate and SAFE_REQUEST_ID.fullmatch(candidate) else str(uuid4())
        )
        request.state.request_id = request_id
        request.state.trace_id = uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-correlation-id"] = request_id
        response.headers["x-trace-id"] = request.state.trace_id
        return response
