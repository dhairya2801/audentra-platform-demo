import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import { JsonStateStore } from "../src/store.js";

const fixedClock = () => new Date("2026-07-24T12:00:00.000Z");

describe("JsonStateStore", () => {
  it("creates deterministic seed state and persists mutations atomically", async () => {
    const directory = await mkdtemp(join(tmpdir(), "vv-demo-store-"));
    const dataFile = join(directory, "state.json");
    const first = new JsonStateStore(dataFile, fixedClock);
    const seeded = await first.initialize();

    assert.equal(seeded.offer.status, "offered");
    assert.equal(seeded.onboarding.currentStep, "offer");
    assert.equal(seeded.fixture.revision, 1);

    await first.transact((draft) => {
      draft.profile.preferredName = "Ari";
      return { saved: true };
    });

    const second = new JsonStateStore(dataFile, fixedClock);
    const restored = await second.initialize();
    assert.equal(restored.profile.preferredName, "Ari");
    assert.equal(restored.fixture.revision, 2);
    assert.equal(restored.fixture.updatedAt, fixedClock().toISOString());

    const onDisk = JSON.parse(await readFile(dataFile, "utf8"));
    assert.equal(onDisk.profile.preferredName, "Ari");
  });

  it("serializes concurrent writes without losing either change", async () => {
    const directory = await mkdtemp(join(tmpdir(), "vv-demo-queue-"));
    const store = new JsonStateStore(join(directory, "state.json"), fixedClock);
    await store.initialize();

    await Promise.all([
      store.transact(async (draft) => {
        await new Promise((resolve) => setTimeout(resolve, 10));
        draft.activities.push({ eventId: "first" });
      }),
      store.transact((draft) => {
        draft.activities.push({ eventId: "second" });
      }),
    ]);

    assert.deepEqual(
      store.snapshot().activities.map((event) => event.eventId),
      ["first", "second"],
    );
    assert.equal(store.snapshot().fixture.revision, 3);
  });

  it("reset restores the exact fixture boundary", async () => {
    const directory = await mkdtemp(join(tmpdir(), "vv-demo-reset-"));
    const store = new JsonStateStore(join(directory, "state.json"), fixedClock);
    const original = await store.initialize();
    await store.transact((draft) => {
      draft.offer.status = "accepted";
    });

    const reset = await store.reset();
    assert.deepEqual(reset, original);
  });
});
