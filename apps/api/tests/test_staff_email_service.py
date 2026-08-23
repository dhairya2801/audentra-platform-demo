from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import jwt
import pytest
from fastapi import FastAPI, Request

from audentra.bootstrap.settings import InstitutionalOAuthSettings
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError
from audentra.core.ports import StaffSession
from audentra.infrastructure.postgres.auth_repository import (
    PostgresDevelopmentAuth,
    _hash_password,
)
from audentra.infrastructure.postgres.staff_email_service import (
    _MICROSOFT_CONSUMER_TENANT_ID,
    PostgresStaffEmailService,
    _base64url,
    _email,
    _gmail_body,
    _google_message,
    _hash,
    _iso,
    _message,
    _microsoft_message,
    _provider,
    _provider_email,
    _provider_success,
    _public_provider_message,
    _safe_text,
    _scope_values,
    _send_intent_response,
    _token_refresh_requires_reconnect,
)
from audentra.interfaces.http import mail_routes
from audentra.interfaces.http.config import HttpSettings
from audentra.interfaces.http.mail_routes import (
    ConfirmSendIntentRequest,
    CreateSendIntentRequest,
    MailSearchRequest,
    complete_mailbox_connect,
    complete_staff_sso,
    confirm_send_intent,
    create_send_intent,
    disconnect_mailbox,
    get_send_intent,
    list_mailboxes,
    recent_mail,
    search_mail,
    staff_auth_options,
    start_mailbox_connect,
    start_staff_sso,
)

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STAFF_ID = "00000000-0000-7000-8000-000000000901"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
OTHER_STUDENT_ID = "00000000-0000-7000-8000-000000000102"
MAILBOX_ID = "00000000-0000-7000-8000-000000000801"
AUTHORIZATION_ID = "00000000-0000-7000-8000-000000000802"
INTENT_ID = "00000000-0000-7000-8000-000000000803"
INTERACTION_ID = "00000000-0000-7000-8000-000000000804"
MESSAGE_ID = "00000000-0000-7000-8000-000000000805"
NOW = datetime.now(UTC)

pytestmark = pytest.mark.anyio


class FakeResult:
    def __init__(
        self,
        rows: list[Mapping[str, object]] | None = None,
        scalar: object = None,
        rowcount: int = 1,
    ) -> None:
        self.rows = rows or []
        self.value = scalar
        self.rowcount = rowcount

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> Mapping[str, object] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[Mapping[str, object]]:
        return self.rows

    def scalar_one(self) -> object:
        assert self.value is not None
        return self.value

    def scalar_one_or_none(self) -> object:
        return self.value


class FakeConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.parameters: list[Mapping[str, object]] = []
        self.missing: set[str] = set()
        self.intent_status = "pending_confirmation"
        self.intent_error_code: str | None = None
        self.interaction_student_id = UUID(STUDENT_ID)
        self.password_hash = "invalid"  # noqa: S105
        self.microsoft_tenant_id = TENANT_ID

    async def execute(self, statement: object, _params: object = None) -> FakeResult:
        sql = str(statement)
        self.queries.append(sql)
        if isinstance(_params, Mapping):
            self.parameters.append(dict(_params))
        if (
            "UPDATE staff_email_send_intent" in sql
            and "SET status='failed'" in sql
            and isinstance(_params, Mapping)
            and "expected_status" in _params
        ):
            if self.intent_status != _params["expected_status"]:
                return FakeResult(rowcount=0)
            self.intent_status = "failed"
            self.intent_error_code = str(_params["code"])
            return FakeResult(rowcount=1)
        if "SELECT id, slug FROM tenant WHERE slug" in sql:
            return self._row("tenant", {"id": UUID(TENANT_ID), "slug": "harvard"})
        if "SELECT s.id AS student_id" in sql:
            return self._row(
                "demo",
                {
                    "student_id": UUID(STUDENT_ID),
                    "person_id": UUID("00000000-0000-7000-8000-000000000102"),
                    "external_ref": "SYN-000101",
                    "preferred_name": "Alex",
                },
            )
        if "SELECT id, slug FROM tenant WHERE id" in sql:
            return self._row("tenant", {"id": UUID(TENANT_ID), "slug": "harvard"})
        if "SELECT provider, microsoft_tenant_id FROM tenant_identity_provider" in sql:
            return FakeResult(
                [
                    {"provider": "google", "microsoft_tenant_id": None},
                    {
                        "provider": "microsoft",
                        "microsoft_tenant_id": self.microsoft_tenant_id,
                    },
                ]
            )
        if "SELECT google_hosted_domain, microsoft_tenant_id" in sql:
            return self._row(
                "provider",
                {
                    "google_hosted_domain": "harvard.edu",
                    "microsoft_tenant_id": self.microsoft_tenant_id,
                },
            )
        if "SELECT id, email_normalized FROM staff_member" in sql:
            return self._row(
                "staff", {"id": UUID(STAFF_ID), "email_normalized": "staff@harvard.edu"}
            )
        if "SELECT id, display_name, email_normalized, component" in sql:
            return self._row(
                "staff",
                {
                    "id": UUID(STAFF_ID),
                    "display_name": "Staff Member",
                    "email_normalized": "staff@harvard.edu",
                    "component": "Advising",
                },
            )
        if "FROM staff_sso_provisioning_grant" in sql:
            return self._row(
                "provisioning_grant",
                {
                    "id": UUID(AUTHORIZATION_ID),
                    "component": "Admissions",
                    "provider_subject": None,
                    "provider_tenant": None,
                },
            )
        if "INSERT INTO staff_member (" in sql and "RETURNING id" in sql:
            values = cast(Mapping[str, object], _params or {})
            return self._row(
                "provisioned_staff",
                {
                    "id": UUID(STAFF_ID),
                    "display_name": values["display_name"],
                    "email_normalized": values["email"],
                    "component": values["component"],
                },
            )
        if "UPDATE staff_sso_provisioning_grant" in sql:
            return FakeResult()
        if "SELECT member.id, member.display_name" in sql:
            return self._row(
                "staff_session",
                {
                    "id": UUID(STAFF_ID),
                    "display_name": "Staff Member",
                    "email_normalized": "staff@harvard.edu",
                    "component": "Advising",
                    "expires_at": NOW + timedelta(hours=1),
                    "authentication_method": "google",
                },
            )
        if "SELECT account.id AS account_id" in sql:
            if "account.student_id" in sql:
                if "account.password_hash" in sql:
                    return self._row(
                        "student_credential",
                        {
                            "account_id": UUID(AUTHORIZATION_ID),
                            "student_id": UUID(STUDENT_ID),
                            "email_normalized": "student@harvard.edu",
                            "phone_e164": "+15551230001",
                            "email_verified_at": NOW,
                            "phone_verified_at": None,
                            "password_hash": self.password_hash,
                            "status": "active",
                            "person_id": UUID("00000000-0000-7000-8000-000000000102"),
                            "onboarding_status": "completed",
                            "preferred_name": "Alex",
                        },
                    )
                return self._row(
                    "student_session",
                    {
                        "account_id": UUID(AUTHORIZATION_ID),
                        "student_id": UUID(STUDENT_ID),
                        "email_normalized": "student@harvard.edu",
                        "phone_e164": "+15551230001",
                        "email_verified_at": NOW,
                        "phone_verified_at": None,
                        "person_id": UUID("00000000-0000-7000-8000-000000000102"),
                        "onboarding_status": "completed",
                        "preferred_name": "Alex",
                        "expires_at": NOW + timedelta(hours=1),
                    },
                )
            return self._row(
                "credential",
                {
                    "account_id": UUID(AUTHORIZATION_ID),
                    "password_hash": self.password_hash,
                    "status": "active",
                    "id": UUID(STAFF_ID),
                    "display_name": "Staff Member",
                    "email_normalized": "staff@harvard.edu",
                    "component": "Advising",
                },
            )
        if "SELECT id FROM tenant_mailbox_policy" in sql:
            return self._row("policy", {"id": UUID(AUTHORIZATION_ID)})
        if "SELECT default_visibility, allow_read, allow_send" in sql:
            return self._row(
                "policy",
                {"default_visibility": "all_staff", "allow_read": True, "allow_send": True},
            )
        if "INSERT INTO staff_mail_authorization" in sql:
            return FakeResult(scalar=UUID(AUTHORIZATION_ID))
        if "INSERT INTO staff_federated_identity" in sql:
            return FakeResult(scalar=None if "identity" in self.missing else UUID(AUTHORIZATION_ID))
        if "INSERT INTO staff_mailbox (" in sql:
            return FakeResult(scalar=UUID(MAILBOX_ID))
        if "SELECT DISTINCT mailbox.id" in sql:
            return FakeResult([self.mailbox_row()])
        if "SELECT mailbox.*, authorization.provider_subject" in sql:
            return self._row("mailbox", self.mailbox_row())
        if "SELECT id, sender_address, recipient_addresses" in sql:
            return FakeResult([self.message_row()])
        if "SELECT sender_address, linked_student_id" in sql:
            return self._row(
                "reply",
                {"sender_address": "student@harvard.edu", "linked_student_id": UUID(STUDENT_ID)},
            )
        if "FROM staff_interaction interaction" in sql:
            parameters = cast(Mapping[str, object], _params or {})
            if parameters.get("student_id") != self.interaction_student_id:
                return FakeResult()
            return self._row("interaction", {"id": UUID(INTERACTION_ID)})
        if "SELECT intent.*, mailbox.address_normalized" in sql:
            return self._row("intent", self.intent_row())
        if "SELECT mailbox.*, authorization.provider_tenant" in sql:
            return self._row("mailbox", self.mailbox_row())
        if "SELECT intent.*, mailbox.provider" in sql:
            return self._row("intent", self.intent_row())
        if "UPDATE oauth_transaction" in sql and "RETURNING" in sql:
            return self._row("transaction", self.transaction_row())
        if "SELECT student.id" in sql:
            return FakeResult(scalar=None if "student" in self.missing else UUID(STUDENT_ID))
        return FakeResult()

    async def scalar(self, statement: object, _params: object = None) -> object:
        sql = str(statement)
        self.queries.append(sql)
        if isinstance(_params, Mapping):
            self.parameters.append(dict(_params))
        if "SELECT email_normalized FROM staff_member" in sql:
            return None if "staff_email" in self.missing else "staff@harvard.edu"
        if "SELECT account.email_normalized" in sql:
            return None if "student_email" in self.missing else "student@harvard.edu"
        if "SELECT student_id FROM credential_account" in sql:
            return UUID(STUDENT_ID)
        if "SELECT COALESCE(MAX(source_sequence)" in sql:
            return 3
        return None

    def _row(self, name: str, row: Mapping[str, object]) -> FakeResult:
        return FakeResult([] if name in self.missing else [row])

    @staticmethod
    def transaction_row() -> dict[str, object]:
        return {
            "tenant_id": UUID(TENANT_ID),
            "tenant_slug": "harvard",
            "expected_provider_tenant": "harvard.edu",
            "initiating_staff_member_id": UUID(STAFF_ID),
            "target_mailbox_address": "staff@harvard.edu",
            "mailbox_kind": "personal",
            "redirect_uri": "https://api.example/v1/auth/staff/sso/google/callback",
            "code_verifier": "verifier",
            "nonce": "nonce",
            "return_path": "/staff/mail",
        }

    @staticmethod
    def mailbox_row() -> dict[str, object]:
        ciphertext, nonce = encrypted_refresh_token()
        return {
            "id": UUID(MAILBOX_ID),
            "provider": "google",
            "address_normalized": "staff@harvard.edu",
            "display_name": "Staff inbox",
            "mailbox_kind": "personal",
            "status": "active",
            "last_synced_at": NOW,
            "can_read": True,
            "can_send": True,
            "can_manage": True,
            "provider_mailbox_id": "staff@harvard.edu",
            "authorization_id": UUID(AUTHORIZATION_ID),
            "provider_tenant": "harvard.edu",
            "granted_scopes": [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
            ],
            "refresh_token_ciphertext": ciphertext,
            "refresh_token_nonce": nonce,
        }

    def intent_row(self) -> dict[str, object]:
        return {
            **self.mailbox_row(),
            "id": UUID(INTENT_ID),
            "mailbox_id": UUID(MAILBOX_ID),
            "created_by_staff_id": UUID(STAFF_ID),
            "student_id": UUID(STUDENT_ID),
            "interaction_id": UUID(INTERACTION_ID),
            "recipient_addresses": ["student@harvard.edu"],
            "subject": "Advising follow-up",
            "body_text": "Please review the next step.",
            "content_sha256": "digest",
            "stable_message_id": f"<{INTENT_ID}@mail.audentra.local>",
            "version": 1,
            "status": self.intent_status,
            "expires_at": NOW + timedelta(minutes=20),
            "sent_at": None,
            "last_error_code": self.intent_error_code,
            "last_error_message": None,
            "idempotency_key": "request-1",
        }

    @staticmethod
    def message_row() -> dict[str, object]:
        return {
            "id": UUID(MESSAGE_ID),
            "provider_thread_id": "thread-1",
            "sender_address": "student@harvard.edu",
            "recipient_addresses": ["staff@harvard.edu"],
            "subject": "Deadline question",
            "body_text": "Is this due today?",
            "direction": "inbound",
            "received_at": NOW,
        }


