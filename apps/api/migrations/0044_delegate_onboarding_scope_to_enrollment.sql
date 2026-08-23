-- Parent and guardian access is always presented through the student portal.
-- The former standalone `onboarding` grant is a subset of My Enrollment, not
-- a navigable parent-facing page. Preserve each delegate's first grant order
-- while canonicalizing existing authorizations for active links and sessions.

UPDATE ferpa_delegate AS delegate
SET scopes = (
  SELECT array_agg(normalized.scope ORDER BY normalized.first_position) AS scopes
  FROM (
    SELECT CASE WHEN item.scope='onboarding' THEN 'enrollment' ELSE item.scope END AS scope,
           min(item.position) AS first_position
    FROM unnest(delegate.scopes) WITH ORDINALITY AS item(scope, position)
    GROUP BY CASE WHEN item.scope='onboarding' THEN 'enrollment' ELSE item.scope END
  ) AS normalized
),
    updated_at = NOW()
WHERE 'onboarding'=ANY(delegate.scopes);
