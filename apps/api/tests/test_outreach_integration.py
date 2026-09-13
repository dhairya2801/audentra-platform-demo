"""Outreach commands persist one delivery with authorized, payload-bound retries."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_document_history_integration import BorrowedEngine, isolated_url

from audentra.bootstrap.api import build_api_runtime
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID
from audentra.integrations.assistant.tools import AssistantToolHost, execute_tool_reads


@pytest.mark.postgres
@pytest.mark.integration
def test_portal_outreach_replay_and_changed_payload_cannot_duplicate_delivery() -> None:
    async def run() -> None:
        runtime = await build_api_runtime(
            RuntimeSettings.from_environment({"DATABASE_URL": isolated_url()})
        )
        service: Any = runtime.service
        repo = service.repository
        try:
            async with runtime.engine.connect() as c, c.begin():
                await c.execute(
                    text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                    {"tenant": SYNTHETIC_TENANT_ID},
                )
                work = (
                    (
                        await c.execute(
                            text("""
                  SELECT id,student_id,version FROM staff_work_item
                  WHERE tenant_id=:tenant AND status NOT IN ('done','cancelled')
                    AND source_type IS DISTINCT FROM 'student_inquiry'
                  ORDER BY id LIMIT 1
                """),
                            {"tenant": SYNTHETIC_TENANT_ID},
                        )
                    )
                    .mappings()
                    .one()
                )
                auth = AuthContext(
                    SYNTHETIC_TENANT_ID,
                    str(work["student_id"]),
                    "01973261-954a-5019-8e9e-24a699abea7b",
                    "staff",
                )
                student = replace(auth, actor_id=auth.student_id, actor_type="student")
                engine = BorrowedEngine(c)
                repo.staff._engine = engine
                repo.portal.engine = engine
                repo.university.engine = engine
                account = await repo.university.record(student, "account")
                initial = await repo.portal.get_student_messages(student)
                start = {
                    "expectedWorkItemVersion": work["version"],
                    "channel": "portal",
                    "objective": "Explain the recorded next step",
                }
                key = str(uuid4())
                begun = await repo.staff.start_interaction(
                    auth, str(work["id"]), start, key, str(uuid4())
                )
                replay_start = await repo.staff.start_interaction(
                    auth, str(work["id"]), start, key, str(uuid4())
                )
                assert replay_start["workItem"] == begun["workItem"]
                assert replay_start["interactions"] == begun["interactions"]
                with pytest.raises(ApiError) as error:
                    await repo.staff.start_interaction(
                        auth,
                        str(work["id"]),
                        {**start, "objective": "Changed objective"},
                        key,
                        str(uuid4()),
                    )
                assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
                assert await repo.portal.get_student_messages(student) == initial
                interaction = begun["interactions"][0]
                command = {
                    "expectedInteractionVersion": interaction["version"],
                    "channel": "portal",
                    "direction": "outbound",
                    "subject": "Canonical outreach test",
                    "body": (
                        "Your next step remains under review; "
                        "this message does not complete your case."
                    ),
                }
                send_key = str(uuid4())
                with pytest.raises(ApiError):
                    await repo.staff.record_interaction_communication(
                        student, interaction["id"], command, send_key, str(uuid4())
                    )
                result = await repo.staff.record_interaction_communication(
                    auth, interaction["id"], command, send_key, str(uuid4())
                )
                replay = await repo.staff.record_interaction_communication(
                    auth, interaction["id"], command, send_key, str(uuid4())
                )
                assert replay["workItem"] == result["workItem"]
                assert replay["interactions"] == result["interactions"]
                for changed in (
                    {**command, "body": "Different message"},
                    {**command, "expectedInteractionVersion": interaction["version"] + 1},
                ):
                    with pytest.raises(ApiError) as error:
                        await repo.staff.record_interaction_communication(
                            auth, interaction["id"], changed, send_key, str(uuid4())
                        )
                    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
                with pytest.raises(ApiError) as error:
                    await repo.staff.record_interaction_communication(
                        auth, interaction["id"], command, str(uuid4()), str(uuid4())
                    )
                assert error.value.code == "VERSION_CONFLICT"
                messages = await repo.portal.get_student_messages(student)
                assert sum(m["body"] == command["body"] for m in messages["items"]) == 1
                records = [
                    m
                    for i in result["interactions"]
                    for m in i["communications"]
                    if m["body"] == command["body"]
                ]
                assert len(records) == 1 and records[0]["deliveryStatus"] == "delivered"
                evidence = await repo.university.record(student, "relationships")
                assert sum(m["body"] == command["body"] for m in evidence["portalInbox"]) == 1
                host = AssistantToolHost(
                    {
                        "university_record": lambda **kwargs: repo.university.record(
                            student, **kwargs
                        )
                    }
                )
                reads = await execute_tool_reads(["getUniversityRelationships"], host)
                assert command["body"] in str(reads)
                latest = next(i for i in result["interactions"] if i["id"] == interaction["id"])
                external = await repo.staff.record_interaction_communication(
                    auth,
                    interaction["id"],
                    {
                        **command,
                        "expectedInteractionVersion": latest["version"],
                        "channel": "email",
                        "body": "External activity recorded, not delivered.",
                    },
                    str(uuid4()),
                    str(uuid4()),
                )
                email = next(
                    m
                    for i in external["interactions"]
                    for m in i["communications"]
                    if m["body"] == "External activity recorded, not delivered."
                )
                assert email["deliveryStatus"] == "recorded"
                assert (await repo.portal.get_student_messages(student))["items"] == messages[
                    "items"
                ]
                assert result["workItem"]["status"] != "done"
                assert await repo.university.record(student, "account") == account
                assert (
                    await c.scalar(
                        text(
                            "SELECT count(*) FROM idempotency_record WHERE tenant_id=:tenant "
                            "AND actor_id=CAST(:actor AS uuid) "
                            "AND idempotency_key IN (:start,:send)"
                        ),
                        {
                            "tenant": auth.tenant_id,
                            "actor": auth.actor_id,
                            "start": key,
                            "send": send_key,
                        },
                    )
                    == 2
                )
                await c.rollback()
        finally:
            await runtime.close()

    asyncio.run(run())
