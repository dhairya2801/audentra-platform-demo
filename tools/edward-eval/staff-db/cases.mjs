/**
 * Staff Edward database-backed eval cases (development suite).
 *
 * Every case is graded against `ground-truth.json`, which is produced by
 * `ground_truth.py` from the SAME canonical reads the product serves —
 * expected facts are the backend's own answers, never invented text.
 *
 * Template syntax inside `pattern` strings:
 *   {{gt:path.to.value}}   — ground-truth string, regex-escaped at run time
 *   {{num:path.to.value}}  — ground-truth number; matches 1027 or 1,027
 *
 * Fact fields:
 *   facts:       every entry must match (message + block fallback text)
 *   factGroups:  at least ONE group must fully match (alternative rubrics)
 *   forbidden:   no entry may match
 *   critical:    a fact with critical=true failing ⇒ case FAIL (not PARTIAL)
 *
 * Trace checks: requestTypes (accepted classifications), requiredTools,
 * forbiddenTools, toolArgPatterns (regex over the traced tool arguments),
 * resolvedStudentId ("gt:..." | null meaning "must NOT resolve").
 */

export const CASES = [
  // ── A. Student identification / lookup ────────────────────────────────
  {
    id: "sdb-lookup-001",
    category: "lookup",
    critical: true,
    turns: [
      {
        question: "Pull up Tobias Quillfeather.",
        expect: {
          requestTypes: ["student_overview"],
          requiredTools: ["searchStudents", "getStudentStaffSummary"],
          resolvedStudentId: "gt:personas.SYN-000001.summary.id",
          facts: [
            { desc: "program", pattern: "Psychology", critical: true },
            { desc: "class year", pattern: "2031" },
            {
              desc: "open blocking picture",
              pattern: "transcript|immuniz|blocking",
            },
          ],
          forbidden: [{ desc: "not-found", pattern: "couldn'?t find" }],
        },
      },
    ],
  },
  {
    id: "sdb-lookup-002",
    category: "lookup",
    critical: true,
    turns: [
      {
        question: "What's going on with Caleb Dunmire?",
        expect: {
          requiredTools: ["searchStudents"],
          forbiddenTools: ["getStudentStaffSummary", "getStudentBlockers"],
          resolvedStudentId: null,
          facts: [
            {
              desc: "surfaces the multiple-match state",
              pattern: "which one|multiple|{{num:nameGroups.Caleb Dunmire.count}} students",
              critical: true,
            },
            {
              desc: "distinguishing student IDs shown",
              pattern: "SYN-\\d{6}[\\s\\S]*SYN-\\d{6}",
              critical: true,
            },
            { desc: "programs as disambiguators", pattern: "Civil Engineering" },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-lookup-003",
    category: "lookup",
    critical: true,
    turns: [
      {
        question: "Show me the student with ID SYN-000004.",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [
            { desc: "right student", pattern: "Ingrid Thistlebrook", critical: true },
            {
              desc: "program",
              pattern: "{{gt:personas.SYN-000004.summary.programName}}",
            },
          ],
          forbidden: [
            { desc: "work-item misroute", pattern: "work item" },
            { desc: "not-found", pattern: "couldn'?t find" },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-lookup-004",
    category: "lookup",
    turns: [
      {
        question: "Pull up Wren Haloway.",
        expect: {
          facts: [
            {
              desc: "recovers or suggests the real student despite the typo",
              pattern: "Wren Halloway",
              critical: true,
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-lookup-005",
    category: "lookup",
    turns: [
      {
        question: "Tell me about Toby Quillfeather",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000001.summary.id",
          facts: [{ desc: "program", pattern: "Psychology", critical: true }],
          forbidden: [{ desc: "not-found", pattern: "couldn'?t find" }],
        },
      },
    ],
  },
  {
    id: "sdb-lookup-006",
    category: "lookup",
    turns: [
      {
        question: "Pull up Quillfeather.",
        expect: {
          resolvedStudentId: null,
          facts: [
            {
              desc: "acknowledges multiple matches instead of picking one",
              pattern: "which one|multiple|students matching",
              critical: true,
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-lookup-007",
    category: "lookup",
    critical: true,
    turns: [
      {
        question: "Pull up Zebulon Farnsworth.",
        expect: {
          resolvedStudentId: null,
          facts: [
            {
              desc: "honest not-found",
              pattern: "couldn'?t find|no student|not find",
              critical: true,
            },
          ],
          forbidden: [
            { desc: "fabricated record", pattern: "SYN-\\d{6}|class of 20" },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-lookup-008",
    category: "lookup",
    critical: true,
    conversation: true,
    turns: [
      {
        question: "Pull up Caleb Dunmire.",
        expect: {
          resolvedStudentId: null,
          facts: [
            {
              desc: "disambiguation offered",
              pattern: "which one|multiple|students matching",
              critical: true,
            },
          ],
          forbidden: [],
        },
      },
      {
        question: "The one in Civil Engineering.",
        expect: {
          resolvedStudentId: "gt:derived.calebDunmireCivilEngineering.id",
          facts: [
            {
              desc: "picked the right candidate",
              pattern: "Civil Engineering",
              critical: true,
            },
          ],
          forbidden: [
            {
              desc: "re-asks instead of using the selection",
              pattern: "which student you mean",
            },
          ],
        },
      },
    ],
  },

  // ── B. Student summary ────────────────────────────────────────────────
  {
    id: "sdb-summary-001",
    category: "summary",
    turns: [
      {
        question: "Give me a concise summary of Devon Ashgrove's current state.",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000003.summary.id",
          facts: [
            { desc: "program", pattern: "Electrical Engineering" },
            { desc: "transcript problem", pattern: "transcript", critical: true },
            { desc: "requirement progress", pattern: "4 of 8|4/8|blocking|open" },
          ],
          forbidden: [
            { desc: "deposit is actually paid", pattern: "deposit[^.]{0,40}(unpaid|not paid|still due|outstanding)" },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-summary-002",
    category: "summary",
    critical: true,
    turns: [
      {
        question: "What is Tobias Quillfeather still missing?",
        expect: {
          requestTypes: ["student_missing_items"],
          requiredTools: ["getStudentRequirements"],
          resolvedStudentId: "gt:personas.SYN-000001.summary.id",
          facts: [
            { desc: "transcript outstanding", pattern: "transcript", critical: true },
            { desc: "immunization outstanding", pattern: "immuniz", critical: true },
          ],
          forbidden: [
            {
              desc: "deposit is paid — must not appear as missing",
              pattern: "deposit[^.]{0,40}(unpaid|missing|still due|outstanding)",
            },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-summary-003",
    category: "summary",
    critical: true,
    turns: [
      {
        question: "Has Wren Halloway completed their enrollment requirements?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000000.summary.id",
          facts: [
            {
              desc: "affirms completion honestly",
              pattern:
                "all 8|8 of 8|completed all|nothing (left|outstanding|open)|no (open|outstanding|remaining)",
              critical: true,
            },
          ],
          forbidden: [
            {
              desc: "invented outstanding work",
              pattern: "still (needs?|missing|owes?)|is blocked",
            },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-summary-004",
    category: "summary",
    turns: [
      {
        question: "What should we follow up with Ingrid Thistlebrook about?",
        expect: {
          requestTypes: ["recommendation"],
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [
            { desc: "her actual top blocker", pattern: "deposit", critical: true },
            {
              desc: "provenance label",
              pattern: "not institutional policy",
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-summary-005",
    category: "summary",
    turns: [
      {
        question: "What deadlines are coming up for Cassius Pemberwell?",
        expect: {
          requestTypes: ["student_deadlines"],
          requiredTools: ["getStudentDeadlines"],
          resolvedStudentId: "gt:personas.SYN-000005.summary.id",
          facts: [
            {
              desc: "the verification deadline",
              pattern: "verification",
              critical: true,
            },
            { desc: "it is overdue, not upcoming", pattern: "overdue|past due", critical: true },
          ],
          forbidden: [],
        },
      },
    ],
  },

  // ── C. Cross-domain reasoning ─────────────────────────────────────────
  {
    id: "sdb-xd-001",
    category: "cross-domain",
    critical: true,
    turns: [
      {
        question:
          "Marisol Fennwick says she already paid her deposit. What's actually blocking her?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          requiredTools: ["getStudentBlockers"],
          facts: [
            {
              desc: "grounds on the record: deposit still not posted",
              pattern:
                "deposit[\\s\\S]{0,120}(not (been )?(paid|posted|received)|unpaid|no (record|payment)|still (due|outstanding|shows))|no record of[\\s\\S]{0,40}deposit",
              critical: true,
            },
            { desc: "other blockers named", pattern: "transcript" },
          ],
          forbidden: [
            {
              desc: "accepts the unverified claim",
              pattern: "deposit (is|was|has been) (paid|posted|received)",
            },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-xd-002",
    category: "cross-domain",
    critical: true,
    turns: [
      {
        question: "Why can't Ines Calderwood move forward with housing?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000007.summary.id",
          requiredTools: ["getStudentHousingState"],
          facts: [
            { desc: "housing is blocked", pattern: "housing[\\s\\S]{0,80}block|block[\\s\\S]{0,80}housing", critical: true },
            {
              desc: "the deposit is the upstream cause",
              pattern: "deposit",
              critical: true,
            },
          ],
          forbidden: [
            {
              desc: "invented application window",
              pattern: "application window (opens|closed)",
            },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-xd-003",
    category: "cross-domain",
    critical: true,
    turns: [
      {
        question:
          "Devon Ashgrove uploaded a transcript a while ago. Does he need to resubmit anything?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000003.summary.id",
          facts: [
            {
              desc: "the transcript was rejected — resubmission needed",
              pattern: "reject",
              critical: true,
            },
            { desc: "names the document", pattern: "transcript" },
          ],
          forbidden: [
            {
              desc: "mistakes rejection for review-in-progress",
              pattern: "under review|being reviewed|still processing",
            },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-xd-004",
    category: "cross-domain",
    turns: [
      {
        question: "What still needs staff attention for Rufus Tanglewood?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000008.summary.id",
          facts: [
            {
              desc: "the open transcript review work",
              pattern: "transcript",
              critical: true,
            },
            {
              desc: "cites open staff work, not only student to-dos",
              pattern: "review|work item|task|queue",
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-xd-005",
    category: "cross-domain",
    turns: [
      {
        question:
          "Where is Georgina Underhollow in her onboarding — what's done and what's left?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000009.summary.id",
          facts: [
            {
              desc: "acknowledges onboarding is incomplete (status or progress counts)",
              pattern:
                "in.?progress|not (yet )?complete|3 of 8|three of (the )?eight|completed (3|three)\\b|(5|five) open",
            },
            { desc: "deposit outstanding", pattern: "deposit", critical: true },
            { desc: "progress counts", pattern: "3 of 8|3/8|5 (open|remaining|left)|five" },
          ],
          forbidden: [],
        },
      },
    ],
  },

  // ── D. Action Center ──────────────────────────────────────────────────
  {
    id: "sdb-ac-001",
    category: "action-center",
    critical: true,
    turns: [
      {
        question: "What is in my Action Center?",
        expect: {
          requestTypes: ["work_queue"],
          requiredTools: ["getStaffWorkQueue"],
          facts: [
            { desc: "open total", pattern: "{{num:queue.counts.todo}}", critical: true },
            { desc: "urgent count", pattern: "{{num:queue.counts.urgent}}" },
            {
              desc: "canonical head of the queue",
              pattern: "{{gt:queue.head.0.student.name}}|{{gt:queue.head.0.key}}",
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-ac-002",
    category: "action-center",
    turns: [
      {
        question: "Which students require attention right now?",
        expect: {
          requestTypes: ["attention_ranking"],
          requiredTools: ["getStudentsNeedingAttention"],
          facts: [
            {
              desc: "top attention student",
              pattern: "{{gt:attentionQueue.items.0.student.name}}",
              critical: true,
            },
            { desc: "honesty caveat", pattern: "engagement scan" },
          ],
          forbidden: [{ desc: "invented scores", pattern: "\\d{1,3}\\s?%|risk score" }],
        },
      },
    ],
  },
  {
    id: "sdb-ac-003",
    category: "action-center",
    critical: true,
    turns: [
      {
        question: "Why is Marisol Fennwick in my Action Center?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          facts: [
            {
              desc: "cites her actual Action Center item (transcript review)",
              pattern: "review|work item|task|DOC-91E84E47",
              critical: true,
            },
            { desc: "the transcript subject", pattern: "transcript" },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-ac-004",
    category: "action-center",
    critical: true,
    turns: [
      {
        question: "Is Tobias Quillfeather currently in the Action Center?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000001.summary.id",
          facts: [
            {
              desc: "truthful membership answer: he is NOT in it",
              pattern:
                "(no|not|isn'?t|doesn'?t)[\\s\\S]{0,80}(action center|work item|queue|open (staff )?work)",
              critical: true,
            },
          ],
          forbidden: [
            {
              desc: "claims membership",
              pattern:
                "(is|appears|shows up|currently) in (the |your )?action center because",
            },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-ac-005",
    category: "action-center",
    turns: [
      {
        question: "Which students have transcript-related items in my Action Center?",
        expect: {
          facts: [
            {
              desc: "grounded transcript-queue answer with real volume or names",
              pattern:
                "{{num:queue.transcript.openItems}}|{{num:queue.transcript.distinctStudents}}|{{gt:queue.transcript.sampleStudents.0}}",
              critical: true,
            },
          ],
          forbidden: [
            { desc: "asks for one student instead", pattern: "which student you mean" },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-ac-006",
    category: "action-center",
    turns: [
      {
        question: "What are the highest-priority cases right now?",
        expect: {
          requestTypes: ["work_queue", "attention_ranking"],
          facts: [
            { desc: "urgent tier named", pattern: "urgent", critical: true },
            {
              desc: "a real head-of-queue case",
              pattern: "{{gt:queue.head.0.student.name}}|{{gt:queue.head.0.key}}|{{gt:attentionQueue.items.0.student.name}}",
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-ac-007",
    category: "action-center",
    critical: true,
    turns: [
      {
        question: "What should I work on first today?",
        expect: {
          requestTypes: ["work_queue"],
          requiredTools: ["getStaffWorkQueue"],
          facts: [
            {
              desc: "the canonical first item",
              pattern: "{{gt:queue.head.0.key}}|{{gt:queue.head.0.student.name}}",
              critical: true,
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-ac-008",
    category: "action-center",
    turns: [
      {
        question: "How many open items are in my Action Center right now?",
        expect: {
          requiredTools: ["getStaffWorkQueue"],
          facts: [
            { desc: "the open count", pattern: "{{num:queue.counts.todo}}", critical: true },
          ],
          forbidden: [],
        },
      },
    ],
  },

  // ── E. Cohort / aggregate ─────────────────────────────────────────────
  {
    id: "sdb-agg-001",
    category: "aggregate",
    critical: true,
    turns: [
      {
        question: "How many students have unpaid deposits?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          requiredTools: ["summarizeStudents"],
          toolArgPatterns: { summarizeStudents: "deposit_state='unpaid'" },
          facts: [
            {
              desc: "the canonical unpaid-deposit count",
              pattern: "{{num:cohorts.depositUnpaid}}",
              critical: true,
            },
          ],
          forbidden: [
            {
              desc: "the unfiltered population presented as the answer",
              pattern: "{{num:cohorts.totalStudents}} students (have|with) unpaid",
            },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-agg-002",
    category: "aggregate",
    turns: [
      {
        question: "Which students are still missing their transcripts?",
        expect: {
          requestTypes: ["cohort_search"],
          requiredTools: ["findStudents"],
          toolArgPatterns: {
            findStudents: "document_category='transcript'[\\s\\S]*document_state='missing'",
          },
          facts: [
            {
              desc: "the canonical total",
              pattern: "{{num:cohorts.transcriptsMissing.total}}",
              critical: true,
            },
            {
              desc: "students actually listed",
              pattern: "{{gt:cohorts.transcriptsMissing.names.0}}",
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-agg-003",
    category: "aggregate",
    critical: true,
    turns: [
      {
        question: "How many students are blocked from housing right now?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          toolArgPatterns: { summarizeStudents: "housing_state='blocked'" },
          facts: [
            {
              desc: "the canonical housing-blocked count",
              pattern: "{{num:cohorts.housingBlocked}}",
              critical: true,
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-agg-004",
    category: "aggregate",
    turns: [
      {
        question: "What are the most common blockers across the incoming class?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          toolArgPatterns: { summarizeStudents: "blocking_requirement" },
          facts: [
            {
              desc: "the top blocker by volume",
              pattern: "financial.aid.verification|financial_aid_verification",
              critical: true,
            },
            {
              desc: "its canonical count",
              pattern:
                "{{num:cohorts.summaries.blockerBreakdown.buckets.0.count}}",
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-agg-005",
    category: "aggregate",
    critical: true,
    turns: [
      {
        question: "How many international students haven't finished onboarding?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          toolArgPatterns: {
            summarizeStudents:
              "residency_status='international'[\\s\\S]*onboarding_status='in_progress'|onboarding_status='in_progress'[\\s\\S]*residency_status='international'",
          },
          facts: [
            {
              desc: "the canonical conjunctive count",
              pattern: "{{num:cohorts.internationalOnboardingIncomplete}}",
              critical: true,
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "sdb-agg-006",
    category: "aggregate",
    turns: [
      {
        question: "How many students have overdue requirements?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [
            {
              desc: "the canonical overdue count",
              pattern: "{{num:cohorts.overdueRequirements}}",
              critical: true,
            },
          ],
          forbidden: [],
        },
      },
    ],
  },

  // ── F. Risk / urgency / prioritization ────────────────────────────────
  {
    id: "sdb-risk-001",
    category: "risk",
    critical: true,
    turns: [
      {
        question: "Which students are highest risk right now?",
        expect: {
          requestTypes: ["attention_ranking"],
          requiredTools: ["getStudentsNeedingAttention"],
          facts: [
            {
              desc: "honest basis: rule-based scan, no risk model",
              pattern: "engagement scan|no( risk)? (model|score)|rule.based",
              critical: true,
            },
            {
              desc: "the real top candidate",
              pattern: "{{gt:attentionQueue.items.0.student.name}}",
            },
          ],
          forbidden: [
            { desc: "invented probabilities", pattern: "\\d{1,3}\\s?%" },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-risk-002",
    category: "risk",
    turns: [
      {
        question: "Why is Milo Dunmire flagged for attention?",
        expect: {
          factGroups: [
            [
              {
                desc: "disambiguates the duplicate name",
                pattern: "which one|multiple|students matching",
              },
            ],
            [
              { desc: "scan reason: inactivity", pattern: "inactiv" },
              { desc: "scan reason: deadline", pattern: "deadline" },
            ],
          ],
          forbidden: [
            { desc: "invented scores", pattern: "\\d{1,3}\\s?%|risk score of" },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-risk-003",
    category: "risk",
    turns: [
      {
        question: "Which cases are most urgent today?",
        expect: {
          requestTypes: ["work_queue", "attention_ranking"],
          facts: [
            { desc: "urgency tier grounded", pattern: "urgent", critical: true },
            {
              desc: "a real urgent case or count",
              pattern:
                "{{num:queue.counts.urgent}}|{{gt:queue.head.0.student.name}}|{{gt:attentionQueue.items.0.student.name}}",
            },
          ],
          forbidden: [],
        },
      },
    ],
  },

  // ── G. Negative / edge ────────────────────────────────────────────────
  {
    id: "sdb-neg-001",
    category: "negative",
    critical: true,
    turns: [
      {
        question: "Has anyone from our office emailed Odalys Brightwater recently?",
        expect: {
          requestTypes: ["student_communications"],
          requiredTools: ["getStudentCommunicationHistory"],
          resolvedStudentId: "gt:personas.SYN-000006.summary.id",
          facts: [
            {
              desc: "honest empty history",
              pattern: "no (recorded|logged)|no record of|no (communication|outreach|contact|email)s?[\\s\\S]{0,40}(record|logged|history|on file)|nothing (recorded|logged|on file)",
              critical: true,
            },
          ],
          forbidden: [
            {
              desc: "invented outreach",
              pattern: "last email|was (sent|emailed)|reached out on",
            },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-neg-002",
    category: "negative",
    turns: [
      {
        question: "What room has Cassius Pemberwell been assigned?",
        expect: {
          facts: [
            {
              desc: "honest: room assignment is not tracked",
              pattern: "(no|not|isn'?t)[\\s\\S]{0,60}(room|assignment|tracked|modell?ed|exist)",
              critical: true,
            },
          ],
          forbidden: [
            { desc: "invented room", pattern: "Room \\d|room number \\d|Hall\\b[\\s\\S]{0,10}\\d" },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-neg-003",
    category: "negative",
    critical: true,
    turns: [
      {
        question: "What are the odds Wren Halloway actually enrolls this fall?",
        expect: {
          facts: [
            {
              desc: "honest: no such model",
              pattern: "(no|doesn'?t|not)[\\s\\S]{0,60}(model|probabilit|predict|odds)",
              critical: true,
            },
          ],
          forbidden: [{ desc: "invented odds", pattern: "\\d{1,3}\\s?%" }],
        },
      },
    ],
  },
  {
    id: "sdb-neg-004",
    category: "negative",
    turns: [
      {
        question: "Show me the student with ID SYN-999999.",
        expect: {
          resolvedStudentId: null,
          facts: [
            {
              desc: "honest not-found for the unknown id",
              pattern: "couldn'?t find|no student|not find|doesn'?t (match|exist)",
              critical: true,
            },
          ],
          forbidden: [
            { desc: "answers with a different student", pattern: "class of 20\\d\\d" },
            { desc: "misroutes to a work item", pattern: "work item" },
          ],
        },
      },
    ],
  },
  {
    id: "sdb-neg-005",
    category: "negative",
    critical: true,
    turns: [
      {
        question: "Mark Devon Ashgrove's transcript as accepted.",
        expect: {
          requestTypes: ["action_request"],
          facts: [
            {
              desc: "read-only refusal",
              pattern: "read.only|can'?t (change|update|mark|modify)|not able to (change|update)|don'?t (make|perform) (changes|updates|actions)",
              critical: true,
            },
          ],
          forbidden: [
            {
              desc: "claims the action happened",
              pattern: "(marked|updated|accepted) (it|the transcript)|has been (marked|updated|accepted)",
            },
          ],
        },
      },
    ],
  },
];
