/**
 * Intent and tool families for the university benchmark.
 *
 * Families are what the grader reasons in, so a routing failure is
 * classified the same way before and after Staff Edward's request-type
 * vocabulary changes: both the legacy types and the staff-aware types map
 * onto one family list.
 */

export const FAMILY_OF_REQUEST_TYPE = {
  // student-scoped
  student_overview: "student",
  student_missing_items: "student",
  student_blockers: "student",
  student_documents: "student",
  student_deadlines: "student",
  student_financials: "student",
  student_housing: "student",
  student_appointments: "student",
  student_communications: "student",
  student_engagement: "student",
  student_timeline: "student",
  student_ownership: "student",
  student_action_center: "student",
  recommendation: "student",
  draft_email: "student",
  draft_sms: "student",
  draft_call_points: "student",
  // cohorts
  cohort_search: "cohort",
  cohort_aggregate: "cohort",
  // queue / operations
  work_queue: "queue",
  work_item_detail: "queue",
  queue_aggregate: "queue",
  daily_briefing: "queue",
  attention_ranking: "ranking",
  // inquiries
  inquiries: "inquiries",
  inquiry_aggregate: "inquiries",
  // staff-aware (new)
  staff_profile: "staff",
  staff_workload: "staff",
  staff_availability: "staff",
  staff_caseload: "staff",
  staff_appointments: "staff",
  staff_comparison: "staff",
  my_work: "my_work",
  my_profile: "my_work",
  team_overview: "team",
  staff_directory: "staff",
  department_operations: "department",
  // institution knowledge
  playbook_lookup: "institution",
  action_rules: "institution",
  mailbox_read: "institution",
  // canned / refusals
  greeting: "refusal",
  capability_overview: "refusal",
  action_request: "refusal",
  unsupported_metric: "refusal",
  unsupported_or_out_of_scope: "refusal",
  not_found: "refusal",
  general_question: "general",
};

export const TOOL_FAMILIES = {
  student: [
    "searchStudents",
    "getStudentStaffSummary",
    "getStudentRequirements",
    "getStudentDocuments",
    "getStudentBlockers",
    "getStudentDeadlines",
    "getStudentFinancialState",
    "getStudentHousingState",
    "getStudentAppointments",
    "getStudentCommunicationHistory",
    "getStudentEngagementSignals",
    "getStudentTimeline",
    "getStudentOwnership",
  ],
  cohort: ["findStudents", "summarizeStudents"],
  queue: [
    "getStaffWorkQueue",
    "summarizeWorkQueue",
    "searchWorkQueue",
    "getWorkItemDetail",
    "getMorningBriefing",
  ],
  inquiries: ["getInquiries", "summarizeInquiries", "searchInquiries", "getInquiryThread"],
  staff: [
    "getStaffProfile",
    "searchStaff",
    "getStaffAvailability",
    "getStaffCaseload",
    "getStaffAppointments",
    "getStaffTeam",
    "compareStaff",
  ],
  department: ["getComponentSummary", "getStaffTeam"],
  ranking: ["getStudentsNeedingAttention"],
};
