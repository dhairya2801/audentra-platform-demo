-- Explicit approval for just-in-time institutional staff creation. A grant is
-- intentionally narrower than an entire hosted domain or Entra tenant: SSO
-- proves identity, while this record supplies the local component and access
-- approval needed to create an active staff_member on the first sign-in.

CREATE TABLE staff_sso_provisioning_grant (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  provider varchar(16) NOT NULL CHECK (provider IN ('google', 'microsoft')),
  email_normalized varchar(320) NOT NULL,
  component varchar(120) NOT NULL,
  active boolean NOT NULL DEFAULT true,
  provider_subject varchar(255),
  provider_tenant varchar(255),
  claimed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, provider, email_normalized),
  CHECK (
    (provider_subject IS NULL AND provider_tenant IS NULL)
    OR (provider_subject IS NOT NULL AND provider_tenant IS NOT NULL)
  )
);

CREATE UNIQUE INDEX staff_sso_provisioning_grant_subject_uidx
  ON staff_sso_provisioning_grant(tenant_id, provider, provider_subject)
  WHERE provider_subject IS NOT NULL;
