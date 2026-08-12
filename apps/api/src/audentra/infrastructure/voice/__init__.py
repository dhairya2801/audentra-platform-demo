"""Edward voice sessions: LiveKit room binding for the student assistant.

Ported from VV_Edgent-voice. A voice session is a short-lived, student-owned
binding between one assistant conversation and one LiveKit room. The browser
receives only join credentials; the voice agent worker resolves the session
through the bearer-authenticated internal API and submits finalized speech
turns, which run the exact same `student.ask_edward` operation as typed
messages — voice never gets its own answer path.
"""

from .service import (
    UnavailableVoiceSessionService,
    VoiceSessionService,
    VoiceSessionServiceProtocol,
)

__all__ = [
    "UnavailableVoiceSessionService",
    "VoiceSessionService",
    "VoiceSessionServiceProtocol",
]
