"""Integrity-checked loader for the canonical legacy demo-data snapshot."""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from dataclasses import dataclass
from typing import cast

from .demo_fixture import (
    FIXTURE_BASE85,
    FIXTURE_ROW_COUNT,
    FIXTURE_SHA256,
    FIXTURE_TABLE_COUNT,
    FIXTURE_TABLES,
)


class SeedFixtureError(RuntimeError):
    """The embedded seed fixture is corrupt or structurally unsafe."""


@dataclass(frozen=True, slots=True)
class FixtureTable:
    name: str
    rows: tuple[dict[str, object], ...]


def load_demo_fixture() -> tuple[FixtureTable, ...]:
    try:
        raw = zlib.decompress(base64.b85decode(FIXTURE_BASE85))
    except (ValueError, zlib.error) as error:
        raise SeedFixtureError("The embedded demo fixture cannot be decoded") from error
    actual_digest = hashlib.sha256(raw).hexdigest()
    if actual_digest != FIXTURE_SHA256:
        raise SeedFixtureError("The embedded demo fixture checksum does not match")
    try:
        decoded = cast(object, json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SeedFixtureError("The embedded demo fixture is not valid JSON") from error
    if not isinstance(decoded, list):
        raise SeedFixtureError("The embedded demo fixture must be a table array")

    tables: list[FixtureTable] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise SeedFixtureError("Every demo fixture table must be an object")
        name = item.get("table")
        raw_rows = item.get("rows")
        if not isinstance(name, str) or not isinstance(raw_rows, list):
            raise SeedFixtureError("A demo fixture table has invalid metadata")
        rows: list[dict[str, object]] = []
        for row in raw_rows:
            if not isinstance(row, dict) or not all(isinstance(key, str) for key in row):
                raise SeedFixtureError(f"Demo fixture table {name!r} contains an invalid row")
            rows.append({str(key): value for key, value in row.items()})
        tables.append(FixtureTable(name=name, rows=tuple(rows)))

    names = tuple(table.name for table in tables)
    row_count = sum(len(table.rows) for table in tables)
    if (
        names != FIXTURE_TABLES
        or len(tables) != FIXTURE_TABLE_COUNT
        or row_count != FIXTURE_ROW_COUNT
    ):
        raise SeedFixtureError("The embedded demo fixture inventory does not match its manifest")
    return tuple(tables)
