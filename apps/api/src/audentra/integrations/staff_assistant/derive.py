"""Deterministic derived state for one staff assistant turn.

Everything a correct staff answer needs that is computable without a model:
the resolved student's overview, blockers, deadline buckets, the
outbound-without-reply signal, queue slices, and the attention list — derived
from the tool reads of this turn only. No field here survives across turns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from audentra.integrations.staff_assistant.tools import StaffToolExecution

JsonDict = dict[str, Any]

_DONE = {"completed", "waived", "not_applicable"}
_OPEN_WORK = {"todo", "in_progress", "follow_up_required", "blocked"}


@dataclass
class StaffDerivedState:
    # Referent resolution results (set by the pipeline, not by reads).
    student: JsonDict | None = None
    search_results: list[JsonDict] = field(default_factory=list)
    # Student-scoped reads.
    requirements: list[JsonDict] = field(default_factory=list)
    open_requirements: list[JsonDict] = field(default_factory=list)
    documents: list[JsonDict] = field(default_factory=list)
    blockers: list[JsonDict] = field(default_factory=list)
    hold_system_operated: bool = False
    deadlines: list[JsonDict] = field(default_factory=list)
    financials: JsonDict | None = None
    housing: JsonDict | None = None
    appointments: list[JsonDict] = field(default_factory=list)
    communications: JsonDict | None = None
    engagement: JsonDict | None = None
    timeline: list[JsonDict] = field(default_factory=list)
    ownership: JsonDict | None = None
    open_work: list[JsonDict] = field(default_factory=list)
    # Cohort reads.
    cohort: JsonDict | None = None
    cohort_summary: JsonDict | None = None
    # Operational reads.
    attention: JsonDict | None = None
    queue: JsonDict | None = None
    briefing: JsonDict | None = None
    work_item: JsonDict | None = None
    inquiries: list[JsonDict] = field(default_factory=list)
    inquiry_thread: JsonDict | None = None
    guidance: JsonDict | None = None
    action_rules: list[JsonDict] = field(default_factory=list)
    # Derived communication signals.
    awaiting_reply: bool | None = None
    last_outbound: JsonDict | None = None
    last_inbound: JsonDict | None = None
    unavailable_data: list[JsonDict] = field(default_factory=list)
    rejected_arguments: list[JsonDict] = field(default_factory=list)


def derive_staff_state(execution: StaffToolExecution) -> StaffDerivedState:
    state = StaffDerivedState(
        unavailable_data=list(execution.unavailable_data),
        rejected_arguments=list(execution.rejected_arguments),
    )
    reads: dict[str, Mapping[str, Any]] = {
        name: data
        for name, read in execution.reads.items()
        if read.get("status") == "available" and isinstance(data := read.get("data"), Mapping)
    }

    search = reads.get("searchStudents")
    if search:
        state.search_results = [dict(_mapping(item)) for item in _sequence(search.get("items"))]

    summary = reads.get("getStudentStaffSummary")
    if summary:
        state.student = dict(summary)
        state.open_work = [dict(_mapping(item)) for item in _sequence(summary.get("openWork"))]

    requirements = reads.get("getStudentRequirements")
    if requirements:
        state.requirements = [dict(item) for item in _items(requirements)]
        state.open_requirements = [
            item for item in state.requirements if str(item.get("status") or "") not in _DONE
        ]

    documents = reads.get("getStudentDocuments")
    if documents:
        state.documents = [dict(item) for item in _items(documents)]

    blockers = reads.get("getStudentBlockers")
    if blockers:
        state.blockers = [
            dict(_mapping(item)) for item in _sequence(blockers.get("derivedBlockers"))
        ]
        state.hold_system_operated = str(blockers.get("holdSystem") or "") == "operated"

    deadlines = reads.get("getStudentDeadlines")
    if deadlines:
        state.deadlines = [dict(item) for item in _items(deadlines)]

    financials = reads.get("getStudentFinancialState")
    if financials:
        state.financials = dict(financials)

    housing = reads.get("getStudentHousingState")
    if housing:
        state.housing = dict(housing)

    appointments = reads.get("getStudentAppointments")
    if appointments:
        state.appointments = [dict(item) for item in _items(appointments)]

    communications = reads.get("getStudentCommunicationHistory")
    if communications:
        state.communications = dict(communications)
        events = [_mapping(item) for item in _sequence(communications.get("events"))]
        outbound = [event for event in events if str(event.get("direction")) == "outbound"]
        inbound = [event for event in events if str(event.get("direction")) == "inbound"]
        state.last_outbound = dict(outbound[0]) if outbound else None
        state.last_inbound = dict(inbound[0]) if inbound else None
        if outbound:
            last_out = str(outbound[0].get("occurredAt") or "")
            last_in = str(inbound[0].get("occurredAt") or "") if inbound else ""
            state.awaiting_reply = not inbound or last_out > last_in
        elif events:
            state.awaiting_reply = False

    engagement = reads.get("getStudentEngagementSignals")
    if engagement:
        state.engagement = dict(engagement)

    timeline = reads.get("getStudentTimeline")
    if timeline:
        state.timeline = [dict(_mapping(item)) for item in _sequence(timeline.get("events"))]

    ownership = reads.get("getStudentOwnership")
    if ownership:
        state.ownership = dict(ownership)

    cohort = reads.get("findStudents")
    if cohort:
        state.cohort = dict(cohort)

    cohort_summary = reads.get("summarizeStudents")
    if cohort_summary:
        state.cohort_summary = dict(cohort_summary)

    attention = reads.get("getStudentsNeedingAttention")
    if attention:
        state.attention = dict(attention)

    queue = reads.get("getStaffWorkQueue")
    if queue:
        state.queue = dict(queue)

    briefing = reads.get("getMorningBriefing")
    if briefing:
        state.briefing = dict(briefing)

    work_item = reads.get("getWorkItemDetail")
    if work_item:
        state.work_item = dict(work_item)

    inquiries = reads.get("getInquiries")
    if inquiries:
        state.inquiries = [dict(item) for item in _items(inquiries)]

    thread = reads.get("getInquiryThread")
    if thread:
        state.inquiry_thread = dict(thread)

    guidance = reads.get("getPlaybooks")
    if guidance:
        state.guidance = dict(guidance)

    rules = reads.get("getActionRules")
    if rules:
        state.action_rules = [dict(item) for item in _items(rules)]

    return state


def overdue_deadlines(state: StaffDerivedState) -> list[JsonDict]:
    return [item for item in state.deadlines if str(item.get("bucket")) == "overdue"]


def deadlines_this_week(state: StaffDerivedState) -> list[JsonDict]:
    return [item for item in state.deadlines if str(item.get("bucket")) == "this_week"]


def open_work_items(state: StaffDerivedState) -> list[JsonDict]:
    if state.queue is None:
        return []
    return [
        dict(_mapping(item))
        for item in _sequence(state.queue.get("items"))
        if str(_mapping(item).get("status")) in _OPEN_WORK
    ]


def _items(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [_mapping(item) for item in _sequence(value.get("items"))]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return ()
