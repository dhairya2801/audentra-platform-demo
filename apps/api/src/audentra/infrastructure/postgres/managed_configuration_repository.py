"""Durable, tenant-scoped staff publications and student experience updates."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import yaml  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError

JsonDict = dict[str, Any]
ManagedConfigurationKind = Literal["journeys", "campus_life", "academics"]

_CONFIGURATION_NAMES: dict[ManagedConfigurationKind, str] = {
    "journeys": "journeys",
    "campus_life": "campus_life",
    "academics": "academics",
}
_CONFIGURATION_FILES: dict[ManagedConfigurationKind, str] = {
    "journeys": "journeys.yaml",
    "campus_life": "campus-life.yaml",
    "academics": "academics.yaml",
}
_COLLECTION_NAMES: dict[ManagedConfigurationKind, str] = {
    "journeys": "flows",
    "campus_life": "events",
    "academics": "courses",
}
_TASK_CODE = re.compile(r"^[a-z][a-z0-9_]{1,99}$")
_CORE_ONBOARDING_STEPS = frozenset(
    {
        "offer",
        "about_you",
        "housing",
        "campus_life",
        "emergency_contacts",
        "family_permissions",
        "review_and_sign",
        "deposit",
    }
)
_TERMINAL_REQUIREMENT_STATUSES = frozenset({"not_applicable", "completed", "waived", "expired"})
_SUBMISSION_TYPES = {
    "upload_file": "document",
    "payment": "payment",
    "scheduling": "appointment",
    "form": "form",
    "single_select": "form",
    "multiple_select": "form",
    "selection_flow": "form",
    "approval": "form",
    "signature": "form",
    "information": "none",
}
_CAMPUS_CATEGORIES = frozenset({"academic", "social", "career", "wellness", "athletics"})
_CAMPUS_ACCENTS = frozenset({"gold", "navy", "blue", "coral"})
_CAMPUS_THEMES = frozenset({"festival", "discovery", "career", "community"})


class PostgresManagedConfigurationRepository:
    """Publish managed YAML atomically and project it into canonical portal tables."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self.engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory

    async def get(
        self,
        auth: AuthContext,
        kind: str,
        fallback_config: Mapping[str, Any],
    ) -> JsonDict:
        self._require_staff(auth)
        normalized = _configuration_kind(kind)
        async with self.engine.connect() as connection:
            row = await self._active_row(connection, auth.tenant_id, normalized)
        if row is None:
            return _validated_fallback(normalized, fallback_config)
        return _public_configuration(row)

    async def list_active(self, auth: AuthContext) -> dict[str, JsonDict]:
        self._require_staff(auth)
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, kind, version, yaml, document, record_count,
                           change_summary, created_by, published_at
                    FROM staff_managed_configuration_version
                    WHERE tenant_id=:tenant_id AND active=true
                    ORDER BY kind
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id)},
            )
            return {
                str(row["kind"]): _public_configuration(dict(row))
                for row in result.mappings().all()
            }

    async def publish(
        self,
        auth: AuthContext,
        kind: str,
        payload: Mapping[str, Any],
        fallback_config: Mapping[str, Any],
        request_id: str,
    ) -> JsonDict:
        self._require_staff(auth)
        normalized = _configuration_kind(kind)
        yaml_text = str(payload.get("yaml") or "")
        document = parse_managed_configuration(
            normalized,
            yaml_text,
            tenant_slug=auth.tenant_slug,
        )
        fallback = _validated_fallback(normalized, fallback_config)
        expected_version = _positive_integer(payload.get("expectedVersion"), "expectedVersion")
        change_summary = _optional_string(payload.get("changeSummary"), maximum=500)
        publication_id = self._uuid_factory()

        async with self.engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"managed-config:{auth.tenant_id}:{normalized}"},
            )
            current = await self._active_row(
                connection,
                auth.tenant_id,
                normalized,
                for_update=True,
            )
            actual_version = int(current["version"]) if current else int(fallback["version"])
            if actual_version != expected_version:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This managed configuration changed in another session",
                )
            version = actual_version + 1
            if current is not None:
                await connection.execute(
                    text(
                        """
                        UPDATE staff_managed_configuration_version
                        SET active=false
                        WHERE id=:id AND tenant_id=:tenant_id
                        """
                    ),
                    {"id": current["id"], "tenant_id": _uuid(auth.tenant_id)},
                )

            record_count = _record_count(normalized, document)
            published_at = self._clock()
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_managed_configuration_version (
                      id, tenant_id, kind, version, yaml, document, record_count,
                      change_summary, created_by, active, created_at, published_at
                    ) VALUES (
                      :id, :tenant_id, :kind, :version, :yaml, CAST(:document AS jsonb),
                      :record_count, :change_summary, :created_by, true,
                      :published_at, :published_at
                    )
                    """
                ),
                {
                    "id": publication_id,
                    "tenant_id": _uuid(auth.tenant_id),
                    "kind": normalized,
                    "version": version,
                    "yaml": yaml_text,
                    "document": _json(document),
                    "record_count": record_count,
                    "change_summary": change_summary,
                    "created_by": _uuid(auth.actor_id),
                    "published_at": published_at,
                },
            )

            materialized = 0
            previous_document = (
                _mapping(current.get("document"))
                if current is not None
                else parse_managed_configuration(
                    normalized,
                    str(fallback["yaml"]),
                    tenant_slug=None,
                )
            )
            if normalized == "journeys":
                materialized = await self._materialize_journeys(
                    connection,
                    auth,
                    document,
                    publication_id,
                    published_at,
                )
            elif normalized == "campus_life":
                materialized = await self._materialize_campus_life(
                    connection,
                    auth,
                    document,
                    previous_document,
                )
            else:
                materialized = await self._materialize_academics(connection, auth, document)

            await self._insert_publication_audit(
                connection,
                auth,
                publication_id,
                normalized,
                version,
                record_count,
                materialized,
                request_id,
            )
            await self._insert_publication_outbox(
                connection,
                auth,
                publication_id,
                normalized,
                version,
                record_count,
                materialized,
                request_id,
                published_at,
            )

        return {
            "id": str(publication_id),
            "kind": normalized,
            "fileName": _CONFIGURATION_FILES[normalized],
            "version": version,
            "yaml": yaml_text,
            "recordCount": record_count,
            "updatedAt": _iso(published_at),
            "updatedBy": auth.actor_id,
            **({"changeSummary": change_summary} if change_summary else {}),
        }

    async def list_student_updates(self, auth: AuthContext) -> list[JsonDict]:
        self._require_student(auth)
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT experience_update.id, experience_update.kind,
                           experience_update.title, experience_update.description,
                           experience_update.status, experience_update.version,
                           experience_update.created_at,
                           definition.code AS requirement_code
                    FROM student_experience_update experience_update
                    LEFT JOIN student_requirement requirement
                      ON requirement.id=experience_update.requirement_id
                     AND requirement.tenant_id=experience_update.tenant_id
                    LEFT JOIN requirement_definition_version definition
                      ON definition.id=requirement.requirement_definition_version_id
                     AND definition.tenant_id=requirement.tenant_id
                    WHERE experience_update.tenant_id=:tenant_id
                      AND experience_update.student_id=:student_id
                      AND experience_update.status IN ('pending', 'deferred')
                    ORDER BY experience_update.created_at, experience_update.id
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                },
            )
            return [_map_student_update(dict(row)) for row in result.mappings().all()]

    async def decide_student_update(
        self,
        auth: AuthContext,
        update_id: str,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> JsonDict:
        self._require_student(auth)
        action = str(payload.get("action") or "")
        if action not in {"handle_now", "later"}:
            raise BadRequestError(
                "INVALID_EXPERIENCE_UPDATE_ACTION",
                "Choose handle_now or later",
            )
        expected_version = _positive_integer(payload.get("expectedVersion"), "expectedVersion")
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, requirement_id, status, version
                    FROM student_experience_update
                    WHERE id=:id AND tenant_id=:tenant_id AND student_id=:student_id
                    FOR UPDATE
                    """
                ),
                {
                    "id": _uuid(update_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError(
                    "STUDENT_EXPERIENCE_UPDATE_NOT_FOUND",
                    "The student experience update was not found",
                )
            if int(row["version"]) != expected_version:
                raise ConflictError("VERSION_CONFLICT", "This update changed in another session")
            if row["status"] == "acknowledged":
                raise ConflictError(
                    "EXPERIENCE_UPDATE_ALREADY_ACKNOWLEDGED",
                    "This update has already been acknowledged",
                )
            next_status = "acknowledged" if action == "handle_now" else "deferred"
            updated = await connection.execute(
                text(
                    """
                    UPDATE student_experience_update
                    SET status=CAST(:status AS varchar),
                        acknowledged_at=CASE
                          WHEN CAST(:status AS varchar)='acknowledged' THEN NOW()
                          ELSE NULL
                        END,
                        deferred_at=CASE
                          WHEN CAST(:status AS varchar)='deferred' THEN NOW()
                          ELSE deferred_at
                        END,
                        version=version+1, updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND student_id=:student_id
                    RETURNING id, requirement_id, status, version
                    """
                ),
                {
                    "status": next_status,
                    "id": _uuid(update_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                },
            )
            updated_row = updated.mappings().one()
            requirement_slug = await self._requirement_slug(
                connection,
                auth,
                updated_row.get("requirement_id"),
            )
            await self._insert_student_decision_audit(
                connection,
                auth,
                update_id,
                action,
                int(updated_row["version"]),
                request_id,
            )
            return {
                "id": str(updated_row["id"]),
                "status": str(updated_row["status"]),
                "version": int(updated_row["version"]),
                "requirementSlug": requirement_slug,
            }

    async def _active_row(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        kind: ManagedConfigurationKind,
        *,
        for_update: bool = False,
    ) -> JsonDict | None:
        query = (
            """
            SELECT id, kind, version, yaml, document, record_count,
                   change_summary, created_by, published_at
            FROM staff_managed_configuration_version
            WHERE tenant_id=:tenant_id AND kind=:kind AND active=true
            FOR UPDATE
            """
            if for_update
            else """
            SELECT id, kind, version, yaml, document, record_count,
                   change_summary, created_by, published_at
            FROM staff_managed_configuration_version
            WHERE tenant_id=:tenant_id AND kind=:kind AND active=true
            """
        )
        result = await connection.execute(
            text(query),
            {"tenant_id": _uuid(tenant_id), "kind": kind},
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _materialize_journeys(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document: Mapping[str, Any],
        publication_id: UUID,
        published_at: datetime,
    ) -> int:
        tasks = materialized_journey_tasks(document)
        if not tasks:
            raise BadRequestError(
                "MANAGED_JOURNEY_EMPTY",
                "A published journey must contain at least one enrollment task",
            )
        version_result = await connection.execute(
            text(
                """
                SELECT COALESCE(MAX(version), 0) AS version
                FROM journey_definition_version
                WHERE tenant_id=:tenant_id AND code='staff_managed_enrollment'
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id)},
        )
        journey_version = int(version_result.scalar_one()) + 1
        journey_definition_id = self._uuid_factory()
        await connection.execute(
            text(
                """
                UPDATE journey_definition_version SET active=0, updated_at=NOW()
                WHERE tenant_id=:tenant_id AND active=1
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id)},
        )
        await connection.execute(
            text(
                """
                INSERT INTO journey_definition_version (
                  id, tenant_id, code, version, active, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, 'staff_managed_enrollment', :version, 1, NOW(), NOW()
                )
                """
            ),
            {
                "id": journey_definition_id,
                "tenant_id": _uuid(auth.tenant_id),
                "version": journey_version,
            },
        )

        definitions: dict[str, UUID] = {}
        for task in tasks:
            result = await connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(version), 0) AS version
                    FROM requirement_definition_version
                    WHERE tenant_id=:tenant_id AND code=:code
                    """
                ),
                {"tenant_id": _uuid(auth.tenant_id), "code": task["code"]},
            )
            definition_id = self._uuid_factory()
            definitions[str(task["code"])] = definition_id
            await connection.execute(
                text(
                    """
                    INSERT INTO requirement_definition_version (
                      id, tenant_id, code, title, description, blocking,
                      display_order, depends_on_codes, due_offset_days, version,
                      submission_type, responsible_office, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :code, :title, :description, :blocking,
                      :display_order, CAST(:depends_on_codes AS text[]), :due_offset_days,
                      :version, :submission_type, :responsible_office, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": definition_id,
                    "tenant_id": _uuid(auth.tenant_id),
                    "code": task["code"],
                    "title": task["title"],
                    "description": task["description"],
                    "blocking": 1 if task["required"] else 0,
                    "display_order": task["displayOrder"],
                    "depends_on_codes": task["dependsOn"],
                    "due_offset_days": task["dueOffsetDays"],
                    "version": int(result.scalar_one()) + 1,
                    "submission_type": task["submissionType"],
                    "responsible_office": task["owner"],
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO journey_requirement_definition (
                      journey_definition_version_id, requirement_definition_version_id
                    ) VALUES (:journey_definition_id, :requirement_definition_id)
                    """
                ),
                {
                    "journey_definition_id": journey_definition_id,
                    "requirement_definition_id": definition_id,
                },
            )
            if int(task["points"]) > 0:
                await connection.execute(
                    text(
                        """
                        INSERT INTO tenant_reward_rule (
                          id, tenant_id, code, title, description,
                          trigger_type, trigger_key, trigger_properties,
                          points, max_awards_per_student, display_order, enabled,
                          created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :code, :title, :description,
                          'requirement_completed', :trigger_key, '{}'::jsonb,
                          :points, 1, :display_order, true, NOW(), NOW()
                        )
                        ON CONFLICT (tenant_id, code) DO UPDATE SET
                          title=EXCLUDED.title,
                          description=EXCLUDED.description,
                          trigger_type='requirement_completed',
                          trigger_key=EXCLUDED.trigger_key,
                          trigger_properties='{}'::jsonb,
                          points=EXCLUDED.points,
                          max_awards_per_student=1,
                          display_order=EXCLUDED.display_order,
                          enabled=true,
                          starts_at=NULL,
                          ends_at=NULL,
                          updated_at=NOW()
                        """
                    ),
                    {
                        "id": self._uuid_factory(),
                        "tenant_id": _uuid(auth.tenant_id),
                        "code": task["code"],
                        "title": task["title"],
                        "description": task["description"],
                        "trigger_key": task["code"],
                        "points": task["points"],
                        "display_order": task["displayOrder"],
                    },
                )
            await connection.execute(
                text(
                    """
                    UPDATE student_requirement requirement
                    SET requirement_definition_version_id=:definition_id,
                        version=requirement.version+1, updated_at=NOW()
                    FROM enrollment_journey journey,
                         requirement_definition_version previous_definition
                    WHERE requirement.journey_id=journey.id
                      AND requirement.tenant_id=journey.tenant_id
                      AND previous_definition.id=requirement.requirement_definition_version_id
                      AND previous_definition.tenant_id=requirement.tenant_id
                      AND journey.tenant_id=:tenant_id
                      AND previous_definition.code=:code
                      AND requirement.status NOT IN (
                        'not_applicable', 'completed', 'waived', 'expired'
                      )
                    """
                ),
                {
                    "definition_id": definition_id,
                    "tenant_id": _uuid(auth.tenant_id),
                    "code": task["code"],
                },
            )

        await connection.execute(
            text(
                """
                UPDATE enrollment_journey
                SET journey_definition_version_id=:journey_definition_id,
                    version=version+1,
                    updated_at=NOW()
                WHERE tenant_id=:tenant_id AND status<>'cancelled'
                  AND journey_definition_version_id<>:journey_definition_id
                """
            ),
            {
                "journey_definition_id": journey_definition_id,
                "tenant_id": _uuid(auth.tenant_id),
            },
        )
        journey_result = await connection.execute(
            text(
                """
                SELECT id, student_id
                FROM enrollment_journey
                WHERE tenant_id=:tenant_id AND status<>'cancelled'
                ORDER BY id
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id)},
        )
        journeys = [dict(row) for row in journey_result.mappings().all()]
        inserted = 0
        for journey in journeys:
            for task in tasks:
                existing = await connection.execute(
                    text(
                        """
                        SELECT requirement.id
                        FROM student_requirement requirement
                        JOIN requirement_definition_version definition
                          ON definition.id=requirement.requirement_definition_version_id
                         AND definition.tenant_id=requirement.tenant_id
                        WHERE requirement.tenant_id=:tenant_id
                          AND requirement.journey_id=:journey_id
                          AND definition.code=:code
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "journey_id": journey["id"],
                        "code": task["code"],
                    },
                )
                if existing.first() is not None:
                    continue
                dependencies = cast(list[str], task["dependsOn"])
                completed_dependencies: set[str] = set()
                if dependencies:
                    dependency_result = await connection.execute(
                        text(
                            """
                            SELECT definition.code, requirement.status
                            FROM student_requirement requirement
                            JOIN requirement_definition_version definition
                              ON definition.id=requirement.requirement_definition_version_id
                             AND definition.tenant_id=requirement.tenant_id
                            WHERE requirement.tenant_id=:tenant_id
                              AND requirement.journey_id=:journey_id
                              AND definition.code=ANY(CAST(:dependencies AS text[]))
                            """
                        ),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "journey_id": journey["id"],
                            "dependencies": dependencies,
                        },
                    )
                    completed_dependencies = {
                        str(row["code"])
                        for row in dependency_result.mappings().all()
                        if row["status"] in _TERMINAL_REQUIREMENT_STATUSES
                    }
                progress = int(task["initialProgressPercent"])
                status = (
                    "blocked"
                    if set(dependencies) - completed_dependencies
                    else "in_progress"
                    if progress > 0
                    else "ready"
                )
                requirement_id = self._uuid_factory()
                due_at = (
                    None
                    if task["dueOffsetDays"] is None
                    else published_at + timedelta(days=int(task["dueOffsetDays"]))
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO student_requirement (
                          id, tenant_id, journey_id, requirement_definition_version_id,
                          status, due_at, progress_percent, version, created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :journey_id, :definition_id,
                          :status, :due_at, :progress, 1, NOW(), NOW()
                        )
                        """
                    ),
                    {
                        "id": requirement_id,
                        "tenant_id": _uuid(auth.tenant_id),
                        "journey_id": journey["id"],
                        "definition_id": definitions[str(task["code"])],
                        "status": status,
                        "due_at": due_at,
                        "progress": progress,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO student_experience_update (
                          id, tenant_id, student_id, publication_id, requirement_id,
                          source_key, kind, title, description, status, version,
                          created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :student_id, :publication_id, :requirement_id,
                          :source_key, :kind, :title, :description, 'pending', 1,
                          NOW(), NOW()
                        ) ON CONFLICT (
                          tenant_id, student_id, publication_id, source_key
                        ) DO NOTHING
                        """
                    ),
                    {
                        "id": self._uuid_factory(),
                        "tenant_id": _uuid(auth.tenant_id),
                        "student_id": journey["student_id"],
                        "publication_id": publication_id,
                        "requirement_id": requirement_id,
                        "source_key": f"{task['kind']}:{task['code']}",
                        "kind": task["kind"],
                        "title": task["title"],
                        "description": task["description"],
                    },
                )
                await connection.execute(
                    text(
                        """
                        UPDATE enrollment_journey
                        SET status='in_progress', version=version+1, updated_at=NOW()
                        WHERE id=:journey_id AND tenant_id=:tenant_id
                          AND status IN ('ready_for_review', 'submitted', 'on_hold', 'completed')
                        """
                    ),
                    {
                        "journey_id": journey["id"],
                        "tenant_id": _uuid(auth.tenant_id),
                    },
                )
                inserted += 1
        return inserted

    async def _materialize_campus_life(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document: Mapping[str, Any],
        previous_document: Mapping[str, Any],
    ) -> int:
        previous_titles = {
            str(item.get("id")): str(item.get("title"))
            for item in _mapping_list(previous_document.get("events"))
            if item.get("id") and item.get("title")
        }
        count = 0
        for event in campus_events(document):
            old_title = previous_titles.get(str(event["sourceId"]), str(event["title"]))
            existing = await connection.execute(
                text(
                    """
                    SELECT id FROM campus_event
                    WHERE tenant_id=:tenant_id AND title IN (:old_title, :title)
                    ORDER BY CASE WHEN title=:old_title THEN 0 ELSE 1 END, id
                    LIMIT 1 FOR UPDATE
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "old_title": old_title,
                    "title": event["title"],
                },
            )
            event_id = existing.scalar_one_or_none()
            if event_id is None:
                event_id = self._uuid_factory()
                await connection.execute(
                    text(
                        """
                        INSERT INTO campus_event (
                          id, tenant_id, title, description, starts_at, ends_at,
                          location, category, featured, accent, active, source_label,
                          source_status, registration_url, visual_theme,
                          created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :title, :description, :starts_at, :ends_at,
                          :location, :category, :featured, :accent, true,
                          'Staff managed campus life', 'tenant_authored',
                          :registration_url, :visual_theme, NOW(), NOW()
                        )
                        """
                    ),
                    {"id": event_id, "tenant_id": _uuid(auth.tenant_id), **event},
                )
            else:
                await connection.execute(
                    text(
                        """
                        UPDATE campus_event SET
                          title=:title, description=:description, starts_at=:starts_at,
                          ends_at=:ends_at, location=:location, category=:category,
                          featured=:featured, accent=:accent, active=true,
                          source_label='Staff managed campus life',
                          source_status='tenant_authored', registration_url=:registration_url,
                          visual_theme=:visual_theme, updated_at=NOW()
                        WHERE id=:id AND tenant_id=:tenant_id
                        """
                    ),
                    {"id": event_id, "tenant_id": _uuid(auth.tenant_id), **event},
                )
            count += 1
        return count

    async def _materialize_academics(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document: Mapping[str, Any],
    ) -> int:
        catalog_result = await connection.execute(
            text(
                """
                SELECT id FROM course_catalog_version
                WHERE tenant_id=:tenant_id AND status='active'
                ORDER BY effective_from DESC, id LIMIT 1 FOR UPDATE
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id)},
        )
        catalog_id = catalog_result.scalar_one_or_none()
        if catalog_id is None:
            catalog_id = self._uuid_factory()
            catalog_code = str(document.get("catalog_version") or "staff-managed.v1")[:80]
            await connection.execute(
                text(
                    """
                    INSERT INTO course_catalog_version (
                      id, tenant_id, code, effective_from, status,
                      source_label, source_status, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :code, :effective_from, 'active',
                      'Staff managed catalog', 'tenant_authored', NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": catalog_id,
                    "tenant_id": _uuid(auth.tenant_id),
                    "code": catalog_code,
                    "effective_from": self._clock().date(),
                },
            )

        course_ids: dict[str, object] = {}
        courses = academic_courses(document)
        for course in courses:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO catalog_course (
                      id, tenant_id, catalog_version_id, code, title, description,
                      credits, level, active, availability_label, instructor_names,
                      meeting_pattern, resources, source_url, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :catalog_id, :code, :title, :description,
                      :credits, :level, true, :availability_label,
                      CAST(:instructor_names AS text[]), :meeting_pattern,
                      CAST(:resources AS jsonb), :source_url, NOW(), NOW()
                    ) ON CONFLICT (catalog_version_id, code) DO UPDATE SET
                      title=EXCLUDED.title, description=EXCLUDED.description,
                      credits=EXCLUDED.credits, level=EXCLUDED.level, active=true,
                      availability_label=EXCLUDED.availability_label,
                      instructor_names=EXCLUDED.instructor_names,
                      meeting_pattern=EXCLUDED.meeting_pattern,
                      resources=EXCLUDED.resources, source_url=EXCLUDED.source_url,
                      updated_at=NOW()
                    RETURNING id
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": _uuid(auth.tenant_id),
                    "catalog_id": catalog_id,
                    "code": course["code"],
                    "title": course["title"],
                    "description": course["description"],
                    "credits": course["credits"],
                    "level": course["level"],
                    "availability_label": course["availabilityLabel"],
                    "instructor_names": course["instructorNames"],
                    "meeting_pattern": course["meetingPattern"],
                    "resources": _json(course["resources"]),
                    "source_url": course["sourceUrl"],
                },
            )
            course_ids[str(course["code"])] = result.scalar_one()

        for course in courses:
            course_id = course_ids[str(course["code"])]
            await connection.execute(
                text(
                    """
                    DELETE FROM course_prerequisite
                    WHERE tenant_id=:tenant_id AND catalog_version_id=:catalog_id
                      AND course_id=:course_id
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "catalog_id": catalog_id,
                    "course_id": course_id,
                },
            )
            for prerequisite in cast(list[JsonDict], course["prerequisites"]):
                prerequisite_id = course_ids.get(str(prerequisite["courseCode"]))
                if prerequisite_id is None:
                    lookup = await connection.execute(
                        text(
                            """
                            SELECT id FROM catalog_course
                            WHERE tenant_id=:tenant_id AND catalog_version_id=:catalog_id
                              AND code=:code AND active=true
                            LIMIT 1
                            """
                        ),
                        {
                            "tenant_id": _uuid(auth.tenant_id),
                            "catalog_id": catalog_id,
                            "code": prerequisite["courseCode"],
                        },
                    )
                    prerequisite_id = lookup.scalar_one_or_none()
                if prerequisite_id is None:
                    raise BadRequestError(
                        "MANAGED_ACADEMIC_PREREQUISITE_NOT_FOUND",
                        f"Prerequisite {prerequisite['courseCode']} is not in the active catalog",
                    )
                await connection.execute(
                    text(
                        """
                        INSERT INTO course_prerequisite (
                          tenant_id, catalog_version_id, course_id,
                          prerequisite_course_id, minimum_grade, created_at
                        ) VALUES (
                          :tenant_id, :catalog_id, :course_id,
                          :prerequisite_id, :minimum_grade, NOW()
                        ) ON CONFLICT (course_id, prerequisite_course_id)
                        DO UPDATE SET minimum_grade=EXCLUDED.minimum_grade
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "catalog_id": catalog_id,
                        "course_id": course_id,
                        "prerequisite_id": prerequisite_id,
                        "minimum_grade": prerequisite.get("minimumGrade"),
                    },
                )
        await connection.execute(
            text(
                """
                UPDATE course_catalog_version SET updated_at=NOW()
                WHERE id=:id AND tenant_id=:tenant_id
                """
            ),
            {"id": catalog_id, "tenant_id": _uuid(auth.tenant_id)},
        )
        return len(courses)

    async def _requirement_slug(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        requirement_id: object,
    ) -> str | None:
        if requirement_id is None:
            return None
        result = await connection.execute(
            text(
                """
                SELECT definition.code
                FROM student_requirement requirement
                JOIN requirement_definition_version definition
                  ON definition.id=requirement.requirement_definition_version_id
                 AND definition.tenant_id=requirement.tenant_id
                WHERE requirement.id=:id AND requirement.tenant_id=:tenant_id
                """
            ),
            {"id": requirement_id, "tenant_id": _uuid(auth.tenant_id)},
        )
        code = result.scalar_one_or_none()
        return _slug(str(code)) if code is not None else None

    async def _insert_publication_audit(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        publication_id: UUID,
        kind: ManagedConfigurationKind,
        version: int,
        record_count: int,
        materialized: int,
        request_id: str,
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO audit_event (
                  id, tenant_id, actor_type, actor_id, student_id, action,
                  resource_type, resource_id, authorization_basis, request_id,
                  correlation_id, metadata, occurred_at, created_at
                ) VALUES (
                  :id, :tenant_id, 'staff', :actor_id, NULL,
                  'staff.configuration_published', 'staff_managed_configuration',
                  :resource_id, 'staff_tenant_configuration', :request_id,
                  :request_id, CAST(:metadata AS jsonb), NOW(), NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "actor_id": _uuid(auth.actor_id),
                "resource_id": publication_id,
                "request_id": request_id,
                "metadata": _json(
                    {
                        "kind": kind,
                        "version": version,
                        "recordCount": record_count,
                        "materializedCount": materialized,
                    }
                ),
            },
        )

    async def _insert_publication_outbox(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        publication_id: UUID,
        kind: ManagedConfigurationKind,
        version: int,
        record_count: int,
        materialized: int,
        request_id: str,
        occurred_at: datetime,
    ) -> None:
        event_id = self._uuid_factory()
        payload = {
            "eventId": str(event_id),
            "eventName": "staff.configuration_published.v1",
            "occurredAt": _iso(occurred_at),
            "tenantId": auth.tenant_id,
            "aggregateType": "staff_managed_configuration",
            "aggregateId": str(publication_id),
            "aggregateVersion": version,
            "actor": {"type": "staff", "id": auth.actor_id},
            "correlationId": request_id,
            "causationId": str(event_id),
            "data": {
                "publicationId": str(publication_id),
                "kind": kind,
                "recordCount": record_count,
                "materializedCount": materialized,
            },
        }
        await connection.execute(
            text(
                """
                INSERT INTO outbox_event (
                  id, tenant_id, event_name, aggregate_type, aggregate_id,
                  aggregate_version, occurred_at, actor_type, actor_id,
                  correlation_id, causation_id, payload, created_at
                ) VALUES (
                  :id, :tenant_id, 'staff.configuration_published.v1',
                  'staff_managed_configuration', :aggregate_id, :aggregate_version,
                  :occurred_at, 'staff', :actor_id, :request_id, :causation_id,
                  CAST(:payload AS jsonb), :occurred_at
                )
                """
            ),
            {
                "id": event_id,
                "tenant_id": _uuid(auth.tenant_id),
                "aggregate_id": publication_id,
                "aggregate_version": version,
                "occurred_at": occurred_at,
                "actor_id": _uuid(auth.actor_id),
                "request_id": request_id,
                "causation_id": str(event_id),
                "payload": _json(payload),
            },
        )

    async def _insert_student_decision_audit(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        update_id: str,
        action: str,
        version: int,
        request_id: str,
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO audit_event (
                  id, tenant_id, actor_type, actor_id, student_id, action,
                  resource_type, resource_id, authorization_basis, request_id,
                  correlation_id, metadata, occurred_at, created_at
                ) VALUES (
                  :id, :tenant_id, 'student', :actor_id, :student_id,
                  'student.experience_update_decided', 'student_experience_update',
                  :resource_id, 'student_self_service', :request_id, :request_id,
                  CAST(:metadata AS jsonb), NOW(), NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": _uuid(auth.tenant_id),
                "actor_id": _uuid(auth.actor_id),
                "student_id": _uuid(auth.student_id),
                "resource_id": _uuid(update_id),
                "request_id": request_id,
                "metadata": _json({"action": action, "version": version}),
            },
        )

    @staticmethod
    def _require_staff(auth: AuthContext) -> None:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_ACCESS_REQUIRED", "Staff access is required")

    @staticmethod
    def _require_student(auth: AuthContext) -> None:
        if auth.actor_type != "student":
            raise ApiError(403, "STUDENT_ACCESS_REQUIRED", "Student access is required")


