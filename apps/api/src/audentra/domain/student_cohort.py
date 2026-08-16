"""The canonical vocabulary for asking about groups of students.

Staff work is cohort work: "which admitted students haven't paid their
deposit", "how many are missing a final transcript", "what is blocking the
incoming class". Those are the same facts the individual-student reads already
expose — the difference is scope, not source.

This module owns the *vocabulary*: which dimensions a cohort can be filtered
and grouped by, and what each one means against canonical state. It holds no
SQL and no I/O, so the repository that executes a cohort query and the tool
catalog that describes it to a planner cannot disagree about what
`deposit_state=unpaid` selects.

Every filter resolves against a record the staff portal already renders. There
is deliberately no filter for melt likelihood, recovery likelihood, or any
other synthetic risk index: those live only in the preview workspace and are
not canonical state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

JsonDict = dict[str, Any]

MAX_COHORT_PAGE_SIZE = 50
DEFAULT_COHORT_PAGE_SIZE = 20

# --- filter value vocabularies ---------------------------------------------

OFFER_STATUSES: tuple[str, ...] = ("offered", "accepted", "declined", "expired")

DEPOSIT_STATES: tuple[str, ...] = ("paid", "pending", "unpaid")
"""Mirrors `DepositState`: a submitted-but-unposted payment is its own state,
not a synonym for unpaid. A cohort of "students who still owe a deposit" must
not sweep in students whose payment is mid-flight."""

ONBOARDING_STATUSES: tuple[str, ...] = ("not_started", "in_progress", "completed")

REQUIREMENT_STATES: tuple[str, ...] = (
    "open",
    "blocked",
    "in_review",
    "complete",
    "overdue",
    "any",
)
"""Coarse buckets over the requirement lifecycle. `open` is anything the
student still has to act on; `in_review` is submitted and waiting on the
university; `complete` covers completed/waived/not_applicable."""

DOCUMENT_STATES: tuple[str, ...] = ("missing", "submitted", "under_review", "accepted", "rejected")

AID_DOCUMENT_STATES: tuple[str, ...] = (
    "outstanding",
    "verified",
    "action_required",
    "in_review",
)
"""`outstanding` is any aid document not yet verified. The remaining values are
the canonical `financial_document_requirement.status` buckets; there is no
"received" or "waived" status in this schema."""

HOUSING_STATES: tuple[str, ...] = ("blocked", "actionable", "selected", "no_step")

COHORT_GROUP_BY: tuple[str, ...] = (
    "offer_status",
    "deposit_state",
    "onboarding_status",
    "program",
    "class_year",
    "assigned_staff",
    "blocking_requirement",
    "housing_state",
)
"""Dimensions an aggregation may group by. `blocking_requirement` groups by the
requirement code that is open and blocking, which is what "the most common
blockers" actually means against canonical state."""

COHORT_FILTER_FIELDS = frozenset(
    {
        "query",
        "program",
        "classYear",
        "offerStatus",
        "depositState",
        "onboardingStatus",
        "requirementCode",
        "requirementState",
        "documentCategory",
        "documentState",
        "aidDocumentState",
        "housingState",
        "assignedStaffId",
        "hasOpenWorkItem",
        "hasOverdueRequirement",
        "residencyStatus",
        "citizenshipStatus",
    }
)


@dataclass(frozen=True)
class CohortFilter:
    """A validated cohort selection.

    Fields left as `None` do not constrain the result. Every populated field
    narrows it, conjunctively — a staff user asking for "international students
    with incomplete aid verification" means both conditions, not either.
    """

    query: str | None = None
    program: str | None = None
    class_year: int | None = None
    offer_status: str | None = None
    deposit_state: str | None = None
    onboarding_status: str | None = None
    requirement_code: str | None = None
    requirement_state: str | None = None
    document_category: str | None = None
    document_state: str | None = None
    aid_document_state: str | None = None
    housing_state: str | None = None
    assigned_staff_id: str | None = None
    has_open_work_item: bool | None = None
    has_overdue_requirement: bool | None = None
    residency_status: str | None = None
    citizenship_status: str | None = None

    def describe(self) -> list[str]:
        """Human-readable clauses, so an answer can restate what it counted.

        A cohort number without its definition is unusable to a staff user:
        "42 students" means nothing until it says 42 *of which* students.
        """

        clauses: list[str] = []
        for label, value in (
            ("name or email matching", self.query),
            ("program", self.program),
            ("class year", self.class_year),
            ("admission offer status", self.offer_status),
            ("deposit state", self.deposit_state),
            ("onboarding status", self.onboarding_status),
            ("residency status", self.residency_status),
            ("citizenship status", self.citizenship_status),
            ("housing state", self.housing_state),
            ("assigned staff", self.assigned_staff_id),
        ):
            if value is not None and value != "":
                clauses.append(f"{label} = {value}")
        if self.requirement_code or self.requirement_state:
            code = self.requirement_code or "any requirement"
            clauses.append(f"requirement {code} is {self.requirement_state or 'open'}")
        if self.document_category or self.document_state:
            category = self.document_category or "any document"
            clauses.append(f"{category} document is {self.document_state or 'missing'}")
        if self.aid_document_state:
            clauses.append(f"financial-aid documents are {self.aid_document_state}")
        if self.has_open_work_item is not None:
            clauses.append(
                "has an open Action Center item"
                if self.has_open_work_item
                else "has no open Action Center item"
            )
        if self.has_overdue_requirement is not None:
            clauses.append(
                "has an overdue requirement"
                if self.has_overdue_requirement
                else "has no overdue requirement"
            )
        return clauses

    def is_unconstrained(self) -> bool:
        return not self.describe()


