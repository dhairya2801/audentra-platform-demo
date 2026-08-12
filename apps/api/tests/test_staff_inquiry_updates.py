from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ConflictError, NotFoundError
from audentra.core.ports import ServiceCall
from audentra.infrastructure.postgres.postgres_service import (
    PostgresPlatformService,
    PostgresRepositoryBundle,
)
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.interfaces.http.app import create_app

TENANT_ID = "00000000-0000-7000-8000-000000000001"
FOREIGN_TENANT_ID = "00000000-0000-7000-8000-000000000099"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
STAFF_ID = "00000000-0000-7000-8000-000000000901"
ASSIGNEE_ID = "00000000-0000-7000-8000-000000000902"
INQUIRY_ID = "00000000-0000-7000-8000-000000000952"
NOW = datetime(2026, 8, 2, 19, 30, tzinfo=UTC)
UPDATED_AT = datetime(2026, 8, 2, 19, 45, tzinfo=UTC)


def staff_auth(*, tenant_id: str = TENANT_ID) -> AuthContext:
    return AuthContext(
        tenant_id=tenant_id,
        student_id=STUDENT_ID,
        actor_id=STAFF_ID,
        actor_type="staff",
    )


class FakeMappings:
    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def first(self) -> Mapping[str, object] | None:
        return self._rows[0] if self._rows else None


class FakeResult:
    def __init__(self, rows: Sequence[Mapping[str, object]] = ()) -> None:
        self._rows = rows

    def mappings(self) -> FakeMappings:
        return FakeMappings(self._rows)


class FakeConnection:
    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(
        self, statement: object, parameters: Mapping[str, object] | None = None
    ) -> FakeResult:
        sql = str(statement)
        values = dict(parameters or {})
        self.calls.append((sql, values))
        return FakeResult(self._handler(sql, values))


class FakeContext(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def __init__(self, handler: Any) -> None:
        self.connection = FakeConnection(handler)
        self.begin_count = 0

    def begin(self) -> FakeContext:
        self.begin_count += 1
        return FakeContext(self.connection)


class FakeStudentReader:
    pass


def inquiry_row(*, version: int = 1) -> dict[str, object]:
    return {
        "id": UUID(INQUIRY_ID),
        "student_id": UUID(STUDENT_ID),
        "topic_code": "documents",
        "subject": "Student question about documents",
        "message": "Which transcript should I use?",
        "status": "new",
        "priority": "medium",
        "assignee_id": None,
        "version": version,
        "last_message_at": NOW,
        "expires_at": datetime(2026, 8, 7, 19, 30, tzinfo=UTC),
        "archived_at": None,
        "created_at": NOW,
        "updated_at": NOW,
        "class_year": 2027,
        "first_name": "Alex",
        "last_name": "Morgan",
        "preferred_name": "Alex",
        "program_name": "Computer Science",
    }


def update_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "expectedVersion": 1,
        "status": "waiting_on_student",
        "assigneeId": ASSIGNEE_ID,
        "responseNote": "Please upload both transcripts.",
        "notifyStudent": True,
    }
    payload.update(overrides)
    return payload


