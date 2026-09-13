# read-gen changelog

Every change to a case after the bank was written goes here, with the reason.
Cases are never edited to make a run pass; only a demonstrably wrong *data*
expectation (one that contradicts ground-truth.json) may be corrected.

## 2026-09-13 integration preflight

- Truth extraction now distinguishes an unavailable assessed-risk schema from
  a verified zero-row count (`riskAssessmentAvailable=false`, count `null`).
  The original frozen fixture still receives its actual SQL count.
- No case or expected fact changed. The new university's matched-bank template
  preflight has 155 problems across 128 cases/145 turns; holdout has 31 problems
  across 29 cases/33 turns. Old staff/persona references need an explicit fixture
  reconciliation before a valid provider run. These are preflight failures,
  not model scores.

## 2026-09-02

- Bank written (dev + holdout) against the frozen `vv_enrollment_staffdb_eval`
  snapshot of tenant `00000000-0000-7000-8000-000000000003`, before any case
  was run against Edward. Ground truth generated from SQL only.
- Smoke run of four cases (`rg-adv-001`, `rg-doc-002`, `rg-act-001`,
  `rg-mt-006`, batch `rg-smoke`) to prove the runner end to end. No case was
  edited afterwards. Observed: `rg-adv-001` ("who's my adviser") FAILed —
  the deterministic planner never called `getStudentAdvising` and Edward
  answered that it could not find the adviser (failure class `tool`). Left
  as is: that is a finding, not a bank defect.
- Runner: `--regrade` grades only the cases the stored batch recorded
  (a subset batch no longer fails the rest as missing).
