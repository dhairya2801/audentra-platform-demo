"""Read-only checks against an explicitly isolated synthetic university copy."""

import asyncio
import os
from typing import Any, cast
from urllib.parse import urlparse

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from audentra.core.auth import AuthContext
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.infrastructure.postgres.student_directory import directory_query


def test_directory_rejects_unrecognized_sort_and_filters() -> None:
    from audentra.core.errors import BadRequestError

    for option in ("sort", "view", "risk", "stage"):
        values = {"sort": "recommended", "view": "all", "risk": "", "stage": ""}
        values[option] = "invalid; DROP TABLE student"
        with pytest.raises(BadRequestError):
            directory_query("SELECT 1", **values)


@pytest.mark.postgres
def test_every_student_is_reachable_and_rich_records_lead() -> None:
    url = os.environ.get("STUDENT360_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set STUDENT360_TEST_DATABASE_URL to an isolated synthetic university copy")
    parsed = urlparse(url)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.endswith("_test")

    async def check() -> None:
        engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
        repo = PostgresStaffRepository(engine, PostgresPortalRepository(engine))
        ada = "3bedfe91-6802-4937-893b-72cb7779ecfa"
        auth = AuthContext(
            "00000000-0000-7000-8000-000000000003",
            ada,
            "01973261-954a-5019-8e9e-24a699abea7b",
            "staff",
        )

        async def read(**kwargs: Any) -> dict[str, Any]:
            return cast(
                dict[str, Any],
                await repo.search_students(
                    auth,
                    query=kwargs.pop("query", None),
                    student_id=None,
                    limit=kwargs.pop("limit", 200),
                    featured_student_id=ada,
                    **kwargs,
                ),
            )

        try:
            first = await read(limit=7)
            assert first["items"][0]["id"] == ada
            assert all(s["journey"]["completedTasks"] > 0 for s in first["items"])
            assert first["total"] > 200
            ids: list[str] = []
            for offset in range(0, first["total"], 200):
                page = await read(offset=offset)
                ids.extend(s["id"] for s in page["items"])
            assert len(ids) == len(set(ids)) == first["total"]
            matches = await read(query="Ada")
            match_ids = {s["id"] for s in matches["items"]}
            assert ada in match_ids and match_ids <= set(ids)
            assert any(student_id not in ids[:200] for student_id in match_ids)
            later = await read(offset=200)
            assert later["summary"] == first["summary"]
            assert later["facets"] == first["facets"]
            empty = await read(query="no-such-student-directory", offset=900000)
            assert empty["items"] == [] and empty["total"] == 0 and empty["offset"] == 0
            end = await read(offset=900000, limit=7)
            assert end["offset"] == ((first["total"] - 1) // 7) * 7
            assert len(end["items"]) > 0
            high = await read(risk="High", sort="risk")
            for item in high["items"]:
                assert 60 <= 18 + sum(map(ord, item["id"])) % 67 < 75
            program = first["items"][0]["programName"]
            scoped = await read(program=program, stage="Enrollment", view="blocked")
            assert all(
                s["programName"] == program and s["journey"]["stage"] == "Enrollment"
                for s in scoped["items"]
            )
            foreign = AuthContext(
                "00000000-0000-7000-8000-000000000999", ada, auth.actor_id, "staff"
            )
            denied = await repo.search_students(foreign, query=None, student_id=None, limit=7)
            assert denied["items"] == [] and denied["cohortTotal"] == 0
        finally:
            await engine.dispose()

    asyncio.run(check())
