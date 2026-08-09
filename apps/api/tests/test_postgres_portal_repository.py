from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.contracts.requests import UpdateStudentHousingPlanRequest
from audentra.core.auth import AuthContext
from audentra.core.errors import ConflictError, NotFoundError
from audentra.infrastructure.postgres.portal_repository import (
    PostgresPortalRepository,
    _add_calendar_months,
    _onboarding_screen_configurations,
)


def test_about_you_screen_defaults_address_and_residency_to_optional() -> None:
    configuration = _onboarding_screen_configurations(
        {
            "flows": [
                {
                    "kind": "onboarding",
                    "tasks": [
                        {
                            "student_step": "about_you",
                            "title": "Tell us about you",
                            "description": "Review your identity.",
                        }
                    ],
                }
            ]
        }
    )["about_you"]

    assert configuration["requiredFields"] == [
        "firstName",
        "lastName",
        "preferredName",
        "personalEmail",
        "mobilePhone",
        "citizenshipStatus",
    ]
    assert "streetAddress" not in configuration["requiredFields"]
    assert "residencyVerificationPath" not in configuration["requiredFields"]
    assert configuration["identityQuickUpload"] is True


def test_onboarding_screen_presentation_and_form_fields_share_one_configuration() -> None:
    fields = [
        {
            "id": "preferred_name",
            "title": "What should we call you?",
            "field_type": "text",
            "required": True,
        },
        {
            "id": "arrival_style",
            "title": "How will you arrive?",
            "field_type": "single_select",
            "required": False,
            "options": ["Train", "Car"],
        },
    ]
    configuration = _onboarding_screen_configurations(
        {
            "flows": [
                {
                    "kind": "onboarding",
                    "tasks": [
                        {
                            "student_step": "about_you",
                            "title": "About you list item",
                            "description": "Legacy list description",
                            "input": {
                                "screen_label": "About you",
                                "screen_title": "Identity and arrival",
                                "screen_description": "Review the live student form.",
                                "fields": fields,
                            },
                        }
                    ],
                }
            ]
        }
    )["about_you"]

    assert configuration["label"] == "About you"
    assert configuration["title"] == "Identity and arrival"
    assert configuration["description"] == "Review the live student form."
    assert configuration["fields"] == fields


def test_calendar_month_projection_keeps_date_only_month_end_semantics() -> None:
    assert _add_calendar_months(date(2027, 8, 31), 1) == date(2027, 9, 30)
    assert _add_calendar_months(date(2027, 8, 31), 2) == date(2027, 10, 31)


def test_housing_plan_request_preserves_explicit_roommate_field_clears() -> None:
    request = UpdateStudentHousingPlanRequest.model_validate(
        {
            "expectedVersion": 4,
            "preference": "on_campus",
            "residenceOption": "aster_residence_hall",
            "roommateMatching": "known_roommate",
            "knownRoommateName": "Jordan Lee",
            "knownRoommateEmail": None,
            "sleepSchedule": "early_bird",
            "livingLearningCommunities": ["engineering"],
        }
    )

    assert request.public_payload()["roommateMatching"] == "known_roommate"
    assert request.public_payload()["knownRoommateEmail"] is None


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

    def scalar_one(self) -> object:
        return next(iter(self._rows[0].values()))

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


def test_transcript_credit_upsert_types_nullable_source_code_consistently() -> None:
    engine = FakeEngine(lambda _sql, _params: [])
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))

    asyncio.run(
        repository._persist_transcript_credits(
            cast(AsyncConnection, engine.connection),
            AUTH,
            "20000000-0000-7000-8000-000000000099",
            {
                "institutionName": "Example University",
                "courses": [
                    {
                        "sourceCode": None,
                        "title": "Introduction to Computing",
                        "grade": "A",
                    }
                ],
            },
        )
    )

    sql, parameters = engine.connection.calls[0]
    assert "CAST(:source_code AS varchar)" in sql
    assert "COALESCE(CAST(:source_code AS varchar),'')" in sql
    assert "CAST(:title AS varchar)" in sql
    assert "credit.title=CAST(:title AS varchar)" in sql
    assert "credit.student_id=CAST(:student_id AS uuid)" in sql
    assert parameters["source_code"] is None


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


