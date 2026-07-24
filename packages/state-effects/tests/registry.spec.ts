import { describe, expect, it } from "vitest";
import {
  stateEffects,
  stateFieldOwnership,
  validateStateEffectRegistry,
} from "../src/index";
import type { StateEffect, StateFieldOwnership } from "../src/types";

describe("CRM State Effect Registry", () => {
  it("has one owner per field, no synchronous cycles, and safe consumers", () => {
    expect(
      validateStateEffectRegistry({
        owners: stateFieldOwnership,
        effects: stateEffects,
      }),
    ).toEqual([]);
  });

  it("rejects duplicate ownership", () => {
    const duplicate = [
      ...stateFieldOwnership,
      {
        ...stateFieldOwnership[0],
        owner: "financials",
      },
    ] satisfies readonly StateFieldOwnership[];
    expect(
      validateStateEffectRegistry({
        owners: duplicate,
        effects: stateEffects,
      }).map((issue) => issue.code),
    ).toContain("DUPLICATE_FIELD_OWNER");
  });

  it("rejects synchronous cycles", () => {
    const base = stateEffects[0];
    const effects = [
      {
        ...base,
        id: "test.a",
        synchronousCalls: ["test.b"],
      },
      {
        ...base,
        id: "test.b",
        synchronousCalls: ["test.a"],
      },
    ] satisfies readonly StateEffect[];
    expect(
      validateStateEffectRegistry({
        owners: stateFieldOwnership,
        effects,
      }).map((issue) => issue.code),
    ).toContain("SYNCHRONOUS_CYCLE");
  });

  it("rejects event consumers without deduplication", () => {
    const handler = {
      ...stateEffects.find(
        (effect) => effect.kind === "event_handler",
      )!,
      idempotency: {
        required: false,
        key: null,
        scope: "not_applicable",
      },
    } satisfies StateEffect;
    expect(
      validateStateEffectRegistry({
        owners: stateFieldOwnership,
        effects: [handler],
      }).map((issue) => issue.code),
    ).toContain("EVENT_HANDLER_WITHOUT_DEDUPLICATION");
  });
});
