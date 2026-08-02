"""Command-line composition for relational and media demo seeds."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from audentra.bootstrap.settings import RuntimeSettings
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.storage.s3 import S3ObjectStorage

from .media import MediaSeedReport, seed_portal_media
from .relational import RelationalSeedReport, seed_relational_data
from .safety import seed_environment

SeedMode = Literal["data", "media", "all"]


@dataclass(frozen=True, slots=True)
class SeedRunReport:
    data: RelationalSeedReport | None
    media: MediaSeedReport | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed deterministic Audentra demo data in development or test only"
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--data", action="store_true", help="seed PostgreSQL demo rows")
    modes.add_argument("--media", action="store_true", help="seed S3/MinIO portal media")
    modes.add_argument("--all", action="store_true", help="seed data, then media")
    parser.add_argument(
        "--media-root",
        type=Path,
        help="override PORTAL_MEDIA_DIR for the checked-in JPEG assets",
    )
    return parser


def selected_mode(arguments: argparse.Namespace) -> SeedMode:
    if arguments.data:
        return "data"
    if arguments.media:
        return "media"
    return "all"


async def execute_seed(
    mode: SeedMode,
    *,
    environment: Mapping[str, str] | None = None,
    media_root: Path | None = None,
) -> SeedRunReport:
    values = os.environ if environment is None else environment
    safe_environment = seed_environment(values)
    settings = RuntimeSettings.from_environment(values)
    data_report: RelationalSeedReport | None = None
    media_report: MediaSeedReport | None = None

    if mode in {"data", "all"}:
        database_options = replace(settings.database, application_name="audentra-seeder")
        engine = create_database_engine(settings.database_url, database_options)
        try:
            data_report = await seed_relational_data(
                engine,
                environment=safe_environment,
            )
        finally:
            await engine.dispose()

    if mode in {"media", "all"}:
        storage = S3ObjectStorage(settings.object_storage)
        try:
            media_report = await seed_portal_media(
                storage,
                environment=safe_environment,
                media_root=media_root,
            )
        finally:
            await storage.close()
    return SeedRunReport(data=data_report, media=media_report)


def run() -> None:
    arguments = build_parser().parse_args()
    try:
        report = asyncio.run(
            execute_seed(
                selected_mode(arguments),
                media_root=arguments.media_root,
            )
        )
    except (OSError, RuntimeError, SQLAlchemyError, ValueError) as error:
        raise SystemExit(f"audentra-seed: {error}") from error
    if report.data is not None:
        print(f"Seeded {report.data.rows} relational rows across {report.data.tables} tables")
    if report.media is not None:
        print(
            f"Verified {report.media.assets} media assets "
            f"({report.media.uploaded} uploaded, {report.media.unchanged} unchanged)"
        )


if __name__ == "__main__":
    run()
