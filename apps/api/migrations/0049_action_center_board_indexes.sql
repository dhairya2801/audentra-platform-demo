-- Action Center at university scale: the board is queried, never dumped.
--
-- Open work is the working set; closed work is history. The default page
-- (open, priority → due → updated) and the operational filters (overdue,
-- stale in-progress, unassigned, per-owner, per-component) each get an
-- index over the open subset so a page costs the same at 2,500 or 250,000
-- items. The priority expression matches the repository's ORDER BY verbatim.

CREATE INDEX IF NOT EXISTS staff_work_item_open_priority_idx
  ON staff_work_item (
    tenant_id,
    (CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END),
    due_at,
    updated_at DESC,
    id
  )
  WHERE status IN ('todo', 'in_progress', 'follow_up_required', 'blocked');

CREATE INDEX IF NOT EXISTS staff_work_item_open_due_idx
  ON staff_work_item (tenant_id, due_at)
  WHERE status IN ('todo', 'in_progress', 'follow_up_required', 'blocked');

CREATE INDEX IF NOT EXISTS staff_work_item_open_assignee_idx
  ON staff_work_item (tenant_id, assignee_id, updated_at DESC)
  WHERE status IN ('todo', 'in_progress', 'follow_up_required', 'blocked');

CREATE INDEX IF NOT EXISTS staff_work_item_open_component_idx
  ON staff_work_item (tenant_id, component)
  WHERE status IN ('todo', 'in_progress', 'follow_up_required', 'blocked');

CREATE INDEX IF NOT EXISTS staff_work_item_in_progress_updated_idx
  ON staff_work_item (tenant_id, updated_at)
  WHERE status = 'in_progress';

-- History is read per item (one page's worth), never per tenant.
CREATE INDEX IF NOT EXISTS staff_work_log_item_idx
  ON staff_work_log (tenant_id, work_item_id, occurred_at DESC);

-- The SLA sweep selects due follow-ups and blocked reviews across tenants.
CREATE INDEX IF NOT EXISTS staff_work_item_follow_up_due_idx
  ON staff_work_item (follow_up_at)
  WHERE status = 'follow_up_required' AND follow_up_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS staff_work_item_blocker_review_due_idx
  ON staff_work_item (blocker_review_at)
  WHERE status = 'blocked' AND blocker_review_at IS NOT NULL;
