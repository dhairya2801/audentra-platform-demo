"""Bounded staff-directory, inquiry and department reads for Staff Edward.

A staff member is an entity with a role, a component, a manager, a caseload
and a calendar; the assistant resolves a name against the *staff directory* —
not the student roster — and reads that person's world with one small query.
Inquiry and department questions follow the same discipline: **counts are
computed in SQL, lists are paged in SQL, and the per-turn payload is bounded
by construction.**

Work-queue reads are deliberately *not* here. The Action Center's query layer
(``PostgresStaffRepository.get_work_queue`` / ``summarize_work_queue``, with
its vocabulary in ``domain.action_center``) is the one bounded read of the
board, shared by the Staff Portal and the assistant.

Every read is tenant-scoped in SQL; nothing here writes.
"""

# ruff: noqa: S608 -- interpolations are module-level column lists and validated enums.
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.postgres.advising_repository import (
    OUTCOME_GRACE,
    STALE_WORK_AFTER,
    PostgresAdvisingRepository,
)
from audentra.infrastructure.postgres.staff_repository import _escape_like

JsonDict = dict[str, Any]

INQUIRY_GROUP_BY: tuple[str, ...] = ("status", "assignee", "topic", "priority")
INQUIRY_OPEN_STATUSES: tuple[str, ...] = ("new", "open", "waiting_on_student")
MAX_INQUIRY_PAGE = 25
MAX_STAFF_RESULTS = 12

