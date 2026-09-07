-- Cohort-wide requirement retirement must remain bounded before freshly seeded
-- tables have planner statistics. Both predicates are also used by live flow publication.
CREATE INDEX student_requirement_retirement_lookup_idx
 ON student_requirement(tenant_id,retired_at,id) WHERE retired_at IS NOT NULL;
CREATE INDEX student_experience_update_requirement_pending_idx
 ON student_experience_update(tenant_id,requirement_id)
 WHERE status IN ('pending','deferred');
