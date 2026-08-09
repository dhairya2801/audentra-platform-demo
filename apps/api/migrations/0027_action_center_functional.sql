-- Complete the enrollment Action Center lifecycle and add durable,
-- independently versioned AI projections. Canonical student, task, document,
-- inquiry, and communication records remain the source of truth.

ALTER TABLE staff_work_item
  DROP CONSTRAINT IF EXISTS staff_work_item_status_check;

ALTER TABLE staff_work_item
  ADD CONSTRAINT staff_work_item_status_check CHECK (
    status IN (
      'todo', 'in_progress', 'follow_up_required', 'blocked', 'done', 'cancelled'
    )
  ),
  ADD COLUMN action_type varchar(48) NOT NULL DEFAULT 'enrollment_follow_up'
    CHECK (action_type IN (
      'enrollment_follow_up', 'onboarding_assistance', 'document_review',
      'missing_information', 'external_verification', 'deadline_risk',
      'staff_decision', 'communication_response', 'blocked_dependency'
    )),
  ADD COLUMN selected_channel varchar(16)
    CHECK (selected_channel IN ('email', 'sms', 'voice', 'portal')),
  ADD COLUMN attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  ADD COLUMN follow_up_at timestamptz,
  ADD COLUMN blocker_code varchar(64),
  ADD COLUMN blocker_detail text,
  ADD COLUMN blocker_review_at timestamptz,
  ADD COLUMN outcome_code varchar(48),
  ADD COLUMN resolution_code varchar(48),
  ADD COLUMN next_step text,
  ADD COLUMN terminal_reason varchar(80),
  ADD COLUMN started_at timestamptz,
  ADD COLUMN interaction_completed_at timestamptz,
  ADD COLUMN completed_at timestamptz,
  ADD COLUMN cancelled_at timestamptz;

UPDATE staff_work_item
SET action_type = CASE work_type
  WHEN 'document_review' THEN 'document_review'
  WHEN 'communication' THEN 'communication_response'
  ELSE 'enrollment_follow_up'
END;

ALTER TABLE staff_work_log
  DROP CONSTRAINT IF EXISTS staff_work_log_actor_type_check;

ALTER TABLE staff_work_log
  ADD CONSTRAINT staff_work_log_actor_type_check CHECK (
    actor_type IN ('staff', 'system', 'student')
  );

ALTER TABLE staff_work_log
  DROP CONSTRAINT IF EXISTS staff_work_log_action_check;

ALTER TABLE staff_work_log
  ADD CONSTRAINT staff_work_log_action_check CHECK (
    action IN (
      'created', 'status_changed', 'assigned', 'escalated', 'commented',
      'document_decided', 'student_preferences_updated', 'channel_selected',
      'interaction_started', 'communication_recorded', 'outcome_recorded',
      'follow_up_scheduled', 'blocked', 'cancelled', 'ai_refresh_requested',
      'ai_outcome_updated', 'ai_task_insight_updated',
      'student_summary_updated', 'call_recording_uploaded',
      'call_transcription_updated', 'scheduled_rule_matched'
    )
  );

CREATE TABLE staff_work_comment (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  work_item_id uuid NOT NULL REFERENCES staff_work_item(id) ON DELETE CASCADE,
  author_id uuid NOT NULL REFERENCES staff_member(id),
  request_key varchar(128) NOT NULL,
  body text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2000),
  mentions jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(mentions) = 'array'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_work_comment_request_uidx UNIQUE (
    tenant_id, author_id, request_key
  )
);

CREATE INDEX staff_work_comment_item_idx
  ON staff_work_comment(tenant_id, work_item_id, created_at, id);

CREATE TABLE student_inquiry_student_reply (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  inquiry_id uuid NOT NULL REFERENCES student_inquiry(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES student(id),
  request_key varchar(128) NOT NULL,
  body text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 2000),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT student_inquiry_student_reply_request_uidx UNIQUE (
    tenant_id, student_id, request_key
  )
);

CREATE INDEX student_inquiry_student_reply_history_idx
  ON student_inquiry_student_reply(tenant_id, inquiry_id, created_at, id);

