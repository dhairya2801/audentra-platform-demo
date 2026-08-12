import { describe, expect, it } from "vitest";
import type { JobContext } from "@livekit/agents";
import {
  assertRoomBinding,
  parseDispatchMetadata,
} from "../src/agent.js";
import type { VoiceSessionBinding } from "../src/types.js";

const binding: VoiceSessionBinding = {
  voiceSessionId: "00000000-0000-4000-8000-000000000401",
  conversationId: "00000000-0000-4000-8000-000000000402",
  provider: "livekit",
  roomName: "ev1_room_bound",
  participantIdentity: "ev1_participant_bound",
  pageContext: { path: "/edward", label: "Edward" },
  status: "active",
  expiresAt: "2026-08-03T22:00:00.000Z",
};

describe("trusted LiveKit dispatch metadata", () => {
  it("accepts only a UUID voiceSessionId as the worker lookup key", () => {
    expect(
      parseDispatchMetadata(
        JSON.stringify({
          voiceSessionId: "00000000-0000-4000-8000-000000000401",
          tenantId: "ignored",
          studentId: "ignored",
          conversationId: "ignored",
        }),
      ),
    ).toBe("00000000-0000-4000-8000-000000000401");
  });

  it.each(["not-json", "{}", '{"voiceSessionId":"not-a-uuid"}'])(
    "rejects invalid metadata: %s",
    (metadata) => {
      expect(() => parseDispatchMetadata(metadata)).toThrow(/metadata/);
    },
  );

  it("uses the trusted dispatched room before the worker connects", () => {
    expect(() =>
      assertRoomBinding(
        {
          room: { name: "" },
          job: { room: { name: binding.roomName } },
        } as JobContext,
        binding,
      ),
    ).not.toThrow();
  });

  it("rejects a dispatched room that differs from the backend binding", () => {
    expect(() =>
      assertRoomBinding(
        {
          room: { name: "" },
          job: { room: { name: "ev1_room_wrong" } },
        } as JobContext,
        binding,
      ),
    ).toThrow(/room does not match/);
  });
});
