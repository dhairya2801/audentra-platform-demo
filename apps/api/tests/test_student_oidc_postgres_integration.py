from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.oidc import OidcAuthorizationStart, OidcFlowError, OidcLogin
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.db.migrations import (
    resolve_migrations_directory,
    run_migrations,
)
from audentra.infrastructure.postgres.auth_repository import PostgresDevelopmentAuth
from audentra.infrastructure.postgres.oidc_repository import (
    OidcProviderConfig,
    OidcSettings,
    PostgresOidcAuth,
)

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

_GOOGLE_ISSUER = "https://accounts.google.com"
_GOOGLE_CLIENT_ID = "postgres-integration-student-client"
_GOOGLE_CLIENT_SECRET = "postgres-integration-client-secret"  # noqa: S105
_SIGNING_KEY_ID = "postgres-integration-signing-key"


@dataclass(frozen=True, slots=True)
class _StudentFixture:
    tenant_id: UUID
    tenant_slug: str
    person_id: UUID
    student_id: UUID
    account_id: UUID
    email: str
    phone: str


@dataclass(frozen=True, slots=True)
class _AuthorizationFlow:
    state: str
    binding: str
    nonce: str
    verifier: str


def _database_url() -> str:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "Set AUDENTRA_TEST_DATABASE_URL or TEST_DATABASE_URL to run the student OIDC "
            "PostgreSQL integration test"
        )
    return database_url


def _fixture(*, suffix: str, email: str, phone_suffix: int) -> _StudentFixture:
    return _StudentFixture(
        tenant_id=uuid4(),
        tenant_slug=f"oidc-{suffix}-{uuid4().hex}",
        person_id=uuid4(),
        student_id=uuid4(),
        account_id=uuid4(),
        email=email,
        phone=f"+1555{phone_suffix:07d}",
    )


def _google_configuration() -> OidcProviderConfig:
    return OidcProviderConfig(
        id="google",
        label="Google",
        client_id=_GOOGLE_CLIENT_ID,
        client_secret=_GOOGLE_CLIENT_SECRET,
        issuer=_GOOGLE_ISSUER,
        authorization_endpoint="https://identity.test/google/authorize",
        token_endpoint="https://identity.test/google/token",  # noqa: S106
        jwks_uri="https://identity.test/google/jwks",
    )


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _one(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name)
    assert values is not None and len(values) == 1
    return values[0]


def _signed_google_token(
    signing_key: rsa.RSAPrivateKey,
    *,
    subject: str,
    email: str,
    nonce: str,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": _GOOGLE_ISSUER,
            "aud": _GOOGLE_CLIENT_ID,
            "sub": subject,
            "nonce": nonce,
            "email": email,
            "email_verified": True,
            "iat": now,
            "exp": now + 300,
        },
        signing_key,
        algorithm="RS256",
        headers={"kid": _SIGNING_KEY_ID},
    )


async def _insert_fixture(engine: AsyncEngine, fixture: _StudentFixture) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO tenant (id, name, slug, status)
                VALUES (:tenant_id, :name, :slug, 'active')
                """
            ),
            {
                "tenant_id": fixture.tenant_id,
                "name": f"OIDC integration {fixture.tenant_slug}",
                "slug": fixture.tenant_slug,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO person (id, tenant_id, first_name, last_name)
                VALUES (:person_id, :tenant_id, 'OIDC', 'Student')
                """
            ),
            {"person_id": fixture.person_id, "tenant_id": fixture.tenant_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO student (id, tenant_id, person_id, class_year)
                VALUES (:student_id, :tenant_id, :person_id, 2030)
                """
            ),
            {
                "student_id": fixture.student_id,
                "tenant_id": fixture.tenant_id,
                "person_id": fixture.person_id,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO student_onboarding (
                  tenant_id, student_id, status, current_step, completed_steps,
                  payload, version
                ) VALUES (
                  :tenant_id, :student_id, 'in_progress', 'offer', '{}', '{}'::jsonb, 1
                )
                """
            ),
            {"tenant_id": fixture.tenant_id, "student_id": fixture.student_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO student_profile (
                  tenant_id, student_id, preferred_name, communication_preference, version
                ) VALUES (:tenant_id, :student_id, 'OIDC', 'email', 1)
                """
            ),
            {"tenant_id": fixture.tenant_id, "student_id": fixture.student_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO credential_account (
                  id, tenant_id, student_id, email_normalized, phone_e164,
                  password_hash, password_algorithm, email_verified_at, status
                ) VALUES (
                  :account_id, :tenant_id, :student_id, :email, :phone,
                  'integration-unused-password', 'scrypt-v1', NOW(), 'active'
                )
                """
            ),
            {
                "account_id": fixture.account_id,
                "tenant_id": fixture.tenant_id,
                "student_id": fixture.student_id,
                "email": fixture.email,
                "phone": fixture.phone,
            },
        )


