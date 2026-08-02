"""Fail-closed event dispatch driven by the exhaustive event catalog."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from .catalog import EVENT_CATALOG, EventDisposition, HandlerKey
from .envelope import DomainEventEnvelope

EventHandler = Callable[[DomainEventEnvelope], Awaitable[None]]


class UnknownEventError(RuntimeError):
    """An event has no reviewed disposition and must not be acknowledged."""


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    status: Literal["handled", "ignored"]
    event_name: str
    reason: str | None = None


class EventDispatcher:
    def __init__(
        self,
        handlers: Mapping[HandlerKey, EventHandler],
        *,
        catalog: Mapping[str, EventDisposition] = EVENT_CATALOG,
    ) -> None:
        self._handlers = dict(handlers)
        self._catalog = dict(catalog)

    async def dispatch(self, event: DomainEventEnvelope) -> DispatchOutcome:
        disposition = self._catalog.get(event.event_name)
        if disposition is None:
            raise UnknownEventError(f"No outbox disposition registered for {event.event_name}")
        if disposition.kind == "ignored":
            return DispatchOutcome(
                status="ignored",
                event_name=event.event_name,
                reason=disposition.reason,
            )
        handler_key = disposition.handler_key
        if handler_key is None:
            raise RuntimeError(f"Handler disposition for {event.event_name} is incomplete")
        handler = self._handlers.get(handler_key)
        if handler is None:
            raise RuntimeError(
                f"Worker handler {handler_key!r} is not configured for {event.event_name}"
            )
        await handler(event)
        return DispatchOutcome(status="handled", event_name=event.event_name)
