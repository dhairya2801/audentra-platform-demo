from pathlib import Path

import yaml  # type: ignore[import-untyped]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PREVIEW_ROOT = REPOSITORY_ROOT / "infra" / "preview-vm"


def test_preview_vm_compose_has_only_the_bounded_runtime_services() -> None:
    manifest = yaml.safe_load((PREVIEW_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = manifest["services"]

    assert set(services) == {
        "postgres",
        "minio",
        "minio-init",
        "migrate",
        "seed",
        "api",
        "worker",
        "caddy",
    }
    for private_service in ("postgres", "minio", "api", "worker"):
        assert "ports" not in services[private_service]
    assert services["api"]["expose"] == ["4000"]
    assert services["caddy"]["ports"] == ["80:80", "443:443", "443:443/udp"]


def test_preview_vm_enforces_auth_and_preview_only_seeding() -> None:
    manifest = yaml.safe_load((PREVIEW_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    runtime = manifest["x-runtime-environment"]
    seed_environment = manifest["services"]["seed"]["environment"]

    assert runtime["AUDENTRA_ENV"] == "preview"
    assert runtime["AUTH_MODE"] == "demo"
    assert runtime["BROWSER_AUTH_REQUIRED"] == "true"
    assert seed_environment["AUDENTRA_ENV"] == "preview"
    assert seed_environment["AUTH_MODE"] == "demo"


def test_preview_vm_edge_proxies_only_to_the_private_api_and_flushes_sse() -> None:
    caddyfile = (PREVIEW_ROOT / "Caddyfile").read_text(encoding="utf-8")

    assert "{$API_DOMAIN}" in caddyfile
    assert "reverse_proxy api:4000" in caddyfile
    assert "flush_interval -1" in caddyfile
    assert "minio:" not in caddyfile
    assert "postgres:" not in caddyfile


def test_preview_vm_example_contains_no_populated_provider_secrets() -> None:
    environment = (PREVIEW_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "AUDENTRA_ENV=preview" in environment
    assert "AUTH_MODE=demo" in environment
    assert "BROWSER_AUTH_REQUIRED=true" in environment
    assert "OPENAI_API_KEY=\n" in environment
    assert "OPENROUTER_API_KEY=\n" in environment
    assert "GROQ_API_KEY=\n" in environment
    assert "CHANGE_ME" in environment


def test_preview_deploy_ignores_ambient_shell_configuration() -> None:
    deploy = (PREVIEW_ROOT / "deploy-platform.sh").read_text(encoding="utf-8")

    assert "--ignore-environment" in deploy
    assert '--env-file "$environment_file"' in deploy
    assert '--env-file "$next_deployment_file"' in deploy


def test_preview_deploy_orders_fresh_state_before_long_running_services() -> None:
    deploy = (PREVIEW_ROOT / "deploy-platform.sh").read_text(encoding="utf-8")

    assert deploy.index('run --rm migrate') < deploy.index('run --rm minio-init')
    assert deploy.index('run --rm minio-init') < deploy.index('run --rm seed')
    assert deploy.index('run --rm seed') < deploy.index('up -d --no-build api worker caddy')


def test_legacy_cloud_run_deployment_is_not_triggered_by_main_pushes() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job_gate = workflow.split("  deploy-preview:", maxsplit=1)[1].split("    needs:", maxsplit=1)[0]

    assert "github.event_name == 'workflow_dispatch'" in job_gate
    assert "vars.ENABLE_LEGACY_CLOUD_RUN_DEPLOYMENT == 'true'" in job_gate
    assert "github.event_name == 'push'" not in job_gate
