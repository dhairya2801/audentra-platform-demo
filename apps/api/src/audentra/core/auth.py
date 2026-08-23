"""Authentication values that do not depend on an HTTP framework."""

from dataclasses import dataclass
from typing import Literal, TypeAlias

PortalScope: TypeAlias = Literal[
    "dashboard",
    "enrollment",
    "financials",
    "classrooms",
    "campus_life",
    "edward",
    "documents",
    "messages",
    "appointments",
    "payments",
    "profile",
    "help",
]

PORTAL_SCOPES: tuple[PortalScope, ...] = (
    "dashboard",
    "enrollment",
    "financials",
    "classrooms",
    "campus_life",
    "edward",
    "documents",
    "messages",
    "appointments",
    "payments",
    "profile",
    "help",
)

# ``onboarding`` was briefly exposed as a separate delegate page. Keep it
# readable only while old authorizations are migrated, then canonicalize it to
# My Enrollment at the authentication boundary. It is intentionally not a
# grantable portal scope.
LEGACY_PORTAL_SCOPE_ALIASES: dict[str, PortalScope] = {
    "onboarding": "enrollment",
}
LEGACY_PORTAL_SCOPES = frozenset((*PORTAL_SCOPES, *LEGACY_PORTAL_SCOPE_ALIASES))


@dataclass(frozen=True, slots=True)
class AuthContext:
    """The already-authenticated actor and tenant boundary for one command."""

    tenant_id: str
    student_id: str
    actor_id: str
    actor_type: Literal["student", "staff", "delegate"]
    authentication_method: Literal[
        "demo", "credentials", "oidc", "google", "microsoft", "delegate_link"
    ] = "demo"
    identity_provider: Literal["google", "microsoft"] | None = None
    tenant_slug: str | None = None
    delegate_scopes: frozenset[PortalScope] = frozenset()
    delegate_relationship: str | None = None
    delegate_name: str | None = None
    subject_student_name: str | None = None

    @property
    def is_delegate(self) -> bool:
        return self.actor_type == "delegate"
