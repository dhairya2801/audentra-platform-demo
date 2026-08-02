"""Domain-event envelopes, catalog, dispatch, and transactional outbox support."""

from .catalog import (
    ALL_EMITTED_EVENT_NAMES,
    EVENT_CATALOG,
    EXPLICITLY_ADDED_IGNORED_EVENTS,
    EventDisposition,
)
from .dispatcher import DispatchOutcome, EventDispatcher, UnknownEventError
from .envelope import (
    ClaimedOutboxEvent,
    DomainEventActor,
    DomainEventEnvelope,
    EventLineage,
    parse_outbox_event,
)

__all__ = [
    "ALL_EMITTED_EVENT_NAMES",
    "EVENT_CATALOG",
    "EXPLICITLY_ADDED_IGNORED_EVENTS",
    "ClaimedOutboxEvent",
    "DispatchOutcome",
    "DomainEventActor",
    "DomainEventEnvelope",
    "EventDispatcher",
    "EventDisposition",
    "EventLineage",
    "UnknownEventError",
    "parse_outbox_event",
]
