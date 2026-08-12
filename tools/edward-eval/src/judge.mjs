/**
 * Model judge for answer quality.
 *
 * Scores the eight response-quality criteria from the run brief, 0-2 each, via
 * structured output. The judge only ever sees the question, the answer, and the
 * evidence Edward actually retrieved — never the "right" answer — so it grades
 * whether the reply used what was available rather than matching a key.
 *
 * The judge is advisory. Adversarial, privacy, and contract behaviour is decided
 * by deterministic assertions in `assertions.mjs`.
 */

export const CRITERIA = Object.freeze([
  {
    key: "answers_question",
    text: "Directly answers the question that was asked, in its own terms.",
  },
  {
    key: "explains_reason",
    text: "Explains the relevant reason or state behind the answer, not just the bare status.",
  },
  {
    key: "separates_confirmed",
    text: "Every claim is supported by the retrieved evidence, and anything unverifiable is flagged rather than asserted. Score 2 when the whole reply is supported - hedging language is not required.",
  },
  {
    key: "names_blockers",
    text: "Names the blockers that matter for what was asked, specifically rather than by count. Score 2 when nothing relevant is blocking or the question does not concern a blockable action.",
  },
  {
    key: "next_action",
    text: "Tells the student a concrete next action they can take themselves. Score 2 when the question is purely informational and no action is called for.",
  },
  {
    key: "no_dump",
    text: "Avoids dumping raw records, status enums, long lists, or internal codes.",
  },
  {
    key: "natural_language",
    text: "Reads as natural, concise, human language rather than templated fragments.",
  },
  {
    key: "specific_not_generic",
    text: "Is specific to this student's evidence rather than generic support advice, given the evidence supplied.",
  },
]);

const JUDGE_SYSTEM_PROMPT = [
  "You grade a university assistant's reply to one student.",
  "",
  "You are given the student's question, the assistant's reply, and the evidence the assistant actually retrieved.",
  "Grade only what is in front of you. Do not invent a better answer and grade against it.",
  "",
  "Score each criterion 0, 1, or 2:",
  "  2 - fully met",
  "  1 - partly met",
  "  0 - not met",
  "",
  "Several criteria say when they do not apply. Honour that: a criterion that does not apply to this question scores 2, never 0. Do not penalise a correct answer to a simple factual question for omitting blockers, hedging, or an action the question did not call for.",
  "If the evidence contradicts the reply, score the affected criteria 0.",
  "Repeating the student's own claim as confirmed fact ('since you paid your deposit...') when no evidence confirms it is a 0 for separates_confirmed.",
  "Return only the JSON object.",
].join("\n");

function judgeResponseFormat() {
  const properties = Object.fromEntries(
    CRITERIA.map((criterion) => [
      criterion.key,
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
        required: [...CRITERIA.map((criterion) => criterion.key), "worst_problem"],
      },
    },
  };
}

/** Compact the retrieved evidence so the judge sees what Edward saw. */
function evidenceFor(response) {
  if (!response) return { receipts: [], state: {} };
  return {
    receipts: (response.contextReceipts ?? []).map((receipt) => ({
      source: receipt.source,
      status: receipt.status,
      records: receipt.recordCount,
    })),
    requestTypes: response.requestTypes ?? [],
    remainingSteps: (response.remainingSteps ?? []).map((step) => step.title),
    completedSteps: (response.completedSteps ?? []).map((step) => step.title),
    officialHolds: (response.officialHolds ?? []).length,
    deadlines: (response.deadlines ?? [])
      .slice(0, 10)
      .map((deadline) => `${deadline.title}: ${deadline.dueAt} (${deadline.urgency})`),
    missingDocuments: (response.missingDocuments ?? []).map((item) => item.title),
    unavailable: (response.unavailableData ?? []).map(
      (item) => `${item.source}:${item.reason}`,
    ),
  };
}

export async function judgeAnswer({ record, fetchImpl, apiKey, model = "gpt-4o-mini" }) {
  const body = {
    model,
    temperature: 0,
    max_tokens: 220,
    response_format: judgeResponseFormat(),
    messages: [
      { role: "system", content: JUDGE_SYSTEM_PROMPT },
      {
        role: "user",
        content: JSON.stringify({
          criteria: CRITERIA,
          question: record.question,
          conversationSoFar: record.history ?? [],
          assistantReply: record.answer,
          evidenceRetrieved: evidenceFor(record.response),
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
    CRITERIA.map((criterion) => [criterion.key, clamp(parsed[criterion.key])]),
  );
  return {
    scores,
    total: Object.values(scores).reduce((sum, value) => sum + value, 0),
    maximum: CRITERIA.length * 2,
    worstProblem: String(parsed.worst_problem ?? "").slice(0, 240),
  };
}

function clamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(2, Math.round(number)));
}
