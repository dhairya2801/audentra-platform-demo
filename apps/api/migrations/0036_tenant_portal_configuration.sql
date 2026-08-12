-- Make the tenant slug and portal bootstrap canonical PostgreSQL state.
-- The two preview tenants are bootstrap data only; all runtime reads and edits
-- use tenant and tenant_portal_configuration after this migration.

ALTER TABLE tenant
  ADD COLUMN slug varchar(63),
  ADD COLUMN demo_auth_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN status varchar(24) NOT NULL DEFAULT 'deactivated'
    CHECK (status IN ('active', 'suspended', 'deactivated'));

INSERT INTO tenant (id, name, status)
VALUES
  ('00000000-0000-7000-8000-000000000001', 'Aster University', 'active'),
  ('00000000-0000-7000-8000-000000000002', 'Harvard University', 'active')
ON CONFLICT (id) DO NOTHING;

UPDATE tenant
SET
  name = CASE id
    WHEN '00000000-0000-7000-8000-000000000001' THEN 'Aster University'
    WHEN '00000000-0000-7000-8000-000000000002' THEN 'Harvard University'
  END,
  slug = CASE id
    WHEN '00000000-0000-7000-8000-000000000001' THEN 'aster'
    WHEN '00000000-0000-7000-8000-000000000002' THEN 'harvard'
  END,
  demo_auth_enabled = true,
  status = 'active'
WHERE id IN (
  '00000000-0000-7000-8000-000000000001',
  '00000000-0000-7000-8000-000000000002'
);

-- Existing non-preview tenants receive a stable UUID-derived bootstrap slug.
-- Staff can edit portal configuration, while a later tenant-administration
-- workflow may provide a human-friendly slug before public launch.
UPDATE tenant
SET slug = 'tenant-' || replace(id::text, '-', '')
WHERE slug IS NULL;

ALTER TABLE tenant ALTER COLUMN slug SET NOT NULL;
ALTER TABLE tenant ALTER COLUMN slug
  SET DEFAULT ('tenant-' || replace(gen_random_uuid()::text, '-', ''));
ALTER TABLE tenant
  ADD CONSTRAINT tenant_slug_format_check
    CHECK (
      slug ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'
      AND slug NOT IN (
        'appointments', 'campus-life', 'classrooms', 'dashboard', 'documents',
        'edward', 'enrollment', 'financials', 'health', 'help', 'messages',
        'offer', 'onboarding', 'payments', 'profile', 'sign-in', 'staff', 'v1'
      )
    ),
  ADD CONSTRAINT tenant_slug_uidx UNIQUE (slug);

CREATE TABLE tenant_portal_configuration (
  tenant_id uuid PRIMARY KEY REFERENCES tenant(id) ON DELETE RESTRICT,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  display_name varchar(180) NOT NULL,
  legal_name varchar(180) NOT NULL,
  short_name varchar(80) NOT NULL,
  logo_url varchar(1000) NOT NULL,
  logo_alt varchar(240) NOT NULL,
  logo_dark_url varchar(1000),
  logo_dark_alt varchar(240),
  favicon_url varchar(1000),
  hero_image_url varchar(1000),
  hero_image_alt varchar(240),
  primary_color varchar(9) NOT NULL,
  secondary_color varchar(9) NOT NULL,
  accent_color varchar(9) NOT NULL,
  locale varchar(35) NOT NULL,
  time_zone varchar(100) NOT NULL,
  currency_code char(3) NOT NULL,
  country_code char(2) NOT NULL,
  academic_year_label varchar(80) NOT NULL,
  current_term_label varchar(120) NOT NULL,
  default_campus_name varchar(180),
  contacts jsonb NOT NULL,
  capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
  public_links jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT tenant_portal_configuration_primary_color_check
    CHECK (primary_color ~ '^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$'),
  CONSTRAINT tenant_portal_configuration_secondary_color_check
    CHECK (secondary_color ~ '^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$'),
  CONSTRAINT tenant_portal_configuration_accent_color_check
    CHECK (accent_color ~ '^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$'),
  CONSTRAINT tenant_portal_configuration_currency_check
    CHECK (currency_code ~ '^[A-Z]{3}$'),
  CONSTRAINT tenant_portal_configuration_country_check
    CHECK (country_code ~ '^[A-Z]{2}$'),
  CONSTRAINT tenant_portal_configuration_contacts_object_check
    CHECK (jsonb_typeof(contacts) = 'object'),
  CONSTRAINT tenant_portal_configuration_capabilities_object_check
    CHECK (jsonb_typeof(capabilities) = 'object'),
  CONSTRAINT tenant_portal_configuration_public_links_object_check
    CHECK (jsonb_typeof(public_links) = 'object')
);

CREATE FUNCTION prevent_provisioned_tenant_slug_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.slug IS DISTINCT FROM NEW.slug AND EXISTS (
    SELECT 1 FROM tenant_portal_configuration
    WHERE tenant_id = OLD.id
  ) THEN
    RAISE EXCEPTION 'A provisioned tenant slug is immutable';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER tenant_slug_immutable_after_provisioning
BEFORE UPDATE OF slug ON tenant
FOR EACH ROW EXECUTE FUNCTION prevent_provisioned_tenant_slug_mutation();

