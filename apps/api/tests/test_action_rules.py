from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.infrastructure.worker.agentic_scheduler import AgenticWorkflowScheduler


class FakeResult:
    def __init__(self, rows: list[Mapping[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> Mapping[str, Any] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[Mapping[str, Any]]:
        return self.rows


class FakeConnection:
    def __init__(
        self,
        *,
        duplicate_execution: bool = False,
        due_lifecycle_rows: list[Mapping[str, Any]] | None = None,
        action_rule_rows: list[Mapping[str, Any]] | None = None,
        inbox_rows: list[Mapping[str, Any]] | None = None,
    ) -> None:
        self.duplicate_execution = duplicate_execution
        self.due_lifecycle_rows = due_lifecycle_rows or []
        self.action_rule_rows = action_rule_rows or []
        self.inbox_rows = inbox_rows or []
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    async def execute(
        self,
        statement: object,
        params: Mapping[str, Any] | None = None,
    ) -> FakeResult:
        sql = str(statement)
        values = params or {}
        self.calls.append((sql, values))
        if "WITH due AS" in sql and "blocked_review_due" in sql:
            return FakeResult(self.due_lifecycle_rows)
        if "FROM public.staff_action_rule" in sql:
            return FakeResult(self.action_rule_rows)
        if "UPDATE public.inbox_event AS event" in sql:
            return FakeResult(self.inbox_rows)
        if "FROM public.credential_account" in sql:
            return FakeResult([{"student_id": "student-1"}])
        if "INSERT INTO public.communication_event" in sql and "RETURNING id" in sql:
            return FakeResult([{"id": values["id"]}])
        if "INSERT INTO public.staff_action_rule_execution" in sql:
            return FakeResult([] if self.duplicate_execution else [{"id": values["id"]}])
        if "SELECT id FROM public.staff_member" in sql:
            return FakeResult([{"id": "staff-1"}])
        return FakeResult([])


class FakeContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc, traceback


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    def begin(self) -> FakeContext:
        return FakeContext(self.connection)

    def connect(self) -> FakeContext:
        return FakeContext(self.connection)


class RuleProbeScheduler(AgenticWorkflowScheduler):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self.candidates: list[dict[str, Any]] = [_candidate()]
        self.created: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def _action_rule_candidates(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        del rule
        return self.candidates

    async def _create_rule_work_item(
        self,
        rule: dict[str, Any],
        candidate: dict[str, Any],
    ) -> bool:
        self.created.append((rule, candidate))
        return True


class InboxProbeScheduler(AgenticWorkflowScheduler):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self.appended: list[tuple[str, str]] = []
        self.attached: list[tuple[str, str]] = []

    async def _student_deadline_facts(
        self,
        connection: Any,
        tenant_id: str,
        student_id: str,
    ) -> tuple[datetime | None, bool]:
        del connection, tenant_id, student_id
        return datetime(2026, 8, 11, 12, tzinfo=UTC), True

    async def _existing_work_item(
        self,
        connection: Any,
        tenant_id: str,
        student_id: str,
    ) -> str | None:
        del connection, tenant_id, student_id
        return "work-1"

    async def _existing_inquiry(
        self,
        connection: Any,
        tenant_id: str,
        student_id: str,
    ) -> str | None:
        del connection, tenant_id, student_id
        return None

    async def _append_work_item_context(
        self,
        connection: Any,
        tenant_id: str,
        work_item_id: str,
        communication_id: object,
        body: str,
        priority: str,
    ) -> None:
        del connection, tenant_id, body, priority
        self.appended.append((work_item_id, str(communication_id)))

    async def _attach_communication_to_interaction(
        self,
        connection: Any,
        *,
        tenant_id: str,
        student_id: str,
        work_item_id: str,
        communication_id: str,
        inbox_id: str,
        objective: str,
    ) -> None:
        del connection, tenant_id, student_id, inbox_id, objective
        self.attached.append((work_item_id, communication_id))


class RunOnceProbeScheduler(AgenticWorkflowScheduler):
    async def _process_inbox_events(self) -> int:
        return 1

    async def _scan_engagement(self) -> int:
        return 2

    async def _run_action_rules(self) -> int:
        return 3

    async def _run_due_work_item_actions(self) -> int:
        return 4


def _uuids() -> Iterator[UUID]:
    for value in range(1, 10):
        yield UUID(f"00000000-0000-7000-8000-{value:012d}")


def _rule() -> dict[str, Any]:
    return {
        "id": "rule-1",
        "tenant_id": "tenant-1",
        "code": "transcript-due-soon",
        "name": "Transcript due soon",
        "signal_type": "requirement_due",
        "flow_kind": "enrollment",
        "requirement_code": "official_transcript",
        "lookahead_days": 3,
        "inactivity_days": None,
        "component": "Registrar",
        "priority": "high",
        "action_type": "deadline_risk",
        "title_template": "Follow up with {studentName}: {requirementTitle}",
        "description_template": "{requirementTitle} is due in {daysRemaining} days.",
    }


def _candidate() -> dict[str, Any]:
    return {
        "subject_id": "requirement-1",
        "student_id": "student-1",
        "student_name": "Alex Morgan",
        "requirement_code": "official_transcript",
        "requirement_title": "Official transcript",
        "due_at": datetime(2026, 8, 11, 12, tzinfo=UTC),
        "days_remaining": 3,
        "window_key": "requirement:requirement-1:2026-08-11",
    }


def test_scheduled_rule_creates_one_auditable_staff_action_without_an_llm() -> None:
    connection = FakeConnection()
    values = _uuids()
    scheduler = AgenticWorkflowScheduler(
        cast(AsyncEngine, FakeEngine(connection)),
        uuid_factory=lambda: next(values),
    )

    created = asyncio.run(scheduler._create_rule_work_item(_rule(), _candidate()))

    assert created is True
    work_sql, work_params = next(
        call for call in connection.calls if "INSERT INTO public.staff_work_item" in call[0]
    )
    assert "'scheduled_rule'" in work_sql
    assert work_params["title"] == "Follow up with Alex Morgan: Official transcript"
    assert work_params["description"] == "Official transcript is due in 3 days."
    assert work_params["student_id"] == "student-1"
    assert work_params["action_type"] == "deadline_risk"

    log_sql, log_params = next(
        call for call in connection.calls if "INSERT INTO public.staff_work_log" in call[0]
    )
    assert "actor_type" in log_sql
    assert "occurred_at" in log_sql
    assert "scheduled_rule_matched" in log_sql
    assert log_params["work_item_id"] == work_params["id"]

    notification_sql, notification_params = next(
        call for call in connection.calls if "INSERT INTO public.staff_notification" in call[0]
    )
    assert "ON CONFLICT (tenant_id, dedupe_key) DO NOTHING" in notification_sql
    assert notification_params["staff_member_id"] == "staff-1"
    assert notification_params["resource_id"] == work_params["id"]


def test_scheduled_rule_execution_dedupe_prevents_duplicate_staff_work() -> None:
    connection = FakeConnection(duplicate_execution=True)
    values = _uuids()
    scheduler = AgenticWorkflowScheduler(
        cast(AsyncEngine, FakeEngine(connection)),
        uuid_factory=lambda: next(values),
    )

    created = asyncio.run(scheduler._create_rule_work_item(_rule(), _candidate()))

    assert created is False
    assert not any("INSERT INTO public.staff_work_item" in sql for sql, _ in connection.calls)


def test_inactivity_rule_honors_its_enrollment_or_onboarding_scope() -> None:
    connection = FakeConnection()
    scheduler = AgenticWorkflowScheduler(cast(AsyncEngine, FakeEngine(connection)))
    rule = {
        **_rule(),
        "signal_type": "student_inactive",
        "flow_kind": "onboarding",
        "requirement_code": None,
        "lookahead_days": None,
        "inactivity_days": 5,
    }

    candidates = asyncio.run(scheduler._action_rule_candidates(rule))

    assert candidates == []
    sql, params = connection.calls[-1]
    assert "FROM public.student_onboarding onboarding" in sql
    assert "onboarding.status <> 'completed'" in sql
    assert "journey.status NOT IN ('completed', 'cancelled')" in sql
    assert "CAST(:flow_kind AS varchar) IS NULL" in sql
    assert "CAST(:flow_kind AS varchar) = 'onboarding'" in sql
    assert params["flow_kind"] == "onboarding"


def test_due_rule_casts_optional_filters_for_asyncpg() -> None:
    connection = FakeConnection()
    scheduler = AgenticWorkflowScheduler(cast(AsyncEngine, FakeEngine(connection)))

    candidates = asyncio.run(scheduler._action_rule_candidates(_rule()))

    assert candidates == []
    sql, params = connection.calls[-1]
    assert "CAST(:flow_kind AS varchar) IS NULL" in sql
    assert "definition.flow_kind = CAST(:flow_kind AS varchar)" in sql
    assert "CAST(:requirement_code AS varchar) IS NULL" in sql
    assert "definition.code = CAST(:requirement_code AS varchar)" in sql
    assert params["flow_kind"] == "enrollment"
    assert params["requirement_code"] == "official_transcript"


def test_due_lifecycle_actions_remind_follow_ups_and_escalate_blockers_without_ai() -> None:
    due_rows: list[Mapping[str, Any]] = [
        {
            "id": "work-follow-up",
            "tenant_id": "tenant-1",
            "assignee_id": "staff-1",
            "component": "Admissions",
            "title": "Call the student",
            "version": 5,
            "reason": "follow_up_due",
        },
        {
            "id": "work-blocked",
            "tenant_id": "tenant-1",
            "assignee_id": None,
            "component": "Registrar",
            "title": "Review transcript blocker",
            "version": 3,
            "reason": "blocked_review_due",
        },
    ]
    connection = FakeConnection(due_lifecycle_rows=due_rows)
    values = _uuids()
    scheduler = AgenticWorkflowScheduler(
        cast(AsyncEngine, FakeEngine(connection)),
        uuid_factory=lambda: next(values),
    )

    processed = asyncio.run(scheduler._run_due_work_item_actions())

    assert processed == 2
    lifecycle_sql = connection.calls[0][0]
    assert "WHEN due.reason = 'follow_up_due' THEN 'todo'" in lifecycle_sql
    assert "item.attempt_count + 1" in lifecycle_sql
    assert "item.status NOT IN ('done', 'cancelled')" in lifecycle_sql
    logs = [
        params for sql, params in connection.calls if "INSERT INTO public.staff_work_log" in sql
    ]
    assert [entry["action"] for entry in logs] == ["status_changed", "escalated"]
    notifications = [
        params for sql, params in connection.calls if "INSERT INTO public.staff_notification" in sql
    ]
    assert notifications[0]["staff_member_id"] == "staff-1"
    assert notifications[0]["team_component"] is None
    assert notifications[1]["staff_member_id"] is None
    assert notifications[1]["team_component"] == "Registrar"


def test_due_action_rules_are_evaluated_and_checkpointed_after_a_bounded_scan() -> None:
    connection = FakeConnection(action_rule_rows=[_rule()])
    scheduler = RuleProbeScheduler(cast(AsyncEngine, FakeEngine(connection)))

    matched = asyncio.run(scheduler._run_action_rules())

    assert matched == 1
    assert scheduler.created == [(_rule(), _candidate())]
    update = next(
        params for sql, params in connection.calls if "UPDATE public.staff_action_rule" in sql
    )
    assert update == {"tenant_id": "tenant-1", "rule_id": "rule-1"}


def test_inbox_processing_links_email_to_existing_action_and_records_auditable_decision() -> None:
    connection = FakeConnection(
        inbox_rows=[
            {
                "id": "inbox-1",
                "tenant_id": "tenant-1",
                "provider": "portal",
                "external_message_id": "portal-message-1",
                "sender_address": "student@example.edu",
                "subject": "Please help with my transcript",
                "body_excerpt": "Can you help me understand the transcript deadline?",
                "occurred_at": datetime(2026, 8, 8, 12, tzinfo=UTC),
            }
        ]
    )
    scheduler = InboxProbeScheduler(cast(AsyncEngine, FakeEngine(connection)))

    processed = asyncio.run(scheduler._process_inbox_events())

    assert processed == 1
    assert scheduler.appended[0][0] == "work-1"
    assert scheduler.attached[0][0] == "work-1"
    tool_call = next(
        params for sql, params in connection.calls if "INSERT INTO public.agent_tool_call" in sql
    )
    assert tool_call["tenant_id"] == "tenant-1"
    proposal = next(
        params
        for sql, params in connection.calls
        if "INSERT INTO public.agent_action_proposal" in sql
    )
    assert proposal["action_type"] == "append_to_existing_task"
    assert proposal["target_id"] == "work-1"
    resolution = next(
        params for sql, params in connection.calls if "UPDATE public.communication_event" in sql
    )
    assert resolution["resolution"] == "resolved"


def test_scheduler_run_once_combines_each_independent_worker_count_and_validates_limits() -> None:
    scheduler = RunOnceProbeScheduler(cast(AsyncEngine, FakeEngine(FakeConnection())))

    assert asyncio.run(scheduler.run_once()) == 10

    with pytest.raises(ValueError, match="inbox_limit"):
        AgenticWorkflowScheduler(
            cast(AsyncEngine, FakeEngine(FakeConnection())),
            inbox_limit=0,
        )
