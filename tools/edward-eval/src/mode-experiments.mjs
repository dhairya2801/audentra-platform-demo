/**
 * The normal-vs-deterministic experiment set.
 *
 * Each entry names the persona whose canonical state makes the question have a
 * definite answer, so a difference between the two modes is a difference in
 * *execution*, never in the record. Categories mirror the study: direct reads
 * first, then the cases where a planner or a rewrite might plausibly earn its
 * cost — cross-domain causation, aggregation, ambiguity, multiple intents,
 * follow-ups, and questions canonical state simply cannot answer.
 *
 * Student turns only. The staff assistant shares the same execution-mode
 * mechanism, but this in-memory host serves a single-persona cohort, so a
 * staff comparison here would say more about the fixture than about Edward.
 */

export const EXPERIMENTS = Object.freeze([
  {
    id: "md-direct-simple",
    category: "Direct simple",
    persona: "transcript_under_review",
    question: "What is my transcript status?",
  },
  {
    id: "md-direct-financial",
    category: "Direct financial",
    persona: "new_admit",
    question: "How much is my deposit?",
  },
  {
    id: "md-cross-domain",
    category: "Cross-domain",
    persona: "payment_pending",
    question: "I paid my deposit. Why can't I apply for housing?",
    note: "The deposit payment is pending, not posted — the honest answer has to explain a cause the student did not state.",
  },
  {
    id: "md-cross-domain-blocked",
    category: "Cross-domain",
    persona: "new_admit",
    question: "Why is my housing application blocked?",
  },
  {
    id: "md-aggregation",
    category: "Aggregation",
    persona: "deadline_passed",
    question: "What do I still have to do?",
  },
  {
    id: "md-ambiguous",
    category: "Ambiguous",
    persona: "nearly_complete",
    question: "Am I good to go?",
  },
  {
    id: "md-ambiguous-vague",
    category: "Ambiguous",
    persona: "new_admit",
    question: "Is everything okay with my stuff?",
  },
  {
    id: "md-multi-intent",
    category: "Multi-intent",
    persona: "new_admit",
    question: "What's my transcript status and what do I still owe?",
  },
  {
    id: "md-multi-intent-aid",
    category: "Multi-intent",
    persona: "aid_verification_outstanding",
    question: "Where is my financial aid, and can I register for classes yet?",
  },
  {
    id: "md-follow-up",
    category: "Follow-up",
    persona: "deadline_passed",
    question: "Which of those do I need to do first?",
    history: [
      { role: "user", content: "What do I still have to do?" },
      {
        role: "assistant",
        content:
          "You still have open enrollment steps, including your final transcript and your enrollment deposit.",
      },
    ],
  },
  {
    id: "md-follow-up-why",
    category: "Follow-up",
    persona: "new_admit",
    question: "Why?",
    history: [
      { role: "user", content: "Can I apply for housing?" },
      {
        role: "assistant",
        content: "Your housing application is not open yet.",
      },
    ],
    note: "A bare follow-up: the deterministic classifier and a model planner disagree most often here.",
  },
  {
    id: "md-unsupported-record",
    category: "Unsupported",
    persona: "new_admit",
    question: "What was my roommate's high school GPA?",
    note: "No canonical record can answer this; an honest refusal is the correct result for both modes.",
  },
  {
    id: "md-unsupported-future",
    category: "Unsupported",
    persona: "new_admit",
    question: "Will I get more scholarship money next year?",
  },
]);

export function experimentsFor({ personas = [], categories = [], ids = [] } = {}) {
  return EXPERIMENTS.filter((experiment) => {
    if (ids.length > 0 && !ids.includes(experiment.id)) return false;
    if (personas.length > 0 && !personas.includes(experiment.persona)) return false;
    if (
      categories.length > 0 &&
      !categories.some(
        (category) => category.toLowerCase() === experiment.category.toLowerCase(),
      )
    ) {
      return false;
    }
    return true;
  });
}
