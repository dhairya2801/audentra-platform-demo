# ruff: noqa: S608 -- schema identifiers are validated before interpolation.
"""Canonical tenant identity and portal-bootstrap persistence."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError

_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PostgresTenantRepository:
    """Tenant-scoped reads and atomic staff edits of portal configuration."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        schema: str = "public",
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not _SQL_IDENTIFIER.fullmatch(schema):
            raise ValueError("schema is not a safe PostgreSQL identifier")
        self._engine = engine
        self._schema = schema
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    def _table(self, name: str) -> str:
        return f"{self._schema}.{name}"

    async def get_active_by_id(self, tenant_id: str) -> dict[str, object]:
        """Resolve an active tenant for authenticated/internal ID-only requests."""

        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT tenant.slug, configuration.*
                    FROM {self._table("tenant")} tenant
                    JOIN {self._table("tenant_portal_configuration")} configuration
                      ON configuration.tenant_id=tenant.id
                    WHERE tenant.id=:tenant_id AND tenant.status='active'
                    """
                ),
                {"tenant_id": _uuid(tenant_id)},
            )
            row = result.mappings().first()
        if row is None:
            raise NotFoundError("TENANT_NOT_FOUND", "The tenant was not found")
        return _configuration(cast(Mapping[str, Any], row))

    async def get_staff(self, auth: AuthContext) -> dict[str, object]:
        self._require_staff(auth)
        async with self._engine.connect() as connection:
            row = await self._get_by_tenant(connection, auth.tenant_id)
        if row is None:
            raise NotFoundError(
                "TENANT_CONFIGURATION_NOT_FOUND",
                "The tenant portal configuration has not been provisioned",
            )
        return _configuration(row)

    async def update_staff(
        self,
        auth: AuthContext,
        payload: Mapping[str, object],
        request_id: str,
    ) -> dict[str, object]:
        self._require_staff(auth)
        expected_version = payload.get("expectedVersion")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            raise BadRequestError("VALIDATION_ERROR", "expectedVersion must be a positive integer")
        allowed_groups = {
            "expectedVersion",
            "names",
            "branding",
            "localization",
            "academicContext",
            "contacts",
            "capabilities",
            "publicLinks",
        }
        if set(payload) - allowed_groups:
            raise BadRequestError("VALIDATION_ERROR", "The configuration update has unknown fields")
        changed_groups = sorted(key for key in payload if key != "expectedVersion")
        if not changed_groups:
            raise BadRequestError(
                "VALIDATION_ERROR", "At least one configuration group is required"
            )

        tenant_id = _uuid(auth.tenant_id)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT tenant.slug, configuration.*
                    FROM {self._table("tenant")} tenant
                    JOIN {self._table("tenant_portal_configuration")} configuration
                      ON configuration.tenant_id=tenant.id
                    WHERE tenant.id=:tenant_id AND tenant.status='active'
                    FOR UPDATE OF tenant, configuration
                    """
                ),
                {"tenant_id": tenant_id},
            )
            current = result.mappings().first()
            if current is None:
                raise NotFoundError(
                    "TENANT_CONFIGURATION_NOT_FOUND",
                    "The tenant portal configuration has not been provisioned",
                )
            if int(current["version"]) != expected_version:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "The tenant portal configuration changed in another staff session",
                )

            values = _merged_values(cast(Mapping[str, Any], current), payload)
            next_version = expected_version + 1
            updated_result = await connection.execute(
                text(
                    f"""
                    UPDATE {self._table("tenant_portal_configuration")}
                    SET display_name=:display_name, legal_name=:legal_name,
                        short_name=:short_name, logo_url=:logo_url,
                        logo_alt=:logo_alt, logo_dark_url=:logo_dark_url,
                        logo_dark_alt=:logo_dark_alt, favicon_url=:favicon_url,
                        hero_image_url=:hero_image_url,
                        hero_image_alt=:hero_image_alt,
                        primary_color=:primary_color,
                        secondary_color=:secondary_color,
                        accent_color=:accent_color, locale=:locale,
                        time_zone=:time_zone, currency_code=:currency_code,
                        country_code=:country_code,
                        academic_year_label=:academic_year_label,
                        current_term_label=:current_term_label,
                        default_campus_name=:default_campus_name,
                        contacts=CAST(:contacts AS jsonb),
                        capabilities=CAST(:capabilities AS jsonb),
                        public_links=CAST(:public_links AS jsonb),
                        version=:next_version, updated_at=NOW()
                    WHERE tenant_id=:tenant_id
                    RETURNING *
                    """
                ),
                {
                    **values,
                    "contacts": _json(values["contacts"]),
                    "capabilities": _json(values["capabilities"]),
                    "public_links": _json(values["public_links"]),
                    "next_version": next_version,
                    "tenant_id": tenant_id,
                },
            )
            updated = updated_result.mappings().first()
            if updated is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "The tenant portal configuration changed in another staff session",
                )
            response_row = dict(updated)
            response_row["slug"] = current["slug"]
            occurred_at = self._clock()
            await self._insert_audit(
                connection,
                auth,
                request_id,
                next_version,
                changed_groups,
                occurred_at,
            )
            await self._insert_outbox(
                connection,
                auth,
                request_id,
                next_version,
                changed_groups,
                occurred_at,
            )

        return _configuration(response_row)

    async def _get_by_tenant(
        self, connection: AsyncConnection, tenant_id: str
    ) -> Mapping[str, Any] | None:
        result = await connection.execute(
            text(
                f"""
                SELECT tenant.slug, configuration.*
                FROM {self._table("tenant")} tenant
                JOIN {self._table("tenant_portal_configuration")} configuration
                  ON configuration.tenant_id=tenant.id
                WHERE tenant.id=:tenant_id AND tenant.status='active'
                """
            ),
            {"tenant_id": _uuid(tenant_id)},
        )
        return cast(Mapping[str, Any] | None, result.mappings().first())

    async def _insert_audit(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        request_id: str,
        version: int,
        changed_groups: list[str],
        occurred_at: datetime,
    ) -> None:
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("audit_event")} (
                  id, tenant_id, actor_type, actor_id, student_id, action,
                  resource_type, resource_id, authorization_basis, request_id,
                  correlation_id, metadata, occurred_at, created_at
                ) VALUES (
                  :id, :tenant_id, 'staff', :actor_id, NULL,
                  'staff.tenant_portal_configuration_updated',
                  'tenant_portal_configuration', :tenant_id,
                  'staff_tenant_configuration', :request_id, :request_id,
                  CAST(:metadata AS jsonb), :occurred_at, :occurred_at
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "actor_id": _uuid(auth.actor_id),
                "request_id": request_id,
                "metadata": _json({"version": version, "changedGroups": changed_groups}),
                "occurred_at": occurred_at,
            },
        )

    async def _insert_outbox(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        request_id: str,
        version: int,
        changed_groups: list[str],
        occurred_at: datetime,
    ) -> None:
        event_id = self._uuid_factory()
        payload = {
            "eventId": str(event_id),
            "eventName": "tenant.portal_configuration_updated.v1",
            "occurredAt": _iso(occurred_at),
            "tenantId": auth.tenant_id,
            "aggregateType": "tenant_portal_configuration",
            "aggregateId": auth.tenant_id,
            "aggregateVersion": version,
            "actor": {"type": "staff", "id": auth.actor_id},
            "correlationId": request_id,
            "causationId": str(event_id),
            "data": {"version": version, "changedGroups": changed_groups},
        }
        await connection.execute(
            text(
                f"""
                INSERT INTO {self._table("outbox_event")} (
                  id, tenant_id, event_name, aggregate_type, aggregate_id,
                  aggregate_version, occurred_at, actor_type, actor_id,
                  correlation_id, causation_id, payload, created_at
                ) VALUES (
                  :id, :tenant_id, 'tenant.portal_configuration_updated.v1',
                  'tenant_portal_configuration', :tenant_id, :version,
                  :occurred_at, 'staff', :actor_id, :request_id, :causation_id,
                  CAST(:payload AS jsonb), :occurred_at
                )
                """
            ),
            {
                "id": event_id,
                "tenant_id": _uuid(auth.tenant_id),
                "version": version,
                "occurred_at": occurred_at,
                "actor_id": _uuid(auth.actor_id),
                "request_id": request_id,
                "causation_id": str(event_id),
                "payload": _json(payload),
            },
        )

    @staticmethod
    def _require_staff(auth: AuthContext) -> None:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_ACCESS_REQUIRED", "Staff access is required")


