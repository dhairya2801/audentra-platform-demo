-- Deterministic operational work from real payment attempts. These are NOT
-- financial transactions and cannot affect a balance by moving across a board.
INSERT INTO staff_work_item(id,tenant_id,student_id,key,title,description,status,priority,work_type,component,assignee_id,created_at,updated_at)
SELECT md5('vnext-payment-work:'||p.id)::uuid,p.tenant_id,p.student_id::uuid,
 'PAY-'||(('x'||substr(md5(p.id),1,15))::bit(60)::bigint)::text,
 'Review '||p.status||' payment',
 'Review the canonical payment attempt and settlement evidence. Board movement does not change the account.',
 CASE WHEN p.status='pending' THEN 'in_progress' ELSE 'todo' END,
 CASE WHEN p.status IN ('failed','reversed') THEN 'high' ELSE 'medium' END,
 'enrollment','Student Accounts',
 (SELECT l.runtime_id FROM university.staff s JOIN university.runtime_link l
  ON l.tenant_id=s.tenant_id AND l.world_id=s.id AND l.kind='staff'
  WHERE s.tenant_id=p.tenant_id AND s.office_id='SA' AND s.status='active' ORDER BY s.id LIMIT 1),
 p.submitted_at::timestamptz,p.submitted_at::timestamptz
FROM university.payment p WHERE p.tenant_id=:tenant AND p.status IN ('pending','failed','reversed')
ON CONFLICT DO NOTHING;
INSERT INTO university.runtime_link(tenant_id,kind,world_id,runtime_id)
SELECT p.tenant_id,'payment_work_item',p.id,md5('vnext-payment-work:'||p.id)::uuid
FROM university.payment p JOIN staff_work_item w ON w.tenant_id=p.tenant_id AND w.id=md5('vnext-payment-work:'||p.id)::uuid
WHERE p.tenant_id=:tenant ON CONFLICT DO NOTHING;
