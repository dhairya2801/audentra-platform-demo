/**
 * Cost control for the Edward evaluation harness.
 *
 * Every model call made by the harness routes through `costTrackedFetch`, which
 * refuses to issue a request once the ledger reaches its ceiling and records the
 * actual usage reported by the provider afterwards. Spend is persisted after each
 * call so a killed process never loses its accounting.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

import { MODEL_PRICING, worstCasePricing } from "./pricing.mjs";

/** USD per token, from the eval pricing configuration. Only priced models may be called. */
export { MODEL_PRICING };

/**
 * The tracked ceiling is the operational abort: a batch stops here. The absolute
 * ceiling is the backstop that must never be crossed. The gap between them is
 * deliberately larger than any single gpt-4o-mini call this harness can make, so
 * a call authorised just under the tracked ceiling still cannot reach the
 * absolute one.
 *
 * Raised from 0.85/1.00 when the suite grew from 115 to ~400 cases: a full
 * judged run costs roughly $0.25, and the ledger is cumulative across runs.
 */
export const TRACKED_CEILING_USD = 4;
export const ABSOLUTE_CEILING_USD = 4.5;

export class SpendCeilingExceededError extends Error {
  constructor(spentUsd, ceilingUsd, kind = "tracked") {
    super(
      `Edward eval ${kind} spend ceiling reached: $${spentUsd.toFixed(4)} of $${ceilingUsd.toFixed(2)}. Refusing further model calls.`,
    );
    this.name = "SpendCeilingExceededError";
    this.spentUsd = spentUsd;
    this.ceilingUsd = ceilingUsd;
    this.kind = kind;
  }
}

export class UnpricedModelError extends Error {
  constructor(model) {
    super(
      `Refusing to call unpriced model "${model}". Allowed: ${Object.keys(MODEL_PRICING).join(", ")}.`,
    );
    this.name = "UnpricedModelError";
    this.model = model;
  }
}

const emptyLedger = () => ({
  version: 1,
  totalUsd: 0,
  totalCalls: 0,
  promptTokens: 0,
  completionTokens: 0,
  batches: {},
});

export class SpendLedger {
  /**
   * @param {object} options
   * @param {string} options.file       where cumulative spend is persisted
   * @param {number} [options.ceilingUsd] tracked abort threshold
   * @param {number} [options.absoluteCeilingUsd] never-exceed backstop
   * @param {number} [options.maxCalls]   belt-and-braces call cap
   */
  constructor(options) {
    this.file = options.file;
    this.ceilingUsd = options.ceilingUsd ?? TRACKED_CEILING_USD;
    this.absoluteCeilingUsd =
      options.absoluteCeilingUsd ?? ABSOLUTE_CEILING_USD;
    this.maxCalls = options.maxCalls ?? 20_000;
    this.batch = options.batch ?? "unlabelled";
    this.state = this.#load();
  }

  #load() {
    try {
      const parsed = JSON.parse(readFileSync(this.file, "utf8"));
      if (!parsed || typeof parsed.totalUsd !== "number") return emptyLedger();
      return { ...emptyLedger(), ...parsed };
    } catch {
      return emptyLedger();
    }
  }

  #persist() {
    mkdirSync(dirname(this.file), { recursive: true });
    writeFileSync(this.file, `${JSON.stringify(this.state, null, 2)}\n`);
  }

  get totalUsd() {
    return this.state.totalUsd;
  }

  get totalCalls() {
    return this.state.totalCalls;
  }

  remainingUsd() {
    return Math.max(0, this.ceilingUsd - this.state.totalUsd);
  }

  batchSpend(batch = this.batch) {
    return this.state.batches[batch]?.usd ?? 0;
  }

  /** Throws if another call must not be issued. Call this *before* spending. */
  assertMaySpend() {
    if (this.state.totalUsd >= this.absoluteCeilingUsd) {
      throw new SpendCeilingExceededError(
        this.state.totalUsd,
        this.absoluteCeilingUsd,
        "absolute",
      );
    }
    if (this.state.totalUsd >= this.ceilingUsd) {
      throw new SpendCeilingExceededError(this.state.totalUsd, this.ceilingUsd);
    }
    if (this.state.totalCalls >= this.maxCalls) {
      throw new SpendCeilingExceededError(this.state.totalUsd, this.ceilingUsd);
    }
  }

  /**
   * Record provider-reported usage. Returns the cost of this call in USD.
   * Unknown models are charged at the highest known rate rather than skipped, so
   * a misconfiguration can never make spend look free.
   */
  record({ model, promptTokens, completionTokens, batch = this.batch }) {
    const pricing = MODEL_PRICING[model] ?? worstCasePricing();
    const usd =
      (promptTokens ?? 0) * pricing.input +
      (completionTokens ?? 0) * pricing.output;
    this.state.totalUsd += usd;
    this.state.totalCalls += 1;
    this.state.promptTokens += promptTokens ?? 0;
    this.state.completionTokens += completionTokens ?? 0;
    const bucket = this.state.batches[batch] ?? {
      usd: 0,
      calls: 0,
      promptTokens: 0,
      completionTokens: 0,
    };
    bucket.usd += usd;
    bucket.calls += 1;
    bucket.promptTokens += promptTokens ?? 0;
    bucket.completionTokens += completionTokens ?? 0;
    this.state.batches[batch] = bucket;
    this.#persist();
    return usd;
  }

  summary() {
    return {
      totalUsd: Number(this.state.totalUsd.toFixed(6)),
      totalCalls: this.state.totalCalls,
      remainingUsd: Number(this.remainingUsd().toFixed(6)),
      ceilingUsd: this.ceilingUsd,
      promptTokens: this.state.promptTokens,
      completionTokens: this.state.completionTokens,
      batches: Object.fromEntries(
        Object.entries(this.state.batches).map(([name, bucket]) => [
          name,
          { ...bucket, usd: Number(bucket.usd.toFixed(6)) },
        ]),
      ),
    };
  }
}

