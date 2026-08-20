/**
 * Staff Edward v2 — comprehensive development suite (80 scenarios).
 *
 * Graded against `ground-truth.json`, produced by `ground_truth.py` from the
 * SAME canonical reads the Staff Portal serves, so every expected fact is the
 * backend's own answer rather than an author's belief.
 *
 * Template syntax inside `pattern`:
 *   {{gt:path}}   ground-truth string, regex-escaped
 *   {{num:path}}  ground-truth number, thousands-separator tolerant
 *
 * `expect` fields: requestTypes, requiredTools, forbiddenTools,
 * toolArgPatterns, resolvedStudentId ("gt:…" | null = must NOT resolve),
 * facts / factGroups / forbidden.
 *
 * Categories (brief §4 and §5):
 *   identification  exact / duplicate / ID / partial / misspelled / absent
 *   overview        what's going on with X, what's done, what's outstanding
 *   action_center   membership, ordering, topics, counts, "why is X here"
 *   cohort          counts, filters, groupings, listings
 *   risk            attention ranking and its honest basis
 *   cross_domain    reasoning that needs more than one backend read
 *   communications  contact history, including honest empty state
 *   recommendation  what to do next, separated from fact
 *   multi_intent    two- and three-clause questions
 *   context         the conversational-scope family — carry-forward, explicit
 *                   replacement, cohort/queue/global after a student turn
 */

const NO_STICKY_STUDENT = { desc: "no inherited student referent", pattern: "" };

