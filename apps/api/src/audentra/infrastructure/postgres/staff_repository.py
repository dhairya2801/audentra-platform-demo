# ruff: noqa: S608 -- schema identifiers are validated before interpolation.
"""Tenant-safe PostgreSQL repository for the staff action center.

Every staff mutation uses one SQLAlchemy transaction. Canonical state, append-only
work history, optional student notification, audit, and outbox publication therefore
either commit together or all roll back.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import (
    ApiError,
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from audentra.domain.action_center import (
    AWAY_TIME_OFF_KINDS,
    DEFAULT_QUERY,
    MAX_GROUP_LIMIT,
    OPEN_WORK_STATUSES,
    QUEUE_GROUP_BY,
    STALE_AFTER,
    ActionCenterQuery,
    derive_signals,
    owner_risk_for,
)
from audentra.domain.document_review import public_review_decision
from audentra.domain.documents import bounded_document_label
from audentra.infrastructure.postgres.journey_routing import (
    reconcile_student_journey_routes,
)
from audentra.infrastructure.postgres.university_repository import PostgresUniversityRepository

_MISSING = object()
_NO_DEFAULT = object()
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WORK_ITEM_STATUSES = {
    "todo",
    "in_progress",
    "follow_up_required",
    "blocked",
    "done",
    "cancelled",
}
_TERMINAL_WORK_ITEM_STATUSES = {"done", "cancelled"}
_COMMUNICATION_CHANNELS = {"email", "sms", "voice", "portal"}
# The workspace cohort is one bounded read; the Students view searches the
# whole tenant through `search_students` instead of paging this copy.
ROSTER_WORKSPACE_LIMIT = 1000
ROSTER_SEARCH_MAX_LIMIT = 200
ROSTER_SEARCH_MAX_LENGTH = 120


_ATTENTION_LABELS = {
    "overdue_requirements": "overdue enrollment requirement",
    "blocking_requirements_open": "open blocking requirement",
    "overdue_work": "overdue staff action",
    "escalated_work": "escalated staff action",
    "no_primary_adviser": "no primary adviser assigned",
}


def _plural(count: int, label: str) -> str:
    return f"{count} {label}{'' if count == 1 else 's'}"


def student_attention(
    *,
    overdue_requirements: int,
    blocking_requirements_open: int,
    overdue_work: int,
    escalated_work: int,
    has_primary_adviser: bool,
    offer_accepted: bool,
    evaluated_at: str,
) -> dict[str, object]:
    """Rule-based attention signals, counted from canonical rows.

    There is no model behind this: each signal is a count the staff member can
    verify on the student's record, and the level is the strongest signal
    present. A student who has not accepted an offer is not flagged for
    missing an adviser, because no adviser is owed yet.
    """

    signals: list[dict[str, object]] = []
    if escalated_work > 0:
        signals.append(
            {
                "code": "escalated_work",
                "label": _plural(escalated_work, _ATTENTION_LABELS["escalated_work"]),
                "count": escalated_work,
            }
        )
    if overdue_requirements > 0:
        signals.append(
            {
                "code": "overdue_requirements",
                "label": _plural(overdue_requirements, _ATTENTION_LABELS["overdue_requirements"]),
                "count": overdue_requirements,
            }
        )
    if overdue_work > 0:
        signals.append(
            {
                "code": "overdue_work",
                "label": _plural(overdue_work, _ATTENTION_LABELS["overdue_work"]),
                "count": overdue_work,
            }
        )
    if blocking_requirements_open > 0:
        signals.append(
            {
                "code": "blocking_requirements_open",
                "label": _plural(
                    blocking_requirements_open,
                    _ATTENTION_LABELS["blocking_requirements_open"],
                ),
                "count": blocking_requirements_open,
            }
        )
    if offer_accepted and not has_primary_adviser:
        signals.append(
            {
                "code": "no_primary_adviser",
                "label": _ATTENTION_LABELS["no_primary_adviser"],
                "count": 1,
            }
        )
    if escalated_work > 0 or (overdue_requirements > 0 and overdue_work > 0):
        level = "urgent"
    elif overdue_requirements > 0 or overdue_work > 0:
        level = "attention"
    elif signals:
        level = "watch"
    else:
        level = "none"
    return {"level": level, "signals": signals, "evaluatedAt": evaluated_at}


_WORK_ITEM_PRIORITIES = {"low", "medium", "high", "urgent"}
_WORK_ACTION_TYPES = {
    "enrollment_follow_up",
    "onboarding_assistance",
    "document_review",
    "missing_information",
    "external_verification",
    "deadline_risk",
    "staff_decision",
    "communication_response",
    "blocked_dependency",
}


class StaffStudentReader(Protocol):
    """Narrow read port supplied by the future PostgreSQL student repository."""

    async def get_student_onboarding(self, auth: AuthContext) -> Mapping[str, object]: ...

    async def get_student_profile(self, auth: AuthContext) -> Mapping[str, object]: ...

    async def get_student_requirements(self, auth: AuthContext) -> Mapping[str, object]: ...

    async def get_student_documents(self, auth: AuthContext) -> Mapping[str, object]: ...


_DEFAULT_DOCUMENT_REJECTION_REASONS: tuple[dict[str, str], ...] = (
    {
        "code": "illegible",
        "label": "Image or text is unclear",
        "description": "Upload a clear, complete scan or photo with every edge visible.",
    },
    {
        "code": "incomplete",
        "label": "Document is incomplete",
        "description": "Upload every required page and complete all required fields or signatures.",
    },
    {
        "code": "wrong_document",
        "label": "Wrong document",
        "description": "Upload the document requested for this enrollment task.",
    },
    {
        "code": "expired",
        "label": "Document is expired",
        "description": "Upload a document that is currently valid.",
    },
    {
        "code": "information_mismatch",
        "label": "Information does not match",
        "description": "Correct the conflicting information or upload a matching document.",
    },
    {
        "code": "unsupported_evidence",
        "label": "Evidence cannot be accepted",
        "description": "Upload an official document that meets the requirement instructions.",
    },
)


class PostgresStaffRepository:
    """Port of the Nest staff action store using SQLAlchemy's async connection API."""

    def __init__(
        self,
        engine: AsyncEngine,
        student_reader: StaffStudentReader,
        *,
        university: PostgresUniversityRepository | None = None,
        schema: str = "public",
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not _SQL_IDENTIFIER.fullmatch(schema):
            raise ValueError("schema is not a safe PostgreSQL identifier")
        self._engine = engine
        self._reader = student_reader
        self._university = university
        self._schema = schema
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory

    def _table(self, name: str) -> str:
        return f"{self._schema}.{name}"

    async def get_action_center(
        self, auth: AuthContext, query: ActionCenterQuery = DEFAULT_QUERY
    ) -> dict[str, object]:
        self._require_staff(auth)
        await self._ensure_document_work_items(auth)
        return await self._read_action_center(auth, query)

    async def get_work_queue(
        self, auth: AuthContext, query: ActionCenterQuery = DEFAULT_QUERY
    ) -> dict[str, object]:
        """Read the canonical queue without reconciliation writes."""

        self._require_staff(auth)
        return await self._read_action_center(auth, query)

    async def _read_action_center(
        self, auth: AuthContext, query: ActionCenterQuery
    ) -> dict[str, object]:
        """One bounded page of the board plus board-wide counts and facets.

        The board is queried, never dumped: the page is limited, filters run in
        SQL, and history is attached only to the items on the page. Counts and
        facets are aggregates over the whole board so a client can render the
        filter bar and the column totals without a second read.
        """

        now = self._clock()
        tenant_id = _uuid(auth.tenant_id)
        where, params = self._board_filters(auth, query, now)
        params["tenant_id"] = tenant_id
        params["viewer_id"] = _uuid(auth.actor_id)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._engine.connect() as connection:
            member_result = await connection.execute(
                text(self._board_staff_sql()), {"tenant_id": tenant_id, "now": now}
            )
            page_result = await connection.execute(
                text(
                    self._board_items_sql(
                        where=where,
                        order=self._board_order_sql(query.sort),
                        limit=True,
                    )
                ),
                {**params, "limit": query.limit, "offset": query.offset},
            )
            page_rows = [dict(row) for row in page_result.mappings().all()]
            total_result = await connection.execute(
                text(
                    f"""
                    SELECT COUNT(*) AS total, COUNT(DISTINCT item.student_id) AS students
                    FROM {self._board_from_sql()}
                    WHERE {where}
                    """
                ),
                params,
            )
            total_row = total_result.mappings().one()
            counts_result = await connection.execute(
                text(self._board_counts_sql()),
                {"tenant_id": tenant_id, "now": now, "stale_before": now - STALE_AFTER},
            )
            counts_row = dict(counts_result.mappings().one())
            scope_result = await connection.execute(
                text(self._board_scope_counts_sql()),
                {
                    "tenant_id": tenant_id,
                    "viewer_id": params["viewer_id"],
                    "now": now,
                    "stale_before": now - STALE_AFTER,
                    "day_start": day_start,
                    "day_end": day_start + timedelta(days=1),
                },
            )
            scope_row = dict(scope_result.mappings().one())
            component_result = await connection.execute(
                text(self._board_component_facets_sql()),
                {"tenant_id": tenant_id, "now": now, "stale_before": now - STALE_AFTER},
            )
            assignee_result = await connection.execute(
                text(self._board_assignee_facets_sql()),
                {
                    "tenant_id": tenant_id,
                    "now": now,
                    "stale_before": now - STALE_AFTER,
                    "facet_limit": 25,
                },
            )
            logs = await self._logs_for_items(
                connection, tenant_id, [str(row["id"]) for row in page_rows]
            )
        members = [self._map_board_member(dict(row)) for row in member_result.mappings().all()]
        items = [self._map_work_item(row, logs, now=now) for row in page_rows]
        total = int(total_row["total"])
        return {
            "items": items,
            "staff": members,
            "counts": {
                "todo": int(counts_row["todo"]),
                "inProgress": int(counts_row["in_progress"]),
                "followUpRequired": int(counts_row["follow_up_required"]),
                "blocked": int(counts_row["blocked"]),
                "done": int(counts_row["done"]),
                "cancelled": int(counts_row["cancelled"]),
                "urgent": int(counts_row["urgent"]),
                "escalated": int(counts_row["escalated"]),
                "open": int(counts_row["open"]),
                "overdue": int(counts_row["overdue"]),
                "stale": int(counts_row["stale"]),
                "unassigned": int(counts_row["unassigned"]),
                "ownerRisk": int(counts_row["owner_risk"]),
            },
            "scopes": self._map_scope_counts(scope_row),
            "page": {
                "limit": query.limit,
                "offset": query.offset,
                "total": total,
                "hasMore": query.offset + len(items) < total,
                "distinctStudents": int(total_row["students"]),
            },
            "facets": {
                "components": [
                    {
                        "component": str(row["component"]),
                        "open": int(row["open"]),
                        "overdue": int(row["overdue"]),
                        "unassigned": int(row["unassigned"]),
                        "stale": int(row["stale"]),
                        "ownerRisk": int(row["owner_risk"]),
                        "urgent": int(row["urgent"]),
                    }
                    for row in component_result.mappings().all()
                ],
                "assignees": [
                    {
                        "staff": (
                            self._map_board_member(dict(row)) if row.get("id") is not None else None
                        ),
                        "open": int(row["open"]),
                        "overdue": int(row["overdue"]),
                        "stale": int(row["stale"]),
                        "urgent": int(row["urgent"]),
                    }
                    for row in assignee_result.mappings().all()
                ],
            },
            "query": query.public(),
            "generatedAt": _iso_timestamp(now),
        }

    async def summarize_work_queue(
        self,
        auth: AuthContext,
        query: ActionCenterQuery = DEFAULT_QUERY,
        *,
        group_by: str | None = None,
        limit: int = 12,
    ) -> dict[str, object]:
        """Counts over the items a query matches, optionally bucketed, in SQL.

        The same filter vocabulary as the board page, so "how many unassigned
        Registrar items are overdue" and the Action Center's filtered view can
        never disagree. Never returns items.
        """

        self._require_staff(auth)
        if group_by is not None and group_by not in QUEUE_GROUP_BY:
            raise BadRequestError(
                "INVALID_ACTION_CENTER_QUERY", f"Unknown queue grouping {group_by!r}"
            )
        now = self._clock()
        where, params = self._board_filters(auth, query, now)
        params["tenant_id"] = _uuid(auth.tenant_id)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        params["day_start"] = day_start
        params["day_end"] = day_start + timedelta(days=1)
        params["week_end"] = now + timedelta(days=7)
        measures = f"""
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE item.assignee_id IS NULL) AS unassigned,
              COUNT(*) FILTER (WHERE item.priority = 'urgent') AS urgent,
              COUNT(*) FILTER (WHERE item.priority IN ('urgent', 'high')) AS urgent_or_high,
              COUNT(*) FILTER (WHERE item.escalated) AS escalated,
              COUNT(*) FILTER (WHERE item.due_at IS NOT NULL AND item.due_at < :now) AS overdue,
              COUNT(*) FILTER (WHERE item.due_at >= :day_start AND item.due_at < :day_end)
                AS due_today,
              COUNT(*) FILTER (WHERE item.due_at >= :now AND item.due_at < :week_end)
                AS due_next_7_days,
              COUNT(*) FILTER (WHERE {self._stale_sql()}) AS stale_in_progress,
              COUNT(*) FILTER (WHERE item.status = 'todo') AS todo,
              COUNT(*) FILTER (WHERE item.status = 'in_progress') AS in_progress,
              COUNT(*) FILTER (WHERE item.status = 'blocked') AS blocked,
              COUNT(*) FILTER (WHERE item.status = 'follow_up_required') AS follow_up_required,
              COUNT(DISTINCT item.student_id) AS distinct_students,
              MIN(item.due_at) FILTER (WHERE item.due_at < :now) AS oldest_due
        """
        bucket_sql = {
            "assignee": "COALESCE(assignee.display_name, 'Unassigned')",
            "component": "item.component",
            "status": "item.status",
            "priority": "item.priority",
            "action_type": "item.action_type",
            "work_type": "item.work_type",
            "student": "person.first_name || ' ' || person.last_name",
            "due_window": (
                "CASE WHEN item.due_at IS NULL THEN 'no_due'"
                " WHEN item.due_at < :now THEN 'overdue'"
                " WHEN item.due_at < :day_end THEN 'today'"
                " WHEN item.due_at < :week_end THEN 'seven_days' ELSE 'later' END"
            ),
        }
        async with self._engine.connect() as connection:
            totals = (
                (
                    await connection.execute(
                        text(f"SELECT {measures} FROM {self._board_from_sql()} WHERE {where}"),
                        params,
                    )
                )
                .mappings()
                .one()
            )
            buckets: list[dict[str, object]] = []
            if group_by is not None:
                params["group_limit"] = max(1, min(int(limit or 12), MAX_GROUP_LIMIT))
                bucket_rows = (
                    (
                        await connection.execute(
                            text(
                                f"""
                                SELECT {bucket_sql[group_by]} AS bucket, COUNT(*) AS count,
                                  COUNT(*) FILTER (WHERE item.due_at IS NOT NULL
                                                     AND item.due_at < :now) AS overdue,
                                  COUNT(*) FILTER (WHERE item.assignee_id IS NULL) AS unassigned,
                                  COUNT(*) FILTER (WHERE item.priority = 'urgent') AS urgent,
                                  COUNT(*) FILTER (WHERE {self._stale_sql()}) AS stale
                                FROM {self._board_from_sql()}
                                WHERE {where}
                                GROUP BY 1
                                ORDER BY count DESC, bucket
                                LIMIT :group_limit
                                """
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .all()
                )
                buckets = [dict(row) for row in bucket_rows]
        oldest_due = totals["oldest_due"]
        return {
            "filters": {
                key: value
                for key, value in query.public().items()
                if key not in {"limit", "offset", "sort"} and value not in (None, "", "all")
            },
            "total": int(totals["total"]),
            "unassigned": int(totals["unassigned"]),
            "urgent": int(totals["urgent"]),
            "urgentOrHigh": int(totals["urgent_or_high"]),
            "escalated": int(totals["escalated"]),
            "overdue": int(totals["overdue"]),
            "dueToday": int(totals["due_today"]),
            "dueNext7Days": int(totals["due_next_7_days"]),
            "staleInProgress": int(totals["stale_in_progress"]),
            "byStatus": {
                "todo": int(totals["todo"]),
                "inProgress": int(totals["in_progress"]),
                "blocked": int(totals["blocked"]),
                "followUpRequired": int(totals["follow_up_required"]),
            },
            "distinctStudents": int(totals["distinct_students"]),
            "oldestDueAt": _iso_timestamp(oldest_due) if oldest_due is not None else None,
            "groupBy": group_by,
            "buckets": [
                {
                    "value": str(row["bucket"]),
                    "count": int(cast(int, row["count"])),
                    "overdue": int(cast(int, row["overdue"])),
                    "unassigned": int(cast(int, row["unassigned"])),
                    "urgent": int(cast(int, row["urgent"])),
                    "stale": int(cast(int, row["stale"])),
                }
                for row in buckets
            ],
            "generatedAt": _iso_timestamp(now),
        }

    async def find_work_item_by_key(self, auth: AuthContext, key: str) -> dict[str, object]:
        """The id and status behind a pasted key ("AST-00102"): one indexed row,
        or an empty mapping when no item carries the key."""

        self._require_staff(auth)
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text(
                            f"""
                            SELECT id, key, status FROM {self._table("staff_work_item")}
                            WHERE tenant_id = :tenant_id AND UPPER(key) = UPPER(:key)
                            LIMIT 1
                            """
                        ),
                        {"tenant_id": _uuid(auth.tenant_id), "key": key.strip()},
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return {}
        return {"id": str(row["id"]), "key": str(row["key"]), "status": str(row["status"])}

    async def _read_work_item(
        self, auth: AuthContext, work_item_id: str, *, with_history: bool = True
    ) -> dict[str, object] | None:
        """One item by id, with its own history only."""

        now = self._clock()
        tenant_id = _uuid(auth.tenant_id)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    self._board_items_sql(
                        where="item.tenant_id = :tenant_id AND item.id = :work_item_id",
                        order="item.id",
                        limit=False,
                    )
                ),
                {
                    "tenant_id": tenant_id,
                    "work_item_id": _uuid(work_item_id),
                    "viewer_id": _uuid(auth.actor_id),
                    "now": now,
                },
            )
            row = result.mappings().first()
            if row is None:
                return None
            logs = (
                await self._logs_for_items(connection, tenant_id, [work_item_id])
                if with_history
                else []
            )
        return self._map_work_item(dict(row), logs, now=now)

    async def _logs_for_items(
        self, connection: AsyncConnection, tenant_id: UUID, work_item_ids: list[str]
    ) -> list[dict[str, object]]:
        if not work_item_ids:
            return []
        result = await connection.execute(
            text(
                f"""
                SELECT id, work_item_id, action, message, actor_name, occurred_at
                FROM {self._table("staff_work_log")}
                WHERE tenant_id = :tenant_id AND work_item_id = ANY(:ids)
                ORDER BY occurred_at DESC, id
                """
            ),
            {"tenant_id": tenant_id, "ids": [_uuid(value) for value in work_item_ids]},
        )
        return [dict(row) for row in result.mappings().all()]

    def _board_filters(
        self, auth: AuthContext, query: ActionCenterQuery, now: datetime
    ) -> tuple[str, dict[str, object]]:
        clauses = ["item.tenant_id = :tenant_id", "item.status = ANY(:statuses)"]
        params: dict[str, object] = {
            "statuses": list(query.statuses),
            "now": now,
            "stale_before": now - STALE_AFTER,
        }
        if query.board_project:
            from .work_board_sql import PROJECT_SQL

            clauses.append(f"({PROJECT_SQL}) = :board_project")
            params["board_project"] = query.board_project
        if query.board_attention:
            from .work_board_sql import ATTENTION_SQL

            clauses.append(f"({ATTENTION_SQL})")
        if query.priority:
            clauses.append("item.priority = :priority")
            params["priority"] = query.priority
        if query.component:
            clauses.append("item.component ILIKE :component_pattern")
            params["component_pattern"] = f"%{_escape_like(query.component)}%"
        if query.assignee == "unassigned":
            clauses.append("item.assignee_id IS NULL")
        elif query.assignee == "me":
            clauses.append("item.assignee_id = :assignee_id")
            params["assignee_id"] = _uuid(auth.actor_id)
        elif query.assignee:
            if _UUID_PATTERN.fullmatch(query.assignee):
                clauses.append("item.assignee_id = :assignee_id")
                params["assignee_id"] = _uuid(query.assignee)
            else:
                # A name rather than an id: match the owner's display name.
                clauses.append("assignee.display_name ILIKE :assignee_pattern")
                params["assignee_pattern"] = f"%{_escape_like(query.assignee)}%"
        if query.search:
            clauses.append(
                "(item.key ILIKE :search_pattern OR item.id::text ILIKE :search_pattern"
                " OR item.title ILIKE :search_pattern"
                " OR item.description ILIKE :search_pattern"
                " OR item.component ILIKE :search_pattern"
                " OR person.first_name ILIKE :search_pattern"
                " OR person.last_name ILIKE :search_pattern"
                " OR (person.first_name || ' ' || person.last_name) ILIKE :search_pattern"
                " OR assignee.display_name ILIKE :search_pattern)"
            )
            params["search_pattern"] = f"%{_escape_like(query.search)}%"
        if query.due == "overdue":
            clauses.append("item.due_at IS NOT NULL AND item.due_at < :now")
        elif query.due == "today":
            # "Due today" is the calendar day, whether or not the hour has
            # passed: an item due at 09:00 is still due today at 17:00 (and is
            # also overdue). The windows are not mutually exclusive.
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            clauses.append("item.due_at >= :day_start AND item.due_at < :day_end")
            params["day_start"] = day_start
            params["day_end"] = day_start + timedelta(days=1)
        elif query.due == "seven_days":
            clauses.append("item.due_at >= :now AND item.due_at < :week_end")
            params["week_end"] = now + timedelta(days=7)
        elif query.due == "no_due":
            clauses.append("item.due_at IS NULL")
        if query.stale is not None:
            clauses.append(("" if query.stale else "NOT ") + f"({self._stale_sql()})")
        if query.owner_risk is not None:
            clauses.append(("" if query.owner_risk else "NOT ") + f"({self._owner_risk_sql()})")
        if query.escalated is not None:
            clauses.append("item.escalated = :escalated")
            params["escalated"] = query.escalated
        if query.action_type:
            clauses.append("item.action_type = :action_type")
            params["action_type"] = query.action_type
        if query.work_type:
            clauses.append("item.work_type = :work_type")
            params["work_type"] = query.work_type
        if query.student_id:
            clauses.append("item.student_id = :student_id")
            params["student_id"] = _uuid(query.student_id)
        if query.in_progress_days is not None:
            clauses.append("item.status = 'in_progress' AND item.updated_at < :in_progress_before")
            params["in_progress_before"] = now - timedelta(days=query.in_progress_days)
        return " AND ".join(clauses), params

    @staticmethod
    def _stale_sql() -> str:
        return "item.status = 'in_progress' AND item.updated_at < :stale_before"

    @staticmethod
    def _owner_risk_sql() -> str:
        return (
            "item.assignee_id IS NOT NULL AND ("
            "assignee.employment_status IN ('departed', 'on_leave')"
            " OR away.ends_at IS NOT NULL)"
        )

    def _board_order_sql(self, sort: str) -> str:
        closed_last = "CASE WHEN item.status IN ('done', 'cancelled') THEN 1 ELSE 0 END"
        priority = (
            "CASE item.priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2"
            " WHEN 'medium' THEN 3 ELSE 4 END"
        )
        if sort == "due":
            return f"{closed_last}, item.due_at NULLS LAST, {priority}, item.id"
        if sort == "updated":
            return f"{closed_last}, item.updated_at DESC, item.id"
        if sort == "created":
            return f"{closed_last}, item.created_at, item.id"
        if sort == "stale":
            return f"{closed_last}, item.updated_at, item.id"
        if sort == "attention":
            # Escalated, then overdue, then priority, then the nearest due
            # date: the order a person reads their own queue in.
            overdue = "(item.due_at IS NOT NULL AND item.due_at < :now)"
            return (
                f"{closed_last}, item.escalated DESC, {overdue} DESC, {priority},"
                " item.due_at NULLS LAST, item.updated_at DESC, item.id"
            )
        return f"{closed_last}, {priority}, item.due_at NULLS LAST, item.updated_at DESC, item.id"

    def _board_from_sql(self) -> str:
        item = self._table("staff_work_item")
        student = self._table("student")
        person = self._table("person")
        profile = self._table("student_profile")
        member = self._table("staff_member")
        time_off = self._table("staff_time_off")
        away_kinds = ", ".join(f"'{kind}'" for kind in AWAY_TIME_OFF_KINDS)
        return f"""{item} AS item
            JOIN {student} AS student
              ON student.id = item.student_id AND student.tenant_id = item.tenant_id
            JOIN {person} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {profile} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            LEFT JOIN {member} AS assignee
              ON assignee.id = item.assignee_id AND assignee.tenant_id = item.tenant_id
            LEFT JOIN LATERAL (
              SELECT off.ends_at, off.kind
              FROM {time_off} AS off
              WHERE off.tenant_id = item.tenant_id
                AND off.staff_member_id = item.assignee_id
                AND off.kind IN ({away_kinds})
                AND off.starts_at <= :now AND off.ends_at > :now
              ORDER BY off.ends_at DESC
              LIMIT 1
            ) AS away ON item.assignee_id IS NOT NULL
        """

    def _board_items_sql(self, *, where: str, order: str, limit: bool) -> str:
        """The board projection. ``:viewer_id`` (the reading staff member) is a
        required parameter: each item carries the reader's current caseload
        roles for its student, so a card can say "your advisee"."""

        offer = self._table("admission_offer")
        program = self._table("program")
        assignment = self._table("student_staff_assignment")
        page = "LIMIT :limit OFFSET :offset" if limit else ""
        return f"""
            SELECT
              item.id, item.key, item.student_id, item.title, item.description,
              item.status, item.priority, item.work_type, item.component,
              item.due_at, item.escalated, item.version, item.created_at,
              item.updated_at, item.assignee_id, item.action_type,
              item.selected_channel, item.attempt_count, item.follow_up_at,
              item.blocker_code, item.blocker_detail, item.blocker_review_at,
              item.outcome_code, item.resolution_code, item.next_step,
              item.terminal_reason, item.started_at,
              item.interaction_completed_at, item.completed_at, item.cancelled_at,
              assignee.display_name AS assignee_name,
              assignee.email_normalized AS assignee_email,
              assignee.component AS assignee_component,
              assignee.employment_status AS assignee_employment_status,
              assignee.leave_until AS assignee_leave_until,
              assignee.title AS assignee_title,
              away.ends_at AS assignee_away_until,
              away.kind AS assignee_away_kind,
              person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name,
              COALESCE(onboarding.payload->>'universityProgramName',
                offer_program.name, 'Program not assigned') AS program_name,
              student.class_year, item.source_type, item.source_id,
              viewer_caseload.roles AS viewer_roles
            FROM {self._board_from_sql()}
            LEFT JOIN {self._table("student_onboarding")} AS onboarding
              ON onboarding.student_id = student.id AND onboarding.tenant_id = student.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name
              FROM {offer} AS offer
              JOIN {program} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = item.tenant_id
                AND offer.student_id = item.student_id
              ORDER BY offer.created_at DESC
              LIMIT 1
            ) AS offer_program ON true
            LEFT JOIN LATERAL (
              SELECT array_agg(assignment.role ORDER BY assignment.role) AS roles
              FROM {assignment} AS assignment
              WHERE assignment.tenant_id = item.tenant_id
                AND assignment.student_id = item.student_id
                AND assignment.staff_member_id = :viewer_id
                AND assignment.ended_at IS NULL
            ) AS viewer_caseload ON true
            WHERE {where}
            ORDER BY {order}
            {page}
        """

    def _board_staff_sql(self) -> str:
        member = self._table("staff_member")
        time_off = self._table("staff_time_off")
        away_kinds = ", ".join(f"'{kind}'" for kind in AWAY_TIME_OFF_KINDS)
        return f"""
            SELECT m.id, m.display_name, m.email_normalized, m.component, m.title,
                   m.employment_status, m.leave_until, m.timezone,
                   away.ends_at AS away_until, away.kind AS away_kind
            FROM {member} AS m
            LEFT JOIN LATERAL (
              SELECT off.ends_at, off.kind
              FROM {time_off} AS off
              WHERE off.tenant_id = m.tenant_id AND off.staff_member_id = m.id
                AND off.kind IN ({away_kinds})
                AND off.starts_at <= :now AND off.ends_at > :now
              ORDER BY off.ends_at DESC
              LIMIT 1
            ) AS away ON true
            WHERE m.tenant_id = :tenant_id AND m.active = true
            ORDER BY m.display_name, m.id
        """

    def _board_counts_sql(self) -> str:
        open_statuses = ", ".join(f"'{status}'" for status in OPEN_WORK_STATUSES)
        return f"""
            SELECT
              COUNT(*) FILTER (WHERE item.status = 'todo') AS todo,
              COUNT(*) FILTER (WHERE item.status = 'in_progress') AS in_progress,
              COUNT(*) FILTER (WHERE item.status = 'follow_up_required')
                AS follow_up_required,
              COUNT(*) FILTER (WHERE item.status = 'blocked') AS blocked,
              COUNT(*) FILTER (WHERE item.status = 'done') AS done,
              COUNT(*) FILTER (WHERE item.status = 'cancelled') AS cancelled,
              COUNT(*) FILTER (WHERE item.status IN ({open_statuses})
                                 AND item.priority = 'urgent') AS urgent,
              COUNT(*) FILTER (WHERE item.status IN ({open_statuses})
                                 AND item.escalated) AS escalated,
              COUNT(*) FILTER (WHERE item.status IN ({open_statuses})) AS open,
              COUNT(*) FILTER (WHERE item.status IN ({open_statuses})
                                 AND item.due_at IS NOT NULL AND item.due_at < :now)
                AS overdue,
              COUNT(*) FILTER (WHERE {self._stale_sql()}) AS stale,
              COUNT(*) FILTER (WHERE item.status IN ({open_statuses})
                                 AND item.assignee_id IS NULL) AS unassigned,
              COUNT(*) FILTER (WHERE item.status IN ({open_statuses})
                                 AND {self._owner_risk_sql()}) AS owner_risk
            FROM {self._table("staff_work_item")} AS item
            LEFT JOIN {self._table("staff_member")} AS assignee
              ON assignee.id = item.assignee_id AND assignee.tenant_id = item.tenant_id
            LEFT JOIN LATERAL (
              SELECT off.ends_at
              FROM {self._table("staff_time_off")} AS off
              WHERE off.tenant_id = item.tenant_id
                AND off.staff_member_id = item.assignee_id
                AND off.kind IN ({", ".join(f"'{kind}'" for kind in AWAY_TIME_OFF_KINDS)})
                AND off.starts_at <= :now AND off.ends_at > :now
              LIMIT 1
            ) AS away ON item.assignee_id IS NOT NULL
            WHERE item.tenant_id = :tenant_id
        """

    def _board_scope_counts_sql(self) -> str:
        """Counts for the reader's three scopes in one pass over the tenant.

        ``mine`` is work assigned to the reader; ``myComponent`` is the work of
        the reader's component (the item's component); ``all`` is the tenant.
        Every figure is an aggregate in SQL, never a count over a page.
        """

        open_statuses = ", ".join(f"'{status}'" for status in OPEN_WORK_STATUSES)
        viewer_component = (
            f"(SELECT viewer.component FROM {self._table('staff_member')} AS viewer"
            " WHERE viewer.id = :viewer_id AND viewer.tenant_id = :tenant_id)"
        )
        predicates = {
            "mine": "item.assignee_id = :viewer_id",
            "team": f"item.component = {viewer_component}",
            "all": "TRUE",
        }
        is_open = f"item.status IN ({open_statuses})"
        measures = {
            "todo": "item.status = 'todo'",
            "in_progress": "item.status = 'in_progress'",
            "follow_up_required": "item.status = 'follow_up_required'",
            "blocked": "item.status = 'blocked'",
            "done": "item.status = 'done'",
            "cancelled": "item.status = 'cancelled'",
            "open": is_open,
            "overdue": f"{is_open} AND item.due_at IS NOT NULL AND item.due_at < :now",
            "due_today": f"{is_open} AND item.due_at >= :day_start AND item.due_at < :day_end",
            "urgent": f"{is_open} AND item.priority = 'urgent'",
            "escalated": f"{is_open} AND item.escalated",
            "stale": self._stale_sql(),
            "unassigned": f"{is_open} AND item.assignee_id IS NULL",
        }
        columns = [f"{viewer_component} AS viewer_component"]
        for scope, predicate in predicates.items():
            for name, condition in measures.items():
                columns.append(
                    f"COUNT(*) FILTER (WHERE ({predicate}) AND ({condition})) AS {scope}_{name}"
                )
            columns.append(
                f"COUNT(DISTINCT item.student_id) FILTER (WHERE ({predicate}) AND {is_open})"
                f" AS {scope}_students"
            )
        return f"""
            SELECT {", ".join(columns)}
            FROM {self._table("staff_work_item")} AS item
            WHERE item.tenant_id = :tenant_id
        """

    @staticmethod
    def _map_scope_counts(row: Mapping[str, object]) -> dict[str, object]:
        def scope(prefix: str) -> dict[str, int]:
            return {
                "todo": int(cast(int, row[f"{prefix}_todo"])),
                "inProgress": int(cast(int, row[f"{prefix}_in_progress"])),
                "followUpRequired": int(cast(int, row[f"{prefix}_follow_up_required"])),
                "blocked": int(cast(int, row[f"{prefix}_blocked"])),
                "done": int(cast(int, row[f"{prefix}_done"])),
                "cancelled": int(cast(int, row[f"{prefix}_cancelled"])),
                "open": int(cast(int, row[f"{prefix}_open"])),
                "overdue": int(cast(int, row[f"{prefix}_overdue"])),
                "dueToday": int(cast(int, row[f"{prefix}_due_today"])),
                "urgent": int(cast(int, row[f"{prefix}_urgent"])),
                "escalated": int(cast(int, row[f"{prefix}_escalated"])),
                "stale": int(cast(int, row[f"{prefix}_stale"])),
                "unassigned": int(cast(int, row[f"{prefix}_unassigned"])),
                "students": int(cast(int, row[f"{prefix}_students"])),
            }

        component = row.get("viewer_component")
        return {
            "component": str(component) if component is not None else None,
            "mine": scope("mine"),
            "myComponent": scope("team"),
            "all": scope("all"),
        }

    def _board_open_from_sql(self) -> str:
        open_statuses = ", ".join(f"'{status}'" for status in OPEN_WORK_STATUSES)
        away_kinds = ", ".join(f"'{kind}'" for kind in AWAY_TIME_OFF_KINDS)
        return f"""{self._table("staff_work_item")} AS item
            LEFT JOIN {self._table("staff_member")} AS assignee
              ON assignee.id = item.assignee_id AND assignee.tenant_id = item.tenant_id
            LEFT JOIN LATERAL (
              SELECT off.ends_at, off.kind
              FROM {self._table("staff_time_off")} AS off
              WHERE off.tenant_id = item.tenant_id
                AND off.staff_member_id = item.assignee_id
                AND off.kind IN ({away_kinds})
                AND off.starts_at <= :now AND off.ends_at > :now
              ORDER BY off.ends_at DESC
              LIMIT 1
            ) AS away ON item.assignee_id IS NOT NULL
            WHERE item.tenant_id = :tenant_id AND item.status IN ({open_statuses})
        """

    def _board_component_facets_sql(self) -> str:
        return f"""
            SELECT item.component,
                   COUNT(*) AS open,
                   COUNT(*) FILTER (WHERE item.due_at IS NOT NULL AND item.due_at < :now)
                     AS overdue,
                   COUNT(*) FILTER (WHERE item.assignee_id IS NULL) AS unassigned,
                   COUNT(*) FILTER (WHERE {self._stale_sql()}) AS stale,
                   COUNT(*) FILTER (WHERE {self._owner_risk_sql()}) AS owner_risk,
                   COUNT(*) FILTER (WHERE item.priority = 'urgent') AS urgent
            FROM {self._board_open_from_sql()}
            GROUP BY item.component
            ORDER BY open DESC, item.component
        """

    def _board_assignee_facets_sql(self) -> str:
        return f"""
            SELECT assignee.id, assignee.display_name, assignee.email_normalized,
                   assignee.component, assignee.title, assignee.employment_status,
                   assignee.leave_until,
                   MAX(away.ends_at) AS away_until, MIN(away.kind) AS away_kind,
                   COUNT(*) AS open,
                   COUNT(*) FILTER (WHERE item.due_at IS NOT NULL AND item.due_at < :now)
                     AS overdue,
                   COUNT(*) FILTER (WHERE {self._stale_sql()}) AS stale,
                   COUNT(*) FILTER (WHERE item.priority = 'urgent') AS urgent
            FROM {self._board_open_from_sql()}
            GROUP BY assignee.id, assignee.display_name, assignee.email_normalized,
                     assignee.component, assignee.title, assignee.employment_status,
                     assignee.leave_until
            ORDER BY (assignee.id IS NOT NULL), open DESC, assignee.display_name
            LIMIT :facet_limit
        """

    @staticmethod
    def _map_board_member(row: Mapping[str, object]) -> dict[str, object]:
        away_until = row.get("away_until")
        return {
            "id": str(row["id"]),
            "name": str(row["display_name"]),
            "email": str(row["email_normalized"]),
            "component": str(row["component"]),
            "title": str(row["title"]) if row.get("title") is not None else None,
            "employmentStatus": str(row.get("employment_status") or "active"),
            "leaveUntil": (str(row["leave_until"]) if row.get("leave_until") is not None else None),
            "awayUntil": _iso_timestamp(away_until) if away_until is not None else None,
            "awayKind": str(row["away_kind"]) if row.get("away_kind") is not None else None,
            **({"timezone": str(row["timezone"])} if row.get("timezone") else {}),
        }

    async def get_managed_content(self, auth: AuthContext) -> dict[str, object]:
        """Read durable staff-only content used by the workspace editors."""

        self._require_staff(auth)
        async with self._engine.connect() as connection:
            knowledge_result = await connection.execute(
                text(
                    f"""
                    SELECT id, title, summary, body, category, audience, status,
                           owner_name, version, updated_at
                    FROM {self._table("staff_knowledge_card")}
                    WHERE tenant_id=:tenant_id
                    ORDER BY updated_at DESC, title, id
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            play_result = await connection.execute(
                text(
                    f"""
                    SELECT id, title, description, trigger_description, audience,
                           steps, status, owner_name, version, updated_at
                    FROM {self._table("staff_core_play")}
                    WHERE tenant_id=:tenant_id
                    ORDER BY updated_at DESC, title, id
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            return {
                "knowledgeBase": [
                    _map_knowledge_card(dict(row)) for row in knowledge_result.mappings().all()
                ],
                "corePlays": [_map_core_play(dict(row)) for row in play_result.mappings().all()],
            }

    async def create_knowledge_card(
        self, auth: AuthContext, payload: Mapping[str, object], request_id: str
    ) -> dict[str, object]:
        self._require_staff(auth)
        card_id = str(self._uuid_factory())
        async with self._engine.begin() as connection:
            owner = await self._staff_display_name(connection, auth)
            result = await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("staff_knowledge_card")} (
                      id, tenant_id, title, summary, body, category, audience,
                      status, owner_name, version, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :title, :summary, :body, :category,
                      :audience, :status, :owner, 1, NOW(), NOW()
                    )
                    RETURNING id, title, summary, body, category, audience, status,
                              owner_name, version, updated_at
                    """
                ),
                {
                    "id": _uuid(card_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "title": str(_read(payload, "title")),
                    "summary": str(_read(payload, "summary")),
                    "body": str(_read(payload, "body")),
                    "category": str(_read(payload, "category")),
                    "audience": str(_read(payload, "audience")),
                    "status": str(_read(payload, "status")),
                    "owner": owner,
                },
            )
            row = result.mappings().one()
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff.knowledge_card_created",
                resource_type="staff_knowledge_card",
                resource_id=card_id,
                metadata={"version": 1},
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="staff.knowledge_card_created.v1",
                aggregate_type="staff_knowledge_card",
                aggregate_id=card_id,
                aggregate_version=1,
                data={"status": str(row["status"])},
            )
            return _map_knowledge_card(dict(row))

    async def update_knowledge_card(
        self,
        auth: AuthContext,
        card_id: str,
        payload: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(payload, "expectedVersion", "expected_version"), "expectedVersion"
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_knowledge_card")} SET
                      title=:title, summary=:summary, body=:body, category=:category,
                      audience=:audience, status=:status, version=version+1,
                      updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND version=:expected_version
                    RETURNING id, title, summary, body, category, audience, status,
                              owner_name, version, updated_at
                    """
                ),
                {
                    "id": _uuid(card_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "expected_version": expected_version,
                    "title": str(_read(payload, "title")),
                    "summary": str(_read(payload, "summary")),
                    "body": str(_read(payload, "body")),
                    "category": str(_read(payload, "category")),
                    "audience": str(_read(payload, "audience")),
                    "status": str(_read(payload, "status")),
                },
            )
            row = result.mappings().first()
            if row is None:
                await self._raise_content_write_error(
                    connection,
                    table="staff_knowledge_card",
                    tenant_id=auth.tenant_id,
                    resource_id=card_id,
                    not_found_code="STAFF_KNOWLEDGE_CARD_NOT_FOUND",
                    label="Knowledge card",
                )
            assert row is not None
            version = int(row["version"])
            await self._record_content_change(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff.knowledge_card_updated",
                event_name="staff.knowledge_card_updated.v1",
                resource_type="staff_knowledge_card",
                resource_id=card_id,
                version=version,
                data={"status": str(row["status"])},
            )
            return _map_knowledge_card(dict(row))

    async def create_core_play(
        self, auth: AuthContext, payload: Mapping[str, object], request_id: str
    ) -> dict[str, object]:
        self._require_staff(auth)
        play_id = str(self._uuid_factory())
        async with self._engine.begin() as connection:
            owner = await self._staff_display_name(connection, auth)
            result = await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("staff_core_play")} (
                      id, tenant_id, title, description, trigger_description,
                      audience, steps, status, owner_name, version, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :title, :description, :trigger,
                      :audience, CAST(:steps AS jsonb), :status, :owner, 1, NOW(), NOW()
                    )
                    RETURNING id, title, description, trigger_description, audience,
                              steps, status, owner_name, version, updated_at
                    """
                ),
                {
                    "id": _uuid(play_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "title": str(_read(payload, "title")),
                    "description": str(_read(payload, "description")),
                    "trigger": str(_read(payload, "trigger")),
                    "audience": str(_read(payload, "audience")),
                    "steps": json.dumps(list(cast(list[object], _read(payload, "steps")))),
                    "status": str(_read(payload, "status")),
                    "owner": owner,
                },
            )
            row = result.mappings().one()
            await self._record_content_change(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff.core_play_created",
                event_name="staff.core_play_created.v1",
                resource_type="staff_core_play",
                resource_id=play_id,
                version=1,
                data={"status": str(row["status"])},
            )
            return _map_core_play(dict(row))

    async def update_core_play(
        self,
        auth: AuthContext,
        play_id: str,
        payload: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(payload, "expectedVersion", "expected_version"), "expectedVersion"
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_core_play")} SET
                      title=:title, description=:description,
                      trigger_description=:trigger, audience=:audience,
                      steps=CAST(:steps AS jsonb), status=:status,
                      version=version+1, updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND version=:expected_version
                    RETURNING id, title, description, trigger_description, audience,
                              steps, status, owner_name, version, updated_at
                    """
                ),
                {
                    "id": _uuid(play_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "expected_version": expected_version,
                    "title": str(_read(payload, "title")),
                    "description": str(_read(payload, "description")),
                    "trigger": str(_read(payload, "trigger")),
                    "audience": str(_read(payload, "audience")),
                    "steps": json.dumps(list(cast(list[object], _read(payload, "steps")))),
                    "status": str(_read(payload, "status")),
                },
            )
            row = result.mappings().first()
            if row is None:
                await self._raise_content_write_error(
                    connection,
                    table="staff_core_play",
                    tenant_id=auth.tenant_id,
                    resource_id=play_id,
                    not_found_code="STAFF_CORE_PLAY_NOT_FOUND",
                    label="Core play",
                )
            assert row is not None
            version = int(row["version"])
            await self._record_content_change(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff.core_play_updated",
                event_name="staff.core_play_updated.v1",
                resource_type="staff_core_play",
                resource_id=play_id,
                version=version,
                data={"status": str(row["status"])},
            )
            return _map_core_play(dict(row))

    async def create_club(
        self, auth: AuthContext, payload: Mapping[str, object], request_id: str
    ) -> dict[str, object]:
        self._require_staff(auth)
        club_id = str(self._uuid_factory())
        async with self._engine.begin() as connection:
            media_id = await self._portal_media_id(
                connection,
                auth,
                _read(payload, "imageUrl", "image_url", default=None),
            )
            result = await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("student_club")} (
                      id, tenant_id, name, category, description, contact_name,
                      contact_role, contact_channel, latest_update, next_activity,
                      active, media_asset_id, source_label, source_status,
                      social_links, long_description, membership_open, version,
                      created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :name, :category, :description, :contact_name,
                      :contact_role, :contact_channel, :latest_update, NULL, true,
                      :media_id, 'Staff managed campus life', 'tenant_authored',
                      '[]'::jsonb, :description, :membership_open, 1, NOW(), NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "id": _uuid(club_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "name": str(_read(payload, "name")),
                    "category": str(_read(payload, "category")),
                    "description": str(_read(payload, "description")),
                    "contact_name": str(_read(payload, "contactName", "contact_name")),
                    "contact_role": str(_read(payload, "contactRole", "contact_role")),
                    "contact_channel": str(_read(payload, "contactChannel", "contact_channel")),
                    "latest_update": str(_read(payload, "latestUpdate", "latest_update")),
                    "membership_open": bool(_read(payload, "membershipOpen", "membership_open")),
                    "media_id": media_id,
                },
            )
            result.one()
            await self._record_content_change(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff.club_created",
                event_name="staff.club_created.v1",
                resource_type="student_club",
                resource_id=club_id,
                version=1,
                data={"active": True},
            )
            return await self._staff_club(connection, auth, club_id)

    async def update_club(
        self,
        auth: AuthContext,
        club_id: str,
        payload: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(payload, "expectedVersion", "expected_version"), "expectedVersion"
        )
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("student_club")} SET
                      name=:name, category=:category, description=:description,
                      long_description=:description, contact_name=:contact_name,
                      contact_role=:contact_role, contact_channel=:contact_channel,
                      latest_update=:latest_update, membership_open=:membership_open,
                      source_label='Staff managed campus life',
                      source_status='tenant_authored', version=version+1,
                      updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND version=:expected_version
                    RETURNING id, version
                    """
                ),
                {
                    "id": _uuid(club_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "expected_version": expected_version,
                    "name": str(_read(payload, "name")),
                    "category": str(_read(payload, "category")),
                    "description": str(_read(payload, "description")),
                    "contact_name": str(_read(payload, "contactName", "contact_name")),
                    "contact_role": str(_read(payload, "contactRole", "contact_role")),
                    "contact_channel": str(_read(payload, "contactChannel", "contact_channel")),
                    "latest_update": str(_read(payload, "latestUpdate", "latest_update")),
                    "membership_open": bool(_read(payload, "membershipOpen", "membership_open")),
                },
            )
            row = result.mappings().first()
            if row is None:
                await self._raise_content_write_error(
                    connection,
                    table="student_club",
                    tenant_id=auth.tenant_id,
                    resource_id=club_id,
                    not_found_code="STAFF_CLUB_NOT_FOUND",
                    label="Campus club",
                )
            assert row is not None
            version = int(row["version"])
            await self._record_content_change(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff.club_updated",
                event_name="staff.club_updated.v1",
                resource_type="student_club",
                resource_id=club_id,
                version=version,
                data={"active": True},
            )
            return await self._staff_club(connection, auth, club_id)

    async def _staff_display_name(self, connection: AsyncConnection, auth: AuthContext) -> str:
        result = await connection.execute(
            text(
                f"""
                SELECT display_name FROM {self._table("staff_member")}
                WHERE tenant_id=:tenant_id AND id=:id AND active=true
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id), "id": _uuid(auth.actor_id)},
        )
        name = result.scalar_one_or_none()
        return str(name) if name else "Staff member"

    async def _portal_media_id(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        value: object,
    ) -> object | None:
        image_url = _optional_text(value)
        if image_url is None:
            return None
        result = await connection.execute(
            text(
                f"""
                SELECT id FROM {self._table("media_asset")}
                WHERE tenant_id=:tenant_id AND active=true
                  AND (public_path=:image_url OR :image_url LIKE '%%' || public_path)
                ORDER BY updated_at DESC, id LIMIT 1
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id), "image_url": image_url},
        )
        media_id = result.scalar_one_or_none()
        if media_id is None:
            raise BadRequestError(
                "STAFF_CLUB_IMAGE_NOT_FOUND",
                "Choose an image uploaded to this tenant before publishing the club",
            )
        return cast(object, media_id)

    async def _staff_club(
        self, connection: AsyncConnection, auth: AuthContext, club_id: str
    ) -> dict[str, object]:
        result = await connection.execute(
            text(
                f"""
                SELECT club.id, club.name, club.category, club.description,
                       club.contact_name, club.contact_role, club.contact_channel,
                       club.latest_update, club.next_activity, club.source_label,
                       club.source_url, club.source_status, club.social_links,
                       club.long_description, club.meeting_schedule,
                       club.membership_open, club.version, club.updated_at,
                       COALESCE(media.public_path, '/media/clubs/code-collective.jpg') image_url,
                       COALESCE(
                         media.alt_text, 'Students collaborating in a campus club'
                       ) image_alt,
                       COALESCE(media.attribution, 'Default tenant club image') image_attribution,
                       COALESCE(media.source_url, '') image_source_url
                FROM {self._table("student_club")} club
                LEFT JOIN {self._table("media_asset")} media
                  ON media.id=club.media_asset_id AND media.tenant_id=club.tenant_id
                 AND media.active=true
                WHERE club.id=:id AND club.tenant_id=:tenant_id
                """
            ),
            {"id": _uuid(club_id), "tenant_id": _uuid(auth.tenant_id)},
        )
        row = result.mappings().first()
        if row is None:
            raise NotFoundError("STAFF_CLUB_NOT_FOUND", "The campus club was not found")
        return _map_staff_club(dict(row))

    async def _raise_content_write_error(
        self,
        connection: AsyncConnection,
        *,
        table: str,
        tenant_id: str,
        resource_id: str,
        not_found_code: str,
        label: str,
    ) -> None:
        result = await connection.execute(
            text(f"SELECT version FROM {self._table(table)} WHERE tenant_id=:tenant_id AND id=:id"),
            {"tenant_id": _uuid(tenant_id), "id": _uuid(resource_id)},
        )
        if result.scalar_one_or_none() is None:
            raise NotFoundError(not_found_code, f"The {label.lower()} was not found")
        raise ConflictError("VERSION_CONFLICT", f"The {label.lower()} changed in another session")

    async def _record_content_change(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        request_id: str,
        action: str,
        event_name: str,
        resource_type: str,
        resource_id: str,
        version: int,
        data: Mapping[str, object],
    ) -> None:
        await self._insert_audit(
            connection,
            auth=auth,
            request_id=request_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata={"version": version, **dict(data)},
        )
        await self._insert_outbox(
            connection,
            auth=auth,
            request_id=request_id,
            event_name=event_name,
            aggregate_type=resource_type,
            aggregate_id=resource_id,
            aggregate_version=version,
            data=data,
        )

    async def create_work_item(
        self,
        auth: AuthContext,
        payload: Mapping[str, object],
        idempotency_key: str,
        request_id: str,
    ) -> dict[str, object]:
        """Create a tenant-scoped manual enrollment/onboarding work item."""

        self._require_staff(auth)
        student_id = str(_read(payload, "studentId", "student_id"))
        requirement_value = _read(payload, "requirementId", "requirement_id", default=None)
        requirement_id = str(requirement_value) if requirement_value is not None else None
        flow_kind = str(_read(payload, "flowKind", "flow_kind"))
        title = str(_read(payload, "title")).strip()
        description = str(_read(payload, "description")).strip()
        requested_component = str(_read(payload, "component")).strip()
        assignee_value = _read(payload, "assigneeId", "assignee_id", default=None)
        assignee_id = str(assignee_value) if assignee_value is not None else None
        priority = str(_read(payload, "priority"))
        status = str(_read(payload, "status", default="todo"))
        due_at = _read(payload, "dueAt", "due_at", default=None)
        raw_action_type = _read(payload, "actionType", "action_type", default=None)
        action_type = (
            str(raw_action_type)
            if raw_action_type is not None
            else ("onboarding_assistance" if flow_kind == "onboarding" else "enrollment_follow_up")
        )
        if flow_kind not in {"enrollment", "onboarding"}:
            raise BadRequestError("INVALID_FLOW_KIND", "Choose enrollment or onboarding")
        if priority not in _WORK_ITEM_PRIORITIES:
            raise BadRequestError("INVALID_WORK_ITEM_PRIORITY", "Choose a valid task priority")
        if status not in _WORK_ITEM_STATUSES - _TERMINAL_WORK_ITEM_STATUSES:
            raise BadRequestError("INVALID_WORK_ITEM_STATUS", "Choose a valid initial task status")
        if action_type not in _WORK_ACTION_TYPES:
            raise BadRequestError("INVALID_ACTION_TYPE", "Choose a valid enrollment action type")
        if isinstance(due_at, str):
            due_at = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        if due_at is not None:
            if not isinstance(due_at, datetime) or due_at.tzinfo is None:
                raise BadRequestError("INVALID_DUE_AT", "Task due time must include a timezone")
            if due_at <= self._clock():
                raise BadRequestError("INVALID_DUE_AT", "Task due time must be in the future")

        async def handler(connection: AsyncConnection) -> dict[str, object]:
            student_result = await connection.execute(
                text(
                    f"""
                    SELECT id
                    FROM {self._table("student")}
                    WHERE tenant_id = :tenant_id AND id = :student_id
                    FOR SHARE
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "student_id": _uuid(student_id)},
            )
            if student_result.mappings().first() is None:
                raise BadRequestError(
                    "STAFF_STUDENT_NOT_FOUND",
                    "Choose a student in the current tenant",
                )

            if requirement_id is not None:
                requirement_result = await connection.execute(
                    text(
                        f"""
                        SELECT requirement.id, definition.flow_kind
                        FROM {self._table("student_requirement")} requirement
                        JOIN {self._table("enrollment_journey")} journey
                          ON journey.id = requirement.journey_id
                         AND journey.tenant_id = requirement.tenant_id
                        JOIN {self._table("requirement_definition_version")} definition
                          ON definition.id = requirement.requirement_definition_version_id
                         AND definition.tenant_id = requirement.tenant_id
                        WHERE requirement.tenant_id = :tenant_id
                          AND requirement.id = :requirement_id
                          AND journey.student_id = :student_id
                          AND requirement.retired_at IS NULL
                        FOR SHARE OF requirement
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "requirement_id": _uuid(requirement_id),
                        "student_id": _uuid(student_id),
                    },
                )
                requirement = requirement_result.mappings().first()
                if requirement is None:
                    raise BadRequestError(
                        "STAFF_REQUIREMENT_NOT_FOUND",
                        "Choose an active requirement belonging to this student",
                    )
                if str(requirement["flow_kind"]) != flow_kind:
                    raise BadRequestError(
                        "REQUIREMENT_FLOW_MISMATCH",
                        "The requirement does not belong to the selected flow",
                    )

            component_result = await connection.execute(
                text(
                    f"""
                    SELECT component
                    FROM {self._table("staff_member")}
                    WHERE tenant_id = :tenant_id AND active = true
                      AND lower(component) = lower(:component)
                    ORDER BY component, id
                    LIMIT 1
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "component": requested_component},
            )
            component_row = component_result.mappings().first()
            if component_row is None:
                raise BadRequestError(
                    "STAFF_COMPONENT_NOT_FOUND",
                    "Choose a component with at least one active tenant staff member",
                )
            component = str(component_row["component"])

            assignee: dict[str, object] | None = None
            if assignee_id is not None:
                assignee = await self._active_staff_summary(connection, auth, assignee_id)
                if assignee is None:
                    raise BadRequestError(
                        "STAFF_ASSIGNEE_NOT_FOUND",
                        "Choose an active staff assignee in the current tenant",
                    )
                if str(assignee["component"]).casefold() != component.casefold():
                    raise BadRequestError(
                        "STAFF_ASSIGNEE_COMPONENT_MISMATCH",
                        "The assignee must belong to the selected component",
                    )

            work_item_id = str(self._uuid_factory())
            key = f"MAN-{work_item_id.replace('-', '')[:8].upper()}"
            now = self._clock()
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("staff_work_item")} (
                      id, tenant_id, student_id, key, title, description,
                      status, priority, work_type, component, due_at, escalated,
                      assignee_id, source_type, source_id, version, action_type,
                      started_at, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :student_id, :key, :title, :description,
                      CAST(:status AS varchar), :priority, 'enrollment', :component,
                      :due_at, false,
                      :assignee_id, NULL, NULL, 1, :action_type,
                      CASE WHEN CAST(:status AS varchar) = 'in_progress'
                        THEN CAST(:now AS timestamptz) ELSE NULL::timestamptz END,
                      CAST(:now AS timestamptz), CAST(:now AS timestamptz)
                    )
                    """
                ),
                {
                    "id": _uuid(work_item_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                    "key": key,
                    "title": title,
                    "description": description,
                    "status": status,
                    "priority": priority,
                    "component": component,
                    "due_at": due_at,
                    "assignee_id": _uuid(assignee_id) if assignee_id is not None else None,
                    "action_type": action_type,
                    "now": now,
                },
            )
            if requirement_id is not None:
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("staff_work_item_link")} (
                          id, tenant_id, work_item_id, entity_type, entity_id,
                          relationship, created_at
                        ) VALUES (
                          :id, :tenant_id, :work_item_id, 'requirement',
                          :requirement_id, 'manual_scope', :now
                        )
                        ON CONFLICT (tenant_id, work_item_id, entity_type, entity_id)
                        DO NOTHING
                        """
                    ),
                    {
                        "id": self._uuid_factory(),
                        "tenant_id": _uuid(auth.tenant_id),
                        "work_item_id": _uuid(work_item_id),
                        "requirement_id": _uuid(requirement_id),
                        "now": now,
                    },
                )
            actor_name = await self._staff_name(connection, auth, auth.actor_id)
            await self._insert_work_log(
                connection,
                auth=auth,
                work_item_id=work_item_id,
                actor_name=actor_name,
                action="created",
                message=f"Manual {flow_kind} task created by {actor_name}.",
            )
            await self._queue_task_insight_refresh(
                connection,
                auth=auth,
                work_item_id=work_item_id,
                student_id=student_id,
                source_version=1,
                not_before=now,
            )
            notification_id = await self._insert_staff_notification(
                connection,
                auth=auth,
                work_item_id=work_item_id,
                staff_member_id=assignee_id,
                team_component=None if assignee_id is not None else component,
                tenant_wide=False,
                kind="work_item_created",
                title="New enrollment task",
                body=f"{key}: {title}",
                dedupe_key=f"work-item:{work_item_id}:attention",
            )
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff_work_item.created",
                resource_type="staff_work_item",
                resource_id=work_item_id,
                metadata={
                    "studentId": student_id,
                    "requirementId": requirement_id,
                    "flowKind": flow_kind,
                    "priority": priority,
                    "status": status,
                    "component": component,
                    "assigneeId": assignee_id,
                },
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="staff.work_item_created.v1",
                aggregate_type="staff_work_item",
                aggregate_id=work_item_id,
                aggregate_version=1,
                data={
                    "workItemId": work_item_id,
                    "studentId": student_id,
                    "requirementId": requirement_id,
                    "notificationId": notification_id,
                    "flowKind": flow_kind,
                    "priority": priority,
                    "status": status,
                },
            )
            item_result = await connection.execute(
                text(
                    self._board_items_sql(
                        where="item.tenant_id = :tenant_id AND item.id = :work_item_id",
                        order="item.id",
                        limit=False,
                    )
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "work_item_id": _uuid(work_item_id),
                    "viewer_id": _uuid(auth.actor_id),
                    "now": now,
                },
            )
            row = item_result.mappings().first()
            if row is None:
                raise RuntimeError("The newly created work item could not be reloaded")
            return self._map_work_item(dict(row), [], now=now)

        return await self._run_idempotent(
            auth=auth,
            idempotency_key=idempotency_key,
            operation="staff.work_item.create",
            request_payload=payload,
            response_status=201,
            handler=handler,
        )

    async def get_realtime_events(
        self,
        auth: AuthContext,
        after_cursor: int | None,
        limit: int = 100,
    ) -> dict[str, object]:
        """Read a bounded, lossless staff event replay window."""

        self._require_staff(auth)
        bounded_limit = max(1, min(limit, 100))
        async with self._engine.connect() as connection:
            viewer = await self._active_staff_summary(connection, auth, auth.actor_id)
            if viewer is None:
                raise NotFoundError(
                    "STAFF_MEMBER_NOT_FOUND",
                    "The active staff member was not found in this tenant",
                )
            if after_cursor is None:
                current_result = await connection.execute(
                    text(
                        f"""
                        SELECT COALESCE(MAX(cursor), 0) AS cursor
                        FROM {self._table("staff_realtime_event")}
                        WHERE tenant_id = :tenant_id
                          AND (
                            tenant_wide = true
                            OR staff_member_id = :staff_member_id
                            OR (
                              staff_member_id IS NULL
                              AND team_component = :team_component
                            )
                          )
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "staff_member_id": _uuid(auth.actor_id),
                        "team_component": str(viewer["component"]),
                    },
                )
                current = current_result.mappings().first()
                return {
                    "events": [],
                    "cursor": int(current["cursor"]) if current is not None else 0,
                }
            if after_cursor < 0:
                raise BadRequestError(
                    "INVALID_EVENT_CURSOR",
                    "The realtime event cursor must be non-negative",
                )
            result = await connection.execute(
                text(
                    f"""
                    SELECT cursor, event_type, resource_type, resource_id,
                           work_item_id, payload, created_at
                    FROM {self._table("staff_realtime_event")}
                    WHERE tenant_id = :tenant_id
                      AND cursor > :after_cursor
                      AND (
                        tenant_wide = true
                        OR staff_member_id = :staff_member_id
                        OR (
                          staff_member_id IS NULL
                          AND team_component = :team_component
                        )
                      )
                    ORDER BY cursor
                    LIMIT :limit
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "after_cursor": after_cursor,
                    "staff_member_id": _uuid(auth.actor_id),
                    "team_component": str(viewer["component"]),
                    "limit": bounded_limit,
                },
            )
            rows = [dict(row) for row in result.mappings().all()]
        events = [
            {
                "cursor": int(row["cursor"]),
                "type": str(row["event_type"]),
                "resourceType": str(row["resource_type"]),
                "resourceId": str(row["resource_id"]),
                "workItemId": (
                    str(row["work_item_id"]) if row["work_item_id"] is not None else None
                ),
                "data": dict(row["payload"]) if isinstance(row["payload"], Mapping) else {},
                "occurredAt": _iso_timestamp(row["created_at"]),
            }
            for row in rows
        ]
        next_cursor = after_cursor
        if events:
            next_cursor = _database_integer(events[-1]["cursor"], "staff_realtime_event.cursor")
        return {"events": events, "cursor": next_cursor}

    async def get_student_record(
        self,
        auth: AuthContext,
        student_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        student_auth = replace(auth, student_id=student_id)
        onboarding_task = self._reader.get_student_onboarding(student_auth)
        profile_task = self._reader.get_student_profile(student_auth)
        requirements_task = self._reader.get_student_requirements(student_auth)
        documents_task = self._reader.get_student_documents(student_auth)
        summary_task = self._student_summary(auth.tenant_id, student_id)
        onboarding, profile, requirements, documents, summary = await asyncio.gather(
            onboarding_task,
            profile_task,
            requirements_task,
            documents_task,
            summary_task,
        )
        if summary is None:
            raise NotFoundError("STAFF_STUDENT_NOT_FOUND", "The student was not found")
        return {
            "student": summary,
            "onboarding": dict(onboarding),
            "profile": dict(profile),
            "requirements": dict(requirements),
            "documents": dict(documents),
        }

    async def get_student_roster(self, auth: AuthContext) -> list[dict[str, object]]:
        """Return canonical students for the CRM, including accounts without work rows."""

        self._require_staff(auth)
        async with self._engine.connect() as connection:
            rows = await self._roster_rows(
                connection, auth, query=None, student_id=None, limit=ROSTER_WORKSPACE_LIMIT
            )
        return [self._map_student_operation(dict(row), auth.actor_id) for row in rows]

    async def search_students(
        self,
        auth: AuthContext,
        *,
        query: str | None,
        student_id: str | None,
        limit: int,
    ) -> dict[str, object]:
        """One bounded page of the tenant roster, matched server-side.

        The same projection the workspace cohort uses, so a search result and
        a cohort row never disagree about a student.
        """

        self._require_staff(auth)
        normalized_query = " ".join((query or "").split())[:ROSTER_SEARCH_MAX_LENGTH]
        bounded_limit = max(1, min(int(limit), ROSTER_SEARCH_MAX_LIMIT))
        async with self._engine.connect() as connection:
            rows = await self._roster_rows(
                connection,
                auth,
                query=normalized_query or None,
                student_id=student_id,
                limit=bounded_limit,
            )
            cohort_result = await connection.execute(
                text(
                    f"SELECT COUNT(*) AS total FROM {self._table('student')} "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            cohort_total = int(cohort_result.mappings().one()["total"])
        items = [self._map_student_operation(dict(row), auth.actor_id) for row in rows]
        total = _database_integer(rows[0]["match_count"], "student.match_count") if rows else 0
        return {
            "items": items,
            "total": total,
            "cohortTotal": cohort_total,
            "query": normalized_query,
            "limit": bounded_limit,
            "generatedAt": _iso_timestamp(self._clock()),
        }

    async def _roster_rows(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        *,
        query: str | None,
        student_id: str | None,
        limit: int,
    ) -> list[Mapping[str, object]]:
        params: dict[str, object] = {
            "tenant_id": _uuid(auth.tenant_id),
            "viewer_id": _uuid(auth.actor_id),
            "now": self._clock(),
            "limit": limit,
        }
        if student_id is not None:
            try:
                params["student_id"] = UUID(student_id)
            except ValueError:
                return []
        if query is not None:
            params["pattern"] = f"%{_escape_like(query)}%"
        result = await connection.execute(
            text(
                self._student_roster_sql(
                    by_student=student_id is not None, by_query=query is not None
                )
            ),
            params,
        )
        return [dict(row) for row in result.mappings().all()]

    async def get_student_summary(
        self, auth: AuthContext, student_id: str
    ) -> dict[str, object] | None:
        """Resolve a student only inside the authenticated staff tenant."""

        self._require_staff(auth)
        return await self._student_summary(auth.tenant_id, student_id)

    async def get_work_item_detail(
        self,
        auth: AuthContext,
        work_item_id: str,
        *,
        ensure_document_work_items: bool = True,
    ) -> dict[str, object]:
        self._require_staff(auth)
        if ensure_document_work_items:
            await self._ensure_document_work_items(auth)
        # One item, its own history: never the whole board. At a realistic
        # tenant the board read alone exceeds the assistant's tool budget,
        # and the detail view needs exactly one row of it.
        work_item = await self._read_work_item(auth, work_item_id)
        if work_item is None:
            raise NotFoundError("STAFF_WORK_ITEM_NOT_FOUND", "The work item was not found")
        student_id = str(cast(Mapping[str, object], work_item["student"])["id"])

        async with self._engine.connect() as connection:
            comment_result = await connection.execute(
                text(
                    f"""
                    SELECT comment.id, comment.body, comment.mentions,
                           comment.created_at, member.id AS author_id,
                           member.display_name AS author_name,
                           member.email_normalized AS author_email,
                           member.component AS author_component
                    FROM {self._table("staff_work_comment")} comment
                    JOIN {self._table("staff_member")} member
                      ON member.tenant_id = comment.tenant_id
                     AND member.id = comment.author_id
                    WHERE comment.tenant_id = :tenant_id
                      AND comment.work_item_id = :work_item_id
                    ORDER BY comment.created_at, comment.id
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "work_item_id": _uuid(work_item_id)},
            )
            interaction_result = await connection.execute(
                text(
                    f"""
                    SELECT id, objective, status, selected_channel, source_version,
                           covered_source_version, version, quiet_until,
                           last_activity_at, completed_at, created_at, updated_at
                    FROM {self._table("staff_interaction")}
                    WHERE tenant_id = :tenant_id AND work_item_id = :work_item_id
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "work_item_id": _uuid(work_item_id)},
            )
            communication_result = await connection.execute(
                text(
                    f"""
                    SELECT communication.id, communication.interaction_id,
                           communication.channel, communication.direction,
                           communication.subject, communication.body_excerpt,
                           communication.delivery_status,
                           communication.source_sequence, communication.occurred_at
                    FROM {self._table("communication_event")} communication
                    JOIN {self._table("staff_interaction")} interaction
                      ON interaction.tenant_id = communication.tenant_id
                     AND interaction.id = communication.interaction_id
                    WHERE communication.tenant_id = :tenant_id
                      AND interaction.work_item_id = :work_item_id
                    ORDER BY communication.occurred_at, communication.id
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "work_item_id": _uuid(work_item_id)},
            )
            recording_result = await connection.execute(
                text(
                    f"""
                    SELECT recording.id, recording.interaction_id,
                           recording.file_name, recording.mime_type,
                           recording.size_bytes, recording.sha256,
                           recording.status, recording.attempts,
                           recording.version, recording.last_error_message,
                           recording.uploaded_at, recording.transcribed_at,
                           recording.created_at, recording.updated_at
                    FROM {self._table("staff_call_recording")} recording
                    WHERE recording.tenant_id = :tenant_id
                      AND recording.work_item_id = :work_item_id
                    ORDER BY recording.created_at DESC, recording.id DESC
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "work_item_id": _uuid(work_item_id)},
            )
            transcript_result = await connection.execute(
                text(
                    f"""
                    SELECT transcript.id, transcript.recording_id,
                           transcript.version, transcript.transcript,
                           transcript.language, transcript.duration_seconds,
                           transcript.segments, transcript.provider,
                           transcript.model, transcript.is_current,
                           transcript.generated_at
                    FROM {self._table("staff_call_transcript_revision")} transcript
                    JOIN {self._table("staff_call_recording")} recording
                      ON recording.tenant_id = transcript.tenant_id
                     AND recording.id = transcript.recording_id
                    WHERE transcript.tenant_id = :tenant_id
                      AND recording.work_item_id = :work_item_id
                    ORDER BY transcript.recording_id, transcript.version DESC
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "work_item_id": _uuid(work_item_id)},
            )
            outcome_result = await connection.execute(
                text(
                    f"""
                    SELECT id, interaction_id, version, finality, summary,
                           channel_results, conversation_signals,
                           outcome_code, resolution_code,
                           next_step, follow_up_required, source_ids,
                           covered_source_version, confidence_milli,
                           provider, model, generated_at
                    FROM {self._table("interaction_outcome_revision")}
                    WHERE tenant_id = :tenant_id
                      AND work_item_id = :work_item_id
                      AND is_current = true
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "work_item_id": _uuid(work_item_id)},
            )
            task_insight_result = await connection.execute(
                text(
                    f"""
                    SELECT version, summary, why_this_matters, objective,
                           success_definition, suggested_approach,
                           suggested_channel, source_ids, source_revision,
                           provider, model, generated_at
                    FROM {self._table("task_insight_revision")}
                    WHERE tenant_id = :tenant_id AND work_item_id = :work_item_id
                      AND is_current = true
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "work_item_id": _uuid(work_item_id)},
            )
            summary_result = await connection.execute(
                text(
                    f"""
                    SELECT version, summary, key_facts, risks, next_steps,
                           source_ids, source_revision, provider, model, generated_at
                    FROM {self._table("student_summary_revision")}
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                      AND is_current = true
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "student_id": _uuid(student_id)},
            )
            document_result = await connection.execute(
                text(
                    f"""
                    SELECT id, file_name, mime_type, size_bytes, category,
                           processing_mode, status, extraction, created_at
                    FROM {self._table("document_record")}
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "student_id": _uuid(student_id)},
            )
            await connection.execute(
                text("SELECT set_config('audentra.tenant_id', :tenant, true)"),
                {"tenant": auth.tenant_id},
            )
            decisions = await connection.execute(
                text("""
                SELECT id,document_id,decision,reason_code,reason_label,student_message,
                  reviewer_display_name,source,decided_at
                FROM public.document_review_decision WHERE tenant_id=:tenant AND student_id=:student
                ORDER BY decided_at,id
            """),
                {"tenant": _uuid(auth.tenant_id), "student": _uuid(student_id)},
            )
            review_history: dict[str, list[dict[str, Any]]] = {}
            for row in decisions.mappings():
                review_history.setdefault(str(row["document_id"]), []).append(
                    public_review_decision(dict(row))
                )
            job_result = await connection.execute(
                text(
                    f"""
                    SELECT purpose, interaction_id, status,
                           requested_source_version, covered_source_version,
                           updated_at
                    FROM {self._table("action_center_ai_job")}
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                      AND (work_item_id = :work_item_id OR purpose = 'student_summary')
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                    "work_item_id": _uuid(work_item_id),
                },
            )

        async with self._engine.connect() as connection:
            staff_result = await connection.execute(
                text(self._board_staff_sql()),
                {"tenant_id": _uuid(auth.tenant_id), "now": self._clock()},
            )
            staff_by_id = {
                str(row["id"]): self._map_board_member(dict(row))
                for row in staff_result.mappings().all()
            }
            related_result = await connection.execute(
                text(
                    self._board_items_sql(
                        where=(
                            "item.tenant_id = :tenant_id AND item.student_id = :student_id"
                            " AND item.id <> :work_item_id"
                        ),
                        order=self._board_order_sql("priority"),
                        limit=True,
                    )
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                    "work_item_id": _uuid(work_item_id),
                    "viewer_id": _uuid(auth.actor_id),
                    "now": self._clock(),
                    "limit": 20,
                    "offset": 0,
                },
            )
            related_items = [
                self._map_work_item(dict(row), []) for row in related_result.mappings().all()
            ]
        comments = []
        for row in comment_result.mappings().all():
            mention_ids = [str(value) for value in _json_list(row["mentions"], "comment.mentions")]
            comments.append(
                {
                    "id": str(row["id"]),
                    "body": str(row["body"]),
                    "author": {
                        "id": str(row["author_id"]),
                        "name": str(row["author_name"]),
                        "email": str(row["author_email"]),
                        "component": str(row["author_component"]),
                    },
                    "mentions": [staff_by_id[item] for item in mention_ids if item in staff_by_id],
                    "createdAt": _iso_timestamp(row["created_at"]),
                }
            )

        communications_by_interaction: dict[str, list[dict[str, object]]] = {}
        for row in communication_result.mappings().all():
            interaction_id = str(row["interaction_id"])
            communications_by_interaction.setdefault(interaction_id, []).append(
                {
                    "id": str(row["id"]),
                    "channel": str(row["channel"]),
                    "direction": str(row["direction"]),
                    "subject": str(row["subject"]) if row["subject"] is not None else None,
                    "body": (str(row["body_excerpt"]) if row["body_excerpt"] is not None else None),
                    "deliveryStatus": str(row["delivery_status"]),
                    "sourceSequence": int(row["source_sequence"] or 0),
                    "occurredAt": _iso_timestamp(row["occurred_at"]),
                }
            )

        transcripts_by_recording: dict[str, list[dict[str, object]]] = {}
        for row in transcript_result.mappings().all():
            recording_id = str(row["recording_id"])
            segments = []
            for value in _json_list(row["segments"], "call_transcript.segments"):
                if not isinstance(value, Mapping):
                    continue
                segments.append(
                    {
                        "start": _optional_float(value.get("start")),
                        "end": _optional_float(value.get("end")),
                        "text": str(value.get("text") or ""),
                    }
                )
            transcripts_by_recording.setdefault(recording_id, []).append(
                {
                    "id": str(row["id"]),
                    "version": int(row["version"]),
                    "transcript": str(row["transcript"]),
                    "language": str(row["language"]) if row["language"] is not None else None,
                    "durationSeconds": (
                        float(row["duration_seconds"])
                        if row["duration_seconds"] is not None
                        else None
                    ),
                    "segments": segments,
                    "provider": str(row["provider"]),
                    "model": str(row["model"]),
                    "generatedAt": _iso_timestamp(row["generated_at"]),
                    "isCurrent": bool(row["is_current"]),
                }
            )

        recordings_by_interaction: dict[str, list[dict[str, object]]] = {}
        for row in recording_result.mappings().all():
            recording_id = str(row["id"])
            transcript_history = transcripts_by_recording.get(recording_id, [])
            current_transcript = next(
                (value for value in transcript_history if value.pop("isCurrent", False)),
                None,
            )
            for value in transcript_history:
                value.pop("isCurrent", None)
            interaction_id = str(row["interaction_id"])
            recordings_by_interaction.setdefault(interaction_id, []).append(
                {
                    "id": recording_id,
                    "interactionId": interaction_id,
                    "fileName": str(row["file_name"]),
                    "mimeType": str(row["mime_type"]),
                    "sizeBytes": int(row["size_bytes"]),
                    "sha256": str(row["sha256"]),
                    "status": str(row["status"]),
                    "attempts": int(row["attempts"]),
                    "version": int(row["version"]),
                    "lastError": (
                        str(row["last_error_message"])
                        if row["last_error_message"] is not None
                        else None
                    ),
                    "uploadedAt": (
                        _iso_timestamp(row["uploaded_at"])
                        if row["uploaded_at"] is not None
                        else None
                    ),
                    "transcribedAt": (
                        _iso_timestamp(row["transcribed_at"])
                        if row["transcribed_at"] is not None
                        else None
                    ),
                    "downloadUrl": f"/v1/staff/call-recordings/{recording_id}/content",
                    "currentTranscript": current_transcript,
                    "transcriptHistory": transcript_history,
                    "createdAt": _iso_timestamp(row["created_at"]),
                    "updatedAt": _iso_timestamp(row["updated_at"]),
                }
            )

        outcomes: dict[str, dict[str, object]] = {}
        for row in outcome_result.mappings().all():
            outcomes[str(row["interaction_id"])] = {
                "id": str(row["id"]),
                "version": int(row["version"]),
                "finality": str(row["finality"]),
                "summary": str(row["summary"]),
                "channelResults": _json_list(row["channel_results"], "outcome.channel_results"),
                "conversationSignals": _map_conversation_signals(row["conversation_signals"]),
                "outcomeCode": (
                    str(row["outcome_code"]) if row["outcome_code"] is not None else None
                ),
                "resolutionCode": (
                    str(row["resolution_code"]) if row["resolution_code"] is not None else None
                ),
                "nextStep": str(row["next_step"]) if row["next_step"] is not None else None,
                "followUpRequired": bool(row["follow_up_required"]),
                "sourceIds": [
                    str(value) for value in _json_list(row["source_ids"], "outcome.source_ids")
                ],
                "coveredSourceVersion": int(row["covered_source_version"]),
                "confidence": (
                    int(row["confidence_milli"]) / 1000
                    if row["confidence_milli"] is not None
                    else None
                ),
                "provider": str(row["provider"]),
                "model": str(row["model"]),
                "generatedAt": _iso_timestamp(row["generated_at"]),
            }

        jobs = [dict(row) for row in job_result.mappings().all()]
        interaction_job_states = {
            str(row["interaction_id"]): _job_public_state(row)
            for row in jobs
            if row.get("purpose") == "interaction_enrichment"
            and row.get("interaction_id") is not None
        }
        interactions: list[dict[str, object]] = []
        for row in interaction_result.mappings().all():
            interaction_id = str(row["id"])
            interactions.append(
                {
                    "id": interaction_id,
                    "objective": str(row["objective"]),
                    "status": str(row["status"]),
                    "selectedChannel": (
                        str(row["selected_channel"])
                        if row["selected_channel"] is not None
                        else None
                    ),
                    "sourceVersion": int(row["source_version"]),
                    "coveredSourceVersion": int(row["covered_source_version"]),
                    "version": int(row["version"]),
                    "quietUntil": (
                        _iso_timestamp(row["quiet_until"])
                        if row["quiet_until"] is not None
                        else None
                    ),
                    "lastActivityAt": (
                        _iso_timestamp(row["last_activity_at"])
                        if row["last_activity_at"] is not None
                        else None
                    ),
                    "completedAt": (
                        _iso_timestamp(row["completed_at"])
                        if row["completed_at"] is not None
                        else None
                    ),
                    "communications": communications_by_interaction.get(interaction_id, []),
                    "recordings": recordings_by_interaction.get(interaction_id, []),
                    "outcome": outcomes.get(interaction_id),
                    "aiState": interaction_job_states.get(interaction_id, "not_requested"),
                }
            )

        summary_row = summary_result.mappings().first()
        task_insight_row = task_insight_result.mappings().first()
        task_insight_job = next(
            (row for row in jobs if row.get("purpose") == "task_insight"),
            None,
        )
        task_insight_state = _summary_public_state(
            dict(task_insight_row) if task_insight_row is not None else None,
            task_insight_job,
        )
        task_insight = {
            "state": task_insight_state,
            "version": (int(task_insight_row["version"]) if task_insight_row is not None else None),
            "summary": (str(task_insight_row["summary"]) if task_insight_row is not None else None),
            "whyThisMatters": (
                str(task_insight_row["why_this_matters"]) if task_insight_row is not None else None
            ),
            "objective": (
                str(task_insight_row["objective"]) if task_insight_row is not None else None
            ),
            "successDefinition": (
                str(task_insight_row["success_definition"])
                if task_insight_row is not None
                else None
            ),
            "suggestedApproach": (
                str(task_insight_row["suggested_approach"])
                if task_insight_row is not None
                else None
            ),
            "suggestedChannel": (
                str(task_insight_row["suggested_channel"])
                if task_insight_row is not None
                and task_insight_row["suggested_channel"] is not None
                else None
            ),
            "sourceIds": (
                [
                    str(value)
                    for value in _json_list(
                        task_insight_row["source_ids"], "task_insight.source_ids"
                    )
                ]
                if task_insight_row is not None
                else []
            ),
            "sourceRevision": (
                int(task_insight_row["source_revision"]) if task_insight_row is not None else 0
            ),
            "provider": (
                str(task_insight_row["provider"]) if task_insight_row is not None else None
            ),
            "model": (str(task_insight_row["model"]) if task_insight_row is not None else None),
            "generatedAt": (
                _iso_timestamp(task_insight_row["generated_at"])
                if task_insight_row is not None
                else None
            ),
        }
        summary_job = next(
            (row for row in jobs if row.get("purpose") == "student_summary"),
            None,
        )
        summary_state = _summary_public_state(
            dict(summary_row) if summary_row is not None else None,
            summary_job,
        )
        student_summary = {
            "state": summary_state,
            "version": int(summary_row["version"]) if summary_row is not None else None,
            "summary": str(summary_row["summary"]) if summary_row is not None else None,
            "keyFacts": (
                [str(value) for value in _json_list(summary_row["key_facts"], "summary.key_facts")]
                if summary_row is not None
                else []
            ),
            "risks": (
                [str(value) for value in _json_list(summary_row["risks"], "summary.risks")]
                if summary_row is not None
                else []
            ),
            "nextSteps": (
                [
                    str(value)
                    for value in _json_list(summary_row["next_steps"], "summary.next_steps")
                ]
                if summary_row is not None
                else []
            ),
            "sourceIds": (
                [
                    str(value)
                    for value in _json_list(summary_row["source_ids"], "summary.source_ids")
                ]
                if summary_row is not None
                else []
            ),
            "sourceRevision": int(summary_row["source_revision"]) if summary_row else 0,
            "provider": str(summary_row["provider"]) if summary_row is not None else None,
            "model": str(summary_row["model"]) if summary_row is not None else None,
            "generatedAt": (
                _iso_timestamp(summary_row["generated_at"]) if summary_row is not None else None
            ),
        }
        return {
            "workItem": work_item,
            "taskInsight": task_insight,
            "studentSummary": student_summary,
            "interactions": interactions,
            "comments": comments,
            "relatedItems": related_items,
            "relatedDocuments": [
                {
                    "id": str(row["id"]),
                    "fileName": str(row["file_name"]),
                    "mimeType": str(row["mime_type"]),
                    "sizeBytes": int(row["size_bytes"]),
                    "category": str(row["category"]),
                    "processingMode": str(row["processing_mode"]),
                    "status": str(row["status"]),
                    "contentUrl": f"/v1/staff/documents/{row['id']}/content",
                    "createdAt": _iso_timestamp(row["created_at"]),
                    **(
                        {"extraction": _json_object(row["extraction"], "document.extraction")}
                        if row.get("extraction")
                        else {}
                    ),
                    "reviewHistory": review_history.get(str(row["id"]), []),
                }
                for row in document_result.mappings().all()
            ],
            "aiState": _aggregate_ai_states(
                [task_insight_state, summary_state, *interaction_job_states.values()]
            ),
            "generatedAt": _iso_timestamp(self._clock()),
        }

    async def get_document_content_reference(
        self,
        auth: AuthContext,
        document_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT storage_key, file_name, mime_type
                    FROM {self._table("document_record")}
                    WHERE tenant_id = :tenant_id AND id = :document_id
                      AND storage_key IS NOT NULL
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "document_id": _uuid(document_id),
                },
            )
            row = result.mappings().first()
        if row is None:
            raise NotFoundError(
                "STAFF_DOCUMENT_CONTENT_NOT_FOUND",
                "The uploaded document content was not found",
            )
        return {
            "storageKey": str(row["storage_key"]),
            "fileName": str(row["file_name"]),
            "mimeType": str(row["mime_type"]),
        }

    async def get_action_rules(self, auth: AuthContext) -> dict[str, object]:
        self._require_staff(auth)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT rule.id, rule.code, rule.name, rule.description,
                           rule.enabled, rule.signal_type, rule.flow_kind,
                           rule.requirement_code, rule.lookahead_days,
                           rule.inactivity_days, rule.cadence_minutes,
                           rule.component, rule.priority, rule.action_type,
                           rule.title_template, rule.description_template,
                           rule.version, rule.last_evaluated_at, rule.updated_at,
                           member.id AS updated_by_id,
                           member.display_name AS updated_by_name,
                           member.email_normalized AS updated_by_email,
                           member.component AS updated_by_component
                    FROM {self._table("staff_action_rule")} rule
                    LEFT JOIN {self._table("staff_member")} member
                      ON member.tenant_id = rule.tenant_id
                     AND member.id = rule.updated_by
                    WHERE rule.tenant_id = :tenant_id
                    ORDER BY rule.enabled DESC, rule.name, rule.id
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            rows = result.mappings().all()
        return {
            "items": [_map_action_rule(dict(row)) for row in rows],
            "generatedAt": _iso_timestamp(self._clock()),
        }

    async def get_notifications(self, auth: AuthContext) -> dict[str, object]:
        self._require_staff(auth)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    WITH accessible AS (
                      SELECT notification.id, notification.kind,
                             notification.title, notification.body,
                             notification.resource_type, notification.resource_id,
                             notification.staff_member_id,
                             notification.team_component,
                             notification.tenant_wide,
                             notification.created_at,
                             receipt.read_at,
                             item.key AS work_item_key
                      FROM {self._table("staff_notification")} notification
                      JOIN {self._table("staff_member")} viewer
                        ON viewer.tenant_id = notification.tenant_id
                       AND viewer.id = :staff_member_id
                       AND viewer.active = true
                      LEFT JOIN {self._table("staff_notification_read_receipt")} receipt
                        ON receipt.tenant_id = notification.tenant_id
                       AND receipt.notification_id = notification.id
                       AND receipt.staff_member_id = viewer.id
                      LEFT JOIN {self._table("staff_work_item")} item
                        ON item.tenant_id = notification.tenant_id
                       AND notification.resource_type = 'staff_work_item'
                       AND item.id = notification.resource_id
                      WHERE notification.tenant_id = :tenant_id
                        AND (
                          notification.tenant_wide = true
                          OR
                          notification.staff_member_id = viewer.id
                          OR (
                            notification.staff_member_id IS NULL
                            AND notification.team_component = viewer.component
                          )
                        )
                    )
                    SELECT accessible.*,
                           COUNT(*) FILTER (WHERE read_at IS NULL) OVER () AS unread_count
                    FROM accessible
                    ORDER BY created_at DESC, id DESC
                    LIMIT 30
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "staff_member_id": _uuid(auth.actor_id),
                },
            )
            rows = [dict(row) for row in result.mappings().all()]
        return {
            "items": [
                {
                    "id": str(row["id"]),
                    "kind": str(row["kind"]),
                    "title": str(row["title"]),
                    "body": str(row["body"]),
                    "resourceType": (
                        str(row["resource_type"]) if row["resource_type"] is not None else None
                    ),
                    "resourceId": (
                        str(row["resource_id"]) if row["resource_id"] is not None else None
                    ),
                    "workItemKey": (
                        str(row["work_item_key"]) if row["work_item_key"] is not None else None
                    ),
                    "target": (
                        "tenant"
                        if bool(row.get("tenant_wide", False))
                        else ("staff" if row["staff_member_id"] is not None else "team")
                    ),
                    "isRead": row["read_at"] is not None,
                    "readAt": (
                        _iso_timestamp(row["read_at"]) if row["read_at"] is not None else None
                    ),
                    "createdAt": _iso_timestamp(row["created_at"]),
                }
                for row in rows
            ],
            "unreadCount": (
                _database_integer(rows[0]["unread_count"], "staff_notification.unread_count")
                if rows
                else 0
            ),
            "generatedAt": _iso_timestamp(self._clock()),
        }

    async def mark_notification_read(
        self,
        auth: AuthContext,
        notification_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        async with self._engine.begin() as connection:
            accessible = await connection.execute(
                text(
                    f"""
                    SELECT notification.id
                    FROM {self._table("staff_notification")} notification
                    JOIN {self._table("staff_member")} viewer
                      ON viewer.tenant_id = notification.tenant_id
                     AND viewer.id = :staff_member_id
                     AND viewer.active = true
                    WHERE notification.tenant_id = :tenant_id
                      AND notification.id = :notification_id
                      AND (
                        notification.tenant_wide = true
                        OR
                        notification.staff_member_id = viewer.id
                        OR (
                          notification.staff_member_id IS NULL
                          AND notification.team_component = viewer.component
                        )
                      )
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "staff_member_id": _uuid(auth.actor_id),
                    "notification_id": _uuid(notification_id),
                },
            )
            if accessible.mappings().first() is None:
                raise NotFoundError(
                    "STAFF_NOTIFICATION_NOT_FOUND",
                    "The staff notification was not found",
                )
            receipt = await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("staff_notification_read_receipt")} AS receipt (
                      tenant_id, notification_id, staff_member_id, read_at
                    ) VALUES (
                      :tenant_id, :notification_id, :staff_member_id, NOW()
                    )
                    ON CONFLICT (tenant_id, notification_id, staff_member_id)
                    DO UPDATE SET read_at = receipt.read_at
                    RETURNING read_at
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "staff_member_id": _uuid(auth.actor_id),
                    "notification_id": _uuid(notification_id),
                },
            )
            read_at = receipt.scalar_one()
        return {
            "id": notification_id,
            "isRead": True,
            "readAt": _iso_timestamp(read_at),
        }

    async def _get_action_rule(
        self,
        auth: AuthContext,
        rule_id: str,
    ) -> dict[str, object]:
        rules = await self.get_action_rules(auth)
        for rule in cast(list[dict[str, object]], rules["items"]):
            if rule["id"] == rule_id:
                return rule
        raise NotFoundError("STAFF_ACTION_RULE_NOT_FOUND", "The action rule was not found")

    async def create_action_rule(
        self,
        auth: AuthContext,
        payload: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        values = _validated_action_rule_values(payload)
        rule_id = str(self._uuid_factory())
        async with self._engine.begin() as connection:
            duplicate = await connection.execute(
                text(
                    f"""
                    SELECT 1 FROM {self._table("staff_action_rule")}
                    WHERE tenant_id = :tenant_id AND code = :code
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "code": values["code"]},
            )
            if duplicate.first() is not None:
                raise ConflictError(
                    "ACTION_RULE_CODE_EXISTS",
                    "An action rule already uses this code",
                )
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("staff_action_rule")} (
                      id, tenant_id, code, name, description, enabled,
                      signal_type, flow_kind, requirement_code, lookahead_days,
                      inactivity_days, cadence_minutes, component, priority,
                      action_type, title_template, description_template,
                      version, created_by, updated_by, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :code, :name, :description, :enabled,
                      :signal_type, :flow_kind, :requirement_code,
                      :lookahead_days, :inactivity_days, :cadence_minutes,
                      :component, :priority, :action_type, :title_template,
                      :description_template, 1, :actor_id, :actor_id, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": _uuid(rule_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "actor_id": _uuid(auth.actor_id),
                    **values,
                },
            )
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff_action_rule.created",
                resource_type="staff_action_rule",
                resource_id=rule_id,
                metadata={"code": values["code"], "signalType": values["signal_type"]},
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="staff.action_rule_created.v1",
                aggregate_type="staff_action_rule",
                aggregate_id=rule_id,
                aggregate_version=1,
                data={"ruleId": rule_id, "code": values["code"]},
            )
        return await self._get_action_rule(auth, rule_id)

    async def update_action_rule(
        self,
        auth: AuthContext,
        rule_id: str,
        payload: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(payload, "expectedVersion", "expected_version"),
            "expectedVersion",
        )
        async with self._engine.begin() as connection:
            current_result = await connection.execute(
                text(
                    f"""
                    SELECT * FROM {self._table("staff_action_rule")}
                    WHERE tenant_id = :tenant_id AND id = :rule_id
                    FOR UPDATE
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "rule_id": _uuid(rule_id)},
            )
            current = current_result.mappings().first()
            if current is None:
                raise NotFoundError(
                    "STAFF_ACTION_RULE_NOT_FOUND",
                    "The action rule was not found",
                )
            if (
                _database_integer(current["version"], "staff_action_rule.version")
                != expected_version
            ):
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This action rule changed in another staff session",
                )
            merged: dict[str, object] = {
                "code": current["code"],
                "name": current["name"],
                "description": current["description"],
                "enabled": current["enabled"],
                "signalType": current["signal_type"],
                "flowKind": current["flow_kind"],
                "requirementCode": current["requirement_code"],
                "lookaheadDays": current["lookahead_days"],
                "inactivityDays": current["inactivity_days"],
                "cadenceMinutes": current["cadence_minutes"],
                "component": current["component"],
                "priority": current["priority"],
                "actionType": current["action_type"],
                "titleTemplate": current["title_template"],
                "descriptionTemplate": current["description_template"],
            }
            for public_name, snake_name in (
                ("name", "name"),
                ("description", "description"),
                ("enabled", "enabled"),
                ("flowKind", "flow_kind"),
                ("requirementCode", "requirement_code"),
                ("lookaheadDays", "lookahead_days"),
                ("inactivityDays", "inactivity_days"),
                ("cadenceMinutes", "cadence_minutes"),
                ("component", "component"),
                ("priority", "priority"),
                ("actionType", "action_type"),
                ("titleTemplate", "title_template"),
                ("descriptionTemplate", "description_template"),
            ):
                if public_name in payload:
                    merged[public_name] = payload[public_name]
                elif snake_name in payload:
                    merged[public_name] = payload[snake_name]
            values = _validated_action_rule_values(merged)
            updated = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_action_rule")}
                    SET name = :name, description = :description,
                        enabled = :enabled, flow_kind = :flow_kind,
                        requirement_code = :requirement_code,
                        lookahead_days = :lookahead_days,
                        inactivity_days = :inactivity_days,
                        cadence_minutes = :cadence_minutes,
                        component = :component, priority = :priority,
                        action_type = :action_type,
                        title_template = :title_template,
                        description_template = :description_template,
                        version = version + 1, updated_by = :actor_id,
                        last_evaluated_at = CASE
                          WHEN enabled IS DISTINCT FROM :enabled
                            OR flow_kind IS DISTINCT FROM :flow_kind
                            OR requirement_code IS DISTINCT FROM :requirement_code
                            OR lookahead_days IS DISTINCT FROM :lookahead_days
                            OR inactivity_days IS DISTINCT FROM :inactivity_days
                          THEN NULL ELSE last_evaluated_at
                        END,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND id = :rule_id
                    RETURNING version
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "rule_id": _uuid(rule_id),
                    "actor_id": _uuid(auth.actor_id),
                    **values,
                },
            )
            version = int(updated.scalar_one())
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff_action_rule.updated",
                resource_type="staff_action_rule",
                resource_id=rule_id,
                metadata={"version": version, "enabled": values["enabled"]},
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="staff.action_rule_updated.v1",
                aggregate_type="staff_action_rule",
                aggregate_id=rule_id,
                aggregate_version=version,
                data={"ruleId": rule_id, "enabled": values["enabled"]},
            )
        return await self._get_action_rule(auth, rule_id)

    async def add_work_comment(
        self,
        auth: AuthContext,
        work_item_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(payload, "expectedWorkItemVersion", "expected_work_item_version"),
            "expectedWorkItemVersion",
        )
        body = str(_read(payload, "body")).strip()
        mention_ids = [
            str(value)
            for value in cast(
                list[object],
                _read(payload, "mentionIds", "mention_ids", default=[]),
            )
        ]
        async with self._engine.begin() as connection:
            existing = await connection.execute(
                text(
                    f"""
                    SELECT id FROM {self._table("staff_work_comment")}
                    WHERE tenant_id = :tenant_id AND author_id = :author_id
                      AND request_key = :request_key
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "author_id": _uuid(auth.actor_id),
                    "request_key": idempotency_key,
                },
            )
            if existing.mappings().first() is None:
                current = await self._lock_work_item(connection, auth, work_item_id)
                if (
                    _database_integer(current["version"], "staff_work_item.version")
                    != expected_version
                ):
                    raise ConflictError(
                        "VERSION_CONFLICT",
                        "This work item changed in another staff session",
                    )
                if mention_ids:
                    mention_result = await connection.execute(
                        text(
                            f"""
                            SELECT id FROM {self._table("staff_member")}
                            WHERE tenant_id = :tenant_id AND active = true
                              AND id = ANY(:mention_ids)
                            """
                        ),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "mention_ids": [_uuid(value) for value in mention_ids],
                        },
                    )
                    valid_mentions = {str(row["id"]) for row in mention_result.mappings().all()}
                    if valid_mentions != set(mention_ids):
                        raise BadRequestError(
                            "STAFF_MENTION_NOT_FOUND",
                            "Every mentioned staff member must be active in this university",
                        )
                comment_id = self._uuid_factory()
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("staff_work_comment")} (
                          id, tenant_id, work_item_id, author_id, request_key,
                          body, mentions, created_at
                        ) VALUES (
                          :id, :tenant_id, :work_item_id, :author_id, :request_key,
                          :body, :mentions, NOW()
                        )
                        """
                    ),
                    {
                        "id": comment_id,
                        "tenant_id": _uuid(auth.tenant_id),
                        "work_item_id": _uuid(work_item_id),
                        "author_id": _uuid(auth.actor_id),
                        "request_key": idempotency_key,
                        "body": body,
                        "mentions": json.dumps(mention_ids),
                    },
                )
                updated = await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("staff_work_item")}
                        SET version = version + 1, updated_at = NOW()
                        WHERE tenant_id = :tenant_id AND id = :work_item_id
                          AND version = :expected_version
                        RETURNING version, key, title
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "work_item_id": _uuid(work_item_id),
                        "expected_version": expected_version,
                    },
                )
                updated_row = updated.mappings().first()
                if updated_row is None:
                    raise ConflictError(
                        "VERSION_CONFLICT",
                        "This work item changed in another staff session",
                    )
                actor_name = await self._staff_name(connection, auth, auth.actor_id)
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=work_item_id,
                    actor_name=actor_name,
                    action="commented",
                    message="Added an internal comment.",
                )
                for mention_id in mention_ids:
                    await connection.execute(
                        text(
                            f"""
                            INSERT INTO {self._table("staff_notification")} (
                              id, tenant_id, staff_member_id, kind, title, body,
                              resource_type, resource_id, dedupe_key, created_at
                            ) VALUES (
                              :id, :tenant_id, :staff_member_id, 'mention',
                              :title, :body, 'staff_work_item', :resource_id,
                              :dedupe_key, NOW()
                            )
                            ON CONFLICT (tenant_id, dedupe_key) DO NOTHING
                            """
                        ),
                        {
                            "id": self._uuid_factory(),
                            "tenant_id": _uuid(auth.tenant_id),
                            "staff_member_id": _uuid(mention_id),
                            "title": f"Mentioned on {updated_row['key']}",
                            "body": f"{actor_name} mentioned you on {updated_row['title']}.",
                            "resource_id": _uuid(work_item_id),
                            "dedupe_key": f"mention:{comment_id}:{mention_id}",
                        },
                    )
                await self._insert_audit(
                    connection,
                    auth=auth,
                    request_id=request_id,
                    action="staff_work_comment.created",
                    resource_type="staff_work_comment",
                    resource_id=str(comment_id),
                    metadata={"workItemId": work_item_id, "mentions": len(mention_ids)},
                )
                await self._insert_outbox(
                    connection,
                    auth=auth,
                    request_id=request_id,
                    event_name="staff.work_comment_created.v1",
                    aggregate_type="staff_work_item",
                    aggregate_id=work_item_id,
                    aggregate_version=int(updated_row["version"]),
                    data={"workItemId": work_item_id, "commentId": str(comment_id)},
                )
        return await self.get_work_item_detail(auth, work_item_id)

    async def start_interaction(
        self,
        auth: AuthContext,
        work_item_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(payload, "expectedWorkItemVersion", "expected_work_item_version"),
            "expectedWorkItemVersion",
        )
        channel = str(_read(payload, "channel"))
        objective = str(_read(payload, "objective")).strip()
        async with self._engine.begin() as connection:
            existing = await connection.execute(
                text(
                    f"""
                    SELECT id FROM {self._table("staff_interaction")}
                    WHERE tenant_id = :tenant_id AND created_by = :actor_id
                      AND request_key = :request_key
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "actor_id": _uuid(auth.actor_id),
                    "request_key": idempotency_key,
                },
            )
            if existing.mappings().first() is None:
                current = await self._lock_work_item(connection, auth, work_item_id)
                if (
                    _database_integer(current["version"], "staff_work_item.version")
                    != expected_version
                ):
                    raise ConflictError(
                        "VERSION_CONFLICT",
                        "This work item changed in another staff session",
                    )
                if str(current["status"]) in _TERMINAL_WORK_ITEM_STATUSES:
                    raise ConflictError(
                        "WORK_ITEM_TERMINAL",
                        "Closed work cannot start another interaction",
                    )
                interaction_id = self._uuid_factory()
                now = self._clock()
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("staff_interaction")} (
                          id, tenant_id, student_id, work_item_id, objective,
                          status, selected_channel, source_version,
                          covered_source_version, version, created_by, request_key,
                          created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :student_id, :work_item_id, :objective,
                          'collecting', :channel, 0, 0, 1, :created_by, :request_key,
                          :now, :now
                        )
                        """
                    ),
                    {
                        "id": interaction_id,
                        "tenant_id": _uuid(auth.tenant_id),
                        "student_id": current["student_id"],
                        "work_item_id": _uuid(work_item_id),
                        "objective": objective,
                        "channel": channel,
                        "created_by": _uuid(auth.actor_id),
                        "request_key": idempotency_key,
                        "now": now,
                    },
                )
                updated = await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("staff_work_item")}
                        SET status = 'in_progress', selected_channel = :channel,
                            attempt_count = attempt_count + 1,
                            started_at = COALESCE(started_at, :now),
                            version = version + 1, updated_at = :now
                        WHERE tenant_id = :tenant_id AND id = :work_item_id
                          AND version = :expected_version
                        RETURNING version
                        """
                    ),
                    {
                        "channel": channel,
                        "now": now,
                        "tenant_id": _uuid(auth.tenant_id),
                        "work_item_id": _uuid(work_item_id),
                        "expected_version": expected_version,
                    },
                )
                updated_row = updated.mappings().first()
                if updated_row is None:
                    raise ConflictError(
                        "VERSION_CONFLICT",
                        "This work item changed in another staff session",
                    )
                actor_name = await self._staff_name(connection, auth, auth.actor_id)
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=work_item_id,
                    actor_name=actor_name,
                    action="interaction_started",
                    message=f"Started a {channel} interaction: {objective}",
                )
                await self._insert_outbox(
                    connection,
                    auth=auth,
                    request_id=request_id,
                    event_name="staff.interaction_started.v1",
                    aggregate_type="staff_interaction",
                    aggregate_id=str(interaction_id),
                    aggregate_version=1,
                    data={
                        "workItemId": work_item_id,
                        "studentId": str(current["student_id"]),
                        "channel": channel,
                    },
                )
        return await self.get_work_item_detail(auth, work_item_id)

    async def record_interaction_communication(
        self,
        auth: AuthContext,
        interaction_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(payload, "expectedInteractionVersion", "expected_interaction_version"),
            "expectedInteractionVersion",
        )
        channel = str(_read(payload, "channel"))
        direction = str(_read(payload, "direction"))
        subject = _optional_text(_read(payload, "subject", default=None))
        body = str(_read(payload, "body")).strip()
        occurred_at = (
            _optional_datetime(
                _read(payload, "occurredAt", "occurred_at", default=None),
                "occurredAt",
            )
            or self._clock()
        )

        work_item_id: str | None = None
        async with self._engine.begin() as connection:
            existing = await connection.execute(
                text(
                    f"""
                    SELECT interaction.work_item_id
                    FROM {self._table("communication_event")} communication
                    JOIN {self._table("staff_interaction")} interaction
                      ON interaction.tenant_id = communication.tenant_id
                     AND interaction.id = communication.interaction_id
                    WHERE communication.tenant_id = :tenant_id
                      AND communication.request_key = :request_key
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "request_key": idempotency_key},
            )
            existing_row = existing.mappings().first()
            if existing_row is not None:
                work_item_id = str(existing_row["work_item_id"])
            else:
                interaction = await self._lock_interaction(connection, auth, interaction_id)
                work_item_id = str(interaction["work_item_id"])
                if (
                    _database_integer(interaction["version"], "staff_interaction.version")
                    != expected_version
                ):
                    raise ConflictError(
                        "VERSION_CONFLICT",
                        "This interaction changed in another staff session",
                    )
                work_item = await self._lock_work_item(connection, auth, work_item_id)
                if str(work_item["status"]) in _TERMINAL_WORK_ITEM_STATUSES:
                    raise ConflictError(
                        "WORK_ITEM_TERMINAL",
                        "Closed work cannot receive another communication",
                    )

                sequence = (
                    _database_integer(
                        interaction["source_version"], "staff_interaction.source_version"
                    )
                    + 1
                )
                communication_id = self._uuid_factory()
                delivery_status = "received" if direction == "inbound" else "recorded"
                if channel == "portal" and direction == "outbound":
                    linked_inquiry = await self._lock_linked_inquiry_for_work_item(
                        connection,
                        auth=auth,
                        work_item_id=work_item_id,
                    )
                    if linked_inquiry is not None:
                        self._require_active_inquiry_conversation(linked_inquiry)
                    delivered_message = await self._insert_student_message(
                        connection,
                        auth=auth,
                        student_id=str(interaction["student_id"]),
                        subject=subject or f"Follow-up: {work_item['title']}",
                        body=body,
                        href=(
                            f"/help?conversation={linked_inquiry['id']}"
                            if linked_inquiry is not None
                            else None
                        ),
                    )
                    if linked_inquiry is not None:
                        await connection.execute(
                            text(
                                f"""
                                UPDATE {self._table("student_inquiry")}
                                SET status = 'waiting_on_student',
                                    last_message_at = NOW(),
                                    expires_at = NOW() + interval '5 days',
                                    version = version + 1,
                                    updated_at = NOW()
                                WHERE tenant_id = :tenant_id AND id = :inquiry_id
                                """
                            ),
                            {
                                "tenant_id": _uuid(auth.tenant_id),
                                "inquiry_id": linked_inquiry["id"],
                            },
                        )
                        await connection.execute(
                            text(
                                f"""
                                INSERT INTO {self._table("student_inquiry_reply")} (
                                  id, tenant_id, inquiry_id, student_id, staff_member_id,
                                  response_note, notify_student, student_message_id, created_at
                                ) VALUES (
                                  :id, :tenant_id, :inquiry_id, :student_id, :staff_member_id,
                                  :response_note, true, :student_message_id, NOW()
                                )
                                """
                            ),
                            {
                                "id": self._uuid_factory(),
                                "tenant_id": _uuid(auth.tenant_id),
                                "inquiry_id": linked_inquiry["id"],
                                "student_id": linked_inquiry["student_id"],
                                "staff_member_id": _uuid(auth.actor_id),
                                "response_note": body,
                                "student_message_id": _uuid(str(delivered_message["id"])),
                            },
                        )
                    delivery_status = "delivered"
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("communication_event")} (
                          id, tenant_id, student_id, channel, direction, subject,
                          body_excerpt, metadata, resolution_status, occurred_at,
                          created_at, interaction_id, source_type, source_id,
                          source_sequence, request_key, delivery_status
                        ) VALUES (
                          :id, :tenant_id, :student_id, :channel, :direction,
                          :subject, :body, :metadata, 'unresolved', :occurred_at,
                          NOW(), :interaction_id, 'staff_recorded_communication',
                          :source_id, :source_sequence, :request_key, :delivery_status
                        )
                        """
                    ),
                    {
                        "id": communication_id,
                        "tenant_id": _uuid(auth.tenant_id),
                        "student_id": interaction["student_id"],
                        "channel": channel,
                        "direction": direction,
                        "subject": subject,
                        "body": body,
                        "metadata": json.dumps(
                            {"recordedByStaffId": auth.actor_id}, separators=(",", ":")
                        ),
                        "occurred_at": occurred_at,
                        "interaction_id": _uuid(interaction_id),
                        "source_id": communication_id,
                        "source_sequence": sequence,
                        "request_key": idempotency_key,
                        "delivery_status": delivery_status,
                    },
                )
                quiet_until = occurred_at + timedelta(minutes=5)
                interaction_update = await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("staff_interaction")}
                        SET source_version = :source_version,
                            status = CASE
                              WHEN covered_source_version > 0 THEN 'stale'
                              ELSE 'enrichment_pending'
                            END,
                            quiet_until = :quiet_until,
                            last_activity_at = :occurred_at,
                            version = version + 1,
                            updated_at = NOW()
                        WHERE tenant_id = :tenant_id AND id = :interaction_id
                          AND version = :expected_version
                        RETURNING version
                        """
                    ),
                    {
                        "source_version": sequence,
                        "quiet_until": quiet_until,
                        "occurred_at": occurred_at,
                        "tenant_id": _uuid(auth.tenant_id),
                        "interaction_id": _uuid(interaction_id),
                        "expected_version": expected_version,
                    },
                )
                if interaction_update.mappings().first() is None:
                    raise ConflictError(
                        "VERSION_CONFLICT",
                        "This interaction changed in another staff session",
                    )
                work_update = await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("staff_work_item")}
                        SET status = 'in_progress', selected_channel = :channel,
                            version = version + 1, updated_at = NOW()
                        WHERE tenant_id = :tenant_id AND id = :work_item_id
                        RETURNING version
                        """
                    ),
                    {
                        "channel": channel,
                        "tenant_id": _uuid(auth.tenant_id),
                        "work_item_id": _uuid(work_item_id),
                    },
                )
                work_version = _database_integer(
                    cast(Mapping[str, object], work_update.mappings().one())["version"],
                    "staff_work_item.version",
                )
                await self._queue_interaction_refresh(
                    connection,
                    auth=auth,
                    interaction_id=interaction_id,
                    work_item_id=work_item_id,
                    student_id=str(interaction["student_id"]),
                    source_version=sequence,
                    not_before=quiet_until,
                )
                actor_name = await self._staff_name(connection, auth, auth.actor_id)
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=work_item_id,
                    actor_name=actor_name,
                    action="communication_recorded",
                    message=(
                        f"Recorded {direction} {channel} communication"
                        + (
                            " and delivered it to the portal inbox."
                            if delivery_status == "delivered"
                            else "."
                        )
                    ),
                )
                await self._insert_outbox(
                    connection,
                    auth=auth,
                    request_id=request_id,
                    event_name="staff.communication_recorded.v1",
                    aggregate_type="staff_interaction",
                    aggregate_id=interaction_id,
                    aggregate_version=sequence,
                    data={
                        "workItemId": work_item_id,
                        "studentId": str(interaction["student_id"]),
                        "communicationId": str(communication_id),
                        "channel": channel,
                        "direction": direction,
                        "workItemVersion": work_version,
                    },
                )
        assert work_item_id is not None
        return await self.get_work_item_detail(auth, work_item_id)

    async def complete_interaction(
        self,
        auth: AuthContext,
        interaction_id: str,
        payload: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_interaction_version = _integer(
            _read(payload, "expectedInteractionVersion", "expected_interaction_version"),
            "expectedInteractionVersion",
        )
        expected_work_item_version = _integer(
            _read(payload, "expectedWorkItemVersion", "expected_work_item_version"),
            "expectedWorkItemVersion",
        )
        outcome_code = str(_read(payload, "outcomeCode", "outcome_code"))
        resolution_code = str(_read(payload, "resolutionCode", "resolution_code"))
        next_step = _optional_text(_read(payload, "nextStep", "next_step", default=None))
        follow_up_at = _optional_datetime(
            _read(payload, "followUpAt", "follow_up_at", default=None),
            "followUpAt",
        )
        now = self._clock()
        if follow_up_at is not None and follow_up_at <= now:
            raise BadRequestError(
                "FOLLOW_UP_TIME_INVALID",
                "Choose a follow-up time in the future",
            )
        if follow_up_at is not None and next_step is None:
            raise BadRequestError(
                "FOLLOW_UP_DETAILS_REQUIRED",
                "A scheduled follow-up requires a clear next step",
            )

        async with self._engine.begin() as connection:
            interaction = await self._lock_interaction(connection, auth, interaction_id)
            work_item_id = str(interaction["work_item_id"])
            if (
                _database_integer(interaction["version"], "staff_interaction.version")
                != expected_interaction_version
            ):
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This interaction changed in another staff session",
                )
            work_item = await self._lock_work_item(connection, auth, work_item_id)
            if (
                _database_integer(work_item["version"], "staff_work_item.version")
                != expected_work_item_version
            ):
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This work item changed in another staff session",
                )
            if str(work_item["status"]) in _TERMINAL_WORK_ITEM_STATUSES:
                raise ConflictError("WORK_ITEM_TERMINAL", "This work item is already closed")

            await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_interaction")}
                    SET status = 'enrichment_pending', quiet_until = :now,
                        completed_at = :now, version = version + 1,
                        updated_at = :now
                    WHERE tenant_id = :tenant_id AND id = :interaction_id
                      AND version = :expected_version
                    """
                ),
                {
                    "now": now,
                    "tenant_id": _uuid(auth.tenant_id),
                    "interaction_id": _uuid(interaction_id),
                    "expected_version": expected_interaction_version,
                },
            )
            next_status = "follow_up_required" if follow_up_at is not None else "done"
            updated = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_work_item")}
                    SET status = CAST(:status AS varchar), outcome_code = :outcome_code,
                        resolution_code = :resolution_code, next_step = :next_step,
                        follow_up_at = :follow_up_at,
                        interaction_completed_at = :now,
                        completed_at = CASE
                          WHEN CAST(:status AS varchar) = 'done' THEN :now
                          ELSE completed_at
                        END,
                        version = version + 1, updated_at = :now
                    WHERE tenant_id = :tenant_id AND id = :work_item_id
                      AND version = :expected_version
                    RETURNING version
                    """
                ),
                {
                    "status": next_status,
                    "outcome_code": outcome_code,
                    "resolution_code": resolution_code,
                    "next_step": next_step,
                    "follow_up_at": follow_up_at,
                    "now": now,
                    "tenant_id": _uuid(auth.tenant_id),
                    "work_item_id": _uuid(work_item_id),
                    "expected_version": expected_work_item_version,
                },
            )
            updated_row = updated.mappings().first()
            if updated_row is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This work item changed in another staff session",
                )
            await self._queue_interaction_refresh(
                connection,
                auth=auth,
                interaction_id=interaction_id,
                work_item_id=work_item_id,
                student_id=str(interaction["student_id"]),
                source_version=_database_integer(
                    interaction["source_version"], "staff_interaction.source_version"
                ),
                not_before=now,
                force=True,
            )
            actor_name = await self._staff_name(connection, auth, auth.actor_id)
            await self._insert_work_log(
                connection,
                auth=auth,
                work_item_id=work_item_id,
                actor_name=actor_name,
                action="outcome_recorded",
                message=(
                    f"Completed the interaction with outcome {outcome_code} and "
                    f"resolution {resolution_code}."
                ),
            )
            if follow_up_at is not None:
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=work_item_id,
                    actor_name=actor_name,
                    action="follow_up_scheduled",
                    message=f"Scheduled follow-up for {_iso_timestamp(follow_up_at)}.",
                )
            if next_status == "done":
                await self._resolve_linked_inquiry_for_completed_work(
                    connection,
                    auth=auth,
                    work_item=work_item,
                    work_item_id=work_item_id,
                )
                await self._insert_student_message(
                    connection,
                    auth=auth,
                    student_id=str(interaction["student_id"]),
                    subject=f"Completed: {work_item['title']}",
                    body=(
                        "Your enrollment team completed this support action. "
                        "Open My Enrollment to review your current status and next steps."
                    ),
                    kind="action_completed",
                    href="/enrollment",
                )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="staff.interaction_completed.v1",
                aggregate_type="staff_interaction",
                aggregate_id=interaction_id,
                aggregate_version=expected_interaction_version + 1,
                data={
                    "workItemId": work_item_id,
                    "studentId": str(interaction["student_id"]),
                    "outcomeCode": outcome_code,
                    "resolutionCode": resolution_code,
                    "followUpAt": (
                        _iso_timestamp(follow_up_at) if follow_up_at is not None else None
                    ),
                    "workItemVersion": int(updated_row["version"]),
                },
            )
        return await self.get_work_item_detail(auth, work_item_id)

    async def begin_call_recording(
        self,
        auth: AuthContext,
        interaction_id: str,
        *,
        recording_id: str,
        request_key: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        storage_key: str,
        sha256: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        async with self._engine.begin() as connection:
            existing_result = await connection.execute(
                text(
                    f"""
                    SELECT id, work_item_id, status, storage_key, sha256
                    FROM {self._table("staff_call_recording")}
                    WHERE tenant_id = :tenant_id AND uploaded_by = :uploaded_by
                      AND request_key = :request_key
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "uploaded_by": _uuid(auth.actor_id),
                    "request_key": request_key,
                },
            )
            existing = existing_result.mappings().first()
            if existing is not None:
                if str(existing["sha256"]) != sha256:
                    raise ConflictError(
                        "IDEMPOTENCY_CONFLICT",
                        "This upload key was already used for a different recording",
                    )
                return {
                    "id": str(existing["id"]),
                    "workItemId": str(existing["work_item_id"]),
                    "storageKey": str(existing["storage_key"]),
                    "shouldUpload": str(existing["status"]) in {"uploading", "upload_failed"},
                }

            interaction = await self._lock_interaction(connection, auth, interaction_id)
            if str(interaction["selected_channel"] or "") != "voice":
                raise BadRequestError(
                    "VOICE_INTERACTION_REQUIRED",
                    "Call recordings can only be attached to a voice interaction",
                )
            work_item_id = str(interaction["work_item_id"])
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("staff_call_recording")} (
                      id, tenant_id, interaction_id, work_item_id, student_id,
                      uploaded_by, request_key, file_name, mime_type, size_bytes,
                      storage_key, sha256, consent_confirmed, status, attempts,
                      max_attempts, version, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :interaction_id, :work_item_id, :student_id,
                      :uploaded_by, :request_key, :file_name, :mime_type,
                      :size_bytes, :storage_key, :sha256, true, 'uploading', 0,
                      5, 1, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": _uuid(recording_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "interaction_id": _uuid(interaction_id),
                    "work_item_id": _uuid(work_item_id),
                    "student_id": interaction["student_id"],
                    "uploaded_by": _uuid(auth.actor_id),
                    "request_key": request_key,
                    "file_name": file_name,
                    "mime_type": mime_type,
                    "size_bytes": size_bytes,
                    "storage_key": storage_key,
                    "sha256": sha256,
                },
            )
        return {
            "id": recording_id,
            "workItemId": work_item_id,
            "storageKey": storage_key,
            "shouldUpload": True,
        }

    async def confirm_call_recording_upload(
        self,
        auth: AuthContext,
        recording_id: str,
        request_id: str,
    ) -> str:
        self._require_staff(auth)
        async with self._engine.begin() as connection:
            recording = await self._lock_call_recording(connection, auth, recording_id)
            work_item_id = str(recording["work_item_id"])
            if str(recording["status"]) not in {"queued", "ready"}:
                updated = await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("staff_call_recording")}
                        SET status = 'queued', uploaded_at = COALESCE(uploaded_at, NOW()),
                            last_error_code = NULL, last_error_message = NULL,
                            version = version + 1, updated_at = NOW()
                        WHERE tenant_id = :tenant_id AND id = :recording_id
                        RETURNING version
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "recording_id": _uuid(recording_id),
                    },
                )
                recording_version = int(updated.scalar_one())
                actor_name = await self._staff_name(connection, auth, auth.actor_id)
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=work_item_id,
                    actor_name=actor_name,
                    action="call_recording_uploaded",
                    message="Stored a consent-confirmed call recording; transcription queued.",
                )
                await self._insert_outbox(
                    connection,
                    auth=auth,
                    request_id=request_id,
                    event_name="staff.call_recording_uploaded.v1",
                    aggregate_type="staff_call_recording",
                    aggregate_id=recording_id,
                    aggregate_version=recording_version,
                    data={
                        "recordingId": recording_id,
                        "interactionId": str(recording["interaction_id"]),
                        "workItemId": work_item_id,
                    },
                )
        return work_item_id

    async def fail_call_recording_upload(
        self,
        auth: AuthContext,
        recording_id: str,
        error: Exception,
    ) -> str:
        self._require_staff(auth)
        async with self._engine.begin() as connection:
            recording = await self._lock_call_recording(connection, auth, recording_id)
            await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_call_recording")}
                    SET status = 'upload_failed', last_error_code = :error_code,
                        last_error_message = :error_message,
                        version = version + 1, updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND id = :recording_id
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "recording_id": _uuid(recording_id),
                    "error_code": type(error).__name__[:80],
                    "error_message": (str(error) or "Object storage failed")[:500],
                },
            )
            return str(recording["work_item_id"])

    async def get_call_recording_reference(
        self,
        auth: AuthContext,
        recording_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT storage_key, file_name, mime_type, status
                    FROM {self._table("staff_call_recording")}
                    WHERE tenant_id = :tenant_id AND id = :recording_id
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "recording_id": _uuid(recording_id),
                },
            )
            row = result.mappings().first()
        if row is None:
            raise NotFoundError(
                "STAFF_CALL_RECORDING_NOT_FOUND",
                "The call recording was not found",
            )
        if row["status"] in {"uploading", "upload_failed"}:
            raise ConflictError(
                "CALL_RECORDING_NOT_STORED",
                "The call recording has not been stored successfully",
            )
        return {
            "storageKey": str(row["storage_key"]),
            "fileName": str(row["file_name"]),
            "mimeType": str(row["mime_type"]),
        }

    async def retry_call_transcription(
        self,
        auth: AuthContext,
        recording_id: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(payload, "expectedRecordingVersion", "expected_recording_version"),
            "expectedRecordingVersion",
        )
        async with self._engine.begin() as connection:
            recording = await self._lock_call_recording(connection, auth, recording_id)
            if (
                _database_integer(recording["version"], "staff_call_recording.version")
                != expected_version
            ):
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This recording changed in another staff session",
                )
            status = str(recording["status"])
            if status in {"uploading", "upload_failed"}:
                raise ConflictError(
                    "CALL_RECORDING_NOT_STORED",
                    "Re-upload the original audio before requesting transcription",
                )
            if status in {"queued", "transcribing"}:
                raise ConflictError(
                    "CALL_TRANSCRIPTION_ALREADY_RUNNING",
                    "This recording is already queued for transcription",
                )
            await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_call_recording")}
                    SET status = 'queued', attempts = 0, lease_owner = NULL,
                        lease_expires_at = NULL, next_attempt_at = NULL,
                        last_error_code = NULL,
                        last_error_message = NULL, version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND id = :recording_id
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "recording_id": _uuid(recording_id),
                },
            )
            work_item_id = str(recording["work_item_id"])
        return await self.get_work_item_detail(auth, work_item_id)

    async def request_ai_refresh(
        self,
        auth: AuthContext,
        work_item_id: str,
        payload: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(payload, "expectedWorkItemVersion", "expected_work_item_version"),
            "expectedWorkItemVersion",
        )
        scope = str(_read(payload, "scope"))
        interaction_value = _read(payload, "interactionId", "interaction_id", default=None)
        now = self._clock()
        async with self._engine.begin() as connection:
            work_item = await self._lock_work_item(connection, auth, work_item_id)
            if (
                _database_integer(work_item["version"], "staff_work_item.version")
                != expected_version
            ):
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This work item changed in another staff session",
                )
            if scope in {"interaction", "both"}:
                if interaction_value is None:
                    raise BadRequestError(
                        "INTERACTION_REQUIRED",
                        "Choose an interaction to regenerate its outcome",
                    )
                interaction = await self._lock_interaction(connection, auth, str(interaction_value))
                if str(interaction["work_item_id"]) != work_item_id:
                    raise NotFoundError(
                        "STAFF_INTERACTION_NOT_FOUND",
                        "The interaction was not found on this work item",
                    )
                await self._queue_interaction_refresh(
                    connection,
                    auth=auth,
                    interaction_id=str(interaction_value),
                    work_item_id=work_item_id,
                    student_id=str(work_item["student_id"]),
                    source_version=_database_integer(
                        interaction["source_version"], "staff_interaction.source_version"
                    ),
                    not_before=now,
                    force=True,
                )
            # An interaction enrichment already returns and persists both the
            # interaction outcome and the canonical student summary. Keep the
            # explicit `both` scope on that single durable job so a manual
            # refresh does not spend a second provider call for the same
            # evidence snapshot.
            if scope == "student_summary":
                await self._queue_student_summary_refresh(
                    connection,
                    auth=auth,
                    student_id=str(work_item["student_id"]),
                    not_before=now,
                )
            if scope == "task_insight":
                await self._queue_task_insight_refresh(
                    connection,
                    auth=auth,
                    work_item_id=work_item_id,
                    student_id=str(work_item["student_id"]),
                    source_version=expected_version,
                    not_before=now,
                )
            actor_name = await self._staff_name(connection, auth, auth.actor_id)
            await self._insert_work_log(
                connection,
                auth=auth,
                work_item_id=work_item_id,
                actor_name=actor_name,
                action="ai_refresh_requested",
                message=f"Requested {scope.replace('_', ' ')} AI refresh.",
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="staff.ai_refresh_requested.v1",
                aggregate_type="staff_work_item",
                aggregate_id=work_item_id,
                aggregate_version=expected_version,
                data={
                    "workItemId": work_item_id,
                    "studentId": str(work_item["student_id"]),
                    "scope": scope,
                    "interactionId": (
                        str(interaction_value) if interaction_value is not None else None
                    ),
                },
            )
        return await self.get_work_item_detail(auth, work_item_id)

    async def update_work_item(
        self,
        auth: AuthContext,
        work_item_id: str,
        update: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = _integer(
            _read(update, "expectedVersion", "expected_version"),
            "expectedVersion",
        )
        async with self._engine.begin() as connection:
            current = await self._lock_work_item(connection, auth, work_item_id)
            if _database_integer(current["version"], "staff_work_item.version") != expected_version:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This work item changed in another staff session",
                )

            status_value = _read(update, "status", default=None)
            next_status = str(status_value) if status_value is not None else str(current["status"])
            if next_status not in _WORK_ITEM_STATUSES:
                raise BadRequestError("INVALID_WORK_ITEM_STATUS", "Choose a supported work status")
            if str(current["status"]) in _TERMINAL_WORK_ITEM_STATUSES and next_status != str(
                current["status"]
            ):
                raise ConflictError(
                    "WORK_ITEM_TERMINAL",
                    "Closed work is immutable; create a linked follow-up instead",
                )

            assignee_value = _read(update, "assigneeId", "assignee_id", default=_MISSING)
            next_assignee = current["assignee_id"] if assignee_value is _MISSING else assignee_value
            escalated_value = _read(update, "escalated", default=None)
            next_escalated = (
                bool(current["escalated"]) if escalated_value is None else bool(escalated_value)
            )

            channel_value = _read(update, "selectedChannel", "selected_channel", default=_MISSING)
            next_channel = (
                current.get("selected_channel") if channel_value is _MISSING else channel_value
            )
            if next_channel is not None and str(next_channel) not in _COMMUNICATION_CHANNELS:
                raise BadRequestError(
                    "INVALID_COMMUNICATION_CHANNEL",
                    "Choose email, SMS, voice, or portal messaging",
                )

            follow_up_value = _read(update, "followUpAt", "follow_up_at", default=_MISSING)
            next_follow_up = (
                current.get("follow_up_at")
                if follow_up_value is _MISSING
                else _optional_datetime(follow_up_value, "followUpAt")
            )
            blocker_code_value = _read(update, "blockerCode", "blocker_code", default=_MISSING)
            next_blocker_code = (
                current.get("blocker_code")
                if blocker_code_value is _MISSING
                else _optional_text(blocker_code_value)
            )
            blocker_detail_value = _read(
                update, "blockerDetail", "blocker_detail", default=_MISSING
            )
            next_blocker_detail = (
                current.get("blocker_detail")
                if blocker_detail_value is _MISSING
                else _optional_text(blocker_detail_value)
            )
            blocker_review_value = _read(
                update, "blockerReviewAt", "blocker_review_at", default=_MISSING
            )
            next_blocker_review = (
                current.get("blocker_review_at")
                if blocker_review_value is _MISSING
                else _optional_datetime(blocker_review_value, "blockerReviewAt")
            )
            outcome_value = _read(update, "outcomeCode", "outcome_code", default=_MISSING)
            next_outcome = (
                current.get("outcome_code")
                if outcome_value is _MISSING
                else _optional_text(outcome_value)
            )
            resolution_value = _read(update, "resolutionCode", "resolution_code", default=_MISSING)
            next_resolution = (
                current.get("resolution_code")
                if resolution_value is _MISSING
                else _optional_text(resolution_value)
            )
            next_step_value = _read(update, "nextStep", "next_step", default=_MISSING)
            next_step = (
                current.get("next_step")
                if next_step_value is _MISSING
                else _optional_text(next_step_value)
            )
            terminal_reason_value = _read(
                update, "terminalReason", "terminal_reason", default=_MISSING
            )
            next_terminal_reason = (
                current.get("terminal_reason")
                if terminal_reason_value is _MISSING
                else _optional_text(terminal_reason_value)
            )

            now = self._clock()
            if next_status == "follow_up_required":
                if not isinstance(next_follow_up, datetime) or next_step is None:
                    raise BadRequestError(
                        "FOLLOW_UP_DETAILS_REQUIRED",
                        "Follow-up work requires a future time and a clear next step",
                    )
                if next_follow_up <= now:
                    raise BadRequestError(
                        "FOLLOW_UP_TIME_INVALID",
                        "Choose a follow-up time in the future",
                    )
            if next_status == "blocked" and (
                next_blocker_code is None or next_blocker_detail is None
            ):
                raise BadRequestError(
                    "BLOCKER_DETAILS_REQUIRED",
                    "Blocked work requires a blocker code and explanation",
                )
            if next_status == "done" and (next_outcome is None or next_resolution is None):
                raise BadRequestError(
                    "OUTCOME_REQUIRED",
                    "Completed work requires an outcome and resolution",
                )
            if next_status == "cancelled" and next_terminal_reason is None:
                raise BadRequestError(
                    "CANCELLATION_REASON_REQUIRED",
                    "Cancelled work requires a reason",
                )

            if next_assignee is not None:
                assignee = await connection.execute(
                    text(
                        f"""
                        SELECT id
                        FROM {self._table("staff_member")}
                        WHERE tenant_id = :tenant_id
                          AND id = :assignee_id
                          AND active = true
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "assignee_id": _uuid(str(next_assignee)),
                    },
                )
                if assignee.mappings().first() is None:
                    raise NotFoundError(
                        "STAFF_MEMBER_NOT_FOUND",
                        "The assignee was not found",
                    )

            changes: list[tuple[str, str]] = []
            if next_status != current["status"]:
                changes.append(
                    (
                        "status_changed",
                        "Moved from "
                        f"{str(current['status']).replace('_', ' ')} to "
                        f"{next_status.replace('_', ' ')}.",
                    )
                )
            if _optional_uuid_string(next_assignee) != _optional_uuid_string(
                current["assignee_id"]
            ):
                assignee_name = (
                    await self._staff_name(connection, auth, str(next_assignee))
                    if next_assignee is not None
                    else None
                )
                changes.append(
                    (
                        "assigned",
                        f"Assigned to {assignee_name}."
                        if assignee_name
                        else "Removed the assignee.",
                    )
                )
            if next_escalated != bool(current["escalated"]):
                changes.append(
                    (
                        "escalated",
                        "Marked as escalated."
                        if next_escalated
                        else "Cleared the escalation flag.",
                    )
                )
            if _optional_text(next_channel) != _optional_text(current.get("selected_channel")):
                changes.append(
                    (
                        "channel_selected",
                        (
                            f"Selected {str(next_channel).replace('_', ' ')}."
                            if next_channel is not None
                            else "Cleared the selected channel."
                        ),
                    )
                )
            if next_follow_up != current.get("follow_up_at"):
                changes.append(
                    (
                        "follow_up_scheduled",
                        (
                            f"Scheduled follow-up for {_iso_timestamp(next_follow_up)}."
                            if next_follow_up is not None
                            else "Cleared the scheduled follow-up."
                        ),
                    )
                )
            if (
                _optional_text(next_blocker_code) != _optional_text(current.get("blocker_code"))
                or _optional_text(next_blocker_detail)
                != _optional_text(current.get("blocker_detail"))
                or next_blocker_review != current.get("blocker_review_at")
            ):
                changes.append(
                    (
                        "blocked",
                        (
                            f"Recorded blocker {next_blocker_code}."
                            if next_blocker_code is not None
                            else "Cleared blocker details."
                        ),
                    )
                )
            if (
                _optional_text(next_outcome) != _optional_text(current.get("outcome_code"))
                or _optional_text(next_resolution) != _optional_text(current.get("resolution_code"))
                or _optional_text(next_step) != _optional_text(current.get("next_step"))
            ):
                changes.append(("outcome_recorded", "Recorded the interaction outcome."))
            if next_status == "cancelled" and str(current["status"]) != "cancelled":
                changes.append(("cancelled", f"Cancelled: {next_terminal_reason}."))
            note = _optional_text(_read(update, "note", default=None))
            if note:
                changes.append(("commented", note))
            if not changes:
                raise BadRequestError(
                    "STAFF_WORK_ITEM_NO_CHANGES",
                    "Choose a status, assignee, escalation state, or note to update",
                )

            updated = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_work_item")}
                    SET status = CAST(:status AS varchar),
                        assignee_id = :assignee_id,
                        escalated = :escalated,
                        selected_channel = :selected_channel,
                        follow_up_at = :follow_up_at,
                        blocker_code = :blocker_code,
                        blocker_detail = :blocker_detail,
                        blocker_review_at = :blocker_review_at,
                        outcome_code = :outcome_code,
                        resolution_code = :resolution_code,
                        next_step = :next_step,
                        terminal_reason = :terminal_reason,
                        started_at = CASE
                          WHEN CAST(:status AS varchar) = 'in_progress'
                          THEN COALESCE(started_at, :now)
                          ELSE started_at
                        END,
                        completed_at = CASE
                          WHEN CAST(:status AS varchar) = 'done'
                          THEN COALESCE(completed_at, :now)
                          ELSE completed_at
                        END,
                        cancelled_at = CASE
                          WHEN CAST(:status AS varchar) = 'cancelled'
                          THEN COALESCE(cancelled_at, :now)
                          ELSE cancelled_at
                        END,
                        version = version + 1,
                        updated_at = :now
                    WHERE tenant_id = :tenant_id
                      AND id = :work_item_id
                      AND version = :expected_version
                    RETURNING version
                    """
                ),
                {
                    "status": next_status,
                    "assignee_id": (
                        _uuid(str(next_assignee)) if next_assignee is not None else None
                    ),
                    "escalated": next_escalated,
                    "selected_channel": next_channel,
                    "follow_up_at": next_follow_up,
                    "blocker_code": next_blocker_code,
                    "blocker_detail": next_blocker_detail,
                    "blocker_review_at": next_blocker_review,
                    "outcome_code": next_outcome,
                    "resolution_code": next_resolution,
                    "next_step": next_step,
                    "terminal_reason": next_terminal_reason,
                    "now": now,
                    "tenant_id": _uuid(auth.tenant_id),
                    "work_item_id": _uuid(work_item_id),
                    "expected_version": expected_version,
                },
            )
            updated_row = updated.mappings().first()
            if updated_row is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This work item changed in another staff session",
                )
            version = int(updated_row["version"])
            actor_name = await self._staff_name(connection, auth, auth.actor_id)
            for action, message in changes:
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=work_item_id,
                    actor_name=actor_name,
                    action=action,
                    message=message,
                )
            if next_status in _TERMINAL_WORK_ITEM_STATUSES:
                inquiry_result = await connection.execute(
                    text(
                        f"""
                        SELECT inquiry.id, inquiry.requirement_id,
                               inquiry.status_before_help
                        FROM {self._table("student_inquiry")} inquiry
                        WHERE inquiry.tenant_id = :tenant_id
                          AND inquiry.status IN ('new','open','waiting_on_student')
                          AND (
                            (
                              :source_type = 'message'
                              AND inquiry.id = :source_id
                            )
                            OR EXISTS (
                              SELECT 1
                              FROM {self._table("staff_work_item_link")} link
                              WHERE link.tenant_id = inquiry.tenant_id
                                AND link.work_item_id = :work_item_id
                                AND link.entity_type = 'inquiry'
                                AND link.entity_id = inquiry.id
                            )
                          )
                        ORDER BY inquiry.created_at, inquiry.id
                        LIMIT 1
                        FOR UPDATE OF inquiry
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "source_type": current.get("source_type"),
                        "source_id": current.get("source_id"),
                        "work_item_id": _uuid(work_item_id),
                    },
                )
                inquiry = inquiry_result.mappings().first()
                if inquiry is not None:
                    await connection.execute(
                        text(
                            f"""
                            UPDATE {self._table("student_inquiry")}
                            SET status = 'resolved', resolved_at = NOW(),
                                resolution_reason = :resolution_reason,
                                version = version + 1, updated_at = NOW()
                            WHERE tenant_id = :tenant_id AND id = :inquiry_id
                            """
                        ),
                        {
                            "resolution_reason": f"work_item_{next_status}",
                            "tenant_id": _uuid(auth.tenant_id),
                            "inquiry_id": inquiry["id"],
                        },
                    )
                    if inquiry.get("requirement_id") is not None:
                        restore_status = str(inquiry.get("status_before_help") or "in_progress")
                        if restore_status == "help_requested":
                            restore_status = "in_progress"
                        await connection.execute(
                            text(
                                f"""
                                UPDATE {self._table("student_requirement")}
                                SET status = CAST(:status AS varchar),
                                    version = version + 1, updated_at = NOW()
                                WHERE tenant_id = :tenant_id
                                  AND id = :requirement_id
                                  AND status = 'help_requested'
                                """
                            ),
                            {
                                "status": restore_status,
                                "tenant_id": _uuid(auth.tenant_id),
                                "requirement_id": inquiry["requirement_id"],
                            },
                        )
            if next_status == "done" and str(current["status"]) != "done":
                await self._insert_student_message(
                    connection,
                    auth=auth,
                    student_id=str(current["student_id"]),
                    subject=f"Completed: {current['title']}",
                    body=(
                        "Your enrollment team completed this support action. "
                        "Open My Enrollment to review your current status and next steps."
                    ),
                    kind="action_completed",
                    href="/enrollment",
                )
            material_change = any(
                action
                in {
                    "status_changed",
                    "blocked",
                    "cancelled",
                    "outcome_recorded",
                    "follow_up_scheduled",
                }
                for action, _message in changes
            )
            if material_change:
                await self._queue_student_summary_refresh(
                    connection,
                    auth=auth,
                    student_id=str(current["student_id"]),
                    not_before=now + timedelta(minutes=5),
                )
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="staff_work_item.updated",
                resource_type="staff_work_item",
                resource_id=work_item_id,
                metadata={"version": version, "changes": [change[0] for change in changes]},
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="staff.work_item_updated.v1",
                aggregate_type="staff_work_item",
                aggregate_id=work_item_id,
                aggregate_version=version,
                data={
                    "workItemId": work_item_id,
                    "studentId": str(current["student_id"]),
                    "status": next_status,
                    "assigneeId": _optional_uuid_string(next_assignee),
                    "escalated": next_escalated,
                    "selectedChannel": next_channel,
                    "followUpAt": (
                        _iso_timestamp(next_follow_up) if next_follow_up is not None else None
                    ),
                    "outcomeCode": next_outcome,
                    "resolutionCode": next_resolution,
                },
            )
        return await self._require_work_item(auth, work_item_id)

    async def update_inquiry(
        self,
        auth: AuthContext,
        inquiry_id: str,
        update: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        """Update the canonical tenant inquiry and fail closed when it is absent."""

        self._require_staff(auth)
        expected_version = _integer(
            _read(update, "expectedVersion", "expected_version"),
            "expectedVersion",
        )
        next_status = str(_read(update, "status"))
        assignee_value = _read(update, "assigneeId", "assignee_id", default=_MISSING)
        response_note = _optional_text(_read(update, "responseNote", "response_note", default=None))
        notify_student = bool(_read(update, "notifyStudent", "notify_student"))

        async with self._engine.begin() as connection:
            inquiry = await self._lock_inquiry(connection, auth, inquiry_id)
            if inquiry is None:
                raise NotFoundError(
                    "STAFF_INQUIRY_NOT_FOUND",
                    "The student inquiry was not found",
                )
            if _database_integer(inquiry["version"], "student_inquiry.version") != expected_version:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This inquiry changed in another staff session",
                )
            self._require_active_inquiry_conversation(inquiry)

            next_assignee = inquiry["assignee_id"] if assignee_value is _MISSING else assignee_value
            assignee: dict[str, object] | None = None
            if next_assignee is not None:
                assignee = await self._active_staff_summary(
                    connection,
                    auth,
                    str(next_assignee),
                )
                if assignee is None:
                    raise BadRequestError(
                        "STAFF_ASSIGNEE_NOT_FOUND",
                        "Choose an active staff assignee",
                    )

            updated_result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("student_inquiry")}
                    SET status = :status,
                        assignee_id = :assignee_id,
                        resolved_at = CASE
                          WHEN :is_resolved THEN NOW() ELSE NULL
                        END,
                        resolution_reason = CASE
                          WHEN :is_resolved THEN 'resolved_by_staff'
                          ELSE NULL
                        END,
                        last_message_at = CASE
                          WHEN :has_response THEN NOW() ELSE last_message_at
                        END,
                        expires_at = CASE
                          WHEN :has_response THEN NOW() + interval '5 days' ELSE expires_at
                        END,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND id = :inquiry_id
                      AND version = :expected_version
                    RETURNING version, updated_at
                    """
                ),
                {
                    "status": next_status,
                    "is_resolved": next_status == "resolved",
                    "has_response": response_note is not None,
                    "assignee_id": (
                        _uuid(str(next_assignee)) if next_assignee is not None else None
                    ),
                    "tenant_id": _uuid(auth.tenant_id),
                    "inquiry_id": _uuid(inquiry_id),
                    "expected_version": expected_version,
                },
            )
            updated = updated_result.mappings().first()
            if updated is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This inquiry changed in another staff session",
                )
            version = _database_integer(updated["version"], "student_inquiry.version")
            if inquiry.get("requirement_id") is not None:
                restore_status = str(inquiry.get("status_before_help") or "in_progress")
                if restore_status == "help_requested":
                    restore_status = "in_progress"
                requirement_status = (
                    "help_requested" if next_status != "resolved" else restore_status
                )
                await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("student_requirement")}
                        SET status = CAST(:status AS varchar),
                            version = version + 1,
                            updated_at = NOW()
                        WHERE tenant_id = :tenant_id
                          AND id = :requirement_id
                          AND status NOT IN (
                            'not_applicable','completed','waived','expired'
                          )
                          AND status IS DISTINCT FROM CAST(:status AS varchar)
                        """
                    ),
                    {
                        "status": requirement_status,
                        "tenant_id": _uuid(auth.tenant_id),
                        "requirement_id": _uuid(str(inquiry["requirement_id"])),
                    },
                )

            notification: dict[str, object] | None = None
            if response_note is not None and notify_student:
                notification = await self._insert_student_message(
                    connection,
                    auth=auth,
                    student_id=str(inquiry["student_id"]),
                    subject=f"Reply: {inquiry['subject']}",
                    body=response_note,
                    href=f"/help?conversation={inquiry_id}",
                )
            reply_id: UUID | None = None
            if response_note is not None:
                reply_id = self._uuid_factory()
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("student_inquiry_reply")} (
                          id, tenant_id, inquiry_id, student_id, staff_member_id,
                          response_note, notify_student, student_message_id, created_at
                        )
                        VALUES (
                          :id, :tenant_id, :inquiry_id, :student_id, :staff_member_id,
                          :response_note, :notify_student, :student_message_id, NOW()
                        )
                        """
                    ),
                    {
                        "id": reply_id,
                        "tenant_id": _uuid(auth.tenant_id),
                        "inquiry_id": _uuid(inquiry_id),
                        "student_id": _uuid(str(inquiry["student_id"])),
                        "staff_member_id": _uuid(auth.actor_id),
                        "response_note": response_note,
                        "notify_student": notify_student,
                        "student_message_id": (
                            _uuid(str(notification["id"])) if notification is not None else None
                        ),
                    },
                )

            work_result = await connection.execute(
                text(
                    f"""
                    SELECT item.id, item.status, item.version
                    FROM {self._table("staff_work_item")} AS item
                    WHERE item.tenant_id = :tenant_id
                      AND (
                        (item.source_type = 'message' AND item.source_id = :inquiry_id)
                        OR EXISTS (
                          SELECT 1
                          FROM {self._table("staff_work_item_link")} AS link
                          WHERE link.tenant_id = item.tenant_id
                            AND link.work_item_id = item.id
                            AND link.entity_type = 'inquiry'
                            AND link.entity_id = :inquiry_id
                        )
                      )
                    ORDER BY item.created_at DESC, item.id DESC
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "inquiry_id": _uuid(inquiry_id),
                },
            )
            work_item = work_result.mappings().first()
            if work_item is not None:
                work_item_id = str(work_item["id"])
                work_status = {
                    "new": "todo",
                    "open": "in_progress",
                    "waiting_on_student": "follow_up_required",
                    "resolved": "done",
                }[next_status]
                await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("staff_work_item")}
                        SET status = CAST(:status AS varchar),
                            assignee_id = :assignee_id,
                            selected_channel = 'portal',
                            started_at = CASE
                              WHEN CAST(:status AS varchar) <> 'todo'
                              THEN COALESCE(started_at, NOW())
                              ELSE started_at
                            END,
                            follow_up_at = CASE
                              WHEN CAST(:status AS varchar) = 'follow_up_required'
                              THEN NOW() + interval '3 days'
                              ELSE NULL
                            END,
                            next_step = CASE
                              WHEN CAST(:status AS varchar) = 'follow_up_required'
                              THEN 'Await the student response and follow up if needed.'
                              WHEN CAST(:status AS varchar) = 'done'
                              THEN 'No further action is required unless the student responds.'
                              ELSE next_step
                            END,
                            outcome_code = CASE
                              WHEN CAST(:status AS varchar) = 'done' THEN 'student_reached'
                              ELSE NULL
                            END,
                            resolution_code = CASE
                              WHEN CAST(:status AS varchar) = 'done' THEN 'resolved_by_staff'
                              ELSE NULL
                            END,
                            completed_at = CASE
                              WHEN CAST(:status AS varchar) = 'done' THEN NOW()
                              ELSE NULL
                            END,
                            cancelled_at = NULL,
                            terminal_reason = NULL,
                            version = version + 1,
                            updated_at = NOW()
                        WHERE tenant_id = :tenant_id AND id = :work_item_id
                        """
                    ),
                    {
                        "status": work_status,
                        "assignee_id": (
                            _uuid(str(next_assignee)) if next_assignee is not None else None
                        ),
                        "tenant_id": _uuid(auth.tenant_id),
                        "work_item_id": _uuid(work_item_id),
                    },
                )
                actor_name = await self._staff_name(connection, auth, auth.actor_id)
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=work_item_id,
                    actor_name=actor_name,
                    action="status_changed",
                    message=f"Inquiry status changed to {work_status.replace('_', ' ')}.",
                )

                interaction_result = await connection.execute(
                    text(
                        f"""
                        SELECT id, source_version
                        FROM {self._table("staff_interaction")}
                        WHERE tenant_id = :tenant_id AND work_item_id = :work_item_id
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                        FOR UPDATE
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "work_item_id": _uuid(work_item_id),
                    },
                )
                interaction = interaction_result.mappings().first()
                if interaction is None:
                    interaction_id = str(self._uuid_factory())
                    source_version = 1
                    await connection.execute(
                        text(
                            f"""
                            INSERT INTO {self._table("staff_interaction")} (
                              id, tenant_id, student_id, work_item_id, objective,
                              status, selected_channel, source_version,
                              covered_source_version, version, quiet_until,
                              last_activity_at, completed_at, created_by,
                              request_key, created_at, updated_at
                            ) VALUES (
                              :id, :tenant_id, :student_id, :work_item_id, :objective,
                              'collecting', 'portal', 0, 0, 1, NOW(), NOW(),
                              NULL, :created_by, :request_key, NOW(), NOW()
                            )
                            """
                        ),
                        {
                            "id": _uuid(interaction_id),
                            "tenant_id": _uuid(auth.tenant_id),
                            "student_id": _uuid(str(inquiry["student_id"])),
                            "work_item_id": _uuid(work_item_id),
                            "objective": str(inquiry["subject"]),
                            "created_by": _uuid(auth.actor_id),
                            "request_key": f"inquiry:{inquiry_id}:staff-sync",
                        },
                    )
                else:
                    interaction_id = str(interaction["id"])
                    source_version = _database_integer(
                        interaction["source_version"],
                        "staff_interaction.source_version",
                    ) + (1 if notification is not None else 0)

                if notification is not None and reply_id is not None:
                    await connection.execute(
                        text(
                            f"""
                            INSERT INTO {self._table("communication_event")} (
                              id, tenant_id, student_id, channel, direction,
                              subject, body_excerpt, metadata, resolution_status,
                              occurred_at, created_at, interaction_id, source_type,
                              source_id, source_sequence, request_key, delivery_status
                            ) VALUES (
                              :id, :tenant_id, :student_id, 'portal', 'outbound',
                              :subject, :body, CAST(:metadata AS jsonb), 'unresolved',
                              NOW(), NOW(), :interaction_id, 'student_inquiry_reply',
                              :source_id, :source_sequence, :request_key, 'delivered'
                            )
                            ON CONFLICT (tenant_id, source_type, source_id)
                            WHERE source_type IS NOT NULL AND source_id IS NOT NULL
                            DO NOTHING
                            """
                        ),
                        {
                            "id": self._uuid_factory(),
                            "tenant_id": _uuid(auth.tenant_id),
                            "student_id": _uuid(str(inquiry["student_id"])),
                            "subject": f"Reply: {inquiry['subject']}",
                            "body": response_note,
                            "metadata": json.dumps(
                                {"inquiryId": inquiry_id, "staffMemberId": auth.actor_id}
                            ),
                            "interaction_id": _uuid(interaction_id),
                            "source_id": reply_id,
                            "source_sequence": source_version,
                            "request_key": f"inquiry-reply:{reply_id}",
                        },
                    )
                    quiet_until = (
                        self._clock()
                        if next_status == "resolved"
                        else self._clock() + timedelta(minutes=5)
                    )
                    await connection.execute(
                        text(
                            f"""
                            UPDATE {self._table("staff_interaction")}
                            SET status = 'enrichment_pending',
                                source_version = :source_version,
                                selected_channel = 'portal',
                                quiet_until = :quiet_until,
                                last_activity_at = NOW(),
                                completed_at = CASE
                                  WHEN :completed THEN NOW() ELSE completed_at
                                END,
                                version = version + 1,
                                updated_at = NOW()
                            WHERE tenant_id = :tenant_id AND id = :interaction_id
                            """
                        ),
                        {
                            "source_version": source_version,
                            "quiet_until": quiet_until,
                            "completed": next_status == "resolved",
                            "tenant_id": _uuid(auth.tenant_id),
                            "interaction_id": _uuid(interaction_id),
                        },
                    )
                    await self._queue_interaction_refresh(
                        connection,
                        auth=auth,
                        interaction_id=interaction_id,
                        work_item_id=work_item_id,
                        student_id=str(inquiry["student_id"]),
                        source_version=source_version,
                        not_before=quiet_until,
                        force=next_status == "resolved",
                    )
                    await self._insert_work_log(
                        connection,
                        auth=auth,
                        work_item_id=work_item_id,
                        actor_name=actor_name,
                        action="communication_recorded",
                        message="Portal reply delivered to the student.",
                    )

            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="student_inquiry.updated_by_staff",
                resource_type="student_inquiry",
                resource_id=inquiry_id,
                metadata={
                    "status": next_status,
                    "assigneeId": _optional_uuid_string(next_assignee),
                    "responseRecorded": response_note is not None,
                    "notifiedStudent": notification is not None,
                    "version": version,
                },
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="student.inquiry_updated_by_staff.v1",
                aggregate_type="student_inquiry",
                aggregate_id=inquiry_id,
                aggregate_version=version,
                data={
                    "studentId": str(inquiry["student_id"]),
                    "status": next_status,
                    "assigneeId": _optional_uuid_string(next_assignee),
                    "responseRecorded": response_note is not None,
                    "notifiedStudent": notification is not None,
                },
            )

        return _map_staff_inquiry(
            inquiry,
            status=next_status,
            assignee=assignee,
            version=version,
            updated_at=updated["updated_at"],
        )

    async def update_student_preferences(
        self,
        auth: AuthContext,
        student_id: str,
        update: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_onboarding_version = _integer(
            _read(update, "expectedOnboardingVersion", "expected_onboarding_version"),
            "expectedOnboardingVersion",
        )
        expected_profile_version = _integer(
            _read(update, "expectedProfileVersion", "expected_profile_version"),
            "expectedProfileVersion",
        )
        communication_preference = str(
            _read(update, "communicationPreference", "communication_preference")
        )
        housing_preference = str(_read(update, "housingPreference", "housing_preference"))
        accommodation_interest = str(
            _read(update, "accommodationInterest", "accommodation_interest")
        )
        residency_verification_path = str(
            _read(update, "residencyVerificationPath", "residency_verification_path")
        )
        notify_student = bool(_read(update, "notifyStudent", "notify_student"))
        note = _optional_text(_read(update, "note", default=None))

        async with self._engine.begin() as connection:
            onboarding_result = await connection.execute(
                text(
                    f"""
                    SELECT payload, version
                    FROM {self._table("student_onboarding")}
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                },
            )
            profile_result = await connection.execute(
                text(
                    f"""
                    SELECT version
                    FROM {self._table("student_profile")}
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                },
            )
            onboarding = onboarding_result.mappings().first()
            profile = profile_result.mappings().first()
            if onboarding is None or profile is None:
                raise NotFoundError("STAFF_STUDENT_NOT_FOUND", "The student was not found")
            if (
                int(onboarding["version"]) != expected_onboarding_version
                or int(profile["version"]) != expected_profile_version
            ):
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "The student record changed in another session",
                )
            payload = _json_object(onboarding["payload"], "student_onboarding.payload")
            payload.update(
                {
                    "communicationPreference": communication_preference,
                    "housingPreference": housing_preference,
                    "accommodationInterest": accommodation_interest,
                    "residencyVerificationPath": residency_verification_path,
                }
            )
            if housing_preference != "on_campus":
                payload.pop("housingResidenceOption", None)
                payload.pop("housingResidencePreferences", None)

            onboarding_update = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("student_onboarding")}
                    SET payload = CAST(:payload AS jsonb),
                        version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND version = :expected_version
                    RETURNING version
                    """
                ),
                {
                    "payload": json.dumps(payload, separators=(",", ":")),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                    "expected_version": expected_onboarding_version,
                },
            )
            profile_update = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("student_profile")}
                    SET communication_preference = :communication_preference,
                        version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND version = :expected_version
                    RETURNING version
                    """
                ),
                {
                    "communication_preference": communication_preference,
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                    "expected_version": expected_profile_version,
                },
            )
            onboarding_row = onboarding_update.mappings().first()
            profile_row = profile_update.mappings().first()
            if onboarding_row is None or profile_row is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "The student record changed in another session",
                )
            onboarding_version = int(onboarding_row["version"])
            profile_version = int(profile_row["version"])
            actor_name = await self._staff_name(connection, auth, auth.actor_id)
            onboarding_items = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_work_item")}
                    SET version = version + 1, updated_at = NOW()
                    WHERE tenant_id = :tenant_id
                      AND student_id = :student_id
                      AND source_type = 'onboarding'
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                },
            )
            for item in onboarding_items.mappings().all():
                await self._insert_work_log(
                    connection,
                    auth=auth,
                    work_item_id=str(item["id"]),
                    actor_name=actor_name,
                    action="student_preferences_updated",
                    message=note or "Updated the student's operational onboarding preferences.",
                )
            if notify_student:
                await self._insert_student_message(
                    connection,
                    auth=auth,
                    student_id=student_id,
                    subject="Your enrollment preferences were updated",
                    body=note
                    or (
                        "Your enrollment team updated your communication, housing, "
                        "and support follow-up preferences. Review your enrollment "
                        "page for the latest details."
                    ),
                )
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="student_preferences.updated_by_staff",
                resource_type="student_onboarding",
                resource_id=student_id,
                metadata={
                    "onboardingVersion": onboarding_version,
                    "profileVersion": profile_version,
                    "notifiedStudent": notify_student,
                },
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="student.preferences_updated_by_staff.v1",
                aggregate_type="student_onboarding",
                aggregate_id=student_id,
                aggregate_version=onboarding_version,
                data={
                    "studentId": student_id,
                    "communicationPreference": communication_preference,
                    "housingPreference": housing_preference,
                    "notifiedStudent": notify_student,
                },
            )
        return await self.get_student_record(auth, student_id)

    async def get_document_review_options(self, auth: AuthContext) -> dict[str, object]:
        self._require_staff(auth)
        async with self._engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('audentra.tenant_id', :tenant, true)"),
                {"tenant": auth.tenant_id},
            )
            result = await connection.execute(
                text(
                    f"""
                    SELECT code, label, description, active
                    FROM {self._table("tenant_document_rejection_reason")}
                    WHERE tenant_id=:tenant_id
                    ORDER BY display_order, code
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            configured = list(result.mappings().all())
            reasons = [
                {
                    "code": str(row["code"]),
                    "label": str(row["label"]),
                    "description": str(row["description"]),
                }
                for row in configured
                if bool(row["active"])
            ]
        return {
            "rejectionReasons": (
                reasons
                if configured
                else [dict(reason) for reason in _DEFAULT_DOCUMENT_REJECTION_REASONS]
            )
        }

    async def _active_document_rejection_reason(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        reason_code: str,
    ) -> dict[str, str]:
        result = await connection.execute(
            text(
                f"""
                SELECT code, label, description, active
                FROM {self._table("tenant_document_rejection_reason")}
                WHERE tenant_id=:tenant_id AND code=:code
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id), "code": reason_code},
        )
        row = result.mappings().first()
        if row is None:
            configured_result = await connection.execute(
                text(
                    f"""
                    SELECT 1
                    FROM {self._table("tenant_document_rejection_reason")}
                    WHERE tenant_id=:tenant_id
                    LIMIT 1
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            fallback = next(
                (
                    reason
                    for reason in _DEFAULT_DOCUMENT_REJECTION_REASONS
                    if reason["code"] == reason_code
                ),
                None,
            )
            if configured_result.mappings().first() is None and fallback is not None:
                return dict(fallback)
        if row is None or not bool(row["active"]):
            raise BadRequestError(
                "DOCUMENT_REJECTION_REASON_INVALID",
                "Choose an active rejection reason for this university",
            )
        return {
            "code": str(row["code"]),
            "label": str(row["label"]),
            "description": str(row["description"]),
        }

    async def review_document(
        self,
        auth: AuthContext,
        document_id: str,
        review: Mapping[str, object],
        request_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        self._require_staff(auth)
        work_item_id = str(_read(review, "workItemId", "work_item_id"))
        expected_version = _integer(
            _read(review, "expectedWorkItemVersion", "expected_work_item_version"),
            "expectedWorkItemVersion",
        )
        decision = str(_read(review, "decision"))
        note = str(_read(review, "note")).strip()
        notification_requested = bool(_read(review, "notifyStudent", "notify_student"))
        reason_value = review.get("reasonCode", review.get("reason_code"))
        reason_code = str(reason_value) if reason_value is not None else None
        internal_value = review.get("internalNote", review.get("internal_note"))
        internal_note = str(internal_value).strip() if internal_value is not None else None
        if decision == "rejected" and reason_code is None:
            raise BadRequestError(
                "DOCUMENT_REJECTION_REASON_REQUIRED",
                "Choose a rejection reason when requesting changes",
            )
        if decision == "accepted" and reason_code is not None:
            raise BadRequestError(
                "DOCUMENT_REJECTION_REASON_NOT_ALLOWED",
                "A rejection reason can be used only when changes are requested",
            )
        normalized_request = {
            "documentId": document_id,
            "workItemId": work_item_id,
            "expectedWorkItemVersion": expected_version,
            "decision": decision,
            "reasonCode": reason_code,
            "note": note,
            "internalNote": internal_note,
            "notifyStudent": notification_requested,
        }

        async def handler(connection: AsyncConnection) -> dict[str, object]:
            await connection.execute(
                text("SELECT set_config('audentra.tenant_id', :tenant, true)"),
                {"tenant": auth.tenant_id},
            )
            reason = (
                await self._active_document_rejection_reason(
                    connection, auth, cast(str, reason_code)
                )
                if decision == "rejected"
                else None
            )
            work_item = await self._lock_work_item(connection, auth, work_item_id)
            if work_item["source_type"] != "document" or str(work_item["source_id"]) != document_id:
                raise NotFoundError(
                    "STAFF_WORK_ITEM_NOT_FOUND",
                    "The document review work item was not found",
                )
            if (
                _database_integer(work_item["version"], "staff_work_item.version")
                != expected_version
            ):
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This document review changed in another staff session",
                )
            document_result = await connection.execute(
                text(
                    f"""
                    SELECT id, student_id, requirement_id, file_name, status
                    FROM {self._table("document_record")}
                    WHERE tenant_id=:tenant_id AND id=:document_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "document_id": _uuid(document_id),
                },
            )
            document = document_result.mappings().first()
            if document is None:
                raise NotFoundError("STAFF_DOCUMENT_NOT_FOUND", "The document was not found")
            if document["status"] not in {"needs_review", "under_review"}:
                raise ConflictError(
                    "DOCUMENT_REVIEW_ALREADY_DECIDED",
                    "This document already has an official staff decision",
                )
            await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("document_record")}
                    SET status=:decision, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:document_id
                    """
                ),
                {
                    "decision": decision,
                    "tenant_id": _uuid(auth.tenant_id),
                    "document_id": _uuid(document_id),
                },
            )
            requirement_id = document["requirement_id"]
            if requirement_id is not None:
                requirement_status = "completed" if decision == "accepted" else "rejected"
                if self._university is not None and self._university.is_enabled(auth):
                    evidence_status = await connection.scalar(
                        text("SELECT university.required_document_status(:tenant,:requirement)"),
                        {
                            "tenant": _uuid(auth.tenant_id),
                            "requirement": _uuid(str(requirement_id)),
                        },
                    )
                    if evidence_status is not None:
                        requirement_status = str(evidence_status)
                progress = 100 if requirement_status == "completed" else 60
                await connection.execute(
                    text(
                        f"""
                        UPDATE {self._table("student_requirement")}
                        SET status=:status, progress_percent=:progress,
                            version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND id=:requirement_id
                        """
                    ),
                    {
                        "status": requirement_status,
                        "progress": progress,
                        "tenant_id": _uuid(auth.tenant_id),
                        "requirement_id": _uuid(str(requirement_id)),
                    },
                )
                if requirement_status == "completed":
                    await self._award_requirement_rewards(
                        connection,
                        auth=auth,
                        student_id=str(document["student_id"]),
                        requirement_id=str(requirement_id),
                    )
                    await self._refresh_requirement_dependencies(
                        connection, auth, str(requirement_id)
                    )
            item_update = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("staff_work_item")}
                    SET status='done', version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:work_item_id
                      AND version=:expected_version
                    RETURNING version
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "work_item_id": _uuid(work_item_id),
                    "expected_version": expected_version,
                },
            )
            item_row = item_update.mappings().first()
            if item_row is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This document review changed in another staff session",
                )
            item_version = int(item_row["version"])
            actor_name = await self._staff_name(connection, auth, auth.actor_id)
            verb = "Accepted" if decision == "accepted" else "Requested changes to"
            await self._insert_work_log(
                connection,
                auth=auth,
                work_item_id=work_item_id,
                actor_name=actor_name,
                action="document_decided",
                message=f"{verb} {document['file_name']}: {internal_note or note}",
            )
            decision_id = str(self._uuid_factory())
            decided_at = self._clock()
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("document_review_decision")} (
                      id, tenant_id, document_id, student_id, requirement_id,
                      work_item_id, reviewer_id, reviewer_display_name, decision,
                      reason_code, reason_label, student_message, internal_note,
                      source, decided_at, created_at
                    ) VALUES (
                      :id, :tenant_id, :document_id, :student_id, :requirement_id,
                      :work_item_id, :reviewer_id, :reviewer_name, :decision,
                      :reason_code, :reason_label, :student_message, :internal_note,
                      'staff_review', :decided_at, NOW()
                    )
                    """
                ),
                {
                    "id": _uuid(decision_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "document_id": _uuid(document_id),
                    "student_id": _uuid(str(document["student_id"])),
                    "requirement_id": (
                        _uuid(str(requirement_id)) if requirement_id is not None else None
                    ),
                    "work_item_id": _uuid(work_item_id),
                    "reviewer_id": _uuid(auth.actor_id),
                    "reviewer_name": actor_name,
                    "decision": decision,
                    "reason_code": reason_code,
                    "reason_label": reason["label"] if reason is not None else None,
                    "student_message": note,
                    "internal_note": internal_note,
                    "decided_at": decided_at,
                },
            )
            public_decision = public_review_decision(
                {
                    "id": decision_id,
                    "decision": decision,
                    "decided_at": decided_at,
                    "reason_code": reason_code,
                    "reason_label": reason["label"] if reason else None,
                    "student_message": note,
                    "reviewer_display_name": actor_name,
                    "source": "staff_review",
                }
            )
            notification = await self._insert_student_message(
                connection,
                auth=auth,
                student_id=str(document["student_id"]),
                subject=bounded_document_label(
                    document["file_name"],
                    suffix=(" was accepted" if decision == "accepted" else " needs changes"),
                ),
                body=note
                if notification_requested
                else (
                    "Your document was reviewed and accepted."
                    if decision == "accepted"
                    else "Your document was reviewed and needs changes. Open Documents for details."
                ),
                kind="document_review",
                href=f"/documents?document={document_id}",
            )
            await self._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action="student_document.decided_by_staff",
                resource_type="document_record",
                resource_id=document_id,
                metadata={
                    "decisionId": decision_id,
                    "decision": decision,
                    "reasonCode": reason_code,
                    "workItemId": work_item_id,
                    "notifiedStudent": notification is not None,
                    "notificationRequested": notification_requested,
                },
            )
            await self._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="student.document_decided_by_staff.v1",
                aggregate_type="document_record",
                aggregate_id=document_id,
                aggregate_version=item_version,
                data={
                    "decisionId": decision_id,
                    "documentId": document_id,
                    "studentId": str(document["student_id"]),
                    "decision": decision,
                    "reasonCode": reason_code,
                    "workItemId": work_item_id,
                    "notifiedStudent": notification is not None,
                    "notificationRequested": notification_requested,
                },
            )
            return {
                "workItemId": work_item_id,
                "notification": notification,
                "decision": public_decision,
            }

        receipt = await self._run_idempotent(
            auth=auth,
            idempotency_key=idempotency_key or request_id,
            operation="staff.document.review",
            request_payload=normalized_request,
            response_status=201,
            handler=handler,
        )
        current_work_item = await self._require_work_item(auth, work_item_id)
        student = cast(Mapping[str, object], current_work_item["student"])
        student_auth = replace(auth, student_id=str(student["id"]))
        documents = dict(await self._reader.get_student_documents(student_auth))
        document_items = documents.get("items")
        if not isinstance(document_items, list):
            document_items = []
        decided_document = next(
            (
                candidate
                for candidate in document_items
                if isinstance(candidate, dict) and candidate.get("id") == document_id
            ),
            None,
        )
        if decided_document is None:
            raise ApiError(
                500,
                "STAFF_DOCUMENT_DECISION_INCONSISTENT",
                "The document decision committed but the document could not be reloaded",
            )
        return {
            "document": decided_document,
            "workItem": current_work_item,
            "notification": receipt.get("notification"),
            "decision": receipt.get("decision"),
        }

    async def _ensure_document_work_items(self, auth: AuthContext) -> None:
        async with self._engine.connect() as connection:
            pending_result = await connection.execute(
                text(
                    f"""
                    SELECT document.id, document.student_id,
                           document.file_name, document.category
                    FROM {self._table("document_record")} AS document
                    WHERE document.tenant_id = :tenant_id
                      AND document.status IN ('needs_review', 'under_review')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM {self._table("staff_work_item")} AS item
                        WHERE item.tenant_id = document.tenant_id
                          AND item.source_type = 'document'
                          AND item.source_id = document.id
                      )
                    ORDER BY document.created_at, document.id
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            pending = [dict(row) for row in pending_result.mappings().all()]

        for document in pending:
            component, priority = _document_route(str(document["category"]))
            async with self._engine.begin() as connection:
                assignee_result = await connection.execute(
                    text(
                        f"""
                        SELECT id
                        FROM {self._table("staff_member")}
                        WHERE tenant_id = :tenant_id AND active = true
                        ORDER BY
                          CASE WHEN component = :component THEN 0 ELSE 1 END,
                          display_name,
                          id
                        LIMIT 1
                        """
                    ),
                    {"tenant_id": _uuid(auth.tenant_id), "component": component},
                )
                assignee = assignee_result.mappings().first()
                assignee_id = assignee["id"] if assignee is not None else None
                work_item_id = self._uuid_factory()
                document_id = str(document["id"])
                inserted = await connection.execute(
                    text(
                        f"""
                        INSERT INTO {self._table("staff_work_item")} (
                          id, tenant_id, student_id, key, title, description,
                          status, priority, work_type, component, due_at,
                          escalated, assignee_id, source_type, source_id, version
                        )
                        VALUES (
                          :id, :tenant_id, :student_id, :key, :title,
                          'Verify the stored original and make the official staff decision.',
                          'todo', :priority, 'document_review', :component,
                          NOW() + INTERVAL '2 days', false, :assignee_id,
                          'document', :document_id, 1
                        )
                        ON CONFLICT (tenant_id, source_type, source_id)
                        WHERE source_type IS NOT NULL AND source_id IS NOT NULL
                        DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "id": work_item_id,
                        "tenant_id": _uuid(auth.tenant_id),
                        "student_id": _uuid(str(document["student_id"])),
                        "key": f"DOC-{document_id.replace('-', '')[:8].upper()}",
                        "title": bounded_document_label(document["file_name"], prefix="Review "),
                        "priority": priority,
                        "component": component,
                        "assignee_id": assignee_id,
                        "document_id": _uuid(document_id),
                    },
                )
                if inserted.mappings().first() is not None:
                    await self._insert_work_log(
                        connection,
                        auth=auth,
                        work_item_id=str(work_item_id),
                        actor_name="VV workflow",
                        actor_type="system",
                        action="created",
                        message="Created when the student document entered staff review.",
                    )

    async def _student_summary(
        self,
        tenant_id: str,
        student_id: str,
    ) -> dict[str, object] | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(self._student_summary_sql()),
                {"tenant_id": _uuid(tenant_id), "student_id": _uuid(student_id)},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "name": f"{row['first_name']} {row['last_name']}",
            "preferredName": str(row["preferred_name"]),
            "programName": str(row["program_name"]),
            "classYear": int(row["class_year"]),
        }

    def _student_summary_sql(self) -> str:
        student = self._table("student")
        person = self._table("person")
        profile = self._table("student_profile")
        offer = self._table("admission_offer")
        program = self._table("program")
        return f"""
            SELECT student.id, person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name,
              COALESCE(onboarding.payload->>'universityProgramName',
                offer_program.name, 'Program not assigned') AS program_name,
              student.class_year
            FROM {student} AS student
            JOIN {person} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {profile} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            LEFT JOIN {self._table("student_onboarding")} AS onboarding
              ON onboarding.student_id = student.id AND onboarding.tenant_id = student.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name
              FROM {offer} AS offer
              JOIN {program} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = student.tenant_id
                AND offer.student_id = student.id
              ORDER BY offer.created_at DESC
              LIMIT 1
            ) AS offer_program ON true
            WHERE student.tenant_id = :tenant_id AND student.id = :student_id
        """

    def _student_roster_sql(self, *, by_student: bool = False, by_query: bool = False) -> str:
        student = self._table("student")
        person = self._table("person")
        profile = self._table("student_profile")
        onboarding = self._table("student_onboarding")
        offer = self._table("admission_offer")
        program = self._table("program")
        term = self._table("academic_term")
        campus = self._table("campus")
        journey = self._table("enrollment_journey")
        requirement = self._table("student_requirement")
        definition = self._table("requirement_definition_version")
        work_item = self._table("staff_work_item")
        assignment = self._table("student_staff_assignment")
        member = self._table("staff_member")
        open_statuses = ", ".join(f"'{status}'" for status in OPEN_WORK_STATUSES)
        filters = ["student.tenant_id = :tenant_id"]
        if by_student:
            filters.append("student.id = :student_id")
        if by_query:
            filters.append(
                """(
                  person.first_name ILIKE :pattern ESCAPE '\\'
                  OR person.last_name ILIKE :pattern ESCAPE '\\'
                  OR (person.first_name || ' ' || person.last_name) ILIKE :pattern ESCAPE '\\'
                  OR COALESCE(profile.preferred_name, person.preferred_name, '')
                    ILIKE :pattern ESCAPE '\\'
                  OR COALESCE(student.external_ref, '') ILIKE :pattern ESCAPE '\\'
                  OR COALESCE(onboarding.payload->>'universityProgramName', offer_program.name, '')
                    ILIKE :pattern ESCAPE '\\'
                )"""
            )
        where = "\n              AND ".join(filters)
        return f"""
            SELECT student.id, student.external_ref, person.first_name, person.last_name,
              COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                AS preferred_name,
              COALESCE(onboarding.payload->>'universityProgramName',
                offer_program.name, 'Program not assigned') AS program_name,
              offer_program.term_name,
              offer_program.campus_name,
              student.class_year,
              COALESCE(onboarding.status, 'not_started') AS onboarding_status,
              COALESCE(onboarding.current_step, 'offer') AS onboarding_step,
              COALESCE(cardinality(onboarding.completed_steps), 0) AS onboarding_completed,
              COALESCE(requirement_progress.completed_count, 0) AS requirement_completed,
              COALESCE(requirement_progress.total_count, 0) AS requirement_total,
              COALESCE(requirement_progress.overdue_count, 0) AS requirement_overdue,
              COALESCE(requirement_progress.blocking_open_count, 0)
                AS requirement_blocking_open,
              COALESCE(work_summary.open_count, 0) AS work_open,
              COALESCE(work_summary.overdue_count, 0) AS work_overdue,
              COALESCE(work_summary.escalated_count, 0) AS work_escalated,
              COALESCE(work_summary.owners, '[]'::jsonb) AS work_owners,
              primary_adviser.id AS primary_adviser_id,
              primary_adviser.display_name AS primary_adviser_name,
              viewer_caseload.roles AS viewer_roles,
              GREATEST(
                student.updated_at,
                COALESCE(profile.updated_at, student.updated_at),
                COALESCE(onboarding.updated_at, student.updated_at)
              ) AS last_activity_at,
              next_work.id AS work_item_id,
              next_work.title AS work_title,
              next_work.description AS work_description,
              next_work.assignee_id,
              next_work.selected_channel,
              next_work.priority AS work_priority,
              COUNT(*) OVER () AS match_count
            FROM {student} AS student
            JOIN {person} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {profile} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
            LEFT JOIN {onboarding} AS onboarding
              ON onboarding.student_id = student.id AND onboarding.tenant_id = student.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name, term.name AS term_name, campus.name AS campus_name
              FROM {offer} AS offer
              JOIN {program} AS program
                ON program.id = offer.program_id AND program.tenant_id = offer.tenant_id
              LEFT JOIN {term} AS term
                ON term.id = offer.academic_term_id AND term.tenant_id = offer.tenant_id
              LEFT JOIN {campus} AS campus
                ON campus.id = offer.campus_id AND campus.tenant_id = offer.tenant_id
              WHERE offer.tenant_id = student.tenant_id
                AND offer.student_id = student.id
              ORDER BY offer.created_at DESC
              LIMIT 1
            ) AS offer_program ON true
            LEFT JOIN LATERAL (
              SELECT
                COUNT(requirement.id)::integer AS total_count,
                COUNT(requirement.id) FILTER (
                  WHERE requirement.status IN ('completed', 'waived', 'not_applicable')
                )::integer AS completed_count,
                COUNT(requirement.id) FILTER (
                  WHERE requirement.status NOT IN ('completed', 'waived', 'not_applicable')
                    AND requirement.due_at IS NOT NULL AND requirement.due_at < :now
                )::integer AS overdue_count,
                COUNT(requirement.id) FILTER (
                  WHERE requirement.status NOT IN ('completed', 'waived', 'not_applicable')
                    AND definition.blocking = 1
                )::integer AS blocking_open_count
              FROM {journey} AS journey
              LEFT JOIN {requirement} AS requirement
                ON requirement.tenant_id = journey.tenant_id
               AND requirement.journey_id = journey.id
               AND requirement.retired_at IS NULL
              LEFT JOIN {definition} AS definition
                ON definition.id = requirement.requirement_definition_version_id
               AND definition.tenant_id = requirement.tenant_id
              WHERE journey.tenant_id = student.tenant_id
                AND journey.student_id = student.id
            ) AS requirement_progress ON true
            LEFT JOIN LATERAL (
              SELECT
                COUNT(*)::integer AS open_count,
                COUNT(*) FILTER (
                  WHERE item.due_at IS NOT NULL AND item.due_at < :now
                )::integer AS overdue_count,
                COUNT(*) FILTER (WHERE item.escalated)::integer AS escalated_count,
                jsonb_agg(DISTINCT jsonb_build_object(
                  'id', owner.id, 'name', owner.display_name, 'component', owner.component
                )) FILTER (WHERE owner.id IS NOT NULL) AS owners
              FROM {work_item} AS item
              LEFT JOIN {member} AS owner
                ON owner.id = item.assignee_id AND owner.tenant_id = item.tenant_id
              WHERE item.tenant_id = student.tenant_id
                AND item.student_id = student.id
                AND item.status IN ({open_statuses})
            ) AS work_summary ON true
            LEFT JOIN LATERAL (
              SELECT array_agg(assignment.role ORDER BY assignment.role) AS roles
              FROM {assignment} AS assignment
              WHERE assignment.tenant_id = student.tenant_id
                AND assignment.student_id = student.id
                AND assignment.staff_member_id = :viewer_id
                AND assignment.ended_at IS NULL
            ) AS viewer_caseload ON true
            LEFT JOIN LATERAL (
              SELECT member.id, member.display_name
              FROM {assignment} AS assignment
              JOIN {member} AS member
                ON member.id = assignment.staff_member_id
               AND member.tenant_id = assignment.tenant_id
              WHERE assignment.tenant_id = student.tenant_id
                AND assignment.student_id = student.id
                AND assignment.role = 'primary_advisor'
                AND assignment.ended_at IS NULL
              ORDER BY assignment.assigned_at DESC
              LIMIT 1
            ) AS primary_adviser ON true
            LEFT JOIN LATERAL (
              SELECT item.id, item.title, item.description, item.assignee_id,
                     item.selected_channel, item.priority
              FROM {work_item} AS item
              WHERE item.tenant_id = student.tenant_id
                AND item.student_id = student.id
                AND item.status NOT IN ('done', 'cancelled')
              ORDER BY
                CASE item.priority
                  WHEN 'urgent' THEN 1
                  WHEN 'high' THEN 2
                  WHEN 'medium' THEN 3
                  ELSE 4
                END,
                item.due_at NULLS LAST,
                item.updated_at DESC,
                item.id
              LIMIT 1
            ) AS next_work ON true
            WHERE {where}
            ORDER BY last_activity_at DESC, student.id
            LIMIT :limit
        """

    def _map_student_operation(
        self,
        row: Mapping[str, object],
        current_staff_id: str,
    ) -> dict[str, object]:
        onboarding_status = str(row["onboarding_status"])
        onboarding_completed = _database_integer(
            row["onboarding_completed"], "student_onboarding.completed_steps"
        )
        requirement_completed = _database_integer(
            row["requirement_completed"], "student_requirement.completed_count"
        )
        requirement_total = _database_integer(
            row["requirement_total"], "student_requirement.total_count"
        )
        if onboarding_status != "completed":
            stage = "Onboarding"
            completed_tasks = onboarding_completed
            total_tasks = 8
        else:
            completed_tasks = requirement_completed
            total_tasks = requirement_total
            stage = (
                "Ready"
                if requirement_total > 0 and requirement_completed >= requirement_total
                else "Enrollment"
            )

        work_item_id = row.get("work_item_id")
        assigned_staff_id = str(row.get("assignee_id") or current_staff_id)
        channel = str(row.get("selected_channel") or "portal")
        if channel not in _COMMUNICATION_CHANNELS:
            channel = "portal"
        if work_item_id is None:
            recommended_action: dict[str, object] = {
                "title": "No staff action required",
                "rationale": "The student has no open Action Center work.",
                "channel": "portal",
                "expectedImpact": "Continue monitoring canonical journey progress",
                "taskId": None,
                "recommendedToday": False,
            }
        else:
            recommended_action = {
                "title": str(row.get("work_title") or "Review the open student action"),
                "rationale": str(
                    row.get("work_description") or "Resolve the linked Action Center work item."
                ),
                "channel": channel,
                "expectedImpact": "Resolve the linked Action Center work item",
                "taskId": str(work_item_id),
                "recommendedToday": row.get("assignee_id") is not None,
            }

        overdue_requirements = _database_integer(
            row.get("requirement_overdue", 0), "student_requirement.overdue_count"
        )
        blocking_open = _database_integer(
            row.get("requirement_blocking_open", 0), "student_requirement.blocking_open_count"
        )
        work_open = _database_integer(row.get("work_open", 0), "staff_work_item.open_count")
        work_overdue = _database_integer(
            row.get("work_overdue", 0), "staff_work_item.overdue_count"
        )
        work_escalated = _database_integer(
            row.get("work_escalated", 0), "staff_work_item.escalated_count"
        )
        primary_adviser_id = row.get("primary_adviser_id")
        primary_adviser = (
            {"id": str(primary_adviser_id), "name": str(row.get("primary_adviser_name") or "")}
            if primary_adviser_id is not None
            else None
        )
        external_ref = row.get("external_ref")
        term_name = row.get("term_name")
        campus_name = row.get("campus_name")

        return {
            "id": str(row["id"]),
            "name": f"{row['first_name']} {row['last_name']}",
            "preferredName": str(row["preferred_name"]),
            "externalRef": str(external_ref) if external_ref is not None else None,
            "programName": str(row["program_name"]),
            "termName": str(term_name) if term_name is not None else None,
            "campusName": str(campus_name) if campus_name is not None else None,
            "classYear": _database_integer(row["class_year"], "student.class_year"),
            "assignedStaffId": assigned_staff_id,
            "primaryAdviser": primary_adviser,
            # Owners of the student's open work, distinct, from the same
            # aggregate that counts it; and the reader's own roles for them.
            "openWorkOwners": [
                {
                    "id": str(owner["id"]),
                    "name": str(owner.get("name") or ""),
                    "component": str(owner.get("component") or ""),
                }
                for owner in _json_list(row.get("work_owners") or [], "student.work_owners")
                if isinstance(owner, Mapping) and owner.get("id") is not None
            ],
            "viewerAssignmentRoles": _text_list(row.get("viewer_roles")),
            "syntheticSeed": False,
            "journey": {
                "stage": stage,
                "completedTasks": completed_tasks,
                "totalTasks": total_tasks,
                "lastActivityAt": _iso_timestamp(row["last_activity_at"]),
            },
            "openWorkItems": work_open,
            "overdueWorkItems": work_overdue,
            "attention": student_attention(
                overdue_requirements=overdue_requirements,
                blocking_requirements_open=blocking_open,
                overdue_work=work_overdue,
                escalated_work=work_escalated,
                has_primary_adviser=primary_adviser is not None,
                offer_accepted=onboarding_status == "completed" or requirement_total > 0,
                evaluated_at=_iso_timestamp(self._clock()),
            ),
            "recommendedAction": recommended_action,
            "communicationHistory": [],
        }

    def _map_work_item(
        self,
        item: Mapping[str, object],
        logs: list[dict[str, object]],
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        moment = now or self._clock()
        assignee = None
        owner_risk = None
        if all(
            item.get(key) is not None
            for key in (
                "assignee_id",
                "assignee_name",
                "assignee_email",
                "assignee_component",
            )
        ):
            away_until = item.get("assignee_away_until")
            assignee = {
                "id": str(item["assignee_id"]),
                "name": str(item["assignee_name"]),
                "email": str(item["assignee_email"]),
                "component": str(item["assignee_component"]),
                "title": (
                    str(item["assignee_title"]) if item.get("assignee_title") is not None else None
                ),
                "employmentStatus": str(item.get("assignee_employment_status") or "active"),
                "leaveUntil": (
                    str(item["assignee_leave_until"])
                    if item.get("assignee_leave_until") is not None
                    else None
                ),
                "awayUntil": _iso_timestamp(away_until) if away_until is not None else None,
                "awayKind": (
                    str(item["assignee_away_kind"])
                    if item.get("assignee_away_kind") is not None
                    else None
                ),
            }
            owner_risk = owner_risk_for(
                item.get("assignee_employment_status"), away_until=away_until
            )
        source = None
        if item.get("source_type") is not None and item.get("source_id") is not None:
            source = {"type": str(item["source_type"]), "id": str(item["source_id"])}
        history = [
            {
                "id": str(log["id"]),
                "action": str(log["action"]),
                "message": str(log["message"]),
                "actorName": str(log["actor_name"]),
                "occurredAt": _iso_timestamp(log["occurred_at"]),
            }
            for log in logs
            if str(log["work_item_id"]) == str(item["id"])
        ]
        mapped: dict[str, object] = {
            "id": str(item["id"]),
            "key": str(item["key"]),
            "title": str(item["title"]),
            "description": str(item["description"]),
            "status": str(item["status"]),
            "priority": str(item["priority"]),
            "type": str(item["work_type"]),
            "actionType": str(item["action_type"]),
            "component": str(item["component"]),
            "dueAt": (_iso_timestamp(item["due_at"]) if item.get("due_at") is not None else None),
            "escalated": bool(item["escalated"]),
            "selectedChannel": (
                str(item["selected_channel"]) if item.get("selected_channel") is not None else None
            ),
            "attemptCount": int(cast(int, item["attempt_count"])),
            "followUpAt": (
                _iso_timestamp(item["follow_up_at"])
                if item.get("follow_up_at") is not None
                else None
            ),
            "blocker": (
                {
                    "code": str(item["blocker_code"]),
                    "detail": str(item["blocker_detail"]),
                    "reviewAt": (
                        _iso_timestamp(item["blocker_review_at"])
                        if item.get("blocker_review_at") is not None
                        else None
                    ),
                }
                if item.get("blocker_code") is not None and item.get("blocker_detail") is not None
                else None
            ),
            "outcomeCode": (
                str(item["outcome_code"]) if item.get("outcome_code") is not None else None
            ),
            "resolutionCode": (
                str(item["resolution_code"]) if item.get("resolution_code") is not None else None
            ),
            "nextStep": str(item["next_step"]) if item.get("next_step") is not None else None,
            "terminalReason": (
                str(item["terminal_reason"]) if item.get("terminal_reason") is not None else None
            ),
            "startedAt": (
                _iso_timestamp(item["started_at"]) if item.get("started_at") is not None else None
            ),
            "interactionCompletedAt": (
                _iso_timestamp(item["interaction_completed_at"])
                if item.get("interaction_completed_at") is not None
                else None
            ),
            "completedAt": (
                _iso_timestamp(item["completed_at"])
                if item.get("completed_at") is not None
                else None
            ),
            "cancelledAt": (
                _iso_timestamp(item["cancelled_at"])
                if item.get("cancelled_at") is not None
                else None
            ),
            "version": int(cast(int, item["version"])),
            "createdAt": _iso_timestamp(item["created_at"]),
            "updatedAt": _iso_timestamp(item["updated_at"]),
            "assignee": assignee,
            "student": {
                "id": str(item["student_id"]),
                "name": f"{item['first_name']} {item['last_name']}",
                "preferredName": str(item["preferred_name"]),
                "programName": str(item["program_name"]),
                "classYear": int(cast(int, item["class_year"])),
            },
            "source": source,
            "history": history,
            # The reader's current caseload roles for this student, e.g.
            # ["financial_aid_counselor"]; empty when the student is not theirs.
            "viewerAssignmentRoles": _text_list(item.get("viewer_roles")),
        }
        mapped["signals"] = derive_signals(mapped, now=moment, owner_risk=owner_risk)
        return mapped

    async def _require_work_item(
        self,
        auth: AuthContext,
        work_item_id: str,
    ) -> dict[str, object]:
        await self._ensure_document_work_items(auth)
        item = await self._read_work_item(auth, work_item_id)
        if item is None:
            raise NotFoundError("STAFF_WORK_ITEM_NOT_FOUND", "The work item was not found")
        return item

    async def _lock_work_item(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        work_item_id: str,
    ) -> dict[str, object]:
        result = await connection.execute(
            text(
                f"""
                SELECT id, key, title, student_id, status, assignee_id, escalated, version,
                       source_type, source_id, work_type, selected_channel,
                       follow_up_at, blocker_code, blocker_detail, blocker_review_at,
                       outcome_code, resolution_code, next_step, terminal_reason,
                       started_at, interaction_completed_at, completed_at, cancelled_at
                FROM {self._table("staff_work_item")}
                WHERE tenant_id = :tenant_id AND id = :work_item_id
                FOR UPDATE
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "work_item_id": _uuid(work_item_id),
            },
        )
        item = result.mappings().first()
        if item is None:
            raise NotFoundError("STAFF_WORK_ITEM_NOT_FOUND", "The work item was not found")
        return dict(item)

    async def _lock_interaction(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        interaction_id: str,
    ) -> dict[str, object]:
        result = await connection.execute(
            text(
                f"""
                SELECT id, student_id, work_item_id, status, selected_channel,
                       source_version, covered_source_version, version,
                       quiet_until, last_activity_at, completed_at
                FROM {self._table("staff_interaction")}
                WHERE tenant_id = :tenant_id AND id = :interaction_id
                FOR UPDATE
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "interaction_id": _uuid(interaction_id),
            },
        )
        interaction = result.mappings().first()
        if interaction is None:
            raise NotFoundError(
                "STAFF_INTERACTION_NOT_FOUND",
                "The interaction was not found",
            )
        return dict(interaction)

    async def _lock_call_recording(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        recording_id: str,
    ) -> dict[str, object]:
        result = await connection.execute(
            text(
                f"""
                SELECT id, interaction_id, work_item_id, student_id, status,
                       storage_key, sha256, attempts, version
                FROM {self._table("staff_call_recording")}
                WHERE tenant_id = :tenant_id AND id = :recording_id
                FOR UPDATE
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "recording_id": _uuid(recording_id),
            },
        )
        recording = result.mappings().first()
        if recording is None:
            raise NotFoundError(
                "STAFF_CALL_RECORDING_NOT_FOUND",
                "The call recording was not found",
            )
        return dict(recording)

    def _require_active_inquiry_conversation(self, inquiry: Mapping[str, object]) -> None:
        if inquiry.get("archived_at") is not None or str(inquiry["status"]) == "archived":
            raise ConflictError(
                "SUPPORT_CONVERSATION_EXPIRED",
                (
                    "This conversation was archived after five days without messages. "
                    "Create a new student action to continue."
                ),
            )
        expires_at = inquiry.get("expires_at")
        if isinstance(expires_at, datetime) and expires_at <= self._clock():
            raise ConflictError(
                "SUPPORT_CONVERSATION_EXPIRED",
                (
                    "This conversation was archived after five days without messages. "
                    "Create a new student action to continue."
                ),
            )

    async def _lock_linked_inquiry_for_work_item(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        work_item_id: str,
    ) -> dict[str, object] | None:
        """Find a support thread related to an Action Center work item.

        Help-request work items have a legacy ``message`` source and a durable
        work-item link.  Supporting both makes a portal message recorded in the
        Action Center land in the same student/staff conversation rather than
        creating a disconnected inbox record.
        """

        result = await connection.execute(
            text(
                f"""
                SELECT inquiry.id, inquiry.student_id, inquiry.subject,
                       inquiry.status, inquiry.last_message_at, inquiry.expires_at,
                       inquiry.archived_at
                FROM {self._table("student_inquiry")} AS inquiry
                WHERE inquiry.tenant_id = :tenant_id
                  AND (
                    EXISTS (
                      SELECT 1
                      FROM {self._table("staff_work_item")} AS item
                      WHERE item.tenant_id = inquiry.tenant_id
                        AND item.id = :work_item_id
                        AND item.source_type = 'message'
                        AND item.source_id = inquiry.id
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM {self._table("staff_work_item_link")} AS link
                      WHERE link.tenant_id = inquiry.tenant_id
                        AND link.work_item_id = :work_item_id
                        AND link.entity_type = 'inquiry'
                        AND link.entity_id = inquiry.id
                    )
                  )
                ORDER BY inquiry.created_at, inquiry.id
                LIMIT 1
                FOR UPDATE OF inquiry
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "work_item_id": _uuid(work_item_id),
            },
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _lock_inquiry(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        inquiry_id: str,
    ) -> dict[str, object] | None:
        result = await connection.execute(
            text(
                f"""
                SELECT inquiry.id, inquiry.student_id, inquiry.topic_code,
                       inquiry.subject, inquiry.message, inquiry.status,
                       inquiry.priority, inquiry.assignee_id,
                       inquiry.requirement_id, inquiry.status_before_help,
                       inquiry.version, inquiry.last_message_at, inquiry.expires_at,
                       inquiry.archived_at,
                       inquiry.created_at, inquiry.updated_at, student.class_year,
                       person.first_name, person.last_name,
                       COALESCE(profile.preferred_name, person.preferred_name,
                                person.first_name) AS preferred_name,
                       COALESCE(latest_program.name, 'Program not assigned') AS program_name
                FROM {self._table("student_inquiry")} AS inquiry
                JOIN {self._table("student")} AS student
                  ON student.id = inquiry.student_id
                 AND student.tenant_id = inquiry.tenant_id
                JOIN {self._table("person")} AS person
                  ON person.id = student.person_id
                 AND person.tenant_id = student.tenant_id
                LEFT JOIN {self._table("student_profile")} AS profile
                  ON profile.student_id = student.id
                 AND profile.tenant_id = student.tenant_id
                LEFT JOIN LATERAL (
                  SELECT program.name
                  FROM {self._table("admission_offer")} AS offer
                  JOIN {self._table("program")} AS program
                    ON program.id = offer.program_id
                   AND program.tenant_id = offer.tenant_id
                  WHERE offer.tenant_id = inquiry.tenant_id
                    AND offer.student_id = inquiry.student_id
                  ORDER BY offer.created_at DESC, offer.id DESC
                  LIMIT 1
                ) AS latest_program ON true
                WHERE inquiry.tenant_id = :tenant_id
                  AND inquiry.id = :inquiry_id
                FOR UPDATE OF inquiry
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "inquiry_id": _uuid(inquiry_id),
            },
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _active_staff_summary(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        staff_id: str,
    ) -> dict[str, object] | None:
        result = await connection.execute(
            text(
                f"""
                SELECT id, display_name, email_normalized, component
                FROM {self._table("staff_member")}
                WHERE tenant_id = :tenant_id
                  AND id = :staff_id
                  AND active = true
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "staff_id": _uuid(staff_id),
            },
        )
        row = result.mappings().first()
        if row is None:
            return None
        return {
            "id": str(row["id"]),
            "name": str(row["display_name"]),
            "email": str(row["email_normalized"]),
            "component": str(row["component"]),
        }

    async def _staff_name(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        staff_id: str,
    ) -> str:
        result = await connection.execute(
            text(
                f"""
                SELECT display_name
                FROM {self._table("staff_member")}
                WHERE tenant_id = :tenant_id
                  AND id = :staff_id
                  AND active = true
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id), "staff_id": _uuid(staff_id)},
        )
        member = result.mappings().first()
        if member is None:
            raise ApiError(
                403,
                "STAFF_IDENTITY_NOT_CONFIGURED",
                "The staff identity is not configured for this university",
            )
        return str(member["display_name"])

    async def _queue_task_insight_refresh(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        work_item_id: str,
        student_id: str,
        source_version: int,
        not_before: datetime,
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("action_center_ai_job")} (
                  id, tenant_id, purpose, dedupe_key, student_id, work_item_id,
                  status, requested_source_version, covered_source_version,
                  not_before, attempts, max_attempts, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, 'task_insight', :dedupe_key, :student_id,
                  :work_item_id, 'pending', :source_version, 0, :not_before,
                  0, 5, NOW(), NOW()
                )
                ON CONFLICT (tenant_id, purpose, dedupe_key)
                DO UPDATE SET
                  requested_source_version = GREATEST(
                    {self._table("action_center_ai_job")}.requested_source_version,
                    EXCLUDED.requested_source_version
                  ),
                  status = CASE
                    WHEN {self._table("action_center_ai_job")}.status = 'running'
                    THEN 'running'
                    ELSE 'pending'
                  END,
                  not_before = EXCLUDED.not_before,
                  attempts = CASE
                    WHEN {self._table("action_center_ai_job")}.status = 'dead_letter'
                    THEN 0
                    ELSE {self._table("action_center_ai_job")}.attempts
                  END,
                  completed_at = NULL,
                  last_error_code = NULL,
                  last_error_message = NULL,
                  updated_at = NOW()
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "dedupe_key": f"work-item:{work_item_id}",
                "student_id": _uuid(student_id),
                "work_item_id": _uuid(work_item_id),
                "source_version": source_version,
                "not_before": not_before,
            },
        )

    async def _queue_student_summary_refresh(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        student_id: str,
        not_before: datetime,
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("action_center_ai_job")} (
                  id, tenant_id, purpose, dedupe_key, student_id, status,
                  requested_source_version, covered_source_version,
                  base_summary_version, not_before, attempts, max_attempts,
                  created_at, updated_at
                )
                VALUES (
                  :id, :tenant_id, 'student_summary', :dedupe_key, :student_id,
                  'pending', 1, 0,
                  (
                    SELECT MAX(version)
                    FROM {self._table("student_summary_revision")}
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                  ),
                  :not_before, 0, 5, NOW(), NOW()
                )
                ON CONFLICT (tenant_id, purpose, dedupe_key)
                DO UPDATE SET
                  requested_source_version =
                    {self._table("action_center_ai_job")}.requested_source_version + 1,
                  status = CASE
                    WHEN {self._table("action_center_ai_job")}.status = 'running'
                    THEN 'running'
                    ELSE 'pending'
                  END,
                  not_before = CASE
                    WHEN {self._table("action_center_ai_job")}.status IN (
                      'succeeded', 'dead_letter', 'cancelled'
                    ) THEN EXCLUDED.not_before
                    ELSE LEAST(
                      GREATEST(
                        {self._table("action_center_ai_job")}.not_before,
                        EXCLUDED.not_before
                      ),
                      {self._table("action_center_ai_job")}.created_at + interval '15 minutes'
                    )
                  END,
                  created_at = CASE
                    WHEN {self._table("action_center_ai_job")}.status IN (
                      'succeeded', 'dead_letter', 'cancelled'
                    ) THEN NOW()
                    ELSE {self._table("action_center_ai_job")}.created_at
                  END,
                  attempts = CASE
                    WHEN {self._table("action_center_ai_job")}.status = 'dead_letter'
                      OR (
                        {self._table("action_center_ai_job")}.status <> 'running'
                        AND {self._table("action_center_ai_job")}.attempts
                          >= {self._table("action_center_ai_job")}.max_attempts
                      )
                    THEN 0
                    ELSE {self._table("action_center_ai_job")}.attempts
                  END,
                  completed_at = NULL,
                  last_error_code = NULL,
                  last_error_message = NULL,
                  updated_at = NOW()
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "dedupe_key": f"student:{student_id}",
                "student_id": _uuid(student_id),
                "not_before": not_before,
            },
        )

    async def _queue_interaction_refresh(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        interaction_id: str,
        work_item_id: str,
        student_id: str,
        source_version: int,
        not_before: datetime,
        force: bool = False,
    ) -> None:
        effective_not_before = self._clock() if force else not_before
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("action_center_ai_job")} (
                  id, tenant_id, purpose, dedupe_key, student_id, work_item_id,
                  interaction_id, status, requested_source_version,
                  covered_source_version, not_before, attempts, max_attempts,
                  created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, 'interaction_enrichment', :dedupe_key,
                  :student_id, :work_item_id, :interaction_id, 'pending',
                  :source_version, 0, :not_before, 0, 5, NOW(), NOW()
                )
                ON CONFLICT (tenant_id, purpose, dedupe_key)
                DO UPDATE SET
                  requested_source_version = GREATEST(
                    {self._table("action_center_ai_job")}.requested_source_version,
                    EXCLUDED.requested_source_version
                  ),
                  status = CASE
                    WHEN {self._table("action_center_ai_job")}.status = 'running'
                    THEN 'running'
                    ELSE 'pending'
                  END,
                  not_before = CASE
                    WHEN :force THEN NOW()
                    WHEN {self._table("action_center_ai_job")}.status IN (
                      'succeeded', 'dead_letter', 'cancelled'
                    ) THEN EXCLUDED.not_before
                    ELSE LEAST(
                      GREATEST(
                        {self._table("action_center_ai_job")}.not_before,
                        EXCLUDED.not_before
                      ),
                      {self._table("action_center_ai_job")}.created_at + interval '15 minutes'
                    )
                  END,
                  created_at = CASE
                    WHEN {self._table("action_center_ai_job")}.status IN (
                      'succeeded', 'dead_letter', 'cancelled'
                    ) THEN NOW()
                    ELSE {self._table("action_center_ai_job")}.created_at
                  END,
                  attempts = CASE
                    WHEN {self._table("action_center_ai_job")}.status = 'dead_letter'
                      OR (
                        {self._table("action_center_ai_job")}.status <> 'running'
                        AND {self._table("action_center_ai_job")}.attempts
                          >= {self._table("action_center_ai_job")}.max_attempts
                      )
                    THEN 0
                    ELSE {self._table("action_center_ai_job")}.attempts
                  END,
                  completed_at = NULL,
                  last_error_code = NULL,
                  last_error_message = NULL,
                  updated_at = NOW()
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "dedupe_key": f"interaction:{interaction_id}",
                "student_id": _uuid(student_id),
                "work_item_id": _uuid(work_item_id),
                "interaction_id": _uuid(interaction_id),
                "source_version": source_version,
                "not_before": effective_not_before,
                "force": force,
            },
        )

    async def _insert_work_log(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        work_item_id: str,
        actor_name: str,
        action: str,
        message: str,
        actor_type: str = "staff",
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("staff_work_log")} (
                  id, tenant_id, work_item_id, actor_type, actor_id,
                  actor_name, action, message, occurred_at
                )
                VALUES (
                  :id, :tenant_id, :work_item_id, :actor_type, :actor_id,
                  :actor_name, :action, :message, NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "work_item_id": _uuid(work_item_id),
                "actor_type": actor_type,
                "actor_id": _uuid(auth.actor_id) if actor_type == "staff" else None,
                "actor_name": actor_name,
                "action": action,
                "message": message,
            },
        )

    async def _resolve_linked_inquiry_for_completed_work(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        work_item: Mapping[str, object],
        work_item_id: str,
    ) -> None:
        inquiry_result = await connection.execute(
            text(
                f"""
                SELECT inquiry.id, inquiry.requirement_id,
                       inquiry.status_before_help
                FROM {self._table("student_inquiry")} inquiry
                WHERE inquiry.tenant_id = :tenant_id
                  AND inquiry.status IN ('new','open','waiting_on_student')
                  AND (
                    (
                      :source_type = 'message'
                      AND inquiry.id = :source_id
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM {self._table("staff_work_item_link")} link
                      WHERE link.tenant_id = inquiry.tenant_id
                        AND link.work_item_id = :work_item_id
                        AND link.entity_type = 'inquiry'
                        AND link.entity_id = inquiry.id
                    )
                  )
                ORDER BY inquiry.created_at, inquiry.id
                LIMIT 1
                FOR UPDATE OF inquiry
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "source_type": work_item.get("source_type"),
                "source_id": work_item.get("source_id"),
                "work_item_id": _uuid(work_item_id),
            },
        )
        inquiry = inquiry_result.mappings().first()
        if inquiry is None:
            return
        await connection.execute(
            text(
                f"""
                UPDATE {self._table("student_inquiry")}
                SET status = 'resolved', resolved_at = NOW(),
                    resolution_reason = 'work_item_done',
                    version = version + 1, updated_at = NOW()
                WHERE tenant_id = :tenant_id AND id = :inquiry_id
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "inquiry_id": inquiry["id"],
            },
        )
        if inquiry.get("requirement_id") is None:
            return
        restore_status = str(inquiry.get("status_before_help") or "in_progress")
        if restore_status == "help_requested":
            restore_status = "in_progress"
        await connection.execute(
            text(
                f"""
                UPDATE {self._table("student_requirement")}
                SET status = CAST(:status AS varchar),
                    version = version + 1, updated_at = NOW()
                WHERE tenant_id = :tenant_id
                  AND id = :requirement_id
                  AND status = 'help_requested'
                """
            ),
            {
                "status": restore_status,
                "tenant_id": _uuid(auth.tenant_id),
                "requirement_id": inquiry["requirement_id"],
            },
        )

    async def _insert_student_message(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        student_id: str,
        subject: str,
        body: str,
        kind: str = "general",
        href: str | None = None,
    ) -> dict[str, object]:
        message_id = self._uuid_factory()
        result = await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("student_message")} (
                  id, tenant_id, student_id, subject, body, sender_name,
                  kind, href, sent_at, read_at, created_at
                )
                VALUES (
                  :id, :tenant_id, :student_id, :subject, :body,
                  'Enrollment Team', :kind, :href, NOW(), NULL, NOW()
                )
                RETURNING sent_at
                """
            ),
            {
                "id": message_id,
                "tenant_id": _uuid(auth.tenant_id),
                "student_id": _uuid(student_id),
                "subject": subject,
                "body": body,
                "kind": kind,
                "href": href,
            },
        )
        row = result.mappings().first()
        if row is None:
            raise ApiError(
                500,
                "STUDENT_NOTIFICATION_FAILED",
                "The student notification could not be created",
            )
        await self._insert_student_realtime_event(
            connection,
            tenant_id=auth.tenant_id,
            student_id=student_id,
            event_type="student.message.created",
            resource_type="student_message",
            resource_id=str(message_id),
            payload={
                "messageId": str(message_id),
                "kind": kind,
                "href": href,
                "invalidate": ["messages", "bootstrap"],
            },
        )
        return {
            "id": str(message_id),
            "subject": subject,
            "body": body,
            "senderName": "Enrollment Team",
            "kind": kind,
            "href": href,
            "sentAt": _iso_timestamp(row["sent_at"]),
            "readAt": None,
        }

    async def _insert_student_realtime_event(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: str,
        student_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
        payload: Mapping[str, object],
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("student_realtime_event")} (
                  id, tenant_id, student_id, event_type, resource_type,
                  resource_id, payload, created_at
                ) VALUES (
                  :id, :tenant_id, :student_id, :event_type, :resource_type,
                  :resource_id, CAST(:payload AS jsonb), NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(tenant_id),
                "student_id": _uuid(student_id),
                "event_type": event_type,
                "resource_type": resource_type,
                "resource_id": _uuid(resource_id),
                "payload": json.dumps(dict(payload), separators=(",", ":"), default=str),
            },
        )

    async def _award_requirement_rewards(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        student_id: str,
        requirement_id: str,
    ) -> int:
        definition_result = await connection.execute(
            text(
                f"""
                SELECT definition.code
                FROM {self._table("student_requirement")} AS requirement
                JOIN {self._table("requirement_definition_version")} AS definition
                  ON definition.id = requirement.requirement_definition_version_id
                 AND definition.tenant_id = requirement.tenant_id
                WHERE requirement.tenant_id = :tenant_id
                  AND requirement.id = :requirement_id
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "requirement_id": _uuid(requirement_id),
            },
        )
        definition = definition_result.mappings().first()
        if definition is None:
            return 0
        trigger_key = str(definition["code"])
        properties: dict[str, object] = {}
        rule_result = await connection.execute(
            text(
                f"""
                SELECT id, points, max_awards_per_student
                FROM {self._table("tenant_reward_rule")}
                WHERE tenant_id = :tenant_id
                  AND trigger_type = 'requirement_completed'
                  AND trigger_key = :trigger_key
                  AND enabled = true
                  AND (starts_at IS NULL OR starts_at <= NOW())
                  AND (ends_at IS NULL OR ends_at > NOW())
                  AND CAST(:properties AS jsonb) @> trigger_properties
                ORDER BY display_order, id
                FOR UPDATE
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "trigger_key": trigger_key,
                "properties": json.dumps(properties, separators=(",", ":")),
            },
        )
        awarded = 0
        for rule in rule_result.mappings().all():
            inserted = await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("student_reward_ledger")} (
                      id, tenant_id, student_id, reward_rule_id, source_type,
                      source_key, points, metadata, awarded_at
                    )
                    SELECT :id, :tenant_id, :student_id, :rule_id,
                      'requirement_completed', :source_key, :points,
                      CAST(:metadata AS jsonb), NOW()
                    WHERE (
                      SELECT COUNT(*)
                      FROM {self._table("student_reward_ledger")} AS existing
                      WHERE existing.tenant_id = :tenant_id
                        AND existing.student_id = :student_id
                        AND existing.reward_rule_id = :rule_id
                    ) < :maximum
                    ON CONFLICT (
                      tenant_id, student_id, reward_rule_id, source_key
                    ) DO NOTHING
                    RETURNING points
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(student_id),
                    "rule_id": rule["id"],
                    "source_key": requirement_id,
                    "points": int(rule["points"]),
                    "metadata": json.dumps(
                        {"triggerKey": trigger_key, "properties": properties},
                        separators=(",", ":"),
                    ),
                    "maximum": int(rule["max_awards_per_student"]),
                },
            )
            awarded += sum(int(row["points"]) for row in inserted.mappings().all())
        return awarded

    async def _refresh_requirement_dependencies(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        completed_requirement_id: str,
    ) -> None:
        requirement = self._table("student_requirement")
        context_result = await connection.execute(
            text(
                f"""
                SELECT requirement.journey_id, journey.student_id
                FROM {requirement} AS requirement
                JOIN {self._table("enrollment_journey")} AS journey
                  ON journey.id=requirement.journey_id
                 AND journey.tenant_id=requirement.tenant_id
                WHERE requirement.tenant_id=:tenant_id
                  AND requirement.id=:completed_requirement_id
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "completed_requirement_id": _uuid(completed_requirement_id),
            },
        )
        context = context_result.mappings().first()
        if context is None:
            return
        await reconcile_student_journey_routes(
            connection,
            tenant_id=_uuid(auth.tenant_id),
            student_id=context["student_id"],
            journey_id=context["journey_id"],
            schema=self._schema,
        )
        journey = self._table("enrollment_journey")
        journey_definition = self._table("journey_definition_version")
        onboarding = self._table("student_onboarding")
        completed = await connection.execute(
            text(
                f"""
                UPDATE {journey} AS journey
                SET status='completed', version=journey.version+1, updated_at=NOW()
                WHERE journey.tenant_id=:tenant_id
                  AND journey.id=(
                    SELECT journey_id FROM {requirement}
                    WHERE tenant_id=:tenant_id AND id=:completed_requirement_id
                  )
                  AND journey.status NOT IN ('completed','cancelled')
                  AND (
                    EXISTS (
                      SELECT 1 FROM {journey_definition} AS journey_definition
                      WHERE journey_definition.id=journey.journey_definition_version_id
                        AND journey_definition.tenant_id=journey.tenant_id
                        AND NOT journey_definition.onboarding_required
                    )
                    OR EXISTS (
                      SELECT 1 FROM {onboarding} AS onboarding
                      WHERE onboarding.tenant_id=journey.tenant_id
                        AND onboarding.student_id=journey.student_id
                        AND onboarding.status='completed'
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM {requirement} AS pending
                    WHERE pending.tenant_id=journey.tenant_id
                      AND pending.journey_id=journey.id
                      AND pending.retired_at IS NULL
                      AND pending.status NOT IN (
                        'not_applicable','completed','waived','expired'
                      )
                  )
                RETURNING journey.student_id
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "completed_requirement_id": _uuid(completed_requirement_id),
            },
        )
        completed_row = completed.mappings().first()
        if completed_row is not None:
            await self._insert_student_message(
                connection,
                auth=auth,
                student_id=str(completed_row["student_id"]),
                subject="Enrollment complete",
                body="All required enrollment tasks are complete.",
                kind="enrollment_completed",
                href="/dashboard",
            )

    async def _run_idempotent(
        self,
        *,
        auth: AuthContext,
        idempotency_key: str,
        operation: str,
        request_payload: object,
        response_status: int,
        handler: Callable[[AsyncConnection], Awaitable[dict[str, object]]],
    ) -> dict[str, object]:
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "tenantId": auth.tenant_id,
                    "actorId": auth.actor_id,
                    "requestPayload": request_payload,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        lock_key = f"{auth.tenant_id}:{auth.actor_id}:{operation}:{idempotency_key}"
        async with self._engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            existing_result = await connection.execute(
                text(
                    f"""
                    SELECT request_hash, response_body
                    FROM {self._table("idempotency_record")}
                    WHERE tenant_id = :tenant_id AND actor_id = :actor_id
                      AND operation = :operation AND idempotency_key = :idempotency_key
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "actor_id": _uuid(auth.actor_id),
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                },
            )
            existing = existing_result.mappings().first()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise ConflictError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "This idempotency key was already used for a different request",
                    )
                response = existing["response_body"]
                return dict(response) if isinstance(response, Mapping) else {}
            response = await handler(connection)
            await connection.execute(
                text(
                    f"""
                    INSERT INTO {self._table("idempotency_record")} (
                      tenant_id, actor_id, operation, idempotency_key, request_hash,
                      response_status, response_body, created_at, expires_at
                    ) VALUES (
                      :tenant_id, :actor_id, :operation, :idempotency_key, :request_hash,
                      :response_status, CAST(:response_body AS jsonb), NOW(),
                      NOW() + interval '24 hours'
                    )
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "actor_id": _uuid(auth.actor_id),
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                    "request_hash": request_hash,
                    "response_status": response_status,
                    "response_body": json.dumps(response, separators=(",", ":"), default=str),
                },
            )
            return response

    async def _insert_staff_notification(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        work_item_id: str,
        staff_member_id: str | None,
        team_component: str | None,
        tenant_wide: bool,
        kind: str,
        title: str,
        body: str,
        dedupe_key: str,
    ) -> str:
        notification_id = str(self._uuid_factory())
        inserted_result = await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("staff_notification")} (
                  id, tenant_id, staff_member_id, team_component, tenant_wide,
                  kind, title, body, resource_type, resource_id, dedupe_key,
                  created_at
                ) VALUES (
                  :id, :tenant_id, :staff_member_id, :team_component, :tenant_wide,
                  :kind, :title, :body, 'staff_work_item', :work_item_id,
                  :dedupe_key, NOW()
                )
                ON CONFLICT (tenant_id, dedupe_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": _uuid(notification_id),
                "tenant_id": _uuid(auth.tenant_id),
                "staff_member_id": (
                    _uuid(staff_member_id) if staff_member_id is not None else None
                ),
                "team_component": team_component,
                "tenant_wide": tenant_wide,
                "kind": kind,
                "title": title,
                "body": body[:2_000],
                "work_item_id": _uuid(work_item_id),
                "dedupe_key": dedupe_key[:240],
            },
        )
        inserted = inserted_result.mappings().first()
        if inserted is None:
            existing_result = await connection.execute(
                text(
                    f"""
                    SELECT id
                    FROM {self._table("staff_notification")}
                    WHERE tenant_id = :tenant_id AND dedupe_key = :dedupe_key
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "dedupe_key": dedupe_key[:240]},
            )
            existing = existing_result.mappings().first()
            if existing is None:
                raise RuntimeError("The staff notification could not be persisted")
            return str(existing["id"])
        notification_id = str(inserted["id"])
        await self._insert_realtime_event(
            connection,
            tenant_id=auth.tenant_id,
            event_type="staff.notification.created",
            resource_type="staff_notification",
            resource_id=notification_id,
            work_item_id=work_item_id,
            staff_member_id=staff_member_id,
            team_component=team_component,
            tenant_wide=tenant_wide,
            payload={
                "notificationId": notification_id,
                "workItemId": work_item_id,
                "kind": kind,
                "invalidate": ["notifications", "workspace"],
            },
        )
        return notification_id

    async def _insert_realtime_event(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
        work_item_id: str | None,
        staff_member_id: str | None,
        team_component: str | None,
        tenant_wide: bool,
        payload: Mapping[str, object],
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("staff_realtime_event")} (
                  id, tenant_id, event_type, resource_type, resource_id,
                  work_item_id, staff_member_id, team_component, tenant_wide,
                  payload, created_at
                ) VALUES (
                  :id, :tenant_id, :event_type, :resource_type, :resource_id,
                  :work_item_id, :staff_member_id, :team_component, :tenant_wide,
                  CAST(:payload AS jsonb), NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(tenant_id),
                "event_type": event_type,
                "resource_type": resource_type,
                "resource_id": _uuid(resource_id),
                "work_item_id": _uuid(work_item_id) if work_item_id is not None else None,
                "staff_member_id": (
                    _uuid(staff_member_id) if staff_member_id is not None else None
                ),
                "team_component": team_component,
                "tenant_wide": tenant_wide,
                "payload": json.dumps(dict(payload), separators=(",", ":"), default=str),
            },
        )

    async def _insert_audit(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        request_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        metadata: Mapping[str, object],
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("audit_event")} (
                  id, tenant_id, actor_type, actor_id, student_id, action,
                  resource_type, resource_id, authorization_basis, request_id,
                  correlation_id, metadata, occurred_at, created_at
                )
                VALUES (
                  :id, :tenant_id, 'staff', :actor_id, NULL, :action,
                  :resource_type, :resource_id, 'staff_enrollment_operations',
                  :request_id, :request_id, CAST(:metadata AS jsonb), NOW(), NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "actor_id": _uuid(auth.actor_id),
                "action": action,
                "resource_type": resource_type,
                "resource_id": _uuid(resource_id),
                "request_id": request_id,
                "metadata": json.dumps(dict(metadata), separators=(",", ":")),
            },
        )

    async def _insert_outbox(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        request_id: str,
        event_name: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        data: Mapping[str, object],
    ) -> None:
        event_id = self._uuid_factory()
        occurred_at = self._clock()
        payload = {
            "eventId": str(event_id),
            "eventName": event_name,
            "occurredAt": _iso_timestamp(occurred_at),
            "tenantId": auth.tenant_id,
            "aggregateType": aggregate_type,
            "aggregateId": aggregate_id,
            "aggregateVersion": aggregate_version,
            "actor": {"type": "staff", "id": auth.actor_id},
            "correlationId": request_id,
            "causationId": str(event_id),
            "data": dict(data),
        }
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("outbox_event")} (
                  id, tenant_id, event_name, aggregate_type, aggregate_id,
                  aggregate_version, occurred_at, actor_type, actor_id,
                  correlation_id, causation_id, payload, created_at
                )
                VALUES (
                  :id, :tenant_id, :event_name, :aggregate_type, :aggregate_id,
                  :aggregate_version, :occurred_at, 'staff', :actor_id,
                  :correlation_id, :causation_id, CAST(:payload AS jsonb),
                  :occurred_at
                )
                """
            ),
            {
                "id": event_id,
                "tenant_id": _uuid(auth.tenant_id),
                "event_name": event_name,
                "aggregate_type": aggregate_type,
                "aggregate_id": _uuid(aggregate_id),
                "aggregate_version": aggregate_version,
                "occurred_at": occurred_at,
                "actor_id": _uuid(auth.actor_id),
                "correlation_id": request_id,
                "causation_id": str(event_id),
                "payload": json.dumps(payload, separators=(",", ":")),
            },
        )

    @staticmethod
    def _require_staff(auth: AuthContext) -> None:
        if auth.actor_type != "staff":
            raise ApiError(
                403,
                "STAFF_ACCESS_REQUIRED",
                "This route requires a staff identity",
            )