class FakeContext:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeEngine:
    def __init__(self) -> None:
        self.connection = FakeConnection()

    def connect(self) -> FakeContext:
        return FakeContext(self.connection)

    def begin(self) -> FakeContext:
        return FakeContext(self.connection)


class FakeAuth:
    def __init__(self) -> None:
        self.federated: dict[str, str] | None = None

    async def sign_in_staff_federated(self, **values: str) -> StaffSession:
        self.federated = values
        return StaffSession(
            context=staff_context(authentication_method=values["provider"]),
            name="Staff Member",
            email=values["email"],
            component="Advising",
            token="staff-session-token-that-is-long-enough",  # noqa: S106
            expires_at_epoch=4_102_444_800,
        )


def settings() -> InstitutionalOAuthSettings:
    return InstitutionalOAuthSettings(
        api_public_url="https://api.example",
        portal_origin="https://portal.example",
        google_client_id="google-client",
        google_client_secret="google-secret",  # noqa: S106
        microsoft_client_id="microsoft-client",
        microsoft_client_secret="microsoft-secret",  # noqa: S106
        microsoft_allow_personal_accounts=False,
        token_encryption_key=base64.urlsafe_b64encode(b"k" * 32).rstrip(b"=").decode(),
    )


def encrypted_refresh_token() -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = b"n" * 12
    return AESGCM(b"k" * 32).encrypt(nonce, b"refresh-token", b"audentra-mail-token-v1"), nonce


def staff_context(*, authentication_method: str = "credentials") -> AuthContext:
    return AuthContext(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        actor_id=STAFF_ID,
        actor_type="staff",
        authentication_method=cast(Any, authentication_method),
        tenant_slug="harvard",
    )


def provider_transport(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    path = request.url.path
    if path.endswith("/token"):
        return httpx.Response(
            200,
            json={
                "access_token": "access-token",
                "refresh_token": "replacement-refresh-token",
                "id_token": "identity-token",
                "scope": (
                    "openid email https://www.googleapis.com/auth/gmail.readonly "
                    "https://www.googleapis.com/auth/gmail.send"
                ),
            },
        )
    if path.endswith("/profile") or "mailFolders/inbox/messages" in path:
        if "graph.microsoft" in url:
            return httpx.Response(200, json={"value": [microsoft_payload()]})
        return httpx.Response(200, json={"emailAddress": "staff@harvard.edu"})
    if path.endswith("/messages/send"):
        return httpx.Response(200, json={"id": "sent-google-1"})
    if path.endswith("/sendMail"):
        return httpx.Response(202, json={})
    if path.endswith("/messages") and "gmail.googleapis" in url:
        return httpx.Response(200, json={"messages": [{"id": "gmail-1"}]})
    if "/messages/gmail-1" in url:
        return httpx.Response(200, json=google_payload())
    if path.endswith("/messages") and "graph.microsoft" in url:
        return httpx.Response(200, json={"value": [microsoft_payload()]})
    if path.endswith("/certs") or path.endswith("/keys"):
        return httpx.Response(200, json={"keys": [{"kid": "key-1", "kty": "RSA"}]})
    return httpx.Response(404, json={"error": "unhandled"})


def google_payload() -> dict[str, object]:
    body = _base64url(b"Plain message body")
    return {
        "id": "gmail-1",
        "threadId": "thread-1",
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Student <student@harvard.edu>"},
                {"name": "To", "value": "staff@harvard.edu"},
                {"name": "Subject", "value": "Deadline question"},
                {"name": "Message-ID", "value": "<provider-message-1>"},
            ],
            "body": {"data": body},
        },
    }


def microsoft_payload() -> dict[str, object]:
    return {
        "id": "graph-1",
        "conversationId": "thread-2",
        "internetMessageId": "<provider-message-2>",
        "from": {"emailAddress": {"address": "student@harvard.edu"}},
        "toRecipients": [{"emailAddress": {"address": "staff@harvard.edu"}}],
        "subject": "Required form",
        "body": {"content": "<p>Please complete the form.</p>"},
        "receivedDateTime": NOW.isoformat().replace("+00:00", "Z"),
    }


async def make_service() -> tuple[
    PostgresStaffEmailService, FakeEngine, FakeAuth, httpx.AsyncClient
]:
    engine = FakeEngine()
    auth = FakeAuth()
    client = httpx.AsyncClient(transport=httpx.MockTransport(provider_transport))
    service = PostgresStaffEmailService(cast(Any, engine), client, settings(), cast(Any, auth))
    return service, engine, auth, client