class CohortFilterError(ValueError):
    """A cohort filter value was outside the supported vocabulary."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _enum(name: str, value: Any, allowed: Sequence[str]) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text not in allowed:
        raise CohortFilterError(
            "unsupported_filter_value",
            f"{name} must be one of {', '.join(allowed)}; got {text!r}",
        )
    return text


def _text(value: Any, *, max_length: int = 120) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise CohortFilterError("filter_too_long", f"filter value exceeds {max_length} characters")
    return text


def _boolean(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise CohortFilterError("unsupported_filter_value", "expected a boolean filter value")


def _uuid_text(name: str, value: Any) -> str | None:
    normalized = _text(value, max_length=64)
    if normalized is None:
        return None
    try:
        return str(UUID(normalized))
    except ValueError as error:
        raise CohortFilterError("unsupported_filter_value", f"{name} must be a UUID") from error


def build_cohort_filter(raw: Mapping[str, Any]) -> CohortFilter:
    """Validate untrusted filter arguments into a `CohortFilter`.

    Raises rather than silently dropping an unsupported value: a filter the
    caller believes is applied but which was ignored produces a confidently
    wrong cohort, which is worse than an error.
    """

    unknown = sorted(str(key) for key in raw if key not in COHORT_FILTER_FIELDS)
    if unknown:
        raise CohortFilterError(
            "unsupported_filter",
            f"unsupported cohort filter field(s): {', '.join(unknown)}",
        )

    class_year = raw.get("classYear")
    if class_year is not None:
        try:
            class_year = int(class_year)
        except (TypeError, ValueError) as error:
            raise CohortFilterError("unsupported_filter_value", "classYear must be a year") from (
                error
            )
        if not 1900 <= class_year <= 2200:
            raise CohortFilterError("unsupported_filter_value", "classYear is out of range")

    return CohortFilter(
        query=_text(raw.get("query")),
        program=_text(raw.get("program")),
        class_year=class_year,
        offer_status=_enum("offerStatus", raw.get("offerStatus"), OFFER_STATUSES),
        deposit_state=_enum("depositState", raw.get("depositState"), DEPOSIT_STATES),
        onboarding_status=_enum(
            "onboardingStatus", raw.get("onboardingStatus"), ONBOARDING_STATUSES
        ),
        requirement_code=_text(raw.get("requirementCode"), max_length=64),
        requirement_state=_enum(
            "requirementState", raw.get("requirementState"), REQUIREMENT_STATES
        ),
        document_category=_text(raw.get("documentCategory"), max_length=64),
        document_state=_enum("documentState", raw.get("documentState"), DOCUMENT_STATES),
        aid_document_state=_enum(
            "aidDocumentState", raw.get("aidDocumentState"), AID_DOCUMENT_STATES
        ),
        housing_state=_enum("housingState", raw.get("housingState"), HOUSING_STATES),
        assigned_staff_id=_uuid_text("assignedStaffId", raw.get("assignedStaffId")),
        has_open_work_item=_boolean(raw.get("hasOpenWorkItem")),
        has_overdue_requirement=_boolean(raw.get("hasOverdueRequirement")),
        residency_status=_text(raw.get("residencyStatus"), max_length=48),
        citizenship_status=_text(raw.get("citizenshipStatus"), max_length=48),
    )


def validate_group_by(value: Any) -> str:
    dimension = str(value or "").strip().lower()
    if dimension not in COHORT_GROUP_BY:
        raise CohortFilterError(
            "unsupported_group_by",
            f"groupBy must be one of {', '.join(COHORT_GROUP_BY)}; got {dimension!r}",
        )
    return dimension


def bounded_page_size(value: Any) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return DEFAULT_COHORT_PAGE_SIZE
    return max(1, min(size, MAX_COHORT_PAGE_SIZE))


@dataclass
class CohortResult:
    """A cohort read: the matching students, the true total, and the filter.

    `total` is the count of every student matching the filter, not the length
    of `items`. Conflating the two turns "show me students missing transcripts"
    into a silent top-20 that a staff user reads as the whole population.
    """

    items: list[JsonDict] = field(default_factory=list)
    total: int = 0
    filter_clauses: list[str] = field(default_factory=list)
    truncated: bool = False

    def as_json(self) -> JsonDict:
        return {
            "items": self.items,
            "returned": len(self.items),
            "total": self.total,
            "truncated": self.truncated,
            "filter": self.filter_clauses,
        }
