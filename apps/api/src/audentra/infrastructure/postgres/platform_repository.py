# ruff: noqa: S608 -- schema identifiers are validated before interpolation.
"""Tenant-scoped PostgreSQL repository for dashboard and admission commands.

This is a direct SQLAlchemy/asyncpg port of the legacy ``PostgresPlatformStore``.
Commands own their transaction so canonical state, audit rows, outbox events, reward
ledger entries, and idempotency records cannot commit independently.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from opentelemetry import trace
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError

ACTIVITY_PROPERTY_ALLOWLISTS: dict[str, frozenset[str]] = {
    "ui.portal_session_started.v1": frozenset({"entry_point"}),
    "ui.dashboard_viewed.v1": frozenset({"projection_version", "journey_status"}),
    "ui.admission_offer_viewed.v1": frozenset({"offer_id", "offer_status"}),
    "ui.admission_decision_started.v1": frozenset({"offer_id", "decision", "entry_point"}),
    "ui.enrollment_started.v1": frozenset({"journey_id", "entry_point"}),
    "ui.enrollment_step_viewed.v1": frozenset({"step_code", "entry_point"}),
    "ui.portal_section_viewed.v1": frozenset({"section", "entry_point"}),
    "ui.enrollment_task_viewed.v1": frozenset({"task_code", "task_status", "entry_point"}),
    "ui.enrollment_task_abandoned.v1": frozenset(
        {"task_code", "task_status", "duration_bucket", "last_interaction"}
    ),
    "ui.financial_aid_viewed.v1": frozenset({"surface", "aid_status"}),
    "ui.course_catalog_searched.v1": frozenset({"query_length_bucket", "result_count"}),
    "ui.course_viewed.v1": frozenset({"course_code", "surface"}),
    "ui.exemption_reviewed.v1": frozenset({"rule_code", "recommendation_status"}),
    "ui.campus_event_viewed.v1": frozenset({"event_id", "surface"}),
    "ui.club_viewed.v1": frozenset({"club_id", "surface"}),
    "ui.edward_context_receipts_received.v1": frozenset({"source_count", "page_context"}),
    "ui.edward_tool_invoked.v1": frozenset({"tool_name", "page_context"}),
    "ui.edward_action_widget_viewed.v1": frozenset({"widget_type", "page_context"}),
    "ui.edward_action_completed.v1": frozenset({"widget_type", "outcome"}),
    "ui.help_opened.v1": frozenset({"context", "surface", "topic_code"}),
}

_PROHIBITED_ACTIVITY_PROPERTY = re.compile(
    r"password|token|secret|email|phone|address|government|payment|card|ssn",
    re.IGNORECASE,
)
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ACCEPT_OFFER_OPERATION = "admission_offer.accept"
_ACCEPT_OFFER_EFFECT = "admissions.acceptOffer"
_ZERO_STEP_JOURNEY_CODE = "system_zero_step_enrollment"
_TERMINAL_REQUIREMENT_STATUSES = {"completed", "waived", "not_applicable"}
_PRIORITY_REQUIREMENT_STATUSES = {"rejected", "ready", "in_progress"}
_ENCODE_URI_COMPONENT_SAFE = "~()*!.'-_"


def validate_activity_event_properties(event: Mapping[str, object]) -> None:
    """Reject sensitive, unknown, or structured client activity properties."""

    event_name = event.get("eventName")
    allowlist = (
        ACTIVITY_PROPERTY_ALLOWLISTS.get(event_name) if isinstance(event_name, str) else None
    )
    if allowlist is None:
        raise BadRequestError("VALIDATION_ERROR", "The activity event name is invalid")
    properties = event.get("properties")
    if not isinstance(properties, Mapping):
        raise BadRequestError("INVALID_ACTIVITY_PROPERTY", "Properties must be an object")
    for raw_key, value in properties.items():
        key = str(raw_key)
        if _PROHIBITED_ACTIVITY_PROPERTY.search(key):
            raise BadRequestError(
                "PROHIBITED_ACTIVITY_PROPERTY",
                f'Property "{key}" may not be captured',
            )
        if key not in allowlist:
            raise BadRequestError(
                "UNKNOWN_ACTIVITY_PROPERTY",
                f'Property "{key}" is not allowed for {event_name}',
            )
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise BadRequestError(
                "INVALID_ACTIVITY_PROPERTY",
                f'Property "{key}" must be a primitive value',
            )


def offer_request_hash(auth: AuthContext, offer_id: str) -> str:
    """Return the byte-for-byte equivalent of the legacy JSON request hash."""

    canonical = json.dumps(
        {
            "tenantId": auth.tenant_id,
            "studentId": auth.student_id,
            "offerId": offer_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PostgresPlatformRepository:
    """Persistence implementation for the platform portion of ``PlatformService``."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        schema: str = "public",
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not _SQL_IDENTIFIER.fullmatch(schema):
            raise ValueError("schema is not a safe PostgreSQL identifier")
        self._engine = engine
        self._schema = schema
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory

    def _table(self, name: str) -> str:
        return f"{self._schema}.{name}"

    async def record_ai_provider_response(self, attempt: Mapping[str, Any]) -> None:
        """Append an AI provider diagnostic attempt, ignoring an exact replay."""

        parameters = {
            "id": _uuid(_required_string(attempt, "id")),
            "tenant_id": _uuid(_required_string(attempt, "tenantId")),
            "student_id": _uuid(_required_string(attempt, "studentId")),
            "document_id": _optional_uuid(attempt.get("documentId")),
            "request_id": _required_string(attempt, "requestId"),
            "attempt_number": _required_int(attempt, "attempt"),
            "operation": _required_string(attempt, "operation"),
            "provider": _required_string(attempt, "provider"),
            "requested_model": _optional_string(attempt.get("requestedModel")),
            "response_model": _optional_string(attempt.get("responseModel")),
            "provider_request_id": _optional_string(attempt.get("providerRequestId")),
            "http_status": _optional_int(attempt.get("httpStatus")),
            "response_ok": bool(attempt.get("responseOk")),
            "finish_reason": _optional_string(attempt.get("finishReason")),
            "usage": _json(attempt.get("usage")),
            "raw_response_text": _optional_string(attempt.get("rawResponseText")),
            "response_body": _json(attempt.get("responseBody")),
            "transport_error": _json(attempt.get("transportError")),
            "duration_ms": _required_int(attempt, "durationMs"),
            "recorded_at": _datetime(attempt.get("recordedAt"), "recordedAt"),
            "prompt_template_version_id": _optional_uuid(attempt.get("promptTemplateVersionId")),
            "context_policy_version_id": _optional_uuid(attempt.get("contextPolicyVersionId")),
            "output_schema_version_id": _optional_uuid(attempt.get("outputSchemaVersionId")),
            "config_revision": _optional_int(attempt.get("configRevision")),
            "context_sha256": _optional_string(attempt.get("contextSha256")),
            "prompt_cache_status": _optional_string(attempt.get("promptCacheStatus")),
        }
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("ai_provider_response_attempt")} (
                      id, tenant_id, student_id, document_id, request_id,
                      attempt_number, operation, provider, requested_model,
                      response_model, provider_request_id, http_status, response_ok,
                      finish_reason, usage, raw_response_text, response_body,
                      transport_error, duration_ms, recorded_at,
                      prompt_template_version_id, context_policy_version_id,
                      output_schema_version_id, config_revision, context_sha256,
                      prompt_cache_status
                    ) VALUES (
                      :id, :tenant_id, :student_id, :document_id, :request_id,
                      :attempt_number, :operation, :provider, :requested_model,
                      :response_model, :provider_request_id, :http_status, :response_ok,
                      :finish_reason, CAST(:usage AS jsonb), :raw_response_text,
                      CAST(:response_body AS jsonb), CAST(:transport_error AS jsonb),
                      :duration_ms, :recorded_at, :prompt_template_version_id,
                      :context_policy_version_id, :output_schema_version_id,
                      :config_revision, :context_sha256, :prompt_cache_status
                    )
                    ON CONFLICT (
                      tenant_id, document_id, request_id, attempt_number
                    ) DO NOTHING
                    """
                ),
                parameters,
            )

    async def get_student_dashboard(self, auth: AuthContext) -> dict[str, object]:
        """Read and map the current tenant-scoped student dashboard projection."""

        async with self._engine.connect() as connection:
            base_result = await connection.execute(
                text(self._dashboard_base_sql()),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                },
            )
            base = _first_mapping(base_result)
            if base is None:
                raise NotFoundError(
                    "STUDENT_DASHBOARD_NOT_FOUND",
                    "No dashboard is available for this student",
                )

            requirements: list[dict[str, object]] = []
            journey_id = base.get("journey_id")
            if journey_id is not None:
                requirement_result = await connection.execute(
                    text(self._dashboard_requirements_sql()),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "student_id": _uuid(auth.student_id),
                        "journey_id": journey_id,
                    },
                )
                requirements = [_map_requirement(row) for row in _mapping_rows(requirement_result)]

        journey_status = str(base.get("journey_status") or "not_started")
        completion_percent = (
            100
            if not requirements and journey_id is not None and journey_status == "completed"
            else 0
            if not requirements
            else math.floor(
                sum(
                    _database_int(item["progressPercent"], "progressPercent")
                    for item in requirements
                )
                / len(requirements)
                + 0.5
            )
        )
        next_requirement = next(
            (item for item in requirements if item["status"] in _PRIORITY_REQUIREMENT_STATUSES),
            None,
        )
        if next_requirement is None:
            next_requirement = next(
                (
                    item
                    for item in requirements
                    if item["status"] not in _TERMINAL_REQUIREMENT_STATUSES
                ),
                None,
            )

        journey_id_value = None if journey_id is None else str(journey_id)
        if journey_id_value and next_requirement is not None:
            code = str(next_requirement["code"])
            next_action = {
                "code": code,
                "label": str(next_requirement["title"]),
                "href": (f"/enrollment?requirement={quote(code, safe=_ENCODE_URI_COMPONENT_SAFE)}"),
            }
        elif journey_id_value and journey_status == "completed":
            next_action = {
                "code": "enrollment_complete",
                "label": "Enrollment complete",
                "href": "/enrollment",
            }
        elif journey_id_value:
            next_action = {
                "code": "review_enrollment",
                "label": "Review your enrollment",
                "href": "/enrollment",
            }
        else:
            next_action = {
                "code": "accept_offer",
                "label": "Review and accept your offer",
                "href": "/offer",
            }

        preferred_name = base.get("preferred_name")
        first_name = str(base["first_name"])
        offer_version = _database_int(base["offer_version"], "offer_version")
        stored_projection = _database_int(base.get("projection_version") or 0, "projection_version")
        return {
            "student": {
                "id": str(base["student_id"]),
                "preferredName": (
                    str(preferred_name) if preferred_name is not None else first_name
                ),
                "fullName": f"{first_name} {base['last_name']}",
                "classYear": _database_int(base["class_year"], "class_year"),
            },
            "offer": {
                "id": str(base["offer_id"]),
                "programName": str(base["program_name"]),
                "termName": str(base["term_name"]),
                "campusName": str(base["campus_name"]),
                "responseDeadline": str(base["response_deadline"]),
                "depositAmountCents": _database_int(
                    base["deposit_amount_cents"], "deposit_amount_cents"
                ),
                "status": str(base["offer_status"]),
            },
            "journey": {
                "id": journey_id_value,
                "status": journey_status,
                "completionPercent": completion_percent,
                "nextAction": next_action,
                "requirements": requirements,
            },
            "unreadMessageCount": _database_int(
                base["unread_message_count"], "unread_message_count"
            ),
            "projectionVersion": max(stored_projection, offer_version),
            "generatedAt": _iso_timestamp(self._clock()),
        }

    def _dashboard_base_sql(self) -> str:
        return f"""
            SELECT
              s.id AS student_id, p.preferred_name, p.first_name, p.last_name,
              s.class_year, o.id AS offer_id, pr.name AS program_name,
              at.name AS term_name, c.name AS campus_name,
              o.response_deadline::text AS response_deadline,
              o.deposit_amount_cents, o.status AS offer_status,
              o.version AS offer_version, j.id AS journey_id,
              j.status AS journey_status, spp.projection_version,
              (
                SELECT COUNT(*)
                FROM {self._table("student_message")} sm
                WHERE sm.tenant_id = s.tenant_id
                  AND sm.student_id = s.id
                  AND sm.read_at IS NULL
              ) AS unread_message_count
            FROM {self._table("student")} s
            JOIN {self._table("person")} p
              ON p.id = s.person_id AND p.tenant_id = s.tenant_id
            JOIN {self._table("admission_offer")} o
              ON o.student_id = s.id AND o.tenant_id = s.tenant_id
            JOIN {self._table("program")} pr
              ON pr.id = o.program_id AND pr.tenant_id = o.tenant_id
            JOIN {self._table("academic_term")} at
              ON at.id = o.academic_term_id AND at.tenant_id = o.tenant_id
            JOIN {self._table("campus")} c
              ON c.id = o.campus_id AND c.tenant_id = o.tenant_id
            LEFT JOIN {self._table("enrollment_journey")} j
              ON j.offer_id = o.id AND j.tenant_id = o.tenant_id
            LEFT JOIN {self._table("student_portal_projection")} spp
              ON spp.student_id = s.id AND spp.tenant_id = s.tenant_id
            WHERE s.tenant_id = :tenant_id AND s.id = :student_id
            ORDER BY o.created_at DESC
            LIMIT 1
        """

    def _dashboard_requirements_sql(self) -> str:
        return f"""
            SELECT
              sr.id, rdv.code, rdv.title, rdv.description, sr.status,
              rdv.blocking, sr.due_at, sr.progress_percent,
              reward.reward_points, reward.reward_earned
            FROM {self._table("student_requirement")} sr
            JOIN {self._table("enrollment_journey")} journey
              ON journey.id = sr.journey_id
             AND journey.tenant_id = sr.tenant_id
            JOIN {self._table("requirement_definition_version")} evidence_definition
              ON evidence_definition.id = sr.requirement_definition_version_id
             AND evidence_definition.tenant_id = sr.tenant_id
            JOIN {self._table("journey_requirement_definition")} current_link
              ON current_link.journey_definition_version_id =
                 journey.journey_definition_version_id
            JOIN {self._table("requirement_definition_version")} rdv
              ON rdv.id = current_link.requirement_definition_version_id
             AND rdv.tenant_id = sr.tenant_id
             AND rdv.code = evidence_definition.code
            LEFT JOIN LATERAL (
              SELECT
                COALESCE(SUM(rr.points), 0)::integer AS reward_points,
                CASE WHEN COUNT(rr.id) = 0 THEN false ELSE BOOL_AND(
                  EXISTS (
                    SELECT 1 FROM {self._table("student_reward_ledger")} ledger
                    WHERE ledger.tenant_id = sr.tenant_id
                      AND ledger.student_id = :student_id
                      AND ledger.reward_rule_id = rr.id
                      AND ledger.source_key = sr.id::text
                  )
                ) END AS reward_earned
              FROM {self._table("tenant_reward_rule")} rr
              WHERE rr.tenant_id = sr.tenant_id
                AND rr.trigger_type = 'requirement_completed'
                AND rr.trigger_key = rdv.code
                AND rr.enabled = true
                AND (rr.starts_at IS NULL OR rr.starts_at <= NOW())
                AND (rr.ends_at IS NULL OR rr.ends_at > NOW())
            ) reward ON true
            WHERE sr.tenant_id = :tenant_id AND sr.journey_id = :journey_id
              AND sr.retired_at IS NULL
            ORDER BY CASE rdv.flow_kind WHEN 'onboarding' THEN 0 ELSE 1 END,
                     rdv.display_order, sr.id
        """

    async def accept_admission_offer(
        self,
        auth: AuthContext,
        offer_id: str,
        idempotency_key: str,
        request_id: str,
    ) -> dict[str, object]:
        """Accept an offer and create a configured or zero-step journey atomically."""

        request_hash = offer_request_hash(auth, offer_id)
        lock_key = ":".join(
            (auth.tenant_id, auth.actor_id, _ACCEPT_OFFER_OPERATION, idempotency_key)
        )
        async with self._engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            existing_result = await connection.execute(
                text(
                    f"""
                    SELECT request_hash, response_body
                    FROM {self._table("idempotency_record")}
                    WHERE tenant_id = :tenant_id
                      AND actor_id = :actor_id
                      AND operation = :operation
                      AND idempotency_key = :idempotency_key
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "actor_id": _uuid(auth.actor_id),
                    "operation": _ACCEPT_OFFER_OPERATION,
                    "idempotency_key": idempotency_key,
                },
            )
            existing = _first_mapping(existing_result)
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise ConflictError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "This idempotency key was already used for a different request",
                    )
                return _json_object(existing["response_body"], "response_body")

            offer_result = await connection.execute(
                text(
                    f"""
                    SELECT
                      id, status, accepted_at, version,
                      response_deadline < CURRENT_DATE AS is_expired
                    FROM {self._table("admission_offer")}
                    WHERE tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND id = :offer_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                    "offer_id": _uuid(offer_id),
                },
            )
            offer = _first_mapping(offer_result)
            if offer is None:
                raise NotFoundError(
                    "ADMISSION_OFFER_NOT_FOUND", "The admission offer was not found"
                )

            offer_already_accepted = offer["status"] == "accepted"
            if offer_already_accepted:
                journey_result = await connection.execute(
                    text(
                        f"""
                        SELECT journey.id, journey.status, journey.version,
                               definition.code AS definition_code,
                               definition.onboarding_required,
                               EXISTS (
                                 SELECT 1
                                 FROM {self._table("journey_requirement_definition")} link
                                 WHERE link.journey_definition_version_id=definition.id
                               ) AS has_requirements
                        FROM {self._table("enrollment_journey")} journey
                        JOIN {self._table("journey_definition_version")} definition
                          ON definition.id = journey.journey_definition_version_id
                         AND definition.tenant_id = journey.tenant_id
                        WHERE journey.tenant_id = :tenant_id
                          AND journey.offer_id = :offer_id
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "offer_id": _uuid(offer_id),
                    },
                )
                journey = _first_mapping(journey_result)
                if offer.get("accepted_at") is None:
                    raise ApiError(
                        500,
                        "INCONSISTENT_OFFER_STATE",
                        "The accepted offer has no acceptance timestamp",
                    )
                if journey is not None:
                    onboarding_required = (
                        journey.get("definition_code") != _ZERO_STEP_JOURNEY_CODE
                        and journey.get("onboarding_required", True) is True
                    )
                    has_requirements = journey.get("has_requirements") is True
                    zero_step = not onboarding_required and not has_requirements
                    response: dict[str, object] = {
                        "offerId": str(offer["id"]),
                        "offerStatus": "accepted",
                        "journeyId": str(journey["id"]),
                        "journeyStatus": str(journey["status"]),
                        "projectionVersion": max(
                            _database_int(offer["version"], "offer.version"), 2
                        ),
                        "acceptedAt": _iso_timestamp(offer["accepted_at"]),
                        "onboardingRequired": onboarding_required,
                        "initialRoute": ("/onboarding" if onboarding_required else "/dashboard"),
                        **({"requirementCount": 0} if zero_step else {}),
                    }
                    await self._store_idempotent_response(
                        connection,
                        auth,
                        idempotency_key,
                        request_hash,
                        response,
                    )
                    return response

            if not offer_already_accepted and (
                offer["status"] != "offered" or bool(offer["is_expired"])
            ):
                raise ConflictError(
                    "ADMISSION_OFFER_NOT_ACTIVE",
                    "Only an active admission offer can be accepted",
                )

            accepted_at = (
                _datetime(offer["accepted_at"], "offer.accepted_at")
                if offer_already_accepted
                else _truncate_to_milliseconds(_as_utc(self._clock()))
            )
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"managed-config:{auth.tenant_id}:journeys"},
            )
            definition_result = await connection.execute(
                text(
                    f"""
                    SELECT id AS journey_definition_version_id,
                           code,
                           definition.onboarding_required,
                           EXISTS (
                             SELECT 1
                             FROM {self._table("journey_requirement_definition")} link
                             WHERE link.journey_definition_version_id=definition.id
                           ) AS has_requirements
                    FROM {self._table("journey_definition_version")} definition
                    WHERE definition.tenant_id = :tenant_id AND definition.active = 1
                    ORDER BY version DESC
                    LIMIT 1
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            definition = _first_mapping(definition_result)
            onboarding_required = (
                definition is not None
                and definition.get("code") != _ZERO_STEP_JOURNEY_CODE
                and definition.get("onboarding_required", True) is True
            )
            has_requirements = definition is not None and definition.get("has_requirements") is True
            zero_step = not onboarding_required and not has_requirements
            if definition is None:
                version_result = await connection.execute(
                    text(
                        f"""
                        SELECT COALESCE(MAX(version), 0) + 1 AS version
                        FROM {self._table("journey_definition_version")}
                        WHERE tenant_id = :tenant_id AND code = :code
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "code": _ZERO_STEP_JOURNEY_CODE,
                    },
                )
                version_row = _first_mapping(version_result)
                definition_id = self._new_id()
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("journey_definition_version")} (
                          id, tenant_id, code, version, active, onboarding_required,
                          created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :code, :version, 1, false,
                          :accepted_at, :accepted_at
                        )
                        """
                    ),
                    {
                        "id": _uuid(definition_id),
                        "tenant_id": _uuid(auth.tenant_id),
                        "code": _ZERO_STEP_JOURNEY_CODE,
                        "version": (
                            _database_int(version_row["version"], "journey.version")
                            if version_row is not None
                            else 1
                        ),
                        "accepted_at": accepted_at,
                    },
                )
            else:
                definition_id = str(definition["journey_definition_version_id"])

            if offer_already_accepted:
                offer_version = _database_int(offer["version"], "offer.version")
            else:
                update_result = await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("admission_offer")}
                        SET status = 'accepted',
                            accepted_at = :accepted_at,
                            version = version + 1,
                            updated_at = :accepted_at
                        WHERE id = :offer_id AND tenant_id = :tenant_id
                        RETURNING version
                        """
                    ),
                    {
                        "accepted_at": accepted_at,
                        "offer_id": _uuid(offer_id),
                        "tenant_id": _uuid(auth.tenant_id),
                    },
                )
                updated_offer = _first_mapping(update_result)
                if updated_offer is None or not _database_int(
                    updated_offer["version"], "offer.version"
                ):
                    raise ApiError(
                        500,
                        "OFFER_UPDATE_FAILED",
                        "The admission offer could not be updated",
                    )
                offer_version = _database_int(updated_offer["version"], "offer.version")

            journey_id = self._new_id()
            journey_status = "completed" if zero_step else "in_progress"
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("enrollment_journey")} (
                      id, tenant_id, student_id, offer_id,
                      journey_definition_version_id, status, version,
                      created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :student_id, :offer_id,
                      :definition_id, :journey_status, 1, :accepted_at, :accepted_at
                    )
                    """
                ),
                {
                    "id": _uuid(journey_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                    "offer_id": _uuid(offer_id),
                    "definition_id": _uuid(definition_id),
                    "journey_status": journey_status,
                    "accepted_at": accepted_at,
                },
            )

            requirement_definition_result = await connection.execute(
                text(
                    f"""
                    SELECT rdv.id, rdv.code, rdv.depends_on_codes, rdv.due_offset_days
                    FROM {self._table("journey_requirement_definition")} jrd
                    JOIN {self._table("requirement_definition_version")} rdv
                      ON rdv.id = jrd.requirement_definition_version_id
                    WHERE jrd.journey_definition_version_id = :definition_id
                      AND rdv.tenant_id = :tenant_id
                    ORDER BY rdv.display_order
                    """
                ),
                {
                    "definition_id": _uuid(definition_id),
                    "tenant_id": _uuid(auth.tenant_id),
                },
            )
            requirement_ids: list[str] = []
            for definition_row in _mapping_rows(requirement_definition_result):
                requirement_id = self._new_id()
                requirement_ids.append(requirement_id)
                due_offset_days = definition_row.get("due_offset_days")
                due_at = (
                    None
                    if due_offset_days is None
                    else accepted_at
                    + timedelta(days=_database_int(due_offset_days, "requirement.due_offset_days"))
                )
                dependencies = definition_row.get("depends_on_codes")
                initial_status = "ready" if not dependencies else "blocked"
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("student_requirement")} (
                          id, tenant_id, journey_id,
                          requirement_definition_version_id, status, due_at,
                          progress_percent, version, created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :journey_id, :definition_id,
                          :status, :due_at, 0, 1, :accepted_at, :accepted_at
                        )
                        """
                    ),
                    {
                        "id": _uuid(requirement_id),
                        "tenant_id": _uuid(auth.tenant_id),
                        "journey_id": _uuid(journey_id),
                        "definition_id": _uuid(str(definition_row["id"])),
                        "status": initial_status,
                        "due_at": due_at,
                        "accepted_at": accepted_at,
                    },
                )

            if not onboarding_required:
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("student_onboarding")} AS onboarding (
                          tenant_id, student_id, status, current_step,
                          payload, completed_at, created_at, updated_at
                        ) VALUES (
                          :tenant_id, :student_id, 'completed', 'offer',
                          CAST(:payload AS jsonb), :accepted_at, :accepted_at, :accepted_at
                        )
                        ON CONFLICT (tenant_id, student_id) DO UPDATE SET
                          status = 'completed',
                          completed_at = COALESCE(
                            onboarding.completed_at,
                            EXCLUDED.completed_at
                          ),
                          payload = onboarding.payload || EXCLUDED.payload,
                          version = CASE
                            WHEN onboarding.status = 'completed'
                              THEN onboarding.version
                            ELSE onboarding.version + 1
                          END,
                          updated_at = EXCLUDED.updated_at
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "student_id": _uuid(auth.student_id),
                        "payload": _json(
                            {"onboardingCompletionReason": "no_active_core_onboarding"}
                        ),
                        "accepted_at": accepted_at,
                    },
                )

            lineage = _runtime_lineage(request_id, _ACCEPT_OFFER_EFFECT)
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("audit_event")} (
                      id, tenant_id, actor_type, actor_id, student_id, action,
                      resource_type, resource_id, authorization_basis, request_id,
                      correlation_id, metadata, occurred_at, created_at
                    ) VALUES (
                      :id, :tenant_id, :actor_type, :actor_id, :student_id,
                      :audit_action, 'admission_offer', :offer_id,
                      'student_self_service', :request_id, :request_id,
                      CAST(:metadata AS jsonb), :accepted_at, :accepted_at
                    )
                    """
                ),
                {
                    "id": _uuid(self._new_id()),
                    "tenant_id": _uuid(auth.tenant_id),
                    "actor_type": auth.actor_type,
                    "actor_id": _uuid(auth.actor_id),
                    "student_id": _uuid(auth.student_id),
                    "offer_id": _uuid(offer_id),
                    "audit_action": (
                        "admission_offer.journey_repaired"
                        if offer_already_accepted
                        else "admission_offer.accepted"
                    ),
                    "request_id": request_id,
                    "metadata": _json(
                        {
                            "changedFields": (
                                ["enrollment_journey"]
                                if offer_already_accepted
                                else ["status", "accepted_at"]
                            ),
                            "zeroStepJourney": zero_step,
                            "lineage": lineage,
                        }
                    ),
                    "accepted_at": accepted_at,
                },
            )

            causation_id = self._new_id()
            if not offer_already_accepted:
                await self._insert_outbox_event(
                    connection,
                    event_name="admission.offer_accepted.v1",
                    aggregate_type="admission_offer",
                    aggregate_id=offer_id,
                    aggregate_version=offer_version,
                    auth=auth,
                    occurred_at=accepted_at,
                    correlation_id=request_id,
                    causation_id=causation_id,
                    data={
                        "offerId": offer_id,
                        "studentId": auth.student_id,
                        "journeyId": journey_id,
                        "acceptedAt": _iso_timestamp(accepted_at),
                    },
                )
            await self._insert_outbox_event(
                connection,
                event_name="enrollment.journey_created.v1",
                aggregate_type="enrollment_journey",
                aggregate_id=journey_id,
                aggregate_version=1,
                auth=auth,
                occurred_at=accepted_at,
                correlation_id=request_id,
                causation_id=causation_id,
                data={
                    "journeyId": journey_id,
                    "studentId": auth.student_id,
                    "offerId": offer_id,
                    "requirementIds": requirement_ids,
                },
            )

            response = {
                "offerId": offer_id,
                "offerStatus": "accepted",
                "journeyId": journey_id,
                "journeyStatus": journey_status,
                "projectionVersion": max(offer_version, 2),
                "acceptedAt": _iso_timestamp(accepted_at),
                "onboardingRequired": onboarding_required,
                "initialRoute": "/onboarding" if onboarding_required else "/dashboard",
                **({"requirementCount": 0} if zero_step else {}),
            }
            await self._store_idempotent_response(
                connection,
                auth,
                idempotency_key,
                request_hash,
                response,
            )
            return response

    async def ingest_activity_events(
        self,
        auth: AuthContext,
        events: Sequence[Mapping[str, object]],
        request_id: str,
    ) -> dict[str, int]:
        """Persist a bounded client-signal batch and award matching rewards atomically."""

        for event in events:
            validate_activity_event_properties(event)

        accepted = 0
        async with self._engine.begin() as connection:
            for event in events:
                event_id = _required_string(event, "eventId")
                event_name = _required_string(event, "eventName")
                properties_value = event.get("properties")
                if not isinstance(properties_value, Mapping):
                    raise BadRequestError(
                        "INVALID_ACTIVITY_PROPERTY", "Properties must be an object"
                    )
                properties = {str(key): value for key, value in properties_value.items()}
                correlation_id = _optional_string(event.get("correlationId")) or request_id
                insert_result = await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("activity_event")} (
                          tenant_id, event_id, event_name, occurred_at, received_at,
                          actor_type, actor_id, student_id, session_id,
                          page_instance_id, correlation_id, trust_level,
                          application_version, properties
                        ) VALUES (
                          :tenant_id, :event_id, :event_name, :occurred_at, NOW(),
                          :actor_type, :actor_id, :student_id, :session_id,
                          :page_instance_id, :correlation_id, 'client_signal',
                          '0.1.0', CAST(:properties AS jsonb)
                        )
                        ON CONFLICT (tenant_id, event_id) DO NOTHING
                        RETURNING event_id
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "event_id": _uuid(event_id),
                        "event_name": event_name,
                        "occurred_at": _datetime(event.get("occurredAt"), "occurredAt"),
                        "actor_type": auth.actor_type,
                        "actor_id": _uuid(auth.actor_id),
                        "student_id": _uuid(auth.student_id),
                        "session_id": _required_string(event, "sessionId"),
                        "page_instance_id": _required_string(event, "pageInstanceId"),
                        "correlation_id": correlation_id,
                        "properties": _json(properties),
                    },
                )
                if not _mapping_rows(insert_result):
                    continue
                accepted += 1
                section = properties.get("section")
                source_key = f"{event_name}:{section}" if isinstance(section, str) else event_id
                await self._award_matching_rewards(
                    connection,
                    auth,
                    trigger_type="activity_event",
                    trigger_key=event_name,
                    source_key=source_key,
                    properties=properties,
                )
        return {"accepted": accepted, "duplicates": len(events) - accepted}

    async def _award_matching_rewards(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        *,
        trigger_type: str,
        trigger_key: str,
        source_key: str,
        properties: Mapping[str, object],
    ) -> int:
        rule_result = await connection.execute(
            text(
                f"""
                SELECT id, points, max_awards_per_student
                FROM {self._table("tenant_reward_rule")}
                WHERE tenant_id = :tenant_id
                  AND trigger_type = :trigger_type
                  AND trigger_key = :trigger_key
                  AND enabled = true
                  AND (starts_at IS NULL OR starts_at <= NOW())
                  AND (ends_at IS NULL OR ends_at > NOW())
                  AND CAST(:properties AS jsonb) @> trigger_properties
                ORDER BY display_order, id
                FOR UPDATE
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "trigger_type": trigger_type,
                "trigger_key": trigger_key,
                "properties": _json(properties),
            },
        )
        awarded = 0
        for rule in _mapping_rows(rule_result):
            ledger_result = await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("student_reward_ledger")} (
                      id, tenant_id, student_id, reward_rule_id, source_type,
                      source_key, points, metadata, awarded_at
                    )
                    SELECT
                      :id, :tenant_id, :student_id, :reward_rule_id,
                      :trigger_type, :source_key, :points,
                      CAST(:metadata AS jsonb), NOW()
                    WHERE (
                      SELECT COUNT(*)
                      FROM {self._table("student_reward_ledger")} existing
                      WHERE existing.tenant_id = :tenant_id
                        AND existing.student_id = :student_id
                        AND existing.reward_rule_id = :reward_rule_id
                    ) < :max_awards_per_student
                    ON CONFLICT (
                      tenant_id, student_id, reward_rule_id, source_key
                    ) DO NOTHING
                    RETURNING points
                    """
                ),
                {
                    "id": _uuid(self._new_id()),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                    "reward_rule_id": _uuid(str(rule["id"])),
                    "trigger_type": trigger_type,
                    "source_key": source_key,
                    "points": _database_int(rule["points"], "reward.points"),
                    "metadata": _json({"triggerKey": trigger_key, "properties": dict(properties)}),
                    "max_awards_per_student": _database_int(
                        rule["max_awards_per_student"], "reward.max_awards_per_student"
                    ),
                },
            )
            awarded += sum(
                _database_int(row["points"], "reward.points")
                for row in _mapping_rows(ledger_result)
            )
        return awarded

    async def _store_idempotent_response(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        idempotency_key: str,
        request_hash: str,
        response: Mapping[str, object],
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("idempotency_record")} (
                  tenant_id, actor_id, operation, idempotency_key,
                  request_hash, response_status, response_body,
                  created_at, expires_at
                ) VALUES (
                  :tenant_id, :actor_id, :operation, :idempotency_key,
                  :request_hash, 200, CAST(:response_body AS jsonb),
                  NOW(), NOW() + INTERVAL '24 hours'
                )
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "actor_id": _uuid(auth.actor_id),
                "operation": _ACCEPT_OFFER_OPERATION,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "response_body": _json(response),
            },
        )

    async def _insert_outbox_event(
        self,
        connection: AsyncConnection,
        *,
        event_name: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        auth: AuthContext,
        occurred_at: datetime,
        correlation_id: str,
        causation_id: str,
        data: Mapping[str, object],
    ) -> None:
        event_id = self._new_id()
        envelope = {
            "eventId": event_id,
            "eventName": event_name,
            "occurredAt": _iso_timestamp(occurred_at),
            "tenantId": auth.tenant_id,
            "aggregateType": aggregate_type,
            "aggregateId": aggregate_id,
            "aggregateVersion": aggregate_version,
            "actor": {"type": auth.actor_type, "id": auth.actor_id},
            "correlationId": correlation_id,
            "causationId": causation_id,
            "lineage": _runtime_lineage(correlation_id, _ACCEPT_OFFER_EFFECT),
            "data": dict(data),
        }
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("outbox_event")} (
                  id, tenant_id, event_name, aggregate_type, aggregate_id,
                  aggregate_version, occurred_at, actor_type, actor_id,
                  correlation_id, causation_id, payload, created_at
                ) VALUES (
                  :id, :tenant_id, :event_name, :aggregate_type, :aggregate_id,
                  :aggregate_version, :occurred_at, :actor_type, :actor_id,
                  :correlation_id, :causation_id, CAST(:payload AS jsonb), :occurred_at
                )
                """
            ),
            {
                "id": _uuid(event_id),
                "tenant_id": _uuid(auth.tenant_id),
                "event_name": event_name,
                "aggregate_type": aggregate_type,
                "aggregate_id": _uuid(aggregate_id),
                "aggregate_version": aggregate_version,
                "occurred_at": occurred_at,
                "actor_type": auth.actor_type,
                "actor_id": _uuid(auth.actor_id),
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "payload": _json(envelope),
            },
        )

    def _new_id(self) -> str:
        return str(self._uuid_factory())


