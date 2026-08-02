CREATE TABLE student_inquiry (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  topic_code varchar(40) NOT NULL CHECK (
    topic_code IN ('getting_started', 'documents', 'payments', 'support')
  ),
  subject varchar(240) NOT NULL,
  message text NOT NULL CHECK (
    char_length(message) BETWEEN 1 AND 500
  ),
  status varchar(24) NOT NULL DEFAULT 'new' CHECK (
    status IN ('new', 'open', 'waiting_on_student', 'resolved')
  ),
  priority varchar(16) NOT NULL CHECK (
    priority IN ('urgent', 'high', 'medium', 'low')
  ),
  assignee_id uuid REFERENCES staff_member(id),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX student_inquiry_student_idx
  ON student_inquiry(tenant_id, student_id, created_at DESC);

CREATE INDEX student_inquiry_staff_queue_idx
  ON student_inquiry(tenant_id, status, priority, updated_at DESC);
