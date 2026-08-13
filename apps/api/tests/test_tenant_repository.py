from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
from pydantic import ValidationError

from audentra.contracts.requests import UpdateTenantPortalConfigurationRequest
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, ConflictError, NotFoundError
from audentra.infrastructure.postgres.tenant_repository import PostgresTenantRepository
from audentra.infrastructure.seeding.relational import _RESET_PRESERVED_TABLES
from audentra.interfaces.http.dependencies import RESERVED_TENANT_SLUGS, is_valid_tenant_slug

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
STAFF_ID = "00000000-0000-7000-8000-000000000901"
NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def configuration_row(*, version: int = 1, short_name: str = "Aster") -> dict[str, object]:
    return {
        "tenant_id": UUID(TENANT_ID),
        "slug": "aster",
        "version": version,
        "display_name": "Aster University",
        "legal_name": "Aster University",
        "short_name": short_name,
        "logo_url": "/icon.png",
        "logo_alt": "Aster University",
        "logo_dark_url": None,
        "logo_dark_alt": None,
        "favicon_url": "/icon.png",
        "hero_image_url": None,
        "hero_image_alt": None,
        "primary_color": "#171717",
        "secondary_color": "#F5F5F4",
        "accent_color": "#C79A3B",
        "locale": "en-US",
        "time_zone": "America/New_York",
        "currency_code": "USD",
        "country_code": "US",
        "academic_year_label": "2027-2028",
        "current_term_label": "Fall 2027",
        "default_campus_name": "Aster Main Campus",
        "contacts": {
            "support": {
                "label": "Student support",
                "email": "enrollment@aster.edu",
                "phone": None,
                "hours": None,
                "url": None,
            },
            "admissions": None,
            "financialAid": None,
        },
        "capabilities": {"studentPortal": True},
        "public_links": {"privacy": "/privacy"},
        "updated_at": NOW,
    }


class FakeMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None


class FakeResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> FakeMappings:
        return FakeMappings(self.rows)


Handler = Callable[[str, dict[str, object]], FakeResult]


class FakeConnection:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.executions: list[tuple[str, dict[str, object]]] = []

    async def execute(
        self, statement: object, parameters: Mapping[str, object] | None = None
    ) -> FakeResult:
        sql = str(statement)
        values = dict(parameters or {})
        self.executions.append((sql, values))
        return self.handler(sql, values)


class FakeContext(AbstractAsyncContextManager[FakeConnection]):
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.begin_count = 0

    def connect(self) -> FakeContext:
        return FakeContext(self.connection)

    def begin(self) -> FakeContext:
        self.begin_count += 1
        return FakeContext(self.connection)


def auth(actor_type: Literal["student", "staff"] = "staff") -> AuthContext:
    return AuthContext(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        actor_id=STAFF_ID,
        actor_type=actor_type,
        tenant_slug="aster",
    )


def test_migration_adds_lifecycle_slug_and_bootstrapped_one_to_one_configuration() -> None:
    migration = (
        Path(__file__).parents[1] / "migrations" / "0036_tenant_portal_configuration.sql"
    ).read_text(encoding="utf-8")

    assert "ADD COLUMN slug varchar(63)" in migration
    assert "ADD COLUMN demo_auth_enabled boolean NOT NULL DEFAULT false" in migration
    assert "ADD COLUMN status varchar(24) NOT NULL DEFAULT 'deactivated'" in migration
    assert "'active', 'suspended', 'deactivated'" in migration
    assert "CREATE TABLE tenant_portal_configuration" in migration
    assert "tenant_id uuid PRIMARY KEY REFERENCES tenant(id) ON DELETE RESTRICT" in migration
    assert "version integer NOT NULL DEFAULT 1" in migration
    assert "tenant_slug_immutable_after_provisioning" in migration
    assert "demo_auth_enabled = true" in migration
    assert "^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$" in migration
    assert "'offer', 'onboarding', 'payments'" in migration
    assert "A provisioned tenant slug is immutable" in migration
    assert "INSERT INTO staff_knowledge_card" in migration
    assert "INSERT INTO staff_core_play" in migration
    assert "ON CONFLICT (id) DO NOTHING" in migration
    assert migration.count("00000000-0000-7000-8000-000000000001") >= 2
    assert migration.count("00000000-0000-7000-8000-000000000002") >= 2
    assert migration.index("INSERT INTO tenant (id, name, status)") < migration.index(
        "INSERT INTO tenant_portal_configuration"
    )


