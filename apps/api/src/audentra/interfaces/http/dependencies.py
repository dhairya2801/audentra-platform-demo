"""FastAPI dependencies translating HTTP metadata into core values."""

import re
import secrets
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Header, Request

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, UnauthorizedError
from audentra.core.ports import BrowserAuthService, PlatformService

from .config import HttpSettings

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
TENANT_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def get_settings(request: Request) -> HttpSettings:
    return cast(HttpSettings, request.app.state.http_settings)


def get_platform_service(request: Request) -> PlatformService:
    return cast(PlatformService, request.app.state.platform_service)


def get_browser_auth_service(request: Request) -> BrowserAuthService:
    return cast(BrowserAuthService, request.app.state.browser_auth_service)


def _valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def resolve_request_tenant(request: Request) -> tuple[str, str | None]:
    settings = get_settings(request)
    explicit_tenant_id = request.headers.get("x-demo-tenant-id")
    tenant_id = explicit_tenant_id or settings.demo_tenant_id
    if not _valid_uuid(tenant_id):
        raise UnauthorizedError("Demo identity headers must be valid UUIDs")

    tenant_slug = request.headers.get("x-tenant-slug")
    if tenant_slug is not None and not TENANT_SLUG_PATTERN.fullmatch(tenant_slug):
        raise UnauthorizedError("The tenant slug is invalid")
    if tenant_slug is not None:
        resolved_tenant_id = settings.tenant_slug_ids.get(tenant_slug)
        if resolved_tenant_id is None:
            raise UnauthorizedError("The tenant slug is not recognized")
        if explicit_tenant_id is not None and UUID(explicit_tenant_id) != UUID(resolved_tenant_id):
            raise UnauthorizedError("The tenant slug conflicts with the tenant identity header")
        tenant_id = resolved_tenant_id
    return tenant_id, tenant_slug


async def get_auth_context(request: Request) -> AuthContext:
    settings = get_settings(request)
    auth_service = get_browser_auth_service(request)
    tenant_id, tenant_slug = resolve_request_tenant(request)
    is_staff_route = request.url.path.startswith("/v1/staff")
    is_worker_route = request.url.path.startswith("/v1/student/internal/")

    if is_worker_route:
        # Internal document commands authenticate with the worker credential and
        # carry the durable event's tenant/student/actor lineage in headers.
        # Browser-session enforcement must not turn that service credential into
        # an unrelated student-cookie requirement.
        require_worker_token(request, request.headers.get("x-vv-worker-token"))
    elif is_staff_route:
        staff_token = request.cookies.get("vv_staff_session")
        if staff_token is not None:
            staff_session = await auth_service.resolve_staff(
                staff_token,
                tenant_id,
                tenant_slug,
            )
            if staff_session is None:
                raise UnauthorizedError("The staff session is invalid or has expired")
            return staff_session.context
        if settings.browser_auth_required:
            raise UnauthorizedError("Staff authentication is required")
        if request.headers.get("x-demo-actor-type") != "staff":
            raise UnauthorizedError("Staff routes require the development staff identity header")
    else:
        credential_token = request.cookies.get("vv_session")
        if credential_token is not None:
            student_session = await auth_service.resolve_student(
                credential_token,
                tenant_id,
                tenant_slug,
            )
            if student_session is None:
                raise UnauthorizedError("The student session is invalid or has expired")
            return student_session.context

        demo_token = request.cookies.get("vv_demo_session")
        if demo_token is not None:
            if not secrets.compare_digest(demo_token, settings.demo_session_token):
                raise UnauthorizedError("The student session is invalid or has expired")
            return (await auth_service.demo_student(tenant_id, tenant_slug)).context
        if settings.browser_auth_required:
            raise UnauthorizedError("Student authentication is required")

    student_id = request.headers.get(
        "x-demo-student-id",
        settings.demo_student_for(tenant_slug),
    )
    actor_id = request.headers.get(
        "x-demo-actor-id",
        settings.demo_staff_actor_id if is_staff_route else settings.demo_actor_id,
    )
    if not all(_valid_uuid(value) for value in (student_id, actor_id)):
        raise UnauthorizedError("Demo identity headers must be valid UUIDs")

    return AuthContext(
        tenant_id=tenant_id,
        student_id=student_id,
        actor_id=actor_id,
        actor_type="staff" if is_staff_route else "student",
        tenant_slug=tenant_slug,
    )


def require_idempotency_key(
    value: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if value is None:
        raise BadRequestError("IDEMPOTENCY_KEY_REQUIRED", "The Idempotency-Key header is required")
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise BadRequestError(
            "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must be 8-128 safe characters"
        )
    return value


def require_worker_token(
    request: Request,
    value: Annotated[str | None, Header(alias="X-VV-Worker-Token")] = None,
) -> None:
    expected = get_settings(request).document_worker_token.encode("utf-8")
    provided = value.encode("utf-8") if isinstance(value, str) else b""
    if not secrets.compare_digest(provided, expected):
        raise ApiError(403, "WORKER_AUTHENTICATION_FAILED", "The worker credential is not valid")


AuthDependency = Annotated[AuthContext, Depends(get_auth_context)]
AuthServiceDependency = Annotated[BrowserAuthService, Depends(get_browser_auth_service)]
ServiceDependency = Annotated[PlatformService, Depends(get_platform_service)]
IdempotencyDependency = Annotated[str, Depends(require_idempotency_key)]
WorkerTokenDependency = Annotated[None, Depends(require_worker_token)]