def parse_managed_configuration(
    kind: str,
    yaml_text: str,
    *,
    tenant_slug: str | None,
) -> JsonDict:
    """Parse one bounded managed document into JSON-safe canonical data."""

    normalized = _configuration_kind(kind)
    if not yaml_text.strip() or len(yaml_text) > 250_000:
        raise BadRequestError("INVALID_MANAGED_YAML", "The managed YAML is empty or too large")
    try:
        value = yaml.safe_load(yaml_text)
    except yaml.YAMLError as error:
        raise BadRequestError("INVALID_MANAGED_YAML", "The managed YAML is not valid") from error
    if not isinstance(value, dict):
        raise BadRequestError("INVALID_MANAGED_YAML", "The managed YAML must be an object")
    document = cast(JsonDict, _json_safe(value))
    if document.get("configuration") != _CONFIGURATION_NAMES[normalized]:
        raise BadRequestError(
            "MANAGED_CONFIGURATION_KIND_MISMATCH",
            "The YAML configuration kind does not match this resource",
        )
    declared_tenant = document.get("tenant")
    if tenant_slug and declared_tenant and str(declared_tenant) != tenant_slug:
        raise BadRequestError(
            "MANAGED_CONFIGURATION_TENANT_MISMATCH",
            "The YAML tenant does not match the authenticated tenant",
        )
    collection_name = _COLLECTION_NAMES[normalized]
    if not isinstance(document.get(collection_name), list):
        raise BadRequestError(
            "INVALID_MANAGED_YAML",
            f"The {collection_name} collection is required",
        )
    if normalized == "journeys":
        materialized_journey_tasks(document)
    elif normalized == "campus_life":
        campus_events(document)
    else:
        academic_courses(document)
    return document


