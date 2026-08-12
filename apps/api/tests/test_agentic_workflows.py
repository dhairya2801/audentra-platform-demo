from datetime import UTC, datetime, timedelta

from audentra.domain.agentic_workflows import (
    InboundTriageCandidate,
    constrain_priority,
    decide_inbound_action,
)

NOW = datetime(2026, 8, 7, 12, tzinfo=UTC)


def test_ambiguous_identity_always_routes_to_human_triage() -> None:
    decision = decide_inbound_action(
        InboundTriageCandidate(
            student_resolved=False,
            actionable=True,
            model_priority="urgent",
            priority_evidence=("sender requested help",),
        ),
        now=NOW,
    )

    assert decision.action == "human_triage"
    assert decision.reason_code == "student_identity_ambiguous"
    assert decision.requires_human_review is True


def test_deadline_within_48_hours_is_urgent_even_if_model_says_medium() -> None:
    priority = constrain_priority(
        InboundTriageCandidate(
            student_resolved=True,
            actionable=True,
            model_priority="medium",
            due_at=NOW + timedelta(hours=12),
        ),
        now=NOW,
    )

    assert priority == "urgent"


def test_existing_task_receives_new_communication_instead_of_duplicate_task() -> None:
    decision = decide_inbound_action(
        InboundTriageCandidate(
            student_resolved=True,
            actionable=True,
            existing_work_item_id="task-1",
            model_priority="high",
            priority_evidence=("blocking requirement",),
        ),
        now=NOW,
    )

    assert decision.action == "append_to_existing_task"
    assert decision.reason_code == "matching_staff_task_found"


def test_model_cannot_create_urgency_without_evidence() -> None:
    priority = constrain_priority(
        InboundTriageCandidate(
            student_resolved=True,
            actionable=True,
            model_priority="urgent",
        ),
        now=NOW,
    )

    assert priority == "medium"


def test_existing_inquiry_is_appended_instead_of_duplicated() -> None:
    decision = decide_inbound_action(
        InboundTriageCandidate(
            student_resolved=True,
            actionable=True,
            existing_inquiry_id="inquiry-1",
        ),
        now=NOW,
    )

    assert decision.action == "append_to_existing_inquiry"
