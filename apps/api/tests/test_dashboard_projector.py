from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from pydantic import JsonValue

from audentra.infrastructure.messaging.envelope import DomainEventActor, DomainEventEnvelope
from audentra.infrastructure.worker.dashboard_projector import (
    StudentDashboardProjector,
    _iso_date,
    _iso_timestamp_or_none,
)


def _event(data: dict[str, str] | None = None) -> DomainEventEnvelope:
    return DomainEventEnvelope(
        event_id="event-1",
        event_name="enrollment.journey_created.v1",
        occurred_at=datetime(2026, 8, 2, tzinfo=UTC),
        tenant_id="tenant-1",
        aggregate_type="enrollment_journey",
        aggregate_id="journey-1",
        aggregate_version=1,
        actor=DomainEventActor(type="student", id="student-1"),
        correlation_id="request-1",
        causation_id="command-1",
        data=cast(
            dict[str, JsonValue],
            (
                data
                if data is not None
                else {
                    "studentId": "student-1",
                    "journeyId": "journey-1",
                    "offerId": "offer-1",
                }
            ),
        ),
    )


class FakeResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows or []

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows


class FakeConnection:
    def __init__(
        self,
        *,
        receipt_exists: bool = True,
        existing_version: int | None = 2,
        header: dict[str, object] | None = None,
        requirements: list[dict[str, object]] | None = None,
    ) -> None:
        self.receipt_exists = receipt_exists
        self.existing_version = existing_version
        self.header: dict[str, object] | None = header if header is not None else _header()
        self.requirements = requirements if requirements is not None else _requirements()
        self.executed_sql: list[str] = []
        self.projection_parameters: dict[str, object] | None = None

    async def execute(
        self, statement: object, parameters: dict[str, object] | None = None
    ) -> FakeResult:
        sql = str(statement)
        self.executed_sql.append(" ".join(sql.split()))
        if "INSERT INTO public.projection_event_receipt" in sql:
            return FakeResult([{"event_id": "event-1"}] if self.receipt_exists else [])
        if "SELECT projection_version" in sql:
            return FakeResult(
                []
                if self.existing_version is None
                else [{"projection_version": self.existing_version}]
            )
        if "FROM public.student\n" in sql:
            return FakeResult([] if self.header is None else [self.header])
        if "FROM public.student_requirement" in sql:
            return FakeResult(self.requirements)
        if "INSERT INTO public.student_portal_projection" in sql:
            self.projection_parameters = dict(parameters or {})
        return FakeResult()


