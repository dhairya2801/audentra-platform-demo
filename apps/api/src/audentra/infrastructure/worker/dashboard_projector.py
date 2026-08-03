"""Idempotent read-model projection for enrollment journey creation."""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, date, datetime
from typing import cast
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.infrastructure.messaging.envelope import DomainEventEnvelope, get_event_string

_HEADER_SQL = """
SELECT
  student.id AS student_id,
  person.preferred_name,
  person.first_name,
  person.last_name,
  student.class_year,
  admission_offer.id AS offer_id,
  program.name AS program_name,
  academic_term.name AS term_name,
  campus.name AS campus_name,
  admission_offer.response_deadline,
  admission_offer.deposit_amount_cents,
  admission_offer.status AS offer_status,
  enrollment_journey.id AS journey_id,
  enrollment_journey.status AS journey_status
FROM public.student
JOIN public.person
  ON person.id = student.person_id
 AND person.tenant_id = student.tenant_id
JOIN public.admission_offer
  ON admission_offer.student_id = student.id
 AND admission_offer.tenant_id = student.tenant_id
JOIN public.program
  ON program.id = admission_offer.program_id
 AND program.tenant_id = admission_offer.tenant_id
JOIN public.academic_term
  ON academic_term.id = admission_offer.academic_term_id
 AND academic_term.tenant_id = admission_offer.tenant_id
JOIN public.campus
  ON campus.id = admission_offer.campus_id
 AND campus.tenant_id = admission_offer.tenant_id
JOIN public.enrollment_journey
  ON enrollment_journey.offer_id = admission_offer.id
 AND enrollment_journey.student_id = student.id
 AND enrollment_journey.tenant_id = student.tenant_id
WHERE student.tenant_id = :tenant_id
  AND student.id = :student_id
  AND admission_offer.id = :offer_id
  AND enrollment_journey.id = :journey_id
LIMIT 1
"""

_REQUIREMENTS_SQL = """
SELECT
  student_requirement.id,
  definition.code,
  definition.title,
  definition.description,
  student_requirement.status,
  definition.blocking,
  student_requirement.due_at,
  student_requirement.progress_percent
FROM public.student_requirement
JOIN public.enrollment_journey AS journey
  ON journey.id = student_requirement.journey_id
 AND journey.tenant_id = student_requirement.tenant_id
JOIN public.requirement_definition_version AS evidence_definition
  ON evidence_definition.id = student_requirement.requirement_definition_version_id
 AND evidence_definition.tenant_id = student_requirement.tenant_id
JOIN public.journey_requirement_definition AS current_link
  ON current_link.journey_definition_version_id = journey.journey_definition_version_id
JOIN public.requirement_definition_version AS definition
  ON definition.id = current_link.requirement_definition_version_id
 AND definition.tenant_id = student_requirement.tenant_id
 AND definition.code = evidence_definition.code
WHERE student_requirement.tenant_id = :tenant_id
  AND student_requirement.journey_id = :journey_id
  AND student_requirement.retired_at IS NULL
ORDER BY CASE definition.flow_kind WHEN 'onboarding' THEN 0 ELSE 1 END,
         definition.display_order ASC, student_requirement.id ASC
"""

_TERMINAL_REQUIREMENT_STATUSES = {"completed", "waived", "not_applicable"}


