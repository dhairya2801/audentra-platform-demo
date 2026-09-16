"""The staff tool catalog: names, descriptions, argument schemas, provenance.

Unlike student tools — which take no caller arguments at all — staff tools
accept a small, schema-validated argument set. The invariant that keeps this
safe: **identity arguments are never model-chosen.** ``studentId`` is always
bound server-side from the resolved conversation referent, ``workItemId`` from
a validated key in the message, and the tenant from ``AuthContext`` alone. The
model may propose tool names and non-identity filters; everything else is
bound after validation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from audentra.domain.student_cohort import (
    COHORT_GROUP_BY,
    CohortFilterError,
    build_cohort_filter,
)
from audentra.integrations.assistant.university_catalog import UNIVERSITY_TOOLS

JsonDict = dict[str, Any]

STAFF_TOOL_NAMES: tuple[str, ...] = (
    "searchStudents",
    "findStudents",
    "summarizeStudents",
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
    "getStudentsNeedingAttention",
    "getStaffWorkQueue",
    "getMorningBriefing",
    "getWorkItemDetail",
    "getInquiries",
    "getInquiryThread",
    "getPlaybooks",
    "getActionRules",
    "getMailboxMessages",
    # Staff-aware reads (bounded, SQL-side): people, teams, queues, inquiries,
    # departments. Identity arguments (staffId) are bound server-side from the
    # resolved staff entity or the signed-in member, never from a model.
    "getStaffProfile",
    "searchStaff",
    "getStaffTeam",
    "getStaffCaseload",
    "getStaffAppointments",
    "getStaffAvailability",
    "compareStaff",
    "summarizeWorkQueue",
    "searchWorkQueue",
    "summarizeInquiries",
    "searchInquiries",
    "getComponentSummary",
    # Approved institutional knowledge: policies, procedures, handbook,
    # calendar, office directory. Text arguments only; `studentId` is bound
    # server-side when the turn is about a resolved student.
    "searchInstitutionalKnowledge",
)

# Source-of-truth classification, mirroring the student catalog's discipline.
# "student_state" reads one student's canonical record; "operational_state"
# reads tenant-wide staff work/queue data; "institution_knowledge" reads
# staff-authored content. Nothing in this catalog reads the preview workspace
# or any synthetic risk field.
STAFF_TOOL_INFORMATION_CLASS: Mapping[str, str] = {
    "searchStudents": "student_state",
    "findStudents": "student_state",
    "summarizeStudents": "student_state",
    "getStudentStaffSummary": "student_state",
    "getStudentRequirements": "student_state",
    "getStudentDocuments": "student_state",
    "getStudentBlockers": "student_state",
    "getStudentDeadlines": "student_state",
    "getStudentFinancialState": "student_state",
    "getStudentHousingState": "student_state",
    "getStudentAppointments": "student_state",
    "getStudentCommunicationHistory": "student_state",
    "getStudentEngagementSignals": "student_state",
    "getStudentTimeline": "student_state",
    "getStudentOwnership": "operational_state",
    "getStudentsNeedingAttention": "operational_state",
    "getStaffWorkQueue": "operational_state",
    "getMorningBriefing": "operational_state",
    "getWorkItemDetail": "operational_state",
    "getInquiries": "operational_state",
    "getInquiryThread": "operational_state",
    "getPlaybooks": "institution_knowledge",
    "getActionRules": "institution_knowledge",
    "getMailboxMessages": "operational_state",
    "getStaffProfile": "operational_state",
    "searchStaff": "operational_state",
    "getStaffTeam": "operational_state",
    "getStaffCaseload": "operational_state",
    "getStaffAppointments": "operational_state",
    "getStaffAvailability": "operational_state",
    "compareStaff": "operational_state",
    "summarizeWorkQueue": "operational_state",
    "searchWorkQueue": "operational_state",
    "summarizeInquiries": "operational_state",
    "searchInquiries": "operational_state",
    "getComponentSummary": "operational_state",
    "searchInstitutionalKnowledge": "institution_knowledge",
}

STAFF_TOOL_DESCRIPTIONS: Mapping[str, str] = {
    "searchStudents": (
        "Search the canonical student roster by name, student ID (external "
        "reference), and/or program. Returns concise summaries with the "
        "student ID and requirement progress; when no exact name matches, "
        "close-spelling suggestions are returned marked matchQuality=fuzzy "
        "and must be confirmed, never silently chosen."
    ),
    "findStudents": (
        "Find the students matching a cohort filter and report how many match "
        "in total. Filters combine conjunctively and cover admission offer "
        "status, deposit state, onboarding status, a named requirement's "
        "state, a document category's state, financial-aid document state, "
        "housing state, program, class year, residency and citizenship, "
        "assigned staff, open Action Center work, and overdue requirements. "
        "Use this for questions of the form 'which students ...'."
    ),
    "summarizeStudents": (
        "Count the students matching a cohort filter, grouped by one "
        "dimension: offer status, deposit state, onboarding status, program, "
        "class year, assigned staff, housing state, or blocking requirement. "
        "Use this for 'how many ...' and 'what are the most common ...' "
        "questions instead of listing every student."
    ),
    "getStudentStaffSummary": (
        "Read one student's staff-facing overview: identity, program, offer "
        "and deposit state, onboarding status, requirement counts, open "
        "blocking count, next due date, and open staff work."
    ),
    "getStudentRequirements": (
        "Read one student's full enrollment checklist: every requirement with "
        "status, blocking flag, due date, and responsible office."
    ),
    "getStudentDocuments": (
        "Read one student's document records (transcript, immunization/health, "
        "residency, identity, and others) with statuses including rejection."
    ),
    "getStudentBlockers": (
        "Read what is blocking one student's enrollment: derived blockers from "
        "blocking requirements and unpaid deposit, each with owner and "
        "clearing action. The platform operates no registrar hold system."
    ),
    "getStudentDeadlines": (
        "Read one student's authoritative deadlines: requirement due dates, "
        "the offer response deadline, and aid-document due dates, bucketed."
    ),
    "getStudentFinancialState": (
        "Read one student's financial state: awards, aid document "
        "requirements, cost of attendance, payments, derived remaining "
        "balance, and deposit state. No disbursement schedule exists."
    ),
    "getStudentHousingState": (
        "Read one student's housing preference and housing requirement state. "
        "No application window or room assignment is modeled."
    ),
    "getStudentAppointments": "Read one student's scheduled appointments.",
    "getStudentCommunicationHistory": (
        "Read one student's recorded communications (email, sms, voice, "
        "portal) with direction, delivery status, and resolution status, plus "
        "support inquiries. Recorded interactions only — no vendor send/"
        "receive integration or open/click tracking exists."
    ),
    "getStudentEngagementSignals": (
        "Read one student's engagement snapshot: completion percentage, "
        "blocking count, days to next deadline, upload failures, help "
        "requested, open support cases, and last meaningful activity, with "
        "the snapshot's freshness timestamp."
    ),
    "getStudentTimeline": (
        "Read a bounded chronological timeline for one student from work "
        "logs, documents, payments, recorded communications, appointments, "
        "and inquiries."
    ),
    "getStudentOwnership": (
        "Read who is responsible for one student: the primary academic adviser "
        "and every other assigned counsellor (admissions, financial aid, "
        "international, housing) with employment status and leave dates, plus "
        "the owner of each open Action Center item and inquiry and the offices "
        "responsible for open requirements. Use it for 'who advises / who owns / "
        "who is handling / is their adviser on leave' questions."
    ),
    "getStudentsNeedingAttention": (
        "Read the deterministic attention queue: students flagged by the "
        "engagement scan with priority, reason codes, and evidence. "
        "Rule-based signals, not risk scores or probabilities."
    ),
    "getStaffWorkQueue": (
        "Read the staff work queue — the SAME source of truth the Staff "
        "Portal's Action Center renders — as one bounded page (25 items) in "
        "canonical order (priority, then due date), filtered server-side by "
        "ownership (mine / unassigned / all), assignee name, component, "
        "status, due window, stale (in progress and untouched for 10+ days), "
        "ownerRisk (owner departed, on leave, or away), and topic (keyword "
        "over key, title, student and owner), plus board-wide counts and "
        "per-component / per-owner rollups. Authoritative for what is in the "
        "Action Center, in which order, and who is behind. Use "
        "summarizeWorkQueue for counts and groupings, searchWorkQueue for a "
        "differently sorted page."
    ),
    "getStaffProfile": (
        "Read one staff member's profile: title, role, component, manager, "
        "direct reports, employment status (active / on leave / departed), "
        "current absence, caseload vs cap, open/overdue/stale work, "
        "appointments today and this week, open inquiries, and calendar "
        "summary (next open slot, open slots in 14 days, weekly days). Bound "
        "to the resolved staff member or the signed-in member ('me')."
    ),
    "searchStaff": (
        "Search the staff directory by name, component (department) or role, "
        "or list who is away right now (absentNow: on leave, vacation, sick, "
        "conference); returns briefs with match quality (exact_name, "
        "first_name, last_name, partial), employment status, absence, advisee "
        "and open-item counts. This — not the student roster — is where a "
        "colleague's name resolves."
    ),
    "getStaffTeam": (
        "Read a manager's reporting subtree with per-person flags "
        "(over_cap, on_leave_with_caseload, departed_with_caseload, "
        "no_open_slots, falling_behind, spare_capacity), caseload, backlog, "
        "availability, and the component summary (students with departed / "
        "on-leave adviser, accepted students without an adviser, unassigned "
        "and overdue component items). Bound to the resolved manager or 'me'."
    ),
    "getStaffCaseload": (
        "Read the students currently assigned to one staff member (handle `me` "
        "or a resolved colleague), with each student's requirement progress, "
        "advising status, deposit state and open/overdue work. Without `role` it "
        "covers every assignment role the person holds (primary adviser, "
        "admissions counselor, financial-aid counselor, international adviser, "
        "housing coordinator); pass `role` to narrow. Filters: advisingStatus, "
        "depositState, withOpenWork, withOverdueWork. Each student's open/overdue "
        "work counts every open Action Center item on that student, whoever owns "
        "it; for items assigned to this person use searchWorkQueue with "
        "ownership=mine (or assigneeName) and combine."
    ),
    "getStaffAppointments": (
        "Read one staff member's appointments in a window (today, tomorrow, "
        "this week, next two weeks, the past week, or those awaiting an "
        "outcome) with the student on each and per-status counts."
    ),
    "getStaffAvailability": (
        "Read one staff member's bookability: whether students can book them "
        "now (and why not: on leave, departed, no hours), next open slot, "
        "open slots in the next 14 days, booked slots, weekly appointment "
        "days, current and upcoming absences."
    ),
    "compareStaff": (
        "Read the profiles of two to four staff members side by side "
        "(caseload, backlog, appointments, availability) for a comparison "
        "question. Bound to the resolved staff members."
    ),
    "summarizeWorkQueue": (
        "Count Action Center work in SQL with optional filters (ownership "
        "mine/unassigned, a specific assignee, component, status, priority, "
        "due window, topic keyword, stale in-progress, escalated, action "
        "type, work type) and an optional grouping (assignee, component, "
        "status, priority, due_window, action_type, work_type, student). "
        "Returns totals — open, unassigned, urgent, overdue, due today, due "
        "in 7 days, stale, escalated, distinct students — and the top "
        "buckets. Use this for every 'how many items…' / 'which team or "
        "person has the most…' question instead of reading the board."
    ),
    "searchWorkQueue": (
        "Read a bounded page of Action Center items matching the same filters "
        "as summarizeWorkQueue, in canonical order (or by due date / oldest / "
        "stalest), with the true total behind the page."
    ),
    "summarizeInquiries": (
        "Count student support inquiries in SQL: awaiting a first reply "
        "(status new), open, waiting on the student, resolved, unassigned, "
        "urgent, older than 24 hours, the oldest awaiting inquiry, and "
        "optional grouping by status, assignee, topic or priority."
    ),
    "searchInquiries": (
        "Read a bounded page of support inquiries (oldest first by default) "
        "with subject, topic, priority, age, assignee and student, matching "
        "status / ownership / topic / age filters."
    ),
    "getComponentSummary": (
        "Read one department's (component's) operational picture: headcount, "
        "who is on leave / departed / absent right now, each member's open, "
        "overdue and stale work, the component's open / unassigned / overdue "
        "/ urgent / escalated / due-today items, document reviews, and open "
        "inquiries."
    ),
    "getMorningBriefing": (
        "Read today's Morning Brew — the same start-of-day briefing the Staff "
        "Portal renders, built by the same canonical composer. Carries the "
        "population and cohort counts, what changed in the last 24 hours, the "
        "ranked attention themes, the work-queue summary, open student "
        "requests, and an explicit list of metrics the platform does not hold. "
        "Use for 'what's my briefing', 'what changed overnight', 'catch me up' "
        "and other start-of-day questions. Takes no arguments."
    ),
    "getWorkItemDetail": (
        "Read one work item's full detail: state, saved staff outreach draft, "
        "interactions, recorded "
        "communications, call transcript references, outcomes, comments, and "
        "history. A saved draft is staff-authored unsent text, not verified institutional "
        "evidence or proof of delivery."
    ),
    "getInquiries": "Read student support inquiries with status, priority, and assignee.",
    "getInquiryThread": "Read the full message thread of one support inquiry.",
    "getPlaybooks": (
        "Read staff-authored core plays and knowledge cards. Prose guidance "
        "written by staff — not system-enforced policy."
    ),
    "searchInstitutionalKnowledge": (
        "Search the institution's approved, versioned knowledge — published "
        "policies, staff procedures, handbook chapters, program guides, the "
        "academic calendar and the office directory — by a text `query`. Use "
        "it for what a rule or deadline is, what happens when it is missed, "
        "exceptions and extensions, amounts and dates, service levels, "
        "escalation paths, and which office owns a step. Every hit carries "
        "code, version, effective date and owning office; when the turn is "
        "about a resolved student the result says whether each document "
        "applies to that student (residency, first-year or transfer, admit "
        "term, housing plan, program). Filters: audience (student|internal), "
        "kind, office code, limit."
    ),
    "getActionRules": "Read the configured staff automation rules.",
    "getMailboxMessages": (
        "Read up to 25 messages from the authenticated staff member's authorized "
        "seven-day mailbox cache. Every mailbox grant is rechecked. Results are "
        "ranked by explicit urgent/time-sensitive language, then matched student, "
        "then recency; this is not a predictive priority score."
    ),
}

STAFF_RECEIPT_SOURCES: Mapping[str, str] = {
    "searchStudents": "student_roster",
    "findStudents": "student_cohort",
    "summarizeStudents": "student_cohort",
    "getStudentStaffSummary": "student_record",
    "getStudentRequirements": "requirements",
    "getStudentDocuments": "documents",
    "getStudentBlockers": "blockers",
    "getStudentDeadlines": "deadlines",
    "getStudentFinancialState": "financials",
    "getStudentHousingState": "housing",
    "getStudentAppointments": "appointments",
    "getStudentCommunicationHistory": "communications",
    "getStudentEngagementSignals": "engagement_snapshot",
    "getStudentTimeline": "timeline",
    "getStudentOwnership": "ownership",
    "getStudentsNeedingAttention": "attention_queue",
    "getStaffWorkQueue": "work_queue",
    "getMorningBriefing": "morning_brew",
    "getWorkItemDetail": "work_item",
    "getInquiries": "inquiries",
    "getInquiryThread": "inquiry_thread",
    "getPlaybooks": "staff_guidance",
    "getActionRules": "action_rules",
    "getMailboxMessages": "authorized_mailboxes",
    "getStaffProfile": "staff_directory",
    "searchStaff": "staff_directory",
    "getStaffTeam": "staff_team",
    "getStaffCaseload": "staff_caseload",
    "getStaffAppointments": "staff_calendar",
    "getStaffAvailability": "staff_calendar",
    "compareStaff": "staff_directory",
    "summarizeWorkQueue": "work_queue",
    "searchWorkQueue": "work_queue",
    "summarizeInquiries": "inquiries",
    "searchInquiries": "inquiries",
    "getComponentSummary": "staff_team",
    "searchInstitutionalKnowledge": "institution_knowledge",
}

# Tools whose primary argument is a resolved staff member. The pipeline binds
# ``staffId`` from the turn's resolved staff entity — or from the signed-in
# member when the turn is about "me" — never from a model plan.
STAFF_SCOPED_TOOLS = frozenset(
    {
        "getStaffProfile",
        "getStaffTeam",
        "getStaffCaseload",
        "getStaffAppointments",
        "getStaffAvailability",
    }
)

# Tools whose primary argument is the resolved student referent. The pipeline
# binds ``studentId`` for these itself; a model plan never supplies it.
STUDENT_SCOPED_TOOLS = frozenset(
    {
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
    }
)

_CHANNELS = ("email", "sms", "voice", "portal")
_QUEUE_STATUS_FILTERS = (
    "open",
    "closed",
    "any",
    "todo",
    "in_progress",
    "follow_up_required",
    "blocked",
    "done",
    "cancelled",
)
_PRIORITIES = ("urgent", "high", "medium", "low")
_QUEUE_GROUP_BY = (
    "assignee",
    "component",
    "status",
    "priority",
    "due_window",
    "action_type",
    "work_type",
    "student",
)
_WORK_TYPES = ("enrollment", "document_review", "communication")
_INQUIRY_STATUS_FILTERS = (
    "awaiting_first_reply",
    "open",
    "any",
    "new",
    "waiting_on_student",
    "resolved",
)
_INQUIRY_GROUP_BY = ("status", "assignee", "topic", "priority")
_INQUIRY_SORTS = ("oldest", "newest")
_APPOINTMENT_WINDOWS = (
    "today",
    "tomorrow",
    "week",
    "two_weeks",
    "past_week",
    "awaiting_outcome",
)
_ADVISING_FILTERS = ("any", "not_completed", "no_booking", "completed", "missed", "scheduled")
_DEPOSIT_FILTERS = ("paid", "unpaid")
_ASSIGNMENT_ROLES = (
    "primary_advisor",
    "admissions_counselor",
    "financial_aid_counselor",
    "international_adviser",
    "housing_coordinator",
)

_QUEUE_FILTER_ARGUMENTS: Mapping[str, JsonDict] = {
    "ownership": {"kind": "enum", "values": ("mine", "unassigned", "all"), "optional": True},
    "staffId": {"kind": "uuid", "optional": True},
    "component": {"kind": "text", "max_length": 120, "optional": True},
    "status": {"kind": "enum", "values": _QUEUE_STATUS_FILTERS, "optional": True},
    "priority": {"kind": "enum", "values": _PRIORITIES, "optional": True},
    "dueWindow": {
        "kind": "enum",
        "values": ("overdue", "today", "seven_days", "no_due", "all"),
        "optional": True,
    },
    "topic": {"kind": "text", "max_length": 80, "optional": True},
    "stale": {"kind": "bool", "optional": True},
    "escalated": {"kind": "bool", "optional": True},
    "actionType": {"kind": "text", "max_length": 48, "optional": True},
    "workType": {"kind": "enum", "values": _WORK_TYPES, "optional": True},
    "inProgressOverDays": {"kind": "int", "minimum": 1, "maximum": 365, "optional": True},
    "studentId": {"kind": "uuid", "optional": True},
}
_INQUIRY_FILTER_ARGUMENTS: Mapping[str, JsonDict] = {
    "status": {"kind": "enum", "values": _INQUIRY_STATUS_FILTERS, "optional": True},
    "ownership": {"kind": "enum", "values": ("mine", "unassigned", "all"), "optional": True},
    "staffId": {"kind": "uuid", "optional": True},
    "priority": {"kind": "enum", "values": _PRIORITIES, "optional": True},
    "topic": {"kind": "text", "max_length": 80, "optional": True},
    "olderThanHours": {"kind": "int", "minimum": 1, "maximum": 8760, "optional": True},
    "studentId": {"kind": "uuid", "optional": True},
}
_QUEUE_OWNERSHIP = ("mine", "unassigned", "all")
_QUEUE_STATUSES = ("todo", "in_progress", "follow_up_required", "blocked", "done", "cancelled")
_QUEUE_DUE_WINDOWS = ("overdue", "today", "seven_days", "no_due", "all")
# The board's orderings plus the assistant's synonyms (canonical = priority,
# oldest = created, stalest = stale); tools.py maps them onto the query.
_QUEUE_SORTS = ("priority", "canonical", "due", "updated", "created", "oldest", "stale", "stalest")
_FLAGS = ("true", "false")
_INQUIRY_STATUSES = ("new", "open", "waiting_on_student", "resolved", "archived")

# Argument schema per tool. Every argument the executor will pass must be
# declared here; validation drops or rejects anything else.
#   kind: "uuid" | "text" | "int" | "enum" | "key"
STAFF_TOOL_ARGUMENTS: Mapping[str, Mapping[str, JsonDict]] = {
    "searchStudents": {
        "query": {"kind": "text", "max_length": 120, "optional": True},
        "externalRef": {"kind": "text", "max_length": 64, "optional": True},
        "program": {"kind": "text", "max_length": 120, "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 25, "optional": True},
    },
    "findStudents": {
        "filter": {"kind": "cohort_filter"},
        "limit": {"kind": "int", "minimum": 1, "maximum": 50, "optional": True},
    },
    "summarizeStudents": {
        "filter": {"kind": "cohort_filter", "optional": True},
        "groupBy": {"kind": "enum", "values": COHORT_GROUP_BY},
        "limit": {"kind": "int", "minimum": 1, "maximum": 50, "optional": True},
    },
    "getStudentStaffSummary": {"studentId": {"kind": "uuid"}},
    "getStudentRequirements": {"studentId": {"kind": "uuid"}},
    "getStudentDocuments": {"studentId": {"kind": "uuid"}},
    "getStudentBlockers": {"studentId": {"kind": "uuid"}},
    "getStudentDeadlines": {"studentId": {"kind": "uuid"}},
    "getStudentFinancialState": {"studentId": {"kind": "uuid"}},
    "getStudentHousingState": {"studentId": {"kind": "uuid"}},
    "getStudentAppointments": {"studentId": {"kind": "uuid"}},
    "getStudentCommunicationHistory": {
        "studentId": {"kind": "uuid"},
        "channel": {"kind": "enum", "values": _CHANNELS, "optional": True},
    },
    "getStudentEngagementSignals": {"studentId": {"kind": "uuid"}},
    "getStudentTimeline": {
        "studentId": {"kind": "uuid"},
        "limit": {"kind": "int", "minimum": 1, "maximum": 80, "optional": True},
    },
    "getStudentOwnership": {"studentId": {"kind": "uuid"}},
    "getStudentsNeedingAttention": {
        "limit": {"kind": "int", "minimum": 1, "maximum": 50, "optional": True},
    },
    "getStaffWorkQueue": {
        "ownership": {"kind": "enum", "values": _QUEUE_OWNERSHIP, "optional": True},
        "assigneeName": {"kind": "text", "max_length": 120, "optional": True},
        "component": {"kind": "text", "max_length": 120, "optional": True},
        "status": {"kind": "enum", "values": _QUEUE_STATUSES, "optional": True},
        "dueWindow": {"kind": "enum", "values": _QUEUE_DUE_WINDOWS, "optional": True},
        "topic": {"kind": "text", "max_length": 80, "optional": True},
        "stale": {"kind": "enum", "values": _FLAGS, "optional": True},
        "ownerRisk": {"kind": "enum", "values": _FLAGS, "optional": True},
        "sort": {"kind": "enum", "values": _QUEUE_SORTS, "optional": True},
        "key": {"kind": "text", "max_length": 40, "optional": True},
    },
    "getMorningBriefing": {},
    "getWorkItemDetail": {"workItemId": {"kind": "uuid"}},
    "getInquiries": {
        "status": {"kind": "enum", "values": _INQUIRY_STATUSES, "optional": True},
    },
    "getInquiryThread": {"inquiryId": {"kind": "uuid"}},
    "getPlaybooks": {},
    "getActionRules": {},
    "getMailboxMessages": {
        "query": {"kind": "text", "max_length": 500, "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 25, "optional": True},
    },
    "getStaffProfile": {"staffId": {"kind": "uuid"}},
    "searchStaff": {
        "query": {"kind": "text", "max_length": 120, "optional": True},
        "component": {"kind": "text", "max_length": 120, "optional": True},
        "role": {"kind": "text", "max_length": 80, "optional": True},
        "absentNow": {"kind": "bool", "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 25, "optional": True},
    },
    "getStaffTeam": {"staffId": {"kind": "uuid"}},
    "getStaffCaseload": {
        "staffId": {"kind": "uuid"},
        "role": {"kind": "enum", "values": _ASSIGNMENT_ROLES, "optional": True},
        "advisingStatus": {"kind": "enum", "values": _ADVISING_FILTERS, "optional": True},
        "depositState": {"kind": "enum", "values": _DEPOSIT_FILTERS, "optional": True},
        "withOpenWork": {"kind": "bool", "optional": True},
        "withOverdueWork": {"kind": "bool", "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 25, "optional": True},
    },
    "getStaffAppointments": {
        "staffId": {"kind": "uuid"},
        "window": {"kind": "enum", "values": _APPOINTMENT_WINDOWS, "optional": True},
    },
    "getStaffAvailability": {"staffId": {"kind": "uuid"}},
    "compareStaff": {"staffIds": {"kind": "uuid_list", "maximum": 4}},
    "summarizeWorkQueue": {
        **_QUEUE_FILTER_ARGUMENTS,
        "groupBy": {"kind": "enum", "values": _QUEUE_GROUP_BY, "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 50, "optional": True},
    },
    "searchWorkQueue": {
        **_QUEUE_FILTER_ARGUMENTS,
        "sort": {"kind": "enum", "values": _QUEUE_SORTS, "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 25, "optional": True},
    },
    "summarizeInquiries": {
        **_INQUIRY_FILTER_ARGUMENTS,
        "groupBy": {"kind": "enum", "values": _INQUIRY_GROUP_BY, "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 50, "optional": True},
    },
    "searchInquiries": {
        **_INQUIRY_FILTER_ARGUMENTS,
        "sort": {"kind": "enum", "values": _INQUIRY_SORTS, "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 25, "optional": True},
    },
    "getComponentSummary": {"component": {"kind": "text", "max_length": 120}},
    "searchInstitutionalKnowledge": {
        "query": {"kind": "text", "max_length": 240},
        "audience": {"kind": "enum", "values": ("student", "internal", "all"), "optional": True},
        "kind": {
            "kind": "enum",
            "values": (
                "policy",
                "procedure",
                "handbook",
                "program",
                "service",
                "directory",
                "internal",
            ),
            "optional": True,
        },
        "office": {"kind": "text", "max_length": 16, "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 8, "optional": True},
        "studentId": {"kind": "uuid", "optional": True},
    },
}


class ToolArgumentError(ValueError):
    """A tool argument failed validation; carries a stable failure code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def validate_tool_arguments(tool: str, arguments: Mapping[str, Any]) -> JsonDict:
    """Validate and normalize one tool call's arguments against the schema.

    Unknown argument names are rejected (never silently forwarded), values are
    type- and bound-checked, and required arguments must be present. Returns
    the cleaned argument dict.
    """

    schema = STAFF_TOOL_ARGUMENTS.get(tool)
    if schema is None:
        raise ToolArgumentError("unknown_tool", f"Unknown tool {tool!r}")
    cleaned: JsonDict = {}
    for name, value in arguments.items():
        spec = schema.get(str(name))
        if spec is None:
            raise ToolArgumentError("unknown_argument", f"{tool} does not accept argument {name!r}")
        if value is None:
            continue
        cleaned[str(name)] = _validate_value(tool, str(name), value, spec)
    for name, spec in schema.items():
        if not spec.get("optional") and name not in cleaned:
            raise ToolArgumentError("missing_argument", f"{tool} requires argument {name!r}")
    return cleaned


