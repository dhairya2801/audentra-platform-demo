CREATE TABLE ai_provider_response_attempt (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  student_id uuid NOT NULL REFERENCES student(id),
  document_id uuid NOT NULL REFERENCES document_record(id) ON DELETE CASCADE,
  request_id varchar(128) NOT NULL,
  attempt_number smallint NOT NULL CHECK (attempt_number > 0),
  operation varchar(80) NOT NULL
    CHECK (operation IN ('document_extraction')),
  provider varchar(40) NOT NULL
    CHECK (provider IN ('openrouter', 'groq')),
  requested_model varchar(200),
  response_model varchar(200),
  provider_request_id varchar(200),
  http_status integer CHECK (
    http_status IS NULL OR http_status BETWEEN 100 AND 599
  ),
  response_ok boolean NOT NULL,
  finish_reason varchar(80),
  usage jsonb,
  raw_response_text text,
  response_body jsonb,
  transport_error jsonb,
  duration_ms integer NOT NULL CHECK (duration_ms >= 0),
  recorded_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ai_provider_response_attempt_document_idx
  ON ai_provider_response_attempt(
    tenant_id,
    student_id,
    document_id,
    recorded_at DESC
  );

CREATE UNIQUE INDEX ai_provider_response_attempt_delivery_uidx
  ON ai_provider_response_attempt(
    tenant_id,
    document_id,
    request_id,
    attempt_number
  );
