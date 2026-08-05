from pathlib import Path

import pytest

from audentra.bootstrap.settings import (
    LOCAL_DATABASE_URL,
    LOCAL_WORKER_TOKEN,
    RuntimeSettings,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_development_settings_preserve_legacy_defaults(tmp_path: Path) -> None:
    settings = RuntimeSettings.from_environment({}, package_root=tmp_path)

    assert settings.port == 4000
    assert settings.database_url == LOCAL_DATABASE_URL
    assert settings.document_worker_token == LOCAL_WORKER_TOKEN
    assert len(settings.staff_invitation_code) >= 16
    assert settings.object_storage.endpoint_url == "http://localhost:9000"
    assert settings.object_storage.force_path_style is True
    assert settings.worker.consumer_name == "student-dashboard-v1"
    assert settings.worker.worker_id
    assert settings.ai.openrouter_model == "openai/gpt-4o-mini"
    assert settings.ai.openrouter_document_model == "qwen/qwen3.7-flash"
    assert settings.onboarding_template_dir == tmp_path / "assets" / "onboarding"
    assert settings.http_settings().tenant_slug_ids["aster"].endswith("0001")


def test_document_model_is_wired_through_compose_and_preview_bootstrap() -> None:
    compose_setting = "OPENROUTER_DOCUMENT_MODEL: ${OPENROUTER_DOCUMENT_MODEL:-qwen/qwen3.7-flash}"
    for relative_path in ("infra/compose.yaml", "infra/preview-vm/compose.yaml"):
        manifest = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        assert compose_setting in manifest

    bootstrap = (REPOSITORY_ROOT / "infra/preview-vm/bootstrap-host.sh").read_text(encoding="utf-8")
    assert (
        "printf 'OPENROUTER_DOCUMENT_MODEL=%s\\n' "
        '"$(from_existing_or_legacy OPENROUTER_DOCUMENT_MODEL qwen/qwen3.7-flash)"' in bootstrap
    )
    for relative_path in (".env.example", "apps/api/.env.example", "infra/.env.example"):
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
            "OBJECT_STORAGE_SECRET_KEY": "preview-storage-secret",
            "VV_STAFF_INVITATION_CODE": "preview-private-staff-code",
        },
        package_root=tmp_path,
    )

    settings.assert_api_deployable()

    assert settings.environment == "preview"
    assert settings.http_settings().secure_cookies is True


def test_preview_requires_a_private_staff_invitation_code(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="VV_STAFF_INVITATION_CODE"):
        RuntimeSettings.from_environment(
            {
                "AUDENTRA_ENV": "preview",
                "DATABASE_URL": "postgresql://example/preview",
                "DOCUMENT_WORKER_TOKEN": "preview-worker-token",
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
            "OBJECT_STORAGE_SECRET_KEY": "external-secret",
            "VV_STAFF_INVITATION_CODE": "production-private-staff-code",
        },
        package_root=tmp_path,
    )
    with pytest.raises(ValueError, match="identity adapter"):
        settings.assert_api_deployable()


def test_hostile_edward_browser_fixture_is_prohibited_in_production(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prohibited in production"):
        RuntimeSettings.from_environment(
            {
                "AUDENTRA_ENV": "production",
                "DATABASE_URL": "postgresql://example/prod",
                "DOCUMENT_WORKER_TOKEN": "x" * 40,
                "OBJECT_STORAGE_SECRET_KEY": "external-secret",
                "VV_STAFF_INVITATION_CODE": "production-private-staff-code",
                "EDWARD_E2E_MALICIOUS_PROVIDER_ENABLED": "true",
            },
            package_root=tmp_path,
        )
