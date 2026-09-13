"""Cross-surface invariants against an explicitly isolated imported PostgreSQL world."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import text

from audentra.bootstrap.api import build_api_runtime
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.core.ports import ServiceCall
from audentra.infrastructure.postgres.financial_plan_repository import FinancialPlanService
from audentra.infrastructure.postgres.work_board_repository import WorkBoardProjection
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID
from audentra.integrations.assistant.tools import AssistantToolHost, execute_tool_reads
from audentra.integrations.staff_assistant.tools import (
    PlannedToolCall,
    StaffAssistantToolHost,
    execute_staff_tool_reads,
)

STUDENT = "ac2fa509-b4e3-402d-900b-ffb8440fc430"
STAFF = "01973261-954a-5019-8e9e-24a699abea7b"


def database_url() -> str:
    url = os.getenv("AUDENTRA_UNIVERSITY_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Requires an isolated imported university test database")
    parsed = urlparse(url)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.path.startswith(
        "/audentra_university_test"
    ):
        raise ValueError("Never run parity mutations on an application database")
    return url


def normalized(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


@pytest.mark.postgres
@pytest.mark.integration
def test_finance_reality_and_planning_write_boundaries() -> None:
    async def run() -> None:
        runtime = await build_api_runtime(
            RuntimeSettings.from_environment({"DATABASE_URL": database_url()})
        )
        service: Any = runtime.service
        university = service.repository.university
        auth = AuthContext(SYNTHETIC_TENANT_ID, STUDENT, STUDENT, "student")
        plan_service = FinancialPlanService(university)
        try:
            async with runtime.engine.connect() as c:
                students = (
                    (
                        await c.execute(
                            text(
                                (
                                    "SELECT id FROM university.student "
                                    "WHERE tenant_id=:tenant ORDER BY "
                                    "external_ref LIMIT 30"
                                )
                            ),
                            {"tenant": auth.tenant_id},
                        )
                    )
                    .scalars()
                    .all()
                )
            for sid in students:
                actor = replace(auth, student_id=str(sid), actor_id=str(sid))
                projection = await plan_service.read(actor)
                api = await service.dispatch(
                    ServiceCall("student.financial_plan", actor, str(uuid4()))
                )
                assert normalized(api) == normalized(projection)
                atlas_read = await plan_service.read(
                    replace(actor, actor_type="staff", actor_id=STAFF)
                )
                assert normalized(atlas_read) == normalized(projection)
                host = AssistantToolHost(
                    {
                        "university_record": lambda bound=actor, **kwargs: university.record(
                            bound, **kwargs
                        )
                    }
                )
                tools = await execute_tool_reads(["getUniversityFinancialPlan"], host)
                assert tools.reads["getUniversityFinancialPlan"]["status"] == "available"
                assert normalized(tools.reads["getUniversityFinancialPlan"]["data"]) == normalized(
                    projection
                )
                async with runtime.engine.connect() as c:
                    balance = await c.scalar(
                        text(
                            "SELECT coalesce(sum(amount_cents),0) FROM university.ledger WHERE "
                            "tenant_id=:tenant AND student_id=:sid AND term_id='2026FA'"
                        ),
                        {"tenant": auth.tenant_id, "sid": str(sid)},
                    )
                assert projection["account"]["postedBalanceCents"] == balance
                assert (await university.record(actor, "account"))[
                    "refundSettlementStatus"
                ] == "not_recorded"
            # Course identifiers come from this catalog, not admissions terminology.
            course_policy = await university.policies(auth, "Can I accept my CS 201 seat offer?")
            assert course_policy["sources"][0]["code"] == "registration-policy"
            admission_policy = await university.policies(auth, "accept my admission offer")
            assert admission_policy["sources"][0]["code"] != "registration-policy"
            before = await plan_service.read(auth)
            payload = {
                "termId": "2026FA",
                "expectedVersion": before["planning"]["version"],
                "inputs": {"savingsCents": 123456, "booksCents": 12345},
            }
            preview = await plan_service.simulate(
                auth, {"termId": "2026FA", "inputs": payload["inputs"]}
            )
            assert preview["recordsChanged"] == 0
            assert (await plan_service.read(auth))["planning"] == before["planning"]
            with pytest.raises(ApiError):
                await plan_service.save_inputs(
                    replace(auth, actor_type="staff", actor_id=STAFF),
                    payload,
                    str(uuid4()),
                    str(uuid4()),
                )
            key = str(uuid4())
            receipt = await plan_service.save_inputs(auth, payload, key, str(uuid4()))
            assert await plan_service.save_inputs(auth, payload, key, str(uuid4())) == receipt
            with pytest.raises(ApiError) as changed:
                await plan_service.save_inputs(
                    auth, {**payload, "inputs": {"savingsCents": 5}}, key, str(uuid4())
                )
            assert changed.value.code == "IDEMPOTENCY_CONFLICT"
            with pytest.raises(ApiError) as stale:
                await plan_service.save_inputs(auth, payload, str(uuid4()), str(uuid4()))
            assert stale.value.code == "VERSION_CONFLICT"
            after = await plan_service.read(auth)
            assert after["account"] == before["account"]
            assert after["aid"] == before["aid"]
            assert after["planning"]["inputs"] == payload["inputs"]
            async with runtime.engine.connect() as c:
                count = await c.scalar(
                    text(
                        "SELECT count(*) FROM audit_event "
                        "WHERE tenant_id=:tenant AND id=CAST(:id"
                        " AS uuid)"
                    ),
                    {"tenant": auth.tenant_id, "id": receipt["receiptId"]},
                )
            assert count == 1
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.postgres
@pytest.mark.integration
def test_work_board_projects_match_evidence_and_edward() -> None:
    async def run() -> None:
        runtime = await build_api_runtime(
            RuntimeSettings.from_environment({"DATABASE_URL": database_url()})
        )
        service: Any = runtime.service
        auth = AuthContext(SYNTHETIC_TENANT_ID, STUDENT, STAFF, "staff")
        board = WorkBoardProjection(service.repository.staff)
        try:
            all_work = await board.read(auth)
            from audentra.integrations.assistant.read_loop import bound_result

            bounded = bound_result(all_work)
            assert bounded["projectCounts"] == all_work["projectCounts"]
            assert bounded["paymentStateCounts"] == all_work["paymentStateCounts"]
            assert sum(all_work["projectCounts"].values()) == all_work["page"]["total"]
            for project, count in all_work["projectCounts"].items():
                loaded = await board.read(auth, project=project)
                assert loaded["page"]["total"] == count
                assert all(card["board"] == project for card in loaded["cards"])
                for card in loaded["cards"]:
                    item = next(r for r in loaded["items"] if r["id"] == card["id"])
                    assert card["version"] == item["version"]
                    assert card["due"] == item["dueAt"]
                    assert card["owner"] == (item["assignee"]["id"] if item["assignee"] else "TEAM")
                    if card["payment"]:
                        assert card["amountCents"] == card["payment"]["amount_cents"]
                        assert card["status"] == (
                            "completed"
                            if card["payment"]["status"] == "posted"
                            else "processing"
                            if card["payment"]["status"] == "pending"
                            else "exception"
                        )
            host = StaffAssistantToolHost(
                service._university_staff_primitives(auth), staff_member_id=STAFF
            )
            result = await execute_staff_tool_reads(
                [PlannedToolCall(tool="getUniversityWorkBoard", arguments={})],
                host,
                now=datetime.now(UTC),
            )
            data = result.reads["getUniversityWorkBoard"]["data"]
            assert normalized(data["cards"]) == normalized(all_work["cards"])
            assert data["projectCounts"] == all_work["projectCounts"]
            with pytest.raises(ApiError):
                await board.read(replace(auth, actor_type="student", actor_id=STUDENT))
        finally:
            await runtime.close()

    asyncio.run(run())
