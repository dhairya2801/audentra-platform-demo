"""Engagement-scan decisions that must be honest about missing data.

The scan reads portal activity events. Where a tenant records none (or almost
none), "no activity in seven days" is not evidence of disengagement — it is
the absence of a feed. These rules keep that distinction explicit so a
briefing never reports a whole roster as inactive.
"""

from __future__ import annotations

from datetime import datetime

# Share of the roster that must have recorded activity in the last 30 days
# before "no activity" can be read as "inactive".
ACTIVITY_COVERAGE_SHARE = 0.05
INACTIVE_AFTER_DAYS = 7


def activity_feed_covers_population(active_students: object, roster_size: object) -> bool:
    """True when enough of the roster has activity for silence to mean something.

    Unknown inputs (no columns at all) are treated as covered so older callers
    keep their behaviour; an explicit feed that reaches under 5 % of students
    is not a signal about the other 95 %.
    """

    if active_students is None or roster_size is None:
        return True
    active = int(str(active_students))
    roster = int(str(roster_size))
    if roster <= 0:
        return False
    return active >= max(1, int(roster * ACTIVITY_COVERAGE_SHARE))


def decide_inactive(
    last_meaningful: datetime | None, *, now: datetime, activity_known: bool
) -> bool | None:
    """Seven days without a meaningful action is inactive — but only where the
    tenant records activity for its population. Otherwise: unknown (None)."""

    if not activity_known:
        return None
    return last_meaningful is None or (now - last_meaningful).days >= INACTIVE_AFTER_DAYS
