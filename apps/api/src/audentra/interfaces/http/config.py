"""Configuration for the HTTP boundary only."""

import os
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class HttpSettings:
    environment: Literal["development", "preview", "test", "production"] = "development"
    browser_auth_required: bool = False
    web_origins: tuple[str, ...] = ("http://localhost:3000",)
    # Security note: production composition rejects this explicit local-only fallback.
    document_worker_token: str = "local-development-document-worker-token"  # noqa: S105
    # Empty means voice is not configured: internal voice routes then fail closed.
    voice_agent_internal_token: str = ""
    demo_tenant_id: str = "00000000-0000-7000-8000-000000000001"
    demo_student_id: str = "00000000-0000-7000-8000-000000000101"
    demo_actor_id: str = "00000000-0000-7000-8000-000000000100"
    demo_staff_actor_id: str = "00000000-0000-7000-8000-000000000901"
    demo_session_token: str = "demo-session-v2"  # noqa: S105

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
            browser_auth_required=_boolean_environment(os.getenv("BROWSER_AUTH_REQUIRED"), False),
            web_origins=origins or ("http://localhost:3000",),
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
        )


def _boolean_environment(value: str | None, fallback: bool) -> bool:
    if value is None or not value.strip():
        return fallback
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("BROWSER_AUTH_REQUIRED must be true or false")
