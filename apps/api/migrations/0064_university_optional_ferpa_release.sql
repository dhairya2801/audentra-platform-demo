-- FERPA authorization is a student's optional permission, not a registration
-- condition. Preserve existing consent and the old checklist audit record.
DO $$
DECLARE scope_tenant uuid;
BEGIN
 FOR scope_tenant IN SELECT id FROM tenant LOOP
  PERFORM set_config('audentra.tenant_id',scope_tenant::text,true);
  UPDATE student_requirement r SET retired_at=coalesce(r.retired_at,now()),
    retired_reason=coalesce(r.retired_reason,'inactive'),version=r.version+1
  FROM enrollment_journey j,requirement_definition_version d,university.student s
  WHERE r.tenant_id=scope_tenant AND j.tenant_id=r.tenant_id AND j.id=r.journey_id
    AND d.tenant_id=r.tenant_id AND d.id=r.requirement_definition_version_id
    AND s.tenant_id=r.tenant_id AND s.id=j.student_id::text
    AND d.code='family_permissions' AND r.retired_at IS NULL;
 END LOOP;
 PERFORM set_config('audentra.tenant_id','',true);
END $$;
