-- Durable, response-scoped feedback for Student and Staff Edward.
--
-- The assistant's request id is already the canonical execution trace id. This
-- migration makes the existing sanitized AssistantTurnTrace payload durable,
-- then binds one mutable feedback row to the exact immutable transcript pair
-- that produced it. The four message-id columns keep PostgreSQL foreign keys
-- honest across the two existing transcript tables without introducing a
-- generic, unscoped message reference.

ALTER TABLE assistant_message
  ADD CONSTRAINT assistant_message_feedback_owner_uidx
  UNIQUE (id, tenant_id, student_id);

ALTER TABLE staff_assistant_message
  ADD CONSTRAINT staff_assistant_message_feedback_owner_uidx
  UNIQUE (id, tenant_id, staff_member_id);

CREATE TABLE assistant_turn_trace (
  trace_id varchar(128) PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  assistant_kind varchar(16) NOT NULL
    CHECK (assistant_kind IN ('student', 'staff')),
  student_actor_id uuid,
  staff_member_id uuid,
  referenced_student_id uuid,
  conversation_id uuid,
  user_message_id uuid,
  assistant_message_id uuid,
  trace_payload jsonb NOT NULL
    CHECK (jsonb_typeof(trace_payload) = 'object'),
  started_at timestamptz,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT assistant_turn_trace_actor_check CHECK (
    (assistant_kind = 'student' AND student_actor_id IS NOT NULL AND staff_member_id IS NULL)
    OR
    (assistant_kind = 'staff' AND student_actor_id IS NULL AND staff_member_id IS NOT NULL)
  ),
  CONSTRAINT assistant_turn_trace_student_response_uidx
    UNIQUE (trace_id, tenant_id, student_actor_id, assistant_message_id),
  CONSTRAINT assistant_turn_trace_staff_response_uidx
    UNIQUE (trace_id, tenant_id, staff_member_id, assistant_message_id),
  CONSTRAINT assistant_turn_trace_student_actor_fk
    FOREIGN KEY (student_actor_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE CASCADE,
  CONSTRAINT assistant_turn_trace_staff_actor_fk
    FOREIGN KEY (staff_member_id, tenant_id)
    REFERENCES staff_member(id, tenant_id) ON DELETE CASCADE,
  CONSTRAINT assistant_turn_trace_referenced_student_fk
    FOREIGN KEY (referenced_student_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE SET NULL (referenced_student_id)
);

CREATE INDEX assistant_turn_trace_kind_recorded_idx
  ON assistant_turn_trace(assistant_kind, recorded_at DESC);

CREATE INDEX assistant_turn_trace_tenant_recorded_idx
  ON assistant_turn_trace(tenant_id, recorded_at DESC);

CREATE TABLE edward_response_feedback (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  assistant_kind varchar(16) NOT NULL
    CHECK (assistant_kind IN ('student', 'staff')),
  student_id uuid,
  staff_member_id uuid,
  referenced_student_id uuid,
  conversation_id uuid NOT NULL,
  student_user_message_id uuid,
  student_assistant_message_id uuid,
  staff_user_message_id uuid,
  staff_assistant_message_id uuid,
  trace_id varchar(128) NOT NULL,
  question text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 8000),
  response text NOT NULL CHECK (char_length(response) BETWEEN 1 AND 8000),
  rating varchar(16) CHECK (rating IN ('positive', 'negative')),
  written_feedback text CHECK (
    written_feedback IS NULL
    OR char_length(written_feedback) BETWEEN 1 AND 4000
  ),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT edward_response_feedback_content_check CHECK (
    rating IS NOT NULL OR written_feedback IS NOT NULL
  ),
  CONSTRAINT edward_response_feedback_kind_check CHECK (
    (
      assistant_kind = 'student'
      AND student_id IS NOT NULL
      AND staff_member_id IS NULL
      AND student_user_message_id IS NOT NULL
      AND student_assistant_message_id IS NOT NULL
      AND staff_user_message_id IS NULL
      AND staff_assistant_message_id IS NULL
    )
    OR
    (
      assistant_kind = 'staff'
      AND student_id IS NULL
      AND staff_member_id IS NOT NULL
      AND student_user_message_id IS NULL
      AND student_assistant_message_id IS NULL
      AND staff_user_message_id IS NOT NULL
      AND staff_assistant_message_id IS NOT NULL
    )
  ),
  CONSTRAINT edward_response_feedback_student_actor_fk
    FOREIGN KEY (student_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE CASCADE,
  CONSTRAINT edward_response_feedback_staff_actor_fk
    FOREIGN KEY (staff_member_id, tenant_id)
    REFERENCES staff_member(id, tenant_id) ON DELETE CASCADE,
  CONSTRAINT edward_response_feedback_referenced_student_fk
    FOREIGN KEY (referenced_student_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE SET NULL (referenced_student_id),
  CONSTRAINT edward_response_feedback_student_user_message_fk
    FOREIGN KEY (student_user_message_id, tenant_id, student_id)
    REFERENCES assistant_message(id, tenant_id, student_id) ON DELETE CASCADE,
  CONSTRAINT edward_response_feedback_student_assistant_message_fk
    FOREIGN KEY (student_assistant_message_id, tenant_id, student_id)
    REFERENCES assistant_message(id, tenant_id, student_id) ON DELETE CASCADE,
  CONSTRAINT edward_response_feedback_staff_user_message_fk
    FOREIGN KEY (staff_user_message_id, tenant_id, staff_member_id)
    REFERENCES staff_assistant_message(id, tenant_id, staff_member_id) ON DELETE CASCADE,
  CONSTRAINT edward_response_feedback_staff_assistant_message_fk
    FOREIGN KEY (staff_assistant_message_id, tenant_id, staff_member_id)
    REFERENCES staff_assistant_message(id, tenant_id, staff_member_id) ON DELETE CASCADE,
  CONSTRAINT edward_response_feedback_student_trace_fk
    FOREIGN KEY (trace_id, tenant_id, student_id, student_assistant_message_id)
    REFERENCES assistant_turn_trace(
      trace_id, tenant_id, student_actor_id, assistant_message_id
    ),
  CONSTRAINT edward_response_feedback_staff_trace_fk
    FOREIGN KEY (trace_id, tenant_id, staff_member_id, staff_assistant_message_id)
    REFERENCES assistant_turn_trace(
      trace_id, tenant_id, staff_member_id, assistant_message_id
    )
);

CREATE UNIQUE INDEX edward_response_feedback_student_response_uidx
  ON edward_response_feedback(student_assistant_message_id)
  WHERE student_assistant_message_id IS NOT NULL;

CREATE UNIQUE INDEX edward_response_feedback_staff_response_uidx
  ON edward_response_feedback(staff_assistant_message_id)
  WHERE staff_assistant_message_id IS NOT NULL;

CREATE INDEX edward_response_feedback_lab_idx
  ON edward_response_feedback(assistant_kind, created_at DESC, id);

CREATE INDEX edward_response_feedback_tenant_idx
  ON edward_response_feedback(tenant_id, created_at DESC, id);
