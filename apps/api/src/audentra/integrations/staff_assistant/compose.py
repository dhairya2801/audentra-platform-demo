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
    "call talking points for your review. I can also prioritize recent mail "
    "from mailboxes you are authorized to read. I'm read-only: I never send, "
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
    # Facts the deterministic draft states that a rewrite must not drop —
    # (phrase that must survive, sentence restored when it did not). Used for
    # identity: an overview that loses "Chemistry, class of 2030" has lost the
    # part that told the reader *which* student this is.
    required_phrases: list[tuple[str, str]] = field(default_factory=list)


def compose_staff_deterministic(
    classification: StaffClassification,
    state: StaffDerivedState,
) -> ComposedStaffAnswer:
    composer = _COMPOSERS.get(classification.request_type, _compose_general)
    answer = composer(classification, state)
    _append_additional_intents(answer, classification, state)
    for note in _unavailable_notes(state):
        answer.message = f"{answer.message} {note}"
    answer.message = answer.message.strip()[:1_600]
    return answer


def _append_additional_intents(
    answer: ComposedStaffAnswer,
    classification: StaffClassification,
    state: StaffDerivedState,
) -> None:
    """Answer the other clauses of a multi-intent question.

    Each additional intent is composed by its own canonical composer over the
    same derived state — so the second and third clauses are answered from
    reads, never from the model's sense of what was probably true. Duplicate
    blocks are dropped, and the whole answer stays inside the message bound.
    """

    if not classification.additional_request_types:
        return
    seen_blocks = {_block_identity(block) for block in answer.blocks}
    for request_type in classification.additional_request_types:
        composer = _COMPOSERS.get(request_type)
        if composer is None:
            continue
        supplement = composer(
            StaffClassification(request_type, classification.confidence, source="multi_intent"),
            state,
        )
        sentence = supplement.message.strip()
        if sentence and sentence not in answer.message:
            answer.message = f"{answer.message} {sentence}".strip()
        for block in supplement.blocks:
            identity = _block_identity(block)
            if identity in seen_blocks:
                continue
            seen_blocks.add(identity)
            answer.blocks.append(block)
        for fact in supplement.evidence_texts:
            if fact not in answer.evidence_texts:
                answer.evidence_texts.append(fact)


