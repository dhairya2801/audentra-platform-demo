import type { AssistantVoiceSessionDetails } from "@vv/contracts";
import type { Room, RpcInvocationData } from "@livekit/rtc-node";
import { describe, expect, it, vi } from "vitest";
import {
  LiveKitBrowserTransport,
  stopSpeakingRpcMethod,
  voiceTopics,
} from "../src/livekit-transport.js";
import type { CanonicalAssistantResponse, Logger } from "../src/types.js";

const binding: AssistantVoiceSessionDetails = {
  voiceSessionId: "00000000-0000-4000-8000-000000000301",
  conversationId: "00000000-0000-4000-8000-000000000302",
  provider: "livekit",
  roomName: "ev1_room_transport",
  participantIdentity: "ev1_participant_transport",
  pageContext: { path: "/onboarding", label: "Onboarding" },
  status: "active",
  expiresAt: "2026-08-03T18:00:00.000Z",
  endedAt: null,
  createdAt: "2026-08-03T17:00:00.000Z",
};
const response: CanonicalAssistantResponse = {
  conversationId: binding.conversationId,
  userMessageId: "00000000-0000-4000-8000-000000000303",
  assistantMessageId: "00000000-0000-4000-8000-000000000304",
  requestId: "req-browser-event",
  message: "Canonical answer",
  provider: "guided",
  model: null,
  usage: null,
  suggestedActions: [],
  contextReceipts: [],
  widgets: [],
  studentAssistant: {
    requestType: "completed_steps",
    graphExecution: { graphVersion: "student-onboarding-v1" },
  },
};

describe("LiveKit browser transport", () => {
  it("publishes the unmodified canonical response event", async () => {
    const fixture = createTransport();

    await fixture.transport.publishCanonicalResponse(response);

    expect(fixture.local.sendText).toHaveBeenCalledTimes(1);
    const [text, options] = fixture.local.sendText.mock.calls[0]!;
    expect(options).toEqual({ topic: voiceTopics.response });
    expect(JSON.parse(text)).toEqual({
      type: voiceTopics.response,
      voiceSessionId: binding.voiceSessionId,
      response,
    });
  });

  it("registers the stop-speaking RPC and restricts it to the bound participant", async () => {
    const fixture = createTransport();
    const stop = vi.fn(() => true);
    fixture.transport.registerStopSpeaking(stop);
    const handler = fixture.local.rpcHandlers.get(stopSpeakingRpcMethod)!;

    await expect(
      handler(rpcData(binding.participantIdentity)),
    ).resolves.toBe('{"stopped":true}');
    await expect(handler(rpcData("other-participant"))).resolves.toBe(
      '{"stopped":false}',
    );
    expect(stop).toHaveBeenCalledTimes(1);
  });
});

function createTransport() {
  const rpcHandlers = new Map<
    string,
    (data: RpcInvocationData) => Promise<string>
  >();
  const local = {
    sendText: vi.fn(async () => undefined),
    setAttributes: vi.fn(async () => undefined),
    publishTranscription: vi.fn(async () => undefined),
    registerRpcMethod: vi.fn(
      (method: string, handler: (data: RpcInvocationData) => Promise<string>) => {
        rpcHandlers.set(method, handler);
      },
    ),
    unregisterRpcMethod: vi.fn(),
    rpcHandlers,
  };
  const room = { localParticipant: local } as unknown as Room;
  const logger: Logger = {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
  return {
    transport: new LiveKitBrowserTransport(room, binding, logger),
    local,
  };
}

function rpcData(callerIdentity: string): RpcInvocationData {
  return {
    requestId: "rpc-request",
    callerIdentity,
    payload: "{}",
    responseTimeout: 5_000,
  };
}