CREATE TABLE staff_interaction (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  work_item_id uuid NOT NULL REFERENCES staff_work_item(id) ON DELETE CASCADE,
  objective text NOT NULL CHECK (char_length(objective) BETWEEN 1 AND 1000),
  status varchar(32) NOT NULL CHECK (
    status IN (
      'collecting', 'enrichment_pending', 'provisional', 'completed',
      'stale', 'failed_retryable'
    )
  ),
  selected_channel varchar(16)
    CHECK (selected_channel IN ('email', 'sms', 'voice', 'portal')),
  source_version bigint NOT NULL DEFAULT 0 CHECK (source_version >= 0),
  covered_source_version bigint NOT NULL DEFAULT 0 CHECK (covered_source_version >= 0),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  quiet_until timestamptz,
  last_activity_at timestamptz,
  completed_at timestamptz,
  created_by uuid REFERENCES staff_member(id),
  request_key varchar(128) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_interaction_request_uidx UNIQUE (
    tenant_id, created_by, request_key
  ),
  CHECK (covered_source_version <= source_version)
);

CREATE INDEX staff_interaction_work_idx
  ON staff_interaction(tenant_id, work_item_id, updated_at DESC);

CREATE INDEX staff_interaction_due_idx
  ON staff_interaction(tenant_id, status, quiet_until)
  WHERE status IN ('collecting', 'enrichment_pending', 'stale', 'failed_retryable');

ALTER TABLE communication_event
  ADD COLUMN interaction_id uuid REFERENCES staff_interaction(id) ON DELETE SET NULL,
  ADD COLUMN source_type varchar(48),
  ADD COLUMN source_id uuid,
  ADD COLUMN source_sequence bigint CHECK (source_sequence IS NULL OR source_sequence > 0),
  ADD COLUMN request_key varchar(128),
  ADD COLUMN delivery_status varchar(24) NOT NULL DEFAULT 'recorded'
    CHECK (delivery_status IN (
      'draft', 'queued', 'sent', 'delivered', 'failed', 'bounced',
      'recorded', 'received'
    )),
  ADD CONSTRAINT communication_event_source_pair_check CHECK (
    (source_type IS NULL AND source_id IS NULL)
    OR (source_type IS NOT NULL AND source_id IS NOT NULL)
  );

CREATE UNIQUE INDEX communication_event_source_uidx
  ON communication_event(tenant_id, source_type, source_id)
  WHERE source_type IS NOT NULL AND source_id IS NOT NULL;

CREATE UNIQUE INDEX communication_event_interaction_sequence_uidx
  ON communication_event(tenant_id, interaction_id, source_sequence)
  WHERE interaction_id IS NOT NULL AND source_sequence IS NOT NULL;

CREATE UNIQUE INDEX communication_event_request_uidx
  ON communication_event(tenant_id, request_key)
  WHERE request_key IS NOT NULL;

CREATE TABLE staff_call_recording (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  interaction_id uuid NOT NULL REFERENCES staff_interaction(id) ON DELETE CASCADE,
  work_item_id uuid NOT NULL REFERENCES staff_work_item(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES student(id),
  uploaded_by uuid NOT NULL REFERENCES staff_member(id),
  request_key varchar(128) NOT NULL,
  file_name varchar(255) NOT NULL,
  mime_type varchar(120) NOT NULL,
  size_bytes integer NOT NULL CHECK (size_bytes BETWEEN 1 AND 10485760),
  storage_key varchar(1024) NOT NULL,
  sha256 char(64) NOT NULL,
  consent_confirmed boolean NOT NULL CHECK (consent_confirmed = true),
  status varchar(32) NOT NULL CHECK (
    status IN (
      'uploading', 'queued', 'transcribing', 'ready', 'upload_failed',
      'failed_retryable', 'dead_letter', 'pending_configuration'
    )
  ),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
  lease_owner varchar(160),
  lease_expires_at timestamptz,
  next_attempt_at timestamptz,
  last_error_code varchar(80),
  last_error_message varchar(500),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  uploaded_at timestamptz,
  transcribed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_call_recording_request_uidx UNIQUE (
    tenant_id, uploaded_by, request_key
  )
);

CREATE INDEX staff_call_recording_claim_idx
  ON staff_call_recording(status, next_attempt_at, created_at)
  WHERE status IN ('queued', 'transcribing', 'failed_retryable');

CREATE INDEX staff_call_recording_interaction_idx
  ON staff_call_recording(tenant_id, interaction_id, created_at DESC);

CREATE TABLE staff_call_transcript_revision (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  recording_id uuid NOT NULL REFERENCES staff_call_recording(id) ON DELETE CASCADE,
  version integer NOT NULL CHECK (version > 0),
  transcript text NOT NULL CHECK (char_length(transcript) > 0),
  language varchar(16),
  duration_seconds numeric(12, 3),
  segments jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(segments) = 'array'),
  provider varchar(48) NOT NULL,
  model varchar(160) NOT NULL,
  is_current boolean NOT NULL DEFAULT true,
  generated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, recording_id, version)
);