def _validate_value(tool: str, name: str, value: Any, spec: Mapping[str, Any]) -> Any:
    kind = spec["kind"]
    if kind == "uuid":
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError) as error:
            raise ToolArgumentError("invalid_uuid", f"{tool}.{name} must be a UUID") from error
    if kind == "text":
        if not isinstance(value, str):
            raise ToolArgumentError("invalid_text", f"{tool}.{name} must be a string")
        normalized = re.sub(r"\s+", " ", value).strip()[: int(spec.get("max_length", 200))]
        if not normalized and not spec.get("optional"):
            raise ToolArgumentError("invalid_text", f"{tool}.{name} must not be empty")
        return normalized
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            try:
                value = int(str(value))
            except (TypeError, ValueError) as error:
                raise ToolArgumentError(
                    "invalid_integer", f"{tool}.{name} must be an integer"
                ) from error
        low = int(spec.get("minimum", 1))
        high = int(spec.get("maximum", 100))
        return max(low, min(int(value), high))
    if kind == "enum":
        candidate = str(value)
        values = tuple(spec.get("values", ()))
        if candidate not in values:
            raise ToolArgumentError(
                "invalid_enum",
                f"{tool}.{name} must be one of {', '.join(values)}",
            )
        return candidate
    if kind == "bool":
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
        raise ToolArgumentError("invalid_boolean", f"{tool}.{name} must be true or false")
    if kind == "uuid_list":
        if isinstance(value, str) or not isinstance(value, list | tuple):
            raise ToolArgumentError("invalid_uuid_list", f"{tool}.{name} must be a list of UUIDs")
        cleaned_ids: list[str] = []
        for entry in value[: int(spec.get("maximum", 4))]:
            try:
                cleaned_ids.append(str(UUID(str(entry))))
            except (TypeError, ValueError) as error:
                raise ToolArgumentError(
                    "invalid_uuid", f"{tool}.{name} must contain UUIDs"
                ) from error
        if not cleaned_ids:
            raise ToolArgumentError("invalid_uuid_list", f"{tool}.{name} must not be empty")
        return cleaned_ids
    if kind == "cohort_filter":
        # The cohort vocabulary is owned by the domain module, so the catalog
        # delegates rather than keeping a second copy of the allowed values.
        if not isinstance(value, Mapping):
            raise ToolArgumentError(
                "invalid_cohort_filter", f"{tool}.{name} must be an object of filters"
            )
        try:
            return build_cohort_filter(value)
        except CohortFilterError as error:
            raise ToolArgumentError("invalid_cohort_filter", error.detail) from error
    raise ToolArgumentError("invalid_schema", f"{tool}.{name} has an unknown kind")


