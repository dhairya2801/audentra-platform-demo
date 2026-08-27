"""The demo persona allowlist: configuration, not a second authentication system."""

from __future__ import annotations

import pytest

from audentra.core.demo_personas import DemoPersonaAllowlist


def test_the_development_default_is_open() -> None:
    allowlist = DemoPersonaAllowlist.from_environment({})
    assert allowlist.restricted is False
    assert allowlist.default_student_ref is None
    assert allowlist.allows_student("SYN-002999")
    assert allowlist.allows_staff("SYN-ADV-012")
    assert allowlist.allows_staff(None)


def test_a_restricted_demo_names_its_people_case_insensitively() -> None:
    allowlist = DemoPersonaAllowlist.from_environment(
        {
            "DEMO_STUDENT_ALLOWLIST": " syn-000061 ",
            "DEMO_STAFF_ALLOWLIST": "SYN-ADV-001, syn-stf-adv-dir,SYN-STF-VP,SYN-ADV-001,,",
        }
    )
    assert allowlist.restricted is True
    assert allowlist.students == ("SYN-000061",)
    assert allowlist.staff == ("SYN-ADV-001", "SYN-STF-ADV-DIR", "SYN-STF-VP")
    assert allowlist.default_student_ref == "SYN-000061"
    assert allowlist.allows_student("syn-000061")
    assert not allowlist.allows_student("SYN-000062")
    assert not allowlist.allows_student(None)
    assert allowlist.allows_staff("syn-stf-vp")
    assert not allowlist.allows_staff("SYN-ADV-012")
    assert not allowlist.allows_staff(None)
    # A UUID is a row, never a named person: it cannot satisfy an allowlist.
    assert not allowlist.allows_student("00000000-0000-7000-8000-000000000101")


def test_a_half_configured_restriction_is_refused() -> None:
    with pytest.raises(ValueError, match="set together"):
        DemoPersonaAllowlist.from_environment({"DEMO_STAFF_ALLOWLIST": "SYN-STF-VP"})
    with pytest.raises(ValueError, match="set together"):
        DemoPersonaAllowlist.from_environment({"DEMO_STUDENT_ALLOWLIST": "SYN-000061"})


def test_an_allowlist_is_a_demo_not_a_directory() -> None:
    too_many = ",".join(f"SYN-{index:06d}" for index in range(33))
    with pytest.raises(ValueError, match="at most 32"):
        DemoPersonaAllowlist.from_environment(
            {"DEMO_STUDENT_ALLOWLIST": too_many, "DEMO_STAFF_ALLOWLIST": "SYN-STF-VP"}
        )
    with pytest.raises(ValueError, match="at most 64"):
        DemoPersonaAllowlist.from_environment(
            {"DEMO_STUDENT_ALLOWLIST": "S" * 65, "DEMO_STAFF_ALLOWLIST": "SYN-STF-VP"}
        )