async def _inspect_authorization_start(
    engine: AsyncEngine,
    fixture: _StudentFixture,
    start: OidcAuthorizationStart,
) -> _AuthorizationFlow:
    query = parse_qs(urlsplit(start.authorization_url).query)
    state = _one(query, "state")
    nonce = _one(query, "nonce")
    challenge = _one(query, "code_challenge")

    assert _one(query, "code_challenge_method") == "S256"
    assert _one(query, "scope") == "openid email profile"
    assert _one(query, "redirect_uri") == ("https://portal.test/v1/auth/sso/google/callback")
    assert start.binding_token

    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT tenant_id, provider, state_hash, binding_hash, nonce_hash,
                       code_verifier, return_to, consumed_at
                FROM oidc_authorization_transaction
                WHERE state_hash=:state_hash
                """
            ),
            {"state_hash": _digest(state)},
        )
        row = cast(Mapping[str, Any], result.mappings().one())

    verifier = str(row["code_verifier"])
    assert row["tenant_id"] == fixture.tenant_id
    assert row["provider"] == "google"
    assert secrets.compare_digest(str(row["state_hash"]), _digest(state))
    assert secrets.compare_digest(str(row["binding_hash"]), _digest(start.binding_token))
    assert secrets.compare_digest(str(row["nonce_hash"]), _digest(nonce))
    assert secrets.compare_digest(
        challenge,
        _base64url(hashlib.sha256(verifier.encode("ascii")).digest()),
    )
    assert row["return_to"] == "/dashboard"
    assert row["consumed_at"] is None
    assert state not in {str(row["state_hash"]), str(row["binding_hash"]), str(row["nonce_hash"])}
    assert start.binding_token not in {
        str(row["state_hash"]),
        str(row["binding_hash"]),
        str(row["nonce_hash"]),
    }
    assert nonce not in {str(row["state_hash"]), str(row["binding_hash"]), str(row["nonce_hash"])}
    return _AuthorizationFlow(
        state=state,
        binding=start.binding_token,
        nonce=nonce,
        verifier=verifier,
    )


async def _assert_login_persistence(
    engine: AsyncEngine,
    fixture: _StudentFixture,
    *,
    subject: str,
    login: OidcLogin,
) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                """
                SELECT identity.tenant_id, identity.credential_account_id,
                       identity.provider, identity.issuer, identity.subject,
                       identity.email_normalized, session.token_hash,
                       session.authentication_method, session.identity_provider
                FROM federated_identity identity
                JOIN auth_session session
                  ON session.account_id=identity.credential_account_id
                WHERE identity.tenant_id=:tenant_id
                  AND identity.credential_account_id=:account_id
                  AND session.revoked_at IS NULL
                """
            ),
            {"tenant_id": fixture.tenant_id, "account_id": fixture.account_id},
        )
        row = cast(Mapping[str, Any], result.mappings().one())

    stored_hash = str(row["token_hash"])
    assert row["tenant_id"] == fixture.tenant_id
    assert row["credential_account_id"] == fixture.account_id
    assert row["provider"] == "google"
    assert row["issuer"] == _GOOGLE_ISSUER
    assert row["subject"] == subject
    assert row["email_normalized"] == fixture.email
    assert len(stored_hash) == 64
    assert all(character in "0123456789abcdef" for character in stored_hash)
    assert secrets.compare_digest(stored_hash, _digest(login.session_token))
    assert login.session_token.count(".") == 0
    assert row["authentication_method"] == "oidc"
    assert row["identity_provider"] == "google"


async def _cleanup(engine: AsyncEngine, fixtures: tuple[_StudentFixture, ...]) -> None:
    for fixture in fixtures:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM oidc_authorization_transaction WHERE tenant_id=:tenant_id"),
                {"tenant_id": fixture.tenant_id},
            )
            await connection.execute(
                text("DELETE FROM credential_account WHERE id=:account_id"),
                {"account_id": fixture.account_id},
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM student_profile
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                    """
                ),
                {"tenant_id": fixture.tenant_id, "student_id": fixture.student_id},
            )
            await connection.execute(
                text(
                    """
                    DELETE FROM student_onboarding
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                    """
                ),
                {"tenant_id": fixture.tenant_id, "student_id": fixture.student_id},
            )
            await connection.execute(
                text("DELETE FROM student WHERE id=:student_id AND tenant_id=:tenant_id"),
                {"student_id": fixture.student_id, "tenant_id": fixture.tenant_id},
            )
            await connection.execute(
                text("DELETE FROM person WHERE id=:person_id AND tenant_id=:tenant_id"),
                {"person_id": fixture.person_id, "tenant_id": fixture.tenant_id},
            )
            await connection.execute(
                text("DELETE FROM tenant WHERE id=:tenant_id"),
                {"tenant_id": fixture.tenant_id},
            )


