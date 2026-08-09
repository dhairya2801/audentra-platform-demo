from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from audentra.infrastructure.messaging.catalog import (
    ALL_EMITTED_EVENT_NAMES,
    EVENT_CATALOG,
    EXPLICITLY_ADDED_IGNORED_EVENTS,
)
from audentra.infrastructure.messaging.dispatcher import EventDispatcher, UnknownEventError
from audentra.infrastructure.messaging.envelope import (
    DomainEventActor,
    DomainEventEnvelope,
    parse_outbox_event,
)

EXPECTED_EVENTS = {
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
    "student.profile_updated.v1",
    "student.requirement_responded.v1",
    "student.transcript_credits_imported.v1",
    "student_financial.payment_plan_selected.v1",
}


def _event(event_name: str) -> DomainEventEnvelope:
    return DomainEventEnvelope(
        event_id="event-1",
        event_name=event_name,
        occurred_at=datetime(2026, 7, 24, tzinfo=UTC),
        tenant_id="tenant-1",
        aggregate_type="student",
        aggregate_id="student-1",
        aggregate_version=1,
        actor=DomainEventActor(type="student", id="actor-1"),
        correlation_id="request-1",
        causation_id="command-1",
        data={"studentId": "student-1"},
    )


def test_catalog_is_exhaustive_for_every_emitted_event() -> None:
    assert ALL_EMITTED_EVENT_NAMES == EXPECTED_EVENTS
    assert set(EVENT_CATALOG) == EXPECTED_EVENTS
    assert sum(item.kind == "handler" for item in EVENT_CATALOG.values()) == 5
    assert all(
        item.handler_key is not None if item.kind == "handler" else item.reason
        for item in EVENT_CATALOG.values()
    )


def test_canonical_only_events_have_explicit_ignore_dispositions() -> None:
    assert len(EXPLICITLY_ADDED_IGNORED_EVENTS) == 24
    assert all(EVENT_CATALOG[name].kind == "ignored" for name in EXPLICITLY_ADDED_IGNORED_EVENTS)


def test_row_parser_accepts_current_embedded_envelope_and_serializes_camel_case() -> None:
    event = _event("student.profile_updated.v1")
    row = {
        "id": event.event_id,
        "tenant_id": event.tenant_id,
        "event_name": event.event_name,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "aggregate_version": 1,
        "occurred_at": event.occurred_at,
        "actor_type": "student",
        "actor_id": "actor-1",
        "correlation_id": "request-1",
        "causation_id": "command-1",
        "payload": event.model_dump(mode="json", by_alias=True),
        "attempts": 2,
    }

    parsed = parse_outbox_event(row)

    assert parsed.outbox_id == event.event_id
    assert parsed.attempts == 2
    serialized = parsed.model_dump(mode="json", by_alias=True)
    assert serialized["eventName"] == event.event_name
    assert serialized["aggregateVersion"] == 1


def test_row_parser_accepts_native_postgres_uuid_columns() -> None:
    event = _event("student.profile_updated.v1")
    outbox_id = UUID("53e30c87-c7a8-423c-b2ab-4e6cafd2ac8a")
    tenant_id = UUID("00000000-0000-7000-8000-000000000001")
    aggregate_id = UUID("00000000-0000-7000-8000-000000000101")
    actor_id = UUID("00000000-0000-7000-8000-000000000100")
    row = {
        "id": outbox_id,
        "tenant_id": tenant_id,
        "event_name": event.event_name,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": 1,
        "occurred_at": event.occurred_at,
        "actor_type": "student",
        "actor_id": actor_id,
        "correlation_id": "request-1",
        "causation_id": "command-1",
        "payload": event.model_dump(mode="json", by_alias=True),
        "attempts": 0,
    }

    parsed = parse_outbox_event(row)

    assert parsed.outbox_id == str(outbox_id)
    assert parsed.tenant_id == str(tenant_id)
    assert parsed.aggregate_id == str(aggregate_id)
    assert parsed.actor is not None
    assert parsed.actor.id == str(actor_id)


def test_envelope_rejects_unknown_fields() -> None:
    values = _event("student.profile_updated.v1").model_dump()
    values["unexpected"] = True
    with pytest.raises(ValidationError):
        DomainEventEnvelope.model_validate(values)


def test_dispatcher_handles_ignored_and_fails_closed_for_unknown_events() -> None:
    dispatcher = EventDispatcher({})
    ignored = asyncio.run(dispatcher.dispatch(_event("student.profile_updated.v1")))
    assert ignored.status == "ignored"

    with pytest.raises(UnknownEventError):
        asyncio.run(dispatcher.dispatch(_event("future.unreviewed_event.v1")))
