-- Placeholders are not received files; use the canonical Student Health office.
-- A new file for an unmet named requirement is a revision of that requirement,
-- not a parallel unmet record. Accepted evidence is never silently invalidated.
CREATE OR REPLACE FUNCTION university.insert_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior_scope text; moment text; target_category text; target_status text; office text;
 target university.document%ROWTYPE; ordinal integer; required_code text;
BEGIN
 prior_scope:=current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 SELECT value INTO moment FROM university.meta WHERE tenant_id=NEW.tenant_id AND key='clock';
 IF moment IS NULL THEN
  PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
  RETURN NEW;
 END IF;
 SELECT d.code INTO required_code FROM student_requirement r
 JOIN requirement_definition_version d ON d.tenant_id=r.tenant_id AND d.id=r.requirement_definition_version_id
 WHERE r.tenant_id=NEW.tenant_id AND r.id=NEW.requirement_id;
 target_status:=CASE NEW.status WHEN 'placeholder' THEN 'NOT_SUBMITTED' WHEN 'processing' THEN 'UNDER_REVIEW' WHEN 'needs_review' THEN 'UNDER_REVIEW' ELSE upper(NEW.status) END;
 target_category:=CASE NEW.category WHEN 'identity' THEN 'photo_id' ELSE NEW.category END;
 office:=CASE WHEN target_category='transcript' THEN 'REG' WHEN required_code='financial_aid_verification' THEN 'FA' ELSE 'ADM' END;
 IF required_code='immunization_record' THEN target_category:='immunization'; office:='SHS'; END IF;
 -- A generic financial-aid file still needs classification against the named
 -- worksheet/tax requirements. It cannot approve either requirement by itself.
 IF required_code='financial_aid_verification' THEN target_category:='financial_aid_submission'; END IF;
 SELECT * INTO target FROM university.document WHERE tenant_id=NEW.tenant_id
 AND student_id=NEW.student_id::text AND university.document.category=target_category
 AND status IN ('NOT_SUBMITTED','REJECTED','EXPIRED','NEEDS_RESUBMISSION') ORDER BY id LIMIT 1 FOR UPDATE;
 IF FOUND THEN
  SELECT coalesce(max(revision),0)+1 INTO ordinal FROM university.document_revision
   WHERE tenant_id=NEW.tenant_id AND document_id=target.id;
  UPDATE university.document SET status=target_status,version=version+1 WHERE tenant_id=NEW.tenant_id AND id=target.id;
  INSERT INTO university.runtime_link(tenant_id,kind,world_id,runtime_id)
   VALUES(NEW.tenant_id,'document',target.id,NEW.id)
   ON CONFLICT(tenant_id,kind,world_id) DO UPDATE SET runtime_id=EXCLUDED.runtime_id;
  INSERT INTO university.document_revision(tenant_id,id,document_id,revision,status,effective_at,recorded_at,reason,source)
   VALUES(NEW.tenant_id,NEW.id::text||':upload',target.id,ordinal,target_status,moment,moment,
    'Submission record created; received-file evidence is determined by its status','document_record:'||NEW.id);
 ELSE
  INSERT INTO university.document(tenant_id,id,student_id,category,status,office_id,version)
   VALUES(NEW.tenant_id,NEW.id::text,NEW.student_id::text,target_category,target_status,office,1);
  INSERT INTO university.document_revision(tenant_id,id,document_id,revision,status,effective_at,recorded_at,reason,source)
   VALUES(NEW.tenant_id,NEW.id::text||':upload',NEW.id::text,1,target_status,moment,moment,
    'Submission record created; does not grant credit or satisfy another named requirement','document_record:'||NEW.id);
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;

