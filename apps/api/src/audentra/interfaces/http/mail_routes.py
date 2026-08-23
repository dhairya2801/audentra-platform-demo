"""Institutional staff SSO and delegated mailbox HTTP boundary."""

from __future__ import annotations

import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Path, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from audentra.contracts.responses import ApiErrorEnvelope
from audentra.core.errors import BadRequestError

from .dependencies import (
    AuthDependency,
    IdempotencyDependency,
    StaffEmailServiceDependency,
    get_settings,
    resolve_request_tenant,
)

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class MailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MailSearchRequest(MailRequest):
    mailboxId: NonEmpty
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    limit: Annotated[int, Field(ge=1, le=25)] = 10


class CreateSendIntentRequest(MailRequest):
    mailboxId: UUID
    studentId: UUID | None = None
    replyToMessageId: UUID | None = None
    interactionId: UUID | None = None
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=998)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)]


class ConfirmSendIntentRequest(MailRequest):
    expectedVersion: Annotated[int, Field(ge=1)]
    contentSha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


mail_router = APIRouter(
    responses={
        status_code: {"model": ApiErrorEnvelope}
        for status_code in (400, 401, 403, 404, 409, 500, 502, 503)
    }
)


def _set_staff_cookie(
    response: Response, request: Request, token: str, expires_at_epoch: int
) -> None:
    settings = get_settings(request)
    response.set_cookie(
        key="vv_staff_session",
        value=token,
        max_age=max(0, expires_at_epoch - int(time.time())),
        expires=expires_at_epoch,
        path="/",
        secure=settings.secure_cookies,
        httponly=True,
        samesite=settings.session_cookie_samesite,
    )


async def _require_portal_tenant(request: Request, tenant_slug: str) -> str:
    """Bind an unauthenticated SSO start to this portal's resolved tenant."""

    _, resolved_slug = await resolve_request_tenant(request)
    requested_slug = tenant_slug.strip().lower()
    if resolved_slug is None or requested_slug != resolved_slug.lower():
        raise BadRequestError(
            "SSO_TENANT_MISMATCH",
            "The requested tenant does not match this institution portal",
        )
    return resolved_slug


@mail_router.get("/v1/auth/staff/options", response_model=None)
async def staff_auth_options(
    request: Request,
    service: StaffEmailServiceDependency,
    tenant_slug: Annotated[str, Query(alias="tenantSlug", min_length=1, max_length=80)],
) -> object:
    return await service.staff_options(await _require_portal_tenant(request, tenant_slug))


@mail_router.get("/v1/auth/staff/sso/{provider}/start", response_model=None)
async def start_staff_sso(
    provider: Annotated[str, Path(pattern="^(google|microsoft)$")],
    request: Request,
    service: StaffEmailServiceDependency,
    tenant_slug: Annotated[str, Query(alias="tenantSlug", min_length=1, max_length=80)],
    return_to: Annotated[str | None, Query(alias="returnTo", max_length=1000)] = None,
) -> Response:
    location = await service.start_sso(
        provider,
        await _require_portal_tenant(request, tenant_slug),
        return_to,
    )
    return RedirectResponse(location, status_code=302)


