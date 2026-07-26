# VV background worker

This service consumes the PostgreSQL transactional outbox and maintains the
student dashboard projection. It also handles
`document.extraction_requested.v1` by calling the API's private extraction
command with the original event identity and correlation ID.

## Delivery behavior

- Claims use `FOR UPDATE SKIP LOCKED` and an expiring worker lease.
- Failed deliveries are recorded on `outbox_event` with bounded exponential
  retry and deterministic jitter.
- Events at the maximum attempt count remain unpublished for operational
  inspection and are reported as dead-lettered in heartbeat logs.
- Projection handling records `(event_id, consumer_name)` in
  `projection_event_receipt` in the same transaction as the projection update.
  A crash between projection commit and outbox acknowledgement is therefore
  safe to retry.
- `admission.offer_accepted.v1` is explicitly acknowledged because its paired
  journey-created event performs the dashboard update. Unknown events fail
  closed and enter retry/dead-letter handling instead of being silently lost.
  Add routing or separate consumer cursors before multiple independent
  consumers need every event.

## Health and shutdown

The HTTP listener exposes:

- `GET /health/live`
- `GET /health/ready`

`SIGINT` and `SIGTERM` stop polling, allow the current event to finish, release
unprocessed claims, close the health listener, and drain the database pool.

Configuration is environment-based. See `infra/.env.example` and
`src/config.ts` for supported settings. `API_INTERNAL_URL` and
`DOCUMENT_WORKER_TOKEN` must match the API deployment; use a long unique token
outside local development.
