"""Prevent a seemingly healthy local runtime from silently disabling Edward."""

import importlib.util
from pathlib import Path

import pytest

from audentra.bootstrap.settings import RuntimeSettings


def _settings(*, enable_openai: bool = True, key: str = "") -> RuntimeSettings:
    path = Path(__file__).resolve().parents[3] / "tools/university/run_runtime.py"
    spec = importlib.util.spec_from_file_location("university_runtime_launcher", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = {} if enable_openai else {"enable_openai": False}
    settings: RuntimeSettings = module.runtime_settings(
        "postgresql://localhost/audentra_university_test",
        environ={"OPENAI_API_KEY": key},
        **options,
    )
    return settings


def test_default_runtime_enables_the_configured_openai_provider() -> None:
    settings = _settings(key="unit-test-placeholder")
    assert settings.ai.openai_api_key == "unit-test-placeholder"
    assert settings.ai.openai_model == "gpt-5.6-luna"
    assert not settings.ai.openrouter_api_key


def test_default_runtime_fails_early_when_the_provider_key_is_missing() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        _settings()


def test_offline_runtime_requires_explicit_opt_out_and_removes_the_key() -> None:
    settings = _settings(enable_openai=False, key="unit-test-placeholder")
    assert not settings.ai.openai_api_key
    assert not settings.ai.openrouter_api_key
