"""Read-only staff assistant tools backed by live, tenant-scoped reads.

Every read happens at question time against the same repositories the staff
portal uses. Each executed call produces a receipt recording the tool, its
validated arguments, latency, and record count; a read that failed, timed
out, or is unsupported on this host is reported honestly instead of guessed
around.

Identity discipline: tools that take ``studentId`` receive it from the
pipeline's resolved referent — already validated against the tenant roster —
never directly from model output. The host itself re-validates nothing about
tenancy because it cannot: every primitive it wraps filters on the
authenticated tenant in SQL.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from audentra.domain.student_cohort import (
    DEFAULT_COHORT_PAGE_SIZE,
    build_cohort_filter,
)
from audentra.domain.student_state import AID_DOCUMENT_SATISFIED_STATUSES
from audentra.integrations.staff_assistant.catalog import (
    STAFF_RECEIPT_SOURCES,
    ToolArgumentError,
    validate_tool_arguments,
)

JsonDict = dict[str, Any]
PrimitiveRead = Callable[..., Awaitable[Mapping[str, Any]]]

DEFAULT_STAFF_TOOL_TIMEOUT_SECONDS = 4.0

_DONE_REQUIREMENT_STATUSES = {"completed", "waived", "not_applicable"}
_IN_REVIEW_STATUSES = {"submitted", "under_review"}
_OPEN_WORK_STATUSES = {"todo", "in_progress", "follow_up_required", "blocked"}


@dataclass(frozen=True)
class PlannedToolCall:
    tool: str
    arguments: JsonDict = field(default_factory=dict)


@dataclass
class StaffToolExecution:
    reads: dict[str, JsonDict] = field(default_factory=dict)
    receipts: list[JsonDict] = field(default_factory=list)
    executed_tools: list[str] = field(default_factory=list)
    unavailable_data: list[JsonDict] = field(default_factory=list)
    rejected_arguments: list[JsonDict] = field(default_factory=list)


class StaffAssistantToolHost:
    """Primitive live reads with a per-request, argument-aware cache.

    The cache key includes the validated arguments, so two tools sharing the
    same student's requirements read hit once, while reads for two different
    students never collide. The cache lives for one question only.
    """

    def __init__(
        self,
        primitives: Mapping[str, PrimitiveRead],
        *,
        staff_member_id: str | None = None,
    ) -> None:
        self._primitives = dict(primitives)
        self._cache: dict[tuple[str, tuple[tuple[str, Any], ...]], Mapping[str, Any]] = {}
        self.staff_member_id = staff_member_id

    def supports(self, primitive: str) -> bool:
        return primitive in self._primitives

    async def read(self, primitive: str, **arguments: Any) -> Mapping[str, Any]:
        key = (primitive, tuple(sorted(arguments.items())))
        if key not in self._cache:
            self._cache[key] = await self._primitives[primitive](**arguments)
        return self._cache[key]


class _UnsupportedRead(Exception):
    pass


async def _primitive(
    host: StaffAssistantToolHost, name: str, **arguments: Any
) -> Mapping[str, Any]:
    if not host.supports(name):
        raise _UnsupportedRead(name)
    return await host.read(name, **arguments)


async def execute_staff_tool_reads(
    selected_calls: Sequence[PlannedToolCall],
    host: StaffAssistantToolHost,
    *,
    timeout_seconds: float = DEFAULT_STAFF_TOOL_TIMEOUT_SECONDS,
    now: datetime | None = None,
    receipt_offset: int = 0,
) -> StaffToolExecution:
    execution = StaffToolExecution()
    moment = now or datetime.now(UTC)

    validated: list[PlannedToolCall] = []
    for call in selected_calls:
        if call.tool not in _TOOL_IMPLEMENTATIONS:
            continue
        try:
            arguments = validate_tool_arguments(call.tool, call.arguments)
        except ToolArgumentError as error:
            execution.rejected_arguments.append(
                {"tool": call.tool, "code": error.code, "detail": error.detail}
            )
            continue
        validated.append(PlannedToolCall(tool=call.tool, arguments=arguments))

    async def run(call: PlannedToolCall) -> tuple[PlannedToolCall, JsonDict]:
        started = time.perf_counter()

        def timed(result: JsonDict) -> tuple[PlannedToolCall, JsonDict]:
            result["durationMs"] = round((time.perf_counter() - started) * 1_000)
            return call, result

        try:
            data = await asyncio.wait_for(
                _TOOL_IMPLEMENTATIONS[call.tool](host, call.arguments, moment),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return timed({"status": "timeout", "reason": "timeout", "retryable": True})
        except _UnsupportedRead:
            return timed({"status": "unavailable", "reason": "not_supported", "retryable": False})
        except Exception:
            return timed({"status": "unavailable", "reason": "read_error", "retryable": True})
        return timed({"status": "available", "data": data})

    results = await asyncio.gather(*(run(call) for call in validated))
    for index, (call, result) in enumerate(results):
        receipt = {
            "id": f"receipt-{receipt_offset + index + 1}",
            "source": STAFF_RECEIPT_SOURCES.get(call.tool, "work_queue"),
            "tool": call.tool,
            "arguments": dict(call.arguments),
            "recordCount": _record_count(result.get("data")),
            "status": result["status"],
            "durationMs": result.get("durationMs", 0),
        }
        execution.reads[call.tool] = {**result, "receipt": receipt}
        execution.receipts.append(receipt)
        execution.executed_tools.append(call.tool)
        if result["status"] != "available":
            execution.unavailable_data.append(
                {
                    "source": call.tool,
                    "reason": result.get("reason", "read_error"),
                    "retryable": bool(result.get("retryable", True)),
                }
            )
    return execution


# ---------------------------------------------------------------------------
# Shared derivations
# ---------------------------------------------------------------------------


def derive_student_blockers(
    requirements: Mapping[str, Any], overview: Mapping[str, Any] | None
) -> list[JsonDict]:
    """Derived enrollment blockers: unpaid deposit plus open blocking
    requirements — the same derivation discipline student Edward's holds tool
    uses, with staff-facing wording and responsible offices attached."""

    blockers: list[JsonDict] = []
    offer = _mapping((overview or {}).get("offer"))
    if (
        offer
        and offer.get("depositPaid") is False
        and int(offer.get("depositAmountCents") or 0) > 0
    ):
        blockers.append(
            {
                "code": "enrollment_deposit_posted",
                "title": "Enrollment deposit not posted",
                "owner": "student",
                "responsibleOffice": "Student Accounts",
                "clearingAction": "The student pays the enrollment deposit.",
                "dueAt": offer.get("responseDeadline"),
                "blocksRegistration": True,
            }
        )
    seen = {str(blocker["code"]) for blocker in blockers}
    for item in _items(requirements):
        status = str(item.get("status") or "")
        if not item.get("blocking") or status in _DONE_REQUIREMENT_STATUSES:
            continue
        code = str(item.get("code") or "requirement")
        if code in {"enrollment_deposit"} and "enrollment_deposit_posted" in seen:
            continue
        if code in seen:
            continue
        seen.add(code)
        in_review = status in _IN_REVIEW_STATUSES
        blockers.append(
            {
                "code": code,
                "title": str(item.get("title") or "Enrollment requirement"),
                "status": status,
                "owner": "university" if in_review else "student",
                "responsibleOffice": item.get("responsibleOffice"),
                "clearingAction": (
                    "Submitted — waiting on university review."
                    if in_review
                    else f"The student completes “{item.get('title')}”."
                ),
                "dueAt": item.get("dueAt"),
                "blocksRegistration": True,
            }
        )
    return blockers


def _deadline_bucket(due_at: str, now: datetime) -> str:
    try:
        due = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
    except ValueError:
        return "later"
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    delta_days = (due - now).total_seconds() / 86_400
    if delta_days < 0:
        return "overdue"
    if delta_days <= 7:
        return "this_week"
    if delta_days <= 30:
        return "this_month"
    return "later"


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def _tool_search_students(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(
        await _primitive(
            host,
            "search_students",
            query=str(arguments.get("query") or ""),
            program=arguments.get("program"),
            limit=int(arguments.get("limit") or 10),
        )
    )


async def _tool_find_students(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    """A cohort, with the true total behind the returned page.

    The page is bounded, so the answer must never present `items` as the whole
    population: `total` and `truncated` are what a staff user needs to know
    they are looking at a sample.
    """

    result = await _primitive(
        host,
        "find_students",
        cohort=arguments["filter"],
        limit=int(arguments.get("limit") or DEFAULT_COHORT_PAGE_SIZE),
    )
    return dict(result)


async def _tool_summarize_students(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    cohort = arguments.get("filter") or build_cohort_filter({})
    return dict(
        await _primitive(
            host,
            "summarize_students",
            cohort=cohort,
            group_by=str(arguments["groupBy"]),
            limit=int(arguments.get("limit") or 20),
        )
    )


async def _tool_student_summary(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    student_id = str(arguments["studentId"])
    overview = await _primitive(host, "student_overview", student_id=student_id)
    work = await _primitive(host, "student_work_items", student_id=student_id)
    summary = dict(overview)
    summary["openWork"] = [
        {
            "key": item.get("key"),
            "title": item.get("title"),
            "status": item.get("status"),
            "priority": item.get("priority"),
            "dueAt": item.get("dueAt"),
            "assignee": item.get("assignee"),
        }
        for item in _items(work)[:5]
    ]
    return summary


async def _tool_student_requirements(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    requirements = await _primitive(
        host, "student_requirements", student_id=str(arguments["studentId"])
    )
    items = [
        {
            "code": item.get("code"),
            "title": item.get("title"),
            "status": item.get("status"),
            "blocking": bool(item.get("blocking")),
            "dueAt": item.get("dueAt"),
            "responsibleOffice": item.get("responsibleOffice"),
            "progressPercent": item.get("progressPercent"),
        }
        for item in _items(requirements)
    ]
    return {"items": items, "total": len(items)}


async def _tool_student_documents(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    documents = await _primitive(host, "student_documents", student_id=str(arguments["studentId"]))
    items = [
        {
            "id": item.get("id"),
            "fileName": item.get("fileName"),
            "category": item.get("category"),
            "status": item.get("status"),
            "requirementId": item.get("requirementId"),
            "createdAt": item.get("createdAt"),
            "extractionStatus": (item.get("extraction") or {}).get("status")
            if isinstance(item.get("extraction"), Mapping)
            else None,
        }
        for item in _items(documents)
    ]
    return {"items": items, "total": len(items)}


async def _tool_student_blockers(
    host: StaffAssistantToolHost, arguments: JsonDict, now: datetime
) -> JsonDict:
    student_id = str(arguments["studentId"])
    requirements = await _primitive(host, "student_requirements", student_id=student_id)
    overview = await _primitive(host, "student_overview", student_id=student_id)
    blockers = derive_student_blockers(requirements, overview)
    return {
        # The platform operates no registrar hold system; saying so is part
        # of the answer, never an omission.
        "officialHolds": [],
        "holdSystem": "not_operated",
        "derivedBlockers": blockers,
        "asOf": now.isoformat(),
    }


async def _tool_student_deadlines(
    host: StaffAssistantToolHost, arguments: JsonDict, now: datetime
) -> JsonDict:
    student_id = str(arguments["studentId"])
    requirements = await _primitive(host, "student_requirements", student_id=student_id)
    overview = await _primitive(host, "student_overview", student_id=student_id)
    deadlines: list[JsonDict] = []
    for item in _items(requirements):
        if not item.get("dueAt") or str(item.get("status") or "") in _DONE_REQUIREMENT_STATUSES:
            continue
        deadlines.append(
            {
                "title": str(item.get("title") or "Enrollment requirement"),
                "dueAt": item["dueAt"],
                "kind": "requirement",
                "responsibleOffice": item.get("responsibleOffice"),
            }
        )
    offer = _mapping(overview.get("offer"))
    if offer.get("responseDeadline") and offer.get("depositPaid") is False:
        deadlines.append(
            {
                "title": "Enrollment deposit (offer response deadline)",
                "dueAt": str(offer["responseDeadline"]),
                "kind": "deposit",
            }
        )
    if host.supports("student_financials"):
        try:
            financials = await host.read("student_financials", student_id=student_id)
        except Exception:
            financials = {}
        for raw in _sequence(_mapping(financials).get("requiredDocuments")):
            entry = _mapping(raw)
            if entry.get("dueAt") and str(entry.get("status")) not in (
                AID_DOCUMENT_SATISFIED_STATUSES
            ):
                deadlines.append(
                    {
                        "title": str(entry.get("title") or "Financial aid document"),
                        "dueAt": str(entry["dueAt"]),
                        "kind": "financial_aid",
                    }
                )
    for deadline in deadlines:
        deadline["bucket"] = _deadline_bucket(str(deadline["dueAt"]), now)
    deadlines.sort(key=lambda item: str(item["dueAt"]))
    return {"items": deadlines, "total": len(deadlines), "asOf": now.isoformat()}


async def _tool_student_financials(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    student_id = str(arguments["studentId"])
    financials = await _primitive(host, "student_financials", student_id=student_id)
    overview = await _primitive(host, "student_overview", student_id=student_id)
    offer = _mapping(overview.get("offer"))
    pending_deposit = False
    if host.supports("student_payments"):
        try:
            payments = await host.read("student_payments", student_id=student_id)
        except Exception:
            payments = {}
        pending_deposit = any(
            str(_mapping(item).get("type") or "") == "enrollment_deposit"
            and str(_mapping(item).get("status") or "") == "pending"
            for item in _sequence(_mapping(payments).get("items"))
        )
    return {
        "academicYear": financials.get("academicYear"),
        "costOfAttendanceCents": financials.get("costOfAttendanceCents"),
        "acceptedAidCents": financials.get("acceptedAidCents"),
        "pendingAidCents": financials.get("pendingAidCents"),
        "paymentsCents": financials.get("paymentsCents"),
        "remainingBalanceCents": financials.get("remainingBalanceCents"),
        "awards": [dict(_mapping(item)) for item in _sequence(financials.get("awards"))],
        "requiredDocuments": [
            dict(_mapping(item)) for item in _sequence(financials.get("requiredDocuments"))
        ],
        "deposit": {
            "amountCents": offer.get("depositAmountCents"),
            "paid": offer.get("depositPaid"),
            "paymentPending": pending_deposit,
            "responseDeadline": offer.get("responseDeadline"),
        },
        # No disbursement ledger exists in the platform.
        "disbursementScheduleTracked": False,
    }


async def _tool_student_housing(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    student_id = str(arguments["studentId"])
    housing = await _primitive(host, "student_housing_plan", student_id=student_id)
    requirements = await _primitive(host, "student_requirements", student_id=student_id)
    requirement = next(
        (
            item
            for item in _items(requirements)
            if str(item.get("code") or "").lower() == "housing_preference"
        ),
        None,
    )
    status = str(requirement.get("status") or "") if requirement else None
    if requirement is None:
        eligibility = "no_housing_step"
    elif status in _DONE_REQUIREMENT_STATUSES:
        eligibility = "already_completed"
    elif status == "blocked":
        eligibility = "blocked"
    else:
        eligibility = "eligible_now"
    return {
        "preference": housing.get("preference"),
        "residenceOption": housing.get("residenceOption"),
        "requirementStatus": status,
        "requirementDueAt": requirement.get("dueAt") if requirement else None,
        "eligibility": eligibility,
        # The platform models no housing application window or room assignment.
        "applicationWindow": {"published": False},
        "roomAssignmentTracked": False,
    }


async def _tool_student_appointments(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    appointments = await _primitive(
        host, "student_appointments", student_id=str(arguments["studentId"])
    )
    items = [
        {
            "type": entry.get("type"),
            "startsAt": entry.get("startsAt"),
            "status": entry.get("status"),
        }
        for entry in _items(appointments)
    ]
    return {"items": items, "total": len(items)}


async def _tool_student_communications(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(
        await _primitive(
            host,
            "communication_history",
            student_id=str(arguments["studentId"]),
            channel=arguments.get("channel"),
        )
    )


async def _tool_student_engagement(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(await _primitive(host, "engagement", student_id=str(arguments["studentId"])))


async def _tool_student_timeline(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(
        await _primitive(
            host,
            "timeline",
            student_id=str(arguments["studentId"]),
            limit=int(arguments.get("limit") or 40),
        )
    )


async def _tool_student_ownership(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    student_id = str(arguments["studentId"])
    work = await _primitive(host, "student_work_items", student_id=student_id)
    communications = await _primitive(
        host, "communication_history", student_id=student_id, channel=None
    )
    requirements = await _primitive(host, "student_requirements", student_id=student_id)
    work_assignees = [
        {
            "workItemKey": item.get("key"),
            "workItemTitle": item.get("title"),
            "status": item.get("status"),
            "assignee": item.get("assignee"),
            "component": item.get("component"),
        }
        for item in _items(work)
        if str(item.get("status")) in _OPEN_WORK_STATUSES
    ]
    inquiry_assignees = [
        {
            "inquirySubject": entry.get("subject"),
            "status": entry.get("status"),
            "assignee": entry.get("assignee"),
        }
        for entry in _sequence(_mapping(communications).get("inquiries"))
        if isinstance(entry, Mapping) and str(entry.get("status")) != "resolved"
    ]
    responsible_offices = sorted(
        {
            str(item.get("responsibleOffice"))
            for item in _items(requirements)
            if item.get("responsibleOffice")
            and str(item.get("status") or "") not in _DONE_REQUIREMENT_STATUSES
        }
    )
    return {
        "workItemAssignees": work_assignees,
        "inquiryAssignees": inquiry_assignees,
        "responsibleOffices": responsible_offices,
        # There is no advisor/caseload model; ownership is per open case only.
        "advisorModel": "none",
    }


async def _tool_attention(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(await _primitive(host, "attention", limit=int(arguments.get("limit") or 15)))


async def _tool_work_queue(
    host: StaffAssistantToolHost, arguments: JsonDict, now: datetime
) -> JsonDict:
    queue = await _primitive(host, "work_queue")
    items = [dict(_mapping(item)) for item in _sequence(queue.get("items"))]
    ownership = str(arguments.get("ownership") or "all")
    component = arguments.get("component")
    status = arguments.get("status")
    due_window = str(arguments.get("dueWindow") or "all")
    if ownership == "mine" and host.staff_member_id:
        items = [
            item
            for item in items
            if str(_mapping(item.get("assignee")).get("id") or "") == host.staff_member_id
        ]
    elif ownership == "unassigned":
        items = [item for item in items if not item.get("assignee")]
    if component:
        needle = str(component).lower()
        items = [item for item in items if needle in str(item.get("component") or "").lower()]
    if status:
        items = [item for item in items if str(item.get("status")) == status]
    if due_window != "all":
        items = [item for item in items if _due_window(item.get("dueAt"), now) == due_window]
    open_items = [item for item in items if str(item.get("status")) in _OPEN_WORK_STATUSES]
    return {
        # Canonical order (priority → dueAt → updated) is preserved from SQL.
        "items": [_bounded_queue_item(item) for item in items[:25]],
        "counts": dict(_mapping(queue.get("counts"))),
        "filteredTotal": len(items),
        "filteredOpen": len(open_items),
        "filters": {
            "ownership": ownership,
            "component": component,
            "status": status,
            "dueWindow": due_window,
        },
        "generatedAt": queue.get("generatedAt"),
    }


def _due_window(due_at: object, now: datetime) -> str:
    if not due_at:
        return "no_due"
    try:
        due = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
    except ValueError:
        return "no_due"
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    if due < now:
        return "overdue"
    delta_days = (due - now).total_seconds() / 86_400
    if due.date() == now.date():
        return "today"
    if delta_days <= 7:
        return "seven_days"
    return "later"


def _bounded_queue_item(item: Mapping[str, Any]) -> JsonDict:
    student = _mapping(item.get("student"))
    assignee = _mapping(item.get("assignee"))
    return {
        "id": item.get("id"),
        "key": item.get("key"),
        "title": item.get("title"),
        "status": item.get("status"),
        "priority": item.get("priority"),
        "actionType": item.get("actionType"),
        "component": item.get("component"),
        "dueAt": item.get("dueAt"),
        "escalated": bool(item.get("escalated")),
        "student": {"id": student.get("id"), "name": student.get("name")},
        "assignee": {"id": assignee.get("id"), "name": assignee.get("name")} if assignee else None,
    }


async def _tool_work_item_detail(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    detail = await _primitive(host, "work_item_detail", work_item_id=str(arguments["workItemId"]))
    return dict(detail)


async def _tool_inquiries(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    inquiries = await _primitive(host, "inquiries")
    items = [dict(_mapping(item)) for item in _sequence(inquiries.get("items"))]
    status = arguments.get("status")
    if status:
        items = [item for item in items if str(item.get("status")) == status]
    return {"items": items[:25], "total": len(items)}


async def _tool_inquiry_thread(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(await _primitive(host, "inquiry_thread", inquiry_id=str(arguments["inquiryId"])))


async def _tool_playbooks(
    host: StaffAssistantToolHost, _arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(await _primitive(host, "guidance"))


async def _tool_action_rules(
    host: StaffAssistantToolHost, _arguments: JsonDict, _now: datetime
) -> JsonDict:
    rules = await _primitive(host, "action_rules")
    items = [dict(_mapping(item)) for item in _sequence(_mapping(rules).get("items"))]
    return {"items": items, "total": len(items)}


_TOOL_IMPLEMENTATIONS: Mapping[
    str, Callable[[StaffAssistantToolHost, JsonDict, datetime], Awaitable[JsonDict]]
] = {
    "searchStudents": _tool_search_students,
    "findStudents": _tool_find_students,
    "summarizeStudents": _tool_summarize_students,
    "getStudentStaffSummary": _tool_student_summary,
    "getStudentRequirements": _tool_student_requirements,
    "getStudentDocuments": _tool_student_documents,
    "getStudentBlockers": _tool_student_blockers,
    "getStudentDeadlines": _tool_student_deadlines,
    "getStudentFinancialState": _tool_student_financials,
    "getStudentHousingState": _tool_student_housing,
    "getStudentAppointments": _tool_student_appointments,
    "getStudentCommunicationHistory": _tool_student_communications,
    "getStudentEngagementSignals": _tool_student_engagement,
    "getStudentTimeline": _tool_student_timeline,
    "getStudentOwnership": _tool_student_ownership,
    "getStudentsNeedingAttention": _tool_attention,
    "getStaffWorkQueue": _tool_work_queue,
    "getWorkItemDetail": _tool_work_item_detail,
    "getInquiries": _tool_inquiries,
    "getInquiryThread": _tool_inquiry_thread,
    "getPlaybooks": _tool_playbooks,
    "getActionRules": _tool_action_rules,
}


def _items(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [_mapping(item) for item in _sequence(value.get("items"))]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return ()


def _record_count(data: Any) -> int:
    if isinstance(data, Mapping):
        if isinstance(data.get("items"), Sequence):
            return len(data["items"])
        for key in (
            "events",
            "derivedBlockers",
            "awards",
            "corePlays",
            "workItemAssignees",
            "openWork",
        ):
            if isinstance(data.get(key), Sequence):
                return len(data[key])
        return 1
    return 0
