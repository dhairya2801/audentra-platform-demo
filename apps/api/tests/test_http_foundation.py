from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient, Response

from audentra.core.errors import ApiError
from audentra.core.ports import BinaryPayload, ServiceCall
from audentra.interfaces.http.app import create_app
from audentra.interfaces.http.config import HttpSettings

DOCUMENT_ID = "00000000-0000-7000-8000-000000000311"
BUNDLE_ID = "00000000-0000-7000-8000-000000000312"
HARVARD_TENANT_ID = "00000000-0000-7000-8000-000000000002"

pytestmark = pytest.mark.anyio


class FakePlatformService:
    def __init__(self) -> None:
        self.calls: list[ServiceCall] = []

    async def dispatch(self, call: ServiceCall) -> object:
        self.calls.append(call)
        if call.operation == "student.get_document_content":
            return BinaryPayload(
                data=b"%PDF-test",
                media_type="application/pdf",
                file_name='transcript".pdf',
                cache_control="private, no-store",
            )
        if call.operation == "student.get_document_profile_photo":
            return BinaryPayload(
                data=b"jpeg-test",
                media_type="application/octet-stream",
                file_name="ignored.bin",
                cache_control="ignored",
            )
        return {"operation": call.operation}


@pytest.fixture
def service() -> FakePlatformService:
    return FakePlatformService()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(service: FakePlatformService) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app(service=service))
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


def error_body(response: Response) -> dict[str, Any]:
    payload = cast(dict[str, Any], response.json())
    return cast(dict[str, Any], payload["error"])


