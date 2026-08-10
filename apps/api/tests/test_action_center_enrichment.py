from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from audentra.infrastructure.worker.action_center_enrichment import (
    ActionCenterEnrichmentRunner,
    ClaimedEnrichmentJob,
    EnrichmentSnapshot,
    _ai_safe_text,
    _ai_safe_text_list,
    _bounded_evidence_records,
    _bounded_json_value,
    _budgeted_evidence,
    _channel_results,
    _code,
    _communication_context,
    _document_context,
    _iso,
    _json_list,
    _optional_nonnegative_int,
    _outcome_context,
    _required_text,
    _revision,
    _task_context,
)


def _communication(
    identifier: str,
    *,
    channel: str,
    body: str,
    sequence: int,
) -> dict[str, object]:
    return {
        "id": identifier,
        "interaction_id": "interaction-1",
        "channel": channel,
        "direction": "inbound",
        "subject": "Enrollment follow-up",
        "body_excerpt": body,
        "delivery_status": "received",
        "source_sequence": sequence,
        "occurred_at": datetime(2026, 8, 8, 12, sequence, tzinfo=UTC),
    }


def test_combined_portal_email_and_call_context_fits_one_bounded_enrichment() -> None:
    communications = [
        _communication(
            f"portal-{index}",
            channel="portal",
            body="p" * 4_000,
            sequence=index + 1,
        )
        for index in range(10)
    ]
    communications.extend(
        _communication(
            f"email-{index}",
            channel="email",
            body="e" * 4_000,
            sequence=index + 11,
        )
        for index in range(5)
    )
    communications.append(
        _communication(
            "call-1",
            channel="voice",
            body="v" * 15_000,
            sequence=16,
        )
    )

    context = [_communication_context(item) for item in communications]
    selected = _budgeted_evidence(
        context,
        max_items=120,
        character_budget=70_000,
        newest_first=True,
    )

    assert len(selected) == 16
    assert len(str(selected[0]["body"])) == 3_000
    assert len(str(selected[-1]["body"])) == 12_000
    assert selected[-1]["channel"] == "voice"
    assert "id" not in selected[-1]
    assert "interactionId" not in selected[-1]


def test_context_budget_prefers_recent_evidence_without_reordering_it() -> None:
    values = [{"id": f"message-{index}", "body": "x" * 300} for index in range(10)]

    selected = _budgeted_evidence(
        values,
        max_items=10,
        character_budget=1_100,
        newest_first=True,
    )

    selected_ids = [str(item["id"]) for item in selected]
    assert selected_ids == sorted(selected_ids, key=lambda value: int(value.split("-")[1]))
    assert "message-9" in selected_ids
    assert "message-0" not in selected_ids


class _Result:
    def __init__(self, row: dict[str, object] | None = None) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, object] | None:
        return self._row


class _SummaryConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: object, parameters: object) -> _Result:
        del parameters
        sql = str(statement)
        self.statements.append(sql)
        if "FROM public.student\n" in sql:
            return _Result({"id": "student-1"})
        return _Result()


class _NotificationConnection:
    def __init__(self) -> None:
        self.executions: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement: object, parameters: Mapping[str, object]) -> _Result:
        sql = str(statement)
        values = dict(parameters)
        self.executions.append((sql, values))
        if "FROM public.staff_work_item item" in sql:
            return _Result(
                {
                    "assignee_id": None,
                    "active_assignee_id": None,
                    "component": "Admissions",
                    "key": "INQ-1234",
                }
            )
        if "INSERT INTO public.staff_notification" in sql:
            return _Result()
        if "SELECT id FROM public.staff_notification" in sql:
            return _Result({"id": "00000000-0000-4000-8000-000000000099"})
        return _Result()


