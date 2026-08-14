from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from audentra.core.auth import AuthContext
from audentra.core.errors import NotFoundError, UnauthorizedError
from audentra.core.ports import (
    CredentialStudentSession,
    DemoStudentSession,
    ServiceCall,
    StaffSession,
)
from audentra.interfaces.http.app import create_app
from audentra.interfaces.http.config import HttpSettings

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
ACTOR_ID = "00000000-0000-7000-8000-000000000100"
STAFF_ID = "00000000-0000-7000-8000-000000000901"
STUDENT_TOKEN = "student-session-token-that-is-long-enough"  # noqa: S105
STAFF_TOKEN = "staff-session-token-that-is-long-enough"  # noqa: S105
WRONG_PASSWORD = "wrong-password"  # noqa: S105
STAFF_PASSWORD = "Individual-staff-password-2027"  # noqa: S105
STAFF_INVITATION_CODE = "institution-private-access-code-2027"

pytestmark = pytest.mark.anyio


class FakePlatformService:
    def __init__(self) -> None:
        self.calls: list[ServiceCall] = []

    async def dispatch(self, call: ServiceCall) -> object:
        self.calls.append(call)
        if call.operation == "public.get_tenant_bootstrap":
            tenant_ids = {"aster": TENANT_ID}
            requested_slug = call.path_params.get("slug")
            requested_id = call.path_params.get("tenantId")
            tenant_id = (
                tenant_ids.get(str(requested_slug))
                if requested_slug is not None
                else str(requested_id)
                if requested_id == TENANT_ID
                else None
            )
            if tenant_id is None:
                raise NotFoundError("TENANT_NOT_FOUND", "The tenant was not found")
            return {"tenantId": tenant_id, "slug": str(requested_slug or "aster")}
        return {"operation": call.operation}