def test_explicit_demo_reset_preserves_staff_edited_tenant_configuration() -> None:
    assert "tenant" in _RESET_PRESERVED_TABLES
    assert "tenant_portal_configuration" in _RESET_PRESERVED_TABLES
    assert "staff_knowledge_card" in _RESET_PRESERVED_TABLES
    assert "staff_core_play" in _RESET_PRESERVED_TABLES


def test_tenant_slug_rejects_trailing_hyphens_and_unscoped_routes() -> None:
    assert is_valid_tenant_slug("north-campus") is True
    assert is_valid_tenant_slug("north-campus-") is False
    assert is_valid_tenant_slug("onboarding") is False
    assert {
        "appointments",
        "campus-life",
        "classrooms",
        "dashboard",
        "documents",
        "edward",
        "enrollment",
        "financials",
        "health",
        "help",
        "messages",
        "offer",
        "onboarding",
        "payments",
        "profile",
        "sign-in",
        "staff",
        "v1",
    } == RESERVED_TENANT_SLUGS


def test_patch_request_requires_complete_groups_and_safe_typed_values() -> None:
    accepted = UpdateTenantPortalConfigurationRequest.model_validate(
        {
            "expectedVersion": 1,
            "names": {
                "displayName": "Aster University",
                "legalName": "Aster University",
                "shortName": "Aster",
            },
            "capabilities": {"studentPortal": True},
        }
    )
    assert accepted.public_payload()["expectedVersion"] == 1

    with pytest.raises(ValidationError):
        UpdateTenantPortalConfigurationRequest.model_validate(
            {"expectedVersion": 1, "names": {"shortName": "Aster"}}
        )
    with pytest.raises(ValidationError):
        UpdateTenantPortalConfigurationRequest.model_validate(
            {"expectedVersion": 1, "capabilities": {"studentPortal": 1}}
        )
    with pytest.raises(ValidationError):
        UpdateTenantPortalConfigurationRequest.model_validate(
            {
                "expectedVersion": 1,
                "branding": {
                    "logoUrl": "javascript:alert(1)",
                    "logoAlt": "Aster",
                    "logoDarkUrl": None,
                    "logoDarkAlt": None,
                    "faviconUrl": None,
                    "heroImageUrl": None,
                    "heroImageAlt": None,
                    "primaryColor": "#171717",
                    "secondaryColor": "#F5F5F4",
                    "accentColor": "#C79A3B",
                },
            }
        )


