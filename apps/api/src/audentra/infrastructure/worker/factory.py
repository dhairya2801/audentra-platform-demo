"""Compose the reviewed event catalog with its concrete handlers."""

from __future__ import annotations

from audentra.infrastructure.messaging.catalog import HandlerKey
from audentra.infrastructure.messaging.dispatcher import EventDispatcher, EventHandler

from .dashboard_projector import StudentDashboardProjector
from .document_commands import DocumentExtractionRunner
from .document_review_projector import DocumentReviewProjector
from .staff_email import StaffEmailEventRunner


def build_event_dispatcher(
    projector: StudentDashboardProjector,
    document_runner: DocumentExtractionRunner,
    document_review_projector: DocumentReviewProjector,
    staff_email: StaffEmailEventRunner | None = None,
) -> EventDispatcher:
    handlers: dict[HandlerKey, EventHandler] = {
        "dashboard_projection": projector.handle,
        "document_reservation_recovery": document_runner.handle,
        "document_extraction": document_runner.handle,
        "document_review_routing": document_review_projector.handle,
    }
    if staff_email is not None:
        handlers["staff_email"] = staff_email.handle
    return EventDispatcher(handlers)
