from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Mapping
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.core.oidc import (
    OidcAuthorizationStart,
    OidcAuthService,
    OidcErrorCode,
    OidcFlowError,
    OidcLogin,
    OidcProviderSummary,
)
from audentra.core.ports import (
    CredentialStudentSession,
    ServiceCall,
    StaffSession,
    UnavailableBrowserAuthService,
)
from audentra.infrastructure.postgres import oidc_repository
from audentra.infrastructure.postgres.auth_repository import PostgresDevelopmentAuth
from audentra.infrastructure.postgres.oidc_repository import (
    OidcProviderConfig,
    OidcSettings,
    PostgresOidcAuth,
)
from audentra.interfaces.http.app import create_app
from audentra.interfaces.http.config import HttpSettings

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
SESSION_TOKEN = "opaque-student-session-token-from-oidc"  # noqa: S105
OLD_STAFF_SESSION_TOKEN = "old-local-staff-session"  # noqa: S105
FEDERATED_STAFF_SESSION_TOKEN = "federated-staff-session-token"  # noqa: S105
BINDING_COOKIE = "browser-binding-value-long-enough-for-oidc"
SECURE_BINDING_COOKIE_NAME = "__Host-vv_oidc_binding"

pytestmark = pytest.mark.anyio


class FakePlatformService:
    def __init__(self) -> None:
        self.calls: list[ServiceCall] = []

    async def dispatch(self, call: ServiceCall) -> object:
        self.calls.append(call)
        if call.operation == "public.get_tenant_bootstrap":
            return {"tenantId": TENANT_ID, "slug": "aster"}
        return {"operation": call.operation}


class SessionBrowserAuth(UnavailableBrowserAuthService):
    def __init__(self) -> None:
        self.resolved_tokens: list[str] = []
        self.signed_out_tokens: list[str | None] = []

    async def sign_out_student(self, token: str | None) -> None:
        self.signed_out_tokens.append(token)

    async def resolve_student(
        self,
        token: str,
        tenant_id: str,
        tenant_slug: str | None,
    ) -> CredentialStudentSession | None:
        self.resolved_tokens.append(token)
        if token != SESSION_TOKEN or tenant_id != TENANT_ID or tenant_slug != "aster":
            return None
        return CredentialStudentSession(
            context=AuthContext(
                tenant_id=TENANT_ID,
                student_id=STUDENT_ID,
                actor_id=STUDENT_ID,
                actor_type="student",
                authentication_method="oidc",
                identity_provider="microsoft",
                tenant_slug="aster",
            ),
            preferred_name="Alex",
            email="alex@example.edu",
            phone="+15551230001",
            email_verified=True,
            phone_verified=False,
        )

    async def resolve_staff(
        self,
        token: str,
        tenant_id: str,
        tenant_slug: str | None,
    ) -> StaffSession | None:
        if tenant_id != TENANT_ID or tenant_slug != "aster":
            return None
        authentication_method: Literal["credentials", "oidc"]
        identity_provider: Literal["microsoft"] | None
        if token == OLD_STAFF_SESSION_TOKEN:
            authentication_method = "credentials"
            identity_provider = None
        elif token == FEDERATED_STAFF_SESSION_TOKEN:
            authentication_method = "oidc"
            identity_provider = "microsoft"
        else:
            return None
        return StaffSession(
            context=AuthContext(
                tenant_id=TENANT_ID,
                student_id=STUDENT_ID,
                actor_id="00000000-0000-7000-8000-000000000201",
                actor_type="staff",
                authentication_method=authentication_method,
                identity_provider=identity_provider,
                tenant_slug="aster",
            ),
            name="Morgan Staff",
            email="morgan.staff@example.edu",
            component="enrollment",
        )


class CredentialSessionBrowserAuth(SessionBrowserAuth):
    async def resolve_student(
        self,
        token: str,
        tenant_id: str,
        tenant_slug: str | None,
    ) -> CredentialStudentSession | None:
        session = await super().resolve_student(token, tenant_id, tenant_slug)
        if session is None:
            return None
        return CredentialStudentSession(
            context=AuthContext(
                tenant_id=TENANT_ID,
                student_id=STUDENT_ID,
                actor_id=STUDENT_ID,
                actor_type="student",
                authentication_method="credentials",
                tenant_slug="aster",
            ),
            preferred_name=session.preferred_name,
            email=session.email,
            phone=session.phone,
            email_verified=session.email_verified,
            phone_verified=session.phone_verified,
        )


