"""Voice session lifecycle: create, reconnect, and the internal agent surface.

The service owns no answer logic. A finalized speech turn is dispatched as the
canonical `student.ask_edward` operation with the session's bound student
identity, so voice and text produce byte-identical assistant behavior,
persistence, and audit lineage.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, ConflictError
from audentra.core.ports import PlatformService, ServiceCall
from audentra.infrastructure.postgres.voice_repository import PostgresVoiceSessionRepository
from audentra.infrastructure.voice.config import VoiceSettings
from audentra.infrastructure.voice.livekit import (
    LiveKitVoiceRoomProvisioner,
    VoiceRoomProvisioner,
    mint_participant_token,
)

JsonDict = dict[str, Any]

logger = logging.getLogger(__name__)

_DETAIL_KEYS = (
    "voiceSessionId",
    "conversationId",
    "provider",
    "roomName",
    "participantIdentity",
    "pageContext",
    "status",
    "expiresAt",
    "endedAt",
    "createdAt",
)


class VoiceSessionServiceProtocol(Protocol):
    async def create(
        self, auth: AuthContext, payload: Mapping[str, Any], request_id: str
    ) -> JsonDict: ...

    async def reconnect(
        self, auth: AuthContext, voice_session_id: str, request_id: str
    ) -> JsonDict: ...

    async def get_internal(self, voice_session_id: str) -> JsonDict: ...

    async def submit_turn(
        self, voice_session_id: str, payload: Mapping[str, Any], request_id: str
    ) -> JsonDict: ...

    async def end_internal(self, voice_session_id: str) -> JsonDict: ...


class UnavailableVoiceSessionService:
    """Installed when LiveKit is not configured: every call fails closed."""

    async def create(
        self, auth: AuthContext, payload: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        raise _not_configured()

    async def reconnect(
        self, auth: AuthContext, voice_session_id: str, request_id: str
    ) -> JsonDict:
        raise _not_configured()

    async def get_internal(self, voice_session_id: str) -> JsonDict:
        raise _not_configured()

    async def submit_turn(
        self, voice_session_id: str, payload: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        raise _not_configured()

    async def end_internal(self, voice_session_id: str) -> JsonDict:
        raise _not_configured()


class VoiceSessionService:
    def __init__(
        self,
        settings: VoiceSettings,
        repository: PostgresVoiceSessionRepository,
        platform_service: PlatformService,
        *,
        provisioner: VoiceRoomProvisioner | None = None,
        clock: Any = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._platform = platform_service
        self._provisioner = provisioner or LiveKitVoiceRoomProvisioner(settings)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create(
        self, auth: AuthContext, payload: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        conversation_id = str(payload.get("conversationId", ""))
        page = payload.get("pageContext")
        page_path, page_label = _page_context(page)
        if not await self._repository.is_student_eligible_for_voice(auth):
            raise ApiError(
                403,
                "ASSISTANT_VOICE_INELIGIBLE",
                "Voice is available after the admission offer has been accepted",
            )
        now = self._clock()
        session = await self._repository.create(
            auth,
            conversation_id=conversation_id,
            room_name=_opaque_identifier("ev1_room"),
            participant_identity=_opaque_identifier("ev1_participant"),
            page_path=page_path,
            page_label=page_label,
            expires_at=now + timedelta(seconds=self._settings.session_ttl_seconds),
        )
        logger.info(
            "voice_session_created",
            extra={
                "voice_session_id": session["voiceSessionId"],
                "conversation_id": session["conversationId"],
                "request_id": request_id,
            },
        )
        try:
            await self._provisioner.provision(
                room_name=session["roomName"],
                voice_session_id=session["voiceSessionId"],
            )
        except Exception as error:
            await self._repository.end(session["voiceSessionId"])
            logger.error(
                "voice_worker_dispatch_failed",
                extra={
                    "voice_session_id": session["voiceSessionId"],
                    "request_id": request_id,
                },
            )
            raise ApiError(
                503,
                "ASSISTANT_VOICE_WORKER_UNAVAILABLE",
                "Edward's voice worker could not be started",
            ) from error
        return self._issue_credentials(session, now)

    async def reconnect(
        self, auth: AuthContext, voice_session_id: str, request_id: str
    ) -> JsonDict:
        session = await self._repository.get_for_student(auth, voice_session_id)
        self._assert_active(session)
        return self._issue_credentials(session, self._clock())

    async def get_internal(self, voice_session_id: str) -> JsonDict:
        session = await self._repository.get(voice_session_id)
        self._assert_active(session)
        return _details(session)

    async def submit_turn(
        self, voice_session_id: str, payload: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        session = await self._repository.get(voice_session_id)
        self._assert_active(session)
        result = await self._platform.dispatch(
            ServiceCall(
                operation="student.ask_edward",
                auth=AuthContext(
                    tenant_id=session["tenantId"],
                    student_id=session["studentId"],
                    actor_id=session["actorId"],
                    actor_type="student",
                ),
                request_id=request_id,
                payload={
                    "message": str(payload.get("text", "")),
                    "pageContext": payload.get("pageContext") or session["pageContext"],
                    "conversationId": session["conversationId"],
                    "clientMessageId": payload.get("clientMessageId"),
                    "inputMode": "voice",
                },
            )
        )
        if not isinstance(result, dict):  # pragma: no cover - dispatch contract
            raise ApiError(
                500, "ASSISTANT_RESPONSE_INVALID", "The assistant returned an invalid response"
            )
        return result

    async def end_internal(self, voice_session_id: str) -> JsonDict:
        await self._repository.get(voice_session_id)
        ended = await self._repository.end(voice_session_id)
        logger.info(
            "voice_session_ended",
            extra={"voice_session_id": voice_session_id},
        )
        return _details(ended)

    def _issue_credentials(self, session: JsonDict, now: datetime) -> JsonDict:
        expires_at = datetime.fromisoformat(session["expiresAt"].replace("Z", "+00:00"))
        remaining_seconds = int((expires_at - now).total_seconds())
        if remaining_seconds < 1:
            raise ConflictError(
                "ASSISTANT_VOICE_SESSION_EXPIRED", "This assistant voice session has expired"
            )
        ttl_seconds = min(self._settings.token_ttl_seconds, remaining_seconds)
        minted = mint_participant_token(
            self._settings,
            room_name=session["roomName"],
            participant_identity=session["participantIdentity"],
            ttl_seconds=ttl_seconds,
            token_id=_opaque_identifier("ev1_token"),
        )
        return {
            "server_url": self._settings.livekit_url,
            "participant_token": minted.token,
            "voice_session_id": session["voiceSessionId"],
            "expires_at": (now + timedelta(seconds=ttl_seconds))
            .astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }

    def _assert_active(self, session: JsonDict) -> None:
        if session["status"] == "ended":
            raise ConflictError(
                "ASSISTANT_VOICE_SESSION_ENDED", "This assistant voice session has ended"
            )
        expires_at = datetime.fromisoformat(session["expiresAt"].replace("Z", "+00:00"))
        if expires_at <= self._clock():
            raise ConflictError(
                "ASSISTANT_VOICE_SESSION_EXPIRED", "This assistant voice session has expired"
            )


def _not_configured() -> ApiError:
    return ApiError(
        503,
        "ASSISTANT_VOICE_NOT_CONFIGURED",
        "Voice is not configured for this environment",
    )


def _details(session: JsonDict) -> JsonDict:
    return {key: session[key] for key in _DETAIL_KEYS}


def _page_context(value: object) -> tuple[str | None, str | None]:
    if isinstance(value, Mapping):
        path = str(value.get("path", "") or "") or None
        label = str(value.get("label", "") or "") or None
        return path, label
    return None, None


def _opaque_identifier(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"
