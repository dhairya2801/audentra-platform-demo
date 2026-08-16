-- Durable, tenant-safe Staff Edward conversations.
--
-- Conversation and message ownership is enforced by composite foreign keys.
-- Student referents are also tenant-bound so neither a conversation-level
-- follow-up referent nor a message audit referent can cross tenants.

ALTER TABLE student
  ADD CONSTRAINT student_id_tenant_uidx UNIQUE (id, tenant_id);

ALTER TABLE staff_member
  ADD CONSTRAINT staff_member_id_tenant_uidx UNIQUE (id, tenant_id);

CREATE TABLE staff_assistant_conversation (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  staff_member_id uuid NOT NULL,
  status varchar(16) NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'closed')),
  active_student_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_message_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz,
  CONSTRAINT staff_assistant_conversation_owner_uidx
    UNIQUE (id, tenant_id, staff_member_id),
  CONSTRAINT staff_assistant_conversation_member_fk
    FOREIGN KEY (staff_member_id, tenant_id)
    REFERENCES staff_member(id, tenant_id) ON DELETE CASCADE,
  CONSTRAINT staff_assistant_conversation_referent_fk
    FOREIGN KEY (active_student_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE SET NULL (active_student_id)
);

CREATE INDEX staff_assistant_conversation_member_idx
  ON staff_assistant_conversation(tenant_id, staff_member_id, last_message_at DESC);

CREATE TABLE staff_assistant_message (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  conversation_id uuid NOT NULL,
  staff_member_id uuid NOT NULL,
  exchange_id uuid NOT NULL,
  role varchar(12) NOT NULL CHECK (role IN ('user', 'assistant')),
  content text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 8000),
  client_message_id varchar(128),
  request_id varchar(128),
  provider varchar(24),
  model varchar(120),
  usage jsonb,
  blocks jsonb,
  context_receipts jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(context_receipts) = 'array'),
  referenced_student_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_assistant_message_conversation_owner_fk
    FOREIGN KEY (conversation_id, tenant_id, staff_member_id)
    REFERENCES staff_assistant_conversation(id, tenant_id, staff_member_id)
    ON DELETE CASCADE,
  CONSTRAINT staff_assistant_message_referent_fk
    FOREIGN KEY (referenced_student_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE SET NULL (referenced_student_id)
);

CREATE UNIQUE INDEX staff_assistant_message_client_uidx
  ON staff_assistant_message(tenant_id, staff_member_id, client_message_id)
  WHERE client_message_id IS NOT NULL AND role = 'user';

CREATE UNIQUE INDEX staff_assistant_message_exchange_role_uidx
  ON staff_assistant_message(tenant_id, conversation_id, exchange_id, role);

CREATE INDEX staff_assistant_message_history_idx
  ON staff_assistant_message(tenant_id, conversation_id, created_at, id);
