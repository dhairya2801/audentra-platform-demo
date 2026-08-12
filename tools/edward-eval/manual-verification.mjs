#!/usr/bin/env node
/**
 * The five flows the brief asks to be checked by hand, run against the live
 * system with a real model.
 *
 * This is deliberately not part of the graded suite. It is a transcript a human
 * can read end to end: the same HTTP routes the browser calls, one running
 * server, the upload performed between two questions with no restart in
 * between. Every model call goes through the cost-tracked fetch.
 *
 *   node --import tsx tools/edward-eval/manual-verification.mjs
 */
import { randomUUID } from "node:crypto";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createDemoApi } from "../demo-api/src/http-api.js";
import { createSeedState } from "../demo-api/src/seed.js";
import { JsonStateStore } from "../demo-api/src/store.js";
import { PERSONAS } from "./src/personas.mjs";
import { SpendLedger, costTrackedFetch } from "./src/spend-ledger.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const NOW = "2026-08-05T12:00:00.000Z";
const COOKIE = "vv_demo_session=demo-session-v2";

const apiKey = process.env.OPENAI_API_KEY;
if (!apiKey) {
  console.error("OPENAI_API_KEY is required: this check is about the live system.");
  process.exit(1);
}

const ledger = new SpendLedger({
  file: join(repoRoot, "artifacts", "spend.json"),
  batch: "manual-verification",
});
const startingSpend = ledger.totalUsd;

const clock = () => new Date(NOW);
const directory = await mkdtemp(join(tmpdir(), "edward-manual-"));
const store = new JsonStateStore(join(directory, "state.json"), clock, undefined, () => {
  const state = createSeedState({ acceptedStudent: true });
  PERSONAS.aid_verification_outstanding.apply(state);
  return state;
});
await store.initialize();

const { server } = await createDemoApi({
  store,
  clock,
  logger: null,
  fetch: costTrackedFetch(ledger, {
    onRetry: ({ status, attempt, delayMs }) =>
      console.log(`  (rate limited ${status}; retry ${attempt} in ${delayMs}ms)`),
  }),
  storeOpenRouterResponses: false,
  openAiModel: "gpt-4o-mini",
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
  return { status: response.status, payload: text ? JSON.parse(text) : null };
};

const conversation = await request("POST", "/v1/student/assistant/conversations", {
  body: { pageContext: { path: "/documents", label: "Documents" } },
});

const transcript = [];
const ask = async (message) => {
  const result = await request("POST", "/v1/student/assistant/messages", {
    body: {
      conversationId: conversation.payload.id,
      clientMessageId: randomUUID(),
      message,
      inputMode: "text",
      pageContext: { path: "/documents", label: "Documents" },
    },
  });
  const record = {
    kind: "question",
    question: message,
    answer: result.payload?.message ?? "",
    requestType: result.payload?.studentAssistant?.requestType ?? null,
    executedTools:
      result.payload?.studentAssistant?.graphExecution?.executedTools ?? [],
    blocks: (result.payload?.blocks ?? []).map((block) => block.type),
    blockDetail: result.payload?.blocks ?? [],
  };
  transcript.push(record);
  console.log(`\n> ${message}`);
  console.log(`  [${record.requestType}] tools: ${record.executedTools.join(", ") || "none"}`);
  console.log(`  blocks: ${record.blocks.join(", ") || "none"}`);
  console.log(`\n${record.answer}\n`);
  for (const block of record.blockDetail) {
    if (block.type === "text") continue;
    console.log(`  --- ${block.type} ---`);
    console.log(
      block.fallbackText
        .split("\n")
        .map((line) => `  ${line}`)
        .join("\n"),
    );
  }
  return record;
};

const step = (title) => {
  console.log(`\n${"=".repeat(72)}\n${title}\n${"=".repeat(72)}`);
  transcript.push({ kind: "step", title });
};

// ---------------------------------------------------------------------------

step("1. Transcript, before and after a real upload, on one running server");

await ask("Have I uploaded my transcript?");

const requirements = await request("GET", "/v1/student/requirements");
const transcriptRequirement = requirements.payload.items.find(
  (item) => item.code === "official_transcript",
);
const form = new FormData();
form.set(
  "file",
  new Blob([Buffer.from("%PDF-1.4 synthetic official transcript")], {
    type: "application/pdf",
  }),
  "official-transcript.pdf",
);
form.set("category", "transcript");
form.set("requirementId", transcriptRequirement.id);
const upload = await request("POST", "/v1/student/documents/upload", {
  body: form,
  headers: { "idempotency-key": randomUUID() },
});
console.log(
  `\n  [uploaded through POST /v1/student/documents/upload -> ${upload.status}, document ${upload.payload.status}]`,
);
transcript.push({
  kind: "mutation",
  detail: `POST /v1/student/documents/upload -> ${upload.status}, status ${upload.payload.status}`,
});

await ask("What's the status of my transcript now?");

// The real extraction pipeline reads the synthetic fixture and reports that it
// is not a transcript, which is correct behaviour for that file but not the
// scenario the brief describes. So the reviewing office is then asked to put it
// under review, which is the state the reported bug was about.
const stored = await request("GET", "/v1/student/documents");
const transcriptDocument = stored.payload.items.find(
  (item) => item.category === "transcript",
);
const reviewed = await request(
  "POST",
  `/v1/demo/documents/${transcriptDocument.id}/review`,
  { body: { decision: "under_review", note: null } },
);
console.log(
  `\n  [reviewing office moved it to UNDER_REVIEW -> ${reviewed.status}]`,
);
transcript.push({
  kind: "mutation",
  detail: `POST /v1/demo/documents/:id/review {decision: under_review} -> ${reviewed.status}`,
});

await ask("Have I uploaded my transcript?");

step("2. Accepted financial aid");
await ask("What financial aid have I accepted?");

step("3. Outstanding financial-aid requirements");
await ask("What financial aid requirements am I still missing?");

step("4. A plain greeting");
await ask("Hi");

step("5. Documents still to submit, distinguishing missing from under review");
await ask("What documents do I still need to submit?");

// ---------------------------------------------------------------------------

const spent = ledger.totalUsd - startingSpend;
console.log(`\n${"=".repeat(72)}`);
console.log(
  `manual verification complete. spend $${spent.toFixed(4)} (cumulative $${ledger.totalUsd.toFixed(4)})`,
);

await writeFile(
  join(repoRoot, "artifacts", "manual-verification.json"),
  `${JSON.stringify({ generatedAt: NOW, spendUsd: Number(spent.toFixed(6)), transcript }, null, 2)}\n`,
);
console.log("written to artifacts/manual-verification.json");

await new Promise((resolve) => server.close(resolve));