def test_postgres_inquiry_update_is_atomic_scoped_and_persists_reply() -> None:
    def handler(sql: str, _values: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
        if "FROM public.student_inquiry AS inquiry" in sql:
            return [inquiry_row()]
        if "FROM public.staff_member" in sql:
            return [
                {
                    "id": UUID(ASSIGNEE_ID),
                    "display_name": "Marcus Lee",
                    "email_normalized": "marcus.lee@aster.example.edu",
                    "component": "Registrar",
                }
            ]
        if "UPDATE public.student_inquiry" in sql:
            return [{"version": 2, "updated_at": UPDATED_AT}]
        if "INSERT INTO public.student_message" in sql:
            return [{"sent_at": UPDATED_AT}]
        return []

    engine = FakeEngine(handler)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, engine),
        cast(Any, FakeStudentReader()),
        clock=lambda: UPDATED_AT,
    )

    result = asyncio.run(
        repository.update_inquiry(
            staff_auth(),
            INQUIRY_ID,
            update_payload(),
            "request-inquiry-1",
        )
    )

    assert result == {
        "id": INQUIRY_ID,
        "student": {
            "id": STUDENT_ID,
            "name": "Alex Morgan",
            "preferredName": "Alex",
            "programName": "Computer Science",
            "classYear": 2027,
        },
        "topicCode": "documents",
        "subject": "Student question about documents",
        "message": "Which transcript should I use?",
        "status": "waiting_on_student",
        "priority": "medium",
        "assignee": {
            "id": ASSIGNEE_ID,
            "name": "Marcus Lee",
            "email": "marcus.lee@aster.example.edu",
            "component": "Registrar",
        },
        "createdAt": "2026-08-02T19:30:00.000Z",
        "updatedAt": "2026-08-02T19:45:00.000Z",
        "version": 2,
    }
    assert engine.begin_count == 1

    lock_sql, lock_params = next(
        call
        for call in engine.connection.calls
        if "FROM public.student_inquiry AS inquiry" in call[0]
    )
    assert "inquiry.tenant_id = :tenant_id" in lock_sql
    assert "FOR UPDATE OF inquiry" in lock_sql
    assert lock_params == {
        "tenant_id": UUID(TENANT_ID),
        "inquiry_id": UUID(INQUIRY_ID),
    }

    update_sql, update_params = next(
        call for call in engine.connection.calls if "UPDATE public.student_inquiry" in call[0]
    )
    assert "tenant_id = :tenant_id" in update_sql
    assert "version = :expected_version" in update_sql
    assert update_params["expected_version"] == 1
    assert update_params["is_resolved"] is False

    message_params = next(
        values
        for sql, values in engine.connection.calls
        if "INSERT INTO public.student_message" in sql
    )
    assert message_params["body"] == "Please upload both transcripts."
    assert message_params["student_id"] == UUID(STUDENT_ID)

    reply_params = next(
        values
        for sql, values in engine.connection.calls
        if "INSERT INTO public.student_inquiry_reply" in sql
    )
    assert reply_params["response_note"] == "Please upload both transcripts."
    assert reply_params["notify_student"] is True
    assert reply_params["student_message_id"] is not None

    work_item_sql, _work_item_params = next(
        call for call in engine.connection.calls if "FROM public.staff_work_item AS item" in call[0]
    )
    assert "staff_work_item_link" in work_item_sql
    assert "ORDER BY item.created_at DESC" in work_item_sql

    audit_params = next(
        values for sql, values in engine.connection.calls if "INSERT INTO public.audit_event" in sql
    )
    audit_metadata = json.loads(cast(str, audit_params["metadata"]))
    assert "responseNote" not in audit_metadata
    assert audit_metadata["responseRecorded"] is True
    outbox_params = next(
        values
        for sql, values in engine.connection.calls
        if "INSERT INTO public.outbox_event" in sql
    )
    event = json.loads(cast(str, outbox_params["payload"]))
    assert event["eventName"] == "student.inquiry_updated_by_staff.v1"
    assert "responseNote" not in event["data"]


