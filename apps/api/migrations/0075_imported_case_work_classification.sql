-- Formal cases coordinate an institutional objective; individual document work
-- already has its own source-linked card. Correct only the importer's original
-- default classification, preserving ownership, status, evidence and case steps.
-- Notification cases represent communication work; the others remain requests.
UPDATE public.staff_work_item item
SET work_type=CASE WHEN w.kind='notification' THEN 'communication' ELSE 'enrollment' END,
    action_type=CASE WHEN w.kind='notification' THEN 'communication_response' ELSE 'staff_decision' END,
    version=item.version+1, updated_at=now()
FROM university.runtime_link link
JOIN university.workflow w ON w.tenant_id=link.tenant_id AND w.id=link.world_id
WHERE link.kind='workflow' AND item.tenant_id=link.tenant_id AND item.id=link.runtime_id
  AND item.work_type='document_review' AND item.action_type='enrollment_follow_up'
  AND item.source_type IS NULL;