class FakeOidcAuth:
    def __init__(self, *, complete_error: Exception | None = None) -> None:
        self.complete_error = complete_error
        self.start_calls: list[dict[str, str]] = []
        self.complete_calls: list[dict[str, str | None]] = []

    def providers(self) -> tuple[OidcProviderSummary, ...]:
        return (
            OidcProviderSummary(id="google", label="Google"),
            OidcProviderSummary(id="microsoft", label="Microsoft"),
        )

    async def start(
        self,
        *,
        provider: str,
        tenant_id: str,
        return_to: str,
    ) -> OidcAuthorizationStart:
        self.start_calls.append(
            {"provider": provider, "tenant_id": tenant_id, "return_to": return_to}
        )
        return OidcAuthorizationStart(
            authorization_url=(
                "https://identity.example/authorize"
                "?state=provider-state&code_challenge=provider-pkce-challenge"
                "&code_challenge_method=S256"
            ),
            binding_token=BINDING_COOKIE,
            expires_at_epoch=4_102_444_800,
        )

    async def complete(
        self,
        *,
        provider: str,
        state: str | None,
        code: str | None,
        provider_error: str | None,
        binding_token: str | None,
    ) -> OidcLogin:
        self.complete_calls.append(
            {
                "provider": provider,
                "state": state,
                "code": code,
                "provider_error": provider_error,
                "binding_token": binding_token,
            }
        )
        if self.complete_error is not None:
            raise self.complete_error
        return OidcLogin(
            session_token=SESSION_TOKEN,
            expires_at_epoch=4_102_444_800,
            provider="microsoft",
            return_to="/dashboard",
        )


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    async def execute(
        self,
        statement: object,
        parameters: Mapping[str, Any] | None = None,
    ) -> object:
        self.calls.append((str(statement), parameters))
        return object()


