-- A student inquiry is the canonical support conversation. Keep it active for
-- five days after the most recent participant message, then archive it from
-- active inboxes. The immutable history remains available for audit/recovery.

ALTER TABLE student_inquiry
  DROP CONSTRAINT IF EXISTS student_inquiry_status_check;

ALTER TABLE student_inquiry
  ADD CONSTRAINT student_inquiry_status_check CHECK (
    status IN ('new', 'open', 'waiting_on_student', 'resolved', 'archived')
  );

ALTER TABLE student_inquiry
  ADD COLUMN last_message_at timestamptz,
  ADD COLUMN expires_at timestamptz,
  ADD COLUMN archived_at timestamptz;

UPDATE student_inquiry
SET last_message_at = updated_at,
    expires_at = updated_at + interval '5 days'
WHERE last_message_at IS NULL;

ALTER TABLE student_inquiry
  ALTER COLUMN last_message_at SET NOT NULL,
  ALTER COLUMN expires_at SET NOT NULL;

-- Defaults protect automated/scheduled inbound adapters which create an
-- inquiry without going through the student portal request path.
ALTER TABLE student_inquiry
  ALTER COLUMN last_message_at SET DEFAULT NOW(),
  ALTER COLUMN expires_at SET DEFAULT (NOW() + interval '5 days');

CREATE INDEX student_inquiry_active_conversation_expiry_idx
  ON student_inquiry(tenant_id, expires_at, id)
  WHERE archived_at IS NULL
    AND status IN ('new', 'open', 'waiting_on_student', 'resolved');

CREATE INDEX student_inquiry_student_active_conversation_idx
  ON student_inquiry(tenant_id, student_id, updated_at DESC, id)
  WHERE archived_at IS NULL;