/** Statuses worth another attempt: rate limiting and transient server faults. */
export const RETRYABLE_STATUSES = Object.freeze([408, 409, 429, 500, 502, 503, 504]);

export const DEFAULT_RETRY = Object.freeze({
  maxAttempts: 5,
  baseDelayMs: 500,
  maxDelayMs: 30_000,
});

/**
 * Exponential backoff with full jitter, capped, and never longer than the
 * provider's own Retry-After hint asks for.
 */
export function retryDelayMs(attempt, retryAfterSeconds, policy = DEFAULT_RETRY, random = Math.random) {
  if (Number.isFinite(retryAfterSeconds) && retryAfterSeconds >= 0) {
    return Math.min(policy.maxDelayMs, Math.round(retryAfterSeconds * 1000));
  }
  const ceiling = Math.min(policy.maxDelayMs, policy.baseDelayMs * 2 ** attempt);
  return Math.round(random() * ceiling);
}

function readRetryAfterSeconds(response) {
  const header = response?.headers?.get?.("retry-after");
  if (!header) return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds)) return seconds;
  const at = Date.parse(header);
  return Number.isFinite(at) ? Math.max(0, (at - Date.now()) / 1000) : null;
}

/**
 * Bound how many async operations run at once. The runner asks questions
 * sequentially by design — one student, one conversation — but judging is
 * independent per answer, and an unbounded fan-out is exactly what earns a 429.
 */
export function createConcurrencyLimiter(limit) {
  const bound = Math.max(1, Math.floor(limit));
  let active = 0;
  const waiting = [];
  const next = () => {
    if (active >= bound || waiting.length === 0) return;
    active += 1;
    const { task, resolve, reject } = waiting.shift();
    Promise.resolve()
      .then(task)
      .then(resolve, reject)
      .finally(() => {
        active -= 1;
        next();
      });
  };
  return function run(task) {
    return new Promise((resolve, reject) => {
      waiting.push({ task, resolve, reject });
      next();
    });
  };
}

/**
 * Wrap `fetch` so every provider chat completion is pre-authorised against the
 * ledger and post-recorded from the provider's own usage numbers.
 *
 * Rate limits and transient 5xx are retried with exponential backoff up to a
 * cap; each attempt is re-authorised against the ledger, so a batch that runs
 * out of budget mid-retry stops rather than spinning. A request that exhausts
 * its attempts returns the provider's last response instead of being dropped,
 * so the caller records a failure rather than silently losing the question.
 *
 * @param {SpendLedger} ledger
 * @param {object} [options]
 * @param {typeof fetch} [options.fetch]
 * @param {(event: object) => void} [options.onCall]
 * @param {(event: object) => void} [options.onRetry]
 * @param {typeof DEFAULT_RETRY} [options.retry]
 * @param {(ms: number) => Promise<void>} [options.sleep]
 */
export function costTrackedFetch(ledger, options = {}) {
  const underlying = options.fetch ?? globalThis.fetch;
  const policy = { ...DEFAULT_RETRY, ...(options.retry ?? {}) };
  const sleep =
    options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));

  return async function trackedFetch(url, init) {
    const target = String(url);
    if (!isChatCompletion(target)) return underlying(url, init);

    const requestedModel = readRequestedModel(init);
    if (requestedModel && !(requestedModel in MODEL_PRICING)) {
      throw new UnpricedModelError(requestedModel);
    }

    for (let attempt = 0; ; attempt += 1) {
      // Re-checked every attempt: a retry is a fresh chance to spend.
      ledger.assertMaySpend();

      const response = await underlying(url, init);
      const text = await response.text();
      let payload = null;
      try {
        payload = JSON.parse(text);
      } catch {
        /* non-JSON error bodies still count as an attempt, but cost nothing */
      }
      if (payload?.usage) {
        const usd = ledger.record({
          model: payload.model ?? requestedModel ?? "gpt-4o-mini",
          promptTokens: payload.usage.prompt_tokens ?? 0,
          completionTokens: payload.usage.completion_tokens ?? 0,
        });
        options.onCall?.({
          model: payload.model,
          usd,
          promptTokens: payload.usage.prompt_tokens ?? 0,
          completionTokens: payload.usage.completion_tokens ?? 0,
          totalUsd: ledger.totalUsd,
        });
      }

      const replay = () =>
        new Response(text, {
          status: response.status,
          statusText: response.statusText,
          headers: response.headers,
        });

      if (
        !RETRYABLE_STATUSES.includes(response.status) ||
        attempt >= policy.maxAttempts - 1
      ) {
        return replay();
      }
      const delayMs = retryDelayMs(
        attempt,
        readRetryAfterSeconds(response),
        policy,
      );
      options.onRetry?.({
        status: response.status,
        attempt: attempt + 1,
        maxAttempts: policy.maxAttempts,
        delayMs,
      });
      await sleep(delayMs);
    }
  };
}

function isChatCompletion(url) {
  return url.includes("/chat/completions") || url.includes("/v1/responses");
}

function readRequestedModel(init) {
  if (!init?.body || typeof init.body !== "string") return null;
  try {
    const parsed = JSON.parse(init.body);
    return typeof parsed.model === "string" ? parsed.model : null;
  } catch {
    return null;
  }
}
