# ruff: noqa: S608 -- schema identifiers are validated before interpolation.
"""Pure-read PostgreSQL surface for the staff assistant (Staff Edward).

Every method here is a tenant-scoped SELECT. Nothing in this module writes
canonical state — the assistant is read-only by construction, so its data
layer must be too. The one deliberate exception is the durable staff
conversation store at the bottom of the file, which persists the assistant's
own chat transcript (never student or work state).

Provenance rules this module enforces:

- Tenant comes only from the authenticated ``AuthContext``. Every SQL
  statement filters on it.
- Model-proposed student ids are resolved through ``get_student_overview``
  (or ``search_students``) before any other read runs; an unknown or
  cross-tenant id yields ``None``, never an error revealing existence.
- Nothing here reads the preview workspace repository or any synthetic risk
  field. ``intervention_candidate`` and ``student_engagement_snapshot`` — the
  deterministic, reason-coded attention substrate the scheduler already
  computes — get their first read path here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, NotFoundError
from audentra.domain.student_cohort import (
    DEFAULT_COHORT_PAGE_SIZE,
    CohortFilter,
    CohortResult,
    bounded_page_size,
    validate_group_by,
)
from audentra.infrastructure.postgres.cohort_sql import (
    DONE_REQUIREMENT_STATUSES,
    OPEN_WORK_STATUSES,
    SQL_IDENTIFIER,
    CohortSql,
    escape_like,
    quoted,
    text_bind_expanding,
)

JsonDict = dict[str, Any]

_SQL_IDENTIFIER = SQL_IDENTIFIER
_OPEN_WORK_STATUSES = OPEN_WORK_STATUSES
_DONE_REQUIREMENT_STATUSES = DONE_REQUIREMENT_STATUSES
_escape_like = escape_like
_PRIORITY_RANK_SQL = """
    CASE candidate.priority
      WHEN 'urgent' THEN 1
      WHEN 'high' THEN 2
      WHEN 'medium' THEN 3
      ELSE 4
    END
