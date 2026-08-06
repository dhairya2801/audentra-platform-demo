"""Compatibility runner for the immutable Nest-era SQL migration chain."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self, cast

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .engine import DatabaseEngineOptions, create_database_engine

MIGRATION_LOCK_NAME = "vv-api-schema-migrations"
MIGRATION_FILE_PATTERN = re.compile(r"^\d+.*\.sql$")

# The Nest migrator hashed the bytes present in its deployment checkout. Some
# Windows/local databases therefore recorded CRLF hashes. The historically
# named registry now also pins every immutable Python-era migration through
# 0025 so accidental edits fail before deployment.
LEGACY_MIGRATION_CRLF_SHA256: Mapping[str, str] = {
    "0000_initial.sql": ("ac0d554830a7f53897c21823bca33aef3a1cbf89dada5dc44365c21f6690f5f2"),
    "0001_student_portal_core.sql": (
        "3c68144b72f17d5a3abb804a2b05b10b577943a234d05b2399adba85042c7aed"
    ),
    "0002_agentic_documents.sql": (
        "aed3465d69800c736c37484d223363caf351e698ec6edeb7ee9935517c3d9f77"
    ),
    "0003_student_domain_hubs.sql": (
        "9b86f2b2ff04aad889924ba04a050aba0208bc9d38a964382cf5eb57a39d6530"
    ),
    "0004_contextual_document_uploads.sql": (
        "62f0210288d0428999909682bb07e9f3bd82855f155501eabc4afa3cf97d0e30"
    ),
    "0005_ai_provider_response_attempts.sql": (
        "7e846bd62958974e8930943413a773e498be6f0ffcf84b24cb81d0e6b0053e47"
    ),
    "0006_credential_identity.sql": (
        "aef7b2d09946b6dcb907b26450d26fb330d4767c8eb659c63fc02f6c86b78a0b"
    ),
    "0007_document_processing_policy.sql": (
        "89441676ed3aad901240187aed02b14e3c4a5afeb3a73d50b3080c2ad3e1553d"
    ),
    "0008_onboarding_question_alignment.sql": (
        "f7e969f0d117562bf96f673f3dd1cff6f4ab681e9d066109cac8e17be9a2adea"
    ),
    "0009_document_classification_policy.sql": (
        "9afa14381ee4191cb9be8013da5bc58dfe69f04f0f402f7f6d28c704b39dea83"
    ),
    "0010_tenant_ai_prompt_runtime.sql": (
        "12112e25af781dc9214d13123e89839daa2d2c0dc949b5e8a7eaebfc014a4b7f"
    ),
    "0011_tenant_portal_media.sql": (
        "2ac8c0162837c2aa113b768f8aa18942e423ef954a4ad54608749f2105ceb327"
    ),
    "0012_tenant_immunization_policy.sql": (
        "cd02699c26e4f683e467c82d2367d2b1744b6e8f9432c88ccfe828d6e2936f80"
    ),
    "0013_tenant_academic_campus_content.sql": (
        "5e329f53a6fd10de481f7eed6d78739ec4c80e3ba4b933d81fce9b7df0fc87f3"
    ),
    "0014_course_resources_and_club_events.sql": (
        "3460713ba826b51b3651aa4597bec7f04b1b1d46257a2f9e837f45e6e34e6e3c"
    ),
    "0015_campus_event_visuals.sql": (
        "c1ad75d0934bda4e0b03cabbb80f178beec7df0735b3cc423ef5855f2ff56d19"
    ),
    "0016_tenant_student_rewards.sql": (
        "0ed7dea3212ed142c3de0ebe92e0c1b0687a4a92710595230b59cd80de518909"
    ),
    "0017_student_signed_documents.sql": (
        "4b9122319bfb6df728428aa24ae48ddca7afdeff0b8cfb596a615bf8bc856a1a"
    ),
    "0018_staff_action_center.sql": (
        "a2fad346f1ab632e904228ac89f3b4ddbfb3d44ab7a24d466581960296d83eb6"
    ),
    "0019_student_inquiries.sql": (
        "9a4edddd36e4e8c1bbea002ae2161d0e5cbe61a61f3ca2b22a28b9b8dbfcad39"
    ),
    "0020_staff_inquiry_replies.sql": (
        "e494e3e07b0da8c72550ba6ff97cfe85f7b15cb8d8269dca5d95d19b5d44cd25"
    ),
    "0021_staff_managed_experience.sql": (
        "6b0ca14dac08135c615d9721e78fb3da9e7e1b12abce9283373b8f3befc83ec6"
    ),
    "0022_staff_journey_flow_builder.sql": (
        "fa82d9ddee2b0e61e783a0ed97d52bbec16ab3784ca68152554f7b125cc205d3"
    ),
    "0023_campus_event_advertising.sql": (
        "d13e40848d58f04ae465c1f8bb701ca88b309a65aff45927789de200f1471baf"
    ),
    "0024_staff_credential_identity.sql": (
        "9ea9ed3717ab6c26a8dd304922b7c201eb907587d6e7642da3f4dad07f9e14f2"
    ),
    "0025_student_experience_priorities.sql": (
        "a44836c3bdacabd2f5493b34d31e72a7dedeb1f96cb4337e0d2c0a96640e6eef"
    ),
}

# Canonical LF hashes for immutable migrations through 0025. Git pins these files
# to LF, and discovery normalizes CRLF before hashing so migration identity is
# independent of the operating-system checkout.
LEGACY_MIGRATION_SHA256: Mapping[str, str] = {
    "0000_initial.sql": "e69343bf91636af03b3ff474be78cdf25bc418f74be8c07c7da8109cdd8a0add",
    "0001_student_portal_core.sql": (
        "c3b5eb149c5fb9b189172ac10ef4896197cf109706366ab311e47e29c471efa1"
    ),
    "0002_agentic_documents.sql": (
        "79c48184fe11f691bb20b8a4360058513ab56ff113f3262ba3b3d8abcacdca23"
    ),
    "0003_student_domain_hubs.sql": (
        "6b153bd8b665747a92c96a91a90db4c2692d6ef6512434ca9fdee4116d976487"
    ),
    "0004_contextual_document_uploads.sql": (
        "ace811b99586074119eb0a056f72c795d7b5fff70f3746c0401bcf341721b067"
    ),
    "0005_ai_provider_response_attempts.sql": (
        "74c2283a31890d06e8ba5995ccbecd297516bf3e75c3496c63191c3da55f2d3d"
    ),
    "0006_credential_identity.sql": (
        "4904f77490c99e0c8a30078e79c9f413cad2a9aae96b3b62338841cb28a2507c"
    ),
    "0007_document_processing_policy.sql": (
        "91e4183a182acd1bae5b5b0da0a48bd344f5b9cd3b89f4431edb53edc288b8f5"
    ),
    "0008_onboarding_question_alignment.sql": (
        "0aa4a0fa8fa04a37deb3c77ca08c49d74f4fb9ace70daff6e849aecceb92f7de"
    ),
    "0009_document_classification_policy.sql": (
        "c8d92763aa0eca8eecdd7af91ca7a8f2bdc8403954365094f3689b1a9bc7ba60"
    ),
    "0010_tenant_ai_prompt_runtime.sql": (
        "dcbe8b13b07336131a2730614076401794d206f7482c2716bf6051305f12a25b"
    ),
    "0011_tenant_portal_media.sql": (
        "a4f0eaa3d8acb8d91a0f03519d68a7ce50b4c995b7f4a2d747d328b9971760d2"
    ),
    "0012_tenant_immunization_policy.sql": (
        "bf66c57d1c38e0f400ff59b00a382193292d313965ff5e42aabae1875f375301"
    ),
    "0013_tenant_academic_campus_content.sql": (
        "2243958e4a6de9f6a7ff7ec4baee7ba216f99fb03cb4a6e7ad0cb7bece824978"
    ),
    "0014_course_resources_and_club_events.sql": (
        "14c08dc260d6838b37221def12ae90a8acfb9d02547db39a9bb5c92d2423b255"
    ),
    "0015_campus_event_visuals.sql": (
        "33e7691bbd2a05870f0075656383ce6b2cc3ed0303e3a3cb422d1c814287479a"
    ),
    "0016_tenant_student_rewards.sql": (
        "c0af6c9546a1eba0c66c1af6ee62ef586ed8ffe6582043a77ef963ced4989806"
    ),
    "0017_student_signed_documents.sql": (
        "217a8d9e30f0c9a439901c817f601edca0fbe9f801cac06475a064b99dcb8241"
    ),
    "0018_staff_action_center.sql": (
        "22ebf9b5ea4c7591a3d76e7dfc2e0a336b34712744f9ef71cbcc474906fc26f4"
    ),
    "0019_student_inquiries.sql": (
        "68d90e01da44238980113c65afa0f43a0cd8f63c76edb6509888073d556beef3"
    ),
    "0020_staff_inquiry_replies.sql": (
        "6440d2e69ef7390b333151b823af21ccd88f18ccb621d71d742de601d88d9498"
    ),
    "0021_staff_managed_experience.sql": (
        "dd8a0fd2e7d2e58caf967808bac7e2e5defbb774aa71fe8cd2a5025aea23c39d"
    ),
    "0022_staff_journey_flow_builder.sql": (
        "661b8aaefceca07b49af8d855e60bc2061d70ea322938eec406c17af16c7d45f"
    ),
    "0023_campus_event_advertising.sql": (
        "bec93de49de5c590309c5e739d2dcc3d2f938bec16e867e8a1df6ea75d14006c"
    ),
    "0024_staff_credential_identity.sql": (
        "ccdfe59c6a91dece4156abe75984e660b1bf8e464afb89dfb052817d580aa29d"
    ),
    "0025_student_experience_priorities.sql": (
        "223eb6fd65fc4fb83f3cfd217a21f2d2e0736be954073fe10730103737954fa5"
    ),
}


class MigrationError(RuntimeError):
    """Base migration failure."""


class MigrationChecksumMismatch(MigrationError):
    """An applied or immutable migration no longer matches its checksum."""


class MissingLegacyMigration(MigrationError):
    """One of the required 0000-0018 migration files is absent."""


@dataclass(frozen=True, slots=True)
class Migration:
    name: str
    sql: str
    checksum: str
    path: Path


class MigrationSession(Protocol):
    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def acquire_lock(self) -> None: ...

    async def release_lock(self) -> None: ...

    async def ensure_migration_table(self) -> None: ...

    async def applied_migrations(self) -> Mapping[str, str]: ...

    async def apply(self, migration: Migration) -> None: ...


class _AsyncpgTransaction(Protocol):
    async def start(self) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class _AsyncpgConnection(Protocol):
    async def execute(self, query: str, *args: object) -> str: ...

    async def fetch(self, query: str, *args: object) -> Sequence[Mapping[str, Any]]: ...

    def transaction(self) -> _AsyncpgTransaction: ...


class PostgresMigrationSession:
    """Run whole SQL files through asyncpg while retaining SQLAlchemy pooling."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._connection: AsyncConnection | None = None
        self._raw_connection: Any = None
        self._driver: _AsyncpgConnection | None = None
        self._lock_acquired = False

    async def __aenter__(self) -> Self:
        self._connection = await self._engine.connect()
        self._raw_connection = await self._connection.get_raw_connection()
        self._driver = cast(_AsyncpgConnection, self._raw_connection.driver_connection)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if self._lock_acquired:
                await self.release_lock()
        finally:
            if self._connection is not None:
                await self._connection.close()
            self._connection = None
            self._raw_connection = None
            self._driver = None

    async def acquire_lock(self) -> None:
        await self._require_driver().execute(
            "SELECT pg_advisory_lock(hashtext($1))",
            MIGRATION_LOCK_NAME,
        )
        self._lock_acquired = True

    async def release_lock(self) -> None:
        if self._driver is None or not self._lock_acquired:
            return
        await self._driver.execute(
            "SELECT pg_advisory_unlock(hashtext($1))",
            MIGRATION_LOCK_NAME,
        )
        self._lock_acquired = False

    async def ensure_migration_table(self) -> None:
        await self._require_driver().execute(
            """
            CREATE TABLE IF NOT EXISTS vv_schema_migration (
              name text PRIMARY KEY,
              checksum varchar(64) NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )

    async def applied_migrations(self) -> Mapping[str, str]:
        rows = await self._require_driver().fetch("SELECT name, checksum FROM vv_schema_migration")
        return {str(row["name"]): str(row["checksum"]) for row in rows}

    async def apply(self, migration: Migration) -> None:
        driver = self._require_driver()
        transaction = driver.transaction()
        await transaction.start()
        try:
            # asyncpg executes an unmodified multi-statement SQL script. Splitting on
            # semicolons would corrupt the PL/pgSQL trigger functions in legacy files.
            await driver.execute(migration.sql)
            await driver.execute(
                "INSERT INTO vv_schema_migration (name, checksum) VALUES ($1, $2)",
                migration.name,
                migration.checksum,
            )
            await transaction.commit()
        except BaseException:
            await transaction.rollback()
            raise

    def _require_driver(self) -> _AsyncpgConnection:
        if self._driver is None:
            raise RuntimeError("migration session is not active")
        return self._driver


class MigrationRunner:
    """Apply ordered migrations while rejecting changed historical files."""

    def __init__(
        self,
        session: MigrationSession,
        migrations: Sequence[Migration],
        *,
        validate_legacy: bool = True,
    ) -> None:
        self._session = session
        self._migrations = tuple(migrations)
        self._validate_legacy = validate_legacy

    async def run(self) -> list[str]:
        if self._validate_legacy:
            validate_legacy_migrations(self._migrations)
        applied_now: list[str] = []
        async with self._session as session:
            await session.acquire_lock()
            try:
                await session.ensure_migration_table()
                applied = await session.applied_migrations()
                for migration in self._migrations:
                    previous_checksum = applied.get(migration.name)
                    if previous_checksum is not None:
                        if not _matches_applied_checksum(migration, previous_checksum):
                            raise MigrationChecksumMismatch(
                                f"Applied migration {migration.name} has changed; "
                                "create a new migration instead"
                            )
                        continue
                    await session.apply(migration)
                    applied_now.append(migration.name)
            finally:
                await session.release_lock()
        return applied_now


def discover_migrations(migrations_directory: Path) -> tuple[Migration, ...]:
    if not migrations_directory.is_dir():
        raise FileNotFoundError(f"Migration directory not found: {migrations_directory}")
    migrations: list[Migration] = []
    for path in sorted(migrations_directory.iterdir(), key=lambda candidate: candidate.name):
        if not path.is_file() or not MIGRATION_FILE_PATTERN.fullmatch(path.name):
            continue
        raw = path.read_bytes()
        canonical = _canonical_migration_bytes(raw)
        try:
            sql = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MigrationError(f"Migration {path.name} is not UTF-8") from error
        migrations.append(
            Migration(
                name=path.name,
                sql=sql,
                checksum=hashlib.sha256(canonical).hexdigest(),
                path=path,
            )
        )
    return tuple(migrations)


def _canonical_migration_bytes(raw: bytes) -> bytes:
    """Use LF for identity while preserving the original SQL text for execution."""

    return raw.replace(b"\r\n", b"\n")


def _matches_applied_checksum(migration: Migration, applied_checksum: str) -> bool:
    if applied_checksum == migration.checksum:
        return True
    canonical_checksum = LEGACY_MIGRATION_SHA256.get(migration.name)
    legacy_crlf_checksum = LEGACY_MIGRATION_CRLF_SHA256.get(migration.name)
    return (
        canonical_checksum is not None
        and legacy_crlf_checksum is not None
        and migration.checksum == canonical_checksum
        and applied_checksum == legacy_crlf_checksum
    )


def validate_legacy_migrations(migrations: Sequence[Migration]) -> None:
    by_name = {migration.name: migration for migration in migrations}
    missing = sorted(set(LEGACY_MIGRATION_SHA256).difference(by_name))
    if missing:
        raise MissingLegacyMigration(f"Missing immutable migrations: {', '.join(missing)}")
    for name, expected_checksum in LEGACY_MIGRATION_SHA256.items():
        actual_checksum = by_name[name].checksum
        if actual_checksum != expected_checksum:
            raise MigrationChecksumMismatch(
                f"Immutable migration {name} has checksum {actual_checksum}; "
                f"expected {expected_checksum}"
            )


def resolve_migrations_directory(explicit: str | None = None) -> Path:
    configured = explicit or os.getenv("AUDENTRA_MIGRATIONS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = (
        Path.cwd() / "migrations",
        Path.cwd() / "apps" / "api" / "migrations",
        Path(__file__).resolve().parents[4] / "migrations",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return candidates[0].resolve()


async def run_migrations(database_url: str, migrations_directory: Path) -> list[str]:
    engine = create_database_engine(
        database_url,
        DatabaseEngineOptions(pool_size=1, application_name="audentra-api-migrator"),
    )
    try:
        session = PostgresMigrationSession(engine)
        return await MigrationRunner(session, discover_migrations(migrations_directory)).run()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Audentra PostgreSQL migrations")
    parser.add_argument("--migrations-dir")
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    directory = resolve_migrations_directory(args.migrations_dir)
    applied = asyncio.run(run_migrations(database_url, directory))
    for name in applied:
        print(f"Applied {name}")


if __name__ == "__main__":
    main()
