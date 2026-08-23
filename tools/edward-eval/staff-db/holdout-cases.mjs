/**
 * Staff Edward database-backed HOLDOUT cases.
 *
 * These were written alongside the development suite but deliberately NOT
 * run while implementing fixes. They exist to show the improvements
 * generalize instead of being patched onto the dev questions. Same schema
 * as cases.mjs.
 */

export const HOLDOUT_CASES = [
  {
    id: "hold-amb-001",
    category: "lookup",
    critical: true,
    turns: [
      {
        question: "Pull up Helia Fennwick.",
        expect: {
          resolvedStudentId: null,
          facts: [
            {
              desc: "multiple-match state surfaced",
              pattern: "which one|multiple|{{num:nameGroups.Helia Fennwick.count}} students",
              critical: true,
            },
            {
              desc: "distinguishing student IDs shown",
              pattern: "SYN-\\d{6}[\\s\\S]*SYN-\\d{6}",
              critical: true,
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "hold-lookup-001",
    category: "lookup",
    turns: [
      {
        question: "Open SYN-000008.",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000008.summary.id",
          facts: [
            { desc: "right student", pattern: "Rufus Tanglewood", critical: true },
            { desc: "program", pattern: "{{gt:personas.SYN-000008.summary.programName}}" },
          ],
          forbidden: [{ desc: "work-item misroute", pattern: "couldn'?t find" }],
        },
      },
    ],
  },
  {
    id: "hold-block-001",
    category: "summary",
    critical: true,
    turns: [
      {
        question: "What is blocking Georgina Underhollow?",
        expect: {
          requestTypes: ["student_blockers"],
          resolvedStudentId: "gt:personas.SYN-000009.summary.id",
          facts: [
            { desc: "deposit blocker", pattern: "deposit", critical: true },
            { desc: "transcript blocker", pattern: "transcript", critical: true },
            { desc: "immunization blocker", pattern: "immuniz" },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "hold-ac-001",
    category: "action-center",
    critical: true,
    turns: [
      {
        question: "Why does Rufus Tanglewood show up in my Action Center?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000008.summary.id",
          facts: [
            {
              desc: "cites his actual open work item",
              pattern: "review|work item|task|DOC-0942048A",
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
    id: "hold-ac-002",
    category: "action-center",
    critical: true,
    turns: [
      {
        question: "Is Wren Halloway in the Action Center right now?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000000.summary.id",
          facts: [
            {
              desc: "truthful membership answer: she is NOT in it",
              pattern:
                "(no|not|isn'?t|doesn'?t)[\\s\\S]{0,80}(action center|work item|queue|open (staff )?work)",
              critical: true,
            },
          ],
          forbidden: [
            {
              desc: "claims membership",
              pattern: "(is|appears|shows up|currently) in (the |your )?action center because",
            },
          ],
        },
      },
    ],
  },
  {
    id: "hold-multi-001",
    category: "cross-domain",
    critical: true,
    conversation: true,
    turns: [
      {
        question: "Pull up Ines Calderwood.",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000007.summary.id",
          facts: [
            {
              desc: "program",
              pattern: "{{gt:personas.SYN-000007.summary.programName}}",
            },
          ],
          forbidden: [],
        },
      },
      {
        question: "Why can't she pick housing yet?",
        expect: {
          resolvedStudentId: "gt:personas.SYN-000007.summary.id",
          facts: [
            {
              desc: "housing blocked",
              pattern: "housing[\\s\\S]{0,80}block|block[\\s\\S]{0,80}housing",
              critical: true,
            },
            { desc: "deposit is the upstream cause", pattern: "deposit", critical: true },
          ],
          forbidden: [],
        },
      },
    ],
  },
  {
    id: "hold-agg-001",
    category: "aggregate",
    critical: true,
    turns: [
      {
        question: "How many students still owe their enrollment deposit?",
        expect: {
          requestTypes: ["cohort_aggregate"],
          facts: [
            {
              desc: "the canonical unpaid-deposit count",
              pattern: "{{num:cohorts.depositUnpaid}}",
              critical: true,
            },
          ],
          forbidden: [
            {
              desc: "the unfiltered population as the answer",
              pattern: "{{num:cohorts.totalStudents}} students (still owe|have|with)",
            },
          ],
        },
      },
    ],
  },
  {
    id: "hold-agg-002",
    category: "aggregate",
    turns: [
      {
        question: "Break down the students still in onboarding by program.",
        expect: {
          requestTypes: ["cohort_aggregate"],
          toolArgPatterns: {
            summarizeStudents: "onboarding_status='in_progress'",
          },
          facts: [
            {
              desc: "the canonical matching total",
              pattern:
                "{{num:cohorts.summaries.onboardingInProgressByProgram.matchingStudents}}",
              critical: true,
            },
            {
              desc: "the top program bucket",
              pattern:
                "{{gt:cohorts.summaries.onboardingInProgressByProgram.buckets.0.value}}",
            },
          ],
          forbidden: [],
        },
      },
    ],
  },
];
