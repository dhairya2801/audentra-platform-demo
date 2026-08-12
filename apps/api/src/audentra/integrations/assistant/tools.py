"""Read-only assistant tools backed by live repository reads.

Every tool read happens at question time against the same repositories the
portal pages use — never a fixture or a cache — so a document that flipped to
under-review a second ago is what Edward sees. Each read produces a receipt,
and a read that failed, timed out, or is not supported by this platform is
reported honestly instead of guessed around.

The host supplies primitive reads (profile, requirements, documents, payments,
financials, dashboard, housing plan, appointments, help). Composite tools such
as holds, deadlines, and registration are deterministic projections over those
primitives, computed here so both the Postgres and in-memory services share
one derivation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from audentra.integrations.assistant.planner import RECEIPT_SOURCES

JsonDict = dict[str, Any]
PrimitiveRead = Callable[[], Awaitable[Mapping[str, Any]]]

DEFAULT_TOOL_TIMEOUT_SECONDS = 2.5

_REQUIREMENT_GATE_CODES: Mapping[str, str] = {
    "final_transcript": "final_transcript",
    "transcript": "final_transcript",
    "immunization": "immunization_cleared",
    "immunization_records": "immunization_cleared",
    "advising": "advising_complete",
    "orientation": "orientation_complete",
    "housing_preference": "housing_preference_selected",
    "enrollment_deposit": "enrollment_deposit_posted",
}

_OPEN_REQUIREMENT_STATUSES = {"blocked", "ready", "in_progress", "submitted", "under_review"}
_DONE_REQUIREMENT_STATUSES = {"completed", "waived", "not_applicable"}


@dataclass
class ToolExecution:
    reads: dict[str, JsonDict] = field(default_factory=dict)
    receipts: list[JsonDict] = field(default_factory=list)
    executed_tools: list[str] = field(default_factory=list)
    unavailable_data: list[JsonDict] = field(default_factory=list)


class AssistantToolHost:
    """Primitive repository reads with a per-request cache.

    The cache lives for one question only: two tools sharing the requirements
    read within a single turn is correct, while caching across turns would
    reintroduce exactly the stale-state bug this port removes.
    """

    def __init__(self, primitives: Mapping[str, PrimitiveRead]) -> None:
        self._primitives = dict(primitives)
        self._cache: dict[str, Any] = {}

    def supports(self, primitive: str) -> bool:
        return primitive in self._primitives

    async def read(self, primitive: str) -> Mapping[str, Any]:
        if primitive not in self._cache:
            self._cache[primitive] = await self._primitives[primitive]()
        return self._cache[primitive]


async def execute_tool_reads(
    selected_tools: Sequence[str],
    host: AssistantToolHost,
    *,
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> ToolExecution:
    execution = ToolExecution()
    moment = now or datetime.now(UTC)

    async def run(tool: str) -> tuple[str, JsonDict]:
        try:
            data = await asyncio.wait_for(
                _TOOL_IMPLEMENTATIONS[tool](host, moment), timeout=timeout_seconds
            )
        except TimeoutError:
            return tool, {"status": "timeout", "reason": "timeout", "retryable": True}
        except _UnsupportedRead:
            return tool, {"status": "unavailable", "reason": "not_supported", "retryable": False}
        except Exception:
            return tool, {"status": "unavailable", "reason": "read_error", "retryable": True}
        return tool, {"status": "available", "data": data}

    results = await asyncio.gather(
        *(run(tool) for tool in selected_tools if tool in _TOOL_IMPLEMENTATIONS)
    )
    for index, (tool, result) in enumerate(results):
        receipt = {
            "id": f"receipt-{index + 1}",
            "source": RECEIPT_SOURCES.get(tool, "dashboard"),
            "tool": tool,
            "recordCount": _record_count(result.get("data")),
            "status": result["status"],
        }
        execution.reads[tool] = {**result, "receipt": receipt}
        execution.receipts.append(receipt)
        execution.executed_tools.append(tool)
        if result["status"] != "available":
            execution.unavailable_data.append(
                {
                    "source": tool,
                    "reason": result.get("reason", "read_error"),
                    "retryable": bool(result.get("retryable", True)),
                }
            )
    return execution


class _UnsupportedRead(Exception):
    pass


async def _primitive(host: AssistantToolHost, name: str) -> Mapping[str, Any]:
    if not host.supports(name):
        raise _UnsupportedRead(name)
    return await host.read(name)


async def _tool_profile(host: AssistantToolHost, _now: datetime) -> JsonDict:
    profile = await _primitive(host, "profile")
    return {
        "preferredName": profile.get("preferredName") or profile.get("firstName"),
        "fullName": profile.get("fullName") or profile.get("legalName"),
        "email": profile.get("email"),
    }


async def _tool_checklist(host: AssistantToolHost, _now: datetime) -> JsonDict:
    requirements = await _primitive(host, "requirements")
    items = [
        {
            "id": item.get("id"),
            "code": item.get("code"),
            "slug": item.get("slug"),
            "title": item.get("title"),
            "status": item.get("status"),
            "blocking": bool(item.get("blocking")),
            "dueAt": item.get("dueAt"),
            "documentCategory": item.get("documentCategory"),
            "responsibleOffice": item.get("responsibleOffice"),
            "href": f"/enrollment/requirements/{item.get('slug')}",
        }
        for item in _items(requirements)
    ]
    return {"items": items, "total": len(items)}


async def _tool_documents(host: AssistantToolHost, _now: datetime) -> JsonDict:
    documents = await _primitive(host, "documents")
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


async def _tool_holds(host: AssistantToolHost, now: datetime) -> JsonDict:
    """Official holds plus derived blockers.

    The platform has no registrar hold system, and saying so is part of the
    answer. Derived blockers are computed from the same records the portal
    shows: an unpaid deposit and open blocking requirements, each naming its
    gate, its owner, and the action that clears it.
    """

    requirements = await _primitive(host, "requirements")
    dashboard = await _primitive(host, "dashboard")
    blockers: list[JsonDict] = []
    offer = _mapping(dashboard.get("offer"))
    deposit_paid = _deposit_paid(dashboard)
    if offer and not deposit_paid and int(offer.get("depositAmountCents") or 0) > 0:
        blockers.append(
            {
                "code": "enrollment_deposit_posted",
                "title": "Enrollment deposit not posted",
                "owner": "student",
                "clearingAction": "Pay the enrollment deposit from the Payments page.",
                "href": "/payments",
                "blocksRegistration": True,
            }
        )
    for item in _items(requirements):
        status = str(item.get("status") or "")
        if not item.get("blocking") or status in _DONE_REQUIREMENT_STATUSES:
            continue
        submitted = status in {"submitted", "under_review"}
        blockers.append(
            {
                "code": _REQUIREMENT_GATE_CODES.get(
                    str(item.get("code") or "").lower(), str(item.get("code") or "requirement")
                ),
                "title": str(item.get("title") or "Enrollment requirement"),
                "owner": "university" if submitted else "student",
                "clearingAction": (
                    "Waiting on university review; no student action needed."
                    if submitted
                    else f"Complete “{item.get('title')}” from your enrollment checklist."
                ),
                "href": f"/enrollment/requirements/{item.get('slug')}",
                "blocksRegistration": True,
            }
        )
    return {
        "officialHolds": [],
        "holdSystem": "not_operated",
        "derivedBlockers": blockers,
        "asOf": now.isoformat(),
    }


async def _tool_deadlines(host: AssistantToolHost, now: datetime) -> JsonDict:
    requirements = await _primitive(host, "requirements")
    deadlines: list[JsonDict] = []
    for item in _items(requirements):
        due_at = item.get("dueAt")
        if not due_at or str(item.get("status") or "") in _DONE_REQUIREMENT_STATUSES:
            continue
        deadlines.append(
            {
                "title": str(item.get("title") or "Enrollment requirement"),
                "dueAt": due_at,
                "kind": "requirement",
                "href": f"/enrollment/requirements/{item.get('slug')}",
            }
        )
    dashboard = await _primitive(host, "dashboard")
    offer = _mapping(dashboard.get("offer"))
    if offer.get("responseDeadline") and not _deposit_paid(dashboard):
        deadlines.append(
            {
                "title": "Enrollment deposit",
                "dueAt": str(offer["responseDeadline"]),
                "kind": "deposit",
                "href": "/payments",
            }
        )
    if host.supports("financials"):
        try:
            financials = await host.read("financials")
        except Exception:
            financials = {}
        for document in _sequence(_mapping(financials).get("requiredDocuments")):
            entry = _mapping(document)
            if entry.get("dueAt") and str(entry.get("status")) not in {"received", "waived"}:
                deadlines.append(
                    {
                        "title": str(entry.get("title") or "Financial aid document"),
                        "dueAt": str(entry["dueAt"]),
                        "kind": "financial_aid",
                        "href": str(entry.get("href") or "/financials"),
                    }
                )
    for deadline in deadlines:
        deadline["bucket"] = _deadline_bucket(str(deadline["dueAt"]), now)
    deadlines.sort(key=lambda item: str(item["dueAt"]))
    return {"items": deadlines, "total": len(deadlines), "asOf": now.isoformat()}


async def _tool_support(host: AssistantToolHost, _now: datetime) -> JsonDict:
    help_data = await _primitive(host, "help")
    return dict(help_data)


async def _tool_aid_status(host: AssistantToolHost, _now: datetime) -> JsonDict:
    financials = await _primitive(host, "financials")
    documents = [
        {
            "code": entry.get("code"),
            "title": entry.get("title"),
            "status": entry.get("status"),
            "dueAt": entry.get("dueAt"),
            "href": entry.get("href"),
            "satisfied": str(entry.get("status")) in {"received", "waived"},
        }
        for entry in (_mapping(item) for item in _sequence(financials.get("requiredDocuments")))
    ]
    awards = [
        {
            "name": entry.get("name"),
            "type": entry.get("type"),
            "status": entry.get("status"),
            "requiresAction": bool(entry.get("requiresAction")),
        }
        for entry in (_mapping(item) for item in _sequence(financials.get("awards")))
    ]
    return {
        "academicYear": financials.get("academicYear"),
        "requiredDocuments": documents,
        "awards": awards,
        "openRequirements": [item for item in documents if not item["satisfied"]],
    }


async def _tool_aid_summary(host: AssistantToolHost, _now: datetime) -> JsonDict:
    financials = await _primitive(host, "financials")
    return {
        "academicYear": financials.get("academicYear"),
        "costOfAttendanceCents": financials.get("costOfAttendanceCents"),
        "acceptedAidCents": financials.get("acceptedAidCents"),
        "pendingAidCents": financials.get("pendingAidCents"),
        "paymentsCents": financials.get("paymentsCents"),
        "remainingBalanceCents": financials.get("remainingBalanceCents"),
        "awards": [dict(_mapping(item)) for item in _sequence(financials.get("awards"))],
    }


async def _tool_aid_disbursements(host: AssistantToolHost, now: datetime) -> JsonDict:
    """What the record actually supports about aid money movement.

    No disbursement ledger exists in the platform; the honest projection is
    the accepted total plus every condition still holding the package open.
    """

    financials = await _primitive(host, "financials")
    gates = [
        {
            "code": str(entry.get("code") or "financial_document"),
            "title": str(entry.get("title") or "Financial aid document"),
            "satisfied": str(entry.get("status")) in {"received", "waived"},
        }
        for entry in (_mapping(item) for item in _sequence(financials.get("requiredDocuments")))
    ]
    return {
        "scheduleTracked": False,
        "disbursed": [],
        "scheduled": [],
        "acceptedAidCents": financials.get("acceptedAidCents"),
        "gates": gates,
        "asOf": now.isoformat(),
    }


async def _tool_housing_status(host: AssistantToolHost, _now: datetime) -> JsonDict:
    housing = await _primitive(host, "housing_plan")
    requirements = await _primitive(host, "requirements")
    requirement = next(
        (
            item
            for item in _items(requirements)
            if str(item.get("code") or "").lower() == "housing_preference"
        ),
        None,
    )
    return {
        "preference": housing.get("preference"),
        "residenceOption": housing.get("residenceOption"),
        "roomType": housing.get("roomType"),
        "requirementStatus": requirement.get("status") if requirement else None,
        "requirementHref": (
            f"/enrollment/requirements/{requirement.get('slug')}" if requirement else None
        ),
    }


async def _tool_housing_options(host: AssistantToolHost, _now: datetime) -> JsonDict:
    housing = await _primitive(host, "housing_plan")
    residences = [
        {
            "name": entry.get("name"),
            "value": entry.get("value"),
            "description": entry.get("description"),
            "amenities": list(_sequence(entry.get("amenities"))),
        }
        for entry in (_mapping(item) for item in _sequence(housing.get("residences")))
    ]
    return {"residences": residences, "total": len(residences)}


async def _tool_registration(host: AssistantToolHost, now: datetime) -> JsonDict:
    """Course-registration gates derived from enrollment state.

    No term registration window is modeled; the gates are the exhaustive list
    of what the record shows blocking registration, so a cause outside this
    list is by definition invented.
    """

    holds = await _tool_holds(host, now)
    gates = [
        {
            "code": blocker["code"],
            "title": blocker["title"],
            "satisfied": False,
            "owner": blocker["owner"],
            "clearingAction": blocker["clearingAction"],
            "href": blocker.get("href"),
        }
        for blocker in holds["derivedBlockers"]
        if blocker.get("blocksRegistration")
    ]
    return {
        "windowPublished": False,
        "eligible": not gates,
        "gates": gates,
        "asOf": now.isoformat(),
    }


async def _tool_account(host: AssistantToolHost, _now: datetime) -> JsonDict:
    financials = await _primitive(host, "financials")
    dashboard = await _primitive(host, "dashboard")
    offer = _mapping(dashboard.get("offer"))
    return {
        "remainingBalanceCents": financials.get("remainingBalanceCents"),
        "paymentsCents": financials.get("paymentsCents"),
        "acceptedAidCents": financials.get("acceptedAidCents"),
        "costOfAttendanceCents": financials.get("costOfAttendanceCents"),
        "depositAmountCents": offer.get("depositAmountCents"),
        "depositPaid": _deposit_paid(dashboard),
        "paymentSchedule": [
            dict(_mapping(item)) for item in _sequence(financials.get("paymentSchedule"))
        ],
    }


async def _tool_appointments(host: AssistantToolHost, _now: datetime) -> JsonDict:
    appointments = await _primitive(host, "appointments")
    items = [
        {
            "type": entry.get("type"),
            "startsAt": entry.get("startsAt"),
            "status": entry.get("status"),
        }
        for entry in (_mapping(item) for item in _items(appointments))
    ]
    return {"items": items, "total": len(items)}


_TOOL_IMPLEMENTATIONS: Mapping[
    str, Callable[[AssistantToolHost, datetime], Awaitable[JsonDict]]
] = {
    "getStudentProfile": _tool_profile,
    "getOnboardingChecklist": _tool_checklist,
    "getDocumentStatuses": _tool_documents,
    "getEnrollmentHolds": _tool_holds,
    "getStudentDeadlines": _tool_deadlines,
    "getSupportOptions": _tool_support,
    "getFinancialAidStatus": _tool_aid_status,
    "getFinancialAidSummary": _tool_aid_summary,
    "getAidDisbursements": _tool_aid_disbursements,
    "getStudentHousingStatus": _tool_housing_status,
    "getHousingOptions": _tool_housing_options,
    "getRegistrationStatus": _tool_registration,
    "getStudentAccountSummary": _tool_account,
    "getStudentAppointments": _tool_appointments,
}


def _items(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [_mapping(item) for item in _sequence(value.get("items"))]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return ()


def _deposit_paid(dashboard: Mapping[str, Any]) -> bool:
    offer = _mapping(dashboard.get("offer"))
    if isinstance(offer.get("depositPaid"), bool):
        return bool(offer["depositPaid"])
    journey = _mapping(dashboard.get("journey"))
    next_action = _mapping(journey.get("nextAction"))
    return str(next_action.get("kind") or "") not in {"pay_deposit", "enrollment_deposit"} and bool(
        offer.get("depositPaidAt")
    )


def _deadline_bucket(due_at: str, now: datetime) -> str:
    try:
        due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
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


def _record_count(data: Any) -> int:
    if isinstance(data, Mapping):
        if isinstance(data.get("items"), Sequence):
            return len(data["items"])
        for key in ("awards", "derivedBlockers", "gates", "residences", "requiredDocuments"):
            if isinstance(data.get(key), Sequence):
                return len(data[key])
        return 1
    return 0
