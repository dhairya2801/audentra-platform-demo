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
from audentra.infrastructure.postgres.portal_repository import (
    PostgresPortalRepository,
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
