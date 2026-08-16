/**
 * Drives evaluation cases through the canonical Python Edward: the same
 * FastAPI routes, demo-auth binding, AssistantPipeline, tools, guards, and
 * per-turn tracing that production uses. One `audentra-eval-api` process is
 * booted per (persona, faults) group — the in-memory platform composition,
 * no Postgres needed.
 *
 * What each graded turn carries:
 *  - the wire answer and blocks,
 *  - the turn's full `AssistantTurnTrace` plus a compatibility view
 *    (`requestType`, `graphExecution.executedTools`, per-tool receipts),
 *  - client-observed latency alongside the trace's server-side duration.
 *
 * Ground truth comes from the same host: a per-persona snapshot of the
 * student REST endpoints, fetched from a fault-free boot, is attached to the
 * run so fact-derived checks and judge ground truth can never drift from the
 * fixture. Model spend inside the Python process (planner AND composer) is
 * metered from the trace's per-operation usage.
 *
 * Multi-turn cases run through one durable conversation (server history),
 * exactly like production; single-turn cases with seeded `history` use the
 * client-history compatibility path.
 */
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import net from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fetchSnapshot } from "./snapshot.mjs";

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const WORKER_TOKEN = "local-development-document-worker-token";
const READINESS_TIMEOUT_MS = 120_000;
const DEFAULT_GROUP_CONCURRENCY = 3;

/** Personas served by `audentra-eval-api --persona`; must match eval_personas.py. */
export const PERSONA_NAMES = [
  "new_admit",
  "deposit_posted",
  "payment_pending",
  "nearly_complete",
  "official_hold",
  "deadline_passed",
  "advising_booked",
  "housing_assigned",
  "fafsa_missing",
  "aid_verification_outstanding",
  "aid_finalized",
  "aid_ready_to_disburse",
  "aid_refund_due",
  "no_aid",
  "transcript_under_review",
  "document_needs_resubmission",
];

