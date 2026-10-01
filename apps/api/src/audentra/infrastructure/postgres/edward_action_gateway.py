"""Deterministic policy, intent, confirmation, execution and receipts for Edward.

The assistant pipelines can ask this gateway to propose an action, but only
this code can bind an actor/tenant, read canonical state, authorize, persist a
reviewable effect, consume confirmation, call domain mutations, and issue a
receipt.  No model output is accepted as an ID or an authorization decision.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError
from audentra.domain.edward_action_catalog import CAPABILITY_BY_ACTION
from audentra.domain.edward_actions import SemanticActionRequest, canonical_digest
from audentra.domain.student_cohort import CohortFilter, build_cohort_filter
from audentra.integrations.assistant.trace import get_assistant_trace_recorder

from .portal_repository import PostgresPortalRepository
from .staff_assistant_repository import PostgresStaffAssistantRepository
from .staff_repository import PostgresStaffRepository

JsonDict = dict[str, Any]
LOGGER = logging.getLogger(__name__)

INTENT_TTL = timedelta(minutes=15)
EXECUTION_LEASE = timedelta(minutes=2)
MAXIMUM_COHORT_ACTION_SIZE = 25
# Requirements nothing further can happen to; a follow-up "about" one of these
# is about something else.
_SETTLED_REQUIREMENT_STATUSES = {"completed", "waived", "not_applicable", "expired", "cancelled"}
_ACTIONABLE_REQUIREMENT_STATUSES = {
    # Relational lifecycle plus the compatible memory/eval vocabulary.
    "ready",
    "help_requested",
    "in_progress",
    "rejected",
    "not_started",
    "action_required",
}

#: The action catalogue owns this mapping now, so a new action cannot be added
#: with a capability the responder and the gateway disagree about.
_CAPABILITY = CAPABILITY_BY_ACTION


class StaffEmailActions(Protocol):
    async def list_mailboxes(self, auth: AuthContext) -> dict[str, object]: ...

    async def create_send_intent(
        self, auth: AuthContext, payload: Mapping[str, object]
    ) -> dict[str, object]: ...


class EdwardActionGateway:
    """One closed gateway shared by Student and Staff Edward."""

    def __init__(
        self,
        engine: AsyncEngine,
        portal: PostgresPortalRepository,
        staff: PostgresStaffRepository,
        staff_assistant: PostgresStaffAssistantRepository,
        *,
        staff_email: StaffEmailActions | None = None,
        clock: Any | None = None,
    ) -> None:
        self._engine = engine
        self._portal = portal
        self._staff = staff
        self._staff_assistant = staff_assistant
        self._staff_email = staff_email
        self._clock = clock or (lambda: datetime.now(UTC))

    async def propose_student(
        self,
        auth: AuthContext,
        request: SemanticActionRequest,
        *,
        conversation_id: str,
        trace_id: str,
        page_path: str | None = None,
    ) -> JsonDict:
        if auth.actor_type != "student":
            raise ApiError(403, "EDWARD_STUDENT_ACTION_FORBIDDEN", "Student identity required")
        if request.action == "student.preferences.update":
            proposal = await self._student_preferences(auth, request)
        elif request.action == "student.support.contact":
            proposal = await self._student_support(auth, request, page_path=page_path)
        elif request.action == "student.requirement.submit_response":
            proposal = await self._student_requirement(auth, request, page_path=page_path)
        else:
            raise BadRequestError("EDWARD_ACTION_UNSUPPORTED", "This student action is unavailable")
        return await self._create_intent(
            auth,
            proposal,
            conversation_id=conversation_id,
            trace_id=trace_id,
            request_payload=request.fields,
        )

    async def propose_staff(
        self,
        auth: AuthContext,
        request: SemanticActionRequest,
        *,
        conversation_id: str,
        trace_id: str,
        resolved_student_id: str | None,
        cohort_filter: Mapping[str, Any] | None = None,
        draft: Mapping[str, Any] | None = None,
        work_item_key: str | None = None,
    ) -> JsonDict:
        self._require_staff(auth)
        capability = _CAPABILITY[request.action]
        await self._require_capability(auth, "edward.act")
        await self._require_capability(auth, capability)
        if request.action == "operations.follow_up.create":
            proposal = await self._single_follow_up(auth, request, resolved_student_id)
        elif request.action == "operations.cohort.create_follow_ups":
            proposal = await self._cohort_follow_ups(auth, request, cohort_filter)
        elif request.action == "operations.work_item.update":
            proposal = await self._work_item_update(auth, request, work_item_key)
        elif request.action == "communications.email.prepare":
            proposal = await self._email_prepare(auth, resolved_student_id, draft)
        else:
            raise BadRequestError("EDWARD_ACTION_UNSUPPORTED", "This staff action is unavailable")
        proposal["authorizationCapability"] = capability
        return await self._create_intent(
            auth,
            proposal,
            conversation_id=conversation_id,
            trace_id=trace_id,
            request_payload=request.fields,
        )

    async def get_intent(self, auth: AuthContext, intent_id: str) -> JsonDict:
        row = await self._intent_row(auth, intent_id)
        if row is None:
            raise NotFoundError(
                "EDWARD_ACTION_INTENT_NOT_FOUND", "The action preview was not found"
            )
        return await self._public_intent(row)

    async def recent_work_item_key(self, auth: AuthContext, conversation_id: str) -> str | None:
        """Resolve "that task" only from this actor's committed single-create receipt."""

        self._require_staff(auth)
        async with self._engine.connect() as connection:
            value = await connection.scalar(
                text(
                    """SELECT receipt.result->>'key'
                       FROM agent_action_intent intent
                       JOIN agent_action_receipt receipt
                         ON receipt.tenant_id=intent.tenant_id
                        AND receipt.action_intent_id=intent.id
                       WHERE intent.tenant_id=:tenant_id
                         AND intent.staff_member_id=:staff_id
                         AND intent.staff_conversation_id=:conversation_id
                         AND intent.action_type='operations.follow_up.create'
                         AND receipt.status='succeeded'
                         AND receipt.result ? 'key'
                       ORDER BY receipt.committed_at DESC, receipt.id DESC
                       LIMIT 1"""
                ),
                {
                    "tenant_id": UUID(auth.tenant_id),
                    "staff_id": UUID(auth.actor_id),
                    "conversation_id": UUID(conversation_id),
                },
            )
        return str(value) if value else None

    async def conversation_actions(
        self,
        auth: AuthContext,
        conversation_id: str,
        *,
        limit: int = 6,
        history_after: str | None = None,
    ) -> JsonDict:
        """This actor's recent action intents and receipts in one conversation.

        A conversation that has changed something has to be able to answer
        questions about it — "did that go through?", "what did you just do?",
        "undo that". The assistant message rows record what was *proposed*;
        only `agent_action_receipt` records what was *committed*, and a receipt
        is the one artefact allowed to authorise a success claim. Reading them
        here keeps that rule intact across turns instead of only within one.
        """

        actor_column = (
            "intent.student_actor_id" if auth.actor_type == "student" else "intent.staff_member_id"
        )
        conversation_column = (
            "intent.student_conversation_id"
            if auth.actor_type == "student"
            else "intent.staff_conversation_id"
        )
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(
                            f"""SELECT intent.id, intent.action_type, intent.status,
                                       intent.target_student_id, intent.created_at,
                                       intent.request_payload,
                                       receipt.status AS receipt_status,
                                       receipt.result AS receipt_result,
                                       receipt.target AS receipt_target,
                                       receipt.affected_count
                                FROM agent_action_intent intent
                                LEFT JOIN agent_action_receipt receipt
                                  ON receipt.tenant_id=intent.tenant_id
                                 AND receipt.action_intent_id=intent.id
                                WHERE intent.tenant_id=:tenant_id
                                  AND {actor_column}=:actor_id
                                  AND {conversation_column}=:conversation_id
                                  AND (CAST(:history_after AS text) IS NULL OR EXISTS (
                                    SELECT 1 FROM staff_assistant_message message
                                    WHERE message.tenant_id=:tenant_id
                                      AND message.staff_member_id=:actor_id
                                      AND message.conversation_id=:conversation_id
                                      AND message.action_intents @> jsonb_build_array(
                                        jsonb_build_object('id', intent.id::text))
                                      AND message.created_at >= (
                                        SELECT anchor.created_at FROM staff_assistant_message anchor
                                        WHERE anchor.tenant_id=:tenant_id
                                          AND anchor.staff_member_id=:actor_id
                                          AND anchor.conversation_id=:conversation_id
                                          AND anchor.client_message_id=:history_after
                                          AND anchor.role='user' LIMIT 1
                                      )
                                  ))
                                ORDER BY intent.created_at DESC
                                LIMIT :limit"""  # noqa: S608 -- both columns are literals above
                        ),
                        {
                            "tenant_id": UUID(auth.tenant_id),
                            # A student intent is bound to the student, not to
                            # the person behind the account: `auth.actor_id` is
                            # the person id for a student session, which is why
                            # this join has to pick the column deliberately.
                            "actor_id": UUID(
                                auth.student_id if auth.actor_type == "student" else auth.actor_id
                            ),
                            "conversation_id": UUID(conversation_id),
                            "limit": max(1, min(limit, 20)),
                            "history_after": history_after,
                        },
                    )
                )
                .mappings()
                .all()
            )
        receipts: list[JsonDict] = []
        pending: list[JsonDict] = []
        for row in reversed(list(rows)):
            if row["receipt_status"] is not None:
                receipts.append(
                    {
                        "intentId": str(row["id"]),
                        "action": str(row["action_type"]),
                        "status": str(row["receipt_status"]),
                        "result": _json_object(row["receipt_result"]),
                        "target": _json_object(row["receipt_target"]),
                        "affectedCount": int(row["affected_count"] or 0),
                    }
                )
            elif str(row["status"]) == "pending_confirmation":
                pending.append(
                    {
                        "intentId": str(row["id"]),
                        "action": str(row["action_type"]),
                        "targetStudentId": (
                            str(row["target_student_id"]) if row["target_student_id"] else None
                        ),
                        # The semantic fields the proposal was built from, so an
                        # amendment can start from what is on the table instead
                        # of from nothing — "make it urgent" then "and due
                        # thursday" must end urgent AND due thursday.
                        "fields": _json_object(row["request_payload"]),
                    }
                )
        return {"receipts": receipts, "pending": pending}

    async def cancel(self, auth: AuthContext, intent_id: str, expected_version: int) -> JsonDict:
        async with self._engine.begin() as connection:
            row = await self._intent_row(auth, intent_id, connection=connection, lock=True)
            if row is None:
                raise NotFoundError(
                    "EDWARD_ACTION_INTENT_NOT_FOUND", "The action preview was not found"
                )
            if int(row["version"]) != expected_version:
                raise ConflictError("VERSION_CONFLICT", "The action preview changed")
            if str(row["status"]) != "pending_confirmation":
                raise ConflictError(
                    "EDWARD_ACTION_INTENT_NOT_CANCELLABLE", "This action can no longer be cancelled"
                )
            await connection.execute(
                text(
                    """
                    UPDATE agent_action_intent
                    SET status='cancelled', version=version+1, completed_at=NOW(), updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:id
                    """
                ),
                {"tenant_id": UUID(auth.tenant_id), "id": UUID(intent_id)},
            )
        await self._publish_action_trace(row, {"actionExecutionResult": "cancelled"})
        return {"id": intent_id, "status": "cancelled", "version": expected_version + 1}

    async def confirm(
        self,
        auth: AuthContext,
        intent_id: str,
        *,
        expected_version: int,
        content_sha256: str,
        request_id: str,
    ) -> JsonDict:
        """Consume a same-actor, unmodified, unexpired preview exactly once."""

        recovering = False
        expired = False
        async with self._engine.begin() as connection:
            row = await self._intent_row(auth, intent_id, connection=connection, lock=True)
            if row is None:
                raise NotFoundError(
                    "EDWARD_ACTION_INTENT_NOT_FOUND", "The action preview was not found"
                )
            existing = await self._receipt_row(connection, auth.tenant_id, intent_id)
            if existing is not None:
                return _public_receipt(existing)
            if int(row["version"]) != expected_version:
                raise ConflictError("VERSION_CONFLICT", "The action preview changed")
            server_digest = canonical_digest(
                {
                    "action": str(row["action_type"]),
                    "resolved": _json_object(row["resolved_payload"]),
                    "preview": _json_object(row["preview"]),
                    "scopeSnapshot": _json_object(row["scope_snapshot"]),
                    "expiresAt": cast(datetime, row["expires_at"]).isoformat(),
                }
            )
            if server_digest != str(row["content_sha256"]):
                raise ConflictError(
                    "EDWARD_ACTION_INTENT_CHANGED",
                    "The stored action no longer matches the reviewed preview",
                )
            if str(row["content_sha256"]) != content_sha256:
                raise ConflictError(
                    "EDWARD_ACTION_INTENT_CHANGED", "The reviewed action content changed"
                )
            status = str(row["status"])
            if status == "executing":
                started_at = cast(datetime | None, row.get("execution_started_at"))
                if started_at is None or started_at > self._clock() - EXECUTION_LEASE:
                    raise ConflictError(
                        "EDWARD_ACTION_IN_PROGRESS", "This action is already being executed"
                    )
                recovering = True
            elif status != "pending_confirmation":
                raise ConflictError(
                    "EDWARD_ACTION_INTENT_NOT_CONFIRMABLE", "This action cannot be confirmed"
                )
            elif cast(datetime, row["expires_at"]) <= self._clock():
                await connection.execute(
                    text(
                        """UPDATE agent_action_intent
                           SET status='expired', version=version+1, updated_at=NOW()
                           WHERE tenant_id=:tenant_id AND id=:id"""
                    ),
                    {"tenant_id": UUID(auth.tenant_id), "id": UUID(intent_id)},
                )
                expired = True
            if not expired:
                if auth.actor_type == "staff":
                    capability = str(row.get("authorization_capability") or "")
                    await self._require_capability(auth, "edward.act", connection=connection)
                    await self._require_capability(auth, capability, connection=connection)
                    await self._recheck_staff_scope(auth, row, connection)
                await connection.execute(
                    text(
                        """
                        UPDATE agent_action_intent
                        SET status='executing', confirmed_at=COALESCE(confirmed_at, NOW()),
                            execution_started_at=NOW(),
                            updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND id=:id
                        """
                    ),
                    {"tenant_id": UUID(auth.tenant_id), "id": UUID(intent_id)},
                )

        if expired:
            await self._publish_action_trace(row, {"actionExecutionResult": "expired"})
            raise ConflictError("EDWARD_ACTION_INTENT_EXPIRED", "The action preview expired")

        try:
            if not recovering:
                # Run the canonical-state check after the durable transition to
                # executing. A stale preview must become a terminal failed
                # intent with a receipt, not silently roll back to a preview
                # that can never succeed.
                await self._recheck_canonical_snapshot(auth, row)
            if recovering:
                recovered = await self._recover_applied_state(auth, row)
                if recovered is not None:
                    result, affected = recovered
                else:
                    result, affected = await self._execute(auth, row, request_id)
            else:
                result, affected = await self._execute(auth, row, request_id)
        except Exception as error:
            if isinstance(error, ApiError):
                await self._record_failure(auth, row, error)
            else:
                await self._record_unknown_execution_error(auth, row, error)
            raise
        return await self._record_success(auth, row, result, affected, request_id)

    # ------------------------------------------------------------------ proposal builders

    async def _student_preferences(
        self, auth: AuthContext, request: SemanticActionRequest
    ) -> JsonDict:
        profile = await self._portal.get_student_profile(auth)
        allowed = {
            "preferredName",
            "pronouns",
            "mobilePhone",
            "communicationPreference",
        }
        if set(request.fields) - allowed or not request.fields:
            raise BadRequestError(
                "EDWARD_PREFERENCE_FIELDS_INVALID", "Choose a supported preference"
            )
        changes = []
        unchanged: list[str] = []
        resolved: JsonDict = {"expectedVersion": profile["version"]}
        for key, after in request.fields.items():
            before = profile.get(key)
            if before != after:
                changes.append({"field": key, "before": before, "after": after})
                resolved[key] = after
            else:
                # Half a request dropped without a word is the failure a
                # confirmation card cannot catch: the card shows what was kept.
                unchanged.append(f"{_PREFERENCE_LABELS.get(key, key)} is already {after}")
        if not changes:
            raise ConflictError(
                "EDWARD_ACTION_NO_CHANGE",
                f"{'; '.join(unchanged).capitalize()}, so there is nothing to change."
                if unchanged
                else "That preference already has this value",
            )
        return {
            "action": request.action,
            "targetStudentId": auth.student_id,
            "targetResourceType": "student_profile",
            "targetResourceId": auth.student_id,
            "resolved": resolved,
            "preview": {
                "title": "Update your preferences",
                "summary": "Review the exact profile changes below.",
                "changes": changes,
                "warnings": [
                    f"{note.capitalize()}, so it is not part of this change." for note in unchanged
                ],
            },
            "provenance": [_source("canonical_database", "student_profile", trusted=True)],
            "scopeSnapshot": {"profileVersion": profile["version"]},
            "riskClass": 1,
            "confirmationMode": "confirm",
        }

    async def _student_support(
        self,
        auth: AuthContext,
        request: SemanticActionRequest,
        *,
        page_path: str | None,
    ) -> JsonDict:
        message = str(request.fields.get("message") or "").strip()
        if not message or len(message) > 500:
            raise BadRequestError(
                "EDWARD_SUPPORT_MESSAGE_INVALID", "The support message must be 1-500 characters"
            )
        requirements = cast(
            list[JsonDict], (await self._portal.get_student_requirements(auth))["items"]
        )
        requirement = _match_requirement(message, page_path, requirements, require_unique=False)
        topic = _support_topic(message, page_path)
        resolved: JsonDict = {"topicCode": topic, "message": message}
        if requirement is not None:
            resolved["requirementId"] = requirement["id"]
        return {
            "action": request.action,
            "targetStudentId": auth.student_id,
            "targetResourceType": "student_requirement" if requirement else "student_support",
            "targetResourceId": requirement.get("id") if requirement else None,
            "resolved": resolved,
            "preview": {
                "title": "Contact student support",
                "summary": (
                    "Edward will open an internal support request. This does not send email."
                ),
                "topic": topic,
                "requirement": _requirement_preview(requirement),
                "message": message,
                # Who picks it up is the first thing a student wants to know,
                # and it is the difference between "a request was created" and
                # "your adviser will see this". It also answers the read half
                # of "who is my adviser and can you ask them to call me?".
                "routesTo": await self._support_destination(auth, requirement),
            },
            "provenance": [
                _source("user_authored", "current_message", trusted=False),
                _source("canonical_database", "student_requirements", trusted=True),
            ],
            "scopeSnapshot": {
                "requirementVersion": requirement.get("version") if requirement else None
            },
            "riskClass": 2,
            "confirmationMode": "confirm",
        }

    async def _student_requirement(
        self,
        auth: AuthContext,
        request: SemanticActionRequest,
        *,
        page_path: str | None,
    ) -> JsonDict:
        requirements = cast(
            list[JsonDict], (await self._portal.get_student_requirements(auth))["items"]
        )
        # The step the student named. V1 matched on an empty string, so "I've
        # finished the immunization requirement" was resolved as if the student
        # had named nothing and came back "more than one step matches that" —
        # about a message that named exactly one.
        named = str(request.fields.get("requirement") or "").strip()
        requirement = _match_requirement(named, page_path, requirements, require_unique=True)
        if requirement is None:
            raise ConflictError(
                "EDWARD_REQUIREMENT_AMBIGUOUS",
                (
                    f"I can't tell which step “{named}” is — name it the way it appears "
                    "on your checklist and I'll record it against the right one."
                    if named
                    else "Name the step you finished so I don't record the wrong one."
                ),
            )
        interaction = str(requirement.get("interactionType") or "")
        if interaction != "information":
            raise ConflictError(
                "EDWARD_REQUIREMENT_RESPONSE_NEEDS_FIELDS",
                (
                    f"“{requirement['title']}” isn't something I can record on your word "
                    "alone — it needs its own form or an uploaded document. Open it from "
                    "your checklist and I'll show you what it's waiting on."
                ),
            )
        response = {"acknowledged": True}
        return {
            "action": request.action,
            "targetStudentId": auth.student_id,
            "targetResourceType": "student_requirement",
            "targetResourceId": requirement["id"],
            "resolved": {
                "requirementId": requirement["id"],
                "expectedVersion": requirement["version"],
                "response": response,
            },
            "preview": {
                "title": f"Complete {requirement['title']}",
                "summary": "Edward will record your acknowledgement for this requirement.",
                "requirement": _requirement_preview(requirement),
                "response": response,
            },
            "provenance": [
                _source("canonical_database", "student_requirement", trusted=True),
                _source("user_authored", "current_message", trusted=False),
            ],
            "scopeSnapshot": {"requirementVersion": requirement["version"]},
            "riskClass": 2,
            "confirmationMode": "confirm",
        }

    async def _single_follow_up(
        self, auth: AuthContext, request: SemanticActionRequest, student_id: str | None
    ) -> JsonDict:
        if student_id is None:
            raise ConflictError(
                "EDWARD_STUDENT_REQUIRED", "Name exactly one student for this follow-up"
            )
        await self._require_student_scope(auth, student_id)
        subject = str(request.fields.get("subject") or "").strip() or None
        student, requirement, member = await self._follow_up_context(
            auth, student_id, subject=subject
        )
        # A follow-up "about her financial aid verification" that silently
        # becomes one about housing is a different task than the one asked
        # for. When the named subject is not an open requirement, say so on
        # the card rather than substituting quietly.
        warnings: list[str] = []
        if subject and requirement is not None and not _mentions(subject, requirement):
            warnings.append(
                f"Nothing open matches “{subject}”. This targets "
                f"“{requirement.get('title')}”, the most urgent open item."
            )
        elif subject and requirement is None:
            warnings.append(f"Nothing open matches “{subject}”, and no other item is open either.")
        due_at = _due_at(request.fields.get("due"), self._clock())
        resolved = _follow_up_payload(
            auth, student, requirement, member, request.fields, due_at=due_at
        )
        return {
            "action": request.action,
            "targetStudentId": student_id,
            "targetResourceType": "student_requirement" if requirement else "student",
            "targetResourceId": requirement.get("id") if requirement else student_id,
            "resolved": resolved,
            "preview": {
                "title": "Create a student follow-up",
                "summary": (
                    "This creates internal Action Center work; it does not contact the student."
                ),
                "student": {"id": student_id, "name": student.get("name")},
                "requirement": _requirement_preview(requirement),
                "workItem": _work_item_preview(resolved),
                "warnings": warnings,
            },
            "provenance": [
                _source("canonical_database", "student_record", trusted=True),
                _source("canonical_database", "student_requirements", trusted=True),
            ],
            "scopeSnapshot": {
                "studentId": student_id,
                "requirementVersion": requirement.get("version") if requirement else None,
            },
            "riskClass": 2,
            "confirmationMode": "confirm",
        }

    async def _cohort_follow_ups(
        self,
        auth: AuthContext,
        request: SemanticActionRequest,
        raw_filter: Mapping[str, Any] | None,
    ) -> JsonDict:
        await self._require_capability(auth, "edward.student.any")
        if not raw_filter:
            raise ConflictError(
                "EDWARD_COHORT_CONTEXT_REQUIRED",
                "Ask Edward to list or count a specific group before creating follow-ups",
            )
        cohort = build_cohort_filter(raw_filter)
        if cohort.is_unconstrained():
            raise ConflictError(
                "EDWARD_COHORT_UNCONSTRAINED", "A bulk action needs at least one cohort filter"
            )
        result = await self._staff_assistant.find_students(
            auth, cohort, limit=MAXIMUM_COHORT_ACTION_SIZE + 1
        )
        if result.total == 0:
            raise ConflictError("EDWARD_COHORT_EMPTY", "No students match this cohort")
        if result.total > MAXIMUM_COHORT_ACTION_SIZE:
            raise ApiError(
                413,
                "EDWARD_COHORT_LIMIT_EXCEEDED",
                f"This cohort has {result.total} students; V1 is limited to "
                f"{MAXIMUM_COHORT_ACTION_SIZE}",
            )
        member = await self._staff_member(auth)
        ids = sorted(str(item["id"]) for item in result.items)
        due_at = _due_at(request.fields.get("due"), self._clock())
        template = {
            "flowKind": "enrollment",
            "title": "Edward cohort follow-up",
            "description": f"Follow up with student in cohort: {'; '.join(result.filter_clauses)}",
            "component": member["component"],
            "assigneeId": auth.actor_id if request.fields.get("assignToMe") else None,
            "priority": request.fields.get("priority") or "medium",
            "status": "todo",
            "dueAt": due_at.isoformat() if due_at else None,
            "actionType": "enrollment_follow_up",
        }
        raw = _cohort_json(cohort)
        fingerprint = canonical_digest({"studentIds": ids})
        return {
            "action": request.action,
            "targetStudentId": None,
            "targetResourceType": "cohort",
            "targetResourceId": None,
            "resolved": {"cohortFilter": raw, "template": template},
            "preview": {
                "title": f"Create {result.total} student follow-ups",
                "summary": "The cohort is re-evaluated before any work item is created.",
                "cohort": {
                    "filter": raw,
                    "description": result.filter_clauses,
                    "count": result.total,
                    "sample": [
                        {"id": item["id"], "name": item["name"], "program": item["programName"]}
                        for item in result.items[:5]
                    ],
                },
                "workItem": _work_item_preview(template),
                "warnings": [
                    "No email or SMS will be sent.",
                    f"Execution aborts if membership changes from {result.total} students.",
                ],
            },
            "provenance": [_source("canonical_database", "validated_cohort", trusted=True)],
            "scopeSnapshot": {
                "cohortFilter": raw,
                "memberIds": ids,
                "count": result.total,
                "fingerprint": fingerprint,
            },
            "riskClass": 3,
            "confirmationMode": "strong_confirm",
        }

    async def _work_item_update(
        self, auth: AuthContext, request: SemanticActionRequest, key: str | None
    ) -> JsonDict:
        if not key:
            raise ConflictError(
                "EDWARD_WORK_ITEM_REQUIRED", "Name the task key you want Edward to update"
            )
        found = await self._staff.find_work_item_by_key(auth, key)
        if not found:
            raise NotFoundError("STAFF_WORK_ITEM_NOT_FOUND", "The work item was not found")
        item = _work_item_from_detail(
            await self._staff.get_work_item_detail(auth, str(found["id"]))
        )
        await self._require_work_item_scope(auth, item)
        resolved: JsonDict = {"expectedVersion": item["version"]}
        changes: list[JsonDict] = []
        if request.fields.get("priority") and request.fields["priority"] != item.get("priority"):
            resolved["priority"] = request.fields["priority"]
            changes.append(
                {"field": "priority", "before": item.get("priority"), "after": resolved["priority"]}
            )
        due_on = _task_due_at(request.fields.get("dueOn"), self._clock())
        if due_on:
            resolved["dueAt"] = due_on.isoformat()
            changes.append(
                {"field": "dueAt", "before": item.get("dueAt"), "after": resolved["dueAt"]}
            )
        if request.fields.get("assignToMe"):
            resolved["assigneeId"] = auth.actor_id
            before = cast(Mapping[str, Any] | None, item.get("assignee"))
            changes.append(
                {
                    "field": "assignee",
                    "before": before.get("name") if before else None,
                    "after": "You",
                }
            )
        requested_no_op_status = False
        if request.fields.get("status"):
            if request.fields["status"] == item.get("status"):
                # "Mark AST-01641 as done" on an item already done: a preview
                # that promises done → done is a change that is not one, and
                # the outcome invariant would then dress the no-op up as an
                # edit. Skipping it lets the honest no-change answer fire.
                requested_no_op_status = True
            else:
                resolved["status"] = request.fields["status"]
                changes.append(
                    {
                        "field": "status",
                        "before": item.get("status"),
                        "after": request.fields["status"],
                    }
                )
        follow_up = _task_due_at(request.fields.get("followUp"), self._clock())
        if follow_up:
            resolved["followUpAt"] = follow_up.isoformat()
            changes.append(
                {
                    "field": "followUpAt",
                    "before": item.get("followUpAt"),
                    "after": follow_up.isoformat(),
                }
            )
        stated_next_step = str(request.fields.get("nextStep") or "").strip()[:500]
        # Every terminal status the canonical operation accepts carries its own
        # invariant: done needs an outcome and a resolution, blocked needs a
        # coded blocker and an explanation, cancelled needs a reason. Leaving
        # them out produced a confident preview that could only ever fail on
        # confirmation — the same failure shape as previewing an email to a
        # student with no address. A proposal has to satisfy what execution
        # requires, or ask for the piece it is missing.
        next_status = resolved.get("status")
        if next_status in {"done", "cancelled"}:
            from .work_board_repository import WorkBoardProjection

            board = await WorkBoardProjection(self._staff).read(auth, filters={"search": key})
            card = next((card for card in board["cards"] if card["id"] == item["id"]), None)
            if card and (card.get("document") or card.get("payment") or card.get("case")):
                raise ConflictError(
                    "EDWARD_DOMAIN_DECISION_REQUIRED",
                    "This work is linked to a document, payment or formal case. "
                    "Use its review workflow for a decision; Edward can change its "
                    "priority, owner, due date or active operational status.",
                )
        if next_status == "done":
            resolved["outcomeCode"] = "staff_confirmed_complete"
            resolved["resolutionCode"] = "resolved_by_staff"
            changes.append(
                {"field": "outcome", "before": item.get("outcomeCode"), "after": "closed by you"}
            )
        elif next_status == "blocked":
            if not stated_next_step:
                raise ConflictError(
                    "EDWARD_BLOCKER_REASON_REQUIRED",
                    "Blocked work needs a reason a colleague can act on",
                )
            resolved["blockerCode"] = _blocker_code(stated_next_step)
            resolved["blockerDetail"] = stated_next_step
            changes.append(
                {"field": "blocker", "before": item.get("blockerDetail"), "after": stated_next_step}
            )
        elif next_status == "cancelled":
            if not stated_next_step:
                raise ConflictError(
                    "EDWARD_CANCEL_REASON_REQUIRED",
                    "Cancelled work needs a reason for the record",
                )
            resolved["terminalReason"] = stated_next_step[:80]
            changes.append(
                {"field": "reason", "before": item.get("terminalReason"), "after": stated_next_step}
            )
        if resolved.get("status") == "follow_up_required":
            if follow_up is None:
                raise ConflictError(
                    "EDWARD_FOLLOW_UP_DATE_REQUIRED",
                    "Choose a date for work that moves to follow-up",
                )
            # What the staff member actually said beats a generated sentence.
            # "chase again on Friday, I left a voicemail" carries the next step;
            # replacing it with "Follow up on <title>" throws away the only part
            # of the record a colleague could not reconstruct.
            next_step = stated_next_step or f"Follow up on {item['title']}"[:500]
            resolved["nextStep"] = next_step
            changes.append(
                {"field": "nextStep", "before": item.get("nextStep"), "after": next_step}
            )
        elif stated_next_step:
            resolved["nextStep"] = stated_next_step
            changes.append(
                {"field": "nextStep", "before": item.get("nextStep"), "after": stated_next_step}
            )
        if not changes:
            if requested_no_op_status:
                raise ConflictError(
                    "EDWARD_ACTION_NO_CHANGE",
                    f"{item['key']} is already {str(item.get('status', '')).replace('_', ' ')}, "
                    "so there is nothing to change",
                )
            raise ConflictError(
                "EDWARD_WORK_ITEM_CHANGE_REQUIRED",
                "Specify a supported status or assignment change",
            )
        student = cast(Mapping[str, Any], item["student"])
        return {
            "action": request.action,
            "targetStudentId": student["id"],
            "targetResourceType": "staff_work_item",
            "targetResourceId": item["id"],
            "resolved": resolved,
            "preview": {
                "title": f"Update {item['key']}",
                "summary": str(item["title"]),
                "student": {"id": student["id"], "name": student["name"]},
                "changes": changes,
                # The same shape the create preview uses, so a card, a trace
                # and an eval read one structure for "what the item will be"
                # rather than reconstructing it from the change list.
                "workItem": {
                    "key": item["key"],
                    "title": item["title"],
                    "component": item.get("component"),
                    "status": resolved.get("status", item.get("status")),
                    "priority": resolved.get("priority", item.get("priority")),
                    "assigneeId": resolved.get(
                        "assigneeId",
                        (cast(Mapping[str, Any], item["assignee"]) or {}).get("id")
                        if item.get("assignee")
                        else None,
                    ),
                    "dueAt": resolved.get("dueAt", item.get("dueAt")),
                    "followUpAt": resolved.get("followUpAt", item.get("followUpAt")),
                    "nextStep": resolved.get("nextStep", item.get("nextStep")),
                },
            },
            "provenance": [_source("canonical_database", "staff_work_item", trusted=True)],
            "scopeSnapshot": {"workItemVersion": item["version"]},
            "riskClass": 3,
            "confirmationMode": "strong_confirm",
        }

    async def _email_prepare(
        self,
        auth: AuthContext,
        student_id: str | None,
        draft: Mapping[str, Any] | None,
    ) -> JsonDict:
        if student_id is None:
            raise ConflictError("EDWARD_STUDENT_REQUIRED", "Name the email recipient")
        await self._require_student_scope(auth, student_id)
        if self._staff_email is None:
            raise ApiError(503, "EDWARD_EMAIL_UNAVAILABLE", "Institutional email is unavailable")
        if not draft or draft.get("channel") != "email":
            raise ConflictError(
                "EDWARD_EMAIL_DRAFT_REQUIRED", "Draft the email in Edward before preparing it"
            )
        subject = str(draft.get("subject") or "").strip()
        body = str(draft.get("body") or "").strip()
        if not subject or not body:
            raise ConflictError("EDWARD_EMAIL_DRAFT_INVALID", "The draft is incomplete")
        mailboxes = await self._staff_email.list_mailboxes(auth)
        sendable = [
            item
            for item in cast(Sequence[Mapping[str, Any]], mailboxes.get("items") or [])
            if item.get("canSend") is True and item.get("status") == "active"
        ]
        if len(sendable) != 1:
            raise ConflictError(
                "EDWARD_EMAIL_MAILBOX_REQUIRED",
                "Edward needs exactly one active sendable mailbox; choose one in Mail first",
            )
        student = await self._staff_assistant.get_student_overview(auth, student_id)
        if not student:
            raise NotFoundError("STAFF_STUDENT_NOT_FOUND", "The student was not found")
        # A preview is a promise about what confirming would do, so everything
        # execution needs must resolve now. Without this the card showed a
        # recipient by name, the staff member confirmed it, and only then did
        # the mail service discover the student has no address — a failed
        # receipt for an action that was never possible. Anything the executor
        # requires and the proposal cannot produce belongs here, as an honest
        # denial before the card is drawn.
        recipient_address = await self._student_email_address(auth, student_id)
        if recipient_address is None:
            raise ConflictError(
                "EDWARD_EMAIL_RECIPIENT_UNAVAILABLE",
                f"{student.get('name') or 'That student'} has no email address on file",
            )
        mailbox = sendable[0]
        return {
            "action": "communications.email.prepare",
            "targetStudentId": student_id,
            "targetResourceType": "student",
            "targetResourceId": student_id,
            "resolved": {
                "mailboxId": mailbox["id"],
                "studentId": student_id,
                "subject": subject,
                "body": body,
            },
            "preview": {
                "title": "Prepare an email for final send review",
                "summary": (
                    "This creates a second, hash-pinned email send intent. It does not send yet."
                ),
                "sender": mailbox.get("address"),
                "recipient": {
                    "id": student_id,
                    "name": student.get("name"),
                    "address": recipient_address,
                },
                "subject": subject,
                "body": body,
                "warnings": ["You must review and confirm the email again before it is queued."],
            },
            "provenance": [
                _source("canonical_database", "student_identity", trusted=True),
                _source("model_derived", "reviewed_draft", trusted=False),
            ],
            "scopeSnapshot": {"mailboxId": mailbox["id"], "studentId": student_id},
            "riskClass": 4,
            "confirmationMode": "external_confirm",
        }

    async def _student_email_address(self, auth: AuthContext, student_id: str) -> str | None:
        """The address the mail service would resolve, read the same way it does."""

        async with self._engine.connect() as connection:
            value = await connection.scalar(
                text(
                    """SELECT account.email_normalized
                       FROM credential_account account
                       WHERE account.tenant_id=:tenant_id AND account.student_id=:student_id
                         AND account.status='active'
                       LIMIT 1"""
                ),
                {"tenant_id": UUID(auth.tenant_id), "student_id": UUID(student_id)},
            )
        return str(value) if value else None

    # ------------------------------------------------------------------ execution

    async def _execute(
        self, auth: AuthContext, row: Mapping[str, Any], request_id: str
    ) -> tuple[JsonDict, int]:
        action = str(row["action_type"])
        payload = _json_object(row["resolved_payload"])
        key = str(row["idempotency_key"])
        if action == "student.preferences.update":
            updated = dict(await self._portal.update_student_profile(auth, payload, request_id))
            # Carry the reviewed before/after into the receipt. Without it the
            # only honest thing a later turn can say is "I updated your
            # profile", when the person is asking which field and to what.
            preview = _json_object(row["preview"])
            if isinstance(preview.get("changes"), list):
                updated["changes"] = preview["changes"]
            return updated, 1
        if action == "student.support.contact":
            return await self._portal.create_student_help_request(auth, payload, key, request_id), 1
        if action == "student.requirement.submit_response":
            requirement_id = str(payload.pop("requirementId"))
            return (
                await self._portal.submit_student_requirement_response(
                    auth, requirement_id, payload, key, request_id
                ),
                1,
            )
        if action == "operations.follow_up.create":
            return await self._staff.create_work_item(auth, payload, key, request_id), 1
        if action == "operations.work_item.update":
            resource_id = str(row["target_resource_id"])
            detail = await self._staff.update_work_item(auth, resource_id, payload, request_id)
            return _work_item_from_detail(detail), 1
        if action == "operations.cohort.create_follow_ups":
            return await self._execute_cohort(auth, row, payload, request_id)
        if action == "communications.email.prepare":
            if self._staff_email is None:
                raise ApiError(
                    503, "EDWARD_EMAIL_UNAVAILABLE", "Institutional email is unavailable"
                )
            payload["agentActionIntentId"] = str(row["id"])
            return dict(await self._staff_email.create_send_intent(auth, payload)), 1
        raise BadRequestError("EDWARD_ACTION_UNSUPPORTED", "This action is unavailable")

    async def _execute_cohort(
        self,
        auth: AuthContext,
        row: Mapping[str, Any],
        payload: JsonDict,
        request_id: str,
    ) -> tuple[JsonDict, int]:
        snapshot = _json_object(row["scope_snapshot"])
        cohort = build_cohort_filter(cast(Mapping[str, Any], payload["cohortFilter"]))
        current = await self._staff_assistant.find_students(
            auth, cohort, limit=MAXIMUM_COHORT_ACTION_SIZE + 1
        )
        ids = sorted(str(item["id"]) for item in current.items)
        fingerprint = canonical_digest({"studentIds": ids})
        if current.total != snapshot.get("count") or fingerprint != snapshot.get("fingerprint"):
            raise ConflictError(
                "EDWARD_COHORT_DRIFTED",
                "Cohort membership changed after preview; review a fresh preview before acting",
            )
        template = dict(cast(Mapping[str, Any], payload["template"]))
        successes: list[JsonDict] = []
        failures: list[JsonDict] = []
        for student_id in ids:
            item_payload = {**template, "studentId": student_id}
            item_key = f"edward-batch:{row['id']}:{student_id}"
            try:
                created = await self._staff.create_work_item(
                    auth, item_payload, item_key, f"{request_id}:{student_id}"
                )
                successes.append(
                    {
                        "studentId": student_id,
                        "workItemId": created.get("id"),
                        "key": created.get("key"),
                    }
                )
                await self._record_batch_item(auth, str(row["id"]), student_id, item_key, created)
            except Exception as error:
                failures.append({"studentId": student_id, "code": _error_code(error)})
                await self._record_batch_item(
                    auth, str(row["id"]), student_id, item_key, None, error=error
                )
        return {
            "created": successes,
            "failures": failures,
            "requestedCount": len(ids),
            "succeededCount": len(successes),
            "failedCount": len(failures),
        }, len(successes)

    async def _recover_applied_state(
        self, auth: AuthContext, row: Mapping[str, Any]
    ) -> tuple[JsonDict, int] | None:
        """Recognize non-idempotent effects after an uncertain response.

        Canonical create/submit primitives already use the action's stable
        idempotency key. Profile and work-item updates use optimistic versions,
        so a retry first checks whether the exact reviewed effect is present.
        """

        action = str(row["action_type"])
        payload = _json_object(row["resolved_payload"])
        if action == "student.preferences.update":
            current = await self._portal.get_student_profile(auth)
            expected = {key: value for key, value in payload.items() if key != "expectedVersion"}
            if all(current.get(key) == value for key, value in expected.items()):
                return current, 1
        elif action == "operations.work_item.update":
            current = _work_item_from_detail(
                await self._staff.get_work_item_detail(auth, str(row["target_resource_id"]))
            )
            matches = True
            if "status" in payload:
                matches = matches and current.get("status") == payload["status"]
            if "assigneeId" in payload:
                assignee = cast(Mapping[str, Any] | None, current.get("assignee"))
                current_assignee = str(assignee["id"]) if assignee else None
                matches = matches and current_assignee == payload["assigneeId"]
            if "followUpAt" in payload:
                matches = matches and _same_instant(
                    current.get("followUpAt"), payload["followUpAt"]
                )
            if "dueAt" in payload:
                matches = matches and _same_instant(current.get("dueAt"), payload["dueAt"])
            for field in (
                "priority",
                "nextStep",
                "blockerCode",
                "blockerDetail",
                "terminalReason",
                "outcomeCode",
                "resolutionCode",
            ):
                if field in payload:
                    matches = matches and current.get(field) == payload[field]
            if matches:
                return current, 1
        return None

    # ------------------------------------------------------------------ policy/state

    async def capabilities_for(self, auth: AuthContext) -> tuple[str, ...]:
        """Every Edward capability this staff member's role currently holds.

        Read once per turn so the recognizer is never offered an action that
        is certain to be denied, and so "what can you do?" is answered from
        the same grants the gateway enforces rather than from a sentence
        someone wrote down.
        """

        if auth.actor_type != "staff":
            return ()
        async with self._engine.connect() as connection:
            rows = await connection.execute(
                text(
                    """SELECT capability_grant.capability
                       FROM staff_member member
                       JOIN staff_role_capability capability_grant
                         ON capability_grant.tenant_id=member.tenant_id
                        AND capability_grant.role_code=member.role_code
                       WHERE member.tenant_id=:tenant_id AND member.id=:staff_id
                         AND member.active=true AND member.employment_status<>'departed'"""
                ),
                {"tenant_id": UUID(auth.tenant_id), "staff_id": UUID(auth.actor_id)},
            )
            return tuple(sorted({str(row[0]) for row in rows}))

    async def _require_capability(
        self,
        auth: AuthContext,
        capability: str,
        *,
        connection: AsyncConnection | None = None,
    ) -> None:
        if not capability:
            raise ApiError(403, "EDWARD_ACTION_FORBIDDEN", "No action capability is configured")
        owns = connection is None
        if owns:
            connection = await self._engine.connect()
        assert connection is not None
        try:
            allowed = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1
                      FROM staff_member member
                      JOIN staff_role_capability capability_grant
                        ON capability_grant.tenant_id=member.tenant_id
                       AND capability_grant.role_code=member.role_code
                      WHERE member.tenant_id=:tenant_id AND member.id=:staff_id
                        AND member.active=true AND member.employment_status<>'departed'
                        AND capability_grant.capability=:capability
                    )
                    """
                ),
                {
                    "tenant_id": UUID(auth.tenant_id),
                    "staff_id": UUID(auth.actor_id),
                    "capability": capability,
                },
            )
        finally:
            if owns:
                await connection.close()
        if allowed is not True:
            raise ApiError(
                403,
                "EDWARD_ACTION_CAPABILITY_REQUIRED",
                "Your staff role does not include this Edward action capability",
            )

    async def _require_student_scope(self, auth: AuthContext, student_id: str) -> None:
        try:
            await self._require_capability(auth, "edward.student.any")
            return
        except ApiError as error:
            if error.status_code != 403:
                raise
        async with self._engine.connect() as connection:
            assigned = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1 FROM student_staff_assignment assignment
                      WHERE assignment.tenant_id=:tenant_id
                        AND assignment.student_id=:student_id
                        AND assignment.staff_member_id=:staff_id
                        AND assignment.ended_at IS NULL
                    )
                    """
                ),
                {
                    "tenant_id": UUID(auth.tenant_id),
                    "student_id": UUID(student_id),
                    "staff_id": UUID(auth.actor_id),
                },
            )
        if assigned is not True:
            raise ApiError(
                403,
                "EDWARD_STUDENT_SCOPE_FORBIDDEN",
                "Edward can act only on students in your current assigned caseload",
            )

    async def _require_work_item_scope(self, auth: AuthContext, item: Mapping[str, Any]) -> None:
        try:
            await self._require_capability(auth, "edward.student.any")
            return
        except ApiError as error:
            if error.status_code != 403:
                raise
        member = await self._staff_member(auth)
        assignee = cast(Mapping[str, Any] | None, item.get("assignee"))
        if (assignee and assignee.get("id") == auth.actor_id) or str(item.get("component")) == str(
            member["component"]
        ):
            return
        raise ApiError(
            403,
            "EDWARD_WORK_ITEM_SCOPE_FORBIDDEN",
            "Edward can update only your assigned or component-owned work items",
        )

    async def _recheck_staff_scope(
        self, auth: AuthContext, row: Mapping[str, Any], connection: Any
    ) -> None:
        action = str(row["action_type"])
        if action == "operations.cohort.create_follow_ups":
            await self._require_capability(auth, "edward.student.any", connection=connection)
            return
        if action == "operations.work_item.update":
            item = _work_item_from_detail(
                await self._staff.get_work_item_detail(auth, str(row["target_resource_id"]))
            )
            await self._require_work_item_scope(auth, item)
            return
        target_student = row.get("target_student_id")
        if target_student is not None:
            await self._require_student_scope(auth, str(target_student))

    async def _recheck_canonical_snapshot(self, auth: AuthContext, row: Mapping[str, Any]) -> None:
        action = str(row["action_type"])
        snapshot = _json_object(row["scope_snapshot"])
        if action == "student.preferences.update":
            current = await self._portal.get_student_profile(auth)
            if current["version"] != snapshot.get("profileVersion"):
                raise ConflictError("VERSION_CONFLICT", "Your profile changed after preview")
        elif action == "student.requirement.submit_response":
            requirement = await self._portal.get_student_requirement(
                auth, str(row["target_resource_id"])
            )
            if requirement["version"] != snapshot.get("requirementVersion"):
                raise ConflictError("VERSION_CONFLICT", "The requirement changed after preview")
        elif action == "operations.work_item.update":
            item = _work_item_from_detail(
                await self._staff.get_work_item_detail(auth, str(row["target_resource_id"]))
            )
            if item["version"] != snapshot.get("workItemVersion"):
                raise ConflictError("VERSION_CONFLICT", "The work item changed after preview")
        elif action == "operations.cohort.create_follow_ups":
            # Full membership recheck happens immediately before the first item.
            return

    async def _support_destination(
        self, auth: AuthContext, requirement: Mapping[str, Any] | None
    ) -> JsonDict | None:
        """The office or person a support request reaches, from canonical state."""

        office = str((requirement or {}).get("responsibleOffice") or "").strip() or None
        adviser: JsonDict | None = None
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """SELECT member.display_name, member.title, member.component
                               FROM student_staff_assignment assignment
                               JOIN staff_member member
                                 ON member.id=assignment.staff_member_id
                                AND member.tenant_id=assignment.tenant_id
                               WHERE assignment.tenant_id=:tenant_id
                                 AND assignment.student_id=:student_id
                                 AND assignment.ended_at IS NULL
                                 AND assignment.role='primary_advisor'
                               LIMIT 1"""
                        ),
                        {
                            "tenant_id": UUID(auth.tenant_id),
                            "student_id": UUID(auth.student_id),
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is not None:
            adviser = {
                "name": str(row["display_name"]),
                "title": row["title"],
                "component": row["component"],
            }
        if office is None and adviser is None:
            return None
        return {"office": office, "adviser": adviser}

    async def _follow_up_context(
        self, auth: AuthContext, student_id: str, subject: str | None = None
    ) -> tuple[JsonDict, JsonDict | None, JsonDict]:
        """Resolve the student, the requirement to chase, and the actor.

        `subject` is what the staff member said the follow-up is about. When it
        names one of the student's own open requirements, that requirement wins
        over the most-urgent default: a task created "about her financial aid
        verification" that says "Confirm housing plans" is not the task anyone
        asked for, and the preview makes the substitution look deliberate.
        """

        student = await self._staff_assistant.get_student_overview(auth, student_id)
        if not student:
            raise NotFoundError("STAFF_STUDENT_NOT_FOUND", "The student was not found")
        student_auth = AuthContext(
            tenant_id=auth.tenant_id,
            student_id=student_id,
            actor_id=auth.actor_id,
            actor_type="student",
            authentication_method=auth.authentication_method,
            tenant_slug=auth.tenant_slug,
        )
        requirements = cast(
            list[JsonDict], (await self._portal.get_student_requirements(student_auth))["items"]
        )
        actionable = [
            item
            for item in requirements
            if str(item.get("status")) in _ACTIONABLE_REQUIREMENT_STATUSES
        ]
        actionable.sort(
            key=lambda item: (
                not bool(item.get("blocking")),
                -int(item.get("priority") or 0),
                str(item.get("dueAt") or "9999"),
            )
        )
        # A named subject may point at any requirement that is still open —
        # including one under review ("chase the registrar about her
        # transcript"), which the student cannot act on but staff can. Only
        # the *unnamed* default narrows to what the student must still do.
        still_open = [
            item
            for item in requirements
            if str(item.get("status")) not in _SETTLED_REQUIREMENT_STATUSES
        ]
        chosen = (
            _match_requirement(
                subject,
                None,
                still_open,
                require_unique=False,
                statuses={str(item.get("status")) for item in still_open},
            )
            if subject and still_open
            else None
        )
        if chosen is not None and not _mentions(subject or "", chosen):
            # `_match_requirement` hands back the only open item when nothing
            # mentions it; that is the caller's warning to raise, not a match.
            chosen = None
        return (
            dict(student),
            chosen or (actionable[0] if actionable else None),
            await self._staff_member(auth),
        )

    async def _staff_member(self, auth: AuthContext) -> JsonDict:
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT id, display_name, component, role_code
                            FROM staff_member
                            WHERE tenant_id=:tenant_id AND id=:staff_id AND active=true
                            """
                        ),
                        {"tenant_id": UUID(auth.tenant_id), "staff_id": UUID(auth.actor_id)},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise ApiError(403, "STAFF_ACCESS_REQUIRED", "The active staff member was not found")
        return dict(row)

    # ------------------------------------------------------------------ persistence

    async def _create_intent(
        self,
        auth: AuthContext,
        proposal: JsonDict,
        *,
        conversation_id: str,
        trace_id: str,
        request_payload: Mapping[str, Any],
    ) -> JsonDict:
        intent_id = str(uuid4())
        expires_at = self._clock() + INTENT_TTL
        digest_payload = {
            "action": proposal["action"],
            "resolved": proposal["resolved"],
            "preview": proposal["preview"],
            "scopeSnapshot": proposal["scopeSnapshot"],
            "expiresAt": expires_at.isoformat(),
        }
        digest = canonical_digest(digest_payload)
        idempotency_key = f"edward-action:{intent_id}"
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_action_intent (
                      id, tenant_id, actor_type, student_actor_id, staff_member_id,
                      student_conversation_id, staff_conversation_id, trace_id,
                      action_type, target_student_id, target_resource_type,
                      target_resource_id, request_payload, resolved_payload, preview,
                      provenance, scope_snapshot, risk_class, confirmation_mode,
                      authorization_capability, content_sha256, idempotency_key, expires_at
                    ) VALUES (
                      :id, :tenant_id, :actor_type, :student_actor_id, :staff_member_id,
                      :student_conversation_id, :staff_conversation_id, :trace_id,
                      :action_type, :target_student_id, :target_resource_type,
                      :target_resource_id, CAST(:request_payload AS jsonb),
                      CAST(:resolved_payload AS jsonb), CAST(:preview AS jsonb),
                      CAST(:provenance AS jsonb), CAST(:scope_snapshot AS jsonb),
                      :risk_class, :confirmation_mode, :authorization_capability,
                      :content_sha256, :idempotency_key, :expires_at
                    )
                    """
                ),
                {
                    "id": UUID(intent_id),
                    "tenant_id": UUID(auth.tenant_id),
                    "actor_type": auth.actor_type,
                    "student_actor_id": UUID(auth.student_id)
                    if auth.actor_type == "student"
                    else None,
                    "staff_member_id": UUID(auth.actor_id) if auth.actor_type == "staff" else None,
                    "student_conversation_id": UUID(conversation_id)
                    if auth.actor_type == "student"
                    else None,
                    "staff_conversation_id": UUID(conversation_id)
                    if auth.actor_type == "staff"
                    else None,
                    "trace_id": trace_id,
                    "action_type": proposal["action"],
                    "target_student_id": UUID(str(proposal["targetStudentId"]))
                    if proposal.get("targetStudentId")
                    else None,
                    "target_resource_type": proposal.get("targetResourceType"),
                    "target_resource_id": UUID(str(proposal["targetResourceId"]))
                    if proposal.get("targetResourceId")
                    else None,
                    "request_payload": _dump(request_payload),
                    "resolved_payload": _dump(proposal["resolved"]),
                    "preview": _dump(proposal["preview"]),
                    "provenance": _dump(proposal["provenance"]),
                    "scope_snapshot": _dump(proposal["scopeSnapshot"]),
                    "risk_class": proposal["riskClass"],
                    "confirmation_mode": proposal["confirmationMode"],
                    "authorization_capability": proposal.get("authorizationCapability"),
                    "content_sha256": digest,
                    "idempotency_key": idempotency_key,
                    "expires_at": expires_at,
                },
            )
        return {
            "id": intent_id,
            "action": proposal["action"],
            "status": "pending_confirmation",
            "version": 1,
            "riskClass": proposal["riskClass"],
            "confirmationMode": proposal["confirmationMode"],
            "contentSha256": digest,
            "expiresAt": expires_at.isoformat(),
            "preview": proposal["preview"],
            "provenance": proposal["provenance"],
            "authorizationCapability": proposal.get("authorizationCapability"),
        }

    async def _intent_row(
        self,
        auth: AuthContext,
        intent_id: str,
        *,
        connection: AsyncConnection | None = None,
        lock: bool = False,
    ) -> Mapping[str, Any] | None:
        if auth.actor_type not in {"student", "staff"}:
            raise ApiError(
                403,
                "EDWARD_ACTION_ACTOR_FORBIDDEN",
                "This identity cannot own or confirm Edward actions",
            )
        owns = connection is None
        if owns:
            connection = await self._engine.connect()
        assert connection is not None
        if auth.actor_type == "student":
            query = """
                SELECT * FROM agent_action_intent
                WHERE tenant_id=:tenant_id AND id=:id
                  AND student_actor_id=:actor_id AND actor_type='student'
            """
        else:
            query = """
                SELECT * FROM agent_action_intent
                WHERE tenant_id=:tenant_id AND id=:id
                  AND staff_member_id=:actor_id AND actor_type='staff'
            """
        if lock:
            query += " FOR UPDATE"
        try:
            result = await connection.execute(
                text(query),
                {
                    "tenant_id": UUID(auth.tenant_id),
                    "id": UUID(intent_id),
                    "actor_id": UUID(
                        auth.student_id if auth.actor_type == "student" else auth.actor_id
                    ),
                },
            )
            return cast(Mapping[str, Any] | None, result.mappings().first())
        finally:
            if owns:
                await connection.close()

    async def _public_intent(self, row: Mapping[str, Any]) -> JsonDict:
        result = {
            "id": str(row["id"]),
            "action": str(row["action_type"]),
            "status": str(row["status"]),
            "version": int(row["version"]),
            "riskClass": int(row["risk_class"]),
            "confirmationMode": str(row["confirmation_mode"]),
            "contentSha256": str(row["content_sha256"]),
            "expiresAt": cast(datetime, row["expires_at"]).isoformat(),
            "preview": _json_object(row["preview"]),
            "provenance": _json_array(row["provenance"]),
            "authorizationCapability": row.get("authorization_capability"),
        }
        async with self._engine.connect() as connection:
            receipt = await self._receipt_row(connection, str(row["tenant_id"]), str(row["id"]))
        if receipt is not None:
            result["receipt"] = _public_receipt(receipt)
        return result

    async def _record_success(
        self,
        auth: AuthContext,
        row: Mapping[str, Any],
        result: JsonDict,
        affected: int,
        request_id: str,
    ) -> JsonDict:
        status = (
            "failed"
            if result.get("failedCount") and affected == 0
            else "partial"
            if result.get("failedCount")
            else "succeeded"
        )
        receipt_id = str(uuid4())
        committed = self._clock()
        target = {
            "studentId": str(row["target_student_id"]) if row.get("target_student_id") else None,
            "resourceType": row.get("target_resource_type"),
            "resourceId": str(row["target_resource_id"]) if row.get("target_resource_id") else None,
        }
        async with self._engine.connect() as connection:
            audit_rows = await connection.execute(
                text(
                    """SELECT id FROM audit_event
                       WHERE tenant_id=:tenant_id
                         AND (request_id=:request_id OR request_id LIKE :request_prefix)
                       ORDER BY occurred_at, id"""
                ),
                {
                    "tenant_id": UUID(auth.tenant_id),
                    "request_id": request_id,
                    "request_prefix": f"{request_id}:%",
                },
            )
            audit_event_ids = [str(value) for value in audit_rows.scalars().all()]
        receipt_hash = canonical_digest(
            {
                "intentId": str(row["id"]),
                "action": str(row["action_type"]),
                "status": status,
                "target": target,
                "result": result,
                "affectedCount": affected,
                "auditEventIds": audit_event_ids,
                "committedAt": committed.isoformat(),
            }
        )
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_action_receipt (
                      id, tenant_id, action_intent_id, action_type, status, target,
                      result, affected_count, receipt_sha256, audit_event_ids, committed_at
                    ) VALUES (
                      :id, :tenant_id, :intent_id, :action_type, :status,
                      CAST(:target AS jsonb), CAST(:result AS jsonb), :affected_count,
                      :receipt_sha256, CAST(:audit_event_ids AS jsonb), :committed_at
                    ) ON CONFLICT (action_intent_id) DO NOTHING
                    """
                ),
                {
                    "id": UUID(receipt_id),
                    "tenant_id": UUID(auth.tenant_id),
                    "intent_id": row["id"],
                    "action_type": row["action_type"],
                    "status": status,
                    "target": _dump(target),
                    "result": _dump(result),
                    "affected_count": affected,
                    "receipt_sha256": receipt_hash,
                    "audit_event_ids": _dump(audit_event_ids),
                    "committed_at": committed,
                },
            )
            await connection.execute(
                text(
                    """UPDATE agent_action_intent
                       SET status=:status, completed_at=:committed_at, updated_at=NOW()
                       WHERE tenant_id=:tenant_id AND id=:id"""
                ),
                {
                    "status": status,
                    "committed_at": committed,
                    "tenant_id": UUID(auth.tenant_id),
                    "id": row["id"],
                },
            )
            receipt = await self._receipt_row(connection, auth.tenant_id, str(row["id"]))
        assert receipt is not None
        public_receipt = _public_receipt(receipt)
        await self._publish_action_trace(
            row,
            {
                "actionExecutionResult": status,
                "actionLatencyMs": _elapsed_ms(row.get("created_at"), committed),
                "actionReceipt": _trace_receipt(public_receipt),
            },
        )
        return public_receipt

    async def _record_failure(
        self, auth: AuthContext, row: Mapping[str, Any], error: Exception
    ) -> None:
        code = _error_code(error)
        message = str(getattr(error, "message", "The action could not be completed"))[:1000]
        committed = self._clock()
        target = {
            "studentId": str(row["target_student_id"]) if row.get("target_student_id") else None,
            "resourceType": row.get("target_resource_type"),
            "resourceId": str(row["target_resource_id"]) if row.get("target_resource_id") else None,
        }
        result = {"errorCode": code, "message": message, "outcomeVerified": True}
        receipt_hash = canonical_digest(
            {
                "intentId": str(row["id"]),
                "action": str(row["action_type"]),
                "status": "failed",
                "target": target,
                "result": result,
                "affectedCount": 0,
                "committedAt": committed.isoformat(),
            }
        )
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_action_receipt (
                      id, tenant_id, action_intent_id, action_type, status, target,
                      result, affected_count, receipt_sha256, committed_at
                    ) VALUES (
                      :id, :tenant_id, :intent_id, :action_type, 'failed',
                      CAST(:target AS jsonb), CAST(:result AS jsonb), 0,
                      :receipt_sha256, :committed_at
                    ) ON CONFLICT (action_intent_id) DO NOTHING
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": UUID(auth.tenant_id),
                    "intent_id": row["id"],
                    "action_type": row["action_type"],
                    "target": _dump(target),
                    "result": _dump(result),
                    "receipt_sha256": receipt_hash,
                    "committed_at": committed,
                },
            )
            await connection.execute(
                text(
                    """UPDATE agent_action_intent
                       SET status='failed', failure_code=:code, failure_message=:message,
                           completed_at=:committed_at, updated_at=NOW()
                       WHERE tenant_id=:tenant_id AND id=:id AND status='executing'"""
                ),
                {
                    "code": code,
                    "message": message,
                    "committed_at": committed,
                    "tenant_id": UUID(auth.tenant_id),
                    "id": row["id"],
                },
            )
            receipt = await self._receipt_row(connection, auth.tenant_id, str(row["id"]))
        if receipt is not None:
            public_receipt = _public_receipt(receipt)
            await self._publish_action_trace(
                row,
                {
                    "actionExecutionResult": "failed",
                    "actionLatencyMs": _elapsed_ms(row.get("created_at"), committed),
                    "actionReceipt": _trace_receipt(public_receipt),
                },
            )

    async def _record_unknown_execution_error(
        self, auth: AuthContext, row: Mapping[str, Any], error: Exception
    ) -> None:
        """Keep an uncertain outcome recoverable instead of falsely claiming failure."""

        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """UPDATE agent_action_intent
                       SET failure_code='EDWARD_ACTION_OUTCOME_UNKNOWN',
                           failure_message=:message, updated_at=NOW()
                       WHERE tenant_id=:tenant_id AND id=:id AND status='executing'"""
                ),
                {
                    "message": str(error)[:1000] or "Execution outcome is not yet known",
                    "tenant_id": UUID(auth.tenant_id),
                    "id": row["id"],
                },
            )
        await self._publish_action_trace(row, {"actionExecutionResult": "outcome_unknown"})

    async def _publish_action_trace(
        self, row: Mapping[str, Any], updates: Mapping[str, Any]
    ) -> None:
        trace_id = str(row.get("trace_id") or "")
        if not trace_id:
            return
        get_assistant_trace_recorder().merge_payload(trace_id, updates)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    text(
                        """UPDATE assistant_turn_trace
                           SET trace_payload=trace_payload || CAST(:updates AS jsonb),
                               recorded_at=NOW()
                           WHERE tenant_id=:tenant_id AND trace_id=:trace_id"""
                    ),
                    {
                        "updates": _dump(updates),
                        "tenant_id": row["tenant_id"],
                        "trace_id": trace_id,
                    },
                )
        except Exception:
            LOGGER.exception("Edward action trace update failed trace_id=%s", trace_id)

    async def _receipt_row(
        self, connection: AsyncConnection, tenant_id: str, intent_id: str
    ) -> Mapping[str, Any] | None:
        result = await connection.execute(
            text(
                """SELECT * FROM agent_action_receipt
                   WHERE tenant_id=:tenant_id AND action_intent_id=:intent_id"""
            ),
            {"tenant_id": UUID(tenant_id), "intent_id": UUID(intent_id)},
        )
        return cast(Mapping[str, Any] | None, result.mappings().first())

    async def _record_batch_item(
        self,
        auth: AuthContext,
        intent_id: str,
        student_id: str,
        key: str,
        result: Mapping[str, Any] | None,
        *,
        error: Exception | None = None,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO agent_action_batch_item (
                      id, tenant_id, action_intent_id, student_id, idempotency_key,
                      status, work_item_id, error_code, error_message, completed_at
                    ) VALUES (
                      :id, :tenant_id, :intent_id, :student_id, :key, :status,
                      :work_item_id, :error_code, :error_message, NOW()
                    ) ON CONFLICT (action_intent_id, student_id) DO NOTHING
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": UUID(auth.tenant_id),
                    "intent_id": UUID(intent_id),
                    "student_id": UUID(student_id),
                    "key": key,
                    "status": "failed" if error else "succeeded",
                    "work_item_id": UUID(str(result["id"]))
                    if result and result.get("id")
                    else None,
                    "error_code": _error_code(error) if error else None,
                    "error_message": str(error)[:1000] if error else None,
                },
            )

    @staticmethod
    def _require_staff(auth: AuthContext) -> None:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_ACCESS_REQUIRED", "This action requires staff identity")


def _match_requirement(
    message: str,
    page_path: str | None,
    requirements: Sequence[JsonDict],
    *,
    require_unique: bool,
    statuses: Collection[str] = _ACTIONABLE_REQUIREMENT_STATUSES,
) -> JsonDict | None:
    """The requirement the message names, from those in `statuses`.

    Callers matching a *named* subject pass every still-open status so an
    item under review can be the target; the unnamed default keeps to what
    the student must still act on.
    """

    actionable = [item for item in requirements if str(item.get("status")) in statuses]
    haystack = f"{message} {page_path or ''}".lower()
    normalized_haystack = re.sub(r"[_/-]+", " ", haystack)
    exact_matches = [
        item
        for item in actionable
        if str(item.get("code") or "").replace("_", " ").lower() in normalized_haystack
        or str(item.get("slug") or "").replace("-", " ").lower() in normalized_haystack
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    matches = [
        item
        for item in actionable
        if any(
            token in haystack
            for token in str(item.get("title") or "").lower().split()
            if len(token) >= 6
        )
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches and len(actionable) == 1:
        return actionable[0]
    if require_unique:
        return None
    return matches[0] if len(matches) == 1 else None


_BLOCKER_CODES: tuple[tuple[str, str], ...] = (
    (r"unreach|no response|not respond|no answer|silent|ghost", "awaiting_student"),
    (r"waiting on the student|student (?:to|needs to|has to)|chase", "awaiting_student"),
    # The student asked for time, or life intervened on their side. Checked
    # before the office/system rows so "mum is in hospital, asked for two
    # weeks' grace" does not read as a systems problem.
    (
        r"hospital|bereavement|family (?:situation|emergency)|asked for (?:time|.{0,16}grace)",
        "awaiting_student",
    ),
    # A document that cannot be read has its own canonical code the board
    # filters by; mapping it to a generic wait hid these from the one filter
    # that routes them to the people who fix them.
    (r"parse|unreadable|corrupt|extraction fail|can'?t (?:read|extract)", "document_parse_failure"),
    (
        r"registrar|admissions|external|third[- ]party|another (?:office|team|department)"
        r"|\w+ office\b|bursar|vendor",
        "awaiting_external",
    ),
    # "hold" was here once and made "on hold — <any reason>" a systems
    # problem; the surviving words each actually describe one.
    (r"system|portal|technical|error|bug|outage", "system_hold"),
)


def _mentions(subject: str, requirement: Mapping[str, Any]) -> bool:
    """Whether the requirement is plausibly the one the subject named."""

    haystack = " ".join(
        str(requirement.get(field) or "").lower().replace("-", " ").replace("_", " ")
        for field in ("title", "code", "slug")
    )
    tokens = [token for token in re.split(r"\W+", subject.lower()) if len(token) >= 5]
    return any(token in haystack for token in tokens)


_PREFERENCE_LABELS = {
    "preferredName": "your preferred name",
    "pronouns": "your pronouns",
    "mobilePhone": "your mobile number",
    "communicationPreference": "your contact preference",
}


def _blocker_code(detail: str) -> str:
    """Map a stated reason onto the coded vocabulary the board already uses.

    The codes are what the Action Center groups and reports by; free text alone
    would make an Edward-blocked item invisible to every existing filter.
    """

    lowered = detail.lower()
    for pattern, code in _BLOCKER_CODES:
        if re.search(pattern, lowered):
            return code
    # "Waiting on IT" is a systems wait; lowercased it becomes the pronoun,
    # so the department acronym is checked against the original casing, and
    # only after the specific rows (a doc that won't parse stays a parse
    # failure even when IT will fix it).
    if re.search(r"\bIT\b", detail):
        return "system_hold"
    return "awaiting_student"


def _follow_up_payload(
    auth: AuthContext,
    student: Mapping[str, Any],
    requirement: Mapping[str, Any] | None,
    member: Mapping[str, Any],
    fields: Mapping[str, Any],
    *,
    due_at: datetime | None,
) -> JsonDict:
    name = str(student.get("preferredName") or student.get("name") or "student")
    subject = str(requirement.get("title")) if requirement else "enrollment progress"
    return {
        "studentId": student["id"],
        "flowKind": str(requirement.get("flowKind") or "enrollment")
        if requirement
        else "enrollment",
        "requirementId": requirement.get("id") if requirement else None,
        "title": f"Follow up with {name}: {subject}"[:240],
        "description": (
            f"Review {subject} with {name}. Created from Edward using canonical student state."
        )[:2000],
        "component": str(member["component"]),
        "assigneeId": auth.actor_id if fields.get("assignToMe") else None,
        "priority": fields.get("priority")
        or ("high" if requirement and requirement.get("blocking") else "medium"),
        "status": "todo",
        "dueAt": due_at.isoformat() if due_at else None,
        "actionType": "onboarding_assistance"
        if requirement and requirement.get("flowKind") == "onboarding"
        else "enrollment_follow_up",
    }


_WEEKDAY_INDEX = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4}


def _due_at(value: Any, now: datetime) -> datetime | None:
    """Resolve the recognizer's whole day vocabulary against the server clock.

    The vocabulary is declared in the catalogue; every value it declares must
    land on the row. This mapped only {today, tomorrow, friday} at first, so
    "due Thursday" was recognized, carried in the request — and silently
    dropped from the canonical write, which is exactly the quiet wrongness a
    confirmation card exists to prevent.
    """

    day = now.date()
    if value == "today":
        pass
    elif value == "tomorrow":
        day += timedelta(days=1)
    elif value == "next_week":
        # The start of next week: the coming Monday.
        day += timedelta(days=(7 - day.weekday()) or 7)
    elif value in _WEEKDAY_INDEX:
        days = (_WEEKDAY_INDEX[str(value)] - day.weekday()) % 7
        day += timedelta(days=days or 7)
    else:
        return None
    due = datetime.combine(day, time(17, 0), tzinfo=now.tzinfo or UTC)
    if due <= now:
        due += timedelta(days=1)
    return due


def _task_due_at(value: Any, now: datetime) -> datetime | None:
    """Task Board deadlines use the same local calendar as the board UI/read tools."""
    if not value:
        return None
    local = now.astimezone(ZoneInfo("America/New_York"))
    day = local.date()
    weekdays = {**_WEEKDAY_INDEX, "saturday": 5, "sunday": 6}
    if value == "today":
        pass
    elif value == "tomorrow":
        day += timedelta(days=1)
    elif value == "next_week":
        day += timedelta(days=7 - day.weekday())
    elif value in weekdays:
        day += timedelta(days=(weekdays[str(value)] - day.weekday()) % 7 or 7)
    else:
        try:
            day = date.fromisoformat(str(value))
        except ValueError as error:
            raise BadRequestError(
                "INVALID_TASK_DATE", "Use an explicit date or a named day"
            ) from error
    return datetime.combine(day, time(17), tzinfo=local.tzinfo)


def _same_instant(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    try:
        left_at = (
            left
            if isinstance(left, datetime)
            else datetime.fromisoformat(str(left).replace("Z", "+00:00"))
        )
        right_at = (
            right
            if isinstance(right, datetime)
            else datetime.fromisoformat(str(right).replace("Z", "+00:00"))
        )
    except ValueError:
        return str(left) == str(right)
    return left_at == right_at


def _source(kind: str, source: str, *, trusted: bool) -> JsonDict:
    return {
        "kind": kind,
        "source": source,
        "factTrusted": trusted,
        # Evidence may establish facts. It is never an instruction channel or
        # authorization signal, including when its facts are canonical.
        "instructionTrusted": False,
    }


def _support_topic(message: str, page_path: str | None) -> str:
    text_value = f"{message} {page_path or ''}".lower()
    if any(word in text_value for word in ("document", "transcript", "immunization", "upload")):
        return "documents"
    if any(word in text_value for word in ("payment", "deposit", "bill", "financial")):
        return "payments"
    return "support"


def _requirement_preview(requirement: Mapping[str, Any] | None) -> JsonDict | None:
    if not requirement:
        return None
    return {
        "id": requirement.get("id"),
        "title": requirement.get("title"),
        "status": requirement.get("status"),
        "dueAt": requirement.get("dueAt"),
        "blocking": requirement.get("blocking"),
    }


def _work_item_preview(payload: Mapping[str, Any]) -> JsonDict:
    return {
        key: payload.get(key)
        for key in (
            "title",
            "description",
            "component",
            "assigneeId",
            "priority",
            "status",
            "dueAt",
        )
    }


def _cohort_json(cohort: CohortFilter) -> JsonDict:
    snake = asdict(cohort)
    names = {
        "class_year": "classYear",
        "offer_status": "offerStatus",
        "deposit_state": "depositState",
        "onboarding_status": "onboardingStatus",
        "requirement_code": "requirementCode",
        "requirement_state": "requirementState",
        "document_category": "documentCategory",
        "document_state": "documentState",
        "aid_document_state": "aidDocumentState",
        "housing_state": "housingState",
        "assigned_staff_id": "assignedStaffId",
        "has_open_work_item": "hasOpenWorkItem",
        "has_overdue_requirement": "hasOverdueRequirement",
        "has_open_blocking_requirement": "hasOpenBlockingRequirement",
        "residency_status": "residencyStatus",
        "citizenship_status": "citizenshipStatus",
        "primary_adviser_id": "primaryAdviserId",
        "adviser_state": "adviserState",
    }
    return {names.get(key, key): value for key, value in snake.items() if value is not None}


def _public_receipt(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "intentId": str(row["action_intent_id"]),
        "action": str(row["action_type"]),
        "status": str(row["status"]),
        "target": _json_object(row["target"]),
        "result": _json_object(row["result"]),
        "affectedCount": int(row["affected_count"]),
        "auditEventIds": _json_array(row["audit_event_ids"]),
        "receiptSha256": str(row["receipt_sha256"]),
        "committedAt": cast(datetime, row["committed_at"]).isoformat(),
    }


def _json_object(value: Any) -> JsonDict:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


def _work_item_from_detail(value: Mapping[str, Any]) -> JsonDict:
    work_item = value.get("workItem", value)
    if not isinstance(work_item, Mapping):
        raise ApiError(500, "STAFF_WORK_ITEM_INVALID", "The canonical work item is invalid")
    return dict(work_item)


def _json_array(value: Any) -> list[Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return list(value) if isinstance(value, Sequence) and not isinstance(value, str | bytes) else []


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _elapsed_ms(started: Any, completed: datetime) -> int | None:
    if not isinstance(started, datetime):
        return None
    return max(0, round((completed - started).total_seconds() * 1_000))


def _trace_receipt(receipt: Mapping[str, Any]) -> JsonDict:
    """Keep Lab traces useful without copying message bodies or recipients."""

    result = _json_object(receipt.get("result"))
    summary = {
        key: result[key]
        for key in (
            "id",
            "key",
            "status",
            "version",
            "requestedCount",
            "succeededCount",
            "failedCount",
        )
        if key in result
    }
    return {
        "action": receipt.get("action"),
        "status": receipt.get("status"),
        "target": receipt.get("target"),
        "affectedCount": receipt.get("affectedCount"),
        "auditEventIds": receipt.get("auditEventIds", []),
        "receiptSha256": receipt.get("receiptSha256"),
        "committedAt": receipt.get("committedAt"),
        "result": summary,
    }


def _error_code(error: Exception | None) -> str:
    if error is None:
        return "UNKNOWN"
    return str(getattr(error, "code", error.__class__.__name__.upper()))[:80]