def _map_knowledge_card(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "title": str(row["title"]),
        "summary": str(row["summary"]),
        "body": str(row["body"]),
        "category": str(row["category"]),
        "audience": str(row["audience"]),
        "status": str(row["status"]),
        "owner": str(row["owner_name"]),
        "version": int(cast(int, row["version"])),
        "updatedAt": _iso_timestamp(row["updated_at"]),
    }


def _map_core_play(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "title": str(row["title"]),
        "description": str(row["description"]),
        "trigger": str(row["trigger_description"]),
        "audience": str(row["audience"]),
        "steps": [str(item) for item in _json_list(row["steps"], "core play steps")],
        "status": str(row["status"]),
        "owner": str(row["owner_name"]),
        "version": int(cast(int, row["version"])),
        "updatedAt": _iso_timestamp(row["updated_at"]),
    }


def _map_staff_club(row: Mapping[str, object]) -> dict[str, object]:
    source = None
    if row.get("source_label") and row.get("source_url") and row.get("source_status"):
        source = {
            "label": str(row["source_label"]),
            "url": str(row["source_url"]),
            "dataStatus": str(row["source_status"]),
        }
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "category": str(row["category"]),
        "description": str(row["description"]),
        "contactName": str(row["contact_name"]),
        "contactRole": str(row["contact_role"]),
        "contactChannel": str(row["contact_channel"]),
        "latestUpdate": str(row["latest_update"]),
        "nextActivity": _optional_text(row.get("next_activity")),
        "imageUrl": str(row["image_url"]),
        "imageAlt": str(row["image_alt"]),
        "imageAttribution": str(row["image_attribution"]),
        "imageSourceUrl": str(row["image_source_url"]),
        "source": source,
        "socialLinks": _json_list(row.get("social_links", []), "club social links"),
        "longDescription": _optional_text(row.get("long_description")),
        "meetingSchedule": _optional_text(row.get("meeting_schedule")),
        "membershipOpen": bool(row["membership_open"]),
        "events": [],
        "version": int(cast(int, row["version"])),
        "updatedAt": _iso_timestamp(row["updated_at"]),
    }