def test_postgres_inquiry_update_detects_version_conflict_before_writes() -> None:
    def handler(sql: str, _values: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
        return [inquiry_row(version=2)] if "FROM public.student_inquiry AS inquiry" in sql else []

    engine = FakeEngine(handler)
    repository = PostgresStaffRepository(cast(AsyncEngine, engine), cast(Any, FakeStudentReader()))

    with pytest.raises(ConflictError) as raised:
        asyncio.run(
            repository.update_inquiry(staff_auth(), INQUIRY_ID, update_payload(), "request-stale")
        )

    assert raised.value.code == "VERSION_CONFLICT"
    assert len(engine.connection.calls) == 1


def test_postgres_inquiry_update_rejects_an_expired_support_conversation() -> None:
    expired = inquiry_row()
    expired["expires_at"] = NOW

    def handler(sql: str, _values: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
        return [expired] if "FROM public.student_inquiry AS inquiry" in sql else []

    engine = FakeEngine(handler)
    repository = PostgresStaffRepository(
        cast(AsyncEngine, engine),
        cast(Any, FakeStudentReader()),
        clock=lambda: UPDATED_AT,
    )

    with pytest.raises(ConflictError) as raised:
        asyncio.run(
            repository.update_inquiry(staff_auth(), INQUIRY_ID, update_payload(), "request-expired")
        )

    assert raised.value.code == "SUPPORT_CONVERSATION_EXPIRED"
    assert len(engine.connection.calls) == 1


def test_postgres_inquiry_lookup_hides_foreign_tenant_and_performs_no_write() -> None:
    def handler(sql: str, values: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
        assert "FROM public.student_inquiry AS inquiry" in sql
        assert values["tenant_id"] == UUID(FOREIGN_TENANT_ID)
        return []

    engine = FakeEngine(handler)
    repository = PostgresStaffRepository(cast(AsyncEngine, engine), cast(Any, FakeStudentReader()))

    with pytest.raises(NotFoundError) as raised:
        asyncio.run(
            repository.update_inquiry(
                staff_auth(tenant_id=FOREIGN_TENANT_ID),
                INQUIRY_ID,
                update_payload(),
                "request-foreign",
            )
        )

    assert raised.value.code == "STAFF_INQUIRY_NOT_FOUND"
    assert len(engine.connection.calls) == 1


class NoopSignedDocuments:
    async def ensure(self, **_kwargs: object) -> int:
        return 0


class ServiceStaffRepository:
    def __init__(self, canonical: object) -> None:
        self.canonical = canonical
        self.calls: list[tuple[object, ...]] = []

    async def update_inquiry(self, *args: object) -> object:
        self.calls.append(args)
        return self.canonical

    async def get_action_center(self, _auth: AuthContext) -> dict[str, object]:
        return {
            "staff": [{"id": STAFF_ID, "name": "Priya Shah"}],
            "items": [],
        }


def service_with_inquiry_repositories(
    canonical: object,
) -> tuple[PostgresPlatformService, ServiceStaffRepository]:
    staff = ServiceStaffRepository(canonical)
    bundle = PostgresRepositoryBundle(
        platform=cast(Any, object()),
        portal=cast(Any, object()),
        staff=cast(Any, staff),
    )
    service = PostgresPlatformService(
        bundle,
        cast(Any, object()),
        cast(Any, object()),
        NoopSignedDocuments(),
        "worker-token",
    )
    return service, staff


def inquiry_call() -> ServiceCall:
    return ServiceCall(
        "staff.update_inquiry",
        staff_auth(),
        "request-service",
        payload=update_payload(responseNote=None, notifyStudent=False),
        path_params={"inquiryId": INQUIRY_ID},
    )


def test_service_prefers_canonical_inquiry_without_touching_preview() -> None:
    canonical = {"id": INQUIRY_ID, "source": "postgres"}
    service, staff = service_with_inquiry_repositories(canonical)

    result = asyncio.run(service.dispatch(inquiry_call()))

    assert result == canonical
    assert staff.calls[0][1:] == (
        INQUIRY_ID,
        update_payload(responseNote=None, notifyStudent=False),
        "request-service",
    )


def test_service_does_not_invent_a_preview_inquiry_when_canonical_id_is_absent() -> None:
    service, staff = service_with_inquiry_repositories(None)

    result = asyncio.run(service.dispatch(inquiry_call()))

    assert result is None
    assert len(staff.calls) == 1


class RecordingHttpService:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[ServiceCall] = []
        self.error = error

    async def dispatch(self, call: ServiceCall) -> object:
        self.calls.append(call)
        if call.operation == "public.get_tenant_bootstrap":
            return {"tenantId": TENANT_ID, "slug": "aster"}
        if self.error is not None:
            raise self.error
        return {"id": INQUIRY_ID, "status": "open", "version": 2}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def http_client() -> AsyncIterator[tuple[AsyncClient, RecordingHttpService]]:
    service = RecordingHttpService()
    transport = ASGITransport(app=create_app(service=service))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service


@pytest.mark.anyio
async def test_http_patch_dispatches_exact_frontend_contract(
    http_client: tuple[AsyncClient, RecordingHttpService],
) -> None:
    client, service = http_client

    response = await client.patch(
        f"/v1/staff/inquiries/{INQUIRY_ID}",
        headers={"X-Demo-Actor-Type": "staff"},
        json=update_payload(),
    )

    assert response.status_code == 200
    call = service.calls[-1]
    assert call.operation == "staff.update_inquiry"
    assert call.path_params == {"inquiryId": INQUIRY_ID}
    assert call.payload == update_payload()
    assert call.auth is not None and call.auth.actor_type == "staff"


@pytest.mark.anyio
async def test_http_patch_returns_public_409_for_stale_version() -> None:
    service = RecordingHttpService(
        ConflictError("VERSION_CONFLICT", "This inquiry changed in another staff session")
    )
    transport = ASGITransport(app=create_app(service=service))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.patch(
            f"/v1/staff/inquiries/{INQUIRY_ID}",
            headers={"X-Demo-Actor-Type": "staff"},
            json=update_payload(),
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"


@pytest.mark.anyio
async def test_http_patch_rejects_invalid_reply_before_dispatch(
    http_client: tuple[AsyncClient, RecordingHttpService],
) -> None:
    client, service = http_client

    response = await client.patch(
        f"/v1/staff/inquiries/{INQUIRY_ID}",
        headers={"X-Demo-Actor-Type": "staff"},
        json=update_payload(responseNote="x" * 1001),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert not any(call.operation == "staff.update_inquiry" for call in service.calls)


def test_reply_migration_preserves_private_history_and_notification_link() -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "0020_staff_inquiry_replies.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE student_inquiry_reply" in migration
    assert "REFERENCES student_inquiry(id) ON DELETE CASCADE" in migration
    assert "char_length(response_note) BETWEEN 1 AND 1000" in migration
    assert "student_message_id uuid REFERENCES student_message(id)" in migration
    assert "student_inquiry_reply_history_idx" in migration
