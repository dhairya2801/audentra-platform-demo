# Student Assistant LiveKit voice worker

This service is the media adapter for the shared Student Assistant. It does not
contain an LLM agent, prompts, student-data reads, or student workflows. A
finalized AssemblyAI transcript is submitted to the API's private voice-turn
route, and the complete canonical response produced by the shared Student
Assistant Graph is published to the room before its `message` is spoken by
Cartesia.

## Trust and request contract

The worker accepts one trusted dispatch value:

```json
{ "voiceSessionId": "00000000-0000-4000-8000-000000000001" }
```

It reads that value from `ctx.job.metadata`, then calls:

```text
GET /internal/assistant/voice-sessions/:voiceSessionId
```

The returned `roomName`, `participantIdentity`, `conversationId`, and
`pageContext` are authoritative. A room mismatch fails closed, and microphone
tracks from any participant other than the bound participant are ignored. The
worker never accepts or chooses a tenant, student, conversation, room, or page
context from speech or browser data.

Only final STT events call:

```text
POST /internal/assistant/voice-sessions/:voiceSessionId/turns
Authorization: Bearer $VOICE_AGENT_INTERNAL_TOKEN
Content-Type: application/json

{
  "clientMessageId": "one UUID assigned to this finalized turn",
  "text": "the finalized transcript",
  "inputMode": "voice",
  "pageContext": { "path": "from lookup", "label": "from lookup" },
  "livekitStreamId": "the subscribed LiveKit microphone track SID"
}
```

Transient retries reuse the complete request, including the original
`clientMessageId`. Interim captions are published with LiveKit's transcription
API and never reach this endpoint.

Normal shutdown and bound-participant disconnect call:

```text
POST /internal/assistant/voice-sessions/:voiceSessionId/end
```

## Browser transport contract

JSON text streams use these topics:

- `student-assistant.voice.response.v1` contains `{ type, voiceSessionId,
  response }`. `response` is the unmodified canonical backend object, including
  canonical IDs, message, structured `studentAssistant` result, safe suggested
  actions, context receipts, and allowed widgets.
- `student-assistant.voice.error.v1` contains a safe `{ code, message,
  recoverable }` error. It never contains provider credentials or student
  records.
- `student-assistant.voice.state.v1` contains `initializing`, `listening`,
  `thinking`, `speaking`, `reconnecting`, or `error`.

The same state is published as `vv.voice.state`. LiveKit-native states are also
published as `lk.agent.state` when the value is one of LiveKit's supported
`initializing`, `listening`, `thinking`, or `speaking` states.

The bound browser participant can call the RPC method
`student-assistant.stop-speaking.v1`. The payload is ignored. The response is
`{"stopped":true}` when speech was active and canceled, otherwise
`{"stopped":false}`. Calls from other participant identities do not stop
speech.

Natural barge-in and the stop RPC abort the current Cartesia request and clear
unsent LiveKit audio. They do not issue a delete, alter graph history, repeat a
backend turn, or remove the already-persisted assistant message. The complete
canonical response remains available in the response event.

## Configuration

All credential values are required at startup. Do not commit them.

| Variable | Purpose |
| --- | --- |
| `LIVEKIT_URL` | LiveKit `ws://` or `wss://` endpoint |
| `LIVEKIT_API_KEY` | LiveKit agent worker API key |
| `LIVEKIT_API_SECRET` | LiveKit agent worker API secret |
| `VOICE_AGENT_INTERNAL_API_URL` | API origin, for example `http://localhost:4000` |
| `VOICE_AGENT_INTERNAL_TOKEN` | Dedicated 24–512 character internal bearer token |
| `ASSEMBLYAI_API_KEY` | AssemblyAI streaming STT credential |
| `CARTESIA_API_KEY` | Cartesia streaming TTS credential |
| `CARTESIA_VOICE_ID` | Cartesia voice UUID |
| `VOICE_AGENT_DIAGNOSTIC_TONE` | Development-only: publish a short generated PCM tone through the agent audio track (`false` by default; ignored in production) |
| `VOICE_AGENT_NAME` | Explicit dispatch name; defaults to `student-assistant-voice` |
| `VOICE_AGENT_INTERNAL_API_TIMEOUT_MS` | Complete internal response timeout; defaults to `15000` |
| `VOICE_AGENT_INTERNAL_API_MAX_ATTEMPTS` | Transient attempts; defaults to `2` |
| `VOICE_AGENT_INTERNAL_API_RETRY_BASE_MS` | Linear retry base delay; defaults to `250` |
| `VOICE_AGENT_SHUTDOWN_TIMEOUT_MS` | LiveKit worker drain/process timeout; defaults to `10000` |
| `LOG_LEVEL` | `debug`, `info`, `warn`, or `error` |

The API and worker must use the same `VOICE_AGENT_INTERNAL_TOKEN`. Trusted
server-side LiveKit dispatch must target `VOICE_AGENT_NAME` and attach the
`voiceSessionId` metadata shown above.

## Build and run

From the repository root:

```bash
npm install
npm --workspace @vv/voice-agent run typecheck
npm --workspace @vv/voice-agent run build
npm --workspace @vv/voice-agent start
```

The exact production entry command after dependencies and build output exist
is:

```bash
node apps/voice-agent/dist/index.js start
```

For the legacy local development command, use
`npm run dev:voice-agent`. Current LiveKit guidance prefers `lk agent dev` for
the development lifecycle.

Build the dedicated container from the repository root:

```bash
docker build -f apps/voice-agent/Dockerfile -t vv-voice-agent .
```

## Testing

The normal suite uses only in-memory fakes and synthetic HTTP responses. It
does not read provider environment variables, open provider connections, use
paid credentials, or persist audio:

```bash
npm --workspace @vv/voice-agent test
```

A real-provider smoke test is intentionally manual:

1. Configure a LiveKit project, AssemblyAI, Cartesia, and the running internal
   API with matching credentials.
2. Create an eligible backend voice session and dispatch this agent with its
   `voiceSessionId` in job metadata.
3. Join using the backend-issued participant token and ask: “What onboarding
   steps am I yet to do?”, “What have I already completed?”, “What documents
   am I missing?”, and “What should I do next?”
4. Confirm interim captions do not create turns, each final question creates
   one canonical response event, and audio speaks the same `message`.
5. Interrupt naturally and through the stop RPC. Confirm audio stops while the
   full response remains visible.
6. Ask “I want to update my housing selection.” Confirm the worker only speaks
   the backend's safe response and performs no write.

V1 deliberately waits for the backend's complete response before starting
TTS; model-token streaming is not implemented here. Telephony, MCP, browser
UI, conversation persistence, and write actions are also outside this service.
