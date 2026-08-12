import type { StudentAssistantRequestType } from "../src/contracts";
import type { StudentDeadlineWindow } from "../src/contracts";

export interface StudentAssistantEvaluationFixture {
  id: string;
  question: string;
  expectedRequestType: StudentAssistantRequestType;
  expectedDeadlineWindow?: StudentDeadlineWindow;
  expectedRemainingCodes?: string[];
  expectedCompletedCodes?: string[];
  expectedMissingDocumentCodes?: string[];
  expectedNextCode?: string;
  expectedBlockedCodes?: string[];
  expectedDeadlineTitles?: string[];
  expectsApprovedPolicy?: boolean;
  expectsSupportOptions?: boolean;
  history?: Array<{ role: "user" | "assistant"; content: string }>;
}

export const studentAssistantEvaluationFixtures = [
  {
    id: "remaining-direct",
    question: "What onboarding steps am I yet to do?",
    expectedRequestType: "remaining_steps",
    expectedRemainingCodes: ["official_transcript", "orientation_registration"],
  },
  {
    id: "remaining-outstanding",
    question: "Which enrollment tasks are still outstanding?",
    expectedRequestType: "remaining_steps",
    expectedRemainingCodes: ["official_transcript", "orientation_registration"],
  },
  {
    id: "remaining-unfinished",
    question: "Show me my unfinished onboarding requirements.",
    expectedRequestType: "remaining_steps",
    expectedRemainingCodes: ["official_transcript", "orientation_registration"],
  },
  {
    id: "completed",
    question: "Which onboarding steps are already done?",
    expectedRequestType: "completed_steps",
    expectedCompletedCodes: ["enrollment_deposit", "immunization_record"],
  },
  {
    id: "documents-follow-up",
    question: "What about documents?",
    expectedRequestType: "missing_documents",
    expectedMissingDocumentCodes: ["official_transcript"],
    history: [
      { role: "user", content: "What do I still need to do?" },
      {
        role: "assistant",
        content: "Final transcript and orientation registration remain.",
      },
    ],
  },
  {
    id: "next-action",
    question: "What should I do next?",
    expectedRequestType: "next_action",
    expectedNextCode: "official_transcript",
  },
  {
    id: "blockers",
    question: "Are there any holds or blockers on my enrollment?",
    expectedRequestType: "holds_and_blockers",
    expectedBlockedCodes: [],
  },
  {
    id: "deadlines",
    question: "What deadlines are coming up?",
    expectedRequestType: "deadlines",
    expectedDeadlineWindow: "upcoming",
    expectedDeadlineTitles: ["Final transcript", "Orientation registration"],
  },
  {
    id: "deadlines-due-today",
    question: "What is due today?",
    expectedRequestType: "deadlines",
    expectedDeadlineWindow: "today",
  },
  {
    id: "deadlines-this-week",
    question: "What is due this week?",
    expectedRequestType: "deadlines",
    expectedDeadlineWindow: "this_week",
  },
  {
    id: "deadlines-missed",
    question: "What deadlines have I missed?",
    expectedRequestType: "deadlines",
    expectedDeadlineWindow: "overdue",
  },
  {
    id: "registration-blocked",
    question: "Why can't I register?",
    expectedRequestType: "holds_and_blockers",
  },
  {
    id: "handle-first",
    question: "What should I handle first?",
    expectedRequestType: "next_action",
  },
  {
    id: "explanation",
    question: "Why is the final transcript required?",
    expectedRequestType: "explain_requirement",
    expectsApprovedPolicy: true,
  },
  {
    id: "support",
    question: "I need help from an enrollment counselor.",
    expectedRequestType: "request_support",
    expectsSupportOptions: true,
  },
  {
    id: "onboarding-status",
    question: "How is my onboarding progress?",
    expectedRequestType: "onboarding_status",
  },
  {
    id: "housing-status",
    question: "What is my housing status?",
    expectedRequestType: "housing_status",
  },
  {
    id: "housing-options",
    question: "What housing options are available?",
    expectedRequestType: "housing_options",
  },
  {
    id: "housing-remaining",
    question: "What do I still need to complete for housing?",
    expectedRequestType: "housing_remaining_steps",
  },
  {
    id: "housing-deadline",
    question: "When is my housing deadline?",
    expectedRequestType: "housing_deadlines",
    expectedDeadlineWindow: "all",
  },
  {
    id: "housing-next-action",
    question: "What should I do next for housing?",
    expectedRequestType: "housing_next_action",
  },
  {
    id: "housing-support",
    question: "Who should I contact about housing?",
    expectedRequestType: "housing_support",
    expectsSupportOptions: true,
  },
] as const satisfies readonly StudentAssistantEvaluationFixture[];
