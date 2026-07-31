CREATE TABLE staff_member (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  display_name varchar(160) NOT NULL,
  email_normalized varchar(320) NOT NULL,
  component varchar(120) NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_member_tenant_email_uidx
    UNIQUE (tenant_id, email_normalized)
);

CREATE INDEX staff_member_tenant_component_idx
  ON staff_member(tenant_id, component, display_name)
  WHERE active = true;

CREATE TABLE staff_work_item (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  key varchar(40) NOT NULL,
  title varchar(240) NOT NULL,
  description text NOT NULL,
  status varchar(24) NOT NULL CHECK (
    status IN ('todo', 'in_progress', 'done')
  ),
  priority varchar(16) NOT NULL CHECK (
    priority IN ('urgent', 'high', 'medium', 'low')
  ),
  work_type varchar(32) NOT NULL CHECK (
    work_type IN ('enrollment', 'document_review', 'communication')
  ),
  component varchar(120) NOT NULL,
  due_at timestamptz,
  escalated boolean NOT NULL DEFAULT false,
  assignee_id uuid REFERENCES staff_member(id),
  source_type varchar(24) CHECK (
    source_type IN ('onboarding', 'requirement', 'document', 'message')
  ),
  source_id uuid,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_work_item_tenant_key_uidx UNIQUE (tenant_id, key),
  CONSTRAINT staff_work_item_source_pair_check CHECK (
    (source_type IS NULL AND source_id IS NULL)
    OR (source_type IS NOT NULL AND source_id IS NOT NULL)
  )
);

CREATE UNIQUE INDEX staff_work_item_source_uidx
  ON staff_work_item(tenant_id, source_type, source_id)
  WHERE source_type IS NOT NULL AND source_id IS NOT NULL;

CREATE INDEX staff_work_item_board_idx
  ON staff_work_item(tenant_id, status, priority, due_at, updated_at DESC);

CREATE INDEX staff_work_item_student_idx
  ON staff_work_item(tenant_id, student_id, updated_at DESC);

CREATE TABLE staff_work_log (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  work_item_id uuid NOT NULL REFERENCES staff_work_item(id) ON DELETE CASCADE,
  actor_type varchar(24) NOT NULL CHECK (
    actor_type IN ('staff', 'system')
  ),
  actor_id uuid,
  actor_name varchar(160) NOT NULL,
  action varchar(48) NOT NULL CHECK (
    action IN (
      'created',
      'status_changed',
      'assigned',
      'escalated',
      'commented',
      'document_decided',
      'student_preferences_updated'
    )
  ),
  message text NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX staff_work_log_item_idx
  ON staff_work_log(tenant_id, work_item_id, occurred_at DESC);

CREATE FUNCTION prevent_staff_work_log_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'staff_work_log is append-only';
END;
$$;

CREATE TRIGGER staff_work_log_append_only
BEFORE UPDATE OR DELETE ON staff_work_log
FOR EACH ROW EXECUTE FUNCTION prevent_staff_work_log_mutation();
