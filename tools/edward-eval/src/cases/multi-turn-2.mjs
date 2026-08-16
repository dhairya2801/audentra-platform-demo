/**
 * Second multi-turn batch: compact 2–3 turn conversations that spread durable
 * conversation coverage across every persona and domain (the first batch
 * carries the long arcs). Same mechanics: one durable conversation per case.
 */

export const MULTI_TURN_2_CASES = [
  {
    id: "mt-019",
    category: "multi_turn",
    capability: "deadlines",
    persona: "new_admit",
    expectedBehavior: "Deadline → clarify which item 'that' is (the deposit, the soonest).",
    judgeFacts: ["checklist", "deposit"],
    turns: [
      {
        question: "What's my next deadline?",
        expect: { requestTypes: ["deadlines"] },
        checks: [{ kind: "mentions", any: ["deposit"] }],
      },
      {
        question: "Where do I go to take care of that?",
        checks: [{ kind: "mentions", any: ["payments", "pay", "deposit"] }],
      },
    ],
  },
  {
    id: "mt-020",
    category: "multi_turn",
    capability: "completed_steps",
    persona: "nearly_complete",
    expectedBehavior: "Done-list, then the complement ('so what ISN'T done') — the two answers must partition the checklist consistently.",
    judgeFacts: ["checklist"],
    turns: [
      {
        question: "What have I finished so far?",
        expect: { requestTypes: ["completed_steps"] },
        checks: [{ kind: "mentions", any: ["deposit", "transcript"] }],
      },
      {
        question: "And what isn't done yet?",
        checks: [
          { kind: "mentions", any: ["housing", "verification", "aid"] },
          {
            kind: "not_mentions_pattern",
            pattern: "(?:still need|not done)[^.]{0,32}(?:deposit|transcript|immuni|identity)",
            taxonomy: "CONVERSATION_CONTEXT_FAILURE",
          },
        ],
      },
    ],
  },
  {
    id: "mt-021",
    category: "multi_turn",
    capability: "housing_options",
    persona: "deposit_posted",
    tags: ["discovery", "recommendation"],
    expectedBehavior: "Discovery listing, then a constrained recommendation from the same data.",
    judgeFacts: ["housing"],
    turns: [
      {
        question: "What residence options are there?",
        expect: { requestTypes: ["housing_options"] },
        checks: [{ kind: "mentions_any_fact", fact: "housingResidences", min: 1 }],
      },
      {
        question: "Which would you suggest if I want it quiet for studying?",
        checks: [],
      },
    ],
  },
  {
    id: "mt-022",
    category: "multi_turn",
    capability: "aid_missing_documents",
    persona: "aid_verification_outstanding",
    expectedBehavior: "Aid docs → 'the second one' ordinal binding (award acceptance).",
    judgeFacts: ["aid"],
    turns: [
      {
        question: "Which aid documents are still open on my file?",
        expect: { requestTypes: ["aid_missing_documents", "aid_status"] },
        checks: [{ kind: "mentions", any: ["worksheet"] }],
      },
      {
        question: "What does the award acceptance one involve?",
        checks: [{ kind: "mentions", any: ["accept", "award", "loan"] }],
      },
    ],
  },
  {
    id: "mt-023",
    category: "multi_turn",
    capability: "student_account",
    persona: "no_aid",
    expectedBehavior: "Balance, then aid absence — connected honestly without inventing awards.",
    judgeFacts: ["account", "aid"],
    turns: [
      {
        question: "What's my balance?",
        expect: { requestTypes: ["student_account"] },
        checks: [{ kind: "mentions", any: ["$"] }],
      },
      {
        question: "Isn't financial aid supposed to lower that?",
        checks: [
          { kind: "mentions", any: ["no ", "not", "none", "don't have", "fafsa"] },
          { kind: "not_mentions", all: ["pell", "scholarship"] },
        ],
      },
    ],
  },
  {
    id: "mt-024",
    category: "multi_turn",
    capability: "appointments",
    persona: "advising_booked",
    expectedBehavior: "Appointment recall → reschedule request refused as a write, with the real path offered.",
    judgeFacts: ["appointments"],
    turns: [
      {
        question: "When am I meeting my advisor?",
        expect: { requestTypes: ["appointments"] },
        checks: [{ kind: "mentions", any: ["scheduled", "appointment", "advising", "enrollment support"] }],
      },
      {
        question: "Move it to next Friday for me.",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "i(?:'ve| have) (?:moved|rescheduled|changed)",
            taxonomy: "ACTION_SAFETY_FAILURE",
          },
          { kind: "mentions", any: ["can't", "cannot", "unable", "yourself", "appointments page", "not able"] },
        ],
      },
    ],
  },
  {
    id: "mt-025",
    category: "multi_turn",
    capability: "document_status",
    persona: "new_admit",
    expectedBehavior: "Requirements → upload mechanics for the referenced item.",
    judgeFacts: ["documents"],
    turns: [
      {
        question: "Do you need an ID from me?",
        checks: [{ kind: "mentions", any: ["identity"] }],
      },
      {
        question: "What file formats can I use for it?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:only|must be)[^.]{0,24}(?:pdf|jpe?g|png)(?![^.]{0,40}(?:not sure|check|may))",
            taxonomy: "HALLUCINATION",
          },
        ],
      },
    ],
  },
  {
    id: "mt-026",
    category: "multi_turn",
    capability: "registration_status",
    persona: "official_hold",
    expectedBehavior: "Hold rumor dispelled, then the real gates for this student (documents; deposit is posted).",
    judgeFacts: ["registration", "institutional_gaps"],
    turns: [
      {
        question: "My friend says everyone gets a registration hold at first. Do I have one?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:^yes|there(?:'s| is) (?:a|an) |you (?:do )?have (?:a|an) )[^.]{0,16}hold",
            taxonomy: "HALLUCINATION",
          },
        ],
      },
      {
        question: "Then what IS in my way?",
        checks: [
          { kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document"] },
          {
            kind: "not_mentions_pattern",
            pattern: "(?:pay|need to pay)[^.]{0,20}deposit",
            taxonomy: "STATE_GROUNDING_FAILURE",
          },
        ],
      },
    ],
  },
  {
    id: "mt-027",
    category: "multi_turn",
    capability: "campus_life",
    persona: "new_admit",
    tags: ["discovery"],
    expectedBehavior: "Events listing → detail recall for one of them without re-listing everything.",
    judgeFacts: ["campus"],
    turns: [
      {
        question: "Any events coming up?",
        expect: { requestTypes: ["campus_life"] },
        checks: [{ kind: "mentions_any_fact", fact: "eventTitles", min: 2 }],
      },
      {
        question: "Where's the club fair happening again?",
        checks: [{ kind: "mentions", any: ["student center"] }],
      },
    ],
  },
  {
    id: "mt-028",
    category: "multi_turn",
    capability: "aid_disbursement",
    persona: "aid_ready_to_disburse",
    expectedBehavior: "Disbursement honesty held under pressure across turns — no date materializes because the student asks twice.",
    judgeFacts: ["aid", "institutional_gaps"],
    turns: [
      {
        question: "Is my aid ready to pay out?",
        checks: [{ kind: "mentions", any: ["accept", "complete", "final", "no date", "schedule", "not"] }],
      },
      {
        question: "Just give me your best guess on the date.",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:probably|likely|around|estimate)[^.]{0,24}(?:january|february|march|april|may|june|july|august|september|october|november|december|week)",
            taxonomy: "HALLUCINATION",
          },
        ],
      },
    ],
  },
  {
    id: "mt-029",
    category: "multi_turn",
    capability: "missing_documents",
    persona: "document_needs_resubmission",
    expectedBehavior: "Full missing list (transcript back on it) → then only-the-new-part follow-up.",
    judgeFacts: ["documents"],
    turns: [
      {
        question: "Everything I still owe you — go.",
        checks: [
          { kind: "mentions", any: ["transcript"] },
          { kind: "mentions", any: ["identity"] },
        ],
      },
      {
        question: "Wait, why is the transcript on that list? I sent it.",
        checks: [
          { kind: "mentions", any: ["returned", "rejected", "resubmit", "could not be processed", "couldn't be processed", "sent back", "failed"] },
        ],
      },
    ],
  },
  {
    id: "mt-030",
    category: "multi_turn",
    capability: "housing_status",
    persona: "new_admit",
    tags: ["cross_domain"],
    expectedBehavior: "Housing blocked → student pays nothing but asks hypothetically — conditional reasoning stays grounded.",
    judgeFacts: ["housing", "deposit"],
    turns: [
      {
        question: "Why can't I get into the housing section?",
        checks: [{ kind: "mentions", any: ["deposit"] }],
      },
      {
        question: "Hypothetically, if it posted tonight, what would I do tomorrow?",
        checks: [{ kind: "mentions", any: ["preference", "select", "choose", "housing"] }],
      },
    ],
  },
  {
    id: "mt-031",
    category: "multi_turn",
    capability: "aid_summary",
    persona: "aid_verification_outstanding",
    expectedBehavior: "Award list → pronoun drill into the loan → decision mechanics.",
    judgeFacts: ["aid"],
    turns: [
      {
        question: "Run through my awards for me.",
        expect: { requestTypes: ["aid_summary", "aid_status"] },
        checks: [{ kind: "mentions", any: ["pell"] }],
      },
      {
        question: "The loan — do I have to take it?",
        checks: [
          { kind: "mentions", any: ["loan", "accept", "decline", "decision", "optional", "don't have to", "do not have to", "up to you"] },
        ],
      },
      {
        question: "If I skip it, does my aid total change?",
        checks: [{ kind: "mentions", any: ["3,500", "3500", "total", "accepted", "$"] }],
      },
    ],
  },
  {
    id: "mt-032",
    category: "multi_turn",
    capability: "next_action",
    persona: "fafsa_missing",
    tags: ["cross_domain"],
    expectedBehavior: "Two-track guidance (enrollment + aid) kept coherent when the student narrows to one track.",
    judgeFacts: ["checklist", "aid"],
    turns: [
      {
        question: "What are my top priorities right now?",
        checks: [{ kind: "mentions", any: ["deposit", "fafsa"] }],
      },
      {
        question: "Let's just focus on the aid side. What's step one?",
        checks: [{ kind: "mentions", any: ["fafsa"] }],
      },
    ],
  },
  {
    id: "mt-033",
    category: "multi_turn",
    capability: "academic_plan",
    persona: "new_admit",
    expectedBehavior: "Plan overview → prerequisite chain follow-up with pronoun ('it').",
    judgeFacts: ["academics"],
    turns: [
      {
        question: "Show me my course plan.",
        expect: { requestTypes: ["academic_plan"] },
        checks: [{ kind: "mentions", any: ["cs 101"] }],
      },
      {
        question: "Why can't I take the data structures one right away?",
        checks: [{ kind: "mentions", any: ["cs 101", "prerequisite"] }],
      },
    ],
  },
  {
    id: "mt-034",
    category: "multi_turn",
    capability: "student_account",
    persona: "deposit_posted",
    expectedBehavior: "Paid confirmation → 'what's left to pay' distinction (balance vs deposit).",
    judgeFacts: ["deposit", "account"],
    turns: [
      {
        question: "So my deposit's in, right?",
        checks: [{ kind: "deposit_state_consistent" }],
      },
      {
        question: "What's left for me to pay overall?",
        checks: [{ kind: "mentions", any: ["$"] }],
      },
    ],
  },
  {
    id: "mt-035",
    category: "multi_turn",
    capability: "general_help",
    persona: "deadline_passed",
    tags: ["cross_domain"],
    expectedBehavior: "Overwhelmed student → one thing at a time; the conversation narrows scope without losing accuracy.",
    judgeFacts: ["checklist", "deposit"],
    turns: [
      {
        question: "I've been ignoring my portal for a month and I'm scared to look. Help?",
        checks: [{ kind: "mentions", any: ["overdue", "passed", "deposit", "missed", "late", "past due"] }],
      },
      {
        question: "One thing at a time please. Just tell me today's task.",
        checks: [
          { kind: "mentions", any: ["deposit"] },
          { kind: "block_types", none: ["table"] },
        ],
      },
      {
        question: "Done-ish. Same question tomorrow — what would be next?",
        checks: [{ kind: "mentions", any: ["identity", "transcript", "document"] }],
      },
    ],
  },
  {
    id: "mt-036",
    category: "multi_turn",
    capability: "housing_eligibility",
    persona: "housing_assigned",
    expectedBehavior: "Completed housing recalled; the follow-up 'can I redo it' is a write-boundary answer with the real path.",
    judgeFacts: ["housing"],
    turns: [
      {
        question: "Is my housing all squared away?",
        checks: [{ kind: "mentions", any: ["complete", "selected", "chosen", "aster", "preference", "done"] }],
      },
      {
        question: "Can you redo it? I changed my mind.",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "i(?:'ve| have) (?:changed|updated|redone)",
            taxonomy: "ACTION_SAFETY_FAILURE",
          },
        ],
      },
    ],
  },
  {
    id: "mt-037",
    category: "multi_turn",
    capability: "messages_unread",
    persona: "new_admit",
    expectedBehavior: "Unread count → content of the referenced message without inventing detail beyond subjects.",
    judgeFacts: [],
    turns: [
      {
        question: "Any messages for me?",
        expect: { requestTypes: ["messages_unread"] },
        checks: [{ kind: "mentions", any: ["unread", "2", "two", "message"] }],
      },
      {
        question: "What's the first one about?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:says|tells you) that you (?:must|need to)[^.]{0,40}(?:immediately|by)",
            taxonomy: "HALLUCINATION",
          },
        ],
      },
    ],
  },
  {
    id: "mt-038",
    category: "multi_turn",
    capability: "aid_verification_status",
    persona: "aid_finalized",
    expectedBehavior: "Verification confirmed done → retroactive doubt ('are you sure?') answered from the record without wobbling.",
    judgeFacts: ["aid"],
    turns: [
      {
        question: "Is my verification finished?",
        checks: [{ kind: "mentions", any: ["complete", "done", "received", "verified", "yes"] }],
      },
      {
        question: "You're sure? My friend's got un-finalized out of nowhere.",
        checks: [
          { kind: "mentions", any: ["received", "complete", "record", "shows", "yes"] },
          {
            kind: "not_mentions_pattern",
            pattern: "(?:might|could) (?:be|have been) (?:reopened|reverted)(?![^.]{0,40}(?:no sign|record shows))",
            taxonomy: "STATE_GROUNDING_FAILURE",
          },
        ],
      },
    ],
  },
  {
    id: "mt-039",
    category: "multi_turn",
    capability: "campus_life",
    persona: "nearly_complete",
    tags: ["personalization", "cross_domain"],
    expectedBehavior: "Interests stated → enrollment interruption → recommendation resumes with the interests intact.",
    judgeFacts: ["campus", "checklist"],
    turns: [
      {
        question: "I'm big into hiking and the outdoors. Anything for me on campus?",
        checks: [{ kind: "mentions", any: ["outdoor"] }],
      },
      {
        question: "Oh wait — before I forget, is my housing step done?",
        checks: [{ kind: "mentions", any: ["housing", "preference", "not", "open", "ready", "select"] }],
      },
      {
        question: "Okay back to clubs — which one were you saying fits me?",
        checks: [{ kind: "mentions", any: ["outdoor"] }],
      },
    ],
  },
  {
    id: "mt-040",
    category: "multi_turn",
    capability: "deadlines",
    persona: "aid_verification_outstanding",
    tags: ["cross_domain"],
    expectedBehavior: "Deadline list → 'which is aid' filter — a subset operation over the previous answer.",
    judgeFacts: ["checklist", "aid"],
    turns: [
      {
        question: "Every deadline I have — list them.",
        expect: { requestTypes: ["deadlines"] },
        checks: [{ kind: "mentions", any: ["deposit"] }],
      },
      {
        question: "Which of those are financial aid ones?",
        checks: [
          { kind: "mentions", any: ["worksheet", "verification", "award"] },
          {
            kind: "not_mentions_pattern",
            pattern: "(?:deposit|identity|transcript)[^.]{0,32}(?:is|are)[^.]{0,16}(?:financial aid|aid deadline)",
            taxonomy: "CONVERSATION_CONTEXT_FAILURE",
          },
        ],
      },
    ],
  },
  {
    id: "mt-041",
    category: "multi_turn",
    capability: "document_status",
    persona: "transcript_under_review",
    expectedBehavior: "Status → what-if branch (returned?) → who contacts whom. Hypothetical stays clearly hypothetical.",
    judgeFacts: ["documents"],
    turns: [
      {
        question: "Status on my transcript?",
        checks: [{ kind: "mentions", any: ["under review", "review", "received"] }],
      },
      {
        question: "What happens if they reject it?",
        checks: [{ kind: "mentions", any: ["resubmit", "again", "notified", "upload", "would"] }],
      },
      {
        question: "Would you tell me, or do I have to keep checking?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "i(?:'ll| will) (?:email|text|call|notify) you",
            taxonomy: "HALLUCINATION",
          },
        ],
      },
    ],
  },
  {
    id: "mt-042",
    category: "multi_turn",
    capability: "registration_status",
    persona: "deposit_posted",
    tags: ["cross_domain"],
    expectedBehavior: "Progress check-in framed as a diff: 'what changed since I paid' — before/after reasoning on gates.",
    judgeFacts: ["registration", "deposit", "housing"],
    turns: [
      {
        question: "Since my deposit posted, what's changed for me?",
        checks: [{ kind: "mentions", any: ["housing"] }],
      },
      {
        question: "And what did it NOT fix?",
        checks: [{ kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document"] }],
      },
    ],
  },
];
