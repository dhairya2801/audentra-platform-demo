"""Seed Camila's ten-student, 64-card demo once; preserve subsequent edits."""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg

TENANT = UUID("00000000-0000-7000-8000-000000000003")
STAFF_REF = "AU-55ff7e408818"
VERSION = "camila-board-v1"
ROSTER = ["SYN-000061", *[f"SYN-{i:06d}" for i in range(9)]]


def identity(key):
    return uuid5(NAMESPACE_URL, f"audentra:{VERSION}:{key}")


async def seed(url):
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
        "/audentra_university"
    ):
        raise ValueError("Choose an explicit local audentra_university database")
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/camila-task-board.json").read_text()
    )
    db = await asyncpg.connect(url)
    try:
        async with db.transaction():
            await db.execute(
                "SELECT set_config('audentra.tenant_id',$1,true)", str(TENANT)
            )
            await db.execute("SELECT pg_advisory_xact_lock(hashtext($1))", VERSION)
            if await db.fetchval(
                "SELECT value FROM university.meta WHERE tenant_id=$1 AND key=$2",
                TENANT,
                VERSION,
            ):
                return {
                    "seeded": False,
                    "reason": "Already seeded; all subsequent edits preserved",
                }
            staff = await db.fetchval(
                "SELECT id FROM public.staff_member WHERE tenant_id=$1 AND external_ref=$2 AND active",
                TENANT,
                STAFF_REF,
            )
            if not staff:
                raise ValueError("Camila must exist as an active staff member")
            students = {}
            for ref in ROSTER:
                student = await db.fetchval(
                    "SELECT id FROM public.student WHERE tenant_id=$1 AND external_ref=$2",
                    TENANT,
                    ref,
                )
                if not student:
                    raise ValueError(f"Missing roster student {ref}")
                students[ref] = student
            # Preserve adviser history; only Ada changes adviser in an existing seeded world.
            for ref, student in students.items():
                await db.execute(
                    "UPDATE public.student_staff_assignment SET ended_at=NOW() "
                    "WHERE tenant_id=$1 AND student_id=$2 AND role='primary_advisor' "
                    "AND ended_at IS NULL AND staff_member_id<>$3",
                    TENANT,
                    student,
                    staff,
                )
                await db.execute(
                    "INSERT INTO public.student_staff_assignment "
                    "(id,tenant_id,student_id,staff_member_id,role,source,note) "
                    "SELECT $1,$2,$3,$4,'primary_advisor','demo_scenario',"
                    "'Camila demo roster; historical relationships preserved.' "
                    "WHERE NOT EXISTS(SELECT 1 FROM public.student_staff_assignment "
                    "WHERE tenant_id=$2 AND student_id=$3 AND role='primary_advisor' AND ended_at IS NULL)",
                    identity(f"assignment:{ref}"),
                    TENANT,
                    student,
                    staff,
                )
            # Keep the imported university's read model aligned with the real assignment.
            world_staff = await db.fetchval(
                "SELECT world_id FROM university.runtime_link WHERE tenant_id=$1 "
                "AND kind='staff' AND runtime_id=$2",
                TENANT,
                staff,
            )
            if not world_staff:
                raise ValueError("Camila's university staff link is missing")
            for ref, student in students.items():
                await db.execute(
                    "UPDATE university.assignment SET ends_at=NOW()::text "
                    "WHERE tenant_id=$1 AND student_id=$2 AND role='academic_adviser' "
                    "AND ends_at IS NULL AND staff_id<>$3",
                    TENANT,
                    str(student),
                    world_staff,
                )
                await db.execute(
                    "INSERT INTO university.assignment "
                    "(tenant_id,id,student_id,staff_id,role,starts_at,reason) "
                    "SELECT $1,$2,$3,$4,'academic_adviser',NOW()::text,'Camila demo roster' "
                    "WHERE NOT EXISTS(SELECT 1 FROM university.assignment WHERE tenant_id=$1 "
                    "AND student_id=$3 AND role='academic_adviser' AND ends_at IS NULL)",
                    TENANT,
                    str(identity(f"world-assignment:{ref}")),
                    str(student),
                    world_staff,
                )
            for card in fixture["cards"]:
                work_id = identity(card["key"])
                kind = card["type"]
                work_type = {
                    "document": "document_review",
                    "outreach": "communication",
                }.get(kind, "enrollment")
                action = {
                    "document": "document_review",
                    "outreach": "communication_response",
                }.get(kind, "enrollment_follow_up")
                component = (
                    "Housing"
                    if card["board"].startswith("cl-")
                    else "Financial Aid"
                    if card["board"].startswith("fa-")
                    else "Enrollment Support"
                )
                status = "done" if card["status"] == "completed" else "todo"
                await db.execute(
                    "INSERT INTO public.staff_work_item "
                    "(id,tenant_id,student_id,key,title,description,status,priority,work_type,action_type,component,assignee_id) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
                    work_id,
                    TENANT,
                    students[ROSTER[card["studentSlot"]]],
                    card["key"],
                    card["title"],
                    "Seeded demo work. Document, parser, payment and workflow details remain simulated.",
                    status,
                    card["priority"],
                    work_type,
                    action,
                    component,
                    staff,
                )
                await db.execute(
                    "INSERT INTO public.staff_demo_board_card "
                    "(tenant_id,work_item_id,staff_member_id,template_key,board_id,position,scenario_version) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7)",
                    TENANT,
                    work_id,
                    staff,
                    card["key"],
                    card["board"],
                    card["position"],
                    VERSION,
                )
                await db.execute(
                    "INSERT INTO public.staff_work_log "
                    "(id,tenant_id,work_item_id,actor_type,actor_name,action,message) "
                    "VALUES($1,$2,$3,'system','Demo setup','created',"
                    "'Created seeded demo work; workflow details are illustrative.')",
                    identity(f"log:{card['key']}"),
                    TENANT,
                    work_id,
                )
            await db.execute(
                "INSERT INTO university.meta(tenant_id,key,value) VALUES($1,$2,'complete')",
                TENANT,
                VERSION,
            )
        return {
            "seeded": True,
            "students": len(students),
            "cards": len(fixture["cards"]),
        }
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    print(asyncio.run(seed(parser.parse_args().database_url)))
