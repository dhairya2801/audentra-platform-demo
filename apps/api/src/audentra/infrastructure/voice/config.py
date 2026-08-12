"""Voice configuration consumed by the bootstrap settings snapshot."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """LiveKit-backed Edward voice. Present only when fully configured."""

    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    agent_internal_token: str
    agent_name: str
    session_ttl_seconds: int
    token_ttl_seconds: int