def _campus_event_row(*, active: bool = True, version: int = 3) -> dict[str, Any]:
    starts_at = datetime.now(UTC) + timedelta(days=7)
    return {
        "id": "event-1",
        "title": "Welcome Week Block Party",
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(hours=2),
        "location": "University Green",
        "active": active,
        "version": version,
    }


def test_campus_event_registration_is_durable_notified_audited_and_idempotent() -> None:
    registered_at = datetime.now(UTC)

    def handler(sql: str, _params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "SELECT request_hash, response_body" in sql:
            return []
        if "FROM campus_event\n" in sql and "FOR UPDATE" in sql:
            return [_campus_event_row()]
        if "FROM campus_event_registration" in sql and "FOR UPDATE" in sql:
            return []
        if "INSERT INTO campus_event_registration" in sql:
            return [{"registered_at": registered_at}]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))

    result = asyncio.run(
        repository.register_campus_event(
            AUTH,
            "event-1",
            {"expectedVersion": 3},
            "event-registration-key",
            "request-1",
        )
    )

    assert result["eventId"] == "event-1"
    assert result["eventVersion"] == 3
    assert result["status"] == "registered"
    assert result["registeredAt"] == registered_at.isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    sql_calls = [sql for sql, _params in engine.connection.calls]
    assert any("INSERT INTO campus_event_registration" in sql for sql in sql_calls)
    assert any("INSERT INTO student_message" in sql for sql in sql_calls)
    assert any("INSERT INTO audit_event" in sql for sql in sql_calls)
    assert any("outbox_event" in sql and "INSERT INTO" in sql for sql in sql_calls)
    assert any("INSERT INTO idempotency_record" in sql for sql in sql_calls)
    message_params = next(
        params for sql, params in engine.connection.calls if "INSERT INTO student_message" in sql
    )
    assert message_params["kind"] == "campus_event_registered"
    assert message_params["href"] == "/campus-life"


def test_campus_event_registration_returns_existing_without_duplicate_side_effects() -> None:
    registered_at = datetime.now(UTC) - timedelta(minutes=10)

    def handler(sql: str, _params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "SELECT request_hash, response_body" in sql:
            return []
        if "FROM campus_event\n" in sql and "FOR UPDATE" in sql:
            return [_campus_event_row()]
        if "FROM campus_event_registration" in sql and "FOR UPDATE" in sql:
            return [
                {
                    "id": "registration-1",
                    "status": "registered",
                    "registered_at": registered_at,
                }
            ]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))

    result = asyncio.run(
        repository.register_campus_event(
            AUTH,
            "event-1",
            {"expectedVersion": 3},
            "repeat-registration-key",
            "request-2",
        )
    )

    assert result["id"] == "registration-1"
    assert result["status"] == "registered"
    sql_calls = [sql for sql, _params in engine.connection.calls]
    assert not any("INSERT INTO student_message" in sql for sql in sql_calls)
    assert not any("INSERT INTO audit_event" in sql for sql in sql_calls)
    assert any("INSERT INTO idempotency_record" in sql for sql in sql_calls)


def test_cancelled_event_registration_can_be_reactivated_after_event_returns() -> None:
    registered_at = datetime.now(UTC)

    def handler(sql: str, _params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "SELECT request_hash, response_body" in sql:
            return []
        if "FROM campus_event\n" in sql and "FOR UPDATE" in sql:
            return [_campus_event_row(version=4)]
        if "FROM campus_event_registration" in sql and "FOR UPDATE" in sql:
            return [
                {
                    "id": "registration-1",
                    "status": "cancelled_by_event",
                    "registered_at": registered_at - timedelta(days=1),
                }
            ]
        if "UPDATE campus_event_registration" in sql:
            return [{"registered_at": registered_at}]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))

    result = asyncio.run(
        repository.register_campus_event(
            AUTH,
            "event-1",
            {"expectedVersion": 4},
            "reactivate-registration-key",
            "request-3",
        )
    )

    assert result["id"] == "registration-1"
    assert result["eventVersion"] == 4
    assert any(
        "UPDATE campus_event_registration" in sql for sql, _params in engine.connection.calls
    )


