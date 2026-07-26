-- Credential identity is intentionally separate from student domain data.
-- Students are linked through a single-use, hashed invitation so public
-- registration cannot manufacture an admission record or cross tenant
-- boundaries by guessing an email address.
CREATE TABLE student_identity_invitation (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  email_normalized varchar(254) NOT NULL
    CHECK (email_normalized = lower(email_normalized)),
  phone_e164 varchar(16) NOT NULL
    CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  token_hash char(64) NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  accepted_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (accepted_at IS NULL OR accepted_at >= created_at)
);
CREATE INDEX student_identity_invitation_lookup_idx
  ON student_identity_invitation(tenant_id, email_normalized, expires_at DESC);
CREATE UNIQUE INDEX student_identity_invitation_open_student_uidx
  ON student_identity_invitation(tenant_id, student_id)
  WHERE accepted_at IS NULL;

CREATE TABLE credential_account (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  email_normalized varchar(254) NOT NULL
    CHECK (email_normalized = lower(email_normalized)),
  phone_e164 varchar(16) NOT NULL
    CHECK (phone_e164 ~ '^\+[1-9][0-9]{7,14}$'),
  password_hash text NOT NULL,
  password_algorithm varchar(32) NOT NULL
    CHECK (password_algorithm IN ('scrypt-v1', 'argon2id-v1')),
  email_verified_at timestamptz,
  phone_verified_at timestamptz,
  status varchar(24) NOT NULL
    CHECK (status IN ('active', 'locked', 'disabled')),
  failed_sign_in_count smallint NOT NULL DEFAULT 0
    CHECK (failed_sign_in_count BETWEEN 0 AND 100),
  locked_until timestamptz,
  password_changed_at timestamptz NOT NULL DEFAULT now(),
  last_signed_in_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT credential_account_student_uidx
    UNIQUE (tenant_id, student_id),
  CONSTRAINT credential_account_email_uidx
    UNIQUE (tenant_id, email_normalized),
  CONSTRAINT credential_account_phone_uidx
    UNIQUE (tenant_id, phone_e164)
);

-- Only a SHA-256 digest of the opaque browser token is persisted. Revocation
-- and expiry are server-side, independent of the cookie lifetime.
CREATE TABLE auth_session (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL
    REFERENCES credential_account(id) ON DELETE CASCADE,
  token_hash char(64) NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);
CREATE INDEX auth_session_account_active_idx
  ON auth_session(account_id, expires_at DESC)
  WHERE revoked_at IS NULL;

-- Delivery is adapter-specific. The core stores only a bounded challenge
-- digest and lifecycle, so switching email/SMS vendors does not change the
-- student or credential schemas.
CREATE TABLE auth_verification_challenge (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL
    REFERENCES credential_account(id) ON DELETE CASCADE,
  channel varchar(16) NOT NULL CHECK (channel IN ('email', 'sms')),
  destination_normalized varchar(254) NOT NULL,
  token_hash char(64) NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  failed_attempt_count smallint NOT NULL DEFAULT 0
    CHECK (failed_attempt_count BETWEEN 0 AND 20),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);
CREATE INDEX auth_verification_challenge_account_idx
  ON auth_verification_challenge(account_id, channel, created_at DESC);
