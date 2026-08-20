-- Tenant-bound institutional SSO and delegated staff mailbox integration.
-- Provider credentials and token-encryption keys remain runtime secrets; this
-- migration stores only tenant policy, opaque/encrypted credentials, cursors,
-- and auditable delivery state.

CREATE TABLE tenant_identity_provider (
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  provider varchar(16) NOT NULL CHECK (provider IN ('google', 'microsoft')),
  enabled boolean NOT NULL DEFAULT false,
  google_hosted_domain varchar(253),
  microsoft_tenant_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, provider),
  CHECK (
    (provider = 'google' AND google_hosted_domain IS NOT NULL
      AND microsoft_tenant_id IS NULL)
    OR
    (provider = 'microsoft' AND microsoft_tenant_id IS NOT NULL
      AND google_hosted_domain IS NULL)
  )
);

CREATE TABLE staff_federated_identity (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  staff_member_id uuid NOT NULL REFERENCES staff_member(id) ON DELETE CASCADE,
  provider varchar(16) NOT NULL CHECK (provider IN ('google', 'microsoft')),
  provider_subject varchar(255) NOT NULL,
  provider_tenant varchar(255) NOT NULL,
  email_normalized varchar(320) NOT NULL,
  last_signed_in_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, provider, provider_subject),
  UNIQUE (tenant_id, staff_member_id, provider)
);

-- Existing credential sessions are backfilled to their operational staff
-- identity. Federated sessions have no credential account, while every
-- session always has one tenant-scoped staff member.
ALTER TABLE staff_auth_session
  ADD COLUMN tenant_id uuid REFERENCES tenant(id),
  ADD COLUMN staff_member_id uuid REFERENCES staff_member(id),
  ADD COLUMN authentication_method varchar(16) NOT NULL DEFAULT 'credentials'
    CHECK (authentication_method IN ('credentials', 'google', 'microsoft')),
  ADD COLUMN federated_identity_id uuid
    REFERENCES staff_federated_identity(id) ON DELETE CASCADE;

UPDATE staff_auth_session session
SET tenant_id = account.tenant_id,
    staff_member_id = account.staff_member_id
FROM staff_credential_account account
WHERE account.id = session.account_id;

ALTER TABLE staff_auth_session
  ALTER COLUMN tenant_id SET NOT NULL,
  ALTER COLUMN staff_member_id SET NOT NULL,
  ALTER COLUMN account_id DROP NOT NULL,
  ADD CONSTRAINT staff_auth_session_identity_check CHECK (
    (authentication_method = 'credentials'
      AND account_id IS NOT NULL AND federated_identity_id IS NULL)
    OR
    (authentication_method IN ('google', 'microsoft')
      AND account_id IS NULL AND federated_identity_id IS NOT NULL)
  );

CREATE INDEX staff_auth_session_member_active_idx
  ON staff_auth_session(tenant_id, staff_member_id, expires_at DESC)
  WHERE revoked_at IS NULL;

CREATE TABLE oauth_transaction (
  id uuid PRIMARY KEY,
  state_hash char(64) NOT NULL UNIQUE,
  flow_type varchar(24) NOT NULL
    CHECK (flow_type IN ('staff_sso', 'mailbox_connect')),
  provider varchar(16) NOT NULL CHECK (provider IN ('google', 'microsoft')),
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  tenant_slug varchar(80) NOT NULL,
  expected_provider_tenant varchar(255) NOT NULL,
  initiating_staff_member_id uuid REFERENCES staff_member(id) ON DELETE CASCADE,
  mailbox_kind varchar(16) CHECK (mailbox_kind IN ('personal', 'shared')),
  target_mailbox_address varchar(320),
  code_verifier varchar(128) NOT NULL,
  nonce varchar(128) NOT NULL,
  redirect_uri text NOT NULL,
  return_path text NOT NULL,
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (expires_at > created_at),
  CHECK (
    (flow_type = 'staff_sso' AND initiating_staff_member_id IS NULL
      AND mailbox_kind IS NULL AND target_mailbox_address IS NULL)
    OR
    (flow_type = 'mailbox_connect' AND initiating_staff_member_id IS NOT NULL
      AND mailbox_kind IS NOT NULL AND target_mailbox_address IS NOT NULL)
  )
);

CREATE INDEX oauth_transaction_expiry_idx
  ON oauth_transaction(expires_at) WHERE consumed_at IS NULL;

CREATE TABLE tenant_mailbox_policy (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  provider varchar(16) NOT NULL CHECK (provider IN ('google', 'microsoft')),
  address_normalized varchar(320) NOT NULL,
  mailbox_kind varchar(16) NOT NULL CHECK (mailbox_kind IN ('personal', 'shared')),
  active boolean NOT NULL DEFAULT true,
  default_visibility varchar(16) NOT NULL DEFAULT 'owner'
    CHECK (default_visibility IN ('owner', 'all_staff')),
  allow_read boolean NOT NULL DEFAULT true,
  allow_send boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, provider, address_normalized)
);

