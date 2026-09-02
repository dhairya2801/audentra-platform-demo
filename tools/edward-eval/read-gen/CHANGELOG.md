# read-gen changelog

Every change to a case after the bank was written goes here, with the reason.
Cases are never edited to make a run pass; only a demonstrably wrong *data*
expectation (one that contradicts ground-truth.json) may be corrected.

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
