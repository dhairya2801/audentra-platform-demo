"""Add one pending upload to Ada's local demo without resetting accepted evidence."""

import argparse
import asyncio
import json
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid4, uuid5

from audentra.core.auth import AuthContext
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TENANT = "00000000-0000-7000-8000-000000000003"
VERSION = "camila-ada-upload-request-v1"
CODE = "ada_updated_transcript"


def identity(part):
    return str(uuid5(NAMESPACE_URL, f"audentra:{VERSION}:{part}"))


async def seed(url):
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
        "/audentra_university"
    ):
        raise ValueError("Choose an explicit local audentra_university database")
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    portal = PostgresPortalRepository(engine)
    params = {
        "tenant": TENANT,
        "marker": VERSION,
        "definition": identity("journey"),
        "requirement_definition": identity("definition"),
        "requirement": identity("requirement"),
        "code": CODE,
        "request": str(uuid4()),
    }
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"), params
            )
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:marker))"), params
            )
            if (
                await connection.execute(
                    text("""
                SELECT 1 FROM university.meta WHERE tenant_id=CAST(:tenant AS uuid) AND key=:marker
            """),
                    params,
                )
            ).first():
                return {
                    "seeded": False,
                    "requirementId": params["requirement"],
                    "reason": "Existing submissions preserved",
                }
            target = (
                (
                    await connection.execute(
                        text("""
                SELECT s.id AS student,j.id AS journey,j.journey_definition_version_id AS original,
                  a.staff_member_id AS staff
                FROM student s JOIN enrollment_journey j ON j.student_id=s.id AND j.tenant_id=s.tenant_id
                JOIN student_staff_assignment a ON a.student_id=s.id AND a.tenant_id=s.tenant_id
                  AND a.role='primary_advisor' AND a.ended_at IS NULL
                JOIN staff_member m ON m.id=a.staff_member_id AND m.tenant_id=a.tenant_id AND m.active
                WHERE s.tenant_id=CAST(:tenant AS uuid) AND s.external_ref='SYN-000061'
                  AND m.external_ref='AU-55ff7e408818' AND j.status='in_progress'
                  AND EXISTS(SELECT 1 FROM staff_demo_board_card c
                    WHERE c.tenant_id=s.tenant_id AND c.staff_member_id=m.id)
                ORDER BY j.created_at DESC LIMIT 1 FOR UPDATE OF j
            """),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            if target is None:
                raise ValueError(
                    "Ada needs an active demo journey and Camila adviser assignment"
                )
            params.update({key: str(value) for key, value in target.items()})
            # A separate definition is assigned only to Ada. Do not edit the published
            # shared definition or make this the default for newly enrolled students.
            await connection.execute(
                text("""
                INSERT INTO journey_definition_version
                SELECT (jsonb_populate_record(NULL::journey_definition_version,to_jsonb(v) ||
                  jsonb_build_object('id',CAST(:definition AS text),'code','camila_ada_demo_enrollment',
                    'version',1,'active',0,'created_at',now(),'updated_at',now()))).*
                FROM journey_definition_version v
                WHERE v.id=CAST(:original AS uuid) AND v.tenant_id=CAST(:tenant AS uuid)
            """),
                params,
            )
            await connection.execute(
                text("""
                INSERT INTO journey_requirement_definition
                SELECT CAST(:definition AS uuid),requirement_definition_version_id
                FROM journey_requirement_definition WHERE journey_definition_version_id=CAST(:original AS uuid)
            """),
                params,
            )
            await connection.execute(
                text("""
                INSERT INTO requirement_definition_version(id,tenant_id,code,title,description,
                  blocking,display_order,version,submission_type,responsible_office,
                  flow_kind,interaction_type,priority)
                VALUES(CAST(:requirement_definition AS uuid),CAST(:tenant AS uuid),:code,
                  'Upload updated transcript',
                  'Send your latest transcript to Camila Abernathy for review. Your previously accepted transcript stays on your record.',
                  0,100,1,'document','Academic Advising Center','enrollment','upload_file',80)
            """),
                params,
            )
            await connection.execute(
                text("""
                INSERT INTO journey_requirement_definition
                VALUES(CAST(:definition AS uuid),CAST(:requirement_definition AS uuid))
            """),
                params,
            )
            await connection.execute(
                text("""
                UPDATE enrollment_journey SET journey_definition_version_id=CAST(:definition AS uuid),
                  version=version+1,updated_at=now()
                WHERE id=CAST(:journey AS uuid) AND tenant_id=CAST(:tenant AS uuid)
            """),
                params,
            )
            await connection.execute(
                text("""
                INSERT INTO student_requirement(id,tenant_id,journey_id,
                  requirement_definition_version_id,status,progress_percent)
                VALUES(CAST(:requirement AS uuid),CAST(:tenant AS uuid),CAST(:journey AS uuid),
                  CAST(:requirement_definition AS uuid),'ready',0)
            """),
                params,
            )
            await connection.execute(
                text("""
                INSERT INTO audit_event(id,tenant_id,actor_type,actor_id,student_id,action,
                  resource_type,resource_id,authorization_basis,request_id,correlation_id,metadata)
                VALUES(CAST(:request AS uuid),CAST(:tenant AS uuid),'staff',CAST(:staff AS uuid),
                  CAST(:student AS uuid),'demo.document_requirement_created','student_requirement',
                  CAST(:requirement AS uuid),'demo_setup',:request,:request,
                  jsonb_build_object('scenario',CAST(:marker AS text),'priorJourneyDefinitionId',CAST(:original AS text)))
            """),
                params,
            )
            auth = AuthContext(TENANT, params["student"], params["staff"], "staff")
            await portal._insert_outbox(
                connection,
                auth,
                "demo.document_requirement_created.v1",
                "student_requirement",
                params["requirement"],
                1,
                params["request"],
                {
                    "studentId": params["student"],
                    "requirementId": params["requirement"],
                },
            )
            await connection.execute(
                text("""
                INSERT INTO university.meta(tenant_id,key,value)
                VALUES(CAST(:tenant AS uuid),:marker,'complete')
            """),
                params,
            )
        return {
            "seeded": True,
            "requirementId": params["requirement"],
            "title": "Upload updated transcript",
        }
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(seed(args.database_url)), indent=2))
