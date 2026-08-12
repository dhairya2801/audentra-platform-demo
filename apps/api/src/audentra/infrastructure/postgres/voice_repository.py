"""Postgres store for Edward voice sessions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, NotFoundError

JsonDict = dict[str, Any]


class PostgresVoiceSessionRepository:
    """Tenant-scoped SQLAlchemy Core repository for assistant voice sessions."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def _one(self, statement: str, params: Mapping[str, Any]) -> JsonDict | None:
        async with self.engine.connect() as connection:
            result = await connection.execute(text(statement), dict(params))
            row = result.mappings().first()
            return dict(row) if row is not None else None

    async def create(
        self,
        auth: AuthContext,
        *,
        conversation_id: str,
        room_name: str,
        participant_identity: str,
        page_path: str | None,
        page_label: str | None,
        expires_at: datetime,
    ) -> JsonDict:
        if auth.actor_type != "student":
            raise ApiError(403, "STUDENT_ACCESS_REQUIRED", "Student access is required")
        session_id = str(uuid4())
        async with self.engine.begin() as connection:
            conversation = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT id, status FROM assistant_conversation
                            WHERE tenant_id=:tenant_id AND student_id=:student_id
                              AND id=:conversation_id
                            """
                        ),
                        {
                            "tenant_id": auth.tenant_id,
                            "student_id": auth.student_id,
                            "conversation_id": conversation_id,
                        },
                    )
                )
                .mappings()
                .first()
            )
            if conversation is None:
                raise NotFoundError(
                    "ASSISTANT_CONVERSATION_NOT_FOUND", "The conversation was not found"
                )
            if str(conversation["status"]) != "active":
                raise ApiError(
                    409,
                    "ASSISTANT_CONVERSATION_CLOSED",
                    "This assistant conversation is closed",
                )
            row = (
                (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO assistant_voice_session (
                              id, tenant_id, student_id, actor_id, conversation_id,
                              provider, room_name, participant_identity,
                              page_path, page_label, status, expires_at
                            ) VALUES (
                              :id, :tenant_id, :student_id, :actor_id, :conversation_id,
                              'livekit', :room_name, :participant_identity,
                              :page_path, :page_label, 'active', :expires_at
                            )
                            RETURNING id, tenant_id, student_id, actor_id,
                              conversation_id, provider, room_name,
                              participant_identity, page_path, page_label,
                              status, expires_at, ended_at, created_at
                            """
                        ),
                        {
                            "id": session_id,
                            "tenant_id": auth.tenant_id,
                            "student_id": auth.student_id,
                            "actor_id": auth.actor_id,
                            "conversation_id": conversation_id,
                            "room_name": room_name,
                            "participant_identity": participant_identity,
                            "page_path": (page_path or "")[:240] or None,
                            "page_label": (page_label or "")[:240] or None,
                            "expires_at": expires_at,
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:  # pragma: no cover - RETURNING always yields on success
            raise NotFoundError("STUDENT_NOT_FOUND", "The student record was not found")
        return _map_session(dict(row))

    async def get_for_student(self, auth: AuthContext, voice_session_id: str) -> JsonDict:
        if auth.actor_type != "student":
            raise ApiError(403, "STUDENT_ACCESS_REQUIRED", "Student access is required")
        row = await self._one(
            """
            SELECT id, tenant_id, student_id, actor_id, conversation_id, provider,
                   room_name, participant_identity, page_path, page_label, status,
                   expires_at, ended_at, created_at
            FROM assistant_voice_session
            WHERE tenant_id=:tenant_id AND student_id=:student_id AND id=:id
            """,
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "id": voice_session_id,
            },
        )
        if row is None:
            raise NotFoundError(
                "ASSISTANT_VOICE_SESSION_NOT_FOUND", "The voice session was not found"
            )
        return _map_session(row)

    async def get(self, voice_session_id: str) -> JsonDict:
        """Internal lookup by id alone: the caller is the trusted voice agent."""

        row = await self._one(
            """
            SELECT id, tenant_id, student_id, actor_id, conversation_id, provider,
                   room_name, participant_identity, page_path, page_label, status,
                   expires_at, ended_at, created_at
            FROM assistant_voice_session WHERE id=:id
            """,
            {"id": voice_session_id},
        )
        if row is None:
            raise NotFoundError(
                "ASSISTANT_VOICE_SESSION_NOT_FOUND", "The voice session was not found"
            )
        return _map_session(row)

    async def end(self, voice_session_id: str) -> JsonDict:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE assistant_voice_session
                    SET status='ended',
                        ended_at=COALESCE(ended_at, now()),
                        updated_at=now()
                    WHERE id=:id
                    RETURNING id, tenant_id, student_id, actor_id, conversation_id,
                      provider, room_name, participant_identity, page_path,
                      page_label, status, expires_at, ended_at, created_at
                    """
                ),
                {"id": voice_session_id},
            )
            row = result.mappings().first()
        if row is None:
            raise NotFoundError(
                "ASSISTANT_VOICE_SESSION_NOT_FOUND", "The voice session was not found"
            )
        return _map_session(dict(row))

    async def is_student_eligible_for_voice(self, auth: AuthContext) -> bool:
        """Voice follows the accepted offer, exactly like VV's eligibility gate."""

        row = await self._one(
            """
            SELECT 1 AS eligible FROM admission_offer
            WHERE tenant_id=:tenant_id AND student_id=:student_id AND status='accepted'
            LIMIT 1
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        return row is not None


def _map_session(row: Mapping[str, Any]) -> JsonDict:
    return {
        "voiceSessionId": str(row["id"]),
        "tenantId": str(row["tenant_id"]),
        "studentId": str(row["student_id"]),
        "actorId": str(row["actor_id"]),
        "conversationId": str(row["conversation_id"]),
        "provider": str(row["provider"]),
        "roomName": str(row["room_name"]),
        "participantIdentity": str(row["participant_identity"]),
        "pageContext": {
            "path": str(row["page_path"] or "/"),
            "label": str(row["page_label"] or ""),
        },
        "status": str(row["status"]),
        "expiresAt": _iso(row["expires_at"]),
        "endedAt": _iso(row["ended_at"]) if row["ended_at"] is not None else None,
        "createdAt": _iso(row["created_at"]),
    }


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return str(value)