async def test_oauth_start_options_and_callback_are_tenant_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, engine, auth, client = await make_service()
    try:
        options = await service.staff_options("HARVARD")
        assert options["providers"] == ["google", "microsoft"]
        google_url = await service.start_sso("google", "harvard", "https://attacker.example/after")
        microsoft_url = await service.start_sso("microsoft", "harvard", "/staff")
        assert "accounts.google.com" in google_url and "hd=harvard.edu" in google_url
        assert TENANT_ID in microsoft_url
        assert any("INSERT INTO oauth_transaction" in query for query in engine.connection.queries)

        async def claims(*_args: object) -> Mapping[str, str]:
            return {
                "subject": "subject-1",
                "provider_tenant": "harvard.edu",
                "email": "staff@harvard.edu",
            }

        monkeypatch.setattr(service, "_verify_identity", claims)
        session, return_url = await service.complete_sso(
            "google", "state-value-that-is-definitely-long-enough", "code"
        )
        assert session.email == "staff@harvard.edu"
        assert return_url == "https://portal.example/staff/mail"
        assert auth.federated is not None and auth.federated["tenant_id"] == TENANT_ID
    finally:
        await client.aclose()


async def test_personal_microsoft_account_sso_is_explicit_and_mail_is_unsupported() -> None:
    service, engine, _auth, client = await make_service()
    engine.connection.microsoft_tenant_id = _MICROSOFT_CONSUMER_TENANT_ID
    try:
        hidden_options = await service.staff_options("harvard")
        assert hidden_options["providers"] == ["google"]

        with pytest.raises(ApiError, match="Personal Microsoft accounts"):
            await service.start_sso("microsoft", "harvard", "/staff")

        service._settings = replace(settings(), microsoft_allow_personal_accounts=True)
        visible_options = await service.staff_options("harvard")
        assert visible_options["providers"] == ["google", "microsoft"]

        sign_in_url = await service.start_sso("microsoft", "harvard", "/staff")
        assert "login.microsoftonline.com/consumers/oauth2/v2.0/authorize" in sign_in_url
        assert _MICROSOFT_CONSUMER_TENANT_ID not in sign_in_url

        with pytest.raises(BadRequestError, match="staff SSO only"):
            await service.start_mailbox_connect(
                staff_context(),
                "microsoft",
                "personal",
                "staff@harvard.edu",
                "/staff/mail",
            )
    finally:
        await client.aclose()


async def test_mailbox_connect_list_recent_search_disconnect_and_send_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _engine, _auth, client = await make_service()
    context = staff_context()
    try:
        connect_url = await service.start_mailbox_connect(
            context, "google", "personal", "Staff@Harvard.edu", "/staff/mail"
        )
        assert "gmail.readonly" in connect_url and "login_hint=staff%40harvard.edu" in connect_url

        async def claims(*_args: object) -> Mapping[str, str]:
            return {
                "subject": "subject-1",
                "provider_tenant": "harvard.edu",
                "email": "staff@harvard.edu",
            }

        monkeypatch.setattr(service, "_verify_identity", claims)
        return_url = await service.complete_mailbox_connect(
            "google", "state-value-that-is-definitely-long-enough", "code"
        )
        assert return_url.endswith("/staff/mail")

        listed = await service.list_mailboxes(context)
        assert listed["total"] == 1
        recent = await service.recent_messages(context, MAILBOX_ID, limit=500)
        assert recent["total"] == 1
        searched = await service.search_messages(context, MAILBOX_ID, "deadline", limit=100)
        assert searched["total"] == 1

        created = await service.create_send_intent(
            context,
            {
                "mailboxId": MAILBOX_ID,
                "studentId": STUDENT_ID,
                "interactionId": INTERACTION_ID,
                "subject": "Advising follow-up",
                "body": "Please review the next step.",
            },
        )
        assert created["status"] == "pending_confirmation"
        fetched = await service.get_send_intent(context, INTENT_ID)
        assert fetched["mailboxId"] == MAILBOX_ID
        confirmed = await service.confirm_send_intent(context, INTENT_ID, 1, "digest", "request-1")
        assert confirmed["version"] == 1
        await service.disconnect_mailbox(context, MAILBOX_ID)
    finally:
        await client.aclose()


async def test_shared_mailbox_policy_and_cached_reply_target_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _engine, _auth, client = await make_service()
    context = staff_context()
    try:
        shared_url = await service.start_mailbox_connect(
            context,
            "microsoft",
            "shared",
            "advising@harvard.edu",
            "/staff/mail",
        )
        assert "Mail.Read.Shared" in shared_url
        assert "login_hint" not in shared_url

        transaction = {
            **FakeConnection.transaction_row(),
            "expected_provider_tenant": TENANT_ID,
            "target_mailbox_address": "advising@harvard.edu",
            "mailbox_kind": "shared",
        }

        async def consume(*_args: object, **_kwargs: object) -> Mapping[str, object]:
            return transaction

        async def exchange(*_args: object) -> Mapping[str, object]:
            return {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "scope": "Mail.Read Mail.Send Mail.Read.Shared Mail.Send.Shared",
            }

        async def claims(*_args: object) -> Mapping[str, str]:
            return {
                "subject": "object-id",
                "provider_tenant": TENANT_ID,
                "email": "staff@harvard.edu",
            }

        monkeypatch.setattr(service, "_consume_transaction", consume)
        monkeypatch.setattr(service, "_exchange_code", exchange)
        monkeypatch.setattr(service, "_verify_identity", claims)
        connected = await service.complete_mailbox_connect(
            "microsoft", "state-value-that-is-definitely-long-enough", "code"
        )
        assert connected.endswith("/staff/mail")

        reply_intent = await service.create_send_intent(
            context,
            {
                "mailboxId": MAILBOX_ID,
                "replyToMessageId": MESSAGE_ID,
                "subject": "Re: Deadline question",
                "body": "The deadline is Friday.",
            },
        )
        assert reply_intent["recipients"] == ["student@harvard.edu"]

        with pytest.raises(BadRequestError):
            await service.start_mailbox_connect(
                context, "google", "other", "staff@harvard.edu", None
            )
        with pytest.raises(ApiError):
            await service.start_mailbox_connect(
                context, "google", "personal", "other@harvard.edu", None
            )
    finally:
        await client.aclose()