STAFF_TOOL_NAMES = (
    *STAFF_TOOL_NAMES,
    *UNIVERSITY_TOOLS,
    "getUniversityOperations",
    "getUniversityCohort",
    "searchUniversityPolicies",
)
STAFF_TOOL_DESCRIPTIONS = {
    **STAFF_TOOL_DESCRIPTIONS,
    **{name: value[1] for name, value in UNIVERSITY_TOOLS.items()},
    "getUniversityOperations": (
        "Read my university office, capacities, weekly availability, "
        "meetings, leave/coverage, appointments and accountable case "
        "workload."
    ),
    "getUniversityCohort": (
        "Return the exact deduplicated cohort of enrolled Fall students "
        "with active Fall financial holds AND pending Fall payments, plus "
        "denominator. This is one saved cohort, not an arbitrary student "
        "search."
    ),
    "searchUniversityPolicies": (
        "Search versioned institutional policy/procedure passages by "
        "query, optional at and knownAt ISO timestamps. Filters audience, "
        "effectivity, publication; returns applicability, section "
        "citations and source hashes. Read alongside getUniversity records"
        " for individual exceptions."
    ),
}
STUDENT_SCOPED_TOOLS = STUDENT_SCOPED_TOOLS | frozenset(UNIVERSITY_TOOLS)
STAFF_TOOL_INFORMATION_CLASS = {
    **STAFF_TOOL_INFORMATION_CLASS,
    **{name: "student_state" for name in UNIVERSITY_TOOLS},
    "getUniversityOperations": "operational_state",
    "getUniversityCohort": "operational_state",
    "searchUniversityPolicies": "institution_knowledge",
}
STAFF_TOOL_ARGUMENTS = {
    **STAFF_TOOL_ARGUMENTS,
    **{name: {"studentId": {"kind": "uuid"}} for name in UNIVERSITY_TOOLS},
    "getUniversityOperations": {},
    "getUniversityCohort": {},
    "searchUniversityPolicies": {
        "query": {"kind": "text", "max_length": 500},
        "studentId": {"kind": "uuid", "optional": True},
        "at": {"kind": "text", "max_length": 35, "optional": True},
        "knownAt": {"kind": "text", "max_length": 35, "optional": True},
    },
}
STAFF_TOOL_ARGUMENTS = {
    **STAFF_TOOL_ARGUMENTS,
    "getUniversityHistory": {
        **STAFF_TOOL_ARGUMENTS["getUniversityHistory"],
        "entityType": {"kind": "text", "max_length": 80, "optional": True},
        "entityId": {"kind": "text", "max_length": 100, "optional": True},
        "knownAt": {"kind": "text", "max_length": 35, "optional": True},
        "effectiveAt": {"kind": "text", "max_length": 35, "optional": True},
    },
}

