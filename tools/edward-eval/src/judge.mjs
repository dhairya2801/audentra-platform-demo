/**
 * Model judge for answer quality — v2.
 *
 * The judge receives a *typed evidence contract* and nothing else: the
 * question, the conversation so far, the case's expected behavior, canonical
 * ground-truth lines derived from the persona fixture, the executed tool
 * reads with their (bounded) results, and the final answer. No raw traces,
 * no chain-of-thought, no unrelated state.
 *
 * Dimensions are graded separately, 0–2 each, and only the dimensions that
 * apply to a case are requested — continuity is meaningless on a first turn,
 * personalization on a factual lookup. The judge is advisory: adversarial,
 * privacy, and contract behaviour is decided deterministically in
 * assertions.mjs, and tool selection in tool-grading.mjs.
 */

export const JUDGE_DIMENSIONS = Object.freeze([
  {
    key: "factual_correctness",
    text: "Every factual claim about this student's state matches the ground truth. Contradicting the ground truth scores 0; a materially misleading emphasis scores 1.",
  },
  {
    key: "evidence_grounding",
    text: "Claims are supported by the executed tool reads / ground truth. Repeating the student's own unverified claim as confirmed fact scores 0. Where data does not exist, saying so honestly IS grounded behaviour and scores 2.",
  },
  {
    key: "completeness",
    text: "Answers every part of what was asked (both halves of a two-part question, every relevant blocker). Omitting a part the student asked about scores 0-1. Extra unasked material does NOT raise this score.",
  },
  {
    key: "reasoning",
    text: "The explanation connects cause and effect correctly for this student (why blocked, what depends on what). Asserting a causal link the ground truth contradicts scores 0. Score 2 if no reasoning was called for and none was fabricated.",
  },
  {
    key: "response_mode",
    text: "The answer's mode matches the intent: a listing question gets a listing, a recommendation question gets a recommendation with rationale, a comparison compares, a status question reports status. Correct facts in the wrong mode (e.g. unsolicited recommendations for a 'what exists?' question) score 0-1.",
  },
  {
    key: "helpfulness",
    text: "A student could act on this reply without asking again. Concise and sufficient scores 2; verbose padding, repetition, or burying the answer lowers this score — length is a cost, never a merit.",
  },
  {
    key: "next_step",
    text: "The concrete next action (if one is called for) is correct and actionable for this student's actual state. Score 2 when the question is purely informational and no action was invented.",
  },
  {
    key: "continuity",
    text: "The reply correctly resolves what this turn refers to (pronouns, 'that', topic switches, corrections) from the conversation, without restating everything or losing established context.",
  },
  {
    key: "personalization",
    text: "The reply uses what the student said about themselves (interests, constraints, preferences) where that was the point of the question — generic advice that ignores the stated interests scores 0-1.",
  },
  {
    key: "hallucination_free",
    text: "Nothing is invented: no dates, amounts, offices, policies, statuses, or records beyond the ground truth and tool results. Any invented specific scores 0.",
  },
]);

const ALWAYS = [
  "factual_correctness",
  "evidence_grounding",
  "completeness",
  "reasoning",
  "response_mode",
  "helpfulness",
  "next_step",
  "hallucination_free",
];

/** Which dimensions apply to one turn of one case. */
export function applicableDimensions(caseMeta, turnIndex, conversation) {
  const keys = [...ALWAYS];
  if ((conversation?.length ?? 0) > 0 || turnIndex > 0) keys.push("continuity");
  if (caseMeta.tags?.includes("personalization")) keys.push("personalization");
  return JUDGE_DIMENSIONS.filter((dimension) => keys.includes(dimension.key));
}

