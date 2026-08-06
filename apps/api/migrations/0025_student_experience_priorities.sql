-- Additive student-experience metadata used by the journey planner and the
-- durable in-app notification feed.
ALTER TABLE requirement_definition_version
  ADD COLUMN priority smallint NOT NULL DEFAULT 0
    CHECK (priority BETWEEN 0 AND 100);

ALTER TABLE student_message
  ADD COLUMN kind varchar(80) NOT NULL DEFAULT 'general',
  ADD COLUMN href varchar(500);

CREATE INDEX student_message_student_kind_idx
  ON student_message(tenant_id, student_id, kind, sent_at DESC);