STAFF_RECEIPT_SOURCES = {
    **STAFF_RECEIPT_SOURCES,
    **{name: "university" for name in UNIVERSITY_TOOLS},
    "getUniversityOperations": "university",
    "getUniversityCohort": "university",
    "searchUniversityPolicies": "institution_knowledge",
}

STAFF_TOOL_NAMES = (*STAFF_TOOL_NAMES, "getUniversityCasework")
STAFF_TOOL_DESCRIPTIONS = {
    **STAFF_TOOL_DESCRIPTIONS,
    "getUniversityCasework": (
        "Read the student's cross-office case DAG, owner, steps, dependencies, "
        "completion evidence and delivered/bounced communications. Use for shortest "
        "safe resolution path, handoffs or whether a decision reached the student."
    ),
}
STAFF_TOOL_ARGUMENTS = {
    **STAFF_TOOL_ARGUMENTS,
    "getUniversityCasework": {"studentId": {"kind": "uuid"}},
}
STUDENT_SCOPED_TOOLS = STUDENT_SCOPED_TOOLS | {"getUniversityCasework"}
STAFF_TOOL_INFORMATION_CLASS = {
    **STAFF_TOOL_INFORMATION_CLASS,
    "getUniversityCasework": "student_state",
}
STAFF_RECEIPT_SOURCES = {**STAFF_RECEIPT_SOURCES, "getUniversityCasework": "university"}


