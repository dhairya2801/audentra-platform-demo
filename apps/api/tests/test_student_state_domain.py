"""The canonical student-state projections.

The deposit derivation is the one every reader shares, so its edge cases are
tested here rather than once per caller: which record wins, what a pending
payment means, and — the case that caused the original divergence — what
happens when the payload simply does not carry the answer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from audentra.domain.student_state import (
    deadline_bucket,
    derive_deposit_state,
    derive_enrollment_blockers,
    requirement_gate_code,
)

NOW = datetime(2027, 6, 1, tzinfo=UTC)
OFFER_ID = "00000000-0000-7000-8000-000000000201"

DASHBOARD = {
    "offer": {
        "id": OFFER_ID,
        "depositAmountCents": 50_000,
        "responseDeadline": "2027-08-15",
        "status": "accepted",
    }
}


def _payment(status: str, offer_id: str = OFFER_ID) -> dict[str, object]:
    return {
        "type": "enrollment_deposit",
        "offerId": offer_id,
        "amountCents": 50_000,
        "status": status,
        "createdAt": "2027-05-02T10:00:00Z",
    }


def test_a_succeeded_payment_is_the_receipt() -> None:
    state = derive_deposit_state(dashboard=DASHBOARD, payments={"items": [_payment("succeeded")]})
    assert state.paid is True
    assert state.pending is False
    assert state.outstanding is False
    assert state.paid_at == "2027-05-02T10:00:00Z"
    assert state.amount_cents == 50_000


def test_a_pending_payment_is_neither_paid_nor_outstanding() -> None:
    """The student already acted; telling them to pay again would be wrong."""

    state = derive_deposit_state(dashboard=DASHBOARD, payments={"items": [_payment("pending")]})
    assert state.paid is False
    assert state.pending is True
    assert state.outstanding is False


def test_no_payment_means_the_deposit_is_outstanding() -> None:
    state = derive_deposit_state(dashboard=DASHBOARD, payments={"items": []})
    assert state.known is True
    assert state.paid is False
    assert state.outstanding is True
    assert state.due_at == "2027-08-15"


def test_a_payment_against_a_different_offer_is_not_this_deposit() -> None:
    other = "00000000-0000-7000-8000-000000000999"
    state = derive_deposit_state(
        dashboard=DASHBOARD, payments={"items": [_payment("succeeded", offer_id=other)]}
    )
    assert state.paid is False
    assert state.outstanding is True


def test_a_settled_payment_outranks_an_in_flight_one() -> None:
    state = derive_deposit_state(
        dashboard=DASHBOARD,
        payments={"items": [_payment("pending"), _payment("succeeded")]},
    )
    assert state.paid is True
    assert state.pending is False


def test_the_financial_schedule_settles_it_when_the_ledger_is_unreadable() -> None:
    state = derive_deposit_state(
        dashboard=DASHBOARD,
        payments=None,
        financials={
            "paymentSchedule": [
                {"kind": "deposit", "status": "paid", "amountCents": 50_000, "dueAt": "2027-08-15"}
            ]
        },
    )
    assert state.paid is True
    assert state.sources == ("financials",)


def test_the_payments_ledger_wins_over_a_stale_schedule() -> None:
    """Both are computed from the same table; the ledger is the closer read."""

    state = derive_deposit_state(
        dashboard=DASHBOARD,
        payments={"items": []},
        financials={"paymentSchedule": [{"kind": "deposit", "status": "paid"}]},
    )
    assert state.paid is False
    assert state.outstanding is True


def test_an_unreadable_record_is_unknown_and_never_unpaid() -> None:
    """The regression that started this work.

    The Postgres dashboard carries no deposit field. A derivation that reads
    only the dashboard must report that it does not know, because reporting
    "unpaid" invents a blocker for every student who has already paid.
    """

    state = derive_deposit_state(dashboard=DASHBOARD)
    assert state.known is False
    assert state.paid is False
    assert state.outstanding is False, "unknown must not masquerade as outstanding"


def test_blockers_never_duplicate_the_deposit_gate() -> None:
    """The offer-level deposit and its checklist step are one obstacle."""

    requirements = {
        "items": [
            {
                "code": "enrollment_deposit",
                "title": "Pay your enrollment deposit",
                "status": "ready",
                "blocking": True,
                "slug": "enrollment-deposit",
            }
        ]
    }
    deposit = derive_deposit_state(dashboard=DASHBOARD, payments={"items": []})
    blockers = derive_enrollment_blockers(requirements=requirements, deposit=deposit)
    codes = [blocker["code"] for blocker in blockers]
    assert codes.count("enrollment_deposit_posted") == 1


def test_a_submitted_requirement_is_owned_by_the_university() -> None:
    requirements = {
        "items": [
            {
                "code": "official_transcript",
                "title": "Submit your official transcript",
                "status": "under_review",
                "blocking": True,
                "slug": "official-transcript",
            }
        ]
    }
    deposit = derive_deposit_state(dashboard=DASHBOARD, payments={"items": [_payment("succeeded")]})
    blocker = derive_enrollment_blockers(requirements=requirements, deposit=deposit)[0]
    assert blocker["code"] == "final_transcript"
    assert blocker["owner"] == "university"
    assert "no student action" in blocker["clearingAction"].lower()


def test_a_pending_deposit_blocker_does_not_ask_for_a_second_payment() -> None:
    deposit = derive_deposit_state(dashboard=DASHBOARD, payments={"items": [_payment("pending")]})
    blocker = derive_enrollment_blockers(requirements={"items": []}, deposit=deposit)[0]
    assert blocker["owner"] == "university"
    assert "no new payment" in blocker["clearingAction"].lower()


def test_completed_requirements_are_not_blockers() -> None:
    requirements = {
        "items": [
            {"code": "profile_verification", "status": "completed", "blocking": True},
            {"code": "identity_document", "status": "waived", "blocking": True},
            {"code": "orientation", "status": "not_applicable", "blocking": True},
        ]
    }
    deposit = derive_deposit_state(dashboard=DASHBOARD, payments={"items": [_payment("succeeded")]})
    assert derive_enrollment_blockers(requirements=requirements, deposit=deposit) == []


def test_gate_codes_are_stable_across_requirement_naming() -> None:
    assert requirement_gate_code("official_transcript") == "final_transcript"
    assert requirement_gate_code("transcript") == "final_transcript"
    assert requirement_gate_code("immunization_record") == "immunization_cleared"
    # An unmapped code keeps its own identity rather than collapsing to a
    # generic bucket that would merge two distinct obstacles.
    assert requirement_gate_code("residency_review") == "residency_review"


def test_deadline_buckets() -> None:
    assert deadline_bucket("2027-05-01", NOW) == "overdue"
    assert deadline_bucket("2027-06-04", NOW) == "this_week"
    assert deadline_bucket("2027-06-20", NOW) == "this_month"
    assert deadline_bucket("2027-12-01", NOW) == "later"
    assert deadline_bucket("not a date", NOW) == "later"
    assert deadline_bucket(None, NOW) == "later"
