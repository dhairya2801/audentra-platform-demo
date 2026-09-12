"""Read-only assistant tools backed by live repository reads.

Every tool read happens at question time against the same repositories the
portal pages use — never a fixture or a cache — so a document that flipped to
under-review a second ago is what Edward sees. Each read produces a receipt,
and a read that failed, timed out, or is not supported by this platform is
reported honestly instead of guessed around.

The host supplies primitive reads (profile, requirements, documents, payments,
financials, dashboard, onboarding, housing plan, appointments, help,
academics, campus life, messages). Composite tools such as holds, deadlines,
registration, and housing eligibility are deterministic projections over those
primitives. Their shared derivations live in `audentra.domain.student_state`
so the Postgres service, the in-memory service, and the action-authority check
cannot drift into three different answers to "is the deposit paid".
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from audentra.domain.student_state import (
    AID_DOCUMENT_SATISFIED_STATUSES,
    REQUIREMENT_DONE_STATUSES,
    DepositState,
    deadline_bucket,
    derive_deposit_state,
    derive_enrollment_blockers,
    parse_moment,
    requirement_href,
    requirement_readiness,
)
from audentra.integrations.assistant.planner import RECEIPT_SOURCES
from audentra.integrations.assistant.university_catalog import UNIVERSITY_TOOLS

JsonDict = dict[str, Any]
PrimitiveRead = Callable[[], Awaitable[Mapping[str, Any]]]

DEFAULT_TOOL_TIMEOUT_SECONDS = 2.5

# Onboarding answers that are not facts about the student: a base64 signature
# image is megabytes of pixels, and no answer needs it in evidence.
_ONBOARDING_EXCLUDED_FIELDS = frozenset({"signatureImageData"})

# The one student tool that accepts an optional argument from a model read
# loop: a narrower search phrase than the whole message. Identity is still
# never an argument — the host binds the student.
STUDENT_TOOL_ARGUMENTS: Mapping[str, Mapping[str, str]] = {
    "getInstitutionalPolicies": {
        "query": (
            "text — the institutional question in a few words, passed as the JSON "
            'object string {"query": "..."} (optional; defaults to the message)'
        )
    },
}


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
        self._cache: dict[str, Mapping[str, Any]] = {}
        # The question being answered this turn. The pipeline sets it before
        # any tool runs; the institutional-knowledge tool searches by it.
        # A model read loop may narrow it per call through `tool_arguments`.
        self.question: str | None = None
        self.tool_arguments: dict[str, Mapping[str, Any]] = {}

    def supports(self, primitive: str) -> bool:
        return primitive in self._primitives

    async def read(self, primitive: str, **arguments: Any) -> Mapping[str, Any]:
        key = primitive if not arguments else primitive + "|" + repr(sorted(arguments.items()))
        if key not in self._cache:
            reader = self._primitives[primitive]
            self._cache[key] = await (reader(**arguments) if arguments else reader())
        return self._cache[key]


async def execute_tool_reads(
    selected_tools: Sequence[str],
    host: AssistantToolHost,
    *,
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    now: datetime | None = None,
    receipt_offset: int = 0,
) -> ToolExecution:
    execution = ToolExecution()
    moment = now or datetime.now(UTC)

    async def run(tool: str) -> tuple[str, JsonDict]:
        started = time.perf_counter()

        def timed(result: JsonDict) -> tuple[str, JsonDict]:
            result["durationMs"] = round((time.perf_counter() - started) * 1_000)
            return tool, result

        try:
            data = await asyncio.wait_for(
                _TOOL_IMPLEMENTATIONS[tool](host, moment), timeout=timeout_seconds
            )
        except TimeoutError:
            return timed({"status": "timeout", "reason": "timeout", "retryable": True})
        except _UnsupportedRead:
            return timed({"status": "unavailable", "reason": "not_supported", "retryable": False})
        except Exception:
            return timed({"status": "unavailable", "reason": "read_error", "retryable": True})
        return timed({"status": "available", "data": data})

    results = await asyncio.gather(
        *(run(tool) for tool in selected_tools if tool in _TOOL_IMPLEMENTATIONS)
    )
    for index, (tool, result) in enumerate(results):
        receipt = {
            "id": f"receipt-{receipt_offset + index + 1}",
            "source": RECEIPT_SOURCES.get(tool, "dashboard"),
            "tool": tool,
            "recordCount": _record_count(result.get("data")),
            "status": result["status"],
            "durationMs": result.get("durationMs", 0),
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
    """A read the tool cannot answer without; its absence fails the tool."""

    if not host.supports(name):
        raise _UnsupportedRead(name)
    return await host.read(name)


async def _optional(host: AssistantToolHost, name: str) -> Mapping[str, Any] | None:
    """A read that enriches an answer; `None` means "could not be read".

    The distinction matters downstream: a missing read must never collapse
    into a default value that reads as a verified fact.
    """

    if not host.supports(name):
        return None
    try:
        return await host.read(name)
    except Exception:
        return None


async def _deposit_state(host: AssistantToolHost) -> DepositState:
    """The one deposit derivation, shared by every tool that needs it."""

    dashboard, payments, financials = await asyncio.gather(
        _optional(host, "dashboard"),
        _optional(host, "payments"),
        _optional(host, "financials"),
    )
    return derive_deposit_state(dashboard=dashboard, payments=payments, financials=financials)


# --------------------------------------------------------------------------
# Identity, admission, and enrollment position
# --------------------------------------------------------------------------


async def _tool_profile(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """Who the student is, as the Profile page shows it."""

    profile = await _primitive(host, "profile")
    return {
        "preferredName": profile.get("preferredName") or profile.get("firstName"),
        "fullName": profile.get("fullName")
        or profile.get("legalName")
        or _full_name(profile.get("firstName"), profile.get("lastName")),
        "firstName": profile.get("firstName"),
        "lastName": profile.get("lastName"),
        "email": profile.get("email"),
        "emailVerified": profile.get("emailVerified"),
        "pronouns": profile.get("pronouns"),
        "mobilePhone": profile.get("mobilePhone"),
        "phoneVerified": profile.get("phoneVerified"),
        "communicationPreference": profile.get("communicationPreference"),
        "updatedAt": profile.get("updatedAt"),
        "href": "/profile",
    }


async def _tool_enrollment_state(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """Admission decision, program placement, and enrollment progress.

    The dashboard's program/term/campus strip, the offer's decision and
    deadline, the journey progress bar, and the onboarding wizard's position
    are one question for a student ("where am I in all this?") and one read
    here. Deposit truth comes from the shared derivation, never from the
    dashboard projection, which does not carry it.
    """

    dashboard = await _primitive(host, "dashboard")
    onboarding = await _optional(host, "onboarding")
    deposit = await _deposit_state(host)
    offer = _mapping(dashboard.get("offer"))
    student = _mapping(dashboard.get("student"))
    journey = _mapping(dashboard.get("journey"))
    next_action = _mapping(journey.get("nextAction"))
    state: JsonDict = {
        "student": {
            "id": student.get("id"),
            "preferredName": student.get("preferredName"),
            "fullName": student.get("fullName"),
            "classYear": student.get("classYear"),
        },
        "admission": {
            "offerStatus": offer.get("status"),
            "programName": offer.get("programName"),
            "termName": offer.get("termName"),
            "campusName": offer.get("campusName"),
            "responseDeadline": offer.get("responseDeadline"),
        },
        # Same key as every other tool that carries it: the derived-state
        # lookup finds one name, not three.
        "depositState": deposit.as_json(),
        "journey": {
            "status": journey.get("status"),
            "completionPercent": journey.get("completionPercent"),
            "nextAction": {
                "code": next_action.get("code"),
                "label": next_action.get("label"),
                "href": next_action.get("href"),
            }
            if next_action
            else None,
        },
        "unreadMessageCount": dashboard.get("unreadMessageCount"),
        "href": "/dashboard",
    }
    if onboarding is not None:
        state["onboarding"] = {
            "status": onboarding.get("status"),
            "currentStep": onboarding.get("currentStep"),
            "completedSteps": [str(step) for step in _sequence(onboarding.get("completedSteps"))],
            "skippedSteps": [
                str(step)
                for step in _sequence(_mapping(onboarding.get("data")).get("skippedSteps"))
            ],
            "completedAt": onboarding.get("completedAt"),
            "href": "/onboarding",
        }
    else:
        state["onboarding"] = None
        state["onboardingRead"] = "unavailable"
    return state


async def _tool_onboarding_responses(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """What the student actually answered in the onboarding wizard.

    Residency and citizenship, the mailing address on file, emergency
    contacts, family permissions, insurance and accommodation interest,
    campus interests and goals, the enrollment-signature record, and any
    tenant-authored custom fields. All of it is visible to the student in the
    portal; none of it was reachable before.
    """

    onboarding = await _primitive(host, "onboarding")
    data = _mapping(onboarding.get("data"))
    answers = {
        key: value
        for key, value in data.items()
        if key not in _ONBOARDING_EXCLUDED_FIELDS and value is not None and value != []
    }
    contacts = [
        {
            "name": entry.get("name"),
            "relationship": entry.get("relationship"),
            "phone": entry.get("phone"),
            "email": entry.get("email"),
            "isPrimary": entry.get("isPrimary"),
        }
        for raw in _sequence(data.get("emergencyContacts"))
        if (entry := _mapping(raw))
    ]
    permissions = [
        {
            "name": entry.get("name"),
            "relationship": entry.get("relationship"),
            "email": entry.get("email"),
            "scopes": list(_sequence(entry.get("scopes"))),
        }
        for raw in _sequence(data.get("familyPermissions"))
        if (entry := _mapping(raw))
    ]
    address_parts = [
        data.get("streetAddress"),
        data.get("addressLine2"),
        data.get("city"),
        data.get("stateOrProvince"),
        data.get("postalCode"),
        data.get("country"),
    ]
    mailing_address = ", ".join(str(part) for part in address_parts if part)
    return {
        "status": onboarding.get("status"),
        "currentStep": onboarding.get("currentStep"),
        "completedSteps": [str(step) for step in _sequence(onboarding.get("completedSteps"))],
        "skippedSteps": [str(step) for step in _sequence(data.get("skippedSteps"))],
        "completedAt": onboarding.get("completedAt"),
        "updatedAt": onboarding.get("updatedAt"),
        "citizenshipStatus": data.get("citizenshipStatus"),
        "residencyStatus": data.get("residencyStatus"),
        "residencyVerificationPath": data.get("residencyVerificationPath"),
        "mailingAddress": mailing_address or None,
        "accommodationInterest": data.get("accommodationInterest"),
        "supportNeeds": list(_sequence(data.get("supportNeeds"))),
        "insuranceInterest": data.get("insuranceInterest"),
        "campusInterests": list(_sequence(data.get("campusInterests"))),
        "firstMonthGoals": list(_sequence(data.get("firstMonthGoals"))),
        "socialComfort": data.get("socialComfort"),
        "depositChoice": data.get("depositChoice"),
        "emergencyContacts": contacts,
        "familyPermissions": permissions,
        "signature": {
            "recorded": bool(data.get("signatureFullName")),
            "fullName": data.get("signatureFullName"),
            "method": data.get("signatureMethod"),
            "consent": data.get("signatureConsent"),
            "signedDocumentIds": list(_sequence(data.get("signedDocumentIds"))),
        },
        "customFields": dict(_mapping(data.get("customFields"))),
        "answers": answers,
        "href": "/onboarding",
    }


# --------------------------------------------------------------------------
# Checklist, documents, blockers, deadlines
# --------------------------------------------------------------------------


async def _tool_checklist(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """The enrollment checklist exactly as the Enrollment page renders it."""

    requirements = await _primitive(host, "requirements")
    items = []
    for item in _items(requirements):
        entry: JsonDict = {
            "id": item.get("id"),
            "code": item.get("code"),
            "slug": item.get("slug"),
            "title": item.get("title"),
            "description": _clip(item.get("description"), 400),
            "status": item.get("status"),
            "blocking": bool(item.get("blocking")),
            "dueAt": item.get("dueAt"),
            "progressPercent": item.get("progressPercent"),
            "submissionType": item.get("submissionType"),
            "flowKind": item.get("flowKind"),
            "documentCategory": item.get("documentCategory"),
            "responsibleOffice": item.get("responsibleOffice"),
            "dependencyCodes": [str(code) for code in _sequence(item.get("dependencyCodes"))],
            "href": requirement_href(item),
            **requirement_readiness(item, _items(requirements)),
        }
        reward = _mapping(item.get("reward"))
        if reward:
            entry["reward"] = {"points": reward.get("points"), "earned": reward.get("earned")}
        response = _mapping(item.get("response"))
        if response:
            entry["response"] = {
                "submittedAt": response.get("submittedAt"),
                "interactionType": response.get("interactionType"),
                "answers": _bounded_answers(response.get("data")),
            }
        items.append(entry)
    completed = sum(1 for item in items if str(item["status"]) in REQUIREMENT_DONE_STATUSES)
    return {
        "items": items,
        "total": len(items),
        "completedCount": completed,
        "openCount": len(items) - completed,
        "href": "/enrollment",
    }


async def _tool_documents(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """Uploaded documents with their review and extraction lifecycle."""

    documents = await _primitive(host, "documents")
    items = []
    for item in _items(documents):
        extraction = _mapping(item.get("extraction"))
        entry: JsonDict = {
            "id": item.get("id"),
            "fileName": item.get("fileName"),
            "category": item.get("category"),
            "status": item.get("status"),
            "requirementId": item.get("requirementId"),
            "createdAt": item.get("createdAt"),
            "sizeBytes": item.get("sizeBytes"),
            "processingMode": item.get("processingMode"),
            "extractionStatus": extraction.get("status") if extraction else None,
        }
        if extraction:
            courses = _sequence(extraction.get("courses"))
            entry["extraction"] = {
                "status": extraction.get("status"),
                "confidence": extraction.get("confidence"),
                "courseCount": len(courses),
                "institution": extraction.get("institution"),
                "confirmedAt": extraction.get("confirmedAt"),
                "failureCode": extraction.get("failureCode"),
                "warnings": [str(note) for note in _sequence(extraction.get("warnings"))][:4],
            }
        decision = _mapping(item.get("decision"))
        if decision:
            entry["decision"] = {
                "outcome": decision.get("outcome"),
                "decidedAt": decision.get("decidedAt"),
                "note": _clip(decision.get("note"), 240),
            }
        items.append(entry)
    return {"items": items, "total": len(items), "href": "/documents"}


async def _tool_holds(host: AssistantToolHost, now: datetime) -> JsonDict:
    """Official holds plus derived blockers.

    The platform has no registrar hold system, and saying so is part of the
    answer. Derived blockers come from the shared domain projection over the
    same records the portal shows, each naming its gate, its owner, and the
    action that clears it.
    """

    requirements = await _primitive(host, "requirements")
    deposit = await _deposit_state(host)
    blockers = derive_enrollment_blockers(requirements=requirements, deposit=deposit)
    result: JsonDict = {
        "officialHolds": [],
        "holdSystem": "not_operated",
        "derivedBlockers": blockers,
        "depositState": deposit.as_json(),
        "asOf": now.isoformat(),
    }
    if not deposit.known:
        # Silence here would read as "no deposit blocker"; name the gap.
        result["undeterminedDomains"] = [
            {
                "domain": "enrollment_deposit",
                "reason": "The payment record could not be read this turn.",
            }
        ]
    return result


async def _tool_deadlines(host: AssistantToolHost, now: datetime) -> JsonDict:
    """Every dated obligation the portal shows, in one ordered list."""

    requirements = await _primitive(host, "requirements")
    deadlines: list[JsonDict] = []
    for item in _items(requirements):
        due_at = item.get("dueAt")
        if not due_at or str(item.get("status") or "") in REQUIREMENT_DONE_STATUSES:
            continue
        deadlines.append(
            {
                "title": str(item.get("title") or "Enrollment requirement"),
                "dueAt": due_at,
                "kind": "requirement",
                "href": requirement_href(item),
            }
        )
    deposit = await _deposit_state(host)
    if deposit.due_at and deposit.outstanding:
        deadlines.append(
            {
                "title": "Enrollment deposit",
                "dueAt": deposit.due_at,
                "kind": "deposit",
                "href": "/payments",
            }
        )
    financials = await _optional(host, "financials")
    if financials is not None:
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
                        "href": str(entry.get("href") or "/financials"),
                    }
                )
        for raw in _sequence(_mapping(financials).get("paymentSchedule")):
            entry = _mapping(raw)
            if (
                _text(entry.get("kind")) == "installment"
                and entry.get("dueAt")
                and _text(entry.get("status")) != "paid"
            ):
                deadlines.append(
                    {
                        "title": str(entry.get("label") or "Tuition installment"),
                        "dueAt": str(entry["dueAt"]),
                        "kind": "installment",
                        "projected": bool(entry.get("projected")),
                        "href": "/financials",
                    }
                )
    appointments = await _optional(host, "appointments")
    if appointments is not None:
        for raw in _sequence(_mapping(appointments).get("items")):
            entry = _mapping(raw)
            starts_at = parse_moment(entry.get("startsAt"))
            if starts_at is None or starts_at < now or _text(entry.get("status")) == "cancelled":
                continue
            deadlines.append(
                {
                    "title": f"{_appointment_label(entry.get('type'))} appointment",
                    "dueAt": str(entry.get("startsAt")),
                    "kind": "appointment",
                    "href": "/appointments",
                }
            )
    for deadline in deadlines:
        deadline["bucket"] = deadline_bucket(deadline["dueAt"], now)
    deadlines.sort(key=lambda item: str(item["dueAt"]))
    next_deadline = next(
        (item for item in deadlines if item["bucket"] != "overdue"),
        None,
    )
    return {
        "items": deadlines,
        "total": len(deadlines),
        "overdue": [item for item in deadlines if item["bucket"] == "overdue"],
        "next": next_deadline,
        "asOf": now.isoformat(),
    }


# --------------------------------------------------------------------------
# Support
# --------------------------------------------------------------------------


async def _tool_support(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """Approved institution-wide help articles and the support contact.

    Student-specific support conversations are deliberately not here: they are
    student state, and mixing them into an institution-knowledge read would
    misclassify their provenance.
    """

    help_data = await _primitive(host, "help")
    return {
        "articles": [
            {
                "question": entry.get("question"),
                "answer": _clip(entry.get("answer"), 600),
                "category": entry.get("category"),
            }
            for raw in _sequence(help_data.get("articles"))
            if (entry := _mapping(raw))
        ],
        "support": dict(_mapping(help_data.get("support"))),
        "href": "/help",
    }


async def _tool_support_requests(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """The student's own support conversations and their replies."""

    help_data = await _primitive(host, "help")
    requests = []
    for raw in _sequence(help_data.get("requests")):
        entry = _mapping(raw)
        messages = [_mapping(item) for item in _sequence(entry.get("messages"))]
        requests.append(
            {
                "id": entry.get("id"),
                "topicCode": entry.get("topicCode"),
                "subject": entry.get("subject"),
                "status": entry.get("status"),
                "priority": entry.get("priority"),
                "requirementId": entry.get("requirementId"),
                "createdAt": entry.get("createdAt"),
                "lastMessageAt": entry.get("lastMessageAt"),
                "messageCount": len(messages),
                "latestMessage": (
                    {
                        "authorType": messages[-1].get("authorType"),
                        "body": _clip(messages[-1].get("body"), 400),
                        "createdAt": messages[-1].get("createdAt"),
                    }
                    if messages
                    else None
                ),
            }
        )
    open_requests = [item for item in requests if str(item["status"]) not in {"resolved", "closed"}]
    return {
        "items": requests,
        "total": len(requests),
        "openCount": len(open_requests),
        "href": "/help",
    }