class BeginTransaction:
    def __init__(self, connection: RecordingConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> RecordingConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        return None


class RecordingEngine:
    def __init__(self) -> None:
        self.connection = RecordingConnection()

    def begin(self) -> BeginTransaction:
        return BeginTransaction(self.connection)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _app(
    oidc: OidcAuthService,
    *,
    auth_mode: str = "oidc",
    environment: str = "production",
    platform: FakePlatformService | None = None,
    browser_auth: SessionBrowserAuth | None = None,
) -> FastAPI:
    return create_app(
        service=platform or FakePlatformService(),
        auth_service=browser_auth or SessionBrowserAuth(),
        oidc_auth_service=oidc,
        settings=HttpSettings(
            environment=cast(Any, environment),
            auth_mode=cast(Any, auth_mode),
            browser_auth_required=True,
            oidc_tenant_id=TENANT_ID if auth_mode == "oidc" else None,
            oidc_portal_base_url="https://portal.example",
        ),
    )


def _provider_configuration(provider: str = "google") -> OidcProviderConfig:
    if provider == "microsoft":
        entra_tenant = "11111111-1111-4111-8111-111111111111"
        authority = f"https://login.microsoftonline.com/{entra_tenant}"
        return OidcProviderConfig(
            id="microsoft",
            label="Microsoft",
            client_id="microsoft-student-client",
            client_secret="test-microsoft-client-secret",  # noqa: S106
            issuer=f"{authority}/v2.0",
            authorization_endpoint=f"{authority}/oauth2/v2.0/authorize",
            token_endpoint=f"{authority}/oauth2/v2.0/token",
            jwks_uri=f"{authority}/discovery/v2.0/keys",
            tenant_id=entra_tenant,
        )
    return OidcProviderConfig(
        id="google",
        label="Google",
        client_id="google-student-client",
        client_secret="test-google-client-secret",  # noqa: S106
        issuer="https://accounts.google.com",
        authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",  # noqa: S106
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
    )


async def _exchange_signed_claims(
    configuration: OidcProviderConfig,
    claims: Mapping[str, Any],
    *,
    signing_key: rsa.RSAPrivateKey | None = None,
    published_key: rsa.RSAPrivateKey | None = None,
    algorithm: str = "RS256",
) -> Mapping[str, Any]:
    signing_key = signing_key or rsa.generate_private_key(
        public_exponent=65_537,
        key_size=2048,
    )
    published_key = published_key or signing_key
    key_id = "student-oidc-test-key"
    encoded = jwt.encode(
        dict(claims),
        signing_key
        if algorithm == "RS256"
        else "test-hmac-secret-that-is-more-than-thirty-two-bytes",
        algorithm=algorithm,
        headers={"kid": key_id},
    )
    jwk = RSAAlgorithm.to_jwk(published_key.public_key(), as_dict=True)
    jwk.update({"kid": key_id, "use": "sig", "alg": "RS256"})

    def identity_provider(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(configuration.token_endpoint):
            return httpx.Response(200, json={"id_token": encoded})
        if request.url == httpx.URL(configuration.jwks_uri):
            return httpx.Response(200, json={"keys": [jwk]})
        raise AssertionError(f"unexpected identity-provider request: {request.url}")

    engine = cast(AsyncEngine, RecordingEngine())
    async with httpx.AsyncClient(transport=httpx.MockTransport(identity_provider)) as http:
        service = PostgresOidcAuth(
            engine,
            http,
            OidcSettings(
                callback_base_url="https://portal.example",
                portal_base_url="https://portal.example",
                providers=(configuration,),
            ),
        )
        return await service._exchange_and_validate(
            configuration,
            "authorization-code-value",
            "v" * 86,
            oidc_repository._digest("expected-nonce"),
        )


async def test_provider_discovery_has_exact_order_and_disables_password_in_oidc_mode() -> None:
    app = _app(FakeOidcAuth())

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        response = await client.get("/v1/auth/sso/providers")

    assert response.status_code == 200
    assert response.json() == {
        "providers": [
            {"id": "google", "label": "Google"},
            {"id": "microsoft", "label": "Microsoft"},
        ],
        "passwordEnabled": False,
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_provider_discovery_preserves_password_in_demo_mode() -> None:
    oidc = FakeOidcAuth()
    app = _app(oidc, auth_mode="demo")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        discovery = await client.get("/v1/auth/sso/providers")
        start = await client.get("/v1/auth/sso/google/start")

    assert discovery.status_code == 200
    assert discovery.json() == {"providers": [], "passwordEnabled": True}
    assert start.status_code == 303
    assert start.headers["location"] == "https://portal.example/sign-in?sso_error=invalid_request"
    assert oidc.start_calls == []


async def test_start_redirects_and_sets_a_scoped_http_only_binding_cookie() -> None:
    oidc = FakeOidcAuth()
    platform = FakePlatformService()
    app = _app(oidc, platform=platform)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/v1/auth/sso/google/start",
            params={"returnTo": "/dashboard"},
        )

    assert response.status_code == 307
    assert response.headers["location"] == (
        "https://identity.example/authorize"
        "?state=provider-state&code_challenge=provider-pkce-challenge"
        "&code_challenge_method=S256"
    )
    assert oidc.start_calls == [
        {
            "provider": "google",
            "tenant_id": TENANT_ID,
            "return_to": "/dashboard",
        }
    ]
    binding_cookie = response.headers["set-cookie"]
    assert f"{SECURE_BINDING_COOKIE_NAME}={BINDING_COOKIE}" in binding_cookie
    assert "Path=/" in binding_cookie
    assert "HttpOnly" in binding_cookie
    assert "Secure" in binding_cookie
    assert "SameSite=lax" in binding_cookie


async def test_postgres_start_persists_hashes_and_emits_state_nonce_and_pkce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = ("s" * 43, "b" * 43, "n" * 43, "v" * 86)
    token_values = iter(issued)
    monkeypatch.setattr(
        secrets,
        "token_urlsafe",
        lambda _bytes: next(token_values),
    )
    engine = RecordingEngine()
    provider = OidcProviderConfig(
        id="google",
        label="Google",
        client_id="student-google-client",
        client_secret="not-a-real-provider-secret",  # noqa: S106
        issuer="https://accounts.example",
        authorization_endpoint="https://accounts.example/authorize",
        token_endpoint="https://accounts.example/token",  # noqa: S106
        jwks_uri="https://accounts.example/keys",
    )

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"start unexpectedly called {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as http:
        service = PostgresOidcAuth(
            cast(AsyncEngine, engine),
            http,
            OidcSettings(
                callback_base_url="https://portal.example",
                portal_base_url="https://portal.example",
                providers=(provider,),
            ),
        )
        start = await service.start(
            provider="google",
            tenant_id=TENANT_ID,
            return_to="/dashboard",
        )

    query = parse_qs(urlsplit(start.authorization_url).query)
    expected_challenge = oidc_repository._base64url(
        hashlib.sha256(issued[3].encode("ascii")).digest()
    )
    assert query == {
        "client_id": ["student-google-client"],
        "redirect_uri": ["https://portal.example/v1/auth/sso/google/callback"],
        "response_type": ["code"],
        "scope": ["openid email profile"],
        "state": [issued[0]],
        "nonce": [issued[2]],
        "code_challenge": [expected_challenge],
        "code_challenge_method": ["S256"],
        "prompt": ["select_account"],
    }
    assert start.binding_token == issued[1]

    assert len(engine.connection.calls) == 2
    assert "DELETE FROM oidc_authorization_transaction" in engine.connection.calls[0][0]
    persisted = engine.connection.calls[1][1]
    assert persisted is not None
    assert str(persisted["tenant_id"]) == TENANT_ID
    assert persisted["provider"] == "google"
    assert persisted["state_hash"] == hashlib.sha256(issued[0].encode()).hexdigest()
    assert persisted["binding_hash"] == hashlib.sha256(issued[1].encode()).hexdigest()
    assert persisted["nonce_hash"] == hashlib.sha256(issued[2].encode()).hexdigest()
    assert persisted["code_verifier"] == issued[3]
    assert persisted["return_to"] == "/dashboard"
    assert issued[0] not in persisted.values()
    assert issued[1] not in persisted.values()
    assert issued[2] not in persisted.values()


@pytest.mark.parametrize(
    "safe_code",
    ("access_denied", "invalid_request", "account_not_linked", "provider_error"),
)
async def test_callback_maps_flow_failures_to_the_safe_error_vocabulary(
    safe_code: OidcErrorCode,
) -> None:
    oidc = FakeOidcAuth(complete_error=OidcFlowError(safe_code))
    app = _app(oidc)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/v1/auth/sso/google/callback",
            params={"state": "provider-state-value", "code": "provider-code-value"},
            headers={"Cookie": f"{SECURE_BINDING_COOKIE_NAME}={BINDING_COOKIE}"},
        )

    assert response.status_code == 303
    assert response.headers["location"] == (f"https://portal.example/sign-in?sso_error={safe_code}")
    assert oidc.complete_calls == [
        {
            "provider": "google",
            "state": "provider-state-value",
            "code": "provider-code-value",
            "provider_error": None,
            "binding_token": BINDING_COOKIE,
        }
    ]
    cookies = response.headers.get_list("set-cookie")
    assert not any(cookie.startswith("vv_session=") for cookie in cookies)
    assert any(
        cookie.startswith(f"{SECURE_BINDING_COOKIE_NAME}=consumed")
        and "Max-Age=0" in cookie
        and "Path=/" in cookie
        for cookie in cookies
    )


async def test_callback_hides_unexpected_provider_failures() -> None:
    oidc = FakeOidcAuth(complete_error=RuntimeError("raw-provider-response-secret"))
    app = _app(oidc)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/v1/auth/sso/microsoft/callback",
            params={"state": "provider-state-value", "code": "provider-code-value"},
            headers={"Cookie": f"{SECURE_BINDING_COOKIE_NAME}={BINDING_COOKIE}"},
        )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://portal.example/sign-in?sso_error=provider_error"
    )
    assert "raw-provider-response-secret" not in response.text


