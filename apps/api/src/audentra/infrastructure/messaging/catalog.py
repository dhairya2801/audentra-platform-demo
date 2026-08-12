"""Exhaustive disposition catalog for every event currently emitted by the API."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

DispositionKind = Literal["handler", "ignored"]
HandlerKey = Literal[
    "dashboard_projection",
    "document_reservation_recovery",
    "document_extraction",
    "document_review_routing",
]


@dataclass(frozen=True, slots=True)
class EventDisposition:
    kind: DispositionKind
    handler_key: HandlerKey | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "handler" and self.handler_key is None:
            raise ValueError("handler dispositions require a handler key")
        if self.kind == "ignored" and not self.reason:
            raise ValueError("ignored dispositions require an explicit reason")


ALL_EMITTED_EVENT_NAMES = frozenset(
    {
        "admission.offer_accepted.v1",
        "document.extraction_completed.v1",
        "document.extraction_requested.v1",
        "document.extraction_retry_started.v1",
        "document.placeholder_created.v1",
        "document.storage_failed.v1",
        "document.stored_for_review.v1",
        "document.upload_reserved.v1",
        "enrollment.journey_created.v1",
        "payment.deposit_succeeded.v1",
        "staff.configuration_published.v1",
        "staff.action_rule_created.v1",
        "staff.action_rule_updated.v1",
        "staff.ai_refresh_requested.v1",
        "staff.call_recording_uploaded.v1",
        "staff.club_created.v1",
        "staff.club_updated.v1",
        "staff.communication_recorded.v1",
        "staff.core_play_created.v1",
        "staff.core_play_updated.v1",
        "staff.interaction_completed.v1",
        "staff.interaction_started.v1",
        "staff.knowledge_card_created.v1",
        "staff.knowledge_card_updated.v1",
        "staff.work_comment_created.v1",
        "staff.work_item_auto_resolved_by_document.v1",
        "staff.work_item_created.v1",
        "staff.work_item_updated.v1",
        "student.appointment_scheduled.v1",
        "student.campus_event_registered.v1",
        "student.document_decided_by_staff.v1",
        "student.housing_plan_updated.v1",
        "student.help_request_created.v1",
        "student.inquiry_message_created.v1",
        "student.inquiry_updated_by_staff.v1",
        "student.onboarding_completed.v1",
        "student.preferences_updated_by_staff.v1",
        "student.requirement_responded.v1",
        "student.profile_updated.v1",
        "student.transcript_credits_imported.v1",
        "student_financial.payment_plan_selected.v1",
    }
)

# These events commit canonical state synchronously and have no worker projection.
# They are deliberately ignored instead of repeatedly failing as unknown.
EXPLICITLY_ADDED_IGNORED_EVENTS = frozenset(
    {
        "staff.configuration_published.v1",
        "staff.action_rule_created.v1",
        "staff.action_rule_updated.v1",
        "staff.ai_refresh_requested.v1",
        "staff.call_recording_uploaded.v1",
        "staff.club_created.v1",
        "staff.club_updated.v1",
        "staff.communication_recorded.v1",
        "staff.core_play_created.v1",
        "staff.core_play_updated.v1",
        "staff.interaction_completed.v1",
        "staff.interaction_started.v1",
        "staff.knowledge_card_created.v1",
        "staff.knowledge_card_updated.v1",
        "staff.work_comment_created.v1",
        "staff.work_item_auto_resolved_by_document.v1",
        "staff.work_item_created.v1",
        "staff.work_item_updated.v1",
        "student.document_decided_by_staff.v1",
        "student.campus_event_registered.v1",
        "student.housing_plan_updated.v1",
        "student.inquiry_message_created.v1",
        "student.preferences_updated_by_staff.v1",
        "student.requirement_responded.v1",
        "student.transcript_credits_imported.v1",
        "student_financial.payment_plan_selected.v1",
    }
)

_NO_WORKER_PROJECTION_REASON = "canonical state is already committed; no worker projection exists"

EVENT_CATALOG = MappingProxyType(
    {
        event_name: EventDisposition(
            kind="ignored",
            reason=_NO_WORKER_PROJECTION_REASON,
        )
        for event_name in ALL_EMITTED_EVENT_NAMES
    }
    | {
        "enrollment.journey_created.v1": EventDisposition(
            kind="handler",
            handler_key="dashboard_projection",
        ),
        "document.upload_reserved.v1": EventDisposition(
            kind="handler",
            handler_key="document_reservation_recovery",
        ),
        "document.extraction_requested.v1": EventDisposition(
            kind="handler",
            handler_key="document_extraction",
        ),
        "document.extraction_completed.v1": EventDisposition(
            kind="handler",
            handler_key="document_review_routing",
        ),
        "document.stored_for_review.v1": EventDisposition(
            kind="handler",
            handler_key="document_review_routing",
        ),
    }
)
