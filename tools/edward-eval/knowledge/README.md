# Edward — institutional-knowledge suite

Questions that need **both** a person's record and the approved institutional
corpus: "what happens if I miss this deadline?", "does this rule apply to
me?", "who handles this for my situation?", "what's the SLA for…?". The
corpus is the tenant's `knowledge/` directory (policies, procedures, handbook,
calendar, offices) imported into PostgreSQL; the personas are real rows in the
synthetic university (`vv_enrollment_synthu`).

| File | Role |
| --- | --- |
| `personas.mjs` | 15 students and 10 staff chosen for a situation an institutional question turns on (international, transfer, Spring admit, adviser on leave, rejected document, overdue deposit, SAP probation…). |
| `ground_truth.py` | SQL over the database for each persona (residency, standing, admit term, deposit state and due date, overdue items, documents, adviser, awards, SAP) plus the corpus constants (calendar dates, office details, amounts) read from the packaged files. Regenerate right before a run. |
| `cases.mjs` | 59 development cases / 62 turns across deadline consequences, applies-to-me, record-plus-policy, office routing, calendar, honesty, staff procedures and multi-turn. |
| `holdout-cases.mjs` | 18 cases with unseen phrasings and personas, run only after development-bank fixes. |
| `run.mjs` | The read-gen runner pointed at this bank (same grading, lab headers, artifacts). |

```bash
# host on :45731 bound to vv_enrollment_synthu (see the session launcher), then:
uv run --directory apps/api python ../../tools/edward-eval/knowledge/ground_truth.py \
  --database postgresql://vv:vv_local_password@127.0.0.1:5433/vv_enrollment_synthu
node tools/edward-eval/knowledge/run.mjs --lint
node tools/edward-eval/knowledge/run.mjs --batch kn-dev-$(date +%Y%m%d)
node tools/edward-eval/knowledge/run.mjs --holdout --batch kn-holdout-$(date +%Y%m%d)
node tools/edward-eval/knowledge/run.mjs --ids kn-dep-001 -v          # one case
node tools/edward-eval/summarize-batches.mjs 'kn-*'                    # compare batches
```

Grading is deterministic (no judge): required facts (`{{gt:…}}` literals,
`{{date:…}}` any rendering of a calendar date, `{{num:…}}`), forbidden claims
(invented phone numbers, foreign email domains, refusals, "I've done it"
claims, the wrong term's dates, eligibility a policy denies), entity
resolution, and the reads that must have happened (`getInstitutionalPolicies`
/ `searchInstitutionalKnowledge`). Results and the analysis are in
`docs/synthetic-university-v2.md`.
