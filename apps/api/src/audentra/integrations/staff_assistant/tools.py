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
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
        self._cache: dict[tuple[str, str], Mapping[str, Any]] = {}
        self.staff_member_id = staff_member_id

    def supports(self, primitive: str) -> bool:
        return primitive in self._primitives

    async def read(self, primitive: str, **arguments: Any) -> Mapping[str, Any]:
        # Arguments may carry nested filter mappings; the key is their
        # canonical JSON so equal reads coalesce and unequal ones never do.
        key = (primitive, json.dumps(arguments, sort_keys=True, default=str))
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
    external_ref = str(arguments.get("externalRef") or "").strip()
    query = str(arguments.get("query") or "").strip()
    if external_ref and host.supports("student_by_external_ref"):
        # Exact institutional-ID lookup: zero or one student, never a guess.
        found = await _primitive(host, "student_by_external_ref", external_ref=external_ref)
        items = [dict(found)] if found and found.get("id") else []
        return {
            "items": items,
            "total": len(items),
            "matchQuality": "exact_external_ref",
            "externalRef": external_ref,
        }
    if not query and not external_ref:
        return {"items": [], "total": 0, "matchQuality": "no_criteria"}
    result = dict(
        await _primitive(
            host,
            "search_students",
            query=query or external_ref,
            program=arguments.get("program"),
            limit=int(arguments.get("limit") or 10),
        )
    )
    result.setdefault("matchQuality", "exact")
    if not result.get("items") and query and host.supports("search_students_fuzzy"):
        # Deterministic close-spelling fallback: suggestions to confirm,
        # marked as such — resolution code must never auto-pick from these.
        fuzzy = dict(
            await _primitive(
                host,
                "search_students_fuzzy",
                query=query,
                limit=int(arguments.get("limit") or 5),
            )
        )
        if fuzzy.get("items"):
            fuzzy.setdefault("matchQuality", "fuzzy")
            return fuzzy
    return result


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
            "endsAt": entry.get("endsAt"),
            "status": entry.get("status"),
            "modality": entry.get("modality"),
            "with": _appointment_staff(entry.get("staff")),
        }
        for entry in _items(appointments)
    ]
    return {"items": items, "total": len(items)}


def _appointment_staff(value: object) -> JsonDict | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "id": value.get("id"),
        "name": value.get("name"),
        "title": value.get("title"),
        "component": value.get("component"),
        "employmentStatus": value.get("employmentStatus"),
    }


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
    advising: Mapping[str, Any] = {}
    if host.supports("student_advising"):
        advising = await _primitive(host, "student_advising", student_id=student_id)
    primary = advising.get("primaryAdviser")
    return {
        "workItemAssignees": work_assignees,
        "inquiryAssignees": inquiry_assignees,
        "responsibleOffices": responsible_offices,
        # Standing relationships come from student_staff_assignment; open-case
        # ownership still comes from the work items and inquiries above.
        "advisorModel": "primary_advisor" if advising else "none",
        "primaryAdviser": _mapping(primary).get("staff") if isinstance(primary, Mapping) else None,
        "advisers": [
            {
                "role": _mapping(entry).get("role"),
                "staff": _mapping(entry).get("staff"),
                "nextOpenSlotAt": _mapping(_mapping(entry).get("availability")).get(
                    "nextOpenSlotAt"
                ),
            }
            for entry in _sequence(advising.get("advisers"))
        ],
        "advisingGaps": list(_sequence(advising.get("gaps"))),
        "advisingStatus": advising.get("advising"),
    }


