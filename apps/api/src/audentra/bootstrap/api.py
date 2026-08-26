"""Production API composition with one lifecycle for pooled resources."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.core.oidc import OidcAuthService, UnavailableOidcAuthService
from audentra.core.ports import (
    BrowserAuthService,
    PlatformService,
    UnavailableBrowserAuthService,
    UnavailablePlatformService,
)
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres.advising_repository import PostgresAdvisingRepository
from audentra.infrastructure.postgres.auth_repository import PostgresDevelopmentAuth
from audentra.infrastructure.postgres.edward_feedback_repository import (
    PostgresEdwardFeedbackRepository,
)
from audentra.infrastructure.postgres.ferpa_repository import PostgresFerpaRepository
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
)
from audentra.infrastructure.postgres.morning_brew_repository import (
    PostgresMorningBrewRepository,
)
from audentra.infrastructure.postgres.oidc_repository import PostgresOidcAuth
from audentra.infrastructure.postgres.platform_repository import PostgresPlatformRepository
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.postgres_service import (
    PostgresPlatformService,
    PostgresRepositoryBundle,
    PostgresSignedDocumentGenerator,
)
from audentra.infrastructure.postgres.staff_assistant_repository import (
    PostgresStaffAssistantRepository,
)
from audentra.infrastructure.postgres.staff_email_service import PostgresStaffEmailService
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.infrastructure.postgres.tenant_repository import PostgresTenantRepository
from audentra.infrastructure.postgres.voice_repository import PostgresVoiceSessionRepository
from audentra.infrastructure.storage import ObjectStorage, create_object_storage
from audentra.infrastructure.voice import (
    UnavailableVoiceSessionService,
    VoiceSessionService,
    VoiceSessionServiceProtocol,
)
from audentra.integrations.ai.gateway import StudentAIGateway
from audentra.integrations.ai.prompt_runtime import (
    PostgresPromptRuntimeRepository,
    VersionedPromptRuntime,
)
from audentra.integrations.ai.provider import CompletionClient
from audentra.interfaces.http.app import create_app

from .settings import RuntimeSettings


@dataclass(slots=True)
class ApiRuntimeResources:
    """All resources owned by one API process and closed in reverse order."""

    engine: AsyncEngine
    http_client: httpx.AsyncClient
    object_storage: ObjectStorage
    service: PlatformService
    auth_service: BrowserAuthService = field(default_factory=UnavailableBrowserAuthService)
    oidc_auth_service: OidcAuthService = field(default_factory=UnavailableOidcAuthService)
    staff_email_service: PostgresStaffEmailService | None = None
    voice_service: VoiceSessionServiceProtocol = field(
        default_factory=UnavailableVoiceSessionService
    )

    async def close(self) -> None:
        try:
            await self.http_client.aclose()
        finally:
            try:
                await self.object_storage.close()
            finally:
                await self.engine.dispose()


async def build_api_runtime(settings: RuntimeSettings) -> ApiRuntimeResources:
    """Compose the framework-neutral application service and concrete adapters."""

    engine = create_database_engine(settings.database_url, settings.database)
    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        timeout=httpx.Timeout(45.0, connect=10.0, pool=5.0),
        follow_redirects=False,
    )
    storage: ObjectStorage | None = None
    try:
        storage = create_object_storage(settings.object_storage)
        platform = PostgresPlatformRepository(engine)
        portal = PostgresPortalRepository(engine)
        staff = PostgresStaffRepository(engine, portal)
        managed = PostgresManagedConfigurationRepository(engine)
        tenant = PostgresTenantRepository(engine)
        prompt_runtime = VersionedPromptRuntime(PostgresPromptRuntimeRepository(engine))
        ai = StudentAIGateway(
            settings.ai,
            CompletionClient(http_client, recorder=platform.record_ai_provider_response),
            prompt_runtime,
        )
        advising = PostgresAdvisingRepository(engine, portal)
        service = PostgresPlatformService(
            PostgresRepositoryBundle(
                platform=platform,
                portal=portal,
                staff=staff,
                managed=managed,
                tenant=tenant,
                staff_assistant=PostgresStaffAssistantRepository(engine),
                morning_brew=PostgresMorningBrewRepository(engine, capacity=advising),
                ferpa=PostgresFerpaRepository(engine, portal),
                edward_feedback=PostgresEdwardFeedbackRepository(engine),
                advising=advising,
            ),
            storage,
            ai,
            PostgresSignedDocumentGenerator(settings.onboarding_template_dir),
            settings.document_worker_token,
            ferpa_link_secret=settings.ferpa_delegate_link_secret,
        )
        auth_service = PostgresDevelopmentAuth(
            engine,
            environment=settings.environment,
            staff_invitation_code=settings.staff_invitation_code,
            development_flows_enabled=settings.auth_mode == "demo",
        )
        oidc_auth_service: OidcAuthService = (
            PostgresOidcAuth(engine, http_client, settings.oidc)
            if settings.oidc is not None
            else UnavailableOidcAuthService()
        )
        staff_email_service = PostgresStaffEmailService(
            engine,
            http_client,
            settings.institutional_oauth,
            auth_service,
        )
        voice_service: VoiceSessionServiceProtocol = (
            VoiceSessionService(
                settings.voice,
                PostgresVoiceSessionRepository(engine),
                service,
            )
            if settings.voice is not None
            else UnavailableVoiceSessionService()
        )
        return ApiRuntimeResources(
            engine=engine,
            http_client=http_client,
            object_storage=storage,
            service=service,
            auth_service=auth_service,
            oidc_auth_service=oidc_auth_service,
            staff_email_service=staff_email_service,
            voice_service=voice_service,
        )
    except BaseException:
        try:
            await http_client.aclose()
        finally:
            try:
                if storage is not None:
                    await storage.close()
            finally:
                await engine.dispose()
        raise


def create_production_app(settings: RuntimeSettings | None = None) -> FastAPI:
    """Build the FastAPI adapter while leaving resource construction to lifespan startup."""

    runtime_settings = settings or RuntimeSettings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        runtime_settings.assert_api_deployable()
        resources = await build_api_runtime(runtime_settings)
        app.state.runtime_settings = runtime_settings
        app.state.runtime_resources = resources
        app.state.platform_service = resources.service
        app.state.browser_auth_service = resources.auth_service
        app.state.oidc_auth_service = resources.oidc_auth_service
        app.state.staff_email_service = resources.staff_email_service
        app.state.voice_session_service = resources.voice_service
        try:
            yield
        finally:
            app.state.platform_service = UnavailablePlatformService()
            app.state.browser_auth_service = UnavailableBrowserAuthService()
            app.state.oidc_auth_service = UnavailableOidcAuthService()
            app.state.staff_email_service = None
            app.state.voice_session_service = UnavailableVoiceSessionService()
            await resources.close()

    app = create_app(
        service=UnavailablePlatformService(),
        oidc_auth_service=UnavailableOidcAuthService(),
        settings=runtime_settings.http_settings(),
        lifespan=lifespan,
    )
    app.state.runtime_settings = runtime_settings
    return app
