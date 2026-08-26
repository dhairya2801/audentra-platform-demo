"""Morning Brew composition: what it claims, and what it refuses to claim.

These tests run against a fake reader so they can assert the *semantics* of the
briefing without a database. The PostgreSQL behaviour — tenancy, cohort
agreement, delta reconstruction — lives in
`test_postgres_morning_brew.py`, which needs a migrated test database.

The invariants asserted here are product promises, not implementation details:

- no synthetic risk value ever reaches the payload
- a metric with no timestamped source says so instead of showing a change
- an empty tenant produces an empty briefing, not a placeholder one
- every number carries the cohort definition that produced it
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from audentra.application.morning_brew import build_morning_brew
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.domain.morning_brew import (
    BREW_COHORTS,
    BREW_COHORTS_BY_KEY,
    BREW_DELTAS,
    BREW_METRICS,
    BREW_PRIORITIES,
    BrewCohort,
)
from audentra.domain.student_cohort import COHORT_FILTER_FIELDS, build_cohort_filter

JsonDict = dict[str, Any]

STAFF = AuthContext(
    tenant_id="00000000-0000-7000-8000-000000000002",
    student_id="",
    actor_id="00000000-0000-7000-8000-000000000901",
    actor_type="staff",
)
NOW = datetime(2026, 8, 16, 11, 30, tzinfo=UTC)


class FakeReader:
    """A reader that returns exactly what the test says the database holds."""

    def __init__(
        self,
        *,
        counts: dict[str, int] | None = None,
        activity: dict[str, JsonDict] | None = None,
        deadlines: list[JsonDict] | None = None,
        requests: JsonDict | None = None,
        staff_work: JsonDict | None = None,
        breakdown: list[JsonDict] | None = None,
        sample: list[JsonDict] | None = None,
        scan: JsonDict | None = None,
    ) -> None:
        self._counts = counts or {}
        self._activity = activity or {}
        self._deadlines = deadlines or []
        self._requests = requests or {
            "items": [],
            "total": 0,
            "awaitingFirstReply": 0,
            "unassigned": 0,
        }
        self._staff_work = staff_work or {
            "openItems": 0,
            "urgent": 0,
            "escalated": 0,
            "overdue": 0,
            "unassigned": 0,
            "assignedToMe": 0,
        }
        self._breakdown = breakdown or []
        self._sample = sample or []
        self._scan = scan or {"available": False, "snapshots": 0, "lastProjectedAt": None}
        self.sampled_cohorts: list[str] = []
        self.capacity: JsonDict | None = None

    async def count_cohorts(
        self, auth: AuthContext, cohorts: Sequence[BrewCohort]
    ) -> dict[str, int]:
        return {cohort.key: int(self._counts.get(cohort.key, 0)) for cohort in cohorts}

    async def count_recent_activity(
        self, auth: AuthContext, *, hours: int = 24
    ) -> dict[str, JsonDict]:
        return dict(self._activity)

    async def blocking_requirement_breakdown(
        self, auth: AuthContext, cohort: BrewCohort, *, limit: int = 8
    ) -> list[JsonDict]:
        return list(self._breakdown)

    async def deadline_runway(self, auth: AuthContext) -> list[JsonDict]:
        return list(self._deadlines)

    async def open_student_requests(self, auth: AuthContext) -> JsonDict:
        return dict(self._requests)

    async def staff_work_summary(self, auth: AuthContext) -> JsonDict:
        return dict(self._staff_work)

    async def cohort_sample(
        self, auth: AuthContext, cohort: BrewCohort, *, limit: int = 4
    ) -> list[JsonDict]:
        self.sampled_cohorts.append(cohort.key)
        return list(self._sample)

    async def engagement_scan_freshness(self, auth: AuthContext) -> JsonDict:
        return dict(self._scan)

    async def staff_capacity(self, auth: AuthContext) -> JsonDict | None:
        return self.capacity


def brew(reader: FakeReader, auth: AuthContext = STAFF) -> JsonDict:
    return asyncio.run(build_morning_brew(auth, reader, now=NOW))


def populated_reader(**overrides: Any) -> FakeReader:
    counts = {
        "roster": 14,
        "offer_outstanding": 2,
        "offer_accepted": 11,
        "offer_declined": 1,
        "deposit_paid": 8,
        "deposit_outstanding": 3,
        "onboarding_complete": 4,
        "onboarding_in_progress": 7,
        "onboarding_not_started": 3,
        "enrollment_ready": 5,
        "blocked": 9,
        "deposited_blocked": 3,
        "overdue": 9,
        "deposited_overdue": 3,
        "due_soon": 6,
        "aid_outstanding": 5,
        "aid_action_required": 4,
        "aid_in_review": 1,
        "transcript_missing": 11,
        "documents_in_review": 1,
        "housing_blocked": 0,
        "housing_actionable": 8,
        "housing_selected": 3,
        "open_work_item": 3,
    }
    counts.update(overrides.pop("counts", {}))
    defaults: dict[str, Any] = {
        "counts": counts,
        "activity": {
            "deposits_posted": {"count": 4, "latestAt": "2026-08-16T09:15:00.000Z"},
            "offers_accepted": {"count": 0, "latestAt": None},
        },
        "staff_work": {
            "openItems": 3,
            "urgent": 1,
            "escalated": 1,
            "overdue": 0,
            "unassigned": 1,
            "assignedToMe": 1,
        },
        "breakdown": [
            {
                "code": "financial_aid_verification",
                "title": "Complete financial-aid verification",
                "requirements": 3,
                "students": 3,
                "overdue": 3,
            }
        ],
        "sample": [
            {
                "id": "95000000-0000-7000-8000-000000000101",
                "name": "Lucas Fernandez",
                "programName": "Computer Science",
                "openBlockingCount": 4,
                "nextDueAt": "2026-07-25T00:00:00.000Z",
                "nextDueDays": -22,
            }
        ],
    }
    defaults.update(overrides)
    return FakeReader(**defaults)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_every_named_cohort_is_a_valid_canonical_filter() -> None:
    """A briefing cohort the assistant cannot parse is a briefing nobody can check."""

    for cohort in BREW_COHORTS:
        assert set(cohort.filter_arguments) <= COHORT_FILTER_FIELDS
        # Must round-trip through the same validator the assistant uses.
        build_cohort_filter(cohort.filter_arguments)


def test_cohort_keys_are_unique_and_every_reference_resolves() -> None:
    keys = [cohort.key for cohort in BREW_COHORTS]
    assert len(keys) == len(set(keys))
    for metric in BREW_METRICS:
        assert metric.cohort_key in BREW_COHORTS_BY_KEY
        if metric.basis_key:
            assert metric.basis_key in BREW_COHORTS_BY_KEY
        for key in metric.segment_keys:
            assert key in BREW_COHORTS_BY_KEY
    for priority in BREW_PRIORITIES:
        assert priority.cohort_key in BREW_COHORTS_BY_KEY
        for key in priority.breakdown_keys:
            assert key in BREW_COHORTS_BY_KEY


def test_priority_severity_and_level_agree() -> None:
    """The card chip shows severity and the footer shows level; they are the
    same claim, so a Medium chip over "Priority: High" is a bug in the copy."""

    expected = {"high": "High", "medium": "Medium", "positive": "Low"}
    for spec in BREW_PRIORITIES:
        assert expected[spec.severity] == spec.level, spec.id


def test_every_declared_delta_names_the_column_it_counted() -> None:
    for spec in BREW_DELTAS:
        assert "." in spec.basis, f"{spec.key} must name table.column"
        assert spec.basis_note


def test_metric_delta_keys_match_a_declared_delta() -> None:
    declared = {spec.key for spec in BREW_DELTAS}
    for metric in BREW_METRICS:
        if metric.delta_key:
            assert metric.delta_key in declared


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_a_non_staff_actor_is_refused() -> None:
    student = AuthContext(
        tenant_id=STAFF.tenant_id, student_id="s", actor_id="s", actor_type="student"
    )
    with pytest.raises(ApiError) as error:
        brew(populated_reader(), student)
    assert error.value.code == "STAFF_ACCESS_REQUIRED"


def test_the_funnel_adds_up_from_one_read() -> None:
    payload = brew(populated_reader())
    cohorts = payload["population"]["cohorts"]
    assert cohorts["deposit_paid"] + cohorts["deposit_outstanding"] == cohorts["offer_accepted"]
    assert payload["population"]["students"] == cohorts["roster"]


def test_a_metric_reports_its_share_of_a_named_denominator() -> None:
    payload = brew(populated_reader())
    deposits = next(metric for metric in payload["metrics"] if metric["id"] == "deposits")
    now_frame = next(frame for frame in deposits["frames"] if frame["windowId"] == "now")
    assert now_frame["value"] == 8
    assert now_frame["basisValue"] == 11
    assert now_frame["basisPercent"] == pytest.approx(72.7)
    assert "of accepted students" in now_frame["basisLabel"]
    # There is no plan figure in the platform, so there is no target field.
    assert "target" not in now_frame
    assert "targetProgress" not in now_frame


def test_a_metric_with_no_timestamped_source_says_so_instead_of_showing_a_change() -> None:
    payload = brew(populated_reader())
    blocked = next(metric for metric in payload["metrics"] if metric["id"] == "blocked")
    day_frame = next(frame for frame in blocked["frames"] if frame["windowId"] == "day")
    assert day_frame["unavailable"] is True
    assert day_frame["change"] is None
    assert "reconstructable" in day_frame["window"]


def test_a_delta_carries_the_column_that_proves_it() -> None:
    payload = brew(populated_reader())
    deposits = next(metric for metric in payload["metrics"] if metric["id"] == "deposits")
    now_frame = next(frame for frame in deposits["frames"] if frame["windowId"] == "now")
    assert now_frame["change"]["value"] == 4
    assert now_frame["change"]["basis"] == "payment_transaction.created_at"


def test_a_quiet_event_class_reports_zero_rather_than_disappearing() -> None:
    payload = brew(populated_reader())
    by_id = {change["id"]: change for change in payload["changes"]}
    assert by_id["offers_accepted"]["count"] == 0
    assert by_id["offers_accepted"]["occurredAt"] is None
    assert "Nothing recorded" in by_id["offers_accepted"]["detail"]
    # Every declared class is present, so an empty rail cannot look like a bug.
    assert set(by_id) == {spec.key for spec in BREW_DELTAS}


def test_an_inexact_delta_is_labelled_inexact() -> None:
    payload = brew(populated_reader())
    by_id = {change["id"]: change for change in payload["changes"]}
    assert by_id["requirements_completed"]["exact"] is False
    assert by_id["deposits_posted"]["exact"] is True


def test_attention_items_are_ranked_deterministically_and_carry_their_cohort() -> None:
    payload = brew(populated_reader())
    assert payload["attention"], "a populated tenant must surface attention items"
    first = payload["attention"][0]
    assert first["id"] == "deposited-overdue"
    assert first["cohort"]["filter"] == {"depositState": "paid", "hasOverdueRequirement": True}
    assert first["cohort"]["clauses"] == ["deposit state = paid", "has an overdue requirement"]
    assert first["summary"] == "3 deposited students with an overdue requirement."
    assert first["scope"] == "3 of 14 students on the roster"
    assert first["detail"]["students"][0]["name"] == "Lucas Fernandez"

    # Ranking is the declared weight, so it is predictable from the catalogue.
    weights = {spec.id: spec.weight for spec in BREW_PRIORITIES}
    ranks = [weights[item["id"]] for item in payload["attention"]]
    assert ranks == sorted(ranks, reverse=True)


def test_an_empty_cohort_never_becomes_an_attention_card() -> None:
    payload = brew(populated_reader())
    ids = {item["id"] for item in payload["attention"]}
    # housing_blocked is zero in this tenant.
    assert "housing-blocked" not in ids


def test_blocker_breakdown_states_that_rows_are_requirements_not_people() -> None:
    payload = brew(populated_reader())
    first = payload["attention"][0]
    assert first["detail"]["breakdown"][0]["code"] == "financial_aid_verification"
    assert "do not sum to a headcount" in first["detail"]["breakdownNote"]


def test_detail_reads_are_bounded_to_the_top_items() -> None:
    reader = populated_reader()
    brew(reader)
    assert len(reader.sampled_cohorts) <= 4


def test_synthesis_only_restates_counted_values() -> None:
    payload = brew(populated_reader())
    synthesis = payload["synthesis"]
    assert synthesis["source"] == "deterministic"
    assert synthesis["headline"] == "4 deposits posted in the last 24 hours."
    joined = " ".join([synthesis["headline"], *synthesis["bullets"]])
    # Every integer in the prose must be a value the payload actually holds.
    numbers = {int(token) for token in _integers(joined)}
    available = set(payload["population"]["cohorts"].values()) | {
        int(entry.get("count", 0)) for entry in payload["changes"]
    }
    available |= set(payload["staffWork"].values())
    available |= {
        # "N still do" — the complement of a counted cohort, not a new figure.
        payload["population"]["cohorts"]["deposit_paid"]
        - payload["population"]["cohorts"]["enrollment_ready"],
        # The window length, which the payload also declares.
        payload["window"]["hours"],
    }
    assert numbers <= available, f"unexplained numbers in synthesis: {numbers - available}"


def test_an_empty_tenant_produces_an_empty_briefing() -> None:
    payload = brew(FakeReader(counts={cohort.key: 0 for cohort in BREW_COHORTS}))
    assert payload["population"]["students"] == 0
    assert payload["attention"] == []
    assert payload["priorities"] == []
    assert payload["deadlines"] == []
    assert payload["requests"]["items"] == []
    assert payload["synthesis"]["headline"].startswith("No students are on the roster")
    assert any("no student records" in note.lower() for note in payload["coverage"]["notes"])


def test_a_missing_engagement_scan_is_unavailable_rather_than_zero() -> None:
    payload = brew(populated_reader())
    assert payload["engagementScan"]["available"] is False
    assert any("engagement scan" in note for note in payload["coverage"]["notes"])


def test_the_payload_names_what_it_will_not_report() -> None:
    payload = brew(populated_reader())
    unsupported = " ".join(
        item["metric"] + item["reason"] for item in payload["coverage"]["unsupported"]
    )
    for term in ("melt", "forecast", "snapshot", "mailbox"):
        assert term in unsupported.lower()


def test_no_synthetic_risk_value_reaches_the_payload() -> None:
    """The preview workspace's risk fields are non-canonical and must never appear."""

    payload = json.dumps(brew(populated_reader())).lower()
    for forbidden in (
        "meltlikelihood",
        "recoverylikelihood",
        "riskband",
        "riskscore",
        "confidence",
        "probability",
        "projected deposits",
    ):
        assert forbidden not in payload, f"{forbidden} leaked into the briefing"


