# ruff: noqa: S608 -- schema identifiers are validated before interpolation.
"""Tenant-safe PostgreSQL repository for the staff action center.

Every staff mutation uses one SQLAlchemy transaction. Canonical state, append-only
work history, optional student notification, audit, and outbox publication therefore
either commit together or all roll back.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import (
    ApiError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)

_MISSING = object()
_NO_DEFAULT = object()
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class StaffStudentReader(Protocol):
    """Narrow read port supplied by the future PostgreSQL student repository."""

    async def get_student_onboarding(self, auth: AuthContext) -> Mapping[str, object]: ...

    async def get_student_profile(self, auth: AuthContext) -> Mapping[str, object]: ...

    async def get_student_requirements(self, auth: AuthContext) -> Mapping[str, object]: ...

    async def get_student_documents(self, auth: AuthContext) -> Mapping[str, object]: ...


class PostgresStaffRepository:
    """Port of the Nest staff action store using SQLAlchemy's async connection API."""

    def __init__(
        self,
        engine: AsyncEngine,
        student_reader: StaffStudentReader,
        *,
        schema: str = "public",
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not _SQL_IDENTIFIER.fullmatch(schema):
            raise ValueError("schema is not a safe PostgreSQL identifier")
        self._engine = engine
        self._reader = student_reader
        self._schema = schema
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory

    def _table(self, name: str) -> str:
        return f"{self._schema}.{name}"

    async def get_action_center(self, auth: AuthContext) -> dict[str, object]:
        self._require_staff(auth)
        await self._ensure_document_work_items(auth)
        async with self._engine.connect() as connection:
            member_result = await connection.execute(
                text(
                    f"""
                    SELECT id, display_name, email_normalized, component
                    FROM {self._table("staff_member")}
                    WHERE tenant_id = :tenant_id AND active = true
                    ORDER BY display_name, id
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            item_result = await connection.execute(
                text(self._action_center_items_sql()),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            log_result = await connection.execute(
                text(
                    f"""
                    SELECT id, work_item_id, action, message, actor_name, occurred_at
                    FROM {self._table("staff_work_log")}
                    WHERE tenant_id = :tenant_id
                    ORDER BY occurred_at DESC, id
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            members = [
                {
                    "id": str(row["id"]),
                    "name": str(row["display_name"]),
                    "email": str(row["email_normalized"]),
                    "component": str(row["component"]),
                }
                for row in member_result.mappings().all()
            ]
            logs = [dict(row) for row in log_result.mappings().all()]
            items = [self._map_work_item(dict(row), logs) for row in item_result.mappings().all()]
        return {
            "items": items,
            "staff": members,
            "counts": {
                "todo": sum(item["status"] == "todo" for item in items),
                "inProgress": sum(item["status"] == "in_progress" for item in items),
                "done": sum(item["status"] == "done" for item in items),
                "urgent": sum(item["priority"] == "urgent" for item in items),
                "escalated": sum(bool(item["escalated"]) for item in items),
            },
            "generatedAt": _iso_timestamp(self._clock()),
        }

    async def get_student_record(
        self,
        auth: AuthContext,
        student_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        student_auth = replace(auth, student_id=student_id)
        onboarding_task = self._reader.get_student_onboarding(student_auth)
        profile_task = self._reader.get_student_profile(student_auth)
        requirements_task = self._reader.get_student_requirements(student_auth)
        documents_task = self._reader.get_student_documents(student_auth)
        summary_task = self._student_summary(auth.tenant_id, student_id)
        onboarding, profile, requirements, documents, summary = await asyncio.gather(
            onboarding_task,
            profile_task,
            requirements_task,
            documents_task,
            summary_task,
        )
        if summary is None:
            raise NotFoundError("STAFF_STUDENT_NOT_FOUND", "The student was not found")
        return {
            "student": summary,
            "onboarding": dict(onboarding),
            "profile": dict(profile),
            "requirements": dict(requirements),
            "documents": dict(documents),
        }

    async def update_work_item(
        self,
        auth: AuthContext,
        work_item_id: str,
        update: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(update, "expectedVersion", "expected_version"),
            "expectedVersion",
        )
        async with self._engine.begin() as connection:
            current = await self._lock_work_item(connection, auth, work_item_id)
            if _database_integer(current["version"], "staff_work_item.version") != expected_version:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This work item changed in another staff session",
                )

            status_value = _read(update, "status", default=None)
            next_status = str(status_value) if status_value is not None else str(current["status"])
            assignee_value = _read(update, "assigneeId", "assignee_id", default=_MISSING)
            next_assignee = current["assignee_id"] if assignee_value is _MISSING else assignee_value
            escalated_value = _read(update, "escalated", default=None)
            next_escalated = (
                bool(current["escalated"]) if escalated_value is None else bool(escalated_value)
            )
            if next_assignee is not None:
                assignee = await connection.execute(
                    text(
                        f"""
                        SELECT id
                        FROM {self._table("staff_member")}
                        WHERE tenant_id = :tenant_id
                          AND id = :assignee_id
                          AND active = true
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "assignee_id": _uuid(str(next_assignee)),
                    },
                )
                if assignee.mappings().first() is None:
                    raise NotFoundError(
                        "STAFF_MEMBER_NOT_FOUND",
                        "The assignee was not found",
                    )

            changes: list[tuple[str, str]] = []
            if next_status != current["status"]:
                changes.append(
                    (
                        "status_changed",
                        "Moved from "
                        f"{str(current['status']).replace('_', ' ')} to "
                        f"{next_status.replace('_', ' ')}.",
                    )
                )
            if _optional_uuid_string(next_assignee) != _optional_uuid_string(
                current["assignee_id"]
            ):
                assignee_name = (
                    await self._staff_name(connection, auth, str(next_assignee))
                    if next_assignee is not None
                    else None
                )
                changes.append(
                    (
                        "assigned",
                        f"Assigned to {assignee_name}."
                        if assignee_name
                        else "Removed the assignee.",
                    )
                )
            if next_escalated != bool(current["escalated"]):
                changes.append(
                    (
                        "escalated",
                        "Marked as escalated."
                        if next_escalated
                        else "Cleared the escalation flag.",
                    )
                )
            note = _optional_text(_read(update, "note", default=None))
            if note:
                changes.append(("commented", note))
            if not changes:
                raise BadRequestError(
                    "STAFF_WORK_ITEM_NO_CHANGES",
                    "Choose a status, assignee, escalation state, or note to update",
                )

            updated = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_work_item")}
                    SET status = :status,
                        assignee_id = :assignee_id,
                        escalated = :escalated,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND id = :work_item_id
                      AND version = :expected_version
                    RETURNING version
                    """
                ),
                {
                    "status": next_status,
                    "assignee_id": (
                        _uuid(str(next_assignee)) if next_assignee is not None else None
                    ),
                    "escalated": next_escalated,
                    "tenant_id": _uuid(auth.tenant_id),
                    "work_item_id": _uuid(work_item_id),
                    "expected_version": expected_version,
                },
            )
            updated_row = updated.mappings().first()
            if updated_row is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This work item changed in another staff session",
                )
            version = int(updated_row["version"])
            actor_name = await self._staff_name(connection, auth, auth.actor_id)
            for action, message in changes:
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=work_item_id,
                    actor_name=actor_name,
                    action=action,
                    message=message,
                )
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff_work_item.updated",
                resource_type="staff_work_item",
                resource_id=work_item_id,
                metadata={"version": version, "changes": [change[0] for change in changes]},
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="staff.work_item_updated.v1",
                aggregate_type="staff_work_item",
                aggregate_id=work_item_id,
                aggregate_version=version,
                data={
                    "workItemId": work_item_id,
                    "studentId": str(current["student_id"]),
                    "status": next_status,
                    "assigneeId": _optional_uuid_string(next_assignee),
                    "escalated": next_escalated,
                },
            )
        return await self._require_work_item(auth, work_item_id)

    async def update_inquiry(
        self,
        auth: AuthContext,
        inquiry_id: str,
        update: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object] | None:
        """Update a canonical inquiry, or return ``None`` for preview fallback."""

        self._require_staff(auth)
        expected_version = _integer(
            _read(update, "expectedVersion", "expected_version"),
            "expectedVersion",
        )
        next_status = str(_read(update, "status"))
        assignee_value = _read(update, "assigneeId", "assignee_id", default=_MISSING)
        response_note = _optional_text(_read(update, "responseNote", "response_note", default=None))
        notify_student = bool(_read(update, "notifyStudent", "notify_student"))

        async with self._engine.begin() as connection:
            inquiry = await self._lock_inquiry(connection, auth, inquiry_id)
            if inquiry is None:
                return None
            if _database_integer(inquiry["version"], "student_inquiry.version") != expected_version:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This inquiry changed in another staff session",
                )

            next_assignee = inquiry["assignee_id"] if assignee_value is _MISSING else assignee_value
            assignee: dict[str, object] | None = None
            if next_assignee is not None:
                assignee = await self._active_staff_summary(
                    connection,
                    auth,
                    str(next_assignee),
                )
                if assignee is None:
                    raise BadRequestError(
                        "STAFF_ASSIGNEE_NOT_FOUND",
                        "Choose an active staff assignee",
                    )

            updated_result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("student_inquiry")}
                    SET status = :status,
                        assignee_id = :assignee_id,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND id = :inquiry_id
                      AND version = :expected_version
                    RETURNING version, updated_at
                    """
                ),
                {
                    "status": next_status,
                    "assignee_id": (
                        _uuid(str(next_assignee)) if next_assignee is not None else None
                    ),
                    "tenant_id": _uuid(auth.tenant_id),
                    "inquiry_id": _uuid(inquiry_id),
                    "expected_version": expected_version,
                },
            )
            updated = updated_result.mappings().first()
            if updated is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This inquiry changed in another staff session",
                )
            version = _database_integer(updated["version"], "student_inquiry.version")

            notification: dict[str, object] | None = None
            if response_note is not None and notify_student:
                notification = await self._insert_student_message(
                    connection,
                    auth=auth,
                    student_id=str(inquiry["student_id"]),
                    subject=f"Reply: {inquiry['subject']}",
                    body=response_note,
                )
            if response_note is not None:
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("student_inquiry_reply")} (
                          id, tenant_id, inquiry_id, student_id, staff_member_id,
                          response_note, notify_student, student_message_id, created_at
                        )
                        VALUES (
                          :id, :tenant_id, :inquiry_id, :student_id, :staff_member_id,
                          :response_note, :notify_student, :student_message_id, NOW()
                        )
                        """
                    ),
                    {
                        "id": self._uuid_factory(),
                        "tenant_id": _uuid(auth.tenant_id),
                        "inquiry_id": _uuid(inquiry_id),
                        "student_id": _uuid(str(inquiry["student_id"])),
                        "staff_member_id": _uuid(auth.actor_id),
                        "response_note": response_note,
                        "notify_student": notify_student,
                        "student_message_id": (
                            _uuid(str(notification["id"])) if notification is not None else None
                        ),
                    },
                )

            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="student_inquiry.updated_by_staff",
                resource_type="student_inquiry",
                resource_id=inquiry_id,
                metadata={
                    "status": next_status,
                    "assigneeId": _optional_uuid_string(next_assignee),
                    "responseRecorded": response_note is not None,
                    "notifiedStudent": notification is not None,
                    "version": version,
                },
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="student.inquiry_updated_by_staff.v1",
                aggregate_type="student_inquiry",
                aggregate_id=inquiry_id,
                aggregate_version=version,
                data={
                    "studentId": str(inquiry["student_id"]),
                    "status": next_status,
                    "assigneeId": _optional_uuid_string(next_assignee),
                    "responseRecorded": response_note is not None,
                    "notifiedStudent": notification is not None,
                },
            )

        return _map_staff_inquiry(
            inquiry,
            status=next_status,
            assignee=assignee,
            version=version,
            updated_at=updated["updated_at"],
        )

    async def update_student_preferences(
        self,
        auth: AuthContext,
        student_id: str,
        update: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_onboarding_version = _integer(
            _read(update, "expectedOnboardingVersion", "expected_onboarding_version"),
            "expectedOnboardingVersion",
        )
        expected_profile_version = _integer(
            _read(update, "expectedProfileVersion", "expected_profile_version"),
            "expectedProfileVersion",
        )
        communication_preference = str(
            _read(update, "communicationPreference", "communication_preference")
        )
        housing_preference = str(_read(update, "housingPreference", "housing_preference"))
        accommodation_interest = str(
            _read(update, "accommodationInterest", "accommodation_interest")
        )
        residency_verification_path = str(
            _read(update, "residencyVerificationPath", "residency_verification_path")
        )
        notify_student = bool(_read(update, "notifyStudent", "notify_student"))
        note = _optional_text(_read(update, "note", default=None))

        async with self._engine.begin() as connection:
            onboarding_result = await connection.execute(
                text(
                    f"""
                    SELECT payload, version
                    FROM {self._table("student_onboarding")}
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                },
            )
            profile_result = await connection.execute(
                text(
                    f"""
                    SELECT version
                    FROM {self._table("student_profile")}
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                },
            )
            onboarding = onboarding_result.mappings().first()
            profile = profile_result.mappings().first()
            if onboarding is None or profile is None:
                raise NotFoundError("STAFF_STUDENT_NOT_FOUND", "The student was not found")
            if (
                int(onboarding["version"]) != expected_onboarding_version
                or int(profile["version"]) != expected_profile_version
            ):
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "The student record changed in another session",
                )
            payload = _json_object(onboarding["payload"], "student_onboarding.payload")
            payload.update(
                {
                    "communicationPreference": communication_preference,
                    "housingPreference": housing_preference,
                    "accommodationInterest": accommodation_interest,
                    "residencyVerificationPath": residency_verification_path,
                }
            )
            if housing_preference != "on_campus":
                payload.pop("housingResidenceOption", None)
                payload.pop("housingResidencePreferences", None)

            onboarding_update = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("student_onboarding")}
                    SET payload = CAST(:payload AS jsonb),
                        version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND version = :expected_version
                    RETURNING version
                    """
                ),
                {
                    "payload": json.dumps(payload, separators=(",", ":")),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                    "expected_version": expected_onboarding_version,
                },
            )
            profile_update = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("student_profile")}
                    SET communication_preference = :communication_preference,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND version = :expected_version
                    RETURNING version
                    """
                ),
                {
                    "communication_preference": communication_preference,
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                    "expected_version": expected_profile_version,
                },
            )
            onboarding_row = onboarding_update.mappings().first()
            profile_row = profile_update.mappings().first()
            if onboarding_row is None or profile_row is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "The student record changed in another session",
                )
            onboarding_version = int(onboarding_row["version"])
            profile_version = int(profile_row["version"])
            actor_name = await self._staff_name(connection, auth, auth.actor_id)
            onboarding_items = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_work_item")}
                    SET version = version + 1, updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND source_type = 'onboarding'
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                },
            )
            for item in onboarding_items.mappings().all():
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=str(item["id"]),
                    actor_name=actor_name,
                    action="student_preferences_updated",
                    message=note or "Updated the student's operational onboarding preferences.",
                )
            if notify_student:
                await self._insert_student_message(
                    connection,
                    auth=auth,
                    student_id=student_id,
                    subject="Your enrollment preferences were updated",
                    body=note
                    or (
                        "Your enrollment team updated your communication, housing, "
                        "and support follow-up preferences. Review your enrollment "
                        "page for the latest details."
                    ),
                )
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="student_preferences.updated_by_staff",
                resource_type="student_onboarding",
                resource_id=student_id,
                metadata={
                    "onboardingVersion": onboarding_version,
                    "profileVersion": profile_version,
                    "notifiedStudent": notify_student,
                },
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="student.preferences_updated_by_staff.v1",
                aggregate_type="student_onboarding",
                aggregate_id=student_id,
                aggregate_version=onboarding_version,
                data={
                    "studentId": student_id,
                    "communicationPreference": communication_preference,
                    "housingPreference": housing_preference,
                    "notifiedStudent": notify_student,
                },
            )
        return await self.get_student_record(auth, student_id)

    async def review_document(
        self,
        auth: AuthContext,
        document_id: str,
        review: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        work_item_id = str(_read(review, "workItemId", "work_item_id"))
        expected_version = _integer(
            _read(
                review,
                "expectedWorkItemVersion",
                "expected_work_item_version",
            ),
            "expectedWorkItemVersion",
        )
        decision = str(_read(review, "decision"))
        note = str(_read(review, "note")).strip()
        notification_requested = bool(_read(review, "notifyStudent", "notify_student"))
        notification: dict[str, object] | None = None

        async with self._engine.begin() as connection:
            work_item = await self._lock_work_item(connection, auth, work_item_id)
            if work_item["source_type"] != "document" or str(work_item["source_id"]) != document_id:
                raise NotFoundError(
                    "STAFF_WORK_ITEM_NOT_FOUND",
                    "The document review work item was not found",
                )
            if (
                _database_integer(work_item["version"], "staff_work_item.version")
                != expected_version
            ):
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This document review changed in another staff session",
                )
            document_result = await connection.execute(
                text(
                    f"""
                    SELECT id, student_id, requirement_id, file_name, status
                    FROM {self._table("document_record")}
                    WHERE tenant_id = :tenant_id AND id = :document_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "document_id": _uuid(document_id),
                },
            )
            document = document_result.mappings().first()
            if document is None:
                raise NotFoundError("STAFF_DOCUMENT_NOT_FOUND", "The document was not found")
            if document["status"] not in {"needs_review", "under_review"}:
                raise ConflictError(
                    "DOCUMENT_REVIEW_ALREADY_DECIDED",
                    "This document already has an official staff decision",
                )
            await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("document_record")}
                    SET status = :decision, updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND id = :document_id
                    """
                ),
                {
                    "decision": decision,
                    "tenant_id": _uuid(auth.tenant_id),
                    "document_id": _uuid(document_id),
                },
            )
            requirement_id = document["requirement_id"]
            if requirement_id is not None:
                requirement_status = "completed" if decision == "accepted" else "rejected"
                progress = 100 if decision == "accepted" else 60
                await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("student_requirement")}
                        SET status = :status,
                            progress_percent = :progress,
                            version = version + 1,
                            updated_at = NOW()
                        WHERE tenant_id = :tenant_id AND id = :requirement_id
                        """
                    ),
                    {
                        "status": requirement_status,
                        "progress": progress,
                        "tenant_id": _uuid(auth.tenant_id),
                        "requirement_id": _uuid(str(requirement_id)),
                    },
                )
                if decision == "accepted":
                    await self._award_requirement_rewards(
                        connection,
                        auth=auth,
                        student_id=str(document["student_id"]),
                        requirement_id=str(requirement_id),
                    )
                    await self._refresh_requirement_dependencies(
                        connection,
                        auth,
                        str(requirement_id),
                    )
            item_update = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_work_item")}
                    SET status = 'done', version = version + 1, updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND id = :work_item_id
                      AND version = :expected_version
                    RETURNING version
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "work_item_id": _uuid(work_item_id),
                    "expected_version": expected_version,
                },
            )
            item_row = item_update.mappings().first()
            if item_row is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This document review changed in another staff session",
                )
            item_version = int(item_row["version"])
            actor_name = await self._staff_name(connection, auth, auth.actor_id)
            verb = "Accepted" if decision == "accepted" else "Requested changes to"
            await self._insert_work_log(
                connection,
                auth=auth,
                work_item_id=work_item_id,
                actor_name=actor_name,
                action="document_decided",
                message=f"{verb} {document['file_name']}: {note}",
            )
            notification = await self._insert_student_message(
                connection,
                auth=auth,
                student_id=str(document["student_id"]),
                subject=(
                    f"{document['file_name']} was accepted"
                    if decision == "accepted"
                    else f"{document['file_name']} needs changes"
                ),
                body=note
                if notification_requested
                else (
                    "Your document was reviewed and accepted."
                    if decision == "accepted"
                    else "Your document was reviewed and needs changes. Open Documents for details."
                ),
                kind="document_review",
                href=f"/documents?document={document_id}",
            )
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="student_document.decided_by_staff",
                resource_type="document_record",
                resource_id=document_id,
                metadata={
                    "decision": decision,
                    "workItemId": work_item_id,
                    "notifiedStudent": True,
                    "notificationRequested": notification_requested,
                },
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="student.document_decided_by_staff.v1",
                aggregate_type="document_record",
                aggregate_id=document_id,
                aggregate_version=item_version,
                data={
                    "documentId": document_id,
                    "studentId": str(document["student_id"]),
                    "decision": decision,
                    "workItemId": work_item_id,
                    "notifiedStudent": True,
                    "notificationRequested": notification_requested,
                },
            )

        current_work_item = await self._require_work_item(auth, work_item_id)
        student = cast(Mapping[str, object], current_work_item["student"])
        student_auth = replace(auth, student_id=str(student["id"]))
        documents = dict(await self._reader.get_student_documents(student_auth))
        document_items = documents.get("items")
        if not isinstance(document_items, list):
            document_items = []
        decided_document = next(
            (
                candidate
                for candidate in document_items
                if isinstance(candidate, dict) and candidate.get("id") == document_id
            ),
            None,
        )
        if decided_document is None:
            raise ApiError(
                500,
                "STAFF_DOCUMENT_DECISION_INCONSISTENT",
                "The document decision committed but the document could not be reloaded",
            )
        return {
            "document": decided_document,
            "workItem": current_work_item,
            "notification": notification,
        }

    async def _ensure_document_work_items(self, auth: AuthContext) -> None:
        async with self._engine.connect() as connection:
            pending_result = await connection.execute(
                text(
                    f"""
                    SELECT document.id, document.student_id,
                           document.file_name, document.category
                    FROM {self._table("document_record")} AS document
                    WHERE document.tenant_id = :tenant_id
                      AND document.status IN ('needs_review', 'under_review')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM {self._table("staff_work_item")} AS item
                        WHERE item.tenant_id = document.tenant_id
                          AND item.source_type = 'document'
                          AND item.source_id = document.id
                      )
                    ORDER BY document.created_at, document.id
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            pending = [dict(row) for row in pending_result.mappings().all()]

        for document in pending:
            component, priority = _document_route(str(document["category"]))
            async with self._engine.begin() as connection:
                assignee_result = await connection.execute(
                    text(
                        f"""
                        SELECT id
                        FROM {self._table("staff_member")}
                        WHERE tenant_id = :tenant_id AND active = true
                        ORDER BY
                          CASE WHEN component = :component THEN 0 ELSE 1 END,
                          display_name,
                          id
                        LIMIT 1
                        """
                    ),
                    {"tenant_id": _uuid(auth.tenant_id), "component": component},
                )
                assignee = assignee_result.mappings().first()
                assignee_id = assignee["id"] if assignee is not None else None
                work_item_id = self._uuid_factory()
                document_id = str(document["id"])
                inserted = await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("staff_work_item")} (
                          id, tenant_id, student_id, key, title, description,
                          status, priority, work_type, component, due_at,
                          escalated, assignee_id, source_type, source_id, version
                        )
                        VALUES (
                          :id, :tenant_id, :student_id, :key, :title,
                          'Verify the stored original and make the official staff decision.',
                          'todo', :priority, 'document_review', :component,
                          NOW() + INTERVAL '2 days', false, :assignee_id,
                          'document', :document_id, 1
                        )
                        ON CONFLICT (tenant_id, source_type, source_id) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "id": work_item_id,
                        "tenant_id": _uuid(auth.tenant_id),
                        "student_id": _uuid(str(document["student_id"])),
                        "key": f"DOC-{document_id.replace('-', '')[:8].upper()}",
                        "title": f"Review {document['file_name']}",
                        "priority": priority,
                        "component": component,
                        "assignee_id": assignee_id,
                        "document_id": _uuid(document_id),
                    },
                )
                if inserted.mappings().first() is not None:
                    await self._insert_work_log(
                        connection,
                        auth=auth,
                        work_item_id=str(work_item_id),
                        actor_name="VV workflow",
                        actor_type="system",
                        action="created",
                        message="Created when the student document entered staff review.",
                    )

    def _action_center_items_sql(self) -> str:
        item = self._table("staff_work_item")
        student = self._table("student")
        person = self._table("person")
        profile = self._table("student_profile")
        member = self._table("staff_member")
        offer = self._table("admission_offer")
        program = self._table("program")
        return f"""
            SELECT
              item.id, item.key, item.student_id, item.title, item.description,
              item.status, item.priority, item.work_type, item.component,
              item.due_at, item.escalated, item.version, item.created_at,
              item.updated_at, item.assignee_id,
              assignee.display_name AS assignee_name,
              assignee.email_normalized AS assignee_email,
              assignee.component AS assignee_component,
              person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name,
              COALESCE(offer_program.name, 'Program not assigned') AS program_name,
              student.class_year, item.source_type, item.source_id
            FROM {item} AS item
            JOIN {student} AS student
              ON student.id = item.student_id AND student.tenant_id = item.tenant_id
            JOIN {person} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {profile} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            LEFT JOIN {member} AS assignee
              ON assignee.id = item.assignee_id AND assignee.tenant_id = item.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name
              FROM {offer} AS offer
              JOIN {program} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = item.tenant_id
                AND offer.student_id = item.student_id
              ORDER BY offer.created_at DESC
              LIMIT 1
            ) AS offer_program ON true
            WHERE item.tenant_id = :tenant_id
            ORDER BY
              CASE item.priority
                WHEN 'urgent' THEN 1
                WHEN 'high' THEN 2
                WHEN 'medium' THEN 3
                ELSE 4
              END,
              item.due_at NULLS LAST,
              item.updated_at DESC,
              item.id
        """

    async def _student_summary(
        self,
        tenant_id: str,
        student_id: str,
    ) -> dict[str, object] | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(self._student_summary_sql()),
                {"tenant_id": _uuid(tenant_id), "student_id": _uuid(student_id)},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "name": f"{row['first_name']} {row['last_name']}",
            "preferredName": str(row["preferred_name"]),
            "programName": str(row["program_name"]),
            "classYear": int(row["class_year"]),
        }

    def _student_summary_sql(self) -> str:
        student = self._table("student")
        person = self._table("person")
        profile = self._table("student_profile")
        offer = self._table("admission_offer")
        program = self._table("program")
        return f"""
            SELECT student.id, person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name,
              COALESCE(offer_program.name, 'Program not assigned') AS program_name,
              student.class_year
            FROM {student} AS student
            JOIN {person} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {profile} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name
              FROM {offer} AS offer
              JOIN {program} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = student.tenant_id
                AND offer.student_id = student.id
              ORDER BY offer.created_at DESC
              LIMIT 1
            ) AS offer_program ON true
            WHERE student.tenant_id = :tenant_id AND student.id = :student_id
        """

    def _map_work_item(
        self,
        item: Mapping[str, object],
        logs: list[dict[str, object]],
    ) -> dict[str, object]:
        assignee = None
        if all(
            item.get(key) is not None
            for key in (
                "assignee_id",
                "assignee_name",
                "assignee_email",
                "assignee_component",
            )
        ):
            assignee = {
                "id": str(item["assignee_id"]),
                "name": str(item["assignee_name"]),
                "email": str(item["assignee_email"]),
                "component": str(item["assignee_component"]),
            }
        source = None
        if item.get("source_type") is not None and item.get("source_id") is not None:
            source = {"type": str(item["source_type"]), "id": str(item["source_id"])}
        history = [
            {
                "id": str(log["id"]),
                "action": str(log["action"]),
                "message": str(log["message"]),
                "actorName": str(log["actor_name"]),
                "occurredAt": _iso_timestamp(log["occurred_at"]),
            }
            for log in logs
            if str(log["work_item_id"]) == str(item["id"])
        ]
        return {
            "id": str(item["id"]),
            "key": str(item["key"]),
            "title": str(item["title"]),
            "description": str(item["description"]),
            "status": str(item["status"]),
            "priority": str(item["priority"]),
            "type": str(item["work_type"]),
            "component": str(item["component"]),
            "dueAt": (_iso_timestamp(item["due_at"]) if item.get("due_at") is not None else None),
            "escalated": bool(item["escalated"]),
            "version": int(cast(int, item["version"])),
            "createdAt": _iso_timestamp(item["created_at"]),
            "updatedAt": _iso_timestamp(item["updated_at"]),
            "assignee": assignee,
            "student": {
                "id": str(item["student_id"]),
                "name": f"{item['first_name']} {item['last_name']}",
                "preferredName": str(item["preferred_name"]),
                "programName": str(item["program_name"]),
                "classYear": int(cast(int, item["class_year"])),
            },
            "source": source,
            "history": history,
        }

    async def _require_work_item(
        self,
        auth: AuthContext,
        work_item_id: str,
    ) -> dict[str, object]:
        center = await self.get_action_center(auth)
        items = center["items"]
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("id") == work_item_id:
                    return item
        raise NotFoundError("STAFF_WORK_ITEM_NOT_FOUND", "The work item was not found")

    async def _lock_work_item(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        work_item_id: str,
    ) -> dict[str, object]:
        result = await connection.execute(
            text(
                f"""
                SELECT id, student_id, status, assignee_id, escalated, version,
                       source_type, source_id
                FROM {self._table("staff_work_item")}
                WHERE tenant_id = :tenant_id AND id = :work_item_id
                FOR UPDATE
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "work_item_id": _uuid(work_item_id),
            },
        )
        item = result.mappings().first()
        if item is None:
            raise NotFoundError("STAFF_WORK_ITEM_NOT_FOUND", "The work item was not found")
        return dict(item)

    async def _lock_inquiry(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        inquiry_id: str,
    ) -> dict[str, object] | None:
        result = await connection.execute(
            text(
                f"""
                SELECT inquiry.id, inquiry.student_id, inquiry.topic_code,
                       inquiry.subject, inquiry.message, inquiry.status,
                       inquiry.priority, inquiry.assignee_id, inquiry.version,
                       inquiry.created_at, inquiry.updated_at, student.class_year,
                       person.first_name, person.last_name,
                       COALESCE(profile.preferred_name, person.preferred_name,
                                person.first_name) AS preferred_name,
                       COALESCE(latest_program.name, 'Program not assigned') AS program_name
                FROM {self._table("student_inquiry")} AS inquiry
                JOIN {self._table("student")} AS student
                  ON student.id = inquiry.student_id
                 AND student.tenant_id = inquiry.tenant_id
                JOIN {self._table("person")} AS person
                  ON person.id = student.person_id
                 AND person.tenant_id = student.tenant_id
                LEFT JOIN {self._table("student_profile")} AS profile
                  ON profile.student_id = student.id
                 AND profile.tenant_id = student.tenant_id
                LEFT JOIN LATERAL (
                  SELECT program.name
                  FROM {self._table("admission_offer")} AS offer
                  JOIN {self._table("program")} AS program
                    ON program.id = offer.program_id
                   AND program.tenant_id = offer.tenant_id
                  WHERE offer.tenant_id = inquiry.tenant_id
                    AND offer.student_id = inquiry.student_id
                  ORDER BY offer.created_at DESC, offer.id DESC
                  LIMIT 1
                ) AS latest_program ON true
                WHERE inquiry.tenant_id = :tenant_id
                  AND inquiry.id = :inquiry_id
                FOR UPDATE OF inquiry
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "inquiry_id": _uuid(inquiry_id),
            },
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _active_staff_summary(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        staff_id: str,
    ) -> dict[str, object] | None:
        result = await connection.execute(
            text(
                f"""
                SELECT id, display_name, email_normalized, component
                FROM {self._table("staff_member")}
                WHERE tenant_id = :tenant_id
                  AND id = :staff_id
                  AND active = true
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "staff_id": _uuid(staff_id),
            },
        )
        row = result.mappings().first()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "name": str(row["display_name"]),
            "email": str(row["email_normalized"]),
            "component": str(row["component"]),
        }

    async def _staff_name(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        staff_id: str,
    ) -> str:
        result = await connection.execute(
            text(
                f"""
                SELECT display_name
                FROM {self._table("staff_member")}
                WHERE tenant_id = :tenant_id
                  AND id = :staff_id
                  AND active = true
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id), "staff_id": _uuid(staff_id)},
        )
        member = result.mappings().first()
        if member is None:
            raise ApiError(
                403,
                "STAFF_IDENTITY_NOT_CONFIGURED",
                "The staff identity is not configured for this university",
            )
        return str(member["display_name"])

    async def _insert_work_log(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        work_item_id: str,
        actor_name: str,
        action: str,
        message: str,
        actor_type: str = "staff",
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("staff_work_log")} (
                  id, tenant_id, work_item_id, actor_type, actor_id,
                  actor_name, action, message, occurred_at
                )
                VALUES (
                  :id, :tenant_id, :work_item_id, :actor_type, :actor_id,
                  :actor_name, :action, :message, NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "work_item_id": _uuid(work_item_id),
                "actor_type": actor_type,
                "actor_id": _uuid(auth.actor_id) if actor_type == "staff" else None,
                "actor_name": actor_name,
                "action": action,
                "message": message,
            },
        )

    async def _insert_student_message(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        student_id: str,
        subject: str,
        body: str,
        kind: str = "general",
        href: str | None = None,
    ) -> dict[str, object]:
        message_id = self._uuid_factory()
        result = await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("student_message")} (
                  id, tenant_id, student_id, subject, body, sender_name,
                  kind, href, sent_at, read_at, created_at
                )
                VALUES (
                  :id, :tenant_id, :student_id, :subject, :body,
                  'Enrollment Team', :kind, :href, NOW(), NULL, NOW()
                )
                RETURNING sent_at
                """
            ),
            {
                "id": message_id,
                "tenant_id": _uuid(auth.tenant_id),
                "student_id": _uuid(student_id),
                "subject": subject,
                "body": body,
                "kind": kind,
                "href": href,
            },
        )
        row = result.mappings().first()
        if row is None:
            raise ApiError(
                500,
                "STUDENT_NOTIFICATION_FAILED",
                "The student notification could not be created",
            )
        return {
            "id": str(message_id),
            "subject": subject,
            "body": body,
            "senderName": "Enrollment Team",
            "kind": kind,
            "href": href,
            "sentAt": _iso_timestamp(row["sent_at"]),
            "readAt": None,
        }

    async def _award_requirement_rewards(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        student_id: str,
        requirement_id: str,
    ) -> int:
        definition_result = await connection.execute(
            text(
                f"""
                SELECT definition.code
                FROM {self._table("student_requirement")} AS requirement
                JOIN {self._table("requirement_definition_version")} AS definition
                  ON definition.id = requirement.requirement_definition_version_id
                 AND definition.tenant_id = requirement.tenant_id
                WHERE requirement.tenant_id = :tenant_id
                  AND requirement.id = :requirement_id
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "requirement_id": _uuid(requirement_id),
            },
        )
        definition = definition_result.mappings().first()
        if definition is None:
            return 0
        trigger_key = str(definition["code"])
        properties: dict[str, object] = {}
        rule_result = await connection.execute(
            text(
                f"""
                SELECT id, points, max_awards_per_student
                FROM {self._table("tenant_reward_rule")}
                WHERE tenant_id = :tenant_id
                  AND trigger_type = 'requirement_completed'
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
                "trigger_key": trigger_key,
                "properties": json.dumps(properties, separators=(",", ":")),
            },
        )
        awarded = 0
        for rule in rule_result.mappings().all():
            inserted = await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("student_reward_ledger")} (
                      id, tenant_id, student_id, reward_rule_id, source_type,
                      source_key, points, metadata, awarded_at
                    )
                    SELECT :id, :tenant_id, :student_id, :rule_id,
                      'requirement_completed', :source_key, :points,
                      CAST(:metadata AS jsonb), NOW()
                    WHERE (
                      SELECT COUNT(*)
                      FROM {self._table("student_reward_ledger")} AS existing
                      WHERE existing.tenant_id = :tenant_id
                        AND existing.student_id = :student_id
                        AND existing.reward_rule_id = :rule_id
                    ) < :maximum
                    ON CONFLICT (
                      tenant_id, student_id, reward_rule_id, source_key
                    ) DO NOTHING
                    RETURNING points
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                    "rule_id": rule["id"],
                    "source_key": requirement_id,
                    "points": int(rule["points"]),
                    "metadata": json.dumps(
                        {"triggerKey": trigger_key, "properties": properties},
                        separators=(",", ":"),
                    ),
                    "maximum": int(rule["max_awards_per_student"]),
                },
            )
            awarded += sum(int(row["points"]) for row in inserted.mappings().all())
        return awarded

    async def _refresh_requirement_dependencies(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        completed_requirement_id: str,
    ) -> None:
        requirement = self._table("student_requirement")
        definition = self._table("requirement_definition_version")
        await connection.execute(
            text(
                f"""
                WITH completed_journey AS (
                  SELECT journey_id
                  FROM {requirement}
                  WHERE tenant_id = :tenant_id AND id = :completed_requirement_id
                )
                UPDATE {requirement} AS candidate
                SET status = 'ready', version = version + 1, updated_at = NOW()
                FROM {definition} AS definition
                WHERE candidate.tenant_id = :tenant_id
                  AND candidate.journey_id = (SELECT journey_id FROM completed_journey)
                  AND candidate.status = 'blocked'
                  AND definition.id = candidate.requirement_definition_version_id
                  AND definition.tenant_id = candidate.tenant_id
                  AND NOT EXISTS (
                    SELECT 1
                    FROM unnest(definition.depends_on_codes) AS dependency_code
                    WHERE NOT EXISTS (
                      SELECT 1
                      FROM {requirement} AS dependency
                      JOIN {definition} AS dependency_definition
                        ON dependency_definition.id =
                             dependency.requirement_definition_version_id
                       AND dependency_definition.tenant_id = dependency.tenant_id
                      WHERE dependency.tenant_id = candidate.tenant_id
                        AND dependency.journey_id = candidate.journey_id
                        AND dependency_definition.code = dependency_code
                        AND dependency.status IN (
                          'completed', 'waived', 'not_applicable'
                        )
                    )
                  )
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "completed_requirement_id": _uuid(completed_requirement_id),
            },
        )
        journey = self._table("enrollment_journey")
        journey_definition = self._table("journey_definition_version")
        onboarding = self._table("student_onboarding")
        completed = await connection.execute(
            text(
                f"""
                UPDATE {journey} AS journey
                SET status='completed', version=journey.version+1, updated_at=NOW()
                WHERE journey.tenant_id=:tenant_id
                  AND journey.id=(
                    SELECT journey_id FROM {requirement}
                    WHERE tenant_id=:tenant_id AND id=:completed_requirement_id
                  )
                  AND journey.status NOT IN ('completed','cancelled')
                  AND (
                    EXISTS (
                      SELECT 1 FROM {journey_definition} AS journey_definition
                      WHERE journey_definition.id=journey.journey_definition_version_id
                        AND journey_definition.tenant_id=journey.tenant_id
                        AND NOT journey_definition.onboarding_required
                    )
                    OR EXISTS (
                      SELECT 1 FROM {onboarding} AS onboarding
                      WHERE onboarding.tenant_id=journey.tenant_id
                        AND onboarding.student_id=journey.student_id
                        AND onboarding.status='completed'
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM {requirement} AS pending
                    WHERE pending.tenant_id=journey.tenant_id
                      AND pending.journey_id=journey.id
                      AND pending.retired_at IS NULL
                      AND pending.status NOT IN (
                        'not_applicable','completed','waived','expired'
                      )
                  )
                RETURNING journey.student_id
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "completed_requirement_id": _uuid(completed_requirement_id),
            },
        )
        completed_row = completed.mappings().first()
        if completed_row is not None:
            await self._insert_student_message(
                connection,
                auth=auth,
                student_id=str(completed_row["student_id"]),
                subject="Enrollment complete",
                body="All required enrollment tasks are complete.",
                kind="enrollment_completed",
                href="/dashboard",
            )

    async def _insert_audit(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        request_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata: Mapping[str, object],
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("audit_event")} (
                  id, tenant_id, actor_type, actor_id, student_id, action,
                  resource_type, resource_id, authorization_basis, request_id,
                  correlation_id, metadata, occurred_at, created_at
                )
                VALUES (
                  :id, :tenant_id, 'staff', :actor_id, NULL, :action,
                  :resource_type, :resource_id, 'staff_enrollment_operations',
                  :request_id, :request_id, CAST(:metadata AS jsonb), NOW(), NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "actor_id": _uuid(auth.actor_id),
                "action": action,
                "resource_type": resource_type,
                "resource_id": _uuid(resource_id),
                "request_id": request_id,
                "metadata": json.dumps(dict(metadata), separators=(",", ":")),
            },
        )

    async def _insert_outbox(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        request_id: str,
        event_name: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        data: Mapping[str, object],
    ) -> None:
        event_id = self._uuid_factory()
        occurred_at = self._clock()
        payload = {
            "eventId": str(event_id),
            "eventName": event_name,
            "occurredAt": _iso_timestamp(occurred_at),
            "tenantId": auth.tenant_id,
            "aggregateType": aggregate_type,
            "aggregateId": aggregate_id,
            "aggregateVersion": aggregate_version,
            "actor": {"type": "staff", "id": auth.actor_id},
            "correlationId": request_id,
            "causationId": str(event_id),
            "data": dict(data),
        }
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("outbox_event")} (
                  id, tenant_id, event_name, aggregate_type, aggregate_id,
                  aggregate_version, occurred_at, actor_type, actor_id,
                  correlation_id, causation_id, payload, created_at
                )
                VALUES (
                  :id, :tenant_id, :event_name, :aggregate_type, :aggregate_id,
                  :aggregate_version, :occurred_at, 'staff', :actor_id,
                  :correlation_id, :causation_id, CAST(:payload AS jsonb),
                  :occurred_at
                )
                """
            ),
            {
                "id": event_id,
                "tenant_id": _uuid(auth.tenant_id),
                "event_name": event_name,
                "aggregate_type": aggregate_type,
                "aggregate_id": _uuid(aggregate_id),
                "aggregate_version": aggregate_version,
                "occurred_at": occurred_at,
                "actor_id": _uuid(auth.actor_id),
                "correlation_id": request_id,
                "causation_id": str(event_id),
                "payload": json.dumps(payload, separators=(",", ":")),
            },
        )

    @staticmethod
    def _require_staff(auth: AuthContext) -> None:
        if auth.actor_type != "staff":
            raise ApiError(
                403,
                "STAFF_ACCESS_REQUIRED",
                "This route requires a staff identity",
            )


