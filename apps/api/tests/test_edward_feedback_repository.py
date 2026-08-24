"""Production repository behavior without requiring a shared PostgreSQL service."""

from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import BadRequestError, NotFoundError, UnauthorizedError
from audentra.infrastructure.postgres.edward_feedback_repository import (
    PostgresEdwardFeedbackRepository,
)

TENANT_ID = UUID("00000000-0000-7000-8000-000000000001")
STUDENT_ID = UUID("10000000-0000-7000-8000-000000000001")
STAFF_ID = UUID("10000000-0000-7000-8000-000000000901")
REFERENCED_STUDENT_ID = UUID("10000000-0000-7000-8000-000000000002")
CONVERSATION_ID = UUID("20000000-0000-7000-8000-000000000001")
STUDENT_USER_ID = UUID("30000000-0000-7000-8000-000000000001")
STUDENT_ASSISTANT_ID = UUID("30000000-0000-7000-8000-000000000002")
STAFF_USER_ID = UUID("40000000-0000-7000-8000-000000000001")
STAFF_ASSISTANT_ID = UUID("40000000-0000-7000-8000-000000000002")
CREATED_AT = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)

STUDENT_AUTH = AuthContext(
    tenant_id=str(TENANT_ID),
    student_id=str(STUDENT_ID),
    actor_id=str(STUDENT_ID),
    actor_type="student",
)
STAFF_AUTH = AuthContext(
    tenant_id=str(TENANT_ID),
    student_id=str(REFERENCED_STUDENT_ID),
    actor_id=str(STAFF_ID),
    actor_type="staff",
)


class FakeMappingsResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> FakeMappingsResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def one(self) -> dict[str, Any]:
        assert len(self.rows) == 1
        return self.rows[0]

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class FakeFeedbackConnection:
    def __init__(self) -> None:
        self.traces: dict[str, dict[str, Any] | str] = {}
        self.feedback: list[dict[str, Any]] = []
        self.last_list_params: dict[str, Any] = {}
        self.student_turn: dict[str, Any] | None = {
            "assistant_message_id": STUDENT_ASSISTANT_ID,
            "conversation_id": CONVERSATION_ID,
            "student_id": STUDENT_ID,
            "trace_id": "student-trace-1",
            "response": "Upload the final transcript.",
            "created_at": CREATED_AT,
            "user_message_id": STUDENT_USER_ID,
            "question": "What am I missing?",
        }
        self.staff_turn: dict[str, Any] | None = {
            "assistant_message_id": STAFF_ASSISTANT_ID,
            "conversation_id": CONVERSATION_ID,
            "staff_member_id": STAFF_ID,
            "trace_id": "staff-trace-1",
            "response": "Three documents are waiting for review.",
            "referenced_student_id": REFERENCED_STUDENT_ID,
            "created_at": CREATED_AT,
            "user_message_id": STAFF_USER_ID,
            "question": "What needs attention?",
        }

    async def execute(
        self, statement: object, params: dict[str, Any] | None = None
    ) -> FakeMappingsResult:
        sql = str(statement)
        values = params or {}
        if "SELECT trace_payload" in sql:
            trace = self.traces.get(str(values["trace_id"]))
            return FakeMappingsResult([] if trace is None else [{"trace_payload": trace}])
        if "FROM assistant_message AS assistant" in sql:
            turn = self.student_turn
            matches = (
                turn is not None
                and str(turn["assistant_message_id"]) == str(values["assistant_message_id"])
                and turn["trace_id"] == values["trace_id"]
                and values["tenant_id"] == TENANT_ID
                and values["student_id"] == STUDENT_ID
            )
            return FakeMappingsResult([dict(turn)] if matches and turn else [])
        if "FROM staff_assistant_message AS assistant" in sql:
            turn = self.staff_turn
            matches = (
                turn is not None
                and str(turn["assistant_message_id"]) == str(values["assistant_message_id"])
                and turn["trace_id"] == values["trace_id"]
                and values["tenant_id"] == TENANT_ID
                and values["staff_member_id"] == STAFF_ID
            )
            return FakeMappingsResult([dict(turn)] if matches and turn else [])
        if "INSERT INTO assistant_turn_trace" in sql:
            assert isinstance(values["started_at"], datetime)
            assert values["started_at"].tzinfo is not None
            self.traces[str(values["trace_id"])] = str(values["trace_payload"])
            return FakeMappingsResult([])
        if "SELECT * FROM edward_response_feedback" in sql:
            message_id = str(values["assistant_message_id"])
            return FakeMappingsResult(
                [
                    row
                    for row in self.feedback
                    if message_id
                    in {
                        str(row.get("student_assistant_message_id") or ""),
                        str(row.get("staff_assistant_message_id") or ""),
                    }
                ]
            )
        if "INSERT INTO edward_response_feedback" in sql:
            now = CREATED_AT
            row = {
                **values,
                "created_at": now,
                "updated_at": now,
                "actor_name": None,
                "referenced_student_name": None,
            }
            self.feedback.append(row)
            return FakeMappingsResult([row])
        if "UPDATE edward_response_feedback" in sql:
            row = next(item for item in self.feedback if item["id"] == values["id"])
            row.update(
                rating=values["rating"],
                written_feedback=values["written_feedback"],
                updated_at=datetime(2026, 8, 24, 15, 0, tzinfo=UTC),
            )
            return FakeMappingsResult([row])
        if "SELECT feedback.*" in sql:
            rows = []
            for item in self.feedback:
                row = dict(item)
                row["actor_name"] = (
                    "Jamie Rivera" if row["assistant_kind"] == "student" else "Avery Morgan"
                )
                row["referenced_student_name"] = (
                    "Taylor Kim" if row.get("referenced_student_id") else None
                )
                rows.append(row)
            return FakeMappingsResult(rows)
        raise AssertionError(f"Unexpected feedback SQL: {sql}")

    async def scalar(self, statement: object, params: dict[str, Any]) -> int:
        assert "COUNT(*)" in str(statement)
        self.last_list_params = dict(params)
        return len(self.feedback)


