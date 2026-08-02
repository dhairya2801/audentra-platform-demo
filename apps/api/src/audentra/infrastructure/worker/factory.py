"""Compose the reviewed event catalog with its concrete handlers."""

from __future__ import annotations

from audentra.infrastructure.messaging.catalog import HandlerKey
from audentra.infrastructure.messaging.dispatcher import EventDispatcher, EventHandler

from .dashboard_projector import StudentDashboardProjector
from .document_commands import DocumentExtractionRunner


def build_event_dispatcher(
    projector: StudentDashboardProjector,
    document_runner: DocumentExtractionRunner,
) -> EventDispatcher:
    handlers: dict[HandlerKey, EventHandler] = {
        "dashboard_projection": projector.handle,
        "document_reservation_recovery": document_runner.handle,
        "document_extraction": document_runner.handle,
    }
    return EventDispatcher(handlers)
