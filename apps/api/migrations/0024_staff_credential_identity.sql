-- Staff credentials are separate from operational staff profiles. A staff
-- member must already be provisioned by the institution before an account can
-- be claimed with the private invitation code.
CREATE TABLE staff_credential_account (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  staff_member_id uuid NOT NULL REFERENCES staff_member(id) ON DELETE CASCADE,
  password_hash text NOT NULL,
  password_algorithm varchar(32) NOT NULL
    CHECK (password_algorithm IN ('scrypt-v1', 'argon2id-v1')),
  status varchar(24) NOT NULL
    CHECK (status IN ('active', 'locked', 'disabled')),
  failed_sign_in_count smallint NOT NULL DEFAULT 0
    CHECK (failed_sign_in_count BETWEEN 0 AND 100),
  locked_until timestamptz,
  password_changed_at timestamptz NOT NULL DEFAULT now(),
  last_signed_in_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT staff_credential_account_member_uidx
    UNIQUE (tenant_id, staff_member_id)
);

CREATE INDEX staff_credential_account_tenant_status_idx
  ON staff_credential_account(tenant_id, status, updated_at DESC);

CREATE TABLE staff_auth_session (
  id uuid PRIMARY KEY,
  account_id uuid NOT NULL
    REFERENCES staff_credential_account(id) ON DELETE CASCADE,
  token_hash char(64) NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE INDEX staff_auth_session_account_active_idx
  ON staff_auth_session(account_id, expires_at DESC)
  WHERE revoked_at IS NULL;
