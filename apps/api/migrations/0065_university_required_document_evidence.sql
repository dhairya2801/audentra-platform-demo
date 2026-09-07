-- An accepted file is not necessarily a satisfied multi-document requirement.
CREATE FUNCTION university.required_document_status(scope_tenant uuid, requirement uuid)
 RETURNS text LANGUAGE plpgsql AS $$
DECLARE prior_scope text; subject_id text; requirement_code text; kinds text[];
 evidence_count integer; severity integer;
BEGIN
 prior_scope:=current_setting('audentra.tenant_id',true);
 PERFORM set_config('audentra.tenant_id',scope_tenant::text,true);
 SELECT s.id,d.code INTO subject_id,requirement_code
 FROM student_requirement r JOIN enrollment_journey j ON j.tenant_id=r.tenant_id AND j.id=r.journey_id
 JOIN requirement_definition_version d ON d.tenant_id=r.tenant_id AND d.id=r.requirement_definition_version_id
 JOIN university.student s ON s.tenant_id=r.tenant_id AND s.id=j.student_id::text
 WHERE r.tenant_id=scope_tenant AND r.id=requirement;
 kinds:=CASE requirement_code
  WHEN 'official_transcript' THEN ARRAY['transcript']
  WHEN 'identity_document' THEN ARRAY['photo_id']
  WHEN 'immunization_record' THEN ARRAY['immunization']
  WHEN 'financial_aid_verification' THEN ARRAY['verification_worksheet','tax_return_transcript']
 END;
 IF kinds IS NOT NULL THEN
  SELECT count(*),max(CASE status WHEN 'ACCEPTED' THEN 0 WHEN 'WAIVED' THEN 0
    WHEN 'NOT_SUBMITTED' THEN 2 WHEN 'REJECTED' THEN 3 WHEN 'EXPIRED' THEN 3
    WHEN 'NEEDS_RESUBMISSION' THEN 3 ELSE 1 END)
  INTO evidence_count,severity FROM university.document
  WHERE tenant_id=scope_tenant AND student_id=subject_id AND category=ANY(kinds);
 END IF;
 PERFORM set_config('audentra.tenant_id',coalesce(prior_scope,''),true);
 IF evidence_count IS NULL OR evidence_count=0 THEN RETURN NULL; END IF;
 RETURN CASE severity WHEN 0 THEN 'completed' WHEN 1 THEN 'under_review'
  WHEN 2 THEN 'ready' ELSE 'rejected' END;
END $$;

CREATE FUNCTION university.guard_requirement_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE evidence_status text;
BEGIN
 IF NEW.status='completed' AND NEW.status IS DISTINCT FROM OLD.status THEN
  evidence_status:=university.required_document_status(NEW.tenant_id,NEW.id);
  IF evidence_status IS NOT NULL AND evidence_status<>'completed' THEN
   RAISE EXCEPTION 'Named university document evidence is not complete'
    USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER university_requirement_evidence_guard BEFORE UPDATE OF status ON student_requirement
 FOR EACH ROW EXECUTE FUNCTION university.guard_requirement_evidence();
