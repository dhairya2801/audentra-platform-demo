-- Student OpenID Connect login state and stable federated identity mappings.
-- Authorization state and browser binding values are stored only as SHA-256
-- digests. The PKCE verifier is short-lived, single-use transaction material.
CREATE TABLE oidc_authorization_transaction (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  provider varchar(24) NOT NULL CHECK (provider IN ('google', 'microsoft')),
  state_hash char(64) NOT NULL UNIQUE,
  binding_hash char(64) NOT NULL,
  nonce_hash char(64) NOT NULL,
  code_verifier varchar(128) NOT NULL
    CHECK (char_length(code_verifier) BETWEEN 43 AND 128),
  return_to varchar(2048) NOT NULL CHECK (return_to LIKE '/%'),
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (consumed_at IS NULL OR consumed_at >= created_at)
);
CREATE INDEX oidc_authorization_transaction_expiry_idx
  ON oidc_authorization_transaction(expires_at)
  WHERE consumed_at IS NULL;

ALTER TABLE credential_account
  ADD CONSTRAINT credential_account_tenant_account_uidx
  UNIQUE (tenant_id, id);

CREATE TABLE federated_identity (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL,
  credential_account_id uuid NOT NULL,
  provider varchar(24) NOT NULL CHECK (provider IN ('google', 'microsoft')),
  issuer varchar(512) NOT NULL,
  subject varchar(255) NOT NULL,
  email_normalized varchar(254) NOT NULL
    CHECK (email_normalized = lower(email_normalized)),
  last_authenticated_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT federated_identity_account_fk
    FOREIGN KEY (tenant_id, credential_account_id)
    REFERENCES credential_account(tenant_id, id) ON DELETE CASCADE,
  CONSTRAINT federated_identity_subject_uidx
    UNIQUE (tenant_id, provider, issuer, subject),
  CONSTRAINT federated_identity_account_provider_uidx
    UNIQUE (tenant_id, credential_account_id, provider)
);
CREATE INDEX federated_identity_account_idx
  ON federated_identity(tenant_id, credential_account_id);

ALTER TABLE auth_session
  ADD COLUMN authentication_method varchar(24) NOT NULL DEFAULT 'credentials'
    CHECK (authentication_method IN ('credentials', 'oidc')),
  ADD COLUMN identity_provider varchar(24)
    CHECK (identity_provider IN ('google', 'microsoft')),
  ADD CONSTRAINT auth_session_identity_method_check CHECK (
    (authentication_method = 'credentials' AND identity_provider IS NULL)
    OR (authentication_method = 'oidc' AND identity_provider IS NOT NULL)
  );