async function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function waitForReady(baseUrl, child) {
  const deadline = Date.now() + READINESS_TIMEOUT_MS;
  let lastError = null;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`edward-eval-api exited early with code ${child.exitCode}`);
    }
    try {
      const response = await fetch(`${baseUrl}/health`);
      if (response.ok) return;
      lastError = new Error(`health returned ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`edward-eval-api never became ready: ${lastError}`);
}

export function faultArgs(faults) {
  return Object.entries(faults ?? {}).flatMap(([primitive, mode]) => [
    "--fault",
    `${primitive}=${mode}`,
  ]);
}

export async function startEdward({ persona, faults = null, ledger }) {
  if (!PERSONA_NAMES.includes(persona)) {
    throw new Error(`Unknown persona: ${persona}`);
  }
  const port = await freePort();
  const child = spawn(
    "uv",
    [
      "run",
      "--directory",
      "apps/api",
      "--locked",
      "audentra-eval-api",
      "--port",
      String(port),
      "--persona",
      persona,
      ...faultArgs(faults),
    ],
    {
      cwd: REPO_ROOT,
      env: process.env,
      stdio: process.env.EVAL_DEBUG ? "inherit" : "ignore",
    },
  );
  const baseUrl = `http://127.0.0.1:${port}`;
  await waitForReady(baseUrl, child);

  const post = async (path, body) => {
    const response = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return { status: response.status, payload: await response.json() };
  };

  const fetchTrace = async (requestId) => {
    if (!requestId) return null;
    try {
      const response = await fetch(
        `${baseUrl}/internal/assistant/traces/${requestId}`,
        { headers: { "x-vv-worker-token": WORKER_TOKEN } },
      );
      if (!response.ok) return null;
      return await response.json();
    } catch {
      return null;
    }
  };

  const meterFromTrace = (trace, payload) => {
    if (!ledger) return;
    const calls = trace?.modelCalls ?? [];
    if (calls.length > 0) {
      for (const call of calls) {
        const usage = call.usage;
        if (usage && typeof usage.totalTokens === "number") {
          ledger.record({
            model: call.model ?? payload?.model ?? "gpt-4o-mini",
            promptTokens: usage.promptTokens ?? 0,
            completionTokens: usage.completionTokens ?? 0,
          });
        }
      }
      return;
    }
    // No trace (endpoint disabled?): fall back to the response's composer usage.
    const usage = payload?.usage;
    if (usage && typeof usage.totalTokens === "number") {
      ledger.record({
        model: payload?.model ?? "gpt-4o-mini",
        promptTokens: usage.promptTokens ?? 0,
        completionTokens: usage.completionTokens ?? 0,
      });
    }
  };

  return {
    persona,
    faults: faults ?? null,
    baseUrl,
    async snapshot() {
      return fetchSnapshot(baseUrl);
    },
    async createConversation() {
      const conversation = await post("/v1/student/assistant/conversations", {
        pageContext: { path: "/enrollment", label: "Enrollment" },
      });
      return conversation.payload?.id ?? null;
    },
    /**
     * One turn. With `conversationId`, durable server history carries the
     * conversation; with `history`, the client-fallback path carries it;
     * with neither, a fresh conversation isolates the turn.
     */
    async ask(message, { history = [], conversationId = null } = {}) {
      ledger?.assertMaySpend();
      const stateless = history.length > 0;
      let conversation = conversationId;
      if (!stateless && !conversation) {
        conversation = await this.createConversation();
      }
      const started = performance.now();
      const result = await post("/v1/student/assistant/messages", {
        ...(conversation
          ? { conversationId: conversation, clientMessageId: randomUUID() }
          : {}),
        message,
        inputMode: "text",
        pageContext: { path: "/enrollment", label: "Enrollment" },
        ...(stateless ? { history } : {}),
      });
      const latencyMs = Math.round(performance.now() - started);
      const trace = await fetchTrace(result.payload?.requestId);
      meterFromTrace(trace, result.payload);
      return { ...result, trace, latencyMs, conversationId: conversation };
    },
    async close() {
      child.kill("SIGTERM");
      await new Promise((resolve) => {
        if (child.exitCode !== null) return resolve();
        child.once("exit", resolve);
        setTimeout(() => {
          child.kill("SIGKILL");
          resolve();
        }, 5_000).unref?.();
      });
    },
  };
}

/**
 * The response object checks and judge grade: the wire answer plus the
 * trace-derived execution view.
 */
export function gradedResponse(payload, trace) {
  const toolCalls = trace?.toolCalls ?? [];
  const requestType = trace?.classification?.requestType ?? null;
  const additional = trace?.classification?.additionalRequestTypes ?? [];
  return {
    message: payload?.message ?? "",
    blocks: payload?.blocks ?? [],
    provider: payload?.provider ?? null,
    model: payload?.model ?? null,
    usage: payload?.usage ?? null,
    suggestedActions: payload?.suggestedActions ?? [],
    requestType,
    requestTypes: requestType ? [requestType, ...additional] : [],
    contextReceipts: toolCalls.map((call) => ({
      source: call.tool,
      status: call.status,
      recordCount: call.recordCount ?? null,
    })),
    graphExecution: {
      executedTools: toolCalls.map((call) => call.tool),
      toolSelectionSource: trace?.toolSelectionSource ?? null,
      secondRead: trace?.secondRead ?? null,
      historySource: trace?.historySource ?? null,
      responseSource: trace?.responseSource ?? null,
      failureCodes: trace?.failureCodes ?? [],
    },
    trace,
  };
}

const groupKey = (item) =>
  `${item.persona}::${JSON.stringify(item.faults ?? {})}`;

async function runOneCase(edward, item) {
  const turns = [];
  let conversationId = null;
  const multiTurn = item.turns.length > 1;
  if (multiTurn) {
    conversationId = await edward.createConversation();
  }
  for (const turn of item.turns) {
    try {
      const result = await edward.ask(turn.question, {
        history: multiTurn ? [] : item.history,
        conversationId: multiTurn ? conversationId : null,
      });
      turns.push({
        question: turn.question,
        httpStatus: result.status,
        requestId: result.payload?.requestId ?? null,
        answer: result.payload?.message ?? "",
        response: gradedResponse(result.payload, result.trace),
        latencyMs: result.latencyMs,
        error: null,
      });
    } catch (error) {
      turns.push({
        question: turn.question,
        httpStatus: 0,
        requestId: null,
        answer: "",
        response: null,
        latencyMs: null,
        error: String(error?.message ?? error),
      });
      break; // a broken conversation is not worth continuing
    }
  }
  return {
    ...item,
    conversationId,
    turns: item.turns.map((turn, index) => ({
      ...turn,
      ...(turns[index] ?? {
        httpStatus: 0,
        requestId: null,
        answer: "",
        response: null,
        latencyMs: null,
        error: "turn never ran (conversation aborted earlier)",
      }),
    })),
    error: turns.find((turn) => turn.error)?.error ?? null,
  };
}

/**
 * Run a set of normalized cases grouped by (persona, faults); each group
 * boots one Python host. Groups run with bounded concurrency — separate
 * processes, separate students, no shared state. Returns
 * `{ transcripts, snapshots }` where snapshots is canonical per-persona
 * ground truth captured from fault-free hosts.
 */
export async function runCases({
  cases,
  ledger,
  onProgress,
  concurrency = DEFAULT_GROUP_CONCURRENCY,
}) {
  const groups = new Map();
  for (const item of cases) {
    const key = groupKey(item);
    const list = groups.get(key) ?? [];
    list.push(item);
    groups.set(key, list);
  }

  // Fault-free groups first so persona snapshots are captured from clean
  // hosts; faulted groups reuse them.
  const ordered = [...groups.entries()].sort(
    ([, a], [, b]) =>
      (a[0].faults ? 1 : 0) - (b[0].faults ? 1 : 0),
  );

  const snapshots = new Map();
  const transcripts = [];
  let done = 0;
  const total = cases.reduce((sum, item) => sum + item.turns.length, 0);

  const ensureSnapshot = async (persona) => {
    if (snapshots.has(persona)) return;
    // Boot a throwaway clean host purely to capture canonical state.
    const probe = await startEdward({ persona, ledger: null });
    try {
      snapshots.set(persona, await probe.snapshot());
    } finally {
      await probe.close();
    }
  };

  const runGroup = async ([, groupCases]) => {
    const { persona, faults } = groupCases[0];
    const edward = await startEdward({ persona, faults, ledger });
    try {
      if (!faults && !snapshots.has(persona)) {
        snapshots.set(persona, await edward.snapshot());
      } else if (faults) {
        await ensureSnapshot(persona);
      }
      for (const item of groupCases) {
        const record = await runOneCase(edward, item);
        transcripts.push(record);
        done += record.turns.length;
        onProgress?.(record, done, total);
      }
    } finally {
      await edward.close();
    }
  };

  // Bounded group concurrency.
  const queue = [...ordered];
  const workers = Array.from(
    { length: Math.max(1, Math.min(concurrency, queue.length)) },
    async () => {
      while (queue.length > 0) {
        const group = queue.shift();
        if (!group) return;
        await runGroup(group);
      }
    },
  );
  await Promise.all(workers);

  // Stable output order regardless of group scheduling.
  const orderIndex = new Map(cases.map((item, index) => [item.id, index]));
  transcripts.sort((a, b) => orderIndex.get(a.id) - orderIndex.get(b.id));
  return { transcripts, snapshots: Object.fromEntries(snapshots) };
}