def _map_requirement(row: Mapping[str, object]) -> dict[str, object]:
    requirement: dict[str, object] = {
        "id": str(row["id"]),
        "code": str(row["code"]),
        "title": str(row["title"]),
        "description": str(row["description"]),
        "status": str(row["status"]),
        "blocking": _database_int(row["blocking"], "requirement.blocking") == 1,
        "dueAt": (None if row.get("due_at") is None else _iso_timestamp(row["due_at"])),
        "progressPercent": _database_int(row["progress_percent"], "requirement.progress_percent"),
    }
    reward_points = _database_int(row.get("reward_points") or 0, "reward.points")
    if reward_points > 0:
        requirement["reward"] = {
            "points": reward_points,
            "earned": row.get("reward_earned") is True,
        }
    return requirement


def _runtime_lineage(correlation_id: str, effect_id: str) -> dict[str, object]:
    lineage: dict[str, object] = {
        "correlationId": correlation_id,
        "effectRegistryVersion": 1,
        "effectId": effect_id,
    }
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        lineage["traceId"] = f"{span_context.trace_id:032x}"
        lineage["spanId"] = f"{span_context.span_id:016x}"
    return lineage


def _mapping_rows(result: Any) -> list[dict[str, object]]:
    return [dict(row) for row in result.mappings().all()]


