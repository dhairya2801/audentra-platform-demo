"""Repeat-safe local demo financial package; only Ada's records are enriched.

Existing generated financial amounts are replaced atomically once. A completion
marker preserves subsequent student edits. No real payment is initiated.
"""

import argparse
import asyncio
import json
from urllib.parse import urlparse
from uuid import UUID

import asyncpg

TENANT = UUID("00000000-0000-7000-8000-000000000003")
VERSION = "ada-financials-v1"
TERM = "2026FA"
AT = "2026-09-08T16:00:00Z"
POLICY = "v3-award-packaging@2026.1"


async def record_billing_extension(db, sid):
    """A scoped synthetic approval explains dates beyond the general policy."""
    approver = await db.fetchval(
        "SELECT id FROM university.staff WHERE tenant_id=$1 AND office_id='SA' "
        "AND status='active' ORDER BY id LIMIT 1",
        TENANT,
    )
    if not approver:
        raise ValueError("A Student Accounts approver is required")
    await db.execute(
        "INSERT INTO university.exception(tenant_id,id,student_id,policy_id,kind,status,"
        "office_id,approver_id,starts_at,ends_at,reason,term_id) "
        "VALUES($1,$2,$3,'billing-and-payment-policy@2026.1','billing_due_date_extension',"
        "'approved','SA',$4,'2026-09-03T14:00:00Z','2026-12-16T05:00:00Z',$5,'2026FA') "
        "ON CONFLICT(tenant_id,id) DO NOTHING",
        TENANT,
        VERSION + "-billing-extension",
        sid,
        approver,
        "Student Accounts approved an individual fall billing extension to September 30, 2026 "
        "at 5 p.m. Eastern while the community scholarship and subsidized loan are scheduled. "
        "If the student enrolls, principal installments may be due September 30, October 30, "
        "November 30 and December 15. The $45 fee and first installment remain required to "
        "enroll; this exception does not sign the proposed agreement or post a payment.",
    )


async def complete_spring(db, sid):
    """Keep the second term consistent with the annual package, once only."""
    marker = VERSION + "-annual-reconciliation"
    if await db.fetchval(
        "SELECT value FROM university.meta WHERE tenant_id=$1 AND key=$2", TENANT, marker
    ):
        return
    rows = await db.fetch(
        "SELECT a.*,t.offered_cents AS fall_offered,t.accepted_cents AS fall_accepted, "
        "f.source,f.posts_to_account FROM university.award a "
        "JOIN university.award_term t ON t.tenant_id=a.tenant_id AND t.award_id=a.id "
        "JOIN university.fund f ON f.tenant_id=a.tenant_id AND f.id=a.fund_id "
        "WHERE a.tenant_id=$1 AND a.student_id=$2 AND t.term_id='2026FA'",
        TENANT,
        sid,
    )
    for row in rows:
        offered = row["offered_cents"] - row["fall_offered"]
        accepted = row["accepted_cents"] - row["fall_accepted"]
        if not offered:
            continue
        fee = (accepted * 106 + 5000) // 10000 if "loan" in row["source"] else 0
        net = accepted - fee
        await db.execute(
            "INSERT INTO university.award_term(tenant_id,award_id,term_id,offered_cents,accepted_cents,accepted_net_cents,note) "
            "VALUES($1,$2,'2027SP',$3,$4,$5,'Second term of the annual award; subject to continued eligibility.') "
            "ON CONFLICT DO NOTHING",
            TENANT,
            row["id"],
            offered,
            accepted,
            net,
        )
        if accepted and row["posts_to_account"]:
            existing = await db.fetchval(
                "SELECT id FROM university.disbursement WHERE tenant_id=$1 AND award_id=$2 AND term_id='2027SP'",
                TENANT,
                row["id"],
            )
            if existing:
                await db.execute(
                    "UPDATE university.disbursement SET amount_cents=$3 WHERE tenant_id=$1 AND id=$2 AND status='scheduled'",
                    TENANT,
                    existing,
                    net,
                )
            else:
                await db.execute(
                    "INSERT INTO university.disbursement(tenant_id,id,award_id,term_id,amount_cents,status,scheduled_at,reason) "
                    "VALUES($1,$2,$3,'2027SP',$4,'scheduled','2027-01-15T14:00:00Z','Subject to spring enrollment and continued eligibility')",
                    TENANT,
                    f"{VERSION}-spring-{row['fund_id']}",
                    row["id"],
                    net,
                )
    await db.execute(
        "INSERT INTO university.meta(tenant_id,key,value) VALUES($1,$2,$3)", TENANT, marker, sid
    )


