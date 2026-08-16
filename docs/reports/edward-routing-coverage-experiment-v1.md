# Edward routing-coverage experiment v1

**Session 4 — Student Edward, platform only. Experimental, opt-in, not merged.**

- Baseline SHA: `a062592f93ab2a290d2603e4614006aa9b5ba7ab` (branch `experiment/edward-routing-coverage`)
- Experiment commit: `55a7493` (`feat(assistant): add experimental full-request coverage gate (opt-in)`)
- Production default routing is byte-identical to baseline (tested).

## 1. The routing architecture as it actually ships

Verified at the baseline SHA — the preflight description survived integration intact:

```
normalize_request → classify (deterministic, priority-ordered branches)
   ├─ Classification returned → select_tool_reads(primary + additional types)
   └─ None → model planner (if configured) → validate_model_tool_plan
                └─ still None → safe_fallback (general_question: checklist/holds/deadlines)
→ execute reads → derive → one bounded dependency read round (open gate codes)
→ compose_deterministic (primary composer + one section per additional type)
→ optional model rewrite, claim-guarded
```

Key facts that define the failure surface:

- `classify()` (`apps/api/src/audentra/integrations/assistant/classify.py`) walks priority-ordered
  domain branches; **the first matching branch wins** and returns confidence ~1.
- Multi-domain detection exists but **only fires when an aggregation operator is present**
  (`_MULTI_DOMAIN_OPERATOR`: "summarize", "both", "everything", "across", …) alongside ≥2
  `_DOMAIN_HINTS` matches.
- Two deliberate escape hatches exist: the housing+aid pair returns `None` (planner), and a few
  branches attach a second intent via `additional_request_types` (e.g. `housing_eligibility` +
  `holds_and_blockers`).
- **If `classify()` returns any Classification, the model planner never runs.** There is no
  post-classification check that the route accounts for the whole request.

## 2. Concrete incomplete-routing cases at baseline

Confirmed empirically by running the pipeline (in-memory host, `new_admit`-style fixtures):

| request | baseline route | what disappears |
|---|---|---|
| "I paid my deposit. Why can't I apply for housing and what documents am I still missing?" | `housing_eligibility` + `holds_and_blockers` | the **documents ask** — `getDocumentStatuses` is never planned; the answer never says where documents stand |
| "What's my account balance and what clubs can I join?" | `campus_life` | the **balance ask** — account is never read; answer: "Campus life currently lists 1 club(s)…" and nothing else |
| "How much do I owe and are my documents all in?" | `document_status` | the **balance ask** |
| "What's my balance, what clubs can I join, and did my transcript arrive?" | `document_status` | **balance and clubs** — two of three asks |
| "Do I have any holds and what clubs can I join?" | `holds_and_blockers` | the **clubs ask** |
| "What documents am I missing and when are they due?" | `missing_documents` | the **deadline ask** ("when are they due" matches no deadline hint) |
| "Show my housing and financial aid status." (deterministic-only mode, no planner) | safe fallback `general_question` | **both named domains** — generic checklist answer |

This is exactly the postulated failure mode, and it is worse than a miss: the answer is correct,
confident, and silently incomplete. A caveat discovered during measurement: on personas where a
housing gate happens to name the transcript, the **dependency read round masks the tool gap**
(documents get read to explain the gate) — but the composition still never answers the documents
question, and on personas without that gate nothing is read at all. Coverage is a routing/composition
property; tool receipts alone under-measure it.

## 3. Root cause

`classify()` answers "which single intent fits best?" — it has no notion of "did I account for
everything that was asked?". Completeness is only handled where it was hand-enumerated (operator
phrases, the housing+aid pair, a few `additional_request_types` attachments). Any compound question
outside those enumerations collapses to its first matching branch at full confidence, and the
confident result suppresses the planner that could have caught it.

## 4. Architectures considered

