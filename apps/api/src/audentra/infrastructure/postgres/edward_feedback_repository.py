"""Durable Edward traces and response-scoped user feedback.

This repository does not decide which transcript turn a client meant. It
proves that association from the immutable Student/Staff Edward message tables,
the authenticated actor, and the trace id stored on the assistant message.
"Latest" never participates in a feedback write.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import BadRequestError, NotFoundError, UnauthorizedError

JsonDict = dict[str, Any]


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _optional_uuid(value: object) -> UUID | None:
    return None if value in (None, "") else _uuid(value)


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return timestamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _optional_datetime(value: object) -> datetime | None:
    """Normalize API/trace timestamps before binding them as timestamptz.

    asyncpg derives the parameter type from ``CAST(... AS timestamptz)`` and
    therefore requires a Python datetime rather than an ISO string. Keeping
    this conversion at the repository boundary also makes trace persistence
    and Lab date filters behave the same way.
    """

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        timestamp = value
    else:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)


def _json_object(value: object) -> JsonDict:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        parsed = json.loads(value)
        return {str(key): item for key, item in parsed.items()} if isinstance(parsed, dict) else {}
    return {}


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _feedback_response(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "assistantKind": str(row["assistant_kind"]),
        "tenantId": str(row["tenant_id"]),
        "actorType": "student" if row["assistant_kind"] == "student" else "staff",
        "actorId": str(row["student_id"] or row["staff_member_id"]),
        "actorName": _optional_text(row.get("actor_name")),
        "referencedStudentId": (
            str(row["referenced_student_id"])
            if row.get("referenced_student_id") is not None
            else None
        ),
        "referencedStudentName": _optional_text(row.get("referenced_student_name")),
        "conversationId": str(row["conversation_id"]),
        "userMessageId": str(row["student_user_message_id"] or row["staff_user_message_id"]),
        "assistantMessageId": str(
            row["student_assistant_message_id"] or row["staff_assistant_message_id"]
        ),
        "traceId": str(row["trace_id"]),
        "question": str(row["question"]),
        "response": str(row["response"]),
        "rating": row.get("rating"),
        "writtenFeedback": row.get("written_feedback"),
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


class PostgresEdwardFeedbackRepository:
    """Shared Student/Staff Edward feedback store over the canonical transcripts."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def save_trace(self, trace_payload: Mapping[str, Any]) -> None:
        """Persist the existing sanitized AssistantTurnTrace representation."""

        values = self._trace_values(trace_payload)
        async with self.engine.begin() as connection:
            await self._upsert_trace(connection, values)

    async def get_trace(self, trace_id: str) -> JsonDict | None:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT trace_payload
                            FROM assistant_turn_trace
                            WHERE trace_id=:trace_id
                            """
                        ),
                        {"trace_id": trace_id},
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else _json_object(row["trace_payload"])

    async def submit_student_feedback(
        self,
        auth: AuthContext,
        *,
        assistant_message_id: str,
        trace_id: str,
        payload: Mapping[str, Any],
        trace_payload: Mapping[str, Any] | None,
    ) -> JsonDict:
        if auth.actor_type != "student":
            raise UnauthorizedError("Student authentication is required")
        async with self.engine.begin() as connection:
            turn = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT assistant.id AS assistant_message_id,
                                   assistant.conversation_id,
                                   assistant.student_id,
                                   assistant.request_id AS trace_id,
                                   assistant.content AS response,
                                   assistant.created_at,
                                   student_message.id AS user_message_id,
                                   student_message.content AS question
                            FROM assistant_message AS assistant
                            JOIN assistant_message AS student_message
                              ON student_message.tenant_id=assistant.tenant_id
                             AND student_message.student_id=assistant.student_id
                             AND student_message.conversation_id=assistant.conversation_id
                             AND student_message.request_id=assistant.request_id
                             AND student_message.role='user'
                            WHERE assistant.tenant_id=:tenant_id
                              AND assistant.student_id=:student_id
                              AND assistant.id=:assistant_message_id
                              AND assistant.role='assistant'
                              AND assistant.request_id=:trace_id
                            LIMIT 1
                            FOR UPDATE OF assistant
                            """
                        ),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "student_id": _uuid(auth.student_id),
                            "assistant_message_id": _uuid(assistant_message_id),
                            "trace_id": trace_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if turn is None:
                raise NotFoundError(
                    "EDWARD_RESPONSE_NOT_FOUND",
                    "That Edward response is not available in this student's conversation",
                )
            turn_values = dict(turn)
            await self._ensure_turn_trace(
                connection,
                assistant_kind="student",
                auth=auth,
                turn=turn_values,
                referenced_student_id=None,
                trace_payload=trace_payload,
            )
            return await self._upsert_feedback(
                connection,
                assistant_kind="student",
                auth=auth,
                turn=turn_values,
                referenced_student_id=None,
                payload=payload,
            )

    async def submit_staff_feedback(
        self,
        auth: AuthContext,
        *,
        assistant_message_id: str,
        trace_id: str,
        payload: Mapping[str, Any],
        trace_payload: Mapping[str, Any] | None,
    ) -> JsonDict:
        if auth.actor_type != "staff":
            raise UnauthorizedError("Staff authentication is required")
        async with self.engine.begin() as connection:
            turn = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT assistant.id AS assistant_message_id,
                                   assistant.conversation_id,
                                   assistant.staff_member_id,
                                   assistant.request_id AS trace_id,
                                   assistant.content AS response,
                                   assistant.referenced_student_id,
                                   assistant.created_at,
                                   staff_message.id AS user_message_id,
                                   staff_message.content AS question
                            FROM staff_assistant_message AS assistant
                            JOIN staff_assistant_message AS staff_message
                              ON staff_message.tenant_id=assistant.tenant_id
                             AND staff_message.staff_member_id=assistant.staff_member_id
                             AND staff_message.conversation_id=assistant.conversation_id
                             AND staff_message.exchange_id=assistant.exchange_id
                             AND staff_message.role='user'
                            WHERE assistant.tenant_id=:tenant_id
                              AND assistant.staff_member_id=:staff_member_id
                              AND assistant.id=:assistant_message_id
                              AND assistant.role='assistant'
                              AND assistant.request_id=:trace_id
                            LIMIT 1
                            FOR UPDATE OF assistant
                            """
                        ),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "staff_member_id": _uuid(auth.actor_id),
                            "assistant_message_id": _uuid(assistant_message_id),
                            "trace_id": trace_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if turn is None:
                raise NotFoundError(
                    "EDWARD_RESPONSE_NOT_FOUND",
                    "That Staff Edward response is not available in this staff conversation",
                )
            turn_values = dict(turn)
            referenced_student_id = (
                str(turn_values["referenced_student_id"])
                if turn_values["referenced_student_id"] is not None
                else None
            )
            await self._ensure_turn_trace(
                connection,
                assistant_kind="staff",
                auth=auth,
                turn=turn_values,
                referenced_student_id=referenced_student_id,
                trace_payload=trace_payload,
            )
            return await self._upsert_feedback(
                connection,
                assistant_kind="staff",
                auth=auth,
                turn=turn_values,
                referenced_student_id=referenced_student_id,
                payload=payload,
            )

    async def list_feedback(
        self,
        *,
        assistant_kind: str | None,
        rating: str | None,
        has_written: bool | None,
        date_from: str | None,
        date_to: str | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> JsonDict:
        clauses: list[str] = []
        params: dict[str, Any] = {
            "limit": max(1, min(limit, 200)),
            "offset": max(0, offset),
        }
        if assistant_kind is not None:
            clauses.append("feedback.assistant_kind=:assistant_kind")
            params["assistant_kind"] = assistant_kind
        if rating == "unrated":
            clauses.append("feedback.rating IS NULL")
        elif rating is not None:
            clauses.append("feedback.rating=:rating")
            params["rating"] = rating
        if has_written is True:
            clauses.append("feedback.written_feedback IS NOT NULL")
        elif has_written is False:
            clauses.append("feedback.written_feedback IS NULL")
        if date_from is not None:
            clauses.append("feedback.created_at >= CAST(:date_from AS timestamptz)")
            params["date_from"] = _optional_datetime(date_from)
        if date_to is not None:
            clauses.append("feedback.created_at <= CAST(:date_to AS timestamptz)")
            params["date_to"] = _optional_datetime(date_to)
        if search:
            clauses.append(
                "(feedback.question ILIKE :search ESCAPE '\\' "
                "OR feedback.response ILIKE :search ESCAPE '\\' "
                "OR COALESCE(feedback.written_feedback, '') ILIKE :search ESCAPE '\\' "
                "OR COALESCE(student_person.first_name || ' ' || student_person.last_name, '') "
                "ILIKE :search ESCAPE '\\' "
                "OR COALESCE(staff_member.display_name, '') ILIKE :search ESCAPE '\\')"
            )
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params["search"] = f"%{escaped[:200]}%"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        joins = """
            LEFT JOIN student AS student_actor
              ON student_actor.id=feedback.student_id
             AND student_actor.tenant_id=feedback.tenant_id
            LEFT JOIN person AS student_person
              ON student_person.id=student_actor.person_id
             AND student_person.tenant_id=feedback.tenant_id
            LEFT JOIN staff_member
              ON staff_member.id=feedback.staff_member_id
             AND staff_member.tenant_id=feedback.tenant_id
            LEFT JOIN student AS referenced_student
              ON referenced_student.id=feedback.referenced_student_id
             AND referenced_student.tenant_id=feedback.tenant_id
            LEFT JOIN person AS referenced_person
              ON referenced_person.id=referenced_student.person_id
             AND referenced_person.tenant_id=feedback.tenant_id
        """
        async with self.engine.connect() as connection:
            total = await connection.scalar(
                text(
                    f"""
                    SELECT COUNT(*)
                    FROM edward_response_feedback AS feedback
                    {joins}
                    {where}
                    """  # noqa: S608 - clauses are fixed module-owned SQL fragments.
                ),
                params,
            )
            rows = (
                (
                    await connection.execute(
                        text(
                            f"""
                            SELECT feedback.*,
                                   CASE
                                     WHEN feedback.assistant_kind='student'
                                     THEN COALESCE(student_person.preferred_name,
                                                   student_person.first_name) || ' ' ||
                                          student_person.last_name
                                     ELSE staff_member.display_name
                                   END AS actor_name,
                                   COALESCE(referenced_person.preferred_name,
                                            referenced_person.first_name) || ' ' ||
                                   referenced_person.last_name AS referenced_student_name
                            FROM edward_response_feedback AS feedback
                            {joins}
                            {where}
                            ORDER BY feedback.created_at DESC, feedback.id DESC
                            LIMIT :limit OFFSET :offset
                            """  # noqa: S608 - clauses are fixed module-owned SQL fragments.
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
        return {
            "items": [_feedback_response(dict(row)) for row in rows],
            "total": int(total or 0),
            "limit": params["limit"],
            "offset": params["offset"],
        }

    async def _ensure_turn_trace(
        self,
        connection: AsyncConnection,
        *,
        assistant_kind: str,
        auth: AuthContext,
        turn: Mapping[str, Any],
        referenced_student_id: str | None,
        trace_payload: Mapping[str, Any] | None,
    ) -> None:
        fallback: JsonDict = {
            "traceId": str(turn["trace_id"]),
            "tenantId": auth.tenant_id,
            "assistantKind": assistant_kind,
            "actorType": auth.actor_type,
            "studentId": auth.student_id if assistant_kind == "student" else referenced_student_id,
            "staffMemberId": auth.actor_id if assistant_kind == "staff" else None,
            "conversationId": str(turn["conversation_id"]),
            "userMessage": str(turn["question"]),
            "finalMessage": str(turn["response"]),
            "userMessageId": str(turn["user_message_id"]),
            "assistantMessageId": str(turn["assistant_message_id"]),
            "path": "historical_feedback_fallback",
            "startedAt": _iso(turn["created_at"]),
        }
        candidate = dict(trace_payload) if trace_payload is not None else fallback
        if candidate.get("traceId") != str(turn["trace_id"]):
            candidate = fallback
        await self._upsert_trace(connection, self._trace_values(candidate, fallback=fallback))

    async def _upsert_feedback(
        self,
        connection: AsyncConnection,
        *,
        assistant_kind: str,
        auth: AuthContext,
        turn: Mapping[str, Any],
        referenced_student_id: str | None,
        payload: Mapping[str, Any],
    ) -> JsonDict:
        message_column = (
            "student_assistant_message_id"
            if assistant_kind == "student"
            else "staff_assistant_message_id"
        )
        actor_column = "student_id" if assistant_kind == "student" else "staff_member_id"
        actor_id = _uuid(auth.student_id) if assistant_kind == "student" else _uuid(auth.actor_id)
        existing = (
            (
                await connection.execute(
                    text(
                        f"""
                        SELECT * FROM edward_response_feedback
                        WHERE {message_column}=:assistant_message_id
                          AND tenant_id=:tenant_id
                          AND {actor_column}=:actor_id
                        FOR UPDATE
                        """  # noqa: S608 - columns are selected from fixed constants.
                    ),
                    {
                        "assistant_message_id": turn["assistant_message_id"],
                        "tenant_id": _uuid(auth.tenant_id),
                        "actor_id": actor_id,
                    },
                )
            )
            .mappings()
            .first()
        )
        rating = (
            payload.get("rating")
            if "rating" in payload
            else (existing["rating"] if existing is not None else None)
        )
        written_feedback = (
            _optional_text(payload.get("writtenFeedback"))
            if "writtenFeedback" in payload
            else (existing["written_feedback"] if existing is not None else None)
        )
        if rating is None and written_feedback is None:
            raise BadRequestError(
                "EDWARD_FEEDBACK_EMPTY",
                "Choose a rating or provide written feedback",
            )

        if existing is None:
            feedback_id = uuid4()
            values = {
                "id": feedback_id,
                "tenant_id": _uuid(auth.tenant_id),
                "assistant_kind": assistant_kind,
                "student_id": _uuid(auth.student_id) if assistant_kind == "student" else None,
                "staff_member_id": _uuid(auth.actor_id) if assistant_kind == "staff" else None,
                "referenced_student_id": _optional_uuid(referenced_student_id),
                "conversation_id": turn["conversation_id"],
                "student_user_message_id": (
                    turn["user_message_id"] if assistant_kind == "student" else None
                ),
                "student_assistant_message_id": (
                    turn["assistant_message_id"] if assistant_kind == "student" else None
                ),
                "staff_user_message_id": (
                    turn["user_message_id"] if assistant_kind == "staff" else None
                ),
                "staff_assistant_message_id": (
                    turn["assistant_message_id"] if assistant_kind == "staff" else None
                ),
                "trace_id": str(turn["trace_id"]),
                "question": str(turn["question"]),
                "response": str(turn["response"]),
                "rating": rating,
                "written_feedback": written_feedback,
            }
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO edward_response_feedback (
                              id, tenant_id, assistant_kind, student_id, staff_member_id,
                              referenced_student_id, conversation_id,
                              student_user_message_id, student_assistant_message_id,
                              staff_user_message_id, staff_assistant_message_id,
                              trace_id, question, response, rating, written_feedback
                            ) VALUES (
                              :id, :tenant_id, :assistant_kind, :student_id, :staff_member_id,
                              :referenced_student_id, :conversation_id,
                              :student_user_message_id, :student_assistant_message_id,
                              :staff_user_message_id, :staff_assistant_message_id,
                              :trace_id, :question, :response, :rating, :written_feedback
                            )
                            RETURNING *
                            """
                        ),
                        values,
                    )
                )
                .mappings()
                .one()
            )
        else:
            row = (
                (
                    await connection.execute(
                        text(
                            f"""
                            UPDATE edward_response_feedback
                            SET rating=:rating,
                                written_feedback=:written_feedback,
                                updated_at=NOW()
                            WHERE id=:id
                              AND tenant_id=:tenant_id
                              AND assistant_kind=:assistant_kind
                              AND {actor_column}=:actor_id
                            RETURNING *
                            """  # noqa: S608 - actor_column is selected from fixed constants.
                        ),
                        {
                            "id": existing["id"],
                            "tenant_id": _uuid(auth.tenant_id),
                            "assistant_kind": assistant_kind,
                            "actor_id": actor_id,
                            "rating": rating,
                            "written_feedback": written_feedback,
                        },
                    )
                )
                .mappings()
                .one()
            )
        return _feedback_response(dict(row))

    @staticmethod
    async def _upsert_trace(connection: AsyncConnection, values: Mapping[str, Any]) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO assistant_turn_trace (
                  trace_id, tenant_id, assistant_kind, student_actor_id,
                  staff_member_id, referenced_student_id, conversation_id,
                  user_message_id, assistant_message_id, trace_payload, started_at
                ) VALUES (
                  :trace_id, :tenant_id, :assistant_kind, :student_actor_id,
                  :staff_member_id, :referenced_student_id, :conversation_id,
                  :user_message_id, :assistant_message_id, CAST(:trace_payload AS jsonb),
                  CAST(:started_at AS timestamptz)
                )
                ON CONFLICT (trace_id) DO UPDATE SET
                  trace_payload=EXCLUDED.trace_payload,
                  conversation_id=COALESCE(EXCLUDED.conversation_id,
                                           assistant_turn_trace.conversation_id),
                  user_message_id=COALESCE(EXCLUDED.user_message_id,
                                           assistant_turn_trace.user_message_id),
                  assistant_message_id=COALESCE(EXCLUDED.assistant_message_id,
                                                assistant_turn_trace.assistant_message_id),
                  recorded_at=NOW()
                WHERE assistant_turn_trace.tenant_id=EXCLUDED.tenant_id
                  AND assistant_turn_trace.assistant_kind=EXCLUDED.assistant_kind
                  AND assistant_turn_trace.student_actor_id
                      IS NOT DISTINCT FROM EXCLUDED.student_actor_id
                  AND assistant_turn_trace.staff_member_id
                      IS NOT DISTINCT FROM EXCLUDED.staff_member_id
                  AND assistant_turn_trace.assistant_message_id
                      IS NOT DISTINCT FROM EXCLUDED.assistant_message_id
                """
            ),
            dict(values),
        )

    @staticmethod
    def _trace_values(
        trace_payload: Mapping[str, Any], *, fallback: Mapping[str, Any] | None = None
    ) -> JsonDict:
        merged = dict(fallback or {})
        merged.update(trace_payload)
        assistant_kind = str(merged.get("assistantKind") or "student")
        tenant_id = merged.get("tenantId")
        trace_id = _optional_text(merged.get("traceId"))
        if assistant_kind not in {"student", "staff"} or tenant_id is None or trace_id is None:
            raise ValueError("Assistant trace is missing its durable identity")
        student_id = _optional_uuid(merged.get("studentId"))
        staff_member_id = _optional_uuid(merged.get("staffMemberId"))
        if assistant_kind == "student" and student_id is None:
            raise ValueError("Student assistant trace is missing its student actor")
        if assistant_kind == "staff" and staff_member_id is None:
            raise ValueError("Staff assistant trace is missing its staff actor")
        return {
            "trace_id": trace_id,
            "tenant_id": _uuid(tenant_id),
            "assistant_kind": assistant_kind,
            "student_actor_id": student_id if assistant_kind == "student" else None,
            "staff_member_id": staff_member_id if assistant_kind == "staff" else None,
            "referenced_student_id": student_id if assistant_kind == "staff" else None,
            "conversation_id": _optional_uuid(merged.get("conversationId")),
            "user_message_id": _optional_uuid(merged.get("userMessageId")),
            "assistant_message_id": _optional_uuid(merged.get("assistantMessageId")),
            "trace_payload": json.dumps(merged, ensure_ascii=False, default=str),
            "started_at": _optional_datetime(merged.get("startedAt")),
        }