CREATE UNIQUE INDEX staff_call_transcript_current_uidx
  ON staff_call_transcript_revision(tenant_id, recording_id)
  WHERE is_current = true;

CREATE TABLE interaction_outcome_revision (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  interaction_id uuid NOT NULL REFERENCES staff_interaction(id) ON DELETE CASCADE,
  work_item_id uuid NOT NULL REFERENCES staff_work_item(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES student(id),
  version integer NOT NULL CHECK (version > 0),
  finality varchar(16) NOT NULL CHECK (finality IN ('provisional', 'final')),
  summary text NOT NULL,
  channel_results jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(channel_results) = 'array'),
  outcome_code varchar(48),
  resolution_code varchar(48),
  next_step text,
  follow_up_required boolean NOT NULL DEFAULT false,
  source_ids jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(source_ids) = 'array'),
  covered_source_version bigint NOT NULL CHECK (covered_source_version >= 0),
  confidence_milli integer CHECK (confidence_milli BETWEEN 0 AND 1000),
  provider varchar(48) NOT NULL,
  model varchar(160) NOT NULL,
  prompt_version varchar(160) NOT NULL,
  agent_run_id uuid REFERENCES agent_run(id),
  is_current boolean NOT NULL DEFAULT true,
  generated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, interaction_id, version)
);

CREATE UNIQUE INDEX interaction_outcome_current_uidx
  ON interaction_outcome_revision(tenant_id, interaction_id)
  WHERE is_current = true;

CREATE TABLE task_insight_revision (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  work_item_id uuid NOT NULL REFERENCES staff_work_item(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES student(id),
  version integer NOT NULL CHECK (version > 0),
  summary text NOT NULL,
  why_this_matters text NOT NULL,
  objective text NOT NULL,
  success_definition text NOT NULL,
  suggested_approach text NOT NULL,
  suggested_channel varchar(16)
    CHECK (suggested_channel IN ('email', 'sms', 'voice', 'portal')),
  source_ids jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(source_ids) = 'array'),
  source_revision bigint NOT NULL CHECK (source_revision >= 0),
  provider varchar(48) NOT NULL,
  model varchar(160) NOT NULL,
  prompt_version varchar(160) NOT NULL,
  agent_run_id uuid REFERENCES agent_run(id),
  is_current boolean NOT NULL DEFAULT true,
  generated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, work_item_id, version)
);

CREATE UNIQUE INDEX task_insight_current_uidx
  ON task_insight_revision(tenant_id, work_item_id)
  WHERE is_current = true;

CREATE TABLE student_summary_revision (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  version integer NOT NULL CHECK (version > 0),
  summary text NOT NULL,
  key_facts jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(key_facts) = 'array'),
  risks jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(risks) = 'array'),
  next_steps jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(next_steps) = 'array'),
  source_ids jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(source_ids) = 'array'),
  source_revision bigint NOT NULL CHECK (source_revision >= 0),
  provider varchar(48) NOT NULL,
  model varchar(160) NOT NULL,
  prompt_version varchar(160) NOT NULL,
  agent_run_id uuid REFERENCES agent_run(id),
  is_current boolean NOT NULL DEFAULT true,
  generated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, student_id, version)
);

CREATE UNIQUE INDEX student_summary_current_uidx
  ON student_summary_revision(tenant_id, student_id)
  WHERE is_current = true;

