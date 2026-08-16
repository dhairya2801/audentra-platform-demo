"""Grounded staff answer composition.

The deterministic composer always produces a complete, true answer with
presentation blocks from the derived state — the floor every model rewrite
must beat. The evidence bundle rendered here is also the corpus the staff
claim guard checks model prose against, so composition and guarding can never
disagree about what the evidence said.

Two staff-specific composition families live here:

- **Drafts** (email, SMS, call talking points): reviewable text grounded in
  the student's record, always labelled "Draft only — nothing has been
  sent." Drafts are never model-rewritten; the deterministic draft is the
  deliverable.
- **Honest refusals**: action requests get the read-only boundary plus a
  concrete read-only alternative; unsupported metrics get a plain statement
  of what does not exist and what related, real data does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from audentra.domain.student_state import AID_DOCUMENT_SATISFIED_STATUSES
from audentra.integrations.assistant.blocks import (
    bullet_list_block,
    next_steps_block,
    table_block,
    text_block,
)
from audentra.integrations.staff_assistant import links as staff_links
from audentra.integrations.staff_assistant.classify import StaffClassification
from audentra.integrations.staff_assistant.derive import (
    StaffDerivedState,
    deadlines_this_week,
    open_work_items,
    overdue_deadlines,
)

JsonDict = dict[str, Any]

DRAFT_DISCLAIMER = "Draft only — nothing has been sent."

_CAPABILITY_MESSAGE = (
    "I'm Edward for staff. I can look up any student in your institution and "
    "explain their enrollment state — requirements, blockers, documents, "
    "deadlines, financial aid, housing, appointments — plus your work queue, "
    "recorded communications, support inquiries, and the deterministic "
    "attention signals. I can recommend next steps and draft emails, SMS, or "
    "call talking points for your review. I'm read-only: I never send, "
    "assign, escalate, or change records."
)

_UNSUPPORTED_METRIC_MESSAGES: dict[str, str] = {
    "melt_risk": (
        "I don't have that — no calibrated melt-risk model exists in Audentra, "
        "and I won't invent a score. What I can give you is the deterministic "
        "attention view: overdue blocking requirements, approaching deadlines, "
        "inactivity, and help requests, each with its evidence."
    ),
    "enrollment_probability": (
        "I don't have enrollment probabilities — no predictive model exists in "
        "Audentra. I can show objective signals instead: deposit state, open "
        "blocking requirements, deadlines, and recorded engagement."
    ),
    "recovery_likelihood": (
        "Recovery likelihood isn't something I have — no such model exists in "
        "Audentra. I can show what is actually blocking a student and whether "
        "they have responded to recorded outreach, which is the honest input "
        "to that judgement."
    ),
    "risk_score": (
        "There is no real risk score in Audentra — the scores shown in the "
        "preview workspace are synthetic test data, and I don't read them. I "
        "can rank students by rule-based attention signals with visible "
        "reasons instead."
    ),
    "student_value": (
        "I don't have student value or revenue data — nothing in Audentra "
        "records it. I can rank students by urgency signals (deadlines, "
        "blockers, inactivity) instead."
    ),
    "email_tracking": (
        "I can't tell you that — email opens and clicks aren't tracked in "
        "Audentra. What I can check is whether a recorded inbound reply exists "
        "after our recorded outbound message."
    ),
    "campaign_performance": (
        "There are no campaigns in Audentra — no campaign records or delivery "
        "metrics exist. I can show recorded per-student communications and "
        "whether each got a reply."
    ),
    "sla_compliance": (
        "No SLA is defined in Audentra — there's no service-level "
        "configuration to measure against. I can show overdue and escalated "
        "work items, which is the closest real signal."
    ),
    "room_assignment": (
        "Room assignments aren't in Audentra — the platform records a housing "
        "preference and the housing requirement's state, nothing further. I "
        "can show those."
    ),
    "disbursement_schedule": (
        "There's no aid disbursement schedule in Audentra — no disbursement "
        "ledger exists. I can show the accepted award amounts and every aid "
        "document requirement still open."
    ),
    "registration_window": (
        "Course-registration windows aren't modeled in Audentra. I can show "
        "what on a student's record would block registration — blocking "
        "requirements and the deposit — which is the part I can verify."
    ),
    "demographics": (
        "I don't have demographic, address, or admit-type data — Audentra's "
        "student record doesn't include it. I can share program, class year, "
        "and enrollment state."
    ),
    "stage_duration": (
        "I can't measure that reliably — requirement status transitions aren't "
        "individually versioned in Audentra. Work items do carry start and "
        "completion timestamps, so I can answer duration questions about "
        "staff work, just not requirement stages."
    ),
}

_ACTION_MESSAGES: dict[str, tuple[str, str]] = {
    # kind -> (what I can't do, the read-only offer)
    "send_message": (
        "I can't send messages — I'm read-only in this version.",
        "I can draft the email or SMS for you to review and send yourself; "
        "just ask me to draft it.",
    ),
    "place_call": (
        "I can't place calls — I'm read-only in this version.",
        "I can prepare call talking points grounded in the student's record.",
    ),
    "create_task": (
        "I can't create tasks — I'm read-only in this version.",
        "You can create it from the Action Center; I can summarize the case "
        "so the task writes itself.",
    ),
    "assign": (
        "I can't assign or reassign work — I'm read-only in this version.",
        "Assignment happens in the Action Center; I can tell you who "
        "currently owns the student's open cases.",
    ),
    "escalate": (
        "I can't escalate cases — I'm read-only in this version.",
        "Escalation is a checkbox on the work item; I can lay out the "
        "evidence you'd escalate with.",
    ),
    "update_record": (
        "I can't change records — I'm read-only in this version.",
        "Record changes happen in the portal workflows; I can show you the "
        "current state and what would clear it.",
    ),
    "schedule": (
        "I can't schedule appointments — I'm read-only in this version.",
        "I can show existing appointments and draft the outreach proposing a time.",
    ),
}


@dataclass
class ComposedStaffAnswer:
    message: str
    blocks: list[JsonDict] = field(default_factory=list)
    evidence_texts: list[str] = field(default_factory=list)


def compose_staff_deterministic(
    classification: StaffClassification,
    state: StaffDerivedState,
) -> ComposedStaffAnswer:
    composer = _COMPOSERS.get(classification.request_type, _compose_general)
    answer = composer(classification, state)
    for note in _unavailable_notes(state):
        answer.message = f"{answer.message} {note}"
    answer.message = answer.message.strip()[:1_600]
    return answer


def build_staff_evidence_bundle(state: StaffDerivedState) -> list[str]:
    """Every fact the composer may use, rendered once as plain text."""

    lines: list[str] = []
    cohort = state.cohort
    if cohort is not None:
        clauses = "; ".join(str(item) for item in cohort.get("filter") or []) or "no filter"
        total = cohort.get("total")
        returned = cohort.get("returned")
        lines.append(
            f"Cohort query ({clauses}): {total} student(s) match in total; {returned} listed below."
        )
        if cohort.get("truncated"):
            lines.append(
                f"The list is a page of {returned} out of {total}. Say the total and that "
                "the names shown are a sample — never present them as the whole cohort."
            )
        for entry in cohort.get("items") or []:
            item = _m(entry)
            requirements = _m(item.get("requirements"))
            lines.append(
                f"Cohort member: {item.get('name')} — {item.get('programName')}, "
                f"class of {item.get('classYear')}, offer "
                f"{item.get('offerStatus') or 'none'}, deposit "
                f"{item.get('depositState') or ('paid' if item.get('depositPaid') else 'unpaid')}, "
                f"{requirements.get('completed')}/{requirements.get('total')} requirements "
                f"complete, {requirements.get('openBlocking')} open blocking"
            )
        if total == 0:
            lines.append(
                "No student matches this filter. That is a real, correct answer: do not "
                "widen the filter silently or name students who do not match."
            )
    summary = state.cohort_summary
    if summary is not None:
        clauses = "; ".join(str(item) for item in summary.get("filter") or []) or "no filter"
        lines.append(
            f"Cohort count ({clauses}): {summary.get('matchingStudents')} student(s) match, "
            f"grouped by {summary.get('groupBy')}."
        )
        represents = str(summary.get("countsRepresent") or "students")
        if represents != "students":
            lines.append(
                f"These bucket counts are {represents}, not headcounts — one student can "
                "appear in several buckets, so the buckets do not sum to the student total."
            )
        for entry in summary.get("buckets") or []:
            bucket = _m(entry)
            lines.append(
                f"Group {bucket.get('value')}: {bucket.get('count')} {represents}"
                + (
                    f" across {bucket.get('students')} student(s)"
                    if represents != "students"
                    else ""
                )
            )
    student = state.student
    name = _student_name(state)
    if student:
        lines.append(
            f"Student: {student.get('name')} (preferred name {student.get('preferredName')}), "
            f"{student.get('programName')}, class of {student.get('classYear')}"
        )
        lines.append(f"Onboarding status: {student.get('onboardingStatus')}")
        offer = _m(student.get("offer"))
        if offer:
            lines.append(
                f"Offer status: {offer.get('status') or 'none recorded'}; deposit paid: "
                f"{'yes' if offer.get('depositPaid') else 'no'}"
                + (
                    f"; offer response deadline {offer.get('responseDeadline')}"
                    if offer.get("responseDeadline")
                    else ""
                )
                + (
                    f"; deposit amount {_usd(offer.get('depositAmountCents'))}"
                    if offer.get("depositAmountCents")
                    else ""
                )
            )
        req = _m(student.get("requirements"))
        if req:
            lines.append(
                f"Requirements: {req.get('completed')} of {req.get('total')} complete, "
                f"{req.get('openBlocking')} open blocking"
                + (f", next due {req.get('nextDueAt')}" if req.get("nextDueAt") else "")
            )
        if student.get("communicationPreference"):
            lines.append(f"Communication preference: {student['communicationPreference']}")
        lines.append(f"Open staff work items: {student.get('openWorkItems')}")
    for result in state.search_results:
        req = _m(result.get("requirements"))
        lines.append(
            f"Roster match: {result.get('name')} — {result.get('programName')}, class of "
            f"{result.get('classYear')}, {req.get('openBlocking', 0)} open blocking requirement(s)"
        )
    for item in state.open_requirements:
        lines.append(
            f"Open requirement: {item.get('title')} (status {item.get('status')}"
            + (", blocking" if item.get("blocking") else "")
            + (f", due {item.get('dueAt')}" if item.get("dueAt") else "")
            + (
                f", responsible office {item.get('responsibleOffice')}"
                if item.get("responsibleOffice")
                else ""
            )
            + ")"
        )
    for item in state.requirements:
        if str(item.get("status")) in {"completed", "waived", "not_applicable"}:
            lines.append(f"Completed requirement: {item.get('title')}")
    for document in state.documents:
        lines.append(
            f"Document: {document.get('category')} — {document.get('fileName')} "
            f"(status {document.get('status')})"
        )
    for blocker in state.blockers:
        lines.append(
            f"Blocker: {blocker.get('title')} — owner: {blocker.get('owner')}; cleared by: "
            f"{blocker.get('clearingAction')}"
            + (f"; due {blocker.get('dueAt')}" if blocker.get("dueAt") else "")
        )
    if (
        state.blockers is not None
        and not state.hold_system_operated
        and (state.blockers or state.student)
    ):
        lines.append(
            "The platform operates no registrar hold system; blockers above are "
            "derived from the enrollment record and this list is complete."
        )
    for deadline in state.deadlines:
        lines.append(
            f"Deadline: {deadline.get('title')} due {deadline.get('dueAt')} "
            f"({str(deadline.get('bucket', '')).replace('_', ' ')})"
        )
    aid = state.financials
    if aid:
        for award in aid.get("awards", []):
            lines.append(
                f"Aid award {award.get('name')} ({award.get('type')}): status "
                f"{award.get('status')}, offered {_usd(award.get('offeredAmountCents'))}, "
                f"accepted {_usd(award.get('acceptedAmountCents'))}"
            )
        for requirement in aid.get("requiredDocuments", []):
            lines.append(
                f"Aid document {requirement.get('title')}: status {requirement.get('status')}"
                + (f", due {requirement['dueAt']}" if requirement.get("dueAt") else "")
            )
        for key, label in (
            ("acceptedAidCents", "Accepted aid total"),
            ("costOfAttendanceCents", "Cost of attendance"),
            ("remainingBalanceCents", "Remaining balance"),
            ("paymentsCents", "Payments recorded"),
        ):
            if aid.get(key) is not None:
                lines.append(f"{label}: {_usd(aid[key])}")
        deposit = _m(aid.get("deposit"))
        if deposit:
            if deposit.get("paymentPending"):
                lines.append("Deposit payment: submitted and pending — not posted yet.")
            else:
                lines.append(f"Deposit paid: {'yes' if deposit.get('paid') else 'no'}")
        lines.append("No aid disbursement schedule is tracked in the platform.")
    housing = state.housing
    if housing:
        lines.append(
            f"Housing preference: {housing.get('preference') or 'not selected'}; housing "
            f"requirement status: {housing.get('requirementStatus') or 'no housing step'}; "
            f"eligibility: {housing.get('eligibility')}"
        )
        lines.append("No housing application window or room assignment is modeled in the platform.")
    for appointment in state.appointments:
        lines.append(
            f"Appointment: {str(appointment.get('type', '')).replace('_', ' ')} at "
            f"{appointment.get('startsAt')} ({appointment.get('status')})"
        )
    communications = state.communications
    if communications:
        events = list(communications.get("events", []))
        lines.append(f"Recorded communications: {len(events)}")
        for event in events[:12]:
            lines.append(
                f"Communication: {event.get('direction')} {event.get('channel')} on "
                f"{event.get('occurredAt')} — "
                f"{event.get('subject') or event.get('bodyExcerpt') or 'no subject'} "
                f"(delivery {event.get('deliveryStatus')}, "
                f"resolution {event.get('resolutionStatus')})"
            )
        for inquiry in communications.get("inquiries", [])[:6]:
            lines.append(
                f"Support inquiry: {inquiry.get('subject')} (status {inquiry.get('status')}, "
                f"priority {inquiry.get('priority')}"
                + (f", assigned to {inquiry.get('assignee')}" if inquiry.get("assignee") else "")
                + ")"
            )
        if state.awaiting_reply is True and state.last_outbound:
            lines.append(
                f"The last recorded outbound message ({state.last_outbound.get('channel')} on "
                f"{state.last_outbound.get('occurredAt')}) has no later recorded inbound reply."
            )
        elif state.awaiting_reply is False and state.last_inbound:
            lines.append(
                f"The latest recorded inbound message from {name or 'the student'} was on "
                f"{state.last_inbound.get('occurredAt')} ({state.last_inbound.get('channel')})."
            )
        lines.append(
            "Coverage: recorded interactions only — no vendor email/SMS integration "
            "or open/click tracking exists."
        )
    engagement = state.engagement
    if engagement:
        if engagement.get("available") is False:
            lines.append("No engagement snapshot has been computed for this student yet.")
        else:
            lines.append(
                "Engagement snapshot: completion "
                f"{engagement.get('completionPercentage')}%, "
                f"{engagement.get('blockingRequirementCount')} blocking requirement(s), "
                + (
                    f"next deadline {engagement.get('nextDeadline')} "
                    f"({engagement.get('daysToNextDeadline')} day(s) away), "
                    if engagement.get("nextDeadline")
                    else "no recorded next deadline, "
                )
                + f"help requested: {'yes' if engagement.get('helpRequested') else 'no'}, "
                f"open support cases: {engagement.get('openSupportCaseCount')}"
            )
            if engagement.get("lastMeaningfulActionAt"):
                lines.append(
                    f"Last meaningful portal action: {engagement['lastMeaningfulActionAt']}"
                )
            if engagement.get("projectedAt"):
                lines.append(f"Snapshot computed at: {engagement['projectedAt']}")
    for event in state.timeline[:20]:
        lines.append(
            f"Timeline: {event.get('occurredAt')} — {event.get('title')}"
            + (f" ({event.get('detail')})" if event.get("detail") else "")
        )
    ownership = state.ownership
    if ownership:
        for entry in ownership.get("workItemAssignees", []):
            lines.append(
                f"Work item {entry.get('workItemKey')} ({entry.get('workItemTitle')}): "
                f"assigned to {entry.get('assignee') or 'no one'} "
                f"in {entry.get('component')}"
            )
        for entry in ownership.get("inquiryAssignees", []):
            lines.append(
                f"Inquiry '{entry.get('inquirySubject')}': assigned to "
                f"{entry.get('assignee') or 'no one'}"
            )
        offices = ownership.get("responsibleOffices", [])
        if offices:
            lines.append(
                "Responsible offices for open requirements: " + ", ".join(map(str, offices))
            )
        lines.append("No formal advisor or caseload model exists; ownership is per open case.")
    attention = state.attention
    if attention:
        for item in attention.get("items", []):
            student_ref = _m(item.get("student"))
            reasons = ", ".join(_reason_phrase(code) for code in item.get("reasonCodes", []))
            snapshot = _m(item.get("snapshot"))
            lines.append(
                f"Attention: {student_ref.get('name')} — priority {item.get('priority')}"
                + (f"; reasons: {reasons}" if reasons else "")
                + (
                    f"; next deadline {snapshot.get('nextDeadline')}"
                    f" ({snapshot.get('daysToNextDeadline')} day(s))"
                    if snapshot.get("nextDeadline")
                    else ""
                )
                + (
                    f"; {snapshot.get('blockingRequirementCount')} blocking requirement(s)"
                    if snapshot.get("blockingRequirementCount") is not None
                    else ""
                )
            )
        lines.append(str(attention.get("rankingBasis") or ""))
    queue = state.queue
    if queue:
        counts = _m(queue.get("counts"))
        lines.append(
            f"Work queue counts: {counts.get('todo', 0)} to do, "
            f"{counts.get('inProgress', 0)} in progress, "
            f"{counts.get('followUpRequired', 0)} follow-up required, "
            f"{counts.get('blocked', 0)} blocked, {counts.get('urgent', 0)} urgent, "
            f"{counts.get('escalated', 0)} escalated"
        )
        for item in queue.get("items", [])[:12]:
            student_ref = _m(item.get("student"))
            assignee = _m(item.get("assignee"))
            lines.append(
                f"Work item {item.get('key')}: {item.get('title')} — {item.get('status')}, "
                f"priority {item.get('priority')}, student {student_ref.get('name')}"
                + (f", due {item.get('dueAt')}" if item.get("dueAt") else "")
                + (f", assignee {assignee.get('name')}" if assignee else ", unassigned")
                + (", escalated" if item.get("escalated") else "")
            )
    work_item = state.work_item
    if work_item:
        item = _m(work_item.get("workItem"))
        student_ref = _m(item.get("student"))
        lines.append(
            f"Work item {item.get('key')}: {item.get('title')} — status {item.get('status')}, "
            f"priority {item.get('priority')}, student {student_ref.get('name')}"
            + (f", due {item.get('dueAt')}" if item.get("dueAt") else "")
        )
        if item.get("nextStep"):
            lines.append(f"Recorded next step: {item['nextStep']}")
        insight = _m(work_item.get("taskInsight"))
        if insight.get("summary"):
            lines.append(f"Task insight (AI-generated): {insight['summary']}")
        for interaction in work_item.get("interactions", [])[:4]:
            entry = _m(interaction)
            lines.append(
                f"Interaction ({entry.get('selectedChannel') or 'no channel'}): status "
                f"{entry.get('status')}, objective {entry.get('objective')}"
            )
            for communication in entry.get("communications", [])[:6]:
                comm = _m(communication)
                lines.append(
                    f"Recorded {comm.get('direction')} {comm.get('channel')} on "
                    f"{comm.get('occurredAt')}: "
                    f"{comm.get('subject') or comm.get('body') or ''}"[:220]
                )
        for log in _m(work_item.get("workItem")).get("history", [])[:8]:
            entry = _m(log)
            lines.append(
                f"Work log: {entry.get('action')} — {entry.get('message')} "
                f"({entry.get('occurredAt')})"
            )
    for inquiry in state.inquiries[:12]:
        student_ref = _m(inquiry.get("student"))
        lines.append(
            f"Inquiry: {inquiry.get('subject')} from "
            f"{student_ref.get('name') or inquiry.get('studentName') or 'a student'} — "
            f"status {inquiry.get('status')}, priority {inquiry.get('priority')}"
            + (f", created {inquiry.get('createdAt')}" if inquiry.get("createdAt") else "")
        )
    thread = state.inquiry_thread
    if thread:
        lines.append(
            f"Inquiry thread status: {thread.get('status')}"
            + (f", active until {thread.get('expiresAt')}" if thread.get("expiresAt") else "")
        )
        for message in thread.get("messages", [])[:10]:
            entry = _m(message)
            lines.append(
                f"Thread message from {entry.get('authorName')} ({entry.get('createdAt')}): "
                f"{str(entry.get('body') or '')[:200]}"
            )
    guidance = state.guidance
    if guidance:
        for play in guidance.get("corePlays", []):
            steps = "; ".join(str(step) for step in play.get("steps", [])[:6])
            lines.append(
                f"Staff-authored play '{play.get('title')}' ({play.get('status')}): trigger — "
                f"{play.get('trigger')}; steps — {steps}"
            )
        for card in guidance.get("knowledgeCards", []):
            lines.append(
                f"Knowledge card '{card.get('title')}' ({card.get('status')}): "
                f"{card.get('summary')}"
            )
        if not guidance.get("corePlays") and not guidance.get("knowledgeCards"):
            lines.append("No staff-authored plays or knowledge cards exist yet.")
        lines.append(
            "These are staff-authored guidance, not system-enforced policy; no "
            "playbook engine evaluates them."
        )
    for rule in state.action_rules[:10]:
        rule_state = "enabled" if rule.get("enabled") else "disabled"
        lines.append(
            f"Automation rule '{rule.get('name')}' ({rule_state}): "
            f"signal {rule.get('signalType')}"
            + (
                f", requirement {rule.get('requirementCode')}"
                if rule.get("requirementCode")
                else ""
            )
            + (
                f", lookahead {rule.get('lookaheadDays')} day(s)"
                if rule.get("lookaheadDays") is not None
                else ""
            )
            + f", creates {rule.get('priority')} {rule.get('actionType')} work for "
            f"{rule.get('component')}"
        )
    return [line for line in lines if line]


_REASON_PHRASES = {
    "inactive": "no meaningful portal activity for 7+ days",
    "blocking_requirement": "open blocking requirement(s)",
    "deadline_within_7_days": "a deadline within 7 days",
    "help_requested": "the student asked for help",
}


def _reason_phrase(code: object) -> str:
    return _REASON_PHRASES.get(str(code), str(code).replace("_", " "))


# ---------------------------------------------------------------------------
# Composers
# ---------------------------------------------------------------------------


def _compose_greeting(
    _classification: StaffClassification, _state: StaffDerivedState
) -> ComposedStaffAnswer:
    message = (
        "Hi! I'm Edward for staff. Ask me about a student, your work queue, "
        "who needs attention today, or ask me to draft outreach for review."
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message)])


def _compose_capabilities(
    _classification: StaffClassification, _state: StaffDerivedState
) -> ComposedStaffAnswer:
    return ComposedStaffAnswer(
        message=_CAPABILITY_MESSAGE, blocks=[text_block(_CAPABILITY_MESSAGE)]
    )


def _compose_action_request(
    classification: StaffClassification, _state: StaffDerivedState
) -> ComposedStaffAnswer:
    boundary, offer = _ACTION_MESSAGES.get(
        str(classification.reference or ""),
        (
            "I can't perform actions — I'm read-only in this version.",
            "I can retrieve, explain, and draft; the action itself happens in the portal.",
        ),
    )
    message = f"{boundary} {offer}"
    return ComposedStaffAnswer(message=message, blocks=[text_block(message)])


def _compose_unsupported_metric(
    classification: StaffClassification, _state: StaffDerivedState
) -> ComposedStaffAnswer:
    message = _UNSUPPORTED_METRIC_MESSAGES.get(
        str(classification.reference or ""),
        "I don't currently have that information in Audentra.",
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message)])


def _compose_unsupported(
    _classification: StaffClassification, _state: StaffDerivedState
) -> ComposedStaffAnswer:
    message = (
        "I can't help with that — it isn't part of the staff or student data I "
        "can read. I can answer questions about students' enrollment state, "
        "your work queue, communications, inquiries, and attention signals."
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message)])


def _compose_student_overview(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    student = state.student
    if not student:
        return _compose_search_results(state, evidence)
    name = str(student.get("preferredName") or student.get("name"))
    offer = _m(student.get("offer"))
    req = _m(student.get("requirements"))
    parts = [
        f"{student.get('name')} — {student.get('programName')}, class of "
        f"{student.get('classYear')}."
    ]
    if offer.get("status"):
        deposit = "deposit paid" if offer.get("depositPaid") else "deposit not paid"
        parts.append(f"Offer {offer['status']}, {deposit}.")
    parts.append(
        f"{req.get('completed', 0)} of {req.get('total', 0)} requirements complete; "
        f"{req.get('openBlocking', 0)} open blocking item(s)."
    )
    if state.blockers:
        titles = _join_titles(state.blockers)
        parts.append(f"Currently blocked on: {titles}.")
    overdue = overdue_deadlines(state)
    if overdue:
        parts.append(f"{len(overdue)} deadline(s) past due.")
    elif req.get("nextDueAt"):
        parts.append(f"Next due date: {str(req['nextDueAt'])[:10]}.")
    if student.get("openWorkItems"):
        parts.append(f"{student['openWorkItems']} open staff work item(s) on {name}.")
    message = " ".join(parts)
    blocks: list[JsonDict] = [text_block(message)]
    if state.open_work:
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": f"{item.get('key')}: {item.get('title')} — "
                        f"{str(item.get('status', '')).replace('_', ' ')} "
                        f"({item.get('priority')})"
                    }
                    for item in state.open_work[:5]
                ],
                title="Open staff work",
            )
        )
    return ComposedStaffAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_search_results(state: StaffDerivedState, evidence: list[str]) -> ComposedStaffAnswer:
    results = state.search_results
    if not results:
        message = (
            "I couldn't find a matching student in your institution. A full "
            "name usually works best."
        )
        return ComposedStaffAnswer(message=message, blocks=[text_block(message)])
    message = f"I found {len(results)} matching student(s)."
    block = bullet_list_block(
        [
            {
                "text": f"{item.get('name')} — {item.get('programName')}, class of "
                f"{item.get('classYear')} "
                f"({_m(item.get('requirements')).get('openBlocking', 0)} open blocking)"
            }
            for item in results[:10]
        ],
        title="Matches",
    )
    if len(results) > 1:
        message += " Which one do you mean?"
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_missing_items(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    open_items = state.open_requirements
    if state.requirements and not open_items:
        message = f"{name} has completed every requirement — nothing is outstanding."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    if not state.requirements:
        message = f"I couldn't read {name}'s requirements just now, so I can't say what's missing."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    blocking = [item for item in open_items if item.get("blocking")]
    message = (
        f"{name} still has {len(open_items)} open requirement(s)"
        + (f", {len(blocking)} of them blocking" if blocking else "")
        + "."
    )
    rows = [
        {
            "requirement": str(item.get("title", "")),
            "status": str(item.get("status", "")).replace("_", " "),
            "blocking": "yes" if item.get("blocking") else "no",
            "due": str(item.get("dueAt") or "")[:10],
            "office": str(item.get("responsibleOffice") or ""),
        }
        for item in open_items
    ]
    block = table_block(
        [
            {"key": "requirement", "label": "Requirement"},
            {"key": "status", "label": "Status"},
            {"key": "blocking", "label": "Blocking"},
            {"key": "due", "label": "Due"},
            {"key": "office", "label": "Responsible office"},
        ],
        rows,
        caption="Outstanding requirements",
    )
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_blockers(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    if not state.blockers:
        message = (
            f"Nothing on {name}'s record is blocking enrollment right now. "
            "Note the platform operates no registrar hold system, so derived "
            "blockers are the complete picture."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    # The block below names each blocker with its clearing action; the prose
    # answers the question and says who moves next.
    waiting = [item for item in state.blockers if item.get("owner") == "university"]
    student_owned = [item for item in state.blockers if item.get("owner") != "university"]
    if len(state.blockers) <= 2:
        summary = f"{_join_titles(state.blockers)}"
    else:
        summary = "listed below with who clears each"
    split = ""
    if waiting and student_owned:
        split = (
            f" {len(student_owned)} need{'s' if len(student_owned) == 1 else ''} action "
            f"from the student; {len(waiting)} {'is' if len(waiting) == 1 else 'are'} "
            "waiting on university review."
        )
    elif waiting:
        split = " All of it is waiting on university review."
    message = (
        f"{name} is blocked by {_count(len(state.blockers), 'item')} — {summary}."
        f"{split} (No registrar hold system exists — these derived blockers "
        "are the complete list.)"
    )
    block = next_steps_block(
        [
            {
                "text": f"{blocker.get('title')} — {blocker.get('clearingAction')}"
                + (
                    f" (office: {blocker.get('responsibleOffice')})"
                    if blocker.get("responsibleOffice")
                    else ""
                ),
                "owner": "student" if blocker.get("owner") == "student" else "university",
            }
            for blocker in state.blockers
        ],
        title="What's blocking enrollment",
    )
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_documents(
    classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    documents = state.documents
    if not documents:
        message = f"{name} has no document records yet."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    reference = classification.reference
    lead = ""
    if reference:
        category_map = {"immunization": "health"}
        category = category_map.get(reference, reference)
        matched = [
            item for item in documents if category in str(item.get("category") or "").lower()
        ]
        if matched:
            newest = matched[0]
            lead = (
                f"{name}'s {reference} document ({newest.get('fileName')}) is "
                f"{str(newest.get('status', '')).replace('_', ' ')}. "
            )
        else:
            lead = f"No {reference} document is on file for {name}. "
    message = f"{lead}{name} has {len(documents)} document record(s) in total."
    rows = [
        {
            "category": str(item.get("category", "")),
            "file": str(item.get("fileName", "")),
            "status": str(item.get("status", "")).replace("_", " "),
            "uploaded": str(item.get("createdAt") or "")[:10],
        }
        for item in documents
    ]
    block = table_block(
        [
            {"key": "category", "label": "Category"},
            {"key": "file", "label": "File"},
            {"key": "status", "label": "Status"},
            {"key": "uploaded", "label": "Uploaded"},
        ],
        rows,
        caption="Documents on file",
    )
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_deadlines(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    if not state.deadlines:
        message = f"Nothing on {name}'s record has an upcoming deadline."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    overdue = overdue_deadlines(state)
    week = deadlines_this_week(state)
    message = (
        f"{len(overdue)} deadline(s) for {name} are past due — start there."
        if overdue
        else f"{name} has {len(state.deadlines)} upcoming deadline(s)"
        + (f", {len(week)} within the next week" if week else "")
        + "."
    )
    bucket_label = {
        "overdue": "Past due",
        "this_week": "This week",
        "this_month": "This month",
        "later": "Later",
    }
    block = table_block(
        [
            {"key": "item", "label": "Item"},
            {"key": "due", "label": "Due"},
            {"key": "window", "label": "Window"},
        ],
        [
            {
                "item": str(deadline.get("title")),
                "due": str(deadline.get("dueAt", ""))[:10],
                "window": bucket_label.get(str(deadline.get("bucket")), "Later"),
            }
            for deadline in state.deadlines
        ],
        caption=f"Deadlines for {name}",
    )
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_financials(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    aid = state.financials
    if not aid:
        message = f"I couldn't read {name}'s financial record just now."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    awards = aid.get("awards", [])
    open_documents = [
        item
        for item in aid.get("requiredDocuments", [])
        if str(_m(item).get("status")) not in AID_DOCUMENT_SATISFIED_STATUSES
    ]
    deposit = _m(aid.get("deposit"))
    parts = [f"{name}'s aid file lists {len(awards)} award(s)."]
    if open_documents:
        parts.append(
            f"{len(open_documents)} aid document requirement(s) still open: "
            f"{_join_titles(open_documents)}."
        )
    if deposit:
        if deposit.get("paymentPending"):
            parts.append("The enrollment deposit payment is submitted but not posted yet.")
        elif deposit.get("paid"):
            parts.append("The enrollment deposit is paid.")
        elif deposit.get("amountCents"):
            parts.append(
                f"The {_usd(deposit['amountCents'])} enrollment deposit has not been paid."
            )
    if aid.get("remainingBalanceCents") is not None:
        parts.append(f"Derived remaining balance: {_usd(aid['remainingBalanceCents'])}.")
    message = " ".join(parts)
    blocks: list[JsonDict] = [text_block(message)]
    if awards:
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
                        "award": str(_m(award).get("name", "")),
                        "status": str(_m(award).get("status", "")).replace("_", " "),
                        "offered": _usd(_m(award).get("offeredAmountCents")),
                        "accepted": _usd(_m(award).get("acceptedAmountCents")),
                    }
                    for award in awards
                ],
                caption=f"Aid awards ({aid.get('academicYear', '')})",
            )
        )
    return ComposedStaffAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_housing(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    housing = state.housing
    if not housing:
        message = f"I couldn't read {name}'s housing state just now."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    preference = housing.get("preference")
    status = housing.get("requirementStatus")
    eligibility = str(housing.get("eligibility") or "")
    parts = []
    if preference:
        parts.append(f"{name}'s housing preference is {str(preference).replace('_', ' ')}.")
    else:
        parts.append(f"{name} hasn't selected a housing preference yet.")
    if status:
        parts.append(f"The housing step is {str(status).replace('_', ' ')}.")
    if eligibility == "blocked" and state.blockers:
        parts.append(f"It's blocked behind: {_join_titles(state.blockers)}.")
    parts.append("Room assignments and application windows aren't tracked in the platform.")
    message = " ".join(parts)
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message)], evidence_texts=evidence
    )


def _compose_appointments(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    if not state.appointments:
        message = f"{name} has no appointments on record."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = f"{name} has {len(state.appointments)} appointment(s) on record."
    block = bullet_list_block(
        [
            {
                "text": f"{str(item.get('type', '')).replace('_', ' ')} — "
                f"{item.get('startsAt')} ({item.get('status')})"
            }
            for item in state.appointments
        ],
        title="Appointments",
    )
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_communications(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    communications = state.communications
    if not communications:
        message = f"I couldn't read {name}'s communication history just now."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    events = list(communications.get("events", []))
    if not events and not communications.get("inquiries"):
        message = (
            f"No communications are recorded for {name}. That covers recorded "
            "interactions only — the platform has no external email/SMS "
            "integration, so contact may have happened outside it."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    if state.awaiting_reply is True and state.last_outbound:
        lead = (
            f"The last recorded outbound {state.last_outbound.get('channel')} to {name} "
            f"({str(state.last_outbound.get('occurredAt', ''))[:10]}) has no recorded "
            "reply."
        )
    elif state.last_inbound:
        lead = (
            f"{name} last wrote back on "
            f"{str(state.last_inbound.get('occurredAt', ''))[:10]} "
            f"via {state.last_inbound.get('channel')}."
        )
    else:
        lead = f"{name} has {len(events)} recorded communication event(s)."
    message = (
        f"{lead} {len(events)} recorded event(s) in total — recorded "
        "interactions only; opens and clicks aren't tracked."
    )
    block = bullet_list_block(
        [
            {
                "text": f"{str(event.get('occurredAt', ''))[:16]} — "
                f"{event.get('direction')} {event.get('channel')}: "
                f"{event.get('subject') or event.get('bodyExcerpt') or 'no subject'}"
            }
            for event in events[:8]
        ],
        title="Recent recorded communications",
    )
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_engagement(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    engagement = state.engagement
    if not engagement or engagement.get("available") is False:
        message = (
            f"No engagement snapshot has been computed for {name} yet — the "
            "engagement scan runs on a schedule. I can still check their "
            "requirements, deadlines, and recorded communications live."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    parts = [
        f"{name} is {engagement.get('completionPercentage')}% through their "
        f"requirements with {engagement.get('blockingRequirementCount')} blocking "
        "item(s) open."
    ]
    if engagement.get("lastMeaningfulActionAt"):
        parts.append(
            f"Last meaningful portal action: {str(engagement['lastMeaningfulActionAt'])[:10]}."
        )
    if engagement.get("nextDeadline"):
        parts.append(
            f"Next deadline {str(engagement['nextDeadline'])[:10]} "
            f"({engagement.get('daysToNextDeadline')} day(s) away)."
        )
    if engagement.get("helpRequested"):
        parts.append("They have asked for help.")
    if engagement.get("projectedAt"):
        parts.append(
            f"(Snapshot computed {str(engagement['projectedAt'])[:16]} — these "
            "figures refresh on the scan schedule, not live.)"
        )
    message = " ".join(parts)
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message)], evidence_texts=evidence
    )


def _compose_timeline(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "this student"
    if not state.timeline:
        message = f"No recorded operational events found for {name} yet."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = (
        f"Here are the {len(state.timeline)} most recent recorded events for {name} "
        "(work logs, documents, payments, communications, appointments, and "
        "inquiries)."
    )
    block = bullet_list_block(
        [
            {
                "text": f"{str(event.get('occurredAt', ''))[:16]} — {event.get('title')}"
                + (f": {event.get('detail')}" if event.get("detail") else "")
            }
            for event in state.timeline[:15]
        ],
        title="Recent activity",
    )
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_ownership(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    ownership = state.ownership
    if not ownership:
        message = f"I couldn't read ownership for {name} just now."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    work = ownership.get("workItemAssignees", [])
    inquiries = ownership.get("inquiryAssignees", [])
    offices = ownership.get("responsibleOffices", [])
    parts = []
    assigned = [entry for entry in work if _m(entry).get("assignee")]
    if assigned:
        names = sorted({str(_m(entry).get("assignee")) for entry in assigned})
        parts.append(f"Open work on {name} is assigned to {', '.join(names)}.")
    elif work:
        parts.append(f"{name} has {len(work)} open work item(s), all unassigned.")
    else:
        parts.append(f"No open staff work items exist for {name}.")
    if inquiries:
        parts.append(f"{len(inquiries)} open inquiry(ies) also carry assignees.")
    if offices:
        parts.append(
            f"Responsible offices for their open requirements: {', '.join(map(str, offices))}."
        )
    parts.append(
        "There's no formal advisor or caseload model — ownership exists only per open case."
    )
    message = " ".join(parts)
    blocks: list[JsonDict] = [text_block(message)]
    if work:
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": f"{_m(entry).get('workItemKey')}: "
                        f"{_m(entry).get('workItemTitle')} — "
                        f"{_m(entry).get('assignee') or 'unassigned'} "
                        f"({_m(entry).get('component')})"
                    }
                    for entry in work[:6]
                ],
                title="Open case ownership",
            )
        )
    return ComposedStaffAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_attention(
    classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    disclaimer = ""
    if classification.reference in _UNSUPPORTED_METRIC_MESSAGES:
        metric_names = {
            "melt_risk": "melt risk",
            "enrollment_probability": "enrollment probability",
            "recovery_likelihood": "recovery likelihood",
            "risk_score": "a risk score",
            "student_value": "student value",
        }
        metric = metric_names.get(str(classification.reference), "that metric")
        disclaimer = (
            f"There's no model for {metric} in Audentra, so I won't rank by it. "
            "Here is the honest alternative — rule-based attention signals with "
            "their reasons. "
        )
    attention = state.attention
    items = list(attention.get("items", [])) if attention else []
    if not items:
        message = (
            f"{disclaimer}The engagement scan hasn't flagged any students right "
            "now (or hasn't produced candidates yet). Your work queue is the "
            "other place to look — I can rank that by priority and due date."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = (
        f"{disclaimer}"
        f"{_count(len(items), 'student currently carries', 'students currently carry')} "
        "attention flags, ranked by the engagement scan's stored priority. "
        "Each entry below shows why:"
    )
    entries = []
    for item in items[:10]:
        student_ref = _m(item.get("student"))
        reasons = ", ".join(_reason_phrase(code) for code in item.get("reasonCodes", []))
        snapshot = _m(item.get("snapshot"))
        detail = ""
        if snapshot.get("nextDeadline"):
            detail = (
                f"; next deadline {str(snapshot['nextDeadline'])[:10]} "
                f"({snapshot.get('daysToNextDeadline')}d)"
            )
        entries.append(
            {
                "text": f"{student_ref.get('name')} — {item.get('priority')}"
                + (f": {reasons}" if reasons else "")
                + detail
            }
        )
    block = numbered_or_bullet(entries, title="Needs attention (rule-based)")
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def numbered_or_bullet(entries: list[JsonDict], *, title: str) -> JsonDict:
    from audentra.integrations.assistant.blocks import numbered_list_block

    return numbered_list_block(entries, title=title)


def _compose_recommendation(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "this student"
    facts = _recommendation_facts(state)
    if not facts:
        message = (
            f"Nothing on {name}'s record calls for an intervention right now: no "
            "open blockers, no approaching deadlines, and no unanswered "
            "recorded outreach that I can see."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    action = _recommended_action_sentence(state, facts)
    reasons = " ".join(fact["sentence"] for fact in facts[:3])
    play_sentence = _matching_play_sentence(state, facts)
    message = (
        f"{reasons} {action}"
        + (f" {play_sentence}" if play_sentence else "")
        + " This is my recommendation from the record, not institutional policy."
    )
    block = next_steps_block(
        [{"text": fact["step"]} for fact in facts[:4] if fact.get("step")],
        title="Evidence and suggested order",
    )
    return ComposedStaffAnswer(
        message=message,
        blocks=[text_block(message), block] if block.get("items") else [text_block(message)],
        evidence_texts=evidence,
    )


def _recommendation_facts(state: StaffDerivedState) -> list[JsonDict]:
    """Ordered, evidence-backed intervention facts. Order encodes urgency:
    overdue blocking work first, then near deadlines, deposit, unanswered
    outreach, then help requests."""

    name = _student_name(state) or "the student"
    facts: list[JsonDict] = []
    for deadline in overdue_deadlines(state):
        facts.append(
            {
                "sentence": (
                    f"{deadline.get('title')} is past due "
                    f"(was due {str(deadline.get('dueAt', ''))[:10]})."
                ),
                "step": f"Address the overdue {deadline.get('title')} first.",
            }
        )
    for blocker in state.blockers:
        if blocker.get("owner") == "university":
            facts.append(
                {
                    "sentence": (
                        f"{blocker.get('title')} is submitted and waiting on "
                        "university review — the ball is on our side."
                    ),
                    "step": f"Nudge the reviewing office for {blocker.get('title')}.",
                }
            )
        else:
            due = f" (due {str(blocker.get('dueAt', ''))[:10]})" if blocker.get("dueAt") else ""
            facts.append(
                {
                    "sentence": f"{blocker.get('title')} is still blocking enrollment{due}.",
                    "step": f"Remind {name} about {blocker.get('title')}.",
                }
            )
    for deadline in deadlines_this_week(state):
        if not any(str(deadline.get("title")) in str(fact["sentence"]) for fact in facts):
            facts.append(
                {
                    "sentence": (
                        f"{deadline.get('title')} is due within the week "
                        f"({str(deadline.get('dueAt', ''))[:10]})."
                    ),
                    "step": f"Flag the {deadline.get('title')} deadline.",
                }
            )
    if state.awaiting_reply is True and state.last_outbound:
        facts.append(
            {
                "sentence": (
                    "The last recorded outbound "
                    f"{state.last_outbound.get('channel')} "
                    f"({str(state.last_outbound.get('occurredAt', ''))[:10]}) got no "
                    "recorded reply."
                ),
                "step": "Follow up on a different channel than last time.",
            }
        )
    elif state.communications is not None and not state.communications.get("events"):
        facts.append(
            {
                "sentence": "No outreach to them is recorded yet.",
                "step": "Make first recorded contact.",
            }
        )
    engagement = state.engagement or {}
    if engagement.get("helpRequested"):
        facts.append(
            {
                "sentence": "They have an open help request.",
                "step": "Answer the help request before new outreach.",
            }
        )
    return facts


def _recommended_action_sentence(state: StaffDerivedState, facts: list[JsonDict]) -> str:
    name = _student_name(state) or "the student"
    student = state.student or {}
    preference = student.get("communicationPreference")
    channel = f" via {preference}" if preference else ""
    first = facts[0]["sentence"] if facts else ""
    if "waiting on university review" in first:
        return "I'd check with the reviewing office before contacting the student."
    return (
        f"I'd reach out to {name} now{channel} about the item above — "
        "I can draft that message for your review."
    )


def _matching_play_sentence(state: StaffDerivedState, facts: list[JsonDict]) -> str | None:
    guidance = state.guidance
    if not guidance:
        return None
    corpus = " ".join(str(fact["sentence"]) for fact in facts).lower()
    for play in guidance.get("corePlays", []):
        entry = _m(play)
        if str(entry.get("status")) != "active":
            continue
        trigger_words = {
            word for word in str(entry.get("trigger", "")).lower().split() if len(word) > 4
        }
        if any(word in corpus for word in trigger_words):
            steps = "; ".join(str(step) for step in entry.get("steps", [])[:3])
            return (
                f"The staff-authored play “{entry.get('title')}” covers this "
                f"situation; its steps: {steps}."
            )
    return None


# ---------------------------------------------------------------------------
# Drafting (read-only deliverables; never model-rewritten)
# ---------------------------------------------------------------------------


def _topic_noun(title: str) -> str:
    """Requirement titles are imperatives ("Submit your official transcript");
    inside a sentence the noun phrase reads correctly."""

    import re as _re

    noun = _re.sub(
        r"^(?:submit|upload|complete|pay|select|provide|register for|finish|choose)\s+",
        "",
        title.strip(),
        flags=_re.IGNORECASE,
    )
    noun = _re.sub(r"^(?:your|the|a)\s+", "", noun, flags=_re.IGNORECASE)
    return noun or title


def _draft_topic(state: StaffDerivedState, reference: str | None = None) -> JsonDict | None:
    """The single item the draft is about.

    A named subject ("...about her missing transcript") wins; otherwise the
    most urgent open item does.
    """

    if reference:
        needle = {"immunization": "immuniz"}.get(reference, reference)
        for source in (state.blockers, state.open_requirements):
            for item in source:
                if needle in str(item.get("title") or "").lower():
                    return {
                        "title": str(item.get("title")),
                        "dueAt": item.get("dueAt"),
                        "overdue": False,
                        "office": item.get("responsibleOffice"),
                    }
    for deadline in overdue_deadlines(state):
        return {
            "title": str(deadline.get("title")),
            "dueAt": deadline.get("dueAt"),
            "overdue": True,
        }
    for blocker in state.blockers:
        if blocker.get("owner") == "student":
            return {
                "title": str(blocker.get("title")),
                "dueAt": blocker.get("dueAt"),
                "overdue": False,
                "office": blocker.get("responsibleOffice"),
            }
    for deadline in deadlines_this_week(state):
        return {
            "title": str(deadline.get("title")),
            "dueAt": deadline.get("dueAt"),
            "overdue": False,
        }
    for item in state.open_requirements:
        return {
            "title": str(item.get("title")),
            "dueAt": item.get("dueAt"),
            "overdue": False,
            "office": item.get("responsibleOffice"),
        }
    return None


def _compose_draft_email(
    classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    student = state.student or {}
    preferred = str(student.get("preferredName") or student.get("name") or "there")
    topic = _draft_topic(state, classification.reference)
    if topic is None:
        message = (
            "Their record shows nothing outstanding to write about — every "
            "requirement is complete. Tell me the subject you have in mind and "
            "I'll draft around it."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    due_clause = ""
    if topic.get("dueAt"):
        due_date = str(topic["dueAt"])[:10]
        due_clause = (
            f" It was due on {due_date}."
            if topic.get("overdue")
            else f" The due date is {due_date}."
        )
    follow_up_clause = ""
    if state.awaiting_reply is True and state.last_outbound:
        follow_up_clause = " I'm following up on our earlier message in case it didn't reach you."
    office = topic.get("office")
    office_clause = f" The {office} team can help if anything is unclear." if office else ""
    noun = _topic_noun(str(topic["title"]))
    subject = f"Action needed: {noun}"
    body_lines = [
        f"Hi {preferred},",
        "",
        (
            f"Our records show your {noun} is still outstanding, and it's "
            f"currently holding up your enrollment.{due_clause}{follow_up_clause}"
        ),
        ("You can complete it from your enrollment checklist in the portal." + office_clause),
        "",
        "Please reach out if you have any questions — we're glad to help.",
        "",
        "[Your name]",
        "[Your office]",
    ]
    preference = student.get("communicationPreference")
    preference_note = (
        f" Note: their recorded communication preference is {preference}."
        if preference and preference != "email"
        else ""
    )
    message = (
        f"Here's a draft email about the {_topic_noun(str(topic['title']))}, grounded in "
        f"{preferred}'s record.{preference_note} {DRAFT_DISCLAIMER}"
    )
    block = {
        "type": "draft",
        "channel": "email",
        "subject": subject,
        "body": "\n".join(body_lines),
        "disclaimer": DRAFT_DISCLAIMER,
        "fallbackText": f"Subject: {subject}\n\n"
        + "\n".join(body_lines)
        + f"\n\n{DRAFT_DISCLAIMER}",
    }
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_draft_sms(
    classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    student = state.student or {}
    preferred = str(student.get("preferredName") or student.get("name") or "there")
    topic = _draft_topic(state, classification.reference)
    if topic is None:
        message = (
            "Their record shows nothing outstanding to text about. Tell me the "
            "subject and I'll draft around it."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    due_clause = f" by {str(topic['dueAt'])[:10]}" if topic.get("dueAt") else ""
    noun = _topic_noun(str(topic["title"]))
    body = (
        f"Hi {preferred}, this is your enrollment team. Your {noun} is "
        f"still needed{due_clause} — you can finish it in the portal checklist. "
        "Reply here with any questions."
    )
    message = f"Here's a draft SMS about the {noun}. {DRAFT_DISCLAIMER}"
    block = {
        "type": "draft",
        "channel": "sms",
        "body": body,
        "disclaimer": DRAFT_DISCLAIMER,
        "fallbackText": f"{body}\n\n{DRAFT_DISCLAIMER}",
    }
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_draft_call_points(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    student = state.student or {}
    preferred = str(student.get("preferredName") or student.get("name") or "the student")
    points: list[JsonDict] = []
    points.append(
        {
            "text": (
                f"Open: confirm you're speaking with {preferred} "
                f"({student.get('programName')}, class of {student.get('classYear')})."
            )
        }
    )
    for blocker in state.blockers[:3]:
        due = f", due {str(blocker.get('dueAt', ''))[:10]}" if blocker.get("dueAt") else ""
        points.append(
            {
                "text": (
                    f"Discuss: {blocker.get('title')} ({blocker.get('status', 'open')}{due}) — "
                    f"{blocker.get('clearingAction')}"
                )
            }
        )
    if state.awaiting_reply is True and state.last_outbound:
        points.append(
            {
                "text": (
                    "Mention: our earlier "
                    f"{state.last_outbound.get('channel')} message from "
                    f"{str(state.last_outbound.get('occurredAt', ''))[:10]} — check it "
                    "was received."
                )
            }
        )
    for deadline in deadlines_this_week(state)[:2]:
        points.append(
            {
                "text": (
                    f"Deadline to flag: {deadline.get('title')} on "
                    f"{str(deadline.get('dueAt', ''))[:10]}."
                )
            }
        )
    points.append({"text": "Close: agree the next step and confirm their preferred channel."})
    if len(points) <= 2:
        message = (
            f"{preferred}'s record shows nothing outstanding, so these points are "
            f"a general check-in. {DRAFT_DISCLAIMER}"
        )
    else:
        message = (
            f"Call talking points for {preferred}, grounded in their record. {DRAFT_DISCLAIMER}"
        )
    block = bullet_list_block(points, title="Talking points")
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


# ---------------------------------------------------------------------------
# Operational composers
# ---------------------------------------------------------------------------


def _compose_work_queue(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    queue = state.queue
    if not queue:
        message = "I couldn't read the work queue just now."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    open_items = open_work_items(state)
    counts = _m(queue.get("counts"))
    if not open_items:
        message = "The queue is clear — no open work items match."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    # The table repeats every field; the prose gives the totals and points at
    # the head of the queue without reciting its row.
    first = open_items[0]
    first_student = _m(first.get("student"))
    message = (
        f"{_count(len(open_items), 'open item')} in canonical order (priority, then due "
        f"date) — {counts.get('urgent', 0)} urgent, "
        f"{counts.get('escalated', 0)} escalated. First up: {first.get('key')} "
        f"for {first_student.get('name')}. The queue:"
    )
    block = table_block(
        [
            {"key": "key", "label": "Key"},
            {"key": "title", "label": "Task"},
            {"key": "student", "label": "Student"},
            {"key": "priority", "label": "Priority"},
            {"key": "due", "label": "Due"},
            {"key": "status", "label": "Status"},
        ],
        [
            {
                "key": str(item.get("key", "")),
                "title": str(item.get("title", ""))[:60],
                "student": str(_m(item.get("student")).get("name", "")),
                "priority": str(item.get("priority", "")),
                "due": str(item.get("dueAt") or "")[:10],
                "status": str(item.get("status", "")).replace("_", " "),
            }
            for item in open_items[:10]
        ],
        caption="Queue (canonical order)",
    )
    open_board = next_steps_block(
        [{"text": "Work the queue from the task board", "href": staff_links.STAFF_TASKS}],
    )
    return ComposedStaffAnswer(
        message=message,
        blocks=[text_block(message), block, open_board],
        evidence_texts=evidence,
    )


def _compose_work_item_detail(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    detail = state.work_item
    if not detail:
        message = "I couldn't find that work item."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    item = _m(detail.get("workItem"))
    student_ref = _m(item.get("student"))
    assignee = _m(item.get("assignee"))
    parts = [
        f"{item.get('key')}: {item.get('title')} — "
        f"{str(item.get('status', '')).replace('_', ' ')}, priority {item.get('priority')}, "
        f"student {student_ref.get('name')}."
    ]
    if assignee:
        parts.append(f"Assigned to {assignee.get('name')} ({assignee.get('component')}).")
    else:
        parts.append("Unassigned.")
    if item.get("dueAt"):
        parts.append(f"Due {str(item['dueAt'])[:10]}.")
    if item.get("escalated"):
        parts.append("It is escalated.")
    interactions = list(detail.get("interactions", []))
    if interactions:
        recorded = sum(
            len(list(_m(interaction).get("communications", []))) for interaction in interactions
        )
        parts.append(
            f"{len(interactions)} interaction(s) with {recorded} recorded communication(s)."
        )
    if item.get("nextStep"):
        parts.append(f"Recorded next step: {item['nextStep']}")
    message = " ".join(parts)
    blocks: list[JsonDict] = [text_block(message)]
    history = list(item.get("history", []))
    if history:
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": f"{str(_m(log).get('occurredAt', ''))[:16]} — "
                        f"{str(_m(log).get('action', '')).replace('_', ' ')}: "
                        f"{str(_m(log).get('message', ''))[:120]}"
                    }
                    for log in history[:8]
                ],
                title="Recent history",
            )
        )
    return ComposedStaffAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_inquiries(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    if state.inquiry_thread:
        thread = state.inquiry_thread
        messages = list(thread.get("messages", []))
        message = f"The inquiry is {thread.get('status')} with {len(messages)} message(s)."
        block = bullet_list_block(
            [
                {
                    "text": f"{_m(entry).get('authorName')} "
                    f"({str(_m(entry).get('createdAt', ''))[:16]}): "
                    f"{str(_m(entry).get('body', ''))[:140]}"
                }
                for entry in messages[:8]
            ],
            title="Thread",
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message), block], evidence_texts=evidence
        )
    inquiries = state.inquiries
    if not inquiries:
        message = "No inquiries match right now."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    new_count = sum(1 for item in inquiries if str(item.get("status")) == "new")
    waiting = sum(1 for item in inquiries if str(item.get("status")) == "waiting_on_student")
    message = f"{len(inquiries)} inquiry(ies): {new_count} new, {waiting} waiting on the student."
    block = bullet_list_block(
        [
            {
                "text": f"{item.get('subject')} — {str(item.get('status', '')).replace('_', ' ')}"
                + (
                    f", assigned to {item.get('assignee')}"
                    if item.get("assignee")
                    else ", unassigned"
                )
            }
            for item in inquiries[:8]
        ],
        title="Inquiries",
    )
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_playbooks(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    guidance = state.guidance
    plays = list(guidance.get("corePlays", [])) if guidance else []
    cards = list(guidance.get("knowledgeCards", [])) if guidance else []
    if not plays and not cards:
        message = (
            "No staff-authored plays or knowledge cards exist yet, and there is "
            "no institutional playbook engine — so I have no policy to cite. I "
            "can still recommend from a student's record, clearly labelled as "
            "my suggestion."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = (
        f"There are {len(plays)} staff-authored core play(s) and {len(cards)} "
        "knowledge card(s). These are prose guidance written by staff — no "
        "engine enforces them."
    )
    blocks: list[JsonDict] = [text_block(message)]
    if plays:
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": f"{_m(play).get('title')} ({_m(play).get('status')}): "
                        f"trigger — {_m(play).get('trigger')}"
                    }
                    for play in plays[:6]
                ],
                title="Core plays",
            )
        )
    if cards:
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": f"{_m(card).get('title')} ({_m(card).get('status')}): "
                        f"{_m(card).get('summary')}"
                    }
                    for card in cards[:6]
                ],
                title="Knowledge cards",
            )
        )
    return ComposedStaffAnswer(message=message, blocks=blocks, evidence_texts=evidence)


def _compose_action_rules(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    rules = state.action_rules
    if not rules:
        message = "No automation rules are configured."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    enabled = [rule for rule in rules if rule.get("enabled")]
    message = (
        f"{len(rules)} automation rule(s) configured, {len(enabled)} enabled. "
        "They create work items from signals — they don't send anything."
    )
    block = bullet_list_block(
        [
            {
                "text": f"{rule.get('name')} "
                f"({'enabled' if rule.get('enabled') else 'disabled'}) — "
                f"{str(rule.get('signalType', '')).replace('_', ' ')}"
                + (f" on {rule.get('requirementCode')}" if rule.get("requirementCode") else "")
                + f", routes {rule.get('priority')} work to {rule.get('component')}"
            }
            for rule in rules[:8]
        ],
        title="Automation rules",
    )
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), block], evidence_texts=evidence
    )


def _compose_general(
    classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    if state.student is not None:
        return _compose_student_overview(classification, state)
    if state.queue is not None or state.attention is not None:
        evidence = build_staff_evidence_bundle(state)
        queue_answer = (
            _compose_work_queue(classification, state) if state.queue is not None else None
        )
        attention_answer = (
            _compose_attention(classification, state)
            if state.attention is not None and (state.attention.get("items"))
            else None
        )
        if queue_answer and attention_answer:
            message = f"{queue_answer.message} Also: {attention_answer.message}"
            blocks = [*queue_answer.blocks, *attention_answer.blocks[1:]]
            return ComposedStaffAnswer(message=message, blocks=blocks, evidence_texts=evidence)
        if queue_answer:
            return queue_answer
        if attention_answer:
            return attention_answer
    message = (
        "I can help with a specific student, your work queue, attention "
        "signals, communications, or a draft. What would you like to look at?"
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message)])


def _compose_cohort_search(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    """List a cohort, always with the total behind the page."""

    evidence = build_staff_evidence_bundle(state)
    cohort = state.cohort
    if cohort is None:
        message = (
            "I couldn't run that cohort query just now. The staff roster has the same filters."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    clauses = [str(clause) for clause in cohort.get("filter") or []]
    described = "; ".join(clauses) if clauses else "no filter applied"
    total = int(cohort.get("total") or 0)
    items = [_m(item) for item in cohort.get("items") or []]
    if total == 0:
        message = f"No students match that ({described})."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = f"{_count(total, 'student matches', 'students match')} ({described})."
    if cohort.get("truncated"):
        message += f" Showing the first {len(items)} — the total counts them all."
    else:
        message += " Here they are:"
    block = table_block(
        [
            {"key": "name", "label": "Student"},
            {"key": "program", "label": "Program"},
            {"key": "deposit", "label": "Deposit"},
            {"key": "blocking", "label": "Open blocking", "align": "right"},
        ],
        [
            {
                "name": str(item.get("name", "")),
                "program": str(item.get("programName", "")),
                "deposit": str(
                    item.get("depositState") or ("paid" if item.get("depositPaid") else "unpaid")
                ),
                "blocking": str(_m(item.get("requirements")).get("openBlocking", "")),
            }
            for item in items
        ],
        caption="Matching students",
    )
    directory = next_steps_block(
        [
            {
                "text": "Open the student directory for the full record view",
                "href": staff_links.STAFF_STUDENTS,
            }
        ],
    )
    return ComposedStaffAnswer(
        message=message,
        blocks=[text_block(message), block, directory],
        evidence_texts=evidence,
    )


def _compose_cohort_aggregate(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    summary = state.cohort_summary
    if summary is None:
        message = "I couldn't run that count just now. The staff roster has the same filters."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    clauses = [str(clause) for clause in summary.get("filter") or []]
    described = "; ".join(clauses) if clauses else "no filter applied"
    matching = int(summary.get("matchingStudents") or 0)
    represents = str(summary.get("countsRepresent") or "students")
    buckets = [_m(bucket) for bucket in summary.get("buckets") or []]
    message = f"{_count(matching, 'student matches', 'students match')} ({described})."
    if buckets:
        message += f" Broken down by {summary.get('groupBy')}:"
        if represents != "students":
            message += (
                f" (these counts are {represents}, so one student can appear in more than one row)"
            )
    blocks = [text_block(message)]
    if buckets:
        blocks.append(
            table_block(
                [
                    {"key": "group", "label": str(summary.get("groupBy") or "Group")},
                    {"key": "count", "label": "Count", "align": "right"},
                ],
                [
                    {
                        "group": str(bucket.get("value", "")),
                        "count": str(bucket.get("count", "")),
                    }
                    for bucket in buckets
                ],
                caption=f"By {summary.get('groupBy')}",
            )
        )
    return ComposedStaffAnswer(message=message, blocks=blocks, evidence_texts=evidence)


_COMPOSERS = {
    "greeting": _compose_greeting,
    "capability_overview": _compose_capabilities,
    "action_request": _compose_action_request,
    "unsupported_metric": _compose_unsupported_metric,
    "draft_email": _compose_draft_email,
    "draft_sms": _compose_draft_sms,
    "draft_call_points": _compose_draft_call_points,
    "student_overview": _compose_student_overview,
    "student_missing_items": _compose_missing_items,
    "student_blockers": _compose_blockers,
    "student_documents": _compose_documents,
    "student_deadlines": _compose_deadlines,
    "student_financials": _compose_financials,
    "student_housing": _compose_housing,
    "student_appointments": _compose_appointments,
    "student_communications": _compose_communications,
    "student_engagement": _compose_engagement,
    "student_timeline": _compose_timeline,
    "student_ownership": _compose_ownership,
    "cohort_search": _compose_cohort_search,
    "cohort_aggregate": _compose_cohort_aggregate,
    "attention_ranking": _compose_attention,
    "recommendation": _compose_recommendation,
    "work_queue": _compose_work_queue,
    "work_item_detail": _compose_work_item_detail,
    "inquiries": _compose_inquiries,
    "playbook_lookup": _compose_playbooks,
    "action_rules": _compose_action_rules,
    "general_question": _compose_general,
    "unsupported_or_out_of_scope": _compose_unsupported,
}


def _unavailable_notes(state: StaffDerivedState) -> list[str]:
    labels = {
        "searchStudents": "the student roster",
        "findStudents": "the student cohort query",
        "summarizeStudents": "the cohort counts",
        "getStudentStaffSummary": "the student's overview",
        "getStudentRequirements": "their requirements",
        "getStudentDocuments": "their documents",
        "getStudentBlockers": "their blockers",
        "getStudentDeadlines": "their deadlines",
        "getStudentFinancialState": "their financial state",
        "getStudentHousingState": "their housing state",
        "getStudentAppointments": "their appointments",
        "getStudentCommunicationHistory": "their communication history",
        "getStudentEngagementSignals": "their engagement snapshot",
        "getStudentTimeline": "their timeline",
        "getStudentOwnership": "case ownership",
        "getStudentsNeedingAttention": "the attention queue",
        "getStaffWorkQueue": "the work queue",
        "getWorkItemDetail": "the work item",
        "getInquiries": "inquiries",
        "getInquiryThread": "the inquiry thread",
        "getPlaybooks": "staff guidance",
        "getActionRules": "automation rules",
    }
    notes = []
    for item in state.unavailable_data:
        label = labels.get(str(item.get("source")))
        if label:
            notes.append(f"I couldn't check {label} just now, so I've left it out.")
    return notes[:2]


def _student_name(state: StaffDerivedState) -> str | None:
    student = state.student
    if not student:
        return None
    return str(student.get("preferredName") or student.get("name") or "") or None


def _join_titles(items: Sequence[dict[str, Any]]) -> str:
    titles = [str(item.get("title") or item.get("name") or "item") for item in items[:4]]
    if len(titles) <= 1:
        return titles[0] if titles else ""
    return ", ".join(titles[:-1]) + f" and {titles[-1]}"


def _count(number: int, singular: str, plural: str | None = None) -> str:
    """ "3 open items", "1 blocker" — never the "(s)" shorthand."""

    word = singular if number == 1 else (plural or f"{singular}s")
    return f"{number} {word}"


def _m(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _usd(cents: Any) -> str:
    try:
        value = int(cents)
    except (TypeError, ValueError):
        return "$0"
    dollars = value / 100
    return f"${dollars:,.2f}".removesuffix(".00")
