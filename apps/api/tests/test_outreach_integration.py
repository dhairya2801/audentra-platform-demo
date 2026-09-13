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
from audentra.infrastructure.postgres.work_board_repository import WorkBoardProjection
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


@pytest.mark.postgres
@pytest.mark.integration
def test_saved_draft_is_private_and_delivery_binds_the_reviewed_version() -> None:
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
                    SELECT w.id,w.student_id,w.version FROM staff_work_item w
                    WHERE w.tenant_id=:tenant AND w.status NOT IN ('done','cancelled')
                      AND w.source_type IS DISTINCT FROM 'student_inquiry'
                      AND w.component NOT ILIKE '%housing%'
                      AND NOT EXISTS(SELECT 1 FROM staff_outreach_draft d
                        WHERE d.tenant_id=w.tenant_id AND d.work_item_id=w.id)
                    ORDER BY w.id LIMIT 1
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
                student = replace(auth, actor_type="student", actor_id=auth.student_id)
                engine = BorrowedEngine(c)
                repo.staff._engine = engine
                repo.portal.engine = engine
                repo.university.engine = engine
                original_inbox = await repo.portal.get_student_messages(student)
                account = await repo.university.record(student, "account")
                before = await repo.staff.get_work_item_detail(auth, str(work["id"]))
                await c.execute(
                    text(
                        "UPDATE staff_work_item SET work_type='communication', "
                        "action_type='communication_response', source_type=NULL, source_id=NULL "
                        "WHERE tenant_id=:tenant AND id=:id"
                    ),
                    {"tenant": auth.tenant_id, "id": work["id"]},
                )
                board = WorkBoardProjection(repo.staff)
                request = {
                    "expectedWorkItemVersion": work["version"],
                    "expectedDraftVersion": 0,
                    "subject": "Saved outreach",
                    "body": "PRIVATE-DRAFT: Staff are still composing this message.",
                }
                key = str(uuid4())
                with pytest.raises(ApiError):
                    await repo.staff.save_outreach_draft(
                        student, str(work["id"]), request, key, str(uuid4())
                    )
                saved = await repo.staff.save_outreach_draft(
                    auth, str(work["id"]), request, key, str(uuid4())
                )
                assert saved == await repo.staff.save_outreach_draft(
                    auth, str(work["id"]), request, key, str(uuid4())
                )
                draft = saved["draft"]
                assert draft["status"] == "draft" and draft["version"] == 1
                detail = await repo.staff.get_work_item_detail(auth, str(work["id"]))
                assert detail["outreachDraft"] == draft
                cards = await board.read(auth, filters={"search": str(work["id"])})
                assert cards["cards"][0]["status"] == "drafting"
                assert cards["cards"][0]["outreachDraft"]["id"] == draft["id"]
                assert detail["interactions"] == before["interactions"]
                assert await repo.portal.get_student_messages(student) == original_inbox
                assert "PRIVATE-DRAFT" not in str(
                    await repo.university.record(student, "relationships")
                )
                with pytest.raises(ApiError) as error:
                    await repo.staff.save_outreach_draft(
                        auth,
                        str(work["id"]),
                        {**request, "body": "Different draft"},
                        key,
                        str(uuid4()),
                    )
                assert error.value.code == "IDEMPOTENCY_KEY_REUSED"
                with pytest.raises(ApiError) as error:
                    await repo.staff.save_outreach_draft(
                        auth,
                        str(work["id"]),
                        {**request, "expectedWorkItemVersion": saved["workItemVersion"]},
                        str(uuid4()),
                        str(uuid4()),
                    )
                assert error.value.code == "DRAFT_VERSION_CONFLICT"
                started = await repo.staff.start_interaction(
                    auth,
                    str(work["id"]),
                    {
                        "expectedWorkItemVersion": saved["workItemVersion"],
                        "channel": "portal",
                        "objective": "Draft delivery test",
                    },
                    str(uuid4()),
                    str(uuid4()),
                )
                interaction = started["interactions"][0]
                send = {
                    "expectedInteractionVersion": interaction["version"],
                    "channel": "portal",
                    "direction": "outbound",
                    "subject": draft["subject"],
                    "body": "Unreviewed changed content",
                    "draftId": draft["id"],
                    "expectedDraftVersion": draft["version"],
                }
                with pytest.raises(ApiError) as error:
                    await repo.staff.record_interaction_communication(
                        auth, interaction["id"], send, str(uuid4()), str(uuid4())
                    )
                assert error.value.code == "DRAFT_CONTENT_CHANGED"
                revised = await repo.staff.save_outreach_draft(
                    auth,
                    str(work["id"]),
                    {
                        "expectedWorkItemVersion": started["workItem"]["version"],
                        "expectedDraftVersion": draft["version"],
                        "subject": draft["subject"],
                        "body": "Reviewed guidance for the student.",
                    },
                    str(uuid4()),
                    str(uuid4()),
                )
                with pytest.raises(ApiError) as error:
                    await repo.staff.record_interaction_communication(
                        auth,
                        interaction["id"],
                        {**send, "body": draft["body"]},
                        str(uuid4()),
                        str(uuid4()),
                    )
                assert error.value.code == "DRAFT_VERSION_CONFLICT"
                assert await repo.portal.get_student_messages(student) == original_inbox
                final_input = {
                    **send,
                    "body": revised["draft"]["body"],
                    "expectedDraftVersion": revised["draft"]["version"],
                }
                send_key = str(uuid4())
                result = await repo.staff.record_interaction_communication(
                    auth, interaction["id"], final_input, send_key, str(uuid4())
                )
                replay = await repo.staff.record_interaction_communication(
                    auth, interaction["id"], final_input, send_key, str(uuid4())
                )
                sent = result["outreachDraft"]
                assert sent == replay["outreachDraft"]
                assert (
                    sent["status"] == "sent" and sent["version"] == revised["draft"]["version"] + 1
                )
                communications = [
                    m
                    for i in result["interactions"]
                    for m in i["communications"]
                    if m["id"] == sent["communicationId"]
                ]
                assert (
                    len(communications) == 1 and communications[0]["deliveryStatus"] == "delivered"
                )
                assert (
                    sum(
                        m["body"] == final_input["body"]
                        for m in (await repo.portal.get_student_messages(student))["items"]
                    )
                    == 1
                )
                assert result["workItem"]["status"] != "done"
                assert await repo.university.record(student, "account") == account
                assert "PRIVATE-DRAFT" not in str(
                    await repo.university.record(student, "relationships")
                )
                cards = await board.read(auth, filters={"search": str(work["id"])})
                assert cards["cards"][0]["status"] == "waiting"
                latest = next(i for i in result["interactions"] if i["id"] == interaction["id"])
                await repo.staff.record_interaction_communication(
                    auth,
                    interaction["id"],
                    {
                        "expectedInteractionVersion": latest["version"],
                        "channel": "portal",
                        "direction": "inbound",
                        "body": "Student reply recorded for review.",
                    },
                    str(uuid4()),
                    str(uuid4()),
                )
                cards = await board.read(auth, filters={"search": str(work["id"])})
                assert cards["cards"][0]["status"] == "responded"
                assert cards["cards"][0]["communication"]["deliveryStatus"] == "received"
                await c.rollback()
        finally:
            await runtime.close()

    asyncio.run(run())