def _document_route(category: str) -> tuple[str, str]:
    if category == "financial_aid":
        return "Financial Aid", "urgent"
    if category == "health":
        return "Student Health", "high"
    return "Registrar", "high"


def _map_staff_inquiry(
    inquiry: Mapping[str, object],
    *,
    status: str,
    assignee: Mapping[str, object] | None,
    version: int,
    updated_at: object,
) -> dict[str, object]:
    return {
        "id": str(inquiry["id"]),
        "student": {
            "id": str(inquiry["student_id"]),
            "name": f"{inquiry['first_name']} {inquiry['last_name']}",
            "preferredName": str(inquiry["preferred_name"]),
            "programName": str(inquiry["program_name"]),
            "classYear": int(cast(int, inquiry["class_year"])),
        },
        "topicCode": str(inquiry["topic_code"]),
        "subject": str(inquiry["subject"]),
        "message": str(inquiry["message"]),
        "status": status,
        "priority": str(inquiry["priority"]),
        "assignee": dict(assignee) if assignee is not None else None,
        "createdAt": _iso_timestamp(inquiry["created_at"]),
        "updatedAt": _iso_timestamp(updated_at),
        "version": version,
    }


def _iso_timestamp(value: object) -> str:
    if isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise RuntimeError("Database returned an invalid timestamp") from error
    elif isinstance(value, datetime):
        timestamp = value
    else:
        raise RuntimeError("Database returned an invalid timestamp")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read(
    values: Mapping[str, object],
    *keys: str,
    default: object = _NO_DEFAULT,
) -> object:
    for key in keys:
        if key in values:
            return values[key]
    if default is not _NO_DEFAULT:
        return default
    raise BadRequestError("VALIDATION_ERROR", f"Missing field {keys[0]}")


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BadRequestError("VALIDATION_ERROR", f"{field} must be a positive integer")
    return value


def _database_integer(value: object, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise RuntimeError(f"Database returned an invalid integer for {field}")


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _json_object(value: object, field: str) -> dict[str, object]:
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Database returned invalid JSON for {field}") from error
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Database returned invalid JSON for {field}")
    return {str(key): item for key, item in parsed.items()}


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise BadRequestError("VALIDATION_ERROR", "A UUID identifier is invalid") from error


def _optional_uuid_string(value: object) -> str | None:
    return None if value is None else str(value)
