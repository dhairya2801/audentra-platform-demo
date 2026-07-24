ALTER TABLE document_record
  DROP CONSTRAINT document_record_category_check;

ALTER TABLE document_record
  ADD CONSTRAINT document_record_category_check CHECK (
    category IN (
      'identity',
      'residency',
      'transcript',
      'financial_aid',
      'health',
      'consent',
      'other'
    )
  );

ALTER TABLE document_record
  DROP CONSTRAINT document_record_status_check;

ALTER TABLE document_record
  ADD CONSTRAINT document_record_status_check CHECK (
    status IN (
      'placeholder',
      'uploaded',
      'processing',
      'needs_review',
      'under_review',
      'accepted',
      'rejected'
    )
  );

ALTER TABLE document_record
  ADD COLUMN storage_key varchar(512),
  ADD COLUMN sha256 char(64),
  ADD COLUMN extraction jsonb;

CREATE UNIQUE INDEX document_record_storage_key_idx
  ON document_record(storage_key)
  WHERE storage_key IS NOT NULL;