async def test_request_context_preserves_safe_correlation_id(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Correlation-Id": "portal-session:123"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "portal-session:123"
    assert response.headers["x-correlation-id"] == "portal-session:123"
    assert len(response.headers["x-trace-id"]) == 32


async def test_request_context_replaces_unsafe_correlation_id(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Correlation-Id": "bad id"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] != "bad id"
    assert response.headers["x-correlation-id"] == response.headers["x-request-id"]


async def test_validation_is_400_with_stable_envelope(client: AsyncClient) -> None:
    response = await client.patch(
        "/v1/student/profile",
        json={"expected_version": 1, "unknownField": True},
    )

    assert response.status_code == 400
    assert error_body(response)["code"] == "VALIDATION_ERROR"
    assert error_body(response)["requestId"] == response.headers["x-request-id"]


async def test_camel_case_body_is_dispatched_without_python_names(
    client: AsyncClient, service: FakePlatformService
) -> None:
    response = await client.patch(
        "/v1/student/profile",
        json={
            "expectedVersion": 4,
            "preferredName": "Sam",
            "communicationPreference": "sms",
        },
    )

    assert response.status_code == 200
    assert service.calls[-1].payload == {
        "expectedVersion": 4,
        "preferredName": "Sam",
        "communicationPreference": "sms",
    }


async def test_idempotency_header_is_required_and_propagated(
    client: AsyncClient, service: FakePlatformService
) -> None:
    body = {
        "fileName": "transcript.pdf",
        "mimeType": "application/pdf",
        "sizeBytes": 128,
        "category": "transcript",
    }
    missing = await client.post("/v1/student/documents", json=body)
    accepted = await client.post(
        "/v1/student/documents",
        json=body,
        headers={"Idempotency-Key": "upload:12345678"},
    )

    assert missing.status_code == 400
    assert error_body(missing)["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert accepted.status_code == 201
    assert service.calls[-1].idempotency_key == "upload:12345678"


async def test_staff_routes_require_explicit_demo_staff_identity(
    client: AsyncClient, service: FakePlatformService
) -> None:
    denied = await client.get("/v1/staff/action-center")
    accepted = await client.get("/v1/staff/action-center", headers={"X-Demo-Actor-Type": "staff"})

    assert denied.status_code == 401
    assert error_body(denied)["code"] == "UNAUTHORIZED"
    assert accepted.status_code == 200
    assert service.calls[-1].auth is not None
    assert service.calls[-1].auth.actor_type == "staff"


async def test_tenant_slug_resolves_to_uuid_tenant(
    client: AsyncClient, service: FakePlatformService
) -> None:
    response = await client.get("/v1/student/dashboard", headers={"X-Tenant-Slug": "harvard"})

    assert response.status_code == 200
    assert service.calls[-1].auth is not None
    assert service.calls[-1].auth.tenant_slug == "harvard"
    assert service.calls[-1].auth.tenant_id == HARVARD_TENANT_ID


@pytest.mark.parametrize(
    ("headers", "message_fragment"),
    [
        ({"X-Tenant-Slug": "unknown"}, "not recognized"),
        (
            {
                "X-Tenant-Slug": "harvard",
                "X-Demo-Tenant-Id": "00000000-0000-7000-8000-000000000001",
            },
            "conflicts",
        ),
    ],
)
async def test_unknown_or_conflicting_tenant_slug_is_rejected(
    client: AsyncClient, headers: dict[str, str], message_fragment: str
) -> None:
    response = await client.get("/v1/student/dashboard", headers=headers)

    assert response.status_code == 401
    assert message_fragment in error_body(response)["message"]


def test_tenant_slug_map_is_environment_configurable_and_immutable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = "00000000-0000-7000-8000-000000000099"
    monkeypatch.setenv("TENANT_SLUG_MAP", f'{{"custom":"{tenant_id}"}}')
    monkeypatch.setenv("DOCUMENT_WORKER_TOKEN", "   ")
    settings = HttpSettings.from_environment()
    manual_settings = HttpSettings(tenant_slug_ids={"custom": tenant_id})

    assert settings.tenant_slug_ids == {"custom": tenant_id}
    assert settings.document_worker_token == HttpSettings().document_worker_token
    with pytest.raises(TypeError):
        settings.tenant_slug_ids["other"] = tenant_id
    with pytest.raises(TypeError):
        manual_settings.tenant_slug_ids["other"] = tenant_id  # type: ignore[index]


async def test_worker_token_uses_existing_header_alias(
    client: AsyncClient, service: FakePlatformService
) -> None:
    route = f"/v1/student/internal/document-extractions/{DOCUMENT_ID}"
    denied = await client.post(route, headers={"X-VV-Worker-Token": "wrong-token"})
    accepted = await client.post(
        route,
        headers={"X-VV-Worker-Token": "local-development-document-worker-token"},
    )

    assert denied.status_code == 403
    assert error_body(denied)["code"] == "WORKER_AUTHENTICATION_FAILED"
    assert accepted.status_code == 200
    assert service.calls[-1].operation == "internal.process_document_extraction"


async def test_worker_token_bypasses_browser_cookie_requirement_only_for_internal_route(
    service: FakePlatformService,
) -> None:
    app = create_app(
        service=service,
        settings=HttpSettings(browser_auth_required=True),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as strict_client:
        internal = await strict_client.post(
            f"/v1/student/internal/document-extractions/{DOCUMENT_ID}",
            headers={"X-VV-Worker-Token": "local-development-document-worker-token"},
        )
        browser_route = await strict_client.get("/v1/student/dashboard")

    assert internal.status_code == 200
    assert browser_route.status_code == 401
    assert service.calls[-1].operation == "internal.process_document_extraction"


async def test_cors_preflight_allows_tenant_slug_and_worker_token(
    client: AsyncClient,
) -> None:
    response = await client.options(
        "/v1/student/dashboard",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Tenant-Slug,X-VV-Worker-Token",
        },
    )

    assert response.status_code == 200
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "x-tenant-slug" in allowed
    assert "x-vv-worker-token" in allowed
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_multipart_upload_preserves_optional_bundle_id(
    client: AsyncClient, service: FakePlatformService
) -> None:
    response = await client.post(
        "/v1/student/documents/upload",
        headers={"Idempotency-Key": "upload:12345678"},
        files={"file": ("transcript.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
        data={"category": "transcript", "uploadBundleId": BUNDLE_ID},
    )

    assert response.status_code == 201
    upload = service.calls[-1].upload
    assert upload is not None
    assert upload.content == b"%PDF-1.4\n%%EOF"
    assert upload.category == "transcript"
    assert upload.upload_bundle_id == BUNDLE_ID


async def test_multipart_upload_rejects_signature_mismatch(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/student/documents/upload",
        headers={"Idempotency-Key": "upload:12345678"},
        files={"file": ("transcript.pdf", b"not-a-pdf", "application/pdf")},
    )

    assert response.status_code == 415
    assert error_body(response)["code"] == "FILE_SIGNATURE_MISMATCH"


async def test_retry_extraction_accepts_empty_object_only(client: AsyncClient) -> None:
    route = f"/v1/student/documents/{DOCUMENT_ID}/retry-extraction"
    headers = {"Idempotency-Key": "retry:12345678"}

    assert (await client.post(route, headers=headers)).status_code == 200
    assert (await client.post(route, headers=headers, json={})).status_code == 200
    rejected = await client.post(route, headers=headers, json={"force": True})
    assert rejected.status_code == 400
    assert error_body(rejected)["code"] == "RETRY_EXTRACTION_BODY_NOT_ALLOWED"


async def test_binary_routes_preserve_content_headers(client: AsyncClient) -> None:
    content = await client.get(f"/v1/student/documents/{DOCUMENT_ID}/content")
    photo = await client.get(f"/v1/student/documents/{DOCUMENT_ID}/profile-photo")

    assert content.status_code == 200
    assert content.content == b"%PDF-test"
    assert content.headers["content-type"] == "application/pdf"
    assert content.headers["content-disposition"] == 'inline; filename="transcript.pdf"'
    assert content.headers["cache-control"] == "private, no-store"
    assert photo.status_code == 200
    assert photo.content == b"jpeg-test"
    assert photo.headers["content-type"] == "image/jpeg"
    assert photo.headers["cache-control"] == "private, max-age=300"


async def test_api_error_from_service_keeps_public_envelope() -> None:
    class ConflictService:
        async def dispatch(self, _call: ServiceCall) -> object:
            raise ApiError(409, "VERSION_CONFLICT", "The record changed")

    transport = ASGITransport(app=create_app(service=ConflictService()))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/health")

    assert response.status_code == 409
    assert error_body(response) == {
        "code": "VERSION_CONFLICT",
        "message": "The record changed",
        "requestId": response.headers["x-request-id"],
    }