_STAFF_BRIEF_COLUMNS = """
    m.id, m.display_name, m.email_normalized, m.component, m.active, m.external_ref,
    m.title, m.role_code, m.manager_id, m.employment_status, m.employment_type,
    m.started_at, m.ended_at, m.leave_until, m.timezone, m.office_location,
    m.caseload_cap, m.student_facing, m.appointment_types
"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value)


def _date(value: object) -> str | None:
    if value is None:
        return None
    return str(value)[:10]


def _require_staff(auth: AuthContext) -> None:
    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_ACCESS_REQUIRED", "This route requires a staff identity")


class PostgresStaffOperationsRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        advising: PostgresAdvisingRepository | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._advising = advising
        self._clock = clock

    # ------------------------------------------------------------------
    # Staff directory
    # ------------------------------------------------------------------

    async def search_staff(
        self,
        auth: AuthContext,
        *,
        query: str = "",
        component: str | None = None,
        role: str | None = None,
        absent_now: bool = False,
        limit: int = 10,
    ) -> JsonDict:
        """Resolve a person by name (or part of one) inside the staff directory.

        Matching is token-conjunctive and reports *how* each row matched, so
        the caller can prefer an exact full-name hit over a surname hit
        without a second query. Departed people are included and labelled —
        "who is Quentin Zephyrine" must not become "no such person".
        """

        _require_staff(auth)
        bounded = max(1, min(int(limit or 10), MAX_STAFF_RESULTS))
        tokens = [token for token in re.split(r"\s+", (query or "").strip()) if token][:4]
        params: dict[str, Any] = {"tenant_id": UUID(auth.tenant_id), "limit": bounded}
        clauses: list[str] = []
        for index, token in enumerate(tokens):
            params[f"token_{index}"] = f"%{_escape_like(token)}%"
            clauses.append(
                f"(m.display_name ILIKE :token_{index} ESCAPE '\\'"
                f" OR COALESCE(m.external_ref, '') ILIKE :token_{index} ESCAPE '\\'"
                f" OR m.email_normalized ILIKE :token_{index} ESCAPE '\\')"
            )
        if component:
            params["component"] = f"%{_escape_like(component.strip())}%"
            clauses.append("m.component ILIKE :component ESCAPE '\\'")
        if role:
            params["role"] = f"%{_escape_like(role.strip())}%"
            clauses.append(
                "(m.role_code ILIKE :role ESCAPE '\\' OR m.title ILIKE :role ESCAPE '\\')"
            )
        if absent_now:
            params["now"] = self._clock()
            clauses.append(
                "(m.employment_status = 'on_leave' OR EXISTS (SELECT 1 FROM staff_time_off t"
                " WHERE t.tenant_id=m.tenant_id AND t.staff_member_id=m.id AND t.blocks_bookings"
                " AND t.starts_at <= CAST(:now AS timestamptz) AND t.ends_at > CAST(:now AS "
                "timestamptz)))"
            )
        if not clauses:
            return {"items": [], "total": 0}
        where = " AND ".join(clauses)
        sql = f"""
            SELECT {_STAFF_BRIEF_COLUMNS},
              (SELECT COUNT(*) FROM student_staff_assignment a
                WHERE a.tenant_id=m.tenant_id AND a.staff_member_id=m.id
                  AND a.role='primary_advisor' AND a.ended_at IS NULL) AS primary_advisees,
              (SELECT COUNT(*) FROM staff_work_item w
                WHERE w.tenant_id=m.tenant_id AND w.assignee_id=m.id
                  AND w.status IN ('todo','in_progress',
                    'follow_up_required','blocked'))
                AS open_items,
              (SELECT string_agg(t.kind || '|' || t.ends_at::text, ';') FROM staff_time_off t
                WHERE t.tenant_id=m.tenant_id AND t.staff_member_id=m.id AND t.blocks_bookings
                  AND t.starts_at <= now() AND t.ends_at > now()) AS current_absence
            FROM staff_member m
            WHERE m.tenant_id=:tenant_id AND {where}
            ORDER BY {"open_items DESC, " if absent_now else ""}m.employment_status,
                     m.display_name, m.id
            LIMIT :limit
        """
        async with self._engine.connect() as connection:
            rows = (await connection.execute(text(sql), params)).mappings().all()
        needle = " ".join(tokens).lower()
        items: list[JsonDict] = []
        for row in rows:
            entry = _map_brief(row)
            entry["primaryAdvisees"] = int(row["primary_advisees"])
            entry["openItems"] = int(row["open_items"])
            entry["currentAbsence"] = _parse_absence(row.get("current_absence"))
            name = str(row["display_name"]).lower()
            parts = name.split()
            if needle and name == needle:
                entry["matchQuality"] = "exact_name"
            elif len(tokens) == 1 and parts and parts[0] == needle:
                entry["matchQuality"] = "first_name"
            elif len(tokens) == 1 and parts and parts[-1] == needle:
                entry["matchQuality"] = "last_name"
            else:
                entry["matchQuality"] = "partial"
            items.append(entry)
        return {"items": items, "total": len(items)}

    async def list_components(self, auth: AuthContext) -> JsonDict:
        """The tenant's components (departments) with headcounts."""

        _require_staff(auth)
        sql = """
            SELECT component,
              COUNT(*) AS staff,
              COUNT(*) FILTER (WHERE employment_status='active') AS active
            FROM staff_member WHERE tenant_id=:tenant_id
            GROUP BY component ORDER BY component
        """
        async with self._engine.connect() as connection:
            rows = (
                (await connection.execute(text(sql), {"tenant_id": UUID(auth.tenant_id)}))
                .mappings()
                .all()
            )
        return {
            "items": [
                {
                    "component": str(r["component"]),
                    "staff": int(r["staff"]),
                    "active": int(r["active"]),
                }
                for r in rows
            ]
        }

    async def staff_profile(self, auth: AuthContext, staff_member_id: str) -> JsonDict | None:
        """One person's world: identity, org position, caseload, backlog,
        calendar summary and the flags the director's team view raises."""

        _require_staff(auth)
        try:
            identifier = UUID(staff_member_id)
        except ValueError:
            return None
        now = self._clock()
        params = {
            "tenant_id": UUID(auth.tenant_id),
            "id": identifier,
            "now": now,
            "stale_before": now - STALE_WORK_AFTER,
            "outcome_before": now - OUTCOME_GRACE,
            "week": now + timedelta(days=7),
            "day_end": now + timedelta(days=1),
        }
        sql = f"""
            SELECT {_STAFF_BRIEF_COLUMNS},
              mgr.display_name AS manager_name, mgr.title AS manager_title,
              mgr.id AS manager_uuid,
              (SELECT COUNT(*) FROM staff_member r
                WHERE r.tenant_id=m.tenant_id AND r.manager_id=m.id) AS direct_reports,
              (SELECT COUNT(*) FROM student_staff_assignment a
                WHERE a.tenant_id=m.tenant_id AND a.staff_member_id=m.id
                  AND a.role='primary_advisor' AND a.ended_at IS NULL) AS primary_advisees,
              (SELECT COUNT(*) FROM student_staff_assignment a
                WHERE a.tenant_id=m.tenant_id AND a.staff_member_id=m.id AND a.ended_at IS NULL)
                AS assignments,
              (SELECT COUNT(*) FROM student_staff_assignment a
                WHERE a.tenant_id=m.tenant_id AND a.staff_member_id=m.id
                  AND a.role='primary_advisor' AND a.ended_at IS NOT NULL) AS ended_assignments,
              (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status IN ('todo','in_progress',
                    'follow_up_required','blocked'))
                AS open_items,
              (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status IN ('todo','in_progress',
                    'follow_up_required','blocked')
                  AND w.due_at IS NOT NULL AND w.due_at < :now) AS overdue_items,
              (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status IN ('todo','in_progress',
                    'follow_up_required','blocked')
                  AND w.priority='urgent') AS urgent_items,
              (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status IN ('todo','in_progress',
                    'follow_up_required','blocked')
                  AND w.escalated) AS escalated_items,
              (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status='in_progress'
                  AND w.updated_at < :stale_before)
                AS stale_items,
              (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status IN ('todo','in_progress',
                    'follow_up_required','blocked')
                  AND w.due_at IS NOT NULL AND w.due_at::date = (:now AT TIME ZONE 'UTC')::date)
                AS due_today_items,
              (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status='done'
                  AND w.completed_at >= (CAST(:now AS timestamptz) - interval '7 days'))
                AS completed_last_7_days,
              (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at < :outcome_before) AS awaiting_outcome,
              (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at >= :now AND ap.starts_at < :week) AS scheduled_next_7_days,
              (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at::date = (:now AT TIME ZONE 'UTC')::date) AS scheduled_today,
              (SELECT COUNT(*) FROM student_inquiry i WHERE i.tenant_id=m.tenant_id
                  AND i.assignee_id=m.id AND i.status IN ('new','open','waiting_on_student')
                  AND i.archived_at IS NULL) AS open_inquiries,
              (SELECT COUNT(*) FROM student_inquiry i WHERE i.tenant_id=m.tenant_id
                  AND i.assignee_id=m.id AND i.status='new' AND i.archived_at IS NULL)
                AS inquiries_awaiting_reply,
              (SELECT string_agg(DISTINCT t.kind || '|' || t.ends_at::text || '|'
                                 || COALESCE(t.note,''), ';')
                 FROM staff_time_off t WHERE t.tenant_id=m.tenant_id AND t.staff_member_id=m.id
                  AND t.starts_at <= :now AND t.ends_at > :now AND t.blocks_bookings)
                AS current_absence,
              (SELECT string_agg(DISTINCT t.kind || '|' || t.starts_at::text || '|'
                                 || t.ends_at::text, ';')
                 FROM staff_time_off t WHERE t.tenant_id=m.tenant_id AND t.staff_member_id=m.id
                  AND t.starts_at > :now
                  AND t.starts_at < (CAST(:now AS timestamptz) + interval '14 days')
                  AND t.blocks_bookings) AS upcoming_absence,
              (SELECT string_agg(DISTINCT av.weekday::text, ',') FROM staff_availability av
                 WHERE av.tenant_id=m.tenant_id AND av.staff_member_id=m.id) AS weekdays
            FROM staff_member m
            LEFT JOIN staff_member mgr ON mgr.id=m.manager_id AND mgr.tenant_id=m.tenant_id
            WHERE m.tenant_id=:tenant_id AND m.id=:id
        """
        async with self._engine.connect() as connection:
            row = (await connection.execute(text(sql), params)).mappings().first()
            if row is None:
                return None
            data = dict(row)
            calendar: JsonDict | None = None
            if self._advising is not None and bool(data.get("student_facing", True)):
                calendar = await self._advising.calendar_summary_for(
                    connection, auth.tenant_id, data, now
                )
        profile = _map_brief(data)
        cap = data.get("caseload_cap")
        advisees = int(data["primary_advisees"])
        profile.update(
            {
                "timezone": str(data.get("timezone") or "UTC"),
                "officeLocation": data.get("office_location"),
                "employmentType": str(data.get("employment_type") or "full_time"),
                "startedAt": _date(data.get("started_at")),
                "studentFacing": bool(data.get("student_facing", True)),
                "appointmentTypes": list(data.get("appointment_types") or []),
                "manager": (
                    {
                        "id": str(data["manager_uuid"]),
                        "name": str(data["manager_name"]),
                        "title": data.get("manager_title"),
                    }
                    if data.get("manager_uuid")
                    else None
                ),
                "directReports": int(data["direct_reports"]),
                "caseload": {
                    "primaryAdvisees": advisees,
                    "assignments": int(data["assignments"]),
                    "endedPrimaryAssignments": int(data["ended_assignments"]),
                    "cap": cap,
                    "utilization": round(advisees / int(cap), 3) if cap else None,
                    "overCap": bool(cap and advisees > int(cap)),
                },
                "work": {
                    "open": int(data["open_items"]),
                    "overdue": int(data["overdue_items"]),
                    "urgent": int(data["urgent_items"]),
                    "escalated": int(data["escalated_items"]),
                    "staleInProgress": int(data["stale_items"]),
                    "dueToday": int(data["due_today_items"]),
                    "completedLast7Days": int(data["completed_last_7_days"]),
                    "appointmentsAwaitingOutcome": int(data["awaiting_outcome"]),
                },
                "appointments": {
                    "scheduledToday": int(data["scheduled_today"]),
                    "scheduledNext7Days": int(data["scheduled_next_7_days"]),
                },
                "inquiries": {
                    "open": int(data["open_inquiries"]),
                    "awaitingReply": int(data["inquiries_awaiting_reply"]),
                },
                "currentAbsence": _parse_absence(data.get("current_absence")),
                "upcomingAbsence": _parse_upcoming(data.get("upcoming_absence")),
                "weekdays": _weekday_names(data.get("weekdays")),
                "availability": calendar,
                "flags": _profile_flags(data, advisees, cap, calendar),
                "generatedAt": _iso(now),
            }
        )
        return profile

    # ------------------------------------------------------------------
    # Inquiries (bounded)
    # ------------------------------------------------------------------

    def _inquiry_where(
        self, auth: AuthContext, filters: Mapping[str, Any], now: datetime
    ) -> tuple[list[str], dict[str, Any]]:
        params: dict[str, Any] = {"tenant_id": UUID(auth.tenant_id), "now": now}
        clauses = ["i.tenant_id = :tenant_id", "i.archived_at IS NULL"]
        status = filters.get("status")
        if status == "awaiting_first_reply":
            clauses.append("i.status = 'new'")
        elif status == "open" or status is None:
            clauses.append("i.status IN ('new','open','waiting_on_student')")
        elif status != "any":
            params["status"] = str(status)
            clauses.append("i.status = :status")
        ownership = filters.get("ownership")
        if ownership == "mine":
            params["me"] = UUID(auth.actor_id)
            clauses.append("i.assignee_id = :me")
        elif ownership == "unassigned":
            clauses.append("i.assignee_id IS NULL")
        if filters.get("assigneeId"):
            params["assignee"] = UUID(str(filters["assigneeId"]))
            clauses.append("i.assignee_id = :assignee")
        if filters.get("priority"):
            params["priority"] = str(filters["priority"])
            clauses.append("i.priority = :priority")
        if filters.get("topic"):
            params["topic"] = f"%{_escape_like(str(filters['topic']))}%"
            clauses.append(
                "(i.topic_code ILIKE :topic ESCAPE '\\' OR i.subject ILIKE :topic ESCAPE '\\')"
            )
        older = filters.get("olderThanHours")
        if older:
            params["older_before"] = now - timedelta(hours=int(older))
            clauses.append("i.created_at < :older_before")
        if filters.get("studentId"):
            params["student_id"] = UUID(str(filters["studentId"]))
            clauses.append("i.student_id = :student_id")
        return clauses, params

    async def summarize_inquiries(
        self,
        auth: AuthContext,
        *,
        filters: Mapping[str, Any],
        group_by: str | None = None,
        limit: int = 10,
    ) -> JsonDict:
        _require_staff(auth)
        now = self._clock()
        clauses, params = self._inquiry_where(auth, filters, now)
        where = " AND ".join(clauses)
        totals_sql = f"""
            SELECT COUNT(*) AS total,
              COUNT(*) FILTER (WHERE i.status='new') AS awaiting_first_reply,
              COUNT(*) FILTER (WHERE i.status='new'
                                 AND i.created_at
                                     < (CAST(:now AS timestamptz) - interval '24 hours'))
                AS awaiting_over_24h,
              COUNT(*) FILTER (WHERE i.status='open') AS open,
              COUNT(*) FILTER (WHERE i.status='waiting_on_student') AS waiting_on_student,
              COUNT(*) FILTER (WHERE i.status='resolved') AS resolved,
              COUNT(*) FILTER (WHERE i.assignee_id IS NULL AND i.status IN ('new','open'))
                AS unassigned_open,
              COUNT(*) FILTER (WHERE i.priority='urgent' AND i.status IN ('new','open'))
                AS urgent_open,
              MIN(i.created_at) FILTER (WHERE i.status='new') AS oldest_awaiting_at
            FROM student_inquiry i
            WHERE {where}
        """
        oldest_sql = f"""
            SELECT i.id, i.subject, i.topic_code, i.priority, i.status, i.created_at,
                   person.first_name || ' ' || person.last_name AS student_name,
                   student.external_ref AS student_external_ref, student.id AS student_id
            FROM student_inquiry i
            JOIN student ON student.id=i.student_id AND student.tenant_id=i.tenant_id
            JOIN person ON person.id=student.person_id AND person.tenant_id=student.tenant_id
            WHERE {where} AND i.status='new'
            ORDER BY i.created_at, i.id LIMIT 1
        """
        group_sql = None
        if group_by:
            if group_by not in INQUIRY_GROUP_BY:
                raise ApiError(400, "VALIDATION_ERROR", f"Unknown inquiry grouping {group_by!r}")
            expression = {
                "status": "i.status",
                "assignee": "COALESCE(assignee.display_name, 'Unassigned')",
                "topic": "i.topic_code",
                "priority": "i.priority",
            }[group_by]
            params["group_limit"] = max(1, min(int(limit or 10), 50))
            group_sql = f"""
                SELECT {expression} AS bucket, COUNT(*) AS count,
                  COUNT(*) FILTER (WHERE i.status='new') AS awaiting
                FROM student_inquiry i
                LEFT JOIN staff_member assignee
                  ON assignee.id=i.assignee_id AND assignee.tenant_id=i.tenant_id
                WHERE {where}
                GROUP BY 1 ORDER BY count DESC, bucket LIMIT :group_limit
            """
        async with self._engine.connect() as connection:
            totals = (await connection.execute(text(totals_sql), params)).mappings().one()
            oldest = (await connection.execute(text(oldest_sql), params)).mappings().first()
            buckets = (
                (await connection.execute(text(group_sql), params)).mappings().all()
                if group_sql
                else []
            )
        return {
            "filters": {k: v for k, v in filters.items() if v not in (None, "", "all")},
            "total": int(totals["total"]),
            "awaitingFirstReply": int(totals["awaiting_first_reply"]),
            "awaitingOver24h": int(totals["awaiting_over_24h"]),
            "open": int(totals["open"]),
            "waitingOnStudent": int(totals["waiting_on_student"]),
            "resolved": int(totals["resolved"]),
            "unassignedOpen": int(totals["unassigned_open"]),
            "urgentOpen": int(totals["urgent_open"]),
            "oldestAwaiting": (
                {
                    "id": str(oldest["id"]),
                    "subject": str(oldest["subject"]),
                    "topic": oldest["topic_code"],
                    "priority": oldest["priority"],
                    "createdAt": _iso(oldest["created_at"]),
                    "ageHours": round(
                        (now - _aware(oldest["created_at"])).total_seconds() / 3600, 1
                    ),
                    "student": {
                        "id": str(oldest["student_id"]),
                        "name": str(oldest["student_name"]),
                        "externalRef": oldest["student_external_ref"],
                    },
                }
                if oldest
                else None
            ),
            "groupBy": group_by,
            "buckets": [
                {
                    "value": str(row["bucket"]),
                    "count": int(row["count"]),
                    "awaiting": int(row["awaiting"]),
                }
                for row in buckets
            ],
            "generatedAt": _iso(now),
        }

    async def search_inquiries(
        self,
        auth: AuthContext,
        *,
        filters: Mapping[str, Any],
        limit: int = 10,
        sort: str = "oldest",
    ) -> JsonDict:
        _require_staff(auth)
        now = self._clock()
        clauses, params = self._inquiry_where(auth, filters, now)
        where = " AND ".join(clauses)
        params["limit"] = max(1, min(int(limit or 10), MAX_INQUIRY_PAGE))
        order = {"oldest": "i.created_at ASC, i.id", "newest": "i.created_at DESC, i.id"}.get(
            sort, "i.created_at ASC, i.id"
        )
        sql = f"""
            SELECT i.id, i.subject, i.topic_code, i.priority, i.status, i.created_at,
                   i.last_message_at, i.assignee_id, assignee.display_name AS assignee_name,
                   person.first_name || ' ' || person.last_name AS student_name,
                   student.external_ref AS student_external_ref, student.id AS student_id,
                   COUNT(*) OVER () AS total
            FROM student_inquiry i
            JOIN student ON student.id=i.student_id AND student.tenant_id=i.tenant_id
            JOIN person ON person.id=student.person_id AND person.tenant_id=student.tenant_id
            LEFT JOIN staff_member assignee
              ON assignee.id=i.assignee_id AND assignee.tenant_id=i.tenant_id
            WHERE {where}
            ORDER BY {order}
            LIMIT :limit
        """
        async with self._engine.connect() as connection:
            rows = (await connection.execute(text(sql), params)).mappings().all()
        total = int(rows[0]["total"]) if rows else 0
        return {
            "items": [
                {
                    "id": str(row["id"]),
                    "subject": str(row["subject"]),
                    "topic": row["topic_code"],
                    "priority": row["priority"],
                    "status": row["status"],
                    "createdAt": _iso(row["created_at"]),
                    "ageHours": round((now - _aware(row["created_at"])).total_seconds() / 3600, 1),
                    "lastMessageAt": _iso(row["last_message_at"]),
                    "assignee": (
                        {"id": str(row["assignee_id"]), "name": str(row["assignee_name"])}
                        if row["assignee_id"]
                        else None
                    ),
                    "student": {
                        "id": str(row["student_id"]),
                        "name": str(row["student_name"]),
                        "externalRef": row["student_external_ref"],
                    },
                }
                for row in rows
            ],
            "total": total,
            "returned": len(rows),
            "truncated": total > len(rows),
            "filters": {k: v for k, v in filters.items() if v not in (None, "", "all")},
            "generatedAt": _iso(now),
        }

    # ------------------------------------------------------------------
    # Components (departments)
    # ------------------------------------------------------------------

    async def component_summary(self, auth: AuthContext, component: str) -> JsonDict | None:
        """One department's operational picture: people, absences, queue, inquiries."""

        _require_staff(auth)
        now = self._clock()
        params = {
            "tenant_id": UUID(auth.tenant_id),
            "component": f"%{_escape_like(component.strip())}%",
            "now": now,
            "stale_before": now - STALE_WORK_AFTER,
            "week": now + timedelta(days=7),
        }
        name_sql = """
            SELECT component FROM staff_member
            WHERE tenant_id=:tenant_id AND component ILIKE :component ESCAPE '\\'
            GROUP BY component ORDER BY COUNT(*) DESC LIMIT 1
        """
        async with self._engine.connect() as connection:
            resolved = (await connection.execute(text(name_sql), params)).scalar_one_or_none()
            if resolved is None:
                return None
            params["exact"] = str(resolved)
            people = (
                (
                    await connection.execute(
                        text(
                            f"""
                        SELECT {_STAFF_BRIEF_COLUMNS},
                          (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                             AND w.assignee_id=m.id
                             AND w.status IN ('todo','in_progress',
                    'follow_up_required','blocked'))
                            AS open_items,
                          (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                             AND w.assignee_id=m.id
                             AND w.status IN ('todo','in_progress',
                    'follow_up_required','blocked')
                             AND w.due_at < :now) AS overdue_items,
                          (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                             AND w.assignee_id=m.id AND w.status='in_progress'
                             AND w.updated_at < :stale_before) AS stale_items,
                          (SELECT COUNT(*) FROM student_staff_assignment a
                             WHERE a.tenant_id=m.tenant_id AND a.staff_member_id=m.id
                               AND a.role='primary_advisor' AND a.ended_at IS NULL) AS advisees,
                          (SELECT string_agg(t.kind || '|' || t.ends_at::text, ';')
                             FROM staff_time_off t WHERE t.tenant_id=m.tenant_id
                              AND t.staff_member_id=m.id AND t.starts_at <= :now
                              AND t.ends_at > :now AND t.blocks_bookings) AS current_absence
                        FROM staff_member m
                        WHERE m.tenant_id=:tenant_id AND m.component=:exact
                        ORDER BY m.employment_status, m.display_name
                        """
                        ),
                        params,
                    )
                )
                .mappings()
                .all()
            )
            queue = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT COUNT(*) AS open,
                          COUNT(*) FILTER (WHERE assignee_id IS NULL) AS unassigned,
                          COUNT(*) FILTER (WHERE due_at IS NOT NULL AND due_at < :now) AS overdue,
                          COUNT(*) FILTER (WHERE priority='urgent') AS urgent,
                          COUNT(*) FILTER (WHERE escalated) AS escalated,
                          COUNT(*) FILTER (WHERE status='in_progress'
                                             AND updated_at < :stale_before) AS stale,
                          COUNT(*) FILTER (WHERE due_at IS NOT NULL
                                             AND due_at::date = (:now AT TIME ZONE 'UTC')::date)
                            AS due_today,
                          COUNT(*) FILTER (WHERE work_type='document_review') AS document_reviews,
                          COUNT(*) FILTER (WHERE work_type='document_review' AND due_at < :now)
                            AS overdue_document_reviews,
                          COUNT(DISTINCT student_id) AS distinct_students
                        FROM staff_work_item
                        WHERE tenant_id=:tenant_id AND component=:exact
                          AND status IN ('todo','in_progress','follow_up_required','blocked')
                        """
                        ),
                        params,
                    )
                )
                .mappings()
                .one()
            )
            inquiries = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT COUNT(*) FILTER (
                                 WHERE i.status IN ('new','open','waiting_on_student')) AS open,
                               COUNT(*) FILTER (WHERE i.status='new') AS awaiting_first_reply
                        FROM student_inquiry i
                        JOIN staff_member m ON m.id=i.assignee_id AND m.tenant_id=i.tenant_id
                        WHERE i.tenant_id=:tenant_id AND m.component=:exact
                          AND i.archived_at IS NULL
                        """
                        ),
                        params,
                    )
                )
                .mappings()
                .one()
            )
        members = []
        for row in people:
            entry = _map_brief(row)
            entry.update(
                {
                    "openItems": int(row["open_items"]),
                    "overdueItems": int(row["overdue_items"]),
                    "staleInProgress": int(row["stale_items"]),
                    "primaryAdvisees": int(row["advisees"]),
                    "currentAbsence": _parse_absence(row.get("current_absence")),
                }
            )
            members.append(entry)
        return {
            "component": str(resolved),
            "staff": {
                "total": len(members),
                "active": sum(1 for m in members if m["employmentStatus"] == "active"),
                "onLeave": [m["name"] for m in members if m["employmentStatus"] == "on_leave"],
                "departed": [m["name"] for m in members if m["employmentStatus"] == "departed"],
                "absentNow": [
                    {"name": m["name"], **(m["currentAbsence"] or {}), "openItems": m["openItems"]}
                    for m in members
                    if m["currentAbsence"] and m["employmentStatus"] == "active"
                ],
                "primaryAdvisees": sum(m["primaryAdvisees"] for m in members),
            },
            "members": sorted(members, key=lambda m: (-m["overdueItems"], m["name"]))[:25],
            "queue": {k: int(v) for k, v in queue.items()},
            "inquiries": {k: int(v) for k, v in inquiries.items()},
            "generatedAt": _iso(now),
        }


