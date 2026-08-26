"""What an enrollment leader needs to know before the day starts.

Morning Brew answers five questions, in this order:

1. Where are we now?              — the funnel, counted at one instant
2. What changed since yesterday?  — events with a real creation timestamp
3. What needs attention today?    — cohorts that are stuck, and why
4. Why does it need attention?    — the canonical evidence behind each number
5. What should we do next?        — the office, the surface, the cohort

This module owns the *vocabulary* of that briefing and nothing else. Every
population it names is a `CohortFilter` from `student_cohort.py`, so a headline
here and a roster answer from Staff Edward resolve through one definition. If
a number appears in the briefing, `findStudents` can list the students behind
it — that round trip is the design constraint, not a nice-to-have.

Two rules constrain what may live here:

- **No metric is invented.** A value that cannot be computed from canonical
  PostgreSQL rows is not shown at all. There is no target, no benchmark, no
  projection, and no probability — the preview workspace's melt/recovery
  percentages are explicitly non-canonical and never enter this file.
- **No delta is guessed.** A change is reported only where a row carries the
  timestamp that proves it. Where the database cannot reconstruct yesterday's
  value, the briefing says what it *can* count in the window instead of
  implying a trend.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from audentra.domain.student_cohort import (
    DUE_SOON_HORIZON_DAYS,
    CohortFilter,
    build_cohort_filter,
)

JsonDict = dict[str, Any]

BREW_WINDOW_HOURS = 24
"""The "since yesterday" window. A rolling 24 hours ending at read time, not a
calendar day: the platform stores no end-of-day snapshot, so a calendar-day
comparison would need a denominator nothing in PostgreSQL retains."""

BrewTopic = Literal["admissions", "financial_aid", "housing", "registrar", "student_success"]
BrewSeverity = Literal["high", "medium", "positive"]
BrewTone = Literal["positive", "watch", "neutral"]
BrewDestination = Literal[
    "overview",
    "outreach",
    "tasks",
    "students",
    "messages",
    "campus_life",
    "academics",
    "journeys",
    "knowledge",
    "edward",
]


# ---------------------------------------------------------------------------
# Named cohorts — the shared selections the whole briefing is built from
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrewCohort:
    """One named population, defined once and counted once.

    `question` is the phrasing that reproduces the number through Staff Edward.
    It ships in the payload so a staff user who distrusts a headline has an
    exact, checkable follow-up rather than a vague "go look in the roster".
    """

    key: str
    label: str
    filter_arguments: Mapping[str, Any]
    question: str

    def to_filter(self) -> CohortFilter:
        return build_cohort_filter(self.filter_arguments)

    def as_json(self) -> JsonDict:
        return {
            "key": self.key,
            "label": self.label,
            "filter": dict(self.filter_arguments),
            "clauses": self.to_filter().describe(),
            "question": self.question,
        }


def _cohort(key: str, label: str, question: str, **arguments: Any) -> BrewCohort:
    return BrewCohort(key=key, label=label, filter_arguments=arguments, question=question)


BREW_COHORTS: tuple[BrewCohort, ...] = (
    _cohort("roster", "students on the roster", "How many students are in the roster?"),
    _cohort(
        "offer_outstanding",
        "students still deciding on an offer",
        "Which students have an outstanding admission offer?",
        offerStatus="offered",
    ),
    _cohort(
        "offer_accepted",
        "students who accepted an offer",
        "Which students have accepted their admission offer?",
        offerStatus="accepted",
    ),
    _cohort(
        "offer_declined",
        "students who declined an offer",
        "Which students declined their admission offer?",
        offerStatus="declined",
    ),
    _cohort(
        "deposit_paid",
        "students with a posted deposit",
        "Which students have paid their enrollment deposit?",
        depositState="paid",
    ),
    _cohort(
        "deposit_outstanding",
        "accepted students who still owe a deposit",
        "Which accepted students have not paid their enrollment deposit?",
        offerStatus="accepted",
        depositState="unpaid",
    ),
    _cohort(
        "onboarding_complete",
        "students who finished onboarding",
        "Which students have completed onboarding?",
        onboardingStatus="completed",
    ),
    _cohort(
        "onboarding_in_progress",
        "students partway through onboarding",
        "Which students are partway through onboarding?",
        onboardingStatus="in_progress",
    ),
    _cohort(
        "onboarding_not_started",
        "students who have not started onboarding",
        "Which students have not started onboarding?",
        onboardingStatus="not_started",
    ),
    _cohort(
        "enrollment_ready",
        "deposited students with nothing blocking them",
        "Which deposited students have no open blocking requirement?",
        depositState="paid",
        hasOpenBlockingRequirement=False,
    ),
    _cohort(
        "blocked",
        "students with an open blocking requirement",
        "Which students have an open blocking requirement?",
        hasOpenBlockingRequirement=True,
    ),
    _cohort(
        "deposited_blocked",
        "deposited students with an open blocking requirement",
        "Which deposited students have an open blocking requirement?",
        depositState="paid",
        hasOpenBlockingRequirement=True,
    ),
    _cohort(
        "overdue",
        "students with an overdue requirement",
        "Which students have an overdue requirement?",
        hasOverdueRequirement=True,
    ),
    _cohort(
        "deposited_overdue",
        "deposited students with an overdue requirement",
        "Which deposited students have an overdue requirement?",
        depositState="paid",
        hasOverdueRequirement=True,
    ),
    _cohort(
        "due_soon",
        f"students with a requirement due inside {DUE_SOON_HORIZON_DAYS} days",
        f"Which students have a requirement due in the next {DUE_SOON_HORIZON_DAYS} days?",
        requirementState="due_soon",
    ),
    _cohort(
        "aid_outstanding",
        "students with unverified financial-aid documents",
        "Which students have financial-aid documents that are not verified?",
        aidDocumentState="outstanding",
    ),
    _cohort(
        "aid_action_required",
        "students whose aid file needs their action",
        "Which students have a financial-aid document that requires action?",
        aidDocumentState="action_required",
    ),
    _cohort(
        "aid_in_review",
        "students whose aid documents are with the university",
        "Which students have financial-aid documents under review?",
        aidDocumentState="in_review",
    ),
    _cohort(
        "transcript_missing",
        "students with no accepted transcript on file",
        "Which students are missing a transcript document?",
        documentCategory="transcript",
        documentState="missing",
    ),
    _cohort(
        "documents_in_review",
        "students with a document waiting on a decision",
        "Which students have a document under review?",
        documentState="under_review",
    ),
    _cohort(
        "housing_blocked",
        "students whose housing step is blocked",
        "Which students have a blocked housing step?",
        housingState="blocked",
    ),
    _cohort(
        "housing_actionable",
        "students who still have to choose housing",
        "Which students still have an open housing step?",
        housingState="actionable",
    ),
    _cohort(
        "housing_selected",
        "students who completed housing selection",
        "Which students have completed the housing step?",
        housingState="selected",
    ),
    _cohort(
        "open_work_item",
        "students with an open Action Center item",
        "Which students have an open Action Center item?",
        hasOpenWorkItem=True,
    ),
)

BREW_COHORTS_BY_KEY: Mapping[str, BrewCohort] = {cohort.key: cohort for cohort in BREW_COHORTS}


# ---------------------------------------------------------------------------
# Funnel metrics — "where are we now"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrewMetricSpec:
    """One tile in the Enrollment Pulse.

    `basis_key` names the denominator the value is a share of. A count with no
    denominator is a number without a scale — "8 deposits" reads completely
    differently against 11 accepted offers than against 3,000.
    """

    id: str
    topic: BrewTopic
    label: str
    icon: str
    cohort_key: str
    definition: str
    window_label: str
    basis_key: str | None = None
    basis_label: str | None = None
    delta_key: str | None = None
    delta_label: str | None = None
    favorable_direction: Literal["up", "down", "none"] = "none"
    segment_keys: tuple[str, ...] = ()


BREW_METRICS: tuple[BrewMetricSpec, ...] = (
    BrewMetricSpec(
        id="roster",
        topic="admissions",
        label="Students",
        icon="◫",
        cohort_key="roster",
        window_label="On the roster",
        definition=(
            "Every student record in this tenant, regardless of admission or enrollment stage."
        ),
        segment_keys=("offer_outstanding", "offer_accepted", "offer_declined"),
    ),
    BrewMetricSpec(
        id="accepted",
        topic="admissions",
        label="Accepted",
        icon="✓",
        cohort_key="offer_accepted",
        window_label="Accepted an offer",
        definition=(
            "Students whose most recent admission offer is accepted. An older "
            "superseded offer never counts."
        ),
        basis_key="roster",
        basis_label="of the roster",
        delta_key="offers_accepted",
        delta_label="accepted",
        favorable_direction="up",
    ),
    BrewMetricSpec(
        id="deposits",
        topic="admissions",
        label="Deposit paid",
        icon="$",
        cohort_key="deposit_paid",
        window_label="Deposit posted",
        definition=(
            "Students with a succeeded enrollment-deposit payment on the canonical ledger."
        ),
        basis_key="offer_accepted",
        basis_label="of accepted students",
        delta_key="deposits_posted",
        delta_label="posted",
        favorable_direction="up",
    ),
    BrewMetricSpec(
        id="deposit_outstanding",
        topic="admissions",
        label="Deposit outstanding",
        icon="◷",
        cohort_key="deposit_outstanding",
        window_label="Accepted, deposit unpaid",
        definition=(
            "Students who accepted an offer but have no succeeded deposit "
            "payment. A submitted-but-unposted payment is a separate state and "
            "is not counted here."
        ),
        basis_key="offer_accepted",
        basis_label="of accepted students",
        favorable_direction="down",
    ),
    BrewMetricSpec(
        id="enrollment_ready",
        topic="admissions",
        label="Enrollment ready",
        icon="◈",
        cohort_key="enrollment_ready",
        window_label="Deposited and unblocked",
        definition=(
            "Deposited students with no open blocking requirement left on their enrollment journey."
        ),
        basis_key="deposit_paid",
        basis_label="of deposited students",
        favorable_direction="up",
    ),
    BrewMetricSpec(
        id="blocked",
        topic="student_success",
        label="Blocked students",
        icon="⚑",
        cohort_key="blocked",
        window_label="Blocked by a requirement",
        definition=("Students with at least one open requirement their journey marks as blocking."),
        basis_key="roster",
        basis_label="of the roster",
        favorable_direction="down",
    ),
    BrewMetricSpec(
        id="overdue",
        topic="student_success",
        label="Overdue requirements",
        icon="!",
        cohort_key="overdue",
        window_label="Past a due date",
        definition=(
            "Students with at least one incomplete requirement whose due date has already passed."
        ),
        basis_key="roster",
        basis_label="of the roster",
        favorable_direction="down",
    ),
    BrewMetricSpec(
        id="onboarding",
        topic="student_success",
        label="Onboarding complete",
        icon="◉",
        cohort_key="onboarding_complete",
        window_label="Onboarding complete",
        definition="Students who finished the first-time onboarding gate.",
        basis_key="roster",
        basis_label="of the roster",
        favorable_direction="up",
        segment_keys=("onboarding_complete", "onboarding_in_progress", "onboarding_not_started"),
    ),
    BrewMetricSpec(
        id="aid_outstanding",
        topic="financial_aid",
        label="Aid files open",
        icon="◆",
        cohort_key="aid_outstanding",
        window_label="Aid file not yet verified",
        definition=(
            "Students with at least one financial-aid document requirement that "
            "is not yet verified."
        ),
        basis_key="roster",
        basis_label="of the roster",
        favorable_direction="down",
        segment_keys=("aid_action_required", "aid_in_review"),
    ),
    BrewMetricSpec(
        id="documents_in_review",
        topic="registrar",
        label="Documents in review",
        icon="▤",
        cohort_key="documents_in_review",
        window_label="Document awaiting a decision",
        definition="Students with an uploaded document waiting on a staff decision.",
        basis_key="roster",
        basis_label="of the roster",
        delta_key="documents_submitted",
        delta_label="submitted",
        favorable_direction="none",
    ),
    BrewMetricSpec(
        id="housing_selected",
        topic="housing",
        label="Housing selected",
        icon="⌂",
        cohort_key="housing_selected",
        window_label="Housing step complete",
        definition="Students whose housing-preference step is complete.",
        basis_key="deposit_paid",
        basis_label="of deposited students",
        favorable_direction="up",
        segment_keys=("housing_selected", "housing_actionable", "housing_blocked"),
    ),
)


# ---------------------------------------------------------------------------
# Deltas — "what changed", and only where a timestamp proves it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrewDeltaSpec:
    """One counted event class inside the window.

    `basis` names the exact column the count comes from. It ships in the
    payload because "4 requirements completed" and "4 requirement records whose
    last write landed in the window" are different claims, and the reader is
    entitled to know which one they are looking at.
    """

    key: str
    topic: BrewTopic
    title: str
    singular: str
    plural: str
    tone: BrewTone
    destination: BrewDestination
    basis: str
    basis_note: str
    exact: bool = True


BREW_DELTAS: tuple[BrewDeltaSpec, ...] = (
    BrewDeltaSpec(
        key="deposits_posted",
        topic="admissions",
        title="Enrollment deposits posted",
        singular="deposit posted",
        plural="deposits posted",
        tone="positive",
        destination="students",
        basis="payment_transaction.created_at",
        basis_note="A succeeded enrollment-deposit payment is written once, when it posts.",
    ),
    BrewDeltaSpec(
        key="offers_accepted",
        topic="admissions",
        title="Admission offers accepted",
        singular="offer accepted",
        plural="offers accepted",
        tone="positive",
        destination="students",
        basis="admission_offer.accepted_at",
        basis_note="The acceptance timestamp is set once and never rewritten.",
    ),
    BrewDeltaSpec(
        key="documents_submitted",
        topic="registrar",
        title="Documents submitted",
        singular="document submitted",
        plural="documents submitted",
        tone="neutral",
        destination="tasks",
        basis="document_record.created_at",
        basis_note="Counts the upload, not the review decision.",
    ),
    BrewDeltaSpec(
        key="requirements_completed",
        topic="student_success",
        title="Requirements reached a completed state",
        singular="requirement completed",
        plural="requirements completed",
        tone="positive",
        destination="journeys",
        basis="student_requirement.updated_at",
        basis_note=(
            "Requirement status transitions are not individually versioned, so "
            "this counts requirements that are complete now and whose record "
            "last changed inside the window."
        ),
        exact=False,
    ),
    BrewDeltaSpec(
        key="requests_opened",
        topic="student_success",
        title="Student support requests opened",
        singular="request opened",
        plural="requests opened",
        tone="watch",
        destination="messages",
        basis="student_inquiry.created_at",
        basis_note="One row per support conversation, written when the student opens it.",
    ),
    BrewDeltaSpec(
        key="requests_replied",
        topic="student_success",
        title="Support conversations with a new message",
        singular="conversation had a new message",
        plural="conversations had new messages",
        tone="neutral",
        destination="messages",
        basis="student_inquiry.last_message_at",
        basis_note="Set by the conversation runtime on every participant message.",
    ),
    BrewDeltaSpec(
        key="work_items_opened",
        topic="student_success",
        title="Action Center items opened",
        singular="staff action opened",
        plural="staff actions opened",
        tone="watch",
        destination="tasks",
        basis="staff_work_item.created_at",
        basis_note="Includes both staff-created and rule-created work.",
    ),
    BrewDeltaSpec(
        key="work_items_closed",
        topic="student_success",
        title="Action Center items closed",
        singular="staff action closed",
        plural="staff actions closed",
        tone="positive",
        destination="tasks",
        basis="staff_work_item.updated_at",
        basis_note=(
            "Counts items that are done or cancelled now and whose record last "
            "changed inside the window."
        ),
        exact=False,
    ),
    BrewDeltaSpec(
        key="work_items_escalated",
        topic="student_success",
        title="Action Center items escalated",
        singular="item escalated",
        plural="items escalated",
        tone="watch",
        destination="tasks",
        basis="staff_work_log.occurred_at",
        basis_note=(
            "Counts escalation log entries in the window: the SLA sweep or a "
            "staff member marked the item escalated at that moment."
        ),
    ),
    BrewDeltaSpec(
        key="attention_flags",
        topic="student_success",
        title="Students flagged by the engagement scan",
        singular="student flagged",
        plural="students flagged",
        tone="watch",
        destination="students",
        basis="intervention_candidate.created_at",
        basis_note=(
            "The scheduled engagement scan writes one deterministic, "
            "reason-coded candidate per student per day. No score is involved."
        ),
    ),
)

BREW_DELTAS_BY_KEY: Mapping[str, BrewDeltaSpec] = {spec.key: spec for spec in BREW_DELTAS}


# ---------------------------------------------------------------------------
# Priorities — "what needs attention, why, and what to do"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrewPrioritySpec:
    """A deterministic attention rule.

    Every rule is a cohort plus a reason plus an owner plus a surface. There is
    no scoring model: rank comes from `weight`, and an empty cohort simply does
    not appear. That is the whole ordering, and it is stated in the payload so
    a reader can predict tomorrow's briefing from today's.
    """

    id: str
    topic: BrewTopic
    title: str
    cohort_key: str
    reason: str
    action: str
    owner: str
    destination: BrewDestination
    severity: BrewSeverity
    level: Literal["High", "Medium", "Low"]
    weight: int
    window: str
    steps: tuple[str, ...]
    breakdown_keys: tuple[str, ...] = ()
    group_by: str | None = None


BREW_PRIORITIES: tuple[BrewPrioritySpec, ...] = (
    BrewPrioritySpec(
        id="deposited-overdue",
        topic="student_success",
        title="Deposited students with overdue requirements",
        cohort_key="deposited_overdue",
        reason=(
            "These students have already committed money. An overdue "
            "requirement after a deposit is friction the university introduced, "
            "not hesitation from the student."
        ),
        action="Clear the overdue requirements behind the committed class first.",
        owner="Enrollment Operations",
        destination="students",
        severity="high",
        level="High",
        weight=100,
        window="Today",
        steps=(
            "Open the cohort and sort by the oldest due date.",
            "Confirm each requirement is genuinely outstanding rather than "
            "waiting on a university review.",
            "Assign an Action Center item for anything the student cannot clear on their own.",
        ),
        breakdown_keys=("deposited_blocked", "deposit_paid"),
        group_by="blocking_requirement",
    ),
    BrewPrioritySpec(
        id="overdue-requirements",
        topic="student_success",
        title="Overdue enrollment requirements",
        cohort_key="overdue",
        reason=(
            "A requirement past its due date will not resolve itself, and every "
            "day it stays open moves the student further from the term."
        ),
        action="Work the overdue queue by requirement type, not by student.",
        owner="Enrollment Operations",
        destination="journeys",
        severity="high",
        level="High",
        weight=90,
        window="Today",
        steps=(
            "Group the overdue set by requirement to find the dominant blocker.",
            "Batch the requirements a single office can clear in one pass.",
        ),
        breakdown_keys=("deposited_overdue",),
        group_by="blocking_requirement",
    ),
    BrewPrioritySpec(
        id="deadline-runway",
        topic="student_success",
        title=f"Requirements due inside {DUE_SOON_HORIZON_DAYS} days",
        cohort_key="due_soon",
        reason=(
            "This is the group that becomes tomorrow's overdue list. Acting "
            "before the date is cheaper than recovering after it."
        ),
        action="Send the deadline reminder while the requirement is still on time.",
        owner="Enrollment Operations",
        destination="students",
        severity="medium",
        level="Medium",
        weight=70,
        window=f"Next {DUE_SOON_HORIZON_DAYS} days",
        steps=(
            "Review the requirements falling due this week.",
            "Prioritise students who also have an unpaid deposit.",
        ),
    ),
    BrewPrioritySpec(
        id="deposit-outstanding",
        topic="admissions",
        title="Accepted students who have not deposited",
        cohort_key="deposit_outstanding",
        reason=(
            "These students said yes and have not paid. Intent is already "
            "visible, so the remaining gap is operational."
        ),
        action="Confirm nothing on the student's side is blocking the payment.",
        owner="Student Accounts",
        destination="outreach",
        severity="high",
        level="High",
        weight=85,
        window="This week",
        steps=(
            "Check each student's response deadline before contacting them.",
            "Resolve any open blocking requirement that would make paying pointless.",
        ),
        breakdown_keys=("offer_accepted", "deposit_paid"),
    ),
    BrewPrioritySpec(
        id="aid-action-required",
        topic="financial_aid",
        title="Financial-aid files waiting on the student",
        cohort_key="aid_action_required",
        reason=(
            "An aid document marked action-required is stalled on the student, "
            "and an unfinished aid file delays everything downstream of it."
        ),
        action="Tell each student exactly which document is missing.",
        owner="Financial Aid",
        destination="students",
        severity="medium",
        level="Medium",
        weight=75,
        window="Today",
        steps=(
            "List the aid requirements marked action-required.",
            "Send one message per student naming the specific document.",
        ),
        breakdown_keys=("aid_in_review", "aid_outstanding"),
    ),
    BrewPrioritySpec(
        id="documents-in-review",
        topic="registrar",
        title="Documents waiting on a staff decision",
        cohort_key="documents_in_review",
        reason=(
            "This queue is entirely on the university. Students who uploaded on "
            "time are waiting on us."
        ),
        action="Clear the review queue before it becomes a student-facing delay.",
        owner="Registrar",
        destination="tasks",
        severity="medium",
        level="Medium",
        weight=65,
        window="Today",
        steps=(
            "Open the document review queue in the Action Center.",
            "Decide the oldest submissions first.",
        ),
    ),
    BrewPrioritySpec(
        id="housing-blocked",
        topic="housing",
        title="Students blocked on housing",
        cohort_key="housing_blocked",
        reason=(
            "A blocked housing step means a prerequisite has not cleared, so "
            "the student cannot act even if they want to."
        ),
        action="Clear the prerequisite behind each blocked housing step.",
        owner="Housing",
        destination="campus_life",
        severity="medium",
        level="Medium",
        weight=55,
        window="This week",
        steps=(
            "Identify the prerequisite holding each housing step closed.",
            "Route the prerequisite to the office that owns it.",
        ),
        breakdown_keys=("housing_actionable", "housing_selected"),
    ),
    BrewPrioritySpec(
        id="transcript-missing",
        topic="registrar",
        title="Students with no transcript on file",
        cohort_key="transcript_missing",
        reason=(
            "No transcript record exists for these students, and a rejected "
            "upload leaves the requirement just as unmet as no upload at all."
        ),
        action="Request the transcript and confirm the rejected uploads were explained.",
        owner="Registrar",
        destination="students",
        severity="medium",
        level="Medium",
        weight=45,
        window="This week",
        steps=(
            "Separate students who never uploaded from those whose upload was rejected.",
            "Send the two groups different messages.",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Pure helpers used by the composition layer
# ---------------------------------------------------------------------------


@dataclass
class BrewCounts:
    """Cohort sizes read at a single instant."""

    values: dict[str, int] = field(default_factory=dict)

    def get(self, key: str) -> int:
        return int(self.values.get(key, 0))

    def share(self, key: str, basis_key: str | None) -> float | None:
        if basis_key is None:
            return None
        basis = self.get(basis_key)
        if basis <= 0:
            return None
        return round(self.get(key) * 100 / basis, 1)


def plural(count: int, singular: str, plural_form: str) -> str:
    return f"{count} {singular if count == 1 else plural_form}"


def severity_for(count: int, severity: BrewSeverity) -> BrewSeverity:
    """A rule with nobody in it is not a problem, whatever its default is."""

    return "positive" if count == 0 else severity


def window_label(hours: int = BREW_WINDOW_HOURS) -> str:
    if hours == 24:
        return "the last 24 hours"
    return f"the last {hours} hours"