def test_student_summary_stream_locks_student_before_allocating_version() -> None:
    connection = _SummaryConnection()
    runner = ActionCenterEnrichmentRunner(
        Any,  # type: ignore[arg-type]
        Any,  # type: ignore[arg-type]
        worker_id="worker-1",
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000001"),
    )
    job = ClaimedEnrichmentJob(
        id="job-1",
        tenant_id="tenant-1",
        purpose="student_summary",
        student_id="student-1",
        work_item_id=None,
        interaction_id=None,
        processing_source_version=1,
        attempts=1,
    )
    snapshot = EnrichmentSnapshot(
        context={},
        source_ids=(),
        source_revision=1,
        base_summary_version=0,
    )

    written = asyncio.run(
        runner._persist_student_summary(
            connection,  # type: ignore[arg-type]
            job=job,
            run_id="run-1",
            snapshot=snapshot,
            result={"keyFacts": [], "risks": [], "nextSteps": []},
            summary="Current student summary.",
            source_ids_json="[]",
            provider="deterministic",
            model="bounded-summary-v1",
            prompt_version="action-center-enrichment-v1",
        )
    )

    assert written is True
    student_lock = next(
        index for index, sql in enumerate(connection.statements) if "FROM public.student\n" in sql
    )
    revision_read = next(
        index
        for index, sql in enumerate(connection.statements)
        if "FROM public.student_summary_revision" in sql
    )
    assert "FOR UPDATE" in connection.statements[student_lock]
    assert student_lock < revision_read


def test_ai_update_dedupes_notification_but_emits_tenant_wide_realtime_event() -> None:
    connection = _NotificationConnection()
    runner = ActionCenterEnrichmentRunner(
        Any,  # type: ignore[arg-type]
        Any,  # type: ignore[arg-type]
        worker_id="worker-1",
        uuid_factory=lambda: UUID("00000000-0000-4000-8000-000000000001"),
    )
    job = ClaimedEnrichmentJob(
        id="job-1",
        tenant_id="tenant-1",
        purpose="student_summary",
        student_id="student-1",
        work_item_id="work-item-1",
        interaction_id="interaction-1",
        processing_source_version=7,
        attempts=1,
    )

    asyncio.run(
        runner._insert_update_notification(
            cast(Any, connection),
            job,
            summary_version=3,
        )
    )

    notification = next(
        values
        for sql, values in connection.executions
        if "INSERT INTO public.staff_notification" in sql
    )
    assert notification["dedupe_key"] == "work-item:work-item-1:attention"
    assert notification["tenant_wide"] is True
    realtime = next(
        values
        for sql, values in connection.executions
        if "INSERT INTO public.staff_realtime_event" in sql
    )
    assert realtime["tenant_wide"] is True
    assert '"summaryVersion":3' in str(realtime["payload"])


class _RowsMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _RowsResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _RowsMappings:
        return _RowsMappings(self._rows)

    def scalar_one(self) -> object:
        assert len(self._rows) == 1 and len(self._rows[0]) == 1
        return next(iter(self._rows[0].values()))