async def test_successful_callback_issues_an_oidc_session_and_authenticates_student_routes() -> (
    None
):
    oidc = FakeOidcAuth()
    platform = FakePlatformService()
    browser_auth = SessionBrowserAuth()
    app = _app(oidc, platform=platform, browser_auth=browser_auth)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/v1/auth/sso/microsoft/callback",
            params={"state": "provider-state-value", "code": "provider-code-value"},
            headers={
                "Cookie": (
                    f"{SECURE_BINDING_COOKIE_NAME}={BINDING_COOKIE}; "
                    "vv_demo_session=earlier-development-session; "
                    "vv_session=earlier-opaque-student-session"
                )
            },
        )
        dashboard = await client.get("/v1/student/dashboard")

    assert response.status_code == 303
    assert response.headers["location"] == "https://portal.example/dashboard"
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(cookie for cookie in cookies if cookie.startswith("vv_session="))
    assert f"vv_session={SESSION_TOKEN}" in session_cookie
    assert "Path=/" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie
    assert "SameSite=lax" in session_cookie
    assert any(
        cookie.startswith("vv_demo_session=signed-out") and "Max-Age=0" in cookie
        for cookie in cookies
    )
    assert any(
        cookie.startswith(f"{SECURE_BINDING_COOKIE_NAME}=consumed")
        and "Max-Age=0" in cookie
        and "Path=/" in cookie
        for cookie in cookies
    )

    assert dashboard.status_code == 200
    assert browser_auth.signed_out_tokens == ["earlier-opaque-student-session"]
    assert browser_auth.resolved_tokens == [SESSION_TOKEN]
    dashboard_call = next(
        call for call in platform.calls if call.operation == "student.get_dashboard"
    )
    assert dashboard_call.auth is not None
    assert dashboard_call.auth.authentication_method == "oidc"
    assert dashboard_call.auth.identity_provider == "microsoft"
    assert oidc.complete_calls == [
        {
            "provider": "microsoft",
            "state": "provider-state-value",
            "code": "provider-code-value",
            "provider_error": None,
            "binding_token": BINDING_COOKIE,
        }
    ]