| option | assessment |
|---|---|
| **A. Deterministic multi-domain detector** (drop the operator requirement) | Right idea, wrong granularity: domain *mentions* are not domain *asks*. "I paid my deposit. Why can't I apply for housing?" mentions the account domain but asks only about housing — naive firing widens correct fast paths and bloats answers. |
| **B. Classification + coverage validation** | The chosen frame: keep `classify()` untouched, make "does the route cover the request?" a separately checkable property. Needs A's vocabulary plus an ask/context distinction. |
| **C. Lightweight LLM gate** ("can the deterministic route fully satisfy this?") | Dominated: it costs a model call on the doubtful turns, and a yes/no still leaves you needing a route. If a model is worth calling, the existing planner returns the full corrected route for the same call. Evaluated via the planner mode below. |
| **D. Structured intent extraction** | Adopted in deterministic form: split the message into interrogative/imperative segments and re-classify uncovered segments individually — the classifier itself is the extractor, no model needed. |
| **E. Always use planner for multi-domain questions** | Over-escalates on every context mention ("I paid my deposit. Why can't I…" names two domains but has one ask) and on compounds deterministic routing already covers (registration+aid, housing+holds). Adds cost and latency to turns that were already right. |
| **F. Coverage gate = B + D, with C/E as a measured variant** | Implemented. |

## 5. Chosen design: the coverage gate

`apps/api/src/audentra/integrations/assistant/coverage.py`, hooked into
`pipeline.py`. Runs only when a mode is opted in.

**Detection (deterministic, both modes):**