@pytest.mark.parametrize(
    ("event_rows", "expected_code"),
    [
        ([], "CAMPUS_EVENT_NOT_FOUND"),
        ([_campus_event_row(active=False)], "CAMPUS_EVENT_UNAVAILABLE"),
        ([_campus_event_row(version=5)], "CAMPUS_EVENT_CHANGED"),
    ],
)
def test_campus_event_registration_rejects_missing_retired_and_stale_events(
    event_rows: list[Mapping[str, Any]], expected_code: str
) -> None:
    def handler(sql: str, _params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "SELECT request_hash, response_body" in sql:
            return []
        if "FROM campus_event\n" in sql and "FOR UPDATE" in sql:
            return event_rows
        return []

    repository = PostgresPortalRepository(cast(AsyncEngine, FakeEngine(handler)))
    error_type = NotFoundError if expected_code == "CAMPUS_EVENT_NOT_FOUND" else ConflictError

    with pytest.raises(error_type) as raised:
        asyncio.run(
            repository.register_campus_event(
                AUTH,
                "event-1",
                {"expectedVersion": 3},
                f"rejected-{expected_code}",
                "request-4",
            )
        )

    assert raised.value.code == expected_code


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


def test_document_upload_authorizes_against_current_published_definition() -> None:
    created_at = datetime(2028, 1, 15, 12, 0, tzinfo=UTC)
    requirement_id = "20000000-0000-7000-8000-000000000020"

    def handler(sql: str, parameters: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "SELECT request_hash, response_body" in sql:
            return []
        if "SELECT current_definition.code, sr.status" in sql:
            return [{"code": "official_transcript", "status": "ready"}]
        if "INSERT INTO document_record" in sql:
            return [
                {
                    "id": parameters["id"],
                    "requirement_id": parameters["requirement_id"],
                    "file_name": parameters["file_name"],
                    "mime_type": parameters["mime_type"],
                    "size_bytes": parameters["size_bytes"],
                    "category": parameters["category"],
                    "processing_mode": parameters["processing_mode"],
                    "status": "uploaded",
                    "storage_key": parameters["storage_key"],
                    "sha256": parameters["sha256"],
                    "extraction": None,
                    "created_at": created_at,
                }
            ]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))

    result = asyncio.run(
        repository.reserve_student_document_upload(
            AUTH,
            {
                "fileName": "transcript.pdf",
                "mimeType": "application/pdf",
                "sizeBytes": 128,
                "category": "other",
                "sha256": "a" * 64,
                "uploadBundleId": "30000000-0000-7000-8000-000000000001",
            },
            "upload-idempotency-key",
            "upload-request-id",
            requirement_id,
        )
    )

    assert result["requirementId"] == requirement_id
    assert result["category"] == "transcript"
    requirement_sql, requirement_parameters = next(
        (sql, parameters)
        for sql, parameters in engine.connection.calls
        if "SELECT current_definition.code, sr.status" in sql
    )
    assert "evidence_definition.id=sr.requirement_definition_version_id" in requirement_sql
    assert "current_link.journey_definition_version_id" in requirement_sql
    assert "j.journey_definition_version_id" in requirement_sql
    assert "current_definition.code=evidence_definition.code" in requirement_sql
    assert "current_definition.submission_type='document'" in requirement_sql
    assert "current_definition.interaction_type='upload_file'" in requirement_sql
    assert requirement_parameters == {
        "tenant_id": AUTH.tenant_id,
        "student_id": AUTH.student_id,
        "requirement_id": requirement_id,
    }


