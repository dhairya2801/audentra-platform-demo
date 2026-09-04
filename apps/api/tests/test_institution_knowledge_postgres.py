"""Corpus import and retrieval on PostgreSQL.

Opt-in (postgres marker): migrates an empty database, provisions a bare
tenant, imports the packaged aster-demo corpus twice (the second import must
be a no-op), and checks the retrieval policy end to end — the right document
first for the questions the suite cares about, audience scoping, effective
windows, applicability from a student's record, calendar and office matches.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.db.migrations import resolve_migrations_directory, run_migrations
from audentra.infrastructure.postgres.knowledge_repository import (
    PostgresInstitutionKnowledgeRepository,
    SearchQuery,
    StudentFacets,
    student_facets,
)
from audentra.infrastructure.seeding.institution_knowledge import (
    default_corpus_root,
    import_corpus,
    load_corpus,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

TENANT_ID = "00000000-0000-7000-8000-0000000000ee"


def _database_url() -> str:
    url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run the knowledge integration test")
    return url


async def _prepare(database_url: str) -> None:
    await run_migrations(database_url, resolve_migrations_directory())
    engine = create_database_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO tenant (id, slug, name)
                    VALUES (:id, 'knowledge-test', 'Knowledge Test University')
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": TENANT_ID},
            )
            # The scratch database survives between runs; start from an empty tenant so
            # the first import really inserts and the second really finds nothing changed.
            for statement in (
                "DELETE FROM institution_knowledge_section WHERE tenant_id = :id",
                "DELETE FROM institution_knowledge_document WHERE tenant_id = :id",
                "DELETE FROM institution_office WHERE tenant_id = :id",
                "DELETE FROM academic_calendar_entry WHERE tenant_id = :id",
                "DELETE FROM help_article WHERE tenant_id = :id",
            ):
                await connection.execute(text(statement), {"id": TENANT_ID})
    finally:
        await engine.dispose()


def test_import_is_idempotent_and_search_ranks_the_right_documents() -> None:
    database_url = _database_url()

    async def run() -> None:
        await _prepare(database_url)
        engine = create_database_engine(database_url)
        try:
            await _exercise(engine)
        finally:
            await engine.dispose()

    asyncio.run(run())


async def _exercise(engine: AsyncEngine) -> None:
    corpus = load_corpus(default_corpus_root("aster-demo"))
    first = await import_corpus(engine, corpus, tenant_id=TENANT_ID)
    assert first.documents_inserted == len(corpus.documents)
    assert first.sections == sum(len(d.sections) for d in corpus.documents)
    second = await import_corpus(engine, corpus, tenant_id=TENANT_ID)
    assert second.documents_inserted == 0 and second.documents_updated == 0
    assert second.documents_unchanged == len(corpus.documents)

    repository = PostgresInstitutionKnowledgeRepository(
        engine, clock=lambda: datetime(2026, 9, 4, tzinfo=UTC)
    )
    assert await repository.available(TENANT_ID)

    first_year_domestic = student_facets(
        residency_status="domestic",
        citizenship_status="us_citizen",
        class_year=2030,
        admit_term_name="Fall 2026",
        admit_term_starts_on=datetime(2026, 8, 31).date(),
        housing_preference="off_campus",
        program_code="BS-DS",
        program_department="Computing",
    )
    transfer = student_facets(
        residency_status="domestic",
        citizenship_status="us_citizen",
        class_year=2028,
        admit_term_name="Fall 2026",
        admit_term_starts_on=datetime(2026, 8, 31).date(),
        housing_preference="on_campus",
        program_code="BS-CE",
        program_department="Engineering",
    )
    international = student_facets(
        residency_status="international",
        citizenship_status="international",
        class_year=2030,
        admit_term_name="Fall 2026",
        admit_term_starts_on=datetime(2026, 8, 31).date(),
        housing_preference="on_campus",
        program_code="BS-EE",
        program_department="Engineering",
    )

    async def top(
        question: str, facets: StudentFacets | None = None, audience: str = "student"
    ) -> dict[str, Any]:
        result = await repository.search(
            TENANT_ID, SearchQuery(text=question, audience=audience), facets=facets
        )
        assert result["documents"], question
        return result

    domestic = first_year_domestic
    expectations = [
        ("what happens if I miss the deposit deadline?", "enrollment-deposit-policy", domestic),
        ("can freshmen live off campus?", "housing-first-year-residency", domestic),
        ("do I have to live on campus?", "housing-first-year-residency", transfer),
        ("when is the add/drop deadline", "registration-policy", domestic),
        (
            "how much is tuition for international students",
            "tuition-and-fees-2026-2027",
            international,
        ),
        (
            "will I lose my aid if my GPA drops below 2.0",
            "satisfactory-academic-progress-policy",
            None,
        ),
        (
            "can I work on campus with an F-1 visa",
            "international-student-employment",
            international,
        ),
        (
            "when does financial aid pay out",
            "financial-aid-award-acceptance-and-disbursement",
            None,
        ),
        ("what classes do I take first semester for computer science", "program-bs-cs", None),
    ]
    for question, expected, facets in expectations:
        result = await top(question, facets)
        assert result["documents"][0]["code"] == expected, (
            question,
            [d["code"] for d in result["documents"]],
        )

    # Applicability is computed from the record, not inferred.
    residency = await top("do I have to live on campus?", transfer)
    assert residency["documents"][0]["applicability"]["verdict"] == "does_not_apply"
    residency = await top("do I have to live on campus?", first_year_domestic)
    assert residency["documents"][0]["applicability"]["verdict"] == "applies"

    # Students never see internal procedures; staff do.
    student_view = await repository.search(
        TENANT_ID,
        SearchQuery(text="deposit extension procedure for counselors", audience="student"),
    )
    assert all(d["audience"] != "internal" for d in student_view["documents"])
    staff_view = await repository.search(
        TENANT_ID, SearchQuery(text="can we extend a deposit deadline", audience="staff")
    )
    assert any(
        d["code"] == "int-deposit-extension-and-waiver-procedure" for d in staff_view["documents"]
    )

    # Calendar entries and offices ride along when the question names them.
    calendar = await top("when is the add/drop deadline", None)
    assert any(entry["code"] == "fall2026-add-drop-deadline" for entry in calendar["calendar"])
    offices = await top("who do I talk to about a hold on my account", None)
    assert any(office["code"] == "SA" for office in offices["offices"])

    # A document outside its effective window is invisible.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE institution_knowledge_document SET effective_until = effective_from "
                "WHERE tenant_id = :tenant_id AND code = 'enrollment-deposit-policy'"
            ),
            {"tenant_id": TENANT_ID},
        )
    expired = await repository.search(
        TENANT_ID, SearchQuery(text="deposit refund deadline", audience="student")
    )
    assert all(d["code"] != "enrollment-deposit-policy" for d in expired["documents"])

    # Facets from a real student row resolve through the same query the service uses.
    async with engine.begin() as connection:
        person_id, student_id = str(uuid4()), str(uuid4())
        await connection.execute(
            text(
                "INSERT INTO person (id, tenant_id, first_name, last_name) "
                "VALUES (:id, :tenant_id, 'Test', 'Student')"
            ),
            {"id": person_id, "tenant_id": TENANT_ID},
        )
        await connection.execute(
            text(
                "INSERT INTO student (id, tenant_id, person_id, class_year) "
                "VALUES (:id, :tenant_id, :person_id, 2030)"
            ),
            {"id": student_id, "tenant_id": TENANT_ID, "person_id": person_id},
        )
        await connection.execute(
            text(
                "INSERT INTO student_onboarding (tenant_id, student_id, status, current_step, "
                "completed_steps, payload, version) "
                "VALUES (:tenant_id, :student_id, 'in_progress', 'offer', '{}', "
                "CAST(:payload AS jsonb), 1)"
            ),
            {
                "tenant_id": TENANT_ID,
                "student_id": student_id,
                "payload": '{"residencyStatus": "international", "housingPreference": "on_campus"}',
            },
        )
    facets = await repository.facets_for_student(TENANT_ID, student_id)
    assert facets is not None and facets.residency == "international"
    assert facets.housing_plan == "on_campus"
    assert facets.class_standing is None  # no admission offer: unknown, never guessed
