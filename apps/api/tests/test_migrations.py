from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from audentra.infrastructure.db.engine import DatabaseEngineOptions, create_database_engine
from audentra.infrastructure.db.migrations import (
    LEGACY_MIGRATION_CRLF_SHA256,
    LEGACY_MIGRATION_SHA256,
    Migration,
    MigrationChecksumMismatch,
    MigrationRunner,
    MissingLegacyMigration,
    PostgresMigrationSession,
    discover_migrations,
    validate_legacy_migrations,
)


class FakeMigrationSession:
    def __init__(self, applied: dict[str, str] | None = None) -> None:
        self.applied = applied or {}
        self.executed: list[Migration] = []
        self.locked = False
        self.released = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def acquire_lock(self) -> None:
        self.locked = True

    async def release_lock(self) -> None:
        self.released = True
        self.locked = False

    async def ensure_migration_table(self) -> None:
        return None

    async def applied_migrations(self) -> dict[str, str]:
        return dict(self.applied)

    async def apply(self, migration: Migration) -> None:
        self.executed.append(migration)


def _migration(name: str, sql: str) -> Migration:
    raw = sql.encode()
    return Migration(
        name=name,
        sql=sql,
        checksum=hashlib.sha256(raw).hexdigest(),
        path=Path(name),
    )


def test_discovery_hashes_canonical_lf_and_preserves_whole_script(tmp_path: Path) -> None:
    raw = b"DO $$ BEGIN\r\n  PERFORM 1;\r\nEND $$;\r\n"
    canonical = raw.replace(b"\r\n", b"\n")
    path = tmp_path / "0019_whole_script.sql"
    path.write_bytes(raw)

    migrations = discover_migrations(tmp_path)

    assert len(migrations) == 1
    assert migrations[0].sql.encode() == raw
    assert migrations[0].checksum == hashlib.sha256(canonical).hexdigest()


def test_discovery_gives_lf_and_crlf_the_same_identity(tmp_path: Path) -> None:
    path = tmp_path / "0019_portable.sql"
    path.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
    crlf_checksum = discover_migrations(tmp_path)[0].checksum

    path.write_bytes(b"SELECT 1;\nSELECT 2;\n")
    lf_checksum = discover_migrations(tmp_path)[0].checksum

    assert crlf_checksum == lf_checksum


def test_runner_skips_applied_and_releases_advisory_lock() -> None:
    first = _migration("0019_first.sql", "SELECT 1;")
    second = _migration("0020_second.sql", "SELECT 2;")
    session = FakeMigrationSession({first.name: first.checksum})

    applied = asyncio.run(MigrationRunner(session, [first, second], validate_legacy=False).run())

    assert applied == [second.name]
    assert session.executed == [second]
    assert session.released is True


def test_runner_rejects_changed_already_applied_migration() -> None:
    migration = _migration("0019_changed.sql", "SELECT 1;")
    session = FakeMigrationSession({migration.name: "0" * 64})

    with pytest.raises(MigrationChecksumMismatch):
        asyncio.run(MigrationRunner(session, [migration], validate_legacy=False).run())
    assert session.executed == []
    assert session.released is True


def test_runner_accepts_known_nest_crlf_checksum_for_canonical_migration() -> None:
    name = "0000_initial.sql"
    migration = Migration(
        name=name,
        sql="SELECT 1;",
        checksum=LEGACY_MIGRATION_SHA256[name],
        path=Path(name),
    )
    session = FakeMigrationSession({name: LEGACY_MIGRATION_CRLF_SHA256[name]})

    applied = asyncio.run(MigrationRunner(session, [migration], validate_legacy=False).run())

    assert applied == []
    assert session.executed == []
    assert session.released is True


def test_runner_rejects_crlf_alias_when_current_historical_file_changed() -> None:
    name = "0000_initial.sql"
    migration = _migration(name, "SELECT 'arbitrary edit';")
    session = FakeMigrationSession({name: LEGACY_MIGRATION_CRLF_SHA256[name]})

    with pytest.raises(MigrationChecksumMismatch):
        asyncio.run(MigrationRunner(session, [migration], validate_legacy=False).run())
    assert session.executed == []
    assert session.released is True


