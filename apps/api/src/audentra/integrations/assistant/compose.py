"""Grounded answer composition.

The deterministic composer always produces a complete, true answer with
presentation blocks from the derived state — it is the floor every model
rewrite must beat. The evidence bundle rendered here is also the corpus the
claim guard checks model prose against, so composition and guarding can never
disagree about what the evidence said.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from audentra.domain.student_state import requirement_gate_code
from audentra.integrations.assistant import links
from audentra.integrations.assistant.blocks import (
    bullet_list_block,
    next_steps_block,
    table_block,
    text_block,
)
from audentra.integrations.assistant.classify import Classification
from audentra.integrations.assistant.derive import DerivedState, deposit_state
from audentra.integrations.assistant.links import club_page

JsonDict = dict[str, Any]

_CAPABILITY_MESSAGE = (
    "I'm Edward, your enrollment assistant. I can check your enrollment "
    "checklist, documents, deadlines, holds, financial aid, housing, "
    "registration, account balance, and appointments — always from your live "
    "university record. I can explain what something means and what to do "
    "next, but I never change a record for you. Ask me one specific question "
    "to start."
)

# Where each family of write actually happens; refusals point there so a
# blocked request still ends with a useful door.
_WRITE_DESTINATIONS = {
    "housing_write_unavailable": (
        "the housing step on your enrollment checklist",
        links.ENROLLMENT,
    ),
    "financial_aid_write_unavailable": ("the Financials page", links.FINANCIALS),
    "document_write_unavailable": ("the Documents page", links.DOCUMENTS),
    "payment_write_unavailable": ("the Payments page", links.PAYMENTS),
    "appointment_write_unavailable": ("the Appointments page", links.APPOINTMENTS),
    "write_unavailable": ("the matching portal page", links.ENROLLMENT),
}

# Student-facing names for each read, used for honest unavailability language
# in both the evidence bundle and deterministic answers.
_SOURCE_LABELS = {
    "getFinancialAidStatus": "your financial aid requirements",
    "getFinancialAidSummary": "your financial aid amounts",
    "getAidDisbursements": "aid disbursements",
    "getFinancialAidSupportOptions": "financial-aid support options",
    "getStudentHousingStatus": "your housing plan",
    "getStudentHousingEligibility": "housing eligibility",
    "getHousingOptions": "housing options",
    "getRegistrationStatus": "registration status",
    "getStudentAccountSummary": "your account balance",
    "getOnboardingChecklist": "your enrollment checklist",
    "getDocumentStatuses": "your documents",
    "getEnrollmentHolds": "holds and blockers",
    "getStudentDeadlines": "deadlines",
    "getStudentAppointments": "your appointments",
    "getStudentAdvising": "your advisers",
    "getAcademicPlan": "your academic plan",
    "getCampusLife": "campus events and clubs",
    "getStudentMessages": "your messages",
    "getStudentProfile": "your profile",
    "getEnrollmentState": "your enrollment record",
    "getOnboardingResponses": "your onboarding answers",
    "getAcademicStanding": "your academic-progress record",
    "getStudentSupportRequests": "your support conversations",
    "getSupportOptions": "the help centre",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


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
    _answer_waiver_claim(answer, classification, state)
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
    _append_policy_note(answer, classification, state)
    for note in _unavailable_notes(state):
        answer.message = f"{answer.message} {note}"
    answer.message = answer.message.strip()[:1_200]
    return answer


def _append_policy_note(
    answer: ComposedAnswer, classification: Classification, state: DerivedState
) -> None:
    """A record answer that also read the corpus carries the rule it found.

    The prose rewrite works from this draft; a rule that only lives in the
    evidence tail is a rule the rewrite tends to drop, so the top document's
    best section rides in the draft itself, named and versioned.
    """

    if classification.request_type == "policy_lookup":
        return
    knowledge = state.institution_knowledge
    if not knowledge:
        return
    documents = [_mapping_or_empty(item) for item in knowledge.get("documents", [])]
    if not documents:
        return
    primary = documents[0]
    sections = [_mapping_or_empty(item) for item in primary.get("sections", [])]
    situation = _mapping_or_empty(primary.get("situationSection"))
    lead = situation or (sections[0] if sections else {})
    text = str(lead.get("highlight") or lead.get("text") or primary.get("summary") or "")
    if not text:
        return
    applicability = _mapping_or_empty(primary.get("applicability"))
    verdict = str(applicability.get("verdict") or "")
    prefix = {
        "applies": "This applies to you. ",
        "does_not_apply": "By your record this does not apply to you. ",
    }.get(verdict, "")
    sentence = (
        f"Policy — {primary.get('title')} (v{primary.get('version')}): {prefix}"
        f"{_clip_text(text, 360)}"
    )
    if sentence not in answer.message:
        # After the first sentence, so a long record answer (which the
        # 1,200-character cap trims from the end) cannot push the rule out.
        head, separator, tail = answer.message.partition(". ")
        answer.message = (
            f"{head}. {sentence} {tail}".strip()
            if separator
            else f"{answer.message} {sentence}".strip()
        )
    items = [
        {"text": f"{doc.get('title')}: {_clip_text(str(doc.get('summary') or ''), 200)}"}
        for doc in documents[:3]
    ]
    answer.blocks.append(bullet_list_block(items, title="From the policy"))


def _clip_text(value: str, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[: limit - 1]
    if "." in cut[limit // 2 :]:
        cut = cut[: cut.rfind(".") + 1]
        return cut
    return cut.rstrip() + "…"


def _answer_waiver_claim(
    answer: ComposedAnswer, classification: Classification, state: DerivedState
) -> None:
    """Address "I thought that was waived" as a claim, not just a status.

    A waiver is a recorded status (`waived`), so whether one exists is a fact
    the checklist read already answers. Restating the requirement's status
    without saying "no waiver is recorded" reads as not having heard the
    student.
    """

    if not classification.claims_waiver:
        return
    if "getOnboardingChecklist" not in state.available_reads:
        return
    waived = [
        str(step.get("title"))
        for step in state.completed_steps
        if str(step.get("status") or "") in {"waived", "not_applicable"}
    ]
    sentence = (
        f"On the waiver question: {_sentence_list(waived)} "
        f"{'is' if len(waived) == 1 else 'are'} recorded as waived — nothing else is."
        if waived
        else "On the waiver question: no waiver or exemption is recorded on your "
        "account, so these are all still required."
    )
    if sentence not in answer.message:
        answer.message = f"{answer.message} {sentence}".strip()
        answer.evidence_texts.append(
            "Recorded waivers: " + (", ".join(waived) if waived else "none")
        )


def _adviser_line(label: str, adviser: Mapping[str, Any]) -> str:
    parts = [f"{label}: {adviser.get('name') or 'unknown'}"]
    if adviser.get("title"):
        parts.append(str(adviser["title"]))
    if adviser.get("email"):
        parts.append(f"email {adviser['email']}")
    if adviser.get("officeLocation"):
        parts.append(f"office {adviser['officeLocation']}")
    status = str(adviser.get("employmentStatus") or "")
    if status and status != "active":
        parts.append(f"currently {status.replace('_', ' ')}")
        if adviser.get("leaveUntil"):
            parts.append(f"until {adviser['leaveUntil']}")
    if adviser.get("bookable") and adviser.get("nextOpenSlotAt"):
        parts.append(
            f"next bookable open slot {adviser['nextOpenSlotAt']} "
            "(a slot the student could book, not an appointment)"
        )
    elif adviser.get("bookable") is False:
        reason = str(adviser.get("availabilityReason") or "no open slots").replace("_", " ")
        parts.append(f"not bookable right now ({reason})")
    return "; ".join(parts)


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def institutional_evidence_lines(knowledge: Mapping[str, Any] | None) -> list[str]:
    """Approved institutional knowledge as evidence, with provenance kept.

    Every line names the document, its version and effective date and the
    office that owns it, so a rewrite can cite the rule rather than assert it,
    and the applicability verdict is stated as a fact of the student's record
    — the composer restates it, never re-derives it.
    """

    if not knowledge:
        return []
    lines: list[str] = []
    documents = [
        _mapping_or_empty(item) for item in _mapping_or_empty(knowledge).get("documents", [])
    ]
    guidance = str(knowledge.get("answerGuidance") or "")
    if documents:
        lines.append(
            "INSTITUTIONAL FACTS (approved, versioned documents) — when the question asks "
            "what a rule is, what happens when something is missed, whether something is "
            "allowed, what it costs, when a date falls, or who handles a step, answer from "
            "these first and use the record facts to say where the student stands."
            + (f" {guidance}" if guidance else "")
        )
    if not documents:
        lines.append(
            "Institutional knowledge search: no approved policy or calendar entry matched "
            "this question. Do not state a rule, date or amount that is not in these facts; "
            "say the official answer comes from enrollment support."
        )
    facets = _mapping_or_empty(knowledge.get("studentFacets"))
    basis = _mapping_or_empty(facets.get("basis"))
    if basis:
        lines.append(
            "Student record used to check applicability: "
            + "; ".join(f"{key.replace('_', ' ')} — {value}" for key, value in basis.items())
        )
    for document in documents[:4]:
        owner = _mapping_or_empty(document.get("owner"))
        owner_text = str(owner.get("name") or document.get("owner") or "the owning office")
        if owner.get("location"):
            owner_text += f", {owner['location']}"
        if owner.get("email"):
            owner_text += f", {owner['email']}"
        if owner.get("hours"):
            owner_text += f", hours {owner['hours']}"
        applicability = _mapping_or_empty(document.get("applicability"))
        verdict = str(applicability.get("verdict") or "unknown")
        verdict_text = {
            "applies": "APPLIES to this student",
            "does_not_apply": "DOES NOT APPLY to this student",
        }.get(verdict, "applicability to this student could not be determined from the record")
        if applicability.get("basis") and verdict != "applies":
            verdict_text += f" ({applicability['basis']})"
        lines.append(
            f"Institutional policy '{document.get('title')}' (code {document.get('code')}, "
            f"version {document.get('version')}, effective from {document.get('effectiveFrom')}"
            + (f" until {document['effectiveUntil']}" if document.get("effectiveUntil") else "")
            + f"; owned by {owner_text}); {verdict_text}. Summary: {document.get('summary')}"
        )
        situation = _mapping_or_empty(document.get("situationSection"))
        if situation.get("text"):
            lines.append(
                f"  Policy text for THIS student ({situation.get('why')}) — "
                f"{document.get('title')} > {situation.get('heading')}: {situation.get('text')}"
            )
        for section in [_mapping_or_empty(item) for item in document.get("sections", [])][:2]:
            if section.get("highlight"):
                lines.append(
                    f"  Key line — {document.get('title')} > {section.get('heading')}: "
                    f"{section.get('highlight')}"
                )
            lines.append(
                f"  Policy text — {document.get('title')} > {section.get('heading')}: "
                f"{section.get('text')}"
            )
    for entry in [_mapping_or_empty(item) for item in knowledge.get("calendar", [])][:6]:
        when = str(entry.get("startsOn") or "")
        if entry.get("endsOn"):
            when += f" to {entry['endsOn']}"
        if entry.get("startsAt"):
            when += f" at {entry['startsAt']}"
        lines.append(
            f"Academic calendar ({entry.get('term') or 'all terms'}): "
            f"{entry.get('label')} — {when}"
            + (f" ({entry.get('relativeToToday')})" if entry.get("relativeToToday") else "")
            + (f"; owner {entry.get('ownerOffice')}" if entry.get("ownerOffice") else "")
        )
    for office in [_mapping_or_empty(item) for item in knowledge.get("offices", [])][:3]:
        lines.append(
            f"Office {office.get('name')}: {office.get('location')}"
            + (f"; email {office.get('email')}" if office.get("email") else "")
            + (f"; hours {office.get('hours')}" if office.get("hours") else "")
            + (
                "; the platform holds no staff records for this office"
                if office.get("known") and not office.get("staffRecordsInPlatform")
                else ""
            )
        )
    if documents:
        lines.append(
            "Institutional facts above come from approved, versioned documents; cite the "
            "document by name when stating a rule, quote dates and amounts exactly as "
            "written, and never extend a rule beyond what its text says."
        )
    return lines


def build_evidence_bundle(state: DerivedState) -> list[str]:
    """Every fact the composer may use, rendered once as plain text.

    Institutional facts come first: the composer sees a bounded window of
    evidence, and when a question asks what a rule is or what follows from
    it, the rule must not be the part that falls off the end.
    """

    lines: list[str] = []
    lines.extend(institutional_evidence_lines(state.institution_knowledge))
    for step in state.remaining_steps:
        note = (
            " — a payment for this is already submitted and pending; the "
            "student must NOT be told to pay again"
            if step.get("processingPending")
            else ""
        )
        lines.append(
            f"Open checklist step: {step.get('title')} (status {step.get('status')}){note}"
        )
    for step in state.completed_steps:
        lines.append(f"Completed checklist step: {step.get('title')}")
    for document in state.document_states:
        decision_note = document.get("decisionNote")
        reason: str = f" — reviewer note: {decision_note}" if decision_note else ""
        if document["submissionState"] == "rejected" and not decision_note:
            reason = " — no reason is recorded on the decision"
        lines.append(
            f"Document {document['title']}: {document['submissionState'].replace('_', ' ')}{reason}"
        )
    # "Due 12 June" reads as future tense whatever today is, so an answer built
    # from bare dates quietly loses the fact that a date has already passed.
    # The bucket is computed in the read; state it in words.
    overdue = [item for item in state.deadlines if item.get("bucket") == "overdue"]
    for deadline in state.deadlines:
        bucket = str(deadline.get("bucket") or "")
        note = {
            "overdue": " — this date has already PASSED; it is OVERDUE",
            "this_week": " — due within the next seven days",
        }.get(bucket, "")
        lines.append(f"Deadline: {deadline.get('title')} due {deadline.get('dueAt')}{note}")
    if overdue:
        lines.append(
            f"{len(overdue)} deadline(s) are already overdue: "
            + "; ".join(str(item.get("title")) for item in overdue)
            + ". An answer about what is outstanding must say these are past due."
        )
    # Hold vocabulary is part of the truth: the platform operates no registrar
    # hold system, so "you have a hold" is never a correct sentence unless an
    # official hold is actually on record.
    if "getEnrollmentHolds" in state.available_reads:
        if state.official_holds:
            for hold in state.official_holds:
                lines.append(f"Official hold on record: {hold.get('title')}")
        else:
            lines.append(
                "Official registrar holds: none. The university operates no "
                "registrar hold system, so nothing on this record is a 'hold' — "
                "open items are enrollment blockers, and calling them holds "
                "would be wrong."
            )
    for blocker in state.derived_blockers:
        lines.append(
            f"Blocker: {blocker.get('title')} — cleared by: {blocker.get('clearingAction')}"
        )
    for gate in state.registration_gates:
        lines.append(f"Registration gate (open): {gate.get('title')}")
    aid = state.financial_aid
    if aid:
        for award in aid.get("awards", []):
            offered = award.get("offeredAmountCents")
            accepted = award.get("acceptedAmountCents")
            amounts = (
                f", offered {_usd(offered)}, accepted {_usd(accepted)}"
                if offered is not None or accepted is not None
                else " (award amounts were not included in this read — do not state one)"
            )
            lines.append(
                f"Aid award {award.get('name')} ({award.get('type')}): status "
                f"{award.get('status')}{amounts}"
            )
        for requirement in aid.get("requiredDocuments", []):
            lines.append(
                f"Aid requirement {requirement.get('title')}: status {requirement.get('status')}"
                + (f", due {requirement['dueAt']}" if requirement.get("dueAt") else "")
            )
        disbursements = aid.get("disbursements")
        if disbursements:
            open_gates = [
                gate for gate in disbursements.get("gates", []) if not gate.get("satisfied")
            ]
            if open_gates:
                lines.append(
                    "Aid disbursement is held open by: "
                    + "; ".join(str(gate.get("title")) for gate in open_gates)
                    + ". Nothing else gates disbursement."
                )
            else:
                lines.append(
                    "Every aid condition for disbursement is satisfied — no aid "
                    "requirement holds it, and enrollment checklist items do "
                    "not gate disbursement."
                )
            lines.append(
                "No disbursement schedule or payout date is tracked in the "
                "record; only the financial aid office can confirm dates."
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
        balance = account.get("remainingBalanceCents")
        if isinstance(balance, int) and balance < 0:
            lines.append(
                "The remaining balance is NEGATIVE: the account is in credit — "
                "aid and payments exceed charges, which normally means money "
                "back to the student. No refund schedule or date exists in the "
                "record."
            )
        enrolled_plan = account.get("enrolledPaymentPlan")
        if isinstance(enrolled_plan, Mapping) and enrolled_plan.get("name"):
            lines.append(
                f"Enrolled payment plan: {enrolled_plan['name']} — "
                f"{enrolled_plan.get('installmentCount')} installments of "
                f"{_usd(enrolled_plan.get('installmentAmountCents'))}"
            )
    deposit = deposit_state(state)
    if deposit is not None:
        if not deposit.get("known"):
            lines.append(
                "Deposit payment state: NOT READABLE this turn. Do not say the "
                "deposit is paid or unpaid — say it could not be checked."
            )
        elif deposit.get("pending"):
            lines.append(
                "Deposit payment: submitted and pending — it has not posted yet, "
                "so the deposit does not count as paid until it clears. The "
                "student must NOT be told to pay again."
            )
        elif deposit.get("paid"):
            lines.append(
                "Deposit paid: yes — a succeeded enrollment-deposit payment is on "
                "the record"
                + (f" ({deposit['paidAt']})" if deposit.get("paidAt") else "")
                + ". Nothing about the deposit is outstanding."
            )
        else:
            lines.append(
                "Deposit paid: no — no succeeded enrollment-deposit payment is on the record."
            )
    if state.enrollment:
        admission = _mapping(state.enrollment.get("admission"))
        for key, label in (
            ("offerStatus", "Admission offer status"),
            ("programName", "Program"),
            ("termName", "Starting term"),
            ("campusName", "Campus"),
            ("responseDeadline", "Offer response deadline"),
        ):
            if admission.get(key):
                lines.append(f"{label}: {admission[key]}")
        journey = _mapping(state.enrollment.get("journey"))
        if journey.get("completionPercent") is not None:
            lines.append(f"Enrollment completion: {journey['completionPercent']}%")
        if journey.get("status"):
            lines.append(f"Enrollment journey status: {journey['status']}")
        student = _mapping(state.enrollment.get("student"))
        if student.get("classYear"):
            lines.append(f"Class year: {student['classYear']}")
        onboarding = _mapping(state.enrollment.get("onboarding"))
        if onboarding.get("status"):
            lines.append(
                f"Onboarding status: {onboarding['status']}"
                + (
                    f", current step {onboarding['currentStep']}"
                    if onboarding.get("currentStep")
                    else ""
                )
            )
    if state.onboarding_responses:
        responses = state.onboarding_responses
        for key, label in (
            ("mailingAddress", "Mailing address on file"),
            ("citizenshipStatus", "Citizenship status"),
            ("residencyStatus", "Residency status"),
            ("residencyVerificationPath", "Residency verification path"),
            ("accommodationInterest", "Accommodation interest"),
            ("insuranceInterest", "Insurance interest"),
            ("depositChoice", "Deposit choice recorded at onboarding"),
            ("socialComfort", "Social comfort answer"),
        ):
            if responses.get(key):
                lines.append(f"{label}: {responses[key]}")
        for contact in responses.get("emergencyContacts") or []:
            lines.append(
                f"Emergency contact: {contact.get('name')} "
                f"({contact.get('relationship') or 'relationship not recorded'})"
            )
        for permission in responses.get("familyPermissions") or []:
            lines.append(
                f"Family permission granted to {permission.get('name')}: "
                + (
                    ", ".join(str(scope) for scope in permission.get("scopes") or [])
                    or "no scopes recorded"
                )
            )
        for key, label in (
            ("campusInterests", "Campus interests"),
            ("firstMonthGoals", "First-month goals"),
            ("supportNeeds", "Support needs"),
        ):
            values = responses.get(key) or []
            if values:
                lines.append(f"{label}: {', '.join(str(value) for value in values)}")
        signature = _mapping(responses.get("signature"))
        if signature:
            lines.append(
                "Enrollment signature: "
                + (
                    f"signed by {signature.get('fullName')}"
                    if signature.get("recorded")
                    else "not signed"
                )
            )
    if state.academic_standing:
        sap = _mapping(state.academic_standing.get("satisfactoryAcademicProgress"))
        for key, label in (
            ("status", "Satisfactory academic progress status"),
            ("cumulativeGpa", "Cumulative GPA"),
            ("minimumGpa", "Minimum GPA required"),
            ("completionRatePercent", "Completion rate percent"),
            ("minimumCompletionRatePercent", "Minimum completion rate percent"),
            ("attemptedCredits", "Attempted credits"),
            ("maximumAttemptedCredits", "Maximum attempted credits"),
        ):
            if sap.get(key) is not None:
                lines.append(f"{label}: {sap[key]}")
        credits = _mapping(state.academic_standing.get("credits"))
        for key, label in (
            ("completedCredits", "Completed credits"),
            ("exemptedCredits", "Exempted credits"),
            ("requiredCredits", "Credits required for the degree"),
        ):
            if credits.get(key) is not None:
                lines.append(f"{label}: {credits[key]}")
    if state.support_requests:
        for item in state.support_requests.get("items") or []:
            latest = _mapping(item.get("latestMessage"))
            lines.append(
                f"Support request “{item.get('subject') or item.get('topicCode')}”: status "
                f"{item.get('status')}"
                + (
                    f"; latest reply from {latest.get('authorType')}: {latest.get('body')}"
                    if latest.get("body")
                    else "; no replies yet"
                )
            )
    if state.housing:
        housing_status = str(state.housing.get("requirementStatus") or "unknown")
        blocked_note = (
            " — blocked means earlier checklist items gate it; the missing "
            "preference itself is NOT the cause"
            if housing_status == "blocked"
            else ""
        )
        lines.append(
            f"Housing preference: {state.housing.get('preference') or 'not selected'}; "
            f"requirement status: {housing_status}{blocked_note}"
        )
        for key, label in (
            ("residenceOption", "Residence hall preference"),
            ("roomType", "Room type preference"),
        ):
            if state.housing.get(key):
                lines.append(f"{label}: {state.housing[key]}")
        room = _mapping(state.housing.get("roomAssignment"))
        if room and room.get("tracked") is False:
            lines.append(
                "Room assignment: the platform tracks NO room assignment for any "
                "student. A preference is not an assignment — never say the "
                "student is in, or has been assigned, a room, hall, or building."
            )
    eligibility = state.housing_eligibility
    if eligibility:
        label = {
            "eligible_now": "The housing step is open for you now.",
            "already_completed": "The housing step is already complete.",
            "blocked": "The housing step is blocked by earlier checklist items.",
            "no_housing_step": "No housing step is on your checklist.",
        }.get(str(eligibility.get("eligibility")), "Housing step state unknown.")
        lines.append(f"Housing eligibility: {label}")
        open_gates = [gate for gate in eligibility.get("gates", []) if not gate.get("satisfied")]
        for gate in open_gates:
            lines.append(
                f"Housing gate (open): {gate.get('title')} — cleared by: "
                f"{gate.get('clearingAction')}"
            )
        if str(eligibility.get("eligibility")) == "blocked" and open_gates:
            lines.append(
                "These are the only items on the record blocking the housing step; "
                "this list is complete."
            )
    if state.aid_support:
        for option in state.aid_support.get("options", []):
            lines.append(f"Financial-aid support route: {option.get('label')}")
        support = state.aid_support.get("support") or {}
        if support.get("email"):
            lines.append(f"Support email: {support['email']}")
        if support.get("phone"):
            lines.append(f"Support phone: {support['phone']}")
        if support.get("hours"):
            lines.append(f"Support hours: {support['hours']}")
    if state.housing_options:
        for residence in state.housing_options.get("residences", []):
            lines.append(f"Housing option: {residence.get('name')}")
    if state.advising is not None:
        primary = state.advising.get("primaryAdviser")
        if isinstance(primary, Mapping):
            lines.append(_adviser_line("Primary adviser", primary))
        else:
            lines.append("Primary adviser: none is currently assigned")
        for adviser in state.advising.get("advisers", []):
            if not isinstance(adviser, Mapping):
                continue
            if isinstance(primary, Mapping) and adviser.get("email") == primary.get("email"):
                continue
            lines.append(_adviser_line(str(adviser.get("role") or "Adviser").capitalize(), adviser))
        for gap in state.advising.get("gaps", []):
            message = _mapping_or_empty(gap).get("message")
            if message:
                lines.append(f"Advising gap: {message}")
    if state.appointments is not None:
        appointment_items = state.appointments.get("items", [])
        if not appointment_items:
            lines.append("Appointments: none on record (nothing booked, nothing past)")
        for appointment in appointment_items:
            staff = appointment.get("with") if isinstance(appointment.get("with"), Mapping) else {}
            who = str(staff.get("name") or "").strip()
            role = str(staff.get("title") or staff.get("component") or "").strip()
            with_phrase = f" with {who}" + (f" ({role})" if role else "") if who else ""
            status = str(appointment.get("status") or "")
            status_note = {
                "no_show": " — the student did not attend (no-show)",
                "cancelled": " — cancelled",
                "completed": " — completed",
                "scheduled": " — scheduled, still to come",
            }.get(status, f" ({status})")
            lines.append(
                f"Appointment: {appointment.get('label') or appointment.get('type')!s}"
                f"{with_phrase} at {appointment.get('startsAt')}{status_note}"
            )
        upcoming = state.appointments.get("upcoming") or []
        if appointment_items and not upcoming:
            lines.append(
                "No upcoming appointment is booked; an adviser's open slot is a bookable "
                "time, not an appointment"
            )
    if state.academics:
        if state.academics.get("selectedProgram"):
            lines.append(
                f"Academic program: {state.academics['selectedProgram']} "
                f"({state.academics.get('degree') or 'degree not recorded'})"
            )
        for course in state.academics.get("plan", []):
            missing = course.get("missingPrerequisites") or []
            lines.append(
                f"Planned course {course.get('code')} {course.get('title')}: "
                f"status {course.get('status')}, recommended term "
                f"{course.get('recommendedTerm')}"
                + (f", missing prerequisites: {', '.join(missing)}" if missing else "")
            )
        for exemption in state.academics.get("suggestedExemptions", []):
            lines.append(f"Suggested course exemption: {exemption}")
    if state.campus_life:
        for event in (
            state.campus_life.get("events") or state.campus_life.get("upcomingEvents") or []
        ):
            lines.append(
                f"Campus event: {event.get('title')} at {event.get('location')} "
                f"on {event.get('startsAt')}"
            )
        for club in state.campus_life.get("clubs", []):
            description = str(club.get("description") or "").strip()
            activity = str(club.get("nextActivity") or "").strip()
            lines.append(
                f"Campus club: {club.get('name')} ({club.get('category')})"
                + (f" — {description[:160]}" if description else "")
                + (f"; next: {activity[:80]}" if activity else "")
            )
    if state.campus_life:
        for event in state.campus_life.get("myRegistrations", []):
            lines.append(
                f"Registered for campus event: {event.get('title')} on {event.get('startsAt')} "
                f"(registration {event.get('registrationStatus')})"
            )
    if state.messages:
        lines.append(f"Unread messages: {state.messages.get('unreadCount')}")
        for entry in state.messages.get("latest", []):
            if entry.get("subject"):
                sender = entry.get("senderName")
                body = str(entry.get("body") or "").strip()
                lines.append(
                    f"Message: {entry['subject']}"
                    + (f" from {sender}" if sender else "")
                    + (" (unread)" if entry.get("unread") else " (read)")
                    + (f" — {body}" if body else "")
                )
    if state.profile:
        for key, label in (
            ("fullName", "Legal name on record"),
            ("preferredName", "Preferred name"),
            ("pronouns", "Pronouns on record"),
            ("email", "Account email"),
            ("mobilePhone", "Mobile phone on record"),
            ("communicationPreference", "Preferred contact channel"),
        ):
            if state.profile.get(key):
                lines.append(f"{label}: {state.profile[key]}")
        if state.profile.get("emailVerified") is not None:
            lines.append(
                "Email verification: "
                + ("verified" if state.profile["emailVerified"] else "not verified yet")
            )
    if state.priority:
        lines.append(f"Priority action: {state.priority['title']} — {state.priority['reason']}")
    # Failed reads are evidence, not silence: the composer must know which
    # domains it cannot speak for this turn. (A failed read is different from
    # an empty one — an empty read appears above with its zero records.)
    for item in state.unavailable_data:
        label = _SOURCE_LABELS.get(str(item.get("source")), str(item.get("source")))
        lines.append(
            f"UNAVAILABLE THIS TURN: {label} could not be read "
            f"({str(item.get('reason') or 'read error').replace('_', ' ')}). "
            f"Do not state {label} as verified — say it couldn't be checked "
            "right now."
        )
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


def _compose_ack(
    _classification: Classification, _state: DerivedState, preferred_name: str | None
) -> ComposedAnswer:
    """Gratitude/acknowledgement/closing: a short social reply, no reads, no
    status report, no next-steps lecture."""

    message = "Any time! I'm here whenever you have another enrollment question."
    return ComposedAnswer(message=message, blocks=[text_block(message)])


def _compose_identity(
    _classification: Classification, _state: DerivedState, _name: str | None
) -> ComposedAnswer:
    """Honest, deterministic identity: never role-play a human."""

    message = (
        "No — I'm Edward, Audentra's AI enrollment assistant, not a person. "
        "I answer from your live university record, and when something needs "
        "a human, I'll point you to the right office."
    )
    return ComposedAnswer(message=message, blocks=[text_block(message)])


def _compose_capabilities(
    _classification: Classification, _state: DerivedState, _name: str | None
) -> ComposedAnswer:
    return ComposedAnswer(message=_CAPABILITY_MESSAGE, blocks=[text_block(_CAPABILITY_MESSAGE)])


def step_action_row(step: JsonDict) -> JsonDict:
    """One checklist step as an action row.

    A step whose payment is already processing is not the student's move.
    Rendering it as a plain "Pay the enrollment deposit" row — directly under
    a sentence saying no new payment is needed — is how a next-steps list
    invites a second payment.
    """

    title = str(step.get("title"))
    pending = bool(step.get("processingPending"))
    return {
        "text": (
            f"{title} (a payment is already pending — no action needed)" if pending else title
        ),
        "href": step.get("href"),
        "owner": "university" if pending else "student",
    }


def step_label(step: JsonDict) -> str:
    return str(step_action_row(step)["text"])


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
        steps.append(step_action_row(step))
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
    if _read_failed(state, "getOnboardingChecklist"):
        return _unavailable_answer(state, "getOnboardingChecklist")
    if classification.request_type == "completed_steps":
        if not state.completed_steps:
            message = "You haven't completed any checklist steps yet."
            return ComposedAnswer(
                message=message, blocks=[text_block(message)], evidence_texts=evidence
            )
        if len(state.completed_steps) == 1:
            message = (
                f"You've completed one checklist step so far: "
                f"{_join_titles(state.completed_steps)}."
            )
        else:
            message = (
                f"You've completed {_count(len(state.completed_steps), 'checklist step')} "
                "so far — here they are:"
            )
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

    # The list itself renders once, below; the prose answers the question and
    # surfaces what changes how the student should read that list.
    pending = [step for step in state.remaining_steps if step.get("processingPending")]
    # A returned submission is the student's move again — the one step that
    # must be called out by name, because "still open" undersells it.
    returned = [step for step in state.remaining_steps if str(step.get("status")) == "rejected"]
    if len(state.remaining_steps) == 1:
        message = (
            f"One checklist step still needs attention: {step_label(state.remaining_steps[0])}."
        )
        if returned:
            message += " Your earlier submission was returned, so it needs a new upload."
    else:
        message = f"{_count(len(state.remaining_steps), 'checklist step')} still need attention"
        if pending:
            message += (
                f" — though {_join_titles(pending)} already "
                f"{'has' if len(pending) == 1 else 'have'} a payment processing, "
                "so no action is needed there"
            )
        message += "."
        if returned:
            message += (
                f" Note: {_join_titles(returned)} was returned and needs your attention again."
            )
        message += " Here's the list, in order:"
    block = next_steps_block(
        [step_action_row(step) for step in state.remaining_steps[:6]],
        title="Still to do",
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _join_document_nouns(items: list[JsonDict]) -> str:
    nouns = [_document_noun(str(item.get("title") or "")) for item in items]
    if len(nouns) == 1:
        return nouns[0]
    return ", ".join(nouns[:-1]) + " and " + nouns[-1]


def _compose_documents(
    classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if _read_failed(state, "getDocumentStatuses"):
        # The upload record is the half that failed; the checklist may still
        # be readable, so name the open requirements without claiming
        # anything about what has or hasn't been uploaded.
        open_documents = [step for step in state.remaining_steps if step.get("documentCategory")]
        message = (
            "I couldn't verify your document uploads right now, so I can't "
            "confirm what has or hasn't been received this moment."
        )
        if open_documents:
            message += (
                " Your checklist still lists these document requirements as "
                f"open: {_join_titles(open_documents)} — but whether files "
                "are already in review couldn't be checked. Try again shortly."
            )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    if _read_failed(state, "getOnboardingChecklist"):
        return _unavailable_answer(state, "getOnboardingChecklist")
    states = state.document_states
    if not states:
        message = "No document requirements are on your checklist right now."
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    if classification.request_type == "missing_documents":
        # A returned upload is the student's move as much as one that was
        # never sent — and it is the one they are most likely to think is
        # done. Counting only never-submitted documents silently drops it.
        returned = [item for item in states if item["submissionState"] == "needs_resubmission"]
        outstanding = [*state.missing_documents, *returned]
        if not outstanding:
            message = "Every required document is in — nothing is waiting on an upload from you."
            return ComposedAnswer(
                message=message, blocks=[text_block(message)], evidence_texts=evidence
            )
        sentences: list[str] = []
        if returned:
            sentences.append(
                f"Yes — your {_join_document_nouns(returned)} "
                f"{'was' if len(returned) == 1 else 'were'} returned and "
                f"{'needs' if len(returned) == 1 else 'need'} a fresh upload."
            )
        if state.missing_documents:
            if len(state.missing_documents) == 1 and not returned:
                sentences.append(
                    "One document still needs to be uploaded: your "
                    f"{_document_noun(state.missing_documents[0]['title'])}."
                )
            else:
                sentences.append(
                    f"{_count(len(state.missing_documents), 'document')} "
                    f"{'has' if len(state.missing_documents) == 1 else 'have'} not been "
                    "sent at all: "
                    f"{_join_document_nouns(state.missing_documents)}."
                )
        message = " ".join(sentences) + " Each step below opens the right page:"
        block = next_steps_block(
            [
                {
                    "text": (
                        f"Re-upload your {_document_noun(item['title'])}"
                        if item["submissionState"] == "needs_resubmission"
                        else f"Upload your {_document_noun(item['title'])}"
                    ),
                    "href": item.get("href"),
                    "owner": "student",
                }
                for item in outstanding
            ],
            title="Documents waiting on you",
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message), block], evidence_texts=evidence
        )
    state_prose = {
        "accepted": "has been accepted",
        "under_review": "is under review",
        "not_submitted": "is not submitted yet",
        "needs_attention": "needs your attention before review can continue",
    }
    # One requirement is a sentence, not a table.
    if len(states) == 1:
        only = states[0]
        message = (
            f"You have one required document — your {_document_noun(only['title'])} — "
            f"and it {state_prose.get(only['submissionState'], only['submissionState'])}."
        )
        blocks: list[JsonDict] = [text_block(message)]
        if only["submissionState"] in {"not_submitted", "needs_attention"} and only.get("href"):
            blocks.append(
                next_steps_block(
                    [
                        {
                            "text": f"Upload your {_document_noun(only['title'])}",
                            "href": only.get("href"),
                            "owner": "student",
                        }
                    ]
                )
            )
        return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)
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
    # Synthesis, not repetition: the table below carries the per-document
    # detail once; the prose gives the shape of the situation and names only
    # what needs the student's eyes.
    accepted = [item for item in states if item["submissionState"] == "accepted"]
    under_review = [item for item in states if item["submissionState"] == "under_review"]
    needs_attention = [item for item in states if item["submissionState"] == "needs_attention"]
    missing = state.missing_documents
    summary_parts = []
    if accepted:
        summary_parts.append(f"{len(accepted)} accepted")
    if under_review:
        summary_parts.append(f"{len(under_review)} under review")
    if missing:
        summary_parts.append(f"{len(missing)} still to submit")
    message = f"Of your {_count(len(states), 'required document')}"
    message += f", {', '.join(summary_parts)}." if summary_parts else ", none have moved yet."
    if needs_attention:
        message += (
            f" {_join_titles(needs_attention)} "
            f"{'needs' if len(needs_attention) == 1 else 'need'} your attention "
            "before review can continue."
        )
    message += " The table shows each one:"
    # When the student named a specific document, answer about that document
    # first so the direct question gets a direct sentence.
    reference = classification.requirement_reference
    if reference:
        matched = [item for item in states if reference in item["title"].lower()]
        if len(matched) == 1:
            item = matched[0]
            message = (
                f"Your {reference} document "
                f"{state_prose.get(item['submissionState'], item['submissionState'])}"
                f" (checklist item: {item['title']}). {message}"
            )
    block = table_block(
        [{"key": "document", "label": "Document"}, {"key": "status", "label": "Status"}],
        rows,
        caption="Document status",
        row_hrefs=[item.get("href") for item in states],
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_holds(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if _read_failed(state, "getEnrollmentHolds"):
        return _unavailable_answer(state, "getEnrollmentHolds")
    if not state.official_holds and not state.derived_blockers:
        message = (
            "There's no official hold on your record — the university doesn't "
            "operate a registrar hold system — and nothing is blocking you "
            "right now."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    # The block below names each item with its clearing action; the prose
    # answers the question and splits the work by who moves next.
    blocking = [*state.official_holds, *state.derived_blockers]
    waiting = [item for item in blocking if item.get("owner") == "university"]
    yours = [item for item in blocking if item.get("owner") != "university"]
    split = ""
    if waiting and yours:
        split = (
            f" {_count(len(yours), 'needs action from you', 'need action from you')}; "
            f"{len(waiting)} {'is' if len(waiting) == 1 else 'are'} waiting on the university."
        )
    elif waiting:
        split = " All of it is waiting on university review — no action needed from you."
    if state.official_holds:
        parts = [f"{_count(len(state.official_holds), 'hold')}"]
        if state.derived_blockers:
            parts.append(f"{_count(len(state.derived_blockers), 'item')} blocking progress")
        message = f"You have {' and '.join(parts)}.{split} Each one below shows what clears it:"
    else:
        message = (
            "No official hold exists — the university operates no registrar "
            f"hold system. What you do have is "
            f"{_count(len(state.derived_blockers), 'item')} blocking progress."
            f"{split} Each one below shows what clears it:"
        )
    # "Which of my requirements are blocking and which aren't?" is a real
    # question, and an answer that lists only the blocking set leaves the
    # student thinking the rest is done. Name the open-but-not-blocking steps.
    # Match on the canonical gate code, not on the title: the deposit blocker
    # is titled "Enrollment deposit not posted" while its checklist step is
    # "Pay the enrollment deposit", so a title comparison lists the same gate
    # as both blocking and not blocking.
    blocking_codes = {str(item.get("code") or "") for item in blocking}
    non_blocking = [
        str(step.get("title"))
        for step in state.remaining_steps
        if requirement_gate_code(step.get("code")) not in blocking_codes
    ]
    if non_blocking:
        listed = _sentence_list(non_blocking[:3])
        verb = "is" if len(non_blocking[:3]) == 1 else "are"
        message = (
            f"{message} Separately, {listed} {verb} still open but not blocking anything else."
        )
        evidence.append("Open but not blocking: " + ", ".join(non_blocking[:3]) + ".")
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
    if _read_failed(state, "getStudentDeadlines"):
        return _unavailable_answer(state, "getStudentDeadlines")
    if not state.deadlines:
        message = "Nothing on your record has an upcoming deadline."
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    overdue = [item for item in state.deadlines if item.get("bucket") == "overdue"]
    # One deadline is a sentence with the date, not a one-row table.
    if len(state.deadlines) == 1:
        only = state.deadlines[0]
        due = str(only.get("dueAt", ""))[:10]
        past = only.get("bucket") == "overdue"
        message = f"You have one deadline: {only.get('title')}, " + (
            f"which was due {due} and is now past due." if past else f"due {due}."
        )
        blocks: list[JsonDict] = [text_block(message)]
        if only.get("href"):
            blocks.append(
                next_steps_block(
                    [
                        {
                            "text": f"Take care of {only.get('title')}",
                            "href": only.get("href"),
                            "owner": "student",
                        }
                    ]
                )
            )
        return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)
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
    this_week = [item for item in state.deadlines if item.get("bucket") == "this_week"]
    if overdue:
        message = (
            f"{_count(len(overdue), 'deadline is', 'deadlines are')} already past due — "
            f"start with {_join_titles(overdue)}. The full list:"
        )
    else:
        message = f"You have {_count(len(state.deadlines), 'upcoming deadline')}"
        message += (
            f" — {len(this_week)} due within the week. Here they are:"
            if this_week
            else ", none of them urgent this week. Here they are:"
        )
    block = table_block(
        [
            {"key": "item", "label": "Item"},
            {"key": "due", "label": "Due"},
            {"key": "window", "label": "Window"},
        ],
        rows,
        caption="Your deadlines",
        row_hrefs=[deadline.get("href") for deadline in state.deadlines],
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
        elif len(open_requirements) <= 2:
            # Few causes: naming them is the direct answer.
            message = (
                f"Your aid is incomplete because {_join_titles(open_requirements)} "
                f"{'is' if len(open_requirements) == 1 else 'are'} still open."
            )
        else:
            message = (
                f"Your aid is incomplete because "
                f"{_count(len(open_requirements), 'requirement')} are still open — "
                "they're listed below with where to act."
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
                f"Your aid file lists {_count(len(awards), 'award')}, and "
                f"{_count(len(open_requirements), 'requirement')} still "
                f"{'needs' if len(open_requirements) == 1 else 'need'} attention — "
                "the details are below."
            )
        else:
            message = (
                f"Your aid file lists {_count(len(awards), 'award')}, "
                "and every requirement is satisfied."
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
                        "offered": (
                            _usd(award["offeredAmountCents"])
                            if award.get("offeredAmountCents") is not None
                            else "—"
                        ),
                        "accepted": (
                            _usd(award["acceptedAmountCents"])
                            if award.get("acceptedAmountCents") is not None
                            else "—"
                        ),
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
        message = (
            f"{_count(len(residences), 'housing option is', 'housing options are')} "
            "listed — here's each one:"
        )
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
    if str(requirement_status or "") == "blocked":
        gates = [
            gate
            for gate in (state.housing_eligibility or {}).get("gates", [])
            if not gate.get("satisfied")
        ]
        if gates:
            named = (
                _join_titles(gates)
                if len(gates) <= 2
                else f"{_count(len(gates), 'earlier checklist item')} (listed below)"
            )
            message = (
                "Housing is locked right now because of earlier checklist "
                f"items — {named} — not because of anything on "
                "the housing side itself. Clear those and the housing step "
                "opens for you to pick a preference."
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
                title="Clears housing",
            )
            return ComposedAnswer(
                message=message, blocks=[text_block(message), block], evidence_texts=evidence
            )
        message = (
            "The housing step is currently blocked by earlier items on your "
            "enrollment checklist — not by anything housing-specific. Your "
            "checklist shows what to clear first."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
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


def _compose_housing_eligibility(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    eligibility = state.housing_eligibility or {}
    kind = str(eligibility.get("eligibility") or "")
    if kind == "eligible_now":
        step = eligibility.get("housingStep") or {}
        message = (
            "Nothing is blocking the housing step — you can complete it now "
            "from your enrollment checklist."
        )
        blocks = [text_block(message)]
        if step.get("href"):
            blocks.append(
                next_steps_block(
                    [
                        {
                            "text": f"Complete {step.get('title') or 'the housing step'}",
                            "href": str(step["href"]),
                            "owner": "student",
                        }
                    ]
                )
            )
        return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)
    if kind == "already_completed":
        message = "Your housing step is already complete — nothing more is needed there."
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    if kind == "blocked":
        gates = [gate for gate in eligibility.get("gates", []) if not gate.get("satisfied")]
        if gates:
            named = _join_titles(gates) if len(gates) <= 2 else "listed below in order"
            message = (
                f"You can't act on housing yet: "
                f"{_count(len(gates), 'earlier item')} on your "
                f"checklist {'comes' if len(gates) == 1 else 'come'} first — {named}. "
                "These are the only items holding it back."
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
                title="Complete these first",
            )
            return ComposedAnswer(
                message=message, blocks=[text_block(message), block], evidence_texts=evidence
            )
        message = (
            "The housing step on your checklist is blocked by earlier steps. "
            "Your enrollment checklist shows the order to work through."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = (
        "No housing step is on your enrollment checklist right now, so there's "
        "nothing housing-related waiting on you. Housing assignments and "
        "application windows come from the housing office."
    )
    return ComposedAnswer(message=message, blocks=[text_block(message)], evidence_texts=evidence)


def _compose_aid_support(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    support = state.aid_support or {}
    options = support.get("options", [])
    message = (
        "The financial aid team can help directly — the fastest route is "
        "booking a financial-aid appointment."
    )
    blocks: list[JsonDict] = [text_block(message)]
    if options:
        blocks.append(
            next_steps_block(
                [
                    {"text": str(option.get("label")), "href": option.get("href")}
                    for option in options
                ],
                title="Financial aid help",
            )
        )
    return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_policy_lookup(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    """Institutional questions, answered from the approved knowledge corpus.

    The read carries provenance (code, version, effective date, owning office)
    and an applicability verdict computed from the student's own record. When
    the corpus is not wired in, or nothing matched, the honest refusal stands
    — inventing a rule or a date is the one failure a student acts on.
    """

    evidence = build_evidence_bundle(state)
    knowledge = state.institution_knowledge
    documents = [
        _mapping_or_empty(item) for item in _mapping_or_empty(knowledge).get("documents", [])
    ]
    if not knowledge or not documents:
        message = (
            "I couldn't find an approved policy or calendar entry that answers that, "
            "and I only answer institutional questions from approved sources. The "
            "enrollment support team can give you the official answer: open the Help "
            "page or book an appointment."
        )
        return ComposedAnswer(
            message=message,
            blocks=[
                text_block(message),
                next_steps_block(
                    [
                        {"text": "Ask enrollment support", "href": links.HELP},
                        {"text": "Book an appointment", "href": links.APPOINTMENTS},
                    ],
                    title="Get the official answer",
                ),
            ],
            evidence_texts=evidence,
        )
    primary = documents[0]
    sections = [_mapping_or_empty(item) for item in primary.get("sections", [])]
    lead = (
        str(sections[0].get("text") or primary.get("summary") or "")
        if sections
        else str(primary.get("summary") or "")
    )
    applicability = _mapping_or_empty(primary.get("applicability"))
    verdict = str(applicability.get("verdict") or "")
    applies_note = {
        "applies": " This applies to you.",
        "does_not_apply": " Based on your record, this rule does not apply to you.",
    }.get(verdict, "")
    owner = _mapping_or_empty(primary.get("owner"))
    owner_note = (
        f" It is owned by {owner.get('name')}"
        + (f" ({owner.get('location')})" if owner.get("location") else "")
        + "."
        if owner.get("name")
        else ""
    )
    message = (
        f"From {primary.get('title')} (version {primary.get('version')}, effective "
        f"{primary.get('effectiveFrom')}): {lead}{applies_note}{owner_note}"
    )
    blocks: list[JsonDict] = [text_block(message)]
    if len(documents) > 1:
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": (
                            f"{_mapping_or_empty(doc).get('title')}: "
                            f"{_mapping_or_empty(doc).get('summary')}"
                        )
                    }
                    for doc in documents[1:4]
                ],
                title="Related policies",
            )
        )
    calendar = [_mapping_or_empty(item) for item in knowledge.get("calendar", [])][:4]
    if calendar:
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": f"{entry.get('label')} — {entry.get('startsOn')}"
                        + (
                            f" ({entry.get('relativeToToday')})"
                            if entry.get("relativeToToday")
                            else ""
                        )
                    }
                    for entry in calendar
                ],
                title="Calendar dates",
            )
        )
    steps = []
    if owner.get("email"):
        steps.append(
            {"text": f"Contact {owner.get('shortName') or owner.get('name')}: {owner['email']}"}
        )
    steps.append({"text": "Ask enrollment support", "href": links.HELP})
    blocks.append(next_steps_block(steps, title="Who to ask"))
    return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_registration(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if _read_failed(state, "getRegistrationStatus", "getEnrollmentHolds", "getOnboardingChecklist"):
        message = (
            "I couldn't fully check registration eligibility right now — part "
            "of your record didn't load, so I can't say whether anything is "
            "blocking you this moment. Try again shortly rather than relying "
            "on a partial answer."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    gates = state.registration_gates
    if not gates:
        message = (
            "Nothing on your enrollment record is blocking registration. "
            "Exact registration windows come from the registrar."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    named = _join_titles(gates) if len(gates) <= 2 else "each one is below with what clears it"
    message = (
        f"{_count(len(gates), 'item')} on your record "
        f"{'is' if len(gates) == 1 else 'are'} blocking registration — {named}. "
        "Clearing them is what opens registration."
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
        if _read_failed(state, "getStudentAccountSummary"):
            message = (
                "I couldn't read your account right now, so I can't verify "
                "your balance or payment status this moment. Open Payments to "
                "check directly, or try again shortly."
            )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    remaining = account.get("remainingBalanceCents")
    message = (
        f"Your remaining balance is {_usd(remaining)}."
        if remaining is not None
        else "Your account has no computed balance yet."
    )
    if account.get("depositPaymentPending"):
        message += (
            " Your enrollment deposit payment has been submitted and is pending — "
            "it has not posted yet."
        )
    elif not account.get("depositPaid") and account.get("depositAmountCents"):
        message += (
            f" Your {_usd(account['depositAmountCents'])} enrollment deposit has not been paid yet."
        )
    return ComposedAnswer(message=message, blocks=[text_block(message)], evidence_texts=evidence)


def _compose_deposit_status(
    classification: Classification, state: DerivedState, name: str | None
) -> ComposedAnswer:
    """Answer the deposit question from the payment record itself.

    Three outcomes are genuinely different and none may be collapsed into
    another: posted, submitted-and-processing, and not paid. A fourth —
    the payment record could not be read — must say so rather than pick one.
    """

    evidence = build_evidence_bundle(state)
    deposit = deposit_state(state)
    if deposit is None or not deposit.get("known"):
        if _read_failed(state, "getStudentAccountSummary", "getEnrollmentState"):
            return _unavailable_answer(state, "getStudentAccountSummary")
        message = (
            "I couldn't read your payment record just now, so I can't confirm "
            "whether your deposit posted. Open Payments to check directly."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    amount = _usd(deposit.get("amountCents"))
    if deposit.get("paid"):
        paid_at = str(deposit.get("paidAt") or "")
        when = f" on {paid_at[:10]}" if paid_at else ""
        message = (
            f"Yes — your {amount} enrollment deposit is paid and posted{when}. "
            "The receipt is on your Payments page."
        )
    elif deposit.get("pending"):
        message = (
            f"Your {amount} enrollment deposit payment is submitted and still "
            "processing — it has not posted yet. No second payment is needed "
            "unless it fails."
        )
    else:
        due = str(deposit.get("dueAt") or "")
        by_when = f" It is due {due[:10]}." if due else ""
        message = (
            f"No — your {amount} enrollment deposit has not been paid yet.{by_when} "
            "You can pay it from the Payments page."
        )
    blocks = [text_block(message)]
    if not deposit.get("paid"):
        blocks.append(
            next_steps_block(
                [{"text": "Open Payments", "href": links.PAYMENTS, "owner": "student"}],
            )
        )
    return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_enrollment_state(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    """Where the student stands: admission, program placement, progress."""

    evidence = build_evidence_bundle(state)
    enrollment = state.enrollment
    if not enrollment:
        if _read_failed(state, "getEnrollmentState"):
            return _unavailable_answer(state, "getEnrollmentState")
        return _compose_checklist(_classification, state, _name)
    admission = enrollment.get("admission") or {}
    journey = enrollment.get("journey") or {}
    parts: list[str] = []
    offer_status = str(admission.get("offerStatus") or "")
    program = admission.get("programName")
    term = admission.get("termName")
    campus = admission.get("campusName")
    if offer_status and program:
        placement = f"your admission offer for {program}"
        if term:
            placement += f" starting {term}"
        if campus:
            placement += f" at {campus}"
        parts.append(
            f"You have accepted {placement}."
            if offer_status == "accepted"
            else f"Your status is “{offer_status.replace('_', ' ')}” for {placement}."
        )
    completion = journey.get("completionPercent")
    if isinstance(completion, int | float):
        parts.append(f"Your enrollment checklist is {int(completion)}% complete.")
    onboarding = enrollment.get("onboarding")
    if isinstance(onboarding, dict) and onboarding.get("status"):
        status = str(onboarding["status"])
        parts.append(
            "Your onboarding is complete."
            if status == "completed"
            else f"Onboarding is {status.replace('_', ' ')}"
            + (
                f" at the “{onboarding['currentStep']}” step."
                if onboarding.get("currentStep")
                else "."
            )
        )
    deposit = deposit_state(state)
    if deposit and deposit.get("known") and not deposit.get("paid"):
        parts.append(
            "Your enrollment deposit payment is submitted and still processing."
            if deposit.get("pending")
            else "Your enrollment deposit has not been paid yet."
        )
    if state.remaining_steps:
        named = (
            f": {_join_titles(state.remaining_steps)}" if len(state.remaining_steps) <= 3 else ""
        )
        parts.append(
            f"{_count(len(state.remaining_steps), 'checklist item is', 'checklist items are')} "
            f"still open{named}."
        )
    elif "getOnboardingChecklist" in state.available_reads:
        parts.append("Every checklist item is complete.")
    else:
        # The checklist was not read this turn. Saying so is the difference
        # between "nothing is outstanding" and "I did not look".
        parts.append("I haven't checked your open checklist items in this answer.")
    next_action = journey.get("nextAction") or {}
    message = " ".join(parts) or "I could not summarise your enrollment position."
    blocks = [text_block(message)]
    if next_action.get("label") and not _is_pending_deposit_action(next_action, state):
        blocks.append(
            next_steps_block(
                [
                    {
                        "text": str(next_action["label"]),
                        "href": str(next_action.get("href") or links.ENROLLMENT),
                        "owner": "student",
                    }
                ],
                title="Next up",
            )
        )
    return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _is_pending_deposit_action(action: Mapping[str, Any], state: DerivedState) -> bool:
    """Is this next-step row the deposit, while a payment is already pending?

    The dashboard's next action is computed from requirement status, which does
    not know a payment is in flight. Offering "Pay your enrollment deposit"
    beside "your payment is processing — no new payment is needed" invites the
    student to pay twice.
    """

    deposit = deposit_state(state)
    if not deposit or not deposit.get("pending"):
        return False
    label = str(action.get("label") or "").lower()
    href = str(action.get("href") or "").lower()
    return "deposit" in label or "deposit" in href or href.startswith("/payments")


def _compose_personal_information(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    """What the student told the university, read back to them."""

    evidence = build_evidence_bundle(state)
    responses = state.onboarding_responses
    if not responses:
        if _read_failed(state, "getOnboardingResponses"):
            return _unavailable_answer(state, "getOnboardingResponses")
        message = (
            "I couldn't read your onboarding answers just now. The Onboarding "
            "and Profile pages show what is on file."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    lines: list[dict[str, Any]] = []
    for label, value in (
        ("Mailing address", responses.get("mailingAddress")),
        ("Citizenship status", responses.get("citizenshipStatus")),
        ("Residency status", responses.get("residencyStatus")),
        ("Accommodation interest", responses.get("accommodationInterest")),
        ("Insurance interest", responses.get("insuranceInterest")),
    ):
        if value:
            lines.append({"text": f"{label}: {str(value).replace('_', ' ')}"})
    for contact in responses.get("emergencyContacts") or []:
        lines.append(
            {
                "text": "Emergency contact: "
                + ", ".join(
                    str(part) for part in (contact.get("name"), contact.get("relationship")) if part
                )
            }
        )
    message = (
        "Here is what your record holds from onboarding."
        if lines
        else "Your onboarding record has no personal details saved yet."
    )
    blocks = [text_block(message)]
    if lines:
        blocks.append(bullet_list_block(lines, title="On file"))
    return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_academic_standing(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    standing = state.academic_standing
    if not standing:
        if _read_failed(state, "getAcademicStanding"):
            return _unavailable_answer(state, "getAcademicStanding")
        message = (
            "I couldn't read your academic-progress record just now. The "
            "Financials page shows your satisfactory academic progress card."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    sap = standing.get("satisfactoryAcademicProgress") or {}
    gpa = sap.get("cumulativeGpa")
    minimum = sap.get("minimumGpa")
    parts: list[str] = []
    if gpa is not None:
        parts.append(
            f"Your cumulative GPA is {gpa}"
            + (f", against a {minimum} minimum." if minimum is not None else ".")
        )
    status = str(sap.get("status") or "")
    if status:
        parts.append(f"Your satisfactory academic progress status is “{status.replace('_', ' ')}”.")
    rate = sap.get("completionRatePercent")
    if rate is not None:
        parts.append(
            f"Your completion rate is {rate}%"
            + (
                f" against a {sap['minimumCompletionRatePercent']}% minimum."
                if sap.get("minimumCompletionRatePercent") is not None
                else "."
            )
        )
    message = " ".join(parts) or "Your academic-progress record has no values recorded."
    return ComposedAnswer(message=message, blocks=[text_block(message)], evidence_texts=evidence)


def _compose_support_requests(
    classification: Classification, state: DerivedState, name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    requests = state.support_requests
    if not requests:
        if _read_failed(state, "getStudentSupportRequests"):
            return _unavailable_answer(state, "getStudentSupportRequests")
        return _compose_support(classification, state, name)
    items = requests.get("items") or []
    if not items:
        message = (
            "You have no support conversations on record. You can start one "
            "from the Help page or book time from Appointments."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    open_count = requests.get("openCount") or 0
    message = f"You have {len(items)} support conversation(s) on record" + (
        f", {open_count} still open." if open_count else ", all resolved."
    )
    block = bullet_list_block(
        [
            {
                "text": f"{item.get('subject') or item.get('topicCode')} — "
                f"{str(item.get('status') or '').replace('_', ' ')}",
                "href": links.HELP,
            }
            for item in items[:5]
        ],
        title="Your support requests",
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_appointments(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if _read_failed(state, "getStudentAppointments"):
        return _unavailable_answer(state, "getStudentAppointments")
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
    elif reference == "housing_assignment_unavailable":
        message = (
            "I can't tell you that — Audentra doesn't hold room assignments, "
            "building allocations, or roommate matching, and there's no model "
            "here that predicts them. What I can show you is your housing "
            "step: whether it's open to you, and what has to clear first."
        )
    elif reference == "future_aid_unavailable":
        message = (
            "I can't predict or promise future financial aid or scholarship "
            "awards. Those are institutional decisions for the relevant award "
            "cycle. I can show the awards and requirements currently recorded "
            "for you."
        )
    elif reference and reference.endswith("write_unavailable"):
        destination, href = _WRITE_DESTINATIONS.get(
            reference, ("the matching portal page", links.ENROLLMENT)
        )
        message = (
            f"That isn't a change I can make from here — {destination} is where it "
            "happens. I can change your preferred name, your pronouns, your mobile "
            "number, and whether we contact you by email or text, and I can open a "
            "support request so a person picks something up."
        )
        return ComposedAnswer(
            message=message,
            blocks=[
                text_block(message),
                next_steps_block(
                    [{"text": f"Open {destination}", "href": href, "owner": "student"}],
                ),
            ],
        )
    else:
        message = (
            "I can't check that — it isn't part of your university record that "
            "I can see. I can answer questions about your enrollment, "
            "documents, deadlines, financial aid, housing, registration, "
            "account, and appointments."
        )
    return ComposedAnswer(message=message, blocks=[text_block(message)])


def _compose_academics(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if _read_failed(state, "getAcademicPlan"):
        return _unavailable_answer(state, "getAcademicPlan")
    academics = state.academics or {}
    plan = academics.get("plan", [])
    program = academics.get("selectedProgram") or ""
    if not plan:
        message = (
            f"No planned courses are on your academic plan yet"
            f"{f' for {program}' if program else ''}. "
            "The Academics page is where your plan appears once it is built."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    with_missing = [course for course in plan if course.get("missingPrerequisites")]
    message = (
        f"Your academic plan{f' for {program}' if program else ''} lists {len(plan)} course(s)."
    )
    if with_missing:
        message += (
            f" {len(with_missing)} of them still list missing prerequisites, shown in the table."
        )
    block = table_block(
        columns=[
            {"key": "code", "label": "Course"},
            {"key": "title", "label": "Title"},
            {"key": "term", "label": "Recommended term"},
            {"key": "status", "label": "Status"},
            {"key": "prerequisites", "label": "Missing prerequisites"},
        ],
        rows=[
            {
                "code": str(course.get("code") or ""),
                "title": str(course.get("title") or ""),
                "term": str(course.get("recommendedTerm") or ""),
                "status": str(course.get("status") or ""),
                "prerequisites": ", ".join(course.get("missingPrerequisites") or []) or "none",
            }
            for course in plan
        ],
        caption="Your planned courses",
    )
    return ComposedAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_campus_life(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if _read_failed(state, "getCampusLife"):
        return _unavailable_answer(state, "getCampusLife")
    campus = state.campus_life or {}
    events = campus.get("events") or campus.get("upcomingEvents") or []
    clubs = campus.get("clubs", [])
    if not events and not clubs:
        message = (
            "No campus events or clubs are published for you right now. "
            "The Campus life page is where new ones appear."
        )
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    # Prose gives the shape (how many, what kinds); each list renders once
    # below, with club entries linking to their portal pages.
    categories = sorted({str(club.get("category") or "").strip() for club in clubs} - {""})
    parts = []
    if clubs:
        spread = (
            f", covering {', '.join(categories[:-1])} and {categories[-1]}"
            if len(categories) > 1
            else ""
        )
        parts.append(f"{_count(len(clubs), 'club')} you can join{spread}")
    if events:
        parts.append(f"{_count(len(events), 'upcoming event')}")
    message = f"Campus life currently lists {' and '.join(parts)}. Here's a breakdown:"
    blocks = [text_block(message)]
    if clubs:
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": (
                            f"{club.get('name')} — "
                            f"{str(club.get('description') or club.get('category') or '')[:120]}"
                        ),
                        "href": club_page(club.get("id")) if club.get("id") else None,
                    }
                    for club in clubs
                ],
                title="Clubs you can join",
            )
        )
    if events:
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": f"{event.get('title')} — {event.get('startsAt')}"
                        + (f" at {event['location']}" if event.get("location") else "")
                    }
                    for event in events
                ],
                title="Upcoming events",
            )
        )
    return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)


# Destination → (human label, route, the derived state worth stating there).
# Routes come from `links`, which mirrors the portal's real app router, so a
# navigation answer can never invent a page.
_NAVIGATION_PAGES: dict[str, tuple[str, str]] = {
    "payments": ("the Payments page", links.PAYMENTS),
    "documents": ("the Documents page", links.DOCUMENTS),
    "financials": ("the Financials page", links.FINANCIALS),
    "appointments": ("the Appointments page", links.APPOINTMENTS),
    "messages": ("the Messages page", links.MESSAGES),
    "campus_life": ("the Campus life page", links.CAMPUS_LIFE),
    "classrooms": ("the Classrooms page", links.CLASSROOMS),
    "profile": ("the Profile page", links.PROFILE),
    "help": ("the Help page", links.HELP),
    "enrollment": ("the Enrollment page", links.ENROLLMENT),
    "housing": ("the Enrollment page", links.ENROLLMENT),
    "dashboard": ("the Dashboard", links.DASHBOARD),
}

# What each destination is for, so the answer says why that page and not just
# which one.
_NAVIGATION_PURPOSE: dict[str, str] = {
    "payments": "make and review payments, including the enrollment deposit",
    "documents": "upload a required document and see what happened to each upload",
    "financials": "see your balance, aid awards, and what you owe",
    "appointments": "see and book advising appointments",
    "messages": "read messages from the university",
    "campus_life": "browse clubs and campus events",
    "classrooms": "see your courses",
    "profile": "review the details on your record",
    "help": "reach enrollment support",
    "enrollment": "see your full enrollment checklist and open each step",
    "housing": "open the housing step on your checklist",
    "dashboard": "see the summary of where you are",
}


def _compose_portal_navigation(
    classification: Classification,
    state: DerivedState,
    preferred_name: str | None,
) -> ComposedAnswer:
    """Answer a "where do I …" question with the real page, plus its state.

    The destination is a route from `links`; the second sentence reports what
    that page currently holds for this student, so the answer is directions
    *and* status rather than either alone.
    """

    target = classification.requirement_reference or "enrollment"
    label, href = _NAVIGATION_PAGES.get(target, ("the Enrollment page", links.ENROLLMENT))
    purpose = _NAVIGATION_PURPOSE.get(target, "continue your enrollment")
    evidence = [f"Portal destination for this request: {label} ({href}) — {purpose}."]
    sentences = [f"You'll find that on {label} — that's where you {purpose}."]

    detail = _navigation_detail(target, state)
    if detail is not None:
        sentences.append(detail)
        evidence.append(f"Current state on that page: {detail}")

    message = " ".join(sentences)
    blocks: list[JsonDict] = [text_block(message)]
    blocks.append(
        {
            "type": "actions",
            "items": [{"text": f"Open {label.removeprefix('the ')}", "href": href}],
        }
    )
    return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _navigation_detail(target: str, state: DerivedState) -> str | None:
    """One grounded sentence about what the destination currently shows."""

    if target in {"documents"}:
        outstanding = [
            document["title"]
            for document in state.document_states
            if document["submissionState"] in {"not_submitted", "needs_resubmission"}
        ]
        if outstanding:
            return "Right now it's waiting on " + _sentence_list(outstanding) + "."
        return "Everything it's waiting on has been submitted."
    if target in {"payments", "financials"}:
        deposit = deposit_state(state)
        if not deposit or not deposit.get("known"):
            return None
        if deposit.get("paid"):
            return "Your enrollment deposit is already posted there."
        if deposit.get("pending"):
            return "Your deposit payment is recorded there and still processing."
        return "Your enrollment deposit is still outstanding there."
    if target in {"enrollment", "housing"}:
        open_steps = [str(step.get("title")) for step in state.remaining_steps]
        if open_steps:
            return f"It has {len(open_steps)} step(s) still open, starting with {open_steps[0]}."
        return "Every step on it is complete."
    if target == "appointments" and state.appointments is not None:
        upcoming = [
            item
            for item in state.appointments.get("items", [])
            if str(_mapping(item).get("status")) == "scheduled"
        ]
        if upcoming:
            return f"You have {len(upcoming)} scheduled appointment(s) listed there."
        return "You have nothing scheduled there yet."
    if target == "messages" and state.messages is not None:
        unread = state.messages.get("unreadCount")
        if isinstance(unread, int):
            return f"You have {unread} unread message(s) there."
    return None


def _sentence_list(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " and " + values[-1]


def _compose_messages(
    _classification: Classification, state: DerivedState, _name: str | None
) -> ComposedAnswer:
    evidence = build_evidence_bundle(state)
    if _read_failed(state, "getStudentMessages"):
        return _unavailable_answer(state, "getStudentMessages")
    messages = state.messages or {}
    unread = messages.get("unreadCount")
    if not isinstance(unread, int):
        message = "I couldn't check your messages just now. The Messages page has the full inbox."
        return ComposedAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    if unread == 0:
        message = "You have no unread messages."
    else:
        message = f"You have {unread} unread message(s). The Messages page has the full inbox."
    blocks = [text_block(message)]
    unread_subjects = [
        entry
        for entry in messages.get("latest", [])
        if entry.get("unread") and entry.get("subject")
    ]
    if unread_subjects:
        blocks.append(
            bullet_list_block(
                [
                    {"text": str(entry["subject"]), "href": links.MESSAGES}
                    for entry in unread_subjects
                ],
                title="Unread messages",
            )
        )
    return ComposedAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_general(
    classification: Classification, state: DerivedState, name: str | None
) -> ComposedAnswer:
    return _compose_next_action(classification, state, name)


_COMPOSERS = {
    "portal_navigation": _compose_portal_navigation,
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
    "aid_support": _compose_aid_support,
    "housing_status": _compose_housing,
    "housing_options": _compose_housing,
    "housing_remaining_steps": _compose_housing,
    "housing_next_action": _compose_housing,
    "housing_support": _compose_housing,
    "housing_eligibility": _compose_housing_eligibility,
    "registration_status": _compose_registration,
    "policy_lookup": _compose_policy_lookup,
    "student_account": _compose_account,
    "deposit_status": _compose_deposit_status,
    "enrollment_state": _compose_enrollment_state,
    "personal_information": _compose_personal_information,
    "academic_standing": _compose_academic_standing,
    "support_requests": _compose_support_requests,
    "appointments": _compose_appointments,
    "academic_plan": _compose_academics,
    "campus_life": _compose_campus_life,
    "messages_unread": _compose_messages,
    "conversational_ack": _compose_ack,
    "assistant_identity": _compose_identity,
    "unsupported_or_out_of_scope": _compose_unsupported,
    "general_question": _compose_general,
}


def _unavailable_notes(state: DerivedState) -> list[str]:
    """An honest sentence per source Edward could not check this turn."""

    notes = []
    for item in state.unavailable_data:
        label = _SOURCE_LABELS.get(str(item.get("source")))
        if label:
            notes.append(f"I couldn't check {label} just now, so I've left it out.")
    return notes[:2]


def _read_failed(state: DerivedState, *tools: str) -> bool:
    """Whether any of the named reads failed this turn (timeout/error)."""

    failed = {str(item.get("source")) for item in state.unavailable_data}
    return any(tool in failed for tool in tools)


def _unavailable_answer(state: DerivedState, *tools: str) -> ComposedAnswer:
    """The honest reply when the read a question depends on did not go
    through: what couldn't be checked, and where to look or retry — never a
    guess about the state itself."""

    labels = [_SOURCE_LABELS.get(tool, tool) for tool in tools if _read_failed(state, tool)]
    subject = labels[0] if labels else "that part of your record"
    message = (
        f"I couldn't check {subject} right now, so I can't verify it this "
        "moment. Try asking again shortly, or check the portal page directly — "
        "I'd rather say so than guess."
    )
    return ComposedAnswer(
        message=message,
        blocks=[text_block(message)],
        evidence_texts=build_evidence_bundle(state),
    )


def _join_titles(items: Sequence[dict[str, Any]]) -> str:
    titles = [str(item.get("title") or item.get("name") or "item") for item in items[:4]]
    if len(titles) <= 1:
        return titles[0] if titles else ""
    return ", ".join(titles[:-1]) + f" and {titles[-1]}"


def _count(number: int, singular: str, plural: str | None = None) -> str:
    """ "3 checklist steps", "1 document" — never the "(s)" shorthand."""

    word = singular if number == 1 else (plural or f"{singular}s")
    return f"{number} {word}"


def _document_noun(title: str) -> str:
    """Checklist titles are imperatives ("Upload an identity document");
    inside a sentence only the noun phrase reads correctly."""

    noun = re.sub(
        r"^(?:submit|upload|complete|pay|select|provide|register for|finish|choose)\s+",
        "",
        title.strip(),
        flags=re.IGNORECASE,
    )
    noun = re.sub(r"^(?:your|the|an|a)\s+", "", noun, flags=re.IGNORECASE)
    return noun or title


def _usd(cents: Any) -> str:
    """Render cents as USD. An absent amount must never become a number —
    the "$0" this used to fabricate went straight into evidence and from
    there into confidently wrong answers."""

    try:
        value = int(cents)
    except (TypeError, ValueError):
        return "not recorded"
    dollars = value / 100
    return f"${dollars:,.2f}".removesuffix(".00")
