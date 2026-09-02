"""Validated process settings shared by the API, worker, and future Python adapters."""

from __future__ import annotations

import os
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from audentra.core.demo_personas import DemoPersonaAllowlist
from audentra.infrastructure.db.engine import DatabaseEngineOptions
from audentra.infrastructure.postgres.oidc_repository import (
    OidcProviderConfig,
    OidcSettings,
)
from audentra.infrastructure.storage import GcsStorageSettings, S3StorageSettings, StorageSettings
from audentra.infrastructure.voice.config import VoiceSettings
from audentra.integrations.ai.gateway import GatewaySettings
from audentra.interfaces.http.config import (
    CookieSameSite,
    HttpSettings,
    parse_session_cookie_samesite,
)

Environment = Literal["development", "preview", "test", "production"]
AuthMode = Literal["demo", "oidc"]
ObjectStorageProvider = Literal["s3", "gcs"]

LOCAL_DATABASE_URL = "postgresql://vv:vv_local_password@localhost:5432/vv_enrollment"
LOCAL_WORKER_TOKEN = "local-development-document-worker-token"  # noqa: S105
LOCAL_STORAGE_SECRET = "vv_minio_password"  # noqa: S105
LOCAL_STAFF_INVITATION_CODE = "local-staff-invitation-2027"
LOCAL_FERPA_DELEGATE_LINK_SECRET = "local-development-ferpa-delegate-link-secret-v1"  # noqa: S105


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    worker_id: str
    consumer_name: str
    api_internal_url: str
    api_internal_audience: str | None
    command_timeout_seconds: float
    poll_interval_seconds: float
    scheduled_interval_seconds: float
    batch_size: int
    lease_seconds: int
    max_attempts: int
    base_retry_ms: int
    max_retry_ms: int


