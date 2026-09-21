"""Give Ada four ready demo steps once, preserving completed work and earned points."""

import argparse
import asyncio
import json
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg

TENANT = UUID("00000000-0000-7000-8000-000000000003")
VERSION = "ada-four-next-steps-v1"


def identity(key):
    return uuid5(NAMESPACE_URL, f"audentra:{VERSION}:{key}")


async def seed(url):
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
        "/audentra_university"
    ):
        raise ValueError("Choose an explicit local audentra_university database")
    db = await asyncpg.connect(url)
    try:
        async with db.transaction():
            await db.execute("SELECT set_config('audentra.tenant_id',$1,true)", str(TENANT))
            await db.execute("SELECT pg_advisory_xact_lock(hashtext($1))", VERSION)
            if await db.fetchval(
                "SELECT value FROM university.meta WHERE tenant_id=$1 AND key=$2",
                TENANT,
                VERSION,
            ):
                return {"seeded": False, "reason": "Subsequent demo actions preserved"}
            journey = await db.fetchrow(
                "SELECT j.* FROM enrollment_journey j JOIN student s ON s.id=j.student_id "
                "AND s.tenant_id=j.tenant_id WHERE s.tenant_id=$1 AND s.external_ref='SYN-000061' "
                "AND j.status='in_progress' ORDER BY j.created_at DESC LIMIT 1 FOR UPDATE OF j",
                TENANT,
            )
            if journey is None:
                raise ValueError("Import Ada and her demo journey first")
            ready = await db.fetch(
                "SELECT d.code FROM student_requirement r JOIN requirement_definition_version d "
                "ON d.id=r.requirement_definition_version_id AND d.tenant_id=r.tenant_id "
                "WHERE r.tenant_id=$1 AND r.journey_id=$2 AND r.retired_at IS NULL "
                "AND r.status IN ('ready','in_progress','rejected')",
                TENANT,
                journey["id"],
            )
            codes = {r["code"] for r in ready}
            if codes not in (
                {"orientation_registration"},
                {"orientation_registration", "ada_updated_transcript"},
            ):
                raise ValueError("Ada's starting steps changed; inspect before adding demo tasks")
            # Clone only her definition. Never change other students or reopen completed evidence.
            definition = identity("journey")
            await db.execute(
                "INSERT INTO journey_definition_version SELECT "
                "(jsonb_populate_record(NULL::journey_definition_version,to_jsonb(v) || "
                "jsonb_build_object('id',$1::text,'code','ada_four_steps_demo','version',1,"
                "'active',0,'created_at',now(),'updated_at',now()))).* "
                "FROM journey_definition_version v WHERE v.id=$2 AND v.tenant_id=$3",
                str(definition),
                journey["journey_definition_version_id"],
                TENANT,
            )
            await db.execute(
                "INSERT INTO journey_requirement_definition "
                "SELECT $1,requirement_definition_version_id "
                "FROM journey_requirement_definition WHERE journey_definition_version_id=$2",
                definition,
                journey["journey_definition_version_id"],
            )
            tasks = [
                (
                    "ada_emergency_contact",
                    "Confirm your emergency contact",
                    "Review who we should contact in an emergency.",
                    "form",
                    "form",
                    30,
                    {
                        "fields": [
                            {
                                "id": "name",
                                "title": "Contact name",
                                "field_type": "text",
                                "required": True,
                            },
                            {
                                "id": "phone",
                                "title": "Phone number",
                                "field_type": "text",
                                "required": True,
                            },
                        ]
                    },
                ),
                (
                    "ada_enrollment_goal",
                    "Share your first-semester goal",
                    "Tell Camila what you would like to work toward this semester.",
                    "form",
                    "form",
                    25,
                    {
                        "fields": [
                            {
                                "id": "goal",
                                "title": "Your goal",
                                "field_type": "text",
                                "required": True,
                            },
                        ]
                    },
                ),
            ]
            transcript = "ada_updated_transcript"
            if transcript not in codes:
                transcript = "ada_demo_transcript"
                tasks.insert(
                    0,
                    (
                        transcript,
                        "Upload your latest transcript",
                        "Send your latest transcript to Camila for review. "
                        "Previously accepted documents stay on your record.",
                        "document",
                        "upload_file",
                        80,
                        {},
                    ),
                )
            for order, (
                code,
                title,
                description,
                submission,
                interaction,
                _points,
                inputs,
            ) in enumerate(tasks):
                if inputs.get("fields"):
                    inputs["form"] = {
                        "version": 1,
                        "pages": [
                            {
                                "id": "details",
                                "title": title,
                                "fields": inputs["fields"],
                            }
                        ],
                    }
                did = identity("definition:" + code)
                await db.execute(
                    "INSERT INTO requirement_definition_version "
                    "(id,tenant_id,code,title,description,blocking,display_order,version,submission_type,"
                    "responsible_office,flow_kind,interaction_type,priority,input_config) "
                    "VALUES($1,$2,$3,$4,$5,0,$6,1,$7,'Academic Advising Center',"
                    "'enrollment',$8,$9,$10::jsonb)",
                    did,
                    TENANT,
                    code,
                    title,
                    description,
                    120 + order,
                    submission,
                    interaction,
                    90 if submission == "document" else 60 - order,
                    json.dumps(inputs),
                )
                await db.execute(
                    "INSERT INTO journey_requirement_definition VALUES($1,$2)",
                    definition,
                    did,
                )
                await db.execute(
                    "INSERT INTO student_requirement "
                    "(id,tenant_id,journey_id,requirement_definition_version_id,"
                    "status,progress_percent) "
                    "VALUES($1,$2,$3,$4,'ready',0)",
                    identity("requirement:" + code),
                    TENANT,
                    journey["id"],
                    did,
                )
            if transcript == "ada_demo_transcript":
                # Continue Ada's existing review card without deleting its accepted evidence.
                await db.execute(
                    "INSERT INTO staff_work_item_link "
                    "(id,tenant_id,work_item_id,entity_type,entity_id,relationship) "
                    "SELECT $1,$2,w.id,'requirement',$3,'document_request' "
                    "FROM staff_demo_board_card c JOIN staff_work_item w "
                    "ON w.id=c.work_item_id AND w.tenant_id=c.tenant_id "
                    "WHERE c.tenant_id=$2 AND c.template_key='ENR-184' AND w.student_id=$4 "
                    "ON CONFLICT DO NOTHING",
                    identity("transcript-link"),
                    TENANT,
                    identity("requirement:" + transcript),
                    journey["student_id"],
                )
            await db.execute(
                "INSERT INTO tenant_reward_program(tenant_id,point_name,points_per_usd,enabled) "
                "VALUES($1,'Aster Points',100,true) ON CONFLICT(tenant_id) "
                "DO UPDATE SET enabled=true,updated_at=now()",
                TENANT,
            )
            rewards = [(t[0], t[1], t[5]) for t in tasks]
            if transcript == "ada_updated_transcript":
                rewards.append((transcript, "Upload updated transcript", 80))
            for code, title, points in rewards:
                await db.execute(
                    "INSERT INTO tenant_reward_rule "
                    "(id,tenant_id,code,title,description,trigger_type,trigger_key,points) "
                    "VALUES($1,$2,$3,$4,'Complete this enrollment step.',"
                    "'requirement_completed',$3,$5) "
                    "ON CONFLICT (tenant_id,code) DO NOTHING",
                    identity("reward:" + code),
                    TENANT,
                    code,
                    title,
                    points,
                )
            await db.execute(
                "UPDATE enrollment_journey SET journey_definition_version_id=$1,"
                "version=version+1,updated_at=now() "
                "WHERE id=$2 AND tenant_id=$3",
                definition,
                journey["id"],
                TENANT,
            )
            actor = await db.fetchval(
                "SELECT id FROM staff_member WHERE tenant_id=$1 AND external_ref='AU-55ff7e408818'",
                TENANT,
            )
            if actor is None:
                raise ValueError("Import Camila before adding the demo steps")
            metadata = json.dumps({"scenario": VERSION, "studentId": str(journey["student_id"])})
            await db.execute(
                "INSERT INTO audit_event "
                "(id,tenant_id,actor_type,actor_id,student_id,action,resource_type,resource_id,"
                "authorization_basis,request_id,correlation_id,metadata) "
                "VALUES($1,$2,'staff',$3,$4,'demo.next_steps_created','enrollment_journey',$5,"
                "'demo_setup',$6,$6,$7::jsonb)",
                identity("audit"),
                TENANT,
                actor,
                journey["student_id"],
                journey["id"],
                VERSION,
                metadata,
            )
            await db.execute(
                "INSERT INTO outbox_event "
                "(id,tenant_id,event_name,aggregate_type,aggregate_id,aggregate_version,"
                "occurred_at,actor_type,actor_id,correlation_id,causation_id,payload) "
                "VALUES($1,$2,'demo.next_steps_created.v1','enrollment_journey',$3,$4,now(),"
                "'staff',$5,$6,$6,$7::jsonb)",
                identity("event"),
                TENANT,
                journey["id"],
                journey["version"] + 1,
                actor,
                VERSION,
                metadata,
            )
            await db.execute(
                "INSERT INTO university.meta(tenant_id,key,value) VALUES($1,$2,$3)",
                TENANT,
                VERSION,
                json.dumps({"transcript": transcript}),
            )
            return {"seeded": True, "readySteps": 4, "transcript": transcript}
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    print(json.dumps(asyncio.run(seed(parser.parse_args().database_url)), indent=2))
