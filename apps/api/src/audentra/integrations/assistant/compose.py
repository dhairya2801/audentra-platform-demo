"""Grounded answer composition.

The deterministic composer always produces a complete, true answer with
presentation blocks from the derived state — it is the floor every model
rewrite must beat. The evidence bundle rendered here is also the corpus the
claim guard checks model prose against, so composition and guarding can never
disagree about what the evidence said.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from audentra.integrations.assistant.blocks import (
    bullet_list_block,
    next_steps_block,
    table_block,
    text_block,
)
from audentra.integrations.assistant.classify import Classification
from audentra.integrations.assistant.derive import DerivedState

JsonDict = dict[str, Any]

_CAPABILITY_MESSAGE = (
    "I can check your enrollment checklist, documents, deadlines, holds, "
    "financial aid, housing, registration, account balance, and appointments — "
    "always from your live university record. I can explain what something "
    "means and what to do next, but I never change a record for you. "
    "Ask me one specific question to start."
)


@dataclass
class ComposedAnswer:
    message: str
    blocks: list[JsonDict] = field(default_factory=list)
    evidence_texts: list[str] = field(default_factory=list)


def compose_deterministic(
    classification: Classification,
    state: DerivedState,
    *,
    preferred_name: str | None = None,
) -> ComposedAnswer:
    request_type = classification.request_type
    composer = _COMPOSERS.get(request_type, _compose_general)
    answer = composer(classification, state, preferred_name)
    for extra in classification.additional_request_types:
        extra_answer = _COMPOSERS.get(extra, _compose_general)(
            classification, state, preferred_name
        )
        if extra_answer.message not in answer.message:
            answer.message = f"{answer.message} {extra_answer.message}"
            answer.blocks.extend(
                block for block in extra_answer.blocks if block not in answer.blocks
            )
            answer.evidence_texts.extend(extra_answer.evidence_texts)
    for note in _unavailable_notes(state):
        answer.message = f"{answer.message} {note}"
    answer.message = answer.message.strip()[:1_200]
    return answer


def build_evidence_bundle(state: DerivedState) -> list[str]:
    """Every fact the composer may use, rendered once as plain text."""

    lines: list[str] = []
    for step in state.remaining_steps:
        lines.append(f"Open checklist step: {step.get('title')} (status {step.get('status')})")
    for step in state.completed_steps:
        lines.append(f"Completed checklist step: {step.get('title')}")
    for document in state.document_states:
        lines.append(
            f"Document {document['title']}: {document['submissionState'].replace('_', ' ')}"
        )
    for deadline in state.deadlines:
        lines.append(f"Deadline: {deadline.get('title')} due {deadline.get('dueAt')}")
    for blocker in state.derived_blockers:
        lines.append(
            f"Blocker: {blocker.get('title')} — cleared by: {blocker.get('clearingAction')}"
        )
    for gate in state.registration_gates:
        lines.append(f"Registration gate (open): {gate.get('title')}")
    aid = state.financial_aid
    if aid:
        for award in aid.get("awards", []):
            lines.append(
                f"Aid award {award.get('name')} ({award.get('type')}): status "
                f"{award.get('status')}, offered {_usd(award.get('offeredAmountCents'))}, "
                f"accepted {_usd(award.get('acceptedAmountCents'))}"
            )
        for requirement in aid.get("requiredDocuments", []):
            lines.append(
                f"Aid requirement {requirement.get('title')}: status {requirement.get('status')}"
                + (f", due {requirement['dueAt']}" if requirement.get("dueAt") else "")
            )
        for key, label in (
            ("acceptedAidCents", "Accepted aid total"),
            ("pendingAidCents", "Pending aid total"),
            ("costOfAttendanceCents", "Cost of attendance"),
            ("remainingBalanceCents", "Remaining balance"),
            ("paymentsCents", "Payments recorded"),
        ):
            if aid.get(key) is not None:
                lines.append(f"{label}: {_usd(aid[key])}")
    account = state.account
    if account:
        for key, label in (
            ("remainingBalanceCents", "Remaining balance"),
            ("depositAmountCents", "Enrollment deposit amount"),
        ):
            if account.get(key) is not None:
                lines.append(f"{label}: {_usd(account[key])}")
        lines.append(f"Deposit paid: {'yes' if account.get('depositPaid') else 'no'}")
    if state.housing:
        lines.append(
            f"Housing preference: {state.housing.get('preference') or 'not selected'}; "
            f"requirement status: {state.housing.get('requirementStatus') or 'unknown'}"
        )
    if state.housing_options:
        for residence in state.housing_options.get("residences", []):
            lines.append(f"Housing option: {residence.get('name')}")
    if state.appointments:
        for appointment in state.appointments.get("items", []):
            lines.append(
                f"Appointment: {appointment.get('type')} at {appointment.get('startsAt')} "
                f"({appointment.get('status')})"
            )
    if state.priority:
        lines.append(f"Priority action: {state.priority['title']} — {state.priority['reason']}")
    return lines


def _compose_greeting(
    _classification: Classification, _state: DerivedState, preferred_name: str | None
) -> ComposedAnswer:
    name = f", {preferred_name}" if preferred_name else ""
    message = (
        f"Hi{name}! I'm Edward, your enrollment assistant. "
        "Ask me about your checklist, documents, deadlines, financial aid, "
        "housing, or anything else about getting enrolled."
    )
    return ComposedAnswer(message=message, blocks=[text_block(message)])


def _compose_capabilities(
    _classification: Classification, _state: DerivedState, _name: str | None
) -> ComposedAnswer:
    return ComposedAnswer(message=_CAPABILITY_MESSAGE, blocks=[text_block(_CAPABILITY_MESSAGE)])


def _compose_next_action(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if state.priority is None:
        message = (
            "You're all caught up — nothing on your enrollment checklist needs action right now."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    steps = [
        {
            "text": f"{state.priority['title']} — {state.priority['reason']}",
            "href": state.priority.get("href"),
            "owner": "student",
        }
    ]
    for step in state.remaining_steps[:3]:
        if state.priority and step.get("title") == state.priority.get("title"):
            continue
        steps.append({"text": str(step.get("title")), "href": step.get("href"), "owner": "student"})
    message = f"Your next step: {state.priority['title']}. {state.priority['reason']}"
    return ComposedAnswer(
        message=message,
        blocks=[text_block(message), next_steps_block(steps[:4], title="What to do next")],
        evidence_texts=evidence,
    )


def _compose_checklist(
    classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if classification.request_type == "completed_steps":
        if not state.completed_steps:
            message = "You haven't completed any checklist steps yet."
            return ComposedAnswer(
                message=message, blocks=[text_block(message)], evidence_texts=evidence
            )
        message = f"You've completed {len(state.completed_steps)} checklist step(s)."
        block = bullet_list_block(
            [{"text": str(step.get("title"))} for step in state.completed_steps],
            title="Completed",
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message), block], evidence_texts=evidence
        )
    if not state.remaining_steps:
        message = "Your enrollment checklist is complete — nothing is outstanding."
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = f"{len(state.remaining_steps)} checklist step(s) still need attention."
    block = next_steps_block(
        [
            {"text": str(step.get("title")), "href": step.get("href"), "owner": "student"}
            for step in state.remaining_steps[:6]
        ],
        title="Still to do",
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_documents(
    classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    states = state.document_states
    if not states:
        message = "No document requirements are on your checklist right now."
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    if classification.request_type == "missing_documents":
        if not state.missing_documents:
            message = "Every required document is in — nothing is waiting on an upload from you."
            return ComposedAnswer(
                message=message, blocks=[text_block(message)], evidence_texts=evidence
            )
        message = f"{len(state.missing_documents)} document(s) still need to be uploaded."
        block = next_steps_block(
            [
                {
                    "text": f"Upload your {item['title']}",
                    "href": item.get("href"),
                    "owner": "student",
                }
                for item in state.missing_documents
            ],
            title="Missing documents",
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message), block], evidence_texts=evidence
        )
    label = {
        "accepted": "Accepted",
        "under_review": "Under review",
        "not_submitted": "Not submitted",
        "needs_attention": "Needs attention",
    }
    rows = [
        {
            "document": item["title"],
            "status": label.get(item["submissionState"], item["submissionState"]),
        }
        for item in states
    ]
    summary_parts = []
    under_review = [item for item in states if item["submissionState"] == "under_review"]
    if under_review:
        summary_parts.append(
            f"{_join_titles(under_review)} {'is' if len(under_review) == 1 else 'are'} under review"
        )
    accepted = [item for item in states if item["submissionState"] == "accepted"]
    if accepted:
        summary_parts.append(f"{_join_titles(accepted)} accepted")
    missing = state.missing_documents
    if missing:
        summary_parts.append(f"{_join_titles(missing)} not submitted yet")
    message = (
        "Here's where your documents stand: " + "; ".join(summary_parts) + "."
        if summary_parts
        else "Here's where your documents stand."
    )
    block = table_block(
        [{"key": "document", "label": "Document"}, {"key": "status", "label": "Status"}],
        rows,
        caption="Document status",
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_holds(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if not state.official_holds and not state.derived_blockers:
        message = "You have no holds, and nothing on your record is blocking you right now."
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    parts = []
    if state.official_holds:
        parts.append(f"{len(state.official_holds)} hold(s)")
    if state.derived_blockers:
        parts.append(f"{len(state.derived_blockers)} item(s) blocking progress")
    message = f"You have {' and '.join(parts)}. Each one lists what clears it."
    block = next_steps_block(
        [
            {
                "text": f"{blocker['title']} — {blocker['clearingAction']}",
                "href": blocker.get("href"),
                "owner": blocker.get("owner", "student"),
            }
            for blocker in [*state.official_holds, *state.derived_blockers]
        ],
        title="What's blocking you",
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_deadlines(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if not state.deadlines:
        message = "Nothing on your record has an upcoming deadline."
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    bucket_label = {
        "overdue": "Past due",
        "this_week": "Due this week",
        "this_month": "Due this month",
        "later": "Later",
    }
    rows = [
        {
            "item": str(deadline.get("title")),
            "due": str(deadline.get("dueAt", ""))[:10],
            "window": bucket_label.get(str(deadline.get("bucket")), "Later"),
        }
        for deadline in state.deadlines
    ]
    overdue = [item for item in state.deadlines if item.get("bucket") == "overdue"]
    message = (
        f"{len(overdue)} deadline(s) are past due — start there."
        if overdue
        else f"You have {len(state.deadlines)} upcoming deadline(s)."
    )
    block = table_block(
        [
            {"key": "item", "label": "Item"},
            {"key": "due", "label": "Due"},
            {"key": "window", "label": "Window"},
        ],
        rows,
        caption="Your deadlines",
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_financial_aid(
    classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    aid = state.financial_aid
    if not aid:
        message = (
            "I couldn't read your financial aid record just now. Open the "
            "Financials page, or ask again in a moment."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    request_type = classification.request_type
    blocks: list[JsonDict] = []
    if request_type == "aid_award_acceptance_status":
        accepted = aid.get("acceptedAwards", [])
        if accepted:
            message = f"You've accepted {len(accepted)} award(s)."
        else:
            message = "You haven't accepted any awards yet."
        offered = aid.get("offeredAwards", [])
        if offered:
            message += f" {len(offered)} award(s) are still waiting on a decision."
    elif request_type == "aid_application_status":
        fafsa = aid.get("fafsa")
        if fafsa is None:
            message = (
                "Your record doesn't list a FAFSA requirement, so there's nothing pending there."
            )
        elif fafsa.get("status") in {"received", "waived"}:
            message = "Your FAFSA has been received."
        else:
            message = (
                f"Your FAFSA is still {str(fafsa.get('status', 'pending')).replace('_', ' ')}."
            )
    elif request_type == "aid_verification_status":
        verification = aid.get("verification")
        if verification is None:
            message = "You have not been selected for verification."
        elif verification.get("status") in {"received", "waived"}:
            message = "Your verification worksheet has been received."
        else:
            message = (
                "Your verification worksheet is still "
                f"{str(verification.get('status', 'pending')).replace('_', ' ')}."
            )
    elif request_type == "aid_incomplete_reason":
        open_requirements = aid.get("openRequirements", [])
        if not open_requirements:
            message = "Your financial aid file is complete — nothing is holding it open."
        else:
            message = (
                f"Your aid is incomplete because {len(open_requirements)} requirement(s) "
                f"are still open: {_join_titles(open_requirements)}."
            )
    elif request_type == "aid_disbursement":
        disbursements = aid.get("disbursements") or {}
        gates = [gate for gate in disbursements.get("gates", []) if not gate.get("satisfied")]
        if gates:
            message = (
                "No disbursement date is published in the portal, and "
                f"{len(gates)} condition(s) must close before aid can pay out: "
                f"{_join_titles(gates)}."
            )
        else:
            message = (
                "No disbursement schedule is published in the portal, but no aid "
                "requirement is holding your package open. The financial aid "
                "office confirms exact dates."
            )
    elif request_type == "aid_coverage":
        remaining = aid.get("remainingBalanceCents")
        accepted_total = aid.get("acceptedAidCents")
        if remaining is not None and accepted_total is not None:
            message = (
                f"Your accepted aid totals {_usd(accepted_total)}. After aid and "
                f"payments, your remaining balance is {_usd(remaining)}."
            )
        else:
            message = "I couldn't compute your covered balance from the record just now."
    else:
        open_requirements = aid.get("openRequirements", [])
        awards = aid.get("awards", [])
        if not awards and not open_requirements:
            message = "Your financial aid record has no awards or open requirements yet."
        elif open_requirements:
            message = (
                f"Your aid file lists {len(awards)} award(s), and "
                f"{len(open_requirements)} requirement(s) still need attention: "
                f"{_join_titles(open_requirements)}."
            )
        else:
            message = (
                f"Your aid file lists {len(awards)} award(s), and every requirement is satisfied."
            )
    awards = aid.get("awards", [])
    if awards and request_type in {
        "aid_summary",
        "aid_status",
        "aid_award_acceptance_status",
        "aid_coverage",
    }:
        blocks.append(
            table_block(
                [
                    {"key": "award", "label": "Award"},
                    {"key": "status", "label": "Status"},
                    {"key": "offered", "label": "Offered", "align": "right"},
                    {"key": "accepted", "label": "Accepted", "align": "right"},
                ],
                [
                    {
                        "award": str(award.get("name", "")),
                        "status": str(award.get("status", "")).replace("_", " "),
                        "offered": _usd(award.get("offeredAmountCents")),
                        "accepted": _usd(award.get("acceptedAmountCents")),
                    }
                    for award in awards
                ],
                caption=f"Financial aid awards ({aid.get('academicYear', '')})",
            )
        )
    open_requirements = aid.get("openRequirements", [])
    if open_requirements and request_type != "aid_coverage":
        blocks.append(
            next_steps_block(
                [
                    {
                        "text": str(item.get("title")),
                        "href": item.get("href"),
                        "owner": "student",
                    }
                    for item in open_requirements
                ],
                title="Open aid requirements",
            )
        )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), *blocks], evidence_texts=evidence
    )


def _compose_housing(
    classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if classification.request_type == "housing_options" and state.housing_options:
        residences = state.housing_options.get("residences", [])
        message = f"{len(residences)} housing option(s) are listed."
        block = bullet_list_block(
            [
                {"text": f"{item.get('name')} — {item.get('description', '')}"[:160]}
                for item in residences
            ],
            title="Housing options",
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message), block], evidence_texts=evidence
        )
    housing = state.housing or {}
    preference = housing.get("preference")
    requirement_status = housing.get("requirementStatus")
    if preference:
        message = f"Your housing preference is {str(preference).replace('_', ' ')}."
        if requirement_status and str(requirement_status) not in {"completed", "waived"}:
            message += (
                " The housing step on your checklist is still "
                f"{str(requirement_status).replace('_', ' ')}."
            )
    else:
        message = (
            "You haven't selected a housing preference yet. You can set it from "
            "the housing step on your enrollment checklist."
        )
    blocks = [text_block(message)]
    if housing.get("requirementHref") and (
        not preference or str(requirement_status or "") not in {"completed", "waived"}
    ):
        blocks.append(
            next_steps_block(
                [
                    {
                        "text": "Finish the housing step on your checklist",
                        "href": str(housing["requirementHref"]),
                        "owner": "student",
                    }
                ]
            )
        )
    return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_registration(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    gates = state.registration_gates
    if not gates:
        message = (
            "Nothing on your enrollment record is blocking registration. "
            "Exact registration windows come from the registrar."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = (
        f"{len(gates)} item(s) on your record are blocking registration: "
        f"{_join_titles(gates)}. Clearing them is what opens registration."
    )
    block = next_steps_block(
        [
            {
                "text": f"{gate['title']} — {gate.get('clearingAction', '')}".strip(" —"),
                "href": gate.get("href"),
                "owner": gate.get("owner", "student"),
            }
            for gate in gates
        ],
        title="Blocking registration",
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_account(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    account = state.account
    if not account:
        message = "I couldn't read your account balance just now. Open Payments to check directly."
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    remaining = account.get("remainingBalanceCents")
    message = (
        f"Your remaining balance is {_usd(remaining)}."
        if remaining is not None
        else "Your account has no computed balance yet."
    )
    if not account.get("depositPaid") and account.get("depositAmountCents"):
        message += (
            f" Your {_usd(account['depositAmountCents'])} enrollment deposit has not been paid yet."
        )
    return ComposedAnswer(message=message, blocks=[text_block(message)], evidence_texts=evidence)


def _compose_appointments(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    appointments = (state.appointments or {}).get("items", [])
    if not appointments:
        message = (
            "You have no appointments scheduled. You can book enrollment, "
            "admissions, or financial-aid support from the Appointments page."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = f"You have {len(appointments)} appointment(s) scheduled."
    block = bullet_list_block(
        [
            {"text": f"{str(item.get('type', '')).replace('_', ' ')} — {item.get('startsAt', '')}"}
            for item in appointments
        ],
        title="Your appointments",
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_support(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    message = (
        "The enrollment support team can help directly — open the Help page to "
        "send a message, or book time from Appointments."
    )
    return ComposedAnswer(message=message, blocks=[text_block(message)], evidence_texts=evidence)


def _compose_unsupported(
    classification: Classification, _state: DerivedState, _name: str | None
) -> ComposedAnswer:
    reference = classification.requirement_reference
    if reference == "sensitive_financial_data":
        message = (
            "Please don't share account, card, or Social Security numbers in this "
            "chat. I didn't keep that, and I can't use it. For anything involving "
            "those details, use the secure portal pages or meet with staff."
        )
    elif reference == "other_person_contact_details":
        message = (
            "I can't share another person's contact details. I can help with your "
            "own record, or point you to the right office."
        )
    elif reference and reference.endswith("write_unavailable"):
        message = (
            "I can't make changes for you — I'm read-only. I can tell you exactly "
            "where to do it yourself: every change happens from the portal pages, "
            "and I can point you to the right one."
        )
    else:
        message = (
            "That's outside what I can check. I can answer questions about your "
            "enrollment, documents, deadlines, financial aid, housing, "
            "registration, account, and appointments."
        )
    return ComposedAnswer(message=message, blocks=[text_block(message)])


def _compose_general(
    classification: Classification, state: DerivedState, name: str | None
) -> ComposedAnswer:
    return _compose_next_action(classification, state, name)


_COMPOSERS = {
    "greeting": _compose_greeting,
    "capability_overview": _compose_capabilities,
    "general_help": _compose_next_action,
    "next_action": _compose_next_action,
    "remaining_steps": _compose_checklist,
    "completed_steps": _compose_checklist,
    "onboarding_status": _compose_checklist,
    "missing_documents": _compose_documents,
    "document_status": _compose_documents,
    "holds_and_blockers": _compose_holds,
    "deadlines": _compose_deadlines,
    "request_support": _compose_support,
    "aid_status": _compose_financial_aid,
    "aid_remaining_steps": _compose_financial_aid,
    "aid_incomplete_reason": _compose_financial_aid,
    "aid_missing_documents": _compose_financial_aid,
    "aid_verification_status": _compose_financial_aid,
    "aid_award_acceptance_status": _compose_financial_aid,
    "aid_summary": _compose_financial_aid,
    "aid_application_status": _compose_financial_aid,
    "aid_disbursement": _compose_financial_aid,
    "aid_coverage": _compose_financial_aid,
    "aid_next_action": _compose_financial_aid,
    "housing_status": _compose_housing,
    "housing_options": _compose_housing,
    "housing_remaining_steps": _compose_housing,
    "housing_next_action": _compose_housing,
    "housing_support": _compose_housing,
    "registration_status": _compose_registration,
    "student_account": _compose_account,
    "appointments": _compose_appointments,
    "unsupported_or_out_of_scope": _compose_unsupported,
    "general_question": _compose_general,
}


def _unavailable_notes(state: DerivedState) -> list[str]:
    """An honest sentence per source Edward could not check this turn."""

    labels = {
        "getFinancialAidStatus": "your financial aid requirements",
        "getFinancialAidSummary": "your financial aid amounts",
        "getAidDisbursements": "aid disbursements",
        "getStudentHousingStatus": "your housing plan",
        "getHousingOptions": "housing options",
        "getRegistrationStatus": "registration status",
        "getStudentAccountSummary": "your account balance",
        "getOnboardingChecklist": "your enrollment checklist",
        "getDocumentStatuses": "your documents",
        "getEnrollmentHolds": "holds",
        "getStudentDeadlines": "deadlines",
        "getStudentAppointments": "your appointments",
    }
    notes = []
    for item in state.unavailable_data:
        label = labels.get(str(item.get("source")))
        if label:
            notes.append(f"I couldn't check {label} just now, so I've left it out.")
    return notes[:2]


def _join_titles(items: Sequence[dict[str, Any]]) -> str:
    titles = [str(item.get("title") or item.get("name") or "item") for item in items[:4]]
    if len(titles) <= 1:
        return titles[0] if titles else ""
    return ", ".join(titles[:-1]) + f" and {titles[-1]}"


def _usd(cents: Any) -> str:
    try:
        value = int(cents)
    except (TypeError, ValueError):
        return "$0"
    dollars = value / 100
    return f"${dollars:,.2f}".removesuffix(".00")
