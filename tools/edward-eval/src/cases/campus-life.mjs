/**
 * Campus-life coverage — the information-vs-reasoning distinction. The same
 * club list must serve four intents:
 *   discovery        "what clubs are there?"          → a listing
 *   recommendation   "what should I join?"            → a suggestion with rationale
 *   personalization  "I like music and CS — what?"    → uses the stated interests
 *   comparison       "ACM or Robotics for ML?"        → compares the two
 *
 * Fixture truths (all personas): 8 clubs — ACM Student Chapter (ML SIG),
 * Robotics Club (computer vision), Data Science Society, University Jazz
 * Ensemble (auditioned), A Cappella Society (no audition), Outdoor Adventure,
 * International Students Association, Intramural Soccer; 3 events (Welcome
 * Week Kickoff, Student Club Fair, Jazz Ensemble Auditions).
 */

export const CAMPUS_LIFE_CASES = [
  {
    id: "camp-001",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "What clubs are there?",
    tags: ["discovery"],
    expect: { requestTypes: ["campus_life"], requiredTools: ["getCampusLife"] },
    checks: [{ kind: "mentions_any_fact", fact: "clubNames", min: 4 }],
    expectedBehavior:
      "A listing of the clubs — broad coverage of the eight, no unsolicited 'you should join'. Discovery, not recommendation.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-002",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "What clubs do you think I should join?",
    tags: ["recommendation"],
    expect: { requestTypes: ["campus_life"], requiredTools: ["getCampusLife"] },
    checks: [{ kind: "mentions_any_fact", fact: "clubNames", min: 1 }],
    expectedBehavior:
      "A recommendation with a rationale — a curated pick or an honest 'depends on your interests' follow-up, not a raw dump of all eight.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-003",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "I like music and computer science. What should I join?",
    tags: ["personalization", "recommendation"],
    expect: { requestTypes: ["campus_life"], requiredTools: ["getCampusLife"] },
    checks: [
      { kind: "mentions", any: ["acm", "data science", "robotics"] },
      { kind: "mentions", any: ["jazz", "cappella"] },
    ],
    expectedBehavior:
      "Both stated interests used: a CS-side pick (ACM / Data Science / Robotics) AND a music-side pick (Jazz Ensemble / A Cappella). Ignoring one interest is the failure.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-004",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "ACM or Robotics for someone interested in machine learning?",
    tags: ["comparison"],
    expect: { requestTypes: ["campus_life"], requiredTools: ["getCampusLife"] },
    checks: [
      { kind: "mentions", any: ["acm"] },
      { kind: "mentions", any: ["robotics"] },
    ],
    expectedBehavior:
      "An actual comparison grounded in the descriptions: ACM runs a machine-learning special interest group; Robotics covers computer vision. A reasoned pick (likely ACM for ML) beats a neutral shrug — but both must be characterized correctly.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-005",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "What's happening on campus in the next few weeks?",
    tags: ["discovery"],
    expect: { requestTypes: ["campus_life"] },
    checks: [{ kind: "mentions_any_fact", fact: "eventTitles", min: 2 }],
    expectedBehavior:
      "The three fixture events with their dates/locations; nothing invented beyond them.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-006",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "Are there any music groups that don't require an audition?",
    tags: ["discovery", "edge_state"],
    expect: { requestTypes: ["campus_life"] },
    checks: [
      { kind: "mentions", any: ["cappella"] },
    ],
    expectedBehavior:
      "A Cappella Society — explicitly no-audition; the Jazz Ensemble is auditioned and should not be offered as the no-audition pick.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-007",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "I'm an international student and worried about making friends. Any suggestions?",
    tags: ["personalization", "recommendation"],
    expect: { requestTypes: ["campus_life", "general_question"] },
    checks: [{ kind: "mentions", any: ["international"] }],
    expectedBehavior:
      "The International Students Association is the on-record fit (plus Welcome Week / Club Fair as easy entry points); empathetic but grounded.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-008",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "When is the club fair?",
    tags: ["discovery"],
    expect: { requestTypes: ["campus_life"] },
    checks: [{ kind: "mentions", any: ["club fair", "student center", "fair"] }],
    expectedBehavior:
      "The Student Club Fair with its recorded date and the Student Center location.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-009",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "Is there a chess club?",
    tags: ["discovery", "edge_state"],
    expect: { requestTypes: ["campus_life"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes,|there is a|we have a)[^.]{0,16}chess club(?![^.]{0,30}(?:listed|however))",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "Not on the list — say no chess club is listed, optionally point at the nearest matches or the club fair. Do not invent one.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-010",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "How do I actually sign up for the Robotics Club?",
    expect: { requestTypes: ["campus_life", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "i(?:'ve| have) (?:signed you up|registered you)",
        taxonomy: "ACTION_SAFETY_FAILURE",
      },
    ],
    expectedBehavior:
      "No sign-up mechanism exists in the record; point at the club's next activity (build-night open house) and the Club Fair as the genuine paths.",
    judgeFacts: ["campus", "institutional_gaps"],
  },
  {
    id: "camp-011",
    category: "campus_life",
    capability: "campus_life",
    persona: "nearly_complete",
    question: "Now that my checklist is nearly done, how do I get involved on campus?",
    tags: ["recommendation", "cross_domain"],
    expect: { requestTypes: ["campus_life", "general_question", "general_help"] },
    checks: [{ kind: "mentions_any_fact", fact: "clubNames", min: 1 }],
    expectedBehavior:
      "Campus-life content (clubs/events), not a checklist recitation — the question moved on even if enrollment context is nearby.",
    judgeFacts: ["campus"],
  },
  {
    id: "camp-012",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "Which clubs are good for getting outdoors?",
    tags: ["discovery"],
    expect: { requestTypes: ["campus_life"] },
    checks: [{ kind: "mentions", any: ["outdoor"] }],
    expectedBehavior:
      "Outdoor Adventure Club (hikes, climbing, gear rental); Intramural Soccer is a reasonable adjacent mention.",
    judgeFacts: ["campus"],
  },
];