def test_windows_are_declared_by_the_api_not_assumed_by_the_client() -> None:
    payload = brew(populated_reader())
    assert [window["id"] for window in payload["windows"]] == ["now", "day"]
    assert payload["window"]["hours"] == 24
    assert payload["window"]["since"] == "2026-08-15T11:30:00.000Z"
    assert "no end-of-day snapshot" in payload["window"]["basis"]


def test_work_queues_are_separate_from_cohort_attention() -> None:
    payload = brew(
        populated_reader(
            requests={
                "items": [],
                "total": 4,
                "awaitingFirstReply": 2,
                "unassigned": 1,
            },
            deadlines=[
                {
                    "kind": "requirement",
                    "code": "official_transcript",
                    "title": "Submit your official transcript",
                    "dueAt": "2026-07-29T00:00:00.000Z",
                    "latestDueAt": "2026-07-29T00:00:00.000Z",
                    "spread": False,
                    "students": 6,
                    "bucket": "overdue",
                    "daysAway": -18,
                }
            ],
        )
    )
    by_id = {item["id"]: item for item in payload["priorities"]}
    assert by_id["urgent-work"]["count"] == 2
    assert by_id["unanswered-requests"]["count"] == 2
    # The queue headline is a headcount from the cohort, not a sum of the
    # per-requirement deadline rows: one student with five overdue requirements
    # is one person to contact.
    assert by_id["overdue-deadlines"]["count"] == 9
    assert {"label": "Overdue requirements outstanding", "value": "1"} in by_id[
        "overdue-deadlines"
    ]["breakdown"]
    # Queues never duplicate the cohort cards.
    assert set(by_id).isdisjoint({item["id"] for item in payload["attention"]})


