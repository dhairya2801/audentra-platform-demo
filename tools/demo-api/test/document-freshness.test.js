/**
 * Regression cover for the reported staleness bug: a student uploads a
 * transcript through the UI and Edward still says it has not been uploaded.
 *
 * These tests deliberately refuse every shortcut that would make the bug
 * invisible. Every mutation goes through the same HTTP route the browser calls;
 * the assistant is then asked on the *same running server*, with no restart, no
 * cache clearing, no re-seeding, and no second student. A test that wrote to the
 * store directly, or booted a fresh process between the write and the question,
 * would pass while the product stayed broken.
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

/**
 * Boots the preview API exactly once and hands back a client. The returned
 * object is the only way the tests touch state: there is no store handle, so a
 * later edit cannot quietly reintroduce a direct database write.
 */
async function startPortal() {
  const clock = () => new Date(NOW);
  const directory = await mkdtemp(join(tmpdir(), "document-freshness-"));
  const store = new JsonStateStore(
    join(directory, "state.json"),
    clock,
    undefined,
    () => createSeedState({ acceptedStudent: true }),
  );
  await store.initialize();
  // An explicit gateway keeps this suite hermetic and free: without one, the
  // preview builds a real provider client from the environment, and these
  // tests would make live model calls outside the spend ledger. The extractor
  // returns a well-formed transcript so the lifecycle under test is the
  // document's, not the parser's.
  const { server } = await createDemoApi({
    store,
    clock,
    logger: null,
    ai: {
      configured: false,
      async extractStudentDocument({ expectedDocumentType }) {
        return {
          status: "completed",
          documentType: expectedDocumentType ?? "other",
          summary: "Synthetic fixture document.",
          studentName: null,
          institutionName: null,
          issueDate: null,
          academicTerm: null,
          fields: [],
          courses: [],
          warnings: [],
          model: "test/document-parser",
          provider: "local",
          processedAt: NOW,
          verifiedAt: null,
        };
      },
      async askEdward() {
        throw new Error("the grounded graph must answer these questions");
      },
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;

  const request = async (method, path, { body, headers } = {}) => {
    const response = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        cookie: COOKIE,
        ...(body instanceof FormData ? {} : { "content-type": "application/json" }),
        ...headers,
      },
      ...(body === undefined
        ? {}
        : { body: body instanceof FormData ? body : JSON.stringify(body) }),
    });
    const text = await response.text();
    return {
      status: response.status,
      payload: text ? JSON.parse(text) : null,
    };
  };

  const conversation = await request("POST", "/v1/student/assistant/conversations", {
    body: { pageContext: { path: "/documents", label: "Documents" } },
  });

  return {
    request,
    async requirement(code) {
      const result = await request("GET", "/v1/student/requirements");
      return result.payload.items.find((item) => item.code === code) ?? null;
    },
    async documents() {
      const result = await request("GET", "/v1/student/documents");
      return result.payload.items;
    },
    /** Record a reviewer decision, as the admissions team would. */
    async review(documentId, decision, note = null) {
      return request("POST", `/v1/demo/documents/${documentId}/review`, {
        body: { decision, note },
      });
    },
    /** Upload through the multipart endpoint the browser posts to. */
    async upload({ category, requirementId, fileName = "upload.pdf" }) {
      const form = new FormData();
      form.set(
        "file",
        new Blob([Buffer.from(`%PDF-1.4 synthetic ${category}`)], {
          type: "application/pdf",
        }),
        fileName,
      );
      form.set("category", category);
      if (requirementId) form.set("requirementId", requirementId);
      return request("POST", "/v1/student/documents/upload", {
        body: form,
        headers: { "idempotency-key": randomUUID() },
      });
    },
    async ask(message) {
      const result = await request("POST", "/v1/student/assistant/messages", {
        body: {
          conversationId: conversation.payload.id,
          clientMessageId: randomUUID(),
          message,
          inputMode: "text",
          pageContext: { path: "/documents", label: "Documents" },
        },
      });
      return {
        status: result.status,
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

describe("document state freshness through the real API", () => {
  it("advances the checklist requirement the moment the upload is accepted", async () => {
    const before = await portal.requirement("official_transcript");
    assert.equal(
      before.status,
      "ready",
      "precondition: the transcript has not been submitted yet",
    );

    const upload = await portal.upload({
      category: "transcript",
      requirementId: before.id,
    });
    assert.equal(upload.status, 201);

    const documents = await portal.documents();
    const stored = documents.find((item) => item.category === "transcript");
    assert.ok(stored, "the document record exists after upload");

    // The document record is fresh. The authoritative checklist requirement the
    // assistant reads from must be fresh too: these are two views of one fact,
    // and letting them disagree is the entire bug.
    const after = await portal.requirement("official_transcript");
    assert.notEqual(
      after.status,
      "ready",
      "the requirement must not still read as awaiting submission once a document is stored",
    );
    assert.ok(
      ["submitted", "under_review", "completed"].includes(after.status),
      `expected a submitted-or-later status, got "${after.status}"`,
    );
  });

  it("does not tell the student to submit a transcript they have already submitted", async () => {
    // Runs against the same server and the same student as the upload above.
    const answer = await portal.ask("What do I still need to do?");
    assert.equal(answer.status, 200);
    assert.ok(answer.assistant, "the grounded graph must answer this question");

    const outstanding = (answer.assistant.remainingSteps ?? []).map(
      (step) => step.code,
    );
    assert.ok(
      !outstanding.includes("official_transcript"),
      `Edward still lists the uploaded transcript as outstanding: ${JSON.stringify(outstanding)}`,
    );
    // The requirement's title is literally "Submit your official transcript",
    // so its presence proves nothing either way. What must not appear is a
    // claim that it is still outstanding.
    assert.doesNotMatch(
      answer.message,
      /official transcript[^.]{0,40}(?:is still (?:ready|blocked|in progress)|not (?:been )?(?:submitted|uploaded|received))/i,
      "the answer must not describe an uploaded transcript as outstanding",
    );
  });

  it("reports the uploaded transcript as received rather than staying silent about it", async () => {
    const answer = await portal.ask("What is the status of my transcript?");
    assert.equal(answer.status, 200);
    assert.ok(answer.assistant, "the grounded graph must answer this question");
    assert.doesNotMatch(
      answer.message,
      /\b(not (been )?(uploaded|submitted|received)|haven'?t (uploaded|submitted)|missing)\b/i,
      "the answer must not describe a stored document as missing",
    );
    assert.match(
      answer.message,
      /\b(uploaded|received|submitted|under review|reviewing)\b/i,
      "the answer must say the document was received",
    );
  });
});

/**
 * The lifecycle the brief specifies, driven end to end on one running server.
 * Each step mutates through the API and immediately asks Edward, so a stale
 * read anywhere in the chain fails here rather than in production.
 */
describe("transcript lifecycle, live, without restarting Edward", () => {
  const statusOf = async (code) => {
    const answer = await portal.ask("What is the status of my transcript?");
    const state = (answer.assistant?.documentStates ?? []).find(
      (item) => item.requirementCode === code,
    );
    return { answer, state };
  };

  it("walks NOT_SUBMITTED -> UNDER_REVIEW -> ACCEPTED -> NEEDS_RESUBMISSION", async () => {
    // The transcript was uploaded by the suite above; take it to review first.
    const documents = await portal.documents();
    const transcript = documents.find((item) => item.category === "transcript");
    assert.ok(transcript, "the uploaded transcript is present");

    await portal.review(transcript.id, "under_review");
    let seen = await statusOf("official_transcript");
    assert.equal(seen.state?.submissionState, "UNDER_REVIEW");
    assert.equal(seen.state?.owner, "university");
    assert.match(seen.answer.message, /under review/i);
    assert.doesNotMatch(seen.answer.message, /\bmissing\b|not submitted/i);

    await portal.review(transcript.id, "accepted");
    seen = await statusOf("official_transcript");
    assert.equal(seen.state?.submissionState, "ACCEPTED");
    assert.equal(seen.state?.owner, "nobody");
    assert.equal(
      (await portal.requirement("official_transcript")).status,
      "completed",
      "an accepted document completes the requirement it satisfies",
    );
    assert.match(seen.answer.message, /accepted/i);

    await portal.review(
      transcript.id,
      "needs_resubmission",
      "The copy provided is unofficial.",
    );
    seen = await statusOf("official_transcript");
    assert.equal(seen.state?.submissionState, "NEEDS_RESUBMISSION");
    assert.equal(seen.state?.owner, "student");
    assert.match(seen.answer.message, /resubmit/i);

    // And it is an action again, not a completed step.
    const next = await portal.ask("What do I still need to do?");
    assert.ok(
      (next.assistant?.remainingSteps ?? []).some(
        (step) => step.code === "official_transcript",
      ),
      "a document needing resubmission is the student's move again",
    );
  });

  it("applies the same freshness to the immunisation record", async () => {
    const requirement = await portal.requirement("immunization_record");
    const before = await portal.ask("What documents do I still need to submit?");
    assert.ok(
      before.message.toLowerCase().includes("immunization") ||
        before.message.toLowerCase().includes("immunisation"),
      "precondition: the immunisation record is outstanding",
    );

    const upload = await portal.upload({
      category: "health",
      requirementId: requirement.id,
      fileName: "immunisation.pdf",
    });
    assert.equal(upload.status, 201);

    const after = await portal.ask("What documents do I still need to submit?");
    const stillMissing = (after.assistant?.documentStates ?? []).filter(
      (item) => item.submissionState === "NOT_SUBMITTED",
    );
    assert.ok(
      !stillMissing.some((item) => item.requirementCode === "immunization_record"),
      "the uploaded immunisation record is no longer missing",
    );
  });

  it("applies the same freshness to the financial-aid verification worksheet", async () => {
    const requirement = await portal.requirement("financial_aid_verification");
    const upload = await portal.upload({
      category: "financial_aid",
      requirementId: requirement.id,
      fileName: "verification-worksheet.pdf",
    });
    assert.equal(upload.status, 201);

    const after = await portal.requirement("financial_aid_verification");
    assert.ok(
      ["submitted", "under_review", "completed"].includes(after.status),
      `the aid requirement must reflect the upload, got "${after.status}"`,
    );

    // The financials page reads a separate document list. It is a projection
    // of the same upload and must not disagree with the checklist.
    const financials = await portal.request("GET", "/v1/student/financials");
    const worksheet = financials.payload.requiredDocuments.find(
      (item) => item.code === "verification_worksheet",
    );
    assert.ok(
      ["submitted", "under_review", "verified"].includes(worksheet.status),
      `the aid document list must reflect the upload, got "${worksheet.status}"`,
    );
  });
});
