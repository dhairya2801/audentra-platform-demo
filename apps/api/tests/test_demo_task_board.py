"""The demo seed and identity projection use canonical, scoped, durable records."""

from __future__ import annotations

import asyncio
import os
import runpy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from test_demo_document_uploads import exercise_demo_uploads

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.postgres.demo_task_board_repository import DemoTaskBoardProjection
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository


@pytest.mark.postgres
def test_demo_seed_identity_updates_scope_and_preservation() -> None:
    async def run() -> None:
        url = os.getenv("AUDENTRA_CAMILA_TEST_DATABASE_URL", "")
        if not url:
            pytest.skip("Requires AUDENTRA_CAMILA_TEST_DATABASE_URL")
        parsed = urlparse(url)
        if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
            "/audentra_university_test_camila"
        ):
            raise ValueError("Use an isolated Camila test database")
        module = runpy.run_path(
            str(Path(__file__).parents[3] / "tools/university/seed_camila_task_board.py")
        )
        tenant = module["TENANT"]
        staff = UUID("01973261-954a-5019-8e9e-24a699abea7b")
        other = UUID("00000000-0000-7000-8000-000000000999")
        db = await asyncpg.connect(url)
        engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
        try:
            await db.execute(
                "INSERT INTO tenant(id,name) VALUES($1,'Demo test') ON CONFLICT DO NOTHING", tenant
            )
            for member, ref, name in [
                (staff, module["STAFF_REF"], "Camila Abernathy"),
                (other, "TEST-OTHER", "Other adviser"),
            ]:
                await db.execute(
                    "INSERT INTO staff_member "
                    "(id,tenant_id,display_name,email_normalized,component,external_ref) "
                    "VALUES($1,$2,$3,$4,'Academic Advising Center',$5) ON CONFLICT DO NOTHING",
                    member,
                    tenant,
                    name,
                    f"{ref}@example.test",
                    ref,
                )
            await db.execute(
                "INSERT INTO university.office(tenant_id,id,name) "
                "VALUES($1,'test-office','Advising') ON CONFLICT DO NOTHING",
                tenant,
            )
            await db.execute(
                "INSERT INTO university.staff "
                "(tenant_id,id,name,office_id,title,status,capacity,email) "
                "VALUES($1,'camila-test','Camila Abernathy','test-office','Adviser','active',10,"
                "'camila@example.test') ON CONFLICT DO NOTHING",
                tenant,
            )
            await db.execute(
                "INSERT INTO university.program(tenant_id,id,name,department,degree_credits) "
                "VALUES($1,'test-program','Data Science','Science',120) ON CONFLICT DO NOTHING",
                tenant,
            )
            await db.execute(
                "INSERT INTO university.term VALUES($1,'2026FA','Fall 2026','2026-08-01',"
                "'2026-12-20','2026-04-01','2026-09-01','2026-09-15') ON CONFLICT DO NOTHING",
                tenant,
            )
            await db.execute(
                "INSERT INTO university.runtime_link(tenant_id,kind,world_id,runtime_id) "
                "VALUES($1,'staff','camila-test',$2) ON CONFLICT DO NOTHING",
                tenant,
                staff,
            )
            for i, ref in enumerate(module["ROSTER"]):
                student = module["identity"](f"test-student:{ref}")
                person = module["identity"](f"test-person:{ref}")
                await db.execute(
                    "INSERT INTO person(id,tenant_id,first_name,last_name) VALUES($1,$2,$3,$4) "
                    "ON CONFLICT DO NOTHING",
                    person,
                    tenant,
                    "Ada" if i == 0 else f"Student{i}",
                    "Kettleby" if i == 0 else "Demo",
                )
                await db.execute(
                    "INSERT INTO student(id,tenant_id,person_id,class_year,external_ref) "
                    "VALUES($1,$2,$3,2026,$4) ON CONFLICT DO NOTHING",
                    student,
                    tenant,
                    person,
                    ref,
                )
                await db.execute(
                    "INSERT INTO university.student "
                    "(tenant_id,id,external_ref,name,preferred_name,email,program_id,admit_term,"
                    "residency,admit_type,status,birth_date) VALUES($1,$2,$3,$4,$4,$5,"
                    "'test-program','2026FA','in_state','first_year','admitted','2008-01-01') "
                    "ON CONFLICT DO NOTHING",
                    tenant,
                    str(student),
                    ref,
                    "Ada" if i == 0 else f"Student{i}",
                    f"{ref}@example.test",
                )
            ada = module["identity"]("test-student:SYN-000061")
            if not await db.fetchval(
                "SELECT 1 FROM university.meta WHERE tenant_id=$1 AND key=$2",
                tenant,
                module["VERSION"],
            ):
                await db.execute(
                    "INSERT INTO student_staff_assignment "
                    "(id,tenant_id,student_id,staff_member_id,role) "
                    "SELECT $1,$2,$3,$4,'primary_advisor' WHERE NOT EXISTS("
                    "SELECT 1 FROM student_staff_assignment WHERE tenant_id=$2 AND student_id=$3 "
                    "AND role='primary_advisor' AND ended_at IS NULL)",
                    uuid4(),
                    tenant,
                    ada,
                    other,
                )
            await module["seed"](url)
            auth = AuthContext(str(tenant), str(ada), str(staff), "staff")
            projection = DemoTaskBoardProjection(PostgresStaffRepository(engine, cast(Any, None)))
            result = await projection.read(auth)
            assert result["total"] == 64
            assert result["studentCount"] == 10
            assert len({c["id"] for c in result["cards"]}) == 64
            hero = next(c for c in result["cards"] if c["templateKey"] == "ENR-184")
            assert hero["student"]["externalRef"] == "SYN-000061"
            assert hero["student"]["name"] == "Ada Kettleby"
            assert (
                await db.fetchval(
                    "SELECT count(*) FROM student_staff_assignment WHERE tenant_id=$1 "
                    "AND staff_member_id=$2 AND ended_at IS NULL",
                    tenant,
                    staff,
                )
                == 10
            )
            assert (
                await db.fetchval(
                    "SELECT count(*) FROM student_staff_assignment WHERE tenant_id=$1 "
                    "AND student_id=$2 AND staff_member_id=$3 AND ended_at IS NOT NULL",
                    tenant,
                    ada,
                    other,
                )
                == 1
            )
            assert (await module["seed"](url))["seeded"] is False
            assert (
                await db.fetchval("SELECT count(*) FROM staff_work_item WHERE tenant_id=$1", tenant)
                == 64
            )
            for denied in [
                replace(auth, actor_id=str(other)),
                replace(auth, tenant_id=str(uuid4())),
                replace(auth, actor_type="student", actor_id=str(ada)),
            ]:
                with pytest.raises(ApiError):
                    await projection.read(denied)
            # Read back a real identity edit; no names may be copied into the card membership.
            try:
                await db.execute(
                    "UPDATE person SET first_name='Updated Ada' WHERE id=$1",
                    module["identity"]("test-person:SYN-000061"),
                )
                assert (await module["seed"](url))["seeded"] is False
                updated = await projection.read(auth)
                assert (
                    next(c for c in updated["cards"] if c["key"] == "ENR-184")["student"]["name"]
                    == "Updated Ada Kettleby"
                )
            finally:
                await db.execute(
                    "UPDATE person SET first_name='Ada' WHERE id=$1",
                    module["identity"]("test-person:SYN-000061"),
                )
            await exercise_demo_uploads(engine, auth)
        finally:
            await engine.dispose()
            await db.close()

    asyncio.run(run())