const JUDGE_SYSTEM_PROMPT = [
  "You grade one reply from a university enrollment assistant to one student.",
  "",
  "You receive a typed evidence bundle: the question, the conversation so far, the expected behaviour for this case, canonical ground truth about this student, the tool reads the assistant executed (with results), and the final answer.",
  "The ground truth is authoritative. If the answer contradicts it, the affected dimensions score 0 even if the answer sounds plausible.",
  "",
  "Score each requested dimension 0 (not met), 1 (partly met), or 2 (fully met), independently.",
  "",
  "HONEST GAPS ARE CORRECT ANSWERS. The ground truth often states that the platform has NO record or source for something (term calendars, policies, room assignments, disbursement dates, SLAs). When the question asks about exactly such a thing, an answer that plainly says the information is not available/published and routes the student to the right office is the RIGHT answer: score factual_correctness, evidence_grounding, completeness, and response_mode 2 for it. Never score it down for failing to produce a fact that does not exist. This does NOT excuse an answer that dodges a question the ground truth CAN answer.",
  "",
  "SCRUTINIZE CAUSATION AND SEQUENCING. Check every 'because X', 'once X, you can Y', 'X is required before Y', and 'X blocks Y' against the gates and dependencies the ground truth actually states. A dependency the ground truth does not state is invented: score reasoning 0 and hallucination_free at most 1, even when every individual fact is right.",
  "",
  "RESPONSE MODE IS STRICT. If the student's message is not a question (a greeting, thanks, a closing, a bare remark), the right mode is a brief social reply — an unrequested status report or next-step lecture scores response_mode 0. A 'where/how do I…' question answered without the where/how fails the mode. Correct facts in the wrong mode still fail this dimension.",
  "",
  "Do NOT reward: length, headings or formatting, restating the question, hedging boilerplate, or recommendations nobody asked for. A short correct answer outscores a long correct answer with padding.",
  "Do NOT penalize: brevity, absence of caveats when everything stated is supported, or refusing to state facts that do not exist in the ground truth (that is correct behaviour).",
  "A dimension that clearly does not apply to this question scores 2, never 0.",
  "",
  "Return only the JSON object.",
].join("\n");

function judgeResponseFormat(dimensions) {
  const properties = Object.fromEntries(
    dimensions.map((dimension) => [
      dimension.key,
      { type: "integer", minimum: 0, maximum: 2 },
    ]),
  );
  return {
    type: "json_schema",
    json_schema: {
      name: "edward_answer_score",
      strict: true,
      schema: {
        type: "object",
        additionalProperties: false,
        properties: {
          ...properties,
          worst_problem: { type: "string", maxLength: 240 },
        },
        required: [...dimensions.map((dimension) => dimension.key), "worst_problem"],
      },
    },
  };
}

/**
 * The typed judge-evidence contract. Everything the judge needs, nothing else.
 */
export function buildJudgeEvidence({
  question,
  conversation,
  expectedBehavior,
  groundTruth,
  response,
}) {
  const trace = response?.trace ?? null;
  return {
    question,
    conversationSoFar: (conversation ?? []).map((entry) => ({
      role: entry.role,
      content: String(entry.content ?? "").slice(0, 400),
    })),
    expectedBehavior: expectedBehavior ?? null,
    groundTruth: groundTruth ?? [],
    executedTools: (trace?.toolCalls ?? []).map((call) => ({
      tool: call.tool,
      round: call.round,
      status: call.status,
    })),
    toolResults: (trace?.toolCalls ?? []).slice(0, 12).map((call) => ({
      tool: call.tool,
      status: call.status,
      result:
        call.result === undefined || call.result === null
          ? null
          : JSON.stringify(call.result).slice(0, 1_200),
    })),
    unavailableReads: (trace?.toolCalls ?? [])
      .filter((call) => call.status !== "available")
      .map((call) => `${call.tool}: ${call.reason ?? call.status}`),
    finalAnswer: response?.message ?? "",
    answerBlockTypes: (response?.blocks ?? []).map((block) => block.type),
  };
}

export async function judgeTurn({
  evidence,
  dimensions,
  fetchImpl,
  apiKey,
  model = "gpt-4o-mini",
}) {
  const body = {
    model,
    temperature: 0,
    max_tokens: 260,
    response_format: judgeResponseFormat(dimensions),
    messages: [
      { role: "system", content: JUDGE_SYSTEM_PROMPT },
      {
        role: "user",
        content: JSON.stringify({
          dimensionsToScore: dimensions.map((dimension) => ({
            key: dimension.key,
            rubric: dimension.text,
          })),
          evidence,
        }),
      },
    ],
  };
  const response = await fetchImpl("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  const content = payload?.choices?.[0]?.message?.content;
  if (!content) throw new Error(`Judge returned no content (HTTP ${response.status})`);
  const parsed = JSON.parse(content);
  const scores = Object.fromEntries(
    dimensions.map((dimension) => [dimension.key, clamp(parsed[dimension.key])]),
  );
  return {
    scores,
    total: Object.values(scores).reduce((sum, value) => sum + value, 0),
    maximum: dimensions.length * 2,
    worstProblem: String(parsed.worst_problem ?? "").slice(0, 240),
  };
}

function clamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(2, Math.round(number)));
}

/* --------------------------------------------------------------------- *
 * Legacy surface kept for calibrate.mjs against pre-v2 batches.          *
 * --------------------------------------------------------------------- */
export const CRITERIA = JUDGE_DIMENSIONS;
