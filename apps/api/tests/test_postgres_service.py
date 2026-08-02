from __future__ import annotations

import asyncio
import base64
from contextlib import AbstractAsyncContextManager
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import fitz  # type: ignore[import-untyped]
import pytest
from PIL import Image, ImageDraw

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.core.ports import ServiceCall
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.postgres_service import (
    HARVARD_TENANT_ID,
    PostgresPlatformService,
    PostgresRepositoryBundle,
    PostgresSignedDocumentGenerator,
    StudentAI,
)
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository

AUTH = AuthContext(
    tenant_id="00000000-0000-7000-8000-000000000001",
    student_id="10000000-0000-7000-8000-000000000001",
    actor_id="10000000-0000-7000-8000-000000000001",
    actor_type="student",
)


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        sha256: str | None = None,
    ) -> str:
        assert content_type == "application/pdf"
        self.objects[key] = body
        return sha256 or ""

    async def get(self, key: str) -> bytes:
        return self.objects[key]


class FakeSignedRepository:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def get_student_documents(self, _auth: AuthContext) -> dict[str, Any]:
        return {"items": [], "total": 0}

    async def save_student_signed_document(
        self, _auth: AuthContext, document: dict[str, Any], _request_id: str
    ) -> dict[str, Any]:
        self.saved.append(document)
        return document


def _onboarding(method: str, image_data: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "signatureConsent": True,
        "signatureFullName": "Alex Morgan",
        "signatureMethod": method,
        "signedDocumentIds": ["ferpa_release"],
    }
    if image_data is not None:
        data["signatureImageData"] = image_data
    return {
        "status": "completed",
        "completedAt": "2026-07-24T12:00:00.000Z",
        "version": 9,
        "data": data,
    }


def _drawn_signature() -> str:
    image = Image.new("RGBA", (300, 80), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.line((10, 60, 80, 20, 150, 58, 280, 15), fill=(23, 63, 49, 255), width=5)
    output = BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def test_signed_generator_uses_default_aster_template_and_typed_signature() -> None:
    repository = FakeSignedRepository()
    storage = FakeStorage()
    created = asyncio.run(
        PostgresSignedDocumentGenerator().ensure(
            auth=AUTH,
            onboarding=_onboarding("typed"),
            repository=cast(PostgresPortalRepository, repository),
            storage=storage,
            request_id="request-1",
        )
    )

    assert created == 1
    document = repository.saved[0]
    signed = storage.objects[document["storageKey"]]
    assert signed.startswith(b"%PDF-")
    pdf = fitz.open(stream=signed, filetype="pdf")
    text = " ".join(page.get_text() for page in pdf)
    assert "Aster University" in text
    assert "Alex Morgan" in text
    assert pdf.metadata["author"] == "Alex Morgan"


def test_signed_generator_uses_injected_harvard_template_and_drawn_signature(
    tmp_path: Path,
) -> None:
    source = Path("assets/onboarding/harvard-ferpa-release.pdf")
    (tmp_path / source.name).write_bytes(source.read_bytes())
    repository = FakeSignedRepository()
    storage = FakeStorage()
    harvard_auth = AuthContext(
        tenant_id=HARVARD_TENANT_ID,
        student_id=AUTH.student_id,
        actor_id=AUTH.actor_id,
        actor_type="student",
    )

    created = asyncio.run(
        PostgresSignedDocumentGenerator(tmp_path).ensure(
            auth=harvard_auth,
            onboarding=_onboarding("drawn", _drawn_signature()),
            repository=cast(PostgresPortalRepository, repository),
            storage=storage,
            request_id="request-2",
        )
    )

    assert created == 1
    document = repository.saved[0]
    assert document["signatureMethod"] == "drawn"
    signed = storage.objects[document["storageKey"]]
    pdf = fitz.open(stream=signed, filetype="pdf")
    assert "Harvard University" in " ".join(page.get_text() for page in pdf)
    assert pdf.metadata["author"] == "Alex Morgan"
    assert len(signed) > len(source.read_bytes())


class FakeConnection:
    async def execute(self, _statement: object) -> None:
        return None


class ConnectionContext(AbstractAsyncContextManager[FakeConnection]):
    async def __aenter__(self) -> FakeConnection:
        return FakeConnection()

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeEngine:
    def connect(self) -> ConnectionContext:
        return ConnectionContext()


class FailingEngine:
    def connect(self) -> ConnectionContext:
        raise RuntimeError("database offline")


class PortalForDispatch:
    def __init__(self, engine: object) -> None:
        self.engine = engine

    async def get_student_appointments(self, _auth: AuthContext) -> dict[str, Any]:
        return {"items": [], "total": 0}


class EmptyAdapter:
    pass


class NoopSignedDocuments:
    async def ensure(self, **_kwargs: object) -> int:
        return 0


def _service(engine: object) -> PostgresPlatformService:
    bundle = PostgresRepositoryBundle(
        platform=cast(PostgresPlatformRepository, EmptyAdapter()),
        portal=cast(PostgresPortalRepository, PortalForDispatch(engine)),
        staff=cast(PostgresStaffRepository, EmptyAdapter()),
    )
    return PostgresPlatformService(
        bundle,
        FakeStorage(),
        cast(StudentAI, EmptyAdapter()),
        NoopSignedDocuments(),
        "worker-token",
    )


def test_health_has_timestamp_and_readiness_queries_database() -> None:
    service = _service(FakeEngine())
    liveness = asyncio.run(service.dispatch(ServiceCall("health.liveness", None, "request-health")))
    readiness = asyncio.run(
        service.dispatch(ServiceCall("health.readiness", None, "request-ready"))
    )

    assert liveness["status"] == "ok"  # type: ignore[index]
    assert str(liveness["timestamp"]).endswith("Z")  # type: ignore[index]
    assert readiness["status"] == "ready"  # type: ignore[index]
    assert str(readiness["timestamp"]).endswith("Z")  # type: ignore[index]


def test_readiness_maps_database_failure_to_public_503() -> None:
    with pytest.raises(ApiError) as raised:
        asyncio.run(
            _service(FailingEngine()).dispatch(
                ServiceCall("health.readiness", None, "request-ready")
            )
        )
    assert raised.value.status_code == 503
    assert raised.value.code == "DATABASE_UNAVAILABLE"


def test_dispatch_routes_portal_operation_without_http_dependency() -> None:
    result = asyncio.run(
        _service(FakeEngine()).dispatch(
            ServiceCall("student.list_appointments", AUTH, "request-appointments")
        )
    )
    assert result == {"items": [], "total": 0}
