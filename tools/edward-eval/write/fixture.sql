-- Write-eval fixtures applied to the base database only.
--
-- The synthetic university deploy cannot create mailboxes: a real mailbox
-- needs an OAuth grant. `communications.email.prepare` therefore has no happy
-- path against the deployed tenant, only its honest "no sendable mailbox"
-- denial. This adds one shared department mailbox with an all-staff send
-- grant so the two-stage prepare/send boundary is actually exercised.
--
-- Nothing here is product code and nothing here runs outside the eval base DB.

\set tenant '00000000-0000-7000-8000-000000000003'

INSERT INTO staff_mail_authorization (
  id, tenant_id, staff_member_id, provider, provider_subject, provider_tenant,
  account_email_normalized, granted_scopes, refresh_token_ciphertext,
  refresh_token_nonce, encryption_key_version, status
)
SELECT '90000000-0000-7000-8000-0000000000e1', :'tenant',
       m.id, 'google', 'eval-write-suite', 'aster.example',
       'advising@synthetic.aster.example',
       ARRAY['https://www.googleapis.com/auth/gmail.send'],
       '\x00'::bytea, '\x00'::bytea, 1, 'active'
FROM staff_member m
WHERE m.tenant_id = :'tenant' AND m.external_ref = 'SYN-STF-ADV-DIR'
ON CONFLICT DO NOTHING;

INSERT INTO staff_mailbox (
  id, tenant_id, authorization_id, provider, provider_mailbox_id,
  address_normalized, display_name, mailbox_kind, status
) VALUES (
  '90000000-0000-7000-8000-0000000000e2', :'tenant',
  '90000000-0000-7000-8000-0000000000e1', 'google', 'eval-write-mailbox',
  'advising@synthetic.aster.example', 'Academic Advising', 'shared', 'active'
) ON CONFLICT DO NOTHING;

INSERT INTO staff_mailbox_grant (
  id, tenant_id, mailbox_id, principal_type, can_read, can_send, can_manage
) VALUES (
  '90000000-0000-7000-8000-0000000000e3', :'tenant',
  '90000000-0000-7000-8000-0000000000e2', 'all_staff', true, true, false
) ON CONFLICT DO NOTHING;

-- The Explorer deploy does not create credential accounts (a real one needs a
-- password or an identity provider), so 2,576 of 2,577 students have no email
-- address in the tenant. That is a deployment artefact, not a property of a
-- university, and without an address `communications.email.prepare` has no
-- reachable happy path at all. One inactive-password account per fixture
-- student gives the recipient an address; nothing here can be signed in to.
INSERT INTO credential_account (
  id, tenant_id, student_id, email_normalized, phone_e164,
  password_hash, password_algorithm, status, created_at, updated_at
)
SELECT gen_random_uuid(), s.tenant_id, s.id,
       lower(replace(p.first_name, ' ', '')) || '.' ||
         lower(replace(p.last_name, ' ', '')) || '.' ||
         lower(s.external_ref) || '@students.aster.example',
       -- `phone_e164` is NOT NULL and unique per tenant, so it is derived from
       -- the student reference rather than shared.
       '+1' || lpad(regexp_replace(s.external_ref, '\D', '', 'g'), 9, '0'),
       -- A syntactically valid but unusable hash: the column is NOT NULL and
       -- constrained to the two real algorithms, and nothing must be able to
       -- sign in as a fixture student.
       'no-login-' || md5(s.id::text), 'scrypt-v1',
       'active', now(), now()
FROM student s
JOIN person p ON p.id = s.person_id AND p.tenant_id = s.tenant_id
WHERE s.tenant_id = :'tenant'
  AND NOT EXISTS (
    SELECT 1 FROM credential_account a
    WHERE a.tenant_id = s.tenant_id AND a.student_id = s.id
  )
ON CONFLICT DO NOTHING;
