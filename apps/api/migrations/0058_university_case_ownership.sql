-- Keep case reassignment and deadline edits in the same institutional reality.
CREATE OR REPLACE FUNCTION university.guard_case_completion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE wid text; prior_scope text; owner text;
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
  SELECT world_id INTO owner FROM university.runtime_link
   WHERE tenant_id=NEW.tenant_id AND kind='staff' AND runtime_id=NEW.assignee_id;
  IF owner IS NULL OR NEW.due_at IS NULL THEN
   RAISE EXCEPTION 'University cases require an accountable university staff owner and deadline'
    USING ERRCODE='23514';
  END IF;
  UPDATE university.workflow SET
   owner_id=owner,title=NEW.title,
   due_at=to_char(NEW.due_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
   status=CASE NEW.status WHEN 'done' THEN 'resolved' WHEN 'cancelled' THEN 'cancelled'
     WHEN 'blocked' THEN 'waiting' ELSE 'open' END,
   version=NEW.version,
   resolved_at=CASE WHEN NEW.status='done' THEN (SELECT value FROM university.meta WHERE tenant_id=NEW.tenant_id AND key='clock') ELSE NULL END
  WHERE tenant_id=NEW.tenant_id AND id=wid;
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
