ALTER TABLE requirement_definition_version
  ADD COLUMN submission_type varchar(32) NOT NULL DEFAULT 'none',
  ADD COLUMN responsible_office varchar(180)
    NOT NULL DEFAULT 'Enrollment Services';

UPDATE requirement_definition_version
SET submission_type = CASE code
      WHEN 'profile_verification' THEN 'form'
      WHEN 'identity_document' THEN 'document'
      WHEN 'enrollment_deposit' THEN 'payment'
      ELSE submission_type
    END,
    responsible_office = CASE code
      WHEN 'profile_verification' THEN 'Enrollment Services'
      WHEN 'identity_document' THEN 'Registrar'
      WHEN 'enrollment_deposit' THEN 'Student Accounts'
      ELSE responsible_office
    END;

CREATE TABLE student_onboarding (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  status varchar(24) NOT NULL
    CHECK (status IN ('not_started', 'in_progress', 'completed')),
  current_step varchar(32) NOT NULL
    CHECK (current_step IN (
      'offer',
      'about_you',
      'housing',
      'campus_life',
      'emergency_contacts',
      'other_records',
      'family_permissions',
      'review_and_sign',
      'deposit'
    )),
  completed_steps text[] NOT NULL DEFAULT '{}',
  payload jsonb NOT NULL DEFAULT '{}',
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, student_id),
  CHECK (
    (status = 'completed' AND completed_at IS NOT NULL)
    OR (status <> 'completed' AND completed_at IS NULL)
  )
);

CREATE TABLE student_message (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  subject varchar(240) NOT NULL,
  body text NOT NULL,
  sender_name varchar(180) NOT NULL,
  sent_at timestamptz NOT NULL,
  read_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX student_message_inbox_idx
  ON student_message(tenant_id, student_id, sent_at DESC);

CREATE TABLE document_record (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  file_name varchar(255) NOT NULL,
  mime_type varchar(80) NOT NULL CHECK (
    mime_type IN ('application/pdf', 'image/jpeg', 'image/png')
  ),
  size_bytes integer NOT NULL CHECK (
    size_bytes BETWEEN 1 AND 10485760
  ),
  category varchar(40) NOT NULL CHECK (
    category IN ('identity', 'residency', 'transcript', 'other')
  ),
  status varchar(40) NOT NULL CHECK (
    status IN (
      'placeholder',
      'uploaded',
      'under_review',
      'accepted',
      'rejected'
    )
  ),
  storage_provider varchar(40) NOT NULL DEFAULT 'local_placeholder',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX document_record_student_idx
  ON document_record(tenant_id, student_id, created_at DESC);

CREATE TABLE student_appointment (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  type varchar(60) NOT NULL CHECK (
    type IN (
      'admissions_counseling',
      'financial_aid',
      'enrollment_support'
    )
  ),
  starts_at timestamptz NOT NULL,
  notes text,
  status varchar(32) NOT NULL CHECK (
    status IN ('scheduled', 'cancelled', 'completed')
  ),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX student_appointment_student_idx
  ON student_appointment(tenant_id, student_id, starts_at);

CREATE TABLE payment_transaction (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  offer_id uuid NOT NULL REFERENCES admission_offer(id),
  type varchar(40) NOT NULL CHECK (type = 'enrollment_deposit'),
  amount_cents integer NOT NULL CHECK (amount_cents > 0),
  status varchar(32) NOT NULL CHECK (
    status IN ('succeeded', 'failed', 'refunded')
  ),
  processor varchar(40) NOT NULL CHECK (processor = 'dummy'),
  processor_reference varchar(128) NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX payment_transaction_student_idx
  ON payment_transaction(tenant_id, student_id, created_at DESC);
CREATE UNIQUE INDEX payment_transaction_successful_deposit_uidx
  ON payment_transaction(tenant_id, student_id, offer_id, type)
  WHERE status = 'succeeded';

CREATE TABLE student_profile (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  preferred_name varchar(120) NOT NULL,
  pronouns varchar(80),
  mobile_phone varchar(32),
  communication_preference varchar(16) NOT NULL
    CHECK (communication_preference IN ('email', 'sms')),
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, student_id)
);

CREATE TABLE help_article (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  category varchar(40) NOT NULL CHECK (
    category IN ('getting_started', 'documents', 'payments', 'support')
  ),
  question varchar(240) NOT NULL,
  answer text NOT NULL,
  sort_order integer NOT NULL CHECK (sort_order >= 0),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX help_article_tenant_idx
  ON help_article(tenant_id, sort_order);

INSERT INTO student_onboarding (
  tenant_id, student_id, status, current_step, completed_steps, payload, version
)
SELECT tenant_id, id, 'not_started', 'offer', '{}', '{}'::jsonb, 1
FROM student
ON CONFLICT DO NOTHING;

INSERT INTO student_profile (
  tenant_id,
  student_id,
  preferred_name,
  communication_preference,
  version
)
SELECT
  s.tenant_id,
  s.id,
  COALESCE(p.preferred_name, p.first_name),
  'email',
  1
FROM student s
JOIN person p ON p.id = s.person_id AND p.tenant_id = s.tenant_id
ON CONFLICT DO NOTHING;
