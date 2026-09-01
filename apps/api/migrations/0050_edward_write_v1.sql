-- Edward Write Abilities V1.
--
-- The model never writes these tables directly.  It may propose one of the
-- closed action names below, while the application resolves canonical state,
-- evaluates policy, and stores the exact preview.  Confirmation consumes that
-- immutable snapshot once; execution produces a server-issued receipt.

CREATE TABLE staff_role_capability (
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  role_code varchar(40) NOT NULL,
  capability varchar(80) NOT NULL CHECK (capability ~ '^[a-z][a-z0-9_.]{2,79}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, role_code, capability)
);

-- Existing roles receive only bounded Edward capabilities.  Broad student
-- scope and bulk execution are deliberately limited to operational leaders;
-- everyone else must have a current student assignment or own the work item.
INSERT INTO staff_role_capability (tenant_id, role_code, capability)
SELECT DISTINCT member.tenant_id, member.role_code, capability
FROM staff_member member
CROSS JOIN unnest(ARRAY[
  'edward.act',
  'edward.follow_up.create',
  'edward.work_item.update',
  'edward.email.prepare'
]::text[]) capability
ON CONFLICT DO NOTHING;

INSERT INTO staff_role_capability (tenant_id, role_code, capability)
SELECT DISTINCT member.tenant_id, member.role_code, capability
FROM staff_member member
CROSS JOIN unnest(ARRAY[
  'edward.student.any',
  'edward.cohort.follow_up.create'
]::text[]) capability
-- Broad student scope and bulk execution follow operational leadership. `vp`
-- was missing from the first draft of this list, which left a VP of Enrollment
-- unable to act outside a caseload she does not have — a denial that is
-- correct by the rule and wrong by the institution. Role strings are a
-- stopgap: capability administration is the real answer.
WHERE lower(member.role_code) IN (
  'admin', 'administrator', 'director', 'manager', 'supervisor',
  'operations_lead', 'vp', 'vice_president', 'dean', 'registrar'
)
ON CONFLICT DO NOTHING;

CREATE TABLE agent_action_intent (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  actor_type varchar(16) NOT NULL CHECK (actor_type IN ('student', 'staff')),
  student_actor_id uuid,
  staff_member_id uuid,
  student_conversation_id uuid,
  staff_conversation_id uuid,
  trace_id varchar(128),
  action_type varchar(80) NOT NULL CHECK (action_type IN (
    'student.requirement.submit_response',
    'student.preferences.update',
    'student.support.contact',
    'operations.follow_up.create',
    'operations.work_item.update',
    'operations.cohort.create_follow_ups',
    'communications.email.prepare'
  )),
  target_student_id uuid,
  target_resource_type varchar(48),
  target_resource_id uuid,
  request_payload jsonb NOT NULL CHECK (jsonb_typeof(request_payload) = 'object'),
  resolved_payload jsonb NOT NULL CHECK (jsonb_typeof(resolved_payload) = 'object'),
  preview jsonb NOT NULL CHECK (jsonb_typeof(preview) = 'object'),
  provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance) = 'array'),
  scope_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(scope_snapshot) = 'object'),
  risk_class smallint NOT NULL CHECK (risk_class BETWEEN 1 AND 4),
  confirmation_mode varchar(24) NOT NULL CHECK (
    confirmation_mode IN ('immediate', 'confirm', 'strong_confirm', 'external_confirm')
  ),
  authorization_capability varchar(80),
  content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  status varchar(24) NOT NULL DEFAULT 'pending_confirmation' CHECK (status IN (
    'pending_confirmation', 'executing', 'succeeded', 'partial', 'failed',
    'cancelled', 'expired'
  )),
  idempotency_key varchar(128) NOT NULL,
  expires_at timestamptz NOT NULL,
  confirmed_at timestamptz,
  execution_started_at timestamptz,
  completed_at timestamptz,
  failure_code varchar(80),
  failure_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT agent_action_intent_actor_check CHECK (
    (actor_type='student' AND student_actor_id IS NOT NULL AND staff_member_id IS NULL
      AND student_conversation_id IS NOT NULL AND staff_conversation_id IS NULL)
    OR
    (actor_type='staff' AND student_actor_id IS NULL AND staff_member_id IS NOT NULL
      AND student_conversation_id IS NULL AND staff_conversation_id IS NOT NULL)
  ),
  CONSTRAINT agent_action_intent_expiry_check CHECK (expires_at > created_at),
  CONSTRAINT agent_action_intent_student_actor_fk
    FOREIGN KEY (student_actor_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE CASCADE,
  CONSTRAINT agent_action_intent_staff_actor_fk
    FOREIGN KEY (staff_member_id, tenant_id)
    REFERENCES staff_member(id, tenant_id) ON DELETE CASCADE,
  CONSTRAINT agent_action_intent_target_student_fk
    FOREIGN KEY (target_student_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE CASCADE,
  UNIQUE (id, tenant_id),
  UNIQUE (id, tenant_id, action_type),
  UNIQUE NULLS NOT DISTINCT (
    tenant_id, student_actor_id, staff_member_id, idempotency_key
  )
);

CREATE INDEX agent_action_intent_actor_pending_idx
  ON agent_action_intent(
    tenant_id, actor_type, student_actor_id, staff_member_id, created_at DESC
  ) WHERE status IN ('pending_confirmation', 'executing');
CREATE INDEX agent_action_intent_expiry_idx
  ON agent_action_intent(expires_at) WHERE status='pending_confirmation';
CREATE INDEX agent_action_intent_trace_idx
  ON agent_action_intent(trace_id) WHERE trace_id IS NOT NULL;

-- Link Edward's external-action preview to the product's existing hash-pinned
-- email send intent. This makes a retry return the same downstream intent
-- instead of creating a second opportunity to send the same message.
ALTER TABLE staff_email_send_intent
  ADD COLUMN agent_action_intent_id uuid,
  ADD CONSTRAINT staff_email_send_intent_agent_action_fk
    FOREIGN KEY (agent_action_intent_id, tenant_id)
    REFERENCES agent_action_intent(id, tenant_id) ON DELETE SET NULL,
  ADD CONSTRAINT staff_email_send_intent_agent_action_uidx
    UNIQUE (agent_action_intent_id);

CREATE TABLE agent_action_receipt (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  action_intent_id uuid NOT NULL,
  action_type varchar(80) NOT NULL,
  status varchar(16) NOT NULL CHECK (status IN ('succeeded', 'partial', 'failed')),
  target jsonb NOT NULL CHECK (jsonb_typeof(target) = 'object'),
  result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
  affected_count integer NOT NULL DEFAULT 0 CHECK (affected_count >= 0),
  audit_event_ids jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(audit_event_ids) = 'array'),
  receipt_sha256 char(64) NOT NULL CHECK (receipt_sha256 ~ '^[a-f0-9]{64}$'),
  committed_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT agent_action_receipt_intent_fk
    FOREIGN KEY (action_intent_id, tenant_id, action_type)
    REFERENCES agent_action_intent(id, tenant_id, action_type) ON DELETE CASCADE,
  UNIQUE (action_intent_id)
);

