-- Optimistic row versions and historical revision ordinals are independent.
CREATE OR REPLACE FUNCTION university.sync_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE doc university.document%ROWTYPE; moment text; prior_scope text; target text; next_revision integer;
BEGIN
 IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
 prior_scope := current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 SELECT * INTO doc FROM university.document WHERE tenant_id=NEW.tenant_id
  AND id=NEW.id::text FOR UPDATE;
 IF FOUND THEN
  target := CASE NEW.status WHEN 'processing' THEN 'UNDER_REVIEW'
    WHEN 'needs_review' THEN 'UNDER_REVIEW' ELSE upper(NEW.status) END;
  IF target IN ('UPLOADED','UNDER_REVIEW','ACCEPTED','REJECTED','WAIVED','EXPIRED','NEEDS_RESUBMISSION')
     AND target<>doc.status THEN
   SELECT value INTO moment FROM university.meta WHERE tenant_id=NEW.tenant_id AND key='clock';
   SELECT coalesce(max(revision),0)+1 INTO next_revision FROM university.document_revision
    WHERE tenant_id=NEW.tenant_id AND document_id=doc.id;
   UPDATE university.document SET status=target, version=version+1
    WHERE tenant_id=NEW.tenant_id AND id=doc.id;
   INSERT INTO university.document_revision
     (tenant_id,id,document_id,revision,status,effective_at,recorded_at,reason,source)
   VALUES(NEW.tenant_id,NEW.id::text||':runtime:'||(doc.version+1),doc.id,next_revision,
     target,moment,moment,'Canonical product document status changed; consult the staff review decision for details.','document_record');
   INSERT INTO university.event
     (tenant_id,id,student_id,entity_type,entity_id,effective_at,recorded_at,actor,from_state,to_state,description,visibility,correlation_id)
   VALUES(NEW.tenant_id,NEW.id::text||':runtime:'||(doc.version+1),NEW.student_id::text,'document',doc.id,
     moment,moment,'product_document_workflow',doc.status,target,'Document review status changed','student',NEW.id::text);
  END IF;
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
