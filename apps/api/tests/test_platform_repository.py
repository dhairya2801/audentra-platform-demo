from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.postgres.platform_repository import (
    PostgresPlatformRepository,
    offer_request_hash,
    validate_activity_event_properties,
)

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
ACTOR_ID = "00000000-0000-7000-8000-000000000100"
OFFER_ID = "00000000-0000-7000-8000-000000000201"
JOURNEY_ID = "00000000-0000-7000-8000-000000000301"
DEFINITION_ID = "00000000-0000-7000-8000-000000000302"
RULE_ID = "00000000-0000-7000-8000-000000000401"
NOW = datetime(2026, 8, 2, 10, 30, tzinfo=UTC)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def auth() -> AuthContext:
    return AuthContext(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        actor_id=ACTOR_ID,
        actor_type="student",
    )


class FakeResult:
    def __init__(self, rows: list[Mapping[str, object]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[Mapping[str, object]]:
        return self._rows


class FakeConnection:
    def __init__(
        self,
        handler: Callable[[str, Mapping[str, object]], list[Mapping[str, object]]],
    ) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement: Any, parameters: Mapping[str, object]) -> FakeResult:
        sql = str(statement)
        copied = dict(parameters)
        self.calls.append((sql, copied))
        return FakeResult(self.handler(sql, copied))


class FakeContext:
    def __init__(self, engine: FakeEngine, transactional: bool) -> None:
        self.engine = engine
        self.transactional = transactional

    async def __aenter__(self) -> FakeConnection:
        if self.transactional:
            self.engine.begin_entries += 1
        else:
            self.engine.connect_entries += 1
        return self.engine.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        if self.transactional:
            if exc_type is None:
                self.engine.commits += 1
            else:
                self.engine.rollbacks += 1


class FakeEngine:
    def __init__(
        self,
        handler: Callable[[str, Mapping[str, object]], list[Mapping[str, object]]],
    ) -> None:
        self.connection = FakeConnection(handler)
        self.begin_entries = 0
        self.connect_entries = 0
        self.commits = 0
        self.rollbacks = 0

    def begin(self) -> FakeContext:
        return FakeContext(self, True)

    def connect(self) -> FakeContext:
        return FakeContext(self, False)


class IncrementingUUIDs:
    def __init__(self, start: int = 1_000) -> None:
        self.value = start

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(int=self.value)


def repository(engine: FakeEngine) -> PostgresPlatformRepository:
    return PostgresPlatformRepository(
        cast(AsyncEngine, engine),
        clock=lambda: NOW,
        uuid_factory=IncrementingUUIDs(),
    )


def normalized(sql: str) -> str:
    return " ".join(sql.split()).lower()


def test_offer_request_hash_matches_legacy_json_bytes() -> None:
    raw = f'{{"tenantId":"{TENANT_ID}","studentId":"{STUDENT_ID}","offerId":"{OFFER_ID}"}}'

    assert offer_request_hash(auth(), OFFER_ID) == hashlib.sha256(raw.encode()).hexdigest()


def test_dashboard_maps_rewards_next_action_and_js_rounding() -> None:
    def handler(sql: str, parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        assert parameters["tenant_id"] == UUID(TENANT_ID)
        if "from public.student s" in query:
            assert parameters["student_id"] == UUID(STUDENT_ID)
            return [
                {
                    "student_id": STUDENT_ID,
                    "preferred_name": None,
                    "first_name": "Avery",
                    "last_name": "Stone",
                    "class_year": 2030,
                    "offer_id": OFFER_ID,
                    "program_name": "Computer Science",
                    "term_name": "Fall 2026",
                    "campus_name": "Main Campus",
                    "response_deadline": "2026-08-15",
                    "deposit_amount_cents": 50000,
                    "offer_status": "offered",
                    "offer_version": 3,
                    "journey_id": UUID(JOURNEY_ID),
                    "journey_status": "in_progress",
                    "projection_version": "2",
                    "unread_message_count": "4",
                }
            ]
        assert "from public.student_requirement sr" in query
        assert parameters["journey_id"] == UUID(JOURNEY_ID)
        return [
            {
                "id": "00000000-0000-7000-8000-000000000501",
                "code": "profile review",
                "title": "Profile review",
                "description": "Review your profile",
                "status": "blocked",
                "blocking": 1,
                "due_at": NOW,
                "progress_percent": 40,
                "reward_points": 25,
                "reward_earned": True,
            },
            {
                "id": "00000000-0000-7000-8000-000000000502",
                "code": "transcript",
                "title": "Upload transcript",
                "description": "Upload the official record",
                "status": "ready",
                "blocking": 0,
                "due_at": None,
                "progress_percent": 41,
                "reward_points": 0,
                "reward_earned": False,
            },
        ]

    engine = FakeEngine(handler)
    result = run(repository(engine).get_student_dashboard(auth()))

    assert engine.connect_entries == 1
    assert result["student"]["preferredName"] == "Avery"
    assert result["journey"]["completionPercent"] == 41
    assert result["journey"]["nextAction"] == {
        "code": "transcript",
        "label": "Upload transcript",
        "href": "/enrollment?requirement=transcript",
    }
    assert result["journey"]["requirements"][0]["reward"] == {
        "points": 25,
        "earned": True,
    }
    assert result["projectionVersion"] == 3
    assert result["generatedAt"] == "2026-08-02T10:30:00.000Z"


def test_dashboard_maps_completed_zero_step_journey_as_fully_complete() -> None:
    def handler(sql: str, _parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        if "from public.student s" in query:
            return [
                {
                    "student_id": STUDENT_ID,
                    "preferred_name": "Avery",
                    "first_name": "Avery",
                    "last_name": "Stone",
                    "class_year": 2030,
                    "offer_id": OFFER_ID,
                    "program_name": "Computer Science",
                    "term_name": "Fall 2026",
                    "campus_name": "Main Campus",
                    "response_deadline": "2026-08-15",
                    "deposit_amount_cents": 50000,
                    "offer_status": "accepted",
                    "offer_version": 2,
                    "journey_id": UUID(JOURNEY_ID),
                    "journey_status": "completed",
                    "projection_version": 2,
                    "unread_message_count": 0,
                }
            ]
        assert "from public.student_requirement sr" in query
        return []

    result = run(repository(FakeEngine(handler)).get_student_dashboard(auth()))

    assert result["journey"] == {
        "id": JOURNEY_ID,
        "status": "completed",
        "completionPercent": 100,
        "nextAction": {
            "code": "enrollment_complete",
            "label": "Enrollment complete",
            "href": "/enrollment",
        },
        "requirements": [],
    }


def test_idempotent_offer_replay_returns_stored_response_without_mutating() -> None:
    stored = {
        "offerId": OFFER_ID,
        "offerStatus": "accepted",
        "journeyId": JOURNEY_ID,
        "journeyStatus": "in_progress",
        "projectionVersion": 2,
        "acceptedAt": "2026-08-02T10:30:00.000Z",
    }

    def handler(sql: str, _parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        if "pg_advisory_xact_lock" in query:
            return []
        assert "from public.idempotency_record" in query
        return [
            {
                "request_hash": offer_request_hash(auth(), OFFER_ID),
                "response_body": json.dumps(stored),
            }
        ]

    engine = FakeEngine(handler)
    result = run(
        repository(engine).accept_admission_offer(
            auth(), OFFER_ID, "accept-offer-0001", "request-1"
        )
    )

    assert result == stored
    assert engine.commits == 1
    assert engine.rollbacks == 0
    assert len(engine.connection.calls) == 2
    assert engine.connection.calls[0][1]["lock_key"] == (
        f"{TENANT_ID}:{ACTOR_ID}:admission_offer.accept:accept-offer-0001"
    )


def test_offer_idempotency_hash_conflict_rolls_back_before_offer_lock() -> None:
    def handler(sql: str, _parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        if "idempotency_record" in sql:
            return [{"request_hash": "different", "response_body": {}}]
        return []

    engine = FakeEngine(handler)
    with pytest.raises(ApiError) as conflict:
        run(
            repository(engine).accept_admission_offer(
                auth(), OFFER_ID, "accept-offer-0001", "request-1"
            )
        )

    assert conflict.value.status_code == 409
    assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert engine.rollbacks == 1
    assert len(engine.connection.calls) == 2


def test_new_offer_acceptance_commits_state_audit_outbox_and_idempotency_together() -> None:
    requirement_one = "00000000-0000-7000-8000-000000000601"
    requirement_two = "00000000-0000-7000-8000-000000000602"

    def handler(sql: str, _parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        if "from public.idempotency_record" in query:
            return []
        if "from public.admission_offer" in query:
            return [
                {
                    "id": OFFER_ID,
                    "status": "offered",
                    "accepted_at": None,
                    "version": 1,
                    "is_expired": False,
                }
            ]
        if "from public.journey_definition_version" in query:
            return [{"journey_definition_version_id": DEFINITION_ID}]
        if query.startswith("update public.admission_offer"):
            return [{"version": 2}]
        if "from public.journey_requirement_definition" in query:
            return [
                {
                    "id": requirement_one,
                    "code": "profile",
                    "depends_on_codes": [],
                    "due_offset_days": 3,
                },
                {
                    "id": requirement_two,
                    "code": "transcript",
                    "depends_on_codes": ["profile"],
                    "due_offset_days": None,
                },
            ]
        return []

    engine = FakeEngine(handler)
    result = run(
        repository(engine).accept_admission_offer(
            auth(), OFFER_ID, "accept-offer-0001", "request-1"
        )
    )
    calls = [(normalized(sql), parameters) for sql, parameters in engine.connection.calls]

    assert result["offerStatus"] == "accepted"
    assert result["projectionVersion"] == 2
    assert result["acceptedAt"] == "2026-08-02T10:30:00.000Z"
    assert engine.begin_entries == 1
    assert engine.commits == 1
    assert engine.rollbacks == 0
    requirement_inserts = [
        parameters
        for sql, parameters in calls
        if sql.startswith("insert into public.student_requirement")
    ]
    assert [item["status"] for item in requirement_inserts] == ["ready", "blocked"]
    assert requirement_inserts[0]["due_at"] == datetime(2026, 8, 5, 10, 30, tzinfo=UTC)
    assert requirement_inserts[1]["due_at"] is None

    audit = next(
        parameters for sql, parameters in calls if sql.startswith("insert into public.audit_event")
    )
    assert audit["tenant_id"] == UUID(TENANT_ID)
    assert json.loads(str(audit["metadata"]))["lineage"]["effectId"] == ("admissions.acceptOffer")
    outbox = [
        parameters for sql, parameters in calls if sql.startswith("insert into public.outbox_event")
    ]
    assert [item["event_name"] for item in outbox] == [
        "admission.offer_accepted.v1",
        "enrollment.journey_created.v1",
    ]
    assert outbox[0]["causation_id"] == outbox[1]["causation_id"]
    assert isinstance(outbox[0]["causation_id"], str)
    first_payload = json.loads(str(outbox[0]["payload"]))
    assert first_payload["tenantId"] == TENANT_ID
    assert first_payload["correlationId"] == "request-1"
    assert first_payload["lineage"]["effectId"] == "admissions.acceptOffer"
    idempotency_sql, idempotency = calls[-1]
    assert idempotency_sql.startswith("insert into public.idempotency_record")
    assert idempotency["request_hash"] == offer_request_hash(auth(), OFFER_ID)


def test_offer_acceptance_without_definition_creates_replayable_zero_step_journey() -> None:
    stored_idempotency: dict[str, object] = {}

    def handler(sql: str, parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        if "from public.idempotency_record" in query:
            return [stored_idempotency] if stored_idempotency else []
        if query.startswith("insert into public.idempotency_record"):
            stored_idempotency.update(
                {
                    "request_hash": parameters["request_hash"],
                    "response_body": parameters["response_body"],
                }
            )
            return []
        if "from public.admission_offer" in query:
            return [
                {
                    "id": OFFER_ID,
                    "status": "offered",
                    "accepted_at": None,
                    "version": 1,
                    "is_expired": False,
                }
            ]
        if "select id as journey_definition_version_id" in query:
            return []
        if "select coalesce(max(version), 0) + 1" in query:
            return [{"version": 1}]
        if query.startswith("update public.admission_offer"):
            return [{"version": 2}]
        if "from public.journey_requirement_definition" in query:
            return []
        return []

    engine = FakeEngine(handler)
    platform = repository(engine)
    first = run(
        platform.accept_admission_offer(
            auth(), OFFER_ID, "accept-offer-zero-step", "request-zero-step"
        )
    )
    replay = run(
        platform.accept_admission_offer(
            auth(), OFFER_ID, "accept-offer-zero-step", "request-zero-step-replay"
        )
    )
    calls = [(normalized(sql), parameters) for sql, parameters in engine.connection.calls]

    assert replay == first
    assert first["offerStatus"] == "accepted"
    assert first["journeyStatus"] == "completed"
    assert first["requirementCount"] == 0
    assert first["onboardingRequired"] is False
    assert first["initialRoute"] == "/dashboard"
    assert engine.commits == 2
    assert engine.rollbacks == 0
    definition_insert = next(
        parameters
        for sql, parameters in calls
        if sql.startswith("insert into public.journey_definition_version")
    )
    assert definition_insert["code"] == "system_zero_step_enrollment"
    journey_inserts = [
        parameters
        for sql, parameters in calls
        if sql.startswith("insert into public.enrollment_journey")
    ]
    assert len(journey_inserts) == 1
    assert journey_inserts[0]["journey_status"] == "completed"
    assert not any(sql.startswith("insert into public.student_requirement") for sql, _ in calls)
    onboarding = next(
        parameters
        for sql, parameters in calls
        if sql.startswith("insert into public.student_onboarding")
    )
    assert json.loads(str(onboarding["payload"])) == {
        "onboardingCompletionReason": "no_active_core_onboarding"
    }
    assert "completed_steps" not in onboarding
    outbox = [
        parameters for sql, parameters in calls if sql.startswith("insert into public.outbox_event")
    ]
    assert [item["event_name"] for item in outbox] == [
        "admission.offer_accepted.v1",
        "enrollment.journey_created.v1",
    ]
    journey_payload = json.loads(str(outbox[1]["payload"]))
    assert journey_payload["data"]["requirementIds"] == []


def test_existing_synthetic_definition_is_zero_step_for_subsequent_student() -> None:
    def handler(sql: str, _parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        if "from public.idempotency_record" in query:
            return []
        if "from public.admission_offer" in query:
            return [
                {
                    "id": OFFER_ID,
                    "status": "offered",
                    "accepted_at": None,
                    "version": 1,
                    "is_expired": False,
                }
            ]
        if "select id as journey_definition_version_id" in query:
            return [
                {
                    "journey_definition_version_id": DEFINITION_ID,
                    "code": "system_zero_step_enrollment",
                }
            ]
        if query.startswith("update public.admission_offer"):
            return [{"version": 2}]
        if "from public.journey_requirement_definition" in query:
            return []
        return []

    engine = FakeEngine(handler)
    result = run(
        repository(engine).accept_admission_offer(
            auth(), OFFER_ID, "accept-offer-second-student", "request-second-student"
        )
    )
    calls = [(normalized(sql), parameters) for sql, parameters in engine.connection.calls]

    assert result["journeyStatus"] == "completed"
    assert result["onboardingRequired"] is False
    assert result["initialRoute"] == "/dashboard"
    assert not any(
        sql.startswith("insert into public.journey_definition_version") for sql, _ in calls
    )
    assert (
        next(
            parameters
            for sql, parameters in calls
            if sql.startswith("insert into public.enrollment_journey")
        )["journey_status"]
        == "completed"
    )


def test_definition_with_active_core_onboarding_is_not_zero_step_without_requirements() -> None:
    def handler(sql: str, _parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        if "from public.idempotency_record" in query:
            return []
        if "from public.admission_offer" in query:
            return [
                {
                    "id": OFFER_ID,
                    "status": "offered",
                    "accepted_at": None,
                    "version": 1,
                    "is_expired": False,
                }
            ]
        if "select id as journey_definition_version_id" in query:
            return [
                {
                    "journey_definition_version_id": DEFINITION_ID,
                    "code": "staff_managed_enrollment",
                    "zero_step": False,
                }
            ]
        if query.startswith("update public.admission_offer"):
            return [{"version": 2}]
        if "from public.journey_requirement_definition" in query:
            return []
        return []

    engine = FakeEngine(handler)
    result = run(
        repository(engine).accept_admission_offer(
            auth(), OFFER_ID, "accept-core-only", "request-core-only"
        )
    )
    calls = [(normalized(sql), parameters) for sql, parameters in engine.connection.calls]

    assert result["journeyStatus"] == "in_progress"
    assert result["onboardingRequired"] is True
    assert result["initialRoute"] == "/onboarding"
    assert any(
        "definition.onboarding_required" in sql and "has_requirements" in sql for sql, _ in calls
    )
    assert not any(
        sql.startswith("insert into public.journey_definition_version") for sql, _ in calls
    )


def test_enrollment_requirements_without_core_onboarding_route_to_dashboard() -> None:
    def handler(sql: str, _parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        if "from public.idempotency_record" in query:
            return []
        if "from public.admission_offer" in query:
            return [
                {
                    "id": OFFER_ID,
                    "status": "offered",
                    "accepted_at": None,
                    "version": 1,
                    "is_expired": False,
                }
            ]
        if "select id as journey_definition_version_id" in query:
            return [
                {
                    "journey_definition_version_id": DEFINITION_ID,
                    "code": "staff_managed_enrollment",
                    "onboarding_required": False,
                    "has_requirements": True,
                }
            ]
        if query.startswith("update public.admission_offer"):
            return [{"version": 2}]
        if "from public.journey_requirement_definition" in query:
            return []
        return []

    engine = FakeEngine(handler)
    result = run(
        repository(engine).accept_admission_offer(
            auth(), OFFER_ID, "accept-enrollment-only", "request-enrollment-only"
        )
    )
    calls = [(normalized(sql), parameters) for sql, parameters in engine.connection.calls]

    assert result["journeyStatus"] == "in_progress"
    assert result["onboardingRequired"] is False
    assert result["initialRoute"] == "/dashboard"
    assert any(sql.startswith("insert into public.student_onboarding") for sql, _ in calls)


def test_accepted_offer_without_journey_repairs_state_without_reaccepting() -> None:
    def handler(sql: str, _parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        if "from public.idempotency_record" in query:
            return []
        if "from public.admission_offer" in query:
            return [
                {
                    "id": OFFER_ID,
                    "status": "accepted",
                    "accepted_at": NOW,
                    "version": 2,
                    "is_expired": False,
                }
            ]
        if "from public.enrollment_journey" in query:
            return []
        if "select id as journey_definition_version_id" in query:
            return []
        if "select coalesce(max(version), 0) + 1" in query:
            return [{"version": 1}]
        if "from public.journey_requirement_definition" in query:
            return []
        return []

    engine = FakeEngine(handler)
    result = run(
        repository(engine).accept_admission_offer(
            auth(), OFFER_ID, "repair-accepted-offer", "request-repair"
        )
    )
    calls = [(normalized(sql), parameters) for sql, parameters in engine.connection.calls]

    assert result["journeyStatus"] == "completed"
    assert result["initialRoute"] == "/dashboard"
    assert not any(sql.startswith("update public.admission_offer") for sql, _ in calls)
    audit = next(
        parameters for sql, parameters in calls if sql.startswith("insert into public.audit_event")
    )
    assert audit["audit_action"] == "admission_offer.journey_repaired"
    outbox_names = [
        parameters["event_name"]
        for sql, parameters in calls
        if sql.startswith("insert into public.outbox_event")
    ]
    assert outbox_names == ["enrollment.journey_created.v1"]


@pytest.mark.parametrize(
    ("properties", "code"),
    [
        ({"email_address": "private@example.test"}, "PROHIBITED_ACTIVITY_PROPERTY"),
        ({"not_allowed": "value"}, "UNKNOWN_ACTIVITY_PROPERTY"),
        ({"entry_point": {"nested": True}}, "INVALID_ACTIVITY_PROPERTY"),
    ],
)
def test_activity_property_validation_rejects_sensitive_unknown_and_structured_values(
    properties: dict[str, object], code: str
) -> None:
    event = {
        "eventName": "ui.portal_session_started.v1",
        "properties": properties,
    }

    with pytest.raises(ApiError) as invalid:
        validate_activity_event_properties(event)
    assert invalid.value.code == code


def test_activity_batch_deduplicates_and_awards_only_new_events() -> None:
    first_id = "00000000-0000-7000-8000-000000000701"
    duplicate_id = "00000000-0000-7000-8000-000000000702"

    def handler(sql: str, parameters: Mapping[str, object]) -> list[Mapping[str, object]]:
        query = normalized(sql)
        if query.startswith("insert into public.activity_event"):
            return [{"event_id": first_id}] if parameters["event_id"] == UUID(first_id) else []
        if "from public.tenant_reward_rule" in query:
            return [{"id": RULE_ID, "points": 10, "max_awards_per_student": 2}]
        if query.startswith("insert into public.student_reward_ledger"):
            return [{"points": 10}]
        raise AssertionError(query)

    events: list[Mapping[str, object]] = [
        {
            "eventId": first_id,
            "eventName": "ui.portal_section_viewed.v1",
            "occurredAt": "2026-08-02T10:00:00Z",
            "sessionId": "session.0001",
            "pageInstanceId": "page.0000001",
            "correlationId": None,
            "properties": {"section": "dashboard", "entry_point": "navigation"},
        },
        {
            "eventId": duplicate_id,
            "eventName": "ui.portal_section_viewed.v1",
            "occurredAt": "2026-08-02T10:01:00Z",
            "sessionId": "session.0001",
            "pageInstanceId": "page.0000001",
            "properties": {"section": "dashboard", "entry_point": "navigation"},
        },
    ]
    engine = FakeEngine(handler)
    result = run(repository(engine).ingest_activity_events(auth(), events, "request-1"))

    assert result == {"accepted": 1, "duplicates": 1}
    assert engine.commits == 1
    reward_calls = [
        (normalized(sql), parameters)
        for sql, parameters in engine.connection.calls
        if "tenant_reward_rule" in sql or "student_reward_ledger" in sql
    ]
    assert len(reward_calls) == 2
    assert reward_calls[0][1]["tenant_id"] == UUID(TENANT_ID)
    assert reward_calls[1][1]["source_key"] == ("ui.portal_section_viewed.v1:dashboard")
    metadata = json.loads(str(reward_calls[1][1]["metadata"]))
    assert metadata["triggerKey"] == "ui.portal_section_viewed.v1"


def test_activity_validation_happens_before_opening_transaction() -> None:
    engine = FakeEngine(lambda _sql, _parameters: [])
    invalid = [
        {
            "eventName": "ui.help_opened.v1",
            "properties": {"email": "private@example.test"},
        }
    ]

    with pytest.raises(ApiError):
        run(repository(engine).ingest_activity_events(auth(), invalid, "request-1"))
    assert engine.begin_entries == 0


def test_ai_response_journal_preserves_json_and_exact_delivery_conflict_target() -> None:
    engine = FakeEngine(lambda _sql, _parameters: [])
    attempt = {
        "id": "00000000-0000-7000-8000-000000000801",
        "tenantId": TENANT_ID,
        "studentId": STUDENT_ID,
        "documentId": "00000000-0000-7000-8000-000000000802",
        "requestId": "request-1",
        "attempt": 1,
        "operation": "document_extraction",
        "provider": "openrouter",
        "requestedModel": "openai/gpt-4o-mini",
        "responseModel": "openai/gpt-4o-mini",
        "providerRequestId": "provider-1",
        "httpStatus": 200,
        "responseOk": True,
        "finishReason": "stop",
        "usage": {"total_tokens": 12},
        "rawResponseText": "{}",
        "responseBody": {"id": "provider-1"},
        "transportError": None,
        "durationMs": 100,
        "recordedAt": "2026-08-02T10:30:00Z",
        "promptTemplateVersionId": None,
        "contextPolicyVersionId": None,
        "outputSchemaVersionId": None,
        "configRevision": 3,
        "contextSha256": "a" * 64,
        "promptCacheStatus": "hit",
    }

    run(repository(engine).record_ai_provider_response(attempt))
    sql, parameters = engine.connection.calls[0]

    assert engine.commits == 1
    assert (
        "on conflict ( tenant_id, document_id, request_id, attempt_number ) do nothing"
        in normalized(sql)
    )
    assert json.loads(str(parameters["usage"])) == {"total_tokens": 12}
    assert json.loads(str(parameters["response_body"])) == {"id": "provider-1"}
    assert parameters["tenant_id"] == UUID(TENANT_ID)
