"""Import ONLY new product seed domains into a previously imported local vNext DB.

Never resets the university, overwrites planning inputs or changes ledger rows.
"""

import argparse
import asyncio
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import text

from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID

TABLES = (
    "rate_catalog",
    "financial_plan_input",
    "financial_scenario",
    "meal_enrollment",
    "insurance_coverage",
    "payment_agreement",
    "payment_installment",
    "loan_terms",
)


async def run(url, source):
    parsed = urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1") or not parsed.path.startswith(
        "/audentra_university"
    ):
        raise ValueError("Choose an explicit isolated local university database")
    db = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    engine = create_database_engine(url)
    try:
        async with engine.begin() as c:
            await c.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": SYNTHETIC_TENANT_ID},
            )
            await c.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtext('audentra-product-import'))"
                )
            )
            raw = (await c.get_raw_connection()).driver_connection
            from uuid import UUID

            for table in TABLES:
                columns = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
                for record in db.execute(f"SELECT * FROM {table}"):
                    placeholders = ",".join(
                        f"${i + 1}" for i in range(len(columns) + 1)
                    )
                    await raw.execute(
                        f"INSERT INTO university.{table}(tenant_id,{','.join(columns)}) VALUES({placeholders}) ON CONFLICT DO NOTHING",
                        UUID(SYNTHETIC_TENANT_ID),
                        *record,
                    )
            from product_content import import_clubs
            from product_history import import_review_snapshots

            await import_clubs(raw, SYNTHETIC_TENANT_ID)
            await import_review_snapshots(raw, SYNTHETIC_TENANT_ID)
            operations = Path(__file__).with_name("product_operations.sql").read_text()
            await raw.execute(
                operations.replace(":tenant", f"'{SYNTHETIC_TENANT_ID}'::uuid")
            )
        print("Imported catalog/product evidence without overwriting existing records")
    finally:
        db.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.database_url, args.source))
