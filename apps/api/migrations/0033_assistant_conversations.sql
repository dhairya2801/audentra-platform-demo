-- Durable Edward conversations.
--
-- A conversation is a student-scoped container; a message is one immutable
-- turn. The assistant's structured payloads (blocks, receipts, widgets,
-- usage) are persisted as jsonb beside the plain-text content so history can
-- be re-rendered exactly as it was answered, while `content` alone remains a
-- faithful plain-text transcript for voice and audit.
--
-- Client retries are absorbed at the storage boundary: a user message carries
-- the client-generated id, and replaying the same id can never produce a
-- second exchange.

CREATE TABLE assistant_conversation (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  status varchar(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
  page_path varchar(240),
  page_label varchar(240),
  created_at timestamptz NOT NULL DEFAULT now(),
  last_message_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz
);

CREATE INDEX assistant_conversation_student_idx
  ON assistant_conversation(tenant_id, student_id, last_message_at DESC);

CREATE TABLE assistant_message (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  conversation_id uuid NOT NULL REFERENCES assistant_conversation(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  role varchar(12) NOT NULL CHECK (role IN ('user', 'assistant')),
  input_mode varchar(8) NOT NULL DEFAULT 'text' CHECK (input_mode IN ('text', 'voice')),
  content text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 8000),
  client_message_id varchar(128),
  request_id varchar(128),
  provider varchar(24),
  model varchar(120),
  usage jsonb,
  blocks jsonb,
  context_receipts jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(context_receipts) = 'array'),
  suggested_actions jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(suggested_actions) = 'array'),
  widgets jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(widgets) = 'array'),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Replay safety: one user message per client-generated id per student.
CREATE UNIQUE INDEX assistant_message_client_uidx
  ON assistant_message(tenant_id, student_id, client_message_id)
  WHERE client_message_id IS NOT NULL AND role = 'user';

CREATE INDEX assistant_message_history_idx
  ON assistant_message(tenant_id, conversation_id, created_at, id);