@mail_router.get("/v1/auth/staff/sso/{provider}/callback", response_model=None)
async def complete_staff_sso(
    provider: Annotated[str, Path(pattern="^(google|microsoft)$")],
    request: Request,
    service: StaffEmailServiceDependency,
    state: Annotated[str, Query(min_length=32, max_length=256)],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> Response:
    if error or not code:
        raise BadRequestError("OAUTH_AUTHORIZATION_DENIED", "Institutional sign-in was cancelled")
    session, location = await service.complete_sso(provider, state, code)
    if session.token is None or session.expires_at_epoch is None:
        raise BadRequestError("AUTH_SESSION_FAILED", "The staff session could not be created")
    response = RedirectResponse(location, status_code=303)
    _set_staff_cookie(response, request, session.token, session.expires_at_epoch)
    return response


@mail_router.get("/v1/staff/mail/oauth/{provider}/start", response_model=None)
async def start_mailbox_connect(
    provider: Annotated[str, Path(pattern="^(google|microsoft)$")],
    auth: AuthDependency,
    service: StaffEmailServiceDependency,
    mailbox_kind: Annotated[str, Query(alias="mailboxKind", pattern="^(personal|shared)$")],
    address: Annotated[str, Query(min_length=3, max_length=320)],
    return_to: Annotated[str | None, Query(alias="returnTo", max_length=1000)] = None,
) -> Response:
    location = await service.start_mailbox_connect(auth, provider, mailbox_kind, address, return_to)
    return RedirectResponse(location, status_code=302)


@mail_router.get("/v1/staff/mail/oauth/{provider}/callback", response_model=None)
async def complete_mailbox_connect(
    provider: Annotated[str, Path(pattern="^(google|microsoft)$")],
    service: StaffEmailServiceDependency,
    state: Annotated[str, Query(min_length=32, max_length=256)],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> Response:
    if error or not code:
        raise BadRequestError("OAUTH_AUTHORIZATION_DENIED", "Mailbox connection was cancelled")
    location = await service.complete_mailbox_connect(provider, state, code)
    return RedirectResponse(location, status_code=303)


@mail_router.get("/v1/staff/mailboxes", response_model=None)
async def list_mailboxes(auth: AuthDependency, service: StaffEmailServiceDependency) -> object:
    return await service.list_mailboxes(auth)


@mail_router.delete("/v1/staff/mailboxes/{mailbox_id}/connection", status_code=204)
async def disconnect_mailbox(
    mailbox_id: Annotated[str, Path(min_length=36, max_length=36)],
    auth: AuthDependency,
    service: StaffEmailServiceDependency,
) -> Response:
    await service.disconnect_mailbox(auth, mailbox_id)
    return Response(status_code=204)


@mail_router.get("/v1/staff/mail/messages", response_model=None)
async def recent_mail(
    auth: AuthDependency,
    service: StaffEmailServiceDependency,
    mailbox_id: Annotated[str, Query(alias="mailboxId", min_length=36, max_length=36)],
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> object:
    return await service.recent_messages(auth, mailbox_id, limit=limit)


@mail_router.post("/v1/staff/mail/search", response_model=None)
async def search_mail(
    auth: AuthDependency,
    service: StaffEmailServiceDependency,
    body: Annotated[MailSearchRequest, Body()],
) -> object:
    return await service.search_messages(auth, body.mailboxId, body.query, limit=body.limit)


@mail_router.post("/v1/staff/mail/send-intents", status_code=201, response_model=None)
async def create_send_intent(
    auth: AuthDependency,
    service: StaffEmailServiceDependency,
    body: Annotated[CreateSendIntentRequest, Body()],
) -> object:
    return await service.create_send_intent(auth, body.model_dump())


@mail_router.get("/v1/staff/mail/send-intents/{intent_id}", response_model=None)
async def get_send_intent(
    intent_id: Annotated[str, Path(min_length=36, max_length=36)],
    auth: AuthDependency,
    service: StaffEmailServiceDependency,
) -> object:
    return await service.get_send_intent(auth, intent_id)


@mail_router.post(
    "/v1/staff/mail/send-intents/{intent_id}/confirm",
    status_code=202,
    response_model=None,
)
async def confirm_send_intent(
    intent_id: Annotated[str, Path(min_length=36, max_length=36)],
    auth: AuthDependency,
    service: StaffEmailServiceDependency,
    idempotency_key: IdempotencyDependency,
    body: Annotated[ConfirmSendIntentRequest, Body()],
) -> object:
    return await service.confirm_send_intent(
        auth,
        intent_id,
        body.expectedVersion,
        body.contentSha256,
        idempotency_key,
    )
