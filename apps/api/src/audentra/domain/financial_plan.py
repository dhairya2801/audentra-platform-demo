"""One financial interpretation for the portal, Edward and Atlas.

All arithmetic is integer cents. Planning estimates cannot settle payments,
accept awards, waive insurance, assign housing, or complete verification.
"""

from __future__ import annotations

import json
from typing import Any

JsonDict = dict[str, Any]
INPUT_KEYS = frozenset(
    {
        "savingsCents",
        "familyContributionCents",
        "employmentIncomeCents",
        "otherIncomeCents",
        "booksCents",
        "transportCents",
        "personalCents",
        "rentCents",
        "groceriesCents",
        "otherExpensesCents",
    }
)


def validate_inputs(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) - INPUT_KEYS:
        raise ValueError("Choose documented term planning inputs")
    if any(type(v) is not int or v < 0 or v > 100_000_000 for v in value.values()):
        raise ValueError("Planning amounts must be integer cents between 0 and 100000000")
    return dict(value)


def allocate(costs: list[JsonDict], sources: list[JsonDict]) -> list[JsonDict]:
    """Visualization of a forecast, never an actual award allocation or ledger."""
    remaining = [row["amountCents"] for row in costs]
    cover = [{**row, "parts": []} for row in costs]
    for source in sources:
        left = source["amountCents"]
        for index, amount in enumerate(remaining):
            used = min(left, amount)
            if used:
                cover[index]["parts"].append({"sourceId": source["id"], "amountCents": used})
                remaining[index] -= used
                left -= used
    for index, amount in enumerate(remaining):
        if amount:
            cover[index]["parts"].append({"sourceId": "gap", "amountCents": amount})
    return cover


