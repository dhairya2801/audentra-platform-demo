from copy import deepcopy
from typing import Any

import pytest

from audentra.domain.financial_plan import financial_plan, validate_inputs


def account() -> dict[str, Any]:
    return {
        "student": {"id": "student-1"},
        "snapshotAt": "2026-09-08T16:00:00Z",
        "ledger": [
            {
                "id": "charge",
                "term_id": "2026FA",
                "kind": "charge",
                "amount_cents": 100001,
                "description": "Tuition",
                "posted_at": "2026-08-01",
            },
            {
                "id": "aid",
                "term_id": "2026FA",
                "kind": "aid",
                "amount_cents": -30000,
                "description": "Grant disbursed",
                "posted_at": "2026-08-10",
            },
            {
                "id": "payment",
                "term_id": "2026FA",
                "kind": "payment",
                "amount_cents": -120000,
                "description": "Settled payment",
                "posted_at": "2026-08-20",
            },
        ],
        "payments": [
            {"id": state, "term_id": "2026FA", "amount_cents": amount, "status": state}
            for state, amount in [
                ("posted", 120000),
                ("pending", 999999),
                ("failed", 22222),
                ("reversed", 55555),
            ]
        ],
        "awards": [
            {
                "id": "grant",
                "posts_to_account": 1,
                "offered_cents": 90000,
                "accepted_cents": 60000,
                "status": "accepted",
            },
            {
                "id": "work",
                "posts_to_account": 0,
                "offered_cents": 30000,
                "accepted_cents": 30000,
                "status": "accepted",
            },
        ],
        "disbursements": [
            {"id": s, "term_id": "2026FA", "status": s, "amount_cents": 30000}
            for s in ["scheduled", "held", "posted", "reversed"]
        ],
        "holds": [{"id": "hold", "released_at": None}],
        "financialAidRequirements": [{"status": "submitted"}],
        "serviceProgress": [{"status": "waiting"}],
    }


def test_financial_truth_is_posted_cents_and_lifecycle_states_remain_distinct() -> None:
    facts = account()
    result = financial_plan(facts, {})
    assert result["account"]["postedBalanceCents"] == -49999
    assert result["account"]["creditBalanceCents"] == 49999
    assert result["account"]["refundSettlementStatus"] == "not_recorded"
    assert result["account"]["refundLedgerEntries"] == []
    assert result["aid"]["acceptedAnnualCents"] == 60000
    assert result["aid"]["offeredAnnualCents"] == 90000
    assert result["aid"]["anticipatedTermCents"] == 60000
    assert result["account"]["postedAidCents"] == 30000
    assert result["account"]["paymentStates"]["pending"] == 999999
    assert result["requirements"][0]["status"] == "submitted"
    assert result["serviceProgress"][0]["status"] == "waiting"


def test_planning_assumptions_do_not_change_money_or_invent_savings() -> None:
    facts = account()
    original = deepcopy(facts)
    empty = financial_plan(facts, {})
    assert empty["planning"]["inputs"] == {}
    assert empty["planning"]["version"] == 0
    result = financial_plan(
        facts,
        {
            "planning": {
                "inputs_json": '{"savingsCents":10001,"booksCents":6000}',
                "version": 1,
                "updated_at": "2026-09-08",
            }
        },
    )
    assert result["planning"]["estimatedCushionCents"] == 4001
    assert result["account"] == empty["account"]
    assert result["aid"] == empty["aid"]
    assert facts == original


def test_reversal_and_refund_postings_change_balance_without_claiming_settlement() -> None:
    facts = account()
    facts["ledger"] += [
        {
            "id": "refund",
            "term_id": "2026FA",
            "kind": "refund",
            "amount_cents": 49999,
            "description": "Refund initiated",
            "posted_at": "2026-09-08",
        }
    ]
    result = financial_plan(facts, {})
    assert result["account"]["postedBalanceCents"] == 0
    assert result["account"]["creditBalanceCents"] == 0
    assert len(result["account"]["refundLedgerEntries"]) == 1
    assert result["visualization"]["postedCoverage"] == []
    assert result["visualization"]["postedCoverageUnavailableReason"]
    assert result["account"]["refundSettlementStatus"] == "not_recorded"


@pytest.mark.parametrize(
    "value",
    [
        {"savingsCents": True},
        {"savingsCents": 1.1},
        {"savingsCents": -1},
        {"savingsCents": 100000001},
        {"bankBalance": 100},
        [],
        None,
    ],
)
def test_planning_rejects_unknown_or_non_integer_money(value: object) -> None:
    with pytest.raises(ValueError):
        validate_inputs(value)


def test_term_awards_keep_principal_fees_and_hypothetical_gift_separate() -> None:
    facts = account()
    facts["ledger"] = facts["ledger"][:1]
    facts["disbursements"] = []
    result = financial_plan(
        facts,
        {
            "termAwards": [
                {
                    "award_id": "gift",
                    "offered_cents": 30000,
                    "accepted_cents": 0,
                    "accepted_net_cents": 0,
                    "posts_to_account": 1,
                    "source": "institutional",
                },
                {
                    "award_id": "loan",
                    "offered_cents": 175000,
                    "accepted_cents": 175000,
                    "accepted_net_cents": 173145,
                    "posts_to_account": 1,
                    "source": "federal_loan",
                },
                {
                    "award_id": "work",
                    "offered_cents": 150000,
                    "accepted_cents": 150000,
                    "accepted_net_cents": 150000,
                    "posts_to_account": 0,
                    "source": "employment",
                },
            ],
            "paymentAgreements": [{"principal_cents": 100001, "fee_cents": 4500}],
        },
    )
    gift, loan, _ = result["aid"]["termAwards"]
    assert gift["estimatedGapIfRemainingGiftAcceptedCents"] == 70001
    assert loan["fee_cents"] == 1855
    assert loan["estimatedGapIfRemainingGiftAcceptedCents"] is None
    assert result["aid"]["termSummary"] == {
        "offeredGiftCents": 30000,
        "acceptedGiftCents": 0,
        "offeredLoanCents": 175000,
        "acceptedLoanCents": 175000,
        "offeredGrossCents": 205000,
        "acceptedGrossCents": 175000,
        "acceptedNetCents": 173145,
        "pendingDecisionCents": 30000,
        "pendingDecisionCount": 1,
    }
    assert result["account"]["postedBalanceCents"] == 100001
    assert result["paymentAgreements"][0]["totalIncludingFeeCents"] == 104501
