# Edward experience evaluation

Read [the report](../../../docs/edward-experience/REPORT.md) and
[results](../../../docs/edward-experience/RESULTS.md) for setup, commands, judgments,
limitations, budget accounting, and the role-complete comparison manifest.

- `serve.py`: production request path on an isolated DB, with a locked API meter
  and evaluation-only read/write faults. Never expose this wrapper publicly.
- `run.py`: actual HTTP conversations; default 88-turn bank, `--holdout`, `--bank`,
  `--role`, or `--ids`. Asserts requested staff identity against real traces.
- `holdouts.json`, `mutations.json`, `unseen.json`: authored scenario inputs.
- `boundaries.py`, `write_fault.py`: real confirmation/isolation/failure checks.
- `assemble.py`: combines complete student/staff runs; refuses missing/duplicate
  baseline turns. Selection is by actor role, not response quality.
- `analyze.py`: deterministic telemetry plus complete per-turn review aggregation.
  It does not certify factual correctness from keywords or guard acceptance.
- `review-*.json`, `*-summary.json`: committed judgments and aggregates.
- `peer_review.py`: optional different-family review through a verified free
  OpenRouter model. No valid independent score was obtained in this task.

Raw transcripts/traces remain in ignored `platform/artifacts/edward-experience/`.
Use a new batch name for each run and retain the shared spend ledger. The report
contains selected fictional before/after transcripts and browser screenshots.