# --------------------------------------------------------------------------
# Financial aid and the student account
# --------------------------------------------------------------------------


async def _tool_aid_status(host: AssistantToolHost, _now: datetime) -> JsonDict:
    financials = await _primitive(host, "financials")
    documents = [
        {
            "code": entry.get("code"),
            "title": entry.get("title"),
            "description": _clip(entry.get("description"), 240),
            "status": entry.get("status"),
            "dueAt": entry.get("dueAt"),
            "href": entry.get("href"),
            "satisfied": str(entry.get("status")) in AID_DOCUMENT_SATISFIED_STATUSES,
        }
        for entry in (_mapping(item) for item in _sequence(financials.get("requiredDocuments")))
    ]
    # Amounts ride along with award identity. The old status/summary split
    # (identity here, amounts only in the summary read) forced a routing
    # knife-edge: any "how much…" question that landed here had no amounts in
    # evidence, and the composer invented $0. An award's amount is part of
    # what the award *is*.
    awards = [
        {
            "name": entry.get("name"),
            "type": entry.get("type"),
            "source": entry.get("source"),
            "status": entry.get("status"),
            "offeredAmountCents": entry.get("offeredAmountCents"),
            "acceptedAmountCents": entry.get("acceptedAmountCents"),
            "requiresAction": bool(entry.get("requiresAction")),
        }
        for entry in (_mapping(item) for item in _sequence(financials.get("awards")))
    ]
    return {
        "academicYear": financials.get("academicYear"),
        "requiredDocuments": documents,
        "awards": awards,
        "openRequirements": [item for item in documents if not item["satisfied"]],
        "href": "/financials",
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
        "paymentPlans": [
            {
                "name": entry.get("name"),
                "installmentCount": entry.get("installmentCount"),
                "installmentAmountCents": entry.get("installmentAmountCents"),
                "enrollmentFeeCents": entry.get("enrollmentFeeCents"),
                "status": entry.get("status"),
            }
            for entry in (_mapping(item) for item in _sequence(financials.get("paymentPlans")))
        ],
        "href": "/financials",
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
            "satisfied": str(entry.get("status")) in AID_DOCUMENT_SATISFIED_STATUSES,
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


async def _tool_aid_support(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """Approved financial-aid support routes.

    Canonical portal destinations plus the same tenant support contact and
    help articles the Help page reads — never a synthetic office fact.
    """

    help_data = await _primitive(host, "help")
    articles = [
        {
            "question": entry.get("question"),
            "answer": _clip(entry.get("answer"), 600),
            "category": entry.get("category"),
        }
        for entry in (_mapping(item) for item in _sequence(help_data.get("articles")))
        if "aid" in str(entry.get("category") or "").lower()
        or "financial" in f"{entry.get('question') or ''} {entry.get('category') or ''}".lower()
    ][:6]
    return {
        "options": [
            {
                "kind": "appointment",
                "label": "Book a financial-aid appointment",
                "href": "/appointments",
            },
            {"kind": "page", "label": "Open Financials", "href": "/financials"},
            {"kind": "page", "label": "Open Documents", "href": "/documents"},
        ],
        "support": dict(_mapping(help_data.get("support"))),
        "articles": articles,
    }


async def _tool_account(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """Balance, charges, the deposit, the plan, and the payment receipts."""

    financials = await _primitive(host, "financials")
    deposit = await _deposit_state(host)
    payments = await _optional(host, "payments")
    history = [
        {
            "type": entry.get("type"),
            "amountCents": entry.get("amountCents"),
            "status": entry.get("status"),
            "createdAt": entry.get("createdAt"),
            "processorReference": entry.get("processorReference"),
        }
        for raw in _sequence(_mapping(payments).get("items"))
        if (entry := _mapping(raw))
    ]
    enrolled_plan = next(
        (
            entry
            for raw in _sequence(financials.get("paymentPlans"))
            if (entry := _mapping(raw)) and _text(entry.get("status")) == "enrolled"
        ),
        None,
    )
    return {
        "remainingBalanceCents": financials.get("remainingBalanceCents"),
        "paymentsCents": financials.get("paymentsCents"),
        "acceptedAidCents": financials.get("acceptedAidCents"),
        "costOfAttendanceCents": financials.get("costOfAttendanceCents"),
        "depositAmountCents": deposit.amount_cents,
        "depositPaid": deposit.paid,
        "depositPaymentPending": deposit.pending,
        "depositState": deposit.as_json(),
        "enrolledPaymentPlan": (
            {
                "name": enrolled_plan.get("name"),
                "installmentCount": enrolled_plan.get("installmentCount"),
                "installmentAmountCents": enrolled_plan.get("installmentAmountCents"),
            }
            if enrolled_plan
            else None
        ),
        "paymentSchedule": [
            dict(_mapping(item)) for item in _sequence(financials.get("paymentSchedule"))
        ],
        "paymentHistory": history if payments is not None else None,
        "href": "/financials",
    }


async def _tool_academic_standing(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """Satisfactory academic progress and earned-credit position.

    The Financials page renders the SAP card — GPA against the minimum,
    completion rate, attempted credits against the maximum — and the
    Classrooms page renders credit progress. Both are academic-standing facts
    a student asks about directly ("what's my GPA?").
    """

    financials = await _primitive(host, "financials")
    sap = _mapping(financials.get("sap"))
    if not sap:
        raise _UnsupportedRead("sap")
    academics = await _optional(host, "academics")
    progress = _mapping(_mapping(academics).get("progress")) if academics else {}
    return {
        "academicYear": financials.get("academicYear"),
        "satisfactoryAcademicProgress": {
            "status": sap.get("status"),
            "cumulativeGpa": sap.get("cumulativeGpa"),
            "minimumGpa": sap.get("minimumGpa"),
            "meetingGpaMinimum": _at_least(sap.get("cumulativeGpa"), sap.get("minimumGpa")),
            "completionRatePercent": sap.get("completionRatePercent"),
            "minimumCompletionRatePercent": sap.get("minimumCompletionRatePercent"),
            "attemptedCredits": sap.get("attemptedCredits"),
            "maximumAttemptedCredits": sap.get("maximumAttemptedCredits"),
        },
        "credits": {
            "completedCredits": progress.get("completedCredits"),
            "exemptedCredits": progress.get("exemptedCredits"),
            "requiredCredits": progress.get("requiredCredits"),
        }
        if progress
        else None,
        "href": "/financials",
    }


# --------------------------------------------------------------------------
# Institutional knowledge
# --------------------------------------------------------------------------


async def _tool_institutional_policies(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """Approved institutional knowledge relevant to this question.

    The search runs server-side against the tenant's published corpus and is
    scoped to the signed-in student: the host's `question` (or a narrower
    `query` a model loop supplied) is the search text, and the repository
    computes each document's applicability from the student's own record. The
    tool never takes a student identifier — identity is bound by the host.
    """

    if not host.supports("institution_knowledge"):
        raise _UnsupportedRead("institution_knowledge")
    arguments = _mapping(host.tool_arguments.get("getInstitutionalPolicies"))
    query = str(arguments.get("query") or host.question or "").strip()
    if not query:
        raise _UnsupportedRead("institution_knowledge")
    result = await host.read("institution_knowledge", query=query)
    if "sources" in result:
        return dict(result)
    documents = [dict(_mapping(item)) for item in _sequence(result.get("documents"))]
    return {
        "query": result.get("query", query),
        "documents": documents,
        "total": len(documents),
        "totalMatches": result.get("totalMatches", len(documents)),
        "calendar": [dict(_mapping(item)) for item in _sequence(result.get("calendar"))],
        "offices": [dict(_mapping(item)) for item in _sequence(result.get("offices"))],
        "studentFacets": dict(_mapping(result.get("studentFacets"))) or None,
        "answerGuidance": result.get("answerGuidance"),
        "today": result.get("today"),
        "retrievalPolicy": result.get("retrievalPolicy"),
        "href": "/help",
    }


# --------------------------------------------------------------------------
# Housing
# --------------------------------------------------------------------------


async def _tool_housing_status(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """The full housing plan the student filled in, plus its requirement."""

    housing = await _primitive(host, "housing_plan")
    requirements = await _optional(host, "requirements")
    requirement = _find_requirement(requirements, "housing_preference")
    preferences: JsonDict = {
        key: housing.get(key)
        for key in (
            "roomType",
            "bathroomPreference",
            "roommateMatching",
            "knownRoommateName",
            "knownRoommateEmail",
            "sleepSchedule",
            "studyHabits",
            "roomNoise",
            "cleanliness",
            "guestPreference",
            "temperaturePreference",
            "smokeVapeCompatibility",
            "substanceFreeHousing",
            "genderInclusiveHousing",
            "accessibleHousingInformation",
        )
        if housing.get(key) is not None
    }
    return {
        "preference": housing.get("preference"),
        "residenceOption": housing.get("residenceOption"),
        "residencePreferences": [
            str(value) for value in _sequence(housing.get("residencePreferences"))
        ],
        "livingLearningCommunities": [
            str(value) for value in _sequence(housing.get("livingLearningCommunities"))
        ],
        "roomType": housing.get("roomType"),
        "preferences": preferences,
        "offCampusStatus": housing.get("offCampusStatus"),
        "commuteMode": housing.get("commuteMode"),
        "commuteDuration": housing.get("commuteDuration"),
        "requirementStatus": requirement.get("status") if requirement else None,
        "requirementHref": requirement_href(requirement) if requirement else None,
        # The platform models no room assignment; saying nothing would invite
        # the composer to imply one exists.
        "roomAssignment": {"tracked": False},
        "href": "/onboarding",
    }


async def _tool_housing_options(host: AssistantToolHost, _now: datetime) -> JsonDict:
    housing = await _primitive(host, "housing_plan")
    residences = [
        {
            "name": entry.get("name"),
            "value": entry.get("value"),
            "description": _clip(entry.get("description"), 400),
            "amenities": list(_sequence(entry.get("amenities"))),
        }
        for entry in (_mapping(item) for item in _sequence(housing.get("residences")))
    ]
    return {"residences": residences, "total": len(residences)}


async def _tool_housing_eligibility(host: AssistantToolHost, now: datetime) -> JsonDict:
    """Whether the student can act on housing right now, from canonical state.

    No synthetic application window and no invented housing rules: eligibility
    is the requirement engine's own status for the housing step, and the gates
    are the open blocking items already on the record when that step is
    blocked. Where the platform has no data (application windows, room
    assignment), the field says so instead of guessing.
    """

    requirements = await _primitive(host, "requirements")
    requirement = _find_requirement(requirements, "housing_preference")
    status = str(requirement.get("status") or "") if requirement else None
    if requirement is None:
        eligibility = "no_housing_step"
    elif status in REQUIREMENT_DONE_STATUSES:
        eligibility = "already_completed"
    elif status == "blocked":
        eligibility = "blocked"
    else:
        eligibility = "eligible_now"
    gates: list[JsonDict] = []
    if eligibility == "blocked":
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
            if blocker["code"] != "housing_preference_selected"
        ]
    return {
        "eligibility": eligibility,
        "eligibleNow": eligibility == "eligible_now",
        "housingStep": (
            {
                "title": requirement.get("title"),
                "status": status,
                "dueAt": requirement.get("dueAt"),
                "href": requirement_href(requirement),
            }
            if requirement
            else None
        ),
        "gates": gates,
        # The platform models no housing application window or assignment.
        "applicationWindow": {"published": False},
        "asOf": now.isoformat(),
    }


# --------------------------------------------------------------------------
# Registration, appointments, academics, campus life, messages
# --------------------------------------------------------------------------


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
        "undeterminedDomains": holds.get("undeterminedDomains", []),
        "asOf": now.isoformat(),
    }


async def _tool_academics(host: AssistantToolHost, _now: datetime) -> JsonDict:
    academics = await _primitive(host, "academics")
    selected = _mapping(academics.get("selectedProgram"))
    progress = _mapping(academics.get("progress"))
    plan: list[JsonDict] = []
    for raw in _sequence(academics.get("plan"))[:16]:
        item = _mapping(raw)
        course = _mapping(item.get("course"))
        plan.append(
            {
                "code": str(course.get("code") or ""),
                "title": str(course.get("title") or ""),
                "credits": course.get("credits"),
                "category": item.get("category"),
                "recommendedTerm": item.get("recommendedTerm"),
                "status": item.get("status"),
                "missingPrerequisites": [
                    str(code) for code in _sequence(item.get("missingPrerequisiteCodes"))[:12]
                ],
            }
        )
    exemptions = [
        {
            "targetCourseCode": entry.get("targetCourseCode"),
            "status": entry.get("status"),
            "reason": _clip(entry.get("reason") or entry.get("rationale"), 240),
            "ruleCode": entry.get("ruleCode"),
        }
        for entry in (
            _mapping(item) for item in _sequence(academics.get("exemptionRecommendations"))[:16]
        )
        if entry.get("targetCourseCode")
    ]
    return {
        "selectedProgram": str(selected.get("name") or ""),
        "degree": str(selected.get("degree") or ""),
        "programCode": selected.get("code"),
        "totalCredits": selected.get("totalCredits"),
        "catalogVersion": str(academics.get("catalogVersion") or ""),
        "progress": {
            "completedCredits": progress.get("completedCredits"),
            "exemptedCredits": progress.get("exemptedCredits"),
            "requiredCredits": progress.get("requiredCredits"),
        }
        if progress
        else None,
        "exemptionRecommendations": exemptions,
        "suggestedExemptions": [
            str(item["targetCourseCode"])
            for item in exemptions
            if str(item.get("status")) in {"suggested", "needs_review"}
        ],
        "plan": plan,
        "total": len(plan),
        "href": "/classrooms",
    }


async def _tool_campus_life(host: AssistantToolHost, now: datetime) -> JsonDict:
    campus = await _primitive(host, "campus_life")
    events = []
    registered = []
    for raw in _sequence(campus.get("events")):
        item = _mapping(raw)
        entry = {
            "title": item.get("title"),
            "startsAt": item.get("startsAt"),
            "location": item.get("location"),
            "category": item.get("category"),
            "registrationStatus": item.get("registrationStatus"),
        }
        if _text(item.get("registrationStatus")) in {"registered", "confirmed", "waitlisted"}:
            registered.append(entry)
        starts_at = parse_moment(item.get("startsAt"))
        if starts_at is None or starts_at >= now:
            events.append(entry)
    clubs = [
        {key: item.get(key) for key in ("id", "name", "category", "description", "nextActivity")}
        for raw in _sequence(campus.get("clubs"))[:16]
        if (item := _mapping(raw))
    ]
    return {
        "upcomingEvents": events[:8],
        "myRegistrations": registered,
        "clubs": clubs,
        "total": len(events[:8]) + len(clubs),
        "href": "/campus-life",
    }


async def _tool_messages(host: AssistantToolHost, _now: datetime) -> JsonDict:
    """Recent portal messages, with enough body to answer "what did X say?"."""

    messages = await _primitive(host, "messages")
    latest = [
        {
            "subject": item.get("subject") or item.get("title"),
            "senderName": item.get("senderName"),
            "kind": item.get("kind"),
            "sentAt": item.get("sentAt") or item.get("createdAt"),
            "unread": item.get("readAt") is None,
            "body": _clip(item.get("body"), 600),
            "href": item.get("href"),
        }
        for raw in _sequence(messages.get("items"))[:8]
        if (item := _mapping(raw))
    ]
    unread = messages.get("unreadCount")
    if not isinstance(unread, int):
        unread = sum(1 for item in latest if item["unread"])
    return {"unreadCount": unread, "latest": latest, "href": "/messages"}


async def _tool_appointments(host: AssistantToolHost, now: datetime) -> JsonDict:
    appointments = await _primitive(host, "appointments")
    items = []
    upcoming = []
    for entry in (_mapping(item) for item in _items(appointments)):
        staff = entry.get("staff")
        record = {
            "type": entry.get("type"),
            "label": _appointment_label(entry.get("type")),
            "startsAt": entry.get("startsAt"),
            "endsAt": entry.get("endsAt"),
            "status": entry.get("status"),
            "notes": _clip(entry.get("notes"), 240),
            "modality": entry.get("modality"),
            "location": entry.get("location"),
            "with": (
                {
                    "name": _mapping(staff).get("name"),
                    "title": _mapping(staff).get("title"),
                    "component": _mapping(staff).get("component"),
                }
                if isinstance(staff, Mapping)
                else None
            ),
        }
        items.append(record)
        starts_at = parse_moment(entry.get("startsAt"))
        if (
            _text(entry.get("status")) not in {"cancelled", "completed"}
            and starts_at is not None
            and starts_at >= now
        ):
            upcoming.append(record)
    result: JsonDict = {
        "items": items,
        "total": len(items),
        "upcoming": upcoming,
        "bookingHref": "/appointments",
    }
    if host.supports("advising"):
        advising = await _primitive(host, "advising")
        primary = advising.get("primaryAdviser")
        result["primaryAdviser"] = (
            {
                "name": _mapping(_mapping(primary).get("staff")).get("name"),
                "title": _mapping(_mapping(primary).get("staff")).get("title"),
                "employmentStatus": _mapping(_mapping(primary).get("staff")).get(
                    "employmentStatus"
                ),
                "nextOpenSlotAt": _mapping(_mapping(primary).get("availability")).get(
                    "nextOpenSlotAt"
                ),
            }
            if isinstance(primary, Mapping)
            else None
        )
        result["advisingGaps"] = [
            _mapping(gap).get("message") for gap in _sequence(advising.get("gaps"))
        ]
    return result


async def _tool_advising(host: AssistantToolHost, now: datetime) -> JsonDict:
    """The student's assigned advisers and whether each can be booked.

    The portal's Advising page and the appointments booking drawer read the
    same projection, so Edward names the same adviser, email and next open
    slot the student sees on screen. Staff phone numbers do not exist in the
    schema and are deliberately absent.
    """

    advising = await _primitive(host, "advising")

    def person(entry: Any) -> JsonDict | None:
        if not isinstance(entry, Mapping):
            return None
        staff = _mapping(entry.get("staff"))
        availability = _mapping(entry.get("availability"))
        return {
            "role": str(entry.get("role") or "").replace("_", " "),
            "name": staff.get("name"),
            "title": staff.get("title"),
            "email": staff.get("email"),
            "component": staff.get("component"),
            "officeLocation": staff.get("officeLocation"),
            "employmentStatus": staff.get("employmentStatus"),
            "leaveUntil": staff.get("leaveUntil"),
            "assignedAt": entry.get("assignedAt"),
            "bookable": availability.get("bookable"),
            "availabilityReason": availability.get("reason"),
            "nextOpenSlotAt": availability.get("nextOpenSlotAt"),
        }

    advisers = [
        record
        for record in (person(item) for item in _sequence(advising.get("advisers")))
        if record is not None
    ]
    primary = person(advising.get("primaryAdviser"))
    coverage = []
    if host.supports("university_record"):
        relationships = await host.read("university_record", domain="relationships")
        coverage = [
            {
                key: row.get(key)
                for key in ("covering_name", "covering_email", "starts_at", "ends_at")
            }
            for row in _sequence(relationships.get("coverage"))
            if isinstance(row, Mapping)
        ]
    return {
        "primaryAdviser": primary,
        "coverage": coverage,
        "advisers": advisers,
        "total": len(advisers),
        "gaps": [
            {"code": _mapping(gap).get("code"), "message": _mapping(gap).get("message")}
            for gap in _sequence(advising.get("gaps"))
            if isinstance(gap, Mapping)
        ],
        "advisingRequirement": _mapping(advising.get("advising")) or None,
        "bookingHref": "/appointments",
        "asOf": now.isoformat(),
    }


_TOOL_IMPLEMENTATIONS: Mapping[
    str, Callable[[AssistantToolHost, datetime], Awaitable[JsonDict]]
] = {
    "getStudentProfile": _tool_profile,
    "getEnrollmentState": _tool_enrollment_state,
    "getOnboardingResponses": _tool_onboarding_responses,
    "getOnboardingChecklist": _tool_checklist,
    "getDocumentStatuses": _tool_documents,
    "getEnrollmentHolds": _tool_holds,
    "getStudentDeadlines": _tool_deadlines,
    "getSupportOptions": _tool_support,
    "getStudentSupportRequests": _tool_support_requests,
    "getFinancialAidStatus": _tool_aid_status,
    "getFinancialAidSummary": _tool_aid_summary,
    "getAidDisbursements": _tool_aid_disbursements,
    "getFinancialAidSupportOptions": _tool_aid_support,
    "getStudentHousingStatus": _tool_housing_status,
    "getStudentHousingEligibility": _tool_housing_eligibility,
    "getHousingOptions": _tool_housing_options,
    "getRegistrationStatus": _tool_registration,
    "getStudentAccountSummary": _tool_account,
    "getAcademicStanding": _tool_academic_standing,
    "getStudentAppointments": _tool_appointments,
    "getStudentAdvising": _tool_advising,
    "getAcademicPlan": _tool_academics,
    "getCampusLife": _tool_campus_life,
    "getStudentMessages": _tool_messages,
    "getInstitutionalPolicies": _tool_institutional_policies,
}


def _items(value: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [_mapping(item) for item in _sequence(value.get("items"))]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return ()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _clip(value: Any, limit: int) -> str | None:
    text = _text(value).strip()
    if not text:
        return None
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _full_name(first: Any, last: Any) -> str | None:
    parts = [_text(first).strip(), _text(last).strip()]
    joined = " ".join(part for part in parts if part)
    return joined or None


def _at_least(value: Any, minimum: Any) -> bool | None:
    if not isinstance(value, int | float) or not isinstance(minimum, int | float):
        return None
    return float(value) >= float(minimum)


def _find_requirement(
    requirements: Mapping[str, Any] | None, code: str
) -> Mapping[str, Any] | None:
    return next(
        (
            item
            for item in _items(_mapping(requirements))
            if str(item.get("code") or "").lower() == code
        ),
        None,
    )


def _appointment_label(value: Any) -> str:
    return _text(value).replace("_", " ").strip().capitalize() or "Appointment"


def _bounded_answers(value: Any) -> JsonDict:
    """A requirement response rendered small enough to sit in evidence."""

    answers: JsonDict = {}
    for key, raw in _mapping(value).items():
        if isinstance(raw, str):
            answers[key] = _clip(raw, 160)
        elif isinstance(raw, bool | int | float) or raw is None:
            answers[key] = raw
        elif isinstance(raw, Sequence):
            answers[key] = [_clip(item, 80) if isinstance(item, str) else item for item in raw[:8]]
        if len(answers) >= 12:
            break
    return answers


def _record_count(data: Any) -> int:
    if isinstance(data, Mapping):
        if isinstance(data.get("items"), Sequence):
            return len(data["items"])
        for key in (
            "awards",
            "derivedBlockers",
            "gates",
            "residences",
            "requiredDocuments",
            "plan",
            "upcomingEvents",
            "latest",
            "articles",
        ):
            if isinstance(data.get(key), Sequence):
                return len(data[key])
        return 1
    return 0


def _university_tool(
    domain: str, name: str
) -> Callable[[AssistantToolHost, datetime], Awaitable[JsonDict]]:
    async def read(host: AssistantToolHost, now: datetime) -> JsonDict:
        if not host.supports("university_record"):
            raise _UnsupportedRead("university_record")
        args = host.tool_arguments.get(name, {})
        return dict(
            await host.read(
                "university_record",
                domain=domain,
                known_at=args.get("knownAt"),
                effective_at=args.get("effectiveAt"),
                entity_type=args.get("entityType"),
                entity_id=args.get("entityId"),
            )
        )

    return read


_TOOL_IMPLEMENTATIONS = {
    **_TOOL_IMPLEMENTATIONS,
    **{name: _university_tool(domain, name) for name, (domain, _) in UNIVERSITY_TOOLS.items()},
}
STUDENT_TOOL_ARGUMENTS = {
    **STUDENT_TOOL_ARGUMENTS,
    "getUniversityHistory": {
        "entityType": "Optional exact event type: application, appointment, disbursement, "
        "document, enrollment, "
        "hold, housing, ledger, payment, sap_evaluation, transfer_credit, waitlist, workflow. "
        "External grade corrections use transfer_credit; institutional grades use enrollment. "
        "Omit the filter if uncertain; grade is not a valid type.",
        "entityId": "Optional exact entity id from a prior record",
        "knownAt": "ISO timestamp with timezone: latest recording time to include",
        "effectiveAt": "ISO timestamp with timezone: latest fact effectivity to include",
    },
}
