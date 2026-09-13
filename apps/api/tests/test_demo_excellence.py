"""Handcrafted demo invariants and live work actions, without changing demo state."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from test_document_history_integration import isolated_url

from audentra.bootstrap.api import build_api_runtime
from audentra.bootstrap.settings import RuntimeSettings
from audentra.contracts.requests import UpdateStaffWorkItemRequest
from audentra.core.auth import AuthContext
from audentra.domain.edward_action_recognizer import parse_staff_action
from audentra.infrastructure.postgres.edward_action_gateway import EdwardActionGateway
from audentra.infrastructure.postgres.financial_plan_repository import FinancialPlanService
from audentra.infrastructure.postgres.staff_assistant_repository import (
    PostgresStaffAssistantRepository,
)
from audentra.infrastructure.postgres.work_board_repository import WorkBoardProjection
from audentra.integrations.assistant.read_loop import bound_result
from audentra.integrations.staff_assistant.normalize import extract_candidate_name


class BorrowedConnection:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def __await__(self) -> Any:
        async def borrow() -> Any:
            return self

        return borrow().__await__()


class BorrowedEngine:
    def __init__(self, connection: Any) -> None:
        self.connection = BorrowedConnection(connection)

    def connect(self) -> Any:
        return self.connection

    begin = connect


TENANT = "00000000-0000-7000-8000-000000000003"
STUDENT = "ac2fa509-b4e3-402d-900b-ffb8440fc430"
STAFF = "01973261-954a-5019-8e9e-24a699abea7b"


@pytest.mark.parametrize(
    ("message", "fields"),
    [
        ("Change DEMO-126 from Medium to High.", {"priority": "high"}),
        ("Move TASK-123 to In Progress.", {"status": "in_progress"}),
        ("Assign DEMO-126 to me.", {"assignToMe": True}),
        ("Change DEMO-126 due date to tomorrow.", {"dueOn": "tomorrow"}),
    ],
)
def test_board_language_resolves_only_supported_fields(
    message: str, fields: dict[str, Any]
) -> None:
    request = parse_staff_action(message)
    assert request and request.action == "operations.work_item.update"
    assert request.fields == fields


@pytest.mark.parametrize(
    "message",
    [
        "What is the priority of DEMO-126?",
        "Show me TASK-123",
        "Which High priority tasks are due today?",
    ],
)
def test_board_questions_remain_reads(message: str) -> None:
    assert parse_staff_action(message) is None


def test_task_board_is_a_product_surface_not_a_student_name() -> None:
    assert extract_candidate_name("Which Task Board items are due today?") is None
    assert extract_candidate_name("How many open items are in each Task Board project?") is None


def test_direct_ui_patch_accepts_the_shared_priority_and_due_date_fields() -> None:
    request = UpdateStaffWorkItemRequest.model_validate(
        {"expectedVersion": 1, "priority": "high", "dueAt": "2026-09-14T21:00:00Z"}
    )
    assert request.public_payload()["priority"] == "high"
    assert str(request.public_payload()["dueAt"]).startswith("2026-09-14")


@pytest.mark.postgres
@pytest.mark.integration
def test_seeded_story_and_confirmed_work_actions_share_postgres() -> None:
    async def run() -> None:
        runtime = await build_api_runtime(
            RuntimeSettings.from_environment({"DATABASE_URL": isolated_url()})
        )
        service: Any = runtime.service
        repo: Any = service.repository
        try:
            async with runtime.engine.connect() as c:
                transaction = await c.begin()
                try:
                    await c.execute(
                        text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                        {"tenant": TENANT},
                    )
                    seeded = await c.scalar(
                        text(
                            "SELECT value FROM university.meta WHERE tenant_id=:tenant "
                            "AND key='demo-excellence-v1'"
                        ),
                        {"tenant": TENANT},
                    )
                    if not seeded:
                        pytest.skip("Requires the handcrafted demo overlay")
                    engine: Any = BorrowedEngine(c)
                    repo.staff._engine = engine
                    repo.portal.engine = engine
                    repo.university.engine = engine
                    student = AuthContext(TENANT, STUDENT, STUDENT, "student")
                    staff = AuthContext(TENANT, STUDENT, STAFF, "staff")
                    plan = await FinancialPlanService(repo.university).read(student)
                    account = await repo.university.record(student, "account")
                    bounded_account = bound_result(account)
                    assert len(bounded_account["payments"]) == 6
                    assert all("id" in p for p in bounded_account["payments"])
                    assert plan["account"]["postedChargesCents"] == 1689500
                    assert plan["account"]["postedBalanceCents"] == 769750
                    assert plan["account"]["postedAidCents"] == 669750
                    assert plan["account"]["paymentStates"] == {
                        "posted": 250000,
                        "pending": 198250,
                        "failed": 125000,
                        "reversed": 1150000,
                    }
                    assert plan["aid"]["offeredAnnualCents"] == 1889500
                    assert plan["aid"]["acceptedAnnualCents"] == 1689500
                    assert plan["aid"]["anticipatedTermCents"] == 175000
                    assert (
                        sum(r["amountCents"] for r in plan["visualization"]["netPostedSources"])
                        + plan["account"]["postedBalanceCents"]
                        == plan["account"]["postedChargesCents"]
                    )
                    assert sum(r["amount_cents"] for r in plan["installments"]) == 594750
                    assert plan["planning"]["livingTotalCents"] == 175000
                    assert plan["planning"]["incomeTotalCents"] == 580000
                    board = WorkBoardProjection(repo.staff)
                    curated = await board.read(staff, filters={"search": "DEMO-"})
                    assert len(curated["cards"]) == 35
                    assert all(
                        sum(card["board"] == project["id"] for card in curated["cards"]) == 5
                        for project in curated["projects"]
                    )
                    assert all(
                        card["studentId"] and card["key"] and card["version"]
                        for card in curated["cards"]
                    )
                    assistant = PostgresStaffAssistantRepository(engine)
                    # Establish reversible action preconditions even after a browser
                    # evaluation changed these two disposable demo cards. Rolled back.
                    await c.execute(
                        text(
                            "UPDATE public.staff_work_item "
                            "SET priority='high',status='todo',"
                            "due_at='2026-09-14T21:00:00Z' "
                            "WHERE tenant_id=:tenant AND key='DEMO-126'"
                        ),
                        {"tenant": TENANT},
                    )
                    await c.execute(
                        text(
                            "UPDATE public.staff_work_item SET assignee_id=NULL "
                            "WHERE tenant_id=:tenant AND key='DEMO-129'"
                        ),
                        {"tenant": TENANT},
                    )
                    gateway = EdwardActionGateway(engine, repo.portal, repo.staff, assistant)
                    conversation = await assistant.create_conversation(staff)
                    for message, field, after in [
                        ("Change DEMO-126 priority to Low.", "priority", "low"),
                        ("Move DEMO-126 to In Progress.", "status", "in_progress"),
                        ("Assign DEMO-129 to me.", "assigneeId", STAFF),
                        ("Change DEMO-126 due date to tomorrow.", "dueAt", None),
                    ]:
                        key = "DEMO-129" if "129" in message else "DEMO-126"
                        old = (await board.read(staff, filters={"search": key}))["cards"][0]
                        request = parse_staff_action(message)
                        assert request
                        intent = await gateway.propose_staff(
                            staff,
                            request,
                            conversation_id=str(conversation["id"]),
                            trace_id=str(uuid4()),
                            resolved_student_id=None,
                            work_item_key=key,
                        )
                        unchanged = (await board.read(staff, filters={"search": key}))["cards"][0]
                        assert unchanged["version"] == old["version"]
                        receipt = await gateway.confirm(
                            staff,
                            str(intent["id"]),
                            expected_version=int(intent["version"]),
                            content_sha256=str(intent["contentSha256"]),
                            request_id=str(uuid4()),
                        )
                        assert receipt["status"] == "succeeded", receipt
                        replay = await gateway.confirm(
                            staff,
                            str(intent["id"]),
                            expected_version=int(intent["version"]),
                            content_sha256=str(intent["contentSha256"]),
                            request_id=str(uuid4()),
                        )
                        assert replay["id"] == receipt["id"]
                        new = (await board.read(staff, filters={"search": key}))["cards"][0]
                        assert new["version"] == old["version"] + 1
                        actual = new[
                            {
                                "priority": "priority",
                                "status": "operationalStatus",
                                "assigneeId": "owner",
                                "dueAt": "due",
                            }[field]
                        ]
                        if after:
                            assert str(actual).lower() == after.lower()
                        else:
                            assert new["due"] != old["due"]
                    # UI uses the same work command, observed by the shared board read.
                    current = (await board.read(staff, filters={"search": "DEMO-126"}))["cards"][0]
                    await repo.staff.update_work_item(
                        staff,
                        current["id"],
                        {"expectedVersion": current["version"], "priority": "urgent"},
                        str(uuid4()),
                    )
                    assert (await board.read(staff, filters={"search": "DEMO-126"}))["cards"][0][
                        "priority"
                    ] == "Urgent"
                finally:
                    await transaction.rollback()
        finally:
            await runtime.close()

    asyncio.run(run())
