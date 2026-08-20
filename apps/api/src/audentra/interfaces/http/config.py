"""Configuration for the HTTP boundary only."""

import os
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

CookieSameSite = Literal["lax", "none", "strict"]
HttpEnvironment = Literal["development", "preview", "test", "production"]


@dataclass(frozen=True, slots=True)
class HttpSettings:
    environment: HttpEnvironment = "development"
    auth_mode: Literal["demo", "oidc"] = "demo"
    browser_auth_required: bool = False
    web_origins: tuple[str, ...] = ("http://localhost:3000",)
    session_cookie_samesite: CookieSameSite = "lax"
    # Security note: production composition rejects this explicit local-only fallback.
    document_worker_token: str = "local-development-document-worker-token"  # noqa: S105
    # Empty means voice is not configured: internal voice routes then fail closed.
    voice_agent_internal_token: str = ""
    demo_tenant_id: str = "00000000-0000-7000-8000-000000000001"
    demo_student_id: str = "00000000-0000-7000-8000-000000000101"
    demo_actor_id: str = "00000000-0000-7000-8000-000000000100"
    demo_staff_actor_id: str = "00000000-0000-7000-8000-000000000901"
    oidc_tenant_id: str | None = None
    oidc_portal_base_url: str = ""
    demo_session_token: str = "demo-session-v2"  # noqa: S105
    # Developer-only trace inspection; production composition rejects it.
    assistant_trace_debug_enabled: bool = False

    @property
    def secure_cookies(self) -> bool:
        return self.environment in {"preview", "production"}

    @classmethod
    def from_environment(cls) -> "HttpSettings":
        environment = (
            os.getenv("AUDENTRA_ENV", os.getenv("NODE_ENV", "development")).strip().lower()
        )
        if environment not in {"development", "preview", "test", "production"}:
            raise ValueError("AUDENTRA_ENV must be development, preview, test, or production")
        origins = tuple(
            origin.strip()
            for origin in os.getenv("WEB_ORIGIN", "http://localhost:3000").split(",")
            if origin.strip()
        )
        return cls(
            environment=environment,  # type: ignore[arg-type]
            auth_mode=_auth_mode(os.getenv("AUTH_MODE")),
            browser_auth_required=_boolean_environment(os.getenv("BROWSER_AUTH_REQUIRED"), False),
            assistant_trace_debug_enabled=_boolean_environment(
                os.getenv("ASSISTANT_TRACE_DEBUG_ENABLED"),
                environment in {"development", "test"},
            ),
            web_origins=origins or ("http://localhost:3000",),
            session_cookie_samesite=parse_session_cookie_samesite(
                os.getenv("SESSION_COOKIE_SAMESITE"),
                environment=environment,  # type: ignore[arg-type]
            ),
            document_worker_token=(
                os.getenv("DOCUMENT_WORKER_TOKEN", "").strip()
                or "local-development-document-worker-token"
            ),
            voice_agent_internal_token=os.getenv("VOICE_AGENT_INTERNAL_TOKEN", "").strip(),
            demo_tenant_id=os.getenv("DEMO_TENANT_ID", "00000000-0000-7000-8000-000000000001"),
            demo_student_id=os.getenv("DEMO_STUDENT_ID", "00000000-0000-7000-8000-000000000101"),
            demo_actor_id=os.getenv("DEMO_ACTOR_ID", "00000000-0000-7000-8000-000000000100"),
            demo_staff_actor_id=os.getenv(
                "DEMO_STAFF_ACTOR_ID", "00000000-0000-7000-8000-000000000901"
            ),
            oidc_tenant_id=_oidc_tenant_id(
                os.getenv("OIDC_AUDENTRA_TENANT_ID"),
                auth_mode=_auth_mode(os.getenv("AUTH_MODE")),
            ),
            oidc_portal_base_url=(
                os.getenv("OIDC_PORTAL_BASE_URL", "").strip().rstrip("/")
                or os.getenv("OIDC_PUBLIC_BASE_URL", "").strip().rstrip("/")
                or os.getenv("OIDC_CALLBACK_BASE_URL", "").strip().rstrip("/")
            ),
        )


def parse_session_cookie_samesite(
    value: str | None,
    *,
    environment: HttpEnvironment,
) -> CookieSameSite:
    default = "none" if environment == "preview" else "lax"
    normalized = (value or "").strip().lower() or default
    if normalized not in {"lax", "none", "strict"}:
        raise ValueError("SESSION_COOKIE_SAMESITE must be lax, none, or strict")
    if normalized == "none" and environment != "preview":
        raise ValueError("SESSION_COOKIE_SAMESITE=none is allowed only in preview")
    if environment == "production" and normalized != "lax":
        raise ValueError("SESSION_COOKIE_SAMESITE must remain lax in production")
    return normalized  # type: ignore[return-value]


def _boolean_environment(value: str | None, fallback: bool) -> bool:
    if value is None or not value.strip():
        return fallback
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Boolean environment values must be true or false")


def _auth_mode(value: str | None) -> Literal["demo", "oidc"]:
    normalized = (value or "demo").strip().lower()
    if normalized not in {"demo", "oidc"}:
        raise ValueError("AUTH_MODE must be demo or oidc")
    return normalized  # type: ignore[return-value]


def _oidc_tenant_id(value: str | None, *, auth_mode: Literal["demo", "oidc"]) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        if auth_mode == "oidc":
            raise ValueError("AUTH_MODE=oidc requires OIDC_AUDENTRA_TENANT_ID")
        return None
    try:
        return str(UUID(candidate))
    except ValueError as error:
        raise ValueError("OIDC_AUDENTRA_TENANT_ID must be a tenant UUID") from error
