CREATE TABLE staff_managed_configuration_version (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  kind varchar(32) NOT NULL
    CHECK (kind IN ('journeys', 'campus_life', 'academics')),
  version integer NOT NULL CHECK (version > 0),
  yaml text NOT NULL,
  document jsonb NOT NULL,
  record_count integer NOT NULL CHECK (record_count >= 0),
  change_summary varchar(500),
  created_by uuid NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_managed_configuration_version_uidx
    UNIQUE (tenant_id, kind, version)
);

CREATE UNIQUE INDEX staff_managed_configuration_active_uidx
  ON staff_managed_configuration_version(tenant_id, kind)
  WHERE active = true;

CREATE TABLE student_experience_update (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  publication_id uuid NOT NULL REFERENCES staff_managed_configuration_version(id),
  requirement_id uuid REFERENCES student_requirement(id),
  source_key varchar(160) NOT NULL,
  kind varchar(32) NOT NULL
    CHECK (kind IN ('onboarding', 'enrollment', 'academics', 'campus_life')),
  title varchar(180) NOT NULL,
  description text NOT NULL,
  status varchar(24) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'deferred', 'acknowledged')),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  acknowledged_at timestamptz,
  deferred_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT student_experience_update_source_uidx
    UNIQUE (tenant_id, student_id, publication_id, source_key),
  CHECK (
    (status = 'pending' AND acknowledged_at IS NULL AND deferred_at IS NULL)
    OR (status = 'deferred' AND acknowledged_at IS NULL AND deferred_at IS NOT NULL)
    OR (status = 'acknowledged' AND acknowledged_at IS NOT NULL)
  )
);

CREATE INDEX student_experience_update_student_idx
  ON student_experience_update(tenant_id, student_id, status, created_at DESC)
  WHERE status IN ('pending', 'deferred');
