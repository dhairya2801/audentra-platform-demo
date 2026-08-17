"""The synthetic-university translation, checked without a database.

The database tests prove the population lands. These prove the translation is
the one we meant: that every value the generator can produce has a canonical
destination, that no branch invents a state, and that the packaged archive is
the one the constants describe.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from audentra.infrastructure.seeding.profile import (
    DEFAULT_SEED_PROFILE,
    SeedProfileError,
    parse_seed_profile,
    seed_profile,
)
from audentra.infrastructure.seeding.synthetic_university import (
    _AWARD_STATUSES,
    _DOCUMENT_CATEGORIES,
    _DOCUMENT_REQUIREMENT_STATES,
    _DOCUMENT_STATUSES,
    _FAFSA_DOCUMENT_STATUSES,
    _REQUIREMENT_CODES,
    _SAP_STATUSES,
    _VERIFICATION_DOCUMENT_STATUSES,
    SYNTHETIC_TENANT_ID,
    SyntheticUniverseError,
    _onboarding_state,
    _requirement_states,
    load_synthetic_universe,
)
from audentra.infrastructure.seeding.synthetic_university_asset import (
    ASSET_SHA256,
    UNIVERSE_COLLECTION_COUNTS,
    UNIVERSE_SEED,
    UNIVERSE_STUDENT_COUNT,
)
from audentra.interfaces.http.demo_identity import (
    issue_demo_student_cookie,
    read_demo_student_cookie,
)

_STUDENT = "ac2fa509-b4e3-402d-900b-ffb8440fc430"
_OTHER_TENANT = "00000000-0000-7000-8000-000000000002"


# ---------------------------------------------------------------------------
# The packaged archive
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def universe() -> dict[str, Any]:
    return load_synthetic_universe()


def test_the_archive_matches_the_constants_that_pin_it(universe: dict[str, Any]) -> None:
    assert universe["meta"]["seed"] == UNIVERSE_SEED
    assert universe["meta"]["studentCount"] == UNIVERSE_STUDENT_COUNT
    assert len(universe["students"]) == UNIVERSE_COLLECTION_COUNTS["students"]
    assert len(ASSET_SHA256) == 64


def test_a_tampered_archive_is_rejected(tmp_path: Any) -> None:
    """The digest is the whole reason the archive can be trusted."""

    corrupt = tmp_path / "demo"
    corrupt.mkdir()
    (corrupt / "synthetic-university-v1.json.gz").write_bytes(b"not a gzip archive")
    with pytest.raises(SyntheticUniverseError):
        load_synthetic_universe(tmp_path)


def test_a_missing_archive_names_where_it_looked(tmp_path: Any) -> None:
    with pytest.raises(SyntheticUniverseError, match="searched"):
        load_synthetic_universe(tmp_path)


# ---------------------------------------------------------------------------
# Vocabulary coverage — the failure this module exists to prevent
# ---------------------------------------------------------------------------


def test_every_generated_document_status_has_a_canonical_destination(
    universe: dict[str, Any],
) -> None:
    produced = {str(document["status"]) for document in universe["documents"]}
    assert produced <= set(_DOCUMENT_STATUSES)
    assert produced <= set(_DOCUMENT_REQUIREMENT_STATES)


def test_every_generated_document_category_has_a_canonical_destination(
    universe: dict[str, Any],
) -> None:
    produced = {str(document["category"]) for document in universe["documents"]}
    assert produced <= set(_DOCUMENT_CATEGORIES)
    # And every destination is a value `document_record.category` allows.
    assert set(_DOCUMENT_CATEGORIES.values()) <= {
        "identity",
        "residency",
        "transcript",
        "financial_aid",
        "health",
        "consent",
        "other",
    }


def test_every_generated_aid_status_has_a_canonical_destination(
    universe: dict[str, Any],
) -> None:
    assert {str(row["status"]) for row in universe["fafsaRecords"]} <= set(_FAFSA_DOCUMENT_STATUSES)
    assert {str(row["status"]) for row in universe["verificationRequirements"]} <= set(
        _VERIFICATION_DOCUMENT_STATUSES
    )
    assert {str(row["status"]) for row in universe["aidAwards"]} <= set(_AWARD_STATUSES)
    assert {str(row["status"]) for row in universe["sapStatus"]} <= set(_SAP_STATUSES)


def test_canonical_destinations_are_all_schema_values() -> None:
    """A mapping table is only useful if its right-hand side is real."""

    assert set(_FAFSA_DOCUMENT_STATUSES.values()) <= {
        "not_started",
        "submitted",
        "under_review",
        "verified",
        "action_required",
    }
    assert set(_VERIFICATION_DOCUMENT_STATUSES.values()) <= {
        "not_started",
        "submitted",
        "under_review",
        "verified",
        "action_required",
    }
    assert set(_SAP_STATUSES.values()) <= {
        "meeting",
        "warning",
        "probation",
        "not_meeting",
        "appeal_pending",
    }
    assert {status for status, _ in _DOCUMENT_REQUIREMENT_STATES.values()} <= {
        "not_applicable",
        "blocked",
        "ready",
        "help_requested",
        "in_progress",
        "submitted",
        "under_review",
        "completed",
        "waived",
        "rejected",
        "expired",
    }


# ---------------------------------------------------------------------------
# Requirement derivation
# ---------------------------------------------------------------------------


def _tasks(**overrides: str) -> dict[str, dict[str, Any]]:
    base = {
        "accept_offer": "complete",
        "pay_enrollment_deposit": "not_started",
        "submit_final_transcript": "not_started",
        "submit_immunization_record": "not_started",
        "complete_fafsa": "not_started",
        "apply_for_housing": "not_started",
        "register_for_orientation": "not_started",
        "meet_academic_advisor": "not_started",
    }
    base.update(overrides)
    return {code: {"status": status, "dueAt": None} for code, status in base.items()}


def test_housing_is_blocked_until_the_deposit_posts() -> None:
    unpaid = _requirement_states(
        tasks=_tasks(),
        document_statuses={},
        fafsa_state=None,
        verification_states=[],
    )
    assert unpaid["housing_preference"] == ("blocked", 0)
    assert unpaid["orientation_registration"] == ("blocked", 0)

    paid = _requirement_states(
        tasks=_tasks(pay_enrollment_deposit="complete", apply_for_housing="complete"),
        document_statuses={},
        fafsa_state=None,
        verification_states=[],
    )
    assert paid["housing_preference"] == ("completed", 100)
    assert paid["orientation_registration"] == ("ready", 0)


def test_a_commuter_waives_housing_rather_than_leaving_it_open() -> None:
    states = _requirement_states(
        tasks=_tasks(pay_enrollment_deposit="complete", apply_for_housing="waived"),
        document_statuses={},
        fafsa_state=None,
        verification_states=[],
    )
    assert states["housing_preference"] == ("waived", 100)


def test_verification_selection_keeps_aid_open_even_though_the_fafsa_is_filed() -> None:
    """The checklist calls a filed FAFSA done; the canonical requirement is
    verification, which a selected student has not finished."""

    outstanding = _requirement_states(
        tasks=_tasks(complete_fafsa="complete"),
        document_statuses={},
        fafsa_state="selected_for_verification",
        verification_states=["outstanding", "submitted"],
    )
    assert outstanding["financial_aid_verification"] == ("in_progress", 40)

    submitted = _requirement_states(
        tasks=_tasks(complete_fafsa="complete"),
        document_statuses={},
        fafsa_state="selected_for_verification",
        verification_states=["submitted"],
    )
    assert submitted["financial_aid_verification"] == ("under_review", 70)

    satisfied = _requirement_states(
        tasks=_tasks(complete_fafsa="complete"),
        document_statuses={},
        fafsa_state="selected_for_verification",
        verification_states=["satisfied", "satisfied"],
    )
    assert satisfied["financial_aid_verification"] == ("completed", 100)


def test_document_backed_requirements_follow_the_document() -> None:
    states = _requirement_states(
        tasks=_tasks(),
        document_statuses={
            "transcript": "UNDER_REVIEW",
            "immunization": "NEEDS_RESUBMISSION",
            "photo_id": "ACCEPTED",
        },
        fafsa_state=None,
        verification_states=[],
    )
    assert states["official_transcript"] == ("under_review", 80)
    assert states["immunization_record"] == ("rejected", 0)
    assert states["identity_document"] == ("completed", 100)


def test_every_canonical_code_is_always_derived() -> None:
    states = _requirement_states(
        tasks=_tasks(),
        document_statuses={},
        fafsa_state=None,
        verification_states=[],
    )
    assert set(states) == set(_REQUIREMENT_CODES)


def test_onboarding_completion_follows_the_deposit() -> None:
    assert _onboarding_state(deposit_state="complete", housing_selected=True)[0] == "completed"
    assert _onboarding_state(deposit_state="in_progress", housing_selected=True)[0] == (
        "in_progress"
    )
    status, step, completed = _onboarding_state(deposit_state="not_started", housing_selected=False)
    assert (status, step) == ("in_progress", "housing")
    assert completed == ("offer", "about_you")


# ---------------------------------------------------------------------------
# Generator-wide invariants the import relies on
# ---------------------------------------------------------------------------


def test_only_admitted_applicants_carry_enrollment_state(universe: dict[str, Any]) -> None:
    """The import rejects non-admitted applicants, so nothing downstream may
    depend on them having a deposit or a housing application."""

    not_admitted = {
        str(row["studentId"])
        for row in universe["applications"]
        if str(row["decision"]) != "admitted"
    }
    deposited = {
        str(row["studentId"])
        for row in universe["accountLedger"]
        if str(row["code"]) == "enrollment_deposit"
    }
    housed = {str(row["studentId"]) for row in universe["housingApplications"]}
    assert not (not_admitted & deposited)
    assert not (not_admitted & housed)


def test_external_references_are_unique_and_typable(universe: dict[str, Any]) -> None:
    refs = [str(student["externalRef"]) for student in universe["students"]]
    assert len(set(refs)) == len(refs)
    assert all(len(ref) <= 64 and ref.replace("-", "").isalnum() for ref in refs)


# ---------------------------------------------------------------------------
# The signed demo-student cookie
# ---------------------------------------------------------------------------


def test_a_signed_cookie_round_trips() -> None:
    value = issue_demo_student_cookie("secret", SYNTHETIC_TENANT_ID, _STUDENT)
    assert read_demo_student_cookie("secret", SYNTHETIC_TENANT_ID, value) == _STUDENT


def test_a_cookie_minted_for_one_tenant_is_not_valid_at_another() -> None:
    value = issue_demo_student_cookie("secret", SYNTHETIC_TENANT_ID, _STUDENT)
    assert read_demo_student_cookie("secret", _OTHER_TENANT, value) is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        _STUDENT,
        f"{_STUDENT}.deadbeef",
        "not-a-uuid.deadbeef",
        "x" * 200,
    ],
)
def test_malformed_cookies_resolve_to_no_choice(value: str | None) -> None:
    assert read_demo_student_cookie("secret", SYNTHETIC_TENANT_ID, value) is None


def test_a_different_secret_does_not_verify() -> None:
    value = issue_demo_student_cookie("secret", SYNTHETIC_TENANT_ID, _STUDENT)
    assert read_demo_student_cookie("other-secret", SYNTHETIC_TENANT_ID, value) is None


# ---------------------------------------------------------------------------
# Seed profiles
# ---------------------------------------------------------------------------


def test_the_profile_defaults_to_compact() -> None:
    assert seed_profile({}) == DEFAULT_SEED_PROFILE == "compact"
    assert parse_seed_profile(None) == "compact"
    assert parse_seed_profile("  ") == "compact"


def test_the_profile_accepts_both_spellings() -> None:
    assert seed_profile({"DEMO_SEED_PROFILE": "synthetic_university"}) == "synthetic_university"
    assert seed_profile({"DEMO_SEED_PROFILE": "Synthetic-University"}) == "synthetic_university"


def test_an_unknown_profile_names_the_accepted_values() -> None:
    with pytest.raises(SeedProfileError, match="compact"):
        seed_profile({"DEMO_SEED_PROFILE": "everything"})


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_decoding_the_archive_twice_produces_identical_data() -> None:
    first = json.dumps(load_synthetic_universe(), sort_keys=True)
    second = json.dumps(load_synthetic_universe(), sort_keys=True)
    assert hashlib.sha256(first.encode()).hexdigest() == (
        hashlib.sha256(second.encode()).hexdigest()
    )
