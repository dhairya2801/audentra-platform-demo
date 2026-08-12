import { randomUUID } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import {
  VoiceBackendClient,
  VoiceBackendError,
} from "../src/backend-client.js";
import type {
  CanonicalAssistantResponse,
  FinalizedVoiceTurn,
  Logger,
  VoiceSessionBinding,
} from "../src/types.js";

const voiceSessionId = "00000000-0000-4000-8000-000000000101";
const binding: VoiceSessionBinding = {
  voiceSessionId,
  conversationId: "00000000-0000-4000-8000-000000000102",
  provider: "livekit",
  roomName: "ev1_room_opaque",
  participantIdentity: "ev1_participant_opaque",
  pageContext: { path: "/enrollment", label: "Enrollment" },
  status: "active",
  expiresAt: "2026-08-03T18:00:00.000Z",
  endedAt: null,
  createdAt: "2026-08-03T17:00:00.000Z",
};
const canonicalResponse: CanonicalAssistantResponse = {
  conversationId: binding.conversationId,
  userMessageId: "00000000-0000-4000-8000-000000000103",
  assistantMessageId: "00000000-0000-4000-8000-000000000104",
  requestId: "req-voice-1",
  message: "Your next step is to complete About You.",
  provider: "guided",
  model: null,
  usage: null,
  suggestedActions: [],
  contextReceipts: [{ source: "onboarding" }],
  widgets: [],
  studentAssistant: {
    requestType: "next_action",
    graphExecution: { graphVersion: "student-onboarding-v1" },
  },
};
const logger: Logger = {
  debug: vi.fn(),
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
};

describe("internal voice backend client", () => {
  it("performs trusted session lookup using only the route ID and bearer token", async () => {
    const fetch = vi.fn(async () => jsonResponse(binding));
    const client = createClient(fetch as unknown as typeof globalThis.fetch);

    await expect(client.lookupSession(voiceSessionId)).resolves.toEqual(binding);
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe(
      `http://api:4000/internal/assistant/voice-sessions/${voiceSessionId}`,
    );
    expect(init).toMatchObject({
      method: "GET",
      headers: {
        authorization: "Bearer test-internal-token-at-least-24-characters",
      },
    });
  });

  it("retries a finalized turn with the original clientMessageId", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ error: { code: "UPSTREAM_UNAVAILABLE" } }, 503),
      )
      .mockResolvedValueOnce(jsonResponse(canonicalResponse));
    const client = createClient(fetch as unknown as typeof globalThis.fetch, {
      maxAttempts: 2,
    });
    const turn: FinalizedVoiceTurn = {
      clientMessageId: randomUUID(),
      text: "What should I do next?",
      inputMode: "voice",
      pageContext: binding.pageContext,
      livekitStreamId: "TR_opaque_stream",
    };

    await expect(
      client.submitFinalizedTurn(voiceSessionId, turn),
    ).resolves.toEqual(canonicalResponse);
    expect(fetch).toHaveBeenCalledTimes(2);
    const bodies = fetch.mock.calls.map((call) =>
      JSON.parse(String(call[1]?.body)),
    );
    expect(bodies[0].clientMessageId).toBe(turn.clientMessageId);
    expect(bodies[1]).toEqual(bodies[0]);
    expect(bodies[0]).not.toHaveProperty("studentId");
  });

  it("times out the complete internal response", async () => {
    const fetch: typeof globalThis.fetch = async (_input, init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener(
          "abort",
          () => reject(new DOMException("aborted", "AbortError")),
          { once: true },
        );
      });
    const client = createClient(fetch, { timeoutMs: 5, maxAttempts: 1 });

    await expect(client.lookupSession(voiceSessionId)).rejects.toMatchObject({
      code: "timeout",
      retryable: true,
    });
  });

  it("does not retry an unauthorized response", async () => {
    const fetch = vi.fn(async () =>
      jsonResponse(
        { error: { code: "VOICE_AGENT_AUTHENTICATION_FAILED" } },
        401,
      ),
    );
    const client = createClient(fetch as unknown as typeof globalThis.fetch, {
      maxAttempts: 4,
    });

    await expect(client.lookupSession(voiceSessionId)).rejects.toEqual(
      expect.objectContaining<Partial<VoiceBackendError>>({
        code: "unauthorized",
        retryable: false,
        status: 401,
      }),
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

function createClient(
  fetch: typeof globalThis.fetch,
  overrides: { timeoutMs?: number; maxAttempts?: number } = {},
): VoiceBackendClient {
  return new VoiceBackendClient({
    baseUrl: "http://api:4000",
    token: "test-internal-token-at-least-24-characters",
    timeoutMs: overrides.timeoutMs ?? 1_000,
    maxAttempts: overrides.maxAttempts ?? 1,
    retryBaseMs: 0,
    logger,
    fetch,
    sleep: async () => undefined,
  });
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}