def _first_mapping(result: Any) -> dict[str, object] | None:
    rows = _mapping_rows(result)
    return rows[0] if rows else None


def _required_string(source: Mapping[str, object], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional string value must be a string or null")
    return value


def _required_int(source: Mapping[str, object], key: str) -> int:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("optional integer value must be an integer or null")
    return value


def _database_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as error:
            raise ValueError(f"Database returned an invalid integer for {field}") from error
    raise ValueError(f"Database returned an invalid integer for {field}")


def _uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("UUID value must be a string")
    return UUID(value)


def _optional_uuid(value: object) -> UUID | None:
    return None if value is None else _uuid(value)


def _datetime(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an ISO 8601 date-time")
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO 8601 date-time") from error


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _truncate_to_milliseconds(value: datetime) -> datetime:
    return value.replace(microsecond=(value.microsecond // 1_000) * 1_000)


def _iso_timestamp(value: object) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        parsed = datetime(value.year, value.month, value.day, tzinfo=UTC)
    elif isinstance(value, datetime):
        parsed = _as_utc(value)
    elif isinstance(value, str):
        try:
            parsed = _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError as error:
            raise ValueError("Database returned an invalid timestamp") from error
    else:
        raise ValueError("Database returned an invalid timestamp")
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.") + f"{parsed.microsecond // 1000:03d}Z"


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _iso_timestamp(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _json_object(value: object, field: str) -> dict[str, object]:
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as error:
            raise ApiError(500, "INVALID_IDEMPOTENCY_RECORD", f"{field} is invalid") from error
    if not isinstance(parsed, Mapping):
        raise ApiError(500, "INVALID_IDEMPOTENCY_RECORD", f"{field} is invalid")
    return {str(key): item for key, item in parsed.items()}
