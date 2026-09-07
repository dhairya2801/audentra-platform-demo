-- New product records join the university world in the originating transaction.
CREATE FUNCTION university.insert_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior_scope text; moment text; category text; office text;
BEGIN
 prior_scope:=current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 SELECT value INTO moment FROM university.meta WHERE tenant_id=NEW.tenant_id AND key='clock';
 IF moment IS NOT NULL AND EXISTS(SELECT 1 FROM university.student WHERE tenant_id=NEW.tenant_id AND id=NEW.student_id::text) THEN
  category:=CASE NEW.category WHEN 'identity' THEN 'photo_id' ELSE NEW.category END;
  office:=CASE category WHEN 'transcript' THEN 'REG' ELSE 'ADM' END;
  INSERT INTO university.document(tenant_id,id,student_id,category,status,office_id,version)
  VALUES(NEW.tenant_id,NEW.id::text,NEW.student_id::text,category,
   CASE NEW.status WHEN 'placeholder' THEN 'UPLOADED' WHEN 'processing' THEN 'UNDER_REVIEW' WHEN 'needs_review' THEN 'UNDER_REVIEW' ELSE upper(NEW.status) END,office,1)
  ON CONFLICT DO NOTHING;
  IF FOUND THEN
   INSERT INTO university.document_revision(tenant_id,id,document_id,revision,status,effective_at,recorded_at,reason,source)
   SELECT tenant_id,id||':runtime:1',id,1,status,moment,moment,'Uploaded through the student portal','document_record'
   FROM university.document WHERE tenant_id=NEW.tenant_id AND id=NEW.id::text;
  END IF;
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
CREATE TRIGGER university_document_insert AFTER INSERT ON document_record
 FOR EACH ROW EXECUTE FUNCTION university.insert_document();

CREATE FUNCTION university.insert_appointment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior_scope text; staff_id text;
BEGIN
 prior_scope:=current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 SELECT world_id INTO staff_id FROM university.runtime_link WHERE tenant_id=NEW.tenant_id AND kind='staff' AND runtime_id=NEW.staff_member_id;
 IF staff_id IS NOT NULL THEN
  INSERT INTO university.appointment(tenant_id,id,student_id,staff_id,starts_at,ends_at,status,purpose)
  VALUES(NEW.tenant_id,NEW.id::text,NEW.student_id::text,staff_id,
   to_char(NEW.starts_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
   to_char(coalesce(NEW.ends_at,NEW.starts_at+interval '30 minutes') AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
   CASE NEW.status WHEN 'rescheduled' THEN 'cancelled' ELSE NEW.status END,coalesce(NEW.notes,NEW.type)) ON CONFLICT DO NOTHING;
  INSERT INTO university.runtime_link(tenant_id,kind,world_id,runtime_id)
   VALUES(NEW.tenant_id,'appointment',NEW.id::text,NEW.id) ON CONFLICT DO NOTHING;
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
CREATE TRIGGER university_appointment_insert AFTER INSERT ON student_appointment
 FOR EACH ROW EXECUTE FUNCTION university.insert_appointment();

CREATE FUNCTION university.post_deposit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prior_scope text; moment text;
BEGIN
 prior_scope:=current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',NEW.tenant_id::text,true);
 SELECT value INTO moment FROM university.meta WHERE tenant_id=NEW.tenant_id AND key='clock';
 IF moment IS NOT NULL AND NEW.status='succeeded' THEN
  INSERT INTO university.payment(tenant_id,id,student_id,term_id,amount_cents,status,submitted_at,settled_at,method,idempotency_key)
  VALUES(NEW.tenant_id,NEW.id::text,NEW.student_id::text,'2026FA',NEW.amount_cents,'posted',moment,moment,'dummy',NEW.processor_reference) ON CONFLICT DO NOTHING;
  IF FOUND THEN
   INSERT INTO university.ledger(tenant_id,id,student_id,term_id,kind,amount_cents,posted_at,payment_id,description)
   VALUES(NEW.tenant_id,'deposit:'||NEW.id,NEW.student_id::text,'2026FA','payment',-NEW.amount_cents,moment,NEW.id::text,'Enrollment deposit credited to tuition');
  END IF;
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 RETURN NEW;
END $$;
CREATE TRIGGER university_deposit_insert AFTER INSERT ON payment_transaction
 FOR EACH ROW EXECUTE FUNCTION university.post_deposit();
