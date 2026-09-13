"""Read-only Action Center projection, shared with Edward and Atlas."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.domain.action_center import parse_action_center_query
from audentra.domain.work_board import board_card

from .work_board_sql import ATTENTION_SQL, PROJECT_LABELS, PROJECT_SQL, PROJECTS

if TYPE_CHECKING:
    from .staff_repository import PostgresStaffRepository


class WorkBoardProjection:
    def __init__(self, staff: PostgresStaffRepository) -> None:
        self.staff = staff

    async def read(
        self,
        auth: AuthContext,
        offset: int = 0,
        project: str | None = None,
        filters: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
        if offset < 0 or offset > 100000:
            raise ApiError(400, "INVALID_OFFSET", "Invalid board page")
        if project is not None and project not in PROJECTS:
            raise ApiError(400, "INVALID_PROJECT", "Choose an institutional work board")
        raw = dict(filters or {})
        quick = raw.pop("quick", "all")
        if quick not in {"all", "mine", "exceptions", "overdue"}:
            raise ApiError(400, "INVALID_QUICK_FILTER", "Choose a supported work filter")
        if quick == "mine":
            raw["assignee"] = "me"
        if quick == "overdue":
            raw["due"] = "overdue"
        query = replace(
            parse_action_center_query({"status": "all", **raw, "limit": 100, "offset": offset}),
            board_project=project,
            board_attention=quick == "exceptions",
        )
        async with self.staff._engine.connect() as connection:
            await connection.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": auth.tenant_id},
            )
            counts = await connection.execute(
                text(
                    f"SELECT {PROJECT_SQL} AS project, count(*) AS total, "  # noqa: S608 — code-owned SQL
                    "count(*) FILTER (WHERE item.status NOT IN ('done','cancelled')) AS open, "
                    f"count(*) FILTER (WHERE {ATTENTION_SQL}) AS attention, "
                    "count(*) FILTER (WHERE item.due_at < CURRENT_TIMESTAMP) AS overdue, "
                    "array_agg(DISTINCT item.component ORDER BY item.component) AS components "
                    "FROM public.staff_work_item item WHERE item.tenant_id=CAST(:tenant AS uuid) "
                    "GROUP BY 1"
                ),
                {"tenant": auth.tenant_id},
            )
            summaries = {row.project: dict(row._mapping) for row in counts}
            project_counts = {key: row["total"] for key, row in summaries.items()}
            payments = await connection.execute(
                text("""SELECT p.status,count(*) AS total FROM university.payment p
                    WHERE p.tenant_id=CAST(:tenant AS uuid) AND EXISTS (
                      SELECT 1 FROM university.runtime_link l
                      WHERE l.tenant_id=p.tenant_id AND l.kind='payment_work_item'
                      AND l.world_id=p.id) GROUP BY p.status"""),
                {"tenant": auth.tenant_id},
            )
            payment_states = {row.status: row.total for row in payments}
        envelope = cast(
            dict[str, Any],
            await self.staff.get_work_queue(
                auth,
                query,
            ),
        )
        items = envelope["items"]
        linked: dict[str, dict[str, Any]] = {r["id"]: {} for r in items}
        if items:
            async with self.staff._engine.begin() as c:
                await c.execute(
                    text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                    {"tenant": auth.tenant_id},
                )
                params = {"tenant": auth.tenant_id, "ids": list(linked)}
                result = await c.execute(
                    text("""SELECT work_item_id,id,status,version,communication_id
                    FROM public.staff_outreach_draft WHERE tenant_id=CAST(:tenant AS uuid)
                    AND work_item_id=ANY(CAST(:ids AS uuid[]))"""),
                    params,
                )
                for row in result.mappings():
                    linked[str(row["work_item_id"])]["outreachDraft"] = {
                        "id": str(row["id"]),
                        "status": row["status"],
                        "version": row["version"],
                        "communicationId": str(row["communication_id"])
                        if row["communication_id"]
                        else None,
                    }
                result = await c.execute(
                    text("""SELECT DISTINCT ON(i.work_item_id) i.work_item_id,c.id,
                    c.channel,c.direction,c.delivery_status,c.source_type,c.occurred_at
                    FROM public.staff_interaction i JOIN public.communication_event c
                      ON c.tenant_id=i.tenant_id AND c.interaction_id=i.id
                    WHERE i.tenant_id=CAST(:tenant AS uuid)
                      AND i.work_item_id=ANY(CAST(:ids AS uuid[]))
                      AND c.delivery_status IN ('received','delivered')
                    ORDER BY i.work_item_id,c.occurred_at DESC,c.id DESC"""),
                    params,
                )
                for row in result.mappings():
                    linked[str(row["work_item_id"])]["communication"] = {
                        "id": str(row["id"]),
                        "channel": row["channel"],
                        "direction": row["direction"],
                        "deliveryStatus": row["delivery_status"],
                        "source": row["source_type"],
                        "occurredAt": row["occurred_at"].isoformat(),
                    }
                result = await c.execute(
                    text("""
                    SELECT wi.id AS work_id,d.id,d.status,d.file_name AS filename,wi.version
                    FROM public.staff_work_item wi JOIN public.document_record d
                    ON d.tenant_id=wi.tenant_id AND d.id=wi.source_id
                    WHERE wi.tenant_id=:tenant AND wi.id=ANY(CAST(:ids AS uuid[]))
                    AND wi.source_type='document'
                """),
                    params,
                )
                for row in result.mappings():
                    linked[str(row["work_id"])]["document"] = {
                        k: str(v) if k == "id" else v for k, v in row.items() if k != "work_id"
                    }
                result = await c.execute(
                    text("""
                    SELECT l.runtime_id AS work_id,w.id,w.title,w.status,w.owner_id,w.due_at,
                    (SELECT count(*) FROM university.workflow_step s WHERE s.tenant_id=w.tenant_id
                     AND s.workflow_id=w.id
                     AND (s.status NOT IN ('complete','waived') OR s.evidence IS NULL))
                     AS incomplete_steps
                    FROM university.runtime_link l JOIN university.workflow w
                    ON w.tenant_id=l.tenant_id AND w.id=l.world_id
                    WHERE l.tenant_id=:tenant AND l.kind='workflow'
                    AND l.runtime_id=ANY(CAST(:ids AS uuid[]))
                """),
                    params,
                )
                for row in result.mappings():
                    linked[str(row["work_id"])]["case"] = {
                        k: v for k, v in row.items() if k != "work_id"
                    }
                result = await c.execute(
                    text("""
                    SELECT l.runtime_id AS work_id,p.* FROM university.runtime_link l
                    JOIN university.payment p ON p.tenant_id=l.tenant_id AND p.id=l.world_id
                    WHERE l.tenant_id=:tenant AND l.kind='payment_work_item'
                    AND l.runtime_id=ANY(CAST(:ids AS uuid[]))
                """),
                    params,
                )
                for row in result.mappings():
                    linked[str(row["work_id"])]["payment"] = {
                        k: v for k, v in row.items() if k != "work_id"
                    }
        return {
            **envelope,
            "project": project,
            "projectCounts": project_counts,
            "projectSummaries": summaries,
            "filterScope": "full_matching_queue",
            "projects": [
                {"id": key, "name": name, "count": project_counts.get(key, 0)}
                for key, name in PROJECT_LABELS.items()
            ],
            "paymentStateCounts": payment_states,
            "paymentCountScope": "Payment attempts linked to canonical work items",
            "actorId": auth.actor_id,
            "cards": [board_card(item, linked[item["id"]]) for item in items],
            "basis": "canonical_work_and_domain_evidence",
            "readOnlyProjection": True,
        }