@dataclass(frozen=True, slots=True)
class InstitutionalOAuthSettings:
    api_public_url: str
    portal_origin: str
    google_client_id: str
    google_client_secret: str
    microsoft_client_id: str
    microsoft_client_secret: str
    microsoft_allow_personal_accounts: bool
    token_encryption_key: str

    def provider_configured(self, provider: str) -> bool:
        if provider == "google":
            return bool(self.google_client_id and self.google_client_secret)
        if provider == "microsoft":
            return bool(self.microsoft_client_id and self.microsoft_client_secret)
        return False


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """One immutable settings snapshot; no business code reads the environment directly."""

    environment: Environment
    auth_mode: AuthMode
    host: str
    port: int
    database_url: str
    database: DatabaseEngineOptions
    web_origins: tuple[str, ...]
    browser_auth_required: bool
    session_cookie_samesite: CookieSameSite
    staff_invitation_code: str
    document_worker_token: str
    demo_tenant_id: str
    demo_student_id: str
    demo_actor_id: str
    demo_staff_actor_id: str
    demo_personas: DemoPersonaAllowlist
    oidc_tenant_id: str | None
    object_storage: StorageSettings
    ai: GatewaySettings
    worker: WorkerSettings
    institutional_oauth: InstitutionalOAuthSettings
    voice: VoiceSettings | None
    onboarding_template_dir: Path
    assistant_trace_debug_enabled: bool = False
    ferpa_delegate_link_secret: str = LOCAL_FERPA_DELEGATE_LINK_SECRET
    oidc: OidcSettings | None = None

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        package_root: Path | None = None,
    ) -> RuntimeSettings:
        values = os.environ if environment is None else environment
        app_environment = _environment(
            values.get("AUDENTRA_ENV", values.get("NODE_ENV", "development"))
        )
        auth_mode = values.get("AUTH_MODE", "demo").strip().lower()
        if auth_mode not in {"demo", "oidc"}:
            raise ValueError(f"Unsupported AUTH_MODE: {auth_mode}")

        database_url = values.get("DATABASE_URL", "").strip()
        if not database_url:
            if app_environment == "production":
                raise ValueError("DATABASE_URL is required in production")
            database_url = LOCAL_DATABASE_URL

        worker_token = values.get("DOCUMENT_WORKER_TOKEN", "").strip()
        if not worker_token:
            if app_environment == "production":
                raise ValueError("DOCUMENT_WORKER_TOKEN is required in production")
            worker_token = LOCAL_WORKER_TOKEN
        if app_environment == "production" and len(worker_token) < 32:
            raise ValueError("DOCUMENT_WORKER_TOKEN must contain at least 32 characters")

        ferpa_link_secret = values.get("FERPA_DELEGATE_LINK_SECRET", "").strip()
        if not ferpa_link_secret:
            if app_environment in {"preview", "production"}:
                raise ValueError("FERPA_DELEGATE_LINK_SECRET is required in deployed environments")
            ferpa_link_secret = LOCAL_FERPA_DELEGATE_LINK_SECRET
        if len(ferpa_link_secret) < 32:
            raise ValueError("FERPA_DELEGATE_LINK_SECRET must contain at least 32 characters")

        deployed_environment = app_environment in {"preview", "production"}
        staff_invitation_code = (
            values.get("VV_STAFF_INVITATION_CODE", "").strip()
            or values.get("VV_STAFF_BOOTSTRAP_PASSWORD", "").strip()
            or (LOCAL_STAFF_INVITATION_CODE if not deployed_environment else "")
        )
        if auth_mode == "demo" and len(staff_invitation_code) < 16:
            raise ValueError("VV_STAFF_INVITATION_CODE must contain at least 16 characters")

        origins = _origins(values.get("WEB_ORIGIN", "http://localhost:3000"))
        session_cookie_samesite = parse_session_cookie_samesite(
            values.get("SESSION_COOKIE_SAMESITE"),
            environment=app_environment,
        )
        object_storage_provider = _object_storage_provider(values.get("OBJECT_STORAGE_PROVIDER"))
        storage_secret = values.get("OBJECT_STORAGE_SECRET_KEY", "").strip()
        if object_storage_provider == "s3" and not storage_secret:
            if app_environment == "production":
                raise ValueError("OBJECT_STORAGE_SECRET_KEY is required in production")
            storage_secret = LOCAL_STORAGE_SECRET

        source_root = package_root or Path(__file__).resolve().parents[3]
        template_dir = Path(
            values.get(
                "ONBOARDING_DOCUMENT_TEMPLATE_DIR",
                str(source_root / "assets" / "onboarding"),
            )
        ).expanduser()

        port = _bounded_int(values, ("API_PORT", "PORT"), 4000, 1, 65_535)
        openrouter_timeout_ms = _bounded_int(
            values, ("OPENROUTER_DOCUMENT_TIMEOUT_MS",), 120_000, 1_000, 300_000
        )
        groq_timeout_ms = _bounded_int(
            values, ("GROQ_TRANSCRIPT_TIMEOUT_MS",), 60_000, 1_000, 300_000
        )
        e2e_malicious_provider_enabled = _boolean(
            values.get("EDWARD_E2E_MALICIOUS_PROVIDER_ENABLED"), False
        )
        if app_environment == "production" and e2e_malicious_provider_enabled:
            raise ValueError("EDWARD_E2E_MALICIOUS_PROVIDER_ENABLED is prohibited in production")
        microsoft_allow_personal_accounts = _boolean(
            values.get("MICROSOFT_OAUTH_ALLOW_PERSONAL_ACCOUNTS"), False
        )
        if microsoft_allow_personal_accounts and app_environment not in {"development", "test"}:
            raise ValueError(
                "MICROSOFT_OAUTH_ALLOW_PERSONAL_ACCOUNTS is allowed only in development or test"
            )
        assistant_trace_debug_enabled = _boolean(
            values.get("ASSISTANT_TRACE_DEBUG_ENABLED"),
            app_environment in {"development", "test"},
        )
        if app_environment == "production" and assistant_trace_debug_enabled:
            raise ValueError("ASSISTANT_TRACE_DEBUG_ENABLED is prohibited in production")
        api_internal_url = values.get("API_INTERNAL_URL", f"http://localhost:{port}").strip()
        _http_url(api_internal_url, "API_INTERNAL_URL")
        api_internal_audience = values.get("API_INTERNAL_AUDIENCE", "").strip() or None
        if api_internal_audience is not None:
            _http_url(api_internal_audience, "API_INTERNAL_AUDIENCE")
        worker_command_timeout = _bounded_float(
            values, "WORKER_COMMAND_TIMEOUT_SECONDS", 30.0, 1.0, 300.0
        )
        worker_lease_seconds = _bounded_int(values, ("WORKER_LEASE_SECONDS",), 60, 5, 3_600)
        if worker_lease_seconds <= worker_command_timeout:
            raise ValueError(
                "WORKER_LEASE_SECONDS must be greater than WORKER_COMMAND_TIMEOUT_SECONDS"
            )

        voice = _voice_settings(values, deployed_environment=deployed_environment)
        api_public_url = (
            values.get("API_PUBLIC_URL", f"http://localhost:{port}").strip().rstrip("/")
        )
        _http_url(api_public_url, "API_PUBLIC_URL")

        oidc = _oidc_settings(values, environment=app_environment, auth_mode=auth_mode)
        # Which demo people a browser may open. Development stays open; a
        # deployed demo must say who it shows, so the restriction is a
        # deliberate configuration rather than an accident of the fixture.
        demo_personas = DemoPersonaAllowlist.from_environment(values)
        if app_environment == "preview" and auth_mode == "demo" and not demo_personas.restricted:
            raise ValueError(
                "A deployed demo (AUDENTRA_ENV=preview, AUTH_MODE=demo) must name the people it "
                "exposes: set DEMO_STUDENT_ALLOWLIST and DEMO_STAFF_ALLOWLIST"
            )
        configured_oidc_tenant = (
            values.get("OIDC_AUDENTRA_TENANT_ID", "").strip() if auth_mode == "oidc" else ""
        )
        oidc_tenant_id: str | None = None
        if configured_oidc_tenant:
            try:
                oidc_tenant_id = str(UUID(configured_oidc_tenant))
            except ValueError as error:
                raise ValueError("OIDC_AUDENTRA_TENANT_ID must be a tenant UUID") from error
        if auth_mode == "oidc" and oidc_tenant_id is None:
            raise ValueError("AUTH_MODE=oidc requires OIDC_AUDENTRA_TENANT_ID")

        return cls(
            environment=app_environment,
            auth_mode=auth_mode,  # type: ignore[arg-type]
            host=values.get("API_HOST", "0.0.0.0").strip() or "0.0.0.0",  # noqa: S104
            port=port,
            database_url=database_url,
            database=DatabaseEngineOptions(
                pool_size=_bounded_int(values, ("DB_POOL_SIZE",), 10, 1, 100),
                max_overflow=_bounded_int(values, ("DB_MAX_OVERFLOW",), 0, 0, 100),
                pool_timeout_seconds=_bounded_float(
                    values, "DB_POOL_TIMEOUT_SECONDS", 5.0, 0.1, 60.0
                ),
                pool_recycle_seconds=_bounded_int(
                    values, ("DB_POOL_RECYCLE_SECONDS",), 1_800, 30, 86_400
                ),
                statement_timeout_ms=_bounded_int(
                    values, ("DB_STATEMENT_TIMEOUT_MS",), 15_000, 100, 300_000
                ),
                application_name=values.get("DB_APPLICATION_NAME", "audentra-api").strip()
                or "audentra-api",
                echo=_boolean(values.get("DB_ECHO"), False),
            ),
            web_origins=origins,
            browser_auth_required=_boolean(values.get("BROWSER_AUTH_REQUIRED"), False),
            session_cookie_samesite=session_cookie_samesite,
            staff_invitation_code=staff_invitation_code,
            document_worker_token=worker_token,
            ferpa_delegate_link_secret=ferpa_link_secret,
            demo_tenant_id=values.get("DEMO_TENANT_ID", "00000000-0000-7000-8000-000000000001"),
            demo_student_id=values.get("DEMO_STUDENT_ID", "00000000-0000-7000-8000-000000000101"),
            demo_actor_id=values.get("DEMO_ACTOR_ID", "00000000-0000-7000-8000-000000000100"),
            demo_staff_actor_id=values.get(
                "DEMO_STAFF_ACTOR_ID", "00000000-0000-7000-8000-000000000901"
            ),
            demo_personas=demo_personas,
            oidc_tenant_id=oidc_tenant_id,
            object_storage=_object_storage_settings(
                values,
                provider=object_storage_provider,
                secret=storage_secret,
            ),
            ai=GatewaySettings(
                openai_api_key=values.get("OPENAI_API_KEY", "").strip(),
                openai_model=values.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
                openrouter_api_key=values.get("OPENROUTER_API_KEY", "").strip(),
                openrouter_model=values.get("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
                or "openai/gpt-4o-mini",
                reasoning_effort=(
                    values.get("EDWARD_REASONING_EFFORT", "none").strip().lower() or "none"
                ),
                reasoning_effort_overrides=values.get(
                    "EDWARD_REASONING_EFFORT_OVERRIDES", ""
                ).strip(),
                read_planner=(
                    values.get("EDWARD_READ_PLANNER", "hybrid").strip().lower() or "hybrid"
                ),
                model_overrides=values.get("EDWARD_MODEL_OVERRIDES", "").strip(),
                read_loop_max_rounds=_bounded_int(
                    values, ("EDWARD_READ_LOOP_MAX_ROUNDS",), 3, 1, 6
                ),
                openrouter_document_model=values.get(
                    "OPENROUTER_DOCUMENT_MODEL", "qwen/qwen3.7-flash"
                ).strip()
                or "qwen/qwen3.7-flash",
                openrouter_transcription_model=values.get(
                    "OPENROUTER_TRANSCRIPTION_MODEL", "openai/whisper-large-v3"
                ).strip()
                or "openai/whisper-large-v3",
                app_url=values.get("OPENROUTER_APP_URL", "http://localhost:3000").strip(),
                app_name=values.get("OPENROUTER_APP_NAME", "Audentra Student Portal").strip(),
                document_timeout_seconds=openrouter_timeout_ms / 1_000,
                document_max_tokens=_bounded_int(
                    values, ("OPENROUTER_DOCUMENT_MAX_TOKENS",), 6_000, 1_200, 16_000
                ),
                document_reasoning_tokens=_bounded_int(
                    values,
                    ("OPENROUTER_DOCUMENT_REASONING_TOKENS",),
                    256,
                    0,
                    4_096,
                ),
                transcript_provider=(
                    "groq"
                    if values.get("TRANSCRIPT_PARSING", "").strip().lower() == "groq"
                    else "openrouter"
                ),
                groq_api_key=values.get("GROQ_API_KEY", "").strip(),
                groq_model=values.get("GROQ_MODEL", "qwen/qwen3.6-27b").strip()
                or "qwen/qwen3.6-27b",
                groq_timeout_seconds=groq_timeout_ms / 1_000,
                groq_max_tokens=_bounded_int(
                    values, ("GROQ_TRANSCRIPT_MAX_TOKENS",), 1_400, 600, 16_384
                ),
                groq_max_text_characters=_bounded_int(
                    values,
                    ("GROQ_TRANSCRIPT_MAX_TEXT_CHARACTERS",),
                    40_000,
                    2_000,
                    100_000,
                ),
                groq_reasoning_effort=_reasoning_effort(
                    values.get("GROQ_TRANSCRIPT_REASONING_EFFORT")
                ),
                e2e_malicious_provider_enabled=e2e_malicious_provider_enabled,
            ),
            worker=WorkerSettings(
                worker_id=(
                    values.get("WORKER_ID", "").strip() or f"{socket.gethostname()}-{os.getpid()}"
                )[:128],
                consumer_name=(
                    values.get("WORKER_CONSUMER_NAME", "student-dashboard-v1").strip()
                    or "student-dashboard-v1"
                )[:120],
                api_internal_url=api_internal_url,
                api_internal_audience=api_internal_audience,
                command_timeout_seconds=worker_command_timeout,
                poll_interval_seconds=_bounded_float(
                    values, "WORKER_POLL_INTERVAL_SECONDS", 1.0, 0.05, 60.0
                ),
                scheduled_interval_seconds=_bounded_float(
                    values, "AGENTIC_WORKFLOW_INTERVAL_SECONDS", 300.0, 60.0, 3_600.0
                ),
                batch_size=_bounded_int(values, ("WORKER_BATCH_SIZE",), 20, 1, 500),
                lease_seconds=worker_lease_seconds,
                max_attempts=_bounded_int(values, ("WORKER_MAX_ATTEMPTS",), 10, 1, 100),
                base_retry_ms=_bounded_int(
                    values, ("WORKER_BASE_RETRY_MS",), 1_000, 100, 3_600_000
                ),
                max_retry_ms=_bounded_int(
                    values, ("WORKER_MAX_RETRY_MS",), 300_000, 1_000, 86_400_000
                ),
            ),
            institutional_oauth=InstitutionalOAuthSettings(
                api_public_url=api_public_url,
                portal_origin=origins[0],
                google_client_id=values.get("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
                google_client_secret=values.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
                microsoft_client_id=values.get("MICROSOFT_OAUTH_CLIENT_ID", "").strip(),
                microsoft_client_secret=values.get("MICROSOFT_OAUTH_CLIENT_SECRET", "").strip(),
                microsoft_allow_personal_accounts=microsoft_allow_personal_accounts,
                token_encryption_key=values.get("MAIL_TOKEN_ENCRYPTION_KEY", "").strip(),
            ),
            voice=voice,
            onboarding_template_dir=template_dir,
            assistant_trace_debug_enabled=assistant_trace_debug_enabled,
            oidc=oidc,
        )

    def http_settings(self) -> HttpSettings:
        return HttpSettings(
            environment=self.environment,
            auth_mode=self.auth_mode,
            browser_auth_required=self.browser_auth_required,
            assistant_trace_debug_enabled=self.assistant_trace_debug_enabled,
            web_origins=self.web_origins,
            session_cookie_samesite=self.session_cookie_samesite,
            document_worker_token=self.document_worker_token,
            voice_agent_internal_token=(self.voice.agent_internal_token if self.voice else ""),
            demo_tenant_id=self.demo_tenant_id,
            demo_student_id=self.demo_student_id,
            demo_actor_id=self.demo_actor_id,
            demo_staff_actor_id=self.demo_staff_actor_id,
            demo_personas=self.demo_personas,
            oidc_tenant_id=self.oidc_tenant_id,
            oidc_portal_base_url=(self.oidc.portal_base_url if self.oidc else ""),
        )

    def assert_api_deployable(self) -> None:
        """Fail closed until a real production identity adapter is configured."""

        if self.environment == "production" and self.auth_mode == "demo":
            raise ValueError(
                "The demo identity adapter is disabled in production; configure a production "
                "identity adapter first"
            )

        if self.auth_mode == "oidc" and self.oidc is None:
            raise ValueError("AUTH_MODE=oidc requires a configured OIDC provider")


def _oidc_settings(
    values: Mapping[str, str],
    *,
    environment: Environment,
    auth_mode: str,
) -> OidcSettings | None:
    # Provider credentials can remain present while a developer deliberately
    # switches back to the local credential flow.  AUTH_MODE is the authority:
    # never compose or expose an OIDC adapter unless it explicitly selects OIDC.
    if auth_mode != "oidc":
        return None

    public_base = values.get("OIDC_PUBLIC_BASE_URL", "").strip().rstrip("/")
    legacy_callback_base = values.get("OIDC_CALLBACK_BASE_URL", "").strip().rstrip("/")
    if public_base and legacy_callback_base and public_base != legacy_callback_base:
        raise ValueError(
            "OIDC_PUBLIC_BASE_URL and OIDC_CALLBACK_BASE_URL must match when both are set"
        )
    callback_base = public_base or legacy_callback_base
    portal_base = values.get("OIDC_PORTAL_BASE_URL", "").strip().rstrip("/") or callback_base
    google_id = values.get("GOOGLE_OIDC_CLIENT_ID", "").strip()
    google_secret = values.get("GOOGLE_OIDC_CLIENT_SECRET", "").strip()
    microsoft_id = values.get("MICROSOFT_OIDC_CLIENT_ID", "").strip()
    microsoft_secret = values.get("MICROSOFT_OIDC_CLIENT_SECRET", "").strip()
    microsoft_tenant = values.get("MICROSOFT_OIDC_TENANT_ID", "").strip()

    if bool(google_id) != bool(google_secret):
        raise ValueError("GOOGLE_OIDC_CLIENT_ID and GOOGLE_OIDC_CLIENT_SECRET must both be set")
    microsoft_values = (microsoft_id, microsoft_secret, microsoft_tenant)
    if any(microsoft_values) and not all(microsoft_values):
        raise ValueError(
            "MICROSOFT_OIDC_CLIENT_ID, MICROSOFT_OIDC_CLIENT_SECRET, and "
            "MICROSOFT_OIDC_TENANT_ID must all be set"
        )
    if not callback_base:
        raise ValueError("OIDC_PUBLIC_BASE_URL is required when OIDC is configured")
    _oidc_origin(callback_base, "OIDC_PUBLIC_BASE_URL", environment)
    _oidc_origin(portal_base, "OIDC_PORTAL_BASE_URL", environment)

    providers: list[OidcProviderConfig] = []
    if google_id:
        providers.append(
            OidcProviderConfig(
                id="google",
                label="Google",
                client_id=google_id,
                client_secret=google_secret,
                issuer="https://accounts.google.com",
                authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
                token_endpoint="https://oauth2.googleapis.com/token",  # noqa: S106
                jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
            )
        )
    if microsoft_id:
        try:
            tenant_id = str(UUID(microsoft_tenant))
        except ValueError as error:
            raise ValueError("MICROSOFT_OIDC_TENANT_ID must be a tenant UUID") from error
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        providers.append(
            OidcProviderConfig(
                id="microsoft",
                label="Microsoft",
                client_id=microsoft_id,
                client_secret=microsoft_secret,
                issuer=f"{authority}/v2.0",
                authorization_endpoint=f"{authority}/oauth2/v2.0/authorize",
                token_endpoint=f"{authority}/oauth2/v2.0/token",
                jwks_uri=f"{authority}/discovery/v2.0/keys",
                tenant_id=tenant_id,
            )
        )
    if not providers:
        raise ValueError("AUTH_MODE=oidc requires at least one configured OIDC provider")
    return OidcSettings(
        callback_base_url=callback_base,
        portal_base_url=portal_base,
        providers=tuple(providers),
    )


def _oidc_origin(value: str, name: str, environment: Environment) -> None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{name} must be an exact HTTPS origin") from error

    local_http = parsed.scheme == "http" and hostname in {"localhost", "127.0.0.1"}
    canonical_host = f"[{hostname}]" if hostname is not None and ":" in hostname else hostname
    canonical_netloc = (
        f"{canonical_host}:{port}"
        if canonical_host is not None and port is not None
        else canonical_host
    )
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or not _oidc_hostname(hostname)
        or port == 0
        or canonical_netloc is None
        or parsed.netloc.casefold() != canonical_netloc.casefold()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not local_http)
        or (environment in {"preview", "production"} and parsed.scheme != "https")
    ):
        raise ValueError(f"{name} must be an exact HTTPS origin")