CREATE TABLE action_center_ai_job (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  purpose varchar(32) NOT NULL CHECK (
    purpose IN ('interaction_enrichment', 'student_summary', 'task_insight')
  ),
  dedupe_key varchar(240) NOT NULL,
  student_id uuid NOT NULL REFERENCES student(id),
  work_item_id uuid REFERENCES staff_work_item(id) ON DELETE CASCADE,
  interaction_id uuid REFERENCES staff_interaction(id) ON DELETE CASCADE,
  status varchar(24) NOT NULL CHECK (
    status IN (
      'pending', 'running', 'succeeded', 'failed_retryable',
      'dead_letter', 'cancelled'
    )
  ),
  requested_source_version bigint NOT NULL CHECK (requested_source_version >= 0),
  processing_source_version bigint CHECK (processing_source_version >= 0),
  covered_source_version bigint NOT NULL DEFAULT 0 CHECK (covered_source_version >= 0),
  base_summary_version integer CHECK (base_summary_version IS NULL OR base_summary_version > 0),
  not_before timestamptz NOT NULL DEFAULT now(),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 20),
  lease_owner varchar(160),
  lease_expires_at timestamptz,
  last_error_code varchar(80),
  last_error_message varchar(500),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CONSTRAINT action_center_ai_job_dedupe_uidx
    UNIQUE (tenant_id, purpose, dedupe_key),
  CHECK (covered_source_version <= requested_source_version),
  CHECK (
    (purpose = 'interaction_enrichment' AND interaction_id IS NOT NULL AND work_item_id IS NOT NULL)
    OR purpose = 'student_summary'
    OR (purpose = 'task_insight' AND work_item_id IS NOT NULL)
  )
);

CREATE INDEX action_center_ai_job_claim_idx
  ON action_center_ai_job(status, not_before, created_at)
  WHERE status IN ('pending', 'running', 'failed_retryable');

CREATE FUNCTION queue_staff_work_item_ai() RETURNS trigger AS $$
BEGIN
  INSERT INTO action_center_ai_job (
    id, tenant_id, purpose, dedupe_key, student_id, work_item_id,
    status, requested_source_version, covered_source_version,
    not_before, attempts, max_attempts, created_at, updated_at
  ) VALUES (
    gen_random_uuid(), NEW.tenant_id, 'task_insight',
    'work-item:' || NEW.id::text, NEW.student_id, NEW.id,
    'pending', NEW.version, 0,
    CASE WHEN TG_OP = 'INSERT' THEN now() ELSE now() + interval '5 minutes' END,
    0, 5, now(), now()
  )
  ON CONFLICT (tenant_id, purpose, dedupe_key)
  DO UPDATE SET
    requested_source_version = GREATEST(
      action_center_ai_job.requested_source_version,
      EXCLUDED.requested_source_version
    ),
    status = CASE
      WHEN action_center_ai_job.status = 'running' THEN 'running'
      ELSE 'pending'
    END,
    not_before = CASE
      WHEN action_center_ai_job.status IN ('succeeded', 'dead_letter', 'cancelled')
      THEN EXCLUDED.not_before
      ELSE LEAST(
        GREATEST(action_center_ai_job.not_before, EXCLUDED.not_before),
        action_center_ai_job.created_at + interval '15 minutes'
      )
    END,
    attempts = CASE
      WHEN action_center_ai_job.status = 'dead_letter' THEN 0
      ELSE action_center_ai_job.attempts
    END,
    created_at = CASE
      WHEN action_center_ai_job.status IN ('succeeded', 'dead_letter', 'cancelled')
      THEN now()
      ELSE action_center_ai_job.created_at
    END,
    completed_at = NULL,
    last_error_code = NULL,
    last_error_message = NULL,
    updated_at = now();

  IF TG_OP = 'INSERT' THEN
    INSERT INTO action_center_ai_job (
      id, tenant_id, purpose, dedupe_key, student_id, status,
      requested_source_version, covered_source_version,
      not_before, attempts, max_attempts, created_at, updated_at
    ) VALUES (
      gen_random_uuid(), NEW.tenant_id, 'student_summary',
      'student:' || NEW.student_id::text, NEW.student_id, 'pending',
      1, 0, now() + interval '5 minutes', 0, 5, now(), now()
    )
    ON CONFLICT (tenant_id, purpose, dedupe_key)
    DO UPDATE SET
      requested_source_version = action_center_ai_job.requested_source_version + 1,
      status = CASE
        WHEN action_center_ai_job.status = 'running' THEN 'running'
        ELSE 'pending'
      END,
      not_before = CASE
        WHEN action_center_ai_job.status IN ('succeeded', 'dead_letter', 'cancelled')
        THEN EXCLUDED.not_before
        ELSE LEAST(
          GREATEST(
            action_center_ai_job.not_before,
            EXCLUDED.not_before
          ),
          action_center_ai_job.created_at + interval '15 minutes'
        )
      END,
      attempts = CASE
        WHEN action_center_ai_job.status = 'dead_letter' THEN 0
        ELSE action_center_ai_job.attempts
      END,
      created_at = CASE
        WHEN action_center_ai_job.status IN ('succeeded', 'dead_letter', 'cancelled')
        THEN now()
        ELSE action_center_ai_job.created_at
      END,
      completed_at = NULL,
      updated_at = now();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER staff_work_item_ai_trigger
