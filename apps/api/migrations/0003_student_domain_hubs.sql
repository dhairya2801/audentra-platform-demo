ALTER TABLE program
  ADD COLUMN code varchar(32),
  ADD COLUMN degree varchar(120),
  ADD COLUMN total_credits smallint,
  ADD COLUMN description text;

UPDATE program
SET code = COALESCE(code, 'BS-CS'),
    degree = COALESCE(degree, 'Bachelor of Science'),
    total_credits = COALESCE(total_credits, 120),
    description = COALESCE(
      description,
      'Builds a foundation in software, algorithms, systems, data, and responsible computing.'
    );

ALTER TABLE program
  ALTER COLUMN code SET NOT NULL,
  ALTER COLUMN degree SET NOT NULL,
  ALTER COLUMN total_credits SET NOT NULL,
  ALTER COLUMN description SET NOT NULL,
  ADD CONSTRAINT program_total_credits_check
    CHECK (total_credits BETWEEN 1 AND 300);

CREATE UNIQUE INDEX program_tenant_code_uidx
  ON program(tenant_id, code);

CREATE TABLE course_catalog_version (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  code varchar(80) NOT NULL,
  effective_from date NOT NULL,
  effective_until date,
  status varchar(20) NOT NULL
    CHECK (status IN ('draft', 'active', 'retired')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT course_catalog_version_dates_check
    CHECK (effective_until IS NULL OR effective_until >= effective_from),
  CONSTRAINT course_catalog_version_tenant_code_uidx
    UNIQUE (tenant_id, code)
);

CREATE TABLE catalog_course (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  catalog_version_id uuid NOT NULL REFERENCES course_catalog_version(id),
  code varchar(32) NOT NULL,
  title varchar(180) NOT NULL,
  description text NOT NULL,
  credits numeric(4, 1) NOT NULL CHECK (credits > 0 AND credits <= 20),
  level smallint NOT NULL CHECK (level BETWEEN 0 AND 900),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT catalog_course_version_code_uidx
    UNIQUE (catalog_version_id, code)
);
CREATE INDEX catalog_course_search_idx
  ON catalog_course(tenant_id, code, title);

CREATE TABLE course_prerequisite (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  catalog_version_id uuid NOT NULL REFERENCES course_catalog_version(id),
  course_id uuid NOT NULL REFERENCES catalog_course(id),
  prerequisite_course_id uuid NOT NULL REFERENCES catalog_course(id),
  minimum_grade varchar(8),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (course_id, prerequisite_course_id),
  CHECK (course_id <> prerequisite_course_id)
);

CREATE TABLE program_requirement (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  program_id uuid NOT NULL REFERENCES program(id),
  catalog_version_id uuid NOT NULL REFERENCES course_catalog_version(id),
  course_id uuid NOT NULL REFERENCES catalog_course(id),
  category varchar(32) NOT NULL
    CHECK (category IN (
      'major_core',
      'math_science',
      'general_education',
      'elective'
    )),
  recommended_term smallint NOT NULL
    CHECK (recommended_term BETWEEN 1 AND 16),
  required boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT program_requirement_course_uidx
    UNIQUE (program_id, catalog_version_id, course_id)
);
CREATE INDEX program_requirement_program_idx
  ON program_requirement(tenant_id, program_id, recommended_term);

CREATE TABLE course_equivalency_rule (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  catalog_version_id uuid NOT NULL REFERENCES course_catalog_version(id),
  code varchar(120) NOT NULL,
  source_type varchar(32) NOT NULL
    CHECK (source_type IN (
      'ap',
      'ib',
      'dual_enrollment',
      'transfer',
      'transcript'
    )),
  source_code varchar(120) NOT NULL,
  minimum_score numeric(5, 2),
  minimum_grade varchar(8),
  minimum_credits numeric(4, 1),
  target_course_id uuid NOT NULL REFERENCES catalog_course(id),
  confidence numeric(4, 3) NOT NULL
    CHECK (confidence BETWEEN 0 AND 1),
  active boolean NOT NULL DEFAULT true,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT course_equivalency_rule_code_version_uidx
    UNIQUE (tenant_id, code, version)
);
CREATE INDEX course_equivalency_rule_match_idx
  ON course_equivalency_rule(
    tenant_id,
    catalog_version_id,
    source_type,
    source_code
  ) WHERE active;

CREATE TABLE student_transcript_credit (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  source_document_id uuid REFERENCES document_record(id),
  source_type varchar(32) NOT NULL
    CHECK (source_type IN (
      'ap',
      'ib',
      'dual_enrollment',
      'transfer',
      'transcript'
    )),
  source_code varchar(120),
  title varchar(180) NOT NULL,
  grade_or_score varchar(32),
  credits numeric(4, 1),
  institution_name varchar(200),
  evidence jsonb NOT NULL DEFAULT '{}',
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX student_transcript_credit_student_idx
  ON student_transcript_credit(tenant_id, student_id, created_at);

CREATE TABLE course_exemption_recommendation (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  program_id uuid NOT NULL REFERENCES program(id),
  catalog_version_id uuid NOT NULL REFERENCES course_catalog_version(id),
  transcript_credit_id uuid NOT NULL REFERENCES student_transcript_credit(id),
  target_course_id uuid NOT NULL REFERENCES catalog_course(id),
  equivalency_rule_id uuid NOT NULL REFERENCES course_equivalency_rule(id),
  status varchar(24) NOT NULL
    CHECK (status IN (
      'suggested',
      'needs_review',
      'approved',
      'denied',
      'superseded'
    )),
  confidence numeric(4, 3) NOT NULL
    CHECK (confidence BETWEEN 0 AND 1),
  rationale text NOT NULL,
  decided_by uuid,
  decided_at timestamptz,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT course_exemption_source_target_uidx
    UNIQUE (
      tenant_id,
      student_id,
      transcript_credit_id,
      target_course_id,
      equivalency_rule_id
    ),
  CHECK (
    (status IN ('approved', 'denied') AND decided_at IS NOT NULL)
    OR status NOT IN ('approved', 'denied')
  )
);
CREATE INDEX course_exemption_review_queue_idx
  ON course_exemption_recommendation(tenant_id, status, created_at)
  WHERE status IN ('suggested', 'needs_review');

CREATE TABLE student_financial_summary (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  academic_year varchar(16) NOT NULL,
  cost_of_attendance_cents integer NOT NULL
    CHECK (cost_of_attendance_cents >= 0),
  external_payments_cents integer NOT NULL DEFAULT 0
    CHECK (external_payments_cents >= 0),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, student_id, academic_year)
);

CREATE TABLE student_financial_award (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  academic_year varchar(16) NOT NULL,
  source varchar(24) NOT NULL
    CHECK (source IN ('federal', 'state', 'institutional', 'private')),
  name varchar(180) NOT NULL,
  type varchar(24) NOT NULL
    CHECK (type IN ('grant', 'scholarship', 'loan', 'work_study')),
  offered_amount_cents integer NOT NULL CHECK (offered_amount_cents >= 0),
  accepted_amount_cents integer NOT NULL CHECK (accepted_amount_cents >= 0),
  status varchar(20) NOT NULL
    CHECK (status IN ('offered', 'accepted', 'declined', 'pending')),
  requires_action boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (accepted_amount_cents <= offered_amount_cents)
);
CREATE INDEX student_financial_award_student_idx
  ON student_financial_award(tenant_id, student_id, academic_year);

CREATE TABLE financial_document_requirement (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  code varchar(80) NOT NULL,
  title varchar(180) NOT NULL,
  description text NOT NULL,
  status varchar(24) NOT NULL
    CHECK (status IN (
      'not_started',
      'submitted',
      'under_review',
      'verified',
      'action_required'
    )),
  due_at timestamptz,
  document_id uuid REFERENCES document_record(id),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT financial_document_requirement_code_uidx
    UNIQUE (tenant_id, student_id, code)
);

CREATE TABLE student_payment_plan (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  academic_year varchar(16) NOT NULL,
  name varchar(180) NOT NULL,
  installment_count smallint NOT NULL
    CHECK (installment_count BETWEEN 2 AND 24),
  enrollment_fee_cents integer NOT NULL
    CHECK (enrollment_fee_cents >= 0),
  status varchar(20) NOT NULL
    CHECK (status IN ('available', 'enrolled', 'cancelled')),
  enrolled_at timestamptz,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX student_payment_plan_one_enrolled_idx
  ON student_payment_plan(tenant_id, student_id, academic_year)
  WHERE status = 'enrolled';

CREATE TABLE student_sap_status (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  academic_year varchar(16) NOT NULL,
  status varchar(24) NOT NULL
    CHECK (status IN (
      'meeting',
      'warning',
      'probation',
      'not_meeting',
      'appeal_pending'
    )),
  cumulative_gpa numeric(4, 3) NOT NULL CHECK (cumulative_gpa BETWEEN 0 AND 4),
  minimum_gpa numeric(4, 3) NOT NULL CHECK (minimum_gpa BETWEEN 0 AND 4),
  completion_rate_percent numeric(5, 2) NOT NULL
    CHECK (completion_rate_percent BETWEEN 0 AND 100),
  minimum_completion_rate_percent numeric(5, 2) NOT NULL
    CHECK (minimum_completion_rate_percent BETWEEN 0 AND 100),
  attempted_credits numeric(6, 1) NOT NULL CHECK (attempted_credits >= 0),
  maximum_attempted_credits numeric(6, 1) NOT NULL
    CHECK (maximum_attempted_credits > 0),
  calculated_at timestamptz NOT NULL DEFAULT now(),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, student_id, academic_year)
);

CREATE TABLE campus_event (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  title varchar(180) NOT NULL,
  description text NOT NULL,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  location varchar(180) NOT NULL,
  category varchar(24) NOT NULL
    CHECK (category IN ('academic', 'social', 'career', 'wellness', 'athletics')),
  featured boolean NOT NULL DEFAULT false,
  accent varchar(16) NOT NULL
    CHECK (accent IN ('gold', 'navy', 'blue', 'coral')),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ends_at > starts_at)
);
CREATE INDEX campus_event_feed_idx
  ON campus_event(tenant_id, active, starts_at);

CREATE TABLE student_club (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  name varchar(180) NOT NULL,
  category varchar(100) NOT NULL,
  description text NOT NULL,
  contact_name varchar(180) NOT NULL,
  contact_role varchar(120) NOT NULL,
  contact_channel varchar(200) NOT NULL,
  latest_update text NOT NULL,
  next_activity varchar(240),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT student_club_tenant_name_uidx UNIQUE (tenant_id, name)
);