1. Segment the message on sentence boundaries and on coordinated question clauses
   ("…and what/or is/and can…" — a conjunction followed by an interrogative opener; "housing and
   financial aid" stays one phrase).
2. A segment is an **ask** if it ends in "?" or opens interrogatively/imperatively. Declarative
   segments ("I paid my deposit.") are context. Concessive/causal clauses inside a question
   ("even though I paid") are stripped — the dependency round is what verifies asserted facts.
3. **Ask domains** = the classifier's own `_DOMAIN_HINTS` vocabulary matched over ask segments
   (plus a gate-local extension for phrasings the operator-era vocabulary lacks, e.g. "due" →
   deadlines). Shared vocabulary by import, so the gate and classifier cannot drift.
4. A domain is **covered** when `select_tool_reads(classification)` contains one of its answering
   tools. Uncovered ask domains = coverage gap. No gap → the gate is silent and the fast path is
   untouched.

**Gap handling:**

- `augment` mode: each uncovered segment is re-classified **on its own** — "what documents am I
  missing?" supplements as `missing_documents`, not a generic read — and appended to
  `additional_request_types` (capped at 2, the platform-wide bound shared with the operator path
  and the plan validator; overflow is recorded in the trace as dropped). `compose_deterministic`
  already renders one section per additional type, so composition follows routing with no composer
  changes. Zero model calls.
- `planner` mode: a gap escalates to the model planner; the validated plan is used only if it
  covers the gap, otherwise augmentation is the floor. Never falls through to the bare safe
  fallback.
- Safe-fallback widening (both modes): when `classify()` declines and no planner resolves it, the
  `general_question` fallback is augmented with the named ask domains — "show my housing and aid
  status" answers from both domains even with no model configured.

**Activation (isolated, non-production):** `AssistantPipeline(coverage_gate=...)` or the
`AUDENTRA_EXPERIMENTAL_COVERAGE_GATE` env flag (`augment`|`planner`). Default off; production call
sites pass nothing and set nothing; an explicit `"off"` beats the env. No HTTP/Lab mechanism was
added (Session 3 owns that); the env flag is how the eval host A/Bs over the real request path.
`compose.py` and `guard.py` (Session 2) untouched; Staff untouched.

## 6. Eval corpus and method

`tools/edward-eval/experiments/routing-coverage-v1/run_experiment.py` — 16 hand-labeled cases over
all prescribed categories (single simple/complex, multi with/without operator, natural compound,
unrelated multi-intent, implicit dependency, follow-up, ambiguous, direct status, unsupported, plus
compound variants), run through the real `AssistantPipeline` over canonical eval personas
(`new_admit`, `deposit_posted`), in three modes: `default`, `augment`, `planner`.

Grading is deterministic, per material ask, at three levels: **planned** (the route selects an
answering read), **executed** (an answering read ran, dependency round included), **answered** (the
final message states the asked-for facts). Two aggregates: **strict coverage** (planned ∧ answered —
persona-independent, the decision metric) and **experienced coverage** (executed ∧ answered).
Refusal case graded as refusal. `--provider` wires the real `StudentAIGateway` **planner only**
(composition stays deterministic so grading is stable) — the intentionally enabled paid part.

## 7. Results

**Deterministic (no provider — also the eval-smoke/outage posture):**

| mode | strict coverage | experienced | missed asks | gate fired | model calls | mean ms |
|---|---|---|---|---|---|---|
| default | 5/14 | 7/14 | 13 | 0 | 0 | 1.5 |
| **augment** | **13/14** | **14/14** | **1** | 9 | **0** | 2.2 |
| planner (no key → augments) | 13/14 | 14/14 | 1 | 9 | 0 | 2.4 |

**Provider-enabled (real planner, `gpt-4o-mini`):**

| mode | strict coverage | experienced | model calls | tokens | mean ms | p95 ms |
|---|---|---|---|---|---|---|
| default (+planner fallback) | 6/14 | 8/14 | 2 | 2,084 | 69 | — |
| **augment** | **13/14** | **14/14** | **2** | **2,084** | **63** | 5 |
| planner-gate | 13/14 | 13/14 | 9 | 10,469 | 326 | 1,082 |

Before/after on the headline case (deterministic composer, same persona):

> **default:** "Your housing step is already complete — nothing more is needed there. … What you do
> have is 2 item(s) blocking progress: Enrollment deposit not posted and Final transcript. …"
> *(documents question never answered as asked)*
>
> **augment:** same answer **plus** "Here's where your documents stand: Final transcript not
> submitted yet." — classification `housing_eligibility + [holds_and_blockers, missing_documents]`,
> `getDocumentStatuses` planned, zero model calls.

And the total-loss case: default answered "What's my account balance and what clubs can I join?"
with clubs only; augment answers "…Your remaining balance is $16,000. Your $500 enrollment deposit
has not been paid yet." alongside the clubs.

**Unnecessary escalation / fast-path safety:** the gate fired on 0 of the 7 single-ask,
context-mention, follow-up, ambiguous, and refusal cases; tests assert byte-identical answers and
tool selections vs default for all of them ("I paid my deposit. Why can't I apply for housing?"
and "Why can't I register even though I paid?" included). Measured unnecessary-escalation rate: 0.
Option E, for contrast, would have called a model on every such context mention.

**The one remaining strict miss** (`nc-1`, three asks) is the 2-supplement cap: aid and housing are
supplemented, documents is dropped (recorded in the trace) — and still answered on this persona via
the dependency round, hence experienced 14/14.

**Why planner-gate loses:** same strict coverage as augment, one case *worse* experienced coverage
(a model plan read less context than the deterministic fallback route it replaced), 4.5× the model
calls, 5× mean latency, p95 >1s. The extra model calls bought nothing the deterministic supplements
had not already bought. This also answers option C: an LLM coverage judgment costs what the planner
costs and delivers less.

**Existing suites after the change:** full `apps/api` pytest 749 passed / 47 env-skipped (Postgres
integration suites need the local DB; nothing in this change touches those paths — no migrations,
`relational.py` untouched); `npm run eval:edward:smoke` 45/45 deterministic passes, tool selection
100% clean; ruff and mypy clean.

## 8. Cost and latency implications

- **augment:** zero additional model calls, zero tokens, sub-millisecond gate compute; extra
  bounded tool reads only when a gap is real (in-memory joins; the same reads any correct route
  would have done). This is the mode that costs nothing and fixes the failure.
- **planner-gate:** ~1,170 tokens and ~300–1,000 ms per escalated turn; measured 4.5× model-call
  multiplier over the corpus. Rejected on the evidence.

## 9. Limitations

- Ask detection is vocabulary-bound. Domains named in words outside `_DOMAIN_HINTS` (+ the gate's
  small extension) are invisible — the same bound production's operator path already has, but the
  gate inherits it. The vocabulary is shared by import, so improving it improves both.
- Segmentation is heuristic English (sentence boundaries + conjunction-before-interrogative);
  unusual phrasing can mis-segment. Everything downstream fails *safe* — worst case is the
  current single-intent behavior.
- The 2-supplement cap (a deliberate platform bound) drops the fourth+ ask of very wide questions;
  drops are recorded in the trace.
- Safe-fallback supplements fall back to a domain's generic primary type when a segment doesn't
  classify on its own (e.g. `aid_status` rather than a sharper aid intent).
- The `classify()`-declines path with a live planner is unchanged; model plans can still under-read
  context (the `nc-1` provider regression) — pre-existing behavior, out of this experiment's scope.
- "Experienced" grading is persona-dependent (dependency rounds can mask gaps); the decision metric
  (strict) is persona-independent.
- Corpus is 16 cases; the full ~410-turn judged harness has not been run in gate mode (next step).

## 10. Recommendation: **ADOPT — augment mode only; reject the planner/LLM escalation**

The deterministic coverage gate turns the failure mode's 5/14 into 13/14 strict (14/14
experienced) full-intent coverage at zero model cost, zero measured unnecessary escalation, and no
change to any fast path. The LLM-escalation variant — including the originally proposed LLM gate —
is empirically dominated: no coverage gain, one regression, 4.5× the model calls, 5× the latency.
Ship the detector + deterministic augmentation; do not ship a model in the routing hot path.

