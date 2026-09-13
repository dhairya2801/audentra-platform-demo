-- A replacement submission points to the existing university document via
-- runtime_link. Updating an older submission must not overwrite that new head.
CREATE OR REPLACE FUNCTION university.sync_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE doc university.document%ROWTYPE; moment text; prior_scope text; target text; ordinal integer;
BEGIN
 IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NEW; END IF;
 prior_scope:=current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 SELECT d.* INTO doc FROM university.document d
 LEFT JOIN university.runtime_link l ON l.tenant_id=d.tenant_id AND l.kind='document' AND l.world_id=d.id
 WHERE d.tenant_id=NEW.tenant_id AND d.student_id=NEW.student_id::text
 AND (l.runtime_id=NEW.id OR (d.id=NEW.id::text AND l.runtime_id IS NULL)) FOR UPDATE OF d;
 IF FOUND THEN
  target:=CASE NEW.status WHEN 'placeholder' THEN 'NOT_SUBMITTED' WHEN 'processing' THEN 'UNDER_REVIEW'
    WHEN 'needs_review' THEN 'UNDER_REVIEW' ELSE upper(NEW.status) END;
  IF target IN ('NOT_SUBMITTED','UPLOADED','UNDER_REVIEW','ACCEPTED','REJECTED','WAIVED','EXPIRED','NEEDS_RESUBMISSION')
     AND target<>doc.status THEN
   SELECT value INTO moment FROM university.meta WHERE tenant_id=NEW.tenant_id AND key='clock';
   SELECT coalesce(max(revision),0)+1 INTO ordinal FROM university.document_revision
    WHERE tenant_id=NEW.tenant_id AND document_id=doc.id;
   UPDATE university.document SET status=target,version=version+1 WHERE tenant_id=NEW.tenant_id AND id=doc.id;
   INSERT INTO university.document_revision
    (tenant_id,id,document_id,revision,status,effective_at,recorded_at,reason,source)
   VALUES(NEW.tenant_id,NEW.id::text||':runtime:'||ordinal,doc.id,ordinal,target,moment,moment,
    'Canonical submission status changed; official review guidance is in document_review_decision.', 'document_record:'||NEW.id);
   INSERT INTO university.event
    (tenant_id,id,student_id,entity_type,entity_id,effective_at,recorded_at,actor,from_state,to_state,description,visibility,correlation_id)
   VALUES(NEW.tenant_id,NEW.id::text||':runtime:'||ordinal,NEW.student_id::text,'document',doc.id,
    moment,moment,'product_document_workflow',doc.status,target,'Document submission status changed','student',NEW.id::text);
  END IF;
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
