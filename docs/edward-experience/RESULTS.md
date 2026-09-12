# Evaluation results and limitations

Read alongside [the implementation report](REPORT.md). These are engineering
judgments on synthetic records, not an independent human usability study.
A successful turn answers its requested job or enforces the appropriate boundary;
it does not mean the university case was closed. Partial outcomes are **not**
counted as successes.

## Matched before/after

The matched bank has 88 turns: 50 student and 38 staff. The final staff-only tool
changes were followed by a rerun of **every staff case**, not selected failures.
The comparison combines student `completion` and staff `staff-completion`; the
[manifest](comparison-manifest.json) records this selection. Every original
case/turn appears exactly once. No best-of response selection is used.

| Metric | Original | Matched after |
|---|---:|---:|
| Successful turns | 59/88 (67.0%) | 76/88 (86.4%) |
| Partial turns | 11 | 10 |
| Failed turns | 18 | 2 |
| Student successes | 34/50 | 44/50 |
| Staff successes | 25/38 | 32/38 |
| Median primary-answer words | 54.5 | 21 |
| Median total conversational prose words | 54.5 | 58 |
| Median HTTP latency | 3,732 ms | 4,602.5 ms |
| p90 HTTP latency | 5,249 ms | 6,893 ms |
| Mean traced model calls per turn | 1.73 | 2.00 |
| Tool reads, including entity lookups | 195 | 239 |
| Median tool reads per turn | 2 | 2 |
| Conservative API cost for matched turns | $0.185638 | $0.243882 |
| HTTP 200 / trace present | 88/88 | 88/88 |
| No unconfirmed write receipt in message turns | 88/88 | 88/88 |

The primary answer is about 61% shorter; the complete prose is **not** shorter.
Details are disclosed progressively. More complete reads improved usefulness
but increased latency and cost. Provider variance, local contention and concurrent
holdout work mean the timing difference is observational, not a controlled speed
measurement. The semantic projection itself adds no model call.

Two baseline annotations were corrected during the final audit: the department
actually had one overdue item, despite a zero returned by the incorrectly scoped
query; and a threshold answer gave current state without establishing the policy.
Both before and after use the same criterion. The original transcripts were not
changed or rerun. All annotations are in
[review-baseline.json](../../tools/edward-eval/experience/review-baseline.json) and
[review-matched-after.json](../../tools/edward-eval/experience/review-matched-after.json).

## What improved, and what did not

| Dimension | Evidence and practical limit |
|---|---|
| Correctness and record use | Better distinction between submissions and office review; exact held/posting totals; correct adviser coverage and department scope. A numeric guard still misses false causal statements. |
| Task completion | 17 more matched turns succeeded. End-to-end office review, hold release and housing placement remain outside Edward's authority. |
| Turns to useful resolution | Explicit student/parent questions now answer in one turn instead of asking for identity already supplied. Contact follow-ups no longer automatically divert into support creation. A corrected transcript reason still sometimes takes an extra turn. |
| Unnecessary questions | Identity-loss and negated-send failures were reduced. The remaining ambiguous multi-student question asks for internal handles, which users cannot reasonably provide. Raw question-mark counts are not used as a quality metric. |
| Tool selection | More canonical reads and fewer missing current account facts. Some transcript answers still read requirements/academics without reading the document rejection reason. Entity lookups before obvious staff refusals remain wasteful. |
| Action accuracy | Real preview, version/hash rejection, actor isolation, idempotent confirmation and failed-write receipt checks passed. A requested custom follow-up title was not honored; the canonical preview showed the actual generated title. |
| Privacy and authorization | No cross-user/tenant disclosure or unconfirmed write was observed in the exercised cases. This is evidence for these cases, not a proof of all possible attacks. |
| Policy grounding | Versioned, applicable passages are inspectable. Some threshold answers omit the policy read; retrieval can remain incomplete or include irrelevant procedure detail. |
| Continuity | Payment state survives an eight-turn conversation and corrections change the selected student. A contact follow-up still sometimes chooses generic support instead of rereading the previously verified office directory. |
| Trustworthiness | Record provenance and unknown availability are visible. Remaining failures include a plausible but unsupported dependency in a staff draft. Guard acceptance must not be treated as certification. |
| UX and clarity | Desktop/mobile checks show the complete primary answer before scrolling; sources/details no longer bury it. Confirmed changes display the actual saved value from the receipt. Human trust/perceived understanding were not measured. |
| Personalization | Student suggestions omit completed requirements. Staff's already-read workload informs context. A director's broad “attention today” request can still receive an overly narrow personal-queue answer. |

The two matched failures are retained explicitly: the aid-impact follow-up falls
back instead of answering, and a recipient-facing staff draft invents a dependency
between orientation reconciliation and aid authorization. The presence of a draft
component does not make its content correct.

## Holdouts, mutations and final falsification

| Set | Turns | Success | Partial | Failure |
|---|---:|---:|---:|---:|
| Existing university holdout | 19 | 18 | 1 | 0 |
| New holdout bank, final rerun | 31 | 24 | 5 | 2 |
| Mutation bank, final rerun | 8 | 7 | 1 | 0 |
| Final unseen journeys | 6 | 5 | 1 | 0 |
| Final email-guidance regression | 8 | 8 | 0 | 0 |
| Final comparison-limit explanation | 1 | 0 | 0 | 1 |

These sets are reported separately, not pooled into an inflated score. Holdouts
became regression cases once they exposed a failure. The six unseen turns were
authored after the main fixes and include parent-payer pressure, urgent refund
pressure, switching from a student case to all-owner department totals, and an
approved accommodation with a cancelled old room.

Important residual holdout failures and partials:

