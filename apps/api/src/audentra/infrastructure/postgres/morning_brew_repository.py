# ruff: noqa: S608 -- schema identifiers are validated before interpolation.
"""Canonical PostgreSQL reads behind Morning Brew.

Every method is a tenant-scoped SELECT. Nothing here writes, and nothing here
invents a number: each read maps to rows a staff user can open in the portal.

Two design choices are load-bearing:

- **Cohort sizes come back from one statement.** Counting fifteen cohorts with
  fifteen queries would let the funnel drift between them — deposits counted
  after a payment posts, accepted offers counted before. One `COUNT(*) FILTER`
  pass over the roster gives a briefing that adds up.
- **Predicates come from `CohortSql`, never from here.** This module owns the
  aggregation shape; the cohort vocabulary stays in one place, so
  `findStudents` can always list the students behind a Morning Brew number.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError
from audentra.domain.morning_brew import BREW_WINDOW_HOURS, BrewCohort
from audentra.domain.student_cohort import DUE_SOON_HORIZON_DAYS
from audentra.infrastructure.postgres.cohort_sql import (
    DONE_REQUIREMENT_STATUSES,
    OPEN_WORK_STATUSES,
    SQL_IDENTIFIER,
    CohortSql,
    quoted,
    text_bind_expanding,
)

JsonDict = dict[str, Any]

_CLOSED_WORK_STATUSES: tuple[str, ...] = ("done", "cancelled")
_ACTIVE_INQUIRY_STATUSES: tuple[str, ...] = ("new", "open", "waiting_on_student")
_DEADLINE_SAMPLE_LIMIT = 12
_REQUEST_LIMIT = 12


class PostgresMorningBrewRepository:
    """Read-only aggregate reads for the staff Morning Brew briefing."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        schema: str = "public",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not SQL_IDENTIFIER.fullmatch(schema):
            raise ValueError("schema is not a safe PostgreSQL identifier")
        self._engine = engine
        self._schema = schema
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cohort_sql = CohortSql(schema)

    def _table(self, name: str) -> str:
        return f"{self._schema}.{name}"

    # ------------------------------------------------------------------
    # Cohort sizes
    # ------------------------------------------------------------------

    async def count_cohorts(
        self, auth: AuthContext, cohorts: Sequence[BrewCohort]
    ) -> dict[str, int]:
        """Size every named cohort in one pass over the tenant roster."""

        _require_staff(auth)
        if not cohorts:
            return {}
        params: JsonDict = {"tenant_id": _uuid(auth.tenant_id)}
        selections: list[str] = []
        for index, cohort in enumerate(cohorts):
            clauses, cohort_params = self._cohort_sql.predicates(
                cohort.to_filter(), prefix=f"c{index}_"
            )
            params.update(cohort_params)
            predicate = " AND ".join(clauses) if clauses else "TRUE"
            selections.append(f'COUNT(*) FILTER (WHERE {predicate})::integer AS "c{index}"')
        sql = f"""
            SELECT {", ".join(selections)}
            {self._cohort_sql.from_clause()}
            WHERE student.tenant_id = :tenant_id
        """
        async with self._engine.connect() as connection:
            row = (
                (await connection.execute(self._cohort_sql.statement(sql), params))
                .mappings()
                .first()
            )
        if row is None:
            return {cohort.key: 0 for cohort in cohorts}
        return {cohort.key: int(row[f"c{index}"]) for index, cohort in enumerate(cohorts)}

    # ------------------------------------------------------------------
    # Window deltas
    # ------------------------------------------------------------------

    async def count_recent_activity(
        self, auth: AuthContext, *, hours: int = BREW_WINDOW_HOURS
    ) -> dict[str, JsonDict]:
        """Count canonical events inside the window, with their latest time.

        Only rows carrying a timestamp that *proves* the event are counted.
        Where a table records last-write rather than transition time, the caller
        labels the number accordingly; this method never smooths that over.
        """

        _require_staff(auth)
        bounded_hours = max(1, min(int(hours or BREW_WINDOW_HOURS), 168))
        since = self._clock() - timedelta(hours=bounded_hours)
        done = quoted(DONE_REQUIREMENT_STATUSES)
        closed = quoted(_CLOSED_WORK_STATUSES)
        sql = f"""
            SELECT source, COUNT(*)::integer AS count, MAX(occurred_at) AS latest
            FROM (
              SELECT 'deposits_posted' AS source, payment.created_at AS occurred_at
              FROM {self._table("payment_transaction")} AS payment
              WHERE payment.tenant_id = :tenant_id
                AND payment.type = 'enrollment_deposit'
                AND payment.status = 'succeeded'
                AND payment.created_at >= :since
              UNION ALL
              SELECT 'offers_accepted', offer.accepted_at
              FROM {self._table("admission_offer")} AS offer
              WHERE offer.tenant_id = :tenant_id
                AND offer.accepted_at IS NOT NULL
                AND offer.accepted_at >= :since
              UNION ALL
              SELECT 'documents_submitted', document.created_at
              FROM {self._table("document_record")} AS document
              WHERE document.tenant_id = :tenant_id
                AND document.status <> 'placeholder'
                AND document.created_at >= :since
              UNION ALL
              SELECT 'requirements_completed', requirement.updated_at
              FROM {self._table("student_requirement")} AS requirement
              WHERE requirement.tenant_id = :tenant_id
                AND requirement.retired_at IS NULL
                AND requirement.status IN ({done})
                AND requirement.updated_at >= :since
              UNION ALL
              SELECT 'requests_opened', inquiry.created_at
              FROM {self._table("student_inquiry")} AS inquiry
              WHERE inquiry.tenant_id = :tenant_id
                AND inquiry.created_at >= :since
              UNION ALL
              SELECT 'requests_replied', inquiry.last_message_at
              FROM {self._table("student_inquiry")} AS inquiry
              WHERE inquiry.tenant_id = :tenant_id
                AND inquiry.last_message_at >= :since
                AND inquiry.created_at < :since
              UNION ALL
              SELECT 'work_items_opened', item.created_at
              FROM {self._table("staff_work_item")} AS item
              WHERE item.tenant_id = :tenant_id
                AND item.created_at >= :since
              UNION ALL
              SELECT 'work_items_closed', item.updated_at
              FROM {self._table("staff_work_item")} AS item
              WHERE item.tenant_id = :tenant_id
                AND item.status IN ({closed})
                AND item.updated_at >= :since
              UNION ALL
              SELECT 'attention_flags', candidate.created_at
              FROM {self._table("intervention_candidate")} AS candidate
              WHERE candidate.tenant_id = :tenant_id
                AND candidate.created_at >= :since
            ) AS events
            GROUP BY source
        """
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(sql), {"tenant_id": _uuid(auth.tenant_id), "since": since}
                    )
                )
                .mappings()
                .all()
            )
        return {
            str(row["source"]): {
                "count": int(row["count"]),
                "latestAt": _optional_iso(row["latest"]),
            }
            for row in rows
        }

    # ------------------------------------------------------------------
    # Blocker breakdown
    # ------------------------------------------------------------------

    async def blocking_requirement_breakdown(
        self, auth: AuthContext, cohort: BrewCohort, *, limit: int = 8
    ) -> list[JsonDict]:
        """Open blocking requirements inside a cohort, by requirement.

        Counts requirement rows: one student with three open blockers appears in
        three buckets. The caller says so rather than letting a reader add the
        buckets into a headcount.
        """

        _require_staff(auth)
        clauses, params = self._cohort_sql.predicates(cohort.to_filter(), prefix="b_")
        params["tenant_id"] = _uuid(auth.tenant_id)
        params["limit"] = max(1, min(int(limit or 8), 25))
        where = "WHERE student.tenant_id = :tenant_id" + "".join(
            f" AND {clause}" for clause in clauses
        )
        done = quoted(DONE_REQUIREMENT_STATUSES)
        sql = f"""
            SELECT rdv.code AS code, MIN(rdv.title) AS title,
                   COUNT(*)::integer AS requirements,
                   COUNT(DISTINCT student.id)::integer AS students,
                   COUNT(*) FILTER (
                     WHERE req.due_at IS NOT NULL AND req.due_at < NOW()
                   )::integer AS overdue
            {self._cohort_sql.from_clause()}
            JOIN {self._table("enrollment_journey")} AS jr
              ON jr.tenant_id = student.tenant_id AND jr.student_id = student.id
            JOIN {self._table("student_requirement")} AS req
              ON req.tenant_id = jr.tenant_id AND req.journey_id = jr.id
             AND req.retired_at IS NULL
            JOIN {self._table("requirement_definition_version")} AS rdv
              ON rdv.id = req.requirement_definition_version_id
             AND rdv.tenant_id = req.tenant_id
            {where} AND rdv.blocking = 1 AND req.status NOT IN ({done})
            GROUP BY rdv.code
            ORDER BY students DESC, requirements DESC, code
            LIMIT :limit
        """
        async with self._engine.connect() as connection:
            rows = (
                (await connection.execute(self._cohort_sql.statement(sql), params)).mappings().all()
            )
        return [
            {
                "code": str(row["code"]),
                "title": str(row["title"]),
                "requirements": int(row["requirements"]),
                "students": int(row["students"]),
                "overdue": int(row["overdue"]),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Deadline runway
    # ------------------------------------------------------------------

    async def deadline_runway(self, auth: AuthContext) -> list[JsonDict]:
        """Requirement due dates and offer response deadlines, bucketed.

        Both sources are real dates a student is held to. Bucket boundaries use
        the same `DUE_SOON_HORIZON_DAYS` the cohort vocabulary uses, so "due
        soon" means one thing across the whole product.

        Rows collapse to one per requirement *and* bucket rather than one per
        calendar date. Staggered per-student due dates would otherwise print the
        same requirement a dozen times, which reads as a dozen problems instead
        of one, and `students` counts distinct people so the row is a headcount
        rather than a row count.
        """

        _require_staff(auth)
        done = quoted(DONE_REQUIREMENT_STATUSES)
        horizon = f"{DUE_SOON_HORIZON_DAYS} days"
        bucket_sql = (
            "CASE WHEN due_at < DATE_TRUNC('day', NOW()) THEN 'overdue'"
            " WHEN due_at < DATE_TRUNC('day', NOW()) + INTERVAL '1 day' THEN 'today'"
            f" WHEN due_at < DATE_TRUNC('day', NOW()) + INTERVAL '{horizon}' THEN 'this_week'"
            " ELSE 'this_month' END"
        )
        sql = f"""
            SELECT kind, code, MIN(title) AS title, {bucket_sql} AS bucket,
                   MIN(due_at) AS earliest_due_at, MAX(due_at) AS latest_due_at,
                   COUNT(DISTINCT student_id)::integer AS students
            FROM (
              SELECT 'requirement' AS kind, rdv.code AS code, rdv.title AS title,
                     DATE_TRUNC('day', req.due_at) AS due_at,
                     jr.student_id AS student_id
              FROM {self._table("student_requirement")} AS req
              JOIN {self._table("enrollment_journey")} AS jr
                ON jr.id = req.journey_id AND jr.tenant_id = req.tenant_id
              JOIN {self._table("requirement_definition_version")} AS rdv
                ON rdv.id = req.requirement_definition_version_id
               AND rdv.tenant_id = req.tenant_id
              WHERE req.tenant_id = :tenant_id
                AND req.retired_at IS NULL
                AND req.status NOT IN ({done})
                AND req.due_at IS NOT NULL
                AND req.due_at < NOW() + INTERVAL '30 days'
              UNION ALL
              SELECT 'offer_response', 'offer_response',
                     'Admission offer response',
                     DATE_TRUNC('day', offer.response_deadline::timestamptz),
                     offer.student_id
              FROM {self._table("admission_offer")} AS offer
              WHERE offer.tenant_id = :tenant_id
                AND offer.status = 'offered'
                AND offer.response_deadline < (NOW() + INTERVAL '30 days')::date
            ) AS deadlines
            GROUP BY kind, code, bucket
            ORDER BY MIN(due_at), students DESC, code
            LIMIT :limit
        """
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(sql),
                        {"tenant_id": _uuid(auth.tenant_id), "limit": _DEADLINE_SAMPLE_LIMIT},
                    )
                )
                .mappings()
                .all()
            )
        now = self._clock()
        return [
            {
                "kind": str(row["kind"]),
                "code": str(row["code"]),
                "title": str(row["title"]),
                "dueAt": _iso(row["earliest_due_at"]),
                "latestDueAt": _iso(row["latest_due_at"]),
                "spread": row["earliest_due_at"] != row["latest_due_at"],
                "students": int(row["students"]),
                "bucket": str(row["bucket"]),
                "daysAway": _days_away(row["earliest_due_at"], now),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Inbound student requests
    # ------------------------------------------------------------------

    async def open_student_requests(self, auth: AuthContext) -> JsonDict:
        """Unresolved support conversations, oldest wait first.

        The canonical `student_inquiry` is the only inbound channel the platform
        actually owns. There is no mailbox integration, so this is what "the
        messages that need you today" honestly means.
        """

        _require_staff(auth)
        sql = f"""
            SELECT inquiry.id, inquiry.subject, inquiry.message, inquiry.status,
                   inquiry.priority, inquiry.topic_code, inquiry.created_at,
                   inquiry.last_message_at, inquiry.expires_at,
                   member.display_name AS assignee_name,
                   person.first_name, person.last_name,
                   COALESCE(profile.preferred_name, person.preferred_name,
                            person.first_name) AS preferred_name,
                   COALESCE(offer_program.name, 'Program not assigned') AS program_name
            FROM {self._table("student_inquiry")} AS inquiry
            JOIN {self._table("student")} AS student
              ON student.id = inquiry.student_id AND student.tenant_id = inquiry.tenant_id
            JOIN {self._table("person")} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {self._table("student_profile")} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            LEFT JOIN {self._table("staff_member")} AS member
              ON member.id = inquiry.assignee_id AND member.tenant_id = inquiry.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name
              FROM {self._table("admission_offer")} AS offer
              JOIN {self._table("program")} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = student.tenant_id AND offer.student_id = student.id
              ORDER BY offer.created_at DESC, offer.id DESC LIMIT 1
            ) AS offer_program ON true
            WHERE inquiry.tenant_id = :tenant_id
              AND inquiry.archived_at IS NULL
              AND inquiry.status IN :active_statuses
            ORDER BY
              CASE inquiry.priority
                WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4
              END,
              inquiry.last_message_at,
              inquiry.id
            LIMIT :limit
        """
        count_sql = f"""
            SELECT
              COUNT(*)::integer AS total,
              COUNT(*) FILTER (WHERE status = 'new')::integer AS awaiting_first_reply,
              COUNT(*) FILTER (WHERE assignee_id IS NULL)::integer AS unassigned
            FROM {self._table("student_inquiry")}
            WHERE tenant_id = :tenant_id
              AND archived_at IS NULL
              AND status IN :active_statuses
        """
        statement = text(sql).bindparams(
            text_bind_expanding("active_statuses", _ACTIVE_INQUIRY_STATUSES)
        )
        counter = text(count_sql).bindparams(
            text_bind_expanding("active_statuses", _ACTIVE_INQUIRY_STATUSES)
        )
        params = {"tenant_id": _uuid(auth.tenant_id), "limit": _REQUEST_LIMIT}
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement, params)).mappings().all()
            totals = (await connection.execute(counter, params)).mappings().first()
        now = self._clock()
        return {
            "items": [
                {
                    "id": str(row["id"]),
                    "subject": str(row["subject"]),
                    "summary": _excerpt(row["message"]),
                    "status": str(row["status"]),
                    "priority": str(row["priority"]),
                    "topicCode": str(row["topic_code"]),
                    "studentName": f"{row['first_name']} {row['last_name']}",
                    "preferredName": str(row["preferred_name"]),
                    "programName": str(row["program_name"]),
                    "assigneeName": _optional_text(row["assignee_name"]),
                    "createdAt": _iso(row["created_at"]),
                    "lastMessageAt": _iso(row["last_message_at"]),
                    "waitingHours": _hours_since(row["last_message_at"], now),
                }
                for row in rows
            ],
            "total": int(totals["total"]) if totals else 0,
            "awaitingFirstReply": int(totals["awaiting_first_reply"]) if totals else 0,
            "unassigned": int(totals["unassigned"]) if totals else 0,
        }

    # ------------------------------------------------------------------
    # Staff work
    # ------------------------------------------------------------------

    async def staff_work_summary(self, auth: AuthContext) -> JsonDict:
        """Open Action Center work, split the way a morning stand-up asks for it."""

        _require_staff(auth)
        sql = f"""
            SELECT
              COUNT(*)::integer AS open_items,
              COUNT(*) FILTER (WHERE priority = 'urgent')::integer AS urgent,
              COUNT(*) FILTER (WHERE escalated)::integer AS escalated,
              COUNT(*) FILTER (
                WHERE due_at IS NOT NULL AND due_at < NOW()
              )::integer AS overdue,
              COUNT(*) FILTER (WHERE assignee_id IS NULL)::integer AS unassigned,
              COUNT(*) FILTER (
                WHERE assignee_id = :actor_id
              )::integer AS assigned_to_me
            FROM {self._table("staff_work_item")}
            WHERE tenant_id = :tenant_id AND status IN :open_statuses
        """
        statement = text(sql).bindparams(text_bind_expanding("open_statuses", OPEN_WORK_STATUSES))
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "actor_id": _optional_uuid(auth.actor_id),
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return {
                "openItems": 0,
                "urgent": 0,
                "escalated": 0,
                "overdue": 0,
                "unassigned": 0,
                "assignedToMe": 0,
            }
        return {
            "openItems": int(row["open_items"]),
            "urgent": int(row["urgent"]),
            "escalated": int(row["escalated"]),
            "overdue": int(row["overdue"]),
            "unassigned": int(row["unassigned"]),
            "assignedToMe": int(row["assigned_to_me"]),
        }

    # ------------------------------------------------------------------
    # Cohort samples
    # ------------------------------------------------------------------

    async def cohort_sample(
        self, auth: AuthContext, cohort: BrewCohort, *, limit: int = 4
    ) -> list[JsonDict]:
        """A few named students behind a headline, ordered by urgency.

        The briefing shows names because an enrollment leader acts on people,
        not on counts — and because a sample makes a wrong number obvious.
        """

        _require_staff(auth)
        clauses, params = self._cohort_sql.predicates(cohort.to_filter(), prefix="s_")
        params["tenant_id"] = _uuid(auth.tenant_id)
        params["limit"] = max(1, min(int(limit or 4), 10))
        where = "WHERE student.tenant_id = :tenant_id" + "".join(
            f" AND {clause}" for clause in clauses
        )
        sql = f"""
            SELECT student.id, person.first_name, person.last_name,
              COALESCE(offer_detail.program_name, 'Program not assigned') AS program_name,
              progress.open_blocking_count, progress.next_due_at
            {self._cohort_sql.from_clause()}
            LEFT JOIN LATERAL (
              SELECT program.name AS program_name
              FROM {self._table("admission_offer")} AS offer
              JOIN {self._table("program")} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = student.tenant_id AND offer.student_id = student.id
              ORDER BY offer.created_at DESC, offer.id DESC LIMIT 1
            ) AS offer_detail ON true
            LEFT JOIN LATERAL ({self._cohort_sql.requirement_progress()}) AS progress ON true
            {where}
            ORDER BY progress.next_due_at NULLS LAST,
                     progress.open_blocking_count DESC,
                     person.last_name, person.first_name, student.id
            LIMIT :limit
        """
        async with self._engine.connect() as connection:
            rows = (
                (await connection.execute(self._cohort_sql.statement(sql), params)).mappings().all()
            )
        now = self._clock()
        return [
            {
                "id": str(row["id"]),
                "name": f"{row['first_name']} {row['last_name']}",
                "programName": str(row["program_name"]),
                "openBlockingCount": int(row["open_blocking_count"] or 0),
                "nextDueAt": _optional_iso(row["next_due_at"]),
                "nextDueDays": (
                    _days_away(row["next_due_at"], now) if row["next_due_at"] is not None else None
                ),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Freshness of the deterministic engagement scan
    # ------------------------------------------------------------------

    async def engagement_scan_freshness(self, auth: AuthContext) -> JsonDict:
        """When the scheduled engagement scan last projected this tenant.

        Morning Brew shows attention flags from that scan. If the scan has not
        run, the honest answer is "not computed", never zero.
        """

        _require_staff(auth)
        sql = f"""
            SELECT COUNT(*)::integer AS snapshots, MAX(projected_at) AS projected_at
            FROM {self._table("student_engagement_snapshot")}
            WHERE tenant_id = :tenant_id
        """
        async with self._engine.connect() as connection:
            row = (
                (await connection.execute(text(sql), {"tenant_id": _uuid(auth.tenant_id)}))
                .mappings()
                .first()
            )
        snapshots = int(row["snapshots"]) if row else 0
        return {
            "available": snapshots > 0,
            "snapshots": snapshots,
            "lastProjectedAt": _optional_iso(row["projected_at"]) if row else None,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _days_away(value: object, now: datetime) -> int | None:
    if not isinstance(value, datetime):
        return None
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return (moment.date() - now.astimezone(UTC).date()).days


def _hours_since(value: object, now: datetime) -> int:
    if not isinstance(value, datetime):
        return 0
    moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return max(0, int((now - moment).total_seconds() // 3600))


def _excerpt(value: object, maximum: int = 180) -> str:
    text_value = str(value or "").strip().replace("\n", " ")
    if len(text_value) <= maximum:
        return text_value
    return f"{text_value[: maximum - 1].rstrip()}…"


def _require_staff(auth: AuthContext) -> None:
    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_ACCESS_REQUIRED", "This route requires a staff identity")


def _uuid(value: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise BadRequestError("VALIDATION_ERROR", "A UUID identifier is invalid") from error


def _optional_uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    raise RuntimeError("Database returned an invalid timestamp")


def _optional_iso(value: object) -> str | None:
    return None if value is None else _iso(value)


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
