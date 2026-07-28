CREATE TABLE immunization_policy_version (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  code varchar(80) NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  name varchar(180) NOT NULL,
  status varchar(20) NOT NULL
    CHECK (status IN ('draft', 'published', 'retired')),
  effective_from date NOT NULL,
  effective_until date,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT immunization_policy_version_code_uidx
    UNIQUE (tenant_id, code, version),
  CONSTRAINT immunization_policy_version_dates_check
    CHECK (effective_until IS NULL OR effective_until >= effective_from)
);

CREATE UNIQUE INDEX immunization_policy_one_published_idx
  ON immunization_policy_version(tenant_id)
  WHERE status = 'published';

CREATE TABLE immunization_requirement_rule (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  policy_version_id uuid NOT NULL REFERENCES immunization_policy_version(id),
  code varchar(80) NOT NULL,
  name varchar(180) NOT NULL,
  description text NOT NULL,
  required boolean NOT NULL DEFAULT true,
  dose_count smallint CHECK (dose_count IS NULL OR dose_count > 0),
  validity_days integer CHECK (validity_days IS NULL OR validity_days > 0),
  applies_when jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence_criteria jsonb NOT NULL DEFAULT '{}'::jsonb,
  display_order smallint NOT NULL DEFAULT 0,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT immunization_requirement_rule_code_uidx
    UNIQUE (policy_version_id, code)
);

CREATE INDEX immunization_requirement_rule_policy_idx
  ON immunization_requirement_rule(
    tenant_id,
    policy_version_id,
    display_order
  ) WHERE active;

CREATE TABLE student_immunization_evaluation (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  source_document_id uuid NOT NULL REFERENCES document_record(id),
  policy_version_id uuid NOT NULL REFERENCES immunization_policy_version(id),
  result jsonb NOT NULL,
  generated_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT student_immunization_evaluation_document_policy_uidx
    UNIQUE (tenant_id, student_id, source_document_id, policy_version_id)
);

CREATE INDEX student_immunization_evaluation_student_idx
  ON student_immunization_evaluation(tenant_id, student_id, generated_at DESC);
