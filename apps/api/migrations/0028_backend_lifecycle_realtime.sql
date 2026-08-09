-- Add the durable backend lifecycle used by requirement-scoped help,
-- document-failure escalation, manual staff work, and Kubernetes-safe SSE.

ALTER TABLE student_requirement
  DROP CONSTRAINT IF EXISTS student_requirement_status_check;

ALTER TABLE student_requirement
  ADD CONSTRAINT student_requirement_status_check CHECK (
    status IN (
      'not_applicable', 'blocked', 'ready', 'help_requested', 'in_progress',
      'submitted', 'under_review', 'completed', 'waived', 'rejected', 'expired'
    )
  );

-- Older provider/model-route failures were recorded as terminal capability
-- failures even though originals are retained and retrying is non-destructive.
-- Restore the manual Retry affordance for supported stored originals. New
-- failures use the more precise provider_configuration code.
UPDATE document_record
SET extraction = jsonb_set(extraction, '{retryable}', 'true'::jsonb, true),
    updated_at = NOW()
WHERE mime_type IN ('application/pdf', 'image/jpeg', 'image/png')
  AND extraction->>'status' = 'failed'
  AND extraction->>'failureCode' = 'unsupported_capability'
  AND COALESCE(extraction->>'retryable', 'false') = 'false';

ALTER TABLE student_inquiry
  ADD COLUMN requirement_id uuid REFERENCES student_requirement(id),
  ADD COLUMN status_before_help varchar(32),
  ADD COLUMN resolved_at timestamptz,
  ADD COLUMN resolution_reason varchar(120),
  ADD CONSTRAINT student_inquiry_help_status_check CHECK (
    status_before_help IS NULL OR status_before_help IN (
      'blocked', 'ready', 'help_requested', 'in_progress', 'submitted',
      'under_review', 'rejected'
    )
  );

CREATE INDEX student_inquiry_requirement_idx
  ON student_inquiry(tenant_id, student_id, requirement_id, updated_at DESC)
  WHERE requirement_id IS NOT NULL;

CREATE UNIQUE INDEX student_inquiry_active_requirement_uidx
  ON student_inquiry(tenant_id, student_id, requirement_id)
  WHERE requirement_id IS NOT NULL
    AND status IN ('new', 'open', 'waiting_on_student');

ALTER TABLE staff_notification
  ADD COLUMN tenant_wide boolean NOT NULL DEFAULT false;

ALTER TABLE staff_notification
  DROP CONSTRAINT IF EXISTS staff_notification_target_check;

ALTER TABLE staff_notification
  ADD CONSTRAINT staff_notification_target_check CHECK (
    staff_member_id IS NOT NULL
    OR team_component IS NOT NULL
    OR tenant_wide = true
  );

-- This append-only stream is a durable invalidation journal, not a second
-- source of truth. REST projections remain canonical. A global identity gives
-- every tenant a strictly increasing, reconnect-safe numeric cursor.
CREATE TABLE staff_realtime_event (
  cursor bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL UNIQUE,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  event_type varchar(120) NOT NULL,
  resource_type varchar(80) NOT NULL,
  resource_id uuid NOT NULL,
  work_item_id uuid REFERENCES staff_work_item(id) ON DELETE SET NULL,
  staff_member_id uuid REFERENCES staff_member(id) ON DELETE SET NULL,
  team_component varchar(120),
  tenant_wide boolean NOT NULL DEFAULT false,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(payload) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_realtime_event_target_check CHECK (
    staff_member_id IS NOT NULL
    OR team_component IS NOT NULL
    OR tenant_wide = true
  )
);

CREATE INDEX staff_realtime_event_tenant_cursor_idx
  ON staff_realtime_event(tenant_id, cursor);

CREATE INDEX staff_realtime_event_staff_cursor_idx
  ON staff_realtime_event(tenant_id, staff_member_id, cursor)
  WHERE staff_member_id IS NOT NULL;

CREATE INDEX staff_realtime_event_team_cursor_idx
  ON staff_realtime_event(tenant_id, team_component, cursor)
  WHERE team_component IS NOT NULL;

CREATE FUNCTION prevent_staff_realtime_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'staff_realtime_event is append-only';
END;
$$;

CREATE TRIGGER staff_realtime_event_append_only
BEFORE UPDATE OR DELETE ON staff_realtime_event
FOR EACH ROW EXECUTE FUNCTION prevent_staff_realtime_event_mutation();