def test_legacy_validation_requires_the_complete_chain() -> None:
    with pytest.raises(MissingLegacyMigration):
        validate_legacy_migrations(())


def test_checked_in_legacy_migrations_are_byte_identical_when_present() -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    if not migrations_dir.is_dir():
        pytest.skip("migration files are copied during repository integration")
    migrations = discover_migrations(migrations_dir)
    validate_legacy_migrations(migrations)
    assert {migration.name for migration in migrations}.issuperset(LEGACY_MIGRATION_SHA256)
    assert LEGACY_MIGRATION_CRLF_SHA256.keys() == LEGACY_MIGRATION_SHA256.keys()
    for name, canonical_checksum in LEGACY_MIGRATION_SHA256.items():
        raw = (migrations_dir / name).read_bytes()
        canonical = raw.replace(b"\r\n", b"\n")
        assert b"\r" not in canonical
        assert hashlib.sha256(canonical).hexdigest() == canonical_checksum
        crlf = canonical.replace(b"\n", b"\r\n")
        assert hashlib.sha256(crlf).hexdigest() == LEGACY_MIGRATION_CRLF_SHA256[name]


def test_legacy_validation_rejects_arbitrary_sql_edit() -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    migrations = list(discover_migrations(migrations_dir))
    first = migrations[0]
    migrations[0] = _migration(first.name, first.sql + "\nSELECT 'arbitrary edit';\n")

    with pytest.raises(MigrationChecksumMismatch):
        validate_legacy_migrations(migrations)


def test_backend_lifecycle_migration_restores_manual_retry_for_legacy_failures() -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "0028_backend_lifecycle_realtime.sql"
    ).read_text(encoding="utf-8")

    assert "jsonb_set(extraction, '{retryable}', 'true'::jsonb, true)" in migration
    assert "mime_type IN ('application/pdf', 'image/jpeg', 'image/png')" in migration
    assert "extraction->>'status' = 'failed'" in migration
    assert "extraction->>'failureCode' = 'unsupported_capability'" in migration


@pytest.mark.postgres
def test_postgres_migration_session_can_take_and_release_lock() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    pytest.importorskip("asyncpg")

    async def exercise() -> None:
        engine = create_database_engine(
            database_url,
            DatabaseEngineOptions(pool_size=1, application_name="migration-lock-test"),
        )
        try:
            async with PostgresMigrationSession(engine) as session:
                await session.acquire_lock()
                await session.release_lock()
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_assistant_conversation_migration_scopes_and_replay_protects_messages() -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "0033_assistant_conversations.sql"
    ).read_text(encoding="utf-8")

    assert "tenant_id uuid NOT NULL REFERENCES tenant(id)" in migration
    assert "student_id uuid NOT NULL REFERENCES student(id) ON DELETE CASCADE" in migration
    assert "role IN ('user', 'assistant')" in migration
    assert "input_mode IN ('text', 'voice')" in migration
    assert "char_length(content) BETWEEN 1 AND 8000" in migration
    assert "assistant_message_client_uidx" in migration
    assert "WHERE client_message_id IS NOT NULL AND role = 'user'" in migration
    assert "assistant_message_history_idx" in migration
    assert "jsonb_typeof(context_receipts) = 'array'" in migration


def test_voice_session_migration_binds_rooms_to_owned_conversations() -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "0034_assistant_voice_sessions.sql"
    ).read_text(encoding="utf-8")

    assert "assistant_conversation_owner_id_uidx" in migration
    assert "room_name varchar(255) NOT NULL UNIQUE" in migration
    assert "provider = 'livekit'" in migration
    assert "status IN ('active', 'ended')" in migration
    assert "REFERENCES assistant_conversation(id, tenant_id, student_id)" in migration
    assert "expires_at > created_at" in migration
    assert (
        "(status = 'active' AND ended_at IS NULL)\n"
        "    OR (status = 'ended' AND ended_at IS NOT NULL)" in migration
    )
