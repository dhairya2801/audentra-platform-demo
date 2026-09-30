"""Global directory filters and deterministic paging over the canonical roster.

The UUID checksum is an explicitly illustrative UI score, not an institutional
risk assessment. It is used only to keep mock display filters consistent across
pages and is never stored or used by workflow decisions.
"""

from audentra.core.errors import BadRequestError


def directory_query(roster_sql: str, *, sort: str, view: str, risk: str, stage: str) -> str:
    orders = {
        "recommended": """(id = :featured_student_id) DESC NULLS LAST,
            (completed_tasks > 0) DESC, (total_tasks > 0) DESC,
            (work_open > 0) DESC, completed_tasks DESC, requirement_total DESC,
            last_activity_at DESC, id""",
        "name": "lower(first_name || ' ' || last_name), id",
        "readiness": "completed_tasks::numeric / NULLIF(total_tasks, 0) ASC NULLS LAST, id",
        "risk": "preview_risk DESC, id",
    }
    views = {
        "all": "true",
        "risk": "preview_risk >= 60",
        "blocked": "requirement_blocking_open > 0",
        "inactive": "last_activity_at <= :now - interval '7 days'",
    }
    if (
        sort not in orders
        or view not in views
        or risk not in {"", "Critical", "High", "Medium", "Low"}
        or stage not in {"", "Onboarding", "Enrollment", "Ready"}
    ):
        raise BadRequestError("INVALID_DIRECTORY_FILTER", "Choose a supported directory filter")
    return f"""
        WITH roster AS MATERIALIZED ({roster_sql}),
        enriched AS MATERIALIZED (
            SELECT roster.*,
              CASE WHEN onboarding_status <> 'completed' THEN onboarding_completed
                ELSE requirement_completed END AS completed_tasks,
              CASE WHEN onboarding_status <> 'completed' THEN 8
                ELSE requirement_total END AS total_tasks,
              CASE WHEN onboarding_status <> 'completed' THEN 'Onboarding'
                WHEN requirement_total > 0 AND requirement_completed >= requirement_total
                THEN 'Ready' ELSE 'Enrollment' END AS stage,
              18 + (SELECT sum(ascii(substr(roster.id::text, pos, 1))) % 67
                FROM generate_series(1, length(roster.id::text)) AS pos) AS preview_risk
            FROM roster
        ), scope AS MATERIALIZED (
            SELECT * FROM enriched
            WHERE (:program_filter = '' OR program_name = :program_filter)
              AND (:stage_filter = '' OR stage = :stage_filter)
              AND (:risk_filter = '' OR CASE WHEN preview_risk >= 75 THEN 'Critical'
                WHEN preview_risk >= 60 THEN 'High' WHEN preview_risk >= 30 THEN 'Medium'
                ELSE 'Low' END = :risk_filter)
        ), filtered AS MATERIALIZED (SELECT * FROM scope WHERE {views[view]}),
        stats AS (
            SELECT count(*) AS total_matches,
              LEAST(:offset, GREATEST(0, (count(*) - 1) / :limit) * :limit) AS page_offset
            FROM filtered
        ), metadata AS (
            SELECT stats.*,
              (SELECT jsonb_build_object(
                'students', count(*),
                'completedTasks', coalesce(sum(completed_tasks), 0),
                'totalTasks', coalesce(sum(total_tasks), 0),
                'highRisk', count(*) FILTER (WHERE preview_risk >= 60),
                'blockedStudents', count(*) FILTER (WHERE requirement_blocking_open > 0),
                'blockingSteps', coalesce(sum(requirement_blocking_open), 0),
                'inactiveStudents', count(*) FILTER (
                  WHERE last_activity_at <= :now - interval '7 days')
              ) FROM scope) AS directory_summary,
              jsonb_build_object(
                'programs', ARRAY(SELECT DISTINCT program_name FROM enriched ORDER BY program_name),
                'stages', ARRAY(SELECT DISTINCT stage FROM enriched ORDER BY stage)
              ) AS directory_facets
            FROM stats
        )
        SELECT page.*, metadata.* FROM metadata
        LEFT JOIN LATERAL (
            SELECT * FROM filtered ORDER BY {orders[sort]}
            LIMIT :limit OFFSET metadata.page_offset
        ) AS page ON true
    """  # noqa: S608 -- only code-owned SQL and whitelisted clauses are interpolated.
