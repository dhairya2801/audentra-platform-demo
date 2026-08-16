/**
 * Multi-turn conversations, run through one durable server-side conversation
 * per case (exactly the production path). Every turn is graded; later turns
 * carry the continuity dimension. Coverage: pronouns, implicit references,
 * topic switching, returning to earlier topics, corrections, contradictions,
 * preference accumulation, and multi-domain arcs.
 */

export const MULTI_TURN_CASES = [
  {
    id: "mt-001",
    category: "multi_turn",
    capability: "document_status",
    persona: "transcript_under_review",
    tags: ["cross_domain"],
    expectedBehavior:
      "The brief's canonical arc: documents → specific document → its timing → its effect on housing. Each turn builds on the last without restating everything.",
    judgeFacts: ["documents", "housing", "deposit", "institutional_gaps"],
    turns: [
      {
        question: "What documents am I missing?",
        expect: { requestTypes: ["missing_documents"] },
        checks: [
          { kind: "mentions", any: ["identity"] },
          { kind: "mentions", any: ["immuniz", "immunis"] },
          {
            kind: "not_mentions_pattern",
            pattern: "transcript[^.]{0,40}(?:missing|not (?:been )?(?:submitted|uploaded|received))",
            taxonomy: "STATE_GROUNDING_FAILURE",
          },
        ],
      },
      {
        question: "What about my transcript?",
        expect: { requestTypes: ["document_status", "missing_documents"] },
        checks: [{ kind: "mentions", any: ["under review", "review", "received"] }],
      },
      {
        question: "How long will that take?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:within|takes?|about|typically|usually)\\s+\\d+\\s*(?:hour|day|week|business)",
            taxonomy: "HALLUCINATION",
          },
        ],
      },
      {
        question: "Will that block housing?",
        checks: [
          { kind: "mentions", any: ["deposit"] },
          {
            kind: "not_mentions_pattern",
            pattern: "(?:yes|transcript)[^.]{0,40}(?:blocks?|holds? up)[^.]{0,16}housing",
            taxonomy: "CROSS_DOMAIN_REASONING_FAILURE",
          },
        ],
      },
    ],
  },
  {
    id: "mt-002",
    category: "multi_turn",
    capability: "registration_status",
    persona: "new_admit",
    tags: ["cross_domain"],
    expectedBehavior:
      "Blockers → fix path → partial-payoff reasoning: paying the deposit clears one gate and opens housing but documents still gate registration.",
    judgeFacts: ["registration", "deposit", "housing"],
    turns: [
      {
        question: "Why can't I register for classes yet?",
        expect: { requestTypes: ["registration_status", "holds_and_blockers"] },
        checks: [
          { kind: "mentions", any: ["deposit"] },
          { kind: "mentions", any: ["transcript", "identity", "immuniz", "immunis"] },
        ],
      },
      {
        question: "How do I fix the first one?",
        checks: [{ kind: "mentions", any: ["deposit", "payments", "pay"] }],
      },
      {
        question: "If I do that today, can I register tomorrow?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "yes[^.]{0,40}(?:register|all set)(?![^.]{0,60}(?:still|remaining|other|document))",
            taxonomy: "CROSS_DOMAIN_REASONING_FAILURE",
          },
        ],
      },
      {
        question: "Okay — and does paying it help with anything else?",
        checks: [{ kind: "mentions", any: ["housing"] }],
      },
    ],
  },
  {
    id: "mt-003",
    category: "multi_turn",
    capability: "campus_life",
    persona: "new_admit",
    tags: ["personalization"],
    expectedBehavior:
      "Preference accumulation: interests stated in turns 1–2 must shape the turn-3 recommendation (CS + music, no-audition constraint).",
    judgeFacts: ["campus"],
    turns: [
      {
        question: "I'm studying computer science. Any clubs I should look at?",
        expect: { requestTypes: ["campus_life"] },
        checks: [{ kind: "mentions", any: ["acm", "robotics", "data science"] }],
      },
      {
        question: "I also sing, but I don't want to audition for anything.",
        checks: [{ kind: "mentions", any: ["cappella"] }],
      },
      {
        question: "Great — so what's your final shortlist for me?",
        checks: [
          { kind: "mentions", any: ["acm", "robotics", "data science"] },
          { kind: "mentions", any: ["cappella"] },
          {
            kind: "not_mentions_pattern",
            pattern: "jazz ensemble(?![^.]{0,60}audition)",
            taxonomy: "BAD_PERSONALIZATION",
          },
        ],
      },
    ],
  },
  {
    id: "mt-004",
    category: "multi_turn",
    capability: "aid_status",
    persona: "aid_verification_outstanding",
    tags: ["cross_domain"],
    expectedBehavior:
      "Aid arc with a topic switch and return: aid state → worksheet → balance (switch) → back to 'so what was I doing again' (return).",
    judgeFacts: ["aid", "account"],
    turns: [
      {
        question: "Where does my financial aid stand?",
        expect: { requestTypes: ["aid_status", "aid_summary"] },
        checks: [{ kind: "mentions", any: ["verification", "worksheet", "accept"] }],
      },
      {
        question: "What exactly is that worksheet about?",
        checks: [{ kind: "mentions", any: ["verification", "worksheet", "confirm", "fafsa"] }],
      },
      {
        question: "Separate question — what's my balance right now?",
        expect: { requestTypes: ["student_account", "aid_coverage"] },
        checks: [{ kind: "mentions", any: ["$"] }],
      },
      {
        question: "Right, so what was the thing I needed to finish for aid again?",
        checks: [{ kind: "mentions", any: ["worksheet", "verification"] }],
      },
    ],
  },
  {
    id: "mt-005",
    category: "multi_turn",
    capability: "document_status",
    persona: "document_needs_resubmission",
    tags: ["conflict"],
    expectedBehavior:
      "A correction mid-conversation: the student misremembers, Edward corrects from the record, then the student pushes back and Edward holds the line politely.",
    judgeFacts: ["documents"],
    turns: [
      {
        question: "Is my transcript all set?",
        expect: { requestTypes: ["document_status"] },
        checks: [
          { kind: "mentions", any: ["resubmit", "returned", "again", "attention", "sent back"] },
        ],
      },
      {
        question: "No, I'm sure it was accepted last week.",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:you're right|my mistake|it was accepted)",
            taxonomy: "STATE_GROUNDING_FAILURE",
          },
          { kind: "mentions", any: ["returned", "resubmit", "shows", "record", "current"] },
        ],
      },
      {
        question: "Fine. What exactly do I upload this time?",
        checks: [{ kind: "mentions", any: ["transcript"] }],
      },
    ],
  },
  {
    id: "mt-006",
    category: "multi_turn",
    capability: "housing_eligibility",
    persona: "new_admit",
    tags: ["cross_domain"],
    expectedBehavior:
      "Housing desire → gate discovery → cost of the gate → decision support. The $500 deposit amount should surface when asked.",
    judgeFacts: ["housing", "deposit"],
    turns: [
      {
        question: "I want to live on campus. What do I do?",
        checks: [{ kind: "mentions", any: ["deposit", "housing", "preference"] }],
      },
      {
        question: "Why is the deposit involved in housing at all?",
        checks: [{ kind: "mentions", any: ["deposit"] }],
      },
      {
        question: "How much is it again?",
        checks: [{ kind: "mentions", any: ["$500", "500"] }],
      },
      {
        question: "And once I pay, housing opens right away?",
        checks: [
          { kind: "deposit_state_consistent" },
        ],
      },
    ],
  },
  {
    id: "mt-007",
    category: "multi_turn",
    capability: "next_action",
    persona: "deadline_passed",
    expectedBehavior:
      "Triage conversation over overdue state: what's wrong → what first → what can wait. Consistent prioritization across turns.",
    judgeFacts: ["checklist", "deposit"],
    turns: [
      {
        question: "Be straight with me — how bad is my situation?",
        checks: [{ kind: "mentions", any: ["overdue", "passed", "missed", "past due", "late"] }],
      },
      {
        question: "What should I knock out first?",
        checks: [{ kind: "mentions", any: ["deposit"] }],
      },
      {
        question: "Which of the rest can wait a bit?",
        checks: [
          { kind: "mentions", any: ["immuniz", "immunis", "housing", "aid", "verification"] },
        ],
      },
    ],
  },
  {
    id: "mt-008",
    category: "multi_turn",
    capability: "aid_summary",
    persona: "aid_finalized",
    tags: ["cross_domain"],
    expectedBehavior:
      "Numbers conversation: total → per-award drill-down ('the scholarship one') → cross to balance. Pronoun 'that one' must bind to the Aster scholarship.",
    judgeFacts: ["aid", "account"],
    turns: [
      {
        question: "What's my total aid now?",
        expect: { requestTypes: ["aid_summary"] },
        checks: [{ kind: "mentions", any: ["21,395", "21395"] }],
      },
      {
        question: "How much of that is the scholarship?",
        checks: [{ kind: "mentions", any: ["8,000", "8000"] }],
      },
      {
        question: "And where does that leave my bill?",
        checks: [{ kind: "mentions", any: ["$"] }],
      },
    ],
  },
  {
    id: "mt-009",
    category: "multi_turn",
    capability: "holds_and_blockers",
    persona: "payment_pending",
    tags: ["edge_state", "cross_domain"],
    expectedBehavior:
      "The pending-payment state held consistently across three turns — it must not drift into 'unpaid' or 'posted' as the conversation moves.",
    judgeFacts: ["deposit", "housing", "registration"],
    turns: [
      {
        question: "Did my deposit go through?",
        expect: { requestTypes: ["deposit_status", "student_account"] },
        checks: [{ kind: "deposit_state_consistent" }],
      },
      {
        question: "So can I pick housing while it settles?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:yes|go ahead)[^.]{0,32}(?:pick|choose|select)",
            taxonomy: "STATE_GROUNDING_FAILURE",
          },
        ],
      },
      {
        question: "Ugh. Is there anything useful I CAN do today?",
        checks: [{ kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "worksheet", "document"] }],
      },
    ],
  },
  {
    id: "mt-010",
    category: "multi_turn",
    capability: "general_help",
    persona: "new_admit",
    tags: ["cross_domain"],
    expectedBehavior:
      "A realistic onboarding chat that spans four domains and ends by returning to the first topic. The final turn's summary must be consistent with everything said before.",
    judgeFacts: ["checklist", "deposit", "aid", "campus"],
    turns: [
      {
        question: "I just accepted my offer. What's the big picture of what happens now?",
        checks: [{ kind: "mentions", any: ["deposit", "checklist", "document"] }],
      },
      {
        question: "Which part of that involves money?",
        checks: [{ kind: "mentions", any: ["deposit", "$500", "500", "balance", "aid"] }],
      },
      {
        question: "Nice. Anything fun to look forward to?",
        checks: [{ kind: "mentions", any: ["welcome week", "club fair", "club", "event", "jazz"] }],
      },
      {
        question: "Back to the serious stuff — remind me what my very first step was?",
        checks: [{ kind: "mentions", any: ["deposit"] }],
      },
    ],
  },
  {
    id: "mt-011",
    category: "multi_turn",
    capability: "document_status",
    persona: "new_admit",
    tags: ["conflict"],
    expectedBehavior:
      "Contradiction across turns: the student's turn-3 claim contradicts their turn-1 admission; Edward should stay with the record, not the latest claim.",
    judgeFacts: ["documents"],
    turns: [
      {
        question: "I haven't uploaded anything yet. What do you need from me?",
        checks: [
          { kind: "mentions", any: ["identity"] },
          { kind: "mentions", any: ["transcript"] },
        ],
      },
      {
        question: "Which one matters most?",
        checks: [{ kind: "mentions", any: ["transcript", "identity", "immuniz", "immunis", "deposit", "due"] }],
      },
      {
        question: "Actually wait — I did upload my transcript already. So skip that one.",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:okay|sure|got it)[^.]{0,32}skip",
            taxonomy: "STATE_GROUNDING_FAILURE",
          },
          {
            kind: "mentions",
            any: ["no record", "doesn't show", "does not show", "nothing", "not showing", "haven't received", "shows", "indicated", "not been submitted", "checklist"],
          },
        ],
      },
    ],
  },
  {
    id: "mt-012",
    category: "multi_turn",
    capability: "housing_status",
    persona: "housing_assigned",
    expectedBehavior:
      "Recall + boundary: recorded preference recited, then an unanswerable follow-up (roommate) handled honestly, then a genuine next concern.",
    judgeFacts: ["housing", "institutional_gaps", "checklist"],
    turns: [
      {
        question: "Remind me what housing I chose?",
        expect: { requestTypes: ["housing_status"] },
        checks: [{ kind: "mentions", any: ["aster", "double", "on campus", "on-campus"] }],
      },
      {
        question: "Who's my roommate going to be?",
        checks: [
          {
            // Assertions lowercase the answer and match case-insensitively, so
            // a `[A-Z]` "looks like a name" pattern matches any word — the old
            // form fired on the correct refusal "who your roommate will be at
            // this time". Name the fabrication shapes literally instead.
            kind: "not_mentions_pattern",
            pattern: "roommate named|matched you with|paired you with|assigned you (?:a |to a )?roommate",
            taxonomy: "HALLUCINATION",
          },
          // No roommate exists in the record, so any confident answer is
          // fabricated; requiring the refusal language is what catches it.
          {
            kind: "mentions",
            any: ["not", "no ", "cannot", "can't", "isn't", "yet", "unknown"],
          },
          { kind: "no_other_student_data" },
        ],
      },
      {
        question: "Fair enough. What should I focus on instead?",
        checks: [{ kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document", "aid"] }],
      },
    ],
  },
  {
    id: "mt-013",
    category: "multi_turn",
    capability: "aid_application_status",
    persona: "fafsa_missing",
    tags: ["cross_domain"],
    expectedBehavior:
      "From absence to plan: no FAFSA → what it is → filing it → what happens after. Grounded at each step; no invented award projections.",
    judgeFacts: ["aid", "institutional_gaps"],
    turns: [
      {
        question: "Do I have any financial aid lined up?",
        checks: [{ kind: "mentions", any: ["fafsa", "no ", "not", "none"] }],
      },
      {
        question: "What's a FAFSA and why do I need it?",
        checks: [{ kind: "mentions", any: ["federal", "application", "aid"] }],
      },
      {
        question: "If I file it this week, how much will I get?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:you(?:'ll| will) (?:get|receive)|around|approximately|estimate of)\\s*\\$\\d",
            taxonomy: "HALLUCINATION",
          },
        ],
      },
    ],
  },
  {
    id: "mt-014",
    category: "multi_turn",
    capability: "registration_status",
    persona: "nearly_complete",
    tags: ["cross_domain"],
    expectedBehavior:
      "Eligible-state conversation crossing into academics: cleared → which courses → the prerequisite wrinkle. No invented registration window.",
    judgeFacts: ["registration", "academics", "institutional_gaps"],
    turns: [
      {
        question: "Is anything still blocking me from registering?",
        expect: { requestTypes: ["registration_status", "holds_and_blockers"] },
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:blocked|can't register)[^.]{0,32}(?:deposit|transcript|immuni|identity)",
            taxonomy: "STATE_GROUNDING_FAILURE",
          },
        ],
      },
      {
        question: "So which courses would I actually start with?",
        checks: [{ kind: "mentions", any: ["cs 101", "math 140", "eng 110"] }],
      },
      {
        question: "Can I swap one of those for Data Structures?",
        checks: [{ kind: "mentions", any: ["cs 101", "prerequisite"] }],
      },
    ],
  },
  {
    id: "mt-015",
    category: "multi_turn",
    capability: "student_account",
    persona: "aid_refund_due",
    tags: ["edge_state"],
    expectedBehavior:
      "The refund arc: negative balance noticed → why → when is the money coming (no ledger — honest unknown).",
    judgeFacts: ["account", "aid", "institutional_gaps"],
    turns: [
      {
        question: "My account shows a negative number. What is going on?",
        checks: [{ kind: "mentions", any: ["credit", "refund", "exceeds", "more than", "negative"] }],
      },
      {
        question: "So the school owes ME money?",
        checks: [{ kind: "mentions", any: ["credit", "refund", "aid"] }],
      },
      {
        question: "When do I get it?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:within|in|by)\\s+\\d+\\s*(?:day|week|business)",
            taxonomy: "HALLUCINATION",
          },
        ],
      },
    ],
  },
  {
    id: "mt-016",
    category: "multi_turn",
    capability: "greeting",
    persona: "deposit_posted",
    expectedBehavior:
      "Social open, real question second, gratitude close. The greeting must not dump status; the close must not restart the checklist.",
    judgeFacts: ["checklist"],
    turns: [
      {
        question: "hey!",
        judged: false,
        expect: { requestTypes: ["greeting"], maxTools: 1 },
        checks: [{ kind: "max_sentences", max: 4 }],
      },
      {
        question: "quick one — did my deposit actually post?",
        expect: { requestTypes: ["deposit_status", "student_account"] },
        checks: [{ kind: "deposit_state_consistent" }],
      },
      {
        question: "perfect, thanks, that's all I needed",
        judged: false,
        checks: [
          { kind: "block_types", none: ["table", "next_steps"] },
          { kind: "max_sentences", max: 4 },
        ],
      },
    ],
  },
  {
    id: "mt-017",
    category: "multi_turn",
    capability: "missing_documents",
    persona: "transcript_under_review",
    tags: ["cross_domain"],
    expectedBehavior:
      "List → item drill-in by ordinal reference ('the second one') → office ownership. Ordinal binding is the test.",
    judgeFacts: ["documents"],
    turns: [
      {
        question: "List what I still owe you, in order of due date.",
        checks: [
          { kind: "mentions", any: ["deposit"] },
          { kind: "mentions", any: ["identity"] },
        ],
      },
      {
        question: "Tell me more about the identity one.",
        checks: [{ kind: "mentions", any: ["identity"] }],
      },
      {
        question: "Which office handles that?",
        checks: [
          {
            kind: "not_mentions_pattern",
            pattern: "(?:registrar|health services)(?![^.]{0,80}identity)",
            taxonomy: "CONVERSATION_CONTEXT_FAILURE",
          },
        ],
      },
    ],
  },
  {
    id: "mt-018",
    category: "multi_turn",
    capability: "aid_verification_status",
    persona: "aid_verification_outstanding",
    tags: ["cross_domain", "conflict"],
    expectedBehavior:
      "A stressful aid conversation with a false belief injected mid-way; Edward corrects it and the final summary is fully consistent with the record.",
    judgeFacts: ["aid", "registration"],
    turns: [
      {
        question: "Why is my aid still not finalized?",
        checks: [{ kind: "mentions", any: ["verification", "worksheet", "accept"] }],
      },
      {
        question: "And because of that I can't register, right?",
        checks: [{ kind: "no_false_causation" }],
      },
      {
        question: "Okay, summarize: what do I do about aid, and what actually blocks registration?",
        checks: [
          { kind: "mentions", any: ["worksheet", "verification"] },
          { kind: "mentions", any: ["deposit"] },
        ],
      },
    ],
  },
];
