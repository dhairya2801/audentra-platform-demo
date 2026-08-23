from __future__ import annotations

from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError
from audentra.core.ports import ServiceCall
from audentra.interfaces.http import routes

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
DELEGATE_ID = "00000000-0000-7000-8000-000000000151"
STAFF_ID = "00000000-0000-7000-8000-000000000901"

pytestmark = pytest.mark.anyio


class _StreamingRequest:
    def __init__(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        connected_checks: int = 1,
    ) -> None:
        self.headers = dict(headers or {})
        self.state = SimpleNamespace(request_id="ferpa-route-coverage-request")
        self._connected_checks = connected_checks
        self._disconnect_checks = 0

    async def is_disconnected(self) -> bool:
        self._disconnect_checks += 1
        return self._disconnect_checks > self._connected_checks


class _EventService:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[ServiceCall] = []

    async def dispatch(self, call: ServiceCall) -> object:
        self.calls.append(call)
        return self.response


def _request(
    *,
    headers: Mapping[str, str] | None = None,
    connected_checks: int = 1,
) -> Request:
    return cast(
        Request,
        _StreamingRequest(headers=headers, connected_checks=connected_checks),
    )


def _delegate_auth() -> AuthContext:
    return AuthContext(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        actor_id=DELEGATE_ID,
        actor_type="delegate",
        authentication_method="delegate_link",
        tenant_slug="aster",
        delegate_scopes=frozenset({"dashboard", "enrollment"}),
        delegate_relationship="parent",
        delegate_name="Aster Parent",
        subject_student_name="Aster Student",
    )


def _staff_auth() -> AuthContext:
    return AuthContext(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        actor_id=STAFF_ID,
        actor_type="staff",
        authentication_method="credentials",
        tenant_slug="aster",
    )


async def _chunks(response: StreamingResponse) -> list[str]:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        assert isinstance(chunk, str)
        chunks.append(chunk)
    return chunks


async def test_delegate_stream_replays_ferpa_change_and_emits_ready_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _EventService(
        {
            "events": [
                "ignore malformed event",
                {
                    "cursor": 17,
                    "type": "ferpa.authorization.updated\r\nignored-event-header",
                    "resourceType": "ferpa_authorization",
                    "resourceId": "authorization-17",
                    "data": {"invalidate": ["bootstrap", "enrollment", "profile"]},
                },
            ],
            "cursor": 17,
        }
    )
    sleep = AsyncMock()
    monkeypatch.setattr(routes, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(routes, "time", SimpleNamespace(monotonic=lambda: 10.0))

    response = await routes.stream_student_events(
        _request(),
        service,
        _delegate_auth(),
        after=None,
    )
    chunks = await _chunks(response)

    assert response.media_type == "text/event-stream"
    assert chunks[0] == "retry: 2000\n\n"
    assert chunks[1].startswith("id: 17\nevent: ferpa.authorization.updatedignored-event-header\n")
    assert '"invalidate":["bootstrap","enrollment","profile"]' in chunks[1]
    assert chunks[2].startswith("id: 17\nevent: student.stream.ready\n")
    assert len(service.calls) == 1
    assert service.calls[0].operation == "student.get_realtime_events"
    assert service.calls[0].auth == _delegate_auth()
    assert service.calls[0].payload == {"afterCursor": None, "limit": 100}
    sleep.assert_awaited_once_with(1)


async def test_delegate_stream_uses_batch_cursor_and_emits_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _EventService({"events": [], "cursor": "23"})
    sleep = AsyncMock()
    moments = iter((0.0, 0.0, 16.0))
    monkeypatch.setattr(routes, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(routes, "time", SimpleNamespace(monotonic=lambda: next(moments)))

    response = await routes.stream_student_events(
        _request(),
        service,
        _delegate_auth(),
        after=0,
    )
    chunks = await _chunks(response)

    assert [chunk.splitlines()[0] for chunk in chunks] == [
        "retry: 2000",
        "id: 23",
        ": heartbeat 23",
    ]
    assert service.calls[0].payload == {"afterCursor": None, "limit": 100}
    sleep.assert_awaited_once_with(1)


async def test_student_stream_rejects_an_invalid_last_event_id() -> None:
    service = _EventService({"events": [], "cursor": 1})

    with pytest.raises(BadRequestError) as error:
        await routes.stream_student_events(
            _request(headers={"last-event-id": "17\N{ARABIC-INDIC DIGIT ONE}"}),
            service,
            _delegate_auth(),
            after=None,
        )

    assert error.value.code == "INVALID_EVENT_CURSOR"
    assert service.calls == []


async def test_student_stream_rejects_a_non_mapping_service_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _EventService([{"cursor": 1}])
    monkeypatch.setattr(routes, "time", SimpleNamespace(monotonic=lambda: 10.0))
    response = await routes.stream_student_events(
        _request(),
        service,
        _delegate_auth(),
        after=9,
    )
    iterator = response.body_iterator.__aiter__()

    assert await anext(iterator) == "retry: 2000\n\n"
    with pytest.raises(ApiError) as error:
        await anext(iterator)

    assert error.value.status_code == 500
    assert error.value.code == "INVALID_EVENT_STREAM_RESPONSE"
    assert service.calls[0].payload == {"afterCursor": 9, "limit": 100}


async def test_staff_stream_replays_ferpa_scope_change_and_ready_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _EventService(
        {
            "events": [
                None,
                {
                    "cursor": 31,
                    "type": "ferpa.delegate.scope.changed",
                    "resourceType": "ferpa_delegate",
                    "resourceId": DELEGATE_ID,
                    "workItemId": None,
                    "data": {"invalidate": ["workspace", "student"]},
                },
            ],
            "cursor": 31,
        }
    )
    sleep = AsyncMock()
    monkeypatch.setattr(routes, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(routes, "time", SimpleNamespace(monotonic=lambda: 10.0))

    response = await routes.stream_staff_events(
        _request(),
        service,
        _staff_auth(),
        after=None,
    )
    chunks = await _chunks(response)

    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert chunks[1].startswith("id: 31\nevent: ferpa.delegate.scope.changed\n")
    assert chunks[2].startswith("id: 31\nevent: staff.stream.ready\n")
    assert service.calls[0].operation == "staff.get_realtime_events"
    assert service.calls[0].payload == {"afterCursor": None, "limit": 100}
    sleep.assert_awaited_once_with(1)


@pytest.mark.parametrize(
    ("renderer", "message"),
    [
        (routes._student_sse_message, "Student realtime event cursor must be an integer"),
        (routes._staff_sse_message, "Staff realtime event cursor must be an integer"),
    ],
)
def test_realtime_renderers_reject_non_integer_cursors(
    renderer: Callable[[Mapping[str, object]], str],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        renderer({"cursor": "31", "type": "ferpa.updated"})
