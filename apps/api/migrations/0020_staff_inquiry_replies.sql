CREATE TABLE student_inquiry_reply (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  inquiry_id uuid NOT NULL REFERENCES student_inquiry(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES student(id),
  staff_member_id uuid NOT NULL REFERENCES staff_member(id),
  response_note text NOT NULL CHECK (
    char_length(response_note) BETWEEN 1 AND 1000
  ),
  notify_student boolean NOT NULL,
  student_message_id uuid REFERENCES student_message(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (notify_student OR student_message_id IS NULL),
  CHECK (NOT notify_student OR student_message_id IS NOT NULL)
);

CREATE INDEX student_inquiry_reply_history_idx
  ON student_inquiry_reply(tenant_id, inquiry_id, created_at DESC);
