"""Tool selection rules and model-plan validation.

The rules table is the port of `selectToolReadsNode` from
student-assistant-core, restricted to the tools this platform hosts. A model
plan is validated the same way as `validateModelToolPlan`: out-of-allowlist
reads are dropped rather than used as grounds to discard the whole plan, and
reads an intent cannot be answered without are added rather than demanded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from audentra.domain.student_state import (
    REQUIREMENT_GATE_CODES as DOMAIN_REQUIREMENT_GATE_CODES,
)
from audentra.integrations.assistant.classify import REQUEST_TYPES, Classification

TOOL_NAMES = (
    "getStudentProfile",
    "getEnrollmentState",
    "getOnboardingResponses",
    "getOnboardingChecklist",
    "getDocumentStatuses",
    "getEnrollmentHolds",
    "getStudentDeadlines",
    "getSupportOptions",
    "getStudentSupportRequests",
    "getFinancialAidStatus",
    "getFinancialAidSummary",
    "getAidDisbursements",
    "getFinancialAidSupportOptions",
    "getStudentHousingStatus",
    "getStudentHousingEligibility",
    "getHousingOptions",
    "getRegistrationStatus",
    "getStudentAccountSummary",
    "getAcademicStanding",
    "getStudentAppointments",
    "getStudentAdvising",
    "getAcademicPlan",
    "getCampusLife",
    "getStudentMessages",
)

# Source-of-truth classification for every tool. "student_state" must come
# from canonical per-student records; "institution_knowledge" from approved
# tenant-wide data. Conversation history is linguistic context only and is
# never a source of either. Keep this in sync when adding tools — it is the
# boundary that keeps demo constants out of production answers.
TOOL_INFORMATION_CLASS: Mapping[str, str] = {
    "getStudentProfile": "student_state",
    "getEnrollmentState": "student_state",
    "getOnboardingResponses": "student_state",
    "getOnboardingChecklist": "student_state",
    "getDocumentStatuses": "student_state",
    "getEnrollmentHolds": "student_state",
    "getStudentDeadlines": "student_state",
    "getSupportOptions": "institution_knowledge",
    "getStudentSupportRequests": "student_state",
    "getAcademicStanding": "student_state",
    "getFinancialAidStatus": "student_state",
    "getFinancialAidSummary": "student_state",
    "getAidDisbursements": "student_state",
    "getFinancialAidSupportOptions": "institution_knowledge",
    "getStudentHousingStatus": "student_state",
    "getStudentHousingEligibility": "student_state",
    "getHousingOptions": "institution_knowledge",
    "getRegistrationStatus": "student_state",
    "getStudentAccountSummary": "student_state",
    "getStudentAppointments": "student_state",
    "getStudentAdvising": "student_state",
    "getAcademicPlan": "student_state",
    "getCampusLife": "institution_knowledge",
    "getStudentMessages": "student_state",
}

# Reads any in-scope intent may draw on for context. Explaining *why*
# something is blocked nearly always needs the checklist, holds, and deadlines
# even when the question names a single domain — "why can't I apply for
# housing?" is answerable only if the housing intent can also see the deposit
# and any blocker. All four are read-only and scoped to the authenticated
# student, so widening them changes what Edward can reason over without
# widening what it can reach.
UNIVERSAL_CONTEXT_TOOLS = (
    "getStudentProfile",
    "getOnboardingChecklist",
    "getEnrollmentHolds",
    "getStudentDeadlines",
)

# The catalog a model planner sees. Descriptions are ported from
# student-assistant-core's studentAssistantToolCatalog, trimmed to the tools
# this platform hosts; the three portal-specific reads carry their own text.
TOOL_DESCRIPTIONS: Mapping[str, str] = {
    "getStudentProfile": (
        "Read the authenticated student's profile: names, pronouns, email and "
        "phone with their verification state, and communication preference."
    ),
    "getEnrollmentState": (
        "Read where the student stands overall: admission decision, program, "
        "starting term, campus, class year, enrollment-deposit payment state, "
        "enrollment journey progress and next action, and onboarding position."
    ),
    "getOnboardingResponses": (
        "Read what the student answered during onboarding: citizenship and "
        "residency, mailing address, emergency contacts, family permissions, "
        "insurance and accommodation interest, campus interests and goals, and "
        "the enrollment signature record."
    ),
    "getOnboardingChecklist": (
        "Read enrollment and onboarding requirements with their statuses, due "
        "dates, prerequisites, responsible office, and submitted responses."
    ),
    "getDocumentStatuses": (
        "Read the documents the student has actually uploaded, with review status, "
        "extraction outcome, and any staff decision. Uploads only: which documents "
        "are still required or missing comes from getOnboardingChecklist."
    ),
    "getStudentSupportRequests": (
        "Read the student's own support conversations and the latest reply on each."
    ),
    "getAcademicStanding": (
        "Read satisfactory academic progress — cumulative GPA against the "
        "minimum, completion rate, attempted credits — and earned credits."
    ),
    "getEnrollmentHolds": "Read official enrollment holds and derived blockers.",
    "getStudentDeadlines": "Read enrollment, requirement, and appointment deadlines.",
    "getSupportOptions": "Read approved general support contacts and articles.",
    "getFinancialAidStatus": (
        "Read bounded financial-aid requirements, verification, and award acceptance statuses."
    ),
    "getFinancialAidSummary": (
        "Read how much aid the student has: FAFSA state, whether the package is "
        "estimated or finalized, every award with its offered and accepted "
        "amount, what the aid covers against the cost of attendance, and each "
        "condition still holding the package open."
    ),
    "getAidDisbursements": (
        "Read when aid money actually moves: what has paid out, what is "
        "scheduled and when, and the exhaustive list of reasons a disbursement "
        "is being held."
    ),
    "getFinancialAidSupportOptions": "Read approved financial-aid support routes.",
    "getStudentHousingStatus": "Read the housing plan and housing requirement state.",
    "getStudentHousingEligibility": (
        "Read whether the student can act on housing right now: the housing "
        "step's own state and each open item blocking it."
    ),
    "getHousingOptions": "Read tenant-listed housing preference options.",
    "getRegistrationStatus": (
        "Read course-registration eligibility for the current term: each gate "
        "blocking registration and what clears it."
    ),
    "getStudentAccountSummary": (
        "Read the student account: balance, charges, posted and pending "
        "payments, and whether the balance blocks registration."
    ),
    "getStudentAppointments": (
        "Read scheduled advising, orientation, financial-aid, and housing "
        "appointments, plus where the student can book one."
    ),
    "getStudentAdvising": (
        "Read who advises the student: the primary adviser and every other "
        "assigned staff member (admissions counselor, financial-aid counselor, "
        "international adviser, housing coordinator) with their role, title, "
        "institutional email, office location and whether they can be booked "
        "right now — plus the next open slot and any advising gap (no primary "
        "adviser, adviser on leave or departed, no open slots). Use this for "
        "'who is my adviser', 'how do I reach my counselor', and 'when could I "
        "meet them' questions."
    ),
    "getAcademicPlan": (
        "Read the student's academic program and planned courses, including "
        "missing prerequisites and suggested exemptions."
    ),
    "getCampusLife": "Read upcoming campus events and student clubs.",
    "getStudentMessages": "Read the unread-message count and latest message subjects.",
    "getInstitutionalPolicies": (
        "Search Aster's approved institutional knowledge — published policies, "
        "procedures, handbook chapters, program guides, the academic calendar and "
        "the office directory — for the rules relevant to the question: what a "
        "deadline or requirement means, what happens when it is missed, "
        "exceptions and exemptions, amounts, dates, which office owns it and how "
        "to reach that office. Each document carries its version, effective date "
        "and an applicability verdict computed from this student's own record "
        "(residency, admit term, first-year or transfer, housing plan, program). "
        "Optional argument `query`: the question in the student's words (defaults "
        "to the message)."
    ),
}

RECEIPT_SOURCES: Mapping[str, str] = {
    "getStudentProfile": "profile",
    "getEnrollmentState": "enrollment",
    "getOnboardingResponses": "onboarding",
    "getOnboardingChecklist": "onboarding",
    "getDocumentStatuses": "documents",
    "getEnrollmentHolds": "holds",
    "getStudentDeadlines": "deadlines",
    "getSupportOptions": "policies",
    "getStudentSupportRequests": "support_requests",
    "getAcademicStanding": "academic_standing",
    "getFinancialAidStatus": "financial_aid",
    "getFinancialAidSummary": "financial_aid",
    "getAidDisbursements": "financial_aid",
    "getFinancialAidSupportOptions": "financial_aid",
    "getStudentHousingStatus": "housing",
    "getStudentHousingEligibility": "housing",
    "getHousingOptions": "housing",
    "getRegistrationStatus": "registration",
    "getStudentAccountSummary": "account",
    "getStudentAppointments": "appointments",
    "getStudentAdvising": "appointments",
    "getAcademicPlan": "academics",
    "getCampusLife": "campus_life",
    "getStudentMessages": "messages",
    "getInstitutionalPolicies": "institution_knowledge",
}

_SELECTION_RULES: Mapping[str, tuple[str, ...]] = {
    # A greeting reads the student's own name and nothing else; the capability
    # overview reads nothing at all. Pulling a checklist to say hello invites
    # the answer to become a status report.
    "greeting": ("getStudentProfile",),
    "capability_overview": (),
    "general_help": (
        "getOnboardingChecklist",
        "getEnrollmentHolds",
        "getStudentDeadlines",
        "getStudentAdvising",
    ),
    "remaining_steps": ("getOnboardingChecklist",),
    "completed_steps": ("getOnboardingChecklist",),
    "next_action": ("getOnboardingChecklist", "getEnrollmentHolds", "getStudentDeadlines"),
    "missing_documents": ("getOnboardingChecklist", "getDocumentStatuses"),
    "document_status": ("getOnboardingChecklist", "getDocumentStatuses"),
    "onboarding_status": ("getOnboardingChecklist", "getEnrollmentState"),
    "enrollment_state": ("getEnrollmentState", "getOnboardingChecklist"),
    "personal_information": ("getOnboardingResponses", "getStudentProfile"),
    "academic_standing": ("getAcademicStanding", "getAcademicPlan"),
    "support_requests": ("getStudentSupportRequests", "getSupportOptions"),
    "holds_and_blockers": ("getEnrollmentHolds",),
    "deadlines": ("getStudentDeadlines",),
    "request_support": ("getSupportOptions", "getStudentSupportRequests", "getStudentAdvising"),
    "aid_status": ("getFinancialAidStatus",),
    "aid_remaining_steps": ("getFinancialAidStatus",),
    "aid_incomplete_reason": ("getFinancialAidStatus",),
    "aid_missing_documents": ("getFinancialAidStatus",),
    "aid_verification_status": ("getFinancialAidStatus",),
    "aid_award_acceptance_status": ("getFinancialAidStatus", "getFinancialAidSummary"),
    "aid_summary": ("getFinancialAidSummary", "getFinancialAidStatus"),
    "aid_application_status": ("getFinancialAidSummary", "getFinancialAidStatus"),
    "aid_disbursement": ("getAidDisbursements", "getFinancialAidSummary", "getEnrollmentHolds"),
    # Coverage crosses into billing by its nature: "does my aid cover tuition"
    # is unanswerable from the aid record alone.
    "aid_coverage": ("getFinancialAidSummary", "getStudentAccountSummary"),
    "aid_next_action": ("getFinancialAidStatus",),
    "aid_support": ("getFinancialAidStatus", "getFinancialAidSupportOptions", "getStudentAdvising"),
    "housing_status": ("getStudentHousingStatus",),
    # Navigation answers name a page *and* what it currently shows, so the
    # destination's own read comes along. The checklist is the shared
    # fallback: every destination has a checklist step behind it.
    "portal_navigation": ("getOnboardingChecklist",),
    "housing_options": ("getHousingOptions",),
    "housing_remaining_steps": ("getStudentHousingStatus",),
    "housing_next_action": ("getStudentHousingStatus", "getStudentDeadlines"),
    "housing_support": ("getStudentHousingStatus", "getSupportOptions", "getStudentAdvising"),
    "housing_eligibility": (
        "getStudentHousingEligibility",
        "getStudentHousingStatus",
        "getOnboardingChecklist",
        "getEnrollmentHolds",
    ),
    # Institutional policy retrieval has no reviewed source yet; the composer
    # answers honestly and routes to support instead of reading student state.
    # Institutional questions read the approved corpus plus enough of the
    # student's own record to say whether a rule applies to them.
    "policy_lookup": ("getInstitutionalPolicies", "getEnrollmentState", "getOnboardingChecklist"),
    "registration_status": (
        "getRegistrationStatus",
        "getEnrollmentHolds",
        "getOnboardingChecklist",
    ),
    "student_account": ("getStudentAccountSummary", "getEnrollmentHolds"),
    # "Is it paid?" needs the payment record; "what changed now that it is
    # paid?" needs the gates it released, and both arrive as one question.
    "deposit_status": (
        "getStudentAccountSummary",
        "getEnrollmentState",
        "getEnrollmentHolds",
    ),
    "appointments": ("getStudentAppointments", "getStudentAdvising"),
    "academic_plan": ("getAcademicPlan",),
    "campus_life": ("getCampusLife",),
    "messages_unread": ("getStudentMessages",),
    "general_question": ("getOnboardingChecklist", "getEnrollmentHolds", "getStudentDeadlines"),
    # Social turns read nothing: gratitude answered with a checklist would be
    # a status report nobody asked for, and both are answered deterministically.
    "conversational_ack": (),
    "assistant_identity": (),
    "unsupported_or_out_of_scope": (),
}

_REQUIRED_TOOLS: Mapping[str, tuple[str, ...]] = {
    "next_action": ("getOnboardingChecklist", "getEnrollmentHolds", "getStudentDeadlines"),
    # Both, always. A document's state is the checklist and the document list
    # read together; with either one missing the derivation produces nothing.
    "missing_documents": ("getOnboardingChecklist", "getDocumentStatuses"),
    "document_status": ("getOnboardingChecklist", "getDocumentStatuses"),
    "aid_summary": ("getFinancialAidSummary",),
    "aid_application_status": ("getFinancialAidSummary",),
    "aid_coverage": ("getFinancialAidSummary", "getStudentAccountSummary"),
    "aid_disbursement": ("getAidDisbursements", "getFinancialAidSummary"),
    "registration_status": ("getRegistrationStatus",),
    "housing_eligibility": ("getStudentHousingEligibility",),
    "aid_support": ("getFinancialAidSupportOptions",),
    "academic_plan": ("getAcademicPlan",),
    "campus_life": ("getCampusLife",),
    "messages_unread": ("getStudentMessages",),
    # The enrollment projection carries admission and completion percent but
    # not the open checklist items. Reading it alone leaves a silence about
    # outstanding work that a rewrite will fill with "nothing outstanding".
    "enrollment_state": ("getEnrollmentState", "getOnboardingChecklist"),
    "personal_information": ("getOnboardingResponses",),
    "academic_standing": ("getAcademicStanding",),
    "support_requests": ("getStudentSupportRequests",),
    # "Is my deposit paid?" is answered by the payment record, never by the
    # checklist step alone — a completed step and a posted payment are
    # different facts and the account read is the one that carries both.
    "deposit_status": ("getStudentAccountSummary",),
}


# The verifying read for each gate/blocker code the first round can surface.
# When a gate names a domain that was not read, exactly one deterministic
# follow-up round fetches the evidence needed to *explain* the gate — never a
# second model-planned expansion.
_GATE_DEPENDENCY_TOOLS: Mapping[str, tuple[str, ...]] = {
    "enrollment_deposit_posted": ("getStudentAccountSummary",),
    "enrollment_confirmed": ("getStudentAccountSummary",),
    "account_balance": ("getStudentAccountSummary",),
    "housing_preference_selected": ("getStudentHousingStatus",),
    "final_transcript": ("getDocumentStatuses", "getOnboardingChecklist"),
    "immunization_cleared": ("getDocumentStatuses", "getOnboardingChecklist"),
    "advising_complete": ("getStudentAppointments",),
    "orientation_complete": ("getStudentAppointments",),
    "fafsa_received": ("getFinancialAidStatus",),
    "verification_complete": ("getFinancialAidStatus",),
    "financial_aid_verification": ("getFinancialAidStatus",),
    "profile_verification": ("getStudentProfile",),
    # An onboarding-authored step is explained by the answers the student gave
    # in the wizard, not by the checklist row that mirrors it.
    "personal_information_confirmed": ("getOnboardingResponses",),
    "enrollment_agreement": ("getOnboardingResponses", "getDocumentStatuses"),
    # A housing step that reads "blocked" without gate evidence cannot be
    # explained — the eligibility read carries the actual blockers.
    "housing_step_blocked": ("getStudentHousingEligibility", "getEnrollmentHolds"),
}

# Open blocking checklist items surface these gate codes even when the holds
# read did not run, so a checklist-only turn can still fetch the verifying
# read (e.g. the payments state behind an open deposit requirement). The table
# is the domain's, not the planner's: a second copy here would let the two
# drift into disagreeing about what a requirement gates.
REQUIREMENT_GATE_CODES: Mapping[str, str] = DOMAIN_REQUIREMENT_GATE_CODES

MAX_DEPENDENCY_TOOLS = 3


def resolve_dependency_reads(
    executed_tools: Sequence[str],
    open_gate_codes: Sequence[str],
) -> tuple[list[str], list[dict[str, str]]]:
    """Deterministic second-round plan: verify gates whose domain wasn't read.

    Returns the bounded tool list plus one reason record per selection so the
    trace can say exactly why each dependency read ran.
    """

    already = set(executed_tools)
    tools: list[str] = []
    reasons: list[dict[str, str]] = []
    for code in open_gate_codes:
        for tool in _GATE_DEPENDENCY_TOOLS.get(str(code), ()):
            if tool in already or tool in tools:
                continue
            if len(tools) >= MAX_DEPENDENCY_TOOLS:
                return tools, reasons
            tools.append(tool)
            reasons.append({"gate": str(code), "tool": tool})
    return tools, reasons


def select_tool_reads(classification: Classification) -> list[str]:
    """The deterministic plan: the primary intent plus every additional one."""

    selected: list[str] = []
    for request_type in (classification.request_type, *classification.additional_request_types):
        for tool in _SELECTION_RULES.get(request_type, ()):
            if tool not in selected:
                selected.append(tool)
    return selected


def validate_model_tool_plan(value: Any) -> tuple[Classification, list[str]] | None:
    """Validate an untrusted model plan; filter, never fail, on extra reads."""

    if not isinstance(value, Mapping):
        return None
    request_type = value.get("requestType")
    confidence = value.get("confidence")
    tool_names = value.get("toolNames")
    if (
        request_type not in REQUEST_TYPES
        or not isinstance(confidence, int | float)
        or confidence < 0.55
        or not isinstance(tool_names, Sequence)
        or isinstance(tool_names, str | bytes)
        or len(tool_names) > 8
    ):
        return None
    additional_raw = value.get("additionalRequestTypes") or []
    if not isinstance(additional_raw, Sequence) or isinstance(additional_raw, str | bytes):
        return None
    # Two is enough for a genuine two-part question. Three invited the planner
    # to bolt unrelated domains onto a narrow question.
    additional = [
        item
        for item in dict.fromkeys(additional_raw)
        if item in REQUEST_TYPES and item not in {request_type, "unsupported_or_out_of_scope"}
    ]
    if len(additional) > 2 or len(additional) != len(list(additional_raw)):
        return None
    if any(name not in TOOL_NAMES for name in tool_names):
        return None
    if request_type == "unsupported_or_out_of_scope" and (tool_names or additional):
        return None

    intents = [str(request_type), *[str(item) for item in additional]]
    # Universal context reads are legitimate for any in-scope intent: a plan
    # that adds the checklist or holds to a housing question is a better plan,
    # not an allowlist violation. An out-of-scope refusal still admits nothing.
    allowed = {tool for intent in intents for tool in _SELECTION_RULES.get(intent, ())}
    if request_type != "unsupported_or_out_of_scope":
        allowed.update(UNIVERSAL_CONTEXT_TOOLS)
    filtered = [str(name) for name in dict.fromkeys(tool_names) if name in allowed]
    # Every intent still needs at least one of its own reads, otherwise the
    # plan carries no evidence for something it claims to answer.
    for intent in intents:
        intent_tools = set(_SELECTION_RULES.get(intent, ()))
        if intent_tools and not intent_tools.intersection(filtered):
            return None
    if request_type != "unsupported_or_out_of_scope" and not filtered:
        return None
    for intent in intents:
        for required in _REQUIRED_TOOLS.get(intent, ()):
            if required not in filtered:
                filtered.append(required)
    classification = Classification(
        request_type=str(request_type),
        confidence=float(confidence),
        source="model_plan",
        additional_request_types=tuple(additional),
    )
    return classification, filtered