"""


class PostgresStaffAssistantRepository:
    """Read-only staff-assistant queries plus the durable staff chat store."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        schema: str = "public",
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not _SQL_IDENTIFIER.fullmatch(schema):
            raise ValueError("schema is not a safe PostgreSQL identifier")
        self._engine = engine
        self._schema = schema
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory
        # The one SQL translation of the canonical cohort vocabulary. Morning
        # Brew builds on the same object, so a briefing count and an assistant
        # roster answer resolve through identical predicates.
        self._cohort_sql = CohortSql(schema)

    def _table(self, name: str) -> str:
        return f"{self._schema}.{name}"

    # ------------------------------------------------------------------
    # Student identity
    # ------------------------------------------------------------------

    async def search_students(
        self,
        auth: AuthContext,
        *,
        query: str = "",
        program: str | None = None,
        limit: int = 10,
    ) -> JsonDict:
        """Bounded canonical-roster search by name and/or program.

        Matching is conjunctive over query tokens against first, last, and
        preferred names, so "maria alvarez" narrows rather than widens.
        Returns concise summaries, never full records.
        """

        _require_staff(auth)
        bounded_limit = max(1, min(int(limit or 10), 25))
        tokens = [token for token in re.split(r"\s+", (query or "").strip()) if token][:5]
        clauses: list[str] = []
        params: dict[str, Any] = {"tenant_id": _uuid(auth.tenant_id), "limit": bounded_limit}
        for index, token in enumerate(tokens):
            key = f"token_{index}"
            params[key] = f"%{_escape_like(token)}%"
            clauses.append(
                f"(person.first_name ILIKE :{key} ESCAPE '\\'"
                f" OR person.last_name ILIKE :{key} ESCAPE '\\'"
                f" OR COALESCE(profile.preferred_name, person.preferred_name, '')"
                f" ILIKE :{key} ESCAPE '\\'"
                f" OR COALESCE(student.external_ref, '') ILIKE :{key} ESCAPE '\\')"
            )
        if program:
            params["program"] = f"%{_escape_like(program.strip())}%"
            clauses.append("COALESCE(offer_program.name, '') ILIKE :program ESCAPE '\\'")
        where = f"WHERE student.tenant_id = :tenant_id{''.join(f' AND {c}' for c in clauses)}"
        async with self._engine.connect() as connection:
            rows = (
                (await connection.execute(text(self._roster_search_sql(where)), params))
                .mappings()
                .all()
            )
        return {
            "items": [self._map_roster_row(row) for row in rows],
            "total": len(rows),
            "limit": bounded_limit,
        }

    def _roster_search_sql(self, where: str, *, limit_clause: str = "LIMIT :limit") -> str:
        return f"""
            SELECT student.id, student.external_ref, person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name,
              student.class_year,
              COALESCE(offer_program.name, 'Program not assigned') AS program_name,
              offer_program.status AS offer_status,
              COALESCE(requirement_progress.total_count, 0) AS requirement_total,
              COALESCE(requirement_progress.completed_count, 0) AS requirement_completed,
              COALESCE(requirement_progress.open_blocking_count, 0) AS open_blocking_count,
              requirement_progress.next_due_at
            FROM {self._table("student")} AS student
            JOIN {self._table("person")} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {self._table("student_profile")} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name, offer.status
              FROM {self._table("admission_offer")} AS offer
              JOIN {self._table("program")} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = student.tenant_id
                AND offer.student_id = student.id
              ORDER BY offer.created_at DESC
              LIMIT 1
            ) AS offer_program ON true
            LEFT JOIN LATERAL ({self._requirement_progress_sql()}) AS requirement_progress ON true
            {where}
            ORDER BY person.last_name, person.first_name, student.id
            {limit_clause}
        """

    @staticmethod
    def _map_roster_row(row: Mapping[Any, Any]) -> JsonDict:
        return {
            "id": str(row["id"]),
            "externalRef": _optional_text(row["external_ref"]),
            "name": f"{row['first_name']} {row['last_name']}",
            "preferredName": str(row["preferred_name"]),
            "programName": str(row["program_name"]),
            "classYear": int(row["class_year"]),
            "offerStatus": _optional_text(row["offer_status"]),
            "requirements": {
                "total": int(row["requirement_total"]),
                "completed": int(row["requirement_completed"]),
                "openBlocking": int(row["open_blocking_count"]),
            },
            "nextDueAt": _optional_iso(row["next_due_at"]),
        }

    async def get_student_by_external_ref(
        self, auth: AuthContext, external_ref: str
    ) -> JsonDict | None:
        """Exact institutional-ID lookup inside the authenticated tenant."""

        _require_staff(auth)
        ref = (external_ref or "").strip()
        if not ref:
            return None
        where = (
            "WHERE student.tenant_id = :tenant_id "
            "AND UPPER(student.external_ref) = UPPER(:external_ref)"
        )
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(self._roster_search_sql(where, limit_clause="LIMIT 1")),
                        {"tenant_id": _uuid(auth.tenant_id), "external_ref": ref},
                    )
                )
                .mappings()
                .first()
            )
        return self._map_roster_row(row) if row is not None else None

    async def search_students_fuzzy(
        self, auth: AuthContext, *, query: str, limit: int = 5
    ) -> JsonDict:
        """Close-spelling name suggestions when the exact search found nothing.

        Deterministic: the tenant's roster names are compared with difflib
        ratios in code — no extension requirements, no model involvement.
        Results are suggestions for the staff member to confirm, and carry
        ``matchQuality: "fuzzy"`` so no caller auto-resolves them.
        """

        _require_staff(auth)
        needle = re.sub(r"\s+", " ", (query or "").strip().lower())
        if not needle:
            return {"items": [], "total": 0, "matchQuality": "fuzzy"}
        bounded_limit = max(1, min(int(limit or 5), 10))
        sql = f"""
            SELECT student.id, person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name
            FROM {self._table("student")} AS student
            JOIN {self._table("person")} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {self._table("student_profile")} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            WHERE student.tenant_id = :tenant_id
            LIMIT 10000
        """
        async with self._engine.connect() as connection:
            rows = (
                (await connection.execute(text(sql), {"tenant_id": _uuid(auth.tenant_id)}))
                .mappings()
                .all()
            )
        scored: list[tuple[float, str]] = []
        for row in rows:
            candidates = (
                f"{row['first_name']} {row['last_name']}".lower(),
                f"{row['preferred_name']} {row['last_name']}".lower(),
                str(row["last_name"]).lower(),
            )
            score = max(SequenceMatcher(None, needle, name).ratio() for name in candidates)
            if score >= 0.72:
                scored.append((score, str(row["id"])))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top_ids = [student_id for _, student_id in scored[:bounded_limit]]
        if not top_ids:
            return {"items": [], "total": 0, "matchQuality": "fuzzy"}
        where = "WHERE student.tenant_id = :tenant_id AND student.id = ANY(:ids)"
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(self._roster_search_sql(where, limit_clause="")),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "ids": [_uuid(student_id) for student_id in top_ids],
                        },
                    )
                )
                .mappings()
                .all()
            )
        order = {student_id: index for index, student_id in enumerate(top_ids)}
        items = sorted(
            (self._map_roster_row(row) for row in rows),
            key=lambda item: order.get(str(item["id"]), len(order)),
        )
        return {"items": items, "total": len(items), "matchQuality": "fuzzy"}

    async def get_student_overview(self, auth: AuthContext, student_id: str) -> JsonDict | None:
        """One student's staff-facing overview, or None when the id does not
        resolve inside the authenticated tenant."""

        _require_staff(auth)
        sql = f"""
            SELECT student.id, person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name,
              profile.communication_preference,
              student.class_year,
              COALESCE(onboarding.status, 'not_started') AS onboarding_status,
              COALESCE(offer_detail.program_name, 'Program not assigned') AS program_name,
              offer_detail.status AS offer_status,
              offer_detail.response_deadline,
              offer_detail.deposit_amount_cents,
              COALESCE(deposit.paid, false) AS deposit_paid,
              COALESCE(requirement_progress.total_count, 0) AS requirement_total,
              COALESCE(requirement_progress.completed_count, 0) AS requirement_completed,
              COALESCE(requirement_progress.open_blocking_count, 0) AS open_blocking_count,
              requirement_progress.next_due_at,
              COALESCE(open_work.open_count, 0) AS open_work_count
            FROM {self._table("student")} AS student
            JOIN {self._table("person")} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {self._table("student_profile")} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            LEFT JOIN {self._table("student_onboarding")} AS onboarding
              ON onboarding.student_id = student.id AND onboarding.tenant_id = student.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name AS program_name, offer.status, offer.response_deadline,
                     offer.deposit_amount_cents, offer.id AS offer_id
              FROM {self._table("admission_offer")} AS offer
              JOIN {self._table("program")} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = student.tenant_id
                AND offer.student_id = student.id
              ORDER BY offer.created_at DESC
              LIMIT 1
            ) AS offer_detail ON true
            LEFT JOIN LATERAL (
              SELECT EXISTS (
                SELECT 1 FROM {self._table("payment_transaction")} AS payment
                WHERE payment.tenant_id = student.tenant_id
                  AND payment.student_id = student.id
                  AND payment.type = 'enrollment_deposit'
                  AND payment.status = 'succeeded'
              ) AS paid
            ) AS deposit ON true
            LEFT JOIN LATERAL ({self._requirement_progress_sql()}) AS requirement_progress ON true
            LEFT JOIN LATERAL (
              SELECT COUNT(*)::integer AS open_count
              FROM {self._table("staff_work_item")} AS item
              WHERE item.tenant_id = student.tenant_id
                AND item.student_id = student.id
                AND item.status IN :open_statuses
            ) AS open_work ON true
            WHERE student.tenant_id = :tenant_id AND student.id = :student_id
        """
        statement = text(sql).bindparams(text_bind_expanding("open_statuses", _OPEN_WORK_STATUSES))
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        statement,
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "student_id": _uuid(student_id),
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "name": f"{row['first_name']} {row['last_name']}",
            "preferredName": str(row["preferred_name"]),
            "communicationPreference": _optional_text(row["communication_preference"]),
            "programName": str(row["program_name"]),
            "classYear": int(row["class_year"]),
            "onboardingStatus": str(row["onboarding_status"]),
            "offer": {
                "status": _optional_text(row["offer_status"]),
                "responseDeadline": _optional_iso(row["response_deadline"]),
                "depositAmountCents": (
                    int(row["deposit_amount_cents"])
                    if row["deposit_amount_cents"] is not None
                    else None
                ),
                "depositPaid": bool(row["deposit_paid"]),
            },
            "requirements": {
                "total": int(row["requirement_total"]),
                "completed": int(row["requirement_completed"]),
                "openBlocking": int(row["open_blocking_count"]),
                "nextDueAt": _optional_iso(row["next_due_at"]),
            },
            "openWorkItems": int(row["open_work_count"]),
        }

    # ------------------------------------------------------------------
    # Cohort SQL — delegated to the shared canonical translation
    # ------------------------------------------------------------------
    #
    # These wrappers exist so this file reads the way it always has while the
    # definitions live in exactly one place. Adding a predicate here rather
    # than in `cohort_sql.py` would immediately re-open the drift these
    # methods were extracted to close.

    def _requirement_progress_sql(self) -> str:
        return self._cohort_sql.requirement_progress()

    def _cohort_predicates(self, cohort: CohortFilter) -> tuple[list[str], JsonDict]:
        return self._cohort_sql.predicates(cohort)

    def _deposit_bucket_sql(self) -> str:
        return self._cohort_sql.deposit_bucket()

    def _housing_bucket_sql(self) -> str:
        return self._cohort_sql.housing_bucket()

    def _cohort_from_sql(self) -> str:
        return self._cohort_sql.from_clause()

    def _cohort_statement(self, sql: str, params: JsonDict) -> Any:
        return self._cohort_sql.statement(sql)

    async def find_students(
        self,
        auth: AuthContext,
        cohort: CohortFilter,
        *,
        limit: int = DEFAULT_COHORT_PAGE_SIZE,
    ) -> CohortResult:
        """The cohort itself, plus the true total behind the returned page."""

        _require_staff(auth)
        bounded = bounded_page_size(limit)
        clauses, params = self._cohort_predicates(cohort)
        params["tenant_id"] = _uuid(auth.tenant_id)
        params["limit"] = bounded
        where = "WHERE student.tenant_id = :tenant_id" + "".join(
            f" AND {clause}" for clause in clauses
        )
        from_sql = self._cohort_from_sql()
        list_sql = f"""
            SELECT student.id, person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name,
              student.class_year,
              COALESCE(offer_detail.program_name, 'Program not assigned') AS program_name,
              offer_detail.status AS offer_status,
              offer_detail.response_deadline,
              {self._deposit_bucket_sql()} AS deposit_state,
              COALESCE(deposit.paid, false) AS deposit_paid,
              COALESCE(requirement_progress.total_count, 0) AS requirement_total,
              COALESCE(requirement_progress.completed_count, 0) AS requirement_completed,
              COALESCE(requirement_progress.open_blocking_count, 0) AS open_blocking_count,
              requirement_progress.next_due_at
            {from_sql}
            LEFT JOIN LATERAL (
              SELECT program.name AS program_name, offer.status, offer.response_deadline
              FROM {self._table("admission_offer")} AS offer
              JOIN {self._table("program")} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = student.tenant_id AND offer.student_id = student.id
              ORDER BY offer.created_at DESC LIMIT 1
            ) AS offer_detail ON true
            LEFT JOIN LATERAL (
              SELECT EXISTS (
                SELECT 1 FROM {self._table("payment_transaction")} AS payment
                WHERE payment.tenant_id = student.tenant_id
                  AND payment.student_id = student.id
                  AND payment.type = 'enrollment_deposit'
                  AND payment.status = 'succeeded'
              ) AS paid
            ) AS deposit ON true
            LEFT JOIN LATERAL ({self._requirement_progress_sql()}) AS requirement_progress ON true
            {where}
            ORDER BY person.last_name, person.first_name, student.id
            LIMIT :limit
        """
        count_sql = f"SELECT COUNT(*)::integer AS total {from_sql} {where}"
        async with self._engine.connect() as connection:
            rows = (
                (await connection.execute(self._cohort_statement(list_sql, params), params))
                .mappings()
                .all()
            )
            total_row = (
                (await connection.execute(self._cohort_statement(count_sql, params), params))
                .mappings()
                .first()
            )
        total = int(total_row["total"]) if total_row else 0
        items = [
            {
                "id": str(row["id"]),
                "name": f"{row['first_name']} {row['last_name']}",
                "preferredName": str(row["preferred_name"]),
                "programName": str(row["program_name"]),
                "classYear": int(row["class_year"]),
                "offerStatus": _optional_text(row["offer_status"]),
                "offerResponseDeadline": _optional_iso(row["response_deadline"]),
                "depositState": str(row["deposit_state"]),
                "depositPaid": bool(row["deposit_paid"]),
                "requirements": {
                    "total": int(row["requirement_total"]),
                    "completed": int(row["requirement_completed"]),
                    "openBlocking": int(row["open_blocking_count"]),
                    "nextDueAt": _optional_iso(row["next_due_at"]),
                },
            }
            for row in rows
        ]
        return CohortResult(
            items=items,
            total=total,
            filter_clauses=cohort.describe(),
            truncated=total > len(items),
        )

    async def summarize_students(
        self,
        auth: AuthContext,
        cohort: CohortFilter,
        *,
        group_by: str,
        limit: int = 20,
    ) -> JsonDict:
        """Counts over the same selection `find_students` would return.

        `blocking_requirement` counts *requirement rows*, so one student with
        three open blockers contributes to three buckets; the response says so
        rather than letting the reader assume the buckets sum to a headcount.
        """

        _require_staff(auth)
        group_by = validate_group_by(group_by)
        clauses, params = self._cohort_predicates(cohort)
        params["tenant_id"] = _uuid(auth.tenant_id)
        params["group_limit"] = max(1, min(int(limit or 20), 50))
        where = "WHERE student.tenant_id = :tenant_id" + "".join(
            f" AND {clause}" for clause in clauses
        )
        from_sql = self._cohort_from_sql()
        done = quoted(_DONE_REQUIREMENT_STATUSES)
        counts_students = True

        if group_by == "blocking_requirement":
            counts_students = False
            group_sql = f"""
                SELECT rdv.code AS bucket, COUNT(*)::integer AS count,
                       COUNT(DISTINCT student.id)::integer AS students
                {from_sql}
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
                ORDER BY count DESC, bucket
                LIMIT :group_limit
            """
        else:
            expressions = {
                "offer_status": (
                    f"COALESCE((SELECT o.status FROM {self._table('admission_offer')} AS o"
                    f" WHERE o.tenant_id = student.tenant_id AND o.student_id = student.id"
                    f" ORDER BY o.created_at DESC, o.id DESC LIMIT 1), 'no_offer')"
                ),
                "deposit_state": self._deposit_bucket_sql(),
                "onboarding_status": (
                    f"COALESCE((SELECT ob.status FROM"
                    f" {self._table('student_onboarding')} AS ob"
                    f" WHERE ob.tenant_id = student.tenant_id AND ob.student_id = student.id),"
                    f" 'not_started')"
                ),
                "program": (
                    f"COALESCE((SELECT p.name FROM {self._table('admission_offer')} AS o"
                    f" JOIN {self._table('program')} AS p"
                    f"   ON p.id = o.program_id AND p.tenant_id = o.tenant_id"
                    f" WHERE o.tenant_id = student.tenant_id AND o.student_id = student.id"
                    f" ORDER BY o.created_at DESC, o.id DESC LIMIT 1), 'Program not assigned')"
                ),
                "class_year": "student.class_year::text",
                "assigned_staff": (
                    f"COALESCE((SELECT member.display_name FROM"
                    f" {self._table('staff_work_item')} AS wi"
                    f" JOIN {self._table('staff_member')} AS member"
                    f"   ON member.id = wi.assignee_id AND member.tenant_id = wi.tenant_id"
                    f" WHERE wi.tenant_id = student.tenant_id AND wi.student_id = student.id"
                    f"   AND wi.status IN :open_statuses"
                    f" ORDER BY wi.updated_at DESC LIMIT 1), 'Unassigned')"
                ),
                "housing_state": f"{self._housing_bucket_sql()}",
            }
            expression = expressions[group_by]
            group_sql = f"""
                SELECT {expression} AS bucket, COUNT(*)::integer AS count,
                       COUNT(*)::integer AS students
                {from_sql}
                {where}
                GROUP BY 1
                ORDER BY count DESC, bucket
                LIMIT :group_limit
            """

        count_sql = f"SELECT COUNT(*)::integer AS total {from_sql} {where}"
        async with self._engine.connect() as connection:
            rows = (
                (await connection.execute(self._cohort_statement(group_sql, params), params))
                .mappings()
                .all()
            )
            total_row = (
                (await connection.execute(self._cohort_statement(count_sql, params), params))
                .mappings()
                .first()
            )
        return {
            "groupBy": group_by,
            "matchingStudents": int(total_row["total"]) if total_row else 0,
            "countsRepresent": "students" if counts_students else "open blocking requirements",
            "buckets": [
                {
                    "value": _optional_text(row["bucket"]) or str(row["bucket"] or "unknown"),
                    "count": int(row["count"]),
                    "students": int(row["students"]),
                }
                for row in rows
            ],
            "filter": cohort.describe(),
        }

    # ------------------------------------------------------------------
    # Attention / engagement (first read path for the scheduler's output)
    # ------------------------------------------------------------------

    async def get_students_needing_attention(
        self, auth: AuthContext, *, limit: int = 15
    ) -> JsonDict:
        """The deterministic attention queue: intervention candidates joined
        to their engagement snapshots, ranked by stored priority.

        This is rule-based output of the engagement scan (reason codes +
        evidence), not a risk score. Nothing here is a probability.
        """

        _require_staff(auth)
        bounded_limit = max(1, min(int(limit or 15), 50))
        sql = f"""
            SELECT candidate.id, candidate.student_id, candidate.trigger_code,
              candidate.priority, candidate.reason_codes, candidate.evidence,
              candidate.status, candidate.created_at, candidate.updated_at,
              person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name,
              snapshot.completion_percentage, snapshot.blocking_requirement_count,
              snapshot.next_deadline, snapshot.days_to_next_deadline,
              snapshot.recent_upload_failures, snapshot.help_requested,
              snapshot.open_support_case_count, snapshot.last_meaningful_action_at,
              snapshot.projected_at
            FROM {self._table("intervention_candidate")} AS candidate
            JOIN {self._table("student")} AS student
              ON student.id = candidate.student_id AND student.tenant_id = candidate.tenant_id
            JOIN {self._table("person")} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {self._table("student_profile")} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            LEFT JOIN {self._table("student_engagement_snapshot")} AS snapshot
              ON snapshot.tenant_id = candidate.tenant_id
             AND snapshot.student_id = candidate.student_id
            WHERE candidate.tenant_id = :tenant_id
              AND candidate.status IN ('new', 'accepted')
              AND (candidate.suppression_until IS NULL OR candidate.suppression_until <= NOW())
            ORDER BY {_PRIORITY_RANK_SQL}, candidate.created_at DESC, candidate.id
            LIMIT :limit
        """
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(sql),
                        # Over-fetch so per-student dedupe still fills the limit.
                        {"tenant_id": _uuid(auth.tenant_id), "limit": bounded_limit * 3},
                    )
                )
                .mappings()
                .all()
            )
        items: list[JsonDict] = []
        seen_students: set[str] = set()
        for row in rows:
            student_key = str(row["student_id"])
            if student_key in seen_students:
                continue
            seen_students.add(student_key)
            items.append(
                {
                    "student": {
                        "id": student_key,
                        "name": f"{row['first_name']} {row['last_name']}",
                        "preferredName": str(row["preferred_name"]),
                    },
                    "priority": str(row["priority"]),
                    "triggerCode": str(row["trigger_code"]),
                    "reasonCodes": _json_list(row["reason_codes"]),
                    "evidence": _json_mapping(row["evidence"]),
                    "candidateStatus": str(row["status"]),
                    "candidateCreatedAt": _optional_iso(row["created_at"]),
                    "snapshot": _map_engagement_snapshot(row)
                    if row["projected_at"] is not None
                    else None,
                }
            )
            if len(items) >= bounded_limit:
                break
        return {
            "items": items,
            "total": len(items),
            "rankingBasis": (
                "Deterministic engagement-scan rules: stored priority "
                "(urgent = deadline within 2 days), then candidate recency. "
                "Reason codes and evidence come from the scan, not a model."
            ),
            "generatedAt": _iso(self._clock()),
        }

    async def get_student_engagement_signals(self, auth: AuthContext, student_id: str) -> JsonDict:
        """One student's engagement snapshot, with honest freshness."""

        _require_staff(auth)
        sql = f"""
            SELECT snapshot.*
            FROM {self._table("student_engagement_snapshot")} AS snapshot
            WHERE snapshot.tenant_id = :tenant_id AND snapshot.student_id = :student_id
        """
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(sql),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "student_id": _uuid(student_id),
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return {
                "available": False,
                "note": (
                    "No engagement snapshot has been computed for this student "
                    "yet. The engagement scan runs on a schedule; live "
                    "requirement and deadline reads still work."
                ),
            }
        snapshot = _map_engagement_snapshot(row)
        snapshot["available"] = True
        return snapshot

    # ------------------------------------------------------------------
    # Communications / work / timeline
    # ------------------------------------------------------------------

    async def get_student_communication_history(
        self,
        auth: AuthContext,
        student_id: str,
        *,
        channel: str | None = None,
        limit: int = 40,
    ) -> JsonDict:
        """Recorded communications for one student, newest first.

        Coverage caveat carried in the payload: these are *recorded*
        interactions (staff-logged and portal messages). The platform has no
        vendor send/receive integration and no open/click tracking, so
        absence of a record is not proof no communication happened elsewhere.
        """

        _require_staff(auth)
        bounded_limit = max(1, min(int(limit or 40), 100))
        params: dict[str, Any] = {
            "tenant_id": _uuid(auth.tenant_id),
            "student_id": _uuid(student_id),
            "limit": bounded_limit,
        }
        channel_clause = ""
        if channel:
            if channel not in {"email", "sms", "voice", "portal"}:
                raise BadRequestError(
                    "VALIDATION_ERROR", "channel must be email, sms, voice, or portal"
                )
            params["channel"] = channel
            channel_clause = "AND communication.channel = :channel"
        events_sql = f"""
            SELECT communication.id, communication.channel, communication.direction,
              communication.subject, communication.body_excerpt,
              communication.delivery_status, communication.resolution_status,
              communication.occurred_at, communication.interaction_id,
              communication.source_type
            FROM {self._table("communication_event")} AS communication
            WHERE communication.tenant_id = :tenant_id
              AND communication.student_id = :student_id
              {channel_clause}
            ORDER BY communication.occurred_at DESC, communication.id DESC
            LIMIT :limit
        """
        inquiries_sql = f"""
            SELECT inquiry.id, inquiry.topic_code, inquiry.subject, inquiry.status,
              inquiry.priority, inquiry.assignee_id, member.display_name AS assignee_name,
              inquiry.created_at, inquiry.last_message_at, inquiry.resolved_at
            FROM {self._table("student_inquiry")} AS inquiry
            LEFT JOIN {self._table("staff_member")} AS member
              ON member.id = inquiry.assignee_id AND member.tenant_id = inquiry.tenant_id
            WHERE inquiry.tenant_id = :tenant_id AND inquiry.student_id = :student_id
            ORDER BY inquiry.created_at DESC, inquiry.id DESC
            LIMIT 15
        """
        async with self._engine.connect() as connection:
            event_rows = (await connection.execute(text(events_sql), params)).mappings().all()
            inquiry_rows = (
                (
                    await connection.execute(
                        text(inquiries_sql),
                        {
                            "tenant_id": params["tenant_id"],
                            "student_id": params["student_id"],
                        },
                    )
                )
                .mappings()
                .all()
            )
        events = [
            {
                "id": str(row["id"]),
                "channel": str(row["channel"]),
                "direction": str(row["direction"]),
                "subject": _optional_text(row["subject"]),
                "bodyExcerpt": _bounded_text(row["body_excerpt"], 400),
                "deliveryStatus": str(row["delivery_status"]),
                "resolutionStatus": str(row["resolution_status"]),
                "occurredAt": _optional_iso(row["occurred_at"]),
                "interactionId": _optional_uuid_text(row["interaction_id"]),
                "sourceType": _optional_text(row["source_type"]),
            }
            for row in event_rows
        ]
        return {
            "events": events,
            "inquiries": [
                {
                    "id": str(row["id"]),
                    "topicCode": str(row["topic_code"]),
                    "subject": str(row["subject"]),
                    "status": str(row["status"]),
                    "priority": str(row["priority"]),
                    "assignee": _optional_text(row["assignee_name"]),
                    "createdAt": _optional_iso(row["created_at"]),
                    "lastMessageAt": _optional_iso(row["last_message_at"]),
                    "resolvedAt": _optional_iso(row["resolved_at"]),
                }
                for row in inquiry_rows
            ],
            "total": len(events),
            "coverage": (
                "Recorded communications only: staff-logged interactions and "
                "portal messages. No external email/SMS integration exists and "
                "opens/clicks are not tracked."
            ),
        }

    async def get_student_work_items(
        self, auth: AuthContext, student_id: str, *, include_done: bool = False
    ) -> JsonDict:
        """Work items for one student in canonical queue order."""

        _require_staff(auth)
        status_clause = "" if include_done else "AND item.status IN :open_statuses"
        sql = f"""
            SELECT item.id, item.key, item.title, item.status, item.priority,
              item.component, item.action_type, item.due_at, item.escalated,
              item.follow_up_at, item.blocker_code, item.blocker_detail,
              item.attempt_count, item.selected_channel, item.next_step,
              item.created_at, item.updated_at,
              assignee.display_name AS assignee_name,
              assignee.component AS assignee_component
            FROM {self._table("staff_work_item")} AS item
            LEFT JOIN {self._table("staff_member")} AS assignee
              ON assignee.id = item.assignee_id AND assignee.tenant_id = item.tenant_id
            WHERE item.tenant_id = :tenant_id
              AND item.student_id = :student_id
              {status_clause}
            ORDER BY
              CASE item.priority
                WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4
              END,
              item.due_at NULLS LAST,
              item.updated_at DESC,
              item.id
            LIMIT 50
        """
        statement = text(sql)
        if not include_done:
            statement = statement.bindparams(
                text_bind_expanding("open_statuses", _OPEN_WORK_STATUSES)
            )
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        statement,
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "student_id": _uuid(student_id),
                        },
                    )
                )
                .mappings()
                .all()
            )
        return {
            "items": [
                {
                    "id": str(row["id"]),
                    "key": str(row["key"]),
                    "title": str(row["title"]),
                    "status": str(row["status"]),
                    "priority": str(row["priority"]),
                    "component": str(row["component"]),
                    "actionType": _optional_text(row["action_type"]),
                    "dueAt": _optional_iso(row["due_at"]),
                    "escalated": bool(row["escalated"]),
                    "followUpAt": _optional_iso(row["follow_up_at"]),
                    "blockerCode": _optional_text(row["blocker_code"]),
                    "blockerDetail": _bounded_text(row["blocker_detail"], 300),
                    "attemptCount": int(row["attempt_count"] or 0),
                    "selectedChannel": _optional_text(row["selected_channel"]),
                    "nextStep": _bounded_text(row["next_step"], 300),
                    "assignee": _optional_text(row["assignee_name"]),
                    "assigneeComponent": _optional_text(row["assignee_component"]),
                    "createdAt": _optional_iso(row["created_at"]),
                    "updatedAt": _optional_iso(row["updated_at"]),
                }
                for row in rows
            ],
            "total": len(rows),
        }

    async def get_student_timeline(
        self, auth: AuthContext, student_id: str, *, limit: int = 40
    ) -> JsonDict:
        """A bounded operational timeline from existing event-bearing tables.

        Sources: work logs, documents, deposit payments, recorded
        communications, appointments, and inquiries. ``audit_event`` is
        deliberately not exposed yet — its rows mix system-internal actions
        that need a reviewed allowlist before staff-facing interpretation.
        """

        _require_staff(auth)
        bounded_limit = max(1, min(int(limit or 40), 80))
        log = self._table("staff_work_log")
        item = self._table("staff_work_item")
        document = self._table("document_record")
        payment = self._table("payment_transaction")
        communication = self._table("communication_event")
        appointment = self._table("student_appointment")
        inquiry = self._table("student_inquiry")
        sql = f"""
            SELECT * FROM (
              SELECT log.occurred_at, 'work_log' AS kind,
                     CONCAT(item.key, ': ', log.action) AS title,
                     LEFT(log.message, 300) AS detail
              FROM {log} AS log
              JOIN {item} AS item
                ON item.id = log.work_item_id AND item.tenant_id = log.tenant_id
              WHERE log.tenant_id = :tenant_id AND item.student_id = :student_id
              UNION ALL
              SELECT document.created_at, 'document',
                     CONCAT('Document uploaded: ', document.category),
                     CONCAT(document.file_name, ' (current status: ', document.status, ')')
              FROM {document} AS document
              WHERE document.tenant_id = :tenant_id AND document.student_id = :student_id
                AND document.status <> 'placeholder'
              UNION ALL
              SELECT payment.created_at, 'payment',
                     CONCAT('Enrollment deposit payment ', payment.status),
                     CONCAT('$', ROUND(payment.amount_cents / 100.0, 2)::text)
              FROM {payment} AS payment
              WHERE payment.tenant_id = :tenant_id AND payment.student_id = :student_id
              UNION ALL
              SELECT communication.occurred_at, 'communication',
                     CONCAT(communication.direction, ' ', communication.channel,
                            ' (', communication.delivery_status, ')'),
                     LEFT(COALESCE(communication.subject, communication.body_excerpt, ''), 200)
              FROM {communication} AS communication
              WHERE communication.tenant_id = :tenant_id
                AND communication.student_id = :student_id
              UNION ALL
              SELECT appointment.starts_at, 'appointment',
                     CONCAT('Appointment: ', appointment.type),
                     appointment.status
              FROM {appointment} AS appointment
              WHERE appointment.tenant_id = :tenant_id
                AND appointment.student_id = :student_id
              UNION ALL
              SELECT inquiry.created_at, 'inquiry',
                     CONCAT('Support inquiry opened: ', inquiry.subject),
                     inquiry.status
              FROM {inquiry} AS inquiry
              WHERE inquiry.tenant_id = :tenant_id AND inquiry.student_id = :student_id
              UNION ALL
              SELECT inquiry.resolved_at, 'inquiry',
                     CONCAT('Support inquiry resolved: ', inquiry.subject),
                     inquiry.status
              FROM {inquiry} AS inquiry
              WHERE inquiry.tenant_id = :tenant_id AND inquiry.student_id = :student_id
                AND inquiry.resolved_at IS NOT NULL
            ) AS merged
            WHERE merged.occurred_at IS NOT NULL
            ORDER BY merged.occurred_at DESC
            LIMIT :limit
        """
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(sql),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "student_id": _uuid(student_id),
                            "limit": bounded_limit,
                        },
                    )
                )
                .mappings()
                .all()
            )
        return {
            "events": [
                {
                    "occurredAt": _optional_iso(row["occurred_at"]),
                    "kind": str(row["kind"]),
                    "title": str(row["title"]),
                    "detail": _optional_text(row["detail"]),
                }
                for row in rows
            ],
            "total": len(rows),
            "coverage": (
                "Work logs, documents, deposit payments, recorded "
                "communications, appointments, and inquiries. Requirement "
                "status transitions are not individually versioned and the "
                "audit ledger is not yet exposed, so this timeline is "
                "operational, not exhaustive."
            ),
        }

    # ------------------------------------------------------------------
    # Institutional guidance (pure read — no default seeding)
    # ------------------------------------------------------------------

    async def get_staff_guidance(self, auth: AuthContext) -> JsonDict:
        """Staff-authored core plays and knowledge cards, without the
        default-seeding write that the workspace read performs."""

        _require_staff(auth)
        plays_sql = f"""
            SELECT id, title, description, trigger_description, audience, steps,
                   status, owner_name, version, updated_at
            FROM {self._table("staff_core_play")}
            WHERE tenant_id = :tenant_id
            ORDER BY updated_at DESC, title, id
            LIMIT 50
        """
        cards_sql = f"""
            SELECT id, title, summary, body, category, audience, status,
                   owner_name, version, updated_at
            FROM {self._table("staff_knowledge_card")}
            WHERE tenant_id = :tenant_id
            ORDER BY updated_at DESC, title, id
            LIMIT 50
        """
        params = {"tenant_id": _uuid(auth.tenant_id)}
        async with self._engine.connect() as connection:
            play_rows = (await connection.execute(text(plays_sql), params)).mappings().all()
            card_rows = (await connection.execute(text(cards_sql), params)).mappings().all()
        return {
            "corePlays": [
                {
                    "id": str(row["id"]),
                    "title": str(row["title"]),
                    "description": str(row["description"]),
                    "trigger": str(row["trigger_description"]),
                    "audience": str(row["audience"]),
                    "steps": _json_list(row["steps"]),
                    "status": str(row["status"]),
                    "owner": str(row["owner_name"]),
                }
                for row in play_rows
            ],
            "knowledgeCards": [
                {
                    "id": str(row["id"]),
                    "title": str(row["title"]),
                    "summary": str(row["summary"]),
                    "body": _bounded_text(row["body"], 600),
                    "category": str(row["category"]),
                    "audience": str(row["audience"]),
                    "status": str(row["status"]),
                    "owner": str(row["owner_name"]),
                }
                for row in card_rows
            ],
            "nature": (
                "Staff-authored prose guidance. Triggers are descriptions, not "
                "evaluated rules — no playbook engine enforces these."
            ),
        }

    # ------------------------------------------------------------------
    # Durable staff conversations
    # ------------------------------------------------------------------

    async def create_conversation(self, auth: AuthContext) -> JsonDict:
        _require_staff(auth)
        conversation_id = str(self._uuid_factory())
        sql = f"""
            INSERT INTO {self._table("staff_assistant_conversation")} (
              id, tenant_id, staff_member_id, status
            )
            SELECT :id, member.tenant_id, member.id, 'active'
            FROM {self._table("staff_member")} AS member
            WHERE member.tenant_id = :tenant_id AND member.id = :staff_member_id
              AND member.active = true
            RETURNING id, status, created_at
        """
        async with self._engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        text(sql),
                        {
                            "id": conversation_id,
                            "tenant_id": _uuid(auth.tenant_id),
                            "staff_member_id": _uuid(auth.actor_id),
                        },
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise NotFoundError(
                "STAFF_MEMBER_NOT_FOUND",
                "The active staff member was not found in this tenant",
            )
        return {
            "id": str(row["id"]),
            "status": str(row["status"]),
            "messages": [],
            "createdAt": _iso(row["created_at"]),
        }

    async def get_conversation_messages(self, auth: AuthContext, conversation_id: str) -> JsonDict:
        _require_staff(auth)
        conversation = await self._conversation_row(auth, conversation_id)
        if conversation is None:
            raise NotFoundError(
                "STAFF_ASSISTANT_CONVERSATION_NOT_FOUND", "The conversation was not found"
            )
        sql = f"""
            SELECT id, conversation_id, role, content, client_message_id, request_id,
                   provider, model, usage, blocks, context_receipts,
                   referenced_student_id, created_at
            FROM {self._table("staff_assistant_message")}
            WHERE tenant_id = :tenant_id AND staff_member_id = :staff_member_id
              AND conversation_id = :conversation_id
            ORDER BY created_at, CASE role WHEN 'user' THEN 0 ELSE 1 END, id
        """
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(sql),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "staff_member_id": _uuid(auth.actor_id),
                            "conversation_id": _uuid(conversation_id),
                        },
                    )
                )
                .mappings()
                .all()
            )
        return {
            "conversationId": conversation_id,
            "activeStudentId": _optional_uuid_text(conversation["active_student_id"]),
            "messages": [_map_staff_message(row) for row in rows],
        }

    async def get_recent_history(
        self, auth: AuthContext, conversation_id: str, *, limit: int = 12
    ) -> JsonDict:
        """Bounded recent turns plus the conversation's active student referent.

        The durable store — never the browser — is the source of history. An
        unknown or foreign conversation yields empty history; the write path
        still validates ownership before appending.
        """

        _require_staff(auth)
        conversation = await self._conversation_row(auth, conversation_id)
        if conversation is None:
            return {"history": [], "activeStudentId": None}
        sql = f"""
            SELECT role, content FROM {self._table("staff_assistant_message")}
            WHERE tenant_id = :tenant_id AND staff_member_id = :staff_member_id
              AND conversation_id = :conversation_id
            ORDER BY created_at DESC, CASE role WHEN 'user' THEN 1 ELSE 0 END, id DESC
            LIMIT :limit
        """
        async with self._engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        text(sql),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "staff_member_id": _uuid(auth.actor_id),
                            "conversation_id": _uuid(conversation_id),
                            "limit": max(1, min(int(limit), 40)),
                        },
                    )
                )
                .mappings()
                .all()
            )
        return {
            "history": [
                {"role": str(row["role"]), "content": str(row["content"])} for row in reversed(rows)
            ],
            "activeStudentId": _optional_uuid_text(conversation["active_student_id"]),
        }

    async def find_exchange_by_client_id(
        self, auth: AuthContext, client_message_id: str
    ) -> JsonDict | None:
        """Return the exact stored exchange for a staff-scoped retry."""

        _require_staff(auth)
        async with self._engine.connect() as connection:
            return await self._find_exchange_on_connection(connection, auth, client_message_id)

    async def _find_exchange_on_connection(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        client_message_id: str,
    ) -> JsonDict | None:
        user_sql = f"""
            SELECT id, conversation_id, exchange_id
            FROM {self._table("staff_assistant_message")}
            WHERE tenant_id = :tenant_id AND staff_member_id = :staff_member_id
              AND client_message_id = :client_message_id AND role = 'user'
        """
        user_row = (
            (
                await connection.execute(
                    text(user_sql),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "staff_member_id": _uuid(auth.actor_id),
                        "client_message_id": client_message_id,
                    },
                )
            )
            .mappings()
            .first()
        )
        if user_row is None:
            return None
        assistant_sql = f"""
            SELECT id, conversation_id, role, content, client_message_id, request_id,
                   provider, model, usage, blocks, context_receipts,
                   referenced_student_id, created_at
            FROM {self._table("staff_assistant_message")}
            WHERE tenant_id = :tenant_id AND staff_member_id = :staff_member_id
              AND conversation_id = :conversation_id
              AND exchange_id = :exchange_id AND role = 'assistant'
            LIMIT 1
        """
        assistant_row = (
            (
                await connection.execute(
                    text(assistant_sql),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "staff_member_id": _uuid(auth.actor_id),
                        "conversation_id": user_row["conversation_id"],
                        "exchange_id": user_row["exchange_id"],
                    },
                )
            )
            .mappings()
            .first()
        )
        if assistant_row is None:
            return None
        assistant = _map_staff_message(assistant_row)
        return {
            "conversationId": str(user_row["conversation_id"]),
            "userMessageId": str(user_row["id"]),
            "assistantMessageId": assistant["id"],
            "requestId": assistant.get("requestId"),
            "message": assistant["content"],
            "blocks": assistant.get("blocks"),
            "provider": assistant.get("provider") or "guided",
            "model": assistant.get("model"),
            "usage": assistant.get("usage"),
            "contextReceipts": assistant.get("contextReceipts", []),
        }

    async def append_exchange(
        self,
        auth: AuthContext,
        *,
        conversation_id: str | None,
        user_message: Mapping[str, Any],
        assistant_message: Mapping[str, Any],
        referenced_student_id: str | None,
        request_id: str,
        referent_action: str = "set",
        active_student_id: str | None = None,
    ) -> JsonDict:
        """Atomically create/reuse a conversation and append one replay-safe exchange."""

        _require_staff(auth)
        client_message_id = _optional_text(user_message.get("clientMessageId"))
        requested_conversation_id = _uuid(conversation_id) if conversation_id else None
        referent = _uuid(referenced_student_id) if referenced_student_id else None
        # What the conversation carries forward may differ from what this turn
        # resolved: a queue turn resolves no student but puts one on the table.
        carried = _uuid(active_student_id) if active_student_id else referent
        user_message_id = str(self._uuid_factory())
        assistant_message_id = str(self._uuid_factory())
        exchange_id = str(self._uuid_factory())

        insert_sql = f"""
            INSERT INTO {self._table("staff_assistant_message")} (
              id, tenant_id, conversation_id, staff_member_id, exchange_id,
              role, content, client_message_id, request_id, provider, model,
              usage, blocks, context_receipts, referenced_student_id
            ) VALUES (
              :id, :tenant_id, :conversation_id, :staff_member_id, :exchange_id,
              :role, :content, :client_message_id, :request_id, :provider, :model,
              CAST(:usage AS jsonb), CAST(:blocks AS jsonb),
              CAST(:context_receipts AS jsonb), :referenced_student_id
            )
        """
        update_sql = f"""
            UPDATE {self._table("staff_assistant_conversation")}
            SET last_message_at = NOW(),
                -- The active referent has a lifecycle. "set" records the
                -- student this turn resolved; "clear" drops it because the
                -- turn was explicitly about the population, the queue, or the
                -- attention scan; "keep" leaves it for a genuine follow-up.
                -- COALESCE-only (the previous rule) made the column
                -- monotonic, so one student lookup scoped the rest of the
                -- conversation to that student.
                active_student_id = CASE
                  WHEN :referent_action = 'clear' THEN NULL
                  WHEN :referent_action = 'set' THEN COALESCE(
                    :active_student_id, active_student_id
                  )
                  ELSE active_student_id
                END
            WHERE tenant_id = :tenant_id AND staff_member_id = :staff_member_id
              AND id = :conversation_id
        """

        async with self._engine.begin() as connection:
            if client_message_id is not None:
                await connection.execute(
                    text(
                        """
                        SELECT pg_advisory_xact_lock(
                          hashtextextended(CAST(:replay_key AS text), 0)
                        )
                        """
                    ),
                    {
                        "replay_key": (
                            f"staff-assistant-exchange:{auth.tenant_id}:"
                            f"{auth.actor_id}:{client_message_id}"
                        )
                    },
                )
                replay = await self._find_exchange_on_connection(
                    connection, auth, client_message_id
                )
                if replay is not None:
                    return replay

            if referent is not None:
                referent_result = await connection.execute(
                    text(
                        f"""
                        SELECT id FROM {self._table("student")}
                        WHERE tenant_id = :tenant_id AND id = :student_id
                        """
                    ),
                    {"tenant_id": _uuid(auth.tenant_id), "student_id": referent},
                )
                if referent_result.mappings().first() is None:
                    raise BadRequestError(
                        "STAFF_ASSISTANT_STUDENT_REFERENT_INVALID",
                        "The referenced student is not available in this tenant",
                    )

            if requested_conversation_id is None:
                conversation_id = await self._create_conversation_on_connection(connection, auth)
            else:
                existing = await self._conversation_row_on_connection(
                    connection,
                    auth,
                    str(requested_conversation_id),
                    for_update=True,
                )
                if existing is None:
                    raise NotFoundError(
                        "STAFF_ASSISTANT_CONVERSATION_NOT_FOUND",
                        "The conversation was not found",
                    )
                conversation_id = str(requested_conversation_id)

            common = {
                "tenant_id": _uuid(auth.tenant_id),
                "conversation_id": _uuid(conversation_id),
                "staff_member_id": _uuid(auth.actor_id),
                "exchange_id": _uuid(exchange_id),
                "request_id": request_id,
                "referenced_student_id": referent,
            }
            await connection.execute(
                text(insert_sql),
                {
                    **common,
                    "id": _uuid(user_message_id),
                    "role": "user",
                    "content": _bounded_content(user_message.get("content")),
                    "client_message_id": client_message_id,
                    "provider": None,
                    "model": None,
                    "usage": None,
                    "blocks": None,
                    "context_receipts": "[]",
                },
            )
            await connection.execute(
                text(insert_sql),
                {
                    **common,
                    "id": _uuid(assistant_message_id),
                    "role": "assistant",
                    "content": _bounded_content(assistant_message.get("content")),
                    "client_message_id": None,
                    "provider": _optional_text(assistant_message.get("provider")),
                    "model": _optional_text(assistant_message.get("model")),
                    "usage": _json_or_none(assistant_message.get("usage")),
                    "blocks": _json_or_none(assistant_message.get("blocks")),
                    "context_receipts": json.dumps(assistant_message.get("contextReceipts") or []),
                },
            )
            await connection.execute(
                text(update_sql),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "staff_member_id": _uuid(auth.actor_id),
                    "conversation_id": _uuid(conversation_id),
                    "active_student_id": carried,
                    "referent_action": referent_action,
                },
            )
        return {
            "conversationId": conversation_id,
            "userMessageId": user_message_id,
            "assistantMessageId": assistant_message_id,
        }

    async def _create_conversation_on_connection(
        self, connection: AsyncConnection, auth: AuthContext
    ) -> str:
        conversation_id = str(self._uuid_factory())
        sql = f"""
            INSERT INTO {self._table("staff_assistant_conversation")} (
              id, tenant_id, staff_member_id, status
            )
            SELECT :id, member.tenant_id, member.id, 'active'
            FROM {self._table("staff_member")} AS member
            WHERE member.tenant_id = :tenant_id AND member.id = :staff_member_id
              AND member.active = true
            RETURNING id
        """
        row = (
            (
                await connection.execute(
                    text(sql),
                    {
                        "id": _uuid(conversation_id),
                        "tenant_id": _uuid(auth.tenant_id),
                        "staff_member_id": _uuid(auth.actor_id),
                    },
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFoundError(
                "STAFF_MEMBER_NOT_FOUND",
                "The active staff member was not found in this tenant",
            )
        return str(row["id"])

    async def _conversation_row(
        self, auth: AuthContext, conversation_id: str
    ) -> Mapping[Any, Any] | None:
        async with self._engine.connect() as connection:
            return await self._conversation_row_on_connection(connection, auth, conversation_id)

    async def _conversation_row_on_connection(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        conversation_id: str,
        *,
        for_update: bool = False,
    ) -> Mapping[Any, Any] | None:
        lock = " FOR UPDATE" if for_update else ""
        sql = f"""
            SELECT id, active_student_id
            FROM {self._table("staff_assistant_conversation")}
            WHERE tenant_id = :tenant_id AND staff_member_id = :staff_member_id
              AND id = :conversation_id{lock}
        """
        return (
            (
                await connection.execute(
                    text(sql),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "staff_member_id": _uuid(auth.actor_id),
                        "conversation_id": _uuid(conversation_id),
                    },
                )
            )
            .mappings()
            .first()
        )


def _require_staff(auth: AuthContext) -> None:
    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_ACCESS_REQUIRED", "This route requires a staff identity")


def _map_engagement_snapshot(row: Mapping[Any, Any]) -> JsonDict:
    return {
        "completionPercentage": (
            int(row["completion_percentage"]) if row["completion_percentage"] is not None else None
        ),
        "blockingRequirementCount": int(row["blocking_requirement_count"] or 0),
        "nextDeadline": _optional_iso(row["next_deadline"]),
        "daysToNextDeadline": (
            int(row["days_to_next_deadline"]) if row["days_to_next_deadline"] is not None else None
        ),
        "recentUploadFailures": int(row["recent_upload_failures"] or 0),
        "helpRequested": bool(row["help_requested"]),
        "openSupportCaseCount": int(row["open_support_case_count"] or 0),
        "lastMeaningfulActionAt": _optional_iso(row["last_meaningful_action_at"]),
        "projectedAt": _optional_iso(row["projected_at"]),
    }


def _map_staff_message(row: Mapping[Any, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "conversationId": str(row["conversation_id"]),
        "role": str(row["role"]),
        "content": str(row["content"]),
        "clientMessageId": _optional_text(row["client_message_id"]),
        "requestId": _optional_text(row["request_id"]),
        "provider": _optional_text(row["provider"]),
        "model": _optional_text(row["model"]),
        "usage": row["usage"] if isinstance(row["usage"], Mapping) else None,
        "blocks": row["blocks"] if isinstance(row["blocks"], list) else None,
        "contextReceipts": row["context_receipts"]
        if isinstance(row["context_receipts"], list)
        else [],
        "referencedStudentId": _optional_uuid_text(row["referenced_student_id"]),
        "createdAt": _iso(row["created_at"]),
    }


def _uuid(value: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise BadRequestError("VALIDATION_ERROR", "A UUID identifier is invalid") from error


def _iso(value: object) -> str:
    if isinstance(value, str):
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, date):
        # DATE columns (e.g. admission_offer.response_deadline) carry no time.
        return value.isoformat()
    else:
        raise RuntimeError("Database returned an invalid timestamp")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _optional_iso(value: object) -> str | None:
    return None if value is None else _iso(value)


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_uuid_text(value: object) -> str | None:
    return None if value is None else str(value)


def _bounded_text(value: object, maximum: int) -> str | None:
    normalized = _optional_text(value)
    return normalized[:maximum] if normalized else None


def _bounded_content(value: object) -> str:
    content = str(value or "").strip()
    return content[:8000] or "(empty)"


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _json_mapping(value: object) -> JsonDict:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_or_none(value: object) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None
