"""Pure policy functions shared by scheduled and inbound agent workflows.

The model may suggest an intent or priority, but these functions constrain the
result before any application command can create a task or intervention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

InboundAction = Literal[
    "append_to_existing_task",
    "create_staff_task",
    "create_inquiry",
    "human_triage",
    "record_only",
]
Priority = Literal["urgent", "high", "medium", "low"]


@dataclass(frozen=True, slots=True)
class InboundTriageCandidate:
    """Bounded facts supplied to the policy after identity resolution."""

    student_resolved: bool
    actionable: bool
    existing_work_item_id: str | None = None
    existing_inquiry_id: str | None = None
    model_priority: Priority = "medium"
    priority_evidence: tuple[str, ...] = ()
    due_at: datetime | None = None
    blocking: bool = False
    explicit_help_request: bool = False
    requires_human_review: bool = True


@dataclass(frozen=True, slots=True)
class InboundTriageDecision:
    action: InboundAction
    priority: Priority
    reason_code: str
    requires_human_review: bool


def constrain_priority(candidate: InboundTriageCandidate, *, now: datetime) -> Priority:
    """Apply deterministic deadline and evidence floors to model priority."""

    current = _utc(now)
    if candidate.due_at is not None:
        hours = (_utc(candidate.due_at) - current).total_seconds() / 3600
        if hours <= 48:
            return "urgent"
        if hours <= 24 * 7:
            return "high"

    if candidate.blocking and candidate.explicit_help_request:
        return "high"

    # A model cannot elevate a task without evidence that can be shown to a
    # staff member. This keeps emotional wording alone from creating urgency.
    if candidate.model_priority in {"urgent", "high"}:
        return candidate.model_priority if candidate.priority_evidence else "medium"

    return candidate.model_priority


def decide_inbound_action(
    candidate: InboundTriageCandidate,
    *,
    now: datetime,
) -> InboundTriageDecision:
    """Choose a reversible operational action; never mutate official state."""

    priority = constrain_priority(candidate, now=now)
    if not candidate.student_resolved:
        return InboundTriageDecision(
            action="human_triage",
            priority=priority,
            reason_code="student_identity_ambiguous",
            requires_human_review=True,
        )

    if not candidate.actionable:
        return InboundTriageDecision(
            action="record_only",
            priority=priority,
            reason_code="no_actionable_request",
            requires_human_review=False,
        )

    if candidate.existing_work_item_id:
        return InboundTriageDecision(
            action="append_to_existing_task",
            priority=priority,
            reason_code="matching_staff_task_found",
            requires_human_review=candidate.requires_human_review,
        )

    if candidate.existing_inquiry_id:
        return InboundTriageDecision(
            action="create_inquiry",
            priority=priority,
            reason_code="matching_student_inquiry_found",
            requires_human_review=candidate.requires_human_review,
        )

    return InboundTriageDecision(
        action="create_staff_task",
        priority=priority,
        reason_code="new_actionable_communication",
        requires_human_review=candidate.requires_human_review,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