INSERT INTO tenant_portal_configuration (
  tenant_id, display_name, legal_name, short_name,
  logo_url, logo_alt, favicon_url,
  primary_color, secondary_color, accent_color,
  locale, time_zone, currency_code, country_code,
  academic_year_label, current_term_label, default_campus_name,
  contacts, capabilities, public_links
)
VALUES
  (
    '00000000-0000-7000-8000-000000000001',
    'Aster University', 'Aster University', 'Aster',
    '/icon.png', 'Aster University', '/icon.png',
    '#171717', '#F5F5F4', '#C79A3B',
    'en-US', 'America/New_York', 'USD', 'US',
    '2027-2028', 'Fall 2027', 'Aster Main Campus',
    '{"support":{"label":"Student support","email":"enrollment@aster.edu","phone":null,"hours":null,"url":null},"admissions":{"label":"Admissions","email":"admissions@aster.edu","phone":null,"hours":null,"url":null},"financialAid":{"label":"Financial aid","email":"financialaid@aster.edu","phone":null,"hours":null,"url":null}}'::jsonb,
    '{"studentPortal":true,"staffPortal":true,"assistant":true,"campusLife":true}'::jsonb,
    '{"institution":"/","privacy":"/privacy","accessibility":"/accessibility"}'::jsonb
  ),
  (
    '00000000-0000-7000-8000-000000000002',
    'Harvard University', 'President and Fellows of Harvard College', 'Harvard',
    '/icon.png', 'Harvard University', '/icon.png',
    '#A51C30', '#F3F4F4', '#1E1E1E',
    'en-US', 'America/New_York', 'USD', 'US',
    '2027-2028', 'Fall 2027', 'Cambridge Campus',
    '{"support":{"label":"Student services","email":"studentservices@harvard.edu","phone":null,"hours":null,"url":null},"admissions":{"label":"Admissions","email":"admissions@harvard.edu","phone":null,"hours":null,"url":"https://college.harvard.edu/admissions"},"financialAid":{"label":"Financial aid","email":"college@fas.harvard.edu","phone":null,"hours":null,"url":"https://college.harvard.edu/financial-aid"}}'::jsonb,
    '{"studentPortal":true,"staffPortal":true,"assistant":true,"campusLife":true}'::jsonb,
    '{"institution":"https://www.harvard.edu","privacy":"https://www.harvard.edu/privacy-statement/","accessibility":"https://accessibility.huit.harvard.edu"}'::jsonb
  );

-- Migrations 0027 and 0029 seeded per-tenant starter records before a clean
-- database had tenant rows. Backfill the two migration-bootstrapped preview
-- tenants here without replacing later staff edits.
INSERT INTO staff_knowledge_card (
  id, tenant_id, title, summary, body, category, audience, status, owner_name
)
SELECT md5(tenant.id::text || starter.seed_key)::uuid, tenant.id,
       starter.title, starter.summary, starter.body, starter.category,
       starter.audience, starter.status, starter.owner_name
FROM tenant
CROSS JOIN (VALUES
  (':knowledge:deposit', 'Enrollment deposit policy',
   'Approved guidance for deposit deadlines, waivers, and escalation.',
   'Verify the offer deadline before discussing extensions or waivers.',
   'Enrollment', 'internal', 'published', 'Admissions Operations'),
  (':knowledge:transcript', 'Transcript review expectations',
   'What students and reviewers should expect after an upload.',
   'Extracted fields remain suggestions until a reviewer confirms a decision.',
   'Documents', 'student', 'published', 'Registrar'),
  (':knowledge:housing', 'Housing follow-up guide',
   'Routing notes for undecided and off-campus students.',
   'Use housing and accommodation preferences to select the advising queue.',
   'Housing', 'internal', 'draft', 'Student Life')
) AS starter(seed_key, title, summary, body, category, audience, status, owner_name)
WHERE tenant.id IN (
  '00000000-0000-7000-8000-000000000001',
  '00000000-0000-7000-8000-000000000002'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO staff_core_play (
  id, tenant_id, title, description, trigger_description, audience,
  steps, status, owner_name
)
SELECT md5(tenant.id::text || starter.seed_key)::uuid, tenant.id,
       starter.title, starter.description, starter.trigger_description,
       starter.audience, starter.steps, starter.status, starter.owner_name
FROM tenant
CROSS JOIN (VALUES
  (':core-play:deposit', 'Deposit deadline rescue',
   'A coordinated sequence for an approaching deposit deadline.',
   'Deposit due within 72 hours and requirement incomplete',
   'Admitted students with incomplete deposits',
   '["Verify the student has an active offer", "Check for an approved waiver or extension", "Draft a reminder with the secure payment link"]'::jsonb,
   'active', 'Admissions Operations'),
  (':core-play:documents', 'Missing document recovery',
   'A follow-up path for blocking enrollment documents.',
   'Blocking document is rejected or seven days overdue',
   'Students with blocking document requirements',
   '["Confirm the rejection reason", "Draft resubmission instructions"]'::jsonb,
   'draft', 'Registrar')
) AS starter(
  seed_key, title, description, trigger_description, audience, steps, status, owner_name
)
WHERE tenant.id IN (
  '00000000-0000-7000-8000-000000000001',
  '00000000-0000-7000-8000-000000000002'
)
ON CONFLICT (id) DO NOTHING;