class _EnrichmentConnection:
    def __init__(self, *, lease_owner: str = "worker-1") -> None:
        self.lease_owner = lease_owner
        self.executions: list[tuple[str, dict[str, object]]] = []

    async def execute(
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> _RowsResult:
        sql = str(statement)
        values = dict(parameters or {})
        self.executions.append((sql, values))
        now = datetime(2026, 8, 8, 12, tzinfo=UTC)
        if "COALESCE(profile.preferred_name" in sql:
            return _RowsResult(
                [
                    {
                        "id": "student-1",
                        "preferred_name": "Taylor",
                        "first_name": "Taylor",
                        "last_name": "Nguyen",
                        "class_year": 2027,
                        "communication_preference": "portal",
                        "program_name": "Computer Science",
                        "offer_status": "accepted",
                        "journey_status": "in_progress",
                        "journey_version": 5,
                        "onboarding_status": "completed",
                        "onboarding_current_step": "complete",
                        "onboarding_version": 8,
                        "source_updated_at": now,
                    }
                ]
            )
        if "SELECT id, key, title, description" in sql:
            return _RowsResult([_enrichment_work_item(now)])
        if "SELECT requirement.id, definition.code" in sql:
            return _RowsResult(
                [
                    {
                        "id": "requirement-1",
                        "code": "official_transcript",
                        "title": "Official transcript",
                        "flow_kind": "enrollment",
                        "blocking": True,
                        "status": "under_review",
                        "progress_percent": 75,
                        "due_at": now,
                        "version": 3,
                        "retired_at": None,
                        "retired_reason": None,
                        "updated_at": now,
                    }
                ]
            )
        if "SELECT id, file_name, category, status" in sql:
            return _RowsResult(
                [
                    {
                        "id": "document-1",
                        "file_name": "transcript.pdf",
                        "category": "transcript",
                        "status": "parsed",
                        "processing_mode": "automatic",
                        "requirement_id": "requirement-1",
                        "extraction": json.dumps(
                            {
                                "documentType": "transcript",
                                "summary": "57 courses parsed.",
                                "institutionName": "METU",
                                "academicTerm": "Fall 2026",
                                "fields": [{"name": "student", "value": "Taylor"}],
                                "courses": [{"code": "CS101", "grade": "A"}],
                                "warnings": [],
                            }
                        ),
                        "created_at": now,
                        "updated_at": now,
                    }
                ]
            )
        if "FROM public.communication_event AS communication" in sql:
            return _RowsResult(
                [
                    {
                        "id": "communication-1",
                        "interaction_id": "interaction-1",
                        "channel": "portal",
                        "direction": "outbound",
                        "subject": "Transcript review update",
                        "body_excerpt": "The transcript is ready for Registrar review.",
                        "delivery_status": "delivered",
                        "source_sequence": 1,
                        "occurred_at": now,
                    }
                ]
            )
        if "FROM public.interaction_outcome_revision AS outcome" in sql:
            return _RowsResult(
                [
                    {
                        "id": "outcome-previous",
                        "interaction_id": "interaction-previous",
                        "summary": "The student provided the requested record.",
                        "channel_results": '[{"channel":"portal","result":"Delivered"}]',
                        "outcome_code": "student_reached",
                        "resolution_code": "resolved_by_staff",
                        "next_step": "Registrar review",
                        "follow_up_required": False,
                        "generated_at": now,
                    }
                ]
            )
        if "SELECT version, summary, key_facts" in sql:
            return _RowsResult(
                [
                    {
                        "version": 2,
                        "summary": (
                            "Taylor uploaded the record. 00000000-0000-4000-8000-000000000099"
                        ),
                        "key_facts": '["Transcript uploaded"]',
                        "risks": '["Review pending"]',
                        "next_steps": '["Registrar review"]',
                        "source_revision": 2,
                        "generated_at": now,
                    }
                ]
            )
        if "SELECT requested_source_version" in sql:
            return _RowsResult(
                [
                    {
                        "requested_source_version": 1,
                        "processing_source_version": 1,
                        "status": "running",
                        "lease_owner": self.lease_owner,
                        "work_item_id": "work-item-1",
                        "interaction_id": "interaction-1",
                    }
                ]
            )
        if "SELECT source_version, covered_source_version" in sql:
            return _RowsResult(
                [
                    {
                        "source_version": 1,
                        "covered_source_version": 0,
                        "completed_at": now,
                    }
                ]
            )
        if "FROM public.task_insight_revision" in sql and "COALESCE(MAX(version)" in sql:
            return _RowsResult([{"next_version": 3}])
        if "FROM public.interaction_outcome_revision" in sql and "COALESCE(MAX(version)" in sql:
            return _RowsResult([{"next_version": 4}])
        if "SELECT id\n                FROM public.student" in sql:
            return _RowsResult([{"id": "student-1"}])
        if "SELECT version\n                FROM public.student_summary_revision" in sql:
            return _RowsResult([{"version": 2}])
        if "SELECT version\n                FROM public.staff_work_item" in sql:
            return _RowsResult([{"version": 4}])
        if "SELECT item.assignee_id, item.component, item.key" in sql:
            return _RowsResult(
                [
                    {
                        "assignee_id": "staff-1",
                        "active_assignee_id": "staff-1",
                        "component": "Registrar",
                        "key": "MAN-12345678",
                    }
                ]
            )
        if "INSERT INTO public.staff_notification" in sql and "RETURNING id" in sql:
            return _RowsResult([{"id": "notification-1"}])
        if "UPDATE public.action_center_ai_job" in sql and "RETURNING status" in sql:
            return _RowsResult([{"status": "failed_retryable"}])
        return _RowsResult()


class _EnrichmentContext(AbstractAsyncContextManager[_EnrichmentConnection]):
    def __init__(self, connection: _EnrichmentConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _EnrichmentConnection:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _EnrichmentEngine:
    def __init__(self, connection: _EnrichmentConnection) -> None:
        self.connection = connection

    def begin(self) -> _EnrichmentContext:
        return _EnrichmentContext(self.connection)


class _Gateway:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.contexts: list[Mapping[str, Any]] = []

    async def enrich_action_center(
        self,
        *,
        context: Mapping[str, Any],
        tenant_id: str,
        student_id: str,
        request_id: str,
        attempt: int = 1,
    ) -> dict[str, Any]:
        del tenant_id, student_id, request_id, attempt
        self.contexts.append(context)
        if self.failure is not None:
            raise self.failure
        return {
            "provider": "openrouter",
            "model": "openai/gpt-5.6-luna-pro",
            "promptVersion": "action-center-enrichment-v1",
            "taskSummary": "Registrar review of the parsed transcript is pending.",
            "whyThisMatters": "The transcript controls enrollment progression.",
            "taskObjective": "Complete Registrar review.",
            "successDefinition": "The transcript is accepted or returned with guidance.",
            "suggestedApproach": "Review the extracted courses and notify the student.",
            "suggestedChannel": "portal",
            "outcomeSummary": "The student was reached and the record is under review.",
            "studentSummary": "Taylor submitted a transcript that awaits final review.",
            "channelResults": [
                {"channel": "portal", "result": "Update delivered"},
                {"channel": "invalid", "result": "Ignored"},
            ],
            "outcomeCode": "student_reached",
            "resolutionCode": "resolved_by_staff",
            "nextStep": "Registrar completes final review.",
            "followUpRequired": False,
            "confidence": 1.2,
            "conversationSignals": {
                "sentiment": {"label": "Positive", "score": 0.8},
                "engagement": {"label": "High", "score": 0.9},
                "intent": "Document completion",
                "likelihoodToProgress": {"label": "High", "score": 0.85},
            },
            "keyFacts": ["Transcript parsed", "Student notified"],
            "risks": ["Registrar review pending"],
            "nextSteps": ["Complete final review"],
            "usage": {"inputTokens": "1250", "outputTokens": 220},
        }


def _enrichment_work_item(now: datetime) -> dict[str, object]:
    return {
        "id": "work-item-1",
        "key": "MAN-12345678",
        "title": "Review parsed transcript",
        "description": "Confirm the extracted academic record.",
        "status": "in_progress",
        "priority": "urgent",
        "work_type": "enrollment",
        "action_type": "document_review",
        "component": "Registrar",
        "due_at": now,
        "escalated": False,
        "selected_channel": "portal",
        "attempt_count": 1,
        "follow_up_at": None,
        "blocker_code": None,
        "blocker_detail": None,
        "blocker_review_at": None,
        "outcome_code": None,
        "resolution_code": None,
        "next_step": None,
        "terminal_reason": None,
        "version": 4,
        "created_at": now,
        "updated_at": now,
    }


def _enrichment_job(purpose: str, *, interaction: bool) -> ClaimedEnrichmentJob:
    return ClaimedEnrichmentJob(
        id=f"job-{purpose}",
        tenant_id="tenant-1",
        purpose=purpose,
        student_id="student-1",
        work_item_id="work-item-1",
        interaction_id="interaction-1" if interaction else None,
        processing_source_version=1,
        attempts=1,
    )


def test_interaction_enrichment_persists_one_outcome_and_canonical_student_summary() -> None:
    connection = _EnrichmentConnection()
    gateway = _Gateway()
    runner = ActionCenterEnrichmentRunner(
        cast(Any, _EnrichmentEngine(connection)),
        gateway,
        worker_id="worker-1",
        uuid_factory=uuid4,
    )

    processed = asyncio.run(
        runner._process(_enrichment_job("interaction_enrichment", interaction=True))
    )

    assert processed is True
    assert gateway.contexts[0]["student"]["displayName"] == "Taylor"
    assert gateway.contexts[0]["contextCoverage"]["communications"]["channelCounts"] == {
        "portal": 1
    }
    assert "[redacted identifier]" in gateway.contexts[0]["previousStudentSummary"]["summary"]
    statements = [sql for sql, _ in connection.executions]
    assert any("INSERT INTO public.interaction_outcome_revision" in sql for sql in statements)
    outcome_values = next(
        values
        for sql, values in connection.executions
        if "INSERT INTO public.interaction_outcome_revision" in sql
    )
    assert json.loads(str(outcome_values["conversation_signals"]))["intent"] == (
        "Document completion"
    )
    assert any("INSERT INTO public.student_summary_revision" in sql for sql in statements)
    assert any("INSERT INTO public.staff_realtime_event" in sql for sql in statements)
    assert any("INSERT INTO public.model_usage" in sql for sql in statements)


def test_task_insight_enrichment_persists_guidance_with_student_summary() -> None:
    connection = _EnrichmentConnection()
    runner = ActionCenterEnrichmentRunner(
        cast(Any, _EnrichmentEngine(connection)),
        _Gateway(),
        worker_id="worker-1",
        uuid_factory=uuid4,
    )

    processed = asyncio.run(runner._process(_enrichment_job("task_insight", interaction=False)))

    assert processed is True
    statements = [sql for sql, _ in connection.executions]
    assert any("INSERT INTO public.task_insight_revision" in sql for sql in statements)
    assert not any("INSERT INTO public.interaction_outcome_revision" in sql for sql in statements)


def test_enrichment_failure_stays_retryable_without_losing_source_evidence() -> None:
    connection = _EnrichmentConnection()
    runner = ActionCenterEnrichmentRunner(
        cast(Any, _EnrichmentEngine(connection)),
        _Gateway(failure=RuntimeError("provider temporarily unavailable")),
        worker_id="worker-1",
        uuid_factory=uuid4,
    )

    processed = asyncio.run(
        runner._process(_enrichment_job("interaction_enrichment", interaction=True))
    )

    assert processed is False
    failure_values = next(
        values
        for sql, values in connection.executions
        if "UPDATE public.action_center_ai_job" in sql and "RETURNING status" in sql
    )
    assert failure_values["error_code"] == "RuntimeError"
    assert failure_values["error_message"] == "provider temporarily unavailable"
    assert failure_values["retry_delay"] == 5


def test_enrichment_context_helpers_reject_untrusted_shapes_and_bound_values() -> None:
    now = datetime(2026, 8, 8, 12)
    work_item = _enrichment_work_item(now.replace(tzinfo=UTC))
    assert _task_context(None) is None
    task_context = _task_context(work_item)
    assert task_context is not None
    assert task_context["updatedAt"].endswith("Z")
    assert _document_context(
        {
            "file_name": "record.pdf",
            "category": "transcript",
            "status": "parsed",
            "processing_mode": "automatic",
            "extraction": {"fields": ["plain"], "courses": "not-json"},
            "updated_at": now,
        }
    )["extraction"]["fields"] == [{"value": "plain"}]
    assert (
        _outcome_context(
            {
                "summary": "done",
                "channel_results": "invalid",
                "outcome_code": "done",
                "resolution_code": "resolved",
                "next_step": None,
                "follow_up_required": False,
                "generated_at": now,
            }
        )["channelResults"]
        == []
    )
    assert _channel_results([None, {"channel": "email", "result": " delivered "}]) == [
        {"channel": "email", "result": "delivered"}
    ]
    assert _json_list('[1,{"ok":true}]') == [1, {"ok": True}]
    assert _json_list("invalid") == []
    assert _bounded_evidence_records(
        [{"nested": {"items": list(range(30))}}],
        max_items=2,
        character_budget=10_000,
    )
    assert _bounded_json_value(object(), depth=3)
    assert _ai_safe_text("00000000-0000-4000-8000-000000000001", 100) == "[redacted identifier]"
    assert _ai_safe_text_list('["same","same",3]', 5, 100) == ["same"]
    assert _code(" Student Reached ") == "student_reached"
    assert _code("bad/code") is None
    assert _optional_nonnegative_int(False) is None
    assert _optional_nonnegative_int("12") == 12
    assert _optional_nonnegative_int("bad") is None
    assert _optional_nonnegative_int(-1) is None
    assert _revision(now) > 0
    assert _revision("not-a-date") == 0
    assert _iso("not-a-date") is None
    with pytest.raises(ValueError, match="omitted required summary"):
        _required_text("   ", 20)


@pytest.mark.parametrize(
    ("batch_size", "concurrency", "lease_seconds", "message"),
    [
        (0, 1, 60, "batch_size"),
        (1, 0, 60, "concurrency"),
        (1, 1, 59, "lease_seconds"),
    ],
)
def test_enrichment_runner_rejects_unsafe_worker_bounds(
    batch_size: int,
    concurrency: int,
    lease_seconds: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ActionCenterEnrichmentRunner(
            cast(Any, object()),
            _Gateway(),
            worker_id="worker-1",
            batch_size=batch_size,
            concurrency=concurrency,
            lease_seconds=lease_seconds,
        )
