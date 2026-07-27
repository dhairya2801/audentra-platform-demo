ALTER TABLE document_record
  DROP CONSTRAINT document_record_processing_mode_check;

UPDATE document_record
SET processing_mode = 'classification_only'
WHERE category = 'financial_aid';

ALTER TABLE document_record
  ADD CONSTRAINT document_record_processing_mode_check CHECK (
    processing_mode IN ('agentic', 'classification_only', 'manual_review')
  );
