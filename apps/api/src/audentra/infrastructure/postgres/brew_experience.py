"""Small tenant/staff-bound services for Morning Brew settings, feedback and RSS."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError

from .brew_news import parse_feed, retrieve_feed

_NEWS_LOCK = asyncio.Lock()
REASONS = {
    "accurate",
    "useful_context",
    "clear_actions",
    "easy_to_scan",
    "inaccurate",
    "missing_context",
    "unclear_actions",
    "too_long",
}


class BrewExperienceService:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def member(self, c: AsyncConnection, auth: AuthContext) -> RowMapping:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
        row = (
            (
                await c.execute(
                    text("""SELECT m.id,m.component,EXISTS(SELECT 1 FROM staff_role_capability cap
          WHERE cap.tenant_id=m.tenant_id AND cap.role_code=m.role_code
          AND cap.capability='morning_brew.team.configure') AS can_manage
          FROM staff_member m WHERE m.tenant_id=:tenant AND m.id=:actor AND m.active"""),
                    {"tenant": UUID(auth.tenant_id), "actor": UUID(auth.actor_id)},
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ApiError(403, "STAFF_REQUIRED", "Active staff access required")
        return row

    async def settings(self, auth: AuthContext) -> dict[str, Any]:
        async with self.engine.begin() as c:
            member = await self.member(c, auth)
            row = (
                (
                    await c.execute(
                        text(
                            "SELECT * FROM staff_brew_team_setting "
                            "WHERE tenant_id=:tenant AND component=:team"
                        ),
                        {"tenant": UUID(auth.tenant_id), "team": member["component"]},
                    )
                )
                .mappings()
                .first()
            )
            return {
                "team": member["component"],
                "canManage": member["can_manage"],
                "intelligenceEnabled": row["intelligence_enabled"] if row else True,
                "version": row["version"] if row else 0,
            }

    async def event(
        self,
        c: AsyncConnection,
        auth: AuthContext,
        name: str,
        version: int,
        metadata: dict[str, Any],
        request_id: str,
    ) -> None:
        params = {
            "id": uuid4(),
            "tenant": UUID(auth.tenant_id),
            "actor": UUID(auth.actor_id),
            "name": name,
            "version": version,
            "body": json.dumps(metadata),
            "request": request_id,
        }
        await c.execute(
            text("""INSERT INTO
audit_event(id,tenant_id,actor_type,actor_id,action,resource_type,resource_id,
authorization_basis,request_id,correlation_id,metadata)
VALUES(:id,:tenant,'staff',:actor,:name,'staff_member',:actor,
'authenticated_team_scope',:request,:request,CAST(:body AS jsonb))"""),
            params,
        )
        await c.execute(
            text("""INSERT INTO
outbox_event(id,tenant_id,event_name,aggregate_type,aggregate_id,aggregate_version,
occurred_at,actor_type,actor_id,correlation_id,causation_id,payload,created_at)
VALUES(:id,:tenant,:name,'staff_member',:actor,:version,now(),'staff',:actor,:request,:request,CAST(:body
AS jsonb),now())"""),
            params,
        )

    async def update_settings(
        self, auth: AuthContext, payload: dict[str, Any], request_id: str
    ) -> dict[str, Any]:
        if (
            set(payload) != {"expectedVersion", "intelligenceEnabled"}
            or type(payload["expectedVersion"]) is not int
            or payload["expectedVersion"] < 0
            or type(payload["intelligenceEnabled"]) is not bool
        ):
            raise ApiError(
                400, "INVALID_SETTING", "Supply the displayed version and visibility choice"
            )
        async with self.engine.begin() as c:
            member = await self.member(c, auth)
            if not member["can_manage"]:
                raise ApiError(
                    403, "TEAM_PERMISSION_REQUIRED", "Your team administrator manages this setting"
                )
            params = {
                "tenant": UUID(auth.tenant_id),
                "team": member["component"],
                "actor": UUID(auth.actor_id),
                "enabled": payload["intelligenceEnabled"],
                "version": payload["expectedVersion"],
            }
            await c.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtextextended("
                    "CAST(CAST(:tenant AS uuid) AS text)||:team,0))"
                ),
                params,
            )
            current = (
                await c.scalar(
                    text(
                        "SELECT version FROM staff_brew_team_setting "
                        "WHERE tenant_id=:tenant AND component=:team"
                    ),
                    params,
                )
                or 0
            )
            if current != payload["expectedVersion"]:
                raise ApiError(
                    409,
                    "VERSION_CONFLICT",
                    "Your team setting changed. Review the latest choice before saving.",
                )
            await c.execute(
                text("""INSERT INTO
