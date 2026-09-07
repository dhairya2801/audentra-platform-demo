-- Existing product writes keep their canonical APIs, optimistic concurrency,
-- audit and outbox. Linked v3 evidence changes in the SAME database transaction.
ALTER TABLE document_record DROP CONSTRAINT document_record_status_check;
ALTER TABLE document_record ADD CONSTRAINT document_record_status_check CHECK (
 status IN ('placeholder','uploaded','processing','needs_review','under_review',
            'accepted','rejected','needs_resubmission','waived','expired')
);

ALTER TABLE university.runtime_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE university.runtime_link FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON university.runtime_link
 USING (tenant_id=NULLIF(current_setting('audentra.tenant_id',true),'')::uuid);

CREATE FUNCTION university.sync_profile() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior_scope text;
BEGIN
 prior_scope := current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 UPDATE university.student SET preferred_name=NEW.preferred_name, version=NEW.version
  WHERE tenant_id=NEW.tenant_id AND id=NEW.student_id::text;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
CREATE TRIGGER university_profile_sync AFTER UPDATE ON student_profile
 FOR EACH ROW EXECUTE FUNCTION university.sync_profile();

CREATE FUNCTION university.sync_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE doc university.document%ROWTYPE; moment text; prior_scope text; target text;
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
   UPDATE university.document SET status=target, version=version+1
    WHERE tenant_id=NEW.tenant_id AND id=doc.id;
   INSERT INTO university.document_revision
     (tenant_id,id,document_id,revision,status,effective_at,recorded_at,reason,source)
   VALUES(NEW.tenant_id,NEW.id::text||':runtime:'||(doc.version+1),doc.id,doc.version+1,
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
CREATE TRIGGER university_document_sync AFTER UPDATE ON document_record
 FOR EACH ROW EXECUTE FUNCTION university.sync_document();

CREATE FUNCTION university.sync_appointment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE world_id text; prior_scope text;
BEGIN
 prior_scope := current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 SELECT l.world_id INTO world_id FROM university.runtime_link l
  WHERE l.tenant_id=NEW.tenant_id AND l.kind='appointment' AND l.runtime_id=NEW.id;
 IF world_id IS NOT NULL THEN
  UPDATE university.appointment SET
   starts_at=to_char(NEW.starts_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
   ends_at=to_char(coalesce(NEW.ends_at,NEW.starts_at+interval '30 minutes') AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
   status=CASE NEW.status WHEN 'rescheduled' THEN 'cancelled' ELSE NEW.status END
  WHERE tenant_id=NEW.tenant_id AND id=world_id;
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
CREATE TRIGGER university_appointment_sync AFTER UPDATE ON student_appointment
 FOR EACH ROW EXECUTE FUNCTION university.sync_appointment();

CREATE FUNCTION university.guard_case_completion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE wid text; prior_scope text;
BEGIN
 prior_scope := current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 SELECT world_id INTO wid FROM university.runtime_link
  WHERE tenant_id=NEW.tenant_id AND kind='workflow' AND runtime_id=NEW.id;
 IF wid IS NOT NULL AND NEW.status='done' AND OLD.status<>'done'
   AND EXISTS(SELECT 1 FROM university.workflow_step WHERE tenant_id=NEW.tenant_id
     AND workflow_id=wid AND (status NOT IN ('complete','waived') OR evidence IS NULL)) THEN
   RAISE EXCEPTION 'University case requires every handoff step and completion evidence'
     USING ERRCODE='23514';
 END IF;
 IF wid IS NOT NULL THEN
  UPDATE university.workflow SET
   status=CASE NEW.status WHEN 'done' THEN 'resolved' WHEN 'cancelled' THEN 'cancelled'
     WHEN 'blocked' THEN 'waiting' ELSE 'open' END,
   version=NEW.version,
   resolved_at=CASE WHEN NEW.status='done' THEN (SELECT value FROM university.meta WHERE tenant_id=NEW.tenant_id AND key='clock') ELSE NULL END
  WHERE tenant_id=NEW.tenant_id AND id=wid;
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
CREATE TRIGGER university_case_completion BEFORE UPDATE ON staff_work_item
 FOR EACH ROW EXECUTE FUNCTION university.guard_case_completion();
