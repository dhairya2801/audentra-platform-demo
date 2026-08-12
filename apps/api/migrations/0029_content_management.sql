-- Durable tenant content, stable publication identities, and campus-event
-- registrations. Student-facing projections remain REST/DB reads on refresh;
-- registrations and staff-authored content keep history through soft state.

ALTER TABLE campus_event
  ADD COLUMN source_id varchar(160),
  ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0);

CREATE UNIQUE INDEX campus_event_tenant_source_uidx
  ON campus_event(tenant_id, source_id)
  WHERE source_id IS NOT NULL;

ALTER TABLE catalog_course
  ADD COLUMN source_id varchar(160),
  ADD COLUMN related_videos jsonb NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(related_videos) = 'array'),
  ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0);

CREATE UNIQUE INDEX catalog_course_catalog_source_uidx
  ON catalog_course(catalog_version_id, source_id)
  WHERE source_id IS NOT NULL;

-- Backfill stable source keys and curated optional media for the two preview
-- tenants so a freshly seeded student sees the same content before any staff
-- republishes the managed catalog.
UPDATE catalog_course
SET source_id = CASE UPPER(REPLACE(code, ' ', ''))
  WHEN 'CS101' THEN 'cs-101'
  WHEN 'CS201' THEN 'cs-201'
  WHEN 'CS230' THEN 'cs-230'
  WHEN 'MATH151' THEN 'math-151'
  WHEN 'MATH152' THEN 'math-152'
  WHEN 'WRIT101' THEN 'writ-101'
  ELSE source_id
END
WHERE tenant_id='00000000-0000-7000-8000-000000000001'
  AND UPPER(REPLACE(code, ' ', '')) IN (
    'CS101', 'CS201', 'CS230', 'MATH151', 'MATH152', 'WRIT101'
  );

UPDATE catalog_course
SET source_id = CASE UPPER(REPLACE(code, ' ', ''))
  WHEN 'COMPSCI20' THEN 'compsci-20'
  WHEN 'COMPSCI32' THEN 'compsci-32'
  WHEN 'COMPSCI50' THEN 'compsci-50'
  WHEN 'COMPSCI51' THEN 'compsci-51'
  WHEN 'MATH21B' THEN 'math-21b'
  WHEN 'STAT110' THEN 'stat-110'
  ELSE source_id
END
WHERE tenant_id='00000000-0000-7000-8000-000000000002'
  AND UPPER(REPLACE(code, ' ', '')) IN (
    'COMPSCI20', 'COMPSCI32', 'COMPSCI50', 'COMPSCI51', 'MATH21B', 'STAT110'
  );

UPDATE catalog_course
SET related_videos = '[{"id":"mit-6-100l-introduction","title":"Introduction to Computer Science and Programming Using Python","description":"An open introductory programming lecture from MIT OpenCourseWare.","url":"https://www.youtube.com/watch?v=xAcTmDO6NTI","provider":"YouTube","sourceLabel":"MIT OpenCourseWare"}]'::jsonb
WHERE tenant_id='00000000-0000-7000-8000-000000000001'
  AND UPPER(REPLACE(code, ' ', ''))='CS101';

UPDATE catalog_course
SET related_videos = '[{"id":"mit-18-01-calculus","title":"Single Variable Calculus","description":"An open single-variable calculus lecture from MIT OpenCourseWare.","url":"https://www.youtube.com/watch?v=7K1sB05pE0A","provider":"YouTube","sourceLabel":"MIT OpenCourseWare"}]'::jsonb
WHERE tenant_id='00000000-0000-7000-8000-000000000001'
  AND UPPER(REPLACE(code, ' ', ''))='MATH151';

UPDATE catalog_course
SET related_videos = '[{"id":"cs50-2026-full-course","title":"Harvard CS50 (2026) - Full Computer Science University Course","description":"The official CS50 full-course video for introductory computer science.","url":"https://www.youtube.com/watch?v=gmuTjeQUbTM","provider":"YouTube","sourceLabel":"CS50"}]'::jsonb,
    source_url = COALESCE(source_url, 'https://cs50.harvard.edu/x/')
WHERE tenant_id='00000000-0000-7000-8000-000000000002'
  AND UPPER(REPLACE(code, ' ', ''))='COMPSCI50';

UPDATE catalog_course
SET related_videos = '[{"id":"stat-110-lectures","title":"Statistics 110 - Probability lecture series","description":"Harvard''s complete introductory probability lecture playlist.","url":"https://www.youtube.com/playlist?list=PL2SOU6wwxB0uwwH80KTQ6ht66KWxbzTIo","provider":"YouTube","sourceLabel":"Harvard Statistics 110"}]'::jsonb,
    source_url = COALESCE(source_url, 'https://stat110.hsites.harvard.edu/about')
WHERE tenant_id='00000000-0000-7000-8000-000000000002'
  AND UPPER(REPLACE(code, ' ', ''))='STAT110';

ALTER TABLE student_club
  ADD COLUMN version integer NOT NULL DEFAULT 1 CHECK (version > 0);

