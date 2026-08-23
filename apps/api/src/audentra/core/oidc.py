"""Framework-neutral values for browser OpenID Connect authentication."""

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

OidcProvider = Literal["google", "microsoft"]
OidcErrorCode = Literal[
    "access_denied",
    "invalid_request",
    "account_not_linked",
    "provider_error",
]


@dataclass(frozen=True, slots=True)
class OidcProviderSummary:
    id: OidcProvider
    label: str


@dataclass(frozen=True, slots=True)
class OidcAuthorizationStart:
    authorization_url: str
    binding_token: str
    expires_at_epoch: int


@dataclass(frozen=True, slots=True)
class OidcLogin:
    session_token: str
    expires_at_epoch: int
    provider: OidcProvider
    return_to: str


class OidcFlowError(Exception):
    """A browser-safe OIDC failure with a deliberately small public vocabulary."""

    def __init__(self, code: OidcErrorCode) -> None:
        super().__init__(code)
        self.code = code


@runtime_checkable
class OidcAuthService(Protocol):
    def providers(self) -> tuple[OidcProviderSummary, ...]: ...

    async def start(
        self,
        *,
        provider: str,
        tenant_id: str,
        return_to: str,
    ) -> OidcAuthorizationStart: ...

    async def complete(
        self,
        *,
        provider: str,
        state: str | None,
        code: str | None,
        provider_error: str | None,
        binding_token: str | None,
    ) -> OidcLogin: ...


class UnavailableOidcAuthService:
    def providers(self) -> tuple[OidcProviderSummary, ...]:
        return ()

    async def start(
        self,
        *,
        provider: str,
        tenant_id: str,
        return_to: str,
    ) -> OidcAuthorizationStart:
        del provider, tenant_id, return_to
        raise OidcFlowError("invalid_request")

    async def complete(
        self,
        *,
        provider: str,
        state: str | None,
        code: str | None,
        provider_error: str | None,
        binding_token: str | None,
    ) -> OidcLogin:
        del provider, state, code, provider_error, binding_token
        raise OidcFlowError("invalid_request")