def _oidc_hostname(value: str) -> bool:
    """Accept an IP literal or an ASCII DNS name, never an ambiguous authority."""

    try:
        ip_address(value)
    except ValueError:
        if not value.isascii() or len(value) > 253:
            return False
        candidate = value[:-1] if value.endswith(".") else value
        labels = candidate.split(".")
        return bool(candidate) and all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in labels
        )
    return True


def _environment(value: str) -> Environment:
    normalized = value.strip().lower()
    if normalized not in {"development", "preview", "test", "production"}:
        raise ValueError("AUDENTRA_ENV must be development, preview, test, or production")
    return normalized  # type: ignore[return-value]


def _object_storage_provider(value: str | None) -> ObjectStorageProvider:
    normalized = (value or "s3").strip().lower()
    if normalized not in {"s3", "gcs"}:
        raise ValueError("OBJECT_STORAGE_PROVIDER must be s3 or gcs")
    return normalized  # type: ignore[return-value]


def _object_storage_settings(
    values: Mapping[str, str],
    *,
    provider: ObjectStorageProvider,
    secret: str,
) -> StorageSettings:
    max_object_bytes = _bounded_int(
        values,
        ("MAX_DOCUMENT_BYTES",),
        10_485_760,
        1_024,
        104_857_600,
    )
    if provider == "gcs":
        return GcsStorageSettings(
            bucket=values.get("OBJECT_STORAGE_BUCKET", "vv-documents").strip(),
            project_id=(
                values.get("GOOGLE_CLOUD_PROJECT", "").strip()
                or values.get("GCP_PROJECT_ID", "").strip()
                or None
            ),
            max_object_bytes=max_object_bytes,
        )
    return S3StorageSettings(
        endpoint_url=values.get("OBJECT_STORAGE_ENDPOINT", "http://localhost:9000").strip() or None,
        region=values.get("OBJECT_STORAGE_REGION", "us-east-1").strip() or "us-east-1",
        bucket=values.get("OBJECT_STORAGE_BUCKET", "vv-documents").strip(),
        access_key_id=values.get("OBJECT_STORAGE_ACCESS_KEY", "vv_minio").strip() or None,
        secret_access_key=secret,
        force_path_style=_boolean(values.get("OBJECT_STORAGE_FORCE_PATH_STYLE"), True),
        max_object_bytes=max_object_bytes,
    )


