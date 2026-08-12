-- Bind every persisted assistant message to the same tenant and student as
-- its conversation. The earlier single-column foreign key proves only that
-- the conversation exists; this composite key also enforces ownership.

ALTER TABLE assistant_message
  ADD CONSTRAINT assistant_message_conversation_owner_fk
  FOREIGN KEY (conversation_id, tenant_id, student_id)
  REFERENCES assistant_conversation(id, tenant_id, student_id)
  ON DELETE CASCADE;