def _block_identity(block: JsonDict) -> tuple[str, str]:
    return (str(block.get("type") or ""), str(block.get("title") or block.get("text") or "")[:80])


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
        dimension = str(summary.get("groupBy") or "group").replace("_", " ")
        for entry in summary.get("buckets") or []:
            bucket = _m(entry)
            # Name the dimension rather than the word "Group": a rewrite that
            # copies the evidence line verbatim otherwise says "Group
            # Psychology has 60 students".
            lines.append(
                f"{dimension.capitalize()} {bucket.get('value')}: "
                f"{bucket.get('count')} {represents}"
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
            f"Work queue counts (whole board): {counts.get('open', 0)} open — "
            f"{counts.get('todo', 0)} to do, "
            f"{counts.get('inProgress', 0)} in progress, "
            f"{counts.get('followUpRequired', 0)} follow-up required, "
            f"{counts.get('blocked', 0)} blocked; {counts.get('urgent', 0)} urgent, "
            f"{counts.get('escalated', 0)} escalated, {counts.get('overdue', 0)} overdue, "
            f"{counts.get('unassigned', 0)} unassigned, {counts.get('stale', 0)} stale, "
            f"{counts.get('ownerRisk', 0)} owned by someone departed, on leave or away"
        )
        filters = {
            key: value
            for key, value in _m(queue.get("filters")).items()
            if value not in (None, "", "all")
        }
        if filters:
            page = _m(queue.get("page"))
            lines.append(
                f"Queue read with filters {filters}: {queue.get('filteredOpen', 0)} open items "
                f"match in total across {queue.get('distinctOpenStudents', 0)} students; the "
                f"page below lists the first {page.get('returned', 0)}"
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
        for component in queue.get("byComponent", [])[:12]:
            row = _m(component)
            lines.append(
                f"Component {row.get('component')}: {row.get('open', 0)} open, "
                f"{row.get('overdue', 0)} overdue, {row.get('unassigned', 0)} unassigned, "
                f"{row.get('stale', 0)} stale, {row.get('ownerRisk', 0)} owned by someone "
                "departed, on leave or away"
            )
        for owner in queue.get("byOwner", [])[:12]:
            row = _m(owner)
            status = row.get("employmentStatus")
            away = row.get("awayUntil")
            note = (
                f" ({status.replace('_', ' ')})"
                if status and status != "active"
                else (f" (away until {str(away)[:10]})" if away else "")
            )
            lines.append(
                f"Owner {row.get('name')}{note}: {row.get('open', 0)} open, "
                f"{row.get('overdue', 0)} overdue, {row.get('stale', 0)} stale in progress"
            )
    for person in state.staff_members[:5]:
        lines.append(_staff_member_sentence(person))
    briefing = state.briefing
    capacity = _m((briefing or {}).get("staffCapacity"))
    if capacity.get("available"):
        summary = _m(capacity.get("summary"))
        lines.append(
            f"Staff capacity: {summary.get('staff', 0)} staff, {summary.get('onLeave', 0)} on "
            f"leave, {summary.get('departed', 0)} departed, {summary.get('awayNow', 0)} away "
            f"today, {summary.get('overCap', 0)} over caseload cap, "
            f"{summary.get('spareCapacity', 0)} with spare capacity, "
            f"{summary.get('itemsOwnedByUnavailable', 0)} open items owned by someone "
            f"unavailable, {summary.get('staleItems', 0)} stale in-progress items, "
            f"{summary.get('depositedWithoutAdviser', 0)} deposited students without an adviser"
        )
        for signal in capacity.get("signals", [])[:10]:
            row = _m(signal)
            lines.append(
                f"Staff signal ({row.get('severity')}): {row.get('title')} — "
                f"{row.get('detail')} Suggested: {row.get('action')}"
            )
    elif briefing is not None and "staffCapacity" in briefing:
        lines.append("Staff capacity (caseload, leave, availability) is not readable here.")
    if state.inquiry_counts:
        counts = state.inquiry_counts
        oldest = state.oldest_awaiting_reply
        lines.append(
            f"Student requests: {counts.get('active', 0)} active, "
            f"{counts.get('awaitingFirstReply', 0)} awaiting a first reply "
            f"({counts.get('awaitingOver24h', 0)} for more than 24 hours), "
            f"{counts.get('unassigned', 0)} unassigned"
            + (
                f"; oldest awaiting a reply: {oldest.get('student')} — {oldest.get('subject')} "
                f"(opened {str(oldest.get('createdAt'))[:16]})"
                if oldest
                else ""
            )
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
    for message in state.mailbox_messages[:25]:
        reasons = ", ".join(
            str(reason).replace("_", " ") for reason in message.get("priorityReasons", [])
        )
        lines.append(
            f"Authorized mailbox message: {message.get('receivedAt')} from "
            f"{message.get('sender')} to {message.get('mailboxAddress')} â€” "
            f"{message.get('subject') or '(no subject)'}"
            + (
                f"; linked student {message.get('linkedStudentName')}"
                if message.get("linkedStudentName")
                else ""
            )
            + (f"; priority reasons: {reasons}" if reasons else "")
            + f"; body: {str(message.get('body') or '')[:300]}"
        )
    if state.mailbox_ranking_method:
        lines.append(f"Mailbox ranking method: {state.mailbox_ranking_method}")
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
    if student.get("onboardingStatus"):
        parts.append(f"Onboarding {str(student['onboardingStatus']).replace('_', ' ')}.")
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
    identity = parts[0]
    required = [
        (str(value), identity)
        for value in (student.get("programName"), student.get("classYear"))
        if value
    ]
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
    return ComposedStaffAnswer(
        message=message,
        blocks=blocks,
        evidence_texts=evidence,
        required_phrases=required,
    )


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
    completed = len(state.requirements) - len(open_items)
    message = (
        f"{name} has completed {completed} of {len(state.requirements)} requirements and "
        f"still has {len(open_items)} open requirement(s)"
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


def _compose_daily_briefing(
    _classification: StaffClassification,
    state: StaffDerivedState,
) -> ComposedStaffAnswer:
    """The Staff Portal's Morning Brew, answered as prose plus its own blocks.

    Every number here is one the briefing already computed and the staff
    member can already see; the briefing's own "not tracked" list is carried
    through so a missing metric reads as missing rather than as zero.
    """

    briefing = state.briefing
    if not briefing:
        message = (
            "I couldn't read today's briefing just now. I can still show your "
            "work queue, the attention scan, or any student's record."
        )
        return ComposedStaffAnswer(message=message, blocks=[text_block(message)])

    evidence: list[str] = []
    sentences: list[str] = []
    headline = str(briefing.get("headline") or "").strip()
    if headline:
        sentences.append(headline)
        evidence.append(f"Briefing headline: {headline}")
    window = str(briefing.get("window") or "").strip()
    if window:
        evidence.append(f"Briefing window: {window}.")

    bullets = [str(item) for item in briefing.get("bullets") or [] if str(item).strip()]
    for bullet in bullets:
        evidence.append(f"Briefing point: {bullet}")

    attention = [item for item in briefing.get("attention") or [] if isinstance(item, dict)]
    for item in attention:
        title = str(item.get("title") or "")
        count = item.get("count")
        if title:
            evidence.append(
                f"Attention theme: {title}"
                + (f" — {count} student(s)." if isinstance(count, int) else ".")
            )

    staff_work = briefing.get("staffWork") or {}
    if isinstance(staff_work, dict) and staff_work:
        open_items = staff_work.get("openItems")
        assigned = staff_work.get("assignedToMe")
        urgent = staff_work.get("urgent")
        overdue = staff_work.get("overdue")
        parts = []
        if isinstance(open_items, int):
            parts.append(f"{open_items} open Action Center item(s)")
        if isinstance(assigned, int):
            parts.append(f"{assigned} assigned to you")
        if isinstance(urgent, int):
            parts.append(f"{urgent} urgent")
        if isinstance(overdue, int):
            parts.append(f"{overdue} overdue")
        if parts:
            summary = "Work queue: " + ", ".join(parts) + "."
            evidence.append(summary)
            sentences.append(summary)

    requests = briefing.get("requests") or {}
    if isinstance(requests, dict) and isinstance(requests.get("total"), int):
        evidence.append(
            f"Open student requests: {requests['total']}, "
            f"{requests.get('awaitingFirstReply', 0)} awaiting a first reply."
        )

    unsupported = [item for item in briefing.get("unsupported") or [] if isinstance(item, dict)]
    if unsupported:
        evidence.append(
            "The briefing states these are not tracked: "
            + "; ".join(str(item.get("metric")) for item in unsupported[:3])
            + "."
        )

    blocks: list[JsonDict] = []
    if bullets:
        blocks.append(
            numbered_or_bullet(
                [{"text": bullet} for bullet in bullets],
                title="This morning",
            )
        )
    if attention:
        blocks.append(
            numbered_or_bullet(
                [
                    {
                        "text": str(item.get("title") or "")
                        + (
                            f" — {item.get('count')} student(s)"
                            if isinstance(item.get("count"), int)
                            else ""
                        )
                    }
                    for item in attention
                ],
                title="Where attention is concentrated",
            )
        )
    message = " ".join(sentences).strip() or headline or "Here is today's briefing."
    blocks.insert(0, text_block(message))
    return ComposedStaffAnswer(message=message, blocks=blocks, evidence_texts=evidence)


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
    filters = _m(queue.get("filters"))
    active_filters = {
        key: value for key, value in filters.items() if value not in (None, "", "all")
    }
    if not open_items:
        scope = f" matching {_describe_queue_filters(active_filters)}" if active_filters else ""
        message = f"The queue is clear — no open work items{scope}."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    # The table repeats every field; the prose gives the totals and points at
    # the head of the queue without reciting its row.
    first = open_items[0]
    first_student = _m(first.get("student"))
    if active_filters:
        # A filtered slice must report its own numbers, never blend the
        # whole-queue counts with a filtered head item.
        filtered_open = int(queue.get("filteredOpen") or len(open_items))
        distinct = queue.get("distinctOpenStudents")
        students_part = (
            f" across {_count(int(distinct), 'student')}" if distinct is not None else ""
        )
        message = (
            f"{_count(filtered_open, 'open Action Center item')}{students_part} matching "
            f"{_describe_queue_filters(active_filters)}, in canonical order (priority, "
            f"then due date). First up: {first.get('key')} for "
            f"{first_student.get('name')}. The matching items:"
        )
    else:
        # `open_items` is the returned page (the read caps `items` at 25); the
        # queue's own open count is the number to state. Saying the page size
        # here is how a 1,027-item queue got reported as 25 items.
        total_open = int(queue.get("filteredOpen") or counts.get("todo") or len(open_items))
        message = (
            f"{_count(total_open, 'open item')} in canonical order (priority, then due "
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


def _staff_member_sentence(person: JsonDict) -> str:
    caseload = _m(person.get("caseload"))
    work = _m(person.get("work"))
    availability = _m(person.get("availability"))
    status = str(person.get("employmentStatus") or "active")
    if status == "on_leave":
        state = f"on leave until {str(person.get('leaveUntil') or 'an unknown date')[:10]}"
    elif status == "departed":
        state = f"departed {str(person.get('endedAt') or '')[:10]}".strip()
    elif person.get("awayUntil"):
        state = (
            f"active but {person.get('awayKind') or 'away'} until "
            f"{str(person.get('awayUntil'))[:10]}"
        )
    else:
        state = "active"
    advisees = int(caseload.get("primaryAdvisees") or 0)
    cap = caseload.get("cap")
    caseload_text = (
        f"{advisees} primary advisees" + (f" against a cap of {cap}" if cap else "")
        if advisees or cap
        else "no primary advisees"
    )
    slots = availability.get("openSlotsNext14Days")
    slot_text = (
        f"{slots} open slots in the next 14 days"
        + (
            f", next {str(availability.get('nextOpenSlotAt'))[:16]}"
            if availability.get("nextOpenSlotAt")
            else ""
        )
        if slots is not None
        else "no bookable calendar"
    )
    return (
        f"Staff member {person.get('name')} ({person.get('title') or 'staff'}, "
        f"{person.get('component')}): {state}; {caseload_text}; "
        f"{work.get('open', 0)} open items ({work.get('overdue', 0)} overdue, "
        f"{work.get('staleInProgress', 0)} stale in progress, "
        f"{work.get('appointmentsAwaitingOutcome', 0)} appointments awaiting an outcome); "
        f"{slot_text}."
    )


def _compose_staff_workload(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    """People and offices: who is behind, who is absent, where capacity is.

    Built from the briefing's staff-capacity signals and the queue's per-owner
    rollup — the same rules the Morning Brew shows, never a per-person score.
    """

    evidence = build_staff_evidence_bundle(state)
    capacity = _m((state.briefing or {}).get("staffCapacity"))
    owners = [_m(row) for row in _m(state.queue).get("byOwner", [])]
    blocks: list[JsonDict] = []
    sentences: list[str] = []
    if state.staff_members:
        # A named person comes first; the tenant-wide picture follows.
        sentences.extend(_staff_member_sentence(person) for person in state.staff_members[:3])
        queue_items = open_work_items(state)
        if queue_items:
            first = queue_items[0]
            matching = int(_m(state.queue).get("filteredOpen") or len(queue_items))
            sentences.append(
                f"{_count(matching, 'matching open item')} in their queue; first up "
                f"{first.get('key')}: {first.get('title')}."
            )
            blocks.append(
                table_block(
                    [
                        {"key": "key", "label": "Key"},
                        {"key": "title", "label": "Task"},
                        {"key": "status", "label": "Status"},
                        {"key": "due", "label": "Due"},
                        {"key": "stale", "label": "Stale"},
                    ],
                    [
                        {
                            "key": str(item.get("key", "")),
                            "title": str(item.get("title", ""))[:60],
                            "status": str(item.get("status", "")).replace("_", " "),
                            "due": str(item.get("dueAt") or "")[:10],
                            "stale": (
                                f"{_m(item.get('signals')).get('staleDays')}d"
                                if _m(item.get("signals")).get("stale")
                                else ""
                            ),
                        }
                        for item in queue_items[:10]
                    ],
                    caption="Their open items (canonical order)",
                )
            )
    if capacity.get("available") and not state.staff_members:
        summary = _m(capacity.get("summary"))
        sentences.append(
            f"{_count(int(summary.get('staff', 0)), 'staff member')}: "
            f"{summary.get('onLeave', 0)} on leave, {summary.get('departed', 0)} departed, "
            f"{summary.get('awayNow', 0)} away today, {summary.get('overCap', 0)} over "
            f"caseload cap, {summary.get('spareCapacity', 0)} with spare capacity."
        )
        signals = [_m(item) for item in capacity.get("signals", [])]
        if signals:
            lead = signals[0]
            sentences.append(f"Most severe: {lead.get('title')} — {lead.get('detail')}")
            blocks.append(
                bullet_list_block(
                    [
                        {
                            "text": f"[{item.get('severity')}] {item.get('title')}: "
                            f"{item.get('detail')} → {item.get('action')}"
                        }
                        for item in signals[:10]
                    ],
                    title="Staff and capacity signals",
                )
            )
    elif state.briefing is not None:
        sentences.append(
            "Caseload, leave and availability are not readable on this deployment; "
            "the queue's per-owner totals are below."
        )
    components = [_m(row) for row in _m(state.queue).get("byComponent", [])]
    if components and not state.staff_members:
        behind = max(components, key=lambda row: int(row.get("overdue") or 0))
        most_unassigned = max(components, key=lambda row: int(row.get("unassigned") or 0))
        sentences.append(
            f"By office, {behind.get('component')} has the most overdue work "
            f"({behind.get('overdue', 0)} of {behind.get('open', 0)} open)"
            + (
                f" and {most_unassigned.get('component')} the most unassigned "
                f"({most_unassigned.get('unassigned', 0)})."
                if int(most_unassigned.get("unassigned") or 0)
                else "."
            )
        )
        blocks.append(
            table_block(
                [
                    {"key": "component", "label": "Office"},
                    {"key": "open", "label": "Open"},
                    {"key": "overdue", "label": "Overdue"},
                    {"key": "unassigned", "label": "Unassigned"},
                    {"key": "stale", "label": "Stale"},
                    {"key": "risk", "label": "Owner unavailable"},
                ],
                [
                    {
                        "component": str(row.get("component")),
                        "open": str(row.get("open", 0)),
                        "overdue": str(row.get("overdue", 0)),
                        "unassigned": str(row.get("unassigned", 0)),
                        "stale": str(row.get("stale", 0)),
                        "risk": str(row.get("ownerRisk", 0)),
                    }
                    for row in components[:12]
                ],
                caption="Open work by office (most open first)",
            )
        )
    named = [row for row in owners if row.get("name") and row.get("name") != "Unassigned"]
    if named and not state.staff_members:
        top = named[0]
        sentences.append(
            f"By open work, {top.get('name')} carries the most: {top.get('open', 0)} open, "
            f"{top.get('overdue', 0)} overdue."
        )
        blocks.append(
            table_block(
                [
                    {"key": "owner", "label": "Owner"},
                    {"key": "state", "label": "Status"},
                    {"key": "open", "label": "Open"},
                    {"key": "overdue", "label": "Overdue"},
                    {"key": "stale", "label": "Stale"},
                ],
                [
                    {
                        "owner": str(row.get("name")),
                        "state": str(row.get("employmentStatus") or "active").replace("_", " ")
                        + (" · away" if row.get("awayUntil") else ""),
                        "open": str(row.get("open", 0)),
                        "overdue": str(row.get("overdue", 0)),
                        "stale": str(row.get("stale", 0)),
                    }
                    for row in named[:12]
                ],
                caption="Open work by owner (most open first)",
            )
        )
    if not sentences:
        message = "I couldn't read the staff workload just now."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    message = " ".join(sentences)
    return ComposedStaffAnswer(
        message=message, blocks=[text_block(message), *blocks], evidence_texts=evidence
    )


def _describe_queue_filters(active: dict[str, Any]) -> str:
    labels = {
        "ownership": "ownership",
        "component": "component",
        "status": "status",
        "dueWindow": "due window",
        "topic": "topic",
    }
    return "; ".join(f"{labels.get(key, key)} “{value}”" for key, value in active.items())


def _compose_student_action_center(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    """Answer Action Center membership from the student's actual open staff
    work items — never inferred from their blockers."""

    evidence = build_staff_evidence_bundle(state)
    name = _student_name(state) or "This student"
    if state.student is None:
        message = "I couldn't read that student's record just now."
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    open_work = state.open_work
    blocker_titles = _join_titles(state.blockers) if state.blockers else None
    blocks: list[JsonDict] = []
    if open_work:
        first = _m(open_work[0])
        message = (
            f"Yes — {name} is in the Action Center with "
            f"{_count(len(open_work), 'open staff work item')}. "
            f"{first.get('key')}: {first.get('title')} "
            f"({str(first.get('priority') or '').strip() or 'unprioritized'}, "
            f"{str(first.get('status', '')).replace('_', ' ')}) is what put them there."
        )
        if blocker_titles:
            message += f" On the student's own side, they are blocked on: {blocker_titles}."
        blocks.append(text_block(message))
        blocks.append(
            bullet_list_block(
                [
                    {
                        "text": f"{item.get('key')}: {item.get('title')} — "
                        f"{str(item.get('status', '')).replace('_', ' ')} "
                        f"({item.get('priority')})"
                    }
                    for item in open_work[:5]
                ],
                title="Open staff work",
            )
        )
    else:
        message = (
            f"No — {name} is not in the Action Center right now: there are no "
            "open staff work items on their record."
        )
        if blocker_titles:
            message += (
                f" They do still have open blockers on their side ({blocker_titles}), "
                "but no staff task is currently queued for them."
            )
        else:
            message += " Nothing is blocking them either."
        blocks.append(text_block(message))
    return ComposedStaffAnswer(message=message, blocks=blocks, evidence_texts=evidence)


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
    counts = state.inquiry_counts or {}
    new_count = int(
        counts.get("awaitingFirstReply")
        or sum(1 for item in inquiries if str(item.get("status")) == "new")
    )
    waiting = sum(1 for item in inquiries if str(item.get("status")) == "waiting_on_student")
    if counts:
        oldest = state.oldest_awaiting_reply
        message = (
            f"{_count(int(counts.get('active', len(inquiries))), 'active student request')}: "
            f"{new_count} awaiting a first reply"
            f" ({counts.get('awaitingOver24h', 0)} for more than 24 hours), "
            f"{counts.get('unassigned', 0)} unassigned."
            + (
                f" The oldest still waiting is {oldest.get('student')} — "
                f"{oldest.get('subject')} (opened {str(oldest.get('createdAt'))[:10]})."
                if oldest
                else ""
            )
        )
    else:
        message = (
            f"{len(inquiries)} inquiry(ies): {new_count} new, {waiting} waiting on the student."
        )
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


def _compose_mailbox_read(
    _classification: StaffClassification, state: StaffDerivedState
) -> ComposedStaffAnswer:
    evidence = build_staff_evidence_bundle(state)
    messages = state.mailbox_messages
    if not messages:
        message = (
            "I found no messages in the authorized seven-day mailbox cache. "
            "Connect or refresh a mailbox in the Mailboxes workspace, or use its "
            "provider search for older mail."
        )
        return ComposedStaffAnswer(
            message=message, blocks=[text_block(message)], evidence_texts=evidence
        )
    urgent = sum(1 for item in messages if int(item.get("priorityScore") or 0) == 2)
    linked = sum(1 for item in messages if item.get("linkedStudentId"))
    message = (
        f"I found {len(messages)} recent authorized message(s): {urgent} contain explicit "
        f"urgent or time-sensitive language and {linked} match a student record. "
        "The order is deterministicâ€”keywords, then student match, then recencyâ€”"
        "not an AI risk score."
    )
    block = table_block(
        [
            {"key": "priority", "label": "Priority basis"},
            {"key": "from", "label": "From"},
            {"key": "subject", "label": "Subject"},
            {"key": "student", "label": "Student"},
            {"key": "received", "label": "Received"},
        ],
        [
            {
                "priority": ", ".join(
                    str(reason).replace("_", " ") for reason in item.get("priorityReasons", [])
                )
                or "recency",
                "from": str(item.get("sender") or ""),
                "subject": str(item.get("subject") or "(No subject)"),
                "student": str(item.get("linkedStudentName") or "â€”"),
                "received": str(item.get("receivedAt") or "")[:16],
            }
            for item in messages[:10]
        ],
        caption="Recent authorized email priorities",
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
    "student_action_center": _compose_student_action_center,
    "cohort_search": _compose_cohort_search,
    "cohort_aggregate": _compose_cohort_aggregate,
    "attention_ranking": _compose_attention,
    "recommendation": _compose_recommendation,
    "work_queue": _compose_work_queue,
    "staff_workload": _compose_staff_workload,
    "daily_briefing": _compose_daily_briefing,
    "work_item_detail": _compose_work_item_detail,
    "inquiries": _compose_inquiries,
    "playbook_lookup": _compose_playbooks,
    "action_rules": _compose_action_rules,
    "mailbox_read": _compose_mailbox_read,
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
        "getMailboxMessages": "authorized mailbox messages",
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