def test_sufficient_document_match_enters_review_without_completing_or_rewarding() -> None:
    now = datetime(2028, 1, 15, 12, 0, tzinfo=UTC)
    document_id = "20000000-0000-7000-8000-000000000030"
    requirement_id = "20000000-0000-7000-8000-000000000031"

    def document_row(parameters: Mapping[str, Any], *, linked: bool = False) -> Mapping[str, Any]:
        return {
            "id": document_id,
            "requirement_id": requirement_id if linked else None,
            "file_name": "identity.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 512,
            "category": "identity",
            "processing_mode": "agentic",
            "status": parameters.get("status", "needs_review"),
            "storage_key": "tenant/student/identity.pdf",
            "sha256": "a" * 64,
            "extraction": json.loads(str(parameters["extraction"])),
            "created_at": now,
        }

    work_item_insertions = 0

    def handler(sql: str, parameters: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        nonlocal work_item_insertions
        if "UPDATE document_record SET status=:status" in sql:
            return [document_row(parameters)]
        if "SELECT sr.id, sr.status, rdv.code, rdv.title" in sql:
            return [
                {
                    "id": requirement_id,
                    "status": "ready",
                    "code": "identity_document",
                    "title": "Provide identity documentation",
                }
            ]
        if "UPDATE student_requirement SET status=:status" in sql:
            return [{"id": requirement_id}]
        if "requirement_id=COALESCE(requirement_id, :requirement_id)" in sql:
            return [document_row(parameters, linked=True)]
        if "SELECT id FROM staff_member" in sql:
            return [{"id": "10000000-0000-7000-8000-000000000901"}]
        if "INSERT INTO staff_work_item (" in sql:
            work_item_insertions += 1
            return [{"id": parameters["id"], "version": 1, "inserted": True}]
        if "INSERT INTO staff_notification" in sql:
            return [{"id": parameters["id"]}]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))
    result = asyncio.run(
        repository.complete_student_document_extraction(
            AUTH,
            document_id,
            {
                "status": "completed",
                "documentType": "identity",
                "summary": "Identity evidence extracted.",
                "studentName": "Ada Example",
                "institutionName": "Civil Registry",
                "issueDate": "2026-06-01",
                "academicTerm": None,
                "fields": [
                    {
                        "key": "full_name",
                        "label": "Full name",
                        "value": "Ada Example",
                        "confidence": 0.98,
                    }
                ],
                "courses": [],
                "visualRegions": [],
                "warnings": [],
                "model": "test/model",
                "provider": "openrouter",
                "processedAt": "2028-01-15T12:00:00Z",
                "verifiedAt": None,
                "contextMatches": [
                    {
                        "targetType": "requirement",
                        "targetId": requirement_id,
                        "title": "Provide identity documentation",
                        "status": "sufficient",
                        "matchedFieldKeys": [],
                        "evidenceKeys": ["document_type", "full_name"],
                        "confidence": 0.98,
                        "rationale": "The identity evidence is sufficient for review.",
                        "applied": False,
                        "reviewRequired": True,
                        "href": "/enrollment/requirements/identity-document-upload",
                    }
                ],
            },
            "request-1",
        )
    )

    assert result["requirementId"] == requirement_id
    assert result["status"] == "needs_review"
    assert result["extraction"]["contextMatches"][0]["applied"] is True
    statements = [sql for sql, _parameters in engine.connection.calls]
    requirement_update = next(
        sql for sql in statements if "UPDATE student_requirement SET status=:status" in sql
    )
    assert "progress_percent=LEAST(80" in requirement_update
    assert "completed" not in requirement_update
    assert not any("student_reward_ledger" in sql for sql in statements)
    assert any("INSERT INTO student_message" in sql for sql in statements)
    assert work_item_insertions == 1
    extraction_event = next(
        parameters
        for sql, parameters in engine.connection.calls
        if "INSERT INTO public.outbox_event" in sql
        and parameters.get("event_name") == "document.extraction_completed.v1"
    )
    assert json.loads(str(extraction_event["payload"]))["data"]["staffReviewQueued"] is True


