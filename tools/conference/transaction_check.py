"""Deterministic integration fault injection on the isolated local database."""

import asyncio, json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from runtime import environment, ARTIFACTS
from audentra.core.auth import AuthContext
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.demo_document_reset import reset_documents

T = "00000000-0000-7000-8000-000000000003"
S = "3bedfe91-6802-4937-893b-72cb7779ecfa"
A = "01973261-954a-5019-8e9e-24a699abea7b"


async def main():
    engine = create_async_engine(
        environment()["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    )
    portal = PostgresPortalRepository(engine)

    async def snapshot():
        async with engine.connect() as c:
            return {
                table: (
                    await c.execute(
                        text(
                            f"SELECT row_to_json(t)::text FROM {table} t ORDER BY row_to_json(t)::text"
                        )
                    )
                )
                .scalars()
                .all()
                for table in [
                    "document_record",
                    "student_requirement",
                    "staff_work_item",
                    "staff_demo_board_card",
                    "financial_document_requirement",
                ]
            }

    before = await snapshot()

    async def fail(*args, **kwargs):
        raise RuntimeError("Deterministic failure before commit")

    portal._insert_audit = fail
    try:
        await reset_documents(
            portal, AuthContext(T, S, A, "staff"), "SYN-000061", "transaction-test"
        )
    except RuntimeError as e:
        assert str(e) == "Deterministic failure before commit"
    else:
        raise AssertionError("Fault injection did not fire")
    assert before == await snapshot()
    (ARTIFACTS / "transaction-results.json").write_text(
        json.dumps(
            {
                "rollbackPreservesEveryRow": True,
                "method": "Injected failure before audit in real database transaction; no model call",
            }
        )
    )
    print(
        "PASS: injected partial-reset failure rolled back every document, requirement, work item, board card and financial requirement"
    )
    await engine.dispose()


asyncio.run(main())
