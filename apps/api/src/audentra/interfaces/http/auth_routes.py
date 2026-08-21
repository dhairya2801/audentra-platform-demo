"""Development browser-auth compatibility endpoints."""

from __future__ import annotations

import time
from typing import Any, cast

from fastapi import APIRouter, Request, Response

from audentra.contracts.requests import (
    DemoStudentSignInRequest,
    EmptyBody,
    ExchangeDelegateLinkRequest,
    StaffSignInRequest,
    StaffSignUpRequest,
    StartGuidedOnboardingRequest,
    StudentSignInRequest,
    StudentSignUpRequest,
)
from audentra.contracts.responses import ApiErrorEnvelope
from audentra.core.errors import ApiError
from audentra.core.ports import (
    CredentialStudentSession,
    DelegateBrowserAuthService,
    DelegateSession,
    DemoStudentSession,
    StaffSession,
)

from .demo_identity import DEMO_STUDENT_COOKIE, issue_demo_student_cookie
from .dependencies import (
    AuthServiceDependency,
    get_settings,
    resolve_request_tenant,
)

auth_router = APIRouter(
    responses={
        status_code: {"model": ApiErrorEnvelope}
        for status_code in (400, 401, 403, 404, 409, 500, 503)
    }
)


def _development_only(request: Request) -> None:
    if get_settings(request).environment == "production":
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


def _delegate_response(session: DelegateSession) -> dict[str, Any]:
    routes = {
        "dashboard": "/dashboard",
        "enrollment": "/enrollment",
        "financials": "/financials",
        "classrooms": "/classrooms",
        "campus_life": "/campus-life",
        "edward": "/edward",
        "documents": "/documents",
        "messages": "/messages",
        "appointments": "/appointments",
        "payments": "/payments",
        "profile": "/profile",
        "help": "/help",
    }
    initial_route = next(
        (route for scope, route in routes.items() if scope in session.context.delegate_scopes),
        "/help",
    )
    return {
        "authenticated": True,
        "mode": "delegate",
        "actorType": "delegate",
        "delegate": {
            "id": session.context.actor_id,
            "fullName": session.full_name,
            "relationship": session.relationship,
            "email": session.email,
            "studentId": session.context.student_id,
            "studentName": session.student_name,
            "studentPreferredName": session.student_preferred_name,
            "scopes": sorted(session.context.delegate_scopes),
        },
        "initialRoute": initial_route,
        "capabilities": {"canManageFerpa": False, "canSignFerpa": False},
        "expiresAt": session.expires_at_epoch,
    }


@auth_router.post("/v1/auth/delegate/exchange", status_code=200, response_model=None)
async def exchange_delegate_link(
    body: ExchangeDelegateLinkRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    tenant_id, tenant_slug = await resolve_request_tenant(request)
    session = await cast(DelegateBrowserAuthService, auth).exchange_delegate(
        body.token, tenant_id, tenant_slug
    )
    if session.token is None or session.expires_at_epoch is None:
        raise ApiError(500, "AUTH_SESSION_FAILED", "The delegate session could not be created")
    _set_session_cookie(
        response,
        request,
        name="vv_delegate_session",
        token=session.token,
        expires_at_epoch=session.expires_at_epoch,
    )
    _expire_cookie(response, request, "vv_session")
    _expire_cookie(response, request, "vv_demo_session")
    _expire_cookie(response, request, DEMO_STUDENT_COOKIE)
    return _delegate_response(session)


@auth_router.get("/v1/auth/delegate/session", status_code=200, response_model=None)
async def get_delegate_session(
    request: Request,
    auth: AuthServiceDependency,
) -> object:
    tenant_id, tenant_slug = await resolve_request_tenant(request)
    token = request.cookies.get("vv_delegate_session")
    session = await cast(DelegateBrowserAuthService, auth).resolve_delegate(
        token or "", tenant_id, tenant_slug
    )
    if session is None:
        raise ApiError(401, "UNAUTHORIZED", "The parent or guardian session has expired")
    return _delegate_response(session)


@auth_router.post("/v1/auth/delegate/sign-out", status_code=200, response_model=None)
async def sign_out_delegate(
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    await cast(DelegateBrowserAuthService, auth).sign_out_delegate(
        request.cookies.get("vv_delegate_session")
    )
    _expire_cookie(response, request, "vv_delegate_session")
    return {"authenticated": False, "mode": "delegate"}


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
    _expire_cookie(response, request, "vv_delegate_session")
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
    _expire_cookie(response, request, "vv_delegate_session")
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
    _expire_cookie(response, request, "vv_delegate_session")
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
    _expire_cookie(response, request, "vv_delegate_session")
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
    _expire_cookie(response, request, "vv_delegate_session")
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
    _expire_cookie(response, request, "vv_delegate_session")
    return _credential_response(session)


@auth_router.post("/v1/auth/sign-out", status_code=200, response_model=None)
async def sign_out_student(
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    await auth.sign_out_student(request.cookies.get("vv_session"))
    _expire_cookie(response, request, "vv_session")
    _expire_cookie(response, request, "vv_demo_session")
    _expire_cookie(response, request, "vv_delegate_session")
    return {"authenticated": False, "mode": "credentials"}


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
