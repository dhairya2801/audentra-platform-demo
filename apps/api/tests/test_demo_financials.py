"""Financial UI/Edward invariants on an explicitly disposable imported world."""

import asyncio
import json
import os
import runpy
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import text

from audentra.bootstrap.api import build_api_runtime
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.postgres.financial_plan_repository import FinancialPlanService
from audentra.integrations.assistant.read_loop import bound_result


def test_demo_financial_reconciliation_and_complete_edward_evidence() -> None:
    url = os.getenv("AUDENTRA_FINANCIAL_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Requires isolated financial test database")
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
        "/audentra_university_test_financial"
    ):
        raise ValueError("Use an isolated financial test database")

    seed_module = runpy.run_path(
        str(Path(__file__).resolve().parents[3] / "tools/university/seed_ada_financials.py")
    )

    async def run() -> None:
        runtime = await build_api_runtime(RuntimeSettings.from_environment({"DATABASE_URL": url}))
        service: Any = runtime.service
        tenant = "00000000-0000-7000-8000-000000000003"
        async with runtime.engine.connect() as connection:
            sid = await connection.scalar(
                text(
                    "SELECT id FROM university.student WHERE tenant_id=:tenant "
                    "AND external_ref='SYN-000061'"
                ),
                {"tenant": tenant},
            )
        auth = AuthContext(tenant, sid, sid, "student")
        finance = FinancialPlanService(service.repository.university)
        try:
            plan = await finance.read(auth)
            assert plan["account"]["postedChargesCents"] == 2210000
            assert sum(r["amount_cents"] for r in plan["ledger"]) == 713300
            assert plan["account"]["postedBalanceCents"] == 713300
            assert plan["aid"]["anticipatedTermCents"] == 273145
            assert plan["aid"]["termSummary"]["acceptedGiftCents"] == 1296700
            assert plan["aid"]["termSummary"]["offeredGiftCents"] == 1621700
            assert plan["aid"]["termSummary"]["acceptedNetCents"] == 1469845
            assert plan["aid"]["termSummary"]["pendingDecisionCents"] == 425000
            assert plan["planning"]["estimatedAccountGapAfterAnticipatedAidCents"] == 440155
            assert sum(r["amount_cents"] for r in plan["installments"]) == 440155
            assert plan["paymentAgreements"][0]["totalIncludingFeeCents"] == 444655
            assert plan["paymentAgreements"][0]["status"] == "proposed"
            assert plan["exceptions"][0]["status"] == "approved"
            assert plan["exceptions"][0]["approver_name"]
            assert "September 30" in plan["exceptions"][0]["reason"]
            assert plan["planning"]["totalAttendanceEstimateCents"] == 2493000
            assert plan["planning"]["estimatedCushionCents"] == 114500
            spring = await finance.read(auth, "2027SP")
            for award in plan["aid"]["awards"]:
                allocations = [
                    r
                    for r in plan["aid"]["termAwards"] + spring["aid"]["termAwards"]
                    if r["award_id"] == award["id"]
                ]
                assert sum(r["accepted_cents"] for r in allocations) == award["accepted_cents"]
                assert sum(r["offered_cents"] for r in allocations) == award["offered_cents"]
            # Model evidence must retain the last alphabetical award, the loan,
            # all four installments and the final charge, without truncation.
            evidence = bound_result(plan)
            encoded = json.dumps(evidence)
            assert "truncated" not in encoded
            assert len(evidence["aid"]["termAwards"]) == 8
            assert len(evidence["installments"]) == 4
            assert "$1,731.45" in encoded
            assert "$18.55" in encoded
            assert "$4,446.55" in encoded
            assert "$12,967" in encoded
            assert "Federal Pell Grant" in encoded
            account = await service.repository.university.record(auth, "account")
            assert account["balanceAfterAnticipatedAid"][0]["estimatedRemainingCents"] == 440155
            # Seed replay must preserve canonical student edits and posted rows.
            before = json.dumps(plan, sort_keys=True, default=str)
            assert not (await seed_module["seed"](url))["seeded"]
            assert json.dumps(await finance.read(auth), sort_keys=True, default=str) == before
            async with runtime.engine.connect() as connection:
                person = await connection.scalar(
                    text(
                        "SELECT person_id FROM public.student WHERE tenant_id=:tenant AND id=:sid"
                    ),
                    {"tenant": tenant, "sid": sid},
                )
            browser_auth = replace(auth, actor_id=str(person))
            original_inputs = plan["planning"]["inputs"]
            payload = {
                "termId": "2026FA",
                "expectedVersion": plan["planning"]["version"],
                "inputs": {**original_inputs, "booksCents": 60001},
            }
            with pytest.raises(ApiError) as denied:
                await finance.save_inputs(
                    replace(browser_auth, actor_id=str(uuid4())),
                    payload,
                    str(uuid4()),
                    "test-finance",
                )
            assert denied.value.status_code == 403
            receipt = await finance.save_inputs(browser_auth, payload, str(uuid4()), "test-finance")
            edited = await finance.read(auth)
            assert edited["planning"]["inputs"]["booksCents"] == 60001
            assert edited["account"] == plan["account"]
            assert not (await seed_module["seed"](url))["seeded"]
            assert (await finance.read(auth))["planning"]["inputs"]["booksCents"] == 60001
            await finance.save_inputs(
                browser_auth,
                {
                    "termId": "2026FA",
                    "expectedVersion": receipt["version"],
                    "inputs": original_inputs,
                },
                str(uuid4()),
                "test-finance-restore",
            )
        finally:
            await runtime.engine.dispose()

    asyncio.run(run())