async def test_send_intent_binds_interaction_to_tenant_student_and_active_staff_context() -> None:
    service, engine, _auth, client = await make_service()
    try:
        with pytest.raises(BadRequestError) as invalid_identifier:
            await service.create_send_intent(
                staff_context(),
                {
                    "mailboxId": MAILBOX_ID,
                    "studentId": "not-a-uuid",
                    "subject": "Advising follow-up",
                    "body": "Please review the next step.",
                },
            )
        assert invalid_identifier.value.code == "EMAIL_IDENTIFIER_INVALID"

        engine.connection.missing.add("interaction")
        with pytest.raises(NotFoundError) as unbound_interaction:
            await service.create_send_intent(
                staff_context(),
                {
                    "mailboxId": MAILBOX_ID,
                    "studentId": STUDENT_ID,
                    "interactionId": INTERACTION_ID,
                    "subject": "Advising follow-up",
                    "body": "Please review the next step.",
                },
            )
        assert unbound_interaction.value.code == "EMAIL_INTERACTION_NOT_FOUND"
        engine.connection.missing.remove("interaction")
        with pytest.raises(NotFoundError) as wrong_student_interaction:
            await service.create_send_intent(
                staff_context(),
                {
                    "mailboxId": MAILBOX_ID,
                    "studentId": OTHER_STUDENT_ID,
                    "interactionId": INTERACTION_ID,
                    "subject": "Advising follow-up",
                    "body": "Please review the next step.",
                },
            )
        assert wrong_student_interaction.value.code == "EMAIL_INTERACTION_NOT_FOUND"
        assert not any(
            "INSERT INTO staff_email_send_intent" in query
            for query in engine.connection.queries
        )
    finally:
        await client.aclose()


