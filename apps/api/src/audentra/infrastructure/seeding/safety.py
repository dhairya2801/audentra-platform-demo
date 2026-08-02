"""Fail-closed environment checks shared by every seed entry point."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

SeedEnvironment = Literal["development", "test"]


class SeedEnvironmentError(RuntimeError):
    """Seeding was requested outside an explicitly safe environment."""


def assert_seed_environment(environment: str) -> SeedEnvironment:
    normalized = environment.strip().lower()
    if normalized == "production":
        raise SeedEnvironmentError("Demo seeding is disabled in production")
    if normalized not in {"development", "test"}:
        raise SeedEnvironmentError(
            "AUDENTRA_ENV must explicitly be development or test before seeding"
        )
    return normalized  # type: ignore[return-value]


def seed_environment(values: Mapping[str, str]) -> SeedEnvironment:
    raw = values.get("AUDENTRA_ENV", values.get("NODE_ENV", ""))
    return assert_seed_environment(raw)