class FakeBrowserAuthService:
    def __init__(self) -> None:
        self.reset_completed: bool | None = None
        self.sign_up_input: dict[str, str | None] | None = None
        self.student_revoked = False
        self.staff_revoked = False
        self.staff_sign_up_input: dict[str, str | None] | None = None

    def student_context(self, method: str = "credentials") -> AuthContext:
        return AuthContext(
            tenant_id=TENANT_ID,
            student_id=STUDENT_ID,
            actor_id=ACTOR_ID,
            actor_type="student",
            authentication_method=method,  # type: ignore[arg-type]
            tenant_slug="aster",
        )

    async def demo_student(self, tenant_id: str, tenant_slug: str | None) -> DemoStudentSession:
        assert tenant_id == TENANT_ID
        assert tenant_slug in {None, "aster"}
        return DemoStudentSession(self.student_context("demo"), "Alex")

    async def resolve_student(
        self, token: str, tenant_id: str, tenant_slug: str | None
    ) -> CredentialStudentSession | None:
        if self.student_revoked or token != STUDENT_TOKEN:
            return None
        return self._credential_session()

    async def sign_up_student(
        self,
        *,
        tenant_id: str,
        tenant_slug: str | None,
        email: str,
        phone: str,
        password: str,
    ) -> CredentialStudentSession:
        self.sign_up_input = {
            "tenant_id": tenant_id,
            "tenant_slug": tenant_slug,
            "email": email,
            "phone": phone,
            "password": password,
        }
        return self._credential_session(email=email, phone=phone)

    async def sign_in_student(
        self,
        *,
        tenant_id: str,
        tenant_slug: str | None,
        email: str,
        password: str,
    ) -> CredentialStudentSession:
        if password == WRONG_PASSWORD:
            raise UnauthorizedError("Email or password is incorrect")
        return self._credential_session(email=email)

    async def sign_out_student(self, token: str | None) -> None:
        assert token in {None, STUDENT_TOKEN}
        self.student_revoked = True

    async def resolve_staff(
        self, token: str, tenant_id: str, tenant_slug: str | None
    ) -> StaffSession | None:
        if self.staff_revoked or token != STAFF_TOKEN:
            return None
        return self._staff_session()

    async def sign_in_staff(
        self,
        *,
        tenant_id: str,
        tenant_slug: str | None,
        email: str,
        password: str,
    ) -> StaffSession:
        if password != STAFF_PASSWORD:
            raise UnauthorizedError("Email or password is incorrect")
        return self._staff_session(email=email)

    async def sign_up_staff(
        self,
        *,
        tenant_id: str,
        tenant_slug: str | None,
        email: str,
        password: str,
        institution_access_code: str,
    ) -> StaffSession:
        if institution_access_code != STAFF_INVITATION_CODE:
            raise UnauthorizedError("Staff account could not be created with these credentials")
        self.staff_sign_up_input = {
            "tenant_id": tenant_id,
            "tenant_slug": tenant_slug,
            "email": email,
            "password": password,
            "institution_access_code": institution_access_code,
        }
        return self._staff_session(email=email)

    async def sign_out_staff(self, token: str | None) -> None:
        assert token in {None, STAFF_TOKEN}
        self.staff_revoked = True

    async def reset_demo_fixture(self, *, completed_onboarding: bool) -> None:
        self.reset_completed = completed_onboarding

    def _credential_session(
        self,
        *,
        email: str = "student@example.com",
        phone: str = "+15551230001",
    ) -> CredentialStudentSession:
        return CredentialStudentSession(
            context=self.student_context(),
            preferred_name=None,
            email=email,
            phone=phone,
            email_verified=False,
            phone_verified=False,
            token=STUDENT_TOKEN,
            expires_at_epoch=4_102_444_800,
        )

    def _staff_session(self, *, email: str = "priya.shah@aster.example.edu") -> StaffSession:
        return StaffSession(
            context=AuthContext(
                tenant_id=TENANT_ID,
                student_id=STUDENT_ID,
                actor_id=STAFF_ID,
                actor_type="staff",
                authentication_method="credentials",
                tenant_slug="aster",
            ),
            name="Priya Shah",
            email=email,
            component="Admissions",
            token=STAFF_TOKEN,
            expires_at_epoch=4_102_444_800,
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def auth_service() -> FakeBrowserAuthService:
    return FakeBrowserAuthService()


@pytest.fixture
def platform_service() -> FakePlatformService:
    return FakePlatformService()


@pytest.fixture
async def client(
    auth_service: FakeBrowserAuthService,
    platform_service: FakePlatformService,
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        service=platform_service,
        auth_service=auth_service,
        settings=HttpSettings(browser_auth_required=True),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


async def test_demo_cookie_gates_protected_routes_and_sign_out_invalidates_it(
    client: AsyncClient,
    platform_service: FakePlatformService,
) -> None:
    denied = await client.get("/v1/student/bootstrap")
    signed_in = await client.post("/v1/auth/demo/sign-in", json={})
    accepted = await client.get("/v1/student/bootstrap")
    signed_out = await client.post("/v1/auth/demo/sign-out")
    denied_again = await client.get("/v1/student/bootstrap")

    assert denied.status_code == 401
    assert signed_in.status_code == 200
    assert "vv_demo_session=demo-session-v2" in signed_in.headers["set-cookie"]
    assert "HttpOnly" in signed_in.headers["set-cookie"]
    assert "SameSite=lax" in signed_in.headers["set-cookie"]
    assert "Secure" not in signed_in.headers["set-cookie"]
    assert accepted.status_code == 200
    accepted_call = next(
        call for call in platform_service.calls if call.operation == "student.get_bootstrap"
    )
    assert accepted_call.auth is not None
    assert accepted_call.auth.authentication_method == "demo"
    assert signed_out.json() == {"authenticated": False, "mode": "demo"}
    assert denied_again.status_code == 401


async def test_general_student_sign_out_invalidates_a_demo_session(
    client: AsyncClient,
) -> None:
    signed_in = await client.post("/v1/auth/demo/sign-in", json={})
    accepted = await client.get("/v1/student/bootstrap")
    signed_out = await client.post("/v1/auth/sign-out")
    denied = await client.get("/v1/student/bootstrap")

    assert signed_in.status_code == 200
    assert accepted.status_code == 200
    assert signed_out.status_code == 200
    assert "vv_demo_session=signed-out" in signed_out.headers["set-cookie"]
    assert denied.status_code == 401


async def test_signup_normalizes_email_and_issues_an_http_only_credential_cookie(
    client: AsyncClient,
    auth_service: FakeBrowserAuthService,
) -> None:
    response = await client.post(
        "/v1/auth/sign-up",
        json={
            "email": "  BROWSER.STUDENT@EXAMPLE.COM  ",
            "phone": "+15551230001",
            "password": "Browser-student-123",
        },
    )

    assert response.status_code == 201
    assert response.json()["student"]["email"] == "browser.student@example.com"
    assert auth_service.sign_up_input is not None
    assert auth_service.sign_up_input["email"] == "browser.student@example.com"
    assert f"vv_session={STUDENT_TOKEN}" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


async def test_invalid_credentials_use_one_non_enumerating_message(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/auth/sign-in",
        json={"email": "missing.student@example.com", "password": WRONG_PASSWORD},
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Email or password is incorrect"


async def test_staff_cookie_authorizes_staff_routes_and_is_revoked_on_sign_out(
    client: AsyncClient,
) -> None:
    signed_in = await client.post(
        "/v1/auth/staff/sign-in",
        json={
            "email": "PRIYA.SHAH@ASTER.EXAMPLE.EDU",
            "password": STAFF_PASSWORD,
        },
    )
    accepted = await client.get(
        "/v1/staff/action-center",
        headers={"X-Demo-Actor-Type": "staff"},
    )
    await client.post("/v1/auth/staff/sign-out")
    denied = await client.get(
        "/v1/staff/action-center",
        headers={"X-Demo-Actor-Type": "staff"},
    )

    assert signed_in.status_code == 200
    assert signed_in.json()["staff"]["name"] == "Priya Shah"
    assert "vv_staff_session=" in signed_in.headers["set-cookie"]
    assert accepted.status_code == 200
    assert denied.status_code == 401


async def test_staff_signup_claims_an_approved_identity_and_sets_http_only_cookie(
    client: AsyncClient,
    auth_service: FakeBrowserAuthService,
) -> None:
    response = await client.post(
        "/v1/auth/staff/sign-up",
        json={
            "email": "  PRIYA.SHAH@ASTER.EXAMPLE.EDU  ",
            "password": STAFF_PASSWORD,
            "institutionAccessCode": STAFF_INVITATION_CODE,
        },
    )

    assert response.status_code == 201
    assert response.json()["staff"]["email"] == "priya.shah@aster.example.edu"
    assert auth_service.staff_sign_up_input is not None
    assert auth_service.staff_sign_up_input["email"] == "priya.shah@aster.example.edu"
    assert f"vv_staff_session={STAFF_TOKEN}" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


async def test_staff_signup_rejects_an_invalid_institution_access_code(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/auth/staff/sign-up",
        json={
            "email": "priya.shah@aster.example.edu",
            "password": STAFF_PASSWORD,
            "institutionAccessCode": "not-the-approved-access-code",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == (
        "Staff account could not be created with these credentials"
    )


async def test_guided_reset_explicitly_selects_completed_browser_fixture(
    client: AsyncClient,
    auth_service: FakeBrowserAuthService,
) -> None:
    response = await client.post(
        "/v1/auth/demo/start-guided-onboarding",
        json={"completedOnboarding": True},
    )

    assert response.status_code == 200
    assert auth_service.reset_completed is True


async def test_guided_reset_is_hidden_in_preview_and_does_not_mutate_fixture(
    auth_service: FakeBrowserAuthService,
    platform_service: FakePlatformService,
) -> None:
    app = create_app(
        service=platform_service,
        auth_service=auth_service,
        settings=HttpSettings(environment="preview", browser_auth_required=True),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://preview.example"
    ) as preview_client:
        response = await preview_client.post(
            "/v1/auth/demo/start-guided-onboarding",
            json={"completedOnboarding": False},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GUIDED_RESET_DISABLED"
    assert auth_service.reset_completed is None


async def test_guided_reset_validates_tenant_before_mutating_fixture(
    client: AsyncClient,
    auth_service: FakeBrowserAuthService,
) -> None:
    response = await client.post(
        "/v1/auth/demo/start-guided-onboarding",
        headers={"X-Tenant-Slug": "unknown"},
        json={"completedOnboarding": False},
    )

    assert response.status_code == 401
    assert auth_service.reset_completed is None


async def test_development_auth_endpoints_are_hidden_in_production(
    auth_service: FakeBrowserAuthService,
    platform_service: FakePlatformService,
) -> None:
    app = create_app(
        service=platform_service,
        auth_service=auth_service,
        settings=HttpSettings(environment="production", browser_auth_required=True),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as production_client:
        response = await production_client.post("/v1/auth/demo/sign-in", json={})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DEVELOPMENT_AUTH_DISABLED"


async def test_preview_cross_site_auth_cookie_and_cors_are_credential_safe(
    auth_service: FakeBrowserAuthService,
    platform_service: FakePlatformService,
) -> None:
    app = create_app(
        service=platform_service,
        auth_service=auth_service,
        settings=HttpSettings(
            environment="preview",
            browser_auth_required=True,
            web_origins=("https://audentra-portals-demo-web.vercel.app",),
            session_cookie_samesite="none",
        ),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://preview.example",
    ) as preview_client:
        demo_response = await preview_client.post(
            "/v1/auth/demo/sign-in",
            headers={"Origin": "https://audentra-portals-demo-web.vercel.app"},
            json={},
        )
        accepted = await preview_client.get(
            "/v1/student/bootstrap",
            headers={"Origin": "https://audentra-portals-demo-web.vercel.app"},
        )
        credential_response = await preview_client.post(
            "/v1/auth/sign-in",
            headers={"Origin": "https://audentra-portals-demo-web.vercel.app"},
            json={
                "email": "browser.student@example.com",
                "password": "Browser-student-123",
            },
        )

    assert demo_response.status_code == 200
    assert accepted.status_code == 200
    assert credential_response.status_code == 200
    for response in (demo_response, credential_response):
        cookies = response.headers.get_list("set-cookie")
        assert cookies
        assert all("HttpOnly" in cookie for cookie in cookies)
        assert all("Secure" in cookie for cookie in cookies)
        assert all("SameSite=none" in cookie for cookie in cookies)
    assert demo_response.headers["access-control-allow-credentials"] == "true"
    assert demo_response.headers["access-control-allow-origin"] == (
        "https://audentra-portals-demo-web.vercel.app"
    )
    assert demo_response.headers["access-control-allow-origin"] != "*"