def materialized_journey_tasks(document: Mapping[str, Any]) -> list[JsonDict]:
    tasks: list[JsonDict] = []
    seen: set[str] = set()
    skipped_core_task_ids: set[str] = set()
    display_order = 0
    for flow in _mapping_list(document.get("flows")):
        if flow.get("status", "published") != "published":
            continue
        flow_kind = str(flow.get("kind") or "")
        if flow_kind not in {"onboarding", "enrollment"}:
            raise BadRequestError(
                "INVALID_MANAGED_JOURNEY",
                "Journey flow kind must be onboarding or enrollment",
            )
        for raw_task in _mapping_list(flow.get("tasks")):
            code = str(raw_task.get("id") or "")
            student_step = raw_task.get("student_step")
            if flow_kind == "onboarding" and student_step in _CORE_ONBOARDING_STEPS:
                if code:
                    skipped_core_task_ids.add(code)
                continue
            if not _TASK_CODE.fullmatch(code):
                raise BadRequestError(
                    "INVALID_MANAGED_JOURNEY_TASK",
                    "Every materialized journey task needs a stable snake_case id",
                )
            if code in seen:
                raise BadRequestError(
                    "DUPLICATE_MANAGED_JOURNEY_TASK",
                    f"Journey task {code} is duplicated",
                )
            seen.add(code)
            display_order += 10
            task_type = str(raw_task.get("task_type") or "information")
            submission_type = str(
                raw_task.get("submission_type") or _SUBMISSION_TYPES.get(task_type, "none")
            )
            if submission_type not in {"none", "form", "document", "payment", "appointment"}:
                raise BadRequestError(
                    "INVALID_MANAGED_JOURNEY_TASK",
                    f"Journey task {code} has an invalid submission type",
                )
            title = _required_string(raw_task.get("title"), "journey task title", maximum=180)
            description = _required_string(
                raw_task.get("description"),
                "journey task description",
                maximum=4_000,
            )
            owner = _required_string(
                raw_task.get("owner") or "Enrollment Services",
                "journey task owner",
                maximum=180,
            )
            raw_dependencies = raw_task.get("depends_on", [])
            if not isinstance(raw_dependencies, list) or not all(
                isinstance(item, str) for item in raw_dependencies
            ):
                raise BadRequestError(
                    "INVALID_MANAGED_JOURNEY_TASK",
                    f"Journey task {code} has invalid dependencies",
                )
            due_offset = raw_task.get("due_days_after_acceptance")
            if due_offset is not None:
                due_offset = _bounded_integer(due_offset, "due days", minimum=0, maximum=3650)
            initial_progress = _bounded_integer(
                raw_task.get("initial_progress_percent", 0),
                "initial progress",
                minimum=0,
                maximum=100,
            )
            points = _bounded_integer(
                raw_task.get("points", 0),
                "journey task points",
                minimum=0,
                maximum=100_000,
            )
            tasks.append(
                {
                    "code": code,
                    "kind": flow_kind,
                    "title": title,
                    "description": description,
                    "owner": owner,
                    "required": bool(raw_task.get("required", False)),
                    "displayOrder": display_order,
                    "dependsOn": list(raw_dependencies),
                    "dueOffsetDays": due_offset,
                    "initialProgressPercent": initial_progress,
                    "submissionType": submission_type,
                    "points": points,
                }
            )
    available = {str(item["code"]) for item in tasks}
    for task in tasks:
        dependencies: list[str] = []
        for dependency in cast(list[str], task["dependsOn"]):
            if dependency in available:
                dependencies.append(dependency)
            elif dependency not in _CORE_ONBOARDING_STEPS | skipped_core_task_ids:
                raise BadRequestError(
                    "MANAGED_JOURNEY_DEPENDENCY_NOT_FOUND",
                    f"Journey task {task['code']} depends on unknown task {dependency}",
                )
        task["dependsOn"] = dependencies
    return tasks