STAFF_TOOL_NAMES = (*STAFF_TOOL_NAMES, "getUniversityWorkBoard")
STAFF_TOOL_DESCRIPTIONS = {
    **STAFF_TOOL_DESCRIPTIONS,
    "getUniversityWorkBoard": (
        "Read the canonical Task Board projection: a bounded page of work cards, "
        "total queue counts, owners, priorities, due dates and linked document, case and "
        "payment evidence. Card operational status never proves settlement or verification. "
        "Filter by project, status, priority, owner, due date or task key. Project summaries "
        "always count the full institutional queue; page.total counts all filter matches. "
        "Advance offset to read additional pages; never claim the first page is exhaustive."
    ),
}
STAFF_TOOL_ARGUMENTS = {
    **STAFF_TOOL_ARGUMENTS,
    "getUniversityWorkBoard": {
        "project": {
            "kind": "enum",
            "values": (
                "fa-docs",
                "fa-outreach",
                "fa-payments",
                "en-docs",
                "en-outreach",
                "en-requests",
                "cl-housing",
            ),
            "optional": True,
        },
        "search": {"kind": "text", "max_length": 120, "optional": True},
        "priority": {"kind": "enum", "values": _PRIORITIES, "optional": True},
        "status": {
            "kind": "enum",
            "values": (
                "all",
                "open",
                "todo",
                "in_progress",
                "blocked",
                "follow_up_required",
                "done",
                "cancelled",
            ),
            "optional": True,
        },
        "assignee": {"kind": "enum", "values": ("me", "unassigned"), "optional": True},
        "due": {
            "kind": "enum",
            "values": ("overdue", "today", "seven_days", "all"),
            "optional": True,
        },
        "offset": {"kind": "int", "minimum": 0, "maximum": 100000, "optional": True},
    },
}
STAFF_TOOL_INFORMATION_CLASS = {
    **STAFF_TOOL_INFORMATION_CLASS,
    "getUniversityWorkBoard": "operational_state",
}
STAFF_RECEIPT_SOURCES = {**STAFF_RECEIPT_SOURCES, "getUniversityWorkBoard": "university"}


