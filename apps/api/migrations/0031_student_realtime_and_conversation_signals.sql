-- Durable student invalidations and model-generated conversation signals.
-- REST projections remain canonical; the realtime table is only a replayable
-- hint that a student client should read the affected resource again.

ALTER TABLE interaction_outcome_revision
  ADD COLUMN conversation_signals jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(conversation_signals) = 'object');

CREATE TABLE student_realtime_event (
  cursor bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id uuid NOT NULL UNIQUE,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  event_type varchar(120) NOT NULL,
  resource_type varchar(80) NOT NULL,
  resource_id uuid NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(payload) = 'object'),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX student_realtime_event_student_cursor_idx
  ON student_realtime_event(tenant_id, student_id, cursor);

CREATE FUNCTION prevent_student_realtime_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'student_realtime_event is append-only';
END;
$$;

CREATE TRIGGER student_realtime_event_append_only
BEFORE UPDATE OR DELETE ON student_realtime_event
FOR EACH ROW EXECUTE FUNCTION prevent_student_realtime_event_mutation();
