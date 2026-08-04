"""Development browser-auth compatibility endpoints."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request, Response

from audentra.contracts.requests import (
    EmptyBody,
    StaffSignInRequest,
    StaffSignUpRequest,
    StartGuidedOnboardingRequest,
    StudentSignInRequest,
    StudentSignUpRequest,
)
from audentra.contracts.responses import ApiErrorEnvelope
from audentra.core.errors import ApiError
from audentra.core.ports import CredentialStudentSession, DemoStudentSession, StaffSession

from .dependencies import (
    AuthServiceDependency,
    get_settings,
    resolve_request_tenant,
)

auth_router = APIRouter(
    responses={
        status_code: {"model": ApiErrorEnvelope} for status_code in (400, 401, 404, 409, 500, 503)
    }
)


def _development_only(request: Request) -> None:
    if get_settings(request).environment == "production":
        raise ApiError(
            404,
            "DEVELOPMENT_AUTH_DISABLED",
            "Development authentication is not available",
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
        samesite="lax",
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
        samesite="lax",
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
        samesite="lax",
    )


def _demo_response(session: DemoStudentSession) -> dict[str, Any]:
    return {
        "authenticated": True,
        "mode": "demo",
        "actorType": "student",
        "student": {
            "id": session.context.student_id,
            "preferredName": session.preferred_name,
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


@auth_router.post("/v1/auth/demo/sign-in", status_code=200, response_model=None)
async def sign_in_demo_student(
    _body: EmptyBody,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    tenant_id, tenant_slug = resolve_request_tenant(request)
    session = await auth.demo_student(tenant_id, tenant_slug)
    await auth.sign_out_student(request.cookies.get("vv_session"))
    _expire_cookie(response, request, "vv_session")
    _demo_cookie(response, request)
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
    _development_only(request)
    await auth.reset_demo_fixture(completed_onboarding=body.completed_onboarding)
    tenant_id, tenant_slug = resolve_request_tenant(request)
    session = await auth.demo_student(tenant_id, tenant_slug)
    await auth.sign_out_student(request.cookies.get("vv_session"))
    _expire_cookie(response, request, "vv_session")
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
    return {"authenticated": False, "mode": "demo"}


@auth_router.post("/v1/auth/sign-up", status_code=201, response_model=None)
async def sign_up_student(
    body: StudentSignUpRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    tenant_id, tenant_slug = resolve_request_tenant(request)
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
    tenant_id, tenant_slug = resolve_request_tenant(request)
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
    _development_only(request)
    await auth.sign_out_student(request.cookies.get("vv_session"))
    _expire_cookie(response, request, "vv_session")
    _expire_cookie(response, request, "vv_demo_session")
    return {"authenticated": False, "mode": "credentials"}


@auth_router.post("/v1/auth/staff/sign-in", status_code=200, response_model=None)
async def sign_in_staff(
    body: StaffSignInRequest,
    request: Request,
    response: Response,
    auth: AuthServiceDependency,
) -> object:
    _development_only(request)
    tenant_id, tenant_slug = resolve_request_tenant(request)
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
    tenant_id, tenant_slug = resolve_request_tenant(request)
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
