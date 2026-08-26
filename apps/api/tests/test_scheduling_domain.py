"""Open-slot derivation and booking rules, independent of any database."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from audentra.domain.scheduling import (
    AvailabilityRule,
    BusyInterval,
    blocking_reason,
    derive_open_slots,
    find_slot,
)

ET = ZoneInfo("America/New_York")


def _et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET).astimezone(UTC)


# Tuesday 2026-08-25 (DOW 2) and Thursday 2026-08-27 (DOW 4).
TUE_THU = [
    AvailabilityRule(weekday=2, start_minute=9 * 60, end_minute=12 * 60, slot_minutes=30),
    AvailabilityRule(weekday=4, start_minute=13 * 60, end_minute=15 * 60, slot_minutes=30),
]
NOW = _et(2026, 8, 24, 8)  # Monday morning


def test_slots_follow_the_weekly_pattern_in_local_time() -> None:
    slots = derive_open_slots(
        rules=TUE_THU,
        time_off=[],
        appointments=[],
        timezone="America/New_York",
        window_start=_et(2026, 8, 24, 0),
        window_end=_et(2026, 8, 29, 0),
        now=NOW,
    )
    starts = [slot.starts_at for slot in slots]
    assert starts[0] == _et(2026, 8, 25, 9)
    assert _et(2026, 8, 25, 11, 30) in starts
    assert _et(2026, 8, 25, 12) not in starts  # a slot must end inside the rule
    assert _et(2026, 8, 27, 13) in starts
    assert _et(2026, 8, 26, 10) not in starts  # Wednesday has no rule
    assert len(starts) == 6 + 4
    assert all(
        slot.ends_at - slot.starts_at == slots[0].ends_at - slots[0].starts_at for slot in slots
    )


def test_past_slots_time_off_and_bookings_are_removed() -> None:
    now = _et(2026, 8, 25, 10, 5)
    slots = derive_open_slots(
        rules=TUE_THU,
        time_off=[BusyInterval(_et(2026, 8, 27, 0), _et(2026, 8, 28, 0))],
        appointments=[BusyInterval(_et(2026, 8, 25, 11), _et(2026, 8, 25, 11, 30))],
        timezone="America/New_York",
        window_start=_et(2026, 8, 24, 0),
        window_end=_et(2026, 8, 29, 0),
        now=now,
    )
    starts = [slot.starts_at for slot in slots]
    assert starts == [_et(2026, 8, 25, 10, 30), _et(2026, 8, 25, 11, 30)]


def test_rules_can_be_restricted_to_an_appointment_type() -> None:
    rules = [
        AvailabilityRule(
            weekday=2,
            start_minute=9 * 60,
            end_minute=10 * 60,
            slot_minutes=30,
            appointment_types=("financial_aid",),
        )
    ]
    common: dict[str, Any] = {
        "rules": rules,
        "time_off": [],
        "appointments": [],
        "timezone": "America/New_York",
        "window_start": _et(2026, 8, 24, 0),
        "window_end": _et(2026, 8, 29, 0),
        "now": NOW,
    }
    assert len(derive_open_slots(**common, appointment_type="financial_aid")) == 2
    assert derive_open_slots(**common, appointment_type="academic_advising") == []
    assert len(derive_open_slots(**common, appointment_type=None)) == 2


def test_find_slot_only_accepts_the_published_grid() -> None:
    common: dict[str, Any] = {
        "rules": TUE_THU,
        "time_off": [],
        "appointments": [],
        "timezone": "America/New_York",
        "now": NOW,
        "appointment_type": "academic_advising",
    }
    assert find_slot(_et(2026, 8, 25, 9, 30), **common) is not None
    assert find_slot(_et(2026, 8, 25, 9, 15), **common) is None  # off grid
    assert find_slot(_et(2026, 8, 25, 3), **common) is None  # 3 am
    assert find_slot(_et(2026, 8, 26, 9, 30), **common) is None  # Wednesday


def test_blocking_reason_names_the_hardest_fact_first() -> None:
    kwargs: dict[str, Any] = {
        "rules": TUE_THU,
        "timezone": "America/New_York",
        "appointment_type": "academic_advising",
    }
    taken = _et(2026, 8, 25, 9, 30)
    assert (
        blocking_reason(
            taken,
            time_off=[],
            appointments=[BusyInterval(taken, _et(2026, 8, 25, 10))],
            **kwargs,
        )
        == "APPOINTMENT_SLOT_TAKEN"
    )
    assert (
        blocking_reason(
            taken,
            time_off=[BusyInterval(_et(2026, 8, 25, 0), _et(2026, 8, 26, 0))],
            appointments=[],
            **kwargs,
        )
        == "STAFF_UNAVAILABLE"
    )
    assert (
        blocking_reason(_et(2026, 8, 25, 3), time_off=[], appointments=[], **kwargs)
        == "OUTSIDE_WORKING_HOURS"
    )
    assert (
        blocking_reason(_et(2026, 8, 25, 9, 15), time_off=[], appointments=[], **kwargs)
        == "APPOINTMENT_OFF_GRID"
    )


def test_unknown_timezone_falls_back_to_utc_rather_than_failing() -> None:
    rules = [AvailabilityRule(weekday=2, start_minute=0, end_minute=60, slot_minutes=30)]
    slots = derive_open_slots(
        rules=rules,
        time_off=[],
        appointments=[],
        timezone="Mars/Olympus",
        window_start=datetime(2026, 8, 25, tzinfo=UTC),
        window_end=datetime(2026, 8, 26, tzinfo=UTC),
        now=datetime(2026, 8, 24, tzinfo=UTC),
    )
    assert [slot.starts_at for slot in slots] == [
        datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 25, 0, 30, tzinfo=UTC),
    ]
