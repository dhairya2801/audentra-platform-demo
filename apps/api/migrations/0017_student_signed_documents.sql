CREATE TABLE student_signed_document (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE,
  template_code varchar(100) NOT NULL,
  onboarding_version integer NOT NULL CHECK (onboarding_version > 0),
  title varchar(180) NOT NULL,
  file_name varchar(255) NOT NULL,
  mime_type varchar(80) NOT NULL DEFAULT 'application/pdf'
    CHECK (mime_type = 'application/pdf'),
  size_bytes integer NOT NULL CHECK (size_bytes > 0),
  storage_provider varchar(40) NOT NULL,
  storage_key varchar(512) NOT NULL,
  sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  signer_name varchar(240) NOT NULL,
  signature_method varchar(20) NOT NULL
    CHECK (signature_method IN ('typed', 'drawn')),
  signed_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, student_id, template_code, onboarding_version),
  UNIQUE (tenant_id, storage_key)
);

CREATE INDEX student_signed_document_student_idx
  ON student_signed_document (tenant_id, student_id, signed_at DESC);

CREATE OR REPLACE FUNCTION prevent_student_signed_document_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION
    'student_signed_document is immutable; create a new signed version';
END;
$$;

CREATE TRIGGER student_signed_document_immutable
BEFORE UPDATE OR DELETE ON student_signed_document
FOR EACH ROW
EXECUTE FUNCTION prevent_student_signed_document_mutation();
