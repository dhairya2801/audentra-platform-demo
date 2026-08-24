/**
 * Staff Edward v2 — holdout suite (20 scenarios).
 *
 * Written with the development suite and **not executed** until the
 * implementation work was finished. Same capabilities, different students,
 * different phrasings, different intent combinations and different
 * conversational transitions, so the score measures generalization.
 */

export const HOLDOUT_CASES = [
  {
    id: "h2-id-001",
    category: "identification",
    turns: [
      {
        question: "pull Odalys Brightwater",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000006.summary.id",
          facts: [{ desc: "program", pattern: "Chemistry", critical: true }],
        },
      },
    ],
  },
  {
    id: "h2-id-002",
    category: "identification",
    turns: [
      {
        question: "Which student is SYN-000007?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000007.summary.id",
          facts: [{ desc: "the right student", pattern: "Ines Calderwood", critical: true }],
        },
      },
    ],
  },
  {
    id: "h2-ovw-001",
    category: "overview",
    turns: [
      {
        question: "give me the state of play on Cassius Pemberwell",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000005.summary.id",
          facts: [
            { desc: "program", pattern: "Data Science", critical: true },
            { desc: "progress", pattern: "7 of 8|{{num:personas.SYN-000005.summary.requirements.completed}} of {{num:personas.SYN-000005.summary.requirements.total}}|one|1 (?:open )?blocking" },
          ],
        },
      },
    ],
  },
  {
    id: "h2-ovw-002",
    category: "overview",
    turns: [
      {
        question: "anything outstanding on Odalys Brightwater's record?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000006.summary.id",
          facts: [
            { desc: "names a real open item", pattern: "transcript|immuni|deposit|orientation|housing|aid", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-ac-001",
    category: "action_center",
    turns: [
      {
        question: "Is Wren Halloway on my task board?",
        expect: {
          requestTypes: ["student_action_center"],
          resolvedStudentId: "gt:personas.SYN-000000.summary.id",
          facts: [
            { desc: "answers no from the work-item read", pattern: "no open (?:staff )?work item|not (?:in|on)|no staff task", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-ac-002",
    category: "action_center",
    turns: [
      {
        question: "Why does Devon Ashgrove have a task on him?",
        expect: {
          requestTypes: ["student_action_center"],
          resolvedStudentId: "gt:personas.SYN-000003.summary.id",
          facts: [
            { desc: "cites the actual work item", pattern: "{{gt:personas.SYN-000003.summary.openWork.0.key}}|{{gt:personas.SYN-000003.summary.openWork.0.title}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-ac-003",
    category: "action_center",
    turns: [
      {
        question: "how much is on my plate right now",
        expect: {
          requestTypes: ["work_queue"],
          resolvedStudentId: null,
          facts: [{ desc: "the canonical count", pattern: "{{num:queue.counts.todo}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "h2-coh-001",
    category: "cohort",
    turns: [
      {
        question: "how many admits still haven't paid up",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [{ desc: "the canonical count", pattern: "{{num:cohorts.depositUnpaid}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "h2-coh-002",
    category: "cohort",
    turns: [
      {
        question: "Split the students missing transcripts by program.",
        expect: {
          requestTypes: ["cohort_aggregate"],
          toolArgPatterns: { summarizeStudents: "program" },
          facts: [
            { desc: "the top program bucket", pattern: "{{gt:cohorts.summaries.transcriptsMissingByProgram.buckets.0.value}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-coh-003",
    category: "cohort",
    turns: [
      {
        question: "How many students have finished onboarding?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [{ desc: "the canonical count", pattern: "{{num:cohorts.onboardingCompleted}}", critical: true }],
        },
      },
    ],
  },
  {
    id: "h2-risk-001",
    category: "risk",
    turns: [
      {
        question: "who's slipping",
        expect: {
          requestTypes: ["attention_ranking"],
          facts: [
            { desc: "names the canonical top candidate", pattern: "{{gt:attentionQueue.items.0.student.name}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-xd-001",
    category: "cross_domain",
    turns: [
      {
        question: "Ines Calderwood's transcript is fine. What's actually holding her back?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000007.summary.id",
          facts: [
            { desc: "names real blockers", pattern: "deposit|immuni|orientation|housing|aid|transcript", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-xd-002",
    category: "cross_domain",
    turns: [
      {
        question: "Wren Halloway finished everything — is there anything left for us to do on her?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000000.summary.id",
          facts: [
            { desc: "reports the finished state honestly", pattern: "complete|nothing|no open|all (?:8|eight)", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-com-001",
    category: "communications",
    turns: [
      {
        question: "any outreach history on Cassius Pemberwell?",
        expect: {
          requiredTools: ["getStudentCommunicationHistory"],
          resolvedStudentId: "gt:personas.SYN-000005.summary.id",
          facts: [
            { desc: "honest empty state", pattern: "no (?:recorded )?communications?|nothing recorded|no (?:outreach|contact|record)", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-rec-001",
    category: "recommendation",
    turns: [
      {
        question: "Give me talking points for a call with Devon Ashgrove.",
        expect: {
          requestTypes: ["draft_call_points"],
          resolvedStudentId: "gt:personas.SYN-000003.summary.id",
          facts: [
            { desc: "grounded in a real open item", pattern: "transcript|immuni|deposit|orientation|housing|aid", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-mi-001",
    category: "multi_intent",
    turns: [
      {
        question: "Who's at the top of my queue and how many students are in the Action Center overall?",
        expect: {
          facts: [
            { desc: "the head item clause", pattern: "{{gt:queue.head.0.key}}|{{gt:queue.head.0.title}}|{{gt:queue.head.0.student.name}}", critical: true },
            { desc: "the membership-count clause", pattern: "{{num:cohorts.inActionCenter}}", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-mi-002",
    category: "multi_intent",
    turns: [
      {
        question: "What's Odalys Brightwater blocked on and when is it due?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000006.summary.id",
          facts: [
            { desc: "the blocker clause", pattern: "transcript|immuni|deposit|orientation|housing|aid", critical: true },
            { desc: "the deadline clause", pattern: "20\\d\\d|overdue|due", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "h2-ctx-001",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "Tell me about Cassius Pemberwell.",
        expect: { resolvedStudentId: "gt:personas.SYN-000005.summary.id" },
      },
      {
        question: "How many students are missing transcripts?",
        expect: {
          requestTypes: ["cohort_aggregate", "cohort_search"],
          resolvedStudentId: null,
          facts: [{ desc: "the tenant-wide count", pattern: "{{num:cohorts.transcriptsMissing.total}}", critical: true }],
          forbidden: [{ desc: "scoped to the prior student", pattern: "Cassius|Pemberwell" }],
        },
      },
    ],
  },
  {
    id: "h2-ctx-002",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "What's blocking Wren Halloway?",
        expect: { resolvedStudentId: "gt:personas.SYN-000000.summary.id" },
      },
      {
        question: "Now Rufus Tanglewood.",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000008.summary.id",
          facts: [{ desc: "answers about the new student", pattern: "Rufus|Tanglewood", critical: true }],
          forbidden: [{ desc: "keeps the old referent", pattern: "Wren|Halloway" }],
        },
      },
    ],
  },
  {
    id: "h2-ctx-003",
    category: "context",
    conversation: true,
    critical: true,
    turns: [
      {
        question: "Tell me about Georgina Underhollow.",
        expect: { resolvedStudentId: "gt:personas.SYN-000009.summary.id" },
      },
      {
        question: "Which students need attention most?",
        expect: {
          requestTypes: ["attention_ranking"],
          resolvedStudentId: null,
          facts: [
            { desc: "answers from the attention queue", pattern: "{{gt:attentionQueue.items.0.student.name}}", critical: true },
          ],
          forbidden: [{ desc: "scoped to the prior student", pattern: "Georgina|Underhollow" }],
        },
      },
    ],
  },
];

export default HOLDOUT_CASES;