def test_a_deadline_with_staggered_dates_says_so() -> None:
    payload = brew(
        populated_reader(
            deadlines=[
                {
                    "kind": "requirement",
                    "code": "housing_preference",
                    "title": "Choose your housing preference",
                    "dueAt": "2026-08-20T00:00:00.000Z",
                    "latestDueAt": "2026-08-22T00:00:00.000Z",
                    "spread": True,
                    "students": 3,
                    "bucket": "this_week",
                    "daysAway": 4,
                }
            ]
        )
    )
    deadline = payload["deadlines"][0]
    assert deadline["relativeLabel"] == "from in 4d"
    assert deadline["detail"] == "3 students due across staggered dates."
    assert deadline["students"] == 3


def _integers(text: str) -> list[str]:
    current = ""
    found: list[str] = []
    for character in text:
        if character.isdigit():
            current += character
        else:
            if current:
                found.append(current)
            current = ""
    if current:
        found.append(current)
    return found


# ---------------------------------------------------------------------------
# The people dimension
# ---------------------------------------------------------------------------


def _capacity_snapshot() -> JsonDict:
    return {
        "people": [
            {
                "id": "quentin",
                "name": "Quentin Zephyrine",
                "title": "Adviser",
                "component": "Academic Advising",
                "employmentStatus": "departed",
                "endedAt": "2026-08-01",
                "caseload": {"primaryAdvisees": 79, "cap": 160},
                "work": {"open": 6, "overdue": 4, "staleInProgress": 0},
                "availability": None,
            }
        ],
        "components": [
            {
                "component": "Registrar",
                "open": 100,
                "overdue": 60,
                "unassigned": 0,
                "stale": 25,
                "urgent": 0,
                "escalated": 0,
                "ownerRisk": 96,
                "oldestOverdueDays": 22,
            }
        ],
        "students": {
            "acceptedWithoutPrimaryAdviser": 434,
            "depositedWithoutPrimaryAdviser": 55,
            "withDepartedAdviser": 79,
            "withAdviserOnLeave": 0,
        },
    }


