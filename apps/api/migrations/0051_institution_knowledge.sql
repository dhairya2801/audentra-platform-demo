-- Institutional knowledge: the approved, versioned corpus Edward may cite.
--
-- Three tenant-scoped tables replace "no reviewed institutional source":
--   institution_office               the directory (where to send a person)
--   academic_calendar_entry          dated deadlines and windows
--   institution_knowledge_document   policies, procedures, handbook chapters,
--                                    program guides, service guides, internal
--                                    staff procedures — Markdown with metadata
--   institution_knowledge_section    one row per "##" section of a document,
--                                    the unit of retrieval
--
-- PostgreSQL is canonical; the tenant's `knowledge/` directory is the
-- seed/import input (see infrastructure/seeding/institution_knowledge.py).
-- Retrieval is plain full-text search (generated tsvector columns + GIN) plus
-- curated keywords and applicability facets; no extension beyond what the
-- base image already ships (unaccent, pg_trgm) is required, and neither is
-- used here so the migration also applies where they are absent.

CREATE TABLE institution_office (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  code varchar(16) NOT NULL,
  name varchar(160) NOT NULL,
  short_name varchar(80) NOT NULL,
  -- staff_member.component whose people work here; NULL when the platform
  -- holds no staff records for the unit.
  component varchar(80),
  location varchar(160) NOT NULL,
  hours varchar(240),
  email varchar(160),
  head_title varchar(160),
  sla_business_days smallint CHECK (sla_business_days IS NULL OR sla_business_days >= 0),
  description text NOT NULL,
  services text[] NOT NULL DEFAULT '{}',
  display_order integer NOT NULL DEFAULT 0,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, code)
);

CREATE INDEX institution_office_tenant_component_idx
  ON institution_office(tenant_id, component);

CREATE TABLE institution_knowledge_document (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  code varchar(80) NOT NULL,
  kind varchar(20) NOT NULL CHECK (
    kind IN ('policy', 'procedure', 'handbook', 'program', 'service', 'directory', 'internal')
  ),
  title varchar(200) NOT NULL,
  summary text NOT NULL,
  body text NOT NULL,
  owner_office_code varchar(16) NOT NULL,
  audience varchar(20) NOT NULL CHECK (audience IN ('student', 'internal', 'all')),
  status varchar(20) NOT NULL CHECK (status IN ('published', 'draft', 'retired')),
  version varchar(20) NOT NULL,
  effective_from date NOT NULL,
  effective_until date,
  supersedes_code varchar(80),
  -- Facet -> allowed values, e.g. {"residency": ["international"]}. A missing
  -- facet means the document applies to everyone on that dimension.
  applies_to jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(applies_to) = 'object'),
  related_codes text[] NOT NULL DEFAULT '{}',
  keywords text[] NOT NULL DEFAULT '{}',
  -- keywords joined with spaces: array_to_string is not immutable, so the
  -- generated search column reads this plain-text mirror instead.
  keywords_text text NOT NULL DEFAULT '',
  source_path varchar(240),
  content_sha256 char(64) NOT NULL,
  search tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(summary, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(keywords_text, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(body, '')), 'C')
  ) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, code),
  CHECK (effective_until IS NULL OR effective_until >= effective_from)
);

CREATE INDEX institution_knowledge_document_search_idx
  ON institution_knowledge_document USING GIN (search);
CREATE INDEX institution_knowledge_document_tenant_status_idx
  ON institution_knowledge_document(tenant_id, status, audience);

CREATE TABLE institution_knowledge_section (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  document_id uuid NOT NULL REFERENCES institution_knowledge_document(id) ON DELETE CASCADE,
  ordinal smallint NOT NULL CHECK (ordinal >= 0),
  heading varchar(200) NOT NULL,
  body text NOT NULL,
  search tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(heading, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(body, '')), 'C')
  ) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (document_id, ordinal)
);

CREATE INDEX institution_knowledge_section_search_idx
  ON institution_knowledge_section USING GIN (search);
CREATE INDEX institution_knowledge_section_document_idx
  ON institution_knowledge_section(tenant_id, document_id, ordinal);

CREATE TABLE academic_calendar_entry (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  code varchar(80) NOT NULL,
  term varchar(40),
  label varchar(240) NOT NULL,
  category varchar(40) NOT NULL,
  audience varchar(40) NOT NULL DEFAULT 'all',
  starts_on date NOT NULL,
  ends_on date,
  starts_at time,
  ends_at time,
  owner_office_code varchar(16),
  document_code varchar(80),
  description text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, code),
  CHECK (ends_on IS NULL OR ends_on >= starts_on)
);

CREATE INDEX academic_calendar_entry_tenant_date_idx
  ON academic_calendar_entry(tenant_id, starts_on);

-- The academic department a program belongs to, for adviser matching and
-- for "applies to" facets on program guides. Nullable: populated by the
-- tenant's seed and by the enrichment, never required by the portal.
ALTER TABLE program ADD COLUMN department varchar(80);