def _map_action_rule(row: Mapping[str, object]) -> dict[str, object]:
    updated_by = None
    if row.get("updated_by_id") is not None:
        updated_by = {
            "id": str(row["updated_by_id"]),
            "name": str(row["updated_by_name"]),
            "email": str(row["updated_by_email"]),
            "component": str(row["updated_by_component"]),
        }
    return {
        "id": str(row["id"]),
        "code": str(row["code"]),
        "name": str(row["name"]),
        "description": str(row["description"]),
        "enabled": bool(row["enabled"]),
        "signalType": str(row["signal_type"]),
        "flowKind": str(row["flow_kind"]) if row["flow_kind"] is not None else None,
        "requirementCode": (
            str(row["requirement_code"]) if row["requirement_code"] is not None else None
        ),
        "lookaheadDays": (
            _database_integer(row["lookahead_days"], "staff_action_rule.lookahead_days")
            if row["lookahead_days"] is not None
            else None
        ),
        "inactivityDays": (
            _database_integer(row["inactivity_days"], "staff_action_rule.inactivity_days")
            if row["inactivity_days"] is not None
            else None
        ),
        "cadenceMinutes": _database_integer(
            row["cadence_minutes"], "staff_action_rule.cadence_minutes"
        ),
        "component": str(row["component"]),
        "priority": str(row["priority"]),
        "actionType": str(row["action_type"]),
        "titleTemplate": str(row["title_template"]),
        "descriptionTemplate": str(row["description_template"]),
        "version": _database_integer(row["version"], "staff_action_rule.version"),
        "lastEvaluatedAt": (
            _iso_timestamp(row["last_evaluated_at"])
            if row["last_evaluated_at"] is not None
            else None
        ),
        "updatedAt": _iso_timestamp(row["updated_at"]),
        "updatedBy": updated_by,
    }


