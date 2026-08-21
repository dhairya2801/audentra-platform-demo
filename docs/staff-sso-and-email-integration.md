# Staff SSO and delegated email integration

The platform supports tenant-bound staff sign-in and separately consented Google Workspace or Microsoft 365 mailbox access. By default, staff must already exist as an active `staff_member`. An institution may instead issue a narrow, exact-email `staff_sso_provisioning_grant`; its first verified SSO sign-in creates the active staff profile with the approved local component. SSO never grants a broad role merely because an account belongs to a hosted domain or Entra tenant.

## Provider registration

Register these exact redirect URIs, replacing the API origin for each environment:

- `/v1/auth/staff/sso/google/callback`
- `/v1/auth/staff/sso/microsoft/callback`
- `/v1/staff/mail/oauth/google/callback`
- `/v1/staff/mail/oauth/microsoft/callback`

Configure the server-only variables documented in `apps/api/.env.example`. `MAIL_TOKEN_ENCRYPTION_KEY` is a base64url-encoded 32-byte AES key; generate and store it in Secret Manager. Never expose client secrets, refresh tokens, or this key through `NEXT_PUBLIC_*` variables.

Google mailbox consent requests `gmail.readonly` and `gmail.send`. These scopes require the appropriate Google OAuth consent-screen publication, verification, and security review for the intended deployment. Microsoft mailbox consent uses delegated `Mail.Read`, `Mail.Send`, and the shared-mail variants. Institutional administrators may need to approve consent.

## Tenant policy

Provider enablement is data, not inferred from an email suffix. Insert the institution's verified Google hosted domain or immutable Microsoft Entra tenant ID:

```sql
INSERT INTO tenant_identity_provider (
  tenant_id, provider, enabled, google_hosted_domain, microsoft_tenant_id
) VALUES
  ('<tenant-uuid>', 'google', true, 'university.edu', NULL),
  ('<tenant-uuid>', 'microsoft', true, NULL, '<entra-tenant-uuid>');
```

Only configure the provider actually owned by that tenant. The OAuth transaction stores the Audentra tenant, provider, expected hosted domain or Entra tenant, nonce, PKCE verifier, and canonical return path. Callbacks consume the transaction once and do not accept a tenant selector.

For just-in-time creation, issue one grant per approved person. This is intentionally an allowlist, not a domain-wide switch:

```sql
INSERT INTO staff_sso_provisioning_grant (
  id, tenant_id, provider, email_normalized, component
) VALUES (
  gen_random_uuid(), '<tenant-uuid>', 'google',
  'approved.staff@university.edu', 'Admissions'
);
```

The first successful callback binds that grant to the immutable Google `sub` or Microsoft `oid` plus the verified provider organization. The profile name comes from the OIDC `name` claim; the component remains the institution-controlled value in the grant.

Approve a shared mailbox explicitly before a staff member can connect it:

```sql
INSERT INTO tenant_mailbox_policy (
  id, tenant_id, provider, address_normalized, mailbox_kind,
  default_visibility, allow_read, allow_send
) VALUES (
  gen_random_uuid(), '<tenant-uuid>', 'microsoft',
  'information@university.edu', 'shared', 'all_staff', true, true
);
```

Use `default_visibility='owner'` when only the connecting staff member should receive a grant. More precise staff/component grants can be managed directly in `staff_mailbox_grant` until an administration UI is added.

## Runtime behavior

- Password sign-in remains available. SSO is an additional route for pre-provisioned active staff or an exact, active provisioning grant.
- `returnTo` accepts only a relative path under `/staff` on the tenant-bound portal; the start endpoint verifies the requested tenant matches that portal before it creates state. External, cross-tenant, encoded-separator, backslash, fragment, and traversal forms fall back to the staff home.
- SSO links an immutable Google `sub` or Microsoft `(tid, oid)` identity to one staff record. A later subject or tenant mismatch fails closed.
- Mailbox OAuth is separate from SSO. Personal mailboxes must match the provisioned staff email. Shared mailboxes must match active tenant policy and pass a provider access probe.
- Refresh tokens are AES-GCM encrypted. The platform stores no attachments and caches normalized text for seven days. Recent reads refresh from the provider; explicit searches run provider-side across the mailbox and cache only returned results.
- Every mailbox request rechecks the tenant, active staff record, authorization status, and read/send/manage grant.
- EAC delivery is two step: create an immutable, expiring intent, then confirm its version and content hash. The worker uses a stable message identifier and never blindly retries an ambiguous provider failure.

## Operational checks

After migrations and provider policy are applied:

1. `GET /v1/auth/staff/options?tenantSlug=<slug>` lists only enabled and runtime-configured providers.
2. Test SSO with a pre-provisioned user from the configured organization, then verify a user from another hosted domain or Entra tenant is rejected.
3. Connect a personal mailbox and confirm the Mailboxes page can read recent messages and provider-side search results.
4. Create an EAC email, review the exact sender/recipient/content, confirm it, and verify the worker records a sent or failed status without duplicate delivery.
5. Revoke provider consent and verify subsequent access marks the authorization `reconnect_required`.

Provider credentials and tenant IDs are environment-specific. They are deliberately not seeded into source control.