async def _tool_attention(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(await _primitive(host, "attention", limit=int(arguments.get("limit") or 15)))


async def _tool_morning_briefing(
    host: StaffAssistantToolHost, _arguments: JsonDict, _now: datetime
) -> JsonDict:
    """Today's Morning Brew, bounded to what an answer can actually use.

    The payload the Staff Portal renders is large (every metric carries its
    cohort definition and per-window frames); the assistant needs the
    headline, the counts, what moved, the ranked attention themes, the work
    summary, and — importantly — the briefing's own list of metrics the
    platform does not hold, so an honest "not tracked" survives into the
    answer.
    """

    brew = await _primitive(host, "morning_brew")
    synthesis = _mapping(brew.get("synthesis"))
    population = _mapping(brew.get("population"))
    coverage = _mapping(brew.get("coverage"))
    return {
        "generatedAt": brew.get("generatedAt"),
        "window": _mapping(brew.get("window")).get("label"),
        "headline": synthesis.get("headline"),
        "bullets": [str(item) for item in _sequence(synthesis.get("bullets"))][:6],
        "population": {
            "students": population.get("students"),
            "cohorts": dict(_mapping(population.get("cohorts"))),
        },
        "attention": [
            {
                "title": _mapping(item).get("title"),
                "count": _mapping(item).get("count"),
                "severity": _mapping(item).get("severity"),
                "detail": _mapping(item).get("detail"),
            }
            for item in _sequence(brew.get("attention"))[:5]
        ],
        "changes": [
            {
                "label": _mapping(item).get("label"),
                "value": _mapping(item).get("value"),
            }
            for item in _sequence(brew.get("changes"))[:6]
        ],
        "priorities": [
            {
                "title": _mapping(item).get("title"),
                "detail": _mapping(item).get("detail"),
            }
            for item in _sequence(brew.get("priorities"))[:5]
        ],
        "staffWork": dict(_mapping(brew.get("staffWork"))),
        "requests": {
            key: value for key, value in _mapping(brew.get("requests")).items() if key != "items"
        },
        "unsupported": [
            {
                "metric": _mapping(item).get("metric"),
                "reason": _mapping(item).get("reason"),
            }
            for item in _sequence(coverage.get("unsupported"))[:6]
        ],
    }


async def _tool_work_queue(
    host: StaffAssistantToolHost, arguments: JsonDict, now: datetime
) -> JsonDict:
    """A bounded page of the Action Center with SQL-side counts.

    The board read (`work_queue`) loads every item and every log row; at a
    realistic tenant that alone exceeds the tool budget. When the bounded
    primitives exist the page and the counts come from SQL; the whole-board
    read remains only as the fallback for hosts without them.
    """

    ownership = str(arguments.get("ownership") or "all")
    component = arguments.get("component")
    status = arguments.get("status")
    due_window = str(arguments.get("dueWindow") or "all")
    topic = str(arguments.get("topic") or "").strip().lower()
    if host.supports("work_queue_search") and host.supports("work_queue_summary"):
        filters: JsonDict = {
            "ownership": ownership if ownership != "all" else None,
            "component": component,
            "status": status or "open",
            "dueWindow": due_window if due_window != "all" else None,
            "topic": topic or None,
        }
        page = await _primitive(host, "work_queue_search", filters=filters, limit=25)
        summary = await _primitive(host, "work_queue_summary", filters=filters)
        board = await _primitive(host, "work_queue_summary", filters={"status": "open"})
        items = [dict(_mapping(item)) for item in _sequence(page.get("items"))]
        by_status = _mapping(summary.get("byStatus"))
        return {
            "items": items,
            "counts": {
                "todo": int(_mapping(board.get("byStatus")).get("todo") or 0),
                "inProgress": int(_mapping(board.get("byStatus")).get("inProgress") or 0),
                "followUpRequired": int(
                    _mapping(board.get("byStatus")).get("followUpRequired") or 0
                ),
                "blocked": int(_mapping(board.get("byStatus")).get("blocked") or 0),
                "open": int(board.get("total") or 0),
                "unassigned": int(board.get("unassigned") or 0),
                "urgent": int(board.get("urgent") or 0),
                "escalated": int(board.get("escalated") or 0),
                "overdue": int(board.get("overdue") or 0),
            },
            "filteredTotal": int(page.get("total") or 0),
            "filteredOpen": int(summary.get("total") or 0)
            if (status or "open") == "open"
            else sum(int(by_status.get(key) or 0) for key in by_status),
            "filteredOverdue": int(summary.get("overdue") or 0),
            "filteredUnassigned": int(summary.get("unassigned") or 0),
            "filteredUrgent": int(summary.get("urgent") or 0),
            "distinctOpenStudents": int(summary.get("distinctStudents") or 0),
            "filters": {
                "ownership": ownership,
                "component": component,
                "status": status,
                "dueWindow": due_window,
                "topic": topic or None,
            },
            "bounded": True,
            "generatedAt": page.get("generatedAt"),
        }
    queue = await _primitive(host, "work_queue")
    items = [dict(_mapping(item)) for item in _sequence(queue.get("items"))]
    if topic:
        items = [
            item
            for item in items
            if topic in str(item.get("title") or "").lower()
            or topic in str(item.get("description") or "").lower()
        ]
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
    if status and status not in {"open", "any", "closed"}:
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
        "distinctOpenStudents": len(
            {
                str(_mapping(item.get("student")).get("id") or "")
                for item in open_items
                if _mapping(item.get("student")).get("id")
            }
        ),
        "filters": {
            "ownership": ownership,
            "component": component,
            "status": status,
            "dueWindow": due_window,
            "topic": topic or None,
        },
        "bounded": False,
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


async def _tool_mailbox_messages(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    result = await _primitive(
        host,
        "mailbox_messages",
        query=str(arguments.get("query") or ""),
        limit=int(arguments.get("limit") or 10),
    )
    return dict(result)


# ---------------------------------------------------------------------------
# Staff-aware tools (bounded reads about people, teams, queues, departments)
# ---------------------------------------------------------------------------


_QUEUE_FILTER_KEYS = (
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
    "studentId",
)
_INQUIRY_FILTER_KEYS = (
    "status",
    "ownership",
    "priority",
    "topic",
    "olderThanHours",
    "studentId",
)


def _queue_filters(arguments: JsonDict) -> JsonDict:
    filters = {
        key: arguments.get(key) for key in _QUEUE_FILTER_KEYS if arguments.get(key) is not None
    }
    if arguments.get("staffId"):
        filters["assigneeId"] = str(arguments["staffId"])
    if filters.get("ownership") == "all":
        filters.pop("ownership")
    if filters.get("dueWindow") == "all":
        filters.pop("dueWindow")
    return filters


def _inquiry_filters(arguments: JsonDict) -> JsonDict:
    filters = {
        key: arguments.get(key) for key in _INQUIRY_FILTER_KEYS if arguments.get(key) is not None
    }
    if arguments.get("staffId"):
        filters["assigneeId"] = str(arguments["staffId"])
    if filters.get("ownership") == "all":
        filters.pop("ownership")
    return filters


async def _tool_staff_profile(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    profile = await _primitive(host, "staff_profile", staff_member_id=str(arguments["staffId"]))
    return dict(profile or {})


async def _tool_search_staff(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(
        await _primitive(
            host,
            "search_staff",
            query=str(arguments.get("query") or ""),
            component=arguments.get("component"),
            role=arguments.get("role"),
            absent_now=bool(arguments.get("absentNow")),
            limit=int(arguments.get("limit") or 10),
        )
    )


async def _tool_staff_team(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    team = await _primitive(host, "staff_team", staff_member_id=str(arguments["staffId"]))
    data = dict(team or {})
    members = [dict(_mapping(entry)) for entry in _sequence(data.get("team"))]
    # Bound the payload: the subtree can be large, so keep the fields an
    # answer needs and the flagged people first.
    bounded = [
        {
            "id": entry.get("id"),
            "name": entry.get("name"),
            "title": entry.get("title"),
            "roleCode": entry.get("roleCode"),
            "component": entry.get("component"),
            "employmentStatus": entry.get("employmentStatus"),
            "leaveUntil": entry.get("leaveUntil"),
            "level": entry.get("level"),
            "caseload": dict(_mapping(entry.get("caseload"))),
            "work": dict(_mapping(entry.get("work"))),
            "availability": dict(_mapping(entry.get("availability"))),
            "flags": list(_sequence(entry.get("flags"))),
        }
        for entry in members
    ]
    bounded.sort(key=lambda entry: (0 if entry["flags"] else 1, int(str(entry.get("level") or 1))))
    return {
        "manager": dict(_mapping(data.get("manager"))),
        "members": bounded[:40],
        "total": len(members),
        "directReports": sum(1 for entry in members if int(entry.get("level") or 1) == 1),
        "componentSummary": dict(_mapping(data.get("componentSummary"))),
        "flagged": [
            {"name": entry["name"], "flags": entry["flags"]} for entry in bounded if entry["flags"]
        ][:20],
    }


async def _tool_staff_caseload(
    host: StaffAssistantToolHost, arguments: JsonDict, now: datetime
) -> JsonDict:
    role = arguments.get("role") or "primary_advisor"
    caseload = await _primitive(
        host, "staff_caseload", staff_member_id=str(arguments["staffId"]), role=role
    )
    data = dict(caseload or {})
    items = [dict(_mapping(item)) for item in _sequence(data.get("items"))]
    advising = str(arguments.get("advisingStatus") or "any")
    if advising == "not_completed":
        items = [i for i in items if _mapping(i.get("advising")).get("status") != "completed"]
    elif advising == "no_booking":
        items = [
            i
            for i in items
            if _mapping(i.get("advising")).get("status") not in {"completed", "scheduled"}
        ]
    elif advising in {"completed", "missed", "scheduled"}:
        items = [i for i in items if _mapping(i.get("advising")).get("status") == advising]
    deposit = arguments.get("depositState")
    if deposit == "paid":
        items = [i for i in items if i.get("depositPaid") or i.get("depositState") == "paid"]
    elif deposit == "unpaid":
        items = [i for i in items if not (i.get("depositPaid") or i.get("depositState") == "paid")]
    if arguments.get("withOpenWork"):
        items = [i for i in items if int(_mapping(i.get("work")).get("open") or 0) > 0]
    if arguments.get("withOverdueWork"):
        items = [i for i in items if int(_mapping(i.get("work")).get("overdue") or 0) > 0]
    limit = int(arguments.get("limit") or 12)
    statuses = [str(_mapping(i.get("advising")).get("status") or "none") for i in items]
    return {
        "staff": dict(_mapping(data.get("staff"))),
        "role": role,
        "filters": {
            key: arguments.get(key)
            for key in ("advisingStatus", "depositState", "withOpenWork", "withOverdueWork")
            if arguments.get(key) not in (None, "any", False)
        },
        "total": len(items),
        "returned": min(len(items), limit),
        "truncated": len(items) > limit,
        "summary": {
            "advisingCompleted": statuses.count("completed"),
            "advisingScheduled": statuses.count("scheduled"),
            "advisingMissed": statuses.count("missed"),
            "advisingNone": statuses.count("none"),
            "withOpenWork": sum(1 for i in items if int(_mapping(i.get("work")).get("open") or 0)),
            "withOverdueWork": sum(
                1 for i in items if int(_mapping(i.get("work")).get("overdue") or 0)
            ),
        },
        "items": [
            {
                "student": dict(_mapping(item.get("student"))),
                "offerStatus": item.get("offerStatus"),
                "advising": dict(_mapping(item.get("advising"))),
                "work": dict(_mapping(item.get("work"))),
                "requirements": dict(_mapping(item.get("requirements"))),
            }
            for item in items[:limit]
        ],
        "asOf": now.isoformat(),
    }


def _appointment_window(window: str, now: datetime) -> tuple[str | None, str | None]:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "today":
        return day.isoformat(), (day + timedelta(days=1)).isoformat()
    if window == "tomorrow":
        return (day + timedelta(days=1)).isoformat(), (day + timedelta(days=2)).isoformat()
    if window == "week":
        return now.isoformat(), (now + timedelta(days=7)).isoformat()
    if window == "two_weeks":
        return now.isoformat(), (now + timedelta(days=14)).isoformat()
    if window == "past_week":
        return (now - timedelta(days=7)).isoformat(), now.isoformat()
    if window == "awaiting_outcome":
        return (now - timedelta(days=60)).isoformat(), (now - timedelta(days=3)).isoformat()
    return None, None


async def _tool_staff_appointments(
    host: StaffAssistantToolHost, arguments: JsonDict, now: datetime
) -> JsonDict:
    window = str(arguments.get("window") or "week")
    start, end = _appointment_window(window, now)
    result = await _primitive(
        host,
        "staff_appointments",
        staff_member_id=str(arguments["staffId"]),
        window_from=start,
        window_to=end,
    )
    data = dict(result or {})
    items = [dict(_mapping(item)) for item in _sequence(data.get("items"))]
    if window == "awaiting_outcome":
        items = [item for item in items if str(item.get("status")) == "scheduled"]
    return {
        "staff": dict(_mapping(data.get("staff"))),
        "window": window,
        "from": data.get("from"),
        "to": data.get("to"),
        "total": len(items),
        "counts": dict(_mapping(data.get("counts"))),
        "items": [
            {
                "startsAt": item.get("startsAt"),
                "type": item.get("type"),
                "status": item.get("status"),
                "modality": item.get("modality"),
                "student": dict(_mapping(item.get("student"))),
            }
            for item in items[:25]
        ],
        "availability": dict(_mapping(data.get("availability"))),
    }


async def _tool_staff_availability(
    host: StaffAssistantToolHost, arguments: JsonDict, now: datetime
) -> JsonDict:
    profile = dict(
        await _primitive(host, "staff_profile", staff_member_id=str(arguments["staffId"])) or {}
    )
    availability = dict(_mapping(profile.get("availability")))
    status = str(profile.get("employmentStatus") or "active")
    reason = availability.get("reason")
    if status == "on_leave":
        reason = "on_leave"
    elif status == "departed":
        reason = "departed"
    return {
        "staff": {
            key: profile.get(key)
            for key in ("id", "name", "title", "component", "employmentStatus", "leaveUntil")
        },
        "bookable": bool(availability.get("bookable")) and status == "active",
        "reason": reason,
        "nextOpenSlotAt": availability.get("nextOpenSlotAt"),
        "openSlotsNext14Days": availability.get("openSlotsNext14Days"),
        "bookedNext14Days": availability.get("bookedNext14Days"),
        "timezone": availability.get("timezone") or profile.get("timezone"),
        "weekdays": list(_sequence(profile.get("weekdays"))),
        "currentAbsence": profile.get("currentAbsence"),
        "upcomingAbsence": list(_sequence(profile.get("upcomingAbsence"))),
        "leaveUntil": profile.get("leaveUntil"),
        "asOf": now.isoformat(),
    }


async def _tool_compare_staff(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    profiles = []
    for staff_id in list(arguments.get("staffIds") or [])[:4]:
        profile = await _primitive(host, "staff_profile", staff_member_id=str(staff_id))
        if profile:
            profiles.append(dict(profile))
    return {"items": profiles, "total": len(profiles)}


async def _tool_summarize_work_queue(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(
        await _primitive(
            host,
            "work_queue_summary",
            filters=_queue_filters(arguments),
            group_by=arguments.get("groupBy"),
            limit=int(arguments.get("limit") or 12),
        )
    )


async def _tool_search_work_queue(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(
        await _primitive(
            host,
            "work_queue_search",
            filters=_queue_filters(arguments),
            limit=int(arguments.get("limit") or 10),
            sort=str(arguments.get("sort") or "canonical"),
        )
    )


async def _tool_summarize_inquiries(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(
        await _primitive(
            host,
            "inquiry_summary",
            filters=_inquiry_filters(arguments),
            group_by=arguments.get("groupBy"),
            limit=int(arguments.get("limit") or 10),
        )
    )


async def _tool_search_inquiries(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    return dict(
        await _primitive(
            host,
            "inquiry_search",
            filters=_inquiry_filters(arguments),
            limit=int(arguments.get("limit") or 10),
            sort=str(arguments.get("sort") or "oldest"),
        )
    )


async def _tool_component_summary(
    host: StaffAssistantToolHost, arguments: JsonDict, _now: datetime
) -> JsonDict:
    summary = await _primitive(host, "component_summary", component=str(arguments["component"]))
    return dict(summary or {})


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
    "getMorningBriefing": _tool_morning_briefing,
    "getWorkItemDetail": _tool_work_item_detail,
    "getInquiries": _tool_inquiries,
    "getInquiryThread": _tool_inquiry_thread,
    "getPlaybooks": _tool_playbooks,
    "getActionRules": _tool_action_rules,
    "getMailboxMessages": _tool_mailbox_messages,
    "getStaffProfile": _tool_staff_profile,
    "searchStaff": _tool_search_staff,
    "getStaffTeam": _tool_staff_team,
    "getStaffCaseload": _tool_staff_caseload,
    "getStaffAppointments": _tool_staff_appointments,
    "getStaffAvailability": _tool_staff_availability,
    "compareStaff": _tool_compare_staff,
    "summarizeWorkQueue": _tool_summarize_work_queue,
    "searchWorkQueue": _tool_search_work_queue,
    "summarizeInquiries": _tool_summarize_inquiries,
    "searchInquiries": _tool_search_inquiries,
    "getComponentSummary": _tool_component_summary,
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
            "members",
            "buckets",
        ):
            if isinstance(data.get(key), Sequence):
                return len(data[key])
        return 1
    return 0