def _validated_action_rule_values(payload: Mapping[str, object]) -> dict[str, object]:
    code = str(_read(payload, "code")).strip()
    name = str(_read(payload, "name")).strip()
    description = str(_read(payload, "description")).strip()
    signal_type = str(_read(payload, "signalType", "signal_type"))
    flow_kind_value = _read(payload, "flowKind", "flow_kind", default=None)
    flow_kind = str(flow_kind_value) if flow_kind_value is not None else None
    requirement_code_value = _read(payload, "requirementCode", "requirement_code", default=None)
    requirement_code = (
        str(requirement_code_value).strip() if requirement_code_value is not None else None
    )
    lookahead_value = _read(payload, "lookaheadDays", "lookahead_days", default=None)
    inactivity_value = _read(payload, "inactivityDays", "inactivity_days", default=None)
    if lookahead_value is None:
        lookahead_days = None
    elif isinstance(lookahead_value, int) and not isinstance(lookahead_value, bool):
        lookahead_days = lookahead_value
    else:
        raise BadRequestError("VALIDATION_ERROR", "lookaheadDays must be an integer")
    if inactivity_value is None:
        inactivity_days = None
    elif isinstance(inactivity_value, int) and not isinstance(inactivity_value, bool):
        inactivity_days = inactivity_value
    else:
        raise BadRequestError("VALIDATION_ERROR", "inactivityDays must be an integer")
    cadence_value = _read(payload, "cadenceMinutes", "cadence_minutes")
    if isinstance(cadence_value, bool) or not isinstance(cadence_value, int):
        raise BadRequestError("VALIDATION_ERROR", "cadenceMinutes must be an integer")
    cadence_minutes = cadence_value
    component = str(_read(payload, "component")).strip()
    priority = str(_read(payload, "priority"))
    action_type = str(_read(payload, "actionType", "action_type"))
    title_template = str(_read(payload, "titleTemplate", "title_template")).strip()
    description_template = str(
        _read(payload, "descriptionTemplate", "description_template")
    ).strip()
    enabled_value = _read(payload, "enabled", default=True)
    if not isinstance(enabled_value, bool):
        raise BadRequestError("VALIDATION_ERROR", "enabled must be a boolean")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", code):
        raise BadRequestError("VALIDATION_ERROR", "Choose a valid action rule code")
    if not name or len(name) > 160 or not description or len(description) > 1000:
        raise BadRequestError("VALIDATION_ERROR", "Complete the action rule name and description")
    if signal_type not in {"requirement_due", "student_inactive"}:
        raise BadRequestError("VALIDATION_ERROR", "Choose a supported action rule signal")
    if flow_kind not in {None, "enrollment", "onboarding"}:
        raise BadRequestError("VALIDATION_ERROR", "Choose a valid journey flow")
    if not 5 <= cadence_minutes <= 1440:
        raise BadRequestError("VALIDATION_ERROR", "Cadence must be between 5 and 1440 minutes")
    if priority not in _WORK_ITEM_PRIORITIES or action_type not in _WORK_ACTION_TYPES:
        raise BadRequestError("VALIDATION_ERROR", "Choose a valid task priority and action type")
    if not component or len(component) > 160:
        raise BadRequestError("VALIDATION_ERROR", "Choose the responsible staff component")
    if not title_template or len(title_template) > 240 or not description_template:
        raise BadRequestError("VALIDATION_ERROR", "Complete the task title and description")
    if len(description_template) > 2000:
        raise BadRequestError("VALIDATION_ERROR", "The task description is too long")
    if signal_type == "requirement_due":
        if lookahead_days is None or not 0 <= lookahead_days <= 365 or inactivity_days is not None:
            raise BadRequestError(
                "VALIDATION_ERROR",
                "Requirement rules need a lookahead between 0 and 365 days",
            )
    elif (
        inactivity_days is None
        or not 1 <= inactivity_days <= 365
        or lookahead_days is not None
        or requirement_code is not None
    ):
        raise BadRequestError(
            "VALIDATION_ERROR",
            "Inactivity rules need an inactivity threshold between 1 and 365 days",
        )
    return {
        "code": code,
        "name": name,
        "description": description,
        "enabled": enabled_value,
        "signal_type": signal_type,
        "flow_kind": flow_kind,
        "requirement_code": requirement_code,
        "lookahead_days": lookahead_days,
        "inactivity_days": inactivity_days,
        "cadence_minutes": cadence_minutes,
        "component": component,
        "priority": priority,
        "action_type": action_type,
        "title_template": title_template,
        "description_template": description_template,
    }


