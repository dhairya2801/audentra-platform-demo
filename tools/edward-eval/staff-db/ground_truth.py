"""Ground-truth extractor for the Staff Edward database-backed eval suite.

Reads the SAME canonical sources Staff Edward and the Staff Portal read —
the production `StaffAssistantToolHost` built by `PostgresPlatformService`
and executed through `execute_staff_tool_reads` — so every expected fact in
the eval suite is the product's own answer to the same read, never a
re-implementation of it. Nothing here writes.

Usage (from the repo root):

    DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:55432/vv_enrollment \
    uv run --directory apps/api --locked python \
        ../../tools/edward-eval/staff-db/ground_truth.py \
        > artifacts/staff-db-eval/ground-truth.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.domain.student_cohort import CohortFilter
from audentra.infrastructure.db.engine import DatabaseEngineOptions, create_database_engine
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
)
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.postgres_service import (
    PostgresPlatformService,
    PostgresRepositoryBundle,
)
from audentra.infrastructure.postgres.staff_assistant_repository import (
    PostgresStaffAssistantRepository,
)
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.integrations.staff_assistant.tools import (
    PlannedToolCall,
    execute_staff_tool_reads,
)

TENANT_ID = os.getenv("STAFF_EVAL_TENANT_ID", "00000000-0000-7000-8000-000000000003")
STAFF_ACTOR_ID = os.getenv("STAFF_EVAL_ACTOR_ID", "30000000-0000-7000-8000-000000000901")
DEFAULT_DATABASE_URL = "postgresql://vv:vv_local_password@127.0.0.1:55432/vv_enrollment"

# The ten named synthetic personas plus any extra refs a case needs.
PERSONA_REFS = [f"SYN-{index:06d}" for index in range(10)]

# Names the ambiguity cases exercise. Each must have >1 canonical match.
AMBIGUOUS_NAMES = ["Caleb Dunmire", "Helia Fennwick", "Quillfeather"]


def _auth() -> AuthContext:
    return AuthContext(
        tenant_id=TENANT_ID,
        student_id="00000000-0000-0000-0000-000000000000",
        actor_id=STAFF_ACTOR_ID,
        actor_type="staff",
        tenant_slug=None,
    )


def _service(engine: Any) -> PostgresPlatformService:
    portal = PostgresPortalRepository(engine)
    return PostgresPlatformService(
        PostgresRepositoryBundle(
            platform=PostgresPlatformRepository(engine),
            portal=portal,
            staff=PostgresStaffRepository(engine, portal),
            managed=PostgresManagedConfigurationRepository(engine),
            staff_assistant=PostgresStaffAssistantRepository(engine),
        ),
        storage=cast(Any, None),
        ai=cast(Any, None),
        signed_documents=cast(Any, None),
        worker_token="ground-truth-read-only",  # noqa: S106
    )


async def _read(host: Any, tool: str, **arguments: Any) -> dict[str, Any]:
    """One production tool read; raises if the read is not available."""

    result = await execute_staff_tool_reads([PlannedToolCall(tool=tool, arguments=arguments)], host)
    read = result.reads.get(tool, {})
    if read.get("status") != "available":
        raise RuntimeError(f"{tool} read unavailable: {read.get('reason')}")
    return dict(read["data"])


async def _student_ids_by_external_ref(engine: Any) -> dict[str, dict[str, Any]]:
    sql = text(
        """
        SELECT s.id, s.external_ref, p.first_name, p.last_name
        FROM student s JOIN person p ON p.id = s.person_id
        WHERE s.tenant_id = CAST(:tenant AS uuid) AND s.external_ref = ANY(:refs)
        """
    )
    async with engine.connect() as connection:
        rows = (
            (await connection.execute(sql, {"tenant": TENANT_ID, "refs": PERSONA_REFS}))
            .mappings()
            .all()
        )
    return {
        str(row["external_ref"]): {
            "id": str(row["id"]),
            "name": f"{row['first_name']} {row['last_name']}",
        }
        for row in rows
    }


async def _profile_student(host: Any, assistant: Any, auth: AuthContext, student_id: str) -> dict:
    summary = await _read(host, "getStudentStaffSummary", studentId=student_id)
    requirements = await _read(host, "getStudentRequirements", studentId=student_id)
    documents = await _read(host, "getStudentDocuments", studentId=student_id)
    blockers = await _read(host, "getStudentBlockers", studentId=student_id)
    deadlines = await _read(host, "getStudentDeadlines", studentId=student_id)
    housing = await _read(host, "getStudentHousingState", studentId=student_id)
    financials = await _read(host, "getStudentFinancialState", studentId=student_id)
    work = await assistant.get_student_work_items(auth, student_id)
    open_requirements = [
        item
        for item in requirements.get("items", [])
        if str(item.get("status")) not in {"completed", "waived", "not_applicable"}
    ]
    return {
        "summary": summary,
        "openRequirements": open_requirements,
        "completedRequirementCount": len(requirements.get("items", []))
        - len(open_requirements),
        "documents": documents.get("items", []),
        "blockers": blockers.get("derivedBlockers", []),
        "deadlines": deadlines.get("items", []),
        "housing": housing,
        "financials": financials,
        "workItems": [
            {
                "key": item.get("key"),
                "title": item.get("title"),
                "status": item.get("status"),
                "priority": item.get("priority"),
                "dueAt": item.get("dueAt"),
            }
            for item in work.get("items", [])
        ],
    }


async def main() -> None:
    database_url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    engine = create_database_engine(database_url, DatabaseEngineOptions())
    auth = _auth()
    service = _service(engine)
    assistant = PostgresStaffAssistantRepository(engine)
    host = service._staff_assistant_host(auth)  # noqa: SLF001 — the production host, on purpose

    truth: dict[str, Any] = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "tenantId": TENANT_ID,
    }

    # --- Personas ---------------------------------------------------------
    refs = await _student_ids_by_external_ref(engine)
    personas: dict[str, Any] = {}
    for ref in PERSONA_REFS:
        entry = refs.get(ref)
        if entry is None:
            continue
        profile = await _profile_student(host, assistant, auth, entry["id"])
        profile["externalRef"] = ref
        personas[ref] = profile
    truth["personas"] = personas

    # --- Ambiguous name groups -------------------------------------------
    name_groups: dict[str, Any] = {}
    for name in AMBIGUOUS_NAMES:
        search = await _read(host, "searchStudents", query=name, limit=25)
        matches = search.get("items", [])
        name_groups[name] = {
            "count": len(matches),
            "students": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "programName": item.get("programName"),
                    "classYear": item.get("classYear"),
                }
                for item in matches
            ],
        }
    truth["nameGroups"] = name_groups

    # --- Action Center (canonical work queue) -----------------------------
    queue = await _read(host, "getStaffWorkQueue")
    items = queue.get("items", [])
    # The full, unbounded queue (the tool pages to 25) for topic counts.
    raw_queue = dict(await host.read("work_queue"))
    raw_items = [dict(item) for item in raw_queue.get("items", [])]
    open_statuses = {"todo", "in_progress", "follow_up_required", "blocked"}
    transcript_items = [
        item
        for item in raw_items
        if str(item.get("status")) in open_statuses
        and "transcript" in str(item.get("title") or "").lower()
    ]
    truth["queue"] = {
        "counts": queue.get("counts", {}),
        "filteredTotal": queue.get("filteredTotal"),
        "head": items[:10],
        "transcript": {
            "openItems": len(transcript_items),
            "distinctStudents": len(
                {str((item.get("student") or {}).get("id")) for item in transcript_items}
            ),
            "sampleStudents": [
                str((item.get("student") or {}).get("name")) for item in transcript_items[:5]
            ],
        },
    }

    # --- Attention queue --------------------------------------------------
    attention = await _read(host, "getStudentsNeedingAttention", limit=15)
    truth["attentionQueue"] = attention

    # Per-persona Action Center / attention membership.
    attention_full = await assistant.get_students_needing_attention(auth, limit=50)
    attention_ids = {str(item["student"]["id"]) for item in attention_full.get("items", [])}
    for ref, profile in personas.items():
        student_id = str(profile["summary"]["id"])
        profile["inActionCenter"] = bool(profile["workItems"])
        profile["inTopAttention"] = student_id in attention_ids

    # --- Cohort truths, via the same canonical CohortSql ------------------
    async def cohort_total(**filters: Any) -> int:
        result = await assistant.find_students(auth, CohortFilter(**filters), limit=1)
        return int(result.total)

    async def cohort_page(limit: int = 20, **filters: Any) -> dict[str, Any]:
        result = await assistant.find_students(auth, CohortFilter(**filters), limit=limit)
        payload = result.as_json()
        return {
            "total": payload["total"],
            "names": [str(item.get("name")) for item in payload["items"]],
        }

    summaries: dict[str, Any] = {}
    blocker_breakdown = await assistant.summarize_students(
        auth, CohortFilter(), group_by="blocking_requirement"
    )
    summaries["blockerBreakdown"] = blocker_breakdown
    onboarding_by_program = await assistant.summarize_students(
        auth, CohortFilter(onboarding_status="in_progress"), group_by="program"
    )
    summaries["onboardingInProgressByProgram"] = onboarding_by_program

    # --- Derived case anchors ---------------------------------------------
    caleb_group = name_groups.get("Caleb Dunmire", {}).get("students", [])
    truth["derived"] = {
        "calebDunmireCivilEngineering": next(
            (
                item
                for item in caleb_group
                if str(item.get("programName")) == "Civil Engineering"
            ),
            None,
        ),
    }

    truth["cohorts"] = {
        "totalStudents": await cohort_total(),
        "depositUnpaid": await cohort_total(deposit_state="unpaid"),
        "depositPending": await cohort_total(deposit_state="pending"),
        "depositPaid": await cohort_total(deposit_state="paid"),
        "transcriptsMissing": await cohort_page(
            document_category="transcript", document_state="missing"
        ),
        "housingBlocked": await cohort_total(housing_state="blocked"),
        "overdueRequirements": await cohort_total(has_overdue_requirement=True),
        "internationalOnboardingIncomplete": await cohort_total(
            residency_status="international", onboarding_status="in_progress"
        ),
        "offeredNotAccepted": await cohort_total(offer_status="offered"),
        "summaries": summaries,
    }

    await engine.dispose()
    json.dump(truth, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    asyncio.run(main())
