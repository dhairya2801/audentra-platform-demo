from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from audentra.bootstrap import api as api_bootstrap
from audentra.bootstrap.api import ApiRuntimeResources, create_production_app
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.ports import ServiceCall
from audentra.infrastructure.postgres.auth_repository import PostgresDevelopmentAuth


class StubService:
    async def dispatch(self, call: ServiceCall) -> object:
        return {"status": "ok", "operation": call.operation}


class StubClosable:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.closed = False
        self.error = error

    async def close(self) -> None:
        self.closed = True
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True
        if self.error is not None:
            raise self.error


class StubEngine:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.closed = False
        self.error = error

    async def dispose(self) -> None:
        self.closed = True
        if self.error is not None:
            raise self.error


@pytest.mark.anyio
async def test_production_lifespan_installs_and_closes_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = RuntimeSettings.from_environment({"AUDENTRA_ENV": "test"}, package_root=tmp_path)
    http = StubClosable()
    storage = StubClosable()
    engine = StubEngine()

    async def build(_settings: RuntimeSettings) -> ApiRuntimeResources:
        assert _settings is settings
        return ApiRuntimeResources(
            engine=engine,  # type: ignore[arg-type]
            http_client=http,  # type: ignore[arg-type]
            object_storage=storage,  # type: ignore[arg-type]
            service=StubService(),
        )

    monkeypatch.setattr(api_bootstrap, "build_api_runtime", build)
    app = create_production_app(settings)

    async with app.router.lifespan_context(app):
        assert app.state.platform_service.__class__ is StubService
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["operation"] == "health.liveness"

    assert http.closed is True
    assert storage.closed is True
    assert engine.closed is True


def test_production_composition_fails_closed_for_demo_auth(tmp_path: Path) -> None:
    settings = RuntimeSettings.from_environment(
        {
            "AUDENTRA_ENV": "production",
            "DATABASE_URL": "postgresql://example/prod",
            "DOCUMENT_WORKER_TOKEN": "x" * 40,
            "OBJECT_STORAGE_SECRET_KEY": "external-secret",
        },
        package_root=tmp_path,
    )
    app: FastAPI = create_production_app(settings)

    async def start() -> None:
        async with app.router.lifespan_context(app):
            pass

    with pytest.raises(ValueError, match="identity adapter"):
        asyncio.run(start())


def test_preview_composes_the_browser_auth_adapter() -> None:
    auth = PostgresDevelopmentAuth(
        StubEngine(),  # type: ignore[arg-type]
        environment="preview",
        staff_invitation_code="x" * 32,
        demo_student_ids={"aster": "00000000-0000-7000-8000-000000000101"},
    )

    assert auth is not None


def test_browser_auth_adapter_still_rejects_production() -> None:
    with pytest.raises(ValueError, match="disabled in production"):
        PostgresDevelopmentAuth(
            StubEngine(),  # type: ignore[arg-type]
            environment="production",
            staff_invitation_code="x" * 32,
            demo_student_ids={"aster": "00000000-0000-7000-8000-000000000101"},
        )


def test_api_resource_close_attempts_storage_and_engine_after_http_failure() -> None:
    http = StubClosable(error=RuntimeError("http close failed"))
    storage = StubClosable()
    engine = StubEngine()
    resources = ApiRuntimeResources(
        engine=engine,  # type: ignore[arg-type]
        http_client=http,  # type: ignore[arg-type]
        object_storage=storage,  # type: ignore[arg-type]
        service=StubService(),
    )

    with pytest.raises(RuntimeError, match="http close failed"):
        asyncio.run(resources.close())

    assert http.closed is True
    assert storage.closed is True
    assert engine.closed is True


def test_api_resource_close_attempts_engine_after_storage_failure() -> None:
    http = StubClosable()
    storage = StubClosable(error=RuntimeError("storage close failed"))
    engine = StubEngine()
    resources = ApiRuntimeResources(
        engine=engine,  # type: ignore[arg-type]
        http_client=http,  # type: ignore[arg-type]
        object_storage=storage,  # type: ignore[arg-type]
        service=StubService(),
    )

    with pytest.raises(RuntimeError, match="storage close failed"):
        asyncio.run(resources.close())

    assert http.closed is True
    assert storage.closed is True
    assert engine.closed is True


@pytest.mark.anyio
async def test_api_builder_disposes_created_resources_when_composition_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = RuntimeSettings.from_environment({"AUDENTRA_ENV": "test"}, package_root=tmp_path)
    http = StubClosable(error=RuntimeError("http cleanup failed"))
    storage = StubClosable()
    engine = StubEngine()

    monkeypatch.setattr(api_bootstrap, "create_database_engine", lambda *_args: engine)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: http)
    monkeypatch.setattr(api_bootstrap, "S3ObjectStorage", lambda _settings: storage)

    def fail_repository(_engine: object) -> object:
        raise ValueError("repository composition failed")

    monkeypatch.setattr(api_bootstrap, "PostgresPlatformRepository", fail_repository)

    with pytest.raises(RuntimeError, match="http cleanup failed"):
        await api_bootstrap.build_api_runtime(settings)

    assert http.closed is True
    assert storage.closed is True
    assert engine.closed is True
