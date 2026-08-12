"""Voice session routes, LiveKit credentials, and Postgres persistence."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.core.ports import ServiceCall
from audentra.infrastructure.db.engine import normalize_database_url
from audentra.infrastructure.postgres.voice_repository import PostgresVoiceSessionRepository
from audentra.infrastructure.voice.config import VoiceSettings
from audentra.infrastructure.voice.service import VoiceSessionService
from audentra.interfaces.http.app import create_app
from audentra.interfaces.http.config import HttpSettings

INTERNAL_TOKEN = "internal-test-token-123456"  # noqa: S105
VOICE_SETTINGS = VoiceSettings(
    livekit_url="wss://example.livekit.cloud",
    livekit_api_key="lk_test",
    livekit_api_secret="lk_secret_test_0123456789abcdef",  # noqa: S106
    agent_internal_token=INTERNAL_TOKEN,
    agent_name="student-assistant-voice",
    session_ttl_seconds=900,
    token_ttl_seconds=600,
)


class RecordingVoiceService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def create(
        self, auth: AuthContext, payload: Mapping[str, Any], request_id: str
    ) -> dict[str, Any]:
        self.calls.append(("create", (auth, dict(payload), request_id)))
        return {"voice_session_id": "created"}

    async def reconnect(
        self, auth: AuthContext, voice_session_id: str, request_id: str
    ) -> dict[str, Any]:
        self.calls.append(("reconnect", (auth, voice_session_id, request_id)))
        return {"voice_session_id": voice_session_id}

    async def get_internal(self, voice_session_id: str) -> dict[str, Any]:
        self.calls.append(("get_internal", (voice_session_id,)))
        return {"voiceSessionId": voice_session_id}

    async def submit_turn(
        self, voice_session_id: str, payload: Mapping[str, Any], request_id: str
    ) -> dict[str, Any]:
        self.calls.append(("submit_turn", (voice_session_id, dict(payload), request_id)))
        return {"conversationId": "conversation", "message": "answer"}

    async def end_internal(self, voice_session_id: str) -> dict[str, Any]:
        self.calls.append(("end_internal", (voice_session_id,)))
        return {"voiceSessionId": voice_session_id, "status": "ended"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def voice_client() -> AsyncIterator[tuple[AsyncClient, RecordingVoiceService]]:
    service = RecordingVoiceService()
    app = create_app(
        settings=HttpSettings(voice_agent_internal_token=INTERNAL_TOKEN),
        voice_service=service,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, service


@pytest.mark.anyio
async def test_create_voice_session_route_forwards_the_camel_case_payload(
    voice_client: tuple[AsyncClient, RecordingVoiceService],
) -> None:
    client, service = voice_client
    conversation_id = str(uuid4())
    response = await client.post(
        "/v1/student/assistant/voice-sessions",
        json={
            "conversationId": conversation_id,
            "pageContext": {"path": "/edward", "label": "Edward"},
        },
    )

    assert response.status_code == 201
    name, (auth, payload, request_id) = service.calls[-1]
    assert name == "create"
    assert auth.actor_type == "student"
    assert payload == {
        "conversationId": conversation_id,
        "pageContext": {"path": "/edward", "label": "Edward"},
    }
    assert request_id


@pytest.mark.anyio
async def test_create_voice_session_route_rejects_invalid_bodies(
    voice_client: tuple[AsyncClient, RecordingVoiceService],
) -> None:
    client, service = voice_client
    for body in (
        {},
        {"conversationId": "not-a-uuid", "pageContext": {"path": "/", "label": "x"}},
        {"conversationId": str(uuid4())},
        {"conversationId": str(uuid4()), "pageContext": {"path": "/"}, "extra": True},
    ):
        response = await client.post("/v1/student/assistant/voice-sessions", json=body)
        assert response.status_code == 400
    assert service.calls == []


@pytest.mark.anyio
async def test_reconnect_route_validates_the_session_id(
    voice_client: tuple[AsyncClient, RecordingVoiceService],
) -> None:
    client, service = voice_client
    voice_session_id = str(uuid4())
    response = await client.post(f"/v1/student/assistant/voice-sessions/{voice_session_id}/token")
    assert response.status_code == 200
    assert service.calls[-1][0] == "reconnect"
    assert service.calls[-1][1][1] == voice_session_id

    invalid = await client.post("/v1/student/assistant/voice-sessions/not-a-uuid/token")
    assert invalid.status_code == 400


@pytest.mark.anyio
async def test_voice_routes_fail_closed_when_voice_is_not_configured() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/student/assistant/voice-sessions",
            json={
                "conversationId": str(uuid4()),
                "pageContext": {"path": "/", "label": "Home"},
            },
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ASSISTANT_VOICE_NOT_CONFIGURED"


@pytest.mark.anyio
async def test_internal_routes_require_the_voice_agent_bearer_token(
    voice_client: tuple[AsyncClient, RecordingVoiceService],
) -> None:
    client, service = voice_client
    voice_session_id = str(uuid4())

    for headers in (
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": INTERNAL_TOKEN},
        {"Authorization": "Basic abc"},
    ):
        response = await client.get(
            f"/internal/assistant/voice-sessions/{voice_session_id}",
            headers=headers,
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "VOICE_AGENT_AUTHENTICATION_FAILED"
    assert service.calls == []

    authorized = await client.get(
        f"/internal/assistant/voice-sessions/{voice_session_id}",
        headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
    )
    assert authorized.status_code == 200
    assert service.calls[-1] == ("get_internal", (voice_session_id,))


@pytest.mark.anyio
async def test_internal_token_fails_closed_when_unset() -> None:
    app = create_app(voice_service=RecordingVoiceService())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            f"/internal/assistant/voice-sessions/{uuid4()}",
            headers={"Authorization": "Bearer "},
        )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_internal_turn_route_validates_the_turn_and_forwards_it(
    voice_client: tuple[AsyncClient, RecordingVoiceService],
) -> None:
    client, service = voice_client
    voice_session_id = str(uuid4())
    headers = {"Authorization": f"Bearer {INTERNAL_TOKEN}"}
    turn = {
        "clientMessageId": str(uuid4()),
        "text": "What do I need to do next?",
        "inputMode": "voice",
        "pageContext": {"path": "/edward", "label": "Edward"},
        "livekitStreamId": "stream-1",
    }

    response = await client.post(
        f"/internal/assistant/voice-sessions/{voice_session_id}/turns",
        headers=headers,
        json=turn,
    )
    assert response.status_code == 200
    name, (session_id, payload, request_id) = service.calls[-1]
    assert name == "submit_turn"
    assert session_id == voice_session_id
    assert payload["text"] == "What do I need to do next?"
    assert payload["inputMode"] == "voice"
    assert payload["livekitStreamId"] == "stream-1"
    assert request_id

    for invalid in (
        {**turn, "inputMode": "text"},
        {**turn, "text": "   "},
        {**turn, "livekitStreamId": "bad stream id!"},
        {**turn, "clientMessageId": "short"},
    ):
        rejected = await client.post(
            f"/internal/assistant/voice-sessions/{voice_session_id}/turns",
            headers=headers,
            json=invalid,
        )
        assert rejected.status_code == 400

    ended = await client.post(
        f"/internal/assistant/voice-sessions/{voice_session_id}/end",
        headers=headers,
    )
    assert ended.status_code == 200
    assert service.calls[-1] == ("end_internal", (voice_session_id,))


class FakeVoiceRepository:
    def __init__(self, session: dict[str, Any] | None = None, *, eligible: bool = True) -> None:
        self.session = session
        self.eligible = eligible
        self.ended: list[str] = []
        self.created_with: dict[str, Any] | None = None

    async def is_student_eligible_for_voice(self, auth: AuthContext) -> bool:
        return self.eligible

    async def create(self, auth: AuthContext, **kwargs: Any) -> dict[str, Any]:
        self.created_with = kwargs
        expires_at = kwargs["expires_at"]
        self.session = _session_record(
            conversation_id=kwargs["conversation_id"],
            room_name=kwargs["room_name"],
            participant_identity=kwargs["participant_identity"],
            expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        )
        return dict(self.session)

    async def get_for_student(self, auth: AuthContext, voice_session_id: str) -> dict[str, Any]:
        assert self.session is not None
        return dict(self.session)

    async def get(self, voice_session_id: str) -> dict[str, Any]:
        assert self.session is not None
        return dict(self.session)

    async def end(self, voice_session_id: str) -> dict[str, Any]:
        self.ended.append(voice_session_id)
        assert self.session is not None
        self.session = {
            **self.session,
            "status": "ended",
            "endedAt": "2026-08-12T00:00:00.000Z",
        }
        return dict(self.session)


class FakeProvisioner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.provisioned: list[dict[str, str]] = []

    async def provision(self, *, room_name: str, voice_session_id: str) -> None:
        if self.fail:
            raise RuntimeError("livekit unavailable")
        self.provisioned.append({"room_name": room_name, "voice_session_id": voice_session_id})


class RecordingPlatformService:
    def __init__(self, result: object | None = None) -> None:
        self.calls: list[ServiceCall] = []
        self.result = result or {
            "conversationId": "c",
            "userMessageId": "u",
            "assistantMessageId": "a",
            "requestId": "r",
            "message": "answer",
            "suggestedActions": [],
            "contextReceipts": [],
            "widgets": [],
        }

    async def dispatch(self, call: ServiceCall) -> object:
        self.calls.append(call)
        return self.result


def _session_record(
    *,
    conversation_id: str = "11111111-1111-7111-8111-111111111111",
    room_name: str = "ev1_room_test",
    participant_identity: str = "ev1_participant_test",
    status: str = "active",
    expires_at: str | None = None,
) -> dict[str, Any]:
    return {
        "voiceSessionId": "22222222-2222-7222-8222-222222222222",
        "tenantId": "33333333-3333-7333-8333-333333333333",
        "studentId": "44444444-4444-7444-8444-444444444444",
        "actorId": "44444444-4444-7444-8444-444444444444",
        "conversationId": conversation_id,
        "provider": "livekit",
        "roomName": room_name,
        "participantIdentity": participant_identity,
        "pageContext": {"path": "/edward", "label": "Edward"},
        "status": status,
        "expiresAt": expires_at
        or (datetime.now(UTC) + timedelta(seconds=600)).isoformat().replace("+00:00", "Z"),
        "endedAt": None,
        "createdAt": "2026-08-11T00:00:00.000Z",
    }


def _auth() -> AuthContext:
    return AuthContext(
        tenant_id="33333333-3333-7333-8333-333333333333",
        student_id="44444444-4444-7444-8444-444444444444",
        actor_id="44444444-4444-7444-8444-444444444444",
        actor_type="student",
    )


def test_create_provisions_the_room_and_mints_livekit_join_credentials() -> None:
    repository = FakeVoiceRepository()
    provisioner = FakeProvisioner()
    service = VoiceSessionService(
        VOICE_SETTINGS,
        repository,  # type: ignore[arg-type]
        RecordingPlatformService(),
        provisioner=provisioner,
    )

    credentials = asyncio.run(
        service.create(
            _auth(),
            {
                "conversationId": "11111111-1111-7111-8111-111111111111",
                "pageContext": {"path": "/edward", "label": "Edward"},
            },
            "request-1",
        )
    )

    assert provisioner.provisioned == [
        {
            "room_name": repository.session["roomName"],  # type: ignore[index]
            "voice_session_id": credentials["voice_session_id"],
        }
    ]
    assert credentials["server_url"] == "wss://example.livekit.cloud"
    assert set(credentials) == {
        "server_url",
        "participant_token",
        "voice_session_id",
        "expires_at",
    }
    claims = jwt.decode(
        credentials["participant_token"],
        "lk_secret_test_0123456789abcdef",
        algorithms=["HS256"],
        options={"verify_exp": False},
    )
    assert claims["sub"] == repository.session["participantIdentity"]  # type: ignore[index]
    assert claims["video"]["room"] == repository.session["roomName"]  # type: ignore[index]
    assert claims["video"]["roomJoin"] is True
    assert claims["video"]["canPublishSources"] == ["microphone"]
    assert claims["exp"] - claims["nbf"] <= 600 + 60


def test_create_requires_an_accepted_offer() -> None:
    service = VoiceSessionService(
        VOICE_SETTINGS,
        FakeVoiceRepository(eligible=False),  # type: ignore[arg-type]
        RecordingPlatformService(),
        provisioner=FakeProvisioner(),
    )
    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.create(
                _auth(),
                {
                    "conversationId": "11111111-1111-7111-8111-111111111111",
                    "pageContext": {"path": "/", "label": "Home"},
                },
                "request-1",
            )
        )
    assert error.value.status_code == 403
    assert error.value.code == "ASSISTANT_VOICE_INELIGIBLE"


def test_create_ends_the_session_when_the_room_cannot_be_provisioned() -> None:
    repository = FakeVoiceRepository()
    service = VoiceSessionService(
        VOICE_SETTINGS,
        repository,  # type: ignore[arg-type]
        RecordingPlatformService(),
        provisioner=FakeProvisioner(fail=True),
    )
    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.create(
                _auth(),
                {
                    "conversationId": "11111111-1111-7111-8111-111111111111",
                    "pageContext": {"path": "/", "label": "Home"},
                },
                "request-1",
            )
        )
    assert error.value.status_code == 503
    assert error.value.code == "ASSISTANT_VOICE_WORKER_UNAVAILABLE"
    assert repository.ended  # the failed session cannot be joined later


def test_reconnect_rejects_ended_and_expired_sessions() -> None:
    ended = FakeVoiceRepository(_session_record(status="ended"))
    service = VoiceSessionService(
        VOICE_SETTINGS,
        ended,  # type: ignore[arg-type]
        RecordingPlatformService(),
        provisioner=FakeProvisioner(),
    )
    with pytest.raises(ApiError) as ended_error:
        asyncio.run(service.reconnect(_auth(), "any", "request-1"))
    assert ended_error.value.code == "ASSISTANT_VOICE_SESSION_ENDED"

    expired = FakeVoiceRepository(_session_record(expires_at="2020-01-01T00:00:00.000Z"))
    service = VoiceSessionService(
        VOICE_SETTINGS,
        expired,  # type: ignore[arg-type]
        RecordingPlatformService(),
        provisioner=FakeProvisioner(),
    )
    with pytest.raises(ApiError) as expired_error:
        asyncio.run(service.reconnect(_auth(), "any", "request-1"))
    assert expired_error.value.code == "ASSISTANT_VOICE_SESSION_EXPIRED"


def test_submit_turn_runs_the_canonical_ask_edward_operation() -> None:
    repository = FakeVoiceRepository(_session_record())
    platform = RecordingPlatformService()
    service = VoiceSessionService(
        VOICE_SETTINGS,
        repository,  # type: ignore[arg-type]
        platform,
        provisioner=FakeProvisioner(),
    )

    response = asyncio.run(
        service.submit_turn(
            "22222222-2222-7222-8222-222222222222",
            {
                "clientMessageId": "voice-turn-0001",
                "text": "Do I have any holds?",
                "inputMode": "voice",
                "pageContext": {"path": "/edward", "label": "Edward"},
                "livekitStreamId": "stream-7",
            },
            "request-9",
        )
    )

    call = platform.calls[-1]
    assert call.operation == "student.ask_edward"
    assert call.auth is not None
    assert call.auth.student_id == "44444444-4444-7444-8444-444444444444"
    assert call.auth.actor_type == "student"
    assert call.payload["message"] == "Do I have any holds?"
    assert call.payload["inputMode"] == "voice"
    assert call.payload["conversationId"] == "11111111-1111-7111-8111-111111111111"
    assert call.payload["clientMessageId"] == "voice-turn-0001"
    assert call.request_id == "request-9"
    assert response["message"] == "answer"


def test_internal_details_expose_only_the_session_binding() -> None:
    repository = FakeVoiceRepository(_session_record())
    service = VoiceSessionService(
        VOICE_SETTINGS,
        repository,  # type: ignore[arg-type]
        RecordingPlatformService(),
        provisioner=FakeProvisioner(),
    )
    details = asyncio.run(service.get_internal("22222222-2222-7222-8222-222222222222"))
    assert set(details) == {
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
    }
    assert "studentId" not in details
    assert "tenantId" not in details


def test_real_postgres_voice_session_round_trip_is_tenant_isolated() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run PostgreSQL integration tests")

    schema = f"voice_{uuid4().hex}"
    tenant_id = str(uuid4())
    student_id = str(uuid4())
    conversation_id = str(uuid4())

    async def scenario() -> None:
        admin = create_async_engine(normalize_database_url(database_url))
        engine: AsyncEngine | None = None
        try:
            async with admin.begin() as connection:
                await connection.execute(text(f"CREATE SCHEMA {schema}"))
                for statement in (
                    f"""CREATE TABLE {schema}.student (
                      id uuid NOT NULL, tenant_id uuid NOT NULL,
                      PRIMARY KEY (tenant_id, id)
                    )""",
                    f"""CREATE TABLE {schema}.admission_offer (
                      id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
                      student_id uuid NOT NULL, status varchar(24) NOT NULL
                    )""",
                    f"""CREATE TABLE {schema}.assistant_conversation (
                      id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
                      student_id uuid NOT NULL,
                      status varchar(16) NOT NULL DEFAULT 'active',
                      page_path varchar(240), page_label varchar(240),
                      created_at timestamptz NOT NULL DEFAULT now(),
                      last_message_at timestamptz NOT NULL DEFAULT now(),
                      closed_at timestamptz
                    )""",
                    f"""CREATE TABLE {schema}.assistant_voice_session (
                      id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
                      student_id uuid NOT NULL, actor_id uuid NOT NULL,
                      conversation_id uuid NOT NULL,
                      provider varchar(24) NOT NULL DEFAULT 'livekit',
                      room_name varchar(255) NOT NULL UNIQUE,
                      participant_identity varchar(255) NOT NULL,
                      page_path varchar(240), page_label varchar(240),
                      status varchar(24) NOT NULL DEFAULT 'active',
                      expires_at timestamptz NOT NULL,
                      ended_at timestamptz,
                      created_at timestamptz NOT NULL DEFAULT now(),
                      updated_at timestamptz NOT NULL DEFAULT now()
                    )""",
                ):
                    await connection.execute(text(statement))
                await connection.execute(
                    text(
                        f"INSERT INTO {schema}.student (id, tenant_id) VALUES (:id, :tenant_id)"  # noqa: S608
                    ),
                    {"id": student_id, "tenant_id": tenant_id},
                )
                await connection.execute(
                    text(
                        f"""INSERT INTO {schema}.assistant_conversation
                        (id, tenant_id, student_id) VALUES (:id, :tenant_id, :student_id)"""  # noqa: S608
                    ),
                    {"id": conversation_id, "tenant_id": tenant_id, "student_id": student_id},
                )

            engine = create_async_engine(
                normalize_database_url(database_url),
                connect_args={"server_settings": {"search_path": schema}},
            )
            repository = PostgresVoiceSessionRepository(engine)
            auth = AuthContext(
                tenant_id=tenant_id,
                student_id=student_id,
                actor_id=student_id,
                actor_type="student",
            )

            assert not await repository.is_student_eligible_for_voice(auth)
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        """INSERT INTO admission_offer (id, tenant_id, student_id, status)
                        VALUES (:id, :tenant_id, :student_id, 'accepted')"""
                    ),
                    {"id": str(uuid4()), "tenant_id": tenant_id, "student_id": student_id},
                )
            assert await repository.is_student_eligible_for_voice(auth)

            session = await repository.create(
                auth,
                conversation_id=conversation_id,
                room_name="ev1_room_roundtrip",
                participant_identity="ev1_participant_roundtrip",
                page_path="/edward",
                page_label="Edward",
                expires_at=datetime.now(UTC) + timedelta(seconds=600),
            )
            assert session["status"] == "active"
            assert session["conversationId"] == conversation_id
            assert session["actorId"] == student_id

            owned = await repository.get_for_student(auth, session["voiceSessionId"])
            assert owned["roomName"] == "ev1_room_roundtrip"

            foreign = AuthContext(
                tenant_id=str(uuid4()),
                student_id=str(uuid4()),
                actor_id=str(uuid4()),
                actor_type="student",
            )
            with pytest.raises(ApiError) as not_found:
                await repository.get_for_student(foreign, session["voiceSessionId"])
            assert not_found.value.status_code == 404

            with pytest.raises(ApiError) as missing_conversation:
                await repository.create(
                    auth,
                    conversation_id=str(uuid4()),
                    room_name="ev1_room_other",
                    participant_identity="ev1_participant_other",
                    page_path=None,
                    page_label=None,
                    expires_at=datetime.now(UTC) + timedelta(seconds=600),
                )
            assert missing_conversation.value.code == "ASSISTANT_CONVERSATION_NOT_FOUND"

            ended = await repository.end(session["voiceSessionId"])
            assert ended["status"] == "ended"
            assert ended["endedAt"] is not None

            internal = await repository.get(session["voiceSessionId"])
            assert internal["status"] == "ended"
        finally:
            if engine is not None:
                await engine.dispose()
            try:
                async with admin.begin() as connection:
                    await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            finally:
                await admin.dispose()

    asyncio.run(scenario())
