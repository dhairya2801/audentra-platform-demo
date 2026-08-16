"""Ports used by the FastAPI adapter to call framework-neutral application code."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NoReturn, Protocol, runtime_checkable

from .assistant_execution import (
    DEFAULT_ASSISTANT_EXECUTION,
    ResolvedAssistantExecutionMode,
)
from .auth import AuthContext


@dataclass(frozen=True, slots=True)
class FileUpload:
    file_name: str
    mime_type: str
    content: bytes
    category: str | None = None
    requirement_id: str | None = None
    upload_bundle_id: str | None = None


@dataclass(frozen=True, slots=True)
class BinaryPayload:
    data: bytes
    media_type: str
    file_name: str
    cache_control: str


@dataclass(frozen=True, slots=True)
class DemoStudentSession:
    context: AuthContext
    preferred_name: str


@dataclass(frozen=True, slots=True)
class CredentialStudentSession:
    context: AuthContext
    preferred_name: str | None
    email: str
    phone: str
    email_verified: bool
    phone_verified: bool
    token: str | None = None
    expires_at_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class StaffSession:
    context: AuthContext
    name: str
    email: str
    component: str
    token: str | None = None
    expires_at_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class ServiceCall:
    operation: str
    auth: AuthContext | None
    request_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    path_params: Mapping[str, str] = field(default_factory=dict)
    query_params: Mapping[str, str] = field(default_factory=dict)
    upload: FileUpload | None = None
    idempotency_key: str | None = None
    # Development/evaluation only, and only for the assistant operations: the
    # HTTP boundary resolves this from the Lab's mode header and leaves it at
    # the default everywhere else, so an ordinary request takes exactly the
    # existing production path.
    assistant_execution: ResolvedAssistantExecutionMode = DEFAULT_ASSISTANT_EXECUTION


@runtime_checkable
class PlatformService(Protocol):
    async def dispatch(self, call: ServiceCall) -> object:
        """Execute one application operation and return a JSON-safe or binary value."""


class UnavailablePlatformService:
    async def dispatch(self, call: ServiceCall) -> object:
        from .errors import ApiError

        raise ApiError(
            503,
            "PLATFORM_SERVICE_UNAVAILABLE",
            "The platform service is not configured",
        )


@runtime_checkable
class BrowserAuthService(Protocol):
    """Development browser-session boundary; production composition stays fail-closed."""

    async def demo_student(self, tenant_id: str, tenant_slug: str | None) -> DemoStudentSession: ...

    async def resolve_student(
        self, token: str, tenant_id: str, tenant_slug: str | None
    ) -> CredentialStudentSession | None: ...

    async def sign_up_student(
        self, *, tenant_id: str, tenant_slug: str | None, email: str, phone: str, password: str
    ) -> CredentialStudentSession: ...

    async def sign_in_student(
        self, *, tenant_id: str, tenant_slug: str | None, email: str, password: str
    ) -> CredentialStudentSession: ...

    async def sign_out_student(self, token: str | None) -> None: ...

    async def resolve_staff(
        self, token: str, tenant_id: str, tenant_slug: str | None
    ) -> StaffSession | None: ...

    async def sign_up_staff(
        self,
        *,
        tenant_id: str,
        tenant_slug: str | None,
        email: str,
        password: str,
        institution_access_code: str,
    ) -> StaffSession: ...

    async def sign_in_staff(
        self, *, tenant_id: str, tenant_slug: str | None, email: str, password: str
    ) -> StaffSession: ...

    async def sign_out_staff(self, token: str | None) -> None: ...

    async def reset_demo_fixture(self, *, completed_onboarding: bool) -> None: ...


class UnavailableBrowserAuthService:
    async def demo_student(self, tenant_id: str, tenant_slug: str | None) -> DemoStudentSession:
        self._raise()

    async def resolve_student(
        self, token: str, tenant_id: str, tenant_slug: str | None
    ) -> CredentialStudentSession | None:
        return None

    async def sign_up_student(
        self, *, tenant_id: str, tenant_slug: str | None, email: str, phone: str, password: str
    ) -> CredentialStudentSession:
        self._raise()

    async def sign_in_student(
        self, *, tenant_id: str, tenant_slug: str | None, email: str, password: str
    ) -> CredentialStudentSession:
        self._raise()

    async def sign_out_student(self, token: str | None) -> None:
        return None

    async def resolve_staff(
        self, token: str, tenant_id: str, tenant_slug: str | None
    ) -> StaffSession | None:
        return None

    async def sign_up_staff(
        self,
        *,
        tenant_id: str,
        tenant_slug: str | None,
        email: str,
        password: str,
        institution_access_code: str,
    ) -> StaffSession:
        self._raise()

    async def sign_in_staff(
        self, *, tenant_id: str, tenant_slug: str | None, email: str, password: str
    ) -> StaffSession:
        self._raise()

    async def sign_out_staff(self, token: str | None) -> None:
        return None

    async def reset_demo_fixture(self, *, completed_onboarding: bool) -> None:
        self._raise()

    @staticmethod
    def _raise() -> NoReturn:
        from .errors import ApiError

        raise ApiError(
            503,
            "AUTH_SERVICE_UNAVAILABLE",
            "The development authentication service is not configured",
        )
