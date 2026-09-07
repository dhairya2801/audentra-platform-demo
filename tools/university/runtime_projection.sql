-- Seed-time compatibility projection. Runtime authorities: public profiles,
-- requirements, messages, work items and appointments; university academics,
-- ledger, physical housing, policies, consent and case completion evidence.
-- :tenant is bound by the importer; never execute across all tenants.

UPDATE admission_offer a SET program_id=p.id
 FROM university.student s JOIN program p ON lower(p.code)=lower(s.program_id) AND p.tenant_id=s.tenant_id
 WHERE s.tenant_id=:tenant AND a.tenant_id=s.tenant_id AND a.student_id::text=s.id;

UPDATE document_record d SET status=lower(w.status)
 FROM university.document w
 WHERE w.tenant_id=:tenant AND d.tenant_id=w.tenant_id AND d.id::text=w.id
   AND w.status<>'NOT_SUBMITTED';

UPDATE student_requirement r SET
 status=CASE evidence.worst WHEN 5 THEN 'rejected' WHEN 4 THEN 'ready'
   WHEN 3 THEN 'under_review' WHEN 2 THEN 'submitted' ELSE 'completed' END,
 progress_percent=CASE WHEN evidence.worst=1 THEN 100 ELSE 0 END
 FROM enrollment_journey j, requirement_definition_version def,
 (SELECT student_id, CASE category WHEN 'transcript' THEN 'official_transcript'
   WHEN 'photo_id' THEN 'identity_document' WHEN 'immunization' THEN 'immunization_record'
   ELSE 'financial_aid_verification' END AS code,
   max(CASE status WHEN 'ACCEPTED' THEN 1 WHEN 'WAIVED' THEN 1
     WHEN 'UPLOADED' THEN 2 WHEN 'UNDER_REVIEW' THEN 3 WHEN 'NOT_SUBMITTED' THEN 4 ELSE 5 END) AS worst
  FROM university.document WHERE tenant_id=:tenant AND category IN
   ('transcript','photo_id','immunization','verification_worksheet','tax_return_transcript')
  GROUP BY student_id,CASE category WHEN 'transcript' THEN 'official_transcript'
   WHEN 'photo_id' THEN 'identity_document' WHEN 'immunization' THEN 'immunization_record'
   ELSE 'financial_aid_verification' END) evidence
 WHERE r.journey_id=j.id AND j.tenant_id=r.tenant_id AND r.tenant_id=:tenant AND def.tenant_id=r.tenant_id AND def.id=r.requirement_definition_version_id
   AND j.student_id::text=evidence.student_id AND def.code=evidence.code;

UPDATE student_requirement r SET status=CASE WHEN EXISTS(
 SELECT 1 FROM university.ledger l WHERE l.tenant_id=r.tenant_id AND l.student_id=j.student_id::text
  AND l.kind='payment' AND l.description='Enrollment deposit credited to tuition') THEN 'completed' ELSE 'ready' END,
 progress_percent=CASE WHEN EXISTS(SELECT 1 FROM university.ledger l WHERE l.tenant_id=r.tenant_id
  AND l.student_id=j.student_id::text AND l.kind='payment' AND l.description='Enrollment deposit credited to tuition') THEN 100 ELSE 0 END
 FROM enrollment_journey j, requirement_definition_version d WHERE r.journey_id=j.id AND j.tenant_id=r.tenant_id AND r.tenant_id=:tenant AND d.tenant_id=r.tenant_id
  AND d.id=r.requirement_definition_version_id AND d.code='enrollment_deposit';

UPDATE student_requirement r SET status=CASE WHEN h.status IN ('assigned','exempt') THEN 'completed' ELSE 'submitted' END,
 progress_percent=CASE WHEN h.status IN ('assigned','exempt') THEN 100 ELSE 50 END
 FROM enrollment_journey j, requirement_definition_version d,university.housing h
 WHERE r.journey_id=j.id AND j.tenant_id=r.tenant_id AND r.tenant_id=:tenant AND d.tenant_id=r.tenant_id AND h.tenant_id=r.tenant_id
  AND j.student_id::text=h.student_id AND h.ends_at IS NULL
  AND d.id=r.requirement_definition_version_id AND d.code='housing_preference';

-- Optional family authorization must never become a registration hold.
UPDATE student_requirement r SET retired_at=coalesce(r.retired_at,now()),
 retired_reason=coalesce(r.retired_reason,'inactive'),version=r.version+1
 FROM enrollment_journey j,requirement_definition_version d
 WHERE r.tenant_id=:tenant AND j.tenant_id=r.tenant_id AND j.id=r.journey_id
 AND d.tenant_id=r.tenant_id AND d.id=r.requirement_definition_version_id
 AND d.code='family_permissions' AND r.retired_at IS NULL;