AFTER INSERT OR UPDATE OF title, description, status, priority, action_type,
  component, due_at, assignee_id, selected_channel, follow_up_at,
  blocker_code, blocker_detail, outcome_code, resolution_code, next_step
ON staff_work_item
FOR EACH ROW EXECUTE FUNCTION queue_staff_work_item_ai();

ALTER TABLE staff_work_item
  DROP CONSTRAINT IF EXISTS staff_work_item_source_type_check;

ALTER TABLE staff_work_item
  ADD CONSTRAINT staff_work_item_source_type_check CHECK (
    source_type IN (
      'onboarding', 'requirement', 'document', 'message', 'scheduled_rule'
    )
  );

CREATE TABLE staff_action_rule (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  code varchar(80) NOT NULL,
  name varchar(160) NOT NULL,
  description text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  signal_type varchar(32) NOT NULL CHECK (
    signal_type IN ('requirement_due', 'student_inactive')
  ),
  flow_kind varchar(16) CHECK (flow_kind IN ('enrollment', 'onboarding')),
  requirement_code varchar(120),
  lookahead_days integer CHECK (lookahead_days BETWEEN 0 AND 365),
  inactivity_days integer CHECK (inactivity_days BETWEEN 1 AND 365),
  cadence_minutes integer NOT NULL DEFAULT 60
    CHECK (cadence_minutes BETWEEN 5 AND 1440),
  component varchar(120) NOT NULL,
  priority varchar(16) NOT NULL CHECK (
    priority IN ('low', 'medium', 'high', 'urgent')
  ),
  action_type varchar(48) NOT NULL CHECK (
    action_type IN (
      'enrollment_follow_up', 'onboarding_assistance', 'document_review',
      'missing_information', 'external_verification', 'deadline_risk',
      'staff_decision', 'communication_response', 'blocked_dependency'
    )
  ),
  title_template varchar(240) NOT NULL,
  description_template text NOT NULL,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_by uuid REFERENCES staff_member(id),
  updated_by uuid REFERENCES staff_member(id),
  last_evaluated_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_action_rule_code_uidx UNIQUE (tenant_id, code),
  CONSTRAINT staff_action_rule_condition_check CHECK (
    (
      signal_type = 'requirement_due'
      AND lookahead_days IS NOT NULL
      AND inactivity_days IS NULL
    ) OR (
      signal_type = 'student_inactive'
      AND inactivity_days IS NOT NULL
      AND lookahead_days IS NULL
      AND requirement_code IS NULL
    )
  )
);

CREATE INDEX staff_action_rule_due_idx
  ON staff_action_rule(enabled, last_evaluated_at)
  WHERE enabled = true;

CREATE TABLE staff_action_rule_execution (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  rule_id uuid NOT NULL REFERENCES staff_action_rule(id),
  student_id uuid NOT NULL REFERENCES student(id),
  subject_id uuid,
  window_key varchar(240) NOT NULL,
  work_item_id uuid REFERENCES staff_work_item(id) ON DELETE SET NULL,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(evidence) = 'object'),
  matched_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_action_rule_execution_uidx UNIQUE (
    tenant_id, rule_id, student_id, window_key
  )
);

CREATE INDEX staff_action_rule_execution_work_idx
  ON staff_action_rule_execution(tenant_id, work_item_id)
  WHERE work_item_id IS NOT NULL;