export const CASES = [
  // ── identification ────────────────────────────────────────────────────
  {
    id: "s2-id-001",
    category: "identification",
    turns: [
      {
        question: "Pull up Ingrid Thistlebrook.",
        expect: {
          requestTypes: ["student_overview"],
          requiredTools: ["searchStudents", "getStudentStaffSummary"],
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [
            { desc: "program", pattern: "Chemistry", critical: true },
            { desc: "class year", pattern: "2030" },
          ],
          forbidden: [{ desc: "not-found", pattern: "couldn'?t find" }],
        },
      },
    ],
  },
  {
    id: "s2-id-002",
    category: "identification",
    turns: [
      {
        question: "Show me the student with ID SYN-000008.",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000008.summary.id",
          facts: [
            { desc: "the right student", pattern: "Rufus Tanglewood", critical: true },
            { desc: "program", pattern: "Chemistry" },
          ],
          forbidden: [{ desc: "work-item misroute", pattern: "couldn'?t find that work item" }],
        },
      },
    ],
  },
  {
    id: "s2-id-003",
    category: "identification",
    critical: true,
    turns: [
      {
        question: "What's going on with Milo Dunmire?",
        expect: {
          requiredTools: ["searchStudents"],
          resolvedStudentId: null,
          facts: [
            { desc: "surfaces the ambiguity", pattern: "which one|multiple|\\d+ students", critical: true },
            { desc: "IDs offered to pick with", pattern: "SYN-\\d{6}[\\s\\S]*SYN-\\d{6}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-id-004",
    category: "identification",
    turns: [
      {
        question: "Look up Thistlebrok for me.",
        expect: {
          facts: [
            {
              desc: "offers the close spelling rather than dead-ending",
              pattern: "Thistlebrook|did you mean|closest match",
              critical: true,
            },
          ],
        },
      },
    ],
  },
  {
    id: "s2-id-005",
    category: "identification",
    turns: [
      {
        question: "Pull up Pemberwell.",
        expect: {
          requiredTools: ["searchStudents"],
          resolvedStudentId: null,
          facts: [
            { desc: "surfaces the ambiguity rather than guessing", pattern: "which one|multiple|\\d+ students", critical: true },
            { desc: "IDs offered to pick with", pattern: "SYN-\\d{6}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-id-006",
    category: "identification",
    turns: [
      {
        question: "Do we have a student called Bartholomew Nevermore?",
        expect: {
          resolvedStudentId: null,
          facts: [
            { desc: "honest not-found", pattern: "couldn'?t find|no (?:student|match|record)", critical: true },
          ],
          forbidden: [{ desc: "invented student", pattern: "Bartholomew Nevermore is (?:a|an|enrolled)" }],
        },
      },
    ],
  },
  {
    id: "s2-id-007",
    category: "identification",
    conversation: true,
    turns: [
      {
        question: "What's going on with Milo Dunmire?",
        expect: { resolvedStudentId: null },
      },
      {
        question: "The second one.",
        expect: {
          facts: [{ desc: "resolves a specific candidate", pattern: "Milo Dunmire", critical: true }],
          forbidden: [{ desc: "re-asks instead of resolving", pattern: "which one do you mean" }],
        },
      },
    ],
  },
  {
    id: "s2-id-008",
    category: "identification",
    turns: [
      {
        question: "There was a student named Wren or Ren something with housing problems. Can you find them?",
        expect: {
          facts: [
            { desc: "surfaces the Wren candidate", pattern: "Wren|couldn'?t find|which one", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-id-009",
    category: "identification",
    turns: [
      {
        question: "open Georgina Underhollow",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000009.summary.id",
          facts: [{ desc: "program", pattern: "Computer Science", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-id-010",
    category: "identification",
    turns: [
      {
        question: "who is SYN-000000",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000000.summary.id",
          facts: [{ desc: "the right student", pattern: "Wren Halloway", critical: true }],
        },
      },
    ],
  },

  // ── overview ──────────────────────────────────────────────────────────
  {
    id: "s2-ovw-001",
    category: "overview",
    turns: [
      {
        question: "Tell me about Marisol Fennwick.",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          requiredTools: ["getStudentStaffSummary"],
          facts: [
            { desc: "program", pattern: "Biology", critical: true },
            { desc: "progress", pattern: "{{num:personas.SYN-000002.summary.requirements.completed}} of {{num:personas.SYN-000002.summary.requirements.total}}|4 of 8|{{num:personas.SYN-000002.summary.requirements.openBlocking}} (?:open )?blocking" },
            { desc: "deposit state", pattern: "deposit" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ovw-002",
    category: "overview",
    turns: [
      {
        question: "What's blocking Ingrid Thistlebrook?",
        expect: {
          requestTypes: ["student_blockers"],
          requiredTools: ["getStudentBlockers"],
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [
            { desc: "transcript blocker", pattern: "transcript", critical: true },
            { desc: "immunization blocker", pattern: "immuni" },
            { desc: "deposit blocker", pattern: "deposit", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ovw-003",
    category: "overview",
    turns: [
      {
        question: "What has Wren Halloway already completed?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000000.summary.id",
          facts: [
            {
              desc: "reports the finished state",
              pattern: "all (?:8|eight)|{{num:personas.SYN-000000.summary.requirements.completed}} of {{num:personas.SYN-000000.summary.requirements.total}}|complete",
              critical: true,
            },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ovw-004",
    category: "overview",
    turns: [
      {
        question: "What's still outstanding for Tobias Quillfeather?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000001.summary.id",
          facts: [
            { desc: "names an actual open requirement", pattern: "transcript|immuni|orientation|housing|deposit|aid", critical: true },
            { desc: "quantifies", pattern: "{{num:personas.SYN-000001.summary.requirements.openBlocking}}|two|2 " },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ovw-005",
    category: "overview",
    turns: [
      {
        question: "Give me the deadlines for Devon Ashgrove.",
        expect: {
          requestTypes: ["student_deadlines"],
          requiredTools: ["getStudentDeadlines"],
          resolvedStudentId: "gt:personas.SYN-000003.summary.id",
          facts: [{ desc: "names a dated item", pattern: "20\\d\\d|overdue|due", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-ovw-006",
    category: "overview",
    turns: [
      {
        question: "What documents do we have on file for Marisol Fennwick?",
        expect: {
          requestTypes: ["student_documents"],
          requiredTools: ["getStudentDocuments"],
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          facts: [
            { desc: "the transcript upload", pattern: "transcript", critical: true },
            { desc: "its review state", pattern: "under review|reviewing|submitted" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ovw-007",
    category: "overview",
    turns: [
      {
        question: "Who owns Rufus Tanglewood's case?",
        expect: {
          requestTypes: ["student_ownership"],
          resolvedStudentId: "gt:personas.SYN-000008.summary.id",
          facts: [
            { desc: "answers ownership from the record", pattern: "assign|owner|counselor|advisor|no(?:t| ) (?:assigned|recorded)", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ovw-008",
    category: "overview",
    turns: [
      {
        question: "What's Ines Calderwood's financial situation?",
        expect: {
          requestTypes: ["student_financials"],
          requiredTools: ["getStudentFinancialState"],
          resolvedStudentId: "gt:personas.SYN-000007.summary.id",
          facts: [{ desc: "reports the deposit state", pattern: "deposit", critical: true }],
        },
      },
    ],
  },

  // ── action_center ─────────────────────────────────────────────────────
  {
    id: "s2-ac-001",
    category: "action_center",
    critical: true,
    turns: [
      {
        question: "What's in my Action Center?",
        expect: {
          requestTypes: ["work_queue"],
          requiredTools: ["getStaffWorkQueue"],
          resolvedStudentId: null,
          facts: [
            { desc: "the canonical open count", pattern: "{{num:queue.counts.todo}}", critical: true },
            { desc: "the queue head", pattern: "{{gt:queue.head.0.key}}|{{gt:queue.head.0.title}}" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ac-002",
    category: "action_center",
    turns: [
      {
        question: "What should I work on first today?",
        expect: {
          requestTypes: ["work_queue", "attention_ranking"],
          resolvedStudentId: null,
          facts: [
            { desc: "names a concrete first case", pattern: "{{gt:queue.head.0.key}}|{{gt:queue.head.0.title}}|{{gt:attentionQueue.items.0.student.name}}", critical: true },
          ],
          forbidden: [
            {
              desc: "answers a prioritization question with only a bulk count",
              pattern: "^[^.]{0,40}{{num:queue.counts.todo}} (?:open )?(?:items|work items)\\.?$",
            },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ac-003",
    category: "action_center",
    critical: true,
    turns: [
      {
        question: "Why is Marisol Fennwick in my Action Center?",
        expect: {
          requestTypes: ["student_action_center"],
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          facts: [
            { desc: "cites the actual work item", pattern: "{{gt:personas.SYN-000002.summary.openWork.0.key}}|{{gt:personas.SYN-000002.summary.openWork.0.title}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ac-004",
    category: "action_center",
    critical: true,
    turns: [
      {
        question: "Is Ingrid Thistlebrook currently in the Action Center?",
        expect: {
          requestTypes: ["student_action_center"],
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [
            { desc: "answers no, from the work-item read", pattern: "\\bno\\b|not (?:in|currently)", critical: true },
            { desc: "states there are no open staff work items", pattern: "no open (?:staff )?work item|no staff task", critical: true },
          ],
          forbidden: [
            { desc: "invents membership", pattern: "is (?:currently )?in (?:the|your) action cent(?:er|re)" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ac-005",
    category: "action_center",
    turns: [
      {
        question: "Which cases in my Action Center relate to transcripts?",
        expect: {
          requestTypes: ["work_queue"],
          requiredTools: ["getStaffWorkQueue"],
          resolvedStudentId: null,
          facts: [
            { desc: "the filtered count, not the whole queue", pattern: "{{num:queue.transcript.openItems}}", critical: true },
          ],
          toolArgPatterns: { getStaffWorkQueue: "transcript" },
        },
      },
    ],
  },
  {
    id: "s2-ac-006",
    category: "action_center",
    turns: [
      {
        question: "How many open items are in my Action Center right now?",
        expect: {
          requestTypes: ["work_queue"],
          requiredTools: ["getStaffWorkQueue"],
          facts: [{ desc: "the canonical count", pattern: "{{num:queue.counts.todo}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-ac-007",
    category: "action_center",
    turns: [
      {
        question: "How many students are represented in the Action Center?",
        expect: {
          resolvedStudentId: null,
          facts: [
            { desc: "distinct-student membership count", pattern: "{{num:cohorts.inActionCenter}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ac-008",
    category: "action_center",
    turns: [
      {
        question: "How many of my Action Center items are urgent or escalated?",
        expect: {
          requiredTools: ["getStaffWorkQueue"],
          facts: [
            { desc: "urgent count", pattern: "{{num:queue.counts.urgent}}", critical: true },
            { desc: "escalated count", pattern: "{{num:queue.counts.escalated}}" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ac-009",
    category: "action_center",
    turns: [
      {
        question: "What's the top item on the board?",
        expect: {
          requiredTools: ["getStaffWorkQueue"],
          facts: [
            { desc: "the canonical head item", pattern: "{{gt:queue.head.0.key}}|{{gt:queue.head.0.title}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ac-010",
    category: "action_center",
    turns: [
      {
        question: "I thought Tobias Quillfeather was in my Action Center — why isn't he showing up?",
        expect: {
          requestTypes: ["student_action_center"],
          resolvedStudentId: "gt:personas.SYN-000001.summary.id",
          facts: [
            { desc: "confirms the absence from the queue read", pattern: "no open (?:staff )?work item|not in (?:the|your) action cent|no staff task", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ac-011",
    category: "action_center",
    turns: [
      {
        question: "Show me the deposit cases in the queue.",
        expect: {
          requestTypes: ["work_queue"],
          requiredTools: ["getStaffWorkQueue"],
          resolvedStudentId: null,
          facts: [{ desc: "answers about the queue, filtered", pattern: "deposit|no (?:open )?items", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-ac-012",
    category: "action_center",
    turns: [
      {
        question: "Is Rufus Tanglewood in the Action Center, and if so why?",
        expect: {
          requestTypes: ["student_action_center"],
          resolvedStudentId: "gt:personas.SYN-000008.summary.id",
          facts: [
            { desc: "affirms membership", pattern: "\\byes\\b|is (?:currently )?in", critical: true },
            { desc: "cites the item", pattern: "{{gt:personas.SYN-000008.summary.openWork.0.key}}|{{gt:personas.SYN-000008.summary.openWork.0.title}}", critical: true },
          ],
        },
      },
    ],
  },

  // ── cohort ────────────────────────────────────────────────────────────
  {
    id: "s2-coh-001",
    category: "cohort",
    critical: true,
    turns: [
      {
        question: "How many students have unpaid deposits?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          requiredTools: ["summarizeStudents"],
          resolvedStudentId: null,
          toolArgPatterns: { summarizeStudents: "unpaid" },
          facts: [{ desc: "the canonical count", pattern: "{{num:cohorts.depositUnpaid}}", critical: true }],
          forbidden: [
            { desc: "the unfiltered population presented as the answer", pattern: "{{num:cohorts.totalStudents}} students (?:have|with) unpaid" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-coh-002",
    category: "cohort",
    turns: [
      {
        question: "Which students are still missing a transcript?",
        expect: {
          requestTypes: ["cohort_search", "cohort_aggregate"],
          resolvedStudentId: null,
          facts: [
            { desc: "the canonical count", pattern: "{{num:cohorts.transcriptsMissing.total}}", critical: true },
            { desc: "names actual students", pattern: "{{gt:cohorts.transcriptsMissing.names.0}}" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-coh-003",
    category: "cohort",
    turns: [
      {
        question: "How many students are still in onboarding?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          resolvedStudentId: null,
          facts: [{ desc: "the canonical count", pattern: "{{num:cohorts.onboardingInProgress}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-coh-004",
    category: "cohort",
    turns: [
      {
        question: "Break down the students still in onboarding by program.",
        expect: {
          requestTypes: ["cohort_aggregate"],
          requiredTools: ["summarizeStudents"],
          toolArgPatterns: { summarizeStudents: "program" },
          facts: [
            { desc: "the top program bucket", pattern: "{{gt:cohorts.summaries.onboardingInProgressByProgram.buckets.0.value}}", critical: true },
            { desc: "its count", pattern: "{{num:cohorts.summaries.onboardingInProgressByProgram.buckets.0.count}}" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-coh-005",
    category: "cohort",
    turns: [
      {
        question: "What's the most common blocker across the class?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [
            { desc: "the canonical top blocker", pattern: "financial.aid.verification|financial aid verification", critical: true },
            { desc: "its count", pattern: "{{num:cohorts.summaries.blockerBreakdown.buckets.0.count}}" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-coh-006",
    category: "cohort",
    turns: [
      {
        question: "How many students have housing blocked?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [{ desc: "the canonical count", pattern: "{{num:cohorts.housingBlocked}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-coh-007",
    category: "cohort",
    turns: [
      {
        question: "How many students have an overdue requirement?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [{ desc: "the canonical count", pattern: "{{num:cohorts.overdueRequirements}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-coh-008",
    category: "cohort",
    turns: [
      {
        question: "how many international students haven't finished onboarding",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [
            { desc: "the canonical count", pattern: "{{num:cohorts.internationalOnboardingIncomplete}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-coh-009",
    category: "cohort",
    turns: [
      {
        question: "Which programs have the most students with unpaid deposits?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          toolArgPatterns: { summarizeStudents: "program" },
          facts: [
            { desc: "the top program", pattern: "{{gt:cohorts.summaries.depositUnpaidByProgram.buckets.0.value}}", critical: true },
            { desc: "its count", pattern: "{{num:cohorts.summaries.depositUnpaidByProgram.buckets.0.count}}" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-coh-010",
    category: "cohort",
    turns: [
      {
        question: "How many students still owe an immunization record?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [{ desc: "the canonical count", pattern: "{{num:cohorts.immunizationOpen}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-coh-011",
    category: "cohort",
    turns: [
      {
        question: "How big is the roster?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [{ desc: "the canonical population", pattern: "{{num:cohorts.totalStudents}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-coh-012",
    category: "cohort",
    turns: [
      {
        question: "How many students have outstanding financial aid verification?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [
            { desc: "the canonical count", pattern: "{{num:cohorts.aidVerificationOutstanding}}", critical: true },
          ],
        },
      },
    ],
  },

  // ── risk ──────────────────────────────────────────────────────────────
  {
    id: "s2-risk-001",
    category: "risk",
    turns: [
      {
        question: "Which students are highest risk right now?",
        expect: {
          requestTypes: ["attention_ranking"],
          requiredTools: ["getStudentsNeedingAttention"],
          resolvedStudentId: null,
          facts: [
            { desc: "names the canonical top candidate", pattern: "{{gt:attentionQueue.items.0.student.name}}", critical: true },
            { desc: "states the basis is a rule-based scan", pattern: "engagement scan|rule[- ]based|not a risk model" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-risk-002",
    category: "risk",
    turns: [
      {
        question: "Who should I contact first today?",
        expect: {
          requestTypes: ["attention_ranking"],
          facts: [
            { desc: "names a concrete student", pattern: "{{gt:attentionQueue.items.0.student.name}}", critical: true },
            { desc: "gives a reason", pattern: "inactive|deadline|blocking|no .{0,20}activity" },
          ],
        },
      },
    ],
  },
  {
    id: "s2-risk-003",
    category: "risk",
    turns: [
      {
        question: "What are the odds Wren Halloway actually enrolls this fall?",
        expect: {
          requestTypes: ["unsupported_metric"],
          facts: [
            { desc: "declines the prediction", pattern: "(?:no|don'?t have|not).{0,60}(?:probabilit|predictive model|forecast)", critical: true },
          ],
          forbidden: [{ desc: "implied prediction", pattern: "\\b\\d{1,3}%\\s*(?:chance|likely|probability)" }],
        },
      },
    ],
  },
  {
    id: "s2-risk-004",
    category: "risk",
    turns: [
      {
        question: "Why is the top attention student flagged?",
        expect: {
          facts: [
            { desc: "gives the actual reason codes", pattern: "inactive|deadline|blocking requirement|engagement scan", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-risk-005",
    category: "risk",
    turns: [
      {
        question: "Rank my students by melt risk.",
        expect: {
          requestTypes: ["attention_ranking", "unsupported_metric"],
          facts: [
            { desc: "disclaims the missing model", pattern: "no .{0,40}(?:melt|risk) (?:model|score)|not a risk model|rule[- ]based", critical: true },
          ],
        },
      },
    ],
  },

  // ── cross_domain ──────────────────────────────────────────────────────
  {
    id: "s2-xd-001",
    category: "cross_domain",
    critical: true,
    turns: [
      {
        question: "Marisol Fennwick says she already paid her deposit. What's actually blocking her?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          requiredTools: ["getStudentBlockers"],
          facts: [
            { desc: "corrects the claim from the record", pattern: "deposit", critical: true },
            { desc: "not posted", pattern: "not (?:been )?(?:paid|posted)|unpaid|outstanding", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-xd-002",
    category: "cross_domain",
    turns: [
      {
        question: "Ingrid Thistlebrook's identity document is accepted. What is preventing her housing?",
        expect: {
          requestTypes: ["student_housing"],
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [
            { desc: "names the real housing gate", pattern: "deposit|transcript|immuni|orientation", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-xd-003",
    category: "cross_domain",
    turns: [
      {
        question: "Why is Rufus Tanglewood still in my Action Center if his checklist looks mostly done?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000008.summary.id",
          facts: [
            { desc: "separates the staff work item from student progress", pattern: "{{gt:personas.SYN-000008.summary.openWork.0.key}}|{{gt:personas.SYN-000008.summary.openWork.0.title}}|work item|staff task", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-xd-004",
    category: "cross_domain",
    turns: [
      {
        question: "Devon Ashgrove paid his deposit — so why is he not enrollment-ready?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000003.summary.id",
          facts: [
            { desc: "names remaining blockers rather than the deposit", pattern: "transcript|immuni|orientation|aid|housing", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-xd-005",
    category: "cross_domain",
    turns: [
      {
        question: "Is Cassius Pemberwell close to done, and what's the one thing left?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000005.summary.id",
          facts: [
            { desc: "reports near-complete progress", pattern: "{{num:personas.SYN-000005.summary.requirements.completed}} of {{num:personas.SYN-000005.summary.requirements.total}}|7 of 8|one|1 (?:open )?blocking", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-xd-006",
    category: "cross_domain",
    turns: [
      {
        question: "Georgina Underhollow has an overdue deadline. What does she need to do and who owns it?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000009.summary.id",
          facts: [
            { desc: "names an actual open requirement", pattern: "transcript|immuni|deposit|orientation|housing|aid", critical: true },
            { desc: "attributes the office or owner", pattern: "Registrar|Student Health|Student Accounts|Housing|Financial Aid|New Student Programs|student" },
          ],
        },
      },
    ],
  },

  // ── communications ────────────────────────────────────────────────────
  {
    id: "s2-com-001",
    category: "communications",
    turns: [
      {
        question: "Has anyone contacted Ingrid Thistlebrook?",
        expect: {
          requestTypes: ["student_communications"],
          requiredTools: ["getStudentCommunicationHistory"],
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [
            { desc: "honest empty state, from the read", pattern: "(?:no|zero) (?:recorded )?communications?|nothing recorded|no(?:t| one has| record of) (?:contact|been contacted)", critical: true },
          ],
          forbidden: [{ desc: "invented outreach", pattern: "we emailed|was contacted on|last contacted on \\w+ \\d" }],
        },
      },
    ],
  },
  {
    id: "s2-com-002",
    category: "communications",
    turns: [
      {
        question: "When did we last email Marisol Fennwick?",
        expect: {
          requiredTools: ["getStudentCommunicationHistory"],
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          facts: [
            { desc: "honest empty state", pattern: "no (?:recorded )?communications?|nothing recorded|no (?:record|email)", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-com-003",
    category: "communications",
    turns: [
      {
        question: "Did Devon Ashgrove ever reply to us?",
        expect: {
          requiredTools: ["getStudentCommunicationHistory"],
          resolvedStudentId: "gt:personas.SYN-000003.summary.id",
          facts: [
            { desc: "honest empty state", pattern: "no (?:recorded )?communications?|nothing recorded|no (?:reply|response|record)", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-com-004",
    category: "communications",
    turns: [
      {
        question: "Did Odalys Brightwater open our last email?",
        expect: {
          requestTypes: ["unsupported_metric"],
          facts: [
            { desc: "states email tracking does not exist", pattern: "(?:no|don'?t|not|aren'?t|isn'?t|can'?t).{0,60}(?:track|open rate|read receipt|opens and clicks)", critical: true },
          ],
        },
      },
    ],
  },

  // ── recommendation ────────────────────────────────────────────────────
  {
    id: "s2-rec-001",
    category: "recommendation",
    turns: [
      {
        question: "What should I contact Ingrid Thistlebrook about?",
        expect: {
          requestTypes: ["recommendation"],
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [
            { desc: "grounds the recommendation in a real open item", pattern: "deposit|transcript|immuni|orientation", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-rec-002",
    category: "recommendation",
    turns: [
      {
        question: "What's the best next step for Georgina Underhollow?",
        expect: {
          requestTypes: ["recommendation"],
          resolvedStudentId: "gt:personas.SYN-000009.summary.id",
          facts: [
            { desc: "names a real next step", pattern: "deposit|transcript|immuni|orientation|housing|aid", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-rec-003",
    category: "recommendation",
    turns: [
      {
        question: "Draft an email to Marisol Fennwick about her transcript.",
        expect: {
          requestTypes: ["draft_email"],
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          facts: [
            { desc: "produces a draft about the transcript", pattern: "transcript", critical: true },
          ],
          forbidden: [{ desc: "claims to have sent it", pattern: "I(?:'ve| have) sent|email sent" }],
        },
      },
    ],
  },
  {
    id: "s2-rec-004",
    category: "recommendation",
    turns: [
      {
        question: "Mark Ingrid Thistlebrook's transcript as accepted.",
        expect: {
          requestTypes: ["action_request"],
          resolvedStudentId: null,
          facts: [
            { desc: "refuses the write", pattern: "can'?t|cannot|read[- ]only|don'?t (?:make|change)", critical: true },
          ],
        },
      },
    ],
  },

  // ── multi_intent ──────────────────────────────────────────────────────
  {
    id: "s2-mi-001",
    category: "multi_intent",
    critical: true,
    turns: [
      {
        question: "Is Marisol Fennwick in my Action Center, what's blocking her, and has anyone contacted her?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          requiredTools: ["getStudentCommunicationHistory"],
          facts: [
            { desc: "answers the membership clause", pattern: "{{gt:personas.SYN-000002.summary.openWork.0.key}}|action cent|work item", critical: true },
            { desc: "answers the blockers clause", pattern: "deposit|transcript|immuni|orientation|housing", critical: true },
            { desc: "answers the communications clause from a read", pattern: "no (?:recorded )?communications?|nothing recorded|no contact", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-mi-002",
    category: "multi_intent",
    turns: [
      {
        question: "How many students have unpaid deposits, and which programs have the most?",
        expect: {
          resolvedStudentId: null,
          facts: [
            { desc: "the count clause", pattern: "{{num:cohorts.depositUnpaid}}", critical: true },
            { desc: "the program clause", pattern: "{{gt:cohorts.summaries.depositUnpaidByProgram.buckets.0.value}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-mi-003",
    category: "multi_intent",
    turns: [
      {
        question: "Find Tobias Quillfeather, tell me what's blocking him, and whether he's in the Action Center.",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000001.summary.id",
          facts: [
            { desc: "the blockers clause", pattern: "transcript|immuni|orientation|deposit|housing|aid", critical: true },
            { desc: "the Action Center clause, from the work-item read", pattern: "no open (?:staff )?work item|not in (?:the|your) action cent|no staff task", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-mi-004",
    category: "multi_intent",
    turns: [
      {
        question: "What's Devon Ashgrove's deposit state and what documents are on file?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000003.summary.id",
          requiredTools: ["getStudentDocuments"],
          facts: [
            { desc: "the deposit clause", pattern: "deposit", critical: true },
            { desc: "the documents clause", pattern: "transcript|identity|immuni|residency|\\.pdf", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-mi-005",
    category: "multi_intent",
    turns: [
      {
        question: "How many open Action Center items do I have, and how many students do they cover?",
        expect: {
          facts: [
            { desc: "the item count", pattern: "{{num:queue.counts.todo}}", critical: true },
            { desc: "the distinct-student count", pattern: "\\b\\d{2,4}\\b students", critical: true },
          ],
        },
      },
    ],
  },

  // ── context (the conversational-scope family) ─────────────────────────
  {
    id: "s2-ctx-001",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "Tell me about Ingrid Thistlebrook.",
        expect: { resolvedStudentId: "gt:personas.SYN-000004.summary.id" },
      },
      {
        question: "What's blocking her?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [{ desc: "carries the referent forward", pattern: "deposit|transcript|immuni|orientation", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-002",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "What's blocking Ingrid Thistlebrook?",
        expect: { resolvedStudentId: "gt:personas.SYN-000004.summary.id" },
      },
      {
        question: "How many students still have unpaid deposits?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          resolvedStudentId: null,
          facts: [{ desc: "the tenant-wide count", pattern: "{{num:cohorts.depositUnpaid}}", critical: true }],
          forbidden: [{ desc: "scoped to the prior student", pattern: "Ingrid|Thistlebrook" }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-003",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "Tell me about Ingrid Thistlebrook.",
        expect: { resolvedStudentId: "gt:personas.SYN-000004.summary.id" },
      },
      {
        question: "What is in my Action Center?",
        expect: {
          requestTypes: ["work_queue"],
          requiredTools: ["getStaffWorkQueue"],
          resolvedStudentId: null,
          facts: [{ desc: "the canonical queue count", pattern: "{{num:queue.counts.todo}}", critical: true }],
          forbidden: [{ desc: "reinterpreted as the prior student's membership", pattern: "Ingrid|Thistlebrook" }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-004",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "Tell me about Ingrid Thistlebrook.",
        expect: { resolvedStudentId: "gt:personas.SYN-000004.summary.id" },
      },
      {
        question: "What about Marisol Fennwick?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          facts: [{ desc: "answers about the new student", pattern: "Marisol|Mari\\b|Fennwick", critical: true }],
          forbidden: [{ desc: "keeps the old referent", pattern: "Ingrid|Thistlebrook" }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-005",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "Why is Ingrid Thistlebrook blocked?",
        expect: { resolvedStudentId: "gt:personas.SYN-000004.summary.id" },
      },
      {
        question: "Which students are waiting on transcripts?",
        expect: {
          requestTypes: ["cohort_search", "cohort_aggregate"],
          resolvedStudentId: null,
          facts: [{ desc: "answers the cohort question", pattern: "{{num:cohorts.transcriptsMissing.total}}", critical: true }],
          forbidden: [{ desc: "scoped to the prior student", pattern: "Ingrid|Thistlebrook" }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-006",
    category: "context",
    conversation: true,
    turns: [
      {
        question: "Tell me about Marisol Fennwick.",
        expect: { resolvedStudentId: "gt:personas.SYN-000002.summary.id" },
      },
      {
        question: "Has anyone contacted her?",
        expect: {
          requestTypes: ["student_communications"],
          resolvedStudentId: "gt:personas.SYN-000002.summary.id",
          requiredTools: ["getStudentCommunicationHistory"],
          facts: [{ desc: "answers for the carried referent", pattern: "no (?:recorded )?communications?|nothing recorded", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-007",
    category: "context",
    conversation: true,
    turns: [
      {
        question: "Tell me about Ingrid Thistlebrook.",
        expect: { resolvedStudentId: "gt:personas.SYN-000004.summary.id" },
      },
      {
        question: "What about housing?",
        expect: {
          requestTypes: ["student_housing"],
          resolvedStudentId: "gt:personas.SYN-000004.summary.id",
          facts: [{ desc: "reads her housing state", pattern: "housing", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-008",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "Tell me about Ingrid Thistlebrook.",
        expect: { resolvedStudentId: "gt:personas.SYN-000004.summary.id" },
      },
      {
        question: "What deadlines should my team care about today?",
        expect: {
          resolvedStudentId: null,
          forbidden: [{ desc: "answers a team-wide question about one student", pattern: "Ingrid|Thistlebrook" }],
          facts: [
            { desc: "answers at team scope", pattern: "{{num:cohorts.overdueRequirements}}|{{num:queue.counts.todo}}|{{gt:attentionQueue.items.0.student.name}}|overdue|queue|action cent", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ctx-009",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "What's blocking Devon Ashgrove?",
        expect: { resolvedStudentId: "gt:personas.SYN-000003.summary.id" },
      },
      {
        question: "Who should I contact first today?",
        expect: {
          requestTypes: ["attention_ranking"],
          resolvedStudentId: null,
          facts: [{ desc: "answers from the attention queue", pattern: "{{gt:attentionQueue.items.0.student.name}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-010",
    category: "context",
    conversation: true,
    turns: [
      {
        question: "Show me the top transcript case in my Action Center.",
        expect: { requestTypes: ["work_queue"] },
      },
      {
        question: "What else is blocking that student?",
        expect: {
          facts: [{ desc: "answers about the student on that case", pattern: "transcript|deposit|immuni|orientation|blocking|housing|aid", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-011",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "How many students have unpaid deposits?",
        expect: { requestTypes: ["cohort_aggregate"], resolvedStudentId: null },
      },
      {
        question: "Break that down by program.",
        expect: {
          requestTypes: ["cohort_aggregate"],
          resolvedStudentId: null,
          toolArgPatterns: { summarizeStudents: "program" },
          facts: [
            { desc: "the top program bucket", pattern: "{{gt:cohorts.summaries.depositUnpaidByProgram.buckets.0.value}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "s2-ctx-012",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "Tell me about Rufus Tanglewood.",
        expect: { resolvedStudentId: "gt:personas.SYN-000008.summary.id" },
      },
      {
        question: "How big is my queue?",
        expect: {
          requestTypes: ["work_queue"],
          resolvedStudentId: null,
          facts: [{ desc: "the canonical queue count", pattern: "{{num:queue.counts.todo}}", critical: true }],
          forbidden: [{ desc: "scoped to the prior student", pattern: "Rufus|Tanglewood" }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-013",
    category: "context",
    conversation: true,
    turns: [
      {
        question: "What's blocking Cassius Pemberwell?",
        expect: { resolvedStudentId: "gt:personas.SYN-000005.summary.id" },
      },
      {
        question: "And his documents?",
        expect: {
          requestTypes: ["student_documents"],
          resolvedStudentId: "gt:personas.SYN-000005.summary.id",
          requiredTools: ["getStudentDocuments"],
          facts: [{ desc: "reads his documents", pattern: "transcript|identity|immuni|residency|\\.pdf|no documents", critical: true }],
        },
      },
    ],
  },
  {
    id: "s2-ctx-014",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "Tell me about Ines Calderwood.",
        expect: { resolvedStudentId: "gt:personas.SYN-000007.summary.id" },
      },
      {
        question: "What's the most common blocker across the whole class?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          resolvedStudentId: null,
          facts: [
            { desc: "the canonical top blocker", pattern: "financial.aid.verification|financial aid verification", critical: true },
          ],
          forbidden: [{ desc: "scoped to the prior student", pattern: "Ines|Calderwood" }],
        },
      },
    ],
  },
];

export default CASES;