class StudentDashboardProjector:
    def __init__(
        self,
        engine: AsyncEngine,
        consumer_name: str,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        if not consumer_name.strip() or len(consumer_name) > 120:
            raise ValueError("consumer_name must be between 1 and 120 characters")
        self._engine = engine
        self._consumer_name = consumer_name
        self._logger = logger or logging.getLogger(__name__)

    async def handle(self, event: DomainEventEnvelope) -> None:
        student_id = get_event_string(event.data, "studentId", "student_id")
        journey_id = get_event_string(event.data, "journeyId", "journey_id")
        offer_id = get_event_string(event.data, "offerId", "offer_id")
        if student_id is None or journey_id is None or offer_id is None:
            raise ValueError(f"{event.event_name} requires studentId, journeyId, and offerId")

        async with self._engine.begin() as connection:
            receipt = await connection.execute(
                text(
                    """
                    INSERT INTO public.projection_event_receipt (
                      event_id, consumer_name, processed_at
                    )
                    VALUES (:event_id, :consumer_name, NOW())
                    ON CONFLICT (event_id, consumer_name) DO NOTHING
                    RETURNING event_id
                    """
                ),
                {"event_id": event.event_id, "consumer_name": self._consumer_name},
            )
            if receipt.first() is None:
                self._logger.info(
                    "projection_event_already_consumed",
                    extra={
                        "event_id": event.event_id,
                        "event_name": event.event_name,
                        "consumer_name": self._consumer_name,
                    },
                )
                return
            projection_version = await self._rebuild(
                connection,
                tenant_id=event.tenant_id,
                student_id=student_id,
                offer_id=offer_id,
                journey_id=journey_id,
            )

        self._logger.info(
            "student_dashboard_projected",
            extra={
                "event_id": event.event_id,
                "tenant_id": event.tenant_id,
                "student_id": student_id,
                "journey_id": journey_id,
                "projection_version": projection_version,
            },
        )

    async def _rebuild(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: str,
        student_id: str,
        offer_id: str,
        journey_id: str,
    ) -> int:
        await connection.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:tenant_id || ':' || :student_id, 0))"
            ),
            {"tenant_id": tenant_id, "student_id": student_id},
        )
        existing = await connection.execute(
            text(
                """
                SELECT projection_version
                FROM public.student_portal_projection
                WHERE tenant_id = :tenant_id AND student_id = :student_id
                FOR UPDATE
                """
            ),
            {"tenant_id": tenant_id, "student_id": student_id},
        )
        existing_row = existing.mappings().first()
        previous_version = int(existing_row["projection_version"]) if existing_row else 0
        if previous_version < 0:
            raise ValueError("Existing projection_version is invalid")
        projection_version = previous_version + 1

        header_result = await connection.execute(
            text(_HEADER_SQL),
            {
                "tenant_id": tenant_id,
                "student_id": student_id,
                "offer_id": offer_id,
                "journey_id": journey_id,
            },
        )
        header = header_result.mappings().first()
        if header is None:
            raise RuntimeError(
                "Cannot project dashboard: authoritative enrollment data is missing "
                f"for student {student_id}"
            )
        requirements_result = await connection.execute(
            text(_REQUIREMENTS_SQL),
            {"tenant_id": tenant_id, "journey_id": journey_id},
        )
        requirements = [
            {
                "id": str(row["id"]),
                "code": str(row["code"]),
                "title": str(row["title"]),
                "description": str(row["description"]),
                "status": str(row["status"]),
                "blocking": bool(row["blocking"]),
                "dueAt": _iso_timestamp_or_none(row["due_at"]),
                "progressPercent": int(row["progress_percent"]),
            }
            for row in requirements_result.mappings().all()
        ]
        first_blocking = next(
            (
                item
                for item in requirements
                if bool(item["blocking"])
                and str(item["status"]) not in _TERMINAL_REQUIREMENT_STATUSES
            ),
            None,
        )
        journey_status = str(header["journey_status"])
        completion_percent = (
            100
            if not requirements and journey_status == "completed"
            else 0
            if not requirements
            else math.floor(
                sum(cast(int, item["progressPercent"]) for item in requirements) / len(requirements)
                + 0.5
            )
        )
        next_action = (
            {
                "code": first_blocking["code"],
                "label": first_blocking["title"],
                "href": f"/enrollment?requirement={quote(str(first_blocking['code']), safe='')}",
            }
            if first_blocking is not None
            else {
                "code": "enrollment_complete",
                "label": "Enrollment complete",
                "href": "/enrollment",
            }
            if not requirements and journey_status == "completed"
            else {
                "code": "review_enrollment",
                "label": "Review your enrollment",
                "href": "/enrollment",
            }
        )
        preferred_name = (
            str(header["preferred_name"] or "").strip() or str(header["first_name"]).strip()
        )
        dashboard = {
            "student": {
                "id": str(header["student_id"]),
                "preferredName": preferred_name,
                "fullName": (
                    f"{str(header['first_name']).strip()} {str(header['last_name']).strip()}"
                ).strip(),
                "classYear": int(header["class_year"]),
            },
            "offer": {
                "id": str(header["offer_id"]),
                "programName": str(header["program_name"]),
                "termName": str(header["term_name"]),
                "campusName": str(header["campus_name"]),
                "responseDeadline": _iso_date(header["response_deadline"]),
                "depositAmountCents": int(header["deposit_amount_cents"]),
                "status": str(header["offer_status"]),
            },
            "journey": {
                "id": str(header["journey_id"]),
                "status": journey_status,
                "completionPercent": completion_percent,
                "nextAction": next_action,
                "requirements": requirements,
            },
            "unreadMessageCount": 0,
            "projectionVersion": projection_version,
            "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        await connection.execute(
            text(
                """
                INSERT INTO public.student_portal_projection (
                  tenant_id, student_id, projection_version, dashboard,
                  source_updated_at, projected_at
                )
                VALUES (
                  :tenant_id, :student_id, :projection_version,
                  CAST(:dashboard AS jsonb), NOW(), NOW()
                )
                ON CONFLICT (tenant_id, student_id)
                DO UPDATE SET
                  projection_version = EXCLUDED.projection_version,
                  dashboard = EXCLUDED.dashboard,
                  source_updated_at = EXCLUDED.source_updated_at,
                  projected_at = EXCLUDED.projected_at
                """
            ),
            {
                "tenant_id": tenant_id,
                "student_id": student_id,
                "projection_version": projection_version,
                "dashboard": json.dumps(dashboard, separators=(",", ":")),
            },
        )
        return projection_version


def _iso_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            pass
    raise ValueError(f"Invalid response deadline: {value}")


def _iso_timestamp_or_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    raise ValueError(f"Invalid requirement due date: {value}")