CREATE TABLE campus_event_registration (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  event_id uuid NOT NULL REFERENCES campus_event(id),
  student_id uuid NOT NULL REFERENCES student(id),
  status varchar(32) NOT NULL DEFAULT 'registered'
    CHECK (status IN ('registered', 'cancelled_by_event', 'cancelled_by_student')),
  event_version integer NOT NULL CHECK (event_version > 0),
  registered_at timestamptz NOT NULL DEFAULT now(),
  cancelled_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT campus_event_registration_student_uidx
    UNIQUE (tenant_id, event_id, student_id),
  CHECK (
    (status = 'registered' AND cancelled_at IS NULL)
    OR (status <> 'registered' AND cancelled_at IS NOT NULL)
  )
);

CREATE INDEX campus_event_registration_event_idx
  ON campus_event_registration(tenant_id, event_id, status);

CREATE INDEX campus_event_registration_student_idx
  ON campus_event_registration(tenant_id, student_id, registered_at DESC);

CREATE TABLE staff_knowledge_card (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  title varchar(120) NOT NULL,
  summary varchar(240) NOT NULL,
  body text NOT NULL CHECK (char_length(body) BETWEEN 3 AND 2000),
  category varchar(60) NOT NULL,
  audience varchar(20) NOT NULL CHECK (audience IN ('internal', 'student')),
  status varchar(20) NOT NULL CHECK (status IN ('draft', 'published', 'archived')),
  owner_name varchar(180) NOT NULL,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX staff_knowledge_card_tenant_status_idx
  ON staff_knowledge_card(tenant_id, status, updated_at DESC);

CREATE TABLE staff_core_play (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  title varchar(120) NOT NULL,
  description varchar(500) NOT NULL,
  trigger_description varchar(240) NOT NULL,
  audience varchar(240) NOT NULL,
  steps jsonb NOT NULL CHECK (jsonb_typeof(steps) = 'array'),
  status varchar(20) NOT NULL CHECK (status IN ('draft', 'active', 'archived')),
  owner_name varchar(180) NOT NULL,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX staff_core_play_tenant_status_idx
  ON staff_core_play(tenant_id, status, updated_at DESC);

-- Preserve the useful preview starter records, but give each tenant its own
-- durable identities. ON CONFLICT keeps demo seeding and migration replay safe.
INSERT INTO staff_knowledge_card (
  id, tenant_id, title, summary, body, category, audience, status, owner_name
)
SELECT md5(tenant.id::text || ':knowledge:deposit')::uuid, tenant.id,
       'Enrollment deposit policy',
       'Approved guidance for deposit deadlines, waivers, and escalation.',
       'Verify the offer deadline before discussing extensions or waivers.',
       'Enrollment', 'internal', 'published', 'Admissions Operations'
FROM tenant
ON CONFLICT (id) DO NOTHING;

INSERT INTO staff_knowledge_card (
  id, tenant_id, title, summary, body, category, audience, status, owner_name
)
SELECT md5(tenant.id::text || ':knowledge:transcript')::uuid, tenant.id,
       'Transcript review expectations',
       'What students and reviewers should expect after an upload.',
       'Extracted fields remain suggestions until a reviewer confirms a decision.',
       'Documents', 'student', 'published', 'Registrar'
FROM tenant
ON CONFLICT (id) DO NOTHING;

INSERT INTO staff_knowledge_card (
  id, tenant_id, title, summary, body, category, audience, status, owner_name
)
SELECT md5(tenant.id::text || ':knowledge:housing')::uuid, tenant.id,
       'Housing follow-up guide',
       'Routing notes for undecided and off-campus students.',
       'Use housing and accommodation preferences to select the advising queue.',
       'Housing', 'internal', 'draft', 'Student Life'
FROM tenant
ON CONFLICT (id) DO NOTHING;

INSERT INTO staff_core_play (
  id, tenant_id, title, description, trigger_description, audience,
  steps, status, owner_name
)
SELECT md5(tenant.id::text || ':core-play:deposit')::uuid, tenant.id,
       'Deposit deadline rescue',
       'A coordinated sequence for an approaching deposit deadline.',
       'Deposit due within 72 hours and requirement incomplete',
       'Admitted students with incomplete deposits',
       '["Verify the student has an active offer", "Check for an approved waiver or extension", "Draft a reminder with the secure payment link"]'::jsonb,
       'active', 'Admissions Operations'
FROM tenant
ON CONFLICT (id) DO NOTHING;

INSERT INTO staff_core_play (
  id, tenant_id, title, description, trigger_description, audience,
  steps, status, owner_name
)
SELECT md5(tenant.id::text || ':core-play:documents')::uuid, tenant.id,
       'Missing document recovery',
       'A follow-up path for blocking enrollment documents.',
       'Blocking document is rejected or seven days overdue',
       'Students with blocking document requirements',
       '["Confirm the rejection reason", "Draft resubmission instructions"]'::jsonb,
       'draft', 'Registrar'
FROM tenant
ON CONFLICT (id) DO NOTHING;
