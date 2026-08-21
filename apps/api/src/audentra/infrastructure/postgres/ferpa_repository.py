"""Canonical FERPA aggregate and scoped delegate-link persistence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import PORTAL_SCOPES, AuthContext
from audentra.core.delegate_authorization import (
    require_delegate_scope,
    require_student_ferpa_control,
)
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError
from audentra.infrastructure.postgres.journey_routing import reconcile_student_journey_routes

if TYPE_CHECKING:
    from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository

JsonDict = dict[str, Any]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _mapping(value: object) -> JsonDict:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        parsed = json.loads(value)
        return _mapping(parsed)
    return {}


def _list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _canonical_delegate_scopes(values: Sequence[object]) -> list[str]:
    """Map the retired standalone onboarding grant to My Enrollment."""

    scopes: list[str] = []
    for raw_scope in values:
        scope = "enrollment" if str(raw_scope) == "onboarding" else str(raw_scope)
        if scope not in scopes:
            scopes.append(scope)
    return scopes


def _uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise BadRequestError("VALIDATION_ERROR", "A FERPA identifier is invalid") from error


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _iso(value: object) -> str:
    return _timestamp(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _nullable_iso(value: object) -> str | None:
    return None if value is None else _iso(value)


def _positive_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BadRequestError("VALIDATION_ERROR", "expectedVersion must be a positive integer")
    return value


def _completion_request_hash(auth: AuthContext, payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        _json(
            {
                "tenantId": auth.tenant_id,
                "studentId": auth.student_id,
                "payload": payload,
            }
        ).encode("utf-8")
    ).hexdigest()


async def reconcile_completed_ferpa_requirements(
    connection: AsyncConnection,
    *,
    tenant_id: object,
    student_id: object | None = None,
) -> int:
    """Carry an already-signed canonical authorization onto a replacement task.

    Publishing a replacement FERPA definition must not make the journey say the
    student has unsigned work while the one-per-student authorization still says
    completed. The original immutable evidence and delegate access are retained;
    only the newly-bound journey requirement is reconciled to that decision.
    """

    updated = await connection.execute(
        text(
            """
            UPDATE student_requirement requirement
            SET status='completed', progress_percent=100,
                version=requirement.version+1, updated_at=NOW()
            FROM ferpa_authorization ferpa_auth
            WHERE ferpa_auth.tenant_id=:tenant_id
              AND ferpa_auth.status='completed'
              AND requirement.tenant_id=ferpa_auth.tenant_id
              AND requirement.id=ferpa_auth.requirement_id
              AND requirement.retired_at IS NULL
              AND requirement.status<>'completed'
              AND (
                CAST(:student_id AS uuid) IS NULL
                OR ferpa_auth.student_id=CAST(:student_id AS uuid)
              )
            RETURNING requirement.id, requirement.journey_id,
                      ferpa_auth.student_id
            """
        ),
        {"tenant_id": tenant_id, "student_id": student_id},
    )
    rows = [dict(row) for row in updated.mappings().all()]
    for row in rows:
        await reconcile_student_journey_routes(
            connection,
            tenant_id=tenant_id,
            student_id=row["student_id"],
            journey_id=row["journey_id"],
        )
        await connection.execute(
            text(
                """
                UPDATE student_experience_update
                SET status='acknowledged', acknowledged_at=NOW(),
                    version=version+1, updated_at=NOW()
                WHERE tenant_id=:tenant_id AND student_id=:student_id
                  AND requirement_id=:requirement_id
                  AND status IN ('pending','deferred')
                """
            ),
            {
                "tenant_id": tenant_id,
                "student_id": row["student_id"],
                "requirement_id": row["id"],
            },
        )
        await connection.execute(
            text(
                """
                UPDATE enrollment_journey journey
                SET status='completed', version=journey.version+1, updated_at=NOW()
                WHERE journey.id=:journey_id AND journey.tenant_id=:tenant_id
                  AND journey.status NOT IN ('completed','cancelled')
                  AND (
                    EXISTS (
                      SELECT 1 FROM journey_definition_version definition
                      WHERE definition.id=journey.journey_definition_version_id
                        AND definition.tenant_id=journey.tenant_id
                        AND NOT definition.onboarding_required
                    )
                    OR EXISTS (
                      SELECT 1 FROM student_onboarding onboarding
                      WHERE onboarding.tenant_id=journey.tenant_id
                        AND onboarding.student_id=journey.student_id
                        AND onboarding.status='completed'
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM student_requirement outstanding
                    WHERE outstanding.tenant_id=journey.tenant_id
                      AND outstanding.journey_id=journey.id
                      AND outstanding.retired_at IS NULL
                      AND outstanding.status NOT IN (
                        'not_applicable','completed','waived','expired'
                      )
                  )
                """
            ),
            {"journey_id": row["journey_id"], "tenant_id": tenant_id},
        )
    return len(rows)


def _normalize_access(payload: Mapping[str, Any], *, allow_ids: bool) -> tuple[str, list[JsonDict]]:
    decision = payload.get("accessDecision")
    if decision not in {"grant", "no_access"}:
        raise BadRequestError(
            "INVALID_FERPA_ACCESS_DECISION",
            "Choose whether to grant parent or guardian portal access",
        )
    raw_delegates = payload.get("delegates", [])
    if not isinstance(raw_delegates, Sequence) or isinstance(raw_delegates, (str, bytes)):
        raise BadRequestError("INVALID_FERPA_DELEGATES", "delegates must be a list")
    if len(raw_delegates) > 4:
        raise BadRequestError("FERPA_DELEGATE_LIMIT", "Add no more than four delegates")
    if decision == "grant" and not raw_delegates:
        raise BadRequestError(
            "FERPA_DELEGATE_REQUIRED",
            "Add at least one parent or guardian when granting access",
        )
    if decision == "no_access" and raw_delegates:
        raise BadRequestError(
            "FERPA_NO_ACCESS_DELEGATES",
            "Remove delegates before choosing not to grant access",
        )
    normalized: list[JsonDict] = []
    seen_emails: set[str] = set()
    valid_scopes = set(PORTAL_SCOPES)
    for index, raw in enumerate(raw_delegates):
        if not isinstance(raw, Mapping):
            raise BadRequestError("INVALID_FERPA_DELEGATES", "Every delegate must be an object")
        full_name = str(raw.get("fullName") or "").strip()
        relationship = str(raw.get("relationship") or "")
        email = str(raw.get("email") or "").strip().lower()
        raw_scopes = raw.get("scopes")
        if not full_name or len(full_name) > 160:
            raise BadRequestError("INVALID_FERPA_DELEGATE_NAME", "Enter a valid delegate name")
        if relationship not in {"parent", "guardian", "partner", "sponsor", "relative", "other"}:
            raise BadRequestError(
                "INVALID_FERPA_DELEGATE_RELATIONSHIP",
                "Choose a valid delegate relationship",
            )
        if len(email) > 254 or _EMAIL.fullmatch(email) is None or email in seen_emails:
            raise BadRequestError(
                "INVALID_FERPA_DELEGATE_EMAIL",
                "Every delegate needs a unique valid email address",
            )
        if (
            not isinstance(raw_scopes, Sequence)
            or isinstance(raw_scopes, (str, bytes))
            or not raw_scopes
            or len(raw_scopes) > len(PORTAL_SCOPES)
        ):
            raise BadRequestError(
                "INVALID_FERPA_DELEGATE_SCOPES",
                "Choose one or more portal pages for every delegate",
            )
        scopes = [str(scope) for scope in raw_scopes]
        if len(scopes) != len(set(scopes)) or any(scope not in valid_scopes for scope in scopes):
            raise BadRequestError(
                "INVALID_FERPA_DELEGATE_SCOPES",
                "A delegate contains an invalid or duplicate portal scope",
            )
        delegate_id = raw.get("id")
        if delegate_id is not None and not allow_ids:
            raise BadRequestError(
                "INVALID_FERPA_DELEGATE_ID",
                "New FERPA delegates must not provide an identifier",
            )
        seen_emails.add(email)
        normalized.append(
            {
                **({"id": str(_uuid(delegate_id))} if delegate_id is not None else {}),
                "fullName": full_name,
                "relationship": relationship,
                "email": email,
                "scopes": scopes,
                "displayOrder": index,
            }
        )
    return str(decision), normalized


class PostgresFerpaRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        portal: PostgresPortalRepository | None = None,
    ) -> None:
        self.engine = engine
        self.portal = portal

    async def get_current(self, auth: AuthContext) -> JsonDict | None:
        if auth.actor_type not in {"student", "delegate"}:
            raise ApiError(403, "FERPA_STUDENT_CONTROL_REQUIRED", "FERPA is student-controlled")
        async with self.engine.begin() as connection:
            await self._materialize_current(connection, auth)
            row = await self._current_row(connection, auth, for_update=False)
            if row is None:
                return None
            return await self._map_authorization(connection, auth, row)

    async def preflight_completion(
        self,
        auth: AuthContext,
        identifier: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> JsonDict:
        """Validate a completion before any immutable document bytes are created."""

        require_student_ferpa_control(auth)
        expected_version = _positive_version(payload.get("expectedVersion"))
        _normalize_access(payload, allow_ids=True)
        operation = f"ferpa.complete:{identifier}"
        request_hash = _completion_request_hash(auth, payload)
        async with self.engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"{auth.tenant_id}:{auth.actor_id}:{operation}:{idempotency_key}"},
            )
            existing = await connection.execute(
                text(
                    """
                    SELECT request_hash, response_body FROM idempotency_record
                    WHERE tenant_id=:tenant_id AND actor_id=:actor_id
                      AND operation=:operation AND idempotency_key=:key
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "actor_id": _uuid(auth.actor_id),
                    "operation": operation,
                    "key": idempotency_key,
                },
            )
            cached = existing.mappings().first()
            if cached is not None:
                if str(cached["request_hash"]) != request_hash:
                    raise ConflictError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "This idempotency key was already used for a different FERPA request",
                    )
                return {"cachedResponse": _mapping(cached["response_body"])}
            await self._materialize_current(connection, auth)
            row = await self._current_row(
                connection,
                auth,
                identifier=identifier,
                for_update=True,
            )
            if row is None:
                raise NotFoundError("FERPA_REQUIREMENT_NOT_FOUND", "The FERPA task was not found")
            self._validate_completion_row(row, expected_version)
            signed_evidence = await connection.scalar(
                text(
                    """
                    SELECT 1 FROM ferpa_signed_document
                    WHERE authorization_id=:authorization_id AND tenant_id=:tenant_id
                    """
                ),
                {
                    "authorization_id": row["authorization_id"],
                    "tenant_id": _uuid(auth.tenant_id),
                },
            )
            reservation: JsonDict | None = None
            if signed_evidence is None:
                if not isinstance(payload.get("signature"), Mapping):
                    raise BadRequestError(
                        "FERPA_SIGNATURE_REQUIRED",
                        "The FERPA document must be signed before completion",
                    )
                reservation = await self._reserve_signing_evidence(
                    connection,
                    auth,
                    authorization_id=row["authorization_id"],
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
            return {
                "authorization": await self._map_authorization(connection, auth, row),
                "hasSignedEvidence": signed_evidence is not None,
                **({"signingReservation": reservation} if reservation is not None else {}),
            }

    async def claim_signing_reservation(
        self,
        auth: AuthContext,
        reservation_id: str,
    ) -> JsonDict:
        require_student_ferpa_control(auth)
        async with self.engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"ferpa-signing:{reservation_id}"},
            )
            result = await connection.execute(
                text(
                    """
                    SELECT id, authorization_id, request_hash, document_id,
                           signed_at, storage_key, status, sha256, size_bytes,
                           title, file_name, signer_name, signature_method,
                           lease_expires_at
                    FROM ferpa_signing_reservation
                    WHERE id=:id AND tenant_id=:tenant_id
                      AND student_id=:student_id AND actor_id=:actor_id
                    FOR UPDATE
                    """
                ),
                {
                    "id": _uuid(reservation_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                    "actor_id": _uuid(auth.actor_id),
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError(
                    "FERPA_SIGNING_RESERVATION_NOT_FOUND",
                    "The FERPA signing attempt was not found",
                )
            if row["status"] == "stored":
                return {**self._signing_reservation_projection(dict(row)), "claimed": False}
            if row["status"] == "finalized":
                raise ConflictError(
                    "FERPA_SIGNING_ALREADY_FINALIZED",
                    "This FERPA signing attempt is already complete",
                )
            if row["status"] == "failed":
                raise ConflictError(
                    "FERPA_SIGNING_RESERVATION_SUPERSEDED",
                    "This FERPA signing attempt was superseded; start again",
                )
            if (
                row["status"] == "generating"
                and row["lease_expires_at"] is not None
                and cast(datetime, row["lease_expires_at"]) > datetime.now(UTC)
            ):
                raise ConflictError(
                    "FERPA_SIGNING_IN_PROGRESS",
                    "This FERPA signature is already being prepared; retry shortly",
                )
            claimed = await connection.execute(
                text(
                    """
                    UPDATE ferpa_signing_reservation
                    SET status='generating', lease_expires_at=NOW()+INTERVAL '2 minutes',
                        updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id
                      AND (
                        status='reserved'
                        OR (
                          status='generating'
                          AND (lease_expires_at IS NULL OR lease_expires_at<=NOW())
                        )
                      )
                    RETURNING id, authorization_id, request_hash, document_id,
                              signed_at, storage_key, status, sha256, size_bytes,
                              title, file_name, signer_name, signature_method
                    """
                ),
                {"id": _uuid(reservation_id), "tenant_id": _uuid(auth.tenant_id)},
            )
            claimed_row = claimed.mappings().first()
            if claimed_row is None:
                raise ConflictError(
                    "FERPA_SIGNING_RESERVATION_SUPERSEDED",
                    "This FERPA signing attempt changed; reload and try again",
                )
            return {
                **self._signing_reservation_projection(dict(claimed_row)),
                "claimed": True,
            }

    async def mark_signing_reservation_stored(
        self,
        auth: AuthContext,
        reservation_id: str,
        document: Mapping[str, Any],
    ) -> JsonDict:
        require_student_ferpa_control(auth)
        async with self.engine.begin() as connection:
            updated = await connection.execute(
                text(
                    """
                    UPDATE ferpa_signing_reservation
                    SET status='stored', sha256=:sha256, size_bytes=:size_bytes,
                        title=:title, file_name=:file_name,
                        signer_name=:signer_name, signature_method=:signature_method,
                        lease_expires_at=NULL, updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND student_id=:student_id
                      AND actor_id=:actor_id AND status IN ('generating','stored')
                      AND document_id=:document_id AND storage_key=:storage_key
                      AND (sha256 IS NULL OR sha256=:sha256)
                      AND (size_bytes IS NULL OR size_bytes=:size_bytes)
                      AND (title IS NULL OR title=:title)
                      AND (file_name IS NULL OR file_name=:file_name)
                      AND (signer_name IS NULL OR signer_name=:signer_name)
                      AND (signature_method IS NULL OR signature_method=:signature_method)
                    RETURNING id, authorization_id, request_hash, document_id,
                              signed_at, storage_key, status, sha256, size_bytes,
                              title, file_name, signer_name, signature_method
                    """
                ),
                {
                    "id": _uuid(reservation_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                    "actor_id": _uuid(auth.actor_id),
                    "document_id": _uuid(document["id"]),
                    "storage_key": document["storageKey"],
                    "sha256": document["sha256"],
                    "size_bytes": int(document["sizeBytes"]),
                    "title": document["title"],
                    "file_name": document["fileName"],
                    "signer_name": document["signerName"],
                    "signature_method": document["signatureMethod"],
                },
            )
            row = updated.mappings().first()
            if row is None:
                raise ConflictError(
                    "FERPA_SIGNING_RESERVATION_CONFLICT",
                    "The FERPA signing evidence does not match its reservation",
                )
            return self._signing_reservation_projection(dict(row))

    async def abandon_signing_reservation(
        self,
        auth: AuthContext,
        reservation_id: str,
    ) -> None:
        require_student_ferpa_control(auth)
        async with self.engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE ferpa_signing_reservation
                    SET status='failed', lease_expires_at=NULL, updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND student_id=:student_id
                      AND actor_id=:actor_id AND status<>'finalized'
                    """
                ),
                {
                    "id": _uuid(reservation_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                    "actor_id": _uuid(auth.actor_id),
                },
            )

    async def complete(
        self,
        auth: AuthContext,
        identifier: str,
        payload: Mapping[str, Any],
        document: Mapping[str, Any] | None,
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        require_student_ferpa_control(auth)
        expected_version = _positive_version(payload.get("expectedVersion"))
        decision, delegates = _normalize_access(payload, allow_ids=True)
        operation = f"ferpa.complete:{identifier}"
        request_hash = _completion_request_hash(auth, payload)
        async with self.engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"{auth.tenant_id}:{auth.actor_id}:{operation}:{idempotency_key}"},
            )
            existing = await connection.execute(
                text(
                    """
                    SELECT request_hash, response_body FROM idempotency_record
                    WHERE tenant_id=:tenant_id AND actor_id=:actor_id
                      AND operation=:operation AND idempotency_key=:key
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "actor_id": _uuid(auth.actor_id),
                    "operation": operation,
                    "key": idempotency_key,
                },
            )
            cached = existing.mappings().first()
            if cached is not None:
                if str(cached["request_hash"]) != request_hash:
                    raise ConflictError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "This idempotency key was already used for a different FERPA request",
                    )
                return _mapping(cached["response_body"])
            await self._materialize_current(connection, auth)
            row = await self._current_row(
                connection,
                auth,
                identifier=identifier,
                for_update=True,
            )
            if row is None:
                raise NotFoundError("FERPA_REQUIREMENT_NOT_FOUND", "The FERPA task was not found")
            self._validate_completion_row(row, expected_version)
            if document is not None:
                finalized = await connection.execute(
                    text(
                        """
                        UPDATE ferpa_signing_reservation
                        SET status='finalized', lease_expires_at=NULL, updated_at=NOW()
                        WHERE tenant_id=:tenant_id
                          AND authorization_id=:authorization_id
                          AND actor_id=:actor_id AND operation=:operation
                          AND status='stored'
                          AND document_id=:document_id AND storage_key=:storage_key
                          AND sha256=:sha256 AND size_bytes=:size_bytes
                        RETURNING id
                        """
                    ),
                    {
                        "tenant_id": _uuid(auth.tenant_id),
                        "authorization_id": row["authorization_id"],
                        "actor_id": _uuid(auth.actor_id),
                        "operation": operation,
                        "document_id": _uuid(document["id"]),
                        "storage_key": document["storageKey"],
                        "sha256": document["sha256"],
                        "size_bytes": int(document["sizeBytes"]),
                    },
                )
                if finalized.mappings().first() is None:
                    raise ConflictError(
                        "FERPA_SIGNING_RESERVATION_CONFLICT",
                        "The FERPA signing evidence is not finalized for this request",
                    )
                await self._insert_signed_document(connection, row, document)
            signed_evidence = await connection.execute(
                text(
                    """
                    SELECT 1 FROM ferpa_signed_document
                    WHERE authorization_id=:authorization_id AND tenant_id=:tenant_id
                    """
                ),
                {
                    "authorization_id": row["authorization_id"],
                    "tenant_id": _uuid(auth.tenant_id),
                },
            )
            if signed_evidence.first() is None:
                raise BadRequestError(
                    "FERPA_SIGNATURE_REQUIRED",
                    "The FERPA document must be signed before completion",
                )
            await self._sync_delegates(
                connection,
                row,
                decision=decision,
                delegates=delegates,
            )
            updated = await connection.execute(
                text(
                    """
                    UPDATE ferpa_authorization
                    SET access_decision=:decision, status='completed', completed_at=NOW(),
                        version=version+1, updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND version=:expected_version
                    RETURNING version
                    """
                ),
                {
                    "decision": decision,
                    "id": row["authorization_id"],
                    "tenant_id": _uuid(auth.tenant_id),
                    "expected_version": expected_version,
                },
            )
            updated_row = updated.mappings().first()
            if updated_row is None:
                raise ConflictError("VERSION_CONFLICT", "FERPA changed in another session")
            requirement_update = await connection.execute(
                text(
                    """
                    UPDATE student_requirement
                    SET status='completed', progress_percent=100,
                        version=version+1, updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND retired_at IS NULL
                      AND version=:expected_requirement_version
                    RETURNING version
                    """
                ),
                {
                    "id": row["requirement_id"],
                    "tenant_id": _uuid(auth.tenant_id),
                    "expected_requirement_version": int(row["requirement_version"]),
                },
            )
            if requirement_update.mappings().first() is None:
                raise ConflictError("VERSION_CONFLICT", "The FERPA task changed during completion")
            await reconcile_student_journey_routes(
                connection,
                tenant_id=auth.tenant_id,
                student_id=auth.student_id,
                journey_id=str(row["journey_id"]),
            )
            await connection.execute(
                text(
                    """
                    UPDATE student_experience_update
                    SET status='acknowledged', acknowledged_at=NOW(),
                        version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND requirement_id=:requirement_id
                      AND status IN ('pending','deferred')
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                    "requirement_id": row["requirement_id"],
                },
            )
            completed_journey = await connection.execute(
                text(
                    """
                    UPDATE enrollment_journey journey
                    SET status='completed', version=journey.version+1, updated_at=NOW()
                    WHERE journey.id=:journey_id AND journey.tenant_id=:tenant_id
                      AND journey.status NOT IN ('completed','cancelled')
                      AND (
                        EXISTS (
                          SELECT 1 FROM journey_definition_version definition
                          WHERE definition.id=journey.journey_definition_version_id
                            AND definition.tenant_id=journey.tenant_id
                            AND NOT definition.onboarding_required
                        )
                        OR EXISTS (
                          SELECT 1 FROM student_onboarding onboarding
                          WHERE onboarding.tenant_id=journey.tenant_id
                            AND onboarding.student_id=journey.student_id
                            AND onboarding.status='completed'
                        )
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM student_requirement requirement
                        WHERE requirement.tenant_id=journey.tenant_id
                          AND requirement.journey_id=journey.id
                          AND requirement.retired_at IS NULL
                          AND requirement.status NOT IN (
                            'not_applicable','completed','waived','expired'
                          )
                      )
                    RETURNING journey.id
                    """
                ),
                {
                    "journey_id": row["journey_id"],
                    "tenant_id": _uuid(auth.tenant_id),
                },
            )
            if self.portal is not None:
                await self.portal._award_rewards(
                    connection,
                    auth,
                    "requirement_completed",
                    str(row["code"]),
                    str(row["requirement_id"]),
                    {},
                )
                await self.portal._insert_student_message(
                    connection,
                    auth,
                    subject="FERPA release and parent access complete",
                    body="Your signed FERPA release and portal-access decision were saved.",
                    kind="requirement_completed",
                    href="/enrollment/requirements/family-permissions",
                )
                if completed_journey.mappings().first() is not None:
                    await self.portal._insert_student_message(
                        connection,
                        auth,
                        subject="Enrollment complete",
                        body="All required enrollment tasks are complete.",
                        kind="enrollment_completed",
                        href="/dashboard",
                    )
            await self._write_legacy_projection(
                connection,
                auth,
                row,
                decision=decision,
                delegates=delegates,
            )
            version = int(updated_row["version"])
            refreshed = await self._current_row(connection, auth, for_update=False)
            if refreshed is None:  # pragma: no cover - same transaction invariant
                raise RuntimeError("FERPA authorization disappeared after completion")
            authorization = await self._map_authorization(connection, auth, refreshed)
            await self._record_change(
                connection,
                auth,
                authorization_id=str(row["authorization_id"]),
                version=version,
                action="completed",
                request_id=request_id,
                snapshot=await self._revision_snapshot(connection, auth, refreshed),
            )
            response = {"authorization": authorization}
            await connection.execute(
                text(
                    """
                    INSERT INTO idempotency_record (
                      tenant_id, actor_id, operation, idempotency_key, request_hash,
                      response_status, response_body, created_at, expires_at
                    ) VALUES (
                      :tenant_id, :actor_id, :operation, :key, :request_hash,
                      200, CAST(:response AS jsonb), NOW(), NOW()+INTERVAL '24 hours'
                    )
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "actor_id": _uuid(auth.actor_id),
                    "operation": operation,
                    "key": idempotency_key,
                    "request_hash": request_hash,
                    "response": _json(response),
                },
            )
            return response

    @staticmethod
    def _validate_completion_row(row: Mapping[str, Any], expected_version: int) -> None:
        if int(row["authorization_version"]) != expected_version:
            raise ConflictError("VERSION_CONFLICT", "FERPA changed in another session")
        if row["authorization_status"] == "completed":
            raise ConflictError("FERPA_ALREADY_COMPLETED", "FERPA is already complete")
        if row["requirement_status"] in {
            "blocked",
            "not_applicable",
            "submitted",
            "under_review",
            "waived",
            "expired",
        }:
            raise ConflictError(
                "FERPA_REQUIREMENT_NOT_ACTIONABLE",
                "The FERPA task is not currently available for completion",
            )
        input_config = _mapping(row.get("input_config"))
        if input_config.get("signatureProvider") == "docusign":
            raise ConflictError(
                "DOCUSIGN_EXECUTION_NOT_CONFIGURED",
                "DocuSign execution is not configured for this environment",
            )

    async def update_access(
        self,
        auth: AuthContext,
        authorization_id: str,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> JsonDict:
        require_student_ferpa_control(auth)
        expected_version = _positive_version(payload.get("expectedVersion"))
        decision, delegates = _normalize_access(payload, allow_ids=True)
        async with self.engine.begin() as connection:
            row = await self._authorization_row(
                connection,
                auth,
                authorization_id=authorization_id,
                for_update=True,
            )
            if row is None:
                raise NotFoundError(
                    "FERPA_AUTHORIZATION_NOT_FOUND", "The FERPA authorization was not found"
                )
            if int(row["authorization_version"]) != expected_version:
                raise ConflictError("VERSION_CONFLICT", "FERPA changed in another session")
            if row["authorization_status"] != "completed":
                raise ConflictError(
                    "FERPA_NOT_COMPLETED",
                    "Sign and complete FERPA before changing ongoing access",
                )
            await self._sync_delegates(
                connection,
                row,
                decision=decision,
                delegates=delegates,
            )
            updated = await connection.execute(
                text(
                    """
                    UPDATE ferpa_authorization
                    SET access_decision=:decision, version=version+1, updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND version=:expected_version
                    RETURNING version
                    """
                ),
                {
                    "decision": decision,
                    "id": _uuid(authorization_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "expected_version": expected_version,
                },
            )
            updated_row = updated.mappings().first()
            if updated_row is None:
                raise ConflictError("VERSION_CONFLICT", "FERPA changed in another session")
            await self._write_legacy_projection(
                connection,
                auth,
                row,
                decision=decision,
                delegates=delegates,
            )
            version = int(updated_row["version"])
            refreshed = await self._authorization_row(
                connection,
                auth,
                authorization_id=authorization_id,
                for_update=False,
            )
            if refreshed is None:  # pragma: no cover
                raise RuntimeError("FERPA authorization disappeared after access update")
            authorization = await self._map_authorization(connection, auth, refreshed)
            await self._record_change(
                connection,
                auth,
                authorization_id=authorization_id,
                version=version,
                action="access_updated",
                request_id=request_id,
                snapshot=await self._revision_snapshot(connection, auth, refreshed),
            )
            return {"authorization": authorization}

    async def issue_link(
        self,
        auth: AuthContext,
        authorization_id: str,
        delegate_id: str,
        *,
        expected_version: int,
        token: str,
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        require_student_ferpa_control(auth)
        expected_version = _positive_version(expected_version)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        key_hash = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        async with self.engine.begin() as connection:
            row = await self._authorization_row(
                connection,
                auth,
                authorization_id=authorization_id,
                for_update=True,
            )
            if row is None:
                raise NotFoundError(
                    "FERPA_AUTHORIZATION_NOT_FOUND", "The FERPA authorization was not found"
                )
            delegate = await self._active_delegate(
                connection,
                row,
                delegate_id,
                for_update=True,
            )
            existing = await connection.execute(
                text(
                    """
                    SELECT status, token_hash, idempotency_key_hash, issued_at, rotated_at
                    FROM ferpa_delegate_link WHERE delegate_id=:delegate_id
                    FOR UPDATE
                    """
                ),
                {"delegate_id": _uuid(delegate_id)},
            )
            link = existing.mappings().first()
            if link is not None and str(link["idempotency_key_hash"]) == key_hash:
                if link["status"] != "active" or str(link["token_hash"]) != token_hash:
                    raise ConflictError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "Use a new idempotency key to issue a new delegate link",
                    )
                return {
                    "authorizationVersion": int(row["authorization_version"]),
                    "delegateId": delegate_id,
                    "status": "active",
                    "token": token,
                    "issuedAt": _iso(link["rotated_at"] or link["issued_at"]),
                }
            if int(row["authorization_version"]) != expected_version:
                raise ConflictError("VERSION_CONFLICT", "FERPA changed in another session")
            if row["authorization_status"] != "completed" or row["access_decision"] != "grant":
                raise ConflictError(
                    "FERPA_ACCESS_NOT_GRANTED", "Grant this delegate access before creating a link"
                )
            action = "link_issued" if link is None else "link_rotated"
            await connection.execute(
                text(
                    """
                    INSERT INTO ferpa_delegate_link (
                      delegate_id, tenant_id, token_hash, status, idempotency_key_hash,
                      issued_at, updated_at
                    ) VALUES (
                      :delegate_id, :tenant_id, :token_hash, 'active', :key_hash, NOW(), NOW()
                    ) ON CONFLICT (delegate_id) DO UPDATE SET
                      token_hash=EXCLUDED.token_hash, status='active',
                      idempotency_key_hash=EXCLUDED.idempotency_key_hash,
                      rotated_at=NOW(), revoked_at=NULL, last_used_at=NULL, updated_at=NOW()
                    """
                ),
                {
                    "delegate_id": _uuid(delegate_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "token_hash": token_hash,
                    "key_hash": key_hash,
                },
            )
            await self._revoke_delegate_sessions(connection, delegate_id)
            version = await self._increment_authorization(connection, row, expected_version)
            timestamp = await connection.execute(
                text(
                    """
                    SELECT COALESCE(rotated_at, issued_at) FROM ferpa_delegate_link
                    WHERE delegate_id=:delegate_id
                    """
                ),
                {"delegate_id": _uuid(delegate_id)},
            )
            refreshed = await self._authorization_row(
                connection,
                auth,
                authorization_id=authorization_id,
                for_update=False,
            )
            if refreshed is None:  # pragma: no cover
                raise RuntimeError("FERPA authorization disappeared after link update")
            snapshot = await self._revision_snapshot(connection, auth, refreshed)
            snapshot["change"] = {
                "delegateId": delegate_id,
                "relationship": str(delegate["relationship"]),
                "linkAction": action,
            }
            await self._record_change(
                connection,
                auth,
                authorization_id=authorization_id,
                version=version,
                action=action,
                request_id=request_id,
                snapshot=snapshot,
            )
            return {
                "authorizationVersion": version,
                "delegateId": delegate_id,
                "status": "active",
                "token": token,
                "issuedAt": _iso(timestamp.scalar_one()),
            }

    async def revoke_link(
        self,
        auth: AuthContext,
        authorization_id: str,
        delegate_id: str,
        *,
        expected_version: int,
        request_id: str,
    ) -> JsonDict:
        require_student_ferpa_control(auth)
        expected_version = _positive_version(expected_version)
        async with self.engine.begin() as connection:
            row = await self._authorization_row(
                connection,
                auth,
                authorization_id=authorization_id,
                for_update=True,
            )
            if row is None:
                raise NotFoundError(
                    "FERPA_AUTHORIZATION_NOT_FOUND", "The FERPA authorization was not found"
                )
            if int(row["authorization_version"]) != expected_version:
                raise ConflictError("VERSION_CONFLICT", "FERPA changed in another session")
            await self._active_delegate(connection, row, delegate_id, for_update=True)
            revoked = await connection.execute(
                text(
                    """
                    UPDATE ferpa_delegate_link
                    SET status='revoked', revoked_at=NOW(), updated_at=NOW()
                    WHERE delegate_id=:delegate_id AND tenant_id=:tenant_id AND status='active'
                    RETURNING delegate_id
                    """
                ),
                {"delegate_id": _uuid(delegate_id), "tenant_id": _uuid(auth.tenant_id)},
            )
            if revoked.mappings().first() is None:
                raise ConflictError("FERPA_LINK_NOT_ACTIVE", "This delegate link is not active")
            await self._revoke_delegate_sessions(connection, delegate_id)
            version = await self._increment_authorization(connection, row, expected_version)
            refreshed = await self._authorization_row(
                connection,
                auth,
                authorization_id=authorization_id,
                for_update=False,
            )
            if refreshed is None:  # pragma: no cover
                raise RuntimeError("FERPA authorization disappeared after link revocation")
            authorization = await self._map_authorization(connection, auth, refreshed)
            snapshot = await self._revision_snapshot(connection, auth, refreshed)
            snapshot["change"] = {"delegateId": delegate_id, "linkAction": "link_revoked"}
            await self._record_change(
                connection,
                auth,
                authorization_id=authorization_id,
                version=version,
                action="link_revoked",
                request_id=request_id,
                snapshot=snapshot,
            )
            return {"authorization": authorization}

    async def requirement_context(
        self,
        auth: AuthContext,
        identifier: str,
        *,
        enforce_delegate_scope: bool = True,
    ) -> JsonDict:
        """Resolve a subject-owned current requirement for scoped cross-page actions."""

        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT requirement.id, requirement.status,
                           current_definition.code,
                           current_definition.flow_kind,
                           current_definition.interaction_type
                    FROM student_requirement requirement
                    JOIN enrollment_journey journey
                      ON journey.id=requirement.journey_id
                     AND journey.tenant_id=requirement.tenant_id
                    JOIN requirement_definition_version evidence_definition
                      ON evidence_definition.id=requirement.requirement_definition_version_id
                     AND evidence_definition.tenant_id=requirement.tenant_id
                    JOIN journey_requirement_definition current_link
                      ON current_link.journey_definition_version_id=
                         journey.journey_definition_version_id
                    JOIN requirement_definition_version current_definition
                      ON current_definition.id=current_link.requirement_definition_version_id
                     AND current_definition.tenant_id=requirement.tenant_id
                     AND current_definition.code=evidence_definition.code
                    WHERE requirement.tenant_id=:tenant_id
                      AND journey.student_id=:student_id
                      AND requirement.retired_at IS NULL
                      AND (requirement.id::text=:identifier OR current_definition.code=:code)
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                    "identifier": identifier,
                    "code": identifier.lower().replace("-", "_"),
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError(
                    "STUDENT_REQUIREMENT_NOT_FOUND", "The requirement was not found"
                )
            interaction_type = str(row["interaction_type"])
            if auth.is_delegate and interaction_type == "ferpa":
                raise ApiError(
                    403,
                    "FERPA_STUDENT_CONTROL_REQUIRED",
                    "Only the student may sign FERPA or manage parent and guardian access",
                )
            flow = str(row["flow_kind"])
            if auth.is_delegate and enforce_delegate_scope:
                require_delegate_scope(auth, "enrollment")
            return {
                "id": str(row["id"]),
                "status": str(row["status"]),
                "code": str(row["code"]),
                "flowKind": flow,
                "interactionType": interaction_type,
            }

    async def requirement_flow(self, auth: AuthContext, identifier: str) -> str:
        context = await self.requirement_context(auth, identifier)
        return str(context["interactionType"])

    async def document_requirement_context(
        self, auth: AuthContext, document_id: str
    ) -> JsonDict | None:
        """Resolve the page owner for a requirement-bound student document."""

        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT current_definition.flow_kind,
                           current_definition.interaction_type
                    FROM student_document document
                    JOIN student_requirement requirement
                      ON requirement.id=document.requirement_id
                     AND requirement.tenant_id=document.tenant_id
                    JOIN enrollment_journey journey
                      ON journey.id=requirement.journey_id
                     AND journey.tenant_id=requirement.tenant_id
                    JOIN requirement_definition_version evidence_definition
                      ON evidence_definition.id=requirement.requirement_definition_version_id
                     AND evidence_definition.tenant_id=requirement.tenant_id
                    JOIN journey_requirement_definition current_link
                      ON current_link.journey_definition_version_id=
                         journey.journey_definition_version_id
                    JOIN requirement_definition_version current_definition
                      ON current_definition.id=current_link.requirement_definition_version_id
                     AND current_definition.tenant_id=requirement.tenant_id
                     AND current_definition.code=evidence_definition.code
                    WHERE document.id=:document_id
                      AND document.tenant_id=:tenant_id
                      AND document.student_id=:student_id
                    LIMIT 1
                    """
                ),
                {
                    "document_id": _uuid(document_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                },
            )
            row = result.mappings().first()
            return (
                {
                    "flowKind": str(row["flow_kind"]),
                    "interactionType": str(row["interaction_type"]),
                }
                if row is not None
                else None
            )

    async def get_document_reference(self, auth: AuthContext, document_id: str) -> JsonDict | None:
        if auth.is_delegate:
            raise ApiError(
                403,
                "FERPA_STUDENT_CONTROL_REQUIRED",
                "The signed FERPA evidence is available only to the student",
            )
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT storage_key, file_name, mime_type
                    FROM ferpa_signed_document
                    WHERE id=:id AND tenant_id=:tenant_id AND student_id=:student_id
                    """
                ),
                {
                    "id": _uuid(document_id),
                    "tenant_id": _uuid(auth.tenant_id),
                    "student_id": _uuid(auth.student_id),
                },
            )
            row = result.mappings().first()
            return (
                {
                    "storageKey": str(row["storage_key"]),
                    "fileName": str(row["file_name"]),
                    "mimeType": str(row["mime_type"]),
                }
                if row is not None
                else None
            )

    async def _materialize_current(self, connection: AsyncConnection, auth: AuthContext) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO ferpa_authorization (id, tenant_id, student_id, requirement_id)
                SELECT gen_random_uuid(), requirement.tenant_id, journey.student_id, requirement.id
                FROM student_requirement requirement
                JOIN enrollment_journey journey
                  ON journey.id=requirement.journey_id
                 AND journey.tenant_id=requirement.tenant_id
                JOIN requirement_definition_version evidence_definition
                  ON evidence_definition.id=requirement.requirement_definition_version_id
                 AND evidence_definition.tenant_id=requirement.tenant_id
                JOIN journey_requirement_definition current_link
                  ON current_link.journey_definition_version_id=
                     journey.journey_definition_version_id
                JOIN requirement_definition_version current_definition
                  ON current_definition.id=current_link.requirement_definition_version_id
                 AND current_definition.tenant_id=requirement.tenant_id
                 AND current_definition.code=evidence_definition.code
                WHERE requirement.tenant_id=:tenant_id AND journey.student_id=:student_id
                  AND requirement.retired_at IS NULL
                  AND current_definition.interaction_type='ferpa'
                ORDER BY requirement.created_at DESC, requirement.id DESC
                LIMIT 1
                ON CONFLICT (tenant_id, student_id) DO NOTHING
                RETURNING id
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id), "student_id": _uuid(auth.student_id)},
        )
        # The authorization is canonical per student. A replacement published
        # FERPA task rebinds that same aggregate instead of creating a second
        # consent record or invalidating existing delegate links.
        await connection.execute(
            text(
                """
                UPDATE ferpa_authorization ferpa_auth
                SET requirement_id=candidate.requirement_id,
                    version=ferpa_auth.version+1, updated_at=NOW()
                FROM (
                  SELECT requirement.id AS requirement_id
                  FROM student_requirement requirement
                  JOIN enrollment_journey journey
                    ON journey.id=requirement.journey_id
                   AND journey.tenant_id=requirement.tenant_id
                  JOIN requirement_definition_version evidence_definition
                    ON evidence_definition.id=requirement.requirement_definition_version_id
                   AND evidence_definition.tenant_id=requirement.tenant_id
                  JOIN journey_requirement_definition current_link
                    ON current_link.journey_definition_version_id=
                       journey.journey_definition_version_id
                  JOIN requirement_definition_version current_definition
                    ON current_definition.id=current_link.requirement_definition_version_id
                   AND current_definition.tenant_id=requirement.tenant_id
                   AND current_definition.code=evidence_definition.code
                  WHERE requirement.tenant_id=:tenant_id
                    AND journey.student_id=:student_id
                    AND requirement.retired_at IS NULL
                    AND current_definition.interaction_type='ferpa'
                  ORDER BY requirement.created_at DESC, requirement.id DESC
                  LIMIT 1
                ) candidate
                WHERE ferpa_auth.tenant_id=:tenant_id
                  AND ferpa_auth.student_id=:student_id
                  AND ferpa_auth.requirement_id<>candidate.requirement_id
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id), "student_id": _uuid(auth.student_id)},
        )
        legacy_candidate = await connection.execute(
            text(
                """
                SELECT ferpa_auth.id
                FROM ferpa_authorization ferpa_auth
                WHERE ferpa_auth.tenant_id=:tenant_id
                  AND ferpa_auth.student_id=:student_id
                  AND ferpa_auth.status='incomplete'
                  AND ferpa_auth.access_decision IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM ferpa_delegate delegate
                    WHERE delegate.authorization_id=ferpa_auth.id
                  )
                LIMIT 1
                """
            ),
            {"tenant_id": _uuid(auth.tenant_id), "student_id": _uuid(auth.student_id)},
        )
        legacy_row = legacy_candidate.mappings().first()
        if legacy_row is not None:
            await self._import_legacy_delegates(
                connection,
                auth,
                authorization_id=legacy_row["id"],
            )
        await reconcile_completed_ferpa_requirements(
            connection,
            tenant_id=_uuid(auth.tenant_id),
            student_id=_uuid(auth.student_id),
        )

    @staticmethod
    def _signing_reservation_projection(row: Mapping[str, Any]) -> JsonDict:
        return {
            "id": str(row["id"]),
            "authorizationId": str(row["authorization_id"]),
            "documentId": str(row["document_id"]),
            "signedAt": _iso(row["signed_at"]),
            "storageKey": str(row["storage_key"]),
            "status": str(row["status"]),
            "sha256": None if row.get("sha256") is None else str(row["sha256"]),
            "sizeBytes": (None if row.get("size_bytes") is None else int(row["size_bytes"])),
            "title": None if row.get("title") is None else str(row["title"]),
            "fileName": None if row.get("file_name") is None else str(row["file_name"]),
            "signerName": (None if row.get("signer_name") is None else str(row["signer_name"])),
            "signatureMethod": (
                None if row.get("signature_method") is None else str(row["signature_method"])
            ),
        }

    async def _reserve_signing_evidence(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        *,
        authorization_id: object,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> JsonDict:
        """Reserve stable evidence while retaining failed attempts for safe GC.

        A reload may use a new idempotency key. The exact same request hash can
        resume the active reservation; a different signature may supersede only
        a reservation that is not under an unexpired generation lease. Failed
        rows retain their storage keys so no PII object becomes untracked.
        """

        select_sql = """
            SELECT id, authorization_id, actor_id, operation, idempotency_key,
                   request_hash, document_id, signed_at, storage_key, status,
                   sha256, size_bytes, title, file_name, signer_name,
                   signature_method, lease_expires_at
            FROM ferpa_signing_reservation
        """
        params = {
            "tenant_id": _uuid(auth.tenant_id),
            "actor_id": _uuid(auth.actor_id),
            "authorization_id": authorization_id,
            "operation": operation,
            "idempotency_key": idempotency_key,
        }
        keyed_result = await connection.execute(
            text(
                select_sql
                + """
                WHERE tenant_id=:tenant_id AND actor_id=:actor_id
                  AND operation=:operation AND idempotency_key=:idempotency_key
                LIMIT 1 FOR UPDATE
                """
            ),
            params,
        )
        keyed = keyed_result.mappings().first()
        if keyed is not None and str(keyed["request_hash"]) != request_hash:
            raise ConflictError(
                "IDEMPOTENCY_KEY_REUSED",
                "This idempotency key was already used for a different FERPA request",
            )

        active_result = await connection.execute(
            text(
                select_sql
                + """
                WHERE tenant_id=:tenant_id AND authorization_id=:authorization_id
                  AND status IN ('reserved','generating','stored')
                LIMIT 1 FOR UPDATE
                """
            ),
            params,
        )
        active = active_result.mappings().first()
        if active is not None and str(active["request_hash"]) == request_hash:
            self._validate_signing_reservation_request(
                dict(active),
                auth,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            return self._signing_reservation_projection(dict(active))
        if active is not None:
            if active["status"] == "stored":
                return self._signing_reservation_projection(dict(active))
            lease_active = (
                active["status"] == "generating"
                and active["lease_expires_at"] is not None
                and cast(datetime, active["lease_expires_at"]) > datetime.now(UTC)
            )
            if lease_active:
                raise ConflictError(
                    "FERPA_SIGNING_RESERVATION_ACTIVE",
                    "This FERPA authorization already has an active signing attempt",
                )
            await connection.execute(
                text(
                    """
                    UPDATE ferpa_signing_reservation
                    SET status='failed', lease_expires_at=NULL, updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id
                      AND status IN ('reserved','generating','stored')
                    """
                ),
                {"id": active["id"], "tenant_id": params["tenant_id"]},
            )

        if keyed is not None and keyed["status"] == "failed":
            restored = await connection.execute(
                text(
                    """
                    UPDATE ferpa_signing_reservation
                    SET status=CASE
                          WHEN sha256 IS NOT NULL AND size_bytes IS NOT NULL
                          THEN 'stored' ELSE 'reserved' END,
                        lease_expires_at=NULL, updated_at=NOW()
                    WHERE id=:id AND tenant_id=:tenant_id AND status='failed'
                    RETURNING id, authorization_id, actor_id, operation,
                              idempotency_key, request_hash, document_id,
                              signed_at, storage_key, status, sha256, size_bytes,
                              title, file_name, signer_name, signature_method,
                              lease_expires_at
                    """
                ),
                {"id": keyed["id"], "tenant_id": params["tenant_id"]},
            )
            restored_row = restored.mappings().one()
            return self._signing_reservation_projection(dict(restored_row))
        if keyed is not None:
            raise ConflictError(
                "FERPA_SIGNING_RESERVATION_ACTIVE",
                "This FERPA signing attempt cannot be restarted",
            )

        reservation_id = uuid4()
        document_id = uuid4()
        signed_at = datetime.now(UTC)
        storage_key = (
            f"{auth.tenant_id}/{auth.student_id}/signed-ferpa/{authorization_id}/{document_id}.pdf"
        )
        inserted = await connection.execute(
            text(
                """
                INSERT INTO ferpa_signing_reservation (
                  id, tenant_id, student_id, authorization_id, actor_id,
                  operation, idempotency_key, request_hash, document_id,
                  signed_at, storage_key
                ) VALUES (
                  :id, :tenant_id, :student_id, :authorization_id, :actor_id,
                  :operation, :idempotency_key, :request_hash, :document_id,
                  :signed_at, :storage_key
                )
                RETURNING id, authorization_id, actor_id, operation,
                          idempotency_key, request_hash, document_id,
                          signed_at, storage_key, status, sha256, size_bytes,
                          title, file_name, signer_name, signature_method,
                          lease_expires_at
                """
            ),
            {
                **params,
                "id": reservation_id,
                "student_id": _uuid(auth.student_id),
                "request_hash": request_hash,
                "document_id": document_id,
                "signed_at": signed_at,
                "storage_key": storage_key,
            },
        )
        return self._signing_reservation_projection(dict(inserted.mappings().one()))

    @staticmethod
    def _validate_signing_reservation_request(
        row: Mapping[str, Any],
        auth: AuthContext,
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
    ) -> None:
        if str(row["actor_id"]) != str(_uuid(auth.actor_id)) or str(row["operation"]) != operation:
            raise ConflictError(
                "FERPA_SIGNING_RESERVATION_ACTIVE",
                "This FERPA authorization already has an active signing attempt",
            )
        if str(row["request_hash"]) != request_hash:
            if str(row["idempotency_key"]) == idempotency_key:
                raise ConflictError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "This idempotency key was already used for a different FERPA request",
                )
            raise ConflictError(
                "FERPA_SIGNING_RESERVATION_ACTIVE",
                "This FERPA authorization already has an active signing attempt",
            )

    async def _import_legacy_delegates(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        *,
        authorization_id: object,
    ) -> None:
        """Surface legacy contacts for review without granting any page access."""

        await connection.execute(
            text(
                """
                INSERT INTO ferpa_delegate (
                  id, tenant_id, student_id, authorization_id, full_name,
                  relationship, email_normalized, scopes, display_order,
                  active, legacy_review_required
                )
                SELECT DISTINCT ON (lower(left(trim(permission->>'email'),254)))
                       gen_random_uuid(), :tenant_id, :student_id, :authorization_id,
                       left(
                         COALESCE(NULLIF(trim(permission->>'fullName'),''),'Legacy delegate'),
                         160
                       ),
                       CASE permission->>'relationship'
                         WHEN 'parent' THEN 'parent'
                         WHEN 'guardian' THEN 'guardian'
                         WHEN 'partner' THEN 'partner'
                         WHEN 'sponsor' THEN 'sponsor'
                         WHEN 'relative' THEN 'relative'
                         ELSE 'other'
                       END,
                       lower(left(trim(permission->>'email'),254)), '{}',
                       (ordinality-1)::smallint, true, true
                FROM student_onboarding onboarding
                CROSS JOIN LATERAL jsonb_array_elements(
                  CASE
                    WHEN jsonb_typeof(onboarding.payload->'familyPermissions')='array'
                    THEN onboarding.payload->'familyPermissions'
                    ELSE '[]'::jsonb
                  END
                ) WITH ORDINALITY AS legacy(permission, ordinality)
                WHERE onboarding.tenant_id=:tenant_id
                  AND onboarding.student_id=:student_id
                  AND ordinality<=4
                  AND trim(COALESCE(permission->>'email',''))<>''
                ORDER BY lower(left(trim(permission->>'email'),254)), ordinality
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "student_id": _uuid(auth.student_id),
                "authorization_id": authorization_id,
            },
        )

    async def _current_row(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        *,
        identifier: str | None = None,
        for_update: bool,
    ) -> Mapping[str, Any] | None:
        # Read paths use a short transaction too, so one constant locking query
        # avoids dynamic SQL while mutation paths retain their serialization.
        del for_update
        result = await connection.execute(
            text(
                """
                SELECT ferpa_auth.id AS authorization_id,
                       ferpa_auth.access_decision,
                       ferpa_auth.status AS authorization_status,
                       ferpa_auth.version AS authorization_version,
                       ferpa_auth.completed_at, ferpa_auth.updated_at,
                       ferpa_auth.tenant_id, ferpa_auth.student_id,
                       requirement.id AS requirement_id,
                       requirement.status AS requirement_status,
                       requirement.version AS requirement_version,
                       requirement.journey_id,
                       evidence_definition.id AS requirement_definition_version_id,
                       COALESCE(current_definition.code, evidence_definition.code) AS code,
                       COALESCE(
                         current_definition.flow_kind, evidence_definition.flow_kind
                       ) AS flow_kind,
                       COALESCE(
                         current_definition.input_config, evidence_definition.input_config
                       ) AS input_config
                FROM ferpa_authorization ferpa_auth
                JOIN student_requirement requirement
                  ON requirement.id=ferpa_auth.requirement_id
                 AND requirement.tenant_id=ferpa_auth.tenant_id
                JOIN enrollment_journey journey
                  ON journey.id=requirement.journey_id
                 AND journey.tenant_id=requirement.tenant_id
                JOIN requirement_definition_version evidence_definition
                  ON evidence_definition.id=requirement.requirement_definition_version_id
                 AND evidence_definition.tenant_id=requirement.tenant_id
                LEFT JOIN requirement_definition_version current_definition
                  ON current_definition.tenant_id=requirement.tenant_id
                 AND current_definition.code=evidence_definition.code
                 AND EXISTS (
                   SELECT 1 FROM journey_requirement_definition current_link
                   WHERE current_link.journey_definition_version_id=
                         journey.journey_definition_version_id
                     AND current_link.requirement_definition_version_id=
                         current_definition.id
                 )
                WHERE ferpa_auth.tenant_id=:tenant_id
                  AND ferpa_auth.student_id=:student_id
                  AND (
                    ferpa_auth.status='completed'
                    OR (
                      requirement.retired_at IS NULL
                      AND current_definition.interaction_type='ferpa'
                    )
                  )
                  AND (CAST(:identifier AS text) IS NULL
                       OR requirement.id::text=CAST(:identifier AS text)
                       OR COALESCE(
                            current_definition.code, evidence_definition.code
                          )=:code)
                LIMIT 1
                FOR UPDATE OF ferpa_auth, requirement
                """
            ),
            {
                "tenant_id": _uuid(auth.tenant_id),
                "student_id": _uuid(auth.student_id),
                "identifier": identifier,
                "code": identifier.lower().replace("-", "_") if identifier else None,
            },
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _authorization_row(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        *,
        authorization_id: str,
        for_update: bool,
    ) -> Mapping[str, Any] | None:
        row = await self._current_row(connection, auth, for_update=for_update)
        if row is None or str(row["authorization_id"]) != str(_uuid(authorization_id)):
            return None
        return row

    async def _map_authorization(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        row: Mapping[str, Any],
    ) -> JsonDict:
        signed_result = await connection.execute(
            text(
                """
                SELECT id, title, file_name, signer_name, signature_method, signed_at
                FROM ferpa_signed_document
                WHERE authorization_id=:authorization_id AND tenant_id=:tenant_id
                LIMIT 1
                """
            ),
            {
                "authorization_id": row["authorization_id"],
                "tenant_id": _uuid(auth.tenant_id),
            },
        )
        signed = signed_result.mappings().first()
        delegate_result = await connection.execute(
            text(
                """
                SELECT delegate.id, delegate.full_name, delegate.relationship,
                       delegate.email_normalized, delegate.scopes,
                       delegate.legacy_review_required, delegate.updated_at,
                       link.status AS link_status, link.issued_at, link.rotated_at,
                       link.last_used_at, link.updated_at AS link_updated_at
                FROM ferpa_delegate delegate
                LEFT JOIN ferpa_delegate_link link ON link.delegate_id=delegate.id
                WHERE delegate.authorization_id=:authorization_id
                  AND delegate.tenant_id=:tenant_id AND delegate.active=true
                  AND (CAST(:delegate_id AS uuid) IS NULL
                       OR delegate.id=CAST(:delegate_id AS uuid))
                ORDER BY delegate.display_order, delegate.id
                """
            ),
            {
                "authorization_id": row["authorization_id"],
                "tenant_id": _uuid(auth.tenant_id),
                "delegate_id": _uuid(auth.actor_id) if auth.is_delegate else None,
            },
        )
        delegates = []
        for delegate in delegate_result.mappings().all():
            delegates.append(
                {
                    "id": str(delegate["id"]),
                    "fullName": str(delegate["full_name"]),
                    "relationship": str(delegate["relationship"]),
                    "email": str(delegate["email_normalized"]),
                    "scopes": _canonical_delegate_scopes(delegate["scopes"]),
                    **(
                        {"legacyReviewRequired": True}
                        if delegate["legacy_review_required"] is True
                        else {}
                    ),
                    "link": {
                        "status": str(delegate["link_status"] or "not_issued"),
                        "issuedAt": _nullable_iso(delegate["issued_at"]),
                        "rotatedAt": _nullable_iso(delegate["rotated_at"]),
                        "lastUsedAt": _nullable_iso(delegate["last_used_at"]),
                        "updatedAt": _iso(delegate["link_updated_at"] or delegate["updated_at"]),
                    },
                }
            )
        config = _mapping(row.get("input_config"))
        return {
            "id": str(row["authorization_id"]),
            "requirementId": str(row["requirement_id"]),
            "requirementVersion": int(row["requirement_version"]),
            "flowKind": str(row["flow_kind"]),
            "status": str(row["authorization_status"]),
            "accessDecision": row["access_decision"],
            "document": (
                {
                    "status": "signed",
                    "signedDocumentId": str(signed["id"]),
                    "title": str(signed["title"]),
                    "fileName": str(signed["file_name"]),
                    "signedAt": _iso(signed["signed_at"]),
                    "signerName": str(signed["signer_name"]),
                    "signatureMethod": str(signed["signature_method"]),
                }
                if signed is not None
                else {"status": "unsigned"}
            ),
            "delegates": delegates,
            "version": int(row["authorization_version"]),
            "completedAt": _nullable_iso(row["completed_at"]),
            "updatedAt": _iso(row["updated_at"]),
            "configuration": {
                "signatureProvider": str(config.get("signatureProvider") or "built_in"),
                "docusignTemplateId": config.get("docusignTemplateId"),
                "portalScopes": list(PORTAL_SCOPES),
            },
            "capabilities": {
                "canSign": not auth.is_delegate and row["authorization_status"] != "completed",
                "canManageAccess": not auth.is_delegate,
                "canManageLinks": (
                    not auth.is_delegate and row["authorization_status"] == "completed"
                ),
            },
        }

    async def _insert_signed_document(
        self,
        connection: AsyncConnection,
        row: Mapping[str, Any],
        document: Mapping[str, Any],
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO ferpa_signed_document (
                  id, tenant_id, student_id, authorization_id, requirement_id,
                  requirement_version, template_code, title, file_name, mime_type,
                  size_bytes, storage_provider, storage_key, sha256, signer_name,
                  signature_method, signed_at
                ) VALUES (
                  :id, :tenant_id, :student_id, :authorization_id, :requirement_id,
                  :requirement_version, 'ferpa_release', :title, :file_name,
                  'application/pdf', :size_bytes, :storage_provider, :storage_key,
                  :sha256, :signer_name, :signature_method, :signed_at
                ) ON CONFLICT (authorization_id) DO NOTHING
                """
            ),
            {
                "id": _uuid(document["id"]),
                "tenant_id": row["tenant_id"] if "tenant_id" in row else document["tenantId"],
                "student_id": document["studentId"],
                "authorization_id": row["authorization_id"],
                "requirement_id": row["requirement_id"],
                "requirement_version": int(row["requirement_version"]),
                "title": document["title"],
                "file_name": document["fileName"],
                "size_bytes": int(document["sizeBytes"]),
                "storage_provider": document.get("storageProvider", "s3"),
                "storage_key": document["storageKey"],
                "sha256": document["sha256"],
                "signer_name": document["signerName"],
                "signature_method": document["signatureMethod"],
                "signed_at": _timestamp(document["signedAt"]),
            },
        )

    async def _sync_delegates(
        self,
        connection: AsyncConnection,
        row: Mapping[str, Any],
        *,
        decision: str,
        delegates: list[JsonDict],
    ) -> None:
        current_result = await connection.execute(
            text(
                """
                SELECT id, full_name, relationship, email_normalized
                FROM ferpa_delegate
                WHERE authorization_id=:authorization_id AND tenant_id=:tenant_id
                  AND active=true
                FOR UPDATE
                """
            ),
            {
                "authorization_id": row["authorization_id"],
                "tenant_id": row.get("tenant_id") or row.get("authorization_tenant_id"),
            },
        )
        current_rows = {str(item["id"]): dict(item) for item in current_result.mappings().all()}
        current_ids = set(current_rows)
        requested_ids = {str(item["id"]) for item in delegates if item.get("id")}
        if not requested_ids.issubset(current_ids):
            raise BadRequestError(
                "INVALID_FERPA_DELEGATE_ID",
                "A delegate does not belong to this FERPA authorization",
            )
        # Deactivate first so email swaps and removals cannot conflict with the
        # active-only uniqueness boundary.
        await connection.execute(
            text(
                """
                UPDATE ferpa_delegate SET active=false, updated_at=NOW()
                WHERE authorization_id=:authorization_id AND tenant_id=:tenant_id
                """
            ),
            {
                "authorization_id": row["authorization_id"],
                "tenant_id": row.get("tenant_id") or row.get("authorization_tenant_id"),
            },
        )
        identity_changed_ids: set[str] = set()
        for delegate in delegates:
            delegate_id = str(delegate.get("id") or uuid4())
            if delegate.get("id"):
                previous = current_rows[delegate_id]
                if any(
                    (
                        str(previous["full_name"]) != str(delegate["fullName"]),
                        str(previous["relationship"]) != str(delegate["relationship"]),
                        str(previous["email_normalized"]) != str(delegate["email"]),
                    )
                ):
                    identity_changed_ids.add(delegate_id)
                await connection.execute(
                    text(
                        """
                        UPDATE ferpa_delegate
                        SET full_name=:full_name, relationship=:relationship,
                            email_normalized=:email, scopes=CAST(:scopes AS text[]),
                            display_order=:display_order, active=true,
                            legacy_review_required=false, updated_at=NOW()
                        WHERE id=:id AND authorization_id=:authorization_id
                          AND tenant_id=:tenant_id
                        """
                    ),
                    {
                        "id": _uuid(delegate_id),
                        "authorization_id": row["authorization_id"],
                        "tenant_id": row.get("tenant_id") or row.get("authorization_tenant_id"),
                        "full_name": delegate["fullName"],
                        "relationship": delegate["relationship"],
                        "email": delegate["email"],
                        "scopes": delegate["scopes"],
                        "display_order": delegate["displayOrder"],
                    },
                )
            else:
                await connection.execute(
                    text(
                        """
                        INSERT INTO ferpa_delegate (
                          id, tenant_id, student_id, authorization_id, full_name,
                          relationship, email_normalized, scopes, display_order, active
                        ) VALUES (
                          :id, :tenant_id, :student_id, :authorization_id, :full_name,
                          :relationship, :email, CAST(:scopes AS text[]), :display_order, true
                        )
                        """
                    ),
                    {
                        "id": _uuid(delegate_id),
                        "tenant_id": row.get("tenant_id") or row.get("authorization_tenant_id"),
                        "student_id": row.get("student_id") or row.get("authorization_student_id"),
                        "authorization_id": row["authorization_id"],
                        "full_name": delegate["fullName"],
                        "relationship": delegate["relationship"],
                        "email": delegate["email"],
                        "scopes": delegate["scopes"],
                        "display_order": delegate["displayOrder"],
                    },
                )
        removed_ids = (current_ids - requested_ids) | identity_changed_ids
        if decision == "no_access":
            removed_ids = current_ids
        for removed_id in removed_ids:
            await connection.execute(
                text(
                    """
                    UPDATE ferpa_delegate_link
                    SET status='revoked', revoked_at=COALESCE(revoked_at,NOW()), updated_at=NOW()
                    WHERE delegate_id=:delegate_id AND status='active'
                    """
                ),
                {"delegate_id": _uuid(removed_id)},
            )
            await self._revoke_delegate_sessions(connection, removed_id)

    async def _active_delegate(
        self,
        connection: AsyncConnection,
        row: Mapping[str, Any],
        delegate_id: str,
        *,
        for_update: bool,
    ) -> Mapping[str, Any]:
        del for_update
        result = await connection.execute(
            text(
                """
                SELECT id, relationship FROM ferpa_delegate
                WHERE id=:id AND authorization_id=:authorization_id
                  AND tenant_id=:tenant_id AND active=true
                FOR UPDATE
                """
            ),
            {
                "id": _uuid(delegate_id),
                "authorization_id": row["authorization_id"],
                "tenant_id": row.get("tenant_id") or row.get("authorization_tenant_id"),
            },
        )
        delegate = result.mappings().first()
        if delegate is None:
            raise NotFoundError("FERPA_DELEGATE_NOT_FOUND", "The FERPA delegate was not found")
        return dict(delegate)

    async def _increment_authorization(
        self,
        connection: AsyncConnection,
        row: Mapping[str, Any],
        expected_version: int,
    ) -> int:
        updated = await connection.execute(
            text(
                """
                UPDATE ferpa_authorization SET version=version+1, updated_at=NOW()
                WHERE id=:id AND version=:expected_version RETURNING version
                """
            ),
            {"id": row["authorization_id"], "expected_version": expected_version},
        )
        item = updated.mappings().first()
        if item is None:
            raise ConflictError("VERSION_CONFLICT", "FERPA changed in another session")
        return int(item["version"])

    async def _revoke_delegate_sessions(
        self, connection: AsyncConnection, delegate_id: str
    ) -> None:
        await connection.execute(
            text(
                """
                UPDATE ferpa_delegate_session
                SET revoked_at=COALESCE(revoked_at,NOW())
                WHERE delegate_id=:delegate_id AND revoked_at IS NULL
                """
            ),
            {"delegate_id": _uuid(delegate_id)},
        )

    async def _write_legacy_projection(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        row: Mapping[str, Any],
        *,
        decision: str,
        delegates: list[JsonDict],
    ) -> None:
        legacy = (
            [
                {
                    "fullName": item["fullName"],
                    "relationship": item["relationship"],
                    "email": item["email"],
                    "scopes": item["scopes"],
                    "purpose": "other",
                    "expires": "end_enrollment",
                }
                for item in delegates
            ]
            if decision == "grant"
            else []
        )
        await connection.execute(
            text(
                """
                UPDATE student_onboarding
                SET payload=jsonb_set(
                      payload, '{familyPermissions}', CAST(:delegates AS jsonb), true
                    ),
                    version=version+1, updated_at=NOW()
                WHERE tenant_id=:tenant_id AND student_id=:student_id
                """
            ),
            {
                "delegates": _json(legacy),
                "tenant_id": _uuid(auth.tenant_id),
                "student_id": _uuid(auth.student_id),
            },
        )

    async def _revision_snapshot(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        row: Mapping[str, Any],
    ) -> JsonDict:
        authorization = await self._map_authorization(connection, auth, row)
        evidence_result = await connection.execute(
            text(
                """
                SELECT id, sha256, requirement_id, requirement_version, signed_at
                FROM ferpa_signed_document
                WHERE authorization_id=:authorization_id AND tenant_id=:tenant_id
                LIMIT 1
                """
            ),
            {
                "authorization_id": row["authorization_id"],
                "tenant_id": _uuid(auth.tenant_id),
            },
        )
        evidence = evidence_result.mappings().first()
        return {
            "authorizationId": str(row["authorization_id"]),
            "status": authorization["status"],
            "accessDecision": authorization["accessDecision"],
            "version": authorization["version"],
            "requirement": {
                "id": str(row["requirement_id"]),
                "code": str(row["code"]),
                "definitionVersionId": str(row["requirement_definition_version_id"]),
                "version": int(row["requirement_version"]),
                "flowKind": str(row["flow_kind"]),
                "status": str(row["requirement_status"]),
            },
            "signedDocument": (
                {
                    "id": str(evidence["id"]),
                    "sha256": str(evidence["sha256"]),
                    "requirementId": str(evidence["requirement_id"]),
                    "requirementVersion": int(evidence["requirement_version"]),
                    "signedAt": _iso(evidence["signed_at"]),
                }
                if evidence is not None
                else None
            ),
            "delegates": [
                {
                    "id": delegate["id"],
                    "fullName": delegate["fullName"],
                    "relationship": delegate["relationship"],
                    "email": delegate["email"],
                    "scopes": list(delegate["scopes"]),
                    "legacyReviewRequired": bool(delegate.get("legacyReviewRequired", False)),
                    "link": dict(_mapping(delegate.get("link"))),
                }
                for delegate in cast(list[JsonDict], authorization["delegates"])
            ],
        }

    async def _record_change(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        *,
        authorization_id: str,
        version: int,
        action: str,
        request_id: str,
        snapshot: Mapping[str, Any],
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO ferpa_authorization_revision (
                  id, tenant_id, authorization_id, authorization_version,
                  actor_type, actor_id, action, snapshot, request_id
                ) VALUES (
                  :id, :tenant_id, :authorization_id, :version,
                  :actor_type, :actor_id, :action, CAST(:snapshot AS jsonb), :request_id
                )
                """
            ),
            {
                "id": uuid4(),
                "tenant_id": _uuid(auth.tenant_id),
                "authorization_id": _uuid(authorization_id),
                "version": version,
                "actor_type": auth.actor_type,
                "actor_id": _uuid(auth.actor_id),
                "action": action,
                "snapshot": _json(snapshot),
                "request_id": request_id,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO audit_event (
                  id, tenant_id, actor_type, actor_id, student_id, action,
                  resource_type, resource_id, authorization_basis, request_id,
                  correlation_id, metadata
                ) VALUES (
                  :id, :tenant_id, :actor_type, :actor_id, :student_id, :audit_action,
                  'ferpa_authorization', :authorization_id, 'student_self_service',
                  :request_id, :request_id, CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "id": uuid4(),
                "tenant_id": _uuid(auth.tenant_id),
                "actor_type": auth.actor_type,
                "actor_id": _uuid(auth.actor_id),
                "student_id": _uuid(auth.student_id),
                "audit_action": f"ferpa_authorization.{action}",
                "authorization_id": _uuid(authorization_id),
                "request_id": request_id,
                "metadata": _json({**snapshot, "version": version}),
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO outbox_event (
                  id, tenant_id, event_name, aggregate_type, aggregate_id,
                  aggregate_version, occurred_at, actor_type, actor_id,
                  correlation_id, causation_id, payload
                ) VALUES (
                  :id, :tenant_id, :event_name, 'ferpa_authorization',
                  :authorization_id, :version, NOW(), :actor_type, :actor_id,
                  :request_id, :causation_id, CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "id": uuid4(),
                "tenant_id": _uuid(auth.tenant_id),
                "event_name": f"ferpa_authorization.{action}.v1",
                "authorization_id": _uuid(authorization_id),
                "version": version,
                "actor_type": auth.actor_type,
                "actor_id": _uuid(auth.actor_id),
                "request_id": request_id,
                "causation_id": str(uuid4()),
                "payload": _json({"studentId": auth.student_id, **snapshot}),
            },
        )
