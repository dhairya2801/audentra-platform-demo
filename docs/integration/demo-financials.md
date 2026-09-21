# Ada’s My Financials

The demo student is **Ada Kettleby (SYN-000061)**. The four sections at
`http://localhost:3009/financials` now follow the current `test.audentra.ai`
workspace: Overview, Payments, Expenses, and Loans & aid. The reference’s hero,
summary strip, cards, two-ring charts, tables, campus option pages, and responsive
styles are retained. Chart segments and amount cards open details. Budgets save
and survive reload; monthly figures are averages; the simulator previews changes
without changing charges, aid, or housing.

## Account and data

All money is integer cents. Existing university rates and Ada’s room/meal selection
were retained rather than copying the reference’s unrelated illustrative student.
The synthetic institution’s snapshot remains September 8, 2026, clearly displayed.

| Fall 2026 item | Amount |
| --- | ---: |
| Tuition / fees / housing / meals / annual insurance | $14,450 / $725 / $3,625 / $1,450 / $1,850 |
| University charges | $22,100 |
| Posted Merit / Access / Pell | $6,000 / $3,195 / $2,772 |
| Received deposit / family bank payment | $500 / $2,500 |
| Posted balance | $7,133 |
| Scheduled community scholarship / net subsidized loan | $1,000 / $1,731.45 |
| Estimated remaining after scheduled aid | $4,401.55 |
| Additional offered Need Grant / unsubsidized loan | $3,250 / $1,000 |
| Estimated remaining if the Need Grant also disburses | $1,151.55 |
| Living expenses / expected resources / cushion | $2,830 / $3,975 / $1,145 |
| Full term attendance estimate: university + living | $24,930 |

The subsidized loan is $1,750 principal less a $18.55 fee. Stored synthetic terms
are 6.39% interest, 1.06% fee, and 120 months; these are demo terms, not a current
federal-rate claim. Employment authorization is $3,000 annually / $1,500 for fall,
requires a job, and pays the student rather than the university. Living resources
are $1,800 savings, $675 family support, and $1,500 expected earnings; expenses
are $600 books, $730 transportation, and $1,500 personal spending.

The proposed payment agreement is **not signed or enrolled**. Principal installments
are $1,100.39 on September 30, October 30, and November 30, and $1,100.38 on
December 15. A recorded, approved Student Accounts exception extends Ada’s fall
billing deadline and permits these proposed dates; it preserves the fee and first
installment requirements. This scoped exception explains the difference from the
general institutional billing calendar. A separate $45 enrollment fee brings its total to $4,446.55.
The open grant and loan decision deadlines are September 30. Scheduled scholarship
and loan dates are September 15 and September 23. Annual and fall/spring award
allocations reconcile, including Pell’s unequal semester amounts and the community
scholarship’s one-time fall award.

Migration `0080_financial_award_terms.sql` stores explicit term gross offers,
acceptances, net amounts, deadlines and notes with tenant RLS and foreign keys.
Existing ledger, payment, fund, award, disbursement, insurance, installment,
loan-terms and personal-budget tables hold the rest. No per-student frontend or
Edward answer branches were added.

Apply migrations and run the guarded local fixture:

```sh
DATABASE_URL="$LOCAL_UNIVERSITY_DATABASE_URL" npm run db:migrate
apps/api/.venv/bin/python tools/university/seed_ada_financials.py \
  --database-url "$LOCAL_UNIVERSITY_DATABASE_URL"
```

The seed changes only Ada’s financial package, adds required fund/loan metadata,
and preserves subsequent edits on replay. It intentionally replaces her generated
full-settlement amount with a $2,500 family payment. It does not initiate a payment.

## Backend versus illustrative content

Backend-backed: all account balances, charges, awards, loans, disbursements,
payment history, proposed installments, deadlines, adviser, required insurance,
housing/meal prices, saved budgets, derived totals, and simulator results.

Frontend-only: campus illustrations and optional GradGuard/AKKO product examples
($298/semester, $12/month, $15/month). They are labeled illustrative, remain outside
account totals, cannot create coverage, and are not sent to Edward. Chart geometry,
monthly averaging and a split-payment calculator are presentations of backend
amounts, not additional financial records.

Award acceptance, payment collection, plan enrollment, housing changes and insurance
waivers remain office-mediated. Buttons provide details, comparisons, or a drafted
question for the real Edward assistant; they never fabricate a successful action.

## Edward and validation

Edward retains the existing authenticated university tools. Financial evidence now
keeps complete term awards, payment history, loan fees and deadlines within a
financial-specific size budget. Deterministic aid subtotals, account-gap, fee,
agreement-total and catalog-comparison calculations support grounded answers without relaxing the
number guard. The service also describes available financial actions, so a general
policy mentioning online enrollment does not imply this view implements it.
A narrow product-name fix prevents “Need Grant is accepted” being mistaken for
another student’s name; actual named-student privacy checks remain covered.

Browser testing uncovered and fixed an existing budget-save authorization bug:
authenticated browser actors use person IDs, not student-record IDs. The command
now verifies the owning database row and still rejects staff, delegates and
unrelated actors.

Validation includes an isolated PostgreSQL scenario, seed replay, annual/term
reconciliation, complete model evidence, ownership denial, unchanged ledger after
budget edits, desktop/mobile browser checks, save/reload, keyboard chart details,
all four routes, annual aid view, and server simulation. The live model bank covers
12 questions: balances, charges, payments, aid, loans, deadlines, installments,
living budget, attendance, grant effects, employment, and housing comparisons.
Responses were reviewed against the database; a further enrollment follow-up and
browser-to-Edward check verify the office handoff. Provider responses and screenshots
stay in ignored `portals/artifacts/financials/`.

Reproduce focused checks from platform:

```sh
AUDENTRA_FINANCIAL_TEST_DATABASE_URL="$ISOLATED_FINANCIAL_DATABASE_URL" \
  uv run --directory apps/api --locked pytest --no-cov -q \
  tests/test_financial_plan.py tests/test_edward_safety.py \
  tests/test_demo_financials.py tests/test_assistant_read_loop.py
```

From portals, with an isolated seeded API on port 45639:

```sh
FINANCIAL_TEST_API=http://127.0.0.1:45639 node tools/university-explorer/financials-e2e.mjs
node tools/university-explorer/financials-edward.mjs
node tools/university-explorer/financials-edward-browser.mjs
```

The two Edward harnesses use the real local demo API and incur provider calls.
The mutation browser suite requires its separate disposable financial database.
No deployment or push was performed.

Final validation results (September 16, 2026):

- Focused financial, safety, evidence and isolated PostgreSQL suite: **43 passed**.
- Full API suite: **1,448 passed, 164 skipped**; its existing global coverage gate
  remains unmet (**62.28% versus 67% required**). Unconfigured integration services
  account for skips. No coverage threshold was lowered.
- Platform lint/typecheck and Node workspace tests passed.
- Portal typecheck, lint (existing warnings only), 141 tests and build passed.
- Isolated browser suite and real page-to-Edward provider smoke passed. All 12
  question types produced backend-grounded model answers; the enrollment follow-up
  confirmed the proposed/unsigned state and Student Accounts handoff.
