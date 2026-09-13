-- Payment work is operational execution; only settlement evidence permits completion.
-- This trigger never writes payment amounts, statuses or the ledger.
CREATE FUNCTION university.guard_payment_work_completion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE linked_payment text; payment_status text;
BEGIN
 SELECT world_id INTO linked_payment FROM university.runtime_link
 WHERE tenant_id=NEW.tenant_id AND kind='payment_work_item' AND runtime_id=NEW.id;
 IF linked_payment IS NOT NULL AND NEW.status='done' AND OLD.status<>'done' THEN
   SELECT status INTO payment_status FROM university.payment
   WHERE tenant_id=NEW.tenant_id AND id=linked_payment FOR SHARE;
   IF payment_status IS DISTINCT FROM 'posted' THEN
     RAISE EXCEPTION 'Payment work cannot complete without posted settlement evidence' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER university_payment_work_guard BEFORE UPDATE ON public.staff_work_item
 FOR EACH ROW EXECUTE FUNCTION university.guard_payment_work_completion();