class BeginContext(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def begin(self) -> BeginContext:
        return BeginContext(self.connection)


def _header() -> dict[str, object]:
    return {
        "student_id": "student-1",
        "preferred_name": "  ",
        "first_name": " Alex ",
        "last_name": " Morgan ",
        "class_year": 2030,
        "offer_id": "offer-1",
        "program_name": "Biology",
        "term_name": "Fall 2026",
        "campus_name": "Main Campus",
        "response_deadline": date(2026, 9, 1),
        "deposit_amount_cents": 25000,
        "offer_status": "accepted",
        "journey_id": "journey-1",
        "journey_status": "active",
    }


def _requirements() -> list[dict[str, object]]:
    return [
        {
            "id": "requirement-1",
            "code": "profile",
            "title": "Complete profile",
            "description": "Done",
            "status": "completed",
            "blocking": True,
            "due_at": None,
            "progress_percent": 100,
        },
        {
            "id": "requirement-2",
            "code": "transcript review",
            "title": "Upload transcript",
            "description": "Required",
            "status": "in_progress",
            "blocking": True,
            "due_at": datetime(2026, 8, 20, 12, tzinfo=UTC),
            "progress_percent": 50,
        },
    ]


def _projector(connection: FakeConnection) -> StudentDashboardProjector:
    return StudentDashboardProjector(
        cast(Any, FakeEngine(connection)),
        "dashboard-v1",
    )


@pytest.mark.parametrize("consumer_name", ["", " ", "x" * 121])
def test_projector_requires_bounded_consumer_identity(consumer_name: str) -> None:
    with pytest.raises(ValueError, match="consumer_name"):
        StudentDashboardProjector(cast(Any, object()), consumer_name)


def test_projector_builds_versioned_dashboard_from_authoritative_rows() -> None:
    connection = FakeConnection()

    asyncio.run(_projector(connection).handle(_event()))

    assert connection.projection_parameters is not None
    assert connection.projection_parameters["projection_version"] == 3
    dashboard = json.loads(cast(str, connection.projection_parameters["dashboard"]))
    assert dashboard["student"] == {
        "id": "student-1",
        "preferredName": "Alex",
        "fullName": "Alex Morgan",
        "classYear": 2030,
    }
    assert dashboard["offer"]["responseDeadline"] == "2026-09-01"
    assert dashboard["journey"]["completionPercent"] == 75
    assert dashboard["journey"]["nextAction"] == {
        "code": "transcript review",
        "label": "Upload transcript",
        "href": "/enrollment?requirement=transcript%20review",
    }
    assert dashboard["journey"]["requirements"][1]["dueAt"] == ("2026-08-20T12:00:00+00:00")
    assert dashboard["projectionVersion"] == 3
    assert dashboard["generatedAt"].endswith("Z")
    assert any("pg_advisory_xact_lock" in sql for sql in connection.executed_sql)


def test_projector_is_idempotent_for_already_consumed_event() -> None:
    connection = FakeConnection(receipt_exists=False)

    asyncio.run(_projector(connection).handle(_event()))

    assert len(connection.executed_sql) == 1
    assert connection.projection_parameters is None


def test_projector_uses_review_fallback_when_no_requirements_exist() -> None:
    connection = FakeConnection(existing_version=None, requirements=[])

    asyncio.run(_projector(connection).handle(_event()))

    dashboard = json.loads(cast(str, connection.projection_parameters["dashboard"]))  # type: ignore[index]
    assert dashboard["journey"]["completionPercent"] == 0
    assert dashboard["journey"]["nextAction"] == {
        "code": "review_enrollment",
        "label": "Review your enrollment",
        "href": "/enrollment",
    }


def test_projector_marks_completed_zero_step_journey_fully_complete() -> None:
    header = _header()
    header["journey_status"] = "completed"
    connection = FakeConnection(existing_version=None, header=header, requirements=[])

    asyncio.run(_projector(connection).handle(_event()))

    dashboard = json.loads(cast(str, connection.projection_parameters["dashboard"]))  # type: ignore[index]
    assert dashboard["journey"]["completionPercent"] == 100
    assert dashboard["journey"]["nextAction"] == {
        "code": "enrollment_complete",
        "label": "Enrollment complete",
        "href": "/enrollment",
    }


@pytest.mark.parametrize(
    "data",
    (
        {},
        {"studentId": "student-1", "journeyId": "journey-1"},
        {"studentId": "student-1", "offerId": "offer-1"},
    ),
)
def test_projector_rejects_incomplete_event_identity(data: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="requires studentId"):
        asyncio.run(_projector(FakeConnection()).handle(_event(data)))


def test_projector_rejects_missing_authoritative_data_or_invalid_version() -> None:
    missing_header = FakeConnection()
    missing_header.header = None
    with pytest.raises(RuntimeError, match="authoritative enrollment data is missing"):
        asyncio.run(_projector(missing_header).handle(_event()))

    bad_version = FakeConnection(existing_version=-1)
    with pytest.raises(ValueError, match="projection_version"):
        asyncio.run(_projector(bad_version).handle(_event()))


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        (date(2026, 8, 2), "2026-08-02"),
        (datetime(2026, 8, 2, 12, tzinfo=UTC), "2026-08-02"),
        ("2026-08-02T12:00:00Z", "2026-08-02"),
    ),
)
def test_iso_date_accepts_database_date_shapes(value: object, expected: str) -> None:
    assert _iso_date(value) == expected


@pytest.mark.parametrize("value", [None, "bad", 123])
def test_iso_date_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="response deadline"):
        _iso_date(value)


def test_optional_timestamp_normalizes_supported_shapes() -> None:
    assert _iso_timestamp_or_none(None) is None
    assert _iso_timestamp_or_none(datetime(2026, 8, 2, tzinfo=UTC)) == ("2026-08-02T00:00:00+00:00")
    assert _iso_timestamp_or_none("2026-08-02T00:00:00Z") == ("2026-08-02T00:00:00+00:00")
    with pytest.raises(ValueError, match="requirement due date"):
        _iso_timestamp_or_none("not-a-date")
