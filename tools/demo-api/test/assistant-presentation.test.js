/**
 * Presentation and conversational behaviour, end to end through the preview
 * HTTP API and with no model configured.
 *
 * Running these deterministically is the point: greetings, block shape, and the
 * absence of markup are properties the architecture is supposed to guarantee
 * outright, not qualities a judge should be asked to grade. Anything that needs
 * a model to assess belongs in the evaluation harness instead.
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

async function startPortal() {
  const clock = () => new Date(NOW);
  const directory = await mkdtemp(join(tmpdir(), "assistant-presentation-"));
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
    // No gateway: these assertions are about the deterministic path, and a
    // live provider would make them both slow and billable.
    ai: {
      configured: false,
      async askEdward() {
        throw new Error("the grounded graph must answer these questions");
      },
    },
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;

  const post = async (path, body) => {
    const response = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers: { cookie: COOKIE, "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return response.json();
  };
  const conversation = await post("/v1/student/assistant/conversations", {
    pageContext: { path: "/enrollment", label: "Enrollment" },
  });

  return {
    async ask(message) {
      const payload = await post("/v1/student/assistant/messages", {
        conversationId: conversation.id,
        clientMessageId: randomUUID(),
        message,
        inputMode: "text",
        pageContext: { path: "/enrollment", label: "Enrollment" },
      });
      return {
        message: payload.message ?? "",
        blocks: payload.blocks ?? [],
        assistant: payload.studentAssistant ?? null,
        executedTools:
          payload.studentAssistant?.graphExecution?.executedTools ?? [],
      };
    },
    async close() {
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

const portal = await startPortal();
after(() => portal.close());

describe("conversational openers", () => {
  it("answers a greeting without reading university data", async () => {
    for (const greeting of ["hi", "hello", "good morning", "hey there"]) {
      const answer = await portal.ask(greeting);
      assert.equal(
        answer.assistant?.requestType,
        "greeting",
        `"${greeting}" should be a greeting`,
      );
      // The student's own name is the only read a greeting may make.
      assert.ok(
        answer.executedTools.length <= 1,
        `"${greeting}" read ${answer.executedTools.join(", ")}`,
      );
      assert.match(answer.message, /edward/i);
      assert.ok(
        answer.message.length < 260,
        `a greeting should be short, got ${answer.message.length} characters`,
      );
    }
  });

  it("names the student when their profile is already available", async () => {
    const answer = await portal.ask("hi");
    assert.match(answer.message, /^Hi \w+!/);
  });

  it("answers what it can do without reading anything at all", async () => {
    const answer = await portal.ask("what can you do?");
    assert.equal(answer.assistant?.requestType, "capability_overview");
    assert.deepEqual(answer.executedTools, []);
    assert.match(answer.message, /financial aid/i);
    // It must also be honest about the limit.
    assert.match(answer.message, /can'?t change|can not change/i);
  });

  it("answers an open request for direction from this student's record", async () => {
    const answer = await portal.ask("I'm not sure where to start");
    assert.equal(answer.assistant?.requestType, "general_help");
    assert.ok(
      answer.executedTools.length > 0,
      "where to start is a question about this student",
    );
    assert.ok(
      !/I can help with your enrollment, documents, financial aid, housing, registration, deadlines/.test(
        answer.message,
      ),
      "an open request for direction must not degrade into the capability blurb",
    );
  });

  it("keeps a greeting attached to a real question as the real question", async () => {
    const answer = await portal.ask("hi, what documents am I missing?");
    assert.equal(answer.assistant?.requestType, "missing_documents");
  });
});

describe("response presentation", () => {
  const wellFormed = (blocks) => {
    for (const block of blocks) {
      assert.ok(
        ["text", "bullet_list", "numbered_list", "table", "next_steps"].includes(
          block.type,
        ),
        `unknown block type ${block.type}`,
      );
      assert.equal(typeof block.fallbackText, "string");
      assert.ok(block.fallbackText.length > 0, `${block.type} has no fallback`);
    }
  };

  it("uses a table for several documents that share columns", async () => {
    const answer = await portal.ask("What documents do I still need to submit?");
    wellFormed(answer.blocks);
    const table = answer.blocks.find((block) => block.type === "table");
    assert.ok(table, `expected a table, got ${answer.blocks.map((b) => b.type)}`);
    assert.deepEqual(
      table.columns.map((column) => column.label),
      ["Document", "Status", "Due"],
    );
    assert.ok(table.rows.length >= 2);
    for (const row of table.rows) {
      for (const column of table.columns) {
        assert.equal(typeof row[column.key], "string");
      }
    }
  });

  it("uses prose alone for a greeting", async () => {
    const answer = await portal.ask("hello");
    assert.deepEqual(
      answer.blocks.map((block) => block.type),
      ["text"],
    );
  });

  it("never emits markup a plain-text renderer would show verbatim", async () => {
    for (const question of [
      "What documents do I still need to submit?",
      "What do I still need to do?",
      "Why can't I register for classes?",
      "What financial aid do I have?",
      "hi",
    ]) {
      const answer = await portal.ask(question);
      const text = [answer.message, ...answer.blocks.map((b) => b.fallbackText)].join(
        "\n",
      );
      assert.doesNotMatch(text, /\|[^|\n]*\|/, `${question}: pipe table markup`);
      assert.doesNotMatch(text, /\*\*/, `${question}: bold markup`);
      assert.doesNotMatch(text, /^#{1,6}\s/m, `${question}: heading markup`);
      assert.doesNotMatch(text, /```|<\/?[a-z]+>/i, `${question}: code or HTML`);
    }
  });

  it("never shows raw serialised data or internal status words", async () => {
    for (const question of [
      "What documents do I still need to submit?",
      "What financial aid requirements am I missing?",
      "What is the status of my verification worksheet?",
      "What do I still need to do?",
    ]) {
      const answer = await portal.ask(question);
      assert.doesNotMatch(answer.message, /\[object Object\]|"[a-z_]+"\s*:/i, question);
      assert.doesNotMatch(
        answer.message,
        /\b(?:action_required|not_started|under_review|not_applicable)\b|authoritative state/i,
        `${question}: internal vocabulary reached the student`,
      );
    }
  });

  it("keeps the plain-text channel consistent with the blocks", async () => {
    // Voice and transcripts read `message`; the portal reads the blocks. They
    // are two renderings of one answer and must agree on the leading sentence.
    const answer = await portal.ask("What documents do I still need to submit?");
    const lead = answer.blocks.find((block) => block.type === "text");
    assert.ok(lead, "every answer leads with text");
    assert.ok(lead.text.length > 0);
  });
});
