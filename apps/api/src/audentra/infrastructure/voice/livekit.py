"""LiveKit adapters: participant token minting and room provisioning.

The token is a locally signed JWT; only room provisioning talks to the
LiveKit control plane. Provisioning dispatches the named voice agent into the
room with the voice session id as metadata, which is the only identity the
worker ever receives directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from livekit import api as livekit_api

from audentra.infrastructure.voice.config import VoiceSettings


class VoiceRoomProvisioner(Protocol):
    async def provision(self, *, room_name: str, voice_session_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class MintedParticipantToken:
    token: str
    ttl_seconds: int


def mint_participant_token(
    settings: VoiceSettings,
    *,
    room_name: str,
    participant_identity: str,
    ttl_seconds: int,
    token_id: str,
) -> MintedParticipantToken:
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(participant_identity)
        .with_ttl(timedelta(seconds=ttl_seconds))
        .with_metadata(json.dumps({"tokenId": token_id}))
        .with_grants(
            livekit_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_subscribe=True,
                can_publish=True,
                can_publish_data=True,
                can_publish_sources=["microphone"],
                can_update_own_metadata=False,
            )
        )
    )
    return MintedParticipantToken(token=token.to_jwt(), ttl_seconds=ttl_seconds)


class LiveKitVoiceRoomProvisioner:
    """Creates the room and dispatches the voice agent worker into it."""

    def __init__(self, settings: VoiceSettings) -> None:
        self._settings = settings

    async def provision(self, *, room_name: str, voice_session_id: str) -> None:
        client = livekit_api.LiveKitAPI(
            url=_http_url(self._settings.livekit_url),
            api_key=self._settings.livekit_api_key,
            api_secret=self._settings.livekit_api_secret,
        )
        try:
            await client.room.create_room(
                livekit_api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=60,
                    departure_timeout=60,
                    max_participants=2,
                    agents=[
                        livekit_api.RoomAgentDispatch(
                            agent_name=self._settings.agent_name,
                            metadata=json.dumps({"voiceSessionId": voice_session_id}),
                        )
                    ],
                )
            )
        finally:
            await client.aclose()


def _http_url(websocket_url: str) -> str:
    if websocket_url.startswith("wss://"):
        return "https://" + websocket_url.removeprefix("wss://")
    if websocket_url.startswith("ws://"):
        return "http://" + websocket_url.removeprefix("ws://")
    return websocket_url
