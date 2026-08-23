"""FastAPI application factory and HTTP composition root."""

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from starlette.types import Lifespan

from audentra.core.assistant_execution import (
    ASSISTANT_EXECUTION_MODE_HEADER,
    lab_execution_controls_enabled,
)
from audentra.core.oidc import OidcAuthService, UnavailableOidcAuthService
from audentra.core.ports import (
    BrowserAuthService,
    PlatformService,
    UnavailableBrowserAuthService,
    UnavailablePlatformService,
)
from audentra.infrastructure.voice import (
    UnavailableVoiceSessionService,
    VoiceSessionServiceProtocol,
)

from .access_logging import install_access_log_redaction
from .auth_routes import auth_router
from .config import HttpSettings
from .dependencies import PORTAL_SESSION_MODE_HEADER
from .error_handlers import install_error_handlers
from .mail_routes import mail_router
from .middleware import OidcCallbackQueryRedactionMiddleware, RequestContextMiddleware
from .routes import router

ALLOWED_HEADERS = [
    "Content-Type",
    "Idempotency-Key",
    "X-Request-Id",
    "X-Correlation-Id",
    "X-Demo-Tenant-Id",
    "X-Demo-Student-Id",
    "X-Demo-Actor-Id",
    "X-Demo-Actor-Type",
    "X-VV-Worker-Token",
    PORTAL_SESSION_MODE_HEADER,
]


def create_app(
    service: PlatformService | None = None,
    auth_service: BrowserAuthService | None = None,
    oidc_auth_service: OidcAuthService | None = None,
    settings: HttpSettings | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
    voice_service: VoiceSessionServiceProtocol | None = None,
) -> FastAPI:
    """Build an isolated API instance with injected application behavior."""

    install_access_log_redaction()
    app = FastAPI(
        title="Audentra Platform API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.state.http_settings = settings or HttpSettings.from_environment()
    app.state.platform_service = service or UnavailablePlatformService()
    app.state.browser_auth_service = auth_service or UnavailableBrowserAuthService()
    app.state.oidc_auth_service = oidc_auth_service or UnavailableOidcAuthService()
    app.state.staff_email_service = None
    app.state.voice_session_service = voice_service or UnavailableVoiceSessionService()

    app.include_router(auth_router)
    app.include_router(mail_router)
    app.include_router(router)
    install_error_handlers(app)
    # The Lab's execution-mode header is only ever accepted where the control
    # itself is honoured, so a deployed API never advertises it in preflight.
    allowed_headers = list(ALLOWED_HEADERS)
    if lab_execution_controls_enabled(
        environment=app.state.http_settings.environment,
        assistant_trace_debug_enabled=app.state.http_settings.assistant_trace_debug_enabled,
    ):
        allowed_headers.append(ASSISTANT_EXECUTION_MODE_HEADER)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app.state.http_settings.web_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=allowed_headers,
        expose_headers=["X-Request-Id", "X-Correlation-Id", "X-Trace-Id"],
        max_age=600,
    )
    # Added last so correlation headers also decorate CORS and error responses.
    app.add_middleware(RequestContextMiddleware)
    # Outermost user middleware: FastAPI sees a copied scope with the real OIDC
    # response parameters while the server-facing scope is clean before access
    # logging occurs at response start.
    app.add_middleware(OidcCallbackQueryRedactionMiddleware)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app