def campus_events(document: Mapping[str, Any]) -> list[JsonDict]:
    events: list[JsonDict] = []
    seen: set[str] = set()
    for raw in _mapping_list(document.get("events")):
        source_id = _required_string(raw.get("id"), "campus event id", maximum=160)
        if source_id in seen:
            raise BadRequestError(
                "DUPLICATE_CAMPUS_EVENT",
                f"Campus event {source_id} is duplicated",
            )
        seen.add(source_id)
        category = str(raw.get("category") or "social")
        accent = str(raw.get("accent") or "blue")
        theme = str(raw.get("visual_theme") or "community")
        if category not in _CAMPUS_CATEGORIES or accent not in _CAMPUS_ACCENTS:
            raise BadRequestError("INVALID_CAMPUS_EVENT", f"Campus event {source_id} is invalid")
        if theme not in _CAMPUS_THEMES:
            raise BadRequestError(
                "INVALID_CAMPUS_EVENT",
                f"Campus event {source_id} has an invalid theme",
            )
        starts_at = _datetime(raw.get("starts_at"), "campus event start")
        ends_at = _datetime(raw.get("ends_at"), "campus event end")
        if ends_at <= starts_at:
            raise BadRequestError("INVALID_CAMPUS_EVENT", "Campus event end must follow its start")
        events.append(
            {
                "sourceId": source_id,
                "title": _required_string(raw.get("title"), "campus event title", maximum=180),
                "description": _required_string(
                    raw.get("description"), "campus event description", maximum=4_000
                ),
                "starts_at": starts_at,
                "ends_at": ends_at,
                "location": _required_string(
                    raw.get("location"), "campus event location", maximum=180
                ),
                "category": category,
                "featured": bool(raw.get("featured", False)),
                "accent": accent,
                "registration_url": _optional_string(raw.get("registration_url"), maximum=1_000),
                "visual_theme": theme,
            }
        )
    return events


