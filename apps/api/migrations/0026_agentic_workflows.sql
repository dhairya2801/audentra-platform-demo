-- Durable agentic-workflow primitives.
--
-- These records deliberately sit beside, rather than inside, official
-- enrollment state. Agent output is evidence or a proposal until a named
-- application command (and, where required, a human) accepts it.

CREATE TABLE agent_run (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  feature varchar(80) NOT NULL,
  trigger_type varchar(32) NOT NULL CHECK (
    trigger_type IN ('event', 'scheduled', 'integration', 'interactive')
  ),
  trigger_event_id varchar(160),
  actor_type varchar(32) NOT NULL CHECK (
    actor_type IN ('student', 'staff', 'system')
  ),
  actor_id uuid,
  student_id uuid REFERENCES student(id),
  snapshot_version integer CHECK (snapshot_version IS NULL OR snapshot_version > 0),
  snapshot_hash char(64),
  prompt_version varchar(160),
  output_schema_version varchar(160),
  provider varchar(32),
  model varchar(160),
  status varchar(24) NOT NULL CHECK (
    status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
  ),
  result jsonb,
  failure_code varchar(64),
  correlation_id varchar(160) NOT NULL,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX agent_run_subject_idx
  ON agent_run(tenant_id, student_id, created_at DESC);

CREATE INDEX agent_run_trigger_idx
  ON agent_run(tenant_id, feature, trigger_type, created_at DESC);

CREATE TABLE agent_tool_call (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  agent_run_id uuid NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
  sequence integer NOT NULL CHECK (sequence > 0),
  tool_name varchar(120) NOT NULL,
  authorization_scope varchar(240) NOT NULL,
  arguments_hash char(64) NOT NULL,
  result_status varchar(24) NOT NULL CHECK (
    result_status IN ('succeeded', 'rejected', 'failed')
  ),
  error_code varchar(80),
  duration_ms integer CHECK (duration_ms IS NULL OR duration_ms >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT agent_tool_call_run_sequence_uidx UNIQUE (agent_run_id, sequence)
);

CREATE INDEX agent_tool_call_run_idx
  ON agent_tool_call(tenant_id, agent_run_id, sequence);

CREATE TABLE model_usage (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  agent_run_id uuid NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
  provider varchar(32) NOT NULL,
  model varchar(160) NOT NULL,
  input_tokens integer CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens integer CHECK (output_tokens IS NULL OR output_tokens >= 0),
  cached_input_tokens integer CHECK (
    cached_input_tokens IS NULL OR cached_input_tokens >= 0
  ),
  estimated_cost_micros bigint CHECK (
    estimated_cost_micros IS NULL OR estimated_cost_micros >= 0
  ),
  latency_ms integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
  rate_card_version varchar(80),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT model_usage_run_uidx UNIQUE (agent_run_id)
);

CREATE TABLE agent_action_proposal (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  agent_run_id uuid NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
  student_id uuid REFERENCES student(id),
  action_type varchar(80) NOT NULL,
  target_type varchar(80),
  target_id uuid,
  payload jsonb NOT NULL DEFAULT '{}',
  rationale text NOT NULL,
  status varchar(24) NOT NULL CHECK (
    status IN ('proposed', 'approved', 'rejected', 'expired', 'executed')
  ),
  expected_version integer CHECK (expected_version IS NULL OR expected_version > 0),
  approved_by uuid,
  expires_at timestamptz,
  approved_at timestamptz,
  executed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX agent_action_proposal_pending_idx
  ON agent_action_proposal(tenant_id, status, expires_at, created_at DESC);

CREATE TABLE inbox_event (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  provider varchar(48) NOT NULL,
  external_message_id varchar(320) NOT NULL,
  external_thread_id varchar(320),
  direction varchar(16) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
  sender_address varchar(320),
  recipient_addresses jsonb NOT NULL DEFAULT '[]',
  subject varchar(500),
  body_excerpt text CHECK (body_excerpt IS NULL OR char_length(body_excerpt) <= 12000),
  raw_object_key varchar(512),
  occurred_at timestamptz NOT NULL,
  status varchar(24) NOT NULL CHECK (
    status IN ('received', 'processing', 'processed', 'needs_triage', 'failed')
  ),
  failure_code varchar(80),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT inbox_event_provider_message_uidx
    UNIQUE (tenant_id, provider, external_message_id)
);

CREATE INDEX inbox_event_pending_idx
  ON inbox_event(tenant_id, status, occurred_at);

CREATE TABLE communication_event (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  inbox_event_id uuid UNIQUE REFERENCES inbox_event(id),
  student_id uuid REFERENCES student(id),
  channel varchar(16) NOT NULL CHECK (channel IN ('email', 'sms', 'voice', 'portal')),
  direction varchar(16) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
  external_thread_id varchar(320),
  subject varchar(500),
  body_excerpt text CHECK (body_excerpt IS NULL OR char_length(body_excerpt) <= 12000),
  metadata jsonb NOT NULL DEFAULT '{}',
  resolution_status varchar(24) NOT NULL CHECK (
    resolution_status IN ('unresolved', 'resolved', 'ambiguous', 'ignored')
  ),
  occurred_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX communication_event_student_idx
  ON communication_event(tenant_id, student_id, occurred_at DESC);

CREATE INDEX communication_event_resolution_idx
  ON communication_event(tenant_id, resolution_status, occurred_at);

CREATE TABLE communication_attachment (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  communication_id uuid NOT NULL REFERENCES communication_event(id) ON DELETE CASCADE,
  file_name varchar(255) NOT NULL,
  mime_type varchar(80) NOT NULL,
  size_bytes integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 52428800),
  sha256 char(64) NOT NULL,
  storage_key varchar(512) NOT NULL,
  scan_status varchar(24) NOT NULL CHECK (
    scan_status IN ('pending', 'clean', 'rejected', 'failed')
  ),
  document_id uuid REFERENCES document_record(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT communication_attachment_hash_uidx UNIQUE (tenant_id, communication_id, sha256)
);

CREATE INDEX communication_attachment_document_idx
  ON communication_attachment(tenant_id, document_id);

CREATE TABLE staff_work_item_link (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  work_item_id uuid NOT NULL REFERENCES staff_work_item(id) ON DELETE CASCADE,
  entity_type varchar(40) NOT NULL CHECK (
    entity_type IN ('communication', 'inbox_event', 'document', 'requirement', 'payment', 'inquiry')
  ),
  entity_id uuid NOT NULL,
  relationship varchar(40) NOT NULL DEFAULT 'related',
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_work_item_link_uidx
    UNIQUE (tenant_id, work_item_id, entity_type, entity_id)
);

CREATE INDEX staff_work_item_link_entity_idx
  ON staff_work_item_link(tenant_id, entity_type, entity_id);

CREATE TABLE student_engagement_snapshot (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  snapshot_version integer NOT NULL CHECK (snapshot_version > 0),
  last_active_at timestamptz,
  last_meaningful_action_at timestamptz,
  current_step_code varchar(120),
  completion_percentage smallint CHECK (completion_percentage BETWEEN 0 AND 100),
  blocking_requirement_count integer NOT NULL DEFAULT 0 CHECK (blocking_requirement_count >= 0),
  next_deadline timestamptz,
  days_to_next_deadline integer,
  recent_upload_failures integer NOT NULL DEFAULT 0 CHECK (recent_upload_failures >= 0),
  help_requested boolean NOT NULL DEFAULT false,
  open_support_case_count integer NOT NULL DEFAULT 0 CHECK (open_support_case_count >= 0),
  last_intervention_at timestamptz,
  signals jsonb NOT NULL DEFAULT '{}',
  projected_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, student_id)
);

CREATE TABLE intervention_candidate (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  trigger_code varchar(120) NOT NULL,
  dedupe_key varchar(240) NOT NULL,
  priority varchar(16) NOT NULL CHECK (
    priority IN ('urgent', 'high', 'medium', 'low')
  ),
  reason_codes jsonb NOT NULL DEFAULT '[]',
  evidence jsonb NOT NULL DEFAULT '{}',
  status varchar(24) NOT NULL CHECK (
    status IN ('new', 'accepted', 'dismissed', 'converted', 'suppressed', 'expired')
  ),
  suppression_until timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT intervention_candidate_dedupe_uidx
    UNIQUE (tenant_id, student_id, dedupe_key)
);

CREATE INDEX intervention_candidate_queue_idx
  ON intervention_candidate(tenant_id, status, priority, created_at DESC);
