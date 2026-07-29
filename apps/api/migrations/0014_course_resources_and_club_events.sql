ALTER TABLE catalog_course
  ADD COLUMN resources jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE student_club
  ADD COLUMN long_description text,
  ADD COLUMN meeting_schedule varchar(240),
  ADD COLUMN membership_open boolean NOT NULL DEFAULT true;

CREATE TABLE student_club_event (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  club_id uuid NOT NULL REFERENCES student_club(id) ON DELETE CASCADE,
  title varchar(180) NOT NULL,
  description text NOT NULL,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  location varchar(180) NOT NULL,
  category varchar(32) NOT NULL
    CHECK (category IN ('meeting', 'workshop', 'social', 'competition', 'service')),
  registration_url varchar(1000),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, club_id, title, starts_at)
);

CREATE INDEX student_club_event_tenant_club_starts_idx
  ON student_club_event (tenant_id, club_id, starts_at)
  WHERE active = true;