async def test_worker_sync_and_delivery_are_idempotent_and_record_communications(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, engine, _auth, client = await make_service()
    try:
        await service.handle_mail_event("staff.mailbox_connected.v1", TENANT_ID, MAILBOX_ID)
        await service.handle_mail_event("staff.email_send_queued.v1", TENANT_ID, INTENT_ID)
        assert any(
            "INSERT INTO communication_event" in query for query in engine.connection.queries
        )
        assert any("UPDATE staff_interaction" in query for query in engine.connection.queries)

        engine.connection.intent_status = "sending"
        await service._deliver_send_intent(TENANT_ID, INTENT_ID)

        async def fail_send(*_args: object) -> str | None:
            raise httpx.ReadTimeout("ambiguous")

        engine.connection.intent_status = "queued"
        monkeypatch.setattr(service, "_send_provider_message", fail_send)
        await service._deliver_send_intent(TENANT_ID, INTENT_ID)
        assert any(
            values.get("code") == "AMBIGUOUS_PROVIDER_FAILURE"
            and values.get("expected_status") == "sending"
            for values in engine.connection.parameters
        )
        with pytest.raises(ValueError):
            await service.handle_mail_event("unknown.event", TENANT_ID, INTENT_ID)
    finally:
        await client.aclose()


async def test_worker_retries_token_failures_and_requires_reconnect_without_resending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, engine, _auth, client = await make_service()
    try:
        async def token_outage(*_args: object) -> str:
            raise httpx.ConnectError("token endpoint unavailable")

        monkeypatch.setattr(service, "_access_token", token_outage)
        with pytest.raises(httpx.ConnectError):
            await service._deliver_send_intent(TENANT_ID, INTENT_ID)
        assert any(
            values.get("code") == "TOKEN_ACQUISITION_FAILED"
            for values in engine.connection.parameters
        )
        assert not any(
            values.get("code") == "AMBIGUOUS_PROVIDER_FAILURE"
            for values in engine.connection.parameters
        )

        async def reconnect_required(*_args: object) -> str:
            raise ConflictError("MAILBOX_RECONNECT_REQUIRED", "Reconnect the mailbox to continue")

        monkeypatch.setattr(service, "_access_token", reconnect_required)
        await service._deliver_send_intent(TENANT_ID, INTENT_ID)
        assert any(
            values.get("code") == "MAILBOX_RECONNECT_REQUIRED"
            and values.get("expected_status") == "queued"
            for values in engine.connection.parameters
        )

        engine.connection.intent_status = "failed"
        engine.connection.intent_error_code = "MAILBOX_RECONNECT_REQUIRED"
        await service.confirm_send_intent(staff_context(), INTENT_ID, 1, "digest", "request-1")
        assert any(
            "last_error_code=NULL" in query for query in engine.connection.queries
        )
    finally:
        await client.aclose()


async def test_reconnect_failure_does_not_overwrite_an_intent_claimed_by_another_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, engine, _auth, client = await make_service()
    try:
        engine.connection.intent_status = "queued"

        async def reconnect_after_other_worker_claim(_intent: Mapping[str, object]) -> str:
            # Simulate a duplicate outbox handler: it loaded the queued intent,
            # then another worker claimed it and crossed the provider-send boundary.
            engine.connection.intent_status = "sending"
            raise ConflictError("MAILBOX_RECONNECT_REQUIRED", "Reconnect the mailbox to continue")

        monkeypatch.setattr(service, "_access_token", reconnect_after_other_worker_claim)
        await service._deliver_send_intent(TENANT_ID, INTENT_ID)

        # The reconnect path is allowed to fail only a still-queued intent.  It
        # must not make an in-flight provider send manually re-confirmable.
        assert engine.connection.intent_status == "sending"
        assert any(
            values.get("code") == "MAILBOX_RECONNECT_REQUIRED"
            and values.get("expected_status") == "queued"
            for values in engine.connection.parameters
        )
    finally:
        await client.aclose()


async def test_provider_adapters_refresh_tokens_and_parse_text() -> None:
    service, _engine, _auth, client = await make_service()
    try:
        mailbox = FakeConnection.mailbox_row()
        google = await service._provider_messages(
            mailbox, "access-token", query="deadline", limit=10
        )
        assert google[0]["body"] == "Plain message body"
        graph_mailbox = {**mailbox, "provider": "microsoft", "provider_tenant": TENANT_ID}
        graph = await service._provider_messages(
            graph_mailbox, "access-token", query='required "form"', limit=10
        )
        assert graph[0]["body"] == "Please complete the form."
        recent = await service._provider_recent_messages(graph_mailbox, "access-token", limit=10)
        assert recent[0]["providerMessageId"] == "graph-1"

        token = await service._access_token(mailbox)
        assert token == "access-token"  # noqa: S105
        assert (
            await service._send_provider_message(FakeConnection().intent_row(), token)
            == "sent-google-1"
        )
        assert (
            await service._send_provider_message(
                {**FakeConnection().intent_row(), "provider": "microsoft"}, token
            )
            is None
        )
        await service._probe_mailbox("google", "staff@harvard.edu", token)
        await service._probe_mailbox("microsoft", "staff@harvard.edu", token)
    finally:
        await client.aclose()


async def test_oidc_claim_validation_binds_nonce_and_provider_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _engine, _auth, client = await make_service()
    transaction = FakeConnection.transaction_row()
    try:
        monkeypatch.setattr(jwt, "get_unverified_header", lambda _token: {"kid": "key-1"})
        monkeypatch.setattr(jwt.PyJWK, "from_dict", lambda _value: SimpleNamespace(key="key"))
        monkeypatch.setattr(
            jwt,
            "decode",
            lambda *_args, **_kwargs: {
                "nonce": "nonce",
                "hd": "harvard.edu",
                "sub": "subject-1",
                "email": "staff@harvard.edu",
                "email_verified": True,
                "name": "Staff Member",
            },
        )
        verified = await service._verify_identity(
            "google", transaction, {"id_token": "identity-token"}
        )
        assert verified["provider_tenant"] == "harvard.edu"
        assert verified["display_name"] == "Staff Member"

        monkeypatch.setattr(
            jwt,
            "decode",
            lambda *_args, **_kwargs: {
                "nonce": "nonce",
                "tid": TENANT_ID,
                "oid": "object-1",
                "preferred_username": "staff@harvard.edu",
                "name": "Microsoft Staff",
            },
        )
        microsoft = await service._verify_identity(
            "microsoft",
            {**transaction, "expected_provider_tenant": TENANT_ID},
            {"id_token": "identity-token"},
        )
        assert microsoft["subject"] == "object-1"
        assert microsoft["display_name"] == "Microsoft Staff"
    finally:
        await client.aclose()


def test_mail_helpers_cover_sanitization_serialization_and_errors() -> None:
    assert _provider(" Google ") == "google"
    with pytest.raises(NotFoundError):
        _provider("github")
    assert len(_hash("state")) == 64
    assert _email(" Staff@Harvard.edu ") == "staff@harvard.edu"
    with pytest.raises(BadRequestError):
        _email("not-an-email")
    assert _provider_email("Student <student@harvard.edu>") == "student@harvard.edu"
    assert _provider_email("invalid") == "unknown@invalid.local"
    assert _scope_values("openid email") == ["openid", "email"]
    assert _scope_values(None) == []
    assert _safe_text("<p>Hello &amp; goodbye</p>\x00").strip() == "Hello & goodbye"

    html_body = _base64url(b"<p>HTML fallback</p>")
    assert _gmail_body({"mimeType": "text/html", "body": {"data": html_body}}) == "HTML fallback"
    assert _gmail_body(
        {"mimeType": "multipart/alternative", "parts": [google_payload()["payload"]]}
    )
    assert _google_message(google_payload())["sender"] == "student@harvard.edu"
    assert _microsoft_message(microsoft_payload())["sender"] == "student@harvard.edu"

    message = _message(FakeConnection.message_row())
    assert message["recipients"] == ["staff@harvard.edu"]
    public = _public_provider_message(_google_message(google_payload()))
    assert isinstance(public["receivedAt"], str)
    intent = _send_intent_response(FakeConnection().intent_row())
    assert intent["recipients"] == ["student@harvard.edu"]
    assert intent["error"] is None
    failed_row = FakeConnection().intent_row()
    failed_row["last_error_code"] = "MAILBOX_RECONNECT_REQUIRED"
    failed_row["last_error_message"] = "Reconnect the mailbox to continue"
    assert _send_intent_response(failed_row)["error"] == {
        "code": "MAILBOX_RECONNECT_REQUIRED",
        "message": "Reconnect the mailbox to continue",
    }
    assert _iso(None) is None

    _provider_success(httpx.Response(200))
    with pytest.raises(ConflictError):
        _provider_success(httpx.Response(401))
    with pytest.raises(ApiError):
        _provider_success(httpx.Response(500))
    assert _token_refresh_requires_reconnect(
        httpx.Response(400, json={"error": "invalid_grant"})
    )
    assert not _token_refresh_requires_reconnect(
        httpx.Response(503, json={"error": "server_error"})
    )


async def test_service_validation_and_encryption_fail_closed() -> None:
    service, engine, _auth, client = await make_service()
    try:
        encrypted, nonce = service._encrypt("secret")
        assert service._decrypt(encrypted, nonce) == "secret"
        service._settings = replace(settings(), token_encryption_key="bad")  # noqa: S106
        with pytest.raises(ApiError):
            service._encryption_key()
        service._settings = settings()
        service._require_mail_scopes(
            "google",
            "personal",
            [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
            ],
        )
        with pytest.raises(ConflictError):
            service._require_mail_scopes("microsoft", "shared", ["Mail.Read", "Mail.Send"])
        with pytest.raises(ApiError):
            service._require_staff(
                staff_context().__class__(
                    tenant_id=TENANT_ID,
                    student_id=STUDENT_ID,
                    actor_id=STUDENT_ID,
                    actor_type="student",
                    authentication_method="credentials",
                    tenant_slug="harvard",
                )
            )
        with pytest.raises(BadRequestError):
            await service.search_messages(staff_context(), MAILBOX_ID, "")
        with pytest.raises(BadRequestError):
            await service.create_send_intent(staff_context(), {"mailboxId": MAILBOX_ID})

        engine.connection.missing.add("mailbox")
        await service._sync_mailbox(TENANT_ID, MAILBOX_ID)
        engine.connection.missing.remove("mailbox")
        engine.connection.missing.add("intent")
        await service._deliver_send_intent(TENANT_ID, INTENT_ID)
    finally:
        await client.aclose()


async def test_federated_auth_repository_preprovisions_and_pins_identity() -> None:
    engine = FakeEngine()
    repository = PostgresDevelopmentAuth(
        cast(Any, engine), environment="test", staff_invitation_code="invitation-code"
    )
    session = await repository.sign_in_staff_federated(
        tenant_id=TENANT_ID,
        tenant_slug="harvard",
        provider="google",
        provider_subject="immutable-subject",
        provider_tenant="harvard.edu",
        email="Staff@Harvard.edu",
        display_name="Staff Member",
    )
    assert session.context.authentication_method == "google"
    assert session.context.tenant_id == TENANT_ID
    assert any(
        "INSERT INTO staff_federated_identity" in query for query in engine.connection.queries
    )
    assert any("INSERT INTO staff_auth_session" in query for query in engine.connection.queries)

    with pytest.raises(ApiError):
        await repository.sign_in_staff_federated(
            tenant_id=TENANT_ID,
            tenant_slug="harvard",
            provider="github",
            provider_subject="subject",
            provider_tenant="harvard.edu",
            email="staff@harvard.edu",
            display_name="Staff Member",
        )


async def test_federated_auth_repository_provisions_an_allowlisted_staff_member() -> None:
    engine = FakeEngine()
    engine.connection.missing.add("staff")
    repository = PostgresDevelopmentAuth(
        cast(Any, engine), environment="test", staff_invitation_code="invitation-code"
    )

    session = await repository.sign_in_staff_federated(
        tenant_id=TENANT_ID,
        tenant_slug="harvard",
        provider="google",
        provider_subject="immutable-subject",
        provider_tenant="harvard.edu",
        email="sait.yucekaya@vekend.com",
        display_name="Sait Yucekaya",
    )

    assert session.name == "Sait Yucekaya"
    assert session.component == "Admissions"
    assert any("FROM staff_sso_provisioning_grant" in query for query in engine.connection.queries)
    assert any("INSERT INTO staff_member" in query for query in engine.connection.queries)
    assert any(
        "UPDATE staff_sso_provisioning_grant" in query for query in engine.connection.queries
    )

    engine.connection.missing.add("provisioning_grant")
    with pytest.raises(ApiError):
        await repository.sign_in_staff_federated(
            tenant_id=TENANT_ID,
            tenant_slug="harvard",
            provider="google",
            provider_subject="another-subject",
            provider_tenant="harvard.edu",
            email="another@vekend.com",
            display_name="Another User",
        )


async def test_password_fallback_and_staff_session_resolution_remain_available() -> None:
    engine = FakeEngine()
    engine.connection.password_hash = _hash_password("correct-password")
    repository = PostgresDevelopmentAuth(
        cast(Any, engine), environment="test", staff_invitation_code="invitation-code"
    )

    resolved = await repository.resolve_staff(
        "staff-session-token-that-is-long-enough", TENANT_ID, "harvard"
    )
    assert resolved is not None
    assert resolved.context.authentication_method == "google"
    assert await repository.resolve_staff("short", TENANT_ID, "harvard") is None

    student = await repository.resolve_student(
        "student-session-token-that-is-long-enough", TENANT_ID, "harvard"
    )
    assert student is not None and student.preferred_name == "Alex"
    assert await repository.resolve_student("short", TENANT_ID, "harvard") is None
    engine.connection.missing.add("student_session")
    assert (
        await repository.resolve_student(
            "another-student-session-token-that-is-long-enough", TENANT_ID, "harvard"
        )
        is None
    )
    engine.connection.missing.remove("student_session")

    student_sign_in = await repository.sign_in_student(
        tenant_id=TENANT_ID,
        tenant_slug="harvard",
        email="Student@Harvard.edu",
        password="correct-password",  # noqa: S106
    )
    assert student_sign_in.preferred_name == "Alex"
    with pytest.raises(ApiError):
        await repository.sign_in_student(
            tenant_id=TENANT_ID,
            tenant_slug="harvard",
            email="student@harvard.edu",
            password="wrong-password",  # noqa: S106
        )
    await repository.sign_out_student(None)
    await repository.sign_out_student("student-session-token-that-is-long-enough")

    signed_in = await repository.sign_in_staff(
        tenant_id=TENANT_ID,
        tenant_slug="harvard",
        email="Staff@Harvard.edu",
        password="correct-password",  # noqa: S106
    )
    assert signed_in.context.authentication_method == "credentials"
    assert signed_in.token is not None

    signed_up = await repository.sign_up_staff(
        tenant_id=TENANT_ID,
        tenant_slug="harvard",
        email="Staff@Harvard.edu",
        password="new-correct-password",  # noqa: S106
        institution_access_code="invitation-code",
    )
    assert signed_up.email == "staff@harvard.edu"
    with pytest.raises(ApiError):
        await repository.sign_up_staff(
            tenant_id=TENANT_ID,
            tenant_slug="harvard",
            email="staff@harvard.edu",
            password="new-correct-password",  # noqa: S106
            institution_access_code="wrong-code",
        )

    with pytest.raises(ApiError):
        await repository.sign_in_staff(
            tenant_id=TENANT_ID,
            tenant_slug="harvard",
            email="staff@harvard.edu",
            password="wrong-password",  # noqa: S106
        )

    engine.connection.missing.add("staff_session")
    assert (
        await repository.resolve_staff(
            "another-staff-session-token-that-is-long-enough", TENANT_ID, "harvard"
        )
        is None
    )
    await repository.sign_out_staff(None)
    await repository.sign_out_staff("staff-session-token-that-is-long-enough")

    demo = await repository.demo_student(TENANT_ID, "harvard")
    assert demo.preferred_name == "Alex"
    assert (
        await repository.demo_student_by_reference(TENANT_ID, "harvard", "SYN-000101")
    ).external_ref == "SYN-000101"
    assert (
        await repository.demo_student_by_reference(TENANT_ID, "harvard", STUDENT_ID)
    ).context.student_id == STUDENT_ID
    with pytest.raises(ApiError):
        await repository.demo_student_by_reference(TENANT_ID, "harvard", "")

    engine.connection.missing.add("staff")
    engine.connection.missing.add("provisioning_grant")
    with pytest.raises(ApiError):
        await repository.sign_in_staff_federated(
            tenant_id=TENANT_ID,
            tenant_slug="harvard",
            provider="microsoft",
            provider_subject="object-id",
            provider_tenant=TENANT_ID,
            email="staff@harvard.edu",
            display_name="Staff Member",
        )
    engine.connection.missing.remove("staff")
    engine.connection.missing.remove("provisioning_grant")
    engine.connection.missing.add("identity")
    with pytest.raises(ApiError):
        await repository.sign_in_staff_federated(
            tenant_id=TENANT_ID,
            tenant_slug="harvard",
            provider="google",
            provider_subject="different-subject",
            provider_tenant="harvard.edu",
            email="staff@harvard.edu",
            display_name="Staff Member",
        )


async def test_mail_http_boundary_delegates_and_sets_secure_staff_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = staff_context()
    app = FastAPI()
    app.state.http_settings = HttpSettings(
        environment="preview",
        browser_auth_required=True,
        session_cookie_samesite="lax",
    )
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "app": app})
    session = StaffSession(
        context=context,
        name="Staff Member",
        email="staff@harvard.edu",
        component="Advising",
        token="staff-session-token-that-is-long-enough",  # noqa: S106
        expires_at_epoch=4_102_444_800,
    )
    service = SimpleNamespace(
        staff_options=AsyncMock(return_value={"providers": ["google"]}),
        start_sso=AsyncMock(return_value="https://accounts.google.com/auth"),
        complete_sso=AsyncMock(return_value=(session, "https://portal.example/harvard/staff")),
        start_mailbox_connect=AsyncMock(return_value="https://accounts.google.com/mail-auth"),
        complete_mailbox_connect=AsyncMock(
            return_value="https://portal.example/harvard/staff/mail"
        ),
        list_mailboxes=AsyncMock(return_value={"items": [], "total": 0}),
        disconnect_mailbox=AsyncMock(return_value=None),
        recent_messages=AsyncMock(return_value={"items": [], "total": 0}),
        search_messages=AsyncMock(return_value={"items": [], "total": 0}),
        create_send_intent=AsyncMock(return_value={"id": INTENT_ID}),
        get_send_intent=AsyncMock(return_value={"id": INTENT_ID}),
        confirm_send_intent=AsyncMock(return_value={"status": "queued"}),
    )
    typed_service = cast(Any, service)
    require_portal_tenant = AsyncMock(return_value="harvard")
    monkeypatch.setattr(mail_routes, "_require_portal_tenant", require_portal_tenant)

    assert await staff_auth_options(request, typed_service, "harvard") == {"providers": ["google"]}
    assert (
        await start_staff_sso("google", request, typed_service, "harvard", None)
    ).status_code == 302
    assert require_portal_tenant.await_count == 2
    service.staff_options.assert_awaited_once_with("harvard")
    service.start_sso.assert_awaited_once_with("google", "harvard", None)
    callback = await complete_staff_sso(
        "google", request, typed_service, "state-value-that-is-definitely-long-enough", "code"
    )
    assert callback.status_code == 303
    assert "vv_staff_session=" in callback.headers["set-cookie"]
    assert (
        await start_mailbox_connect(
            "google", context, typed_service, "personal", "staff@harvard.edu", None
        )
    ).status_code == 302
    assert (
        await complete_mailbox_connect(
            "google", typed_service, "state-value-that-is-definitely-long-enough", "code"
        )
    ).status_code == 303
    assert await list_mailboxes(context, typed_service) == {"items": [], "total": 0}
    assert (await disconnect_mailbox(MAILBOX_ID, context, typed_service)).status_code == 204
    assert await recent_mail(context, typed_service, MAILBOX_ID, 10) == {"items": [], "total": 0}
    assert await search_mail(
        context, typed_service, MailSearchRequest(mailboxId=MAILBOX_ID, query="deadline")
    ) == {"items": [], "total": 0}
    assert await create_send_intent(
        context,
        typed_service,
        CreateSendIntentRequest(
            mailboxId=MAILBOX_ID,
            studentId=STUDENT_ID,
            subject="Follow-up",
            body="Please review this.",
        ),
    ) == {"id": INTENT_ID}
    assert await get_send_intent(INTENT_ID, context, typed_service) == {"id": INTENT_ID}
    assert await confirm_send_intent(
        INTENT_ID,
        context,
        typed_service,
        "request-1",
        ConfirmSendIntentRequest(expectedVersion=1, contentSha256="a" * 64),
    ) == {"status": "queued"}

    with pytest.raises(BadRequestError):
        await complete_staff_sso(
            "google", request, typed_service, "state-value-that-is-definitely-long-enough", None
        )
    with pytest.raises(BadRequestError):
        await complete_mailbox_connect(
            "google", typed_service, "state-value-that-is-definitely-long-enough", None, "denied"
        )
