from pathlib import Path

import pytest

from audentra.bootstrap.settings import (
    LOCAL_DATABASE_URL,
    LOCAL_WORKER_TOKEN,
    RuntimeSettings,
)
from audentra.infrastructure.storage import GcsStorageSettings, S3StorageSettings

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FERPA_LINK_SECRET = "test-ferpa-delegate-link-secret-at-least-32-bytes"  # noqa: S105


def test_development_settings_preserve_legacy_defaults(tmp_path: Path) -> None:
    settings = RuntimeSettings.from_environment({}, package_root=tmp_path)

    assert settings.port == 4000
    assert settings.database_url == LOCAL_DATABASE_URL
    assert settings.document_worker_token == LOCAL_WORKER_TOKEN
    assert len(settings.staff_invitation_code) >= 16
    assert isinstance(settings.object_storage, S3StorageSettings)
    assert settings.object_storage.endpoint_url == "http://localhost:9000"
    assert settings.object_storage.force_path_style is True
    assert settings.worker.consumer_name == "student-dashboard-v1"
    assert settings.worker.scheduled_interval_seconds == 300
    assert settings.worker.worker_id
    assert settings.ai.openrouter_model == "openai/gpt-4o-mini"
    assert settings.ai.openrouter_document_model == "qwen/qwen3.7-flash"
    assert settings.onboarding_template_dir == tmp_path / "assets" / "onboarding"
    assert not hasattr(settings.http_settings(), "tenant_slug_ids")


def test_voice_settings_are_absent_until_livekit_is_fully_configured(tmp_path: Path) -> None:
    assert RuntimeSettings.from_environment({}, package_root=tmp_path).voice is None

    with pytest.raises(ValueError, match="must all be set"):
        RuntimeSettings.from_environment(
            {"LIVEKIT_URL": "wss://example.livekit.cloud"}, package_root=tmp_path
        )

    complete = {
        "LIVEKIT_URL": "wss://example.livekit.cloud",
        "LIVEKIT_API_KEY": "lk_key",
        "LIVEKIT_API_SECRET": "lk_secret",
        "VOICE_AGENT_INTERNAL_TOKEN": "internal-test-token-123456",
    }
    settings = RuntimeSettings.from_environment(complete, package_root=tmp_path)
    assert settings.voice is not None
    assert settings.voice.livekit_url == "wss://example.livekit.cloud"
    assert settings.voice.agent_name == "student-assistant-voice"
    assert settings.voice.session_ttl_seconds == 900
    assert settings.voice.token_ttl_seconds == 600
    assert settings.http_settings().voice_agent_internal_token == (
        "internal-test-token-123456"  # noqa: S105
    )

    with pytest.raises(ValueError, match="between 24 and 512"):
        RuntimeSettings.from_environment(
            {**complete, "VOICE_AGENT_INTERNAL_TOKEN": "short"}, package_root=tmp_path
        )

    with pytest.raises(ValueError, match="wss://"):
        RuntimeSettings.from_environment(
            {
                **complete,
                "AUDENTRA_ENV": "preview",
                "BROWSER_AUTH_REQUIRED": "true",
                "FERPA_DELEGATE_LINK_SECRET": FERPA_LINK_SECRET,
                "VV_STAFF_INVITATION_CODE": "a-long-enough-invitation-code",
                "LIVEKIT_URL": "ws://livekit.internal:7880",
            },
            package_root=tmp_path,
        )


def test_document_model_is_wired_through_compose_and_environment_examples() -> None:
    compose_setting = "OPENROUTER_DOCUMENT_MODEL: ${OPENROUTER_DOCUMENT_MODEL:-qwen/qwen3.7-flash}"
    for relative_path in ("infra/compose.yaml", "infra/preview-vm/compose.yaml"):
        manifest = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert compose_setting in manifest

    for relative_path in (
        ".env.example",
        "apps/api/.env.example",
        "infra/.env.example",
        "infra/preview-vm/.env.example",
    ):
        environment = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert "OPENROUTER_DOCUMENT_MODEL=qwen/qwen3.7-flash" in environment


