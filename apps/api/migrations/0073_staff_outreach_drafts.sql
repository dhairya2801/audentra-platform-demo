-- Staff-authored product workflow state; never institutional or delivery evidence.
CREATE TABLE staff_outreach_draft (
 id uuid PRIMARY KEY,
 tenant_id uuid NOT NULL REFERENCES tenant(id),
 work_item_id uuid NOT NULL REFERENCES staff_work_item(id) ON DELETE RESTRICT,
 student_id uuid NOT NULL REFERENCES student(id) ON DELETE RESTRICT,
 channel varchar(16) NOT NULL DEFAULT 'portal' CHECK(channel='portal'),
 subject varchar(500),
 body varchar(12000) NOT NULL CHECK(char_length(btrim(body))>0),
 status varchar(16) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','sent')),
 version integer NOT NULL DEFAULT 1 CHECK(version>0),
 created_by uuid NOT NULL REFERENCES staff_member(id),
 updated_by uuid NOT NULL REFERENCES staff_member(id),
 communication_id uuid REFERENCES communication_event(id) ON DELETE RESTRICT,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,work_item_id),
 CHECK((status='draft' AND communication_id IS NULL) OR (status='sent' AND communication_id IS NOT NULL))
);
CREATE FUNCTION validate_staff_outreach_draft_links() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM staff_work_item w WHERE w.id=NEW.work_item_id
   AND w.tenant_id=NEW.tenant_id AND w.student_id=NEW.student_id) THEN
  RAISE EXCEPTION 'Outreach draft work/student/tenant mismatch';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM staff_member s WHERE s.tenant_id=NEW.tenant_id AND s.id=NEW.created_by)
 OR NOT EXISTS(SELECT 1 FROM staff_member s WHERE s.tenant_id=NEW.tenant_id AND s.id=NEW.updated_by) THEN
  RAISE EXCEPTION 'Outreach draft staff tenant mismatch';
 END IF;
 IF NEW.communication_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM communication_event c JOIN staff_interaction i
    ON i.tenant_id=c.tenant_id AND i.id=c.interaction_id
  WHERE c.id=NEW.communication_id AND c.tenant_id=NEW.tenant_id
    AND c.student_id=NEW.student_id AND i.work_item_id=NEW.work_item_id
    AND c.channel='portal' AND c.direction='outbound' AND c.delivery_status='delivered'
    AND c.subject IS NOT DISTINCT FROM NEW.subject AND c.body_excerpt=NEW.body
 ) THEN RAISE EXCEPTION 'Sent draft requires matching portal delivery evidence'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER staff_outreach_draft_links BEFORE INSERT OR UPDATE ON staff_outreach_draft
 FOR EACH ROW EXECUTE FUNCTION validate_staff_outreach_draft_links();
ALTER TABLE staff_outreach_draft ENABLE ROW LEVEL SECURITY;
ALTER TABLE staff_outreach_draft FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON staff_outreach_draft
 USING(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);