def academic_courses(document: Mapping[str, Any]) -> list[JsonDict]:
    courses: list[JsonDict] = []
    seen: set[str] = set()
    for raw in _mapping_list(document.get("courses")):
        code = _required_string(raw.get("code"), "course code", maximum=32).upper()
        if code in seen:
            raise BadRequestError("DUPLICATE_CATALOG_COURSE", f"Course {code} is duplicated")
        seen.add(code)
        instructors = raw.get("instructor_names", [])
        prerequisites = raw.get("prerequisites", [])
        resources = raw.get("resources", [])
        if not isinstance(instructors, list) or not all(
            isinstance(item, str) for item in instructors
        ):
            raise BadRequestError(
                "INVALID_CATALOG_COURSE",
                f"Course {code} has invalid instructors",
            )
        if not isinstance(prerequisites, list) or not all(
            isinstance(item, dict) for item in prerequisites
        ):
            raise BadRequestError(
                "INVALID_CATALOG_COURSE",
                f"Course {code} has invalid prerequisites",
            )
        if not isinstance(resources, list):
            raise BadRequestError("INVALID_CATALOG_COURSE", f"Course {code} has invalid resources")
        mapped_prerequisites: list[JsonDict] = []
        for item in cast(list[dict[str, object]], prerequisites):
            mapped_prerequisites.append(
                {
                    "courseCode": _required_string(
                        item.get("course_code"), "prerequisite course code", maximum=32
                    ).upper(),
                    "minimumGrade": _optional_string(item.get("minimum_grade"), maximum=8),
                }
            )
        courses.append(
            {
                "code": code,
                "title": _required_string(raw.get("title"), "course title", maximum=180),
                "description": _required_string(
                    raw.get("description"), "course description", maximum=4_000
                ),
                "credits": _bounded_float(
                    raw.get("credits", 0),
                    "course credits",
                    minimum=0,
                    maximum=20,
                    exclusive_minimum=True,
                ),
                "level": _bounded_integer(raw.get("level"), "course level", minimum=0, maximum=900),
                "availabilityLabel": _optional_string(raw.get("availability_label"), maximum=120),
                "instructorNames": list(instructors),
                "meetingPattern": _optional_string(raw.get("meeting_pattern"), maximum=240),
                "prerequisites": mapped_prerequisites,
                "resources": copy.deepcopy(resources),
                "sourceUrl": _optional_string(raw.get("source_url"), maximum=1_000),
            }
        )
    return courses


