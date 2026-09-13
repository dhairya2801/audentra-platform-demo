"""One-shot import into a NEW local PostgreSQL university database.

Run the numbered API migrations first. An imported runtime is mutable; rerunning
this command never replaces its writes. Rebuild into a new database to reset.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import text

from audentra.infrastructure.db.engine import create_database_engine, DatabaseEngineOptions
from audentra.infrastructure.seeding.relational import seed_relational_data
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID

from build import CLOCK, DEFAULT_OUTPUT
from validate import validate

TENANT = UUID(SYNTHETIC_TENANT_ID)


def uid(kind, value):
    try:
        return UUID(value)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"audentra-university-v3:{kind}:{value}")


async def run(url, source, resume_bootstrap=False):
    # This importer is intentionally narrower than the normal deployment seed.
    # It cannot accidentally connect to the old vv_enrollment_synthu database.
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.path.startswith(
        "/audentra_university"
    ):
        raise ValueError("Use a NEW loopback database named audentra_university...")
    db = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    errors = validate(db)
    if errors:
        raise ValueError(errors)
    engine = create_database_engine(url, DatabaseEngineOptions(statement_timeout_ms=600000, application_name="audentra-university-import"))
    try:
        async with engine.connect() as c:
            existing = await c.execute(
                text(
                    "SELECT value FROM university.meta WHERE tenant_id=:tenant AND key='version'"
                ),
                {"tenant": TENANT},
            )
            if existing.first():
                print("University already imported; preserving all runtime changes.")
                return
            count = await c.scalar(
                text("SELECT count(*) FROM student WHERE tenant_id=:tenant"),
                {"tenant": TENANT},
            )
            if count and not resume_bootstrap:
                raise ValueError(
                    "Target contains a population without a completed v3 import; use a new database"
                )
        if not count:
            await seed_relational_data(
                engine, environment="development", profile="synthetic_university"
            )
        async with engine.begin() as c:
            await c.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": str(TENANT)},
            )
            raw = (await c.get_raw_connection()).driver_connection
            await raw.execute("SET CONSTRAINTS ALL DEFERRED")
            await raw.execute(
                "SELECT set_config('audentra.university_import','on',true)"
            )
            tables = [
                r[0]
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            tables = ["term", *[t for t in tables if t != "term"]]
            for table in tables:
                columns = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
                records = [
                    (TENANT, *tuple(r)) for r in db.execute(f'SELECT * FROM "{table}"')
                ]
                await raw.copy_records_to_table(
                    table,
                    schema_name="university",
                    columns=["tenant_id", *columns],
                    records=records,
                )
            await raw.execute(
                "INSERT INTO university.meta VALUES($1,'seed_sha256',$2)",
                TENANT,
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            passages = []
            for p in db.execute("SELECT * FROM policy ORDER BY id"):
                parts = re.split(r"(?m)^##\s+(.+)\s*$", p["body"])
                sections = [(p["title"], parts[0])] + list(
                    zip(parts[1::2], parts[2::2])
                )
                for ordinal, (heading, body) in enumerate(sections):
                    if body.strip():
                        passages.append(
                            (
                                TENANT,
                                p["id"],
                                ordinal,
                                p["title"] + " — " + heading,
                                body.strip(),
                            )
                        )
            await raw.copy_records_to_table(
                "policy_section",
                schema_name="university",
                columns=["tenant_id", "policy_id", "ordinal", "heading", "body"],
                records=passages,
            )
            links = []
            students = [
                dict(r)
                for r in db.execute("SELECT * FROM student ORDER BY external_ref")
            ]
            for s in students:
                sid = UUID(s["id"])
                pid = uuid5(
                    NAMESPACE_URL, "audentra-synthetic-university:person:" + s["id"]
                )
                first, _, last = s["name"].partition(" ")
                await raw.execute(
                    "INSERT INTO person(id,tenant_id,preferred_name,first_name,last_name) VALUES($1,$2,$3,$4,$5) ON CONFLICT(id) DO UPDATE SET preferred_name=$3,first_name=$4,last_name=$5",
                    pid,
                    TENANT,
                    s["preferred_name"],
                    first,
                    last,
                )
                await raw.execute(
                    "INSERT INTO student(id,tenant_id,person_id,class_year,external_ref) VALUES($1,$2,$3,2030,$4) ON CONFLICT(id) DO UPDATE SET external_ref=$4",
                    sid,
                    TENANT,
                    pid,
                    s["external_ref"],
                )
                await raw.execute(
                    "INSERT INTO student_profile(tenant_id,student_id,preferred_name,communication_preference) VALUES($1,$2,$3,'email') ON CONFLICT(tenant_id,student_id) DO UPDATE SET preferred_name=$3",
                    TENANT,
                    sid,
                    s["preferred_name"],
                )
                await raw.execute(
                    "INSERT INTO student_onboarding(tenant_id,student_id,status,current_step,completed_steps,payload,completed_at) VALUES($1,$2,'completed','deposit',ARRAY['offer','about_you','housing','campus_life','emergency_contacts','family_permissions','review_and_sign','deposit'],'{}','2026-09-08T13:00:00Z') ON CONFLICT(tenant_id,student_id) DO UPDATE SET status='completed',current_step='deposit',completed_at='2026-09-08T13:00:00Z'",
                    TENANT,
                    sid,
                )
                # Remove unsupported factual claims from the old onboarding
                # projection. A domestic residence is not proof of citizenship.
                payload = {
                    "universityProgramName": db.execute(
                        "SELECT name FROM program WHERE id=?", (s["program_id"],)
                    ).fetchone()[0],
                    "firstName": first,
                    "lastName": last,
                    "preferredName": s["preferred_name"],
                    "personalEmail": s["email"],
                    "residencyStatus": "international"
                    if s["residency"] == "international"
                    else "domestic",
                    "skippedSteps": [],
                }
                if s["residency"] == "international":
                    payload["citizenshipStatus"] = "international"
                house = db.execute(
                    "SELECT h.*,r.name,b.room FROM housing h LEFT JOIN bed b ON b.id=h.bed_id LEFT JOIN residence r ON r.id=b.residence_id WHERE h.student_id=? AND h.ends_at IS NULL",
                    (s["id"],),
                ).fetchone()
                payload["housingPreference"] = (
                    "on_campus"
                    if house and house["status"] in ("assigned", "waitlisted")
                    else "off_campus"
                )
                if house and house["status"] == "assigned":
                    payload["housingAssignmentLabel"] = (
                        f"{house['name']} · {house['room']}"
                    )
                await raw.execute(
                    "UPDATE student_onboarding SET payload=$3::jsonb WHERE tenant_id=$1 AND student_id=$2",
                    TENANT,
                    sid,
                    json.dumps(payload),
                )
            offices = {r["id"]: dict(r) for r in db.execute("SELECT * FROM office")}
            staff = [dict(r) for r in db.execute("SELECT * FROM staff ORDER BY id")]
            for s in staff:
                rid = uid("staff", s["id"])
                links.append((TENANT, "staff", s["id"], rid))
                await raw.execute(
                    """INSERT INTO staff_member(id,tenant_id,display_name,email_normalized,component,active,title,role_code,employment_status,timezone,office_location,caseload_cap,appointment_types,external_ref)
                VALUES($1,$2,$3,$4,$5,$6,$7,'operations_lead',$8,'America/New_York',$9,$10,ARRAY['academic_advising','enrollment_support','financial_aid'],$11)
                ON CONFLICT(id) DO UPDATE SET display_name=$3,email_normalized=$4,component=$5,active=$6,title=$7,role_code='operations_lead',employment_status=$8,timezone='America/New_York',office_location=$9,caseload_cap=$10,appointment_types=EXCLUDED.appointment_types,external_ref=$11""",
                    rid,
                    TENANT,
                    s["name"],
                    s["email"],
                    offices[s["office_id"]]["name"],
                    s["status"] != "departed",
                    s["title"],
                    {"leave": "on_leave"}.get(s["status"], s["status"]),
                    offices[s["office_id"]]["location"],
                    s["capacity"],
                    f"AU-{s['id'][:12]}",
                )
            for s in staff:
                if s["manager_id"]:
                    await raw.execute(
                        "UPDATE staff_member SET manager_id=$3 WHERE tenant_id=$1 AND id=$2",
                        TENANT,
                        uid("staff", s["id"]),
                        uid("staff", s["manager_id"]),
                    )
            # The three seed publishers are provenance identities, not extra
            # university employees. Retain their audit FKs but disable sign-in.
            await raw.execute(
                "UPDATE staff_member SET active=false,employment_status='departed' WHERE tenant_id=$1 AND NOT(id=ANY($2::uuid[]))",
                TENANT,
                [uid("staff", s["id"]) for s in staff],
            )
            # Fresh imported operational projections replace the old generator's
            # samples. Existing audit/work logs are retained with cancelled tasks.
            await raw.execute(
                "UPDATE staff_work_item SET status='cancelled',description='Superseded by canonical v3 workflow import' WHERE tenant_id=$1",
                TENANT,
            )
            for table in (
                "student_staff_assignment",
                "staff_availability",
                "staff_time_off",
                "student_appointment",
            ):
                await raw.execute(f"DELETE FROM {table} WHERE tenant_id=$1", TENANT)
            for a in db.execute("SELECT * FROM assignment"):
                await raw.execute(
                    "INSERT INTO student_staff_assignment(id,tenant_id,student_id,staff_member_id,role,assigned_at,ended_at,source,note) VALUES($1,$2,$3,$4,$5,$6::text::timestamptz,$7::text::timestamptz,'university_v3',$8)",
                    uid("assignment", a["id"]),
                    TENANT,
                    UUID(a["student_id"]),
                    uid("staff", a["staff_id"]),
                    "primary_advisor" if a["role"] == "academic_adviser" else a["role"],
                    a["starts_at"],
                    a["ends_at"],
                    a["reason"],
                )
            for a in db.execute("SELECT * FROM staff_availability"):
                await raw.execute(
                    "INSERT INTO staff_availability(id,tenant_id,staff_member_id,weekday,start_minute,end_minute,location,appointment_types) VALUES($1,$2,$3,$4,$5,$6,$7,ARRAY['academic_advising','enrollment_support','financial_aid'])",
                    uid("hours", a["id"]),
                    TENANT,
                    uid("staff", a["staff_id"]),
                    (a["weekday"] + 1) % 7,
                    a["start_minute"],
                    a["end_minute"],
                    a["location"],
                )
            for a in db.execute("SELECT * FROM staff_absence"):
                await raw.execute(
                    "INSERT INTO staff_time_off(id,tenant_id,staff_member_id,starts_at,ends_at,kind,note) VALUES($1,$2,$3,$4::text::timestamptz,$5::text::timestamptz,'leave',$6)",
                    uid("absence", a["id"]),
                    TENANT,
                    uid("staff", a["staff_id"]),
                    a["starts_at"],
                    a["ends_at"],
                    a["reason"],
                )
            for a in db.execute(
                "SELECT * FROM staff_calendar_event WHERE blocks_bookings=1"
            ):
                await raw.execute(
                    "INSERT INTO staff_time_off(id,tenant_id,staff_member_id,starts_at,ends_at,kind,note) VALUES($1,$2,$3,$4::text::timestamptz,$5::text::timestamptz,'blocked',$6)",
                    uid("meeting", a["id"]),
                    TENANT,
                    uid("staff", a["staff_id"]),
                    a["starts_at"],
                    a["ends_at"],
                    a["title"],
                )
            for a in db.execute("SELECT * FROM appointment"):
                rid = uid("appointment", a["id"])
                links.append((TENANT, "appointment", a["id"], rid))
                await raw.execute(
                    "INSERT INTO student_appointment(id,tenant_id,student_id,staff_member_id,type,starts_at,ends_at,status,notes,modality,booked_via) VALUES($1,$2,$3,$4,'academic_advising',$5::text::timestamptz,$6::text::timestamptz,$7,$8,'in_person','import')",
                    rid,
                    TENANT,
                    UUID(a["student_id"]),
                    uid("staff", a["staff_id"]),
                    a["starts_at"],
                    a["ends_at"],
                    a["status"],
                    a["purpose"],
                )
            for index, w in enumerate(db.execute("SELECT * FROM workflow ORDER BY id")):
                rid = uid("workflow", w["id"])
                links.append((TENANT, "workflow", w["id"], rid))
                await raw.execute(
                    """INSERT INTO staff_work_item(id,tenant_id,student_id,key,title,description,status,priority,work_type,component,due_at,assignee_id,version)
                  VALUES($1,$2,$3,$4,$5,$6,$7,'medium','document_review',$8,$9::text::timestamptz,$10,$11)""",
                    rid,
                    TENANT,
                    UUID(w["student_id"]),
                    f"AUV-{index + 1:05}",
                    w["title"],
                    "Canonical university case; closure requires all case steps and completion evidence.",
                    {
                        "open": "todo",
                        "waiting": "blocked",
                        "resolved": "done",
                        "cancelled": "cancelled",
                    }[w["status"]],
                    offices[w["office_id"]]["name"],
                    w["due_at"],
                    uid("staff", w["owner_id"]),
                    w["version"],
                )
            await raw.copy_records_to_table(
                "runtime_link",
                schema_name="university",
                columns=["tenant_id", "kind", "world_id", "runtime_id"],
                records=links,
            )
            # Role grants use the inherited deterministic write gateway. External
            # email still requires its separately authenticated mailbox boundary.
            # Reconcile existing uploaded records, retaining their original IDs.
            await raw.execute(
                """UPDATE document_record d SET status=lower(w.status)
              FROM university.document w WHERE w.tenant_id=$1 AND d.tenant_id=w.tenant_id AND d.id::text=w.id AND w.status<>'NOT_SUBMITTED'""",
                TENANT,
            )
            projection = (Path(__file__).parent / "runtime_projection.sql").read_text()
            await raw.execute(projection.replace(":tenant", f"'{TENANT}'::uuid"))
            from product_content import import_clubs
            from product_history import import_review_snapshots

            await import_clubs(raw, TENANT)
            await import_review_snapshots(raw, TENANT)
            product_operations = Path(__file__).with_name("product_operations.sql")
            if product_operations.exists():
                await raw.execute(product_operations.read_text().replace(":tenant", f"'{TENANT}'::uuid"))
            # Stop development workers from spending provider credits on thousands
            # of seed-triggered enrichments. Canonical writes/reads need no LLM.
            await raw.execute(
                "UPDATE action_center_ai_job SET status='cancelled' WHERE tenant_id=$1 AND status IN ('pending','failed_retryable')",
                TENANT,
            )
        print(
            f"Imported {len(students)} students, {len(staff)} staff, {len(tables)} relational tables, {len(passages)} indexed policy passages. PostgreSQL is now canonical."
        )
    finally:
        db.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--resume-bootstrap",
        action="store_true",
        help="Resume this importer after its v3 transaction failed; only for the same new local database",
    )
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_OUTPUT / "university.sqlite"
    )
    args = parser.parse_args()
    asyncio.run(run(args.database_url, args.source, args.resume_bootstrap))
