CREATE TABLE tenant (
  id uuid PRIMARY KEY,
  name varchar(180) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE person (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  preferred_name varchar(120),
  first_name varchar(120) NOT NULL,
  last_name varchar(120) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX person_tenant_idx ON person(tenant_id);

CREATE TABLE student (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  person_id uuid NOT NULL REFERENCES person(id),
  class_year smallint NOT NULL CHECK (class_year BETWEEN 2000 AND 2200),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT student_tenant_person_uidx UNIQUE (tenant_id, person_id)
);

CREATE TABLE campus (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  name varchar(180) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE academic_term (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  name varchar(180) NOT NULL,
  starts_on date NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE program (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  name varchar(180) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE admission_offer (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  program_id uuid NOT NULL REFERENCES program(id),
  academic_term_id uuid NOT NULL REFERENCES academic_term(id),
  campus_id uuid NOT NULL REFERENCES campus(id),
  response_deadline date NOT NULL,
  deposit_amount_cents integer NOT NULL CHECK (deposit_amount_cents >= 0),
  status varchar(24) NOT NULL
    CHECK (status IN ('offered', 'accepted', 'declined', 'expired')),
  accepted_at timestamptz,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (status = 'accepted' AND accepted_at IS NOT NULL)
    OR (status <> 'accepted')
  )
);
CREATE INDEX admission_offer_student_idx
  ON admission_offer(tenant_id, student_id, created_at DESC);

CREATE TABLE journey_definition_version (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  code varchar(100) NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  active integer NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT journey_definition_version_uidx
    UNIQUE (tenant_id, code, version)
);

CREATE TABLE requirement_definition_version (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  code varchar(100) NOT NULL,
  title varchar(180) NOT NULL,
  description text NOT NULL,
  blocking integer NOT NULL DEFAULT 1 CHECK (blocking IN (0, 1)),
  display_order integer NOT NULL CHECK (display_order >= 0),
  depends_on_codes text[] NOT NULL DEFAULT '{}',
  due_offset_days integer,
  version integer NOT NULL CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT requirement_definition_version_uidx
    UNIQUE (tenant_id, code, version)
);

CREATE TABLE journey_requirement_definition (
  journey_definition_version_id uuid NOT NULL
    REFERENCES journey_definition_version(id),
  requirement_definition_version_id uuid NOT NULL
    REFERENCES requirement_definition_version(id),
  PRIMARY KEY (
    journey_definition_version_id,
    requirement_definition_version_id
  )
);

CREATE TABLE enrollment_journey (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  offer_id uuid NOT NULL REFERENCES admission_offer(id),
  journey_definition_version_id uuid NOT NULL
    REFERENCES journey_definition_version(id),
  status varchar(32) NOT NULL CHECK (
    status IN (
      'created',
      'in_progress',
      'ready_for_review',
      'submitted',
      'on_hold',
      'completed',
      'cancelled'
    )
  ),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT enrollment_journey_offer_uidx UNIQUE (tenant_id, offer_id)
);

CREATE TABLE student_requirement (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  journey_id uuid NOT NULL REFERENCES enrollment_journey(id),
  requirement_definition_version_id uuid NOT NULL
    REFERENCES requirement_definition_version(id),
  status varchar(32) NOT NULL CHECK (
    status IN (
      'not_applicable',
      'blocked',
      'ready',
      'in_progress',
      'submitted',
      'under_review',
      'completed',
      'waived',
      'rejected',
      'expired'
    )
  ),
  due_at timestamptz,
  progress_percent smallint NOT NULL DEFAULT 0
    CHECK (progress_percent BETWEEN 0 AND 100),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT student_requirement_definition_uidx UNIQUE (
    tenant_id,
    journey_id,
    requirement_definition_version_id
  )
);
CREATE INDEX student_requirement_journey_idx
  ON student_requirement(tenant_id, journey_id);

CREATE TABLE audit_event (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL,
  actor_type varchar(40) NOT NULL,
  actor_id uuid NOT NULL,
  student_id uuid,
  action varchar(120) NOT NULL,
  resource_type varchar(80) NOT NULL,
  resource_id uuid NOT NULL,
  authorization_basis varchar(120) NOT NULL,
  request_id varchar(128) NOT NULL,
  correlation_id varchar(128) NOT NULL,
  metadata jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_event_tenant_resource_idx
  ON audit_event(tenant_id, resource_type, resource_id, occurred_at DESC);

CREATE FUNCTION prevent_audit_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'audit_event is append-only';
END;
$$;

CREATE TRIGGER audit_event_append_only
BEFORE UPDATE OR DELETE ON audit_event
FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();

CREATE TABLE outbox_event (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL,
  event_name varchar(120) NOT NULL,
  aggregate_type varchar(80) NOT NULL,
  aggregate_id uuid NOT NULL,
  aggregate_version integer NOT NULL CHECK (aggregate_version > 0),
  occurred_at timestamptz NOT NULL,
  actor_type varchar(40) NOT NULL,
  actor_id uuid NOT NULL,
  correlation_id varchar(128) NOT NULL,
  causation_id varchar(128) NOT NULL,
  payload jsonb NOT NULL,
  published_at timestamptz,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  locked_at timestamptz,
  locked_by varchar(128),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX outbox_event_pending_idx
  ON outbox_event(published_at, next_attempt_at, created_at)
  WHERE published_at IS NULL;

CREATE TABLE activity_event (
  tenant_id uuid NOT NULL,
  event_id uuid NOT NULL,
  event_name varchar(120) NOT NULL,
  occurred_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  actor_type varchar(40) NOT NULL,
  actor_id uuid NOT NULL,
  student_id uuid,
  session_id varchar(128) NOT NULL,
  page_instance_id varchar(128) NOT NULL,
  correlation_id varchar(128),
  trust_level varchar(40) NOT NULL DEFAULT 'client_signal',
  application_version varchar(80) NOT NULL,
  properties jsonb NOT NULL,
  PRIMARY KEY (tenant_id, event_id)
);
CREATE INDEX activity_event_student_occurred_idx
  ON activity_event(tenant_id, student_id, occurred_at DESC);

CREATE TABLE idempotency_record (
  tenant_id uuid NOT NULL,
  actor_id uuid NOT NULL,
  operation varchar(120) NOT NULL,
  idempotency_key varchar(128) NOT NULL,
  request_hash varchar(64) NOT NULL,
  response_status integer NOT NULL,
  response_body jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)
);
CREATE INDEX idempotency_record_expiry_idx ON idempotency_record(expires_at);

CREATE TABLE student_portal_projection (
  tenant_id uuid NOT NULL,
  student_id uuid NOT NULL,
  projection_version bigint NOT NULL CHECK (projection_version > 0),
  dashboard jsonb NOT NULL,
  source_updated_at timestamptz NOT NULL,
  projected_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, student_id)
);

CREATE TABLE projection_event_receipt (
  event_id uuid NOT NULL,
  consumer_name varchar(128) NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (event_id, consumer_name)
);
