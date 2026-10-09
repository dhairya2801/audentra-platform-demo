-- MB-09: team scope is the existing staff component, not a browser/person preference.
CREATE TABLE staff_brew_team_setting (
 tenant_id uuid NOT NULL REFERENCES tenant(id), component varchar(120) NOT NULL,
 intelligence_enabled boolean NOT NULL DEFAULT true, version integer NOT NULL CHECK(version>0),
 updated_by uuid NOT NULL REFERENCES staff_member(id), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,component)
);
INSERT INTO staff_role_capability(tenant_id,role_code,capability)
SELECT DISTINCT tenant_id,role_code,'morning_brew.team.configure' FROM staff_member
WHERE lower(role_code) IN ('admin','administrator','director','manager','supervisor','operations_lead','vp','vice_president','dean','registrar')
ON CONFLICT DO NOTHING;
-- MB-18: demo prep sheets have no assistant-message ID, so cannot enter response feedback.
CREATE TABLE staff_brew_prep_feedback (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenant(id), staff_id uuid NOT NULL REFERENCES staff_member(id),
 subject_id varchar(200) NOT NULL, snapshot_at timestamptz NOT NULL, data_origin varchar(20) NOT NULL CHECK(data_origin='demo'),
 rating varchar(8) NOT NULL CHECK(rating IN ('up','down')), reasons jsonb NOT NULL,
 comment varchar(1000) NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
-- MB-21: publisher metadata only; no student data or generated institutional analysis.
CREATE TABLE staff_brew_news_cache (
 tenant_id uuid PRIMARY KEY REFERENCES tenant(id), articles jsonb NOT NULL DEFAULT '[]',
 fetched_at timestamptz, attempted_at timestamptz NOT NULL DEFAULT now(),
 etag text, last_modified text, failed boolean NOT NULL DEFAULT false
);
