from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.infrastructure.postgres.auth_repository import PostgresDevelopmentAuth


class FakeResult:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class FakeConnection:
    async def execute(
        self, statement: object, parameters: Mapping[str, Any] | None = None
    ) -> FakeResult:
        sql = str(statement)
        assert "tenant.status='active'" in sql
        assert "demo_auth_enabled" not in sql
        assert parameters == {"tenant_id": UUID("00000000-0000-7000-8000-000000000099")}
        return FakeResult(UUID("10000000-0000-7000-8000-000000000001"))


class FakeContext(AbstractAsyncContextManager[FakeConnection]):
    async def __aenter__(self) -> FakeConnection:
        return FakeConnection()

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def connect(self) -> FakeContext:
        return FakeContext()


class RecordingConnection:
    def __init__(self) -> None:
        self.executions: list[tuple[str, dict[str, Any]]] = []

    async def execute(
        self, statement: object, parameters: Mapping[str, Any] | None = None
    ) -> FakeResult:
        self.executions.append((str(statement), dict(parameters or {})))
        return FakeResult(None)


def test_staff_session_does_not_require_public_demo_auth() -> None:
    auth = PostgresDevelopmentAuth(
        cast(AsyncEngine, FakeEngine()),
        environment="test",
        staff_invitation_code="private-code",
    )

    session = asyncio.run(
        auth._staff_session(
            {
                "id": UUID("20000000-0000-7000-8000-000000000001"),
                "display_name": "Operations User",
                "email_normalized": "operations@example.edu",
                "component": "Admissions",
            },
            "00000000-0000-7000-8000-000000000099",
            "north-campus",
        )
    )

    assert session.context.student_id == "10000000-0000-7000-8000-000000000001"
    assert session.context.tenant_id == "00000000-0000-7000-8000-000000000099"


def test_new_student_onboarding_payload_explicitly_types_json_values() -> None:
    """Postgres needs typed bind values when JSONB is constructed from parameters."""

    auth = PostgresDevelopmentAuth(
        cast(AsyncEngine, FakeEngine()),
        environment="test",
        staff_invitation_code="private-code",
    )
    connection = RecordingConnection()
    test_hash = "scrypt-v1$test"

    asyncio.run(
        auth._insert_new_student(
            cast(Any, connection),
            tenant_id="00000000-0000-7000-8000-000000000099",
            account_id=UUID("20000000-0000-7000-8000-000000000001"),
            person_id=UUID("30000000-0000-7000-8000-000000000001"),
            student_id=UUID("40000000-0000-7000-8000-000000000001"),
            offer_id=UUID("50000000-0000-7000-8000-000000000001"),
            email="student@example.edu",
            phone="+15551234567",
            legal_name="Sait Burak",
            password_hash=test_hash,
            template={
                "program_id": UUID("60000000-0000-7000-8000-000000000001"),
                "academic_term_id": UUID("70000000-0000-7000-8000-000000000001"),
                "campus_id": UUID("80000000-0000-7000-8000-000000000001"),
                "response_deadline": "2026-09-01T00:00:00Z",
                "deposit_amount_cents": 100,
                "class_year": "2030",
                "financial_academic_year": "2026-2027",
                "cost_of_attendance_cents": 1_000,
            },
        )
    )

    onboarding_sql, values = next(
        (sql, values)
        for sql, values in connection.executions
        if "INSERT INTO student_onboarding" in sql
    )
    assert "CAST(:first_name AS text)" in onboarding_sql
    assert "CAST(:last_name AS text)" in onboarding_sql
    assert "CAST(:preferred_name AS text)" in onboarding_sql
    assert "CAST(:email AS text)" in onboarding_sql
    assert "CAST(:phone AS text)" in onboarding_sql
    assert values["first_name"] == "Sait"
    assert values["last_name"] == "Burak"
    assert values["phone"] == "+15551234567"
