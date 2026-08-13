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
