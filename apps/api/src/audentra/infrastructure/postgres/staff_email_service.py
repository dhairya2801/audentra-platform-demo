"""Tenant-bound institutional OAuth and delegated staff mailboxes.

OAuth transactions are persisted, one-time, and bind the provider, Audentra
tenant, expected provider organization, PKCE verifier, nonce, and canonical
portal return path.  Provider callbacks never accept a tenant selector.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

import httpx
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError
from audentra.core.ports import BrowserAuthService, StaffSession

if TYPE_CHECKING:
    from audentra.bootstrap.settings import InstitutionalOAuthSettings

Provider = Literal["google", "microsoft"]

_STATE_LIFETIME = timedelta(minutes=10)
_SESSION_RETURN_RE = re.compile(r"^[A-Za-z0-9/_?&=.:~-]+$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_ENCODED_SEPARATOR_RE = re.compile(r"%(?:2f|5c|25)", re.IGNORECASE)
_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
_MICROSOFT_CONSUMER_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"
_RECONNECT_REQUIRED_ERROR = "MAILBOX_RECONNECT_REQUIRED"
_TOKEN_ACQUISITION_ERROR = "TOKEN_ACQUISITION_FAILED"  # noqa: S105 - public error code


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def canonical_staff_return_path(_tenant_slug: str, value: str | None) -> str:
    """Accept only a canonical path inside this tenant-bound staff portal."""

    fallback = "/staff"
    candidate = (value or fallback).strip()
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or "#" in candidate
        or any(ord(character) < 32 for character in candidate)
        or _ENCODED_SEPARATOR_RE.search(candidate)
        or not _SESSION_RETURN_RE.fullmatch(candidate)
    ):
        return fallback
    path = candidate.split("?", 1)[0]
    prefix = fallback
    if path != prefix and not path.startswith(prefix + "/"):
        return fallback
    if any(segment in {".", ".."} for segment in path.split("/")) or "//" in path:
        return fallback
    return candidate


class PostgresStaffEmailService:
    def __init__(
        self,
        engine: AsyncEngine,
        client: httpx.AsyncClient,
        settings: InstitutionalOAuthSettings,
        auth_service: BrowserAuthService,
    ) -> None:
        self._engine = engine
        self._client = client
        self._settings = settings
        self._auth = auth_service

    async def staff_options(self, tenant_slug: str) -> dict[str, object]:
        async with self._engine.connect() as connection:
            tenant = await self._tenant_by_slug(connection, tenant_slug)
            result = await connection.execute(
                text(
                    """
                    SELECT provider, microsoft_tenant_id FROM tenant_identity_provider
                    WHERE tenant_id=:tenant_id AND enabled=true
                    ORDER BY provider
                    """
                ),
                {"tenant_id": tenant["id"]},
            )
        providers = [
            str(row["provider"])
            for row in result.mappings().all()
            if self._provider_available(
                str(row["provider"]),
                str(row.get("microsoft_tenant_id") or ""),
            )
        ]
        return {
            "tenantSlug": str(tenant["slug"]),
            "providers": providers,
            "passwordEnabled": True,
        }

    async def start_sso(self, provider: str, tenant_slug: str, return_to: str | None) -> str:
        checked_provider = _provider(provider)
        self._require_provider(checked_provider)
        async with self._engine.begin() as connection:
            tenant = await self._tenant_by_slug(connection, tenant_slug)
            expected = await self._provider_tenant(connection, tenant["id"], checked_provider)
            if checked_provider == "microsoft":
                self._microsoft_authority(expected, mailbox=False)
            state, verifier, nonce, redirect_uri = await self._insert_transaction(
                connection,
                flow_type="staff_sso",
                provider=checked_provider,
                tenant=tenant,
                expected_provider_tenant=expected,
                return_path=canonical_staff_return_path(str(tenant["slug"]), return_to),
            )
        return self._authorization_url(
            checked_provider,
            expected,
            state=state,
            nonce=nonce,
            verifier=verifier,
            redirect_uri=redirect_uri,
            mailbox=False,
        )

    async def complete_sso(self, provider: str, state: str, code: str) -> tuple[StaffSession, str]:
        checked_provider = _provider(provider)
        transaction = await self._consume_transaction(
            state, provider=checked_provider, flow_type="staff_sso"
        )
        tokens = await self._exchange_code(checked_provider, transaction, code)
        claims = await self._verify_identity(checked_provider, transaction, tokens)
        session = await self._auth.sign_in_staff_federated(
            tenant_id=str(transaction["tenant_id"]),
            tenant_slug=str(transaction["tenant_slug"]),
            provider=checked_provider,
            provider_subject=str(claims["subject"]),
            provider_tenant=str(claims["provider_tenant"]),
            email=str(claims["email"]),
            display_name=str(claims.get("display_name") or ""),
        )
        return session, self._portal_url(str(transaction["return_path"]))

    async def start_mailbox_connect(
        self,
        auth: AuthContext,
        provider: str,
        mailbox_kind: str,
        address: str,
        return_to: str | None,
    ) -> str:
        self._require_staff(auth)
        checked_provider = _provider(provider)
        self._require_provider(checked_provider)
        kind = mailbox_kind.strip().lower()
        if kind not in {"personal", "shared"}:
            raise BadRequestError("MAILBOX_KIND_INVALID", "Mailbox kind must be personal or shared")
        normalized_address = _email(address)
        async with self._engine.begin() as connection:
            tenant = await self._tenant_by_id(connection, auth.tenant_id)
            expected = await self._provider_tenant(connection, tenant["id"], checked_provider)
            if checked_provider == "microsoft":
                self._microsoft_authority(expected, mailbox=True)
            staff = (
                (
                    await connection.execute(
                        text(
                            """
                        SELECT id, email_normalized FROM staff_member
                        WHERE tenant_id=:tenant_id AND id=:staff_id AND active=true
                        """
                        ),
                        {"tenant_id": tenant["id"], "staff_id": UUID(auth.actor_id)},
                    )
                )
                .mappings()
                .first()
            )
            if staff is None:
                raise ApiError(403, "STAFF_INACTIVE", "Active staff access is required")
            if kind == "personal" and normalized_address != str(staff["email_normalized"]):
                raise ApiError(
                    403,
                    "MAILBOX_IDENTITY_MISMATCH",
                    "A personal mailbox must match the provisioned staff email",
                )
            if kind == "shared":
                policy = (
                    await connection.execute(
                        text(
                            """
                            SELECT id FROM tenant_mailbox_policy
                            WHERE tenant_id=:tenant_id AND provider=:provider
                              AND address_normalized=:address AND mailbox_kind='shared'
                              AND active=true
                            """
                        ),
                        {
                            "tenant_id": tenant["id"],
                            "provider": checked_provider,
                            "address": normalized_address,
                        },
                    )
                ).first()
                if policy is None:
                    raise ApiError(
                        403,
                        "SHARED_MAILBOX_NOT_APPROVED",
                        "This shared mailbox is not approved for the university",
                    )
            state, verifier, nonce, redirect_uri = await self._insert_transaction(
                connection,
                flow_type="mailbox_connect",
                provider=checked_provider,
                tenant=tenant,
                expected_provider_tenant=expected,
                return_path=canonical_staff_return_path(str(tenant["slug"]), return_to),
                staff_member_id=UUID(auth.actor_id),
                mailbox_kind=kind,
                target_mailbox_address=normalized_address,
            )
        return self._authorization_url(
            checked_provider,
            expected,
            state=state,
            nonce=nonce,
            verifier=verifier,
            redirect_uri=redirect_uri,
            mailbox=True,
            login_hint=normalized_address if kind == "personal" else None,
        )

    async def complete_mailbox_connect(self, provider: str, state: str, code: str) -> str:
        checked_provider = _provider(provider)
        transaction = await self._consume_transaction(
            state, provider=checked_provider, flow_type="mailbox_connect"
        )
        tokens = await self._exchange_code(checked_provider, transaction, code)
        claims = await self._verify_identity(checked_provider, transaction, tokens)
        refresh_token = tokens.get("refresh_token")
        access_token = tokens.get("access_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ConflictError(
                "MAILBOX_REFRESH_TOKEN_MISSING",
                "Reconnect the mailbox and grant offline access",
            )
        if not isinstance(access_token, str) or not access_token:
            raise ApiError(502, "PROVIDER_TOKEN_INVALID", "The provider returned no access token")
        address = str(transaction["target_mailbox_address"])
        kind = str(transaction["mailbox_kind"])
        staff_id = str(transaction["initiating_staff_member_id"])
        scopes = _scope_values(tokens.get("scope"))
        self._require_mail_scopes(checked_provider, kind, scopes)
        if kind == "personal":
            async with self._engine.connect() as connection:
                staff_email = await connection.scalar(
                    text(
                        """
                        SELECT email_normalized FROM staff_member
                        WHERE tenant_id=:tenant_id AND id=:staff_id AND active=true
                        """
                    ),
                    {
                        "tenant_id": transaction["tenant_id"],
                        "staff_id": UUID(staff_id),
                    },
                )
            if staff_email is None or str(staff_email) != _email(str(claims["email"])):
                raise ApiError(
                    403,
                    "MAILBOX_IDENTITY_MISMATCH",
                    "The connected provider account does not match this staff record",
                )
        await self._probe_mailbox(checked_provider, address, access_token)
        ciphertext, nonce = self._encrypt(refresh_token)
        async with self._engine.begin() as connection:
            allow_read = True
            allow_send = True
            visibility = "owner"
            if kind == "shared":
                policy = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT default_visibility, allow_read, allow_send
                            FROM tenant_mailbox_policy
                            WHERE tenant_id=:tenant_id AND provider=:provider
                              AND address_normalized=:address
                              AND mailbox_kind='shared' AND active=true
                            FOR UPDATE
                            """
                            ),
                            {
                                "tenant_id": transaction["tenant_id"],
                                "provider": checked_provider,
                                "address": address,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if policy is None:
                    raise ApiError(
                        403,
                        "SHARED_MAILBOX_NOT_APPROVED",
                        "This shared mailbox is no longer approved for the university",
                    )
                visibility = str(policy["default_visibility"])
                allow_read = bool(policy["allow_read"])
                allow_send = bool(policy["allow_send"])
            authorization_id = uuid4()
            result = await connection.execute(
                text(
                    """
                    INSERT INTO staff_mail_authorization (
                      id, tenant_id, staff_member_id, provider, provider_subject,
                      provider_tenant, account_email_normalized, granted_scopes,
                      refresh_token_ciphertext, refresh_token_nonce, status,
                      last_refreshed_at
                    ) VALUES (
                      :id, :tenant_id, :staff_id, :provider, :subject,
                      :provider_tenant, :email, :scopes, :ciphertext, :nonce,
                      'active', NOW()
                    )
                    ON CONFLICT (tenant_id, staff_member_id, provider, provider_subject)
                    DO UPDATE SET granted_scopes=EXCLUDED.granted_scopes,
                      refresh_token_ciphertext=EXCLUDED.refresh_token_ciphertext,
                      refresh_token_nonce=EXCLUDED.refresh_token_nonce,
                      status='active', last_refreshed_at=NOW(), updated_at=NOW()
                    RETURNING id
                    """
                ),
                {
                    "id": authorization_id,
                    "tenant_id": transaction["tenant_id"],
                    "staff_id": UUID(staff_id),
                    "provider": checked_provider,
                    "subject": claims["subject"],
                    "provider_tenant": claims["provider_tenant"],
                    "email": _email(str(claims["email"])),
                    "scopes": scopes,
                    "ciphertext": ciphertext,
                    "nonce": nonce,
                },
            )
            authorization_id = UUID(str(result.scalar_one()))
            mailbox_id = uuid4()
            mailbox_result = await connection.execute(
                text(
                    """
                    INSERT INTO staff_mailbox (
                      id, tenant_id, authorization_id, provider,
                      provider_mailbox_id, address_normalized, mailbox_kind, status
                    ) VALUES (
                      :id, :tenant_id, :authorization_id, :provider,
                      :provider_mailbox_id, :address, :kind, 'active'
                    )
                    ON CONFLICT (tenant_id, provider, address_normalized)
                    DO UPDATE SET authorization_id=EXCLUDED.authorization_id,
                      status='active', updated_at=NOW()
                    RETURNING id
                    """
                ),
                {
                    "id": mailbox_id,
                    "tenant_id": transaction["tenant_id"],
                    "authorization_id": authorization_id,
                    "provider": checked_provider,
                    "provider_mailbox_id": address,
                    "address": address,
                    "kind": kind,
                },
            )
            mailbox_id = UUID(str(mailbox_result.scalar_one()))
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_mailbox_grant (
                      id, tenant_id, mailbox_id, principal_type,
                      staff_member_id, can_read, can_send, can_manage
                    ) VALUES (
                      :id, :tenant_id, :mailbox_id, 'staff', :staff_id,
                      :can_read, :can_send, true
                    )
                    ON CONFLICT (mailbox_id, principal_type, staff_member_id, component)
                    DO UPDATE SET can_read=EXCLUDED.can_read,
                      can_send=EXCLUDED.can_send, can_manage=true
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": transaction["tenant_id"],
                    "mailbox_id": mailbox_id,
                    "staff_id": UUID(staff_id),
                    "can_read": allow_read,
                    "can_send": allow_send,
                },
            )
            if kind == "shared" and visibility == "all_staff":
                await connection.execute(
                    text(
                        """
                            INSERT INTO staff_mailbox_grant (
                              id, tenant_id, mailbox_id, principal_type,
                              can_read, can_send, can_manage
                            ) VALUES (
                              :id, :tenant_id, :mailbox_id, 'all_staff',
                              :can_read, :can_send, false
                            )
                            ON CONFLICT (mailbox_id, principal_type, staff_member_id, component)
                            DO UPDATE SET can_read=EXCLUDED.can_read,
                              can_send=EXCLUDED.can_send, can_manage=false
                            """
                    ),
                    {
                        "id": uuid4(),
                        "tenant_id": transaction["tenant_id"],
                        "mailbox_id": mailbox_id,
                        "can_read": allow_read,
                        "can_send": allow_send,
                    },
                )
            await self._insert_outbox(
                connection,
                tenant_id=str(transaction["tenant_id"]),
                actor_id=staff_id,
                event_name="staff.mailbox_connected.v1",
                aggregate_id=str(mailbox_id),
                data={"mailboxId": str(mailbox_id), "operation": "initial_sync"},
            )
        return self._portal_url(str(transaction["return_path"]))

    async def list_mailboxes(self, auth: AuthContext) -> dict[str, object]:
        self._require_staff(auth)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT DISTINCT mailbox.id, mailbox.provider, mailbox.address_normalized,
                           mailbox.display_name, mailbox.mailbox_kind, mailbox.status,
                           mailbox.last_synced_at,
                           bool_or(mgrant.can_read) AS can_read,
                           bool_or(mgrant.can_send) AS can_send,
                           bool_or(mgrant.can_manage) AS can_manage
                    FROM staff_mailbox mailbox
                    JOIN staff_mailbox_grant mgrant
                      ON mgrant.tenant_id=mailbox.tenant_id AND mgrant.mailbox_id=mailbox.id
                    JOIN staff_member member
                      ON member.tenant_id=mailbox.tenant_id AND member.id=:staff_id
                    WHERE mailbox.tenant_id=:tenant_id AND member.active=true
                      AND (
                        mgrant.principal_type='all_staff'
                        OR (mgrant.principal_type='staff' AND mgrant.staff_member_id=:staff_id)
                        OR (mgrant.principal_type='component' AND mgrant.component=member.component)
                      )
                    GROUP BY mailbox.id
                    ORDER BY mailbox.address_normalized
                    """
                ),
                {"tenant_id": UUID(auth.tenant_id), "staff_id": UUID(auth.actor_id)},
            )
        items = [
            {
                "id": str(row["id"]),
                "provider": str(row["provider"]),
                "address": str(row["address_normalized"]),
                "displayName": row["display_name"],
                "kind": str(row["mailbox_kind"]),
                "status": str(row["status"]),
                "canRead": bool(row["can_read"]),
                "canSend": bool(row["can_send"]),
                "canManage": bool(row["can_manage"]),
                "lastSyncedAt": _iso(row["last_synced_at"]),
            }
            for row in result.mappings().all()
        ]
        return {"items": items, "total": len(items)}

    async def disconnect_mailbox(self, auth: AuthContext, mailbox_id: str) -> None:
        async with self._engine.begin() as connection:
            mailbox = await self._authorized_mailbox(
                connection, auth, mailbox_id, permission="manage", lock=True
            )
            await connection.execute(
                text(
                    """
                    UPDATE staff_mailbox SET status='disabled', updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:mailbox_id
                    """
                ),
                {"tenant_id": UUID(auth.tenant_id), "mailbox_id": mailbox["id"]},
            )

    async def recent_messages(
        self, auth: AuthContext, mailbox_id: str, *, limit: int = 25
    ) -> dict[str, object]:
        bounded = max(1, min(limit, 50))
        async with self._engine.connect() as connection:
            mailbox = await self._authorized_mailbox(
                connection, auth, mailbox_id, permission="read"
            )
        access_token = await self._access_token(mailbox)
        provider_messages = await self._provider_recent_messages(
            mailbox, access_token, limit=bounded
        )
        await self._cache_messages(auth.tenant_id, str(mailbox["id"]), provider_messages)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, sender_address, recipient_addresses, subject, body_text,
                           direction, received_at, provider_thread_id
                    FROM staff_mail_message_cache
                    WHERE tenant_id=:tenant_id AND mailbox_id=:mailbox_id
                      AND expires_at>NOW()
                    ORDER BY received_at DESC, id DESC LIMIT :limit
                    """
                ),
                {
                    "tenant_id": UUID(auth.tenant_id),
                    "mailbox_id": mailbox["id"],
                    "limit": bounded,
                },
            )
        items = [_message(dict(row)) for row in result.mappings().all()]
        return {"items": items, "total": len(items)}

    async def search_messages(
        self, auth: AuthContext, mailbox_id: str, query: str, *, limit: int = 10
    ) -> dict[str, object]:
        normalized_query = query.strip()
        if not normalized_query or len(normalized_query) > 500:
            raise BadRequestError("MAIL_SEARCH_INVALID", "Search must contain 1-500 characters")
        bounded = max(1, min(limit, 25))
        async with self._engine.connect() as connection:
            mailbox = await self._authorized_mailbox(
                connection, auth, mailbox_id, permission="read"
            )
        access_token = await self._access_token(mailbox)
        messages = await self._provider_messages(
            mailbox, access_token, query=normalized_query, limit=bounded
        )
        await self._cache_messages(auth.tenant_id, str(mailbox["id"]), messages)
        return {
            "items": [_public_provider_message(item) for item in messages],
            "total": len(messages),
        }

    async def create_send_intent(
        self,
        auth: AuthContext,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        mailbox_id = str(payload.get("mailboxId") or "")
        subject = str(payload.get("subject") or "").strip()
        body = str(payload.get("body") or "").strip()
        student_id = str(payload.get("studentId") or "").strip() or None
        reply_id = str(payload.get("replyToMessageId") or "").strip() or None
        interaction_id = str(payload.get("interactionId") or "").strip() or None
        agent_action_id = str(payload.get("agentActionIntentId") or "").strip() or None
        if not subject or len(subject) > 998 or not body or len(body) > 100_000:
            raise BadRequestError("EMAIL_CONTENT_INVALID", "Subject and body are required")
        if (student_id is None) == (reply_id is None):
            raise BadRequestError(
                "EMAIL_RECIPIENT_INVALID",
                "Choose exactly one canonical student or authorized message reply target",
            )
        mailbox_uuid = _uuid(mailbox_id, "mailboxId")
        student_uuid = _uuid(student_id, "studentId") if student_id is not None else None
        reply_uuid = _uuid(reply_id, "replyToMessageId") if reply_id is not None else None
        interaction_uuid = (
            _uuid(interaction_id, "interactionId") if interaction_id is not None else None
        )
        agent_action_uuid = (
            _uuid(agent_action_id, "agentActionIntentId") if agent_action_id is not None else None
        )
        async with self._engine.begin() as connection:
            if agent_action_uuid is not None:
                action = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT id FROM agent_action_intent
                                WHERE tenant_id=:tenant_id AND id=:intent_id
                                  AND actor_type='staff' AND staff_member_id=:staff_id
                                  AND action_type='communications.email.prepare'
                                  AND status='executing'
                                FOR UPDATE
                                """
                            ),
                            {
                                "tenant_id": UUID(auth.tenant_id),
                                "intent_id": agent_action_uuid,
                                "staff_id": UUID(auth.actor_id),
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if action is None:
                    raise ApiError(
                        403,
                        "EDWARD_EMAIL_ACTION_FORBIDDEN",
                        "The Edward email action is not executable by this staff member",
                    )
                existing = (
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT * FROM staff_email_send_intent
                                WHERE tenant_id=:tenant_id
                                  AND agent_action_intent_id=:intent_id
                                  AND created_by_staff_id=:staff_id
                                """
                            ),
                            {
                                "tenant_id": UUID(auth.tenant_id),
                                "intent_id": agent_action_uuid,
                                "staff_id": UUID(auth.actor_id),
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing is not None:
                    return _send_intent_response(cast(Mapping[str, object], existing))
            mailbox = await self._authorized_mailbox(
                connection, auth, str(mailbox_uuid), permission="send", lock=True
            )
            recipient: str
            if student_uuid is not None:
                recipient_value = await connection.scalar(
                    text(
                        """
                        SELECT account.email_normalized
                        FROM credential_account account
                        WHERE account.tenant_id=:tenant_id AND account.student_id=:student_id
                          AND account.status='active'
                        """
                    ),
                    {"tenant_id": UUID(auth.tenant_id), "student_id": student_uuid},
                )
                if recipient_value is None:
                    raise NotFoundError(
                        "STUDENT_EMAIL_NOT_FOUND", "The student has no active email"
                    )
                recipient = str(recipient_value)
            else:
                reply = (
                    (
                        await connection.execute(
                            text(
                                """
                            SELECT sender_address, linked_student_id
                            FROM staff_mail_message_cache
                            WHERE tenant_id=:tenant_id AND mailbox_id=:mailbox_id
                              AND id=:message_id AND expires_at>NOW()
                            """
                            ),
                            {
                                "tenant_id": UUID(auth.tenant_id),
                                "mailbox_id": mailbox["id"],
                                "message_id": reply_uuid,
                            },
                        )
                    )
                    .mappings()
                    .first()
                )
                if reply is None:
                    raise NotFoundError("MAIL_MESSAGE_NOT_FOUND", "The reply target is unavailable")
                recipient = _email(str(reply["sender_address"]))
                if recipient == "unknown@invalid.local":
                    raise BadRequestError(
                        "MAIL_RECIPIENT_INVALID",
                        "This message does not contain a replyable sender address",
                    )
                linked_student_id = reply["linked_student_id"]
                student_uuid = (
                    UUID(str(linked_student_id)) if linked_student_id is not None else None
                )
            if interaction_uuid is not None:
                if student_uuid is None:
                    raise BadRequestError(
                        "EMAIL_INTERACTION_STUDENT_REQUIRED",
                        "An interaction can only be linked to a known student recipient",
                    )
                await self._authorized_interaction(
                    connection,
                    auth,
                    interaction_uuid,
                    student_uuid,
                )
            intent_id = uuid4()
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "mailboxId": mailbox_id,
                        "to": [recipient],
                        "subject": subject,
                        "body": body,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            expires_at = datetime.now(UTC) + timedelta(minutes=30)
            stable_message_id = f"<{intent_id}@mail.audentra.local>"
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_email_send_intent (
                      id, tenant_id, mailbox_id, created_by_staff_id, student_id,
                      interaction_id, reply_to_message_id, recipient_addresses,
                      subject, body_text, content_sha256, stable_message_id, expires_at,
                      agent_action_intent_id
                    ) VALUES (
                      :id, :tenant_id, :mailbox_id, :staff_id, :student_id,
                      :interaction_id, :reply_id, CAST(:recipients AS jsonb),
                      :subject, :body, :digest, :message_id, :expires_at,
                      :agent_action_intent_id
                    )
                    """
                ),
                {
                    "id": intent_id,
                    "tenant_id": UUID(auth.tenant_id),
                    "mailbox_id": mailbox["id"],
                    "staff_id": UUID(auth.actor_id),
                    "student_id": student_uuid,
                    "interaction_id": interaction_uuid,
                    "reply_id": reply_uuid,
                    "recipients": json.dumps([recipient]),
                    "subject": subject,
                    "body": body,
                    "digest": digest,
                    "message_id": stable_message_id,
                    "expires_at": expires_at,
                    "agent_action_intent_id": agent_action_uuid,
                },
            )
        return {
            "id": str(intent_id),
            "version": 1,
            "status": "pending_confirmation",
            "mailboxId": str(mailbox["id"]),
            "sender": str(mailbox["address_normalized"]),
            "recipients": [recipient],
            "subject": subject,
            "body": body,
            "contentSha256": digest,
            "expiresAt": expires_at.isoformat(),
        }

    async def get_send_intent(self, auth: AuthContext, intent_id: str) -> dict[str, object]:
        async with self._engine.connect() as connection:
            row = await self._send_intent(connection, auth, intent_id)
        return _send_intent_response(row)

    async def confirm_send_intent(
        self,
        auth: AuthContext,
        intent_id: str,
        expected_version: int,
        content_sha256: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        async with self._engine.begin() as connection:
            row = await self._send_intent(connection, auth, intent_id, lock=True)
            await self._authorized_mailbox(
                connection, auth, str(row["mailbox_id"]), permission="send", lock=True
            )
            if str(row["content_sha256"]) != content_sha256:
                raise ConflictError("EMAIL_INTENT_CHANGED", "The reviewed email content changed")
            if int(str(row["version"])) != expected_version:
                raise ConflictError("VERSION_CONFLICT", "The send intent changed")
            status = str(row["status"])
            if (
                status == "failed"
                and str(row.get("last_error_code") or "") == _RECONNECT_REQUIRED_ERROR
            ):
                # The provider call never began: after a successful reconnect the
                # reviewed, unexpired intent can safely be placed back on the queue.
                pass
            elif status not in {"pending_confirmation", "queued"}:
                raise ConflictError(
                    "EMAIL_INTENT_NOT_CONFIRMABLE", "This email cannot be confirmed"
                )
            if cast(datetime, row["expires_at"]) <= datetime.now(UTC):
                await connection.execute(
                    text("UPDATE staff_email_send_intent SET status='expired' WHERE id=:id"),
                    {"id": row["id"]},
                )
                raise ConflictError("EMAIL_INTENT_EXPIRED", "The reviewed email expired")
            await connection.execute(
                text(
                    """
                    UPDATE staff_email_send_intent
                    SET status='queued', confirmed_by_staff_id=:staff_id,
                        confirmed_at=COALESCE(confirmed_at, NOW()),
                        idempotency_key=COALESCE(idempotency_key, :idempotency_key),
                        last_error_code=NULL, last_error_message=NULL,
                        updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:id
                    """
                ),
                {
                    "staff_id": UUID(auth.actor_id),
                    "idempotency_key": idempotency_key,
                    "tenant_id": UUID(auth.tenant_id),
                    "id": row["id"],
                },
            )
            await self._insert_outbox(
                connection,
                tenant_id=auth.tenant_id,
                actor_id=auth.actor_id,
                event_name="staff.email_send_queued.v1",
                aggregate_id=intent_id,
                data={"sendIntentId": intent_id},
            )
            refreshed = await self._send_intent(connection, auth, intent_id)
        return _send_intent_response(refreshed)

    async def handle_mail_event(self, event_name: str, tenant_id: str, aggregate_id: str) -> None:
        """Worker entry point for idempotent mailbox sync and delivery."""

        await self._purge_expired_cache()
        if event_name == "staff.mailbox_connected.v1":
            await self._sync_mailbox(tenant_id, aggregate_id)
            return
        if event_name == "staff.email_send_queued.v1":
            await self._deliver_send_intent(tenant_id, aggregate_id)
            return
        raise ValueError(f"Unsupported staff mail event: {event_name}")

    async def _sync_mailbox(self, tenant_id: str, mailbox_id: str) -> None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT mailbox.*, mail_auth.provider_tenant,
                           mail_auth.granted_scopes,
                           mail_auth.refresh_token_ciphertext,
                           mail_auth.refresh_token_nonce,
                           mail_auth.id AS authorization_id
                    FROM staff_mailbox mailbox
                    JOIN staff_mail_authorization mail_auth
                      ON mail_auth.tenant_id=mailbox.tenant_id
                     AND mail_auth.id=mailbox.authorization_id
                    WHERE mailbox.tenant_id=:tenant_id AND mailbox.id=:mailbox_id
                      AND mailbox.status='active' AND mail_auth.status='active'
                    """
                ),
                {"tenant_id": UUID(tenant_id), "mailbox_id": UUID(mailbox_id)},
            )
            mailbox = result.mappings().first()
        if mailbox is None:
            return
        mailbox_data = dict(mailbox)
        token = await self._access_token(mailbox_data)
        messages = await self._provider_recent_messages(mailbox_data, token, limit=50)
        await self._cache_messages(tenant_id, mailbox_id, messages)
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE staff_mailbox SET last_synced_at=NOW(), updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:mailbox_id
                    """
                ),
                {"tenant_id": UUID(tenant_id), "mailbox_id": UUID(mailbox_id)},
            )

    async def _deliver_send_intent(self, tenant_id: str, intent_id: str) -> None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT intent.*, mailbox.provider, mailbox.address_normalized,
                           mailbox.provider_mailbox_id, mailbox.authorization_id,
                           mail_auth.provider_tenant, mail_auth.granted_scopes,
                           mail_auth.refresh_token_ciphertext,
                           mail_auth.refresh_token_nonce
                    FROM staff_email_send_intent intent
                    JOIN staff_mailbox mailbox
                      ON mailbox.tenant_id=intent.tenant_id AND mailbox.id=intent.mailbox_id
                    JOIN staff_mail_authorization mail_auth
                      ON mail_auth.tenant_id=mailbox.tenant_id
                     AND mail_auth.id=mailbox.authorization_id
                    WHERE intent.tenant_id=:tenant_id AND intent.id=:intent_id
                      AND intent.status IN ('queued', 'sending')
                      AND mailbox.status='active' AND mail_auth.status='active'
                    FOR UPDATE OF intent
                    """
                ),
                {"tenant_id": UUID(tenant_id), "intent_id": UUID(intent_id)},
            )
            row = result.mappings().first()
            if row is None:
                return
            if str(row["status"]) == "sending":
                # An earlier attempt became ambiguous. Never blindly resend.
                return
        intent = dict(row)
        try:
            access_token = await self._access_token(intent)
        except ConflictError as error:
            if error.code == _RECONNECT_REQUIRED_ERROR:
                await self._mark_intent_failed(
                    tenant_id,
                    intent_id,
                    code=_RECONNECT_REQUIRED_ERROR,
                    message=error.message,
                    expected_status="queued",
                )
                return
            await self._mark_intent_retryable(tenant_id, intent_id, error)
            raise
        except Exception as error:
            # No provider send was attempted, so the transactional outbox can
            # safely retry this intent with its original idempotency boundary.
            await self._mark_intent_retryable(tenant_id, intent_id, error)
            raise
        async with self._engine.begin() as connection:
            claimed = await connection.execute(
                text(
                    """
                    UPDATE staff_email_send_intent
                    SET status='sending', updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:intent_id AND status='queued'
                    """
                ),
                {"tenant_id": UUID(tenant_id), "intent_id": UUID(intent_id)},
            )
            if claimed.rowcount != 1:
                return
        try:
            provider_message_id = await self._send_provider_message(intent, access_token)
        except Exception as error:
            # Once the provider-send boundary has been crossed, a transport or
            # provider failure may still represent a delivered email. Do not retry
            # automatically or allow confirmation to resend it.
            await self._mark_intent_failed(
                tenant_id,
                intent_id,
                code="AMBIGUOUS_PROVIDER_FAILURE",
                message=str(error),
                expected_status="sending",
            )
            return
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE staff_email_send_intent
                    SET status='sent', provider_message_id=:provider_message_id,
                        sent_at=NOW(), last_error_code=NULL, last_error_message=NULL,
                        updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:intent_id
                    """
                ),
                {
                    "provider_message_id": provider_message_id,
                    "tenant_id": UUID(tenant_id),
                    "intent_id": UUID(intent_id),
                },
            )
            interaction_id = intent.get("interaction_id")
            source_sequence: int | None = None
            if interaction_id is not None:
                source_sequence = int(
                    await connection.scalar(
                        text(
                            """
                            SELECT COALESCE(MAX(source_sequence), 0) + 1
                            FROM communication_event
                            WHERE tenant_id=:tenant_id AND interaction_id=:interaction_id
                            """
                        ),
                        {
                            "tenant_id": UUID(tenant_id),
                            "interaction_id": interaction_id,
                        },
                    )
                    or 1
                )
            recipients = intent["recipient_addresses"]
            await connection.execute(
                text(
                    """
                    INSERT INTO communication_event (
                      id, tenant_id, student_id, channel, direction, subject,
                      body_excerpt, metadata, resolution_status, occurred_at,
                      created_at, interaction_id, source_type, source_id,
                      source_sequence, request_key, delivery_status
                    ) VALUES (
                      :id, :tenant_id, :student_id, 'email', 'outbound', :subject,
                      :body, CAST(:metadata AS jsonb), 'unresolved', NOW(), NOW(),
                      :interaction_id, 'staff_email_send_intent', :source_id,
                      :source_sequence, :request_key, 'sent'
                    )
                    ON CONFLICT (tenant_id, source_type, source_id)
                    WHERE source_type IS NOT NULL AND source_id IS NOT NULL DO NOTHING
                    """
                ),
                {
                    "id": uuid4(),
                    "tenant_id": UUID(tenant_id),
                    "student_id": intent.get("student_id"),
                    "subject": intent["subject"],
                    "body": intent["body_text"],
                    "metadata": json.dumps(
                        {
                            "mailboxId": str(intent["mailbox_id"]),
                            "recipients": recipients,
                            "providerMessageId": provider_message_id,
                        }
                    ),
                    "interaction_id": interaction_id,
                    "source_id": UUID(intent_id),
                    "source_sequence": source_sequence,
                    "request_key": intent.get("idempotency_key"),
                },
            )
            if interaction_id is not None and source_sequence is not None:
                await connection.execute(
                    text(
                        """
                        UPDATE staff_interaction
                        SET source_version=GREATEST(source_version, :source_sequence),
                            status=CASE WHEN covered_source_version>0 THEN 'stale'
                                        ELSE 'enrichment_pending' END,
                            selected_channel='email', last_activity_at=NOW(),
                            quiet_until=NOW()+INTERVAL '5 minutes',
                            version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND id=:interaction_id
                        """
                    ),
                    {
                        "source_sequence": source_sequence,
                        "tenant_id": UUID(tenant_id),
                        "interaction_id": interaction_id,
                    },
                )

    async def _send_provider_message(
        self, intent: Mapping[str, object], access_token: str
    ) -> str | None:
        recipients_value = intent["recipient_addresses"]
        recipients = (
            json.loads(recipients_value)
            if isinstance(recipients_value, str)
            else list(cast(Sequence[str], recipients_value))
        )
        headers = {"Authorization": f"Bearer {access_token}"}
        address = quote(str(intent["address_normalized"]), safe="@.")
        if str(intent["provider"]) == "google":
            message = EmailMessage()
            message["From"] = str(intent["address_normalized"])
            message["To"] = ", ".join(str(item) for item in recipients)
            message["Subject"] = str(intent["subject"])
            message["Message-ID"] = str(intent["stable_message_id"])
            message["X-Audentra-Intent-ID"] = str(intent["id"])
            message.set_content(str(intent["body_text"]))
            response = await self._client.post(
                f"https://gmail.googleapis.com/gmail/v1/users/{address}/messages/send",
                headers=headers,
                json={"raw": _base64url(message.as_bytes())},
            )
            _provider_success(response)
            value = response.json()
            return str(value.get("id")) if value.get("id") else None
        response = await self._client.post(
            f"https://graph.microsoft.com/v1.0/users/{address}/sendMail",
            headers=headers,
            json={
                "message": {
                    "subject": str(intent["subject"]),
                    "body": {"contentType": "Text", "content": str(intent["body_text"])},
                    "toRecipients": [
                        {"emailAddress": {"address": str(item)}} for item in recipients
                    ],
                    "internetMessageHeaders": [
                        {"name": "X-Audentra-Intent-ID", "value": str(intent["id"])}
                    ],
                },
                "saveToSentItems": True,
            },
        )
        _provider_success(response)
        return None

    async def _provider_recent_messages(
        self, mailbox: Mapping[str, object], access_token: str, *, limit: int
    ) -> list[dict[str, object]]:
        if str(mailbox["provider"]) == "google":
            return await self._provider_messages(
                mailbox, access_token, query="in:inbox newer_than:7d", limit=limit
            )
        address = quote(str(mailbox["address_normalized"]), safe="@.")
        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat().replace("+00:00", "Z")
        response = await self._client.get(
            f"https://graph.microsoft.com/v1.0/users/{address}/mailFolders/inbox/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "$filter": f"receivedDateTime ge {cutoff}",
                "$orderby": "receivedDateTime desc",
                "$top": str(limit),
                "$select": (
                    "id,conversationId,internetMessageId,from,toRecipients,subject,body,"
                    "receivedDateTime,sentDateTime"
                ),
            },
        )
        _provider_success(response)
        return [_microsoft_message(item) for item in response.json().get("value", [])[:limit]]

    async def _purge_expired_cache(self) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM staff_mail_message_cache WHERE expires_at<=NOW()")
            )

    async def _insert_transaction(
        self,
        connection: AsyncConnection,
        *,
        flow_type: str,
        provider: Provider,
        tenant: Mapping[str, object],
        expected_provider_tenant: str,
        return_path: str,
        staff_member_id: UUID | None = None,
        mailbox_kind: str | None = None,
        target_mailbox_address: str | None = None,
    ) -> tuple[str, str, str, str]:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)[:96]
        nonce = secrets.token_urlsafe(32)
        callback_kind = "sso" if flow_type == "staff_sso" else "mail"
        redirect_uri = (
            f"{self._settings.api_public_url}/v1/auth/staff/sso/{provider}/callback"
            if callback_kind == "sso"
            else f"{self._settings.api_public_url}/v1/staff/mail/oauth/{provider}/callback"
        )
        await connection.execute(
            text(
                """
                INSERT INTO oauth_transaction (
                  id, state_hash, flow_type, provider, tenant_id, tenant_slug,
                  expected_provider_tenant, initiating_staff_member_id,
                  mailbox_kind, target_mailbox_address, code_verifier, nonce,
                  redirect_uri, return_path, expires_at
                ) VALUES (
                  :id, :state_hash, :flow_type, :provider, :tenant_id, :tenant_slug,
                  :expected, :staff_id, :mailbox_kind, :address, :verifier, :nonce,
                  :redirect_uri, :return_path, :expires_at
                )
                """
            ),
            {
                "id": uuid4(),
                "state_hash": _hash(state),
                "flow_type": flow_type,
                "provider": provider,
                "tenant_id": tenant["id"],
                "tenant_slug": tenant["slug"],
                "expected": expected_provider_tenant,
                "staff_id": staff_member_id,
                "mailbox_kind": mailbox_kind,
                "address": target_mailbox_address,
                "verifier": verifier,
                "nonce": nonce,
                "redirect_uri": redirect_uri,
                "return_path": return_path,
                "expires_at": datetime.now(UTC) + _STATE_LIFETIME,
            },
        )
        return state, verifier, nonce, redirect_uri

    async def _consume_transaction(
        self, state: str, *, provider: Provider, flow_type: str
    ) -> Mapping[str, object]:
        if len(state) < 32 or len(state) > 256:
            raise BadRequestError("OAUTH_STATE_INVALID", "The authorization state is invalid")
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE oauth_transaction
                    SET consumed_at=NOW()
                    WHERE state_hash=:state_hash AND provider=:provider
                      AND flow_type=:flow_type AND consumed_at IS NULL
                      AND expires_at>NOW()
                    RETURNING *
                    """
                ),
                {"state_hash": _hash(state), "provider": provider, "flow_type": flow_type},
            )
            row = result.mappings().first()
        if row is None:
            raise BadRequestError(
                "OAUTH_TRANSACTION_INVALID",
                "The authorization transaction is expired, reused, or invalid",
            )
        return dict(row)

    async def _exchange_code(
        self, provider: Provider, transaction: Mapping[str, object], code: str
    ) -> Mapping[str, object]:
        if not code or len(code) > 4096:
            raise BadRequestError("OAUTH_CODE_INVALID", "The authorization code is invalid")
        if provider == "google":
            url = "https://oauth2.googleapis.com/token"
            client_id = self._settings.google_client_id
            client_secret = self._settings.google_client_secret
        else:
            authority = self._microsoft_authority(
                str(transaction["expected_provider_tenant"]),
                mailbox=str(transaction.get("flow_type") or "") == "mailbox_connect",
            )
            tenant = quote(authority, safe="")
            url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
            client_id = self._settings.microsoft_client_id
            client_secret = self._settings.microsoft_client_secret
        response = await self._client.post(
            url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": str(transaction["redirect_uri"]),
                "code_verifier": str(transaction["code_verifier"]),
            },
        )
        if response.status_code >= 400:
            raise ApiError(502, "OAUTH_EXCHANGE_FAILED", "The identity provider rejected the code")
        value = response.json()
        if not isinstance(value, Mapping):
            raise ApiError(502, "OAUTH_RESPONSE_INVALID", "The provider response is invalid")
        return cast(Mapping[str, object], value)

    async def _verify_identity(
        self,
        provider: Provider,
        transaction: Mapping[str, object],
        tokens: Mapping[str, object],
    ) -> Mapping[str, str]:
        raw_token = tokens.get("id_token")
        if not isinstance(raw_token, str) or not raw_token:
            raise ApiError(502, "OIDC_TOKEN_MISSING", "The provider returned no identity token")
        expected_tenant = str(transaction["expected_provider_tenant"])
        client_id = (
            self._settings.google_client_id
            if provider == "google"
            else self._settings.microsoft_client_id
        )
        if provider == "google":
            jwks_url = "https://www.googleapis.com/oauth2/v3/certs"
        else:
            authority = self._microsoft_authority(
                expected_tenant,
                mailbox=str(transaction.get("flow_type") or "") == "mailbox_connect",
            )
            tenant_segment = quote(authority, safe="")
            jwks_url = f"https://login.microsoftonline.com/{tenant_segment}/discovery/v2.0/keys"
        jwks_response = await self._client.get(jwks_url)
        jwks_response.raise_for_status()
        jwks = jwks_response.json()
        header = jwt.get_unverified_header(raw_token)
        key_data = next(
            (
                item
                for item in jwks.get("keys", [])
                if isinstance(item, Mapping) and item.get("kid") == header.get("kid")
            ),
            None,
        )
        if key_data is None:
            raise BadRequestError("OIDC_KEY_INVALID", "The identity token signing key is unknown")
        key = jwt.PyJWK.from_dict(dict(key_data)).key
        issuer: str | Sequence[str]
        if provider == "google":
            issuer = list(_GOOGLE_ISSUERS)
        else:
            issuer = f"https://login.microsoftonline.com/{expected_tenant}/v2.0"
        try:
            claims = jwt.decode(
                raw_token,
                key=key,
                algorithms=["RS256"],
                audience=client_id,
                issuer=issuer,
                options={"require": ["exp", "iat", "iss", "aud", "nonce"]},
            )
        except jwt.PyJWTError as error:
            raise BadRequestError("OIDC_TOKEN_INVALID", "The identity token is invalid") from error
        if not secrets.compare_digest(str(claims.get("nonce") or ""), str(transaction["nonce"])):
            raise BadRequestError("OIDC_NONCE_INVALID", "The identity response did not match")
        if provider == "google":
            provider_tenant = str(claims.get("hd") or "").lower()
            subject = str(claims.get("sub") or "")
            verified = claims.get("email_verified") is True
        else:
            provider_tenant = str(claims.get("tid") or "").lower()
            subject = str(claims.get("oid") or "")
            verified = True
        if not secrets.compare_digest(provider_tenant, expected_tenant.lower()):
            raise ApiError(403, "OAUTH_TENANT_MISMATCH", "The identity belongs to another tenant")
        email_value = claims.get("email") or claims.get("preferred_username")
        if not verified or not subject or not isinstance(email_value, str):
            raise ApiError(403, "OIDC_IDENTITY_INVALID", "A verified staff identity is required")
        return {
            "subject": subject,
            "provider_tenant": provider_tenant,
            "email": _email(email_value),
            "display_name": _identity_display_name(claims.get("name")),
        }

    def _authorization_url(
        self,
        provider: Provider,
        expected_tenant: str,
        *,
        state: str,
        nonce: str,
        verifier: str,
        redirect_uri: str,
        mailbox: bool,
        login_hint: str | None = None,
    ) -> str:
        challenge = _base64url(hashlib.sha256(verifier.encode()).digest())
        if provider == "google":
            scopes = ["openid", "email", "profile"]
            if mailbox:
                scopes += [
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.send",
                ]
            params: dict[str, str] = {
                "client_id": self._settings.google_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "hd": expected_tenant,
            }
            if mailbox:
                params.update(
                    access_type="offline",
                    include_granted_scopes="true",
                    prompt="consent",
                )
            if login_hint:
                params["login_hint"] = login_hint
            return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        scopes = ["openid", "profile", "email"]
        if mailbox:
            scopes += [
                "offline_access",
                "User.Read",
                "Mail.Read",
                "Mail.Send",
                "Mail.Read.Shared",
                "Mail.Send.Shared",
            ]
        params = {
            "client_id": self._settings.microsoft_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": " ".join(scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if login_hint:
            params["login_hint"] = login_hint
        authority = self._microsoft_authority(expected_tenant, mailbox=mailbox)
        tenant = quote(authority, safe="")
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?" + urlencode(
            params
        )

    async def _provider_messages(
        self,
        mailbox: Mapping[str, object],
        access_token: str,
        *,
        query: str,
        limit: int,
    ) -> list[dict[str, object]]:
        provider = str(mailbox["provider"])
        address = quote(str(mailbox["address_normalized"]), safe="@.")
        headers = {"Authorization": f"Bearer {access_token}"}
        if provider == "google":
            listed = await self._client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/{address}/messages",
                headers=headers,
                params={"q": query, "maxResults": limit},
            )
            _provider_success(listed)
            identifiers = listed.json().get("messages", [])
            messages: list[dict[str, object]] = []
            for item in identifiers[:limit]:
                message_id = quote(str(item.get("id") or ""), safe="")
                response = await self._client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/{address}/messages/{message_id}",
                    headers=headers,
                    params={"format": "full"},
                )
                _provider_success(response)
                messages.append(_google_message(response.json()))
            return messages
        response = await self._client.get(
            f"https://graph.microsoft.com/v1.0/users/{address}/messages",
            headers={**headers, "ConsistencyLevel": "eventual"},
            params={
                "$search": f'"{query.replace(chr(34), "")}"',
                "$top": str(limit),
                "$select": (
                    "id,conversationId,internetMessageId,from,toRecipients,subject,body,"
                    "receivedDateTime,sentDateTime"
                ),
            },
        )
        _provider_success(response)
        return [_microsoft_message(item) for item in response.json().get("value", [])[:limit]]

    async def _cache_messages(
        self, tenant_id: str, mailbox_id: str, messages: Sequence[Mapping[str, object]]
    ) -> None:
        async with self._engine.begin() as connection:
            for item in messages:
                sender = _provider_email(str(item["sender"]))
                linked_student_id = await connection.scalar(
                    text(
                        """
                        SELECT student_id FROM credential_account
                        WHERE tenant_id=:tenant_id AND email_normalized=:email
                          AND status='active'
                        """
                    ),
                    {"tenant_id": UUID(tenant_id), "email": sender},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO staff_mail_message_cache (
                          id, tenant_id, mailbox_id, provider_message_id,
                          provider_thread_id, internet_message_id, direction,
                          sender_address, recipient_addresses, subject, body_text,
                          received_at, expires_at, linked_student_id
                        ) VALUES (
                          :id, :tenant_id, :mailbox_id, :provider_message_id,
                          :thread_id, :internet_message_id, :direction,
                          :sender, CAST(:recipients AS jsonb), :subject, :body,
                          :received_at, :expires_at, :linked_student_id
                        )
                        ON CONFLICT (tenant_id, mailbox_id, provider_message_id)
                        DO UPDATE SET subject=EXCLUDED.subject, body_text=EXCLUDED.body_text,
                          recipient_addresses=EXCLUDED.recipient_addresses,
                          linked_student_id=EXCLUDED.linked_student_id,
                          expires_at=EXCLUDED.expires_at, updated_at=NOW()
                        """
                    ),
                    {
                        "id": uuid4(),
                        "tenant_id": UUID(tenant_id),
                        "mailbox_id": UUID(mailbox_id),
                        "provider_message_id": item["providerMessageId"],
                        "thread_id": item.get("threadId"),
                        "internet_message_id": item.get("internetMessageId"),
                        "direction": item.get("direction", "inbound"),
                        "sender": sender,
                        "recipients": json.dumps(item.get("recipients", [])),
                        "subject": item.get("subject"),
                        "body": item.get("body"),
                        "received_at": item["receivedAt"],
                        "expires_at": datetime.now(UTC) + timedelta(days=7),
                        "linked_student_id": linked_student_id,
                    },
                )

    async def _access_token(self, mailbox: Mapping[str, object]) -> str:
        refresh_token = self._decrypt(
            bytes(cast(bytes, mailbox["refresh_token_ciphertext"])),
            bytes(cast(bytes, mailbox["refresh_token_nonce"])),
        )
        provider = str(mailbox["provider"])
        if provider == "google":
            url = "https://oauth2.googleapis.com/token"
            data = {
                "client_id": self._settings.google_client_id,
                "client_secret": self._settings.google_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        else:
            authority = self._microsoft_authority(
                str(mailbox["provider_tenant"]),
                mailbox=True,
            )
            tenant = quote(authority, safe="")
            url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
            data = {
                "client_id": self._settings.microsoft_client_id,
                "client_secret": self._settings.microsoft_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": " ".join(cast(Sequence[str], mailbox["granted_scopes"])),
            }
        response = await self._client.post(url, data=data)
        if _token_refresh_requires_reconnect(response):
            await self._mark_reconnect(str(mailbox["authorization_id"]))
            raise ConflictError(_RECONNECT_REQUIRED_ERROR, "Reconnect the mailbox to continue")
        if response.status_code >= 400:
            # Provider outages and misconfigured token clients are safe to retry:
            # the email provider has not received a send request at this point.
            raise ApiError(
                502,
                "PROVIDER_TOKEN_UNAVAILABLE",
                "The mailbox token could not be refreshed",
            )
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ApiError(502, "PROVIDER_TOKEN_INVALID", "The provider returned no access token")
        replacement = payload.get("refresh_token")
        if isinstance(replacement, str) and replacement:
            ciphertext, nonce = self._encrypt(replacement)
            async with self._engine.begin() as connection:
                await connection.execute(
                    text(
                        """
                        UPDATE staff_mail_authorization
                        SET refresh_token_ciphertext=:ciphertext,
                            refresh_token_nonce=:nonce, last_refreshed_at=NOW(), updated_at=NOW()
                        WHERE id=:id
                        """
                    ),
                    {
                        "ciphertext": ciphertext,
                        "nonce": nonce,
                        "id": mailbox["authorization_id"],
                    },
                )
        return token

    async def _authorized_interaction(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        interaction_id: UUID,
        student_id: UUID,
    ) -> None:
        """Bind an outbound email to the active staff member's tenant work context.

        Staff Action Center permissions are tenant-wide today, so there is no
        separate per-work-item ACL to check here.  The active staff identity and
        mailbox grant are checked by ``_authorized_mailbox``; this query then
        ensures the optional interaction belongs to the same tenant and student
        recipient rather than accepting an arbitrary globally-addressable UUID.
        """

        self._require_staff(auth)
        result = await connection.execute(
            text(
                """
                SELECT interaction.id
                FROM staff_interaction interaction
                JOIN staff_work_item work_item
                  ON work_item.tenant_id=interaction.tenant_id
                 AND work_item.id=interaction.work_item_id
                 AND work_item.student_id=interaction.student_id
                JOIN staff_member member
                  ON member.tenant_id=interaction.tenant_id
                 AND member.id=:staff_id
                 AND member.active=true
                WHERE interaction.tenant_id=:tenant_id
                  AND interaction.id=:interaction_id
                  AND interaction.student_id=:student_id
                """
            ),
            {
                "tenant_id": UUID(auth.tenant_id),
                "staff_id": UUID(auth.actor_id),
                "interaction_id": interaction_id,
                "student_id": student_id,
            },
        )
        if result.mappings().first() is None:
            # Do not reveal whether a supplied UUID belongs to another tenant or
            # another student's work item.
            raise NotFoundError(
                "EMAIL_INTERACTION_NOT_FOUND",
                "The selected interaction is unavailable",
            )

    async def _mark_intent_retryable(
        self,
        tenant_id: str,
        intent_id: str,
        error: Exception,
    ) -> None:
        """Return an intent to the safe pre-send state before outbox retry."""

        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE staff_email_send_intent
                    SET status='queued', last_error_code=:code,
                        last_error_message=:message, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:intent_id AND status='queued'
                    """
                ),
                {
                    "code": _TOKEN_ACQUISITION_ERROR,
                    "message": str(error)[:1000],
                    "tenant_id": UUID(tenant_id),
                    "intent_id": UUID(intent_id),
                },
            )

    async def _mark_intent_failed(
        self,
        tenant_id: str,
        intent_id: str,
        *,
        code: str,
        message: str,
        expected_status: Literal["queued", "sending"],
    ) -> None:
        """Fail an intent only while it remains in the expected delivery phase.

        Token acquisition happens before the intent is claimed for delivery.  A
        duplicate outbox delivery can therefore discover a reconnect requirement
        while another worker has already claimed the intent and is sending it.
        The status compare-and-set protects that in-flight provider call from
        being made manually re-confirmable.
        """

        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE staff_email_send_intent
                    SET status='failed', last_error_code=:code,
                        last_error_message=:message, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:intent_id
                      AND status=:expected_status
                    """
                ),
                {
                    "code": code,
                    "message": message[:1000],
                    "tenant_id": UUID(tenant_id),
                    "intent_id": UUID(intent_id),
                    "expected_status": expected_status,
                },
            )

    async def _authorized_mailbox(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        mailbox_id: str,
        *,
        permission: Literal["read", "send", "manage"],
        lock: bool = False,
    ) -> Mapping[str, object]:
        self._require_staff(auth)
        query = """
            SELECT mailbox.*, mail_auth.provider_subject,
                   mail_auth.provider_tenant, mail_auth.granted_scopes,
                   mail_auth.refresh_token_ciphertext,
                   mail_auth.refresh_token_nonce,
                   mail_auth.status AS authorization_status
            FROM staff_mailbox mailbox
            JOIN staff_mail_authorization mail_auth
              ON mail_auth.tenant_id=mailbox.tenant_id
             AND mail_auth.id=mailbox.authorization_id
            JOIN staff_member member
              ON member.tenant_id=mailbox.tenant_id AND member.id=:staff_id
            WHERE mailbox.tenant_id=:tenant_id AND mailbox.id=:mailbox_id
              AND mailbox.status='active' AND mail_auth.status='active'
              AND member.active=true
              AND EXISTS (
                SELECT 1 FROM staff_mailbox_grant mgrant
                WHERE mgrant.tenant_id=mailbox.tenant_id
                  AND mgrant.mailbox_id=mailbox.id
                  AND CASE :permission
                    WHEN 'read' THEN mgrant.can_read
                    WHEN 'send' THEN mgrant.can_send
                    WHEN 'manage' THEN mgrant.can_manage
                    ELSE false
                  END = true
                  AND (
                    mgrant.principal_type='all_staff'
                    OR (mgrant.principal_type='staff' AND mgrant.staff_member_id=:staff_id)
                    OR (mgrant.principal_type='component' AND mgrant.component=member.component)
                  )
              )
        """
        if lock:
            query += " FOR UPDATE OF mailbox"
        result = await connection.execute(
            text(query),
            {
                "tenant_id": UUID(auth.tenant_id),
                "staff_id": UUID(auth.actor_id),
                "mailbox_id": UUID(mailbox_id),
                "permission": permission,
            },
        )
        row = result.mappings().first()
        if row is None:
            raise NotFoundError("MAILBOX_NOT_FOUND", "The mailbox is unavailable")
        return dict(row)

    async def _send_intent(
        self, connection: AsyncConnection, auth: AuthContext, intent_id: str, *, lock: bool = False
    ) -> Mapping[str, object]:
        query = """
            SELECT intent.*, mailbox.address_normalized
            FROM staff_email_send_intent intent
            JOIN staff_mailbox mailbox
              ON mailbox.tenant_id=intent.tenant_id AND mailbox.id=intent.mailbox_id
            WHERE intent.tenant_id=:tenant_id AND intent.id=:intent_id
              AND intent.created_by_staff_id=:staff_id
        """
        if lock:
            query += " FOR UPDATE"
        result = await connection.execute(
            text(query),
            {
                "tenant_id": UUID(auth.tenant_id),
                "intent_id": UUID(intent_id),
                "staff_id": UUID(auth.actor_id),
            },
        )
        row = result.mappings().first()
        if row is None:
            raise NotFoundError("EMAIL_INTENT_NOT_FOUND", "The email intent was not found")
        return dict(row)

    async def _tenant_by_slug(
        self, connection: AsyncConnection, tenant_slug: str
    ) -> Mapping[str, object]:
        result = await connection.execute(
            text("SELECT id, slug FROM tenant WHERE slug=:slug AND status='active'"),
            {"slug": tenant_slug.strip().lower()},
        )
        row = result.mappings().first()
        if row is None:
            raise NotFoundError("TENANT_NOT_FOUND", "The university is not recognized")
        return dict(row)

    async def _tenant_by_id(
        self, connection: AsyncConnection, tenant_id: str
    ) -> Mapping[str, object]:
        result = await connection.execute(
            text("SELECT id, slug FROM tenant WHERE id=:id AND status='active'"),
            {"id": UUID(tenant_id)},
        )
        row = result.mappings().first()
        if row is None:
            raise NotFoundError("TENANT_NOT_FOUND", "The university is not recognized")
        return dict(row)

    async def _provider_tenant(
        self, connection: AsyncConnection, tenant_id: object, provider: Provider
    ) -> str:
        result = await connection.execute(
            text(
                """
                SELECT google_hosted_domain, microsoft_tenant_id
                FROM tenant_identity_provider
                WHERE tenant_id=:tenant_id AND provider=:provider AND enabled=true
                """
            ),
            {"tenant_id": tenant_id, "provider": provider},
        )
        row = result.mappings().first()
        if row is None:
            raise NotFoundError("SSO_PROVIDER_NOT_ENABLED", "This provider is not enabled")
        value = row["google_hosted_domain"] if provider == "google" else row["microsoft_tenant_id"]
        if value is None:
            raise ApiError(503, "SSO_PROVIDER_INVALID", "Provider configuration is incomplete")
        return str(value).lower()

    async def _probe_mailbox(self, provider: Provider, address: str, token: str) -> None:
        encoded = quote(address, safe="@.")
        headers = {"Authorization": f"Bearer {token}"}
        url = (
            f"https://gmail.googleapis.com/gmail/v1/users/{encoded}/profile"
            if provider == "google"
            else f"https://graph.microsoft.com/v1.0/users/{encoded}/mailFolders/inbox/messages?$top=1&$select=id"
        )
        response = await self._client.get(url, headers=headers)
        if response.status_code >= 400:
            raise ApiError(
                403,
                "MAILBOX_ACCESS_NOT_PROVEN",
                "The provider did not grant access to this mailbox",
            )

    async def _mark_reconnect(self, authorization_id: str) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE staff_mail_authorization
                    SET status='reconnect_required', updated_at=NOW() WHERE id=:id
                    """
                ),
                {"id": UUID(authorization_id)},
            )

    async def _insert_outbox(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: str,
        actor_id: str,
        event_name: str,
        aggregate_id: str,
        data: Mapping[str, object],
    ) -> None:
        event_id = uuid4()
        now = datetime.now(UTC)
        payload = {
            "eventId": str(event_id),
            "eventName": event_name,
            "occurredAt": now.isoformat(),
            "tenantId": tenant_id,
            "aggregateType": "staff_email",
            "aggregateId": aggregate_id,
            "aggregateVersion": 1,
            "actor": {"type": "staff", "id": actor_id},
            "data": dict(data),
        }
        await connection.execute(
            text(
                """
                INSERT INTO outbox_event (
                  id, tenant_id, event_name, aggregate_type, aggregate_id,
                  aggregate_version, occurred_at, actor_type, actor_id,
                  correlation_id, causation_id, payload, created_at
                ) VALUES (
                  :id, :tenant_id, :event_name, 'staff_email', :aggregate_id,
                  1, :occurred_at, 'staff', :actor_id, :correlation_id,
                  :causation_id, CAST(:payload AS jsonb), :occurred_at
                )
                """
            ),
            {
                "id": event_id,
                "tenant_id": UUID(tenant_id),
                "event_name": event_name,
                "aggregate_id": UUID(aggregate_id),
                "occurred_at": now,
                "actor_id": UUID(actor_id),
                "correlation_id": str(event_id),
                "causation_id": str(event_id),
                "payload": json.dumps(payload),
            },
        )

    def _encrypt(self, value: str) -> tuple[bytes, bytes]:
        key = self._encryption_key()
        nonce = secrets.token_bytes(12)
        return AESGCM(key).encrypt(nonce, value.encode(), b"audentra-mail-token-v1"), nonce

    def _decrypt(self, ciphertext: bytes, nonce: bytes) -> str:
        try:
            value = AESGCM(self._encryption_key()).decrypt(
                nonce, ciphertext, b"audentra-mail-token-v1"
            )
        except Exception as error:
            raise ApiError(
                503,
                "MAIL_TOKEN_DECRYPTION_FAILED",
                "Mailbox credentials cannot be decrypted",
            ) from error
        return value.decode()

    def _encryption_key(self) -> bytes:
        raw = self._settings.token_encryption_key
        try:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except ValueError as error:
            raise ApiError(
                503,
                "MAIL_ENCRYPTION_NOT_CONFIGURED",
                "Mail encryption is unavailable",
            ) from error
        if len(key) != 32:
            raise ApiError(503, "MAIL_ENCRYPTION_NOT_CONFIGURED", "Mail encryption is unavailable")
        return key

    def _portal_url(self, return_path: str) -> str:
        return self._settings.portal_origin.rstrip("/") + return_path

    def _require_provider(self, provider: Provider) -> None:
        if not self._settings.provider_configured(provider):
            raise ApiError(503, "SSO_PROVIDER_NOT_CONFIGURED", "This provider is unavailable")

    def _provider_available(self, provider: str, microsoft_tenant_id: str) -> bool:
        if not self._settings.provider_configured(provider):
            return False
        return provider != "microsoft" or (
            not _is_microsoft_consumer_tenant(microsoft_tenant_id)
            or self._settings.microsoft_allow_personal_accounts
        )

    def _microsoft_authority(self, expected_tenant: str, *, mailbox: bool) -> str:
        """Resolve the narrowly enabled personal-account test authority.

        A personal Microsoft account uses the shared consumer tenant. It is
        never an institutional domain signal, so this branch remains explicit,
        development/test-only, and subject to the normal exact local staff
        authorization rules.
        """

        if not _is_microsoft_consumer_tenant(expected_tenant):
            return expected_tenant
        if not self._settings.microsoft_allow_personal_accounts:
            raise ApiError(
                503,
                "MICROSOFT_PERSONAL_ACCOUNTS_DISABLED",
                "Personal Microsoft accounts are unavailable in this environment",
            )
        if mailbox:
            raise BadRequestError(
                "MICROSOFT_PERSONAL_MAILBOX_UNSUPPORTED",
                "Personal Microsoft account testing supports staff SSO only",
            )
        return "consumers"

    @staticmethod
    def _require_mail_scopes(provider: Provider, kind: str, scopes: Sequence[str]) -> None:
        granted = set(scopes)
        if provider == "google":
            required = {
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
            }
        else:
            required = {"Mail.Read", "Mail.Send"}
            if kind == "shared":
                required.update({"Mail.Read.Shared", "Mail.Send.Shared"})
        if not required.issubset(granted):
            raise ConflictError(
                "MAILBOX_SCOPES_MISSING",
                "Reconnect the mailbox and approve the requested read and send permissions",
            )

    @staticmethod
    def _require_staff(auth: AuthContext) -> None:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_ACCESS_REQUIRED", "Staff access is required")