async def test_signed_google_id_token_validates_and_canonicalizes_identity() -> None:
    configuration = _provider_configuration("google")
    now = int(time.time())
    claims = {
        "iss": "accounts.google.com",
        "aud": configuration.client_id,
        "sub": "google-stable-subject",
        "email": "Student@Example.edu",
        "email_verified": True,
        "nonce": "expected-nonce",
        "iat": now,
        "exp": now + 300,
    }

    validated = await _exchange_signed_claims(configuration, claims)

    assert oidc_repository._validated_issuer(configuration, validated) == (
        "https://accounts.google.com"
    )
    assert oidc_repository._provider_identity(configuration, validated) == (
        "google-stable-subject",
        "student@example.edu",
    )


@pytest.mark.parametrize(
    "claim_updates",
    (
        {"aud": "another-client"},
        {"nonce": "wrong-nonce"},
        {"exp": 1},
        {"azp": "another-client"},
    ),
)
async def test_google_id_token_rejects_invalid_security_claims(
    claim_updates: Mapping[str, Any],
) -> None:
    configuration = _provider_configuration("google")
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": configuration.issuer,
        "aud": configuration.client_id,
        "sub": "google-stable-subject",
        "email": "student@example.edu",
        "email_verified": True,
        "nonce": "expected-nonce",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(claim_updates)

    with pytest.raises(OidcFlowError, match="provider_error"):
        await _exchange_signed_claims(configuration, claims)


async def test_id_token_rejects_wrong_signature_and_algorithm() -> None:
    configuration = _provider_configuration("google")
    now = int(time.time())
    claims = {
        "iss": configuration.issuer,
        "aud": configuration.client_id,
        "sub": "google-stable-subject",
        "nonce": "expected-nonce",
        "iat": now,
        "exp": now + 300,
    }

    signing_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    other_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    with pytest.raises(OidcFlowError, match="provider_error"):
        await _exchange_signed_claims(
            configuration,
            claims,
            signing_key=signing_key,
            published_key=other_key,
        )
    with pytest.raises(OidcFlowError, match="provider_error"):
        await _exchange_signed_claims(configuration, claims, algorithm="HS256")


