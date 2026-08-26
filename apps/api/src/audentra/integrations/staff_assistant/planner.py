"""Staff tool selection rules and model-plan validation.

The deterministic table is authoritative; a model plan may only pick tools
the classified intents already allow (plus the universal student-context
reads once a referent is resolved), and may only carry *non-identity*
arguments. ``studentId``, ``workItemId``, and ``inquiryId`` are always bound
by the pipeline from validated referents — a model-proposed identity argument
is stripped, never trusted.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from audentra.integrations.staff_assistant.catalog import (
    STAFF_SCOPED_TOOLS,
    STAFF_TOOL_NAMES,
    STUDENT_SCOPED_TOOLS,
)
from audentra.integrations.staff_assistant.classify import (
    STAFF_REQUEST_TYPES,
    StaffClassification,
)
from audentra.integrations.staff_assistant.tools import PlannedToolCall

JsonDict = dict[str, Any]

# Context reads any student-scoped intent may draw on once a referent is
# resolved: explaining a document question nearly always needs the blocker
# and deadline picture too. All read-only and referent-bound.
STAFF_UNIVERSAL_CONTEXT_TOOLS = (
    "getStudentStaffSummary",
    "getStudentBlockers",
    "getStudentDeadlines",
)

_SELECTION_RULES: Mapping[str, tuple[str, ...]] = {
    "greeting": (),
    "capability_overview": (),
    # The action refusal is deterministic and reads nothing: a refusal that
    # reads records invites the reply to become the action's dry run.
    "action_request": (),
    "unsupported_metric": (),
    "draft_email": (
        "getStudentStaffSummary",
        "getStudentRequirements",
        "getStudentBlockers",
        "getStudentDeadlines",
        "getStudentCommunicationHistory",
        "getPlaybooks",
    ),
    "draft_sms": (
        "getStudentStaffSummary",
        "getStudentRequirements",
        "getStudentBlockers",
        "getStudentDeadlines",
        "getStudentCommunicationHistory",
    ),
    "draft_call_points": (
        "getStudentStaffSummary",
        "getStudentRequirements",
        "getStudentBlockers",
        "getStudentDeadlines",
        "getStudentCommunicationHistory",
        "getPlaybooks",
    ),
    "student_overview": (
        "getStudentStaffSummary",
        "getStudentRequirements",
        "getStudentBlockers",
        "getStudentDeadlines",
        "getStudentOwnership",
    ),
    # Cohort work is not student-scoped: these must never wait on a referent.
    "cohort_search": ("findStudents",),
    "cohort_aggregate": ("summarizeStudents",),
    "student_missing_items": ("getStudentRequirements", "getStudentDocuments"),
    "student_blockers": (
        "getStudentBlockers",
        "getStudentRequirements",
        "getStudentDeadlines",
    ),
    "student_documents": ("getStudentDocuments", "getStudentRequirements"),
    "student_deadlines": ("getStudentDeadlines",),
    "student_financials": ("getStudentFinancialState",),
    "student_housing": ("getStudentHousingState", "getStudentBlockers"),
    "student_appointments": ("getStudentAppointments",),
    "student_communications": ("getStudentCommunicationHistory",),
    "student_engagement": (
        "getStudentEngagementSignals",
        "getStudentCommunicationHistory",
    ),
    "student_timeline": ("getStudentTimeline",),
    "student_ownership": ("getStudentOwnership",),
    # Membership is decided by the student's actual open work items (the
    # summary carries them); blockers/deadlines give the "why" context.
    "student_action_center": (
        "getStudentStaffSummary",
        "getStudentBlockers",
        "getStudentDeadlines",
    ),
    "attention_ranking": ("getStudentsNeedingAttention",),
    "recommendation": (
        "getStudentStaffSummary",
        "getStudentBlockers",
        "getStudentDeadlines",
        "getStudentCommunicationHistory",
        "getPlaybooks",
    ),
    "work_queue": ("getStaffWorkQueue",),
    # The briefing is the portal's own start-of-day read; the queue rides
    # along so "what should I start with" has concrete cases to name.
    "daily_briefing": ("getMorningBriefing", "getStaffWorkQueue"),
    "work_item_detail": ("getWorkItemDetail",),
    "inquiries": ("getInquiries",),
    "playbook_lookup": ("getPlaybooks",),
    "action_rules": ("getActionRules",),
    "mailbox_read": ("getMailboxMessages",),
    # Staff-aware intents. The profile rides along with every facet so the
    # answer can say who the person is (title, status) before the numbers.
    "staff_profile": ("getStaffProfile",),
    "staff_workload": ("getStaffProfile", "searchWorkQueue"),
    "staff_availability": ("getStaffAvailability", "getStaffProfile"),
    "staff_caseload": ("getStaffProfile", "getStaffCaseload"),
    "staff_appointments": ("getStaffProfile", "getStaffAppointments"),
    "staff_comparison": ("compareStaff",),
    "my_work": ("getStaffProfile", "searchWorkQueue"),
    "my_profile": ("getStaffProfile",),
    "team_overview": ("getStaffTeam", "getStaffProfile"),
    "department_operations": ("getComponentSummary",),
    "queue_aggregate": ("summarizeWorkQueue",),
    "inquiry_aggregate": ("summarizeInquiries", "searchInquiries"),
    "staff_directory": ("searchStaff",),
    "not_found": (),
    "general_question": (),
    "unsupported_or_out_of_scope": (),
}

# Facet-specific extras for "my work" and a colleague's workload.
_FACET_EXTRA_TOOLS: Mapping[str, tuple[str, ...]] = {
    "appointments": ("getStaffAppointments",),
    "availability": ("getStaffAvailability",),
    "caseload": ("getStaffCaseload",),
    "priorities": ("searchWorkQueue",),
    "workload": ("searchWorkQueue",),
}

# Arguments a model plan is permitted to carry per tool. Identity arguments
# are conspicuously absent — the pipeline binds those.
_MODEL_SAFE_ARGUMENTS: Mapping[str, tuple[str, ...]] = {
    "searchStudents": ("query", "externalRef", "program", "limit"),
    # Cohort selection is not identity: the filter is validated against the
    # domain vocabulary and the tenant stays server-bound, so a model may
    # propose one.
    "findStudents": ("filter", "limit"),
    "summarizeStudents": ("filter", "groupBy", "limit"),
    "getStudentCommunicationHistory": ("channel",),
    "getStudentTimeline": ("limit",),
    "getStudentsNeedingAttention": ("limit",),
    "getStaffWorkQueue": ("ownership", "component", "status", "dueWindow", "topic"),
    "getInquiries": ("status",),
    "getMailboxMessages": ("query", "limit"),
    "searchStaff": ("query", "component", "role", "absentNow", "limit"),
    "getStaffCaseload": (
        "role",
        "advisingStatus",
        "depositState",
        "withOpenWork",
        "withOverdueWork",
        "limit",
    ),
    "getStaffAppointments": ("window",),
    "summarizeWorkQueue": (
        "ownership",
        "component",
        "status",
        "priority",
        "dueWindow",
        "topic",
        "stale",
        "escalated",
        "actionType",
        "workType",
        "inProgressOverDays",
        "groupBy",
        "limit",
    ),
    "searchWorkQueue": (
        "ownership",
        "component",
        "status",
        "priority",
        "dueWindow",
        "topic",
        "stale",
        "escalated",
        "actionType",
        "workType",
        "inProgressOverDays",
        "sort",
        "limit",
    ),
    "summarizeInquiries": (
        "status",
        "ownership",
        "priority",
        "topic",
        "olderThanHours",
        "groupBy",
        "limit",
    ),
    "searchInquiries": (
        "status",
        "ownership",
        "priority",
        "topic",
        "olderThanHours",
        "sort",
        "limit",
    ),
    "getComponentSummary": ("component",),
}

# The verifying read for blocker codes the first round can surface. One
# bounded deterministic follow-up, never a second model-planned expansion.
_BLOCKER_DEPENDENCY_TOOLS: Mapping[str, tuple[str, ...]] = {
    "enrollment_deposit_posted": ("getStudentFinancialState",),
    "enrollment_deposit": ("getStudentFinancialState",),
    "financial_aid_verification": ("getStudentFinancialState",),
    "housing_preference": ("getStudentHousingState",),
    "official_transcript": ("getStudentDocuments",),
    "identity_document": ("getStudentDocuments",),
    "immunization_record": ("getStudentDocuments",),
}

MAX_DEPENDENCY_TOOLS = 2


def select_staff_tools(
    classification: StaffClassification,
    *,
    student_resolved: bool,
) -> list[str]:
    """The deterministic plan for the classified intents."""

    selected: list[str] = []
    for request_type in (
        classification.request_type,
        *classification.additional_request_types,
    ):
        tools = list(_SELECTION_RULES.get(request_type, ()))
        if request_type in {"my_work", "staff_workload"}:
            tools.extend(_FACET_EXTRA_TOOLS.get(str(classification.reference or ""), ()))
        for tool in tools:
            if tool in STUDENT_SCOPED_TOOLS and not student_resolved:
                continue
            if tool not in selected:
                selected.append(tool)
    if classification.request_type == "general_question":
        # A general question with a resolved student reads that student's
        # context; without one it reads the operational picture.
        fallback = (
            STAFF_UNIVERSAL_CONTEXT_TOOLS
            if student_resolved
            else ("getStaffWorkQueue", "getStudentsNeedingAttention")
        )
        for tool in fallback:
            if tool not in selected:
                selected.append(tool)
    return selected


def resolve_dependency_tools(
    executed_tools: Sequence[str],
    open_blocker_codes: Sequence[str],
) -> tuple[list[str], list[dict[str, str]]]:
    """Bounded second round: verify blockers whose domain wasn't read."""

    already = set(executed_tools)
    tools: list[str] = []
    reasons: list[dict[str, str]] = []
    for code in open_blocker_codes:
        for tool in _BLOCKER_DEPENDENCY_TOOLS.get(str(code), ()):
            if tool in already or tool in tools:
                continue
            if len(tools) >= MAX_DEPENDENCY_TOOLS:
                return tools, reasons
            tools.append(tool)
            reasons.append({"blocker": str(code), "tool": tool})
    return tools, reasons