def test_brew_carries_capacity_priorities_and_a_people_bullet() -> None:
    reader = populated_reader()
    reader.capacity = _capacity_snapshot()
    payload = brew(reader)
    assert payload["staffCapacity"]["available"] is True
    assert payload["staffCapacity"]["signals"][0]["kind"] == "departed_with_caseload"
    priorities = {item["id"]: item for item in payload["priorities"]}
    assert priorities["ownership-at-risk"]["count"] == 96
    assert priorities["ownership-at-risk"]["boardQuery"] == {"ownerRisk": True}
    assert priorities["stale-work"]["count"] == 25
    assert any("Quentin Zephyrine has left" in b for b in payload["synthesis"]["bullets"])
    # The synthesis never quotes a number the payload does not carry.
    assert set(_integers(json.dumps(payload["synthesis"]))) <= set(_integers(json.dumps(payload)))


def test_brew_without_capacity_reader_declares_the_gap() -> None:
    payload = brew(populated_reader())
    assert payload["staffCapacity"]["available"] is False
    ids = {item["id"] for item in payload["priorities"]}
    assert "ownership-at-risk" not in ids and "stale-work" not in ids


def test_inactivity_without_an_activity_feed_is_a_coverage_note() -> None:
    reader = populated_reader(
        scan={
            "available": True,
            "snapshots": 14,
            "lastProjectedAt": "2026-08-16T10:00:00.000Z",
            "activityEvents30d": 0,
            "activeStudents30d": 0,
            "activitySignal": False,
        }
    )
    payload = brew(reader)
    assert any("inactivity is unknown" in note for note in payload["coverage"]["notes"])