CREATE INDEX agent_action_receipt_actor_join_idx
  ON agent_action_receipt(tenant_id, committed_at DESC);

ALTER TABLE staff_work_item
  ADD CONSTRAINT staff_work_item_id_tenant_uidx UNIQUE (id, tenant_id);

CREATE TABLE agent_action_batch_item (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  action_intent_id uuid NOT NULL REFERENCES agent_action_intent(id) ON DELETE CASCADE,
  student_id uuid NOT NULL,
  idempotency_key varchar(128) NOT NULL,
  status varchar(16) NOT NULL DEFAULT 'pending' CHECK (
    status IN ('pending', 'succeeded', 'failed', 'skipped')
  ),
  work_item_id uuid,
  error_code varchar(80),
  error_message text,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT agent_action_batch_student_fk
    FOREIGN KEY (student_id, tenant_id)
    REFERENCES student(id, tenant_id) ON DELETE CASCADE,
  CONSTRAINT agent_action_batch_work_item_fk
    FOREIGN KEY (work_item_id, tenant_id)
    REFERENCES staff_work_item(id, tenant_id) ON DELETE SET NULL (work_item_id),
  UNIQUE (action_intent_id, student_id),
  UNIQUE (tenant_id, idempotency_key)
);

ALTER TABLE assistant_message
  ADD COLUMN action_intents jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(action_intents) = 'array'),
  ADD COLUMN action_receipts jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(action_receipts) = 'array');

ALTER TABLE staff_assistant_conversation
  ADD COLUMN active_cohort_filter jsonb,
  ADD COLUMN active_cohort_fingerprint char(64),
  ADD CONSTRAINT staff_assistant_conversation_cohort_filter_check CHECK (
    active_cohort_filter IS NULL OR jsonb_typeof(active_cohort_filter) = 'object'
  );

ALTER TABLE staff_assistant_message
  ADD COLUMN action_intents jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(action_intents) = 'array'),
  ADD COLUMN action_receipts jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(action_receipts) = 'array');

COMMENT ON TABLE agent_action_intent IS
  'Server-resolved, actor-bound Edward action preview; never an authorization token from the model.';
COMMENT ON TABLE agent_action_receipt IS
  'Server-issued evidence of an attempted committed Edward side effect.';
COMMENT ON COLUMN agent_action_intent.provenance IS
  'Typed evidence origins used to resolve the action; untrusted content is never instruction authority.';
