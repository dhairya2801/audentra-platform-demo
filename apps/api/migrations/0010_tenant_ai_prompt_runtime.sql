CREATE TABLE ai_prompt_template_version (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  operation varchar(80) NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  name varchar(180) NOT NULL,
  system_prompt text NOT NULL,
  user_prompt_template text,
  template_variables jsonb NOT NULL DEFAULT '[]'::jsonb,
  status varchar(24) NOT NULL
    CHECK (status IN ('draft', 'published', 'retired')),
  created_by uuid,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, operation, version)
);

CREATE TABLE ai_context_policy_version (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  operation varchar(80) NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  name varchar(180) NOT NULL,
  context_policy jsonb NOT NULL,
  status varchar(24) NOT NULL
    CHECK (status IN ('draft', 'published', 'retired')),
  created_by uuid,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, operation, version)
);

CREATE TABLE ai_output_schema_version (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  operation varchar(80) NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  name varchar(180) NOT NULL,
  output_schema jsonb NOT NULL,
  status varchar(24) NOT NULL
    CHECK (status IN ('draft', 'published', 'retired')),
  created_by uuid,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, operation, version)
);

CREATE TABLE ai_operation_config (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  operation varchar(80) NOT NULL,
  prompt_template_version_id uuid NOT NULL
    REFERENCES ai_prompt_template_version(id),
  context_policy_version_id uuid NOT NULL
    REFERENCES ai_context_policy_version(id),
  output_schema_version_id uuid
    REFERENCES ai_output_schema_version(id),
  provider varchar(40) NOT NULL,
  model varchar(200) NOT NULL,
  max_output_tokens integer NOT NULL CHECK (max_output_tokens > 0),
  temperature_milli integer NOT NULL DEFAULT 0
    CHECK (temperature_milli BETWEEN 0 AND 2000),
  config_revision bigint NOT NULL DEFAULT 1 CHECK (config_revision > 0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, operation)
);

CREATE INDEX ai_operation_config_updated_idx
  ON ai_operation_config(tenant_id, updated_at DESC);

CREATE TABLE ai_runtime_config_checkpoint (
  instance_id varchar(180) NOT NULL,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  operation varchar(80) NOT NULL,
  last_seen_revision bigint NOT NULL,
  last_checked_at timestamptz NOT NULL,
  last_loaded_at timestamptz NOT NULL,
  PRIMARY KEY (instance_id, tenant_id, operation)
);

ALTER TABLE ai_provider_response_attempt
  DROP CONSTRAINT IF EXISTS ai_provider_response_attempt_operation_check;

ALTER TABLE ai_provider_response_attempt
  ALTER COLUMN document_id DROP NOT NULL,
  ADD COLUMN prompt_template_version_id uuid
    REFERENCES ai_prompt_template_version(id),
  ADD COLUMN context_policy_version_id uuid
    REFERENCES ai_context_policy_version(id),
  ADD COLUMN output_schema_version_id uuid
    REFERENCES ai_output_schema_version(id),
  ADD COLUMN config_revision bigint,
  ADD COLUMN context_sha256 varchar(64),
  ADD COLUMN prompt_cache_status varchar(16)
    CHECK (prompt_cache_status IN ('hit', 'miss', 'reloaded', 'fallback'));

CREATE UNIQUE INDEX ai_provider_response_attempt_general_delivery_uidx
  ON ai_provider_response_attempt(
    tenant_id,
    student_id,
    request_id,
    operation,
    attempt_number
  )
  WHERE document_id IS NULL;