def _merged_values(current: Mapping[str, Any], payload: Mapping[str, object]) -> dict[str, Any]:
    names = (
        _complete_group(
            payload.get("names"),
            "names",
            {"displayName", "legalName", "shortName"},
        )
        if "names" in payload
        else {}
    )
    branding = (
        _complete_group(
            payload.get("branding"),
            "branding",
            {
                "logoUrl",
                "logoAlt",
                "logoDarkUrl",
                "logoDarkAlt",
                "faviconUrl",
                "heroImageUrl",
                "heroImageAlt",
                "primaryColor",
                "secondaryColor",
                "accentColor",
            },
        )
        if "branding" in payload
        else {}
    )
    localization = (
        _complete_group(
            payload.get("localization"),
            "localization",
            {"locale", "timeZone", "currencyCode", "countryCode"},
        )
        if "localization" in payload
        else {}
    )
    academic = (
        _complete_group(
            payload.get("academicContext"),
            "academicContext",
            {"academicYearLabel", "currentTermLabel", "defaultCampusName"},
        )
        if "academicContext" in payload
        else {}
    )
    contacts = (
        _complete_group(
            payload.get("contacts"),
            "contacts",
            {"support", "admissions", "financialAid"},
        )
        if "contacts" in payload
        else _object(current["contacts"])
    )
    capabilities = (
        _group(payload.get("capabilities"), "capabilities")
        if "capabilities" in payload
        else _object(current["capabilities"])
    )
    public_links = (
        _group(payload.get("publicLinks"), "publicLinks")
        if "publicLinks" in payload
        else _object(current["public_links"])
    )
    return {
        "display_name": names.get("displayName", current["display_name"]),
        "legal_name": names.get("legalName", current["legal_name"]),
        "short_name": names.get("shortName", current["short_name"]),
        "logo_url": branding.get("logoUrl", current["logo_url"]),
        "logo_alt": branding.get("logoAlt", current["logo_alt"]),
        "logo_dark_url": branding.get("logoDarkUrl", current["logo_dark_url"]),
        "logo_dark_alt": branding.get("logoDarkAlt", current["logo_dark_alt"]),
        "favicon_url": branding.get("faviconUrl", current["favicon_url"]),
        "hero_image_url": branding.get("heroImageUrl", current["hero_image_url"]),
        "hero_image_alt": branding.get("heroImageAlt", current["hero_image_alt"]),
        "primary_color": branding.get("primaryColor", current["primary_color"]),
        "secondary_color": branding.get("secondaryColor", current["secondary_color"]),
        "accent_color": branding.get("accentColor", current["accent_color"]),
        "locale": localization.get("locale", current["locale"]),
        "time_zone": localization.get("timeZone", current["time_zone"]),
        "currency_code": localization.get("currencyCode", current["currency_code"]),
        "country_code": localization.get("countryCode", current["country_code"]),
        "academic_year_label": academic.get("academicYearLabel", current["academic_year_label"]),
        "current_term_label": academic.get("currentTermLabel", current["current_term_label"]),
        "default_campus_name": academic.get("defaultCampusName", current["default_campus_name"]),
        "contacts": contacts,
        "capabilities": capabilities,
        "public_links": public_links,
    }