def _configuration_kind(value: str) -> ManagedConfigurationKind:
    if value not in _CONFIGURATION_NAMES:
        raise BadRequestError("INVALID_STAFF_CONFIGURATION", "Choose a managed configuration")
    return value


def _validated_fallback(
    kind: ManagedConfigurationKind,
    fallback: Mapping[str, Any],
) -> JsonDict:
    yaml_text = str(fallback.get("yaml") or "")
    parse_managed_configuration(kind, yaml_text, tenant_slug=None)
    version = _positive_integer(fallback.get("version", 1), "fallback version")
    return {
        "kind": kind,
        "fileName": str(fallback.get("fileName") or _CONFIGURATION_FILES[kind]),
        "version": version,
        "yaml": yaml_text,
        "recordCount": int(fallback.get("recordCount") or 0),
        "updatedAt": str(fallback.get("updatedAt") or "1970-01-01T00:00:00Z"),
        "updatedBy": str(fallback.get("updatedBy") or "Initial tenant configuration"),
        **(
            {"changeSummary": str(fallback["changeSummary"])}
            if fallback.get("changeSummary")
            else {}
        ),
    }


def _public_configuration(row: Mapping[str, Any]) -> JsonDict:
    kind = _configuration_kind(str(row["kind"]))
    return {
        "id": str(row["id"]),
        "kind": kind,
        "fileName": _CONFIGURATION_FILES[kind],
        "version": int(row["version"]),
        "yaml": str(row["yaml"]),
        "recordCount": int(row["record_count"]),
        "updatedAt": _iso(cast(datetime, row["published_at"])),
        "updatedBy": str(row["created_by"]),
        **({"changeSummary": str(row["change_summary"])} if row.get("change_summary") else {}),
    }