# The signed-in person's displayed board is distinct from the institution-wide queue.
STAFF_TOOL_NAMES = (*STAFF_TOOL_NAMES, "getTaskBoard", "getTaskBoardTask")
STAFF_TOOL_DESCRIPTIONS = {
    **STAFF_TOOL_DESCRIPTIONS,
    "getTaskBoard": (
        "Read the signed-in staff member's actual Task Board, including curated membership "
        "when configured. Full matching counts plus a bounded page of canonical tasks and "
        "student identities; priority, due dates and status are real records. Filter by "
        "project, student name or task title/key (search), priority, status, or due window. "
        "Omit studentId for the whole board; it is already scoped to the signed-in staff. "
        "studentId is only for a verified student handle, never me or a staff handle. "
        "this_week means Monday through Sunday in the board timezone, not the next 7 days. "
        "Use counts for totals; page through results before claiming an exhaustive list. "
        "Read getTaskBoardTask for documents, conversations, activity and related tasks."
    ),
    "getTaskBoardTask": (
        "Read one task on the signed-in staff member's board by its canonical task key. "
        "Includes actual student identity, priority, deadline, next step, linked original "
        "document metadata and decisions, requirements, messages, staff notes, activity, "
        "canonical work context and other tasks for this student. Overview is bounded; "
        "use section=documents/activity/messages and offset/limit for more detail. "
        "Parser/preview values "
        "are not evidence. The result binds student:<task key> for further student reads. "
        "First search getTaskBoard when the task is ambiguous; never choose arbitrarily."
    ),
}
STAFF_TOOL_ARGUMENTS = {
    **STAFF_TOOL_ARGUMENTS,
    "getTaskBoard": {
        "studentId": {"kind": "uuid", "optional": True},
        **{
            k: v
            for k, v in STAFF_TOOL_ARGUMENTS["getUniversityWorkBoard"].items()
            if k != "assignee"
        },
        "due": {
            "kind": "enum",
            "values": ("all", "overdue", "today", "this_week", "seven_days"),
            "optional": True,
        },
        "limit": {"kind": "int", "minimum": 1, "maximum": 30, "optional": True},
    },
    "getTaskBoardTask": {
        "key": {"kind": "text", "max_length": 40},
        "section": {
            "kind": "enum",
            "values": ("overview", "documents", "activity", "messages"),
            "optional": True,
        },
        "offset": {"kind": "int", "minimum": 0, "maximum": 100000, "optional": True},
        "limit": {"kind": "int", "minimum": 1, "maximum": 20, "optional": True},
    },
}
STAFF_TOOL_INFORMATION_CLASS = {
    **STAFF_TOOL_INFORMATION_CLASS,
    "getTaskBoard": "operational_state",
    "getTaskBoardTask": "operational_state",
}
STAFF_RECEIPT_SOURCES = {
    **STAFF_RECEIPT_SOURCES,
    "getTaskBoard": "task_board",
    "getTaskBoardTask": "task_board_task",
}