def test_failed_document_extraction_queues_human_review_and_realtime_notification() -> None:
    now = datetime(2028, 1, 15, 12, 0, tzinfo=UTC)
    document_id = "20000000-0000-7000-8000-000000000032"

    def handler(sql: str, parameters: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "UPDATE document_record SET status=:status" in sql:
            return [
                {
                    "id": document_id,
                    "requirement_id": None,
                    "file_name": "unreadable.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 512,
                    "category": "other",
                    "processing_mode": "agentic",
                    "status": parameters.get("status", "needs_review"),
                    "storage_key": "tenant/student/unreadable.pdf",
                    "sha256": "b" * 64,
                    "extraction": json.loads(str(parameters["extraction"])),
                    "created_at": now,
                }
            ]
        if "INSERT INTO staff_work_item (" in sql:
            return [{"id": parameters["id"], "version": 1, "inserted": True}]
        if "INSERT INTO staff_notification" in sql:
            return [{"id": parameters["id"]}]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))
    result = asyncio.run(
        repository.complete_student_document_extraction(
            AUTH,
            document_id,
            {
                "status": "failed",
                "documentType": "other",
                "summary": "Extraction failed.",
                "fields": [],
                "courses": [],
                "visualRegions": [],
                "warnings": [],
                "provider": "local",
                "model": None,
                "processedAt": "2028-01-15T12:00:00Z",
                "verifiedAt": None,
            },
            "request-2",
        )
    )

    assert result["status"] == "needs_review"
    work_item_call = next(
        call for call in engine.connection.calls if "INSERT INTO staff_work_item" in call[0]
    )
    assert work_item_call[1]["priority"] == "high"
    assert work_item_call[1]["blocker_code"] == "document_parse_failure"
    assert any("INSERT INTO staff_notification" in sql for sql, _ in engine.connection.calls)
    assert any("INSERT INTO staff_realtime_event" in sql for sql, _ in engine.connection.calls)
    extraction_event = next(
        parameters
        for sql, parameters in engine.connection.calls
        if "INSERT INTO public.outbox_event" in sql
        and parameters.get("event_name") == "document.extraction_completed.v1"
    )
    assert json.loads(str(extraction_event["payload"]))["data"]["staffReviewQueued"] is True


def test_document_review_work_item_creation_is_idempotent() -> None:
    insert_count = 0
    work_item_id: str | None = None

    def handler(sql: str, parameters: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        nonlocal insert_count, work_item_id
        if "SELECT id FROM staff_member" in sql:
            return []
        if "INSERT INTO staff_work_item (" in sql:
            insert_count += 1
            work_item_id = work_item_id or str(parameters["id"])
            return [{"id": work_item_id, "version": insert_count, "inserted": insert_count == 1}]
        if "INSERT INTO staff_notification" in sql:
            return [{"id": parameters["id"]}]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))
    document = {
        "id": "20000000-0000-7000-8000-000000000033",
        "file_name": "record.pdf",
        "category": "other",
    }

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        connection = cast(AsyncConnection, engine.connection)
        return (
            await repository._ensure_document_review_work_item(
                connection,
                AUTH,
                document,
                parse_failure=False,
                failure_code=None,
                request_id="request-1",
            ),
            await repository._ensure_document_review_work_item(
                connection,
                AUTH,
                document,
                parse_failure=False,
                failure_code=None,
                request_id="request-2",
            ),
        )

    first, second = asyncio.run(scenario())
    assert first["created"] is True
    assert second["created"] is False
    assert first["workItemId"] == second["workItemId"]
    work_sql = next(
        sql for sql, _ in engine.connection.calls if "INSERT INTO staff_work_item" in sql
    )
    normalized_sql = " ".join(work_sql.split())
    assert (
        "ON CONFLICT (tenant_id, source_type, source_id) "
        "WHERE source_type IS NOT NULL AND source_id IS NOT NULL DO UPDATE SET"
    ) in normalized_sql


