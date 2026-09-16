"""Canonical task actions, failure boundaries and conversation privacy in isolated Postgres."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, cast
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.postgres.demo_task_board_commands import DemoTaskBoardCommands
from audentra.infrastructure.postgres.demo_task_board_repository import DemoTaskBoardProjection
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository


@pytest.mark.postgres
def test_connected_board_conversation_transactions_and_scope() -> None:
    async def run() -> None:
        url = os.getenv("AUDENTRA_CAMILA_CONNECTED_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("Requires an isolated populated Camila database")
        parsed = urlparse(url)
        if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
            "/audentra_university_test_camila"
        ):
            raise ValueError("Use an isolated Camila test database")
        engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
        try:
            async with engine.connect() as c:
                tx = await c.begin()
                try:

                    @asynccontextmanager
                    async def begin() -> Any:
                        async with c.begin_nested():
                            yield c

                    class Scope:
                        pass

                    scoped = Scope()
                    scoped.begin = begin  # type: ignore[attr-defined]
                    scoped.connect = begin  # type: ignore[attr-defined]
                    portal = PostgresPortalRepository(cast(Any, scoped))
                    staff = PostgresStaffRepository(cast(Any, scoped), portal)
                    command = DemoTaskBoardCommands(staff)
                    projection = DemoTaskBoardProjection(staff)
                    auth = AuthContext(
                        "00000000-0000-7000-8000-000000000003",
                        "3bedfe91-6802-4937-893b-72cb7779ecfa",
                        "01973261-954a-5019-8e9e-24a699abea7b",
                        "staff",
                    )
                    student = replace(auth, actor_type="student", actor_id=auth.student_id)
                    # Start a fresh thread in this rollback-only fixture even after browser runs.
                    await c.execute(
                        text("""
                        DELETE FROM staff_work_item_link l USING staff_demo_board_card b
                        WHERE l.tenant_id=CAST(:tenant AS uuid) AND b.tenant_id=l.tenant_id
                          AND b.work_item_id=l.work_item_id AND b.template_key='ENR-184'
                          AND l.entity_type='inquiry'
                    """),
                        {"tenant": auth.tenant_id},
                    )
                    before = await projection.read(auth)
                    card = next(x for x in before["cards"] if x["templateKey"] == "ENR-184")
                    request_id = str(uuid4())
                    payload = {
                        "kind": "message",
                        "body": "Please send your updated transcript.",
                        "expectedVersion": card["version"],
                        "startNewConversation": False,
                    }
                    result = await command.write(auth, card["id"], payload, request_id, request_id)
                    assert (
                        await command.write(auth, card["id"], payload, str(uuid4()), request_id)
                        == result
                    )
                    with pytest.raises(ApiError) as conflict:
                        await command.write(
                            auth,
                            card["id"],
                            {**payload, "body": "Changed body"},
                            str(uuid4()),
                            request_id,
                        )
                    assert conflict.value.status_code == 409
                    with pytest.raises(ApiError) as stale:
                        await command.write(auth, card["id"], payload, str(uuid4()), str(uuid4()))
                    assert stale.value.code == "VERSION_CONFLICT"
                    for bad in [
                        student,
                        replace(auth, actor_id=str(uuid4())),
                        replace(auth, tenant_id=str(uuid4())),
                    ]:
                        with pytest.raises(ApiError):
                            await command.write(
                                bad, card["id"], payload, str(uuid4()), str(uuid4())
                            )
                    help_data = await portal.get_student_help(student)
                    thread = next(
                        x for x in help_data["requests"] if x["id"] == result["inquiryId"]
                    )
                    assert len(thread["messages"]) == 1
                    assert thread["messages"][0]["direction"] == "staff"
                    assert thread["messages"][0]["body"] == payload["body"]
                    note = "Private staff evidence - must never be delivered"
                    await command.write(
                        auth,
                        card["id"],
                        {"kind": "note", "body": note, "expectedVersion": result["version"]},
                        str(uuid4()),
                        str(uuid4()),
                    )
                    assert note not in str(await portal.get_student_help(student))
                    # Even a reply to a closed demo task reuses the same board membership.
                    await c.execute(
                        text("UPDATE staff_work_item SET status='done' WHERE id=CAST(:id AS uuid)"),
                        {"id": card["id"]},
                    )
                    reply_key = str(uuid4())
                    reply_payload = {
                        "expectedVersion": thread["version"],
                        "body": "I will upload it today.",
                    }
                    reply = await portal.create_student_inquiry_message(
                        student, thread["id"], reply_payload, reply_key, str(uuid4())
                    )
                    assert reply["workItemId"] == card["id"]
                    replay = await portal.create_student_inquiry_message(
                        student, thread["id"], reply_payload, reply_key, str(uuid4())
                    )
                    assert replay == reply
                    assert [m["direction"] for m in reply["messages"]] == ["staff", "student"]
                    assert (await projection.read(auth))["total"] == 64
                    other = next(
                        x["student"]["id"]
                        for x in before["cards"]
                        if x["student"]["id"] != auth.student_id
                    )
                    with pytest.raises(ApiError):
                        await portal.create_student_inquiry_message(
                            replace(student, student_id=other, actor_id=other),
                            thread["id"],
                            {"expectedVersion": reply["version"], "body": "Cross-student"},
                            str(uuid4()),
                            str(uuid4()),
                        )
                    # Explicit restart preserves the expired thread.
                    await c.execute(
                        text(
                            "UPDATE student_inquiry SET expires_at=now()-interval '1 day' "
                            "WHERE id=CAST(:id AS uuid)"
                        ),
                        {"id": thread["id"]},
                    )
                    current = next(
                        x for x in (await projection.read(auth))["cards"] if x["id"] == card["id"]
                    )
                    expired_payload = {**payload, "expectedVersion": current["version"]}
                    with pytest.raises(ApiError) as expired:
                        await command.write(
                            auth, card["id"], expired_payload, str(uuid4()), str(uuid4())
                        )
                    assert expired.value.code == "SUPPORT_CONVERSATION_EXPIRED"
                    restarted = await command.write(
                        auth,
                        card["id"],
                        {**expired_payload, "startNewConversation": True},
                        str(uuid4()),
                        str(uuid4()),
                    )
                    assert restarted["inquiryId"] != thread["id"]
                    current = next(
                        x for x in (await projection.read(auth))["cards"] if x["id"] == card["id"]
                    )
                    assert len(current["conversations"]) == 2
                    # Assignment loss blocks both notes and messages without any partial delivery.
                    await c.execute(
                        text(
                            "UPDATE student_staff_assignment SET ended_at=now() "
                            "WHERE student_id=CAST(:id AS uuid) AND ended_at IS NULL"
                        ),
                        {"id": student.student_id},
                    )
                    with pytest.raises(ApiError) as reassigned:
                        await command.write(
                            auth,
                            card["id"],
                            {**payload, "expectedVersion": current["version"]},
                            str(uuid4()),
                            str(uuid4()),
                        )
                    assert reassigned.value.status_code == 404
                finally:
                    await tx.rollback()
        finally:
            await engine.dispose()

    asyncio.run(run())
