ALTER TABLE document_record
  ADD COLUMN requirement_id uuid REFERENCES student_requirement(id);

CREATE INDEX document_record_requirement_idx
  ON document_record(tenant_id, student_id, requirement_id, created_at DESC)
  WHERE requirement_id IS NOT NULL;