def _document_route(category: str) -> tuple[str, str]:
    if category == "financial_aid":
        return "Financial Aid", "urgent"
    if category == "health":
        return "Student Health", "high"
    return "Registrar", "high"


def _map_staff_inquiry(
    inquiry: Mapping[str, object],
    *,
    status: str,
    assignee: Mapping[str, object] | None,
    version: int,
    updated_at: object,
) -> dict[str, object]:
    return {
        "id": str(inquiry["id"]),
        "student": {
            "id": str(inquiry["student_id"]),
            "name": f"{inquiry['first_name']} {inquiry['last_name']}",
            "preferredName": str(inquiry["preferred_name"]),
            "programName": str(inquiry["program_name"]),
            "classYear": int(cast(int, inquiry["class_year"])),
        },
        "topicCode": str(inquiry["topic_code"]),
        "subject": str(inquiry["subject"]),
        "message": str(inquiry["message"]),
        "status": status,
        "priority": str(inquiry["priority"]),
        "assignee": dict(assignee) if assignee is not None else None,
        "createdAt": _iso_timestamp(inquiry["created_at"]),
        "updatedAt": _iso_timestamp(updated_at),
        "version": version,
    }


_UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _iso_timestamp(value: object) -> str:
    if isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise RuntimeError("Database returned an invalid timestamp") from error
    elif isinstance(value, datetime):
        timestamp = value
    else:
        raise RuntimeError("Database returned an invalid timestamp")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read(
    values: Mapping[str, object],
    *keys: str,
    default: object = _NO_DEFAULT,
) -> object:
    for key in keys:
        if key in values:
            return values[key]
    if default is not _NO_DEFAULT:
        return default
    raise BadRequestError("VALIDATION_ERROR", f"Missing field {keys[0]}")


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BadRequestError("VALIDATION_ERROR", f"{field} must be a positive integer")
    return value