def financial_plan(account: JsonDict, data: JsonDict, term_id: str = "2026FA") -> JsonDict:
    ledger = [r for r in account["ledger"] if r["term_id"] == term_id]
    payments = [r for r in account["payments"] if r["term_id"] == term_id]
    disbursements = [r for r in account["disbursements"] if r["term_id"] == term_id]
    awards = account["awards"]
    plan = data.get("planning")
    inputs = validate_inputs(json.loads(plan["inputs_json"])) if plan else {}
    costs = [
        {
            "id": r["id"],
            "label": r["description"],
            "amountCents": r["amount_cents"],
            "provenance": "posted_ledger",
            "postedAt": r["posted_at"],
        }
        for r in ledger
        if r["kind"] == "charge"
    ]
    balance = sum(r["amount_cents"] for r in ledger)
    # Reversed rows remain visible; only current scheduled/held disbursements are anticipated.
    pending_aid = sum(
        r["amount_cents"] for r in disbursements if r["status"] in ("scheduled", "held")
    )
    payment_states = {
        state: sum(r["amount_cents"] for r in payments if r["status"] == state)
        for state in ("pending", "posted", "failed", "reversed")
    }
    sources = [
        {
            "id": r["id"],
            "label": r["description"],
            "amountCents": -r["amount_cents"],
            "provenance": "posted_ledger",
        }
        for r in ledger
        if r["kind"] in ("aid", "payment", "credit_adjustment")
    ]
    # A returned credit remains in ledger history but must not inflate a funding
    # chart. Net only explicit reversal links; unallocated refunds stay separate.
    reversed_cents: dict[str, int] = {}
    for entry in ledger:
        if entry["kind"] == "reversal" and entry.get("reverses_id"):
            target = str(entry["reverses_id"])
            reversed_cents[target] = reversed_cents.get(target, 0) + entry["amount_cents"]
    net_sources = [
        {**source, "amountCents": source["amountCents"] - reversed_cents.get(source["id"], 0)}
        for source in sources
    ]
    living: list[JsonDict] = [
        {
            "id": key,
            "label": label,
            "amountCents": inputs[key],
            "provenance": "student_entered_estimate",
        }
        for key, label in [
            ("booksCents", "Books & supplies"),
            ("transportCents", "Transportation"),
            ("personalCents", "Personal & phone"),
            ("rentCents", "Rent & utilities"),
            ("groceriesCents", "Groceries"),
            ("otherExpensesCents", "Other living expenses"),
        ]
        if key in inputs
    ]
    income: list[JsonDict] = [
        {
            "id": key,
            "label": label,
            "amountCents": inputs[key],
            "provenance": "student_entered_assumption",
        }
        for key, label in [
            ("savingsCents", "Estimated savings"),
            ("familyContributionCents", "Assumed family contribution"),
            ("employmentIncomeCents", "Estimated employment income"),
            ("otherIncomeCents", "Other estimated income"),
        ]
        if key in inputs
    ]
    living_total = sum(r["amountCents"] for r in living)
    income_total = sum(r["amountCents"] for r in income)
    terms = {r["id"]: r for r in data.get("terms", [])}
    gap = max(0, balance - pending_aid)
    term_awards = [
        {
            **row,
            "fee_cents": row["accepted_cents"] - row["accepted_net_cents"],
            "estimatedGapIfRemainingGiftAcceptedCents": max(
                0, gap - (row["offered_cents"] - row["accepted_cents"])
            )
            if row["posts_to_account"] and "loan" not in row["source"]
            else None,
        }
        for row in data.get("termAwards", [])
    ]
    account_awards = [row for row in term_awards if row["posts_to_account"]]
    open_awards = [
        row
        for row in account_awards
        if row.get("status") != "declined" and row["offered_cents"] > row["accepted_cents"]
    ]
    term_summary = {
        **{
            f"{state}{kind}Cents": sum(
                row[f"{state}_cents"]
                for row in account_awards
                if ("loan" in row["source"]) == is_loan
            )
            for state in ("offered", "accepted")
            for kind, is_loan in (("Gift", False), ("Loan", True))
        },
        "offeredGrossCents": sum(row["offered_cents"] for row in account_awards),
        "acceptedGrossCents": sum(row["accepted_cents"] for row in account_awards),
        "acceptedNetCents": sum(row["accepted_net_cents"] for row in account_awards),
        "pendingDecisionCents": sum(
            row["offered_cents"] - row["accepted_cents"] for row in open_awards
        ),
        "pendingDecisionCount": len(open_awards),
    }
    agreements = [
        {**row, "totalIncludingFeeCents": row["principal_cents"] + row["fee_cents"]}
        for row in data.get("paymentAgreements", [])
    ]
    room_board = sum(
        row["amountCents"]
        for row in costs
        if any(word in row["label"].lower() for word in ("housing", "room", "meal"))
    )
    comparisons = []
    for rate in data.get("catalog", []):
        if rate["kind"] not in ("housing", "meal") or rate["period"] != "term":
            continue
        words = ("housing", "room") if rate["kind"] == "housing" else ("meal",)
        matches = [r for r in costs if any(w in r["label"].lower() for w in words)]
        if matches:
            current = sum(r["amountCents"] for r in matches)
            comparisons.append(
                {
                    "name": rate["name"],
                    "kind": rate["kind"],
                    "currentChargeCents": current,
                    "alternativeCents": rate["amount_cents"],
                    "differenceCents": rate["amount_cents"] - current,
                    "absoluteDifferenceCents": abs(rate["amount_cents"] - current),
                }
            )
    return {
        "schemaVersion": 1,
        "domain": "financial_plan",
        "student": account["student"],
        "snapshotAt": account["snapshotAt"],
        "termId": term_id,
        "term": terms.get(term_id),
        "basis": "posted_ledger",
        "actionGuidance": {
            "paymentPlanEnrollment": "Contact Student Accounts to enroll. This Financials view "
            "cannot enroll or sign a payment agreement, even when a general policy mentions "
            "online enrollment through Financials.",
            "awardAcceptance": "Contact Financial Aid to accept or decline an offer. "
            "This Financials view cannot record an award decision.",
            "payment": "Contact Student Accounts for the institution's payment channel. "
            "This Financials view cannot submit a payment.",
            "budget": "Students can save their personal estimates and preview scenarios here.",
        },
        "advisers": data.get("advisers", []),
        "currency": "USD",
        "account": {
            "postedBalanceCents": balance,
            "postedChargesCents": sum(r["amountCents"] for r in costs),
            "postedAidCents": -sum(r["amount_cents"] for r in ledger if r["kind"] == "aid"),
            "postedPaymentCreditsCents": -sum(
                r["amount_cents"] for r in ledger if r["kind"] == "payment"
            ),
            "adjustmentsCents": sum(
                r["amount_cents"]
                for r in ledger
                if r["kind"] in ("refund", "reversal", "credit_adjustment")
            ),
            "creditBalanceCents": max(0, -balance),
            "paymentStates": payment_states,
            "refundLedgerEntries": [r for r in ledger if r["kind"] == "refund"],
            "refundSettlementStatus": account.get("refundSettlementStatus", "not_recorded"),
        },
        "aid": {
            "totalsScope": "Annual offered and accepted totals include only awards that post "
            "to the student account. Employment authorization is excluded; "
            "it is not disbursed aid.",
            "awards": awards,
            "disbursements": disbursements,
            "offeredAnnualCents": sum(r["offered_cents"] for r in awards if r["posts_to_account"]),
            "acceptedAnnualCents": sum(
                r["accepted_cents"] for r in awards if r["posts_to_account"]
            ),
            "anticipatedTermCents": pending_aid,
            "loanTerms": [
                {
                    **row,
                    "interestRatePercent": row["interest_basis_points"] / 100
                    if row.get("interest_basis_points") is not None
                    else None,
                    "originationFeePercent": row["fee_basis_points"] / 100
                    if row.get("fee_basis_points") is not None
                    else None,
                }
                for row in data.get("loanTerms", [])
            ],
            "termAwards": term_awards,
            "termSummary": term_summary,
        },
        "ledger": ledger,
        "payments": payments,
        "holds": account["holds"],
        "requirements": account["financialAidRequirements"],
        "serviceProgress": account["serviceProgress"],
        "catalog": data.get("catalog", []),
        "mealEnrollments": data.get("mealEnrollments", []),
        "insuranceCoverage": data.get("insuranceCoverage", []),
        "paymentAgreements": agreements,
        "installments": data.get("installments", []),
        "exceptions": data.get("exceptions", []),
        "planning": {
            "version": plan["version"] if plan else 0,
            "inputs": inputs,
            "updatedAt": plan["updated_at"] if plan else None,
            "provenance": "student_entered",
            "scope": "term",
            "living": living,
            "income": income,
            "livingTotalCents": living_total,
            "incomeTotalCents": income_total,
            "estimatedCushionCents": income_total - living_total,
            "roomAndBoardCents": room_board,
            "catalogComparisons": comparisons,
            "totalAttendanceEstimateCents": sum(r["amountCents"] for r in costs) + living_total,
            "estimatedAccountGapAfterAnticipatedAidCents": max(0, balance - pending_aid),
            "scenarios": data.get("scenarios", []),
        },
        "visualization": {
            "charges": costs,
            "postedSources": sources,
            "netPostedSources": net_sources,
            # Refunds/reversals cannot be allocated back to specific charges
            # without an allocation domain. Keep the exact ledger visible.
            "postedCoverage": []
            if any(r["kind"] in ("refund", "reversal") for r in ledger)
            else allocate(costs, sources),
            "postedCoverageUnavailableReason": "Reversal or refund allocation is not recorded"
            if any(r["kind"] in ("refund", "reversal") for r in ledger)
            else None,
            "livingCoverage": allocate(living, income),
        },
        "boundaries": [
            "Offered and accepted awards are annual commitments, not cash received.",
            "Scheduled or held disbursements are anticipated, not posted.",
            "Pending, failed and reversed payment attempts do not settle a bill.",
            "A credit balance or a refund ledger entry is not proof of refund settlement.",
            "Document submission, review, verification and disbursement are separate stages.",
            "Savings and family support are student assumptions, not verified balances.",
            "Coverage charts illustrate amounts; they do not certify fund restrictions "
            "or actual allocations.",
        ],
    }


