"""Open-slot derivation and booking rules for staff appointments.

A staff member's calendar is three facts: a weekly working pattern in their
own time zone, explicit absences, and the appointments already on the books.
Nothing else is stored — open slots are *derived* here, so the same function
answers "what can this student book?" and "is this booking legal?", and the
two can never disagree.

The module is framework-free so the rules can be tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

APPOINTMENT_TYPES: tuple[str, ...] = (
    "academic_advising",
    "admissions_counseling",
    "financial_aid",
    "enrollment_support",
    "international_check_in",
)

# Which standing relationship serves which kind of appointment. A student who
# books "academic advising" without naming a person should land with their
# primary adviser, not with whoever is free.
ASSIGNMENT_ROLE_FOR_TYPE: dict[str, str] = {
    "academic_advising": "primary_advisor",
    "admissions_counseling": "admissions_counselor",
    "financial_aid": "financial_aid_counselor",
    "international_check_in": "international_adviser",
}

MAX_AVAILABILITY_WINDOW = timedelta(days=62)


@dataclass(frozen=True, slots=True)
class AvailabilityRule:
    weekday: int  # 0 = Sunday … 6 = Saturday (matches PostgreSQL EXTRACT(DOW))
    start_minute: int
    end_minute: int
    slot_minutes: int
    modality: str = "either"
    location: str | None = None
    appointment_types: tuple[str, ...] = ()

    def serves(self, appointment_type: str | None) -> bool:
        return (
            appointment_type is None
            or not self.appointment_types
            or appointment_type in self.appointment_types
        )


@dataclass(frozen=True, slots=True)
class BusyInterval:
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class OpenSlot:
    starts_at: datetime
    ends_at: datetime
    modality: str
    location: str | None


def _python_weekday_to_dow(value: int) -> int:
    # date.weekday(): Monday = 0 … Sunday = 6  →  DOW: Sunday = 0 … Saturday = 6
    return (value + 1) % 7


def resolve_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _overlaps(start: datetime, end: datetime, busy: BusyInterval) -> bool:
    return start < busy.ends_at and busy.starts_at < end


def derive_open_slots(
    *,
    rules: list[AvailabilityRule],
    time_off: list[BusyInterval],
    appointments: list[BusyInterval],
    timezone: str | None,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
    appointment_type: str | None = None,
) -> list[OpenSlot]:
    """Return every bookable slot in ``[window_start, window_end)``.

    A slot is bookable when it lies inside a weekly rule that serves the
    appointment type, starts in the future, and overlaps neither a blocking
    absence nor a live appointment. Slots are returned in chronological order
    and never duplicated even when two rules cover the same minutes.
    """

    if window_end <= window_start:
        return []
    zone = resolve_zone(timezone)
    earliest = max(window_start, now)
    if earliest >= window_end:
        return []
    busy = [*time_off, *appointments]
    local_start = window_start.astimezone(zone).date()
    local_end = window_end.astimezone(zone).date()
    seen: set[datetime] = set()
    slots: list[OpenSlot] = []
    day = local_start
    while day <= local_end:
        dow = _python_weekday_to_dow(day.weekday())
        for rule in rules:
            if rule.weekday != dow or not rule.serves(appointment_type):
                continue
            minute = rule.start_minute
            while minute + rule.slot_minutes <= rule.end_minute:
                start = _local_minute(day, minute, zone)
                end = _local_minute(day, minute + rule.slot_minutes, zone)
                minute += rule.slot_minutes
                if start < earliest or start >= window_end or start in seen:
                    continue
                if any(_overlaps(start, end, interval) for interval in busy):
                    continue
                seen.add(start)
                slots.append(
                    OpenSlot(
                        starts_at=start,
                        ends_at=end,
                        modality=rule.modality,
                        location=rule.location,
                    )
                )
        day += timedelta(days=1)
    slots.sort(key=lambda slot: slot.starts_at)
    return slots


def _local_minute(day: date, minute: int, zone: ZoneInfo) -> datetime:
    local = datetime(day.year, day.month, day.day, tzinfo=zone) + timedelta(minutes=minute)
    return local.astimezone(UTC)


def find_slot(
    starts_at: datetime,
    *,
    rules: list[AvailabilityRule],
    time_off: list[BusyInterval],
    appointments: list[BusyInterval],
    timezone: str | None,
    now: datetime,
    appointment_type: str | None,
) -> OpenSlot | None:
    """The open slot that starts exactly at ``starts_at``, or None.

    Booking goes through this rather than a looser "is inside working hours"
    check so that a student can only book the same grid the calendar offers.
    """

    candidates = derive_open_slots(
        rules=rules,
        time_off=time_off,
        appointments=appointments,
        timezone=timezone,
        window_start=starts_at - timedelta(days=1),
        window_end=starts_at + timedelta(days=1),
        now=now,
        appointment_type=appointment_type,
    )
    for slot in candidates:
        if slot.starts_at == starts_at:
            return slot
    return None


def blocking_reason(
    starts_at: datetime,
    *,
    rules: list[AvailabilityRule],
    time_off: list[BusyInterval],
    appointments: list[BusyInterval],
    timezone: str | None,
    appointment_type: str | None,
) -> str:
    """Explain why ``starts_at`` is not bookable, for the error message.

    Ordered from the hardest fact to the softest: a person already booked or
    away beats "outside working hours", which beats "not on the slot grid".
    """

    probe_end = starts_at + timedelta(minutes=1)
    if any(_overlaps(starts_at, probe_end, busy) for busy in appointments):
        return "APPOINTMENT_SLOT_TAKEN"
    if any(_overlaps(starts_at, probe_end, busy) for busy in time_off):
        return "STAFF_UNAVAILABLE"
    zone = resolve_zone(timezone)
    local = starts_at.astimezone(zone)
    minute = local.hour * 60 + local.minute
    dow = _python_weekday_to_dow(local.weekday())
    inside_hours = False
    for rule in rules:
        if rule.weekday != dow or not rule.serves(appointment_type):
            continue
        if rule.start_minute <= minute < rule.end_minute:
            inside_hours = True
            if (minute - rule.start_minute) % rule.slot_minutes == 0:
                # On the grid and inside hours but still not open: the slot end
                # would overrun the rule, or a neighbouring booking overlaps.
                return "APPOINTMENT_SLOT_TAKEN"
    if inside_hours:
        return "APPOINTMENT_OFF_GRID"
    return "OUTSIDE_WORKING_HOURS"