@pytest.mark.parametrize(
    ("existing_status", "expected_reopened"),
    [("in_progress", False), ("done", True), ("cancelled", True)],
)
def test_parse_retry_failure_refreshes_active_or_reopens_terminal_review(
    existing_status: str, expected_reopened: bool
) -> None:
    work_item_id = "20000000-0000-7000-8000-000000000044"
    current_status = existing_status

    def handler(sql: str, parameters: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        nonlocal current_status
        if "SELECT status" in sql and "source_type='document'" in sql:
            return [{"status": current_status}]
        if "INSERT INTO staff_work_item (" in sql:
            if current_status in {"done", "cancelled"}:
                current_status = "todo"
            return [{"id": work_item_id, "version": 4, "inserted": False}]
        if "INSERT INTO staff_notification" in sql:
            return [{"id": parameters["id"]}]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))
    document = {
        "id": "20000000-0000-7000-8000-000000000043",
        "file_name": "transcript.pdf",
        "category": "transcript",
    }

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        connection = cast(AsyncConnection, engine.connection)
        first = await repository._ensure_document_review_work_item(
            connection,
            AUTH,
            document,
            parse_failure=True,
            failure_code="provider_configuration",
            request_id="retry-request-1",
        )
        second = await repository._ensure_document_review_work_item(
            connection,
            AUTH,
            document,
            parse_failure=True,
            failure_code="provider_configuration",
            request_id="retry-request-2",
        )
        return first, second

    first, second = asyncio.run(scenario())

    assert first["created"] is False
    assert first["reopened"] is expected_reopened
    assert second["reopened"] is False
    work_sql = next(
        sql for sql, _ in engine.connection.calls if "INSERT INTO staff_work_item (" in sql
    )
    assert "staff_work_item.status IN ('done','cancelled') THEN 'todo'" in " ".join(
        work_sql.split()
    )
    notification_dedupes = [
        str(parameters["dedupe_key"])
        for sql, parameters in engine.connection.calls
        if "INSERT INTO staff_notification" in sql
    ]
    assert notification_dedupes == [
        f"work-item:{work_item_id}:parse-failure:retry-request-1",
        f"work-item:{work_item_id}:parse-failure:retry-request-2",
    ]
    assert sum("INSERT INTO staff_work_log" in sql for sql, _ in engine.connection.calls) == 2
    assert sum("INSERT INTO staff_realtime_event" in sql for sql, _ in engine.connection.calls) == 2


def test_reward_summary_is_authoritative_from_the_student_ledger() -> None:
    def handler(sql: str, _parameters: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "COALESCE(SUM(ledger.points), 0)" in sql:
            return [
                {
                    "point_name": "Aster Points",
                    "points_per_usd": 100,
                    "lifetime_points": 190,
                }
            ]
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))

    result = asyncio.run(repository._get_reward_summary(AUTH))

    assert result == {
        "pointName": "Aster Points",
        "pointsPerUsd": 100,
        "lifetimePoints": 190,
        "bookstoreCreditCents": 190,
    }
    reward_sql, parameters = next(
        (sql, params)
        for sql, params in engine.connection.calls
        if "COALESCE(SUM(ledger.points), 0)" in sql
    )
    assert "LEFT JOIN student_reward_ledger ledger" in reward_sql
    assert parameters == {"tenant_id": AUTH.tenant_id, "student_id": AUTH.student_id}


