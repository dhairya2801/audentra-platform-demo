from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ConflictError
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository


class FakeResult:
    def __init__(self, rows: list[Mapping[str, Any]], rowcount: int | None = None) -> None:
        self._rows = rows
        self.rowcount = len(rows) if rowcount is None else rowcount

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[Mapping[str, Any]]:
        return self._rows

    def one(self) -> Mapping[str, Any]:
        return self._rows[0]

    def first_scalar(self) -> object | None:
        return next(iter(self._rows[0].values())) if self._rows else None

    def first(self) -> Mapping[str, Any] | None:
        return self._rows[0] if self._rows else None


Handler = Callable[[str, Mapping[str, Any]], list[Mapping[str, Any]] | FakeResult]


class FakeConnection:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, statement: object, parameters: Mapping[str, Any] | None = None
    ) -> FakeResult:
        sql = str(statement)
        params = dict(parameters or {})
        self.calls.append((sql, params))
        value = self.handler(sql, params)
        return value if isinstance(value, FakeResult) else FakeResult(value)


class FakeContext(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def __init__(self, handler: Handler) -> None:
        self.connection = FakeConnection(handler)

    def connect(self) -> FakeContext:
        return FakeContext(self.connection)

    def begin(self) -> FakeContext:
        return FakeContext(self.connection)


AUTH = AuthContext(
    tenant_id="00000000-0000-7000-8000-000000000001",
    student_id="10000000-0000-7000-8000-000000000001",
    actor_id="10000000-0000-7000-8000-000000000001",
    actor_type="student",
)


def test_appointments_read_is_tenant_and_student_scoped() -> None:
    now = datetime.now(UTC) + timedelta(days=1)
    engine = FakeEngine(
        lambda _sql, _params: [
            {
                "id": "appointment-1",
                "type": "advising",
                "starts_at": now,
                "notes": None,
                "status": "scheduled",
                "created_at": now,
            }
        ]
    )
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))

    result = asyncio.run(repository.get_student_appointments(AUTH))

    assert result["total"] == 1
    sql, parameters = engine.connection.calls[0]
    assert "tenant_id=:tenant_id" in sql
    assert "student_id=:student_id" in sql
    assert parameters == {"tenant_id": AUTH.tenant_id, "student_id": AUTH.student_id}


def test_idempotency_replays_exact_response_without_running_handler() -> None:
    response = {"planId": "plan-1", "status": "enrolled"}
    repository: PostgresPortalRepository

    def handler(sql: str, _params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "SELECT request_hash, response_body" in sql:
            return [
                {
                    "request_hash": repository._request_hash(AUTH, {"planId": "plan-1"}),
                    "response_body": response,
                }
            ]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))
    invoked = False

    async def command(_connection: AsyncConnection) -> dict[str, Any]:
        nonlocal invoked
        invoked = True
        return {"unexpected": True}

    actual = asyncio.run(
        repository._run_idempotent(
            AUTH,
            "idempotency-key",
            "request-1",
            "student_financial.payment_plan.select",
            {"planId": "plan-1"},
            200,
            command,
        )
    )

    assert actual == response
    assert invoked is False
    assert "pg_advisory_xact_lock" in engine.connection.calls[0][0]


def test_idempotency_key_reuse_with_different_payload_conflicts() -> None:
    engine = FakeEngine(
        lambda sql, _params: (
            [{"request_hash": "different", "response_body": {}}]
            if "SELECT request_hash, response_body" in sql
            else []
        )
    )
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))

    async def command(_connection: AsyncConnection) -> dict[str, Any]:
        return {}

    with pytest.raises(ConflictError, match="different request") as raised:
        asyncio.run(
            repository._run_idempotent(
                AUTH,
                "idempotency-key",
                "request-1",
                "operation",
                {"value": 1},
                200,
                command,
            )
        )
    assert raised.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_profile_optimistic_lock_reports_version_conflict() -> None:
    def handler(sql: str, _params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "SELECT version FROM student_profile" in sql:
            return [{"version": 5}]
        return []

    repository = PostgresPortalRepository(cast(AsyncEngine, FakeEngine(handler)))

    with pytest.raises(ConflictError) as raised:
        asyncio.run(
            repository.update_student_profile(
                AUTH, {"preferredName": "Alex", "expectedVersion": 4}, "request-1"
            )
        )
    assert raised.value.code == "VERSION_CONFLICT"


def test_signed_document_converts_iso_timestamp_before_database_bind() -> None:
    signed_at = "2026-08-02T17:34:41.830Z"
    created_at = datetime(2026, 8, 2, 17, 34, 41, 830000, tzinfo=UTC)

    def handler(sql: str, params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "INSERT INTO student_signed_document" not in sql:
            return []
        return [
            {
                "id": params["id"],
                "template_code": params["template_code"],
                "onboarding_version": params["onboarding_version"],
                "title": params["title"],
                "file_name": params["file_name"],
                "mime_type": "application/pdf",
                "size_bytes": params["size_bytes"],
                "storage_key": params["storage_key"],
                "sha256": params["sha256"],
                "signer_name": params["signer_name"],
                "signature_method": params["signature_method"],
                "signed_at": params["signed_at"],
                "created_at": created_at,
            }
        ]

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))
    result = asyncio.run(
        repository.save_student_signed_document(
            AUTH,
            {
                "id": "20000000-0000-7000-8000-000000000001",
                "templateCode": "ferpa_release",
                "onboardingVersion": 10,
                "title": "FERPA Information Release",
                "fileName": "ferpa-information-release-signed.pdf",
                "sizeBytes": 43610,
                "storageKey": "tenant/student/signed.pdf",
                "sha256": "a" * 64,
                "signerName": "Alex Morgan",
                "signatureMethod": "typed",
                "signedAt": signed_at,
            },
            "request-1",
        )
    )

    insert_parameters = engine.connection.calls[0][1]
    assert insert_parameters["signed_at"] == created_at
    assert result["signature"]["signedAt"] == signed_at