staff_brew_team_setting(tenant_id,component,intelligence_enabled,version,updated_by)
VALUES(:tenant,:team,:enabled,1,:actor) ON CONFLICT(tenant_id,component) DO
UPDATE SET
intelligence_enabled=:enabled,version=staff_brew_team_setting.version+1,updated_by=:actor,updated_at=now()"""),
                params,
            )
            await self.event(
                c,
                auth,
                "morning_brew.team_setting_updated.v1",
                current + 1,
                {"team": member["component"], "enabled": payload["intelligenceEnabled"]},
                request_id,
            )
            await c.execute(
                text("""INSERT INTO staff_realtime_event
                  (id,tenant_id,event_type,resource_type,resource_id,team_component,payload)
                  VALUES(:id,:tenant,'morning_brew.team_setting_updated.v1',
                  'staff_member',:actor,:team,'{}'::jsonb)"""),
                {**params, "id": uuid4()},
            )
        return await self.settings(auth)

    async def feedback(
        self, auth: AuthContext, payload: dict[str, Any], request_id: str
    ) -> dict[str, Any]:
        try:
            required = {
                "id",
                "snapshotAt",
                "rating",
                "dataOrigin",
                "subjectId",
                "comment",
                "reasons",
            }
            if set(payload) != required or not all(
                isinstance(payload[key], str)
                for key in ("id", "snapshotAt", "rating", "dataOrigin", "subjectId", "comment")
            ):
                raise ValueError("Invalid feedback fields")
            identifier = UUID(payload["id"])
            when = datetime.fromisoformat(payload["snapshotAt"].replace("Z", "+00:00"))
            if not (
                when.tzinfo is not None
                and payload["rating"] in ("up", "down")
                and payload["dataOrigin"] == "demo"
                and 0 < len(payload["subjectId"]) <= 200
                and len(payload["comment"]) <= 1000
                and isinstance(payload["reasons"], list)
                and len(payload["reasons"]) <= 4
                and set(payload["reasons"]) <= REASONS
            ):
                raise ValueError("Invalid feedback values")
        except (KeyError, ValueError, TypeError):
            raise ApiError(
                400, "INVALID_FEEDBACK", "Choose a rating and up to four feedback reasons"
            ) from None
        async with self.engine.begin() as c:
            await self.member(c, auth)
            params = {
                "id": identifier,
                "tenant": UUID(auth.tenant_id),
                "actor": UUID(auth.actor_id),
                "subject": payload["subjectId"],
                "when": when,
                "rating": payload["rating"],
                "reasons": json.dumps(payload["reasons"]),
                "comment": payload["comment"],
            }
            row = await c.scalar(
                text("""INSERT INTO
staff_brew_prep_feedback(id,tenant_id,staff_id,subject_id,snapshot_at,data_origin,rating,reasons
                    ,comment)
VALUES(:id,:tenant,:actor,:subject,:when,'demo',:rating,CAST(:reasons AS
jsonb),:comment) ON CONFLICT(id) DO NOTHING RETURNING id"""),
                params,
            )
            if row is None:
                existing = (
                    (
                        await c.execute(
                            text(
                                "SELECT * FROM staff_brew_prep_feedback "
                                "WHERE id=:id AND tenant_id=:tenant AND staff_id=:actor"
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .first()
                )
                if (
                    existing is None
                    or existing["subject_id"] != payload["subjectId"]
                    or existing["rating"] != payload["rating"]
                    or existing["comment"] != payload["comment"]
                    or existing["reasons"] != payload["reasons"]
                    or existing["snapshot_at"] != when
                ):
                    raise ApiError(
                        409, "FEEDBACK_CONFLICT", "This feedback request was already used"
                    )
            else:
                await self.event(
                    c,
                    auth,
                    "morning_brew.prep_feedback_recorded.v1",
                    1,
                    {"feedbackId": str(identifier), "source": "demo"},
                    request_id,
                )
        return {"id": str(identifier), "saved": True, "dataOrigin": "demo"}

    async def news(self, auth: AuthContext, refresh: bool = False) -> dict[str, Any]:
        params = {"tenant": UUID(auth.tenant_id)}
        async with self.engine.begin() as c:
            await self.member(c, auth)
        async with _NEWS_LOCK:
            async with self.engine.begin() as c:
                cached = (
                    (
                        await c.execute(
                            text("SELECT * FROM staff_brew_news_cache WHERE tenant_id=:tenant"),
                            params,
                        )
                    )
                    .mappings()
                    .first()
                )
            now = datetime.now(UTC)
            due = not cached or now - cached["attempted_at"] > timedelta(
                seconds=60 if refresh else 900
            )
            fetched: datetime | None
            if due:
                try:
                    status, raw, headers = await retrieve_feed(
                        cached["etag"] if cached else None,
                        cached["last_modified"] if cached else None,
                    )
                    articles = (
                        list(cached["articles"])
                        if status == 304 and cached
                        else parse_feed(raw, now)
                    )
                    fetched = now
                    failed = False
                except Exception:  # Provider errors must never replace the last good articles.
                    articles = list(cached["articles"]) if cached else []
                    fetched = cached["fetched_at"] if cached else None
                    headers = {}
                    failed = True
                async with self.engine.begin() as c:
                    await c.execute(
                        text("""INSERT INTO
staff_brew_news_cache(tenant_id,articles,fetched_at,attempted_at,etag,last_modified,failed)
VALUES(:tenant,CAST(:articles AS jsonb),:fetched,:now,:etag,:modified,:failed)
ON CONFLICT(tenant_id) DO UPDATE SET
articles=EXCLUDED.articles,fetched_at=EXCLUDED.fetched_at,attempted_at=EXCLUDED.attempted_at,
etag=EXCLUDED.etag,last_modified=EXCLUDED.last_modified,failed=EXCLUDED.failed"""),
                        {
                            **params,
                            "articles": json.dumps(articles),
                            "fetched": fetched,
                            "now": now,
                            "etag": headers.get("etag", cached["etag"] if cached else None),
                            "modified": headers.get(
                                "last-modified", cached["last_modified"] if cached else None
                            ),
                            "failed": failed,
                        },
                    )
            else:
                assert cached is not None
                articles = list(cached["articles"])
                fetched = cached["fetched_at"]
                failed = cached["failed"]
            stale = failed or not fetched or now - fetched > timedelta(hours=24)
            return {
                "articles": articles,
                "state": "stale"
                if articles and stale
                else "error"
                if failed
                else "empty"
                if not articles
                else "fresh",
                "fetchedAt": fetched.isoformat() if fetched else None,
                "checkedAt": now.isoformat(),
                "cached": not due,
                "refreshAfterSeconds": 60,
                "message": "Publisher retrieval failed. Showing the last successful feed."
                if failed and articles
                else "Publisher feed is temporarily unavailable."
                if failed
                else None,
            }
