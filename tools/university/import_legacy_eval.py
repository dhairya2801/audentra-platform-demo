"""Copy compatible frozen-fixture data into an empty, current evaluation schema.

Both URLs must be explicitly isolated local test databases. This never migrates
the donor's divergent history, imports university runtime metadata, seeds fake
facts, or changes source data. Constraints are re-created and validated before
the target transaction can commit. The report enumerates excluded data.
"""

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

import asyncpg


def checked(url):
    parsed = urlparse(url)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.path.startswith(
        "/audentra_university_test_vnext_legacy"
    ) or parsed.port != 55591:
        raise ValueError("Use explicitly isolated vNext legacy evaluation databases on 55591")
    return url


def ident(value):
    return '"' + value.replace('"', '""') + '"'


async def run(args):
    if args.source == args.target:
        raise ValueError("Source and target must differ")
    source = await asyncpg.connect(checked(args.source))
    target = await asyncpg.connect(checked(args.target))
    report = {"purpose": "legacy evaluation only", "tables": {}, "excludedTables": [], "excludedColumns": {}}
    try:
        async with source.transaction(readonly=True, isolation="repeatable_read"), target.transaction():
            if await target.fetchval("SELECT (SELECT count(*) FROM public.student) + (SELECT count(*) FROM public.staff_member) + (SELECT count(*) FROM university.meta)"):
                raise ValueError("Target must be an empty schema; never overwrite a population")
            tables_sql = "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            target_tables = {r["tablename"] for r in await target.fetch(tables_sql)}
            source_tables = {r["tablename"] for r in await source.fetch(tables_sql)}
            excluded = {"vv_schema_migration"}
            report["excludedTables"] = sorted(source_tables - target_tables | excluded)
            # Fixture data may contain FK cycles. Drop and restore definitions in
            # this single target transaction; ADD CONSTRAINT validates every row.
            constraints = await target.fetch("""
                SELECT n.nspname,t.relname,c.conname,pg_get_constraintdef(c.oid) AS definition
                FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE c.contype='f' AND n.nspname IN ('public','university')
            """)
            for c in constraints:
                await target.execute(f"ALTER TABLE {ident(c['nspname'])}.{ident(c['relname'])} DROP CONSTRAINT {ident(c['conname'])}")
            await target.execute("SET LOCAL session_replication_role=replica")
            for table in sorted(source_tables & target_tables - excluded):
                columns_sql = """SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=$1
                      AND is_generated='NEVER' ORDER BY ordinal_position"""
                available = {r["column_name"] for r in await target.fetch(columns_sql, table)}
                old = [r["column_name"] for r in await source.fetch(columns_sql, table)]
                columns = [c for c in old if c in available]
                if set(old) - available:
                    report["excludedColumns"][table] = sorted(set(old) - available)
                # Empty schema migrations can publish default system rows. The
                # snapshot owns shared public fixture data; no population exists yet.
                await target.execute(f"DELETE FROM public.{ident(table)}")
                query = f"SELECT {','.join(map(ident, columns))} FROM public.{ident(table)}"
                batch, count = [], 0
                async for row in source.cursor(query):
                    batch.append(tuple(row))
                    if len(batch) == 1000:
                        await target.copy_records_to_table(table, schema_name="public", columns=columns, records=batch)
                        count += len(batch)
                        batch = []
                if batch:
                    await target.copy_records_to_table(table, schema_name="public", columns=columns, records=batch)
                    count += len(batch)
                report["tables"][table] = count
            await target.execute("SET LOCAL session_replication_role=origin")
            for c in constraints:
                await target.execute(f"ALTER TABLE {ident(c['nspname'])}.{ident(c['relname'])} ADD CONSTRAINT {ident(c['conname'])} {c['definition']}")
            report["validatedForeignKeys"] = len(constraints)
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
        print(f"Imported {sum(report['tables'].values())} rows; validated {report['validatedForeignKeys']} foreign keys")
    finally:
        await source.close()
        await target.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--report", required=True)
    asyncio.run(run(parser.parse_args()))