CREATE TABLE staff_mail_authorization (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  staff_member_id uuid NOT NULL REFERENCES staff_member(id) ON DELETE CASCADE,
  provider varchar(16) NOT NULL CHECK (provider IN ('google', 'microsoft')),
  provider_subject varchar(255) NOT NULL,
  provider_tenant varchar(255) NOT NULL,
  account_email_normalized varchar(320) NOT NULL,
  granted_scopes text[] NOT NULL,
  refresh_token_ciphertext bytea NOT NULL,
  refresh_token_nonce bytea NOT NULL,
  encryption_key_version smallint NOT NULL DEFAULT 1 CHECK (encryption_key_version > 0),
  status varchar(24) NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'reconnect_required', 'revoked')),
  last_refreshed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, staff_member_id, provider, provider_subject)
);

CREATE TABLE staff_mailbox (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  authorization_id uuid NOT NULL
    REFERENCES staff_mail_authorization(id) ON DELETE CASCADE,
  provider varchar(16) NOT NULL CHECK (provider IN ('google', 'microsoft')),
  provider_mailbox_id varchar(512) NOT NULL,
  address_normalized varchar(320) NOT NULL,
  display_name varchar(160),
  mailbox_kind varchar(16) NOT NULL CHECK (mailbox_kind IN ('personal', 'shared')),
  status varchar(24) NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'reconnect_required', 'disabled')),
  history_cursor text,
  subscription_id varchar(255),
  subscription_expires_at timestamptz,
  last_synced_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, provider, address_normalized)
);

CREATE TABLE staff_mailbox_grant (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  mailbox_id uuid NOT NULL REFERENCES staff_mailbox(id) ON DELETE CASCADE,
  principal_type varchar(16) NOT NULL
    CHECK (principal_type IN ('staff', 'component', 'all_staff')),
  staff_member_id uuid REFERENCES staff_member(id) ON DELETE CASCADE,
  component varchar(120),
  can_read boolean NOT NULL DEFAULT false,
  can_send boolean NOT NULL DEFAULT false,
  can_manage boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (principal_type = 'staff' AND staff_member_id IS NOT NULL AND component IS NULL)
    OR (principal_type = 'component' AND staff_member_id IS NULL AND component IS NOT NULL)
    OR (principal_type = 'all_staff' AND staff_member_id IS NULL AND component IS NULL)
  ),
  UNIQUE NULLS NOT DISTINCT (mailbox_id, principal_type, staff_member_id, component)
);

CREATE TABLE staff_mail_message_cache (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  mailbox_id uuid NOT NULL REFERENCES staff_mailbox(id) ON DELETE CASCADE,
  provider_message_id varchar(512) NOT NULL,
  provider_thread_id varchar(512),
  internet_message_id varchar(998),
  direction varchar(16) NOT NULL CHECK (direction IN ('inbound', 'outbound')),
  sender_address varchar(320) NOT NULL,
  recipient_addresses jsonb NOT NULL CHECK (jsonb_typeof(recipient_addresses) = 'array'),
  subject text,
  body_text text,
  received_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  linked_student_id uuid REFERENCES student(id) ON DELETE SET NULL,
  linked_interaction_id uuid REFERENCES staff_interaction(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, mailbox_id, provider_message_id),
  CHECK (expires_at >= received_at)
);

CREATE INDEX staff_mail_message_recent_idx
  ON staff_mail_message_cache(tenant_id, mailbox_id, received_at DESC);
CREATE INDEX staff_mail_message_expiry_idx ON staff_mail_message_cache(expires_at);

CREATE TABLE staff_email_send_intent (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  mailbox_id uuid NOT NULL REFERENCES staff_mailbox(id),
  created_by_staff_id uuid NOT NULL REFERENCES staff_member(id),
  confirmed_by_staff_id uuid REFERENCES staff_member(id),
  student_id uuid REFERENCES student(id),
  interaction_id uuid REFERENCES staff_interaction(id),
  reply_to_message_id uuid REFERENCES staff_mail_message_cache(id),
  recipient_addresses jsonb NOT NULL CHECK (jsonb_typeof(recipient_addresses) = 'array'),
  subject text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 998),
  body_text text NOT NULL CHECK (char_length(body_text) BETWEEN 1 AND 100000),
  content_sha256 char(64) NOT NULL,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  status varchar(24) NOT NULL DEFAULT 'pending_confirmation'
    CHECK (status IN (
      'pending_confirmation', 'queued', 'sending', 'sent', 'failed',
      'expired', 'cancelled'
    )),
  idempotency_key varchar(128),
  stable_message_id varchar(998) NOT NULL,
  provider_message_id varchar(512),
  last_error_code varchar(80),
  last_error_message text,
  expires_at timestamptz NOT NULL,
  confirmed_at timestamptz,
  sent_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key),
  CHECK (expires_at > created_at)
);

CREATE INDEX staff_email_send_queue_idx
  ON staff_email_send_intent(status, updated_at)
  WHERE status IN ('queued', 'sending', 'failed');