INSERT INTO staff_action_rule (
  id, tenant_id, code, name, description, enabled, signal_type,
  flow_kind, requirement_code, lookahead_days, cadence_minutes,
  component, priority, action_type, title_template, description_template
)
SELECT
  gen_random_uuid(), tenant.id, 'transcript-due-soon',
  'Transcript due soon',
  'Create staff work when an incomplete official transcript is due soon.',
  true, 'requirement_due', 'enrollment', 'official_transcript', 3, 60,
  'Admissions', 'high', 'deadline_risk',
  'Follow up: transcript due soon',
  'The official transcript is incomplete and due within 3 days.'
FROM tenant;

CREATE TABLE staff_notification (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  staff_member_id uuid REFERENCES staff_member(id),
  team_component varchar(120),
  kind varchar(48) NOT NULL,
  title varchar(240) NOT NULL,
  body text NOT NULL,
  resource_type varchar(48),
  resource_id uuid,
  dedupe_key varchar(240) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_notification_target_check CHECK (
    staff_member_id IS NOT NULL OR team_component IS NOT NULL
  ),
  CONSTRAINT staff_notification_dedupe_uidx UNIQUE (tenant_id, dedupe_key)
);

CREATE INDEX staff_notification_inbox_idx
  ON staff_notification(tenant_id, staff_member_id, created_at DESC);

CREATE INDEX staff_notification_team_inbox_idx
  ON staff_notification(tenant_id, team_component, created_at DESC)
  WHERE team_component IS NOT NULL;

CREATE TABLE staff_notification_read_receipt (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  notification_id uuid NOT NULL REFERENCES staff_notification(id) ON DELETE CASCADE,
  staff_member_id uuid NOT NULL REFERENCES staff_member(id) ON DELETE CASCADE,
  read_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, notification_id, staff_member_id)
);

-- Existing portal inquiries become first-class Action Center communication
-- cases. The deterministic keys make this backfill idempotent at migration time.
INSERT INTO staff_work_item (
  id, tenant_id, student_id, key, title, description, status, priority,
  work_type, component, due_at, escalated, assignee_id, source_type, source_id,
  version, created_at, updated_at, action_type
)
SELECT
  gen_random_uuid(), inquiry.tenant_id, inquiry.student_id,
  'INQ-' || upper(substr(replace(inquiry.id::text, '-', ''), 1, 8)),
  inquiry.subject, inquiry.message,
  CASE inquiry.status
    WHEN 'resolved' THEN 'done'
    WHEN 'open' THEN 'in_progress'
    WHEN 'waiting_on_student' THEN 'follow_up_required'
    ELSE 'todo'
  END,
  inquiry.priority, 'communication', 'Enrollment Support', NULL, false,
  inquiry.assignee_id, 'message', inquiry.id, inquiry.version,
  inquiry.created_at, inquiry.updated_at, 'communication_response'
FROM student_inquiry inquiry
WHERE NOT EXISTS (
  SELECT 1 FROM staff_work_item item
  WHERE item.tenant_id = inquiry.tenant_id
    AND item.source_type = 'message'
    AND item.source_id = inquiry.id
);

INSERT INTO staff_interaction (
  id, tenant_id, student_id, work_item_id, objective, status,
  selected_channel, source_version, covered_source_version, version,
  quiet_until, last_activity_at, completed_at, created_at, updated_at,
  request_key
)
SELECT
  gen_random_uuid(), item.tenant_id, item.student_id, item.id,
  item.title,
  'enrichment_pending',
  'portal', 1, 0, 1,
  inquiry.updated_at + interval '5 minutes', inquiry.updated_at,
  CASE WHEN item.status = 'done' THEN inquiry.updated_at ELSE NULL END,
  inquiry.created_at, inquiry.updated_at,
  'migration:' || inquiry.id::text
FROM staff_work_item item
JOIN student_inquiry inquiry
  ON inquiry.tenant_id = item.tenant_id AND inquiry.id = item.source_id
WHERE item.source_type = 'message'
  AND NOT EXISTS (
    SELECT 1 FROM staff_interaction interaction
    WHERE interaction.tenant_id = item.tenant_id
      AND interaction.work_item_id = item.id
  );

