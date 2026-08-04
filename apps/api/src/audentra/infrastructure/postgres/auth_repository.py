"""Non-production PostgreSQL credential and browser-session adapter."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, ConflictError, UnauthorizedError
from audentra.core.ports import CredentialStudentSession, DemoStudentSession, StaffSession
from audentra.infrastructure.seeding.relational import (
    HARVARD_TENANT_ID,
    ensure_harvard_demo_student,
    reset_relational_data,
)

_STUDENT_SESSION_LIFETIME = timedelta(days=7)
_STAFF_SESSION_LIFETIME = timedelta(hours=8)
_MAXIMUM_SESSIONS = 5
_PASSWORD_N = 2**14
_PASSWORD_R = 8
_PASSWORD_P = 1
_PASSWORD_MAX_MEMORY = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _StaffSessionRecord:
    staff_id: str
    tenant_id: str
    tenant_slug: str | None
    expires_at: datetime


class PostgresDevelopmentAuth:
    """Local and preview credential adapter that cannot compose for production."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        environment: str,
        staff_password: str,
        demo_student_ids: Mapping[str, str],
    ) -> None:
        if environment not in {"development", "preview", "test"}:
            raise ValueError("The development authentication adapter is disabled in production")
        if not staff_password:
            raise ValueError("A local staff bootstrap password is required")
        self._engine = engine
        self._environment = environment
        self._staff_password = staff_password
        self._demo_student_ids = dict(demo_student_ids)
        self._staff_sessions: dict[str, _StaffSessionRecord] = {}
        self._staff_lock = asyncio.Lock()

    async def demo_student(self, tenant_id: str, tenant_slug: str | None) -> DemoStudentSession:
        if tenant_id == HARVARD_TENANT_ID:
            await ensure_harvard_demo_student(
                self._engine,
                environment=self._environment,
            )
        student_id = self._demo_student_id(tenant_slug)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT s.id AS student_id, s.person_id,
                           COALESCE(sp.preferred_name, p.preferred_name, p.first_name)
                             AS preferred_name
                    FROM student s
                    JOIN person p ON p.id=s.person_id AND p.tenant_id=s.tenant_id
                    LEFT JOIN student_profile sp
                      ON sp.student_id=s.id AND sp.tenant_id=s.tenant_id
                    WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                    """
                ),
                {"tenant_id": UUID(tenant_id), "student_id": UUID(student_id)},
            )
            row = result.mappings().first()
        if row is None:
            raise ApiError(
                503,
                "DEMO_IDENTITY_NOT_CONFIGURED",
                "The development student identity is not configured for this university",
            )
        return DemoStudentSession(
            context=AuthContext(
                tenant_id=tenant_id,
                student_id=str(row["student_id"]),
                actor_id=str(row["person_id"]),
                actor_type="student",
                authentication_method="demo",
                tenant_slug=tenant_slug,
            ),
            preferred_name=str(row["preferred_name"]),
        )

    async def resolve_student(
        self, token: str, tenant_id: str, tenant_slug: str | None
    ) -> CredentialStudentSession | None:
        token_hash = _session_token_hash(token)
        if token_hash is None:
            return None
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT account.id AS account_id, account.student_id,
                           account.email_normalized, account.phone_e164,
                           account.email_verified_at, account.phone_verified_at,
                           student.person_id, onboarding.status AS onboarding_status,
                           profile.preferred_name, session.expires_at
                    FROM auth_session session
                    JOIN credential_account account ON account.id=session.account_id
                    JOIN student ON student.id=account.student_id
                      AND student.tenant_id=account.tenant_id
                    JOIN student_onboarding onboarding
                      ON onboarding.student_id=account.student_id
                     AND onboarding.tenant_id=account.tenant_id
                    LEFT JOIN student_profile profile
                      ON profile.student_id=account.student_id
                     AND profile.tenant_id=account.tenant_id
                    WHERE session.token_hash=:token_hash
                      AND session.revoked_at IS NULL
                      AND session.expires_at>NOW()
                      AND account.status='active'
                      AND account.tenant_id=:tenant_id
                    """
                ),
                {"token_hash": token_hash, "tenant_id": UUID(tenant_id)},
            )
            row = result.mappings().first()
            if row is None:
                return None
            await connection.execute(
                text(
                    """
                    UPDATE auth_session SET last_seen_at=NOW()
                    WHERE token_hash=:token_hash
                      AND last_seen_at<NOW()-INTERVAL '5 minutes'
                    """
                ),
                {"token_hash": token_hash},
            )
        return _credential_session(dict(row), tenant_id, tenant_slug)

    async def sign_up_student(
        self,
        *,
        tenant_id: str,
        tenant_slug: str | None,
        email: str,
        phone: str,
        password: str,
    ) -> CredentialStudentSession:
        normalized_email = _normalize_email(email)
        password_hash = await asyncio.to_thread(_hash_password, password)
        now = datetime.now(UTC)
        expires_at = now + _STUDENT_SESSION_LIFETIME
        account_id = uuid4()
        person_id = uuid4()
        student_id = uuid4()
        offer_id = uuid4()
        token = secrets.token_urlsafe(32)
        token_hash = _required_session_token_hash(token)
        try:
            async with self._engine.begin() as connection:
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": f"auth-sign-up:{tenant_id}:{normalized_email}:{phone}"},
                )
                duplicate = await connection.execute(
                    text(
                        """
                        SELECT email_normalized=:email AS email_exists,
                               phone_e164=:phone AS phone_exists
                        FROM credential_account
                        WHERE tenant_id=:tenant_id
                          AND (email_normalized=:email OR phone_e164=:phone)
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": UUID(tenant_id),
                        "email": normalized_email,
                        "phone": phone,
                    },
                )
                existing = duplicate.mappings().first()
                if existing is not None:
                    if bool(existing["email_exists"]):
                        raise ConflictError(
                            "AUTH_EMAIL_EXISTS",
                            "An account already exists for this email address",
                        )
                    raise ConflictError(
                        "AUTH_PHONE_EXISTS",
                        "An account already exists for this phone number",
                    )

                template_result = await connection.execute(
                    text(
                        """
                        SELECT offer.program_id, offer.academic_term_id, offer.campus_id,
                               offer.response_deadline, offer.deposit_amount_cents,
                               student.class_year,
                               COALESCE(
                                 (
                                   SELECT summary.academic_year
                                   FROM student_financial_summary summary
                                   WHERE summary.tenant_id=offer.tenant_id
                                   ORDER BY summary.academic_year DESC
                                   LIMIT 1
                                 ),
                                 EXTRACT(YEAR FROM term.starts_on)::integer::text || '-' ||
                                 (EXTRACT(YEAR FROM term.starts_on)::integer + 1)::text
                               ) AS financial_academic_year,
                               COALESCE(
                                 (
                                   SELECT summary.cost_of_attendance_cents
                                   FROM student_financial_summary summary
                                   WHERE summary.tenant_id=offer.tenant_id
                                   ORDER BY summary.academic_year DESC
                                   LIMIT 1
                                 ),
                                 0
                               ) AS cost_of_attendance_cents
                        FROM admission_offer offer
                        JOIN student ON student.id=offer.student_id
                          AND student.tenant_id=offer.tenant_id
                        JOIN academic_term term ON term.id=offer.academic_term_id
                          AND term.tenant_id=offer.tenant_id
                        WHERE offer.tenant_id=:tenant_id
                        ORDER BY offer.created_at
                        LIMIT 1
                        """
                    ),
                    {"tenant_id": UUID(tenant_id)},
                )
                template = template_result.mappings().first()
                if template is None:
                    raise ApiError(
                        503,
                        "AUTH_TENANT_NOT_CONFIGURED",
                        "Account creation is not configured for this university",
                    )
                await self._insert_new_student(
                    connection,
                    tenant_id=tenant_id,
                    account_id=account_id,
                    person_id=person_id,
                    student_id=student_id,
                    offer_id=offer_id,
                    email=normalized_email,
                    phone=phone,
                    password_hash=password_hash,
                    template=dict(template),
                )
                await self._insert_student_session(
                    connection,
                    account_id=account_id,
                    session_id=uuid4(),
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
        except IntegrityError as error:
            raise ConflictError(
                "AUTH_ACCOUNT_EXISTS",
                "An account already exists for this email address or phone number",
            ) from error
        return CredentialStudentSession(
            context=AuthContext(
                tenant_id=tenant_id,
                student_id=str(student_id),
                actor_id=str(person_id),
                actor_type="student",
                authentication_method="credentials",
                tenant_slug=tenant_slug,
            ),
            preferred_name=None,
            email=normalized_email,
            phone=phone,
            email_verified=False,
            phone_verified=False,
            token=token,
            expires_at_epoch=int(expires_at.timestamp()),
        )

    async def sign_in_student(
        self,
        *,
        tenant_id: str,
        tenant_slug: str | None,
        email: str,
        password: str,
    ) -> CredentialStudentSession:
        normalized_email = _normalize_email(email)
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT account.id AS account_id, account.student_id,
                           account.email_normalized, account.phone_e164,
                           account.email_verified_at, account.phone_verified_at,
                           account.password_hash, account.status, student.person_id,
                           onboarding.status AS onboarding_status, profile.preferred_name
                    FROM credential_account account
                    JOIN student ON student.id=account.student_id
                      AND student.tenant_id=account.tenant_id
                    JOIN student_onboarding onboarding
                      ON onboarding.student_id=account.student_id
                     AND onboarding.tenant_id=account.tenant_id
                    LEFT JOIN student_profile profile
                      ON profile.student_id=account.student_id
                     AND profile.tenant_id=account.tenant_id
                    WHERE account.tenant_id=:tenant_id
                      AND account.email_normalized=:email
                    FOR UPDATE OF account
                    """
                ),
                {"tenant_id": UUID(tenant_id), "email": normalized_email},
            )
            row = result.mappings().first()
            stored_hash = str(row["password_hash"]) if row is not None else "invalid"
            valid_password = await asyncio.to_thread(_verify_password, password, stored_hash)
            if row is None or row["status"] != "active" or not valid_password:
                if row is not None:
                    await connection.execute(
                        text(
                            """
                            UPDATE credential_account
                            SET failed_sign_in_count=LEAST(failed_sign_in_count+1, 100),
                                updated_at=NOW()
                            WHERE id=:account_id
                            """
                        ),
                        {"account_id": row["account_id"]},
                    )
                raise UnauthorizedError("Email or password is incorrect")
            now = datetime.now(UTC)
            expires_at = now + _STUDENT_SESSION_LIFETIME
            token = secrets.token_urlsafe(32)
            await connection.execute(
                text(
                    """
                    UPDATE credential_account
                    SET failed_sign_in_count=0, locked_until=NULL,
                        last_signed_in_at=NOW(), updated_at=NOW()
                    WHERE id=:account_id
                    """
                ),
                {"account_id": row["account_id"]},
            )
            await self._insert_student_session(
                connection,
                account_id=UUID(str(row["account_id"])),
                session_id=uuid4(),
                token_hash=_required_session_token_hash(token),
                expires_at=expires_at,
            )
        return _credential_session(
            dict(row),
            tenant_id,
            tenant_slug,
            token=token,
            expires_at_epoch=int(expires_at.timestamp()),
        )

    async def sign_out_student(self, token: str | None) -> None:
        token_hash = _session_token_hash(token)
        if token_hash is None:
            return
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE auth_session SET revoked_at=COALESCE(revoked_at, NOW())
                    WHERE token_hash=:token_hash
                    """
                ),
                {"token_hash": token_hash},
            )

    async def resolve_staff(
        self, token: str, tenant_id: str, tenant_slug: str | None
    ) -> StaffSession | None:
        token_hash = _session_token_hash(token)
        if token_hash is None:
            return None
        async with self._staff_lock:
            record = self._staff_sessions.get(token_hash)
            if record is None or record.expires_at <= datetime.now(UTC):
                self._staff_sessions.pop(token_hash, None)
                return None
            if record.tenant_id != tenant_id:
                return None
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, display_name, email_normalized, component
                    FROM staff_member
                    WHERE tenant_id=:tenant_id AND id=:staff_id AND active=true
                    """
                ),
                {"tenant_id": UUID(tenant_id), "staff_id": UUID(record.staff_id)},
            )
            row = result.mappings().first()
        if row is None:
            return None
        return self._staff_session(dict(row), tenant_id, tenant_slug)

    async def sign_in_staff(
        self,
        *,
        tenant_id: str,
        tenant_slug: str | None,
        email: str,
        password: str,
    ) -> StaffSession:
        if tenant_id == HARVARD_TENANT_ID:
            await ensure_harvard_demo_student(
                self._engine,
                environment=self._environment,
            )
        normalized_email = _normalize_email(email)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, display_name, email_normalized, component
                    FROM staff_member
                    WHERE tenant_id=:tenant_id AND email_normalized=:email AND active=true
                    """
                ),
                {"tenant_id": UUID(tenant_id), "email": normalized_email},
            )
            row = result.mappings().first()
        password_matches = secrets.compare_digest(
            hashlib.sha256(password.encode("utf-8")).digest(),
            hashlib.sha256(self._staff_password.encode("utf-8")).digest(),
        )
        if row is None or not password_matches:
            raise UnauthorizedError("Email or password is incorrect")
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + _STAFF_SESSION_LIFETIME
        token_hash = _required_session_token_hash(token)
        async with self._staff_lock:
            self._prune_staff_sessions(str(row["id"]), datetime.now(UTC))
            self._staff_sessions[token_hash] = _StaffSessionRecord(
                staff_id=str(row["id"]),
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
                expires_at=expires_at,
            )
        return self._staff_session(
            dict(row),
            tenant_id,
            tenant_slug,
            token=token,
            expires_at_epoch=int(expires_at.timestamp()),
        )

    async def sign_out_staff(self, token: str | None) -> None:
        token_hash = _session_token_hash(token)
        if token_hash is None:
            return
        async with self._staff_lock:
            self._staff_sessions.pop(token_hash, None)

    async def reset_demo_fixture(self, *, completed_onboarding: bool) -> None:
        await reset_relational_data(
            self._engine,
            environment=self._environment,
            completed_onboarding=completed_onboarding,
        )
        async with self._staff_lock:
            self._staff_sessions.clear()

    async def _insert_new_student(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: str,
        account_id: UUID,
        person_id: UUID,
        student_id: UUID,
        offer_id: UUID,
        email: str,
        phone: str,
        password_hash: str,
        template: Mapping[str, Any],
    ) -> None:
        values = {
            "tenant_id": UUID(tenant_id),
            "account_id": account_id,
            "person_id": person_id,
            "student_id": student_id,
            "offer_id": offer_id,
            "email": email,
            "phone": phone,
            "password_hash": password_hash,
            "program_id": template["program_id"],
            "academic_term_id": template["academic_term_id"],
            "campus_id": template["campus_id"],
            "response_deadline": template["response_deadline"],
            "deposit_amount_cents": template["deposit_amount_cents"],
            "class_year": template["class_year"],
            "financial_academic_year": template["financial_academic_year"],
            "cost_of_attendance_cents": template["cost_of_attendance_cents"],
        }
        for statement in (
            """
            INSERT INTO person (id, tenant_id, preferred_name, first_name, last_name)
            VALUES (:person_id, :tenant_id, NULL, 'Student', 'Account')
            """,
            """
            INSERT INTO student (id, tenant_id, person_id, class_year)
            VALUES (:student_id, :tenant_id, :person_id, :class_year)
            """,
            """
            INSERT INTO admission_offer (
              id, tenant_id, student_id, program_id, academic_term_id, campus_id,
              response_deadline, deposit_amount_cents, status, version
            ) VALUES (
              :offer_id, :tenant_id, :student_id, :program_id, :academic_term_id,
              :campus_id, :response_deadline, :deposit_amount_cents, 'offered', 1
            )
            """,
            """
            INSERT INTO student_onboarding (
              tenant_id, student_id, status, current_step, completed_steps, payload, version
            ) VALUES (:tenant_id, :student_id, 'in_progress', 'offer', '{}', '{}'::jsonb, 1)
            """,
            """
            INSERT INTO student_profile (
              tenant_id, student_id, preferred_name, communication_preference, version
            ) VALUES (:tenant_id, :student_id, 'Student', 'email', 1)
            """,
            """
            INSERT INTO credential_account (
              id, tenant_id, student_id, email_normalized, phone_e164,
              password_hash, password_algorithm, status
            ) VALUES (
              :account_id, :tenant_id, :student_id, :email, :phone,
              :password_hash, 'scrypt-v1', 'active'
            )
            """,
            """
            INSERT INTO student_portal_projection (
              tenant_id, student_id, projection_version, dashboard, source_updated_at
            ) VALUES (:tenant_id, :student_id, 1, '{}'::jsonb, NOW())
            """,
            """
            INSERT INTO student_financial_summary (
              tenant_id, student_id, academic_year, cost_of_attendance_cents,
              external_payments_cents, version
            ) VALUES (
              :tenant_id, :student_id, :financial_academic_year,
              :cost_of_attendance_cents, 0, 1
            )
            """,
            """
            INSERT INTO student_sap_status (
              tenant_id, student_id, academic_year, status, cumulative_gpa,
              minimum_gpa, completion_rate_percent,
              minimum_completion_rate_percent, attempted_credits,
              maximum_attempted_credits, version
            ) VALUES (
              :tenant_id, :student_id, :financial_academic_year, 'meeting', 0,
              2, 100, 67, 0, 180, 1
            )
            """,
        ):
            await connection.execute(text(statement), values)

    async def _insert_student_session(
        self,
        connection: AsyncConnection,
        *,
        account_id: UUID,
        session_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        await connection.execute(
            text(
                """
                UPDATE auth_session SET revoked_at=NOW()
                WHERE id IN (
                  SELECT id FROM auth_session
                  WHERE account_id=:account_id AND revoked_at IS NULL AND expires_at>NOW()
                  ORDER BY created_at DESC
                  OFFSET :keep_count
                )
                """
            ),
            {"account_id": account_id, "keep_count": _MAXIMUM_SESSIONS - 1},
        )
        await connection.execute(
            text(
                """
                INSERT INTO auth_session (id, account_id, token_hash, expires_at)
                VALUES (:id, :account_id, :token_hash, :expires_at)
                """
            ),
            {
                "id": session_id,
                "account_id": account_id,
                "token_hash": token_hash,
                "expires_at": expires_at,
            },
        )

    def _staff_session(
        self,
        row: Mapping[str, Any],
        tenant_id: str,
        tenant_slug: str | None,
        *,
        token: str | None = None,
        expires_at_epoch: int | None = None,
    ) -> StaffSession:
        return StaffSession(
            context=AuthContext(
                tenant_id=tenant_id,
                student_id=self._demo_student_id(tenant_slug),
                actor_id=str(row["id"]),
                actor_type="staff",
                authentication_method="credentials",
                tenant_slug=tenant_slug,
            ),
            name=str(row["display_name"]),
            email=str(row["email_normalized"]),
            component=str(row["component"]),
            token=token,
            expires_at_epoch=expires_at_epoch,
        )

    def _demo_student_id(self, tenant_slug: str | None) -> str:
        if tenant_slug is not None and tenant_slug in self._demo_student_ids:
            return self._demo_student_ids[tenant_slug]
        return self._demo_student_ids["aster"]

    def _prune_staff_sessions(self, staff_id: str, now: datetime) -> None:
        for token_hash, record in tuple(self._staff_sessions.items()):
            if record.expires_at <= now:
                self._staff_sessions.pop(token_hash, None)
        active = sorted(
            (
                (token_hash, record)
                for token_hash, record in self._staff_sessions.items()
                if record.staff_id == staff_id
            ),
            key=lambda item: item[1].expires_at,
        )
        for token_hash, _record in active[: max(0, len(active) - _MAXIMUM_SESSIONS + 1)]:
            self._staff_sessions.pop(token_hash, None)


def _credential_session(
    row: Mapping[str, Any],
    tenant_id: str,
    tenant_slug: str | None,
    *,
    token: str | None = None,
    expires_at_epoch: int | None = None,
) -> CredentialStudentSession:
    preferred = row.get("preferred_name")
    return CredentialStudentSession(
        context=AuthContext(
            tenant_id=tenant_id,
            student_id=str(row["student_id"]),
            actor_id=str(row["person_id"]),
            actor_type="student",
            authentication_method="credentials",
            tenant_slug=tenant_slug,
        ),
        preferred_name=(
            str(preferred)
            if row.get("onboarding_status") == "completed" and preferred is not None
            else None
        ),
        email=str(row["email_normalized"]),
        phone=str(row["phone_e164"]),
        email_verified=row.get("email_verified_at") is not None,
        phone_verified=row.get("phone_verified_at") is not None,
        token=token,
        expires_at_epoch=expires_at_epoch,
    )


def _normalize_email(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().lower()


def _session_token_hash(token: str | None) -> str | None:
    if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _required_session_token_hash(token: str) -> str:
    token_hash = _session_token_hash(token)
    if token_hash is None:
        raise RuntimeError("Generated session token is invalid")
    return token_hash


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_PASSWORD_N,
        r=_PASSWORD_R,
        p=_PASSWORD_P,
        maxmem=_PASSWORD_MAX_MEMORY,
        dklen=32,
    )
    return f"scrypt-v1${_base64(salt)}${_base64(derived)}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_value, expected_value = encoded.split("$", 2)
        if algorithm != "scrypt-v1":
            raise ValueError
        salt = _unbase64(salt_value)
        expected = _unbase64(expected_value)
        if len(salt) != 16 or len(expected) != 32:
            raise ValueError
    except (ValueError, TypeError):
        salt = b"audentra-invalid"
        expected = bytes(32)
    actual = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_PASSWORD_N,
        r=_PASSWORD_R,
        p=_PASSWORD_P,
        maxmem=_PASSWORD_MAX_MEMORY,
        dklen=32,
    )
    return secrets.compare_digest(actual, expected)


def _base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unbase64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
