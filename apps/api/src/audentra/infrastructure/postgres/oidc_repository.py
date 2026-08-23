"""PostgreSQL-backed Google and Microsoft OpenID Connect login."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
import jwt
from jwt import PyJWK
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.oidc import (
    OidcAuthorizationStart,
    OidcFlowError,
    OidcLogin,
    OidcProvider,
    OidcProviderSummary,
)

_AUTHORIZATION_LIFETIME = timedelta(minutes=10)
_SESSION_LIFETIME = timedelta(days=7)
_MAXIMUM_SESSIONS = 5
_JWKS_CACHE_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class OidcProviderConfig:
    id: OidcProvider
    label: str
    client_id: str
    client_secret: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    tenant_id: str | None = None


@dataclass(frozen=True, slots=True)
class OidcSettings:
    callback_base_url: str
    portal_base_url: str
    providers: tuple[OidcProviderConfig, ...]


class PostgresOidcAuth:
    """Authorization-code OIDC with PKCE and one-time database transactions."""

    def __init__(
        self,
        engine: AsyncEngine,
        client: httpx.AsyncClient,
        settings: OidcSettings,
    ) -> None:
        self._engine = engine
        self._client = client
        self._settings = settings
        self._providers = {provider.id: provider for provider in settings.providers}
        self._jwks: dict[OidcProvider, tuple[float, Mapping[str, Any]]] = {}

    def providers(self) -> tuple[OidcProviderSummary, ...]:
        return tuple(
            OidcProviderSummary(id=provider.id, label=provider.label)
            for provider in self._settings.providers
        )

    async def start(
        self,
        *,
        provider: str,
        tenant_id: str,
        return_to: str,
    ) -> OidcAuthorizationStart:
        configuration = self._provider(provider)
        safe_return_to = _validated_return_to(return_to)
        state = secrets.token_urlsafe(32)
        binding = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
        expires_at = datetime.now(UTC) + _AUTHORIZATION_LIFETIME
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    DELETE FROM oidc_authorization_transaction
                    WHERE expires_at<NOW()-INTERVAL '1 hour'
                       OR consumed_at<NOW()-INTERVAL '1 hour'
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO oidc_authorization_transaction (
                      id, tenant_id, provider, state_hash, binding_hash,
                      nonce_hash, code_verifier, return_to, expires_at
                    ) VALUES (
                      :id, :tenant_id, :provider, :state_hash, :binding_hash,
                      :nonce_hash, :code_verifier, :return_to, :expires_at
                    )
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": UUID(tenant_id),
                    "provider": configuration.id,
                    "state_hash": _digest(state),
                    "binding_hash": _digest(binding),
                    "nonce_hash": _digest(nonce),
                    "code_verifier": verifier,
                    "return_to": safe_return_to,
                    "expires_at": expires_at,
                },
            )
        authorization_parameters = {
            "client_id": configuration.client_id,
            "redirect_uri": self._callback_url(configuration.id),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        authorization_url = (
            f"{configuration.authorization_endpoint}?{urlencode(authorization_parameters)}"
        )
        return OidcAuthorizationStart(
            authorization_url=authorization_url,
            binding_token=binding,
            expires_at_epoch=int(expires_at.timestamp()),
        )

    async def complete(
        self,
        *,
        provider: str,
        state: str | None,
        code: str | None,
        provider_error: str | None,
        binding_token: str | None,
    ) -> OidcLogin:
        configuration = self._provider(provider)
        if not _bounded_secret(state) or not _bounded_secret(binding_token):
            raise OidcFlowError("invalid_request")
        transaction = await self._consume_transaction(
            configuration.id,
            cast(str, state),
            cast(str, binding_token),
        )
        if transaction is None:
            raise OidcFlowError("invalid_request")
        if provider_error is not None:
            raise OidcFlowError(
                "access_denied" if provider_error == "access_denied" else "provider_error"
            )
        if not _bounded_code(code):
            raise OidcFlowError("invalid_request")

        claims = await self._exchange_and_validate(
            configuration,
            cast(str, code),
            str(transaction["code_verifier"]),
            str(transaction["nonce_hash"]),
        )
        issuer = _validated_issuer(configuration, claims)
        subject, email = _provider_identity(configuration, claims)
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + _SESSION_LIFETIME
        await self._link_and_create_session(
            tenant_id=UUID(str(transaction["tenant_id"])),
            provider=configuration.id,
            issuer=issuer,
            subject=subject,
            email=email,
            token_hash=_digest(token),
            expires_at=expires_at,
        )
        return OidcLogin(
            session_token=token,
            expires_at_epoch=int(expires_at.timestamp()),
            provider=configuration.id,
            return_to=str(transaction["return_to"]),
        )

    def _provider(self, provider: str) -> OidcProviderConfig:
        configuration = self._providers.get(cast(OidcProvider, provider))
        if configuration is None:
            raise OidcFlowError("invalid_request")
        return configuration

    def _callback_url(self, provider: OidcProvider) -> str:
        return f"{self._settings.callback_base_url}/v1/auth/sso/{provider}/callback"

    async def _consume_transaction(
        self,
        provider: OidcProvider,
        state: str,
        binding: str,
    ) -> Mapping[str, Any] | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE oidc_authorization_transaction
                    SET consumed_at=NOW()
                    WHERE provider=:provider
                      AND state_hash=:state_hash
                      AND binding_hash=:binding_hash
                      AND consumed_at IS NULL
                      AND expires_at>NOW()
                    RETURNING tenant_id, nonce_hash, code_verifier, return_to
                    """
                ),
                {
                    "provider": provider,
                    "state_hash": _digest(state),
                    "binding_hash": _digest(binding),
                },
            )
            row = result.mappings().first()
            return cast(Mapping[str, Any] | None, row)

    async def _exchange_and_validate(
        self,
        configuration: OidcProviderConfig,
        code: str,
        verifier: str,
        expected_nonce_hash: str,
    ) -> Mapping[str, Any]:
        try:
            response = await self._client.post(
                configuration.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._callback_url(configuration.id),
                    "client_id": configuration.client_id,
                    "client_secret": configuration.client_secret,
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
            encoded_token = payload.get("id_token") if isinstance(payload, Mapping) else None
            if not isinstance(encoded_token, str) or len(encoded_token) > 16_384:
                raise ValueError("missing id token")
            header = jwt.get_unverified_header(encoded_token)
            key_id = header.get("kid")
            if (
                header.get("alg") != "RS256"
                or not isinstance(key_id, str)
                or not 1 <= len(key_id) <= 256
            ):
                raise ValueError("unsupported token header")
            jwks = await self._get_jwks(configuration)
            keys = jwks.get("keys")
            if not isinstance(keys, list):
                raise ValueError("invalid key set")
            key_data = next(
                (
                    item
                    for item in keys
                    if isinstance(item, Mapping)
                    and item.get("kid") == key_id
                    and item.get("kty") == "RSA"
                    and item.get("use", "sig") == "sig"
                ),
                None,
            )
            if key_data is None:
                self._jwks.pop(configuration.id, None)
                jwks = await self._get_jwks(configuration)
                refreshed_keys = jwks.get("keys")
                if not isinstance(refreshed_keys, list):
                    raise ValueError("invalid key set")
                key_data = next(
                    (
                        item
                        for item in refreshed_keys
                        if isinstance(item, Mapping)
                        and item.get("kid") == key_id
                        and item.get("kty") == "RSA"
                        and item.get("use", "sig") == "sig"
                    ),
                    None,
                )
            if key_data is None:
                raise ValueError("signing key not found")
            signing_key = PyJWK.from_dict(dict(key_data), algorithm="RS256").key
            claims = jwt.decode(
                encoded_token,
                key=signing_key,
                algorithms=["RS256"],
                audience=configuration.client_id,
                leeway=60,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub", "nonce"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": False,
                },
            )
            nonce = claims.get("nonce")
            if not isinstance(nonce, str) or not secrets.compare_digest(
                _digest(nonce), expected_nonce_hash
            ):
                raise ValueError("nonce mismatch")
            audiences = claims.get("aud")
            authorized_party = claims.get("azp")
            if (authorized_party is not None and authorized_party != configuration.client_id) or (
                isinstance(audiences, list)
                and len(audiences) > 1
                and authorized_party != configuration.client_id
            ):
                raise ValueError("authorized party mismatch")
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject or len(subject) > 255:
                raise ValueError("invalid subject")
            return cast(Mapping[str, Any], claims)
        except OidcFlowError:
            raise
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError, KeyError) as error:
            raise OidcFlowError("provider_error") from error

    async def _get_jwks(self, configuration: OidcProviderConfig) -> Mapping[str, Any]:
        cached = self._jwks.get(configuration.id)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]
        response = await self._client.get(
            configuration.jwks_uri,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("invalid key set")
        document = cast(Mapping[str, Any], payload)
        self._jwks[configuration.id] = (now + _JWKS_CACHE_SECONDS, document)
        return document

    async def _link_and_create_session(
        self,
        *,
        tenant_id: UUID,
        provider: OidcProvider,
        issuer: str,
        subject: str,
        email: str | None,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        try:
            async with self._engine.begin() as connection:
                account_id = await self._resolve_or_link_account(
                    connection,
                    tenant_id=tenant_id,
                    provider=provider,
                    issuer=issuer,
                    subject=subject,
                    email=email,
                )
                await connection.execute(
                    text(
                        """
                        UPDATE credential_account
                        SET last_signed_in_at=NOW(), failed_sign_in_count=0,
                            locked_until=NULL, updated_at=NOW()
                        WHERE id=:account_id
                        """
                    ),
                    {"account_id": account_id},
                )
                await connection.execute(
                    text(
                        """
                        UPDATE auth_session SET revoked_at=NOW()
                        WHERE id IN (
                          SELECT id FROM auth_session
                          WHERE account_id=:account_id
                            AND revoked_at IS NULL AND expires_at>NOW()
                          ORDER BY created_at DESC
                          OFFSET :keep_count
                        )
                        """
                    ),
                    {"account_id": account_id, "keep_count": _MAXIMUM_SESSIONS - 1},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO auth_session (
                          id, account_id, token_hash, expires_at,
                          authentication_method, identity_provider
                        ) VALUES (
                          :id, :account_id, :token_hash, :expires_at, 'oidc', :provider
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "account_id": account_id,
                        "token_hash": token_hash,
                        "expires_at": expires_at,
                        "provider": provider,
                    },
                )
        except IntegrityError as error:
            raise OidcFlowError("account_not_linked") from error

    async def _resolve_or_link_account(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: UUID,
        provider: OidcProvider,
        issuer: str,
        subject: str,
        email: str | None,
    ) -> UUID:
        mapped = await connection.execute(
            text(
                """
                SELECT account.id
                FROM federated_identity identity
                JOIN credential_account account
                  ON account.id=identity.credential_account_id
                 AND account.tenant_id=identity.tenant_id
                JOIN tenant ON tenant.id=account.tenant_id
                WHERE identity.tenant_id=:tenant_id
                  AND identity.provider=:provider
                  AND identity.issuer=:issuer
                  AND identity.subject=:subject
                  AND account.tenant_id=:tenant_id
                  AND account.status='active'
                  AND tenant.status='active'
                FOR UPDATE OF account
                """
            ),
            {
                "provider": provider,
                "issuer": issuer,
                "subject": subject,
                "tenant_id": tenant_id,
            },
        )
        account_id = mapped.scalar_one_or_none()
        if account_id is not None:
            await connection.execute(
                text(
                    """
                    UPDATE federated_identity
                    SET email_normalized=COALESCE(:email, email_normalized),
                        last_authenticated_at=NOW(), updated_at=NOW()
                    WHERE tenant_id=:tenant_id
                      AND provider=:provider AND issuer=:issuer AND subject=:subject
                    """
                ),
                {
                    "email": email,
                    "tenant_id": tenant_id,
                    "provider": provider,
                    "issuer": issuer,
                    "subject": subject,
                },
            )
            return UUID(str(account_id))

        # Microsoft email and preferred_username claims are mutable identifiers,
        # not proof that the Entra principal owns a credential-account address.
        # Microsoft identities must therefore be provisioned by stable tid+oid.
        if provider != "google" or email is None:
            raise OidcFlowError("account_not_linked")

        candidate = await connection.execute(
            text(
                """
                SELECT account.id
                FROM credential_account account
                JOIN tenant ON tenant.id=account.tenant_id
                WHERE account.tenant_id=:tenant_id
                  AND account.email_normalized=:email
                  AND account.status='active'
                  AND tenant.status='active'
                  AND NOT EXISTS (
                    SELECT 1 FROM federated_identity existing
                    WHERE existing.credential_account_id=account.id
                  )
                FOR UPDATE OF account
                """
            ),
            {"tenant_id": tenant_id, "email": email},
        )
        account_id = candidate.scalar_one_or_none()
        if account_id is None:
            raise OidcFlowError("account_not_linked")
        await connection.execute(
            text(
                """
                INSERT INTO federated_identity (
                  id, tenant_id, credential_account_id, provider, issuer,
                  subject, email_normalized
                ) VALUES (
                  :id, :tenant_id, :account_id, :provider, :issuer, :subject, :email
                )
                """
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "account_id": account_id,
                "provider": provider,
                "issuer": issuer,
                "subject": subject,
                "email": email,
            },
        )
        return UUID(str(account_id))


def _validated_issuer(
    configuration: OidcProviderConfig,
    claims: Mapping[str, Any],
) -> str:
    issuer = claims.get("iss")
    allowed = {configuration.issuer}
    if configuration.id == "google":
        allowed.add("accounts.google.com")
    if not isinstance(issuer, str) or issuer not in allowed:
        raise OidcFlowError("provider_error")
    # Persist one canonical value so Google's two valid issuer spellings cannot
    # create two mappings for the same stable subject.
    return configuration.issuer


def _provider_identity(
    configuration: OidcProviderConfig,
    claims: Mapping[str, Any],
) -> tuple[str, str | None]:
    if configuration.id == "google":
        return str(claims["sub"]), _verified_email(claims)

    tenant_id = claims.get("tid")
    object_id = claims.get("oid")
    try:
        normalized_tenant = str(UUID(str(tenant_id)))
        normalized_object = str(UUID(str(object_id)))
    except (TypeError, ValueError, AttributeError) as error:
        raise OidcFlowError("provider_error") from error
    if configuration.tenant_id is None or not secrets.compare_digest(
        normalized_tenant, configuration.tenant_id
    ):
        raise OidcFlowError("provider_error")
    return normalized_object, None


def _verified_email(claims: Mapping[str, Any]) -> str:
    email = claims.get("email")
    verified = claims.get("email_verified")
    if verified == "true":
        verified = True
    if not isinstance(email, str) or verified is not True:
        raise OidcFlowError("account_not_linked")
    normalized = unicodedata.normalize("NFKC", email).strip().lower()
    if not normalized or len(normalized) > 254 or "@" not in normalized:
        raise OidcFlowError("account_not_linked")
    return normalized


def _validated_return_to(value: str) -> str:
    candidate = value.strip()
    if candidate != "/dashboard":
        raise OidcFlowError("invalid_request")
    return candidate


def _bounded_secret(value: str | None) -> bool:
    return isinstance(value, str) and 16 <= len(value) <= 512


def _bounded_code(value: str | None) -> bool:
    return isinstance(value, str) and 8 <= len(value) <= 4096


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