INSERT INTO communication_event (
  id, tenant_id, student_id, channel, direction, subject, body_excerpt,
  metadata, resolution_status, occurred_at, created_at, interaction_id,
  source_type, source_id, source_sequence, delivery_status
)
SELECT
  gen_random_uuid(), inquiry.tenant_id, inquiry.student_id, 'portal', 'inbound',
  inquiry.subject, inquiry.message,
  jsonb_build_object('topicCode', inquiry.topic_code), 'unresolved',
  inquiry.created_at, inquiry.created_at, interaction.id,
  'student_inquiry', inquiry.id, 1, 'received'
FROM student_inquiry inquiry
JOIN staff_work_item item
  ON item.tenant_id = inquiry.tenant_id
 AND item.source_type = 'message'
 AND item.source_id = inquiry.id
JOIN staff_interaction interaction
  ON interaction.tenant_id = item.tenant_id
 AND interaction.work_item_id = item.id
WHERE NOT EXISTS (
  SELECT 1 FROM communication_event communication
  WHERE communication.tenant_id = inquiry.tenant_id
    AND communication.source_type = 'student_inquiry'
    AND communication.source_id = inquiry.id
);

-- Only replies delivered to the student are communication evidence. Internal
-- response notes remain in the immutable inquiry-reply history but are not sent
-- to the enrichment model as though the student saw them.
INSERT INTO communication_event (
  id, tenant_id, student_id, channel, direction, subject, body_excerpt,
  metadata, resolution_status, occurred_at, created_at, interaction_id,
  source_type, source_id, source_sequence, request_key, delivery_status
)
SELECT
  gen_random_uuid(), reply.tenant_id, reply.student_id, 'portal', 'outbound',
  'Reply: ' || inquiry.subject, reply.response_note,
  jsonb_build_object(
    'inquiryId', inquiry.id,
    'staffMemberId', reply.staff_member_id
  ),
  'unresolved', reply.created_at, reply.created_at, interaction.id,
  'student_inquiry_reply', reply.id,
  1 + row_number() OVER (
    PARTITION BY interaction.id ORDER BY reply.created_at, reply.id
  ),
  'inquiry-reply:' || reply.id::text, 'delivered'
FROM student_inquiry_reply reply
JOIN student_inquiry inquiry
  ON inquiry.tenant_id = reply.tenant_id AND inquiry.id = reply.inquiry_id
JOIN staff_work_item item
  ON item.tenant_id = inquiry.tenant_id
 AND item.source_type = 'message'
 AND item.source_id = inquiry.id
JOIN staff_interaction interaction
  ON interaction.tenant_id = item.tenant_id
 AND interaction.work_item_id = item.id
WHERE reply.notify_student = true
  AND NOT EXISTS (
    SELECT 1 FROM communication_event communication
    WHERE communication.tenant_id = reply.tenant_id
      AND communication.source_type = 'student_inquiry_reply'
      AND communication.source_id = reply.id
  );

UPDATE staff_interaction interaction
SET source_version = evidence.event_count,
    status = 'enrichment_pending',
    quiet_until = CASE
      WHEN interaction.completed_at IS NOT NULL THEN now()
      ELSE GREATEST(interaction.quiet_until, evidence.last_event_at + interval '5 minutes')
    END,
    last_activity_at = evidence.last_event_at,
    updated_at = now()
FROM (
  SELECT tenant_id, interaction_id, count(*)::bigint AS event_count,
         max(occurred_at) AS last_event_at
  FROM communication_event
  WHERE interaction_id IS NOT NULL
  GROUP BY tenant_id, interaction_id
) evidence
WHERE interaction.tenant_id = evidence.tenant_id
  AND interaction.id = evidence.interaction_id;

INSERT INTO action_center_ai_job (
  id, tenant_id, purpose, dedupe_key, student_id, work_item_id,
  interaction_id, status, requested_source_version, covered_source_version,
  not_before, attempts, max_attempts, created_at, updated_at
)
SELECT
  gen_random_uuid(), interaction.tenant_id, 'interaction_enrichment',
  'interaction:' || interaction.id::text, interaction.student_id,
  interaction.work_item_id, interaction.id, 'pending',
  interaction.source_version, interaction.covered_source_version,
  CASE
    WHEN interaction.completed_at IS NOT NULL THEN now()
    ELSE COALESCE(interaction.quiet_until, now())
  END,
  0, 5, now(), now()
FROM staff_interaction interaction
WHERE interaction.source_version > interaction.covered_source_version
ON CONFLICT (tenant_id, purpose, dedupe_key) DO NOTHING;
