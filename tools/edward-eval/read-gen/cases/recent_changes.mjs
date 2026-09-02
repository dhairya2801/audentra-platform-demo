import { S, f, soft, forbid, one, NONE_PATTERN, NO_UUID } from "./common.mjs";

const ADA = S("ada");
const KWAME = S("kwame");
const LUCIA = S("lucia");
const BRUNO = S("bruno");

export const RECENT_CHANGES_CASES = [
  one("rg-rec-001", "recent_changes", "staff", "leandro",
    "what changed recently for Ada Kettleby",
    "Recent timeline: deposit received (Aug 28), housing plans completed (Aug 30), financial-aid verification moved to under review (Aug 31), a document review completed. Any two of those.",
    {
      resolvedStudentId: `gt:${ADA}.id`,
      anyOfTools: [["getStudentTimeline", "getStudentStaffSummary", "getStudentRequirements", "getStudentCommunicationHistory"]],
      factGroups: [
        [f("deposit", "deposit"), f("verification or housing", "verification|housing|review")],
        [f("verification", "verification"), f("housing", "housing")],
        [f("a recent date", `{{date:${ADA}.recent.requirementEvents.0.occurredAt}}|{{date:${ADA}.recent.requirementEvents.1.occurredAt}}|{{date:${ADA}.recent.requirementEvents.2.occurredAt}}`)],
      ],
      forbidden: [NO_UUID, forbid("claims no activity", "no recent (?:activity|changes)|nothing has changed")],
    }),
  one("rg-rec-002", "recent_changes", "staff", "marcus",
    "anything new on Kwame Oakenshaw in the last week or so?",
    "Timeline: immunization requirement rejected / FA verification in progress (Aug 26), an immunisation work item completed (Aug 27). Honest scope if the timeline is thin is acceptable, but must not deny activity outright.",
    {
      resolvedStudentId: `gt:${KWAME}.id`,
      anyOfTools: [["getStudentTimeline", "getStudentStaffSummary", "getStudentRequirements", "getStudentBlockers", "getStudentOwnership"]],
      factGroups: [
        [f("immunization", "immuni[sz]ation")],
        [f("verification", "verification")],
        [f("the completed item", `{{gt:${KWAME}.work.recentlyDone.0.key}}`)],
        [f("honest scope", "timeline (?:only|doesn'?t)|not (?:tracked|recorded) as|no (?:timeline|activity) (?:entries|events) in")],
      ],
    }),
  one("rg-rec-003", "recent_changes", "student", "ada",
    "anything new for me?",
    "Six unread messages (deposit received, onboarding complete, housing preference complete, financial-aid verification needs review, ...).",
    {
      anyOfTools: [["getStudentMessages", "getOnboardingChecklist", "getStudentDeadlines"]],
      facts: [
        f("unread count or a subject", `{{num:${ADA}.messages.unreadCount}} (?:unread|new|messages)|{{any:${ADA}.messages.unreadSubjects}}`),
      ],
      forbidden: [forbid("claims nothing new", "nothing new|no new messages|no unread")],
    }),
  one("rg-rec-004", "recent_changes", "student", "lucia",
    "any updates since i last checked",
    "Transcript moved to under review and financial-aid verification is in progress (both Aug 26); one unread welcome message. Must not invent a decision.",
    {
      anyOfTools: [["getStudentMessages", "getOnboardingChecklist", "getDocumentStatuses", "getStudentDeadlines"]],
      factGroups: [
        [f("transcript under review", "transcript[^.]{0,80}(?:under review|being reviewed|in review)")],
        [f("verification in progress", "verification[^.]{0,80}(?:in progress|started|underway)")],
        [f("unread message", "unread|welcome")],
      ],
      forbidden: [forbid("invents a transcript decision", "transcript[^.]{0,60}(?:accepted|approved|rejected)")],
    }),
  one("rg-rec-005", "recent_changes", "staff", "priya",
    "what happened with bruno stonebrook lately",
    "Journey completed; requirements all completed on Aug 5; nothing open. Honest: little recent activity.",
    {
      resolvedStudentId: `gt:${BRUNO}.id`,
      anyOfTools: [["getStudentTimeline", "getStudentStaffSummary", "getStudentRequirements"]],
      factGroups: [
        [f("completed", "complete")],
        [f("nothing recent", NONE_PATTERN + "|quiet|no recent")],
      ],
      forbidden: [forbid("invents open work", "open (?:work )?items? (?:are|is|include)|overdue")],
    }),
];