async def seed(url):
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
        "/audentra_university"
    ):
        raise ValueError("Choose an explicit local audentra_university database")
    db = await asyncpg.connect(url)
    try:
        async with db.transaction():
            await db.execute("SELECT set_config('audentra.tenant_id',$1,true)", str(TENANT))
            await db.execute("SELECT pg_advisory_xact_lock(hashtext($1))", VERSION)
            if await db.fetchval(
                "SELECT value FROM university.meta WHERE tenant_id=$1 AND key=$2", TENANT, VERSION
            ):
                sid = await db.fetchval(
                    "SELECT id FROM university.student WHERE tenant_id=$1 AND external_ref='SYN-000061'",
                    TENANT,
                )
                await complete_spring(db, sid)
                await record_billing_extension(db, sid)
                return {"seeded": False, "reason": "Already seeded; subsequent edits preserved"}
            sid = await db.fetchval(
                "SELECT id FROM university.student WHERE tenant_id=$1 AND external_ref='SYN-000061'",
                TENANT,
            )
            if not sid:
                raise ValueError("Import Ada before enriching her financials")

            async def insert(table, **values):
                values = {"tenant_id": TENANT, **values}
                cols = ",".join(values)
                placeholders = ",".join(f"${i + 1}" for i in range(len(values)))
                await db.execute(
                    f"INSERT INTO university.{table} ({cols}) VALUES ({placeholders})",
                    *values.values(),
                )

            # Retain Ada's tuition, mandatory fees, room assignment and meal plan.
            # Convert her generated full settlement to a realistic family payment.
            payment = await db.fetchrow(
                "SELECT * FROM university.payment WHERE tenant_id=$1 AND student_id=$2 AND term_id=$3 AND method='bank_transfer' AND status='posted'",
                TENANT,
                sid,
                TERM,
            )
            if not payment or payment["amount_cents"] != 1525000:
                raise ValueError("Ada financial baseline changed; inspect before replacing it")
            await db.execute(
                "UPDATE university.payment SET amount_cents=250000 WHERE tenant_id=$1 AND id=$2",
                TENANT,
                payment["id"],
            )
            await db.execute(
                "UPDATE university.ledger SET amount_cents=-250000,description='Family payment · bank transfer' WHERE tenant_id=$1 AND payment_id=$2",
                TENANT,
                payment["id"],
            )
            await db.execute(
                "UPDATE university.ledger SET due_at='2026-09-30T21:00:00Z' WHERE tenant_id=$1 AND student_id=$2 AND term_id=$3 AND kind='charge'",
                TENANT,
                sid,
                TERM,
            )
            await insert(
                "ledger",
                id=f"{VERSION}-health",
                student_id=sid,
                term_id=TERM,
                kind="charge",
                amount_cents=185000,
                posted_at="2026-08-10T14:00:00Z",
                due_at="2026-09-30T21:00:00Z",
                description="Health insurance · annual premium billed in fall",
            )
            await insert(
                "insurance_coverage",
                id=f"{VERSION}-coverage",
                student_id=sid,
                term_id=TERM,
                catalog_id="health-insurance",
                status="enrolled",
                version=1,
                updated_at=AT,
            )

            # Amounts are cents. Annual figures and term allocations are explicit.
            # Access is institutional aid: Ada is out-of-state, so no Florida award.
            awards = [
                (
                    "MERIT",
                    "Aster Merit Scholarship",
                    "institutional",
                    1200000,
                    1200000,
                    600000,
                    600000,
                    600000,
                    "posted",
                ),
                (
                    "PELL",
                    "Federal Pell Grant",
                    "federal",
                    554500,
                    554500,
                    277200,
                    277200,
                    277200,
                    "posted",
                ),
                (
                    "ACCESS",
                    "Aster Access Scholarship",
                    "institutional",
                    639000,
                    639000,
                    319500,
                    319500,
                    319500,
                    "posted",
                ),
                (
                    "COMMUNITY",
                    "Community Foundation Scholarship",
                    "external",
                    100000,
                    100000,
                    100000,
                    100000,
                    100000,
                    "scheduled",
                ),
                ("NEED", "Aster Need Grant", "institutional", 650000, 0, 325000, 0, 0, None),
                (
                    "DEMO-SUB",
                    "Direct Subsidized Loan",
                    "federal_loan",
                    350000,
                    350000,
                    175000,
                    175000,
                    173145,
                    "scheduled",
                ),
                (
                    "DIRECT-UNSUB",
                    "Direct Unsubsidized Loan",
                    "federal_loan",
                    200000,
                    0,
                    100000,
                    0,
                    0,
                    None,
                ),
                (
                    "WORK",
                    "Campus employment authorization",
                    "employment",
                    300000,
                    300000,
                    150000,
                    150000,
                    150000,
                    None,
                ),
            ]
            for (
                fund,
                name,
                source,
                offered,
                accepted,
                term_offer,
                term_accept,
                net,
                disb_status,
            ) in awards:
                if not await db.fetchval(
                    "SELECT 1 FROM university.fund WHERE tenant_id=$1 AND id=$2", TENANT, fund
                ):
                    await insert(
                        "fund",
                        id=fund,
                        name=name,
                        source=source,
                        annual_cap_cents=offered,
                        posts_to_account=int(source != "employment"),
                        international_eligible=0,
                        policy_id=POLICY,
                    )
                award = await db.fetchval(
                    "SELECT id FROM university.award WHERE tenant_id=$1 AND student_id=$2 AND fund_id=$3 AND aid_year=$4",
                    TENANT,
                    sid,
                    fund,
                    "2026-2027",
                )
                status = "accepted" if accepted else "offered"
                if award:
                    await db.execute(
                        "UPDATE university.award SET offered_cents=$3,accepted_cents=$4,status=$5 WHERE tenant_id=$1 AND id=$2",
                        TENANT,
                        award,
                        offered,
                        accepted,
                        status,
                    )
                else:
                    award = f"{VERSION}-{fund}"
                    await insert(
                        "award",
                        id=award,
                        student_id=sid,
                        fund_id=fund,
                        aid_year="2026-2027",
                        offered_cents=offered,
                        accepted_cents=accepted,
                        status=status,
                    )
                note = (
                    "Projected earnings require a campus job; paid to the student, not the bill."
                    if source == "employment"
                    else "One-time fall scholarship."
                    if fund == "COMMUNITY"
                    else "Synthetic demo loan terms: 6.39% interest, 1.06% origination fee; 120-month repayment. Loan prerequisites complete."
                    if source == "federal_loan"
                    else "Maintain eligibility and satisfactory academic progress; annual award is split across fall and spring."
                )
                await insert(
                    "award_term",
                    award_id=award,
                    term_id=TERM,
                    offered_cents=term_offer,
                    accepted_cents=term_accept,
                    accepted_net_cents=net,
                    decision_due_at="2026-09-30T21:00:00Z" if not accepted else None,
                    note=note,
                )
                if source == "federal_loan":
                    await db.execute(
                        "INSERT INTO university.loan_terms(tenant_id,fund_id,interest_basis_points,fee_basis_points,term_months,policy_id,effective_from,effective_until) VALUES($1,$2,639,106,120,$3,'2026-07-01','2027-06-30') ON CONFLICT DO NOTHING",
                        TENANT,
                        fund,
                        "loans-and-work-study@2026.1",
                    )
                if not disb_status:
                    continue
                scheduled = (
                    "2026-09-04T14:00:00Z"
                    if disb_status == "posted"
                    else "2026-09-23T14:00:00Z"
                    if source == "federal_loan"
                    else "2026-09-15T14:00:00Z"
                )
                disb = await db.fetchval(
                    "SELECT id FROM university.disbursement WHERE tenant_id=$1 AND award_id=$2 AND term_id=$3",
                    TENANT,
                    award,
                    TERM,
                )
                if disb:
                    await db.execute(
                        "UPDATE university.disbursement SET amount_cents=$3 WHERE tenant_id=$1 AND id=$2",
                        TENANT,
                        disb,
                        net,
                    )
                    await db.execute(
                        "UPDATE university.ledger SET amount_cents=-$3::integer WHERE tenant_id=$1 AND disbursement_id=$2",
                        TENANT,
                        disb,
                        net,
                    )
                else:
                    disb = f"{VERSION}-disb-{fund}"
                    await insert(
                        "disbursement",
                        id=disb,
                        award_id=award,
                        term_id=TERM,
                        amount_cents=net,
                        status=disb_status,
                        scheduled_at=scheduled,
                        posted_at=scheduled if disb_status == "posted" else None,
                        reason=None
                        if disb_status == "posted"
                        else "Scheduled; not yet credited to the account",
                    )
                    if disb_status == "posted":
                        await insert(
                            "ledger",
                            id=f"{VERSION}-credit-{fund}",
                            student_id=sid,
                            term_id=TERM,
                            kind="aid",
                            amount_cents=-net,
                            posted_at=scheduled,
                            disbursement_id=disb,
                            description=name,
                        )

            inputs = dict(
                booksCents=60000,
                transportCents=73000,
                personalCents=150000,
                savingsCents=180000,
                familyContributionCents=67500,
                employmentIncomeCents=150000,
            )
            await insert(
                "financial_plan_input",
                student_id=sid,
                term_id=TERM,
                version=1,
                inputs_json=json.dumps(inputs),
                updated_at=AT,
                provenance="student_entered",
            )
            # Proposed, not signed: exact cent allocation, fee collected separately.
            principal = 440155
            agreement = f"{VERSION}-plan"
            await insert(
                "payment_agreement",
                id=agreement,
                student_id=sid,
                term_id=TERM,
                principal_cents=principal,
                fee_cents=4500,
                status="proposed",
                signed_at=None,
                version=1,
                terms_policy_id="tuition-and-fees-2026-2027@2026.1",
            )
            for i, day in enumerate(["2026-09-30", "2026-10-30", "2026-11-30", "2026-12-15"]):
                await insert(
                    "payment_installment",
                    id=f"{agreement}-{i}",
                    agreement_id=agreement,
                    amount_cents=principal // 4 + (1 if i < principal % 4 else 0),
                    due_at=f"{day}T21:00:00Z",
                )
            balance = await db.fetchval(
                "SELECT sum(amount_cents) FROM university.ledger WHERE tenant_id=$1 AND student_id=$2 AND term_id=$3",
                TENANT,
                sid,
                TERM,
            )
            assert balance == 713300, balance
            await insert(
                "meta",
                key=VERSION,
                value=json.dumps(
                    {
                        "studentId": sid,
                        "postedBalanceCents": balance,
                        "estimatedRemainingCents": principal,
                        "seededAt": AT,
                    }
                ),
            )
            await complete_spring(db, sid)
            await record_billing_extension(db, sid)
            return {
                "seeded": True,
                "postedBalanceCents": balance,
                "estimatedRemainingCents": principal,
            }
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    print(asyncio.run(seed(parser.parse_args().database_url)))
