"""Explicit, repeatable setup against the isolated local snapshot only."""

import asyncio, json
from uuid import UUID, uuid5, NAMESPACE_URL
from pathlib import Path
import asyncpg, boto3
from runtime import environment, ARTIFACTS
from audentra.infrastructure.db.migrations import discover_migrations

T = UUID("00000000-0000-7000-8000-000000000003")
S = UUID("3bedfe91-6802-4937-893b-72cb7779ecfa")
KINDS = {
    "ada_demo_transcript": "transcript",
    "ada_updated_transcript": "transcript",
    "camila_enr_190": "transcript",
    "official_transcript": "transcript",
    "identity_document": "identity",
    "immunization_record": "immunization",
    "camila_fin_154": "financial_aid",
    "financial_aid_verification": "financial_aid",
    "ada_passport": "passport",
}


async def main():
    e = environment()
    db = await asyncpg.connect(e["DATABASE_URL"])
    for migration in discover_migrations(Path("apps/api/migrations")):
        if not migration.name.startswith(("0081_", "0082_")):
            continue
        previous = await db.fetchval(
            "SELECT checksum FROM vv_schema_migration WHERE name=$1", migration.name
        )
        if previous:
            assert previous == migration.checksum, "Migration checksum changed: " + migration.name
            continue
        async with db.transaction():
            await db.execute(migration.sql)
            await db.execute(
                "INSERT INTO vv_schema_migration(name,checksum) VALUES($1,$2)",
                migration.name,
                migration.checksum,
            )
    async with db.transaction():
        assert (
            await db.fetchval("SELECT external_ref FROM student WHERE id=$1 AND tenant_id=$2", S, T)
            == "SYN-000061"
        )
        j = await db.fetchrow(
            "SELECT * FROM enrollment_journey WHERE student_id=$1 AND tenant_id=$2 ORDER BY created_at DESC LIMIT 1",
            S,
            T,
        )
        code = "ada_passport"
        definition = uuid5(NAMESPACE_URL, "audentra-conference-oct8:passport:def")
        rid = uuid5(NAMESPACE_URL, "audentra-conference-oct8:passport:req")
        await db.execute(
            """INSERT INTO requirement_definition_version(id,tenant_id,code,title,description,display_order,version,submission_type,interaction_type,input_config)
    VALUES($1,$2,$3,'Upload your passport','Upload the biographical page for staff review.',123,1,'document','upload_file','{}') ON CONFLICT(id) DO NOTHING""",
            definition,
            T,
            code,
        )
        await db.execute(
            "INSERT INTO journey_requirement_definition VALUES($1,$2) ON CONFLICT DO NOTHING",
            j["journey_definition_version_id"],
            definition,
        )
        await db.execute(
            "INSERT INTO student_requirement(id,tenant_id,journey_id,requirement_definition_version_id,status) VALUES($1,$2,$3,$4,'ready') ON CONFLICT(id) DO NOTHING",
            rid,
            T,
            j["id"],
            definition,
        )
        manifest = []
        for code, kind in KINDS.items():
            row = await db.fetchrow(
                "SELECT r.id,r.requirement_definition_version_id FROM student_requirement r JOIN requirement_definition_version d ON d.id=r.requirement_definition_version_id WHERE r.tenant_id=$1 AND r.journey_id=$2 AND d.code=$3",
                T,
                j["id"],
                code,
            )
            if not row:
                raise ValueError("Missing canonical requirement " + code)
            category = {"passport": "identity", "immunization": "health"}.get(kind, kind)
            file = {
                "identity": "passport",
                "financial_aid": "financial-aid",
                "immunization": "immunization",
            }.get(kind, kind) + ".pdf"
            await db.execute(
                "INSERT INTO demo_document_requirement VALUES($1,$2,$3,$4,$5) ON CONFLICT(tenant_id,student_id,requirement_id) DO UPDATE SET expected_type=excluded.expected_type,fixture_name=excluded.fixture_name",
                T,
                S,
                row["id"],
                kind,
                file,
            )
            await db.execute(
                "UPDATE requirement_definition_version SET input_config=input_config || $2::jsonb WHERE id=$1",
                row["requirement_definition_version_id"],
                json.dumps(
                    {
                        "document_category": category,
                        "staff_review_only": True,
                        "demoDocumentFilename": file,
                    }
                ),
            )
            manifest.append(
                {
                    "id": str(row["id"]),
                    "code": code,
                    "expectedType": kind,
                    "category": category,
                    "fixture": file,
                }
            )
        ARTIFACTS.joinpath("inventory.json").write_text(
            json.dumps(
                {
                    "studentId": str(S),
                    "reference": "SYN-000061",
                    "name": "Ada Kettleby",
                    "birthDate": "2007-03-08",
                    "requirements": manifest,
                },
                indent=2,
            )
        )
    client = boto3.client(
        "s3",
        endpoint_url=e["OBJECT_STORAGE_ENDPOINT"],
        aws_access_key_id=e["OBJECT_STORAGE_ACCESS_KEY"],
        aws_secret_access_key=e["OBJECT_STORAGE_SECRET_KEY"],
    )
    if not any(b["Name"] == e["OBJECT_STORAGE_BUCKET"] for b in client.list_buckets()["Buckets"]):
        client.create_bucket(Bucket=e["OBJECT_STORAGE_BUCKET"])
    await db.close()
    print("Configured 9 canonical requirements, isolated storage; no hosted changes.")


if __name__ == "__main__":
    asyncio.run(main())