class FakeContext(AbstractAsyncContextManager[FakeFeedbackConnection]):
    def __init__(self, connection: FakeFeedbackConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeFeedbackConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeFeedbackEngine:
    def __init__(self) -> None:
        self.connection = FakeFeedbackConnection()

    def begin(self) -> FakeContext:
        return FakeContext(self.connection)

    def connect(self) -> FakeContext:
        return FakeContext(self.connection)


def repository() -> tuple[PostgresEdwardFeedbackRepository, FakeFeedbackConnection]:
    engine = FakeFeedbackEngine()
    return (
        PostgresEdwardFeedbackRepository(cast(AsyncEngine, engine)),
        engine.connection,
    )


def student_trace() -> dict[str, Any]:
    return {
        "traceId": "student-trace-1",
        "tenantId": str(TENANT_ID),
        "assistantKind": "student",
        "actorType": "student",
        "studentId": str(STUDENT_ID),
        "conversationId": str(CONVERSATION_ID),
        "userMessageId": str(STUDENT_USER_ID),
        "assistantMessageId": str(STUDENT_ASSISTANT_ID),
        "startedAt": "2026-08-24T14:00:00Z",
        "toolCalls": [{"tool": "getEnrollmentChecklist", "status": "ok"}],
    }


@pytest.mark.anyio
async def test_student_repository_inserts_then_updates_one_feedback_row() -> None:
    repo, connection = repository()
    created = await repo.submit_student_feedback(
        STUDENT_AUTH,
        assistant_message_id=str(STUDENT_ASSISTANT_ID),
        trace_id="student-trace-1",
        payload={"rating": "positive"},
        trace_payload=student_trace(),
    )
    assert created["rating"] == "positive"
    assert created["question"] == "What am I missing?"
    assert created["traceId"] == "student-trace-1"

    updated = await repo.submit_student_feedback(
        STUDENT_AUTH,
        assistant_message_id=str(STUDENT_ASSISTANT_ID),
        trace_id="student-trace-1",
        payload={"rating": "negative", "writtenFeedback": "  Explain the deadline.  "},
        trace_payload=None,
    )
    assert updated["id"] == created["id"]
    assert updated["rating"] == "negative"
    assert updated["writtenFeedback"] == "Explain the deadline."
    assert len(connection.feedback) == 1

    trace = await repo.get_trace("student-trace-1")
    assert trace is not None
    assert trace["assistantMessageId"] == str(STUDENT_ASSISTANT_ID)


@pytest.mark.anyio
async def test_staff_repository_and_lab_filters_return_staff_identity() -> None:
    repo, connection = repository()
    created = await repo.submit_staff_feedback(
        STAFF_AUTH,
        assistant_message_id=str(STAFF_ASSISTANT_ID),
        trace_id="staff-trace-1",
        payload={"writtenFeedback": "Needs a clearer queue explanation."},
        trace_payload=None,
    )
    assert created["assistantKind"] == "staff"
    assert created["actorId"] == str(STAFF_ID)
    assert created["referencedStudentId"] == str(REFERENCED_STUDENT_ID)
    assert created["rating"] is None

    listing = await repo.list_feedback(
        assistant_kind="staff",
        rating="unrated",
        has_written=True,
        date_from="2026-08-01T00:00:00Z",
        date_to="2026-08-31T23:59:59Z",
        search="queue_100%\\",
        limit=500,
        offset=-4,
    )
    assert listing["total"] == 1
    assert listing["limit"] == 200
    assert listing["offset"] == 0
    assert listing["items"][0]["actorName"] == "Avery Morgan"
    assert listing["items"][0]["referencedStudentName"] == "Taylor Kim"
    assert len(connection.traces) == 1
    assert isinstance(connection.last_list_params["date_from"], datetime)
    assert isinstance(connection.last_list_params["date_to"], datetime)

    negative = await repo.list_feedback(
        assistant_kind=None,
        rating="negative",
        has_written=False,
        date_from=None,
        date_to=None,
        search=None,
        limit=10,
        offset=0,
    )
    assert negative["total"] == 1


@pytest.mark.anyio
async def test_repository_rejects_wrong_actor_missing_turn_and_empty_feedback() -> None:
    repo, connection = repository()
    with pytest.raises(UnauthorizedError):
        await repo.submit_student_feedback(
            STAFF_AUTH,
            assistant_message_id=str(STUDENT_ASSISTANT_ID),
            trace_id="student-trace-1",
            payload={"rating": "positive"},
            trace_payload=None,
        )

    other_student = AuthContext(
        tenant_id=str(TENANT_ID),
        student_id=str(REFERENCED_STUDENT_ID),
        actor_id=str(REFERENCED_STUDENT_ID),
        actor_type="student",
    )
    with pytest.raises(NotFoundError):
        await repo.submit_student_feedback(
            other_student,
            assistant_message_id=str(STUDENT_ASSISTANT_ID),
            trace_id="student-trace-1",
            payload={"rating": "positive"},
            trace_payload=None,
        )
    with pytest.raises(UnauthorizedError):
        await repo.submit_staff_feedback(
            STUDENT_AUTH,
            assistant_message_id=str(STAFF_ASSISTANT_ID),
            trace_id="staff-trace-1",
            payload={"rating": "positive"},
            trace_payload=None,
        )

    connection.student_turn = None
    with pytest.raises(NotFoundError):
        await repo.submit_student_feedback(
            STUDENT_AUTH,
            assistant_message_id=str(STUDENT_ASSISTANT_ID),
            trace_id="student-trace-1",
            payload={"rating": "positive"},
            trace_payload=None,
        )
    connection.student_turn = FakeFeedbackConnection().student_turn
    with pytest.raises(BadRequestError):
        await repo.submit_student_feedback(
            STUDENT_AUTH,
            assistant_message_id=str(STUDENT_ASSISTANT_ID),
            trace_id="student-trace-1",
            payload={"rating": None},
            trace_payload=None,
        )

    connection.staff_turn = None
    with pytest.raises(NotFoundError):
        await repo.submit_staff_feedback(
            STAFF_AUTH,
            assistant_message_id=str(STAFF_ASSISTANT_ID),
            trace_id="staff-trace-1",
            payload={"rating": "negative"},
            trace_payload=None,
        )


@pytest.mark.anyio
async def test_trace_storage_accepts_json_rows_and_rejects_invalid_identity() -> None:
    repo, connection = repository()
    await repo.save_trace(student_trace())
    connection.traces["student-trace-1"] = json.dumps(student_trace())
    assert (await repo.get_trace("student-trace-1"))["traceId"] == "student-trace-1"  # type: ignore[index]
    assert await repo.get_trace("missing") is None
    with pytest.raises(ValueError):
        await repo.save_trace({"traceId": "invalid", "assistantKind": "student"})
    with pytest.raises(ValueError):
        await repo.save_trace(
            {
                "traceId": "invalid-staff",
                "tenantId": str(TENANT_ID),
                "assistantKind": "staff",
            }
        )
