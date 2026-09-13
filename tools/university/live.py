"""PostgreSQL live adapter for the loopback-only Atlas operator UI.

Every SQL read uses the university repository's tenant-filtered CTEs, in a
read-only transaction. Financial Plan and Work Board use the product services.
No SQLite file, oracle or sandbox action can enter this live adapter.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse
from dataclasses import replace
from uuid import UUID

import asyncpg

from audentra.bootstrap.api import build_api_runtime
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.infrastructure.postgres.financial_plan_repository import (
    FinancialPlanService,
)
from audentra.infrastructure.postgres.university_repository import _scope
from audentra.infrastructure.postgres.work_board_repository import WorkBoardProjection
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return next(iter(self.rows), None)

    def fetchall(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class LiveDatabase:
    def __init__(self, url):
        parsed = urlparse(url)
        if parsed.hostname not in (
            "127.0.0.1",
            "localhost",
        ) or not parsed.path.startswith("/audentra_university"):
            raise ValueError(
                "Atlas live mode requires an explicitly chosen loopback university DB"
            )
        self.url = url
        self.runner = asyncio.Runner()

    async def start(self):
        self.connection = await asyncpg.connect(
            self.url,
            server_settings={
                "statement_timeout": "15000",
                "application_name": "audentra-atlas-readonly",
            },
        )
        self.transaction = self.connection.transaction(readonly=True)
        await self.transaction.start()
        await self.connection.execute(
            "SELECT set_config('audentra.tenant_id',$1,true)", SYNTHETIC_TENANT_ID
        )

    def __enter__(self):
        self.runner.run(self.start())
        return self

    def execute(self, query, args=()):
        if not query.lstrip().upper().startswith("SELECT"):
            raise ValueError(
                "Atlas live mode permits only code-owned SELECT statements"
            )
        if "sqlite_master" in query:
            query = "SELECT table_name AS name,table_type AS type,'PostgreSQL university schema' AS sql FROM information_schema.tables WHERE table_schema='university' ORDER BY table_name"
            return Result(self.runner.run(self.connection.fetch(query)))
        query = query.replace(
            "SELECT p.*,count(s.id) AS students FROM program p LEFT JOIN student s ON s.program_id=p.id GROUP BY p.id ORDER BY students DESC",
            "SELECT p.*,(SELECT count(*) FROM student s WHERE s.program_id=p.id) AS students FROM program p ORDER BY students DESC",
        )
        parts = query.split("?")
        query = "".join(
            part + (f"${i + 1}" if i < len(parts) - 1 else "")
            for i, part in enumerate(parts)
        )
        query = _scope(query).replace(":tenant_id", f"${len(args) + 1}")
        return Result(
            self.runner.run(
                self.connection.fetch(query, *args, UUID(SYNTHETIC_TENANT_ID))
            )
        )

    async def close(self):
        await self.transaction.rollback()
        await self.connection.close()

    def __exit__(self, *args):
        try:
            self.runner.run(self.close())
        finally:
            self.runner.close()


async def projection(url, kind, student_id, actor_id, offset=0, work_item_id=None):
    runtime = await build_api_runtime(
        RuntimeSettings.from_environment({"DATABASE_URL": url})
    )
    auth = AuthContext(SYNTHETIC_TENANT_ID, student_id, actor_id, "staff")
    try:
        if kind == "campus-life":
            return await runtime.service.repository.portal.get_campus_life(
                replace(auth, actor_type="student", actor_id=student_id)
            )
        if kind == "documents":
            return await runtime.service.repository.university.record(auth, "documents")
        if kind == "relationships":
            return await runtime.service.repository.university.record(auth, "relationships")
        if kind == "work-item":
            return await runtime.service.repository.staff.get_work_item_detail(
                auth, str(UUID(str(work_item_id)))
            )
        if kind == "student-profile":
            return await runtime.service.repository.portal.get_student_profile(
                replace(auth, actor_type="student", actor_id=student_id)
            )
        if kind == "student-overview":
            return await runtime.service.repository.university.record(auth, "overview")
        if kind == "staff-profile":
            return await runtime.service._advising().get_staff_me(auth)
        if kind == "financial-plan":
            return await FinancialPlanService(
                runtime.service.repository.university
            ).read(auth)
        if kind == "work-board":
            return await WorkBoardProjection(runtime.service.repository.staff).read(
                auth, offset
            )
        raise ValueError("Unknown live projection")
    finally:
        await runtime.close()
