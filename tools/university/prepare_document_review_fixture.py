"""Prepare a disposable document-review browser fixture through canonical commands.

This verifies workflow persistence, not object storage or extraction providers.
"""

import asyncio, json, argparse
from urllib.parse import urlparse
from pathlib import Path
from uuid import uuid4
from sqlalchemy import text
from audentra.bootstrap.api import build_api_runtime
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = urlparse(args.database_url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
        "/audentra_university_test"
    ):
        parser.error("Use an explicitly named isolated local university test database")
    url = args.database_url
    runtime = await build_api_runtime(RuntimeSettings.from_environment({"DATABASE_URL": url}))
    tenant = "00000000-0000-7000-8000-000000000003"
    student = "ac2fa509-b4e3-402d-900b-ffb8440fc430"
    auth = AuthContext(tenant, student, student, "student")
    try:
        portal = runtime.service.repository.portal
        key = str(uuid4())
        doc = await portal.reserve_student_document_upload(
            auth,
            {
                "fileName": "Integration review specimen.pdf",
                "mimeType": "application/pdf",
                "sizeBytes": 512,
                "category": "other",
                "sha256": "b" * 64,
            },
            key,
            key,
        )
        await portal.claim_student_document_processing(auth, doc["id"])
        await portal.complete_student_document_extraction(
            auth,
            doc["id"],
            {
                "status": "failed",
                "documentType": "other",
                "provider": "local",
                "fields": [],
                "warnings": ["Synthetic browser fixture: original storage is not exercised."],
                "model": None,
                "processedAt": None,
                "verifiedAt": None,
            },
            str(uuid4()),
        )
        async with runtime.engine.connect() as c:
            work = (
                (
                    await c.execute(
                        text(
                            "SELECT id,key,version FROM staff_work_item WHERE tenant_id=:tenant AND source_type='document' AND source_id=CAST(:id AS uuid)"
                        ),
                        {"tenant": tenant, "id": doc["id"]},
                    )
                )
                .mappings()
                .one()
            )
        args.output.write_text(
            json.dumps(
                {
                    "student": student,
                    "document": doc["id"],
                    "workId": str(work["id"]),
                    "key": work["key"],
                },
                indent=2,
            )
        )
    finally:
        await runtime.close()


if __name__ == "__main__":
    asyncio.run(main())
