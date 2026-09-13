-- Operational writers may not already have set university RLS context.
CREATE OR REPLACE FUNCTION university.guard_payment_work_completion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE linked_payment text; payment_status text; prior_scope text;
BEGIN
 prior_scope:=current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 SELECT world_id INTO linked_payment FROM university.runtime_link
 WHERE tenant_id=NEW.tenant_id AND kind='payment_work_item' AND runtime_id=NEW.id;
 IF linked_payment IS NOT NULL AND NEW.status='done' AND OLD.status<>'done' THEN
   SELECT status INTO payment_status FROM university.payment
   WHERE tenant_id=NEW.tenant_id AND id=linked_payment FOR SHARE;
   IF payment_status IS DISTINCT FROM 'posted' THEN
     RAISE EXCEPTION 'Payment work cannot complete without posted settlement evidence' USING ERRCODE='23514';
   END IF;
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
