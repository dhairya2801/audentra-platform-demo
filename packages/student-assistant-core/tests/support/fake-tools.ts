/**
 * A deliberately small authoritative-read fixture for tests that care about how
 * an answer is written rather than about the breadth of the underlying records.
 * `graph.spec.ts` keeps its own larger fixture for coverage of the derivation
 * rules themselves.
 */
import type {
  StudentAssistantTools,
  ToolReadResult,
} from "../../src/contracts";

const observedAt = "2026-08-03T12:00:00.000Z";

function available<T>(data: T): ToolReadResult<T> {
  return { status: "available", data, observedAt, sourceVersion: "1" };
}

function unavailable(): ToolReadResult<never> {
  return { status: "unavailable", reason: "not_configured", retryable: false };
}

const requirement = (input: {
  code: string;
  title: string;
  status: "completed" | "ready" | "blocked";
  dueAt: string | null;
}) => ({
  id: `requirement-${input.code}`,
  code: input.code,
  title: input.title,
  description: `${input.title} description.`,
  status: input.status,
  progressPercent: input.status === "completed" ? 100 : 0,
  dueAt: input.dueAt,
  blocking: input.status !== "completed",
  category: "enrollment" as const,
  slug: input.code.replaceAll("_", "-"),
  dependencyCodes: [],
  resolutionOwner: null,
  submissionType: null,
  supportRoute: null,
  sourceOrder: 1,
});

export function createFakeTools(): StudentAssistantTools {
  const requirements = [
    requirement({
      code: "enrollment_deposit",
      title: "Pay your enrollment deposit",
      status: "completed",
      dueAt: "2026-08-25T12:00:00.000Z",
    }),
    requirement({
      code: "financial_aid_verification",
      title: "Complete financial-aid verification",
      status: "ready",
      dueAt: "2026-08-14T12:00:00.000Z",
    }),
    requirement({
      code: "official_transcript",
      title: "Submit your official transcript",
      status: "ready",
      dueAt: "2026-08-18T12:00:00.000Z",
    }),
  ];

  return {
    async getStudentProfile() {
      return available({
        id: "student-authoritative",
        firstName: "Sample",
        lastName: "Student",
        email: "sample.student@aster.example.edu",
        programName: "Undeclared",
        termName: "Fall 2026",
        status: "accepted",
      } as never);
    },
    async getOnboardingChecklist() {
      return available({ items: requirements } as never);
    },
    async getDocumentStatuses() {
      return available({ items: [] } as never);
    },
    async getEnrollmentHolds() {
      return available({
        journey: null,
        requirements: [],
        academicPlan: [],
        financialActions: [],
        unavailableSources: [],
      });
    },
    async getStudentDeadlines() {
      return available({
        items: requirements.map((item, index) => ({
          id: `deadline-${item.code}`,
          label: item.title,
          kind: "other_requirement" as const,
          dueAt: item.dueAt,
          duePrecision: "instant" as const,
          sourceStatus: item.status,
          requirementId: item.id,
          requirementCode: item.code,
          source: "student_requirement" as const,
          completionState:
            item.status === "completed"
              ? ("satisfied" as const)
              : ("outstanding" as const),
          currentlyBlocking: false,
          blockingRequirement: item.status !== "completed",
          hardOrRecommended: "hard" as const,
          dependencyCodes: [],
          resolutionOwner: null,
          navigationRoute: null,
          sourceOrder: index,
          lastVerifiedAt: observedAt,
          sourceVersion: "1",
        })),
        unavailableSources: [],
      });
    },
    async getSupportOptions() {
      return available({ contacts: [], articles: [] } as never);
    },
    async retrieveApprovedPolicy() {
      return unavailable();
    },
    async getFinancialAidStatus() {
      return available({
        academicYear: "2026-2027",
        overallStatus: "incomplete" as const,
        items: [
          {
            id: "aid-verification-worksheet",
            code: "verification_worksheet",
            kind: "verification_document" as const,
            label: "Verification worksheet",
            status: "action_required" as const,
            dueAt: "2026-08-14T12:00:00.000Z",
            navigationRoute: null,
            policyTopic: "verification",
            authoritativeDocumentState: "none" as const,
            lastVerifiedAt: observedAt,
            sourceVersion: "1",
          },
        ],
        awards: [],
        highLevelVerificationStatus: null,
        unavailableSources: [],
      });
    },
    async getFinancialAidSupportOptions() {
      return available({ financialAidSpecificConfigured: false, options: [] });
    },
    async retrieveApprovedFinancialAidPolicy() {
      return unavailable();
    },
    async getStudentHousingStatus() {
      return unavailable();
    },
    async getHousingOptions() {
      return unavailable();
    },
  };
}