def test_generic_requirement_response_commits_evidence_progress_and_idempotency() -> None:
    submitted_at = datetime(2028, 1, 15, 12, 0, tzinfo=UTC)
    requirement_id = "20000000-0000-7000-8000-000000000010"
    journey_id = "20000000-0000-7000-8000-000000000011"
    definition_id = "20000000-0000-7000-8000-000000000012"

    def handler(sql: str, _params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "SELECT request_hash, response_body" in sql:
            return []
        if "SELECT sr.id, sr.journey_id" in sql:
            return [
                {
                    "id": requirement_id,
                    "journey_id": journey_id,
                    "status": "ready",
                    "version": 2,
                    "due_at": None,
                    "progress_percent": 0,
                    "current_definition_id": definition_id,
                    "code": "meal_plan",
                    "title": "Choose a meal plan",
                    "description": "Select one dining option.",
                    "blocking": 1,
                    "submission_type": "form",
                    "responsible_office": "Dining Services",
                    "depends_on_codes": [],
                    "flow_kind": "enrollment",
                    "interaction_type": "single_select",
                    "input_config": {"options": ["Standard", "Vegetarian"]},
                    "display_order": 10,
                }
            ]
        if "UPDATE student_requirement" in sql and "RETURNING version" in sql:
            return [{"version": 3, "updated_at": submitted_at}]
        if "SELECT COALESCE(MAX(version),0)+1" in sql:
            return [{"version": 1}]
        if "INSERT INTO student_requirement_response" in sql:
            return [{"submitted_at": submitted_at}]
        if "SELECT id, points, max_awards_per_student" in sql:
            return []
        return []

    engine = FakeEngine(handler)
    repository = PostgresPortalRepository(cast(AsyncEngine, engine))
    result = asyncio.run(
        repository.submit_student_requirement_response(
            AUTH,
            requirement_id,
            {
                "expectedVersion": 2,
                "response": {"selectedOption": "Vegetarian"},
            },
            "meal-response-key",
            "meal-response-request",
        )
    )

    assert result["status"] == "completed"
    assert result["version"] == 3
    assert result["progressPercent"] == 100
    assert result["response"] == {
        "id": result["response"]["id"],
        "interactionType": "single_select",
        "data": {"selectedOption": "Vegetarian"},
        "version": 1,
        "submittedAt": "2028-01-15T12:00:00.000Z",
    }
    calls = [sql for sql, _params in engine.connection.calls]
    assert any("INSERT INTO student_requirement_response" in sql for sql in calls)
    assert any("UPDATE student_experience_update" in sql for sql in calls)
    assert any("INSERT INTO idempotency_record" in sql for sql in calls)


def test_financials_link_documents_and_project_enrolled_installments_from_date_deadline() -> None:
    document_id = "20000000-0000-7000-8000-000000000099"

    def handler(sql: str, _params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if "FROM student_financial_summary" in sql:
            return [
                {
                    "academic_year": "2027-2028",
                    "cost_of_attendance_cents": 1_000,
                    "external_payments_cents": 0,
                    "portal_payments_cents": 0,
                }
            ]
        if "FROM student_financial_award" in sql:
            return []
        if "FROM financial_document_requirement" in sql:
            return [
                {
                    "id": "financial-document-1",
                    "code": "verification_worksheet",
                    "title": "Verification worksheet",
                    "description": "Upload the requested worksheet.",
                    "status": "submitted",
                    "due_at": None,
                    "document_id": document_id,
                },
                {
                    "id": "financial-document-2",
                    "code": "tax_transcript",
                    "title": "Tax transcript",
                    "description": "Provide a tax transcript.",
                    "status": "not_started",
                    "due_at": None,
                    "document_id": None,
                },
            ]
        if "FROM student_payment_plan" in sql:
            return [
                {
                    "id": "payment-plan-1",
                    "name": "Two payments",
                    "installment_count": 2,
                    "enrollment_fee_cents": 25,
                    "status": "enrolled",
                }
            ]
        if "FROM student_sap_status" in sql:
            return [
                {
                    "status": "meeting",
                    "cumulative_gpa": 3.4,
                    "minimum_gpa": 2.0,
                    "completion_rate_percent": 80,
                    "minimum_completion_rate_percent": 67,
                    "attempted_credits": 30,
                    "maximum_attempted_credits": 180,
                }
            ]
        if "FROM admission_offer offer" in sql:
            return [
                {
                    "id": "offer-1",
                    "response_deadline": date(2027, 8, 31),
                    "deposit_amount_cents": 100,
                    "deposit_paid": False,
                }
            ]
        return []

    repository = PostgresPortalRepository(cast(AsyncEngine, FakeEngine(handler)))
    result = asyncio.run(repository.get_student_financials(AUTH))

    assert result["requiredDocuments"][0]["documentId"] == document_id
    assert result["requiredDocuments"][0]["href"] == f"/documents?document={document_id}"
    assert result["requiredDocuments"][1]["href"] == (
        "/enrollment/requirements/financial-aid-verification"
    )
    assert result["paymentSchedule"] == [
        {
            "id": "offer-1:enrollment_deposit",
            "kind": "deposit",
            "label": "Enrollment deposit",
            "amountCents": 100,
            "enrollmentFeeCents": 0,
            "dueAt": "2027-08-31",
            "status": "due",
            "projected": False,
        },
        {
            "id": "payment-plan-1:installment:1",
            "kind": "installment",
            "label": "Two payments installment 1 of 2",
            "amountCents": 450,
            "enrollmentFeeCents": 25,
            "dueAt": "2027-09-30",
            "status": "projected",
            "projected": True,
        },
        {
            "id": "payment-plan-1:installment:2",
            "kind": "installment",
            "label": "Two payments installment 2 of 2",
            "amountCents": 450,
            "enrollmentFeeCents": 0,
            "dueAt": "2027-10-31",
            "status": "projected",
            "projected": True,
        },
    ]
