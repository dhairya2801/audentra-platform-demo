"""Browser authentication endpoints for local credentials and student OIDC."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse

from audentra.contracts.requests import (
    DemoStudentSignInRequest,
    EmptyBody,
    StaffSignInRequest,
    StaffSignUpRequest,
    StartGuidedOnboardingRequest,
    StudentSignInRequest,
    StudentSignUpRequest,
)
from audentra.contracts.responses import ApiErrorEnvelope
from audentra.core.errors import ApiError
from audentra.core.oidc import OidcErrorCode, OidcFlowError
from audentra.core.ports import CredentialStudentSession, DemoStudentSession, StaffSession

from .demo_identity import DEMO_STUDENT_COOKIE, issue_demo_student_cookie
from .dependencies import (
    AuthServiceDependency,
    OidcAuthServiceDependency,
    get_settings,
    resolve_request_tenant,
)

auth_router = APIRouter(
    responses={
        status_code: {"model": ApiErrorEnvelope} for status_code in (400, 401, 404, 409, 500, 503)
    }
)

OIDC_BINDING_COOKIE = "vv_oidc_binding"
OIDC_SECURE_BINDING_COOKIE = "__Host-vv_oidc_binding"


def _development_only(request: Request) -> None:
    settings = get_settings(request)
    if settings.environment == "production" or settings.auth_mode != "demo":
        raise ApiError(
            404,
            "DEVELOPMENT_AUTH_DISABLED",
            "Development authentication is not available",
        )


def _guided_reset_only(request: Request) -> None:
    if get_settings(request).environment not in {"development", "test"}:
        raise ApiError(
            404,
            "GUIDED_RESET_DISABLED",
            "The guided onboarding reset is not available",
        )


def _set_session_cookie(
    response: Response,
    request: Request,
    *,
    name: str,
    token: str,
    expires_at_epoch: int,
) -> None:
    response.set_cookie(
        key=name,
        value=token,
        max_age=max(0, expires_at_epoch - int(time.time())),
        expires=expires_at_epoch,
        path="/",
        secure=get_settings(request).secure_cookies,
        httponly=True,
        samesite=get_settings(request).session_cookie_samesite,
    )


def _expire_cookie(response: Response, request: Request, name: str) -> None:
    response.set_cookie(
        key=name,
        value="signed-out",
        max_age=0,
        expires=0,
        path="/",
        secure=get_settings(request).secure_cookies,
        httponly=True,
        samesite=get_settings(request).session_cookie_samesite,
    )


def _demo_cookie(response: Response, request: Request) -> None:
    settings = get_settings(request)
    response.set_cookie(
        key="vv_demo_session",
        value=settings.demo_session_token,
        max_age=86_400,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )


def _demo_response(session: DemoStudentSession) -> dict[str, Any]:
    return {
        "authenticated": True,
        "mode": "demo",
        "actorType": "student",
        "student": {
            "id": session.context.student_id,
            "preferredName": session.preferred_name,
            "externalRef": session.external_ref,
        },
        "notice": (
            "Development fixture only. This is not an institutional authentication session."
        ),
    }


def _credential_response(session: CredentialStudentSession) -> dict[str, Any]:
    return {
        "authenticated": True,
        "mode": "credentials",
        "actorType": "student",
        "student": {
            "id": session.context.student_id,
            "preferredName": session.preferred_name,
            "email": session.email,
            "phone": session.phone,
            "emailVerified": session.email_verified,
            "phoneVerified": session.phone_verified,
        },
        "notice": (
            "Email and phone verification are pending until delivery providers are configured."
        ),
    }


def _staff_response(session: StaffSession) -> dict[str, Any]:
    return {
        "authenticated": True,
        "mode": "credentials",
        "actorType": "staff",
        "staff": {
            "id": session.context.actor_id,
            "name": session.name,
            "email": session.email,
            "component": session.component,
        },
        "notice": (
            "Authenticated local staff session. Institutional deployments should replace this "
            "adapter with university SSO while preserving the same role boundary."
        ),
    }


def _oidc_binding_cookie_name(request: Request) -> str:
    return (
        OIDC_SECURE_BINDING_COOKIE if get_settings(request).secure_cookies else OIDC_BINDING_COOKIE
    )


def _portal_url(request: Request, path: str) -> str:
    base_url = get_settings(request).oidc_portal_base_url
    return f"{base_url}{path}" if base_url else path


def _protect_sso_response(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _sso_error(request: Request, code: OidcErrorCode) -> RedirectResponse:
    response = RedirectResponse(
        url=_portal_url(request, f"/sign-in?sso_error={code}"),
        status_code=303,
    )
    _protect_sso_response(response)
    return response


@auth_router.get("/v1/auth/sso/providers", status_code=200, response_model=None)
async def sso_providers(
    request: Request,
    response: Response,
    oidc: OidcAuthServiceDependency,
) -> object:
    _protect_sso_response(response)
    settings = get_settings(request)
    return {
        "providers": [
            {"id": provider.id, "label": provider.label}
            for provider in (oidc.providers() if settings.auth_mode == "oidc" else ())
        ],
        "passwordEnabled": settings.auth_mode == "demo",
    }


@auth_router.get("/v1/auth/sso/{provider}/start", response_model=None)
async def start_sso(
    provider: str,
    request: Request,
    oidc: OidcAuthServiceDependency,
    return_to: str = Query("/dashboard", alias="returnTo"),
) -> Response:
    if get_settings(request).auth_mode != "oidc":
        return _sso_error(request, "invalid_request")
    try:
        tenant_id, _tenant_slug = await resolve_request_tenant(request)
        start = await oidc.start(
            provider=provider,
            tenant_id=tenant_id,
            return_to=return_to,
        )
    except OidcFlowError as error:
        return _sso_error(request, error.code)
    except Exception:
        return _sso_error(request, "invalid_request")
    response = RedirectResponse(start.authorization_url, status_code=307)
    response.set_cookie(
        key=_oidc_binding_cookie_name(request),
        value=start.binding_token,
        max_age=max(0, start.expires_at_epoch - int(time.time())),
        expires=start.expires_at_epoch,
        path="/",
        secure=get_settings(request).secure_cookies,
        httponly=True,
        samesite="lax",
    )
    return _protect_sso_response(response)


@auth_router.get("/v1/auth/sso/{provider}/callback", response_model=None)
async def complete_sso(
    provider: str,
    request: Request,
    oidc: OidcAuthServiceDependency,
    auth: AuthServiceDependency,
    state: str | None = Query(None),
    code: str | None = Query(None),
    provider_error: str | None = Query(None, alias="error"),
) -> Response:
    if get_settings(request).auth_mode != "oidc":
        return _sso_error(request, "invalid_request")
    try:
        login = await oidc.complete(
            provider=provider,
            state=state,
            code=code,
            provider_error=provider_error,
            binding_token=request.cookies.get(_oidc_binding_cookie_name(request)),
        )
    except OidcFlowError as error:
        response = _sso_error(request, error.code)
    except Exception:
        response = _sso_error(request, "provider_error")
    else:
        await auth.sign_out_student(request.cookies.get("vv_session"))
        response = RedirectResponse(
            _portal_url(request, login.return_to),
            status_code=303,
        )
        _set_session_cookie(
            response,
            request,
            name="vv_session",
            token=login.session_token,
            expires_at_epoch=login.expires_at_epoch,
        )
        _expire_cookie(response, request, "vv_demo_session")
    response.set_cookie(
        key=_oidc_binding_cookie_name(request),
        value="consumed",
        max_age=0,
        expires=0,
        path="/",
        secure=get_settings(request).secure_cookies,
        httponly=True,
        samesite="lax",
    )
    return _protect_sso_response(response)


@auth_router.post("/v1/auth/demo/sign-in", status_code=200, response_model=None)
async def sign_in_demo_student(
    _body: EmptyBody,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    tenant_id, tenant_slug = await resolve_request_tenant(request)
    session = await auth.demo_student(tenant_id, tenant_slug)
    await auth.sign_out_student(request.cookies.get("vv_session"))
    _expire_cookie(response, request, "vv_session")
    # Signing in as "the demo student" clears any earlier per-student choice.
    _expire_cookie(response, request, DEMO_STUDENT_COOKIE)
    _demo_cookie(response, request)
    return _demo_response(session)


@auth_router.post("/v1/auth/demo/sign-in-as", status_code=200, response_model=None)
async def sign_in_demo_student_by_reference(
    body: DemoStudentSignInRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    """Open a chosen demo student's portal. Development and preview only.

    This reads existing state; it never writes any. Signing in as a student who
    has paid a deposit and stalled on a transcript shows exactly that, and
    signing out and back in shows it again — which is the entire reason the
    endpoint exists and the reason it must not touch the demo fixture reset.
    """

    _development_only(request)
    tenant_id, tenant_slug = await resolve_request_tenant(request)
    session = await auth.demo_student_by_reference(tenant_id, tenant_slug, body.student_ref)
    await auth.sign_out_student(request.cookies.get("vv_session"))
    _expire_cookie(response, request, "vv_session")
    _demo_cookie(response, request)
    _set_session_cookie(
        response,
        request,
        name=DEMO_STUDENT_COOKIE,
        token=issue_demo_student_cookie(
            get_settings(request).demo_session_token,
            tenant_id,
            session.context.student_id,
        ),
        expires_at_epoch=int(time.time()) + 86_400,
    )
    return _demo_response(session)


@auth_router.post(
    "/v1/auth/demo/start-guided-onboarding",
    status_code=200,
    response_model=None,
)
async def start_guided_onboarding(
    body: StartGuidedOnboardingRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _guided_reset_only(request)
    tenant_id, tenant_slug = await resolve_request_tenant(request)
    await auth.reset_demo_fixture(completed_onboarding=body.completed_onboarding)
    session = await auth.demo_student(tenant_id, tenant_slug)
    await auth.sign_out_student(request.cookies.get("vv_session"))
    _expire_cookie(response, request, "vv_session")
    _expire_cookie(response, request, DEMO_STUDENT_COOKIE)
    _demo_cookie(response, request)
    return _demo_response(session)


@auth_router.post("/v1/auth/demo/sign-out", status_code=200, response_model=None)
async def sign_out_demo_student(
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    await auth.sign_out_student(request.cookies.get("vv_session"))
    _expire_cookie(response, request, "vv_session")
    _expire_cookie(response, request, "vv_demo_session")
    _expire_cookie(response, request, DEMO_STUDENT_COOKIE)
    return {"authenticated": False, "mode": "demo"}


@auth_router.post("/v1/auth/sign-up", status_code=201, response_model=None)
async def sign_up_student(
    body: StudentSignUpRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    tenant_id, tenant_slug = await resolve_request_tenant(request)
    session = await auth.sign_up_student(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        email=body.email,
        phone=body.phone,
        password=body.password,
    )
    if session.token is None or session.expires_at_epoch is None:
        raise ApiError(500, "AUTH_SESSION_FAILED", "The student session could not be created")
    _set_session_cookie(
        response,
        request,
        name="vv_session",
        token=session.token,
        expires_at_epoch=session.expires_at_epoch,
    )
    _expire_cookie(response, request, "vv_demo_session")
    return _credential_response(session)


@auth_router.post("/v1/auth/sign-in", status_code=200, response_model=None)
async def sign_in_student(
    body: StudentSignInRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    tenant_id, tenant_slug = await resolve_request_tenant(request)
    session = await auth.sign_in_student(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        email=body.email,
        password=body.password,
    )
    if session.token is None or session.expires_at_epoch is None:
        raise ApiError(500, "AUTH_SESSION_FAILED", "The student session could not be created")
    _set_session_cookie(
        response,
        request,
        name="vv_session",
        token=session.token,
        expires_at_epoch=session.expires_at_epoch,
    )
    _expire_cookie(response, request, "vv_demo_session")
    return _credential_response(session)


@auth_router.post("/v1/auth/sign-out", status_code=200, response_model=None)
async def sign_out_student(
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    await auth.sign_out_student(request.cookies.get("vv_session"))
    _expire_cookie(response, request, "vv_session")
    _expire_cookie(response, request, "vv_demo_session")
    return {
        "authenticated": False,
        "mode": "oidc" if get_settings(request).auth_mode == "oidc" else "credentials",
    }


@auth_router.post("/v1/auth/staff/sign-in", status_code=200, response_model=None)
async def sign_in_staff(
    body: StaffSignInRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    tenant_id, tenant_slug = await resolve_request_tenant(request)
    session = await auth.sign_in_staff(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        email=body.email,
        password=body.password,
    )
    if session.token is None or session.expires_at_epoch is None:
        raise ApiError(500, "AUTH_SESSION_FAILED", "The staff session could not be created")
    _set_session_cookie(
        response,
        request,
        name="vv_staff_session",
        token=session.token,
        expires_at_epoch=session.expires_at_epoch,
    )
    return _staff_response(session)


@auth_router.post("/v1/auth/staff/sign-up", status_code=201, response_model=None)
async def sign_up_staff(
    body: StaffSignUpRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    tenant_id, tenant_slug = await resolve_request_tenant(request)
    session = await auth.sign_up_staff(
        tenant_id=tenant_id,
        tenant_slug=tenant_slug,
        email=body.email,
        password=body.password,
        institution_access_code=body.institution_access_code,
    )
    if session.token is None or session.expires_at_epoch is None:
        raise ApiError(500, "AUTH_SESSION_FAILED", "The staff session could not be created")
    _set_session_cookie(
        response,
        request,
        name="vv_staff_session",
        token=session.token,
        expires_at_epoch=session.expires_at_epoch,
    )
    return _staff_response(session)


@auth_router.post("/v1/auth/staff/sign-out", status_code=200, response_model=None)
async def sign_out_staff(
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    await auth.sign_out_staff(request.cookies.get("vv_staff_session"))
    _expire_cookie(response, request, "vv_staff_session")
    return {"authenticated": False, "mode": "credentials", "actorType": "staff"}