- Multi-student comparison cannot resolve two independent student handles safely.
- The personal queue's **primary** count and priority are correct, but one secondary
  department list still mixes institution-wide facets into personal work.
- A transcript correction is recognized only after another question causes the
  document read.
- Some implicit drafts ignore a requested two-sentence limit.
- A correct refusal to promise a refund does not solve the student's urgent rent
  problem; suggesting contact “tonight” without office availability is weak help.
- One long-thread answer turned an office's execution procedure into a student's
  email checklist, requesting authorization/version/idempotency details.

The last issue motivated one final student-facing instruction: distinguish office
execution requirements from what a student should write in an email. The complete
eight-turn journey was rerun: it retained the direct office contact, provided a
usable short email with student/payment facts, and requested no transaction
parameters. That final prompt adjustment and the receipt-display polish occurred
**after** the matched run; the eight-turn result and browser receipt check are
reported separately, without replacing the earlier failed holdout answer. A final
staff context instruction also replaces the request for internal handles with a
plain explanation of the one-student limit. Its targeted comparison still counts
as a task failure, because clearer wording does not add the missing capability.

The first mutation attempt accidentally used the default staff actor for the two
nondefault staff journeys. Those four turns were excluded as invalid tests, retained
locally, and rerun with explicit actor IDs. The runner now asserts tenant and staff
identity against the actual trace. This is a harness correction, not an Edward fix.

Evaluation stopped with known limitations, not a claim of 100%. Fresh cases mostly
confirmed existing failure classes or exposed limited presentation/tool-selection
errors. Repeated full-bank runs would mainly measure sampling variance. The next
large capability gains require the focused work listed in the report, plus real
student/staff usability research.

## Deterministic, failure and browser checks

- **81/81** record components in the matched run carry tool provenance.
- **58/58** next-action blocks have no model-authored URL.
- **11/11** live action-boundary checks passed: foreign conversations/intents,
  stale version, altered preview hash, exactly-once confirmation, and restoration.
- **4/4** injected write-failure checks passed: failure response, durable failed
  receipt, no profile change, and no execution on retry of the failed intent.
  The fault is before mutation; this is not a power-loss/commit-recovery test.
- Four injected read-error turns retained honest unavailability or a bounded
  partial answer. Two empty-record turns returned guard fallbacks rather than
  inventing a successful payment or zero balance. Safe fallback still fails the
  original information job.
- Four real browser configurations—student/staff at 1440×1000 and 390×844—had no
  horizontal overflow, visible primary answers, no runtime errors, and zero axe
  WCAG 2 A/AA or 2.1 AA violations inside the Edward dialog.
- Keyboard/Escape focus return, reopening, sources, stop/idempotent retry,
  preview/cancellation, confirmed profile change/restoration, real staff draft,
  mailbox-unavailable error and Lab semantic inspection were exercised.

See [browser evidence](browser-evidence.json), [action checks](action-checks.json),
[the screenshots](screenshots/README.md), and the committed per-turn review files.
A free independent model review was attempted, but no valid independent score was
obtained; malformed NVIDIA output and a rate-limited Google Gemma request are not
counted as judging evidence.

## API spend

Total OpenAI usage estimate: **$2.250589**, under the **$10** authorization.
This includes discovery, unsuccessful iterations, regressions, holdouts, and
browser conversations: **1,651 metered model requests**, 10,147,703 prompt tokens
and 184,207 completion tokens. No unsettled reservations remain.

The meter charges cached input at the full $0.20/million input rate and output at
$1.20/million, making this conservative rather than an invoice reconciliation.
Rates were checked against the official
[GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna).
The failed/free independent review attempts cost $0. See
[spend-summary.json](spend-summary.json). Raw usage entries stay in the ignored
local `platform/artifacts/edward-experience/spend.json` ledger.

## Verification commands

From `platform`, after migrating and seeding the two isolated test databases:

```bash
npm run lint
npm run typecheck
AUDENTRA_TEST_DATABASE_URL=postgresql://dhairya2801@127.0.0.1:55487/audentra_edward_integration AUDENTRA_UNIVERSITY_TEST_DATABASE_URL=postgresql://dhairya2801@127.0.0.1:55487/audentra_university_test_edward OPENAI_API_KEY= OPENROUTER_API_KEY= npm test
```

From `portals`:

```bash
npm run lint
npm run typecheck
NEXT_PUBLIC_EDWARD_DEBUG_ENABLED=false npm test
NEXT_PUBLIC_EDWARD_DEBUG_ENABLED=false npm run build
```

The explicit production debug flag is necessary because the local `.env.local`
enables Edward Lab for browser inspection. The production exposure test correctly
rejects builds with that local debugging flag enabled.

The first full backend test run without database configuration failed the coverage
threshold. The first isolated integration run then found an unseeded authentication
fixture; seeding the disposable database made its focused retest pass. An existing
university integration test also selected a random financial-aid requirement that
might have no verification documents; the fixture now explicitly selects a case
with the two required documents, in stable order. Those unsuccessful runs are
preserved locally rather than omitted.

Final verification completed:

| Check | Result |
|---|---|
| Platform lint and typecheck | Passed |
| Full backend API suite with isolated seeded databases | 1,524 passed, 26 skipped; 72.79% coverage, above the 67% gate |
| Platform Node workspace tests | Passed, including contracts, import/archive and voice suites |
| Latest focused assistant regression suite | 220 passed |
| Portal lint | Passed with 14 existing warnings and zero errors |
| Portal typecheck | Passed |
| Portal tests | 129 passed, zero failures |
| Portal production build | Passed with Edward Lab disabled |

The full backend run took 18m22s. Later scoped prompt/presentation adjustments
were checked with the focused regression suite and their real HTTP/browser
journeys rather than repeatedly rerunning unrelated integration tests.
