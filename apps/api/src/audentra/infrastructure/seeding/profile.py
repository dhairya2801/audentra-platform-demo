"""Which demo population a seed run converges.

Two populations serve two different jobs and neither substitutes for the
other:

``compact``
    The fourteen-student funnel plus the two primary demo identities. Small,
    hand-authored, and fast enough that every integration test can reseed it.
    This is the default, so nothing that exists today changes behaviour.

``synthetic_university``
    ``compact`` **plus** the three-thousand-student synthetic university,
    imported into its own demo tenant. This is the population to run the
    product against; it is far too large to reseed inside a unit test.

The larger profile is additive on purpose. Loading it never removes the
compact fixture, so a developer can flip the profile on, demo against a real
cohort, and still run the fast suite against the same database.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, get_args

SeedProfile = Literal["compact", "synthetic_university"]

SEED_PROFILES: tuple[SeedProfile, ...] = get_args(SeedProfile)
DEFAULT_SEED_PROFILE: SeedProfile = "compact"
SEED_PROFILE_VARIABLE = "DEMO_SEED_PROFILE"


class SeedProfileError(ValueError):
    """The requested demo seed profile is not one this build knows about."""


def parse_seed_profile(raw: str | None) -> SeedProfile:
    """Normalize a profile name, or raise with the accepted spellings."""

    if raw is None:
        return DEFAULT_SEED_PROFILE
    normalized = raw.strip().lower().replace("-", "_")
    if not normalized:
        return DEFAULT_SEED_PROFILE
    if normalized not in SEED_PROFILES:
        accepted = ", ".join(SEED_PROFILES)
        raise SeedProfileError(
            f"{SEED_PROFILE_VARIABLE} must be one of {accepted}; received {raw!r}"
        )
    return normalized


def seed_profile(values: Mapping[str, str]) -> SeedProfile:
    """Read the profile from an environment mapping."""

    return parse_seed_profile(values.get(SEED_PROFILE_VARIABLE))
