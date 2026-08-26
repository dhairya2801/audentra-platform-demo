"""FastAPI dependencies translating HTTP metadata into core values."""

import re
import secrets
from collections.abc import Mapping
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Header, Request

from audentra.core.auth import AuthContext
from audentra.core.delegate_authorization import authorize_delegate_route
from audentra.core.errors import ApiError, BadRequestError, UnauthorizedError
from audentra.core.oidc import OidcAuthService
from audentra.core.ports import (
    BrowserAuthService,
    DelegateBrowserAuthService,
    PlatformService,
    ServiceCall,
)
from audentra.infrastructure.postgres.staff_email_service import PostgresStaffEmailService
from audentra.infrastructure.voice import VoiceSessionServiceProtocol

from .config import HttpSettings
from .demo_identity import DEMO_STUDENT_COOKIE, read_demo_student_cookie

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")

# This header only chooses between two independently authenticated browser
# sessions.  It never grants access on its own: the selected HTTP-only cookie
# is still resolved and authorized below.  Keeping the choice per tab prevents
# a parent-link tab from changing the identity used by an already-open student
# portal tab on the same origin.
PORTAL_SESSION_MODE_HEADER = "x-audentra-session-mode"


def get_settings(request: Request) -> HttpSettings:
    return cast(HttpSettings, request.app.state.http_settings)


def get_platform_service(request: Request) -> PlatformService:
    return cast(PlatformService, request.app.state.platform_service)


def get_browser_auth_service(request: Request) -> BrowserAuthService:
    return cast(BrowserAuthService, request.app.state.browser_auth_service)


def get_staff_email_service(request: Request) -> PostgresStaffEmailService:
    service = getattr(request.app.state, "staff_email_service", None)
    if service is None:
        raise ApiError(503, "STAFF_EMAIL_UNAVAILABLE", "Institutional email is unavailable")
    return cast(PostgresStaffEmailService, service)


def get_oidc_auth_service(request: Request) -> OidcAuthService:
    return cast(OidcAuthService, request.app.state.oidc_auth_service)


def _valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return True


async def resolve_request_tenant(request: Request) -> tuple[str, str | None]:
    settings = get_settings(request)
    # Deployed OIDC is currently a single-server-configured-tenant proof. Never
    # let a browser select its authentication tenant with a demo header.
    explicit_tenant_id = None
    if settings.auth_mode == "oidc":
        tenant_id = settings.oidc_tenant_id
        if tenant_id is None:
            raise ApiError(
                503,
                "OIDC_TENANT_NOT_CONFIGURED",
                "The institutional sign-in tenant is not configured",
            )
    else:
        explicit_tenant_id = request.headers.get("x-demo-tenant-id")
        tenant_id = explicit_tenant_id or settings.demo_tenant_id
    if not _valid_uuid(tenant_id):
        raise UnauthorizedError("Demo identity headers must be valid UUIDs")

    try:
        bootstrap = await get_platform_service(request).dispatch(
            ServiceCall(
                operation="public.get_tenant_bootstrap",
                auth=None,
                request_id=getattr(request.state, "request_id", "tenant-resolution"),
                path_params={"tenantId": tenant_id},
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
        or not resolved_tenant_slug
    ):
        raise ApiError(
            503,
            "TENANT_CONFIGURATION_INVALID",
            "Tenant configuration returned an invalid tenant identity",
        )
    if explicit_tenant_id is not None and UUID(explicit_tenant_id) != UUID(resolved_tenant_id):
        raise UnauthorizedError("The configured tenant conflicts with the tenant identity header")
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
            # `AUTH_MODE=oidc` governs student sign-in.  Staff federation can
            # be configured independently, so do not reject a valid staff
            # session solely because students use institutional sign-in.  Old
            # password-backed staff sessions still cannot cross that boundary.
            if (
                settings.auth_mode == "oidc"
                and staff_session.context.authentication_method == "credentials"
            ):
                raise UnauthorizedError("The staff session must use institutional sign-in")
            # A demo staff session is a development fixture. The adapter already
            # refuses it when development flows are off; this is the HTTP-side
            # twin so a production deployment never honours one by accident.
            if staff_session.context.authentication_method == "demo" and (
                settings.environment == "production" or settings.auth_mode != "demo"
            ):
                raise UnauthorizedError("Development staff sessions are not available")
            return staff_session.context
        if settings.browser_auth_required:
            raise UnauthorizedError("Staff authentication is required")
        if request.headers.get("x-demo-actor-type") != "staff":
            raise UnauthorizedError("Staff routes require the development staff identity header")
    else:
        requested_mode = request.headers.get(PORTAL_SESSION_MODE_HEADER, "").strip().lower()
        if requested_mode == "delegate":
            delegate_token = request.cookies.get("vv_delegate_session")
            if delegate_token is None:
                raise UnauthorizedError("The parent or guardian session is invalid or has expired")
            delegate_session = await cast(
                DelegateBrowserAuthService, auth_service
            ).resolve_delegate(
                delegate_token,
                tenant_id,
                tenant_slug,
            )
            if delegate_session is None:
                raise UnauthorizedError("The parent or guardian session is invalid or has expired")
            authorize_delegate_route(
                delegate_session.context,
                request.method,
                request.url.path,
            )
            return delegate_session.context

        # Student is the deliberate safe default.  A delegate cookie may be
        # present because another tab opened a secure link, but it must never
        # take over ordinary student requests merely by existing.
        credential_token = request.cookies.get("vv_session")
        if credential_token is not None:
            student_session = await auth_service.resolve_student(
                credential_token,
                tenant_id,
                tenant_slug,
            )
            if student_session is None:
                raise UnauthorizedError("The student session is invalid or has expired")
            if (
                settings.auth_mode == "oidc"
                and student_session.context.authentication_method != "oidc"
            ):
                raise UnauthorizedError("The student session must use institutional sign-in")
            return student_session.context

        demo_token = request.cookies.get("vv_demo_session")
        if (
            demo_token is not None
            and settings.auth_mode == "demo"
            and settings.environment != "production"
        ):
            if not secrets.compare_digest(demo_token, settings.demo_session_token):
                raise UnauthorizedError("The student session is invalid or has expired")
            chosen_student = read_demo_student_cookie(
                settings.demo_session_token,
                tenant_id,
                request.cookies.get(DEMO_STUDENT_COOKIE),
            )
            if chosen_student is not None:
                # Resolved through the repository, not trusted from the
                # cookie: the signature says which student was chosen, the
                # query says whether that student is this tenant's to open.
                return (
                    await auth_service.demo_student_by_reference(
                        tenant_id, tenant_slug, chosen_student
                    )
                ).context
            return (await auth_service.demo_student(tenant_id, tenant_slug)).context
        if settings.browser_auth_required or settings.auth_mode == "oidc":
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
OidcAuthServiceDependency = Annotated[OidcAuthService, Depends(get_oidc_auth_service)]
ServiceDependency = Annotated[PlatformService, Depends(get_platform_service)]
IdempotencyDependency = Annotated[str, Depends(require_idempotency_key)]
WorkerTokenDependency = Annotated[None, Depends(require_worker_token)]
VoiceAgentTokenDependency = Annotated[None, Depends(require_voice_agent_token)]
VoiceSessionServiceDependency = Annotated[
    VoiceSessionServiceProtocol, Depends(get_voice_session_service)
]
StaffEmailServiceDependency = Annotated[PostgresStaffEmailService, Depends(get_staff_email_service)]
