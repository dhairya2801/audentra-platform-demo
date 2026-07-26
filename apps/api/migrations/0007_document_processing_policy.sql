ALTER TABLE document_record
  ADD COLUMN processing_mode varchar(24) NOT NULL DEFAULT 'agentic';

UPDATE document_record
SET processing_mode = CASE
  WHEN category IN ('identity', 'transcript') THEN 'agentic'
  ELSE 'manual_review'
END;

ALTER TABLE document_record
  ADD CONSTRAINT document_record_processing_mode_check CHECK (
    processing_mode IN ('agentic', 'manual_review')
  );