# ---------------------------------------------------------------------------
# mapping helpers
# ---------------------------------------------------------------------------


def _aware(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _map_brief(row: Mapping[Any, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "name": str(row["display_name"]),
        "email": str(row["email_normalized"]),
        "component": str(row["component"]),
        "title": row.get("title"),
        "roleCode": str(row.get("role_code") or "staff"),
        "externalRef": row.get("external_ref"),
        "employmentStatus": str(row.get("employment_status") or "active"),
        "active": bool(row.get("active", True)),
        "leaveUntil": _date(row.get("leave_until")),
        "endedAt": _date(row.get("ended_at")),
    }


def _parse_absence(value: object) -> JsonDict | None:
    if not value:
        return None
    first = str(value).split(";")[0]
    parts = first.split("|")
    kind = parts[0] if parts else "other"
    ends = parts[1] if len(parts) > 1 else None
    note = parts[2] if len(parts) > 2 else None
    return {"kind": kind, "endsAt": _date(ends), "note": note or None}


def _parse_upcoming(value: object) -> list[JsonDict]:
    if not value:
        return []
    out = []
    for chunk in str(value).split(";")[:4]:
        parts = chunk.split("|")
        if len(parts) >= 3:
            out.append({"kind": parts[0], "startsAt": _date(parts[1]), "endsAt": _date(parts[2])})
    return out


_WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def _weekday_names(value: object) -> list[str]:
    if not value:
        return []
    names = []
    for token in str(value).split(","):
        try:
            names.append(_WEEKDAYS[int(token)])
        except (ValueError, IndexError):
            continue
    return names


def _profile_flags(
    row: Mapping[str, Any], advisees: int, cap: object, calendar: Mapping[str, Any] | None
) -> list[str]:
    flags: list[str] = []
    status = str(row.get("employment_status") or "active")
    if status == "departed":
        flags.append("departed_with_caseload" if advisees else "departed")
    if status == "on_leave":
        flags.append("on_leave_with_caseload" if advisees else "on_leave")
    if cap and advisees > int(str(cap)):
        flags.append("over_cap")
    open_slots = int((calendar or {}).get("openSlotsNext14Days") or 0)
    if (
        status == "active"
        and calendar is not None
        and calendar.get("bookable")
        and open_slots == 0
        and bool(row.get("student_facing"))
    ):
        flags.append("no_open_slots")
    if int(row.get("stale_items") or 0) >= 3 or int(row.get("awaiting_outcome") or 0) >= 5:
        flags.append("falling_behind")
    if cap and status == "active" and advisees < 0.5 * int(str(cap)) and open_slots >= 40:
        flags.append("spare_capacity")
    if row.get("current_absence"):
        flags.append("absent_now")
    return flags
