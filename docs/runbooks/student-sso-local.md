# Student Google and Microsoft SSO proof

This runbook covers the student sign-in proof only. It does not enable SSO for
staff, leader, or VP portals. The API uses OpenID Connect (OIDC) authorization
code flow with PKCE to authenticate an existing student account. It requests
only `openid email profile`; it does not request Gmail, Outlook, Microsoft
Graph, mailbox, contacts, or calendar access.

`AUTH_MODE=oidc` currently selects this student-only proof for the whole API.
While it is enabled, credential sign-up/sign-in and staff authentication fail
closed; staff, leader, and VP authentication is intentionally unavailable.

## Server configuration

Copy an environment example to an ignored local `.env` file and set
`AUTH_MODE=oidc`. At least one provider must be complete. Configure both sets to
show Google first and Microsoft second in provider discovery.

| Variable | Purpose |
| --- | --- |
| `OIDC_AUDENTRA_TENANT_ID` | UUID of the one Audentra tenant whose existing student accounts may sign in. This is server configuration and cannot be selected with a browser tenant header. |
| `OIDC_PUBLIC_BASE_URL` | Exact externally visible API origin used to build fixed provider callback URLs. Local development may use `http://localhost:4000`; preview and production require HTTPS. Do not include a path. |
| `OIDC_PORTAL_BASE_URL` | Exact portal origin used for `/dashboard` and safe `/sign-in?sso_error=...` redirects. Local development normally uses `http://localhost:3000`; preview and production require HTTPS. Do not include a path. |
| `GOOGLE_OIDC_CLIENT_ID` | Google OAuth web client ID. |
| `GOOGLE_OIDC_CLIENT_SECRET` | Google OAuth web client secret. |
| `MICROSOFT_OIDC_CLIENT_ID` | Microsoft identity platform application (client) ID. |
| `MICROSOFT_OIDC_CLIENT_SECRET` | Microsoft identity platform client secret. |
| `MICROSOFT_OIDC_TENANT_ID` | UUID of the allowed Microsoft Entra directory (`tid`). This is different from `OIDC_AUDENTRA_TENANT_ID`. |

For a direct local API on port 4000 and portal on port 3000, the relevant
settings are:

```dotenv
AUTH_MODE=oidc
OIDC_AUDENTRA_TENANT_ID=<audentra-tenant-uuid>
OIDC_PUBLIC_BASE_URL=http://localhost:4000
OIDC_PORTAL_BASE_URL=http://localhost:3000

GOOGLE_OIDC_CLIENT_ID=<google-web-client-id>
GOOGLE_OIDC_CLIENT_SECRET=<google-web-client-secret>

MICROSOFT_OIDC_CLIENT_ID=<entra-application-client-id>
MICROSOFT_OIDC_CLIENT_SECRET=<entra-application-client-secret>
MICROSOFT_OIDC_TENANT_ID=<entra-directory-tenant-uuid>
```

Remove either provider's entire block if it is not being tested. A partial
provider block fails startup. Never commit the populated `.env`, a provider
secret, an authorization code, an ID token, or a captured provider response.
Client secrets are API-only and must never use a `NEXT_PUBLIC_*` variable.

## Provider registrations

Register web callbacks exactly as follows for the local proof:

```text
http://localhost:4000/v1/auth/sso/google/callback
http://localhost:4000/v1/auth/sso/microsoft/callback
```

For a hosted deployment, replace only the origin with the configured public
HTTPS origin. Paths are fixed. Do not register wildcards or derive callback
hosts from request headers. The student portal starts sign-in at
`/v1/auth/sso/{provider}/start?returnTo=/dashboard`; `/dashboard` is the only
accepted return path.

After local PostgreSQL and object storage are running, apply the forward-only
migrations and start the API from this repository:

```powershell
npm run db:migrate
npm run dev:api
```

Run the portal separately from `Audentra-portals` on
`http://localhost:3000`. Provider discovery is available at
`http://localhost:4000/v1/auth/sso/providers`.

## Callback query logging

Providers return the authorization `code` and `state` in each callback URL's
query string. Treat the full callback URL as credential-bearing. The
application's access-log redaction protects only the local/direct Uvicorn
process; it cannot sanitize logs written by a public edge, reverse proxy, load
balancer, Cloud Run, or Cloud Logging before the request reaches the app.

Before any hosted test, configure the public edge to omit or redact query
strings for both fixed callback paths. Also configure Cloud Logging to exclude
those callback request logs, or ensure the query component is redacted before
ingestion. Verify with dummy `code` and `state` values that neither raw value
appears at any logging layer. Do not rely on deleting logs after a real login.

## Existing-account linking policy

OIDC does not create a student, tenant, or credential account.

- On first Google sign-in, the API requires Google's `email_verified` claim and
  may link only an active `credential_account` in
  `OIDC_AUDENTRA_TENANT_ID` whose normalized email matches exactly and which
  does not already have a federated identity. Later sign-ins use the stable
  issuer and Google `sub`, not the mutable email address.
- Microsoft email and `preferred_username` are not used for linking. An
  operator must pre-provision the stable Entra directory `tid` plus object
  `oid` against the intended tenant-scoped credential account. Later sign-ins
  require that exact mapping and the configured Entra directory.

Use a trusted Entra administrative source for `tid` and `oid`; do not provision
from an unverified browser token. The following psql-oriented statement uses
named parameters and copies the already stored account email. The values are
identifiers, not provider secrets. Bind them locally; do not replace them with
client secrets, access tokens, or ID tokens.

```sql
BEGIN;

INSERT INTO federated_identity (
  id,
  tenant_id,
  credential_account_id,
  provider,
  issuer,
  subject,
  email_normalized
)
SELECT
  gen_random_uuid(),
  account.tenant_id,
  account.id,
  'microsoft',
  'https://login.microsoftonline.com/'
    || ((:'entra_tenant_id')::uuid)::text
    || '/v2.0',
  ((:'entra_object_id')::uuid)::text,
  account.email_normalized
FROM credential_account AS account
JOIN tenant ON tenant.id = account.tenant_id
WHERE account.tenant_id = (:'audentra_tenant_id')::uuid
  AND account.id = (:'credential_account_id')::uuid
  AND account.status = 'active'
  AND tenant.status = 'active';

-- Expect exactly one inserted row. A zero-row result means the account or
-- tenant scope/status is wrong; roll back and investigate.
COMMIT;
```

The unique constraints deliberately reject a Microsoft principal mapped twice
inside a tenant or a second Microsoft identity mapped to the same credential
account. Treat such a conflict as an identity review, not as a reason to edit
or bypass the constraint.

## Private Cloud Run and the laptop proxy

The `audentra-api-preview` Cloud Run service is private. A normal hosted browser
and an identity provider cannot call that private service URL directly. A
hosted portal needs a public same-origin server edge/BFF that receives the
fixed `/v1/auth/sso/*` routes and invokes Cloud Run server-side with keyless
workload identity. Configure `OIDC_PUBLIC_BASE_URL` to that public HTTPS origin,
not to the private `run.app` URL. Never place a service-account key or Cloud Run
ID token in browser code.

For laptop-only testing, the documented gcloud proxy may remain bound to
`127.0.0.1:4000`; its loopback URL can be the local public callback base while
the portal remains on port 3000. Restart the proxy after a laptop restart and
verify `http://127.0.0.1:4000/health/ready` before starting a login. Do not bind
the proxy to the LAN or internet.