**Exact next step if adopted:**

1. Run one full edward-eval judged batch A/B (`--persona` sweep, env flag on vs off) and compare
   with `compare.mjs` — confirm the completeness dimension gains and no aggregate drop (count
   gains as well as drops).
2. If clean, make `augment` the pipeline default: change `resolve_coverage_gate_mode`'s fallback
   from `"off"` to `"augment"`, retire the env flag, and promote the corpus's compound cases into
   `tools/edward-eval/src/cases/` with `requiredTools` expectations.
3. Fold `_EXTRA_DOMAIN_HINTS` into the shared vocabulary review, and evaluate lifting the
   supplement cap for the safe-fallback route only (the `nc-1` miss).
4. Assess Staff Edward for the same first-match collapse before porting anything (not assumed).

## 11. Tests and files

Tests (`apps/api/tests/test_assistant_coverage_gate.py`, 17 tests): failure-mode pins at default
(documents ask dropped; balance ask dropped), augment recovery (routing, reads, and answered
facts), zero-model-call proof (exploding planner never called), byte-identical fast paths across 6
no-fire cases, write-refusal never widened, context-clause and cap/drop unit behavior, planner
mode (covering plan accepted; non-covering plan → augment; crash → augment; planner not called
without a gap; no planner → augment), and opt-in wiring (off by default, env enables, explicit off
beats env, no `coverage_gate` trace stage in default runs).

Files changed (commit `55a7493`, not merged):

- `apps/api/src/audentra/integrations/assistant/coverage.py` — new, the gate
- `apps/api/src/audentra/integrations/assistant/pipeline.py` — opt-in hook; planner invocation
  extracted to a shared `_invoke_model_planner` (behavior identical, verified by existing tests)
- `apps/api/tests/test_assistant_coverage_gate.py` — new
- `tools/edward-eval/experiments/routing-coverage-v1/` — corpus runner + committed
  deterministic/provider results
