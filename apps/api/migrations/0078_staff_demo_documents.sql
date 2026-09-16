-- Original files are canonical; extraction and review simulations stay in the browser.
ALTER TABLE staff_demo_board_card ADD COLUMN preview_template_key varchar(40);
CREATE TABLE staff_demo_document (
  document_id uuid PRIMARY KEY REFERENCES document_record(id),
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  work_item_id uuid NOT NULL REFERENCES staff_demo_board_card(work_item_id),
  received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX staff_demo_document_work_idx ON staff_demo_document(tenant_id,work_item_id,received_at);
CREATE FUNCTION validate_staff_demo_document() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM document_record d JOIN staff_work_item w
    ON w.student_id=d.student_id AND w.tenant_id=d.tenant_id
    WHERE d.id=NEW.document_id AND w.id=NEW.work_item_id AND d.tenant_id=NEW.tenant_id) THEN
    RAISE EXCEPTION 'Demo document student/tenant mismatch';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER staff_demo_document_links BEFORE INSERT OR UPDATE ON staff_demo_document
  FOR EACH ROW EXECUTE FUNCTION validate_staff_demo_document();
ALTER TABLE staff_demo_document ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_demo_document FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON staff_demo_document
  USING(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
