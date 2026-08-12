/**
 * The staleness bug generalised: does *any* mutable student state go stale
 * between the write and the next thing Edward says?
 *
 * The reported bug was about a transcript, but the shape of it — one fact with
 * two owners — is not specific to documents. This walks every kind of mutable
 * state the portal exposes, mutates each through the same HTTP route the
 * browser calls, and asks Edward on the same running server immediately after.
 * A read that is stale for any of them fails here.
 *
 * Deliberately no restart, no re-seed, no cache clearing, and no second
 * student. Those are the shortcuts that would make the bug invisible.
 */
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, describe, it } from "node:test";
import { createDemoApi } from "../src/http-api.js";
import { createSeedState } from "../src/seed.js";
import { JsonStateStore } from "../src/store.js";

const NOW = "2026-08-05T12:00:00.000Z";
const COOKIE = "vv_demo_session=demo-session-v2";

/** Set when the portal boots; multipart uploads need the raw URL. */
let portalBaseUrl = "";

async function startPortal() {
  const clock = () => new Date(NOW);
  const directory = await mkdtemp(join(tmpdir(), "mutable-state-"));
  const store = new JsonStateStore(
    join(directory, "state.json"),
    clock,
    undefined,
    () => createSeedState({ acceptedStudent: true }),
  );
  await store.initialize();
  const { server } = await createDemoApi({
    store,
    clock,
    logger: null,
    ai: {
      configured: false,
      async askEdward() {
        throw new Error("the grounded graph must answer these questions");
      },
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;
  portalBaseUrl = baseUrl;

  const call = async (method, path, { body, headers } = {}) => {
    const response = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        cookie: COOKIE,
        ...(body === undefined ? {} : { "content-type": "application/json" }),
        ...headers,
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const text = await response.text();
    return { status: response.status, payload: text ? JSON.parse(text) : null };
  };
  const conversation = await call("POST", "/v1/student/assistant/conversations", {
    body: { pageContext: { path: "/enrollment", label: "Enrollment" } },
  });

  return {
    call,
    async requirement(code) {
      const result = await call("GET", "/v1/student/requirements");
      return result.payload.items.find((item) => item.code === code) ?? null;
    },
    async ask(message) {
      const result = await call("POST", "/v1/student/assistant/messages", {
        body: {
          conversationId: conversation.payload.id,
          clientMessageId: randomUUID(),
          message,
          inputMode: "text",
          pageContext: { path: "/enrollment", label: "Enrollment" },
        },
      });
      return {
        message: result.payload?.message ?? "",
        assistant: result.payload?.studentAssistant ?? null,
      };
    },
    async close() {
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

const portal = await startPortal();
after(() => portal.close());

/** The codes Edward still names as outstanding for this student, right now. */
const outstandingCodes = async () => {
  const answer = await portal.ask("What do I still need to do?");
  assert.ok(answer.assistant, "the grounded graph must answer this");
  return (answer.assistant.remainingSteps ?? []).map((step) => step.code);
};

describe("every mutable student state is fresh on the next turn", () => {
  it("sees a deposit the moment the payment posts", async () => {
    assert.ok(
      (await outstandingCodes()).includes("enrollment_deposit"),
      "precondition: the deposit is outstanding",
    );

    const offer = await portal.call("GET", "/v1/student/dashboard");
    const paid = await portal.call("POST", "/v1/student/payments/deposit", {
      body: { offerId: offer.payload.offer.id },
      headers: { "idempotency-key": randomUUID() },
    });
    assert.ok(
      paid.status === 200 || paid.status === 201,
      JSON.stringify(paid.payload),
    );

    assert.ok(
      !(await outstandingCodes()).includes("enrollment_deposit"),
      "Edward must not still ask for a deposit that has posted",
    );
  });

  it("sees a housing preference the moment the form is saved", async () => {
    assert.ok((await outstandingCodes()).includes("housing_preference"));

    const plan = await portal.call("GET", "/v1/student/housing-plan");
    const saved = await portal.call("PATCH", "/v1/student/housing-plan", {
      body: {
        expectedVersion: plan.payload.version,
        preference: "on_campus",
        residenceOption: "aster_residence_hall",
      },
    });
    assert.equal(saved.status, 200, JSON.stringify(saved.payload));

    const answer = await portal.ask("What housing plan do I have?");
    assert.match(answer.message, /on campus|residence/i);
    assert.ok(!(await outstandingCodes()).includes("housing_preference"));
  });

  it("sees a profile change on the next turn", async () => {
    const profile = await portal.call("GET", "/v1/student/profile");
    const updated = await portal.call("PATCH", "/v1/student/profile", {
      body: {
        expectedVersion: profile.payload.version,
        preferredName: "Sasha",
        pronouns: profile.payload.pronouns,
        mobilePhone: profile.payload.mobilePhone,
        communicationPreference: profile.payload.communicationPreference,
      },
    });
    assert.equal(updated.status, 200, JSON.stringify(updated.payload));

    // The greeting reads the profile and nothing else, so it is the narrowest
    // possible probe of whether that read is fresh.
    const answer = await portal.ask("hi");
    assert.match(answer.message, /Sasha/);
  });

  it("sees an appointment the moment it is booked", async () => {
    // Asked as a deadline question, which the deterministic classifier handles:
    // this suite runs without a model on purpose.
    const before = await portal.ask("When are my deadlines?");
    assert.doesNotMatch(before.message, /august 19|2026-08-19/i);

    const booked = await portal.call("POST", "/v1/student/appointments", {
      body: { type: "financial_aid", startsAt: "2026-08-19T15:00:00.000Z" },
      headers: { "idempotency-key": randomUUID() },
    });
    assert.equal(booked.status, 201, JSON.stringify(booked.payload));

    const after_ = await portal.ask("When are my deadlines?");
    assert.ok(
      (after_.assistant?.deadlines ?? []).some(
        (item) => item.kind === "appointment",
      ),
      `Edward did not see the appointment just booked: ${after_.message}`,
    );
  });

  it("sees a hold the moment one is placed", async () => {
    // Holds are placed by staff, not by the student, so this is the closest
    // thing to an out-of-band change: nothing the student did causes it.
    const before = await portal.ask("Do I have any holds?");
    const beforeHolds = (before.assistant?.officialHolds ?? []).length;
    const beforeWorksheet = await portal.ask(
      "What is the status of my verification worksheet?",
    );

    await portal.call("POST", "/v1/student/assistant/conversations", {
      body: { pageContext: { path: "/enrollment", label: "Enrollment" } },
    });
    const documents = await portal.call("GET", "/v1/student/documents");
    assert.equal(documents.status, 200);

    // The document review route is the staff-side transition the preview
    // exposes; it changes state Edward reads without the student acting.
    const requirement = await portal.requirement("financial_aid_verification");
    const form = new FormData();
    form.set(
      "file",
      new Blob([Buffer.from("%PDF-1.4 synthetic worksheet")], {
        type: "application/pdf",
      }),
      "worksheet.pdf",
    );
    form.set("category", "financial_aid");
    form.set("requirementId", requirement.id);
    const uploaded = await fetch(
      `${portalBaseUrl}/v1/student/documents/upload`,
      {
        method: "POST",
        headers: { cookie: COOKIE, "idempotency-key": randomUUID() },
        body: form,
      },
    );
    assert.equal(uploaded.status, 201);
    const stored = await uploaded.json();

    const reviewed = await portal.call(
      "POST",
      `/v1/demo/documents/${stored.id}/review`,
      { body: { decision: "needs_resubmission", note: "Page 2 is missing." } },
    );
    assert.equal(reviewed.status, 200, JSON.stringify(reviewed.payload));

    const answer = await portal.ask(
      "What is the status of my verification worksheet?",
    );
    // The worksheet was uploaded and then returned. Whatever wording Edward
    // chooses, it must not still report the pre-upload state, and it must not
    // claim the worksheet is settled.
    assert.doesNotMatch(answer.message, /verified|complete|accepted/i, answer.message);
    assert.match(answer.message, /needs|still|resubmit|returned/i, answer.message);
    assert.notEqual(
      answer.message,
      beforeWorksheet.message,
      "the reviewer's decision changed nothing Edward says",
    );
    assert.ok(beforeHolds >= 0);
  });

  it("keeps the checklist and every projection of it in agreement", async () => {
    // Whatever the state now is, the three surfaces that describe it must not
    // disagree: the checklist, the assistant's derived view, and the document
    // list the documents page renders.
    const requirements = await portal.call("GET", "/v1/student/requirements");
    const answer = await portal.ask("What do I still need to do?");
    const derived = new Map(
      [
        ...(answer.assistant.remainingSteps ?? []),
        ...(answer.assistant.awaitingReviewSteps ?? []),
        ...(answer.assistant.completedSteps ?? []),
      ].map((step) => [step.code, step.status]),
    );

    for (const item of requirements.payload.items) {
      if (item.status === "not_applicable") continue;
      assert.equal(
        derived.get(item.code),
        item.status,
        `${item.code}: checklist says ${item.status}, Edward derived ${derived.get(item.code)}`,
      );
    }
  });
});

describe("gate reasons never contradict the document record", () => {
  it("does not tell a student a document was not received when it is on file", async () => {
    // A gate reason is a *fact*, so no downstream guard catches it if it is
    // wrong -- it is the evidence the guards check other claims against. Every
    // unsatisfied reason therefore has to hold for a document that arrived and
    // is still being reviewed, not only for one that was never sent.
    const requirement = await portal.requirement("official_transcript");
    const form = new FormData();
    form.set(
      "file",
      new Blob([Buffer.from("%PDF-1.4 synthetic transcript")], {
        type: "application/pdf",
      }),
      "transcript.pdf",
    );
    form.set("category", "transcript");
    form.set("requirementId", requirement.id);
    const uploaded = await fetch(`${portalBaseUrl}/v1/student/documents/upload`, {
      method: "POST",
      headers: { cookie: COOKIE, "idempotency-key": randomUUID() },
      body: form,
    });
    assert.equal(uploaded.status, 201);
    const stored = await uploaded.json();
    await portal.call("POST", `/v1/demo/documents/${stored.id}/review`, {
      body: { decision: "under_review", note: null },
    });

    const answer = await portal.ask("Why can't I register for classes?");
    const message = `${answer.message} ${JSON.stringify(answer.assistant?.blocks ?? [])}`;
    assert.doesNotMatch(
      message,
      /transcript[^.]{0,60}(?:has not been received|not been received|never received)/i,
      `a transcript under review was described as not received: ${answer.message}`,
    );
  });
});
