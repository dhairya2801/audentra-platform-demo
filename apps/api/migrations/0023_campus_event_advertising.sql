ALTER TABLE campus_event
  ADD COLUMN advertisement_starts_at timestamptz,
  ADD COLUMN advertisement_ends_at timestamptz,
  ADD CONSTRAINT campus_event_advertisement_window_check CHECK (
    advertisement_starts_at IS NULL
    OR advertisement_ends_at IS NULL
    OR advertisement_ends_at > advertisement_starts_at
  );

CREATE INDEX campus_event_advertisement_feed_idx
  ON campus_event(tenant_id, active, advertisement_starts_at, advertisement_ends_at);
