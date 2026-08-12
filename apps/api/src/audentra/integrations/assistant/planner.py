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

from audentra.integrations.assistant.classify import REQUEST_TYPES, Classification

TOOL_NAMES = (
    "getStudentProfile",
    "getOnboardingChecklist",
    "getDocumentStatuses",
    "getEnrollmentHolds",
    "getStudentDeadlines",
    "getSupportOptions",
    "getFinancialAidStatus",
    "getFinancialAidSummary",
    "getAidDisbursements",
    "getStudentHousingStatus",
    "getHousingOptions",
    "getRegistrationStatus",
    "getStudentAccountSummary",
    "getStudentAppointments",
)

RECEIPT_SOURCES: Mapping[str, str] = {
    "getStudentProfile": "profile",
    "getOnboardingChecklist": "onboarding",
    "getDocumentStatuses": "documents",
    "getEnrollmentHolds": "holds",
    "getStudentDeadlines": "deadlines",
    "getSupportOptions": "policies",
    "getFinancialAidStatus": "financial_aid",
    "getFinancialAidSummary": "financial_aid",
    "getAidDisbursements": "financial_aid",
    "getStudentHousingStatus": "housing",
    "getHousingOptions": "housing",
    "getRegistrationStatus": "registration",
    "getStudentAccountSummary": "account",
    "getStudentAppointments": "appointments",
}

_SELECTION_RULES: Mapping[str, tuple[str, ...]] = {
    # A greeting reads the student's own name and nothing else; the capability
    # overview reads nothing at all. Pulling a checklist to say hello invites
    # the answer to become a status report.
    "greeting": ("getStudentProfile",),
    "capability_overview": (),
    "general_help": ("getOnboardingChecklist", "getEnrollmentHolds", "getStudentDeadlines"),
    "remaining_steps": ("getOnboardingChecklist",),
    "completed_steps": ("getOnboardingChecklist",),
    "next_action": ("getOnboardingChecklist", "getEnrollmentHolds", "getStudentDeadlines"),
    "missing_documents": ("getOnboardingChecklist", "getDocumentStatuses"),
    "document_status": ("getOnboardingChecklist", "getDocumentStatuses"),
    "onboarding_status": ("getOnboardingChecklist",),
    "holds_and_blockers": ("getEnrollmentHolds",),
    "deadlines": ("getStudentDeadlines",),
    "request_support": ("getSupportOptions",),
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
    "housing_status": ("getStudentHousingStatus",),
    "housing_options": ("getHousingOptions",),
    "housing_remaining_steps": ("getStudentHousingStatus",),
    "housing_next_action": ("getStudentHousingStatus", "getStudentDeadlines"),
    "housing_support": ("getStudentHousingStatus", "getSupportOptions"),
    "registration_status": (
        "getRegistrationStatus",
        "getEnrollmentHolds",
        "getOnboardingChecklist",
    ),
    "student_account": ("getStudentAccountSummary", "getEnrollmentHolds"),
    "appointments": ("getStudentAppointments",),
    "general_question": ("getOnboardingChecklist", "getEnrollmentHolds", "getStudentDeadlines"),
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
}


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
    allowed = {tool for intent in intents for tool in _SELECTION_RULES.get(intent, ())}
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
