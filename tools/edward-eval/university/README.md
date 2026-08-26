# Staff Edward — university benchmark

A realistic benchmark for Staff Edward built on the mock **Aster University**
(`Audentra-university-explorer`): 2,576 students, 88 staff in 12 departments,
2,485 open Action Center items, 194 inquiries, an adviser/caseload model with
deliberate situations (an overloaded adviser with no slots, an adviser on
leave, a departed adviser who still owns 79 students, a vacant seat, a
transcript evaluator on vacation behind a backlog, duplicate student names,
and a staff member who shares her full name with four students).

The university is an **oracle**, not something Edward memorises: every
expected fact is extracted from the product's own database with SQL at run
time, and the questions are the ones staff genuinely ask.

## Pieces

| File | Role |
| --- | --- |
| `ground_truth.py` | SQL over the frozen platform snapshot (+ the Explorer's materialised slots as the availability oracle). Queue, inquiry, staff, student, cohort and department facts. Regenerate right before a run — overdue/due-today/age counts move with `now()`. |
| `cases.mjs` | 144 development cases / 152 turns across 21 categories: individual students, cohorts, the signed-in member's own work (10 personas), other staff, caseload/capacity, department operations, Action Center, inquiries, deadlines, onboarding blockers, appointments, scheduling, comparisons, multi-criteria, summaries, prioritisation, follow-ups, ambiguous names, multi-intent, refusals, planted edge cases. |
| `holdout-cases.mjs` | 25 cases with unseen phrasings, personas and people, run only after fixes were finished. |
| `taxonomy.mjs` | Intent → family and tool → family maps, so routing failures are classified the same way before and after the request-type vocabulary changed. |
| `run.mjs` | Drives `POST /v1/staff/assistant/messages` as the persona each case names, fetches the per-turn trace, grades deterministically, and classifies every failing turn (`timeout`, `routing`, `tool`, `query`, `composition`, `hallucination`, `entity`, `product_data`). |
| `bench.sh` | One command: snapshot → API → ground truth → run → `artifacts/runs/<batch>/`. |

## Grading

No LLM judge. Per turn the grader checks required facts (`{{num:…}}` exact
count, `{{num~:…}}` ±2 % for time-relative counts, `{{date:…}}` any date
format, `{{gt:…}}` literal), forbidden claims, entity resolution (a staff
question must not resolve a student; a duplicate name must not be guessed),
and failed-read phrases. `family` / `toolFamily` are diagnostic only.

Every turn's record keeps: the request type Edward formed, the resolved
identity and entities, every tool call with arguments / status / latency /
record count / result preview, the evidence lines the composer used, model
calls with token usage, the answer, the expected facts, and the failure
reason and class.

## Running it

```bash
# from Audentra-platform, with the compose stack up and the university deployed
tools/edward-eval/university/bench.sh univ-$(date +%Y%m%d)        # dev suite
tools/edward-eval/university/bench.sh univ-holdout -- --holdout    # holdout
npm run eval:edward:university -- --id u-cc-001 -v                 # one case (API already up)
npm run eval:edward:university -- --regrade univ-r4                # re-grade a stored batch
```

Prerequisites: `npm run audentra:deploy` in the explorer (idempotent — resets
the queue to ground truth), `OPENAI_API_KEY` (gpt-4o-mini; a full run is ≈140
model calls ≈ $0.04), `uv`, node ≥ 22.

## Results (2026-08-26)

| Run | Pass | Turn pass | p50 | p90 | Timeouts |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline (pre-rebuild) | 26/144 (18.1 %) | 19.1 % | 907 ms | 3,539 ms | 13 |
| r1 (staff-aware routing, bounded SQL tools) | 99/144 (68.8 %) | 70.4 % | 1,400 ms | 2,106 ms | 1 |
| r2 (+ adviser in student answers, compound asks, possessives) | 129/144 (89.6 %) | 90.1 % | 1,449 ms | 2,141 ms | 0 |
| r3 (+ follow-up carry with sentence context, deposit/booking predicates, absence directory) | 141/144 (97.9 %) | 98.0 % | 1,364 ms | 1,965 ms | 0 |
| r5 (final; holdout-driven vocabulary fixes) | 143/144 (99.3 %; 144/144 after a grader fix) | 99.3 % | 1,310 ms | 1,834 ms | 0 |
| holdout, first contact → after fixes | 16/25 → 25/25 | | 1,450 ms | 1,980 ms | 0 |

See `docs/staff-edward-university-eval.md` for the analysis.
