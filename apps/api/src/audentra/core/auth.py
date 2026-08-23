"""Authentication values that do not depend on an HTTP framework."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class AuthContext:
    """The already-authenticated actor and tenant boundary for one command."""

    tenant_id: str
    student_id: str
    actor_id: str
    actor_type: Literal["student", "staff"]
    authentication_method: Literal["demo", "credentials", "oidc", "google", "microsoft"] = "demo"
    identity_provider: Literal["google", "microsoft"] | None = None
    tenant_slug: str | None = None
