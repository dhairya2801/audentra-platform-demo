"""Validated process settings shared by the API, worker, and future Python adapters."""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from audentra.infrastructure.db.engine import DatabaseEngineOptions
from audentra.infrastructure.storage import GcsStorageSettings, S3StorageSettings, StorageSettings
from audentra.integrations.ai.gateway import GatewaySettings
from audentra.interfaces.http.config import HttpSettings, parse_tenant_slug_ids

Environment = Literal["development", "preview", "test", "production"]
AuthMode = Literal["demo"]
ObjectStorageProvider = Literal["s3", "gcs"]

LOCAL_DATABASE_URL = "postgresql://vv:vv_local_password@localhost:5432/vv_enrollment"
LOCAL_WORKER_TOKEN = "local-development-document-worker-token"  # noqa: S105
LOCAL_STORAGE_SECRET = "vv_minio_password"  # noqa: S105
LOCAL_STAFF_INVITATION_CODE = "local-staff-invitation-2027"


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    worker_id: str
    consumer_name: str
    api_internal_url: str
    api_internal_audience: str | None
    command_timeout_seconds: float
    poll_interval_seconds: float
    batch_size: int
    lease_seconds: int
    max_attempts: int
    base_retry_ms: int
    max_retry_ms: int


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
    staff_invitation_code: str
    document_worker_token: str
    demo_tenant_id: str
    demo_student_id: str
    demo_actor_id: str
    demo_staff_actor_id: str
    tenant_slug_map_json: str | None
    object_storage: StorageSettings
    ai: GatewaySettings
    worker: WorkerSettings
    onboarding_template_dir: Path

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
        if auth_mode != "demo":
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

        deployed_environment = app_environment in {"preview", "production"}
        staff_invitation_code = (
            values.get("VV_STAFF_INVITATION_CODE", "").strip()
            or values.get("VV_STAFF_BOOTSTRAP_PASSWORD", "").strip()
            or (LOCAL_STAFF_INVITATION_CODE if not deployed_environment else "")
        )
        if len(staff_invitation_code) < 16:
            raise ValueError("VV_STAFF_INVITATION_CODE must contain at least 16 characters")

        origins = _origins(values.get("WEB_ORIGIN", "http://localhost:3000"))
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

        return cls(
            environment=app_environment,
            auth_mode="demo",
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
            staff_invitation_code=staff_invitation_code,
            document_worker_token=worker_token,
            demo_tenant_id=values.get("DEMO_TENANT_ID", "00000000-0000-7000-8000-000000000001"),
            demo_student_id=values.get("DEMO_STUDENT_ID", "00000000-0000-7000-8000-000000000101"),
            demo_actor_id=values.get("DEMO_ACTOR_ID", "00000000-0000-7000-8000-000000000100"),
            demo_staff_actor_id=values.get(
                "DEMO_STAFF_ACTOR_ID", "00000000-0000-7000-8000-000000000901"
            ),
            tenant_slug_map_json=values.get("TENANT_SLUG_MAP"),
            object_storage=_object_storage_settings(
                values,
                provider=object_storage_provider,
                secret=storage_secret,
            ),
            ai=GatewaySettings(
                openrouter_api_key=values.get("OPENROUTER_API_KEY", "").strip(),
                openrouter_model=values.get("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
                or "openai/gpt-4o-mini",
                openrouter_document_model=values.get(
                    "OPENROUTER_DOCUMENT_MODEL", "qwen/qwen3.7-flash"
                ).strip()
                or "qwen/qwen3.7-flash",
                app_url=values.get("OPENROUTER_APP_URL", "http://localhost:3000").strip(),
                app_name=values.get("OPENROUTER_APP_NAME", "Aster Student Portal").strip(),
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
            onboarding_template_dir=template_dir,
        )

    def http_settings(self) -> HttpSettings:
        return HttpSettings(
            environment=self.environment,
            browser_auth_required=self.browser_auth_required,
            web_origins=self.web_origins,
            document_worker_token=self.document_worker_token,
            demo_tenant_id=self.demo_tenant_id,
            demo_student_id=self.demo_student_id,
            demo_actor_id=self.demo_actor_id,
            demo_staff_actor_id=self.demo_staff_actor_id,
            tenant_slug_ids=parse_tenant_slug_ids(self.tenant_slug_map_json),
        )

    def assert_api_deployable(self) -> None:
        """Fail closed until a real production identity adapter is configured."""

        if self.environment == "production" and self.auth_mode == "demo":
            raise ValueError(
                "The demo identity adapter is disabled in production; configure a production "
                "identity adapter first"
            )


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
