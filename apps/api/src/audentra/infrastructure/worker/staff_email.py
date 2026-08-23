"""Durable delegated-mailbox event handling."""

from audentra.infrastructure.messaging.envelope import DomainEventEnvelope
from audentra.infrastructure.postgres.staff_email_service import PostgresStaffEmailService


class StaffEmailEventRunner:
    def __init__(self, service: PostgresStaffEmailService) -> None:
        self._service = service

    async def handle(self, event: DomainEventEnvelope) -> None:
        await self._service.handle_mail_event(
            event.event_name,
            event.tenant_id,
            event.aggregate_id,
        )
