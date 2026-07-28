CREATE TABLE media_asset (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  purpose varchar(80) NOT NULL,
  storage_provider varchar(40) NOT NULL,
  storage_key varchar(512) NOT NULL,
  public_path varchar(512) NOT NULL,
  mime_type varchar(100) NOT NULL,
  sha256 varchar(64) NOT NULL,
  alt_text varchar(500) NOT NULL,
  attribution varchar(300) NOT NULL,
  source_url varchar(1000) NOT NULL,
  license_name varchar(120) NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, storage_key)
);

CREATE INDEX media_asset_tenant_purpose_idx
  ON media_asset(tenant_id, purpose, active);

CREATE TABLE housing_residence_option (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  code varchar(80) NOT NULL,
  name varchar(180) NOT NULL,
  description text NOT NULL,
  amenities jsonb NOT NULL DEFAULT '[]'::jsonb,
  media_asset_id uuid NOT NULL REFERENCES media_asset(id),
  display_order integer NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, code)
);

ALTER TABLE student_club
  ADD COLUMN media_asset_id uuid REFERENCES media_asset(id);
