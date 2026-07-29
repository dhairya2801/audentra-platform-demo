ALTER TABLE program
  ADD COLUMN source_label varchar(180),
  ADD COLUMN source_url varchar(1000),
  ADD COLUMN source_status varchar(32)
    CHECK (source_status IN (
      'official_source',
      'synthetic_preview',
      'tenant_authored'
    ));

ALTER TABLE course_catalog_version
  ADD COLUMN source_label varchar(180),
  ADD COLUMN source_url varchar(1000),
  ADD COLUMN source_status varchar(32)
    CHECK (source_status IN (
      'official_source',
      'synthetic_preview',
      'tenant_authored'
    ));

ALTER TABLE catalog_course
  ADD COLUMN availability_label varchar(120),
  ADD COLUMN instructor_names text[] NOT NULL DEFAULT '{}',
  ADD COLUMN meeting_pattern varchar(240),
  ADD COLUMN source_url varchar(1000);

ALTER TABLE campus_event
  ADD COLUMN source_label varchar(180),
  ADD COLUMN source_url varchar(1000),
  ADD COLUMN source_status varchar(32)
    CHECK (source_status IN (
      'official_source',
      'synthetic_preview',
      'tenant_authored'
    )),
  ADD COLUMN registration_url varchar(1000);

ALTER TABLE student_club
  ADD COLUMN source_label varchar(180),
  ADD COLUMN source_url varchar(1000),
  ADD COLUMN source_status varchar(32)
    CHECK (source_status IN (
      'official_source',
      'synthetic_preview',
      'tenant_authored'
    )),
  ADD COLUMN social_links jsonb NOT NULL DEFAULT '[]'::jsonb;
