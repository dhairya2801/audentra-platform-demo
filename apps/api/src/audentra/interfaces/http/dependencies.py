"""FastAPI dependencies translating HTTP metadata into core values."""

import re
import secrets
from collections.abc import Mapping
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Header, Request

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, UnauthorizedError
from audentra.core.ports import BrowserAuthService, PlatformService, ServiceCall
from audentra.infrastructure.voice import VoiceSessionServiceProtocol

from .config import HttpSettings

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
TENANT_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
RESERVED_TENANT_SLUGS = frozenset(
    {
        "appointments",
        "campus-life",
        "classrooms",
        "dashboard",
        "documents",
        "edward",
        "enrollment",
        "financials",
        "health",
        "help",
        "messages",
        "offer",
        "onboarding",
        "payments",
        "profile",
        "sign-in",
        "staff",
        "v1",
    }
)


def is_valid_tenant_slug(value: str) -> bool:
    return bool(TENANT_SLUG_PATTERN.fullmatch(value)) and value not in RESERVED_TENANT_SLUGS


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


async def resolve_request_tenant(request: Request) -> tuple[str, str | None]:
    settings = get_settings(request)
    explicit_tenant_id = request.headers.get("x-demo-tenant-id")
    tenant_id = explicit_tenant_id or settings.demo_tenant_id
    if not _valid_uuid(tenant_id):
        raise UnauthorizedError("Demo identity headers must be valid UUIDs")

    tenant_slug = request.headers.get("x-tenant-slug")
    if tenant_slug is not None and not is_valid_tenant_slug(tenant_slug):
        raise UnauthorizedError("The tenant slug is invalid")
    try:
        bootstrap = await get_platform_service(request).dispatch(
            ServiceCall(
                operation="public.get_tenant_bootstrap",
                auth=None,
                request_id=getattr(request.state, "request_id", "tenant-resolution"),
                path_params=(
                    {"slug": tenant_slug} if tenant_slug is not None else {"tenantId": tenant_id}
                ),
            )
        )
    except ApiError as error:
        if error.status_code == 404:
            raise UnauthorizedError("The tenant is not recognized or is inactive") from error
        raise
    resolved_tenant_id = bootstrap.get("tenantId") if isinstance(bootstrap, Mapping) else None
    resolved_tenant_slug = bootstrap.get("slug") if isinstance(bootstrap, Mapping) else None
    if (
        not isinstance(resolved_tenant_id, str)
        or not _valid_uuid(resolved_tenant_id)
        or not isinstance(resolved_tenant_slug, str)
        or not is_valid_tenant_slug(resolved_tenant_slug)
    ):
        raise ApiError(
            503,
            "TENANT_CONFIGURATION_INVALID",
            "Tenant configuration returned an invalid tenant identity",
        )
    if explicit_tenant_id is not None and UUID(explicit_tenant_id) != UUID(resolved_tenant_id):
        raise UnauthorizedError("The tenant slug conflicts with the tenant identity header")
    return resolved_tenant_id, resolved_tenant_slug


async def get_auth_context(request: Request) -> AuthContext:
    settings = get_settings(request)
    auth_service = get_browser_auth_service(request)
    tenant_id, tenant_slug = await resolve_request_tenant(request)
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

    student_id = request.headers.get("x-demo-student-id")
    demo_context: AuthContext | None = None
    needs_demo_context = student_id is None or (
        not is_staff_route and request.headers.get("x-demo-actor-id") is None
    )
    if needs_demo_context and tenant_slug is not None:
        try:
            demo_context = (await auth_service.demo_student(tenant_id, tenant_slug)).context
        except ApiError as error:
            if error.code != "AUTH_SERVICE_UNAVAILABLE":
                raise
    student_id = student_id or (
        demo_context.student_id if demo_context else settings.demo_student_id
    )
    actor_id = request.headers.get(
        "x-demo-actor-id",
        (
            settings.demo_staff_actor_id
            if is_staff_route
            else (demo_context.actor_id if demo_context else settings.demo_actor_id)
        ),
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


VOICE_AGENT_BEARER_PATTERN = re.compile(r"^Bearer (\S+)$", re.IGNORECASE)


def require_voice_agent_token(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> None:
    """Constant-time bearer check for the voice agent; fails closed when unset."""

    expected = get_settings(request).voice_agent_internal_token
    match = (
        VOICE_AGENT_BEARER_PATTERN.fullmatch(authorization)
        if isinstance(authorization, str)
        else None
    )
    provided = (match.group(1) if match else "").encode("utf-8")
    if not expected or not secrets.compare_digest(provided, expected.encode("utf-8")):
        raise ApiError(
            401,
            "VOICE_AGENT_AUTHENTICATION_FAILED",
            "The voice agent credential is not valid",
        )


def get_voice_session_service(request: Request) -> VoiceSessionServiceProtocol:
    service = getattr(request.app.state, "voice_session_service", None)
    if service is None:
        raise ApiError(
            503,
            "ASSISTANT_VOICE_NOT_CONFIGURED",
            "Voice is not configured for this environment",
        )
    return cast(VoiceSessionServiceProtocol, service)


AuthDependency = Annotated[AuthContext, Depends(get_auth_context)]
AuthServiceDependency = Annotated[BrowserAuthService, Depends(get_browser_auth_service)]
ServiceDependency = Annotated[PlatformService, Depends(get_platform_service)]
IdempotencyDependency = Annotated[str, Depends(require_idempotency_key)]
WorkerTokenDependency = Annotated[None, Depends(require_worker_token)]
VoiceAgentTokenDependency = Annotated[None, Depends(require_voice_agent_token)]
VoiceSessionServiceDependency = Annotated[
    VoiceSessionServiceProtocol, Depends(get_voice_session_service)
]
