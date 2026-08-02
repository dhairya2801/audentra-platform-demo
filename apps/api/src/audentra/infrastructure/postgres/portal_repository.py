# ruff: noqa: S608 -- only the module-owned DOCUMENT_SELECT constant is interpolated.
"""Async SQLAlchemy Core port of the legacy PostgreSQL portal store.

The repository deliberately owns transaction boundaries for commands.  State,
audit, outbox, rewards, and idempotency records therefore commit or roll back
together exactly as they did in the Nest implementation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError
from audentra.domain.onboarding import (
    ONBOARDING_STEPS,
    is_skippable_onboarding_step,
    validate_onboarding_step,
)
from audentra.infrastructure.messaging.envelope import DomainEventActor, DomainEventEnvelope
from audentra.infrastructure.messaging.outbox import OutboxRepository, OutboxRepositoryConfig

JsonDict = dict[str, Any]
IdempotentHandler = Callable[[AsyncConnection], Awaitable[JsonDict]]

REQUIREMENT_SLUGS = {
    "profile_verification": "profile-verification",
    "identity_document": "identity-document-upload",
    "official_transcript": "transcript-upload",
    "financial_aid_verification": "financial-aid-verification",
    "immunization_record": "immunization-upload",
    "enrollment_deposit": "enrollment-deposit",
}
DOCUMENT_CATEGORIES = {
    "identity_document": "identity",
    "official_transcript": "transcript",
    "financial_aid_verification": "financial_aid",
    "immunization_record": "health",
}
PROCESSING_MODES = {
    "identity": "agentic",
    "transcript": "agentic",
    "health": "agentic",
    "residency": "agentic",
    "financial_aid": "classification_only",
    "consent": "manual_review",
    "other": "agentic",
}
DOCUMENT_SELECT = """
id, requirement_id, file_name, mime_type, size_bytes, category,
processing_mode, status, storage_key, sha256, extraction, created_at
"""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    raise RuntimeError("Database returned an invalid timestamp")


def _nullable_iso(value: object) -> str | None:
    return None if value is None else _iso(value)


def _timestamp(value: object) -> datetime:
    """Normalize an API timestamp before binding it to a PostgreSQL timestamptz."""

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise RuntimeError("Application produced an invalid timestamp")


def _mapping(value: object) -> JsonDict:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return {str(key): item for key, item in parsed.items()}
    return {}


def _list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def _map_onboarding(row: Mapping[str, Any]) -> JsonDict:
    return {
        "studentId": str(row["student_id"]),
        "status": row["status"],
        "currentStep": row["current_step"],
        "completedSteps": _list(row["completed_steps"]),
        "data": _mapping(row["payload"]),
        "version": int(row["version"]),
        "completedAt": _nullable_iso(row["completed_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


def _map_housing(row: Mapping[str, Any], residences: list[JsonDict] | None = None) -> JsonDict:
    payload = _mapping(row["payload"])
    preference = payload.get("housingPreference")
    if preference not in {"on_campus", "off_campus", "commuting", "undecided", "family"}:
        preference = None
    residence = payload.get("housingResidenceOption")
    if residence not in {"aster_residence_hall", "aster_apartments", "student_village"}:
        residence = None
    return {
        "preference": preference,
        "residenceOption": residence,
        "residences": residences or [],
        "version": int(row["version"]),
        "updatedAt": _iso(row["updated_at"]),
    }


def _requirement_code(identifier: str) -> str:
    reverse = {slug: code for code, slug in REQUIREMENT_SLUGS.items()}
    return reverse.get(identifier, identifier.lower().replace("-", "_"))


def _map_requirement(row: Mapping[str, Any]) -> JsonDict:
    code = str(row["code"])
    item: JsonDict = {
        "id": str(row["id"]),
        "slug": REQUIREMENT_SLUGS.get(code, code.lower().replace("_", "-")),
        "journeyId": str(row["journey_id"]),
        "code": code,
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "blocking": bool(row["blocking"]),
        "dueAt": _nullable_iso(row.get("due_at")),
        "progressPercent": int(row["progress_percent"]),
        "submissionType": row["submission_type"],
        "documentCategory": DOCUMENT_CATEGORIES.get(code),
        "responsibleOffice": row["responsible_office"],
        "dependencyCodes": _list(row["depends_on_codes"]),
    }
    points = int(row.get("reward_points") or 0)
    if points > 0:
        item["reward"] = {"points": points, "earned": row.get("reward_earned") is True}
    return item


def _map_message(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "subject": row["subject"],
        "body": row["body"],
        "senderName": row["sender_name"],
        "sentAt": _iso(row["sent_at"]),
        "readAt": _nullable_iso(row["read_at"]),
    }


def _map_document(row: Mapping[str, Any]) -> JsonDict:
    item: JsonDict = {
        "id": str(row["id"]),
        "fileName": row["file_name"],
        "mimeType": row["mime_type"],
        "sizeBytes": int(row["size_bytes"]),
        "category": row["category"],
        "processingMode": row["processing_mode"],
        "status": row["status"],
        "createdAt": _iso(row["created_at"]),
    }
    if row.get("requirement_id"):
        item["requirementId"] = str(row["requirement_id"])
    if row.get("storage_key"):
        item["contentUrl"] = f"/v1/student/documents/{row['id']}/content"
    if row.get("sha256"):
        item["sha256"] = str(row["sha256"])
    if row.get("extraction"):
        item["extraction"] = _mapping(row["extraction"])
    return item


def _map_signed_document(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "fileName": row["file_name"],
        "mimeType": "application/pdf",
        "sizeBytes": int(row["size_bytes"]),
        "category": "other",
        "processingMode": "generated",
        "status": "accepted",
        "contentUrl": f"/v1/student/documents/{row['id']}/content",
        "sha256": str(row["sha256"]),
        "signature": {
            "templateCode": row["template_code"],
            "title": row["title"],
            "signerName": row["signer_name"],
            "method": row["signature_method"],
            "signedAt": _iso(row["signed_at"]),
            "onboardingVersion": int(row["onboarding_version"]),
        },
        "createdAt": _iso(row["created_at"]),
    }


def _map_profile(row: Mapping[str, Any]) -> JsonDict:
    item: JsonDict = {
        "studentId": str(row["student_id"]),
        "preferredName": row["preferred_name"],
        "pronouns": row.get("pronouns"),
        "mobilePhone": row.get("mobile_phone"),
        "communicationPreference": row["communication_preference"],
        "version": int(row["version"]),
        "updatedAt": _iso(row["updated_at"]),
    }
    if row.get("first_name"):
        item["firstName"] = row["first_name"]
    if row.get("last_name"):
        item["lastName"] = row["last_name"]
    if row.get("email"):
        item.update(
            {
                "email": row["email"],
                "emailVerified": row.get("email_verified") is True,
                "phoneVerified": row.get("phone_verified") is True,
            }
        )
    return item


def _map_appointment(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "type": row["type"],
        "startsAt": _iso(row["starts_at"]),
        "notes": row.get("notes"),
        "status": row["status"],
        "createdAt": _iso(row["created_at"]),
    }


def _map_payment(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "offerId": str(row["offer_id"]),
        "type": "enrollment_deposit",
        "amountCents": int(row["amount_cents"]),
        "status": row["status"],
        "processor": "dummy",
        "processorReference": row["processor_reference"],
        "createdAt": _iso(row["created_at"]),
    }


def _map_help_request(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "topicCode": row["topic_code"],
        "subject": row["subject"],
        "message": row["message"],
        "status": row["status"],
        "priority": row["priority"],
        "assigneeId": str(row["assignee_id"]) if row.get("assignee_id") else None,
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
        "version": int(row["version"]),
    }


def _map_source(row: Mapping[str, Any]) -> JsonDict | None:
    if not row.get("source_label") or not row.get("source_url") or not row.get("source_status"):
        return None
    return {
        "label": row["source_label"],
        "url": row["source_url"],
        "dataStatus": row["source_status"],
    }


def _map_course(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "code": row["code"],
        "title": row["title"],
        "description": row["description"],
        "credits": float(row["credits"]),
        "level": int(row["level"]),
        "prerequisites": _list(row.get("prerequisites")),
        "availabilityLabel": row.get("availability_label"),
        "instructorNames": _list(row.get("instructor_names")),
        "meetingPattern": row.get("meeting_pattern"),
        "resources": _list(row.get("resources")),
        "source": _map_source(row),
    }


def _map_program(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "code": row["code"],
        "name": row["name"],
        "degree": row["degree"],
        "totalCredits": int(row["total_credits"]),
        "description": row["description"],
        "source": _map_source(row),
    }


class PostgresPortalRepository:
    """Tenant-scoped SQLAlchemy Core repository for every portal store method."""

    def __init__(self, engine: AsyncEngine, outbox: OutboxRepository | None = None) -> None:
        self.engine = engine
        self.outbox = outbox or OutboxRepository(
            engine, OutboxRepositoryConfig(worker_id="audentra-api")
        )

    async def _all(self, statement: str, params: Mapping[str, Any]) -> list[JsonDict]:
        async with self.engine.connect() as connection:
            result = await connection.execute(text(statement), dict(params))
            return [dict(row) for row in result.mappings().all()]

    async def _one(self, statement: str, params: Mapping[str, Any]) -> JsonDict | None:
        rows = await self._all(statement, params)
        return rows[0] if rows else None

    async def get_student_bootstrap(self, auth: AuthContext) -> JsonDict:
        async with self.engine.begin() as connection:
            await self._reconcile_authoritative_rewards(connection, auth)
        row = await self._one(
            """
            SELECT s.id AS student_id,
                   COALESCE(sp.preferred_name, p.preferred_name, p.first_name) AS preferred_name,
                   p.first_name || ' ' || p.last_name AS full_name,
                   so.status, so.current_step, so.version,
                   (SELECT COUNT(*)::integer FROM student_message message
                    WHERE message.tenant_id = s.tenant_id
                      AND message.student_id = s.id AND message.read_at IS NULL)
                     AS unread_message_count
            FROM student s
            JOIN person p ON p.id = s.person_id AND p.tenant_id = s.tenant_id
            JOIN student_onboarding so
              ON so.student_id = s.id AND so.tenant_id = s.tenant_id
            LEFT JOIN student_profile sp
              ON sp.student_id = s.id AND sp.tenant_id = s.tenant_id
            WHERE s.tenant_id = :tenant_id AND s.id = :student_id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if row is None:
            raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
        required = row["status"] != "completed"
        response: JsonDict = {
            "authenticated": True,
            "student": {
                "id": str(row["student_id"]),
                "preferredName": row["preferred_name"],
                "fullName": row["full_name"],
            },
            "onboarding": {
                "required": required,
                "status": row["status"],
                "currentStep": row["current_step"],
                "version": int(row["version"]),
            },
            "unreadMessageCount": int(row["unread_message_count"]),
            "initialRoute": "/onboarding" if required else "/dashboard",
            "generatedAt": _iso(_utc_now()),
        }
        rewards = await self._get_reward_summary(auth)
        if rewards:
            response["rewards"] = rewards
        return response

    async def get_student_onboarding(self, auth: AuthContext) -> JsonDict:
        row = await self._one(
            """
            SELECT student_id, status, current_step, completed_steps, payload,
                   version, completed_at, updated_at
            FROM student_onboarding
            WHERE tenant_id = :tenant_id AND student_id = :student_id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if row is None:
            raise NotFoundError("STUDENT_ONBOARDING_NOT_FOUND", "Student onboarding was not found")
        return _map_onboarding(row)

    async def get_student_housing_plan(self, auth: AuthContext) -> JsonDict:
        onboarding = await self._one(
            """
            SELECT student_id, status, current_step, completed_steps, payload,
                   version, completed_at, updated_at
            FROM student_onboarding
            WHERE tenant_id = :tenant_id AND student_id = :student_id
            LIMIT 1
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if onboarding is None:
            raise NotFoundError("STUDENT_ONBOARDING_NOT_FOUND", "Student onboarding was not found")
        rows = await self._all(
            """
            SELECT residence.id, residence.code, residence.name, residence.description,
                   residence.amenities, media.public_path, media.alt_text,
                   media.attribution, media.source_url
            FROM housing_residence_option residence
            JOIN media_asset media ON media.id = residence.media_asset_id
              AND media.tenant_id = residence.tenant_id AND media.active = true
            WHERE residence.tenant_id = :tenant_id AND residence.active = true
            ORDER BY residence.display_order, residence.id
            """,
            {"tenant_id": auth.tenant_id},
        )
        residences = [
            {
                "id": str(row["id"]),
                "value": row["code"],
                "name": row["name"],
                "description": row["description"],
                "amenities": _list(row["amenities"]),
                "imageUrl": row["public_path"],
                "imageAlt": row["alt_text"],
                "attribution": row["attribution"],
                "sourceUrl": row["source_url"],
            }
            for row in rows
        ]
        return _map_housing(onboarding, residences)

    async def update_student_housing_plan(
        self, auth: AuthContext, update: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            current = await self._locked_onboarding(connection, auth)
            if int(current["version"]) != update["expectedVersion"]:
                raise ConflictError(
                    "VERSION_CONFLICT", "Your housing plan changed in another session"
                )
            residence = (
                update.get("residenceOption") if update["preference"] == "on_campus" else None
            )
            payload = _mapping(current["payload"])
            payload.update(
                {"housingPreference": update["preference"], "housingResidenceOption": residence}
            )
            result = await connection.execute(
                text(
                    """
                    UPDATE student_onboarding SET payload = CAST(:payload AS jsonb),
                      version = version + 1, updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                      AND version = :expected_version
                    RETURNING student_id, status, current_step, completed_steps, payload,
                              version, completed_at, updated_at
                    """
                ),
                {
                    "payload": _json(payload),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "expected_version": update["expectedVersion"],
                },
            )
            row = result.mappings().first()
            if row is None:
                raise ConflictError(
                    "VERSION_CONFLICT", "Your housing plan changed in another session"
                )
            updated = dict(row)
            await self._complete_requirement(connection, auth, "housing_preference")
            await self._insert_audit(
                connection,
                auth,
                "student_housing_plan.updated",
                "student_onboarding",
                auth.student_id,
                request_id,
                {
                    "preference": update["preference"],
                    "residenceOption": residence,
                    "version": int(updated["version"]),
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.housing_plan_updated.v1",
                "student_onboarding",
                auth.student_id,
                int(updated["version"]),
                request_id,
                {
                    "studentId": auth.student_id,
                    "preference": update["preference"],
                    "residenceOption": residence,
                },
            )
            return _map_housing(updated)

    async def update_student_onboarding(
        self, auth: AuthContext, update: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            current = await self._locked_onboarding(connection, auth)
            if current["status"] == "completed":
                raise ConflictError(
                    "ONBOARDING_ALREADY_COMPLETED", "Completed onboarding cannot be changed"
                )
            if int(current["version"]) != update["expectedVersion"]:
                raise ConflictError("VERSION_CONFLICT", "Onboarding changed in another session")
            target = str(update["currentStep"])
            active = str(current["current_step"])
            completed = [str(item) for item in _list(current["completed_steps"])]
            if (
                target not in ONBOARDING_STEPS
                or active not in ONBOARDING_STEPS
                or (target != active and target not in completed)
                or ONBOARDING_STEPS.index(target) > ONBOARDING_STEPS.index(active)
            ):
                raise ConflictError(
                    "ONBOARDING_STEP_OUT_OF_ORDER",
                    f"The next required onboarding step is {active}",
                )
            expected_prior = list(ONBOARDING_STEPS[: ONBOARDING_STEPS.index(active)])
            if completed != expected_prior:
                raise ApiError(
                    500,
                    "ONBOARDING_STATE_INVALID",
                    "The stored onboarding sequence is inconsistent",
                )
            skip = update.get("skip") is True
            if skip and not is_skippable_onboarding_step(target):
                raise BadRequestError(
                    "ONBOARDING_STEP_REQUIRED",
                    "This onboarding step is required before you can continue",
                )
            submitted = dict(update.get("data", {}))
            submitted.pop("skippedSteps", None)
            merged = {**_mapping(current["payload"]), **submitted}
            skipped = list(_mapping(current["payload"]).get("skippedSteps", []))
            if skip and target not in skipped:
                skipped.append(target)
            if not skip:
                skipped = [step for step in skipped if step != target]
                await self._validate_onboarding_step(connection, auth, target, merged)
            merged["skippedSteps"] = skipped
            synchronized_profile_version: int | None = None
            if target == "about_you" and all(
                merged.get(field)
                for field in (
                    "firstName",
                    "lastName",
                    "preferredName",
                    "mobilePhone",
                    "communicationPreference",
                )
            ):
                await connection.execute(
                    text(
                        """
                        UPDATE person p SET first_name=:first_name, last_name=:last_name,
                          preferred_name=:preferred_name, updated_at=NOW()
                        FROM student s WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                          AND p.tenant_id=s.tenant_id AND p.id=s.person_id
                        """
                    ),
                    {
                        "first_name": merged["firstName"],
                        "last_name": merged["lastName"],
                        "preferred_name": merged["preferredName"],
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
                profile_result = await connection.execute(
                    text(
                        """
                        UPDATE student_profile SET preferred_name=:preferred_name,
                          mobile_phone=:mobile_phone,
                          communication_preference=:communication_preference,
                          version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                        RETURNING version
                        """
                    ),
                    {
                        "preferred_name": merged["preferredName"],
                        "mobile_phone": merged["mobilePhone"],
                        "communication_preference": merged["communicationPreference"],
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
                profile_row = profile_result.mappings().first()
                synchronized_profile_version = (
                    int(profile_row["version"]) if profile_row is not None else None
                )
                await self._complete_requirement(connection, auth, "profile_verification")
            advancing = target == active
            next_step = (
                ONBOARDING_STEPS[min(ONBOARDING_STEPS.index(active) + 1, len(ONBOARDING_STEPS) - 1)]
                if advancing
                else active
            )
            next_completed = [*completed, active] if advancing else completed
            result = await connection.execute(
                text(
                    """
                    UPDATE student_onboarding SET status='in_progress', current_step=:next_step,
                      completed_steps=CAST(:completed_steps AS text[]),
                      payload=CAST(:payload AS jsonb), version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                    RETURNING student_id, status, current_step, completed_steps, payload,
                              version, completed_at, updated_at
                    """
                ),
                {
                    "next_step": next_step,
                    "completed_steps": next_completed,
                    "payload": _json(merged),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise ApiError(500, "ONBOARDING_UPDATE_FAILED", "Onboarding could not be saved")
            updated = dict(row)
            await self._insert_audit(
                connection,
                auth,
                "student_onboarding.step_completed"
                if advancing
                else "student_onboarding.step_updated",
                "student_onboarding",
                auth.student_id,
                request_id,
                {
                    "step": target,
                    "skipped": skip,
                    "version": int(updated["version"]),
                    "profileSynchronized": synchronized_profile_version is not None,
                },
            )
            if synchronized_profile_version is not None:
                await self._insert_outbox(
                    connection,
                    auth,
                    "student.profile_updated.v1",
                    "student_profile",
                    auth.student_id,
                    synchronized_profile_version,
                    request_id,
                    {
                        "studentId": auth.student_id,
                        "source": "onboarding.about_you",
                        "changedFields": [
                            "firstName",
                            "lastName",
                            "preferredName",
                            "mobilePhone",
                            "communicationPreference",
                        ],
                    },
                )
            return _map_onboarding(updated)

    async def complete_student_onboarding(
        self,
        auth: AuthContext,
        update: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            current = await self._locked_onboarding(connection, auth)
            if current["status"] == "completed":
                return _map_onboarding(current)
            if int(current["version"]) != update["expectedVersion"]:
                raise ConflictError("VERSION_CONFLICT", "Onboarding changed in another session")
            completed = [str(item) for item in _list(current["completed_steps"])]
            payload = _mapping(current["payload"])
            if completed != list(ONBOARDING_STEPS) or any(
                not is_skippable_onboarding_step(str(step))
                for step in payload.get("skippedSteps", [])
            ):
                raise ConflictError(
                    "ONBOARDING_INCOMPLETE",
                    "Every required onboarding step must be completed in order",
                )
            result = await connection.execute(
                text(
                    """
                    UPDATE student_onboarding SET status='completed', completed_at=NOW(),
                      version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                    RETURNING student_id, status, current_step, completed_steps, payload,
                              version, completed_at, updated_at
                    """
                ),
                {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
            )
            row = result.mappings().first()
            if row is None:
                raise ApiError(
                    500,
                    "ONBOARDING_COMPLETION_FAILED",
                    "Onboarding could not be completed",
                )
            mapped = _map_onboarding(dict(row))
            await self._award_rewards(
                connection,
                auth,
                "onboarding_completed",
                "onboarding",
                auth.student_id,
                {},
            )
            await self._insert_audit(
                connection,
                auth,
                "student_onboarding.completed",
                "student_onboarding",
                auth.student_id,
                request_id,
                {"version": mapped["version"]},
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.onboarding_completed.v1",
                "student_onboarding",
                auth.student_id,
                int(mapped["version"]),
                request_id,
                {"studentId": auth.student_id},
            )
            return mapped

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_onboarding.complete",
            update,
            200,
            handler,
        )

    async def claim_student_document_processing(
        self,
        auth: AuthContext,
        document_id: str,
        *,
        retry: bool = False,
        request_id: str | None = None,
        retry_idempotency_key: str | None = None,
    ) -> bool:
        processing: JsonDict = {
            "status": "processing",
            "documentType": "other",
            "summary": (
                "The original file is safely stored. Edward is preparing a reviewable record."
            ),
            "studentName": None,
            "institutionName": None,
            "issueDate": None,
            "academicTerm": None,
            "fields": [],
            "courses": [],
            "warnings": [],
            "model": None,
            "provider": "local",
            "processedAt": None,
            "verifiedAt": None,
        }
        if retry:
            if not retry_idempotency_key:
                raise BadRequestError(
                    "IDEMPOTENCY_KEY_REQUIRED", "The Idempotency-Key header is required"
                )
            operation = f"student_document.extraction.retry:{document_id}"
            request_hash = self._request_hash(auth, {"documentId": document_id})
            lock_key = f"{auth.tenant_id}:{auth.actor_id}:{operation}:{retry_idempotency_key}"
            async with self.engine.begin() as connection:
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": lock_key},
                )
                existing = await connection.execute(
                    text(
                        """
                        SELECT request_hash, response_body FROM idempotency_record
                        WHERE tenant_id=:tenant_id AND actor_id=:actor_id
                          AND operation=:operation AND idempotency_key=:idempotency_key
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "actor_id": auth.actor_id,
                        "operation": operation,
                        "idempotency_key": retry_idempotency_key,
                    },
                )
                replay = existing.mappings().first()
                if replay is not None:
                    if replay["request_hash"] != request_hash:
                        raise ConflictError(
                            "IDEMPOTENCY_KEY_REUSED",
                            "This idempotency key was already used for a different request",
                        )
                    return False
                result = await connection.execute(
                    text(
                        """
                        UPDATE document_record SET status='processing',
                          extraction=CAST(:processing AS jsonb), updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND id=:document_id AND status='uploaded'
                          AND extraction IS NOT NULL AND (
                            extraction->>'status'='pending_configuration' OR (
                              extraction->>'status'='failed'
                              AND COALESCE(extraction->'retryable','true'::jsonb)<>'false'::jsonb
                            )
                          )
                        RETURNING id
                        """
                    ),
                    {
                        "processing": _json(processing),
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "document_id": document_id,
                    },
                )
                if result.mappings().first() is None:
                    return False
                await connection.execute(
                    text(
                        """
                        INSERT INTO idempotency_record (
                          tenant_id, actor_id, operation, idempotency_key, request_hash,
                          response_status, response_body, created_at, expires_at
                        ) VALUES (
                          :tenant_id, :actor_id, :operation, :idempotency_key,
                          :request_hash, 202, CAST(:response_body AS jsonb), NOW(),
                          NOW()+INTERVAL '24 hours'
                        )
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "actor_id": auth.actor_id,
                        "operation": operation,
                        "idempotency_key": retry_idempotency_key,
                        "request_hash": request_hash,
                        "response_body": _json({"documentId": document_id, "status": "processing"}),
                    },
                )
                correlation = request_id or "document-extraction-retry"
                await self._insert_audit(
                    connection,
                    auth,
                    "document.extraction_retry_started",
                    "document_record",
                    document_id,
                    correlation,
                    {},
                )
                await self._insert_outbox(
                    connection,
                    auth,
                    "document.extraction_retry_started.v1",
                    "document_record",
                    document_id,
                    2,
                    correlation,
                    {"studentId": auth.student_id},
                )
                await self._insert_outbox(
                    connection,
                    auth,
                    "document.extraction_requested.v1",
                    "document_record",
                    document_id,
                    3,
                    correlation,
                    {"studentId": auth.student_id, "retry": True},
                )
                return True

        async with self.engine.begin() as connection:
            correlation = request_id or "document-extraction-request"
            result = await connection.execute(
                text(
                    """
                    UPDATE document_record SET status='under_review', updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id AND status='uploaded' AND extraction IS NULL
                      AND processing_mode='manual_review'
                    RETURNING requirement_id, category
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            manual = result.mappings().first()
            if manual is not None:
                if manual["requirement_id"]:
                    await connection.execute(
                        text(
                            """
                            UPDATE student_requirement SET status='under_review',
                              progress_percent=80, updated_at=NOW()
                            WHERE tenant_id=:tenant_id AND id=:requirement_id
                              AND status<>'completed'
                            """
                        ),
                        {
                            "tenant_id": auth.tenant_id,
                            "requirement_id": manual["requirement_id"],
                        },
                    )
                if manual["category"] == "financial_aid":
                    await connection.execute(
                        text(
                            """
                            UPDATE financial_document_requirement
                            SET status='under_review', document_id=:document_id,
                              version=version+1, updated_at=NOW()
                            WHERE tenant_id=:tenant_id AND student_id=:student_id
                              AND code='verification_worksheet'
                            """
                        ),
                        {
                            "document_id": document_id,
                            "tenant_id": auth.tenant_id,
                            "student_id": auth.student_id,
                        },
                    )
                await self._insert_audit(
                    connection,
                    auth,
                    "document.stored_for_review",
                    "document_record",
                    document_id,
                    correlation,
                    {
                        "storageConfirmed": True,
                        "processingMode": "manual_review",
                        "category": manual["category"],
                    },
                )
                await self._insert_outbox(
                    connection,
                    auth,
                    "document.stored_for_review.v1",
                    "document_record",
                    document_id,
                    2,
                    correlation,
                    {
                        "studentId": auth.student_id,
                        "category": manual["category"],
                        "requirementId": None
                        if manual["requirement_id"] is None
                        else str(manual["requirement_id"]),
                    },
                )
                return False
            result = await connection.execute(
                text(
                    """
                    UPDATE document_record SET status='processing',
                      extraction=CAST(:processing AS jsonb), updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id AND status='uploaded' AND extraction IS NULL
                      AND processing_mode IN ('agentic','classification_only')
                    RETURNING id
                    """
                ),
                {
                    "processing": _json(processing),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            if result.mappings().first() is None:
                return False
            await self._insert_audit(
                connection,
                auth,
                "document.extraction_queued",
                "document_record",
                document_id,
                correlation,
                {"storageConfirmed": True},
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.extraction_requested.v1",
                "document_record",
                document_id,
                2,
                correlation,
                {"studentId": auth.student_id, "retry": False},
            )
            return True

    async def release_student_document_processing(
        self, auth: AuthContext, document_id: str, request_id: str
    ) -> None:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE document_record SET status='uploaded', extraction=NULL,
                      updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id AND status='processing'
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            if result.mappings().first() is None:
                raise ConflictError(
                    "DOCUMENT_PROCESSING_STATE_CHANGED",
                    "The document processing state changed before the upload could be retried",
                )
            await self._insert_audit(
                connection,
                auth,
                "document.storage_failed",
                "document_record",
                document_id,
                request_id,
                {"retryable": True},
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.storage_failed.v1",
                "document_record",
                document_id,
                2,
                request_id,
                {"studentId": auth.student_id, "retryable": True},
            )

    async def complete_student_document_extraction(
        self,
        auth: AuthContext,
        document_id: str,
        extraction: Mapping[str, Any],
        request_id: str,
        retry_idempotency_key: str | None = None,
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            extraction_data = dict(extraction)
            completed = extraction_data.get("status") == "completed"
            status = "needs_review" if completed else "uploaded"
            inferred = self._category_for_document_type(str(extraction_data.get("documentType")))
            result = await connection.execute(
                text(
                    f"""
                    UPDATE document_record SET status=:status,
                      category=CASE WHEN category='other' THEN :inferred ELSE category END,
                      extraction=CAST(:extraction AS jsonb), updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id AND status='processing'
                    RETURNING {DOCUMENT_SELECT}
                    """
                ),
                {
                    "status": status,
                    "inferred": inferred,
                    "extraction": _json(extraction_data),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise ConflictError(
                    "DOCUMENT_PROCESSING_STATE_CHANGED",
                    "The document processing state changed before completion",
                )
            updated = dict(row)
            required_code = {
                "identity": "identity_document",
                "transcript": "official_transcript",
                "financial_aid": "financial_aid_verification",
                "health": "immunization_record",
            }.get(str(updated["category"]))
            classification_matches = (
                not updated.get("requirement_id")
                or self._category_for_document_type(str(extraction_data.get("documentType")))
                == updated["category"]
            )
            if required_code and completed and classification_matches:
                if updated.get("requirement_id"):
                    await connection.execute(
                        text(
                            """
                            UPDATE student_requirement sr SET status='under_review',
                              progress_percent=80, version=sr.version+1, updated_at=NOW()
                            FROM enrollment_journey j WHERE sr.tenant_id=:tenant_id
                              AND sr.journey_id=j.id AND j.student_id=:student_id
                              AND sr.id=:requirement_id
                            """
                        ),
                        {
                            "tenant_id": auth.tenant_id,
                            "student_id": auth.student_id,
                            "requirement_id": updated["requirement_id"],
                        },
                    )
                else:
                    await connection.execute(
                        text(
                            """
                            UPDATE student_requirement sr SET status='under_review',
                              progress_percent=80, version=sr.version+1, updated_at=NOW()
                            FROM requirement_definition_version rdv, enrollment_journey j
                            WHERE sr.tenant_id=:tenant_id
                              AND sr.requirement_definition_version_id=rdv.id
                              AND sr.journey_id=j.id AND j.student_id=:student_id
                              AND rdv.code=:requirement_code
                            """
                        ),
                        {
                            "tenant_id": auth.tenant_id,
                            "student_id": auth.student_id,
                            "requirement_code": required_code,
                        },
                    )
            automatic_transcript = (
                updated["category"] == "transcript"
                and completed
                and classification_matches
                and extraction_data.get("documentType") == "transcript"
            )
            if automatic_transcript:
                await connection.execute(
                    text(
                        """
                        UPDATE document_record SET status='under_review', updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND id=:document_id
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "document_id": document_id,
                    },
                )
                updated["status"] = "under_review"
                await self._persist_transcript_credits(
                    connection, auth, document_id, extraction_data
                )
                await self._project_course_exemptions(
                    connection, auth, document_id, extraction_data
                )
                await self._insert_outbox(
                    connection,
                    auth,
                    "student.transcript_credits_imported.v1",
                    "document_record",
                    document_id,
                    3,
                    request_id,
                    {
                        "studentId": auth.student_id,
                        "courseCount": len(_list(extraction_data.get("courses"))),
                        "projection": "automatic",
                    },
                )
            if extraction_data.get("immunizationCompliance"):
                await self._persist_immunization_evaluation(
                    connection, auth, document_id, extraction_data
                )
            if updated["category"] == "financial_aid" and completed and classification_matches:
                await connection.execute(
                    text(
                        """
                        UPDATE financial_document_requirement SET status='under_review',
                          document_id=:document_id, version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND code='verification_worksheet'
                        """
                    ),
                    {
                        "document_id": document_id,
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
            await self._insert_audit(
                connection,
                auth,
                "document.extraction_completed",
                "document_record",
                document_id,
                request_id,
                {
                    "status": extraction_data.get("status"),
                    "provider": extraction_data.get("provider"),
                    "model": extraction_data.get("model"),
                    "extractedFieldCount": len(_list(extraction_data.get("fields"))),
                    "failureCode": extraction_data.get("failureCode"),
                    "retryable": extraction_data.get("retryable"),
                    "automaticallyProjectedTranscript": automatic_transcript,
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.extraction_completed.v1",
                "document_record",
                document_id,
                2,
                request_id,
                {
                    "studentId": auth.student_id,
                    "category": updated["category"],
                    "extractionStatus": extraction_data.get("status"),
                    "failureCode": extraction_data.get("failureCode"),
                    "retryable": extraction_data.get("retryable"),
                },
            )
            document = _map_document(updated)
            if retry_idempotency_key:
                await connection.execute(
                    text(
                        """
                        UPDATE idempotency_record SET response_status=200,
                          response_body=CAST(:response_body AS jsonb)
                        WHERE tenant_id=:tenant_id AND actor_id=:actor_id
                          AND operation=:operation AND idempotency_key=:idempotency_key
                        """
                    ),
                    {
                        "response_body": _json(document),
                        "tenant_id": auth.tenant_id,
                        "actor_id": auth.actor_id,
                        "operation": f"student_document.extraction.retry:{document_id}",
                        "idempotency_key": retry_idempotency_key,
                    },
                )
            return document

    async def get_student_document(self, auth: AuthContext, document_id: str) -> JsonDict:
        row = await self._one(
            f"""
            SELECT {DOCUMENT_SELECT} FROM document_record
            WHERE tenant_id=:tenant_id AND student_id=:student_id AND id=:document_id
            LIMIT 1
            """,
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "document_id": document_id,
            },
        )
        if row is None:
            raise NotFoundError("STUDENT_DOCUMENT_NOT_FOUND", "The document was not found")
        return _map_document(row)

    async def get_student_document_content_reference(
        self, auth: AuthContext, document_id: str
    ) -> JsonDict:
        row = await self._one(
            """
            SELECT storage_key, file_name, mime_type FROM (
              SELECT storage_key, file_name, mime_type, 1 AS priority
              FROM document_record WHERE tenant_id=:tenant_id
                AND student_id=:student_id AND id=:document_id
                AND storage_key IS NOT NULL
              UNION ALL
              SELECT storage_key, file_name, mime_type, 2 AS priority
              FROM student_signed_document WHERE tenant_id=:tenant_id
                AND student_id=:student_id AND id=:document_id
            ) reference ORDER BY priority LIMIT 1
            """,
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "document_id": document_id,
            },
        )
        if row is None:
            raise NotFoundError(
                "STUDENT_DOCUMENT_CONTENT_NOT_FOUND",
                "The uploaded document content was not found",
            )
        return {
            "storageKey": str(row["storage_key"]),
            "fileName": row["file_name"],
            "mimeType": row["mime_type"],
        }

    async def confirm_student_document_extraction(
        self,
        auth: AuthContext,
        document_id: str,
        confirmation: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            result = await connection.execute(
                text(
                    f"""
                    SELECT {DOCUMENT_SELECT} FROM document_record
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id FOR UPDATE
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_DOCUMENT_NOT_FOUND", "The document was not found")
            current = dict(row)
            extraction = _mapping(current.get("extraction"))
            if extraction.get("status") != "completed":
                raise ConflictError(
                    "DOCUMENT_EXTRACTION_NOT_READY",
                    "Document extraction is not ready for review",
                )
            available = {
                str(field.get("key"))
                for field in _list(extraction.get("fields"))
                if isinstance(field, dict)
            }
            accepted = list(dict.fromkeys(confirmation.get("acceptedFieldKeys", [])))
            if any(key not in available for key in accepted):
                raise BadRequestError(
                    "UNKNOWN_EXTRACTED_FIELD",
                    "One or more extracted fields do not belong to this document",
                )
            extraction.update({"acceptedFieldKeys": accepted, "verifiedAt": _iso(_utc_now())})
            result = await connection.execute(
                text(
                    f"""
                    UPDATE document_record SET status='under_review',
                      extraction=CAST(:extraction AS jsonb), updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id RETURNING {DOCUMENT_SELECT}
                    """
                ),
                {
                    "extraction": _json(extraction),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            updated_row = result.mappings().first()
            if updated_row is None:
                raise ConflictError(
                    "DOCUMENT_PROCESSING_STATE_CHANGED",
                    "The document review state changed before confirmation",
                )
            projection = self._safe_profile_projection(_list(extraction.get("fields")), accepted)
            if projection:
                profile_result = await connection.execute(
                    text(
                        """
                        UPDATE student_profile SET
                          preferred_name=CASE WHEN :has_preferred
                            THEN :preferred ELSE preferred_name END,
                          pronouns=CASE WHEN :has_pronouns THEN :pronouns ELSE pronouns END,
                          mobile_phone=CASE WHEN :has_mobile THEN :mobile ELSE mobile_phone END,
                          communication_preference=CASE WHEN :has_preference
                            THEN :preference ELSE communication_preference END,
                          version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                        RETURNING student_id, preferred_name, pronouns, mobile_phone,
                          communication_preference, version, updated_at
                        """
                    ),
                    {
                        "has_preferred": "preferredName" in projection,
                        "preferred": projection.get("preferredName", ""),
                        "has_pronouns": "pronouns" in projection,
                        "pronouns": projection.get("pronouns"),
                        "has_mobile": "mobilePhone" in projection,
                        "mobile": projection.get("mobilePhone"),
                        "has_preference": "communicationPreference" in projection,
                        "preference": projection.get("communicationPreference", "email"),
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
                profile = profile_result.mappings().first()
                if profile is not None:
                    if "preferredName" in projection:
                        await connection.execute(
                            text(
                                """
                                UPDATE person p SET preferred_name=:preferred_name,
                                  updated_at=NOW() FROM student s
                                WHERE s.person_id=p.id AND s.tenant_id=p.tenant_id
                                  AND s.tenant_id=:tenant_id AND s.id=:student_id
                                """
                            ),
                            {
                                "preferred_name": profile["preferred_name"],
                                "tenant_id": auth.tenant_id,
                                "student_id": auth.student_id,
                            },
                        )
                    await self._insert_outbox(
                        connection,
                        auth,
                        "student.profile_updated.v1",
                        "student_profile",
                        auth.student_id,
                        int(profile["version"]),
                        request_id,
                        {
                            "studentId": auth.student_id,
                            "changedFields": list(projection),
                            "source": "confirmed_document_extraction",
                        },
                    )
            if extraction.get("documentType") == "transcript" and _list(extraction.get("courses")):
                await self._persist_transcript_credits(connection, auth, document_id, extraction)
                await self._project_course_exemptions(connection, auth, document_id, extraction)
                await self._insert_outbox(
                    connection,
                    auth,
                    "student.transcript_credits_imported.v1",
                    "document_record",
                    document_id,
                    3,
                    request_id,
                    {
                        "studentId": auth.student_id,
                        "courseCount": len(_list(extraction.get("courses"))),
                    },
                )
            await self._insert_audit(
                connection,
                auth,
                "document.extraction_confirmed",
                "document_record",
                document_id,
                request_id,
                {"acceptedFieldKeys": accepted, "projectedProfileFields": list(projection)},
            )
            return _map_document(dict(updated_row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            f"student_document.extraction.confirm:{document_id}",
            confirmation,
            200,
            handler,
        )

    async def get_student_requirements(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT sr.id, sr.journey_id, rdv.code, rdv.title, rdv.description,
                   sr.status, rdv.blocking, sr.due_at, sr.progress_percent,
                   rdv.submission_type, rdv.responsible_office, rdv.depends_on_codes,
                   reward.reward_points, reward.reward_earned
            FROM student_requirement sr
            JOIN enrollment_journey j
              ON j.id=sr.journey_id AND j.tenant_id=sr.tenant_id
            JOIN requirement_definition_version rdv
              ON rdv.id=sr.requirement_definition_version_id
             AND rdv.tenant_id=sr.tenant_id
            LEFT JOIN LATERAL (
              SELECT COALESCE(SUM(rr.points),0)::integer AS reward_points,
                     CASE WHEN COUNT(rr.id)=0 THEN false ELSE BOOL_AND(EXISTS (
                       SELECT 1 FROM student_reward_ledger ledger
                       WHERE ledger.tenant_id=sr.tenant_id
                         AND ledger.student_id=:student_id
                         AND ledger.reward_rule_id=rr.id
                         AND ledger.source_key=sr.id::text
                     )) END AS reward_earned
              FROM tenant_reward_rule rr
              WHERE rr.tenant_id=sr.tenant_id
                AND rr.trigger_type='requirement_completed'
                AND rr.trigger_key=rdv.code AND rr.enabled=true
                AND (rr.starts_at IS NULL OR rr.starts_at<=NOW())
                AND (rr.ends_at IS NULL OR rr.ends_at>NOW())
            ) reward ON true
            WHERE sr.tenant_id=:tenant_id AND j.student_id=:student_id
            ORDER BY rdv.display_order, sr.created_at
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [_map_requirement(row) for row in rows]
        return {"items": items, "total": len(items)}

    async def get_student_requirement(self, auth: AuthContext, identifier: str) -> JsonDict:
        row = await self._one(
            """
            SELECT sr.id, sr.journey_id, rdv.code, rdv.title, rdv.description,
                   sr.status, rdv.blocking, sr.due_at, sr.progress_percent,
                   rdv.submission_type, rdv.responsible_office, rdv.depends_on_codes,
                   reward.reward_points, reward.reward_earned
            FROM student_requirement sr
            JOIN enrollment_journey j
              ON j.id=sr.journey_id AND j.tenant_id=sr.tenant_id
            JOIN requirement_definition_version rdv
              ON rdv.id=sr.requirement_definition_version_id
             AND rdv.tenant_id=sr.tenant_id
            LEFT JOIN LATERAL (
              SELECT COALESCE(SUM(rr.points),0)::integer AS reward_points,
                     CASE WHEN COUNT(rr.id)=0 THEN false ELSE BOOL_AND(EXISTS (
                       SELECT 1 FROM student_reward_ledger ledger
                       WHERE ledger.tenant_id=sr.tenant_id
                         AND ledger.student_id=:student_id
                         AND ledger.reward_rule_id=rr.id
                         AND ledger.source_key=sr.id::text
                     )) END AS reward_earned
              FROM tenant_reward_rule rr
              WHERE rr.tenant_id=sr.tenant_id
                AND rr.trigger_type='requirement_completed'
                AND rr.trigger_key=rdv.code AND rr.enabled=true
                AND (rr.starts_at IS NULL OR rr.starts_at<=NOW())
                AND (rr.ends_at IS NULL OR rr.ends_at>NOW())
            ) reward ON true
            WHERE sr.tenant_id=:tenant_id AND j.student_id=:student_id
              AND (sr.id::text=:identifier OR rdv.code=:requirement_code)
            LIMIT 1
            """,
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "identifier": identifier,
                "requirement_code": _requirement_code(identifier),
            },
        )
        if row is None:
            raise NotFoundError("STUDENT_REQUIREMENT_NOT_FOUND", "The requirement was not found")
        mapped = _map_requirement(row)
        if row["code"] != "immunization_record":
            return mapped
        policy = await self.get_immunization_policy_context(auth)
        if policy:
            version = policy["policyVersion"]
            mapped["immunizationPolicy"] = {
                "id": version["id"],
                "code": version["code"],
                "version": version["version"],
                "name": version["name"],
                "effectiveFrom": version["effectiveFrom"],
                "effectiveUntil": version["effectiveUntil"],
                "requirements": [
                    {
                        key: rule[key]
                        for key in (
                            "id",
                            "code",
                            "name",
                            "description",
                            "required",
                            "doseCount",
                            "validityDays",
                        )
                    }
                    for rule in policy["requirements"]
                ],
            }
        return mapped

    async def get_student_messages(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT id, subject, body, sender_name, sent_at, read_at
            FROM student_message
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY sent_at DESC, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [_map_message(row) for row in rows]
        return {"items": items, "unreadCount": sum(x["readAt"] is None for x in items)}

    async def mark_student_message_read(
        self, auth: AuthContext, message_id: str, request_id: str
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, subject, body, sender_name, sent_at, read_at
                    FROM student_message
                    WHERE tenant_id=:tenant_id AND student_id=:student_id AND id=:message_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "message_id": message_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_MESSAGE_NOT_FOUND", "The message was not found")
            current = dict(row)
            if current["read_at"] is not None:
                return _map_message(current)
            result = await connection.execute(
                text(
                    """
                    UPDATE student_message SET read_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id AND id=:message_id
                    RETURNING id, subject, body, sender_name, sent_at, read_at
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "message_id": message_id,
                },
            )
            updated = result.mappings().first()
            if updated is None:
                raise ApiError(
                    500, "MESSAGE_UPDATE_FAILED", "The message could not be marked as read"
                )
            await self._insert_audit(
                connection,
                auth,
                "student_message.read",
                "student_message",
                message_id,
                request_id,
                {},
            )
            return _map_message(dict(updated))

    async def get_student_documents(self, auth: AuthContext) -> JsonDict:
        uploads = await self._all(
            f"""
            SELECT {DOCUMENT_SELECT} FROM document_record
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY created_at DESC, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        signed = await self._all(
            """
            SELECT id, template_code, onboarding_version, title, file_name, mime_type,
                   size_bytes, storage_key, sha256, signer_name, signature_method,
                   signed_at, created_at
            FROM student_signed_document
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY signed_at DESC, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [*map(_map_document, uploads), *map(_map_signed_document, signed)]
        items.sort(key=lambda item: (item["createdAt"], item["id"]), reverse=True)
        return {"items": items, "total": len(items)}

    async def save_student_signed_document(
        self, auth: AuthContext, document: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO student_signed_document (
                      id, tenant_id, student_id, template_code, onboarding_version,
                      title, file_name, mime_type, size_bytes, storage_provider,
                      storage_key, sha256, signer_name, signature_method, signed_at
                    ) VALUES (
                      :id, :tenant_id, :student_id, :template_code, :onboarding_version,
                      :title, :file_name, 'application/pdf', :size_bytes, 's3',
                      :storage_key, :sha256, :signer_name, :signature_method, :signed_at
                    ) ON CONFLICT (tenant_id, student_id, template_code, onboarding_version)
                    DO NOTHING
                    RETURNING id, template_code, onboarding_version, title, file_name,
                      mime_type, size_bytes, storage_key, sha256, signer_name,
                      signature_method, signed_at, created_at
                    """
                ),
                {
                    "id": document["id"],
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "template_code": document["templateCode"],
                    "onboarding_version": document["onboardingVersion"],
                    "title": document["title"],
                    "file_name": document["fileName"],
                    "size_bytes": document["sizeBytes"],
                    "storage_key": document["storageKey"],
                    "sha256": document["sha256"],
                    "signer_name": document["signerName"],
                    "signature_method": document["signatureMethod"],
                    "signed_at": _timestamp(document["signedAt"]),
                },
            )
            row = result.mappings().first()
            if row is None:
                existing = await connection.execute(
                    text(
                        """
                        SELECT id, template_code, onboarding_version, title, file_name,
                          mime_type, size_bytes, storage_key, sha256, signer_name,
                          signature_method, signed_at, created_at
                        FROM student_signed_document
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND template_code=:template_code
                          AND onboarding_version=:onboarding_version
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "template_code": document["templateCode"],
                        "onboarding_version": document["onboardingVersion"],
                    },
                )
                row = existing.mappings().first()
            else:
                await self._insert_audit(
                    connection,
                    auth,
                    "student_signed_document.created",
                    "student_signed_document",
                    str(row["id"]),
                    request_id,
                    {
                        "templateCode": row["template_code"],
                        "onboardingVersion": int(row["onboarding_version"]),
                    },
                )
            if row is None:
                raise ApiError(
                    500,
                    "SIGNED_DOCUMENT_WRITE_FAILED",
                    "The signed document could not be saved",
                )
            return _map_signed_document(dict(row))

    async def get_course_exemption_context(
        self, auth: AuthContext, _courses: Sequence[Mapping[str, Any]]
    ) -> JsonDict | None:
        base = await self._one(
            """
            SELECT p.id AS program_id, p.code AS program_code, p.name AS program_name,
                   ccv.id AS catalog_version_id, ccv.code AS catalog_code,
                   ccv.effective_from, ccv.updated_at
            FROM admission_offer ao
            JOIN program p ON p.id=ao.program_id AND p.tenant_id=ao.tenant_id
            JOIN course_catalog_version ccv
              ON ccv.tenant_id=ao.tenant_id AND ccv.status='active'
            WHERE ao.tenant_id=:tenant_id AND ao.student_id=:student_id
            ORDER BY ccv.effective_from DESC, ccv.updated_at DESC LIMIT 1
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if base is None:
            return None
        common = {
            "tenant_id": auth.tenant_id,
            "catalog_version_id": base["catalog_version_id"],
        }
        courses = await self._all(
            """
            SELECT id, code, title, credits FROM catalog_course
            WHERE tenant_id=:tenant_id AND catalog_version_id=:catalog_version_id
              AND active=true ORDER BY code
            """,
            common,
        )
        requirements = await self._all(
            """
            SELECT id, course_id, category, required, recommended_term
            FROM program_requirement WHERE tenant_id=:tenant_id
              AND program_id=:program_id AND catalog_version_id=:catalog_version_id
            ORDER BY recommended_term, id
            """,
            {**common, "program_id": base["program_id"]},
        )
        prerequisites = await self._all(
            """
            SELECT course_id, prerequisite_course_id, minimum_grade
            FROM course_prerequisite WHERE tenant_id=:tenant_id
              AND catalog_version_id=:catalog_version_id
            ORDER BY course_id, prerequisite_course_id
            """,
            common,
        )
        rules = await self._all(
            """
            SELECT id, code, version, source_type, source_code, minimum_score,
                   minimum_grade, minimum_credits, target_course_id, confidence
            FROM course_equivalency_rule WHERE tenant_id=:tenant_id
              AND catalog_version_id=:catalog_version_id AND active=true
            ORDER BY code, version DESC
            """,
            common,
        )
        highest = max((int(rule["version"]) for rule in rules), default=0)
        return {
            "program": {
                "id": str(base["program_id"]),
                "code": base["program_code"],
                "name": base["program_name"],
            },
            "catalogVersion": {
                "id": str(base["catalog_version_id"]),
                "code": base["catalog_code"],
                "effectiveFrom": str(base["effective_from"]),
                "updatedAt": _iso(base["updated_at"]),
            },
            "policyVersion": f"{base['catalog_code']}:rules-v{highest}",
            "catalogCourses": [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "title": row["title"],
                    "credits": float(row["credits"]),
                }
                for row in courses
            ],
            "programRequirements": [
                {
                    "id": str(row["id"]),
                    "courseId": str(row["course_id"]),
                    "category": row["category"],
                    "required": bool(row["required"]),
                    "recommendedTerm": int(row["recommended_term"]),
                }
                for row in requirements
            ],
            "prerequisites": [
                {
                    "courseId": str(row["course_id"]),
                    "prerequisiteCourseId": str(row["prerequisite_course_id"]),
                    "minimumGrade": row["minimum_grade"],
                }
                for row in prerequisites
            ],
            "equivalencyRules": [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "version": int(row["version"]),
                    "sourceType": row["source_type"],
                    "sourceCode": row["source_code"],
                    "minimumScore": None
                    if row["minimum_score"] is None
                    else float(row["minimum_score"]),
                    "minimumGrade": row["minimum_grade"],
                    "minimumCredits": None
                    if row["minimum_credits"] is None
                    else float(row["minimum_credits"]),
                    "targetCourseId": str(row["target_course_id"]),
                    "confidence": float(row["confidence"]),
                }
                for row in rules
            ],
        }

    async def get_immunization_policy_context(self, auth: AuthContext) -> JsonDict | None:
        policy = await self._one(
            """
            SELECT id, code, version, name, effective_from, effective_until, updated_at
            FROM immunization_policy_version
            WHERE tenant_id=:tenant_id AND status='published'
              AND effective_from<=CURRENT_DATE
              AND (effective_until IS NULL OR effective_until>=CURRENT_DATE)
            ORDER BY version DESC LIMIT 1
            """,
            {"tenant_id": auth.tenant_id},
        )
        if policy is None:
            return None
        rules = await self._all(
            """
            SELECT id, code, name, description, required, dose_count, validity_days,
                   applies_when, evidence_criteria
            FROM immunization_requirement_rule
            WHERE tenant_id=:tenant_id AND policy_version_id=:policy_id AND active=true
            ORDER BY display_order, code
            """,
            {"tenant_id": auth.tenant_id, "policy_id": policy["id"]},
        )
        return {
            "policyVersion": {
                "id": str(policy["id"]),
                "code": policy["code"],
                "version": int(policy["version"]),
                "name": policy["name"],
                "effectiveFrom": str(policy["effective_from"]),
                "effectiveUntil": None
                if policy["effective_until"] is None
                else str(policy["effective_until"]),
                "updatedAt": _iso(policy["updated_at"]),
            },
            "requirements": [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "description": row["description"],
                    "required": bool(row["required"]),
                    "doseCount": row["dose_count"],
                    "validityDays": row["validity_days"],
                    "appliesWhen": _mapping(row["applies_when"]),
                    "evidenceCriteria": _mapping(row["evidence_criteria"]),
                }
                for row in rules
            ],
        }

    async def create_student_document(
        self,
        auth: AuthContext,
        document: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            document_id = str(uuid4())
            result = await connection.execute(
                text(
                    f"""
                    INSERT INTO document_record (
                      id, tenant_id, student_id, file_name, mime_type, size_bytes,
                      category, processing_mode, status, storage_provider
                    ) SELECT :id, s.tenant_id, s.id, :file_name, :mime_type,
                      :size_bytes, :category, :processing_mode, 'placeholder',
                      'local_placeholder'
                    FROM student s WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                    RETURNING {DOCUMENT_SELECT}
                    """
                ),
                {
                    "id": document_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "file_name": document["fileName"],
                    "mime_type": document["mimeType"],
                    "size_bytes": document["sizeBytes"],
                    "category": document["category"],
                    "processing_mode": PROCESSING_MODES.get(str(document["category"]), "agentic"),
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
            await self._insert_audit(
                connection,
                auth,
                "document.placeholder_created",
                "document_record",
                document_id,
                request_id,
                {
                    "category": document["category"],
                    "mimeType": document["mimeType"],
                    "sizeBytes": document["sizeBytes"],
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.placeholder_created.v1",
                "document_record",
                document_id,
                1,
                request_id,
                {"studentId": auth.student_id, "category": document["category"]},
            )
            return _map_document(dict(row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_document.create",
            document,
            201,
            handler,
        )

    async def reserve_student_document_upload(
        self,
        auth: AuthContext,
        document: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
        requirement_id: str | None = None,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            category = str(document["category"])
            if requirement_id:
                result = await connection.execute(
                    text(
                        """
                        SELECT rdv.code, sr.status FROM student_requirement sr
                        JOIN enrollment_journey j
                          ON j.id=sr.journey_id AND j.tenant_id=sr.tenant_id
                        JOIN requirement_definition_version rdv
                          ON rdv.id=sr.requirement_definition_version_id
                         AND rdv.tenant_id=sr.tenant_id
                        WHERE sr.tenant_id=:tenant_id AND j.student_id=:student_id
                          AND sr.id=:requirement_id AND rdv.submission_type='document'
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "requirement_id": requirement_id,
                    },
                )
                requirement = result.mappings().first()
                if requirement is None:
                    raise NotFoundError(
                        "DOCUMENT_REQUIREMENT_NOT_FOUND",
                        "The document requirement was not found",
                    )
                if requirement["status"] == "blocked":
                    raise ConflictError(
                        "DOCUMENT_REQUIREMENT_BLOCKED",
                        "Complete the prerequisite enrollment tasks before uploading "
                        "this document.",
                    )
                category = DOCUMENT_CATEGORIES.get(str(requirement["code"]), category)
            document_id = str(uuid4())
            extension = {
                "application/pdf": ".pdf",
                "image/jpeg": ".jpg",
                "image/png": ".png",
            }[str(document["mimeType"])]
            storage_key = f"{auth.tenant_id}/{auth.student_id}/{document_id}{extension}"
            result = await connection.execute(
                text(
                    f"""
                    INSERT INTO document_record (
                      id, tenant_id, student_id, requirement_id, file_name, mime_type,
                      size_bytes, category, processing_mode, status, storage_provider,
                      storage_key, sha256
                    ) SELECT :id, s.tenant_id, s.id, :requirement_id, :file_name,
                      :mime_type, :size_bytes, :category, :processing_mode, 'uploaded',
                      's3', :storage_key, :sha256
                    FROM student s WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                    RETURNING {DOCUMENT_SELECT}
                    """
                ),
                {
                    "id": document_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "requirement_id": requirement_id,
                    "file_name": document["fileName"],
                    "mime_type": document["mimeType"],
                    "size_bytes": document["sizeBytes"],
                    "category": category,
                    "processing_mode": PROCESSING_MODES.get(category, "agentic"),
                    "storage_key": storage_key,
                    "sha256": document["sha256"],
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
            await self._insert_audit(
                connection,
                auth,
                "document.upload_reserved",
                "document_record",
                document_id,
                request_id,
                {
                    "category": category,
                    "requirementId": requirement_id,
                    "mimeType": document["mimeType"],
                    "sizeBytes": document["sizeBytes"],
                    "sha256": document["sha256"],
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.upload_reserved.v1",
                "document_record",
                document_id,
                1,
                request_id,
                {
                    "studentId": auth.student_id,
                    "category": category,
                    "requirementId": requirement_id,
                },
            )
            return _map_document(dict(row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_document.upload.reserve",
            {"document": dict(document), "requirementId": requirement_id},
            201,
            handler,
        )

    async def get_student_appointments(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT id, type, starts_at, notes, status, created_at
            FROM student_appointment
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY starts_at, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [_map_appointment(row) for row in rows]
        return {"items": items, "total": len(items)}

    async def create_student_appointment(
        self,
        auth: AuthContext,
        appointment: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        try:
            starts_at = datetime.fromisoformat(str(appointment["startsAt"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as error:
            raise BadRequestError("VALIDATION_ERROR", "Appointment time is invalid") from error
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=UTC)
        if starts_at <= _utc_now():
            raise BadRequestError(
                "APPOINTMENT_MUST_BE_FUTURE", "Appointment time must be in the future"
            )

        async def handler(connection: AsyncConnection) -> JsonDict:
            appointment_id = str(uuid4())
            result = await connection.execute(
                text(
                    """
                    INSERT INTO student_appointment (
                      id, tenant_id, student_id, type, starts_at, notes, status
                    ) SELECT :id, s.tenant_id, s.id, :type, :starts_at, :notes, 'scheduled'
                    FROM student s
                    WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                    RETURNING id, type, starts_at, notes, status, created_at
                    """
                ),
                {
                    "id": appointment_id,
                    "type": appointment["type"],
                    "starts_at": starts_at,
                    "notes": appointment.get("notes"),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
            await self._insert_audit(
                connection,
                auth,
                "student_appointment.scheduled",
                "student_appointment",
                appointment_id,
                request_id,
                {"type": appointment["type"]},
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.appointment_scheduled.v1",
                "student_appointment",
                appointment_id,
                1,
                request_id,
                {"studentId": auth.student_id, "startsAt": _iso(starts_at)},
            )
            return _map_appointment(dict(row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_appointment.create",
            appointment,
            201,
            handler,
        )

    async def get_student_payments(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT id, offer_id, amount_cents, status, processor_reference, created_at
            FROM payment_transaction
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY created_at DESC, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [_map_payment(row) for row in rows]
        return {"items": items, "total": len(items)}

    async def create_deposit_payment(
        self,
        auth: AuthContext,
        payment: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            offer_result = await connection.execute(
                text(
                    """
                    SELECT deposit_amount_cents FROM admission_offer
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:offer_id AND status='accepted'
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "offer_id": payment["offerId"],
                },
            )
            offer = offer_result.mappings().first()
            if offer is None:
                raise ConflictError(
                    "ACCEPTED_OFFER_REQUIRED",
                    "An accepted admission offer is required before paying a deposit",
                )
            existing_result = await connection.execute(
                text(
                    """
                    SELECT id, offer_id, amount_cents, status, processor_reference, created_at
                    FROM payment_transaction
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND offer_id=:offer_id AND type='enrollment_deposit'
                      AND status='succeeded'
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "offer_id": payment["offerId"],
                },
            )
            existing = existing_result.mappings().first()
            if existing is not None:
                return _map_payment(dict(existing))
            payment_id = str(uuid4())
            processor_reference = f"dummy_{payment_id.replace('-', '')}"
            result = await connection.execute(
                text(
                    """
                    INSERT INTO payment_transaction (
                      id, tenant_id, student_id, offer_id, type, amount_cents,
                      status, processor, processor_reference
                    ) VALUES (
                      :id, :tenant_id, :student_id, :offer_id, 'enrollment_deposit',
                      :amount_cents, 'succeeded', 'dummy', :processor_reference
                    )
                    RETURNING id, offer_id, amount_cents, status,
                              processor_reference, created_at
                    """
                ),
                {
                    "id": payment_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "offer_id": payment["offerId"],
                    "amount_cents": int(offer["deposit_amount_cents"]),
                    "processor_reference": processor_reference,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise ApiError(500, "PAYMENT_CREATE_FAILED", "The deposit could not be recorded")
            await self._complete_requirement(connection, auth, "enrollment_deposit")
            await self._insert_audit(
                connection,
                auth,
                "payment.deposit_succeeded",
                "payment_transaction",
                payment_id,
                request_id,
                {
                    "offerId": payment["offerId"],
                    "amountCents": int(offer["deposit_amount_cents"]),
                    "processor": "dummy",
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "payment.deposit_succeeded.v1",
                "payment_transaction",
                payment_id,
                1,
                request_id,
                {
                    "studentId": auth.student_id,
                    "offerId": payment["offerId"],
                    "amountCents": int(offer["deposit_amount_cents"]),
                },
            )
            return _map_payment(dict(row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_payment.deposit",
            payment,
            200,
            handler,
        )

    async def get_student_profile(self, auth: AuthContext) -> JsonDict:
        row = await self._one(
            """
            SELECT sp.student_id, sp.preferred_name, p.first_name, p.last_name,
                   ca.email_normalized AS email,
                   ca.email_verified_at IS NOT NULL AS email_verified,
                   ca.phone_verified_at IS NOT NULL AS phone_verified,
                   sp.pronouns, sp.mobile_phone, sp.communication_preference,
                   sp.version, sp.updated_at
            FROM student_profile sp
            JOIN student s ON s.id=sp.student_id AND s.tenant_id=sp.tenant_id
            JOIN person p ON p.id=s.person_id AND p.tenant_id=s.tenant_id
            LEFT JOIN credential_account ca
              ON ca.student_id=sp.student_id AND ca.tenant_id=sp.tenant_id
             AND ca.status='active'
            WHERE sp.tenant_id=:tenant_id AND sp.student_id=:student_id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if row is None:
            raise NotFoundError("STUDENT_PROFILE_NOT_FOUND", "The student profile was not found")
        return _map_profile(row)

    async def update_student_profile(
        self, auth: AuthContext, update: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        fields = ("preferredName", "pronouns", "mobilePhone", "communicationPreference")
        changed = [field for field in fields if field in update]
        if not changed:
            raise BadRequestError(
                "PROFILE_UPDATE_EMPTY", "At least one profile field must be supplied"
            )
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE student_profile SET
                      preferred_name=CASE WHEN :has_preferred
                        THEN :preferred ELSE preferred_name END,
                      pronouns=CASE WHEN :has_pronouns THEN :pronouns ELSE pronouns END,
                      mobile_phone=CASE WHEN :has_mobile THEN :mobile ELSE mobile_phone END,
                      communication_preference=CASE WHEN :has_preference
                        THEN :preference ELSE communication_preference END,
                      version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND version=:expected_version
                    RETURNING student_id, preferred_name, pronouns, mobile_phone,
                              communication_preference, version, updated_at
                    """
                ),
                {
                    "has_preferred": "preferredName" in update,
                    "preferred": update.get("preferredName", ""),
                    "has_pronouns": "pronouns" in update,
                    "pronouns": update.get("pronouns"),
                    "has_mobile": "mobilePhone" in update,
                    "mobile": update.get("mobilePhone"),
                    "has_preference": "communicationPreference" in update,
                    "preference": update.get("communicationPreference", "email"),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "expected_version": update["expectedVersion"],
                },
            )
            row = result.mappings().first()
            if row is None:
                current = await connection.execute(
                    text(
                        """SELECT version FROM student_profile
                           WHERE tenant_id=:tenant_id AND student_id=:student_id"""
                    ),
                    {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
                )
                if current.mappings().first() is None:
                    raise NotFoundError(
                        "STUDENT_PROFILE_NOT_FOUND", "The student profile was not found"
                    )
                raise ConflictError("VERSION_CONFLICT", "The profile changed in another session")
            updated = dict(row)
            if "preferredName" in update:
                await connection.execute(
                    text(
                        """
                        UPDATE person p SET preferred_name=:preferred_name, updated_at=NOW()
                        FROM student s WHERE s.person_id=p.id AND s.tenant_id=p.tenant_id
                          AND s.tenant_id=:tenant_id AND s.id=:student_id
                        """
                    ),
                    {
                        "preferred_name": updated["preferred_name"],
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
            await self._complete_requirement(connection, auth, "profile_verification")
            await self._insert_audit(
                connection,
                auth,
                "student_profile.updated",
                "student_profile",
                auth.student_id,
                request_id,
                {"changedFields": changed, "version": int(updated["version"])},
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.profile_updated.v1",
                "student_profile",
                auth.student_id,
                int(updated["version"]),
                request_id,
                {"studentId": auth.student_id, "changedFields": changed},
            )
            return _map_profile(updated)

    async def get_student_academics(self, auth: AuthContext) -> JsonDict:
        selected = await self._one(
            """
            SELECT p.id, p.code, p.name, p.degree, p.total_credits, p.description,
                   p.source_label, p.source_url, p.source_status,
                   ccv.id AS catalog_id, ccv.code AS catalog_code
            FROM admission_offer ao
            JOIN program p ON p.id=ao.program_id AND p.tenant_id=ao.tenant_id
            JOIN course_catalog_version ccv
              ON ccv.tenant_id=ao.tenant_id AND ccv.status='active'
            WHERE ao.tenant_id=:tenant_id AND ao.student_id=:student_id
            ORDER BY ao.created_at DESC, ccv.effective_from DESC LIMIT 1
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if selected is None:
            raise NotFoundError(
                "STUDENT_ACADEMICS_NOT_FOUND",
                "No academic plan is available for this student",
            )
        available_rows = await self._all(
            """
            SELECT id, code, name, degree, total_credits, description,
                   source_label, source_url, source_status
            FROM program WHERE tenant_id=:tenant_id ORDER BY name
            """,
            {"tenant_id": auth.tenant_id},
        )
        course_rows = await self._all(
            """
            SELECT cc.id, cc.code, cc.title, cc.description, cc.credits, cc.level,
                   cc.availability_label, cc.instructor_names, cc.meeting_pattern,
                   cc.resources, COALESCE(cc.source_url, ccv.source_url) AS source_url,
                   ccv.source_label, ccv.source_status,
                   COALESCE(json_agg(json_build_object(
                     'courseCode', prerequisite.code,
                     'minimumGrade', cp.minimum_grade
                   )) FILTER (WHERE prerequisite.id IS NOT NULL), '[]'::json) AS prerequisites
            FROM catalog_course cc
            JOIN course_catalog_version ccv
              ON ccv.id=cc.catalog_version_id AND ccv.tenant_id=cc.tenant_id
            LEFT JOIN course_prerequisite cp
              ON cp.course_id=cc.id AND cp.tenant_id=cc.tenant_id
             AND cp.catalog_version_id=cc.catalog_version_id
            LEFT JOIN catalog_course prerequisite ON prerequisite.id=cp.prerequisite_course_id
            WHERE cc.tenant_id=:tenant_id AND cc.catalog_version_id=:catalog_id
              AND cc.active=true
            GROUP BY cc.id, ccv.id ORDER BY cc.code
            """,
            {"tenant_id": auth.tenant_id, "catalog_id": selected["catalog_id"]},
        )
        courses = [_map_course(row) for row in course_rows]
        course_by_id = {course["id"]: course for course in courses}
        requirement_rows = await self._all(
            """
            SELECT course_id, category, recommended_term FROM program_requirement
            WHERE tenant_id=:tenant_id AND program_id=:program_id
              AND catalog_version_id=:catalog_id AND required=true
            ORDER BY recommended_term, id
            """,
            {
                "tenant_id": auth.tenant_id,
                "program_id": selected["id"],
                "catalog_id": selected["catalog_id"],
            },
        )
        credit_rows = await self._all(
            """
            SELECT id, source_type, source_code, title, grade_or_score, credits,
                   institution_name, source_document_id
            FROM student_transcript_credit
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY created_at, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        recommendation_rows = await self._all(
            """
            SELECT cer.id, cer.transcript_credit_id, target.code AS target_course_code,
                   target.title AS target_course_title, rule.code AS rule_code,
                   cer.rationale, cer.confidence, cer.status
            FROM course_exemption_recommendation cer
            JOIN catalog_course target ON target.id=cer.target_course_id
            JOIN course_equivalency_rule rule ON rule.id=cer.equivalency_rule_id
            WHERE cer.tenant_id=:tenant_id AND cer.student_id=:student_id
              AND cer.program_id=:program_id AND cer.status<>'superseded'
            ORDER BY target.code, cer.created_at
            """,
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "program_id": selected["id"],
            },
        )
        transcript_credits = [
            {
                "id": str(row["id"]),
                "sourceType": row["source_type"],
                "sourceCode": row.get("source_code"),
                "title": row["title"],
                "gradeOrScore": row.get("grade_or_score"),
                "credits": None if row.get("credits") is None else float(row["credits"]),
                "institutionName": row.get("institution_name"),
                "sourceDocumentId": (
                    str(row["source_document_id"]) if row.get("source_document_id") else None
                ),
            }
            for row in credit_rows
        ]
        recommendations = [
            {
                "id": str(row["id"]),
                "transcriptCreditId": str(row["transcript_credit_id"]),
                "targetCourseCode": row["target_course_code"],
                "targetCourseTitle": row["target_course_title"],
                "ruleCode": row["rule_code"],
                "rationale": row["rationale"],
                "confidence": float(row["confidence"]),
                "status": row["status"],
                "requiresStaffReview": True,
            }
            for row in recommendation_rows
        ]
        recommendation_by_code = {item["targetCourseCode"]: item for item in recommendations}
        approved = {
            item["targetCourseCode"] for item in recommendations if item["status"] == "approved"
        }
        plan: list[JsonDict] = []
        for requirement in requirement_rows:
            course = course_by_id.get(str(requirement["course_id"]))
            if course is None:
                continue
            recommendation = recommendation_by_code.get(str(course["code"]))
            prerequisites = _list(course.get("prerequisites"))
            prerequisite_codes = [
                str(item["courseCode"])
                for item in prerequisites
                if isinstance(item, dict) and item.get("courseCode")
            ]
            missing = [code for code in prerequisite_codes if code not in approved]
            status = (
                "exempted"
                if recommendation and recommendation["status"] == "approved"
                else "exemption_suggested"
                if recommendation
                else "blocked"
                if missing
                else "eligible"
            )
            plan.append(
                {
                    "course": course,
                    "category": requirement["category"],
                    "recommendedTerm": int(requirement["recommended_term"]),
                    "status": status,
                    "satisfiedPrerequisiteCodes": [
                        code for code in prerequisite_codes if code in approved
                    ],
                    "missingPrerequisiteCodes": missing,
                }
            )
        exempted = sum(
            float(item["course"]["credits"]) for item in plan if item["status"] == "exempted"
        )
        required = int(selected["total_credits"])
        return {
            "selectedProgram": _map_program(selected),
            "availablePrograms": [_map_program(row) for row in available_rows],
            "transcriptCredits": transcript_credits,
            "exemptionRecommendations": recommendations,
            "plan": plan,
            "progress": {
                "completedCredits": 0,
                "exemptedCredits": exempted,
                "requiredCredits": required,
                "percent": round(exempted / required * 100) if required else 0,
            },
            "catalogVersion": selected["catalog_code"],
            "generatedAt": _iso(_utc_now()),
        }

    async def search_catalog_courses(self, auth: AuthContext, query: str) -> JsonDict:
        normalized = query.strip()[:120]
        rows = await self._all(
            """
            SELECT cc.id, cc.code, cc.title, cc.description, cc.credits, cc.level,
                   cc.availability_label, cc.instructor_names, cc.meeting_pattern,
                   cc.resources, COALESCE(cc.source_url, ccv.source_url) AS source_url,
                   ccv.source_label, ccv.source_status, ccv.code AS catalog_code,
                   COALESCE(json_agg(json_build_object(
                     'courseCode', prerequisite.code,
                     'minimumGrade', cp.minimum_grade
                   )) FILTER (WHERE prerequisite.id IS NOT NULL), '[]'::json) AS prerequisites
            FROM catalog_course cc
            JOIN course_catalog_version ccv
              ON ccv.id=cc.catalog_version_id AND ccv.tenant_id=cc.tenant_id
             AND ccv.status='active'
            LEFT JOIN course_prerequisite cp
              ON cp.course_id=cc.id AND cp.tenant_id=cc.tenant_id
             AND cp.catalog_version_id=cc.catalog_version_id
            LEFT JOIN catalog_course prerequisite ON prerequisite.id=cp.prerequisite_course_id
            WHERE cc.tenant_id=:tenant_id AND cc.active=true
              AND (:query='' OR cc.code ILIKE :pattern OR cc.title ILIKE :pattern
                   OR cc.description ILIKE :pattern)
            GROUP BY cc.id, ccv.id ORDER BY cc.code LIMIT 60
            """,
            {"tenant_id": auth.tenant_id, "query": normalized, "pattern": f"%{normalized}%"},
        )
        items = [_map_course(row) for row in rows]
        return {
            "items": items,
            "total": len(items),
            "catalogVersion": rows[0]["catalog_code"] if rows else "unavailable",
        }

    async def get_student_financials(self, auth: AuthContext) -> JsonDict:
        summary = await self._one(
            """
            SELECT sfs.academic_year, sfs.cost_of_attendance_cents,
                   sfs.external_payments_cents,
                   COALESCE((SELECT SUM(pt.amount_cents) FROM payment_transaction pt
                     WHERE pt.tenant_id=sfs.tenant_id AND pt.student_id=sfs.student_id
                       AND pt.status='succeeded'), 0) AS portal_payments_cents
            FROM student_financial_summary sfs
            WHERE sfs.tenant_id=:tenant_id AND sfs.student_id=:student_id
            ORDER BY sfs.academic_year DESC LIMIT 1
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if summary is None:
            raise NotFoundError(
                "STUDENT_FINANCIALS_NOT_FOUND",
                "No financial record is available for this student",
            )
        common = {
            "tenant_id": auth.tenant_id,
            "student_id": auth.student_id,
            "academic_year": summary["academic_year"],
        }
        award_rows = await self._all(
            """
            SELECT id, source, name, type, offered_amount_cents,
                   accepted_amount_cents, status, requires_action
            FROM student_financial_award
            WHERE tenant_id=:tenant_id AND student_id=:student_id
              AND academic_year=:academic_year ORDER BY type, name
            """,
            common,
        )
        document_rows = await self._all(
            """
            SELECT id, code, title, description, status, due_at
            FROM financial_document_requirement
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY due_at NULLS LAST, code
            """,
            common,
        )
        plan_rows = await self._all(
            """
            SELECT id, name, installment_count, enrollment_fee_cents, status
            FROM student_payment_plan
            WHERE tenant_id=:tenant_id AND student_id=:student_id
              AND academic_year=:academic_year AND status<>'cancelled'
            ORDER BY installment_count
            """,
            common,
        )
        sap = await self._one(
            """
            SELECT status, cumulative_gpa, minimum_gpa, completion_rate_percent,
                   minimum_completion_rate_percent, attempted_credits,
                   maximum_attempted_credits
            FROM student_sap_status
            WHERE tenant_id=:tenant_id AND student_id=:student_id
              AND academic_year=:academic_year
            """,
            common,
        )
        if sap is None:
            raise NotFoundError(
                "STUDENT_SAP_NOT_FOUND",
                "No satisfactory academic progress record is available",
            )
        awards = [
            {
                "id": str(row["id"]),
                "source": row["source"],
                "name": row["name"],
                "type": row["type"],
                "offeredAmountCents": int(row["offered_amount_cents"]),
                "acceptedAmountCents": int(row["accepted_amount_cents"]),
                "status": row["status"],
                "requiresAction": bool(row["requires_action"]),
            }
            for row in award_rows
        ]
        accepted_aid = sum(
            item["acceptedAmountCents"] for item in awards if item["type"] != "work_study"
        )
        pending_aid = sum(
            item["offeredAmountCents"]
            for item in awards
            if item["type"] != "work_study" and item["status"] in {"offered", "pending"}
        )
        payments = int(summary["external_payments_cents"]) + int(summary["portal_payments_cents"])
        remaining = max(0, int(summary["cost_of_attendance_cents"]) - accepted_aid - payments)
        return {
            "academicYear": str(summary["academic_year"]).replace("-", "\N{EN DASH}", 1),
            "costOfAttendanceCents": int(summary["cost_of_attendance_cents"]),
            "acceptedAidCents": accepted_aid,
            "pendingAidCents": pending_aid,
            "paymentsCents": payments,
            "remainingBalanceCents": remaining,
            "awards": awards,
            "requiredDocuments": [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "title": row["title"],
                    "description": row["description"],
                    "status": row["status"],
                    "dueAt": _nullable_iso(row.get("due_at")),
                    "href": "/financials" if row["code"] == "award_acceptance" else "/documents",
                }
                for row in document_rows
            ],
            "paymentPlans": [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "installmentCount": int(row["installment_count"]),
                    "installmentAmountCents": math.ceil(remaining / int(row["installment_count"])),
                    "enrollmentFeeCents": int(row["enrollment_fee_cents"]),
                    "status": row["status"],
                }
                for row in plan_rows
            ],
            "sap": {
                "status": sap["status"],
                "cumulativeGpa": float(sap["cumulative_gpa"]),
                "minimumGpa": float(sap["minimum_gpa"]),
                "completionRatePercent": float(sap["completion_rate_percent"]),
                "minimumCompletionRatePercent": float(sap["minimum_completion_rate_percent"]),
                "attemptedCredits": float(sap["attempted_credits"]),
                "maximumAttemptedCredits": float(sap["maximum_attempted_credits"]),
            },
            "generatedAt": _iso(_utc_now()),
        }

    async def select_financial_payment_plan(
        self,
        auth: AuthContext,
        plan_id: str,
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            result = await connection.execute(
                text(
                    """
                    SELECT id, academic_year FROM student_payment_plan
                    WHERE id=:plan_id AND tenant_id=:tenant_id
                      AND student_id=:student_id AND status<>'cancelled'
                    FOR UPDATE
                    """
                ),
                {
                    "plan_id": plan_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                },
            )
            plan = result.mappings().first()
            if plan is None:
                raise NotFoundError(
                    "PAYMENT_PLAN_NOT_FOUND", "The selected payment plan was not found"
                )
            await connection.execute(
                text(
                    """
                    UPDATE student_payment_plan
                    SET status=CASE WHEN id=:plan_id THEN 'enrolled' ELSE 'available' END,
                        enrolled_at=CASE WHEN id=:plan_id THEN NOW() ELSE NULL END,
                        version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND academic_year=:academic_year AND status<>'cancelled'
                    """
                ),
                {
                    "plan_id": str(plan["id"]),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "academic_year": plan["academic_year"],
                },
            )
            await self._insert_audit(
                connection,
                auth,
                "student_financial.payment_plan_selected",
                "student_payment_plan",
                str(plan["id"]),
                request_id,
                {"academicYear": plan["academic_year"]},
            )
            await self._insert_outbox(
                connection,
                auth,
                "student_financial.payment_plan_selected.v1",
                "student_payment_plan",
                str(plan["id"]),
                1,
                request_id,
                {"studentId": auth.student_id},
            )
            return {"planId": str(plan["id"]), "status": "enrolled"}

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_financial.payment_plan.select",
            {"planId": plan_id},
            200,
            handler,
        )

    async def get_campus_life(self, auth: AuthContext) -> JsonDict:
        event_rows = await self._all(
            """
            SELECT id, title, description, starts_at, ends_at, location,
                   category, featured, accent, source_label, source_url,
                   source_status, registration_url, visual_theme, image_url,
                   image_alt, image_attribution, image_source_url
            FROM campus_event WHERE tenant_id=:tenant_id AND active=true
            ORDER BY featured DESC, starts_at, id LIMIT 30
            """,
            {"tenant_id": auth.tenant_id},
        )
        club_rows = await self._all(
            """
            SELECT club.id, club.name, club.category, club.description,
                   club.contact_name, club.contact_role, club.contact_channel,
                   club.latest_update, club.next_activity, club.source_label,
                   club.source_url, club.source_status, club.social_links,
                   club.long_description, club.meeting_schedule, club.membership_open,
                   COALESCE(media.public_path, '/media/clubs/code-collective.jpg') AS image_url,
                   COALESCE(media.alt_text, 'Students collaborating in a campus club') AS image_alt,
                   COALESCE(media.attribution, 'Default Aster club image') AS image_attribution,
                   COALESCE(media.source_url, '') AS image_source_url
            FROM student_club club
            LEFT JOIN media_asset media ON media.id=club.media_asset_id
              AND media.tenant_id=club.tenant_id AND media.active=true
            WHERE club.tenant_id=:tenant_id AND club.active=true
            ORDER BY club.name LIMIT 100
            """,
            {"tenant_id": auth.tenant_id},
        )
        club_event_rows = await self._all(
            """
            SELECT id, club_id, title, description, starts_at, ends_at, location,
                   category, registration_url FROM student_club_event
            WHERE tenant_id=:tenant_id AND active=true
            ORDER BY starts_at, id LIMIT 500
            """,
            {"tenant_id": auth.tenant_id},
        )
        events = [
            {
                "id": str(row["id"]),
                "title": row["title"],
                "description": row["description"],
                "startsAt": _iso(row["starts_at"]),
                "endsAt": _iso(row["ends_at"]),
                "location": row["location"],
                "category": row["category"],
                "featured": bool(row["featured"]),
                "accent": row["accent"],
                **({"visualTheme": row["visual_theme"]} if row.get("visual_theme") else {}),
                "imageUrl": row.get("image_url"),
                "imageAlt": row.get("image_alt"),
                "imageAttribution": row.get("image_attribution"),
                "imageSourceUrl": row.get("image_source_url"),
                "source": _map_source(row),
                "registrationUrl": row.get("registration_url"),
            }
            for row in event_rows
        ]
        clubs: list[JsonDict] = []
        for row in club_rows:
            clubs.append(
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "category": row["category"],
                    "description": row["description"],
                    "contactName": row["contact_name"],
                    "contactRole": row["contact_role"],
                    "contactChannel": row["contact_channel"],
                    "latestUpdate": row["latest_update"],
                    "nextActivity": row.get("next_activity"),
                    "imageUrl": row["image_url"],
                    "imageAlt": row["image_alt"],
                    "imageAttribution": row["image_attribution"],
                    "imageSourceUrl": row["image_source_url"],
                    "source": _map_source(row),
                    "socialLinks": _list(row.get("social_links")),
                    "longDescription": row.get("long_description"),
                    "meetingSchedule": row.get("meeting_schedule"),
                    "membershipOpen": bool(row["membership_open"]),
                    "events": [
                        {
                            "id": str(event["id"]),
                            "title": event["title"],
                            "description": event["description"],
                            "startsAt": _iso(event["starts_at"]),
                            "endsAt": _iso(event["ends_at"]),
                            "location": event["location"],
                            "category": event["category"],
                            "registrationUrl": event.get("registration_url"),
                        }
                        for event in club_event_rows
                        if str(event["club_id"]) == str(row["id"])
                    ],
                }
            )
        return {"events": events, "clubs": clubs, "generatedAt": _iso(_utc_now())}

    async def get_student_help(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT id, category, question, answer FROM help_article
            WHERE tenant_id=:tenant_id AND active=true ORDER BY sort_order, id
            """,
            {"tenant_id": auth.tenant_id},
        )
        return {
            "articles": [
                {
                    "id": str(row["id"]),
                    "category": row["category"],
                    "question": row["question"],
                    "answer": row["answer"],
                }
                for row in rows
            ],
            "support": {
                "email": "enrollment-support@vv.example",
                "phone": "+1 555 010 2027",
                "hours": "Monday-Friday, 09:00-17:00",
            },
        }

    async def create_student_help_request(
        self,
        auth: AuthContext,
        payload: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        topic_code = str(payload["topicCode"])
        message = str(payload["message"])
        subject = f"Student question about {topic_code.replace('_', ' ')}"
        priority = "high" if topic_code == "support" else "medium"

        async def handler(connection: AsyncConnection) -> JsonDict:
            inquiry_id = str(uuid4())
            result = await connection.execute(
                text(
                    """
                    INSERT INTO student_inquiry (
                      id, tenant_id, student_id, topic_code, subject, message,
                      status, priority, assignee_id, version, created_at, updated_at
                    )
                    SELECT :id, student.tenant_id, student.id, :topic_code, :subject,
                           :message, 'new', :priority, NULL, 1, NOW(), NOW()
                    FROM student
                    WHERE student.tenant_id=:tenant_id AND student.id=:student_id
                    RETURNING id, topic_code, subject, message, status, priority,
                              assignee_id, version, created_at, updated_at
                    """
                ),
                {
                    "id": inquiry_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "topic_code": topic_code,
                    "subject": subject,
                    "message": message,
                    "priority": priority,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
            response = _map_help_request(dict(row))
            await self._insert_audit(
                connection,
                auth,
                "student.help_request_created",
                "student_inquiry",
                inquiry_id,
                request_id,
                {
                    "topicCode": topic_code,
                    "status": "new",
                    "priority": priority,
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.help_request_created.v1",
                "student_inquiry",
                inquiry_id,
                1,
                request_id,
                {
                    "studentId": auth.student_id,
                    "topicCode": topic_code,
                    "status": "new",
                    "priority": priority,
                },
            )
            return response

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "help.request.create",
            {"topicCode": topic_code, "message": message},
            200,
            handler,
        )

    async def list_staff_help_requests(self, auth: AuthContext) -> list[JsonDict]:
        rows = await self._all(
            """
            SELECT inquiry.id, inquiry.topic_code, inquiry.subject, inquiry.message,
                   inquiry.status, inquiry.priority, inquiry.assignee_id,
                   inquiry.version, inquiry.created_at, inquiry.updated_at,
                   student.id AS student_id, student.class_year,
                   person.first_name, person.last_name,
                   COALESCE(profile.preferred_name, person.preferred_name,
                            person.first_name) AS preferred_name,
                   latest_program.name AS program_name
            FROM student_inquiry inquiry
            JOIN student
              ON student.id=inquiry.student_id
             AND student.tenant_id=inquiry.tenant_id
            JOIN person
              ON person.id=student.person_id
             AND person.tenant_id=student.tenant_id
            LEFT JOIN student_profile profile
              ON profile.student_id=student.id
             AND profile.tenant_id=student.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name
              FROM admission_offer offer
              JOIN program
                ON program.id=offer.program_id
               AND program.tenant_id=offer.tenant_id
              WHERE offer.tenant_id=inquiry.tenant_id
                AND offer.student_id=inquiry.student_id
              ORDER BY offer.created_at DESC, offer.id DESC
              LIMIT 1
            ) latest_program ON true
            WHERE inquiry.tenant_id=:tenant_id
            ORDER BY inquiry.updated_at DESC, inquiry.id
            LIMIT 200
            """,
            {"tenant_id": auth.tenant_id},
        )
        return [
            {
                **_map_help_request(row),
                "student": {
                    "id": str(row["student_id"]),
                    "name": f"{row['first_name']} {row['last_name']}",
                    "preferredName": row["preferred_name"],
                    "programName": row.get("program_name") or "Program not available",
                    "classYear": int(row["class_year"]),
                },
            }
            for row in rows
        ]

    async def _locked_onboarding(self, connection: AsyncConnection, auth: AuthContext) -> JsonDict:
        result = await connection.execute(
            text(
                """
                SELECT student_id, status, current_step, completed_steps, payload,
                       version, completed_at, updated_at
                FROM student_onboarding
                WHERE tenant_id=:tenant_id AND student_id=:student_id
                FOR UPDATE
                """
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        row = result.mappings().first()
        if row is None:
            raise NotFoundError("STUDENT_ONBOARDING_NOT_FOUND", "Student onboarding was not found")
        return dict(row)

    async def _validate_onboarding_step(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        step: str,
        data: Mapping[str, Any],
    ) -> None:
        validate_onboarding_step(step, data)
        if step == "offer":
            result = await connection.execute(
                text(
                    """
                    SELECT 1 FROM admission_offer
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND status='accepted' LIMIT 1
                    """
                ),
                {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
            )
            if result.first() is None:
                raise ConflictError(
                    "ACCEPTED_OFFER_REQUIRED",
                    "Accept the admission offer before completing this step",
                )
        if step == "deposit" and data.get("depositChoice") == "pay_now":
            result = await connection.execute(
                text(
                    """
                    SELECT 1 FROM payment_transaction
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND type='enrollment_deposit' AND status='succeeded' LIMIT 1
                    """
                ),
                {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
            )
            if result.first() is None:
                raise ConflictError(
                    "DEPOSIT_REQUIRED",
                    "Complete the enrollment deposit before saving this step",
                )

    def _request_hash(self, auth: AuthContext, payload: object) -> str:
        encoded = _json(
            {
                "tenantId": auth.tenant_id,
                "studentId": auth.student_id,
                "requestPayload": payload,
            }
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def _run_idempotent(
        self,
        auth: AuthContext,
        idempotency_key: str,
        request_id: str,
        operation: str,
        request_payload: object,
        response_status: int,
        handler: IdempotentHandler,
    ) -> JsonDict:
        del request_id  # request lineage is recorded by the command handler.
        request_hash = self._request_hash(auth, request_payload)
        lock_key = f"{auth.tenant_id}:{auth.actor_id}:{operation}:{idempotency_key}"
        async with self.engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            result = await connection.execute(
                text(
                    """
                    SELECT request_hash, response_body FROM idempotency_record
                    WHERE tenant_id=:tenant_id AND actor_id=:actor_id
                      AND operation=:operation AND idempotency_key=:idempotency_key
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "actor_id": auth.actor_id,
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                },
            )
            existing = result.mappings().first()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise ConflictError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "This idempotency key was already used for a different request",
                    )
                response = existing["response_body"]
                return _mapping(response)
            response = await handler(connection)
            await connection.execute(
                text(
                    """
                    INSERT INTO idempotency_record (
                      tenant_id, actor_id, operation, idempotency_key, request_hash,
                      response_status, response_body, created_at, expires_at
                    ) VALUES (
                      :tenant_id, :actor_id, :operation, :idempotency_key, :request_hash,
                      :response_status, CAST(:response_body AS jsonb), NOW(),
                      NOW()+INTERVAL '24 hours'
                    )
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "actor_id": auth.actor_id,
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                    "request_hash": request_hash,
                    "response_status": response_status,
                    "response_body": _json(response),
                },
            )
            return response

    async def _insert_audit(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO audit_event (
                  id, tenant_id, actor_type, actor_id, student_id, action,
                  resource_type, resource_id, authorization_basis, request_id,
                  correlation_id, metadata
                ) VALUES (
                  :id, :tenant_id, :actor_type, :actor_id, :student_id, :action,
                  :resource_type, :resource_id, 'student_self_service', :request_id,
                  :request_id, CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": auth.tenant_id,
                "actor_type": auth.actor_type,
                "actor_id": auth.actor_id,
                "student_id": auth.student_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "request_id": request_id,
                "metadata": _json(metadata),
            },
        )

    async def _insert_outbox(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        event_name: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        request_id: str,
        data: Mapping[str, Any],
    ) -> None:
        safe_data = json.loads(_json(data))
        event_id = str(uuid4())
        await self.outbox.enqueue(
            connection,
            DomainEventEnvelope(
                event_id=event_id,
                event_name=event_name,
                occurred_at=_utc_now(),
                tenant_id=auth.tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                actor=DomainEventActor(type=auth.actor_type, id=auth.actor_id),
                correlation_id=request_id,
                causation_id=str(uuid4()),
                data=safe_data,
            ),
        )

    async def _complete_requirement(
        self, connection: AsyncConnection, auth: AuthContext, requirement_code: str
    ) -> None:
        result = await connection.execute(
            text(
                """
                UPDATE student_requirement sr SET status='completed', progress_percent=100,
                  version=sr.version+1, updated_at=NOW()
                FROM requirement_definition_version rdv, enrollment_journey journey
                WHERE sr.tenant_id=:tenant_id AND sr.journey_id=journey.id
                  AND journey.student_id=:student_id
                  AND sr.requirement_definition_version_id=rdv.id
                  AND rdv.code=:requirement_code
                  AND sr.status NOT IN ('completed','waived','not_applicable')
                RETURNING sr.id
                """
            ),
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "requirement_code": requirement_code,
            },
        )
        for row in result.mappings().all():
            await self._award_rewards(
                connection,
                auth,
                "requirement_completed",
                requirement_code,
                str(row["id"]),
                {},
            )
        await connection.execute(
            text(
                """
                UPDATE student_requirement candidate SET status='ready',
                  version=candidate.version+1, updated_at=NOW()
                FROM requirement_definition_version definition,
                     enrollment_journey journey
                WHERE candidate.tenant_id=:tenant_id
                  AND candidate.journey_id=journey.id AND journey.student_id=:student_id
                  AND candidate.requirement_definition_version_id=definition.id
                  AND candidate.status='blocked'
                  AND NOT EXISTS (
                    SELECT 1 FROM unnest(definition.depends_on_codes) dependency(code)
                    WHERE NOT EXISTS (
                      SELECT 1 FROM student_requirement prerequisite
                      JOIN requirement_definition_version prerequisite_definition
                        ON prerequisite_definition.id=prerequisite.requirement_definition_version_id
                       AND prerequisite_definition.tenant_id=prerequisite.tenant_id
                      WHERE prerequisite.tenant_id=:tenant_id
                        AND prerequisite.journey_id=candidate.journey_id
                        AND prerequisite_definition.code=dependency.code
                        AND prerequisite.status IN ('completed','waived','not_applicable')
                    )
                  )
                """
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )

    async def _award_rewards(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        trigger_type: str,
        trigger_key: str,
        source_key: str,
        properties: Mapping[str, Any],
    ) -> int:
        result = await connection.execute(
            text(
                """
                SELECT id, points, max_awards_per_student FROM tenant_reward_rule
                WHERE tenant_id=:tenant_id AND trigger_type=:trigger_type
                  AND trigger_key=:trigger_key AND enabled=true
                  AND (starts_at IS NULL OR starts_at<=NOW())
                  AND (ends_at IS NULL OR ends_at>NOW())
                  AND CAST(:properties AS jsonb) @> trigger_properties
                ORDER BY display_order, id FOR UPDATE
                """
            ),
            {
                "tenant_id": auth.tenant_id,
                "trigger_type": trigger_type,
                "trigger_key": trigger_key,
                "properties": _json(properties),
            },
        )
        awarded = 0
        for rule in result.mappings().all():
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO student_reward_ledger (
                      id, tenant_id, student_id, reward_rule_id, source_type,
                      source_key, points, metadata, awarded_at
                    ) SELECT :id, :tenant_id, :student_id, :rule_id, :trigger_type,
                      :source_key, :points, CAST(:metadata AS jsonb), NOW()
                    WHERE (SELECT COUNT(*) FROM student_reward_ledger existing
                      WHERE existing.tenant_id=:tenant_id
                        AND existing.student_id=:student_id
                        AND existing.reward_rule_id=:rule_id) < :maximum
                    ON CONFLICT (tenant_id, student_id, reward_rule_id, source_key)
                    DO NOTHING RETURNING points
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "rule_id": rule["id"],
                    "trigger_type": trigger_type,
                    "source_key": source_key,
                    "points": int(rule["points"]),
                    "metadata": _json({"triggerKey": trigger_key, "properties": properties}),
                    "maximum": int(rule["max_awards_per_student"]),
                },
            )
            awarded += sum(int(row["points"]) for row in inserted.mappings().all())
        return awarded

    async def _reconcile_authoritative_rewards(
        self, connection: AsyncConnection, auth: AuthContext
    ) -> None:
        onboarding = await connection.execute(
            text(
                """SELECT status FROM student_onboarding
                   WHERE tenant_id=:tenant_id AND student_id=:student_id"""
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        row = onboarding.mappings().first()
        if row is not None and row["status"] == "completed":
            await self._award_rewards(
                connection, auth, "onboarding_completed", "onboarding", auth.student_id, {}
            )
        requirements = await connection.execute(
            text(
                """
                SELECT sr.id, rdv.code FROM student_requirement sr
                JOIN enrollment_journey journey
                  ON journey.id=sr.journey_id AND journey.tenant_id=sr.tenant_id
                JOIN requirement_definition_version rdv
                  ON rdv.id=sr.requirement_definition_version_id
                 AND rdv.tenant_id=sr.tenant_id
                WHERE sr.tenant_id=:tenant_id AND journey.student_id=:student_id
                  AND sr.status='completed'
                """
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        for requirement in requirements.mappings().all():
            await self._award_rewards(
                connection,
                auth,
                "requirement_completed",
                str(requirement["code"]),
                str(requirement["id"]),
                {},
            )

    async def _get_reward_summary(self, auth: AuthContext) -> JsonDict | None:
        row = await self._one(
            """
            SELECT program.point_name, program.points_per_usd,
                   COALESCE(SUM(ledger.points), 0)::bigint AS lifetime_points
            FROM tenant_reward_program program
            LEFT JOIN student_reward_ledger ledger
              ON ledger.tenant_id=program.tenant_id AND ledger.student_id=:student_id
            WHERE program.tenant_id=:tenant_id AND program.enabled=true
            GROUP BY program.point_name, program.points_per_usd
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if row is None:
            return None
        lifetime = int(row["lifetime_points"])
        points_per_usd = int(row["points_per_usd"])
        return {
            "pointName": row["point_name"],
            "pointsPerUsd": points_per_usd,
            "lifetimePoints": lifetime,
            "bookstoreCreditCents": math.floor(lifetime * 100 / points_per_usd),
        }

    @staticmethod
    def _category_for_document_type(document_type: str) -> str:
        return {
            "transcript": "transcript",
            "identity": "identity",
            "financial_aid": "financial_aid",
            "ferpa": "consent",
            "immunization": "health",
            "residency": "residency",
            "other": "other",
        }.get(document_type, "other")

    @staticmethod
    def _safe_profile_projection(
        fields: Sequence[Any], accepted_field_keys: Sequence[str]
    ) -> JsonDict:
        accepted = set(accepted_field_keys)
        projection: JsonDict = {}
        for item in fields:
            if not isinstance(item, dict) or item.get("key") not in accepted:
                continue
            value = str(item.get("value", "")).strip()
            if not value:
                continue
            key = item["key"]
            if key == "preferred_name" and len(value) <= 120:
                projection["preferredName"] = value
            elif key == "pronouns" and len(value) <= 80:
                projection["pronouns"] = value
            elif (
                key == "mobile_phone"
                and 7 <= len(value) <= 32
                and all(character.isdigit() or character in "+ ()-" for character in value)
            ):
                projection["mobilePhone"] = value
            elif key == "communication_preference" and value.lower() in {"email", "sms"}:
                projection["communicationPreference"] = value.lower()
        return projection

    async def _persist_transcript_credits(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document_id: str,
        extraction: Mapping[str, Any],
    ) -> None:
        for item in _list(extraction.get("courses")):
            if not isinstance(item, dict) or not item.get("title"):
                continue
            source_label = f"{item.get('sourceCode') or ''} {item['title']}".lower()
            source_type = (
                "ap" if "ap " in source_label else "ib" if "ib " in source_label else "transcript"
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO student_transcript_credit (
                      id, tenant_id, student_id, source_document_id, source_type,
                      source_code, title, grade_or_score, credits, institution_name,
                      evidence, reviewed_at
                    ) SELECT :id, :tenant_id, :student_id, :document_id, :source_type,
                      :source_code, :title, :grade_or_score, :credits, :institution_name,
                      CAST(:evidence AS jsonb), NOW()
                    WHERE NOT EXISTS (
                      SELECT 1 FROM student_transcript_credit credit
                      WHERE credit.tenant_id=:tenant_id AND credit.student_id=:student_id
                        AND credit.source_document_id=:document_id
                        AND COALESCE(credit.source_code,'')=COALESCE(:source_code,'')
                        AND credit.title=:title
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                    "source_type": source_type,
                    "source_code": item.get("sourceCode"),
                    "title": item["title"],
                    "grade_or_score": item.get("score", item.get("grade")),
                    "credits": item.get("credits"),
                    "institution_name": extraction.get("institutionName"),
                    "evidence": _json(
                        {
                            "term": item.get("term"),
                            "confidence": item.get("confidence"),
                            "projection": "automatic_transcript_extraction",
                        }
                    ),
                },
            )

    async def _project_course_exemptions(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document_id: str,
        extraction: Mapping[str, Any],
    ) -> None:
        evaluation = extraction.get("courseExemptionEvaluation")
        if not isinstance(evaluation, dict):
            return
        for item in _list(evaluation.get("decisions")):
            if (
                not isinstance(item, dict)
                or item.get("status") not in {"matched", "needs_review"}
                or not item.get("targetCourseId")
                or not item.get("equivalencyRuleId")
            ):
                continue
            status = "suggested" if item["status"] == "matched" else "needs_review"
            await connection.execute(
                text(
                    """
                    INSERT INTO course_exemption_recommendation (
                      id, tenant_id, student_id, program_id, catalog_version_id,
                      transcript_credit_id, target_course_id, equivalency_rule_id,
                      status, confidence, rationale
                    ) SELECT :id, credit.tenant_id, credit.student_id, offer.program_id,
                      rule.catalog_version_id, credit.id, rule.target_course_id, rule.id,
                      :status, :confidence, :rationale
                    FROM student_transcript_credit credit
                    JOIN admission_offer offer ON offer.tenant_id=credit.tenant_id
                      AND offer.student_id=credit.student_id
                    JOIN course_equivalency_rule rule ON rule.tenant_id=credit.tenant_id
                      AND rule.id=:rule_id AND rule.target_course_id=:target_course_id
                      AND rule.catalog_version_id=:catalog_version_id AND rule.active=true
                    WHERE credit.tenant_id=:tenant_id AND credit.student_id=:student_id
                      AND credit.source_document_id=:document_id
                      AND COALESCE(credit.source_code,'')=COALESCE(:source_code,'')
                      AND credit.title=:source_title
                    ON CONFLICT (
                      tenant_id, student_id, transcript_credit_id,
                      target_course_id, equivalency_rule_id
                    ) DO UPDATE SET
                      status=CASE WHEN course_exemption_recommendation.status
                        IN ('approved','denied') THEN course_exemption_recommendation.status
                        ELSE EXCLUDED.status END,
                      confidence=EXCLUDED.confidence, rationale=EXCLUDED.rationale,
                      version=course_exemption_recommendation.version+1, updated_at=NOW()
                    """
                ),
                {
                    "id": str(uuid4()),
                    "status": status,
                    "confidence": item.get("confidence", 0),
                    "rationale": item.get("rationale", ""),
                    "rule_id": item["equivalencyRuleId"],
                    "target_course_id": item["targetCourseId"],
                    "catalog_version_id": evaluation.get("catalogVersionId"),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                    "source_code": item.get("sourceCode"),
                    "source_title": item.get("sourceTitle", ""),
                },
            )

    async def _persist_immunization_evaluation(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document_id: str,
        extraction: Mapping[str, Any],
    ) -> None:
        evaluation = extraction.get("immunizationCompliance")
        if not isinstance(evaluation, dict):
            return
        generated_at = evaluation.get("generatedAt")
        await connection.execute(
            text(
                """
                INSERT INTO student_immunization_evaluation (
                  id, tenant_id, student_id, source_document_id,
                  policy_version_id, result, generated_at
                ) SELECT :id, :tenant_id, :student_id, :document_id, policy.id,
                  CAST(:result AS jsonb), :generated_at
                FROM immunization_policy_version policy
                WHERE policy.id=:policy_version_id AND policy.tenant_id=:tenant_id
                  AND policy.status='published'
                ON CONFLICT (
                  tenant_id, student_id, source_document_id, policy_version_id
                ) DO UPDATE SET result=EXCLUDED.result, generated_at=EXCLUDED.generated_at
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "document_id": document_id,
                "policy_version_id": evaluation.get("policyVersionId"),
                "result": _json(evaluation),
                "generated_at": (
                    datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
                    if generated_at
                    else _utc_now()
                ),
            },
        )
        await connection.execute(
            text(
                """
                UPDATE student_portal_projection SET projection_version=projection_version+1,
                  source_updated_at=NOW()
                WHERE tenant_id=:tenant_id AND student_id=:student_id
                """
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