def test_legacy_environment_names_and_bounds_are_supported(tmp_path: Path) -> None:
    settings = RuntimeSettings.from_environment(
        {
            "NODE_ENV": "test",
            "PORT": "4100",
            "DATABASE_URL": "postgresql://example/test",
            "WEB_ORIGIN": "https://student.example,https://staff.example/",
            "DOCUMENT_WORKER_TOKEN": "test-worker-token-that-is-not-a-production-secret",
            "TRANSCRIPT_PARSING": "groq",
            "OPENROUTER_MODEL": "test/chat-model",
            "OPENROUTER_DOCUMENT_MODEL": "test/document-model",
            "GROQ_TRANSCRIPT_TIMEOUT_MS": "90000",
            "DB_POOL_SIZE": "7",
            "WORKER_BATCH_SIZE": "25",
            "ONBOARDING_DOCUMENT_TEMPLATE_DIR": str(tmp_path / "templates"),
        },
        package_root=tmp_path,
    )

    assert settings.environment == "test"
    assert settings.port == 4100
    assert settings.web_origins == ("https://student.example", "https://staff.example")
    assert settings.ai.transcript_provider == "groq"
    assert settings.ai.openrouter_model == "test/chat-model"
    assert settings.ai.openrouter_document_model == "test/document-model"
    assert settings.ai.groq_timeout_seconds == 90
    assert settings.database.pool_size == 7
    assert settings.worker.batch_size == 25
    assert settings.onboarding_template_dir == tmp_path / "templates"


def test_preview_keeps_demo_auth_but_uses_secure_browser_cookies(tmp_path: Path) -> None:
    settings = RuntimeSettings.from_environment(
        {
            "AUDENTRA_ENV": "preview",
            "DATABASE_URL": "postgresql://example/preview",
            "DOCUMENT_WORKER_TOKEN": "preview-worker-token",
            "FERPA_DELEGATE_LINK_SECRET": FERPA_LINK_SECRET,
            "OBJECT_STORAGE_SECRET_KEY": "preview-storage-secret",
            "VV_STAFF_INVITATION_CODE": "preview-private-staff-code",
        },
        package_root=tmp_path,
    )

    settings.assert_api_deployable()

    assert settings.environment == "preview"
    assert settings.http_settings().secure_cookies is True
    assert settings.http_settings().session_cookie_samesite == "none"


def test_gcs_storage_uses_application_default_credentials(tmp_path: Path) -> None:
    settings = RuntimeSettings.from_environment(
        {
            "AUDENTRA_ENV": "preview",
            "DATABASE_URL": "postgresql://example/preview",
            "DOCUMENT_WORKER_TOKEN": "preview-worker-token",
            "FERPA_DELEGATE_LINK_SECRET": FERPA_LINK_SECRET,
            "VV_STAFF_INVITATION_CODE": "preview-private-staff-code",
            "OBJECT_STORAGE_PROVIDER": "gcs",
            "OBJECT_STORAGE_BUCKET": "audentra-preview-documents",
            "GOOGLE_CLOUD_PROJECT": "audentra",
            "API_INTERNAL_AUDIENCE": "https://audentra-api-preview.run.app",
        },
        package_root=tmp_path,
    )

    assert isinstance(settings.object_storage, GcsStorageSettings)
    assert settings.object_storage.bucket == "audentra-preview-documents"
    assert settings.object_storage.project_id == "audentra"
    assert settings.worker.api_internal_audience == "https://audentra-api-preview.run.app"


def test_preview_requires_a_private_staff_invitation_code(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="VV_STAFF_INVITATION_CODE"):
        RuntimeSettings.from_environment(
            {
                "AUDENTRA_ENV": "preview",
                "DATABASE_URL": "postgresql://example/preview",
                "DOCUMENT_WORKER_TOKEN": "preview-worker-token",
                "FERPA_DELEGATE_LINK_SECRET": FERPA_LINK_SECRET,
                "OBJECT_STORAGE_SECRET_KEY": "preview-storage-secret",
            },
            package_root=tmp_path,
        )