UPDATE enrollment_journey j SET status=CASE
 WHEN EXISTS(SELECT 1 FROM student_requirement r WHERE r.tenant_id=j.tenant_id AND r.journey_id=j.id
  AND r.retired_at IS NULL AND r.status NOT IN ('completed','waived','not_applicable')) THEN 'in_progress' ELSE 'completed' END
 WHERE j.tenant_id=:tenant;

-- Awards are annual commitments, not postings. No legacy award survives with
-- impossible eligibility or amounts. The ledger remains the account authority.
DELETE FROM student_financial_award WHERE tenant_id=:tenant;
INSERT INTO student_financial_award
 (id,tenant_id,student_id,academic_year,source,name,type,offered_amount_cents,accepted_amount_cents,status,requires_action)
 SELECT md5('v3-award:'||a.id)::uuid,a.tenant_id,a.student_id::uuid,a.aid_year,CASE WHEN f.source='employment' THEN 'institutional' ELSE f.source END,f.name,
 CASE WHEN f.posts_to_account=0 THEN 'work_study' WHEN lower(f.name) LIKE '%loan%' THEN 'loan' ELSE 'grant' END,
 a.offered_cents,a.accepted_cents,a.status,a.status='offered'
 FROM university.award a JOIN university.fund f ON f.tenant_id=a.tenant_id AND f.id=a.fund_id WHERE a.tenant_id=:tenant;

-- Legacy sample messages were a separate world. Keep only the actual v3
-- delivered communications; new product sends remain canonical student_message.
DELETE FROM student_message WHERE tenant_id=:tenant;
INSERT INTO student_message(id,tenant_id,student_id,subject,body,sender_name,sent_at)
 SELECT md5('v3-message:'||id)::uuid,tenant_id,student_id::uuid,'University update',body,
 'Student Services',sent_at::timestamptz FROM university.communication
 WHERE tenant_id=:tenant AND audience='student' AND delivery='delivered';

INSERT INTO staff_role_capability(tenant_id,role_code,capability)
 SELECT :tenant,'operations_lead',capability FROM unnest(ARRAY[
  'edward.act','edward.follow_up.create','edward.work_item.update','edward.email.prepare',
  'edward.student.any','edward.cohort.follow_up.create']) capability ON CONFLICT DO NOTHING;

-- Restore bounded roles after importing the expanded roster. Managers follow
-- actual manager relationships; individual staff act only in their caseload.
UPDATE staff_member m SET role_code=CASE WHEN EXISTS(
 SELECT 1 FROM university.staff report WHERE report.tenant_id=w.tenant_id AND report.manager_id=w.id
) THEN 'operations_lead' ELSE 'staff' END
FROM university.runtime_link l,university.staff w WHERE m.tenant_id=:tenant
AND l.tenant_id=m.tenant_id AND l.kind='staff' AND l.runtime_id=m.id
AND w.tenant_id=l.tenant_id AND w.id=l.world_id;
INSERT INTO staff_role_capability(tenant_id,role_code,capability)
 SELECT :tenant,'staff',capability FROM unnest(ARRAY[
 'edward.act','edward.follow_up.create','edward.work_item.update','edward.email.prepare'
 ]) capability ON CONFLICT DO NOTHING;

-- Enrollment deposits shown in the product are actual posted deposit evidence.
DELETE FROM payment_transaction WHERE tenant_id=:tenant;
INSERT INTO payment_transaction(id,tenant_id,student_id,offer_id,type,amount_cents,status,processor,processor_reference,created_at)
SELECT md5('v3-deposit:'||l.id)::uuid,l.tenant_id,l.student_id::uuid,a.id,
 'enrollment_deposit',-l.amount_cents,'succeeded','dummy','university:'||l.id,l.posted_at::timestamptz
FROM university.ledger l JOIN admission_offer a ON a.tenant_id=l.tenant_id AND a.student_id::text=l.student_id
WHERE l.tenant_id=:tenant AND l.description='Enrollment deposit credited to tuition';

-- Retire incompatible v2 snapshots. These v3 domains are computed directly
-- from account postings, document revisions and SAP evaluations. In particular
-- there is no signed v3 installment contract to justify an old sample plan.
DELETE FROM student_payment_plan WHERE tenant_id=:tenant;
DELETE FROM student_financial_summary WHERE tenant_id=:tenant;
DELETE FROM financial_document_requirement WHERE tenant_id=:tenant;
DELETE FROM student_sap_status WHERE tenant_id=:tenant;
