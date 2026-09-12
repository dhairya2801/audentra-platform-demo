"""Canonical projections over the student's portal-visible records.

Every reader of student state — the portal endpoints, Edward's tools, the
action-authority check that decides whether a "pay your deposit" widget may
render — must agree on what the record says. Before this module each reader
re-derived facts like "is the deposit paid" from whichever payload it happened
to be holding, and they disagreed: the Postgres dashboard projection never
carried a `depositPaid` field, so a reader that looked for one concluded
"unpaid" for every student who had in fact paid.

The functions here are pure. They take the canonical read payloads the portal
itself renders (dashboard projection, payments ledger, financial summary,
requirement list, onboarding record) and return one derivation. They never
guess: where the platform holds no record, the projection says so with an
explicit `unknown`/`not_tracked` marker rather than defaulting to a value that
reads as a fact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

JsonDict = dict[str, Any]

DEPOSIT_PAYMENT_TYPE = "enrollment_deposit"
_SETTLED_PAYMENT_STATUSES = frozenset({"succeeded"})
_PENDING_PAYMENT_STATUSES = frozenset({"pending", "processing", "submitted"})

REQUIREMENT_DONE_STATUSES = frozenset({"completed", "waived", "not_applicable"})
REQUIREMENT_IN_REVIEW_STATUSES = frozenset({"submitted", "under_review"})
REQUIREMENT_OPEN_STATUSES = frozenset(
    {"blocked", "ready", "in_progress", "submitted", "under_review", "rejected", "expired"}
)


def requirement_readiness(
    item: Mapping[str, Any], requirements: Sequence[Mapping[str, Any]]
) -> JsonDict:
    """Explain a recorded block without inventing a cross-office dependency."""
    if item.get("status") != "blocked":
        return {}
    statuses = {row.get("code"): row.get("status") for row in requirements}
    unmet = [
        str(code)
        for code in item.get("dependencyCodes", [])
        if statuses.get(code) not in REQUIREMENT_DONE_STATUSES
    ]
    return {
        "unmetDependencyCodes": unmet,
        "readinessNote": (
            "Blocked: resolve the recorded prerequisites before attempting this item."
            if unmet
            else "Blocked despite no unmet recorded prerequisites. The reason is not established; "
            "ask the responsible office to reconcile this status before attempting completion. "
            "Do not infer a dependency on another open case."
        ),
    }


# `financial_document_requirement.status` is constrained by the schema to
# not_started / submitted / under_review / verified / action_required. Only
# `verified` is satisfied. The previous set ({"received", "waived"}) matched no
# value the database can hold, so every aid document read as outstanding
# forever in production while the eval fixture — which used the non-canonical
# words — passed.
AID_DOCUMENT_SATISFIED_STATUSES = frozenset({"verified"})
AID_DOCUMENT_STATUSES = (
    "not_started",
    "submitted",
    "under_review",
    "verified",
    "action_required",
)
# Submitted work is not satisfied, but it is not the student's move either.
AID_DOCUMENT_IN_REVIEW_STATUSES = frozenset({"submitted", "under_review"})

# Checklist codes that name an enrollment gate. The gate code is the stable
# vocabulary shared by holds, registration, housing eligibility, and the
# planner's dependency table, so a blocker surfaced in one place can be
# verified from the read that owns it.
REQUIREMENT_GATE_CODES: Mapping[str, str] = {
    "advising": "advising_complete",
    "enrollment_deposit": "enrollment_deposit_posted",
    "final_transcript": "final_transcript",
    "housing_preference": "housing_preference_selected",
    "immunization": "immunization_cleared",
    "immunization_record": "immunization_cleared",
    "immunization_records": "immunization_cleared",
    "official_transcript": "final_transcript",
    "orientation": "orientation_complete",
    "transcript": "final_transcript",
}


@dataclass(frozen=True)
class DepositState:
    """Whether the enrollment deposit has actually been paid.

    `known` is false when neither the payments ledger nor the financial
    summary could be read this turn. A caller must not translate an unknown
    deposit into "unpaid": that is the exact substitution that made Edward
    invent a blocker for students who had already paid.
    """

    known: bool
    paid: bool
    pending: bool
    amount_cents: int
    offer_id: str | None
    due_at: str | None
    paid_at: str | None
    sources: tuple[str, ...]

    @property
    def outstanding(self) -> bool:
        """A deposit that is owed right now: known, unpaid, not in flight."""

        return self.known and not self.paid and not self.pending and self.amount_cents > 0

    def as_json(self) -> JsonDict:
        return {
            "known": self.known,
            "paid": self.paid,
            "pending": self.pending,
            "outstanding": self.outstanding,
            "amountCents": self.amount_cents,
            "offerId": self.offer_id,
            "dueAt": self.due_at,
            "paidAt": self.paid_at,
            "sources": list(self.sources),
        }


def derive_deposit_state(
    *,
    dashboard: Mapping[str, Any] | None = None,
    payments: Mapping[str, Any] | None = None,
    financials: Mapping[str, Any] | None = None,
) -> DepositState:
    """The single derivation of enrollment-deposit payment state.

    The payments ledger is authoritative: a `succeeded` enrollment-deposit
    transaction is the receipt the student sees on the Payments page. The
    financial summary's deposit schedule row is a second canonical witness
    computed by the same server from the same table, so it settles the answer
    when the ledger itself could not be read.
    """

    offer = _mapping(_mapping(dashboard).get("offer"))
    offer_id = _text(offer.get("id"))
    amount_cents = _integer(offer.get("depositAmountCents"))
    due_at = _text(offer.get("responseDeadline"))

    sources: list[str] = []
    paid = False
    pending = False
    paid_at: str | None = None

    if payments is not None:
        sources.append("payments")
        for raw in _sequence(_mapping(payments).get("items")):
            item = _mapping(raw)
            if _text(item.get("type")) != DEPOSIT_PAYMENT_TYPE:
                continue
            # An offer id on both sides must match; a ledger row that predates
            # the current offer is not this offer's deposit.
            item_offer = _text(item.get("offerId"))
            if offer_id and item_offer and item_offer != offer_id:
                continue
            status = _text(item.get("status"))
            if status in _SETTLED_PAYMENT_STATUSES:
                paid = True
                paid_at = _text(item.get("createdAt")) or paid_at
            elif status in _PENDING_PAYMENT_STATUSES:
                pending = True

    schedule_row: Mapping[str, Any] | None = None
    if financials is not None:
        sources.append("financials")
        schedule_row = next(
            (
                entry
                for raw in _sequence(_mapping(financials).get("paymentSchedule"))
                if (entry := _mapping(raw)) and _text(entry.get("kind")) == "deposit"
            ),
            None,
        )
        if schedule_row is not None:
            if not amount_cents:
                amount_cents = _integer(schedule_row.get("amountCents"))
            due_at = due_at or _text(schedule_row.get("dueAt"))
            # The ledger wins when it was read; the schedule only fills a gap.
            if payments is None and _text(schedule_row.get("status")) == "paid":
                paid = True

    # A settled payment outranks an in-flight one for the same deposit.
    if paid:
        pending = False

    return DepositState(
        known=bool(sources),
        paid=paid,
        pending=pending,
        amount_cents=amount_cents,
        offer_id=offer_id or None,
        due_at=due_at or None,
        paid_at=paid_at,
        sources=tuple(sources),
    )


def requirement_gate_code(code: Any) -> str:
    """The stable gate vocabulary for a checklist requirement code."""

    text = _text(code).lower()
    return REQUIREMENT_GATE_CODES.get(text, text or "requirement")


def is_requirement_open(item: Mapping[str, Any]) -> bool:
    return _text(item.get("status")) not in REQUIREMENT_DONE_STATUSES


def open_blocking_requirements(requirements: Mapping[str, Any] | None) -> list[JsonDict]:
    """Blocking checklist items the student has not finished."""

    return [
        dict(item)
        for raw in _sequence(_mapping(requirements).get("items"))
        if (item := _mapping(raw)) and item.get("blocking") and is_requirement_open(item)
    ]


def derive_enrollment_blockers(
    *,
    requirements: Mapping[str, Any] | None,
    deposit: DepositState,
) -> list[JsonDict]:
    """Everything on the record standing between the student and enrollment.

    One blocker per gate. The offer-level deposit and the deposit checklist
    step are the same gate and must not both appear, or Edward reports two
    obstacles where the portal shows one.
    """

    blockers: list[JsonDict] = []
    seen: set[str] = set()

    if deposit.outstanding:
        seen.add("enrollment_deposit_posted")
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
    elif deposit.pending:
        # Submitted-but-unposted is neither cleared nor a student to-do: the
        # student already acted and must not be told to pay again.
        seen.add("enrollment_deposit_posted")
        blockers.append(
            {
                "code": "enrollment_deposit_posted",
                "title": "Enrollment deposit payment is still processing",
                "owner": "university",
                "clearingAction": (
                    "The payment is submitted and waiting to post. No new payment is needed "
                    "unless it fails."
                ),
                "href": "/payments",
                "blocksRegistration": True,
            }
        )

    for item in open_blocking_requirements(requirements):
        gate_code = requirement_gate_code(item.get("code"))
        if gate_code in seen:
            continue
        seen.add(gate_code)
        in_review = _text(item.get("status")) in REQUIREMENT_IN_REVIEW_STATUSES
        title = _text(item.get("title")) or "Enrollment requirement"
        blockers.append(
            {
                "code": gate_code,
                "title": title,
                "owner": "university" if in_review else "student",
                "clearingAction": (
                    "Waiting on university review; no student action needed."
                    if in_review
                    else f"Complete “{title}” from your enrollment checklist."
                ),
                "href": requirement_href(item),
                "blocksRegistration": True,
            }
        )
    return blockers


def requirement_href(item: Mapping[str, Any]) -> str:
    slug = _text(item.get("slug")) or _text(item.get("code")).lower().replace("_", "-")
    return f"/enrollment/requirements/{slug}" if slug else "/enrollment"


def deadline_bucket(due_at: Any, now: datetime) -> str:
    """Overdue / this week / this month / later, from an ISO date or datetime."""

    due = parse_moment(due_at)
    if due is None:
        return "later"
    delta_days = (due - now).total_seconds() / 86_400
    if delta_days < 0:
        return "overdue"
    if delta_days <= 7:
        return "this_week"
    if delta_days <= 30:
        return "this_month"
    return "later"


def parse_moment(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return value
    return ()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
