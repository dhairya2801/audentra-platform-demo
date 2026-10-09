"""Local-only failure injection for retry UI QA; original bytes are unchanged."""

import asyncio, json
import asyncpg
from runtime import environment, ARTIFACTS


async def main():
    db = await asyncpg.connect(environment()["DATABASE_URL"])
    row = await db.fetchrow("""SELECT d.id FROM document_record d JOIN demo_document_requirement r
 ON r.tenant_id=d.tenant_id AND r.requirement_id=d.requirement_id WHERE r.expected_type='passport'
 AND d.superseded_at IS NULL ORDER BY d.created_at DESC LIMIT 1""")
    assert row
    await db.execute(
        """UPDATE document_record SET status='needs_review',extraction=extraction ||
 '{"status":"failed","retryable":true,"failureCode":"timeout","warnings":["Simulated timeout for retry verification. Original retained."]}'::jsonb WHERE id=$1""",
        row["id"],
    )
    (ARTIFACTS / "injected-failure.json").write_text(
        json.dumps(
            {
                "documentId": str(row["id"]),
                "type": "deterministic test injection, not a live provider failure",
            }
        )
    )
    await db.close()
    print("Injected retryable failure in local passport parse; original retained.")


asyncio.run(main())