def test_staff_invitation_code_supports_the_legacy_secret_name(tmp_path: Path) -> None:
    settings = RuntimeSettings.from_environment(
        {"VV_STAFF_BOOTSTRAP_PASSWORD": "legacy-private-staff-access-code"},
        package_root=tmp_path,
    )

    assert settings.staff_invitation_code == "legacy-private-staff-access-code"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"AUDENTRA_ENV": "staging"}, "AUDENTRA_ENV"),
        ({"API_PORT": "0"}, "API_PORT"),
        ({"WEB_ORIGIN": "javascript:alert(1)"}, "WEB_ORIGIN"),
        ({"SESSION_COOKIE_SAMESITE": "cross-site"}, "SESSION_COOKIE_SAMESITE"),
        ({"SESSION_COOKIE_SAMESITE": "none"}, "only in preview"),
        ({"OBJECT_STORAGE_FORCE_PATH_STYLE": "sometimes"}, "Boolean"),
        ({"DB_POOL_SIZE": "many"}, "DB_POOL_SIZE"),
        (
            {
                "WORKER_LEASE_SECONDS": "30",
                "WORKER_COMMAND_TIMEOUT_SECONDS": "30",
            },
            "WORKER_LEASE_SECONDS",
        ),
    ],
)
def test_invalid_settings_fail_fast(values: dict[str, str], message: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=message):
        RuntimeSettings.from_environment(values, package_root=tmp_path)


def test_production_requires_external_secrets_and_rejects_demo_auth(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        RuntimeSettings.from_environment({"AUDENTRA_ENV": "production"}, package_root=tmp_path)

    settings = RuntimeSettings.from_environment(
        {
            "AUDENTRA_ENV": "production",
            "DATABASE_URL": "postgresql://example/prod",
            "DOCUMENT_WORKER_TOKEN": "x" * 40,
            "FERPA_DELEGATE_LINK_SECRET": FERPA_LINK_SECRET,
            "OBJECT_STORAGE_SECRET_KEY": "external-secret",
            "VV_STAFF_INVITATION_CODE": "production-private-staff-code",
        },
        package_root=tmp_path,
    )
    assert settings.http_settings().session_cookie_samesite == "lax"
    with pytest.raises(ValueError, match="identity adapter"):
        settings.assert_api_deployable()

    with pytest.raises(ValueError, match="only in preview"):
        RuntimeSettings.from_environment(
            {
                "AUDENTRA_ENV": "production",
                "DATABASE_URL": "postgresql://example/prod",
                "DOCUMENT_WORKER_TOKEN": "x" * 40,
                "FERPA_DELEGATE_LINK_SECRET": FERPA_LINK_SECRET,
                "OBJECT_STORAGE_SECRET_KEY": "external-secret",
                "VV_STAFF_INVITATION_CODE": "production-private-staff-code",
                "SESSION_COOKIE_SAMESITE": "none",
            },
            package_root=tmp_path,
        )


def test_hostile_edward_browser_fixture_is_prohibited_in_production(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prohibited in production"):
        RuntimeSettings.from_environment(
            {
                "AUDENTRA_ENV": "production",
                "DATABASE_URL": "postgresql://example/prod",
                "DOCUMENT_WORKER_TOKEN": "x" * 40,
                "FERPA_DELEGATE_LINK_SECRET": FERPA_LINK_SECRET,
                "OBJECT_STORAGE_SECRET_KEY": "external-secret",
                "VV_STAFF_INVITATION_CODE": "production-private-staff-code",
                "EDWARD_E2E_MALICIOUS_PROVIDER_ENABLED": "true",
            },
            package_root=tmp_path,
        )