def _map_student_update(row: Mapping[str, Any]) -> JsonDict:
    code = row.get("requirement_code")
    return {
        "id": str(row["id"]),
        "kind": str(row["kind"]),
        "title": str(row["title"]),
        "description": str(row["description"]),
        "requirementSlug": _slug(str(code)) if code is not None else None,
        "status": str(row["status"]),
        "version": int(row["version"]),
        "createdAt": _iso(cast(datetime, row["created_at"])),
    }


def _record_count(kind: ManagedConfigurationKind, document: Mapping[str, Any]) -> int:
    if kind == "journeys":
        return sum(
            len(_mapping_list(flow.get("tasks"))) for flow in _mapping_list(document["flows"])
        )
    return len(_mapping_list(document[_COLLECTION_NAMES[kind]]))


def _mapping(value: object) -> JsonDict:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _mapping_list(value: object) -> list[JsonDict]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


def _required_string(value: object, label: str, *, maximum: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise BadRequestError("INVALID_MANAGED_CONFIGURATION", f"The {label} is invalid")
    return result


def _optional_string(value: object, *, maximum: int) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    if not result:
        return None
    if len(result) > maximum:
        raise BadRequestError("INVALID_MANAGED_CONFIGURATION", "A managed text value is too long")
    return result


def _positive_integer(value: object, label: str) -> int:
    return _bounded_integer(value, label, minimum=1, maximum=2_147_483_647)


def _bounded_integer(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise BadRequestError("INVALID_MANAGED_CONFIGURATION", f"The {label} is invalid")
    try:
        result = int(cast(Any, value))
    except (TypeError, ValueError) as error:
        raise BadRequestError("INVALID_MANAGED_CONFIGURATION", f"The {label} is invalid") from error
    if result < minimum or result > maximum:
        raise BadRequestError("INVALID_MANAGED_CONFIGURATION", f"The {label} is invalid")
    return result


def _bounded_float(
    value: object,
    label: str,
    *,
    minimum: float,
    maximum: float,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool):
        raise BadRequestError("INVALID_MANAGED_CONFIGURATION", f"The {label} is invalid")
    try:
        result = float(cast(Any, value))
    except (TypeError, ValueError) as error:
        raise BadRequestError(
            "INVALID_MANAGED_CONFIGURATION",
            f"The {label} is invalid",
        ) from error
    below_minimum = result <= minimum if exclusive_minimum else result < minimum
    if result != result or below_minimum or result > maximum:
        raise BadRequestError("INVALID_MANAGED_CONFIGURATION", f"The {label} is invalid")
    return result


def _datetime(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as error:
            raise BadRequestError(
                "INVALID_MANAGED_CONFIGURATION",
                f"The {label} is invalid",
            ) from error
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _uuid(value: object) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise BadRequestError("INVALID_IDENTIFIER", "The identifier is invalid") from error


def _slug(code: str) -> str:
    return code.lower().replace("_", "-")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
