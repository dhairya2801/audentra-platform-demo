"""Configuration for the HTTP boundary only."""

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal
from uuid import UUID

DEFAULT_TENANT_SLUG_IDS = MappingProxyType(
    {
        "aster": "00000000-0000-7000-8000-000000000001",
        "harvard": "00000000-0000-7000-8000-000000000002",
    }
)
DEFAULT_DEMO_STUDENT_SLUG_IDS = MappingProxyType(
    {
        "aster": "00000000-0000-7000-8000-000000000101",
        "harvard": "80000000-0000-7000-8000-000000000101",
    }
)
TENANT_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def parse_tenant_slug_ids(raw_value: str | None) -> Mapping[str, str]:
    if raw_value is None:
        return DEFAULT_TENANT_SLUG_IDS
    try:
        candidate = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise ValueError("TENANT_SLUG_MAP must be a JSON object") from error
    if not isinstance(candidate, dict):
        raise ValueError("TENANT_SLUG_MAP must be a JSON object")

    validated: dict[str, str] = {}
    for slug, tenant_id in candidate.items():
        if not isinstance(slug, str) or not TENANT_SLUG_PATTERN.fullmatch(slug):
            raise ValueError("TENANT_SLUG_MAP contains an invalid tenant slug")
        if not isinstance(tenant_id, str):
            raise ValueError("TENANT_SLUG_MAP tenant IDs must be UUID strings")
        try:
            validated[slug] = str(UUID(tenant_id))
        except ValueError as error:
            raise ValueError("TENANT_SLUG_MAP tenant IDs must be UUID strings") from error
    return MappingProxyType(validated)


@dataclass(frozen=True, slots=True)
class HttpSettings:
    environment: Literal["development", "preview", "test", "production"] = "development"
    browser_auth_required: bool = False
    web_origins: tuple[str, ...] = ("http://localhost:3000",)
    # Security note: production composition rejects this explicit local-only fallback.
    document_worker_token: str = "local-development-document-worker-token"  # noqa: S105
    demo_tenant_id: str = "00000000-0000-7000-8000-000000000001"
    demo_student_id: str = "00000000-0000-7000-8000-000000000101"
    demo_actor_id: str = "00000000-0000-7000-8000-000000000100"
    demo_staff_actor_id: str = "00000000-0000-7000-8000-000000000901"
    demo_session_token: str = "demo-session-v2"  # noqa: S105
    tenant_slug_ids: Mapping[str, str] = field(default_factory=lambda: DEFAULT_TENANT_SLUG_IDS)
    demo_student_slug_ids: Mapping[str, str] = field(
        default_factory=lambda: DEFAULT_DEMO_STUDENT_SLUG_IDS
    )

    def __post_init__(self) -> None:
        validated: dict[str, str] = {}
        for slug, tenant_id in self.tenant_slug_ids.items():
            if not TENANT_SLUG_PATTERN.fullmatch(slug):
                raise ValueError("tenant_slug_ids contains an invalid tenant slug")
            try:
                validated[slug] = str(UUID(tenant_id))
            except ValueError as error:
                raise ValueError("tenant_slug_ids tenant IDs must be UUID strings") from error
        object.__setattr__(self, "tenant_slug_ids", MappingProxyType(validated))
        validated_students: dict[str, str] = {}
        for slug, student_id in self.demo_student_slug_ids.items():
            if not TENANT_SLUG_PATTERN.fullmatch(slug):
                raise ValueError("demo_student_slug_ids contains an invalid tenant slug")
            try:
                validated_students[slug] = str(UUID(student_id))
            except ValueError as error:
                raise ValueError(
                    "demo_student_slug_ids student IDs must be UUID strings"
                ) from error
        object.__setattr__(
            self,
            "demo_student_slug_ids",
            MappingProxyType(validated_students),
        )

    @property
    def secure_cookies(self) -> bool:
        return self.environment in {"preview", "production"}

    def demo_student_for(self, tenant_slug: str | None) -> str:
        if tenant_slug is None:
            return self.demo_student_id
        return self.demo_student_slug_ids.get(tenant_slug, self.demo_student_id)

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
            demo_tenant_id=os.getenv("DEMO_TENANT_ID", "00000000-0000-7000-8000-000000000001"),
            demo_student_id=os.getenv("DEMO_STUDENT_ID", "00000000-0000-7000-8000-000000000101"),
            demo_actor_id=os.getenv("DEMO_ACTOR_ID", "00000000-0000-7000-8000-000000000100"),
            demo_staff_actor_id=os.getenv(
                "DEMO_STAFF_ACTOR_ID", "00000000-0000-7000-8000-000000000901"
            ),
            tenant_slug_ids=parse_tenant_slug_ids(os.getenv("TENANT_SLUG_MAP")),
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
