-- Preserve originals and decisions while hiding superseded demo submissions.
ALTER TABLE document_record ADD COLUMN superseded_at timestamptz;
CREATE TABLE demo_document_requirement (
 tenant_id uuid NOT NULL REFERENCES tenant(id),
 student_id uuid NOT NULL REFERENCES student(id),
 requirement_id uuid NOT NULL REFERENCES student_requirement(id),
 expected_type text NOT NULL,
 fixture_name text NOT NULL,
 PRIMARY KEY (tenant_id, student_id, requirement_id)
);

ALTER TABLE staff_demo_board_card ADD COLUMN archived_at timestamptz;
