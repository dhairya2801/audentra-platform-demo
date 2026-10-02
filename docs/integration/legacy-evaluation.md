# Legacy evaluation fixtures in the isolated integration

The interactive app remains the 3,000-student PostgreSQL university. Legacy banks use separate copies of their documented 2,577-student snapshot. They never point at a source database while exercising the API. Capture hashes, exclusions and source metadata are in the workspace `provenance/legacy-evaluation-sources.json`; raw snapshots, traces and reports are ignored artifacts.

## Schema and source boundaries

The original frozen snapshot was captured read-only from loopback port 5433 and restored as `audentra_university_test_vnext_legacy` on the integration's port 55591. Its migration checksum for `0050_edward_write_v1.sql` differs from this branch. The migration checker rejected it correctly. No checksum was changed and no original migration was replayed over a conflicting object.

`tools/university/import_legacy_eval.py` copies shared public columns into a newly migrated, empty current schema. It accepts only loopback legacy test names on port 55591, reads its source in a repeatable-read/read-only transaction and refuses a populated destination. It copies 232,754 rows, then re-creates and validates 491 foreign keys before commit. A failure rolls back the target transaction. The source migration table is excluded, and current migration history remains authoritative. This is a fixture import, not an application upgrade or deployment path.

The import report lists excluded tables: external Morning Brew context, staff student notes, application snapshots and risk assessments. Several newer requirement presentation columns have no matching target column and are explicitly excluded. No absent domain is represented as a verified empty result. Current university-v3 metadata is not imported, so this fixture remains in legacy product mode rather than pretending to be the integrated institution.

An additional two-table Explorer oracle was captured read-only from loopback port 5439 into `audentra_university_test_vnext_legacy_oracle`. It holds the 89 original staff archetypes and 8,206 assignment rows used by two old tests. It is not live Atlas data or Edward evidence.

## Running the banks

Start only one paid evaluation process at a time. `run_evaluation_runtime.py` uses the normal API, GPT-5.6 Luna and the model read planner, with the existing persistent spend ledger and process lock. Model calls are serialized for consistent reservations. It starts no worker. `OPENAI_API_KEY` stays in the environment, outside Git.

```bash
PYTHONPATH=apps/api/src apps/api/.venv/bin/python tools/university/run_evaluation_runtime.py \
  --database-url postgresql://YOUR_USER@127.0.0.1:55591/audentra_university_test_vnext_legacy_current \
  --port 45639
```

Generate SQL truth from that same database with `tools/edward-eval/read-gen/ground_truth.py`. Run matched and holdout banks with explicit `READ_GEN_BASE_URL=http://127.0.0.1:45639`, `READ_GEN_GROUND_TRUTH=<captured-json>` and `OPENAI_MODEL=gpt-5.6-luna`. Missing time-relative question templates produce a reported skip without an assistant request. A regression test exercises the runner and transcript output. Apostrophes are normalized on both sides of required/forbidden matching; facts, permissions, case banks and dates are unchanged. Original results and separate punctuation regrades are retained.

Write banks use fresh independent databases `audentra_university_test_vnext_legacy_write` and `audentra_university_test_vnext_legacy_write_holdout`, prepared from the current-schema fixture and the existing `tools/edward-eval/write/fixture.sql`. Its mailbox and recipient rows are explicitly synthetic evaluation fixtures. No external message sender runs.

Point the evaluation host at the selected writable copy, then set all of:

```bash
WRITE_EVAL_BASE_URL=http://127.0.0.1:45639 \
WRITE_EVAL_PSQL='artifacts/university-runtime/pg17/bin/psql -h 127.0.0.1 -p 55591 -d {db}' \
WRITE_EVAL_DB=audentra_university_test_vnext_legacy_write \
OPENAI_MODEL=gpt-5.6-luna \
node tools/edward-eval/write/run.mjs --no-reset --batch <new-batch>
```

For holdouts use the separate holdout database and add `--holdout`. Never run the inherited Docker reset script against an existing database. Do not reuse a mutated write database as a fresh acceptance fixture.

## Results and their limits

- Write development: **118/118 pass**, 148 turns.
- Write holdout: **38/38 pass**, 47 turns.
- Read matched, original grading: **89 pass / 6 partial / 23 fail / 10 skip**, 128 cases/145 turns.
- Read matched, punctuation-only regrade: **92 / 6 / 20 / 10**.
- Read holdout: **18 pass / 1 partial / 9 fail / 1 skip**, 29 cases/33 turns; punctuation normalization leaves these totals unchanged.
- Source-baseline replay of the 23 original matched failures: 2 pass/21 fail before punctuation normalization, 5 pass/18 fail after it. The 9 holdout failures replayed as 2 pass/7 fail. These are targeted baseline comparisons, not a full baseline bank score.
- Nineteen of twenty old backend/Explorer regressions pass in the restored fixtures. The remaining test expects old time-dependent capacity/archetype signals. Morning Brew's deployed demo UI remains unchanged and out of canonicalization scope.
- Independent deterministic world and isolation tests: 19 pass. Evaluation harness: 38 pass.

The read failures are not all regressions and not all harmless. Examples from manual review:

| Concern | Evidence and disposition |
|---|---|
| Private peer records | An initial answer attached Bruno's immunization status to Camila's name; tools had read only Bruno. The student read context now explicitly rejects peer-record/caseload requests and forbids attributing self-only facts to another named person. Live corrected replies refuse those requests. The old regex can still label a refusal that repeats a question as a disclosure; that raw failure is retained. |
| Historical questions classified as changes | “Did anything change on my checklist recently?” reached the mutation refusal. The informational frame now handles past-tense questions while preserving a later explicit action request. Unit routing checks and the live holdout now pass. |
| Staff/student name ambiguity | “Which admissions counselor has Greta Everlyn?” selected the staff namesake in one integration run and the student in the baseline replay, despite identical extracted entities. This remains a semantic routing concern, not a proven deterministic integration regression. |
| Legacy financial deadlines | Legacy deposit derivation can use the offer-response deadline rather than the student's deposit requirement deadline. For example, the snapshot has January 6 for Omar and July 22 for Petra, while replies said September 1. Both baseline and integration reproduce it. The integrated university mode excludes those legacy financial interpretations and uses the shared canonical projections; this old-mode limitation is not declared fixed. |
| Past versus future appointments | Some legacy appointments have expired. Missing templates are skipped; “next appointment on record” phrasing can still be misleading when only a past appointment exists. |
| Negation and wording | “Nothing has been sent” can match the old success regex, “paid, not … outstanding” can match unpaid, and adviser-leave dates can be mistaken for invented appointment dates. “No primary academic adviser” and “not listed” can miss narrow required phrases. These scores are retained with their actual answers, not silently waived. |
| Obsolete unavailability/action assumptions | The fixture has a canonical GPA, while an old question expects GPA to be unavailable. Some old read-bank action expectations conflict with actual capability grants or current confirmation/clarification behavior. The full write banks independently pass against database effects. |
| Completeness and ordering | Some answers omit a required individual owner/count, use due order for “oldest,” or answer a prediction request with a broad status summary. These remain review concerns. |

Final integrated-university verification after the fixes again exercised all 30 oracle questions as both student and staff: **57 Luna responses and 3 expected guided boundary/identity responses**, no runtime errors and no trace failure codes. This proves continuity of the actual integrated path, not a perfect semantic score for every legacy wording.