def _voice_settings(
    values: Mapping[str, str], *, deployed_environment: bool
) -> VoiceSettings | None:
    """Voice is opt-in: absent LiveKit configuration disables it cleanly.

    A partially configured deployment is a misconfiguration, not a downgrade,
    so any LiveKit value without the full set fails startup instead of leaving
    the voice routes half-armed.
    """

    url = values.get("LIVEKIT_URL", "").strip()
    api_key = values.get("LIVEKIT_API_KEY", "").strip()
    api_secret = values.get("LIVEKIT_API_SECRET", "").strip()
    internal_token = values.get("VOICE_AGENT_INTERNAL_TOKEN", "").strip()
    if not any((url, api_key, api_secret, internal_token)):
        return None
    if not all((url, api_key, api_secret)):
        raise ValueError(
            "LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET must all be set to enable voice"
        )
    parsed = urlsplit(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
        raise ValueError("LIVEKIT_URL must be a ws:// or wss:// URL")
    if deployed_environment and parsed.scheme != "wss":
        raise ValueError("LIVEKIT_URL must use wss:// outside local development")
    if not internal_token:
        raise ValueError("VOICE_AGENT_INTERNAL_TOKEN is required when LiveKit is configured")
    if not 24 <= len(internal_token) <= 512:
        raise ValueError("VOICE_AGENT_INTERNAL_TOKEN must contain between 24 and 512 characters")
    return VoiceSettings(
        livekit_url=url.rstrip("/"),
        livekit_api_key=api_key,
        livekit_api_secret=api_secret,
        agent_internal_token=internal_token,
        agent_name=(values.get("VOICE_AGENT_NAME", "").strip() or "student-assistant-voice"),
        session_ttl_seconds=_bounded_int(values, ("VOICE_SESSION_TTL_SECONDS",), 900, 60, 21_600),
        token_ttl_seconds=_bounded_int(values, ("VOICE_TOKEN_TTL_SECONDS",), 600, 60, 3_600),
    )


def _origins(value: str) -> tuple[str, ...]:
    origins = tuple(item.strip().rstrip("/") for item in value.split(",") if item.strip())
    if not origins:
        raise ValueError("WEB_ORIGIN must contain at least one origin")
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError(f"WEB_ORIGIN contains an invalid origin: {origin}")
    return origins


def _http_url(value: str, name: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} must be an HTTP(S) URL")


def _first(values: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    return next((values[name] for name in names if values.get(name, "").strip()), None)


def _bounded_int(
    values: Mapping[str, str],
    names: tuple[str, ...],
    fallback: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = _first(values, names)
    if raw is None:
        return fallback
    try:
        parsed = int(raw)
    except ValueError as error:
        raise ValueError(f"{names[0]} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{names[0]} must be between {minimum} and {maximum}")
    return parsed


def _bounded_float(
    values: Mapping[str, str],
    name: str,
    fallback: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = values.get(name, "").strip()
    if not raw:
        return fallback
    try:
        parsed = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _boolean(value: str | None, fallback: bool) -> bool:
    if value is None or not value.strip():
        return fallback
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("Boolean environment values must be true or false")


def _reasoning_effort(value: str | None) -> str:
    normalized = (value or "none").strip().lower()
    return normalized if normalized in {"none", "low", "medium", "high"} else "none"