def test_provider_specific_identity_rules_require_google_verification_and_entra_tid_oid() -> None:
    google = _provider_configuration("google")
    with pytest.raises(OidcFlowError, match="account_not_linked"):
        oidc_repository._provider_identity(
            google,
            {
                "sub": "google-stable-subject",
                "email": "student@example.edu",
                "email_verified": False,
            },
        )

    microsoft = _provider_configuration("microsoft")
    object_id = str(uuid4())
    assert oidc_repository._provider_identity(
        microsoft,
        {
            "sub": "pairwise-oidc-subject",
            "tid": microsoft.tenant_id,
            "oid": object_id,
            "preferred_username": "mutable@example.edu",
        },
    ) == (object_id, None)
    with pytest.raises(OidcFlowError, match="provider_error"):
        oidc_repository._provider_identity(
            microsoft,
            {
                "sub": "pairwise-oidc-subject",
                "tid": str(uuid4()),
                "oid": object_id,
            },
        )


@pytest.mark.parametrize(
    "unsafe_return_to",
    (
        "//attacker.example",
        "https://attacker.example/dashboard",
        "/dashboard?next=https://attacker.example",
        "/dashboard%5c%5cattacker.example",
        "/onboarding",
        "/dashboard#fragment",
    ),
)
def test_return_path_is_fixed_to_the_student_dashboard(unsafe_return_to: str) -> None:
    with pytest.raises(OidcFlowError, match="invalid_request"):
        oidc_repository._validated_return_to(unsafe_return_to)


async def test_oidc_mode_rejects_known_demo_cookie_and_demo_tenant_header() -> None:
    oidc = FakeOidcAuth()
    platform = FakePlatformService()
    app = _app(oidc, platform=platform)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        unauthorized = await client.get(
            "/v1/student/dashboard",
            headers={"Cookie": "vv_demo_session=demo-session-v2"},
        )
        start = await client.get(
            "/v1/auth/sso/google/start",
            headers={"X-Demo-Tenant-Id": "22222222-2222-4222-8222-222222222222"},
        )

    assert unauthorized.status_code == 401
    assert start.status_code == 307
    assert oidc.start_calls[0]["tenant_id"] == TENANT_ID


async def test_oidc_mode_disables_credentials_but_keeps_session_logout() -> None:
    browser_auth = SessionBrowserAuth()
    app = _app(
        FakeOidcAuth(),
        browser_auth=browser_auth,
        environment="preview",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        password_sign_in = await client.post(
            "/v1/auth/sign-in",
            json={"email": "student@example.edu", "password": "not-a-real-password"},
        )
        sign_out = await client.post(
            "/v1/auth/sign-out",
            headers={"Cookie": "vv_session=active-oidc-session-token"},
        )

    assert password_sign_in.status_code == 404
    assert sign_out.status_code == 200
    assert sign_out.json() == {"authenticated": False, "mode": "oidc"}
    assert browser_auth.signed_out_tokens == ["active-oidc-session-token"]


async def test_oidc_mode_rejects_old_credentials_but_allows_federated_staff_session() -> None:
    app = _app(
        FakeOidcAuth(),
        browser_auth=CredentialSessionBrowserAuth(),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        student = await client.get(
            "/v1/student/dashboard",
            headers={"Cookie": f"vv_session={SESSION_TOKEN}"},
        )
        staff = await client.get(
            "/v1/staff/workspace",
            headers={"Cookie": f"vv_staff_session={OLD_STAFF_SESSION_TOKEN}"},
        )
        federated_staff = await client.get(
            "/v1/staff/workspace",
            headers={"Cookie": f"vv_staff_session={FEDERATED_STAFF_SESSION_TOKEN}"},
        )

    assert student.status_code == 401
    assert staff.status_code == 401
    assert federated_staff.status_code == 200


async def test_development_auth_adapter_fails_closed_when_flows_are_disabled() -> None:
    auth = PostgresDevelopmentAuth(
        cast(AsyncEngine, RecordingEngine()),
        environment="production",
        staff_invitation_code="",
        development_flows_enabled=False,
    )

    with pytest.raises(ApiError, match="Development authentication is not available"):
        await auth.demo_student(TENANT_ID, "aster")


async def test_callback_oversized_values_still_use_safe_redirect_contract() -> None:
    oidc = FakeOidcAuth(complete_error=OidcFlowError("invalid_request"))
    app = _app(oidc)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        response = await client.get(
            "/v1/auth/sso/google/callback",
            params={"state": "s" * 10_000, "code": "c" * 10_000},
            headers={"Cookie": f"{SECURE_BINDING_COOKIE_NAME}={BINDING_COOKIE}"},
        )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://portal.example/sign-in?sso_error=invalid_request"
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