def simulate_plan(plan: JsonDict, payload: JsonDict) -> JsonDict:
    """Bounded, read-only forecast. Catalog alternatives never assign or enroll."""
    allowed = {"termId", "inputs", "housingRateId", "mealRateId"}
    if set(payload) - allowed or payload.get("termId") != plan["termId"]:
        raise ValueError("Supply this term and documented scenario inputs")
    inputs = validate_inputs(payload.get("inputs", plan["planning"]["inputs"]))
    income_keys = {
        "savingsCents",
        "familyContributionCents",
        "employmentIncomeCents",
        "otherIncomeCents",
    }
    income = sum(v for k, v in inputs.items() if k in income_keys)
    living = sum(v for k, v in inputs.items() if k not in income_keys)
    alternatives = []
    delta = 0
    comparable = True
    for kind, key, words in (
        ("housing", "housingRateId", ("housing", "room")),
        ("meal", "mealRateId", ("meal",)),
    ):
        if not payload.get(key):
            continue
        rate = next(
            (r for r in plan["catalog"] if r["id"] == payload[key] and r["kind"] == kind), None
        )
        if rate is None or rate["period"] != "term":
            raise ValueError("Choose a published term rate for this category")
        term = plan.get("term") or {}
        if not (rate["effective_from"] <= term.get("starts_on", "") <= rate["effective_until"]):
            raise ValueError("This catalog rate does not apply to the selected term")
        charges = [
            r
            for r in plan["visualization"]["charges"]
            if any(w in r["label"].lower() for w in words)
        ]
        current = sum(r["amountCents"] for r in charges) if charges else None
        difference = rate["amount_cents"] - current if current is not None else None
        comparable = comparable and difference is not None
        delta += difference or 0
        alternatives.append(
            {
                "kind": kind,
                "rateId": rate["id"],
                "name": rate["name"],
                "amountCents": rate["amount_cents"],
                "currentPostedChargeCents": current,
                "differenceCents": difference,
                "eligibility": rate["eligibility"],
                "policyId": rate["policy_id"],
                "provenance": "hypothetical_catalog_choice",
            }
        )
    gap = max(
        0, plan["account"]["postedBalanceCents"] - plan["aid"]["anticipatedTermCents"] + delta
    )
    return {
        "termId": plan["termId"],
        "inputs": inputs,
        "alternatives": alternatives,
        "livingTotalCents": living,
        "incomeTotalCents": income,
        "catalogDifferenceCents": delta if comparable else None,
        "estimatedFundingGapCents": max(0, gap + living - income) if comparable else None,
        "recordsChanged": 0,
        "basis": "hypothetical_planning_assumptions",
        "limitations": [
            "Anticipated aid may remain held; no award acceptance or disbursement is implied.",
            "Catalog choices are subject to eligibility and availability; "
            "they do not create an assignment or meal enrollment.",
            "When no matching posted housing or meal charge exists, "
            "a replacement comparison is unavailable.",
            "Savings and expected income are unverified assumptions. "
            "No payment, bill or saved plan changes.",
        ],
    }
