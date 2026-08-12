/**
 * Drives evaluation questions through the real preview stack in-process: the
 * same HTTP routes, graph, tools, and gateway the running app uses. Every model
 * call goes through the cost-tracked fetch, so a run cannot outspend the ledger.
 */
import { randomUUID } from "node:crypto";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createDemoApi } from "../../demo-api/src/http-api.js";
import { createSeedState } from "../../demo-api/src/seed.js";
import { JsonStateStore } from "../../demo-api/src/store.js";
import { PERSONAS } from "./personas.mjs";
import { costTrackedFetch } from "./spend-ledger.mjs";

/** Pinned so deadline urgency and calendar windows are reproducible. */
export const EVAL_NOW = "2026-08-05T12:00:00.000Z";

export async function startEdward({ persona, ledger, onCall }) {
  const definition = PERSONAS[persona];
  if (!definition) throw new Error(`Unknown persona: ${persona}`);
  const clock = () => new Date(EVAL_NOW);
  const directory = await mkdtemp(join(tmpdir(), `edward-eval-${persona}-`));
  const store = new JsonStateStore(
    join(directory, "state.json"),
    clock,
    undefined,
    () => {
      const state = createSeedState({ acceptedStudent: true });
      definition.apply(state);
      return state;
    },
  );
  await store.initialize();

  const { server } = await createDemoApi({
    store,
    clock,
    logger: process.env.EVAL_DEBUG ? console : null,
    fetch: costTrackedFetch(ledger, { onCall }),
    storeOpenRouterResponses: false,
    openAiModel: "gpt-4o-mini",
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}`;

  const post = async (path, body) => {
    const response = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers: {
        cookie: "vv_demo_session=demo-session-v2",
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
    });
    return { status: response.status, payload: await response.json() };
  };

  const conversation = await post("/v1/student/assistant/conversations", {
    pageContext: { path: "/enrollment", label: "Enrollment" },
  });

  return {
    persona,
    async ask(message, history = []) {
      const result = await post("/v1/student/assistant/messages", {
        conversationId: conversation.payload.id,
        clientMessageId: randomUUID(),
        message,
        inputMode: "text",
        pageContext: { path: "/enrollment", label: "Enrollment" },
        ...(history.length > 0 ? { history } : {}),
      });
      return result;
    },
    async close() {
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

/**
 * Run a set of cases, grouped by persona so each seeded state is booted once.
 * Returns raw transcripts; scoring happens separately so a run can be re-judged
 * without paying to re-ask.
 */
export async function runCases({ cases, ledger, onProgress }) {
  const byPersona = new Map();
  for (const item of cases) {
    const list = byPersona.get(item.persona) ?? [];
    list.push(item);
    byPersona.set(item.persona, list);
  }

  const transcripts = [];
  for (const [persona, personaCases] of byPersona) {
    const edward = await startEdward({ persona, ledger });
    try {
      for (const item of personaCases) {
        let record;
        try {
          const result = await edward.ask(item.question, item.history);
          const assistant =
            result.payload?.studentAssistant ?? result.payload ?? null;
          record = {
            ...item,
            httpStatus: result.status,
            answer: assistant?.message ?? "",
            response: assistant,
            error: null,
          };
        } catch (error) {
          record = {
            ...item,
            httpStatus: 0,
            answer: "",
            response: null,
            error: String(error?.message ?? error),
          };
        }
        transcripts.push(record);
        onProgress?.(record, transcripts.length, cases.length);
      }
    } finally {
      await edward.close();
    }
  }
  return transcripts;
}
