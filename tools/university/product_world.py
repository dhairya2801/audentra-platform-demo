"""Deterministic catalog from the model university's reviewed policies.

Rates are published facts, never the donor demo's prices. Student savings and
family support are deliberately absent until the student enters assumptions.
"""

import json


def add_product_world(db, clock):
    policy = db.execute(
        "SELECT id FROM policy WHERE id='tuition-and-fees-2026-2027' OR id LIKE 'tuition-and-fees-2026-2027%' ORDER BY version DESC LIMIT 1"
    ).fetchone()[0]
    meal_policy = db.execute(
        "SELECT id FROM policy WHERE id='meal-plans' OR id LIKE 'meal-plans%' ORDER BY version DESC LIMIT 1"
    ).fetchone()[0]
    rates = [
        (
            "tuition-in-state",
            "tuition",
            "In-state tuition",
            920000,
            "term",
            "in_state",
            {},
        ),
        (
            "tuition-out-of-state",
            "tuition",
            "Out-of-state tuition",
            1445000,
            "term",
            "out_of_state",
            {},
        ),
        (
            "tuition-international",
            "tuition",
            "International tuition",
            1560000,
            "term",
            "international",
            {},
        ),
        (
            "housing-traditional-double",
            "housing",
            "Traditional hall · double",
            362500,
            "term",
            "Traditional halls",
            {"style": "traditional", "roomType": "double"},
        ),
        (
            "housing-suite-double",
            "housing",
            "Suite hall · double",
            405000,
            "term",
            "Suite halls",
            {"style": "suite", "roomType": "double"},
        ),
        (
            "housing-apartment-double",
            "housing",
            "Apartment · double",
            445000,
            "term",
            "Apartments",
            {"style": "apartment", "roomType": "double"},
        ),
        (
            "MP-ANY",
            "meal",
            "Unlimited Access",
            145000,
            "term",
            "Anyone",
            {"swipes": "Unlimited", "diningDollarsCents": 15000},
        ),
        (
            "MP-14",
            "meal",
            "Fourteen Meals Weekly",
            122500,
            "term",
            "Anyone",
            {"swipes": "14 per week", "diningDollarsCents": 20000},
        ),
        (
            "MP-10",
            "meal",
            "Ten Meals Weekly",
            102500,
            "term",
            "Anyone",
            {"swipes": "10 per week", "diningDollarsCents": 25000},
        ),
        (
            "MP-COMMUTER",
            "meal",
            "Commuter Block of Sixty",
            42500,
            "term",
            "Off-campus, commuting and apartment residents",
            {"swipes": "60 per term", "diningDollarsCents": 10000},
        ),
        (
            "health-insurance",
            "insurance",
            "Aster Student Health Insurance",
            185000,
            "year",
            "Waiver subject to reviewed policy",
            {},
        ),
        (
            "payment-plan-fee",
            "fee",
            "Payment plan enrollment",
            4500,
            "term",
            "Signed payment plan required",
            {},
        ),
    ]
    for identifier, kind, name, cents, period, eligibility, metadata in rates:
        db.execute(
            "INSERT INTO rate_catalog VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                kind,
                name,
                cents,
                period,
                "2026-05-01",
                "2027-08-29",
                meal_policy if kind == "meal" else policy,
                eligibility,
                json.dumps(metadata, sort_keys=True),
            ),
        )
    # Existing billed meals justify a current meal enrollment; a housing preference alone does not.
    for row in db.execute(
        "SELECT student_id,term_id FROM ledger WHERE description='Unlimited meals — Fall semester' AND kind='charge'"
    ).fetchall():
        sid, term = row
        db.execute(
            "INSERT INTO meal_enrollment VALUES (?,?,?,?,?,?,?)",
            (f"meal-{sid}-{term}", sid, term, "MP-ANY", "active", 1, clock),
        )