def validate_staff_model_plan(
    value: Any,
    *,
    student_resolved: bool,
    staff_resolved: bool = False,
) -> tuple[StaffClassification, list[PlannedToolCall]] | None:
    """Validate an untrusted model plan; strip identity args, filter reads."""

    if not isinstance(value, Mapping):
        return None
    request_type = value.get("requestType")
    confidence = value.get("confidence")
    tool_calls = value.get("toolCalls")
    if (
        request_type not in STAFF_REQUEST_TYPES
        or not isinstance(confidence, int | float)
        or confidence < 0.55
        or not isinstance(tool_calls, Sequence)
        or isinstance(tool_calls, str | bytes)
        or len(tool_calls) > 8
    ):
        return None
    additional_raw = value.get("additionalRequestTypes") or []
    if not isinstance(additional_raw, Sequence) or isinstance(additional_raw, str | bytes):
        return None
    additional = [
        item
        for item in dict.fromkeys(additional_raw)
        if item in STAFF_REQUEST_TYPES
        and item not in {request_type, "unsupported_or_out_of_scope", "action_request"}
    ]
    if len(additional) > 2 or len(additional) != len(list(additional_raw)):
        return None

    intents = [str(request_type), *[str(item) for item in additional]]
    allowed = {tool for intent in intents for tool in _SELECTION_RULES.get(intent, ())}
    for intent in intents:
        if intent in {"my_work", "staff_workload"}:
            allowed.update(tool for tools in _FACET_EXTRA_TOOLS.values() for tool in tools)
    if request_type not in {"unsupported_or_out_of_scope", "action_request"}:
        if student_resolved:
            allowed.update(STAFF_UNIVERSAL_CONTEXT_TOOLS)
        allowed.add("searchStudents")
        allowed.add("searchStaff")
        # Aggregates are safe to combine with any operational intent.
        allowed.update({"summarizeWorkQueue", "summarizeInquiries"})

    calls: list[PlannedToolCall] = []
    seen: set[str] = set()
    for raw in tool_calls:
        if not isinstance(raw, Mapping):
            return None
        tool = raw.get("tool")
        if tool not in STAFF_TOOL_NAMES:
            return None
        if tool not in allowed or tool in seen:
            continue
        if tool in STUDENT_SCOPED_TOOLS and not student_resolved:
            continue
        if tool in STAFF_SCOPED_TOOLS and not staff_resolved:
            continue
        seen.add(str(tool))
        arguments_raw = raw.get("arguments")
        arguments: JsonDict = {}
        if isinstance(arguments_raw, Mapping):
            safe_names = _MODEL_SAFE_ARGUMENTS.get(str(tool), ())
            arguments = {
                str(name): arguments_raw[name]
                for name in safe_names
                if arguments_raw.get(name) is not None
            }
        calls.append(PlannedToolCall(tool=str(tool), arguments=arguments))
    if request_type in {"unsupported_or_out_of_scope", "action_request"} and calls:
        return None
    reference = value.get("facet") if isinstance(value.get("facet"), str) else None
    classification = StaffClassification(
        request_type=str(request_type),
        confidence=float(confidence),
        source="model_plan",
        reference=reference,
        additional_request_types=tuple(str(item) for item in additional),
    )
    return classification, calls
