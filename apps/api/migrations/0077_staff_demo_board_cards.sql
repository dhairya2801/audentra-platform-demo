-- Demo presentation membership. Workflow simulations are never stored as domain evidence.
CREATE TABLE staff_demo_board_card (
  tenant_id uuid NOT NULL REFERENCES tenant(id),
  work_item_id uuid PRIMARY KEY REFERENCES staff_work_item(id) ON DELETE RESTRICT,
  staff_member_id uuid NOT NULL REFERENCES staff_member(id),
  template_key varchar(40) NOT NULL,
  board_id varchar(24) NOT NULL CHECK (board_id IN (
    'en-docs','en-outreach','en-requests','fa-docs','fa-outreach','fa-payments','cl-housing'
  )),
  position integer NOT NULL CHECK (position >= 0),
  scenario_version varchar(40) NOT NULL,
  UNIQUE(tenant_id, staff_member_id, template_key),
  UNIQUE(tenant_id, staff_member_id, position)
);
CREATE FUNCTION validate_staff_demo_board_card() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM staff_work_item w WHERE w.id=NEW.work_item_id
    AND w.tenant_id=NEW.tenant_id AND w.assignee_id=NEW.staff_member_id)
  OR NOT EXISTS (SELECT 1 FROM staff_member s WHERE s.id=NEW.staff_member_id
    AND s.tenant_id=NEW.tenant_id) THEN
    RAISE EXCEPTION 'Demo card work/staff/tenant mismatch';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER staff_demo_board_card_links BEFORE INSERT OR UPDATE ON staff_demo_board_card
  FOR EACH ROW EXECUTE FUNCTION validate_staff_demo_board_card();
ALTER TABLE staff_demo_board_card ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_demo_board_card FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON staff_demo_board_card
  USING(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid)
  WITH CHECK(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
