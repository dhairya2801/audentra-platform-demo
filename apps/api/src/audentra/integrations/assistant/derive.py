"""Deterministic derived student state.

The port of `deriveStudentStateNode`: everything a correct answer needs that
is computable without a model — checklist split, the document lifecycle join,
financial-aid derivation, deadline buckets, gates, and the single highest
priority action — derived from the tool reads of this turn only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from audentra.integrations.assistant.tools import ToolExecution

JsonDict = dict[str, Any]

_DONE = {"completed", "waived", "not_applicable"}
_IN_REVIEW = {"submitted", "under_review"}


@dataclass
class DerivedState:
    completed_steps: list[JsonDict] = field(default_factory=list)
    remaining_steps: list[JsonDict] = field(default_factory=list)
    document_states: list[JsonDict] = field(default_factory=list)
    missing_documents: list[JsonDict] = field(default_factory=list)
    deadlines: list[JsonDict] = field(default_factory=list)
    official_holds: list[JsonDict] = field(default_factory=list)
    derived_blockers: list[JsonDict] = field(default_factory=list)
    registration_gates: list[JsonDict] = field(default_factory=list)
    financial_aid: JsonDict | None = None
    account: JsonDict | None = None
    housing: JsonDict | None = None
    housing_options: JsonDict | None = None
    appointments: JsonDict | None = None
    academics: JsonDict | None = None
    campus_life: JsonDict | None = None
    messages: JsonDict | None = None
    support: JsonDict | None = None
    profile: JsonDict | None = None
    priority: JsonDict | None = None
    suggested_actions: list[JsonDict] = field(default_factory=list)
    unavailable_data: list[JsonDict] = field(default_factory=list)


def derive_student_state(execution: ToolExecution) -> DerivedState:
    state = DerivedState(unavailable_data=list(execution.unavailable_data))
    reads = {
        name: read.get("data")
        for name, read in execution.reads.items()
        if read.get("status") == "available" and isinstance(read.get("data"), Mapping)
    }

    checklist = reads.get("getOnboardingChecklist")
    if checklist:
        for item in _items(checklist):
            step = dict(item)
            if str(item.get("status") or "") in _DONE:
                state.completed_steps.append(step)
            else:
                state.remaining_steps.append(step)

    documents = reads.get("getDocumentStatuses")
    if checklist and documents:
        state.document_states = _derive_document_states(checklist, documents)
        state.missing_documents = [
            item for item in state.document_states if item["submissionState"] == "not_submitted"
        ]

    deadlines = reads.get("getStudentDeadlines")
    if deadlines:
        state.deadlines = [dict(_mapping(item)) for item in _sequence(deadlines.get("items"))]

    holds = reads.get("getEnrollmentHolds")
    if holds:
        state.official_holds = [
            dict(_mapping(item)) for item in _sequence(holds.get("officialHolds"))
        ]
        state.derived_blockers = [
            dict(_mapping(item)) for item in _sequence(holds.get("derivedBlockers"))
        ]

    registration = reads.get("getRegistrationStatus")
    if registration:
        state.registration_gates = [
            dict(_mapping(item)) for item in _sequence(registration.get("gates"))
        ]

    state.financial_aid = _derive_financial_aid(
        reads.get("getFinancialAidStatus"),
        reads.get("getFinancialAidSummary"),
        reads.get("getAidDisbursements"),
    )
    state.account = (
        dict(reads["getStudentAccountSummary"]) if "getStudentAccountSummary" in reads else None
    )
    state.housing = (
        dict(reads["getStudentHousingStatus"]) if "getStudentHousingStatus" in reads else None
    )
    state.housing_options = (
        dict(reads["getHousingOptions"]) if "getHousingOptions" in reads else None
    )
    state.appointments = (
        dict(reads["getStudentAppointments"]) if "getStudentAppointments" in reads else None
    )
    state.academics = dict(reads["getAcademicPlan"]) if "getAcademicPlan" in reads else None
    state.campus_life = dict(reads["getCampusLife"]) if "getCampusLife" in reads else None
    state.messages = dict(reads["getStudentMessages"]) if "getStudentMessages" in reads else None
    state.support = dict(reads["getSupportOptions"]) if "getSupportOptions" in reads else None
    state.profile = dict(reads["getStudentProfile"]) if "getStudentProfile" in reads else None

    state.priority = _derive_priority(state)
    state.suggested_actions = _derive_suggested_actions(state)
    return state


def _derive_document_states(
    checklist: Mapping[str, Any], documents: Mapping[str, Any]
) -> list[JsonDict]:
    """The checklist and the document list read together.

    A document's state is the requirement status joined with the newest
    matching upload: requirement done → accepted; an upload in flight →
    under_review; a failed extraction → needs_attention; otherwise
    not_submitted. This join is what keeps "have I uploaded my transcript?"
    truthful the moment an upload lands.
    """

    uploads = [_mapping(item) for item in _sequence(documents.get("items"))]
    states: list[JsonDict] = []
    for item in _items(checklist):
        category = item.get("documentCategory")
        if not category:
            continue
        requirement_status = str(item.get("status") or "")
        matching = [
            upload
            for upload in uploads
            if upload.get("requirementId") == item.get("id") or upload.get("category") == category
        ]
        newest = max(matching, key=lambda upload: str(upload.get("createdAt") or ""), default=None)
        if requirement_status in _DONE:
            submission_state = "accepted"
        elif newest is not None and str(newest.get("extractionStatus") or "") == "failed":
            submission_state = "needs_attention"
        elif newest is not None or requirement_status in _IN_REVIEW:
            submission_state = "under_review"
        else:
            submission_state = "not_submitted"
        states.append(
            {
                "title": str(item.get("title") or "Document"),
                "category": str(category),
                "requirementStatus": requirement_status,
                "submissionState": submission_state,
                "fileName": newest.get("fileName") if newest else None,
                "href": item.get("href"),
            }
        )
    return states


def _derive_financial_aid(
    status: Mapping[str, Any] | None,
    summary: Mapping[str, Any] | None,
    disbursements: Mapping[str, Any] | None,
) -> JsonDict | None:
    if status is None and summary is None and disbursements is None:
        return None
    aid: JsonDict = {}
    source = summary or status or {}
    awards = [_mapping(item) for item in _sequence(_mapping(source).get("awards"))]
    aid["awards"] = [dict(item) for item in awards]
    aid["acceptedAwards"] = [dict(item) for item in awards if item.get("status") == "accepted"]
    aid["offeredAwards"] = [
        dict(item) for item in awards if item.get("status") in {"offered", "pending"}
    ]
    if summary:
        for key in (
            "academicYear",
            "costOfAttendanceCents",
            "acceptedAidCents",
            "pendingAidCents",
            "paymentsCents",
            "remainingBalanceCents",
        ):
            if summary.get(key) is not None:
                aid[key] = summary[key]
    open_requirements: list[JsonDict] = []
    if status:
        open_requirements = [
            dict(_mapping(item)) for item in _sequence(status.get("openRequirements"))
        ]
        aid["requiredDocuments"] = [
            dict(_mapping(item)) for item in _sequence(status.get("requiredDocuments"))
        ]
    aid["openRequirements"] = open_requirements
    aid["complete"] = not open_requirements
    fafsa = next(
        (
            item
            for item in aid.get("requiredDocuments", [])
            if "fafsa" in str(item.get("code") or "").lower()
            or "fafsa" in str(item.get("title") or "").lower()
        ),
        None,
    )
    aid["fafsa"] = fafsa
    verification = next(
        (
            item
            for item in aid.get("requiredDocuments", [])
            if "verification" in str(item.get("code") or "").lower()
            or "verification" in str(item.get("title") or "").lower()
        ),
        None,
    )
    aid["verification"] = verification
    if disbursements:
        aid["disbursements"] = dict(disbursements)
    return aid


def _derive_priority(state: DerivedState) -> JsonDict | None:
    overdue = next((item for item in state.deadlines if item.get("bucket") == "overdue"), None)
    if overdue:
        return {
            "kind": "overdue_deadline",
            "title": str(overdue.get("title")),
            "href": overdue.get("href"),
            "reason": "It is past due.",
        }
    deposit = next(
        (
            item
            for item in state.derived_blockers
            if item.get("code") == "enrollment_deposit_posted"
        ),
        None,
    )
    if deposit:
        return {
            "kind": "deposit",
            "title": "Pay the enrollment deposit",
            "href": "/payments",
            "reason": "It unlocks the rest of enrollment.",
        }
    missing = state.missing_documents[0] if state.missing_documents else None
    if missing:
        return {
            "kind": "missing_document",
            "title": f"Upload your {missing['title']}",
            "href": missing.get("href") or "/documents",
            "reason": "The requirement is still waiting on a file.",
        }
    remaining = state.remaining_steps[0] if state.remaining_steps else None
    if remaining:
        return {
            "kind": "requirement",
            "title": str(remaining.get("title")),
            "href": remaining.get("href"),
            "reason": "It is the next open checklist step.",
        }
    return None


def _derive_suggested_actions(state: DerivedState) -> list[JsonDict]:
    actions: list[JsonDict] = []

    def add(label: str, href: str) -> None:
        if not any(action["href"] == href for action in actions):
            actions.append({"label": label, "href": href})

    if state.priority and state.priority.get("href"):
        add(str(state.priority["title"]), str(state.priority["href"]))
    if state.missing_documents:
        add("Open documents", "/documents")
    if state.financial_aid and not state.financial_aid.get("complete", True):
        add("Open financial aid", "/financials")
    if state.derived_blockers or state.official_holds:
        add("Open your enrollment checklist", "/enrollment")
    return actions[:3]


def _items(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [_mapping(item) for item in _sequence(value.get("items"))]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return ()
