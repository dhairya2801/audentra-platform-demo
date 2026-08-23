from __future__ import annotations

from typing import cast

import pytest
from starlette.types import Message, Receive, Scope, Send

from audentra.interfaces.http.app import create_app
from audentra.interfaces.http.middleware import (
    OidcCallbackQueryRedactionMiddleware,
    RequestContextMiddleware,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _http_scope(path: str, query: bytes) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": query,
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 443),
    }


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_oidc_callback_query_is_available_downstream_but_redacted_for_server(
    provider: str,
) -> None:
    query = b"code=provider-secret&state=opaque-state"
    original_scope = _http_scope(f"/v1/auth/sso/{provider}/callback", query)
    downstream_queries: list[bytes] = []
    server_queries_at_response_start: list[bytes] = []

    async def downstream(scope: Scope, _receive: Receive, send: Send) -> None:
        downstream_queries.append(cast(bytes, scope["query_string"]))
        await send({"type": "http.response.start", "status": 303, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:
        raise AssertionError("The test application must not receive a request message")

    async def server_send(message: Message) -> None:
        if message["type"] == "http.response.start":
            server_queries_at_response_start.append(cast(bytes, original_scope["query_string"]))

    middleware = OidcCallbackQueryRedactionMiddleware(downstream)
    await middleware(original_scope, receive, server_send)

    assert downstream_queries == [query]
    assert server_queries_at_response_start == [b""]
    assert original_scope["query_string"] == b""


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/auth/sso/google",
        "/v1/auth/sso/google/callback/extra",
        "/v1/auth/sso/github/callback",
        "/v1/student/profile",
    ],
)
async def test_non_callback_query_is_untouched(path: str) -> None:
    query = b"code=ordinary-value&filter=active"
    original_scope = _http_scope(path, query)
    downstream_scope_ids: list[int] = []
    server_queries_at_response_start: list[bytes] = []

    async def downstream(scope: Scope, _receive: Receive, send: Send) -> None:
        downstream_scope_ids.append(id(scope))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:
        raise AssertionError("The test application must not receive a request message")

    async def server_send(message: Message) -> None:
        if message["type"] == "http.response.start":
            server_queries_at_response_start.append(cast(bytes, original_scope["query_string"]))

    middleware = OidcCallbackQueryRedactionMiddleware(downstream)
    await middleware(original_scope, receive, server_send)

    assert downstream_scope_ids == [id(original_scope)]
    assert server_queries_at_response_start == [query]
    assert original_scope["query_string"] == query


@pytest.mark.anyio
async def test_callback_query_is_redacted_when_downstream_raises_before_response() -> None:
    original_scope = _http_scope(
        "/v1/auth/sso/google/callback", b"code=provider-secret&state=opaque-state"
    )

    async def downstream(_scope: Scope, _receive: Receive, _send: Send) -> None:
        raise RuntimeError("early failure")

    async def receive() -> Message:
        raise AssertionError("The test application must not receive a request message")

    async def server_send(_message: Message) -> None:
        raise AssertionError("The downstream application must not send a response")

    middleware = OidcCallbackQueryRedactionMiddleware(downstream)

    with pytest.raises(RuntimeError, match="early failure"):
        await middleware(original_scope, receive, server_send)

    assert original_scope["query_string"] == b""


def test_oidc_callback_redaction_is_the_outermost_user_middleware() -> None:
    app = create_app()
    middleware_names = [getattr(item.cls, "__name__", "") for item in app.user_middleware]

    assert middleware_names[:2] == [
        OidcCallbackQueryRedactionMiddleware.__name__,
        RequestContextMiddleware.__name__,
    ]
