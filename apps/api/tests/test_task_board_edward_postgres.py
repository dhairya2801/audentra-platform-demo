"""Opt-in read-only checks against an isolated copy of the configured demo board."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from typing import Any, cast
from urllib.parse import urlparse

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.infrastructure.postgres.task_board_assistant import TaskBoardAssistant
from audentra.integrations.assistant.read_loop import bound_result


@pytest.mark.postgres
def test_canonical_board_scope_details_paging_and_student_isolation() -> None:
    async def run() -> None:
        url = os.getenv("AUDENTRA_CAMILA_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("Requires an isolated Camila test database")
        parsed = urlparse(url)
        assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.startswith(
            "/audentra_university_test_camila"
        )
        engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
        auth = AuthContext(
            "00000000-0000-7000-8000-000000000003",
            "3bedfe91-6802-4937-893b-72cb7779ecfa",
            "01973261-954a-5019-8e9e-24a699abea7b",
            "staff",
        )
        staff = PostgresStaffRepository(engine, cast(Any, None))
        try:
            board = TaskBoardAssistant(staff, auth)
            actual = await board.snapshot()
            assert len(actual["cards"]) == 64 and actual["studentCount"] == 10
            pages = [await board.read(offset=offset, limit=15) for offset in (0, 15, 30, 45, 60)]
            keys = [c["key"] for page in pages for c in page["cards"]]
            assert len(keys) == len(set(keys)) == 64
            assert not pages[-1]["page"]["hasMore"]
            ada = await board.search_students(query="Ada")
            assert ada["total"] == 1 and ada["items"][0]["id"] == auth.student_id
            doc = next(
                c
                for c in actual["cards"]
                if c["student"]["id"] == auth.student_id and c["documents"]
            )
            detail = await board.task(doc["key"])
            assert detail["task"]["documents"][0]["fileName"] == doc["documents"][0]["fileName"]
            assert "task" in bound_result(detail), (
                "Task context must fit structurally, not collapse into a clipped string"
            )
            messages = await board.task(doc["key"], section="messages", limit=1)
            assert (
                messages["currentEvidence"]["documents"][0]["status"]
                == doc["documents"][0]["status"]
            )
            assert len(messages["items"]) <= 1
            assert messages["task"]["priority"] == doc["priority"]
            assert messages["task"]["dueAt"] == doc["dueAt"]
            with pytest.raises(ApiError):
                await TaskBoardAssistant(staff, replace(auth, actor_type="student")).read()
            with pytest.raises(ApiError):
                await TaskBoardAssistant(
                    staff, replace(auth, tenant_id="00000000-0000-4000-8000-000000000999")
                ).page_context({"workItemKey": doc["key"]})
            async with engine.connect() as c:
                other = await c.scalar(
                    text(
                        """SELECT id FROM staff_member m WHERE tenant_id=CAST(:tenant AS uuid)
                        AND active AND id!=CAST(:actor AS uuid) AND NOT EXISTS (
                          SELECT 1 FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                          AND w.assignee_id=m.id) LIMIT 1"""
                    ),
                    {"tenant": auth.tenant_id, "actor": auth.actor_id},
                )
            assert other
            other_board = TaskBoardAssistant(staff, replace(auth, actor_id=str(other)))
            assert (await other_board.read())["boardTotal"] == 0
            with pytest.raises(ApiError):
                await other_board.task(doc["key"])
        finally:
            await engine.dispose()

    asyncio.run(run())