def test_real_postgres_student_oidc_login_is_tenant_scoped_and_replay_safe() -> None:
    database_url = _database_url()
    unique = uuid4().hex
    shared_email = f"student-oidc-{unique}@example.edu"
    first = _fixture(suffix="first", email=shared_email, phone_suffix=int(unique[:7], 16) % 10**7)
    second = _fixture(
        suffix="second",
        email=shared_email,
        phone_suffix=int(unique[7:14], 16) % 10**7,
    )
    fixtures = (first, second)
    shared_subject = f"google-subject-{uuid4()}"

    async def scenario() -> None:
        await run_migrations(database_url, resolve_migrations_directory())
        engine = create_database_engine(database_url)
        signing_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
        jwk = RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
        jwk.update({"kid": _SIGNING_KEY_ID, "use": "sig", "alg": "RS256"})
        encoded_tokens: dict[str, str] = {}
        expected_verifiers: dict[str, str] = {}
        token_exchange_count = 0
        configuration = _google_configuration()

        def identity_provider(request: httpx.Request) -> httpx.Response:
            nonlocal token_exchange_count
            if request.url == httpx.URL(configuration.jwks_uri):
                return httpx.Response(200, json={"keys": [jwk]})
            if request.url == httpx.URL(configuration.token_endpoint):
                form = parse_qs(request.content.decode("ascii"))
                code = _one(form, "code")
                verifier = _one(form, "code_verifier")
                if code not in encoded_tokens or expected_verifiers.get(code) != verifier:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                token_exchange_count += 1
                return httpx.Response(200, json={"id_token": encoded_tokens[code]})
            return httpx.Response(404)

        http = httpx.AsyncClient(transport=httpx.MockTransport(identity_provider))
        service = PostgresOidcAuth(
            engine,
            http,
            OidcSettings(
                callback_base_url="https://portal.test",
                portal_base_url="https://portal.test",
                providers=(configuration,),
            ),
        )
        resolver = PostgresDevelopmentAuth(
            engine,
            environment="test",
            staff_invitation_code="",
            development_flows_enabled=False,
        )

        try:
            for fixture in fixtures:
                await _insert_fixture(engine, fixture)

            first_start = await service.start(
                provider="google",
                tenant_id=str(first.tenant_id),
                return_to="/dashboard",
            )
            first_flow = await _inspect_authorization_start(engine, first, first_start)
            first_code = "first-authorization-code"
            encoded_tokens[first_code] = _signed_google_token(
                signing_key,
                subject=shared_subject,
                email=shared_email,
                nonce=first_flow.nonce,
            )
            expected_verifiers[first_code] = first_flow.verifier

            results = await asyncio.gather(
                service.complete(
                    provider="google",
                    state=first_flow.state,
                    code=first_code,
                    provider_error=None,
                    binding_token=first_flow.binding,
                ),
                service.complete(
                    provider="google",
                    state=first_flow.state,
                    code=first_code,
                    provider_error=None,
                    binding_token=first_flow.binding,
                ),
                return_exceptions=True,
            )
            login_count = sum(isinstance(result, OidcLogin) for result in results)
            replay_codes = [result.code for result in results if isinstance(result, OidcFlowError)]
            unexpected_count = sum(
                not isinstance(result, (OidcLogin, OidcFlowError)) for result in results
            )
            assert login_count == 1
            assert replay_codes == ["invalid_request"]
            assert unexpected_count == 0
            assert token_exchange_count == 1
            first_login = next(result for result in results if isinstance(result, OidcLogin))
            assert first_login.return_to == "/dashboard"
            assert first_login.provider == "google"

            await _assert_login_persistence(
                engine,
                first,
                subject=shared_subject,
                login=first_login,
            )
            first_session = await resolver.resolve_student(
                first_login.session_token,
                str(first.tenant_id),
                first.tenant_slug,
            )
            assert first_session is not None
            assert first_session.context.tenant_id == str(first.tenant_id)
            assert first_session.context.student_id == str(first.student_id)
            assert first_session.context.actor_id == str(first.person_id)
            assert first_session.context.authentication_method == "oidc"
            assert first_session.context.identity_provider == "google"
            assert (
                await resolver.resolve_student(
                    first_login.session_token,
                    str(second.tenant_id),
                    second.tenant_slug,
                )
                is None
            )

            await resolver.sign_out_student(first_login.session_token)
            assert (
                await resolver.resolve_student(
                    first_login.session_token,
                    str(first.tenant_id),
                    first.tenant_slug,
                )
                is None
            )
            async with engine.connect() as connection:
                revoked_at = await connection.scalar(
                    text(
                        """
                        SELECT revoked_at FROM auth_session
                        WHERE account_id=:account_id AND token_hash=:token_hash
                        """
                    ),
                    {
                        "account_id": first.account_id,
                        "token_hash": _digest(first_login.session_token),
                    },
                )
            assert revoked_at is not None

            second_start = await service.start(
                provider="google",
                tenant_id=str(second.tenant_id),
                return_to="/dashboard",
            )
            second_flow = await _inspect_authorization_start(engine, second, second_start)
            second_code = "second-authorization-code"
            encoded_tokens[second_code] = _signed_google_token(
                signing_key,
                subject=shared_subject,
                email=shared_email,
                nonce=second_flow.nonce,
            )
            expected_verifiers[second_code] = second_flow.verifier
            second_login = await service.complete(
                provider="google",
                state=second_flow.state,
                code=second_code,
                provider_error=None,
                binding_token=second_flow.binding,
            )
            assert token_exchange_count == 2
            await _assert_login_persistence(
                engine,
                second,
                subject=shared_subject,
                login=second_login,
            )

            async with engine.connect() as connection:
                result = await connection.execute(
                    text(
                        """
                        SELECT tenant_id, credential_account_id
                        FROM federated_identity
                        WHERE provider='google' AND issuer=:issuer AND subject=:subject
                        """
                    ),
                    {"issuer": _GOOGLE_ISSUER, "subject": shared_subject},
                )
                identity_rows = result.all()
            tenant_accounts = {
                (cast(UUID, row.tenant_id), cast(UUID, row.credential_account_id))
                for row in identity_rows
            }
            assert tenant_accounts == {
                (first.tenant_id, first.account_id),
                (second.tenant_id, second.account_id),
            }

            second_session = await resolver.resolve_student(
                second_login.session_token,
                str(second.tenant_id),
                second.tenant_slug,
            )
            assert second_session is not None
            assert second_session.context.authentication_method == "oidc"
            assert second_session.context.identity_provider == "google"
            await resolver.sign_out_student(second_login.session_token)
        finally:
            try:
                await http.aclose()
            finally:
                try:
                    await _cleanup(engine, fixtures)
                finally:
                    await engine.dispose()

    asyncio.run(scenario())