def _configuration(row: Mapping[str, Any]) -> dict[str, object]:
    return {
        "tenantId": str(row["tenant_id"]),
        "slug": str(row["slug"]),
        "version": int(row["version"]),
        "names": {
            "displayName": str(row["display_name"]),
            "legalName": str(row["legal_name"]),
            "shortName": str(row["short_name"]),
        },
        "branding": {
            "logoUrl": str(row["logo_url"]),
            "logoAlt": str(row["logo_alt"]),
            "logoDarkUrl": _optional(row["logo_dark_url"]),
            "logoDarkAlt": _optional(row["logo_dark_alt"]),
            "faviconUrl": _optional(row["favicon_url"]),
            "heroImageUrl": _optional(row["hero_image_url"]),
            "heroImageAlt": _optional(row["hero_image_alt"]),
            "primaryColor": str(row["primary_color"]),
            "secondaryColor": str(row["secondary_color"]),
            "accentColor": str(row["accent_color"]),
        },
        "localization": {
            "locale": str(row["locale"]),
            "timeZone": str(row["time_zone"]),
            "currencyCode": str(row["currency_code"]),
            "countryCode": str(row["country_code"]),
        },
        "academicContext": {
            "academicYearLabel": str(row["academic_year_label"]),
            "currentTermLabel": str(row["current_term_label"]),
            "defaultCampusName": _optional(row["default_campus_name"]),
        },
        "contacts": _object(row["contacts"]),
        "capabilities": _object(row["capabilities"]),
        "publicLinks": _object(row["public_links"]),
        "updatedAt": _iso(row["updated_at"]),
    }


def _group(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BadRequestError("VALIDATION_ERROR", f"{name} must be an object")
    return value


def _complete_group(value: object, name: str, required_keys: set[str]) -> Mapping[str, Any]:
    group = _group(value, name)
    if set(group) != required_keys:
        raise BadRequestError(
            "VALIDATION_ERROR",
            f"{name} must include its complete configuration group",
        )
    return group


def _object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise BadRequestError("VALIDATION_ERROR", "Tenant and actor IDs must be UUIDs") from error


def _optional(value: object) -> str | None:
    return None if value is None else str(value)


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _iso(value: object) -> str:
    if not isinstance(value, datetime):
        return str(value)
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized.astimezone(UTC).isoformat().replace("+00:00", "Z")