def _database_integer(value: object, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise RuntimeError(f"Database returned an invalid integer for {field}")


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _optional_datetime(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise BadRequestError(
                "VALIDATION_ERROR", f"{field} must be an ISO timestamp"
            ) from error
    else:
        raise BadRequestError("VALIDATION_ERROR", f"{field} must be an ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_object(value: object, field: str) -> dict[str, object]:
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Database returned invalid JSON for {field}") from error
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Database returned invalid JSON for {field}")
    return {str(key): item for key, item in parsed.items()}


def _text_list(value: object) -> list[str]:
    """A database array (or nothing) as a list of strings."""

    if value is None or isinstance(value, (str, bytes)):
        return []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(entry) for entry in value if entry is not None]


def _json_list(value: object, field: str) -> list[object]:
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Database returned invalid JSON for {field}") from error
    if not isinstance(parsed, list):
        raise RuntimeError(f"Database returned invalid JSON for {field}")
    return list(parsed)


def _map_conversation_signals(value: object) -> dict[str, object]:
    source = _json_object(value, "outcome.conversation_signals")

    def metric(name: str) -> dict[str, object]:
        raw = source.get(name)
        item = raw if isinstance(raw, Mapping) else {}
        score = _optional_float(item.get("score"))
        return {
            "label": _optional_text(item.get("label")) or "Not enough evidence",
            "score": min(1.0, max(0.0, score)) if score is not None else None,
        }

    return {
        "sentiment": metric("sentiment"),
        "engagement": metric("engagement"),
        "intent": _optional_text(source.get("intent")) or "Not enough evidence",
        "likelihoodToProgress": metric("likelihoodToProgress"),
    }


def _job_public_state(job: Mapping[str, object]) -> str:
    status = str(job.get("status", ""))
    requested = int(cast(int, job.get("requested_source_version", 0)))
    covered = int(cast(int, job.get("covered_source_version", 0)))
    if status == "running":
        return "running"
    if status == "failed_retryable":
        return "failed_retryable"
    if status == "dead_letter":
        return "dead_letter"
    if status == "pending":
        return "stale" if covered > 0 and requested > covered else "pending"
    if status == "succeeded":
        return "ready"
    return "not_requested"


def _summary_public_state(
    summary: Mapping[str, object] | None,
    job: Mapping[str, object] | None,
) -> str:
    if job is None:
        return "ready" if summary is not None else "not_requested"
    state = _job_public_state(job)
    if (
        summary is not None
        and state in {"pending", "failed_retryable", "dead_letter"}
        and _iso_timestamp(job["updated_at"]) <= _iso_timestamp(summary["generated_at"])
    ):
        # A task or interaction projection can persist the canonical student
        # summary in the same durable run. An older standalone summary job may
        # still be dead-lettered, but it must not hide that newer current
        # revision from staff.
        return "ready"
    if summary is not None and state == "pending":
        return "stale"
    return state


def _aggregate_ai_states(states: list[str]) -> str:
    precedence = (
        "dead_letter",
        "failed_retryable",
        "running",
        "pending",
        "stale",
        "ready",
        "not_requested",
    )
    return next((state for state in precedence if state in states), "not_requested")


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise BadRequestError("VALIDATION_ERROR", "A UUID identifier is invalid") from error


def _optional_uuid_string(value: object) -> str | None:
    return None if value is None else str(value)