@pytest.mark.anyio
async def test_staff_patch_locks_versions_and_writes_audit_and_outbox_atomically() -> None:
    updated = False

    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        nonlocal updated
        if "FOR UPDATE OF tenant, configuration" in sql:
            return FakeResult([configuration_row()])
        if "UPDATE public.tenant_portal_configuration" in sql:
            updated = True
            row = configuration_row(version=2, short_name="Aster U")
            row.pop("slug")
            return FakeResult([row])
        if "SELECT tenant.slug, configuration.*" in sql:
            return FakeResult(
                [configuration_row(version=2, short_name="Aster U")] if updated else []
            )
        return FakeResult()

    connection = FakeConnection(handler)
    engine = FakeEngine(connection)
    ids = iter(
        (
            UUID("10000000-0000-7000-8000-000000000001"),
            UUID("10000000-0000-7000-8000-000000000002"),
        )
    )
    repository = PostgresTenantRepository(
        engine,  # type: ignore[arg-type]
        clock=lambda: NOW,
        uuid_factory=lambda: next(ids),
    )

    response = await repository.update_staff(
        auth(),
        {
            "expectedVersion": 1,
            "names": {
                "displayName": "Aster University",
                "legalName": "Aster University",
                "shortName": "Aster U",
            },
        },
        "request-tenant-update",
    )

    statements = [sql for sql, _ in connection.executions]
    assert engine.begin_count == 1
    assert response["version"] == 2
    assert response["names"] == {
        "displayName": "Aster University",
        "legalName": "Aster University",
        "shortName": "Aster U",
    }
    assert next(i for i, sql in enumerate(statements) if "FOR UPDATE" in sql) < next(
        i for i, sql in enumerate(statements) if "UPDATE public.tenant_portal" in sql
    )
    assert any("INSERT INTO public.audit_event" in sql for sql in statements)
    assert any("INSERT INTO public.outbox_event" in sql for sql in statements)
    locked_values = next(
        values
        for sql, values in connection.executions
        if "FOR UPDATE OF tenant, configuration" in sql
    )
    assert locked_values == {"tenant_id": UUID(TENANT_ID)}
    outbox_values = next(
        values for sql, values in connection.executions if "INSERT INTO public.outbox_event" in sql
    )
    assert outbox_values["tenant_id"] == UUID(TENANT_ID)
    assert outbox_values["version"] == 2


@pytest.mark.anyio
async def test_staff_patch_rejects_stale_version_before_any_write() -> None:
    def handler(sql: str, values: dict[str, object]) -> FakeResult:
        del values
        return FakeResult([configuration_row(version=2)]) if "FOR UPDATE" in sql else FakeResult()

    connection = FakeConnection(handler)
    repository = PostgresTenantRepository(FakeEngine(connection))  # type: ignore[arg-type]

    with pytest.raises(ConflictError) as error:
        await repository.update_staff(
            auth(),
            {
                "expectedVersion": 1,
                "names": {
                    "displayName": "Aster University",
                    "legalName": "Aster University",
                    "shortName": "Aster U",
                },
            },
            "request-stale",
        )

    assert error.value.code == "VERSION_CONFLICT"
    assert len(connection.executions) == 1


@pytest.mark.anyio
async def test_public_bootstrap_unknown_or_deactivated_slug_is_not_found() -> None:
    connection = FakeConnection(lambda sql, values: FakeResult())
    repository = PostgresTenantRepository(FakeEngine(connection))  # type: ignore[arg-type]

    with pytest.raises(NotFoundError) as error:
        await repository.get_public_by_slug("deactivated-tenant")

    assert error.value.code == "TENANT_NOT_FOUND"
    sql, values = connection.executions[0]
    assert "tenant.status='active'" in sql
    assert values == {"slug": "deactivated-tenant"}


@pytest.mark.anyio
async def test_id_only_tenant_resolution_still_requires_active_database_configuration() -> None:
    connection = FakeConnection(lambda sql, values: FakeResult())
    repository = PostgresTenantRepository(FakeEngine(connection))  # type: ignore[arg-type]

    with pytest.raises(NotFoundError) as error:
        await repository.get_active_by_id(TENANT_ID)

    assert error.value.code == "TENANT_NOT_FOUND"
    sql, values = connection.executions[0]
    assert "tenant.id=:tenant_id AND tenant.status='active'" in sql
    assert values == {"tenant_id": UUID(TENANT_ID)}


@pytest.mark.anyio
async def test_student_cannot_read_or_update_staff_configuration() -> None:
    connection = FakeConnection(lambda sql, values: FakeResult([configuration_row()]))
    repository = PostgresTenantRepository(FakeEngine(connection))  # type: ignore[arg-type]

    with pytest.raises(ApiError) as read_error:
        await repository.get_staff(auth("student"))
    with pytest.raises(ApiError) as update_error:
        await repository.update_staff(
            auth("student"),
            {"expectedVersion": 1, "publicLinks": {}},
            "request-forbidden",
        )

    assert read_error.value.status_code == 403
    assert update_error.value.status_code == 403
    assert connection.executions == []