def _provider(value: str) -> Provider:
    normalized = value.strip().lower()
    if normalized not in {"google", "microsoft"}:
        raise NotFoundError("SSO_PROVIDER_NOT_FOUND", "The identity provider is not supported")
    return cast(Provider, normalized)


def _is_microsoft_consumer_tenant(value: str) -> bool:
    return secrets.compare_digest(
        value.strip().lower(),
        _MICROSOFT_CONSUMER_TENANT_ID,
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _email(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) > 320 or not _EMAIL_RE.fullmatch(normalized):
        raise BadRequestError("EMAIL_INVALID", "A valid email address is required")
    return normalized


def _uuid(value: object, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise BadRequestError(
            "EMAIL_IDENTIFIER_INVALID",
            f"{field_name} must be a valid UUID",
        ) from error


def _identity_display_name(value: object) -> str:
    """Return bounded display-only profile data from a verified ID token."""

    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:160]


def _provider_email(value: str) -> str:
    normalized = (parseaddr(value)[1] or value).strip().lower()
    if len(normalized) <= 320 and _EMAIL_RE.fullmatch(normalized):
        return normalized
    return "unknown@invalid.local"


def _scope_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [item for item in value.split() if item]
    return []


def _provider_success(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise ConflictError(_RECONNECT_REQUIRED_ERROR, "Reconnect the mailbox to continue")
    if response.status_code >= 400:
        raise ApiError(502, "MAIL_PROVIDER_FAILED", "The mail provider request failed")


def _token_refresh_requires_reconnect(response: httpx.Response) -> bool:
    """Return true only for user-grant failures, not transient token outages."""

    if response.status_code not in {400, 401}:
        return False
    try:
        payload = response.json()
    except ValueError:
        return response.status_code == 401
    if not isinstance(payload, Mapping):
        return response.status_code == 401
    error = str(payload.get("error") or "").strip().lower()
    return error in {"invalid_grant", "invalid_token", "interaction_required", "consent_required"}


def _safe_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    text_value = "\n".join(parser.parts) if "<" in value else html.unescape(value)
    text_value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text_value)
    return text_value[:100_000]


def _gmail_headers(payload: Mapping[str, object]) -> dict[str, str]:
    return {
        str(item.get("name") or "").lower(): str(item.get("value") or "")
        for item in cast(Sequence[Mapping[str, object]], payload.get("headers") or [])
    }


def _gmail_body(payload: Mapping[str, object]) -> str:
    mime = str(payload.get("mimeType") or "")
    body = cast(Mapping[str, object], payload.get("body") or {})
    data = body.get("data")
    if mime == "text/plain" and isinstance(data, str):
        try:
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            return _safe_text(decoded.decode(errors="replace"))
        except ValueError:
            return ""
    for part in cast(Sequence[Mapping[str, object]], payload.get("parts") or []):
        value = _gmail_body(part)
        if value:
            return value
    if mime == "text/html" and isinstance(data, str):
        try:
            decoded = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            return _safe_text(decoded.decode(errors="replace"))
        except ValueError:
            return ""
    return ""


def _google_message(value: Mapping[str, object]) -> dict[str, object]:
    payload = cast(Mapping[str, object], value.get("payload") or {})
    headers = _gmail_headers(payload)
    internal_date = int(str(value.get("internalDate") or "0")) / 1000
    received = datetime.fromtimestamp(internal_date, tz=UTC)
    sender = _provider_email(headers.get("from", ""))
    recipients = [address for _, address in getaddresses([headers.get("to", "")]) if address]
    return {
        "providerMessageId": str(value.get("id") or ""),
        "threadId": str(value.get("threadId") or "") or None,
        "internetMessageId": headers.get("message-id"),
        "direction": "inbound",
        "sender": sender[:320],
        "recipients": recipients,
        "subject": headers.get("subject"),
        "body": _gmail_body(payload),
        "receivedAt": received,
    }


def _microsoft_message(value: Mapping[str, object]) -> dict[str, object]:
    from_value = cast(Mapping[str, object], value.get("from") or {})
    sender = cast(Mapping[str, object], from_value.get("emailAddress") or {})
    recipients = [
        str(cast(Mapping[str, object], item.get("emailAddress") or {}).get("address") or "")
        for item in cast(Sequence[Mapping[str, object]], value.get("toRecipients") or [])
    ]
    body = cast(Mapping[str, object], value.get("body") or {})
    received_raw = str(value.get("receivedDateTime") or value.get("sentDateTime") or "")
    received = datetime.fromisoformat(received_raw.replace("Z", "+00:00"))
    return {
        "providerMessageId": str(value.get("id") or ""),
        "threadId": value.get("conversationId"),
        "internetMessageId": value.get("internetMessageId"),
        "direction": "inbound",
        "sender": _provider_email(str(sender.get("address") or "")),
        "recipients": recipients,
        "subject": value.get("subject"),
        "body": _safe_text(str(body.get("content") or "")),
        "receivedAt": received,
    }


def _public_provider_message(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "providerMessageId": value.get("providerMessageId"),
        "threadId": value.get("threadId"),
        "sender": value.get("sender"),
        "recipients": value.get("recipients", []),
        "subject": value.get("subject"),
        "body": value.get("body"),
        "receivedAt": _iso(value.get("receivedAt")),
    }


def _message(row: Mapping[str, object]) -> dict[str, object]:
    recipients = row.get("recipient_addresses")
    if isinstance(recipients, str):
        recipients = json.loads(recipients)
    return {
        "id": str(row["id"]),
        "threadId": row.get("provider_thread_id"),
        "sender": str(row["sender_address"]),
        "recipients": recipients or [],
        "subject": row.get("subject"),
        "body": row.get("body_text"),
        "direction": str(row["direction"]),
        "receivedAt": _iso(row["received_at"]),
    }


def _send_intent_response(row: Mapping[str, object]) -> dict[str, object]:
    recipients = row.get("recipient_addresses")
    if isinstance(recipients, str):
        recipients = json.loads(recipients)
    error_code = row.get("last_error_code")
    error_message = row.get("last_error_message")
    error = (
        {"code": str(error_code), "message": str(error_message)}
        if error_code is not None and error_message is not None
        else None
    )
    return {
        "id": str(row["id"]),
        "version": int(str(row["version"])),
        "status": str(row["status"]),
        "mailboxId": str(row["mailbox_id"]),
        "sender": str(row["address_normalized"]),
        "recipients": recipients or [],
        "subject": str(row["subject"]),
        "body": str(row["body_text"]),
        "contentSha256": str(row["content_sha256"]),
        "expiresAt": _iso(row["expires_at"]),
        "sentAt": _iso(row.get("sent_at")),
        "error": error,
    }


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
