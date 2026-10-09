"""Run only against an isolated Morning Brew demo database; never reset a tenant."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres import brew_experience

pytestmark = pytest.mark.postgres


def test_team_feedback_and_cached_news_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.getenv("MORNING_BREW_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Set MORNING_BREW_TEST_DATABASE_URL to the isolated demo copy")
    assert "_morning_brew_" in url.rsplit("/", 1)[-1]

    async def scenario() -> None:
        engine = create_database_engine(url)
        service = brew_experience.BrewExperienceService(engine)
        try:
            async with engine.begin() as c:
                rows = (
                    (
                        await c.execute(
                            text("""SELECT m.id,m.tenant_id,m.component,m.display_name,
                EXISTS(SELECT 1 FROM staff_role_capability r WHERE r.tenant_id=m.tenant_id
                AND r.role_code=m.role_code
                AND r.capability='morning_brew.team.configure') can_manage
                FROM staff_member m WHERE m.active ORDER BY m.display_name""")
                        )
                    )
                    .mappings()
                    .all()
                )
            leader = next(
                r for r in rows if r["can_manage"] and r["display_name"] == "Camila Abernathy"
            )
            auth = AuthContext(
                tenant_id=str(leader["tenant_id"]),
                actor_id=str(leader["id"]),
                actor_type="staff",
                student_id="",
            )
            original = await service.settings(auth)
            updated = await service.update_settings(
                auth,
                {
                    "expectedVersion": original["version"],
                    "intelligenceEnabled": not original["intelligenceEnabled"],
                },
                "mb-v2-test",
            )
            assert updated["version"] == original["version"] + 1
            assert (await service.settings(auth)) == updated
            teammate = next(
                r
                for r in rows
                if r["component"] == leader["component"]
                and r["tenant_id"] == leader["tenant_id"]
                and r["id"] != leader["id"]
            )
            teammate_view = await service.settings(replace(auth, actor_id=str(teammate["id"])))
            assert teammate_view["intelligenceEnabled"] == updated["intelligenceEnabled"]
            try:
                with pytest.raises(ApiError) as stale:
                    await service.update_settings(
                        auth,
                        {"expectedVersion": original["version"], "intelligenceEnabled": True},
                        "mb-v2-test",
                    )
                assert stale.value.status_code == 409
                colleague = next(r for r in rows if not r["can_manage"])
                with pytest.raises(ApiError) as denied:
                    await service.update_settings(
                        replace(
                            auth,
                            tenant_id=str(colleague["tenant_id"]),
                            actor_id=str(colleague["id"]),
                        ),
                        {"expectedVersion": 0, "intelligenceEnabled": False},
                        "mb-v2-test",
                    )
                assert denied.value.status_code == 403
                with pytest.raises(ApiError) as tenant:
                    await service.settings(replace(auth, tenant_id=str(uuid4())))
                assert tenant.value.status_code == 403
            finally:
                await service.update_settings(
                    auth,
                    {
                        "expectedVersion": updated["version"],
                        "intelligenceEnabled": original["intelligenceEnabled"],
                    },
                    "mb-v2-restore",
                )
            payload: dict[str, Any] = {
                "id": str(uuid4()),
                "subjectId": "demo-migration-test",
                "snapshotAt": datetime.now(UTC).isoformat(),
                "dataOrigin": "demo",
                "rating": "up",
                "reasons": ["useful_context"],
                "comment": "Isolated integration verification",
            }
            first = await service.feedback(auth, payload, "mb-v2-feedback")
            assert first == await service.feedback(auth, payload, "mb-v2-feedback-retry")
            with pytest.raises(ApiError) as conflict:
                await service.feedback(
                    auth, {**payload, "comment": "Different body"}, "mb-v2-feedback"
                )
            assert conflict.value.status_code == 409
            for bad in (
                {"snapshotAt": "2026-10-05"},
                {"reasons": ["unknown"]},
                {"unexpected": True},
            ):
                with pytest.raises(ApiError) as invalid:
                    await service.feedback(auth, {**payload, **bad}, "mb-v2-feedback")
                assert invalid.value.status_code == 400
            async with engine.begin() as c:
                count = await c.scalar(
                    text(
                        "SELECT count(*) FROM staff_brew_prep_feedback WHERE id=CAST(:id AS uuid)"
                    ),
                    {"id": payload["id"]},
                )
                assert count == 1
                cache = (
                    (
                        await c.execute(
                            text(
                                "SELECT * FROM staff_brew_news_cache "
                                "WHERE tenant_id=CAST(:tenant AS uuid)"
                            ),
                            {"tenant": auth.tenant_id},
                        )
                    )
                    .mappings()
                    .first()
                )
            assert cache is not None and cache["articles"], (
                "Retrieve the real feed before this test"
            )

            async def failed_feed(*args: Any) -> Any:
                raise TimeoutError("Test-only provider failure")

            monkeypatch.setattr(brew_experience, "retrieve_feed", failed_feed)
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE staff_brew_news_cache SET attempted_at=now()-interval '1 hour' "
                        "WHERE tenant_id=CAST(:tenant AS uuid)"
                    ),
                    {"tenant": auth.tenant_id},
                )
            try:
                failed = await service.news(auth, refresh=True)
                assert failed["state"] == "stale"
                assert failed["articles"] == list(cache["articles"])
                assert failed["fetchedAt"] == cache["fetched_at"].isoformat()
                assert (await service.news(auth, refresh=True))["cached"] is True
            finally:
                async with engine.begin() as c:
                    await c.execute(
                        text(
                            "UPDATE staff_brew_news_cache SET attempted_at=:when,failed=:failed "
                            "WHERE tenant_id=CAST(:tenant AS uuid)"
                        ),
                        {
                            "when": cache["attempted_at"],
                            "failed": cache["failed"],
                            "tenant": auth.tenant_id,
                        },
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
