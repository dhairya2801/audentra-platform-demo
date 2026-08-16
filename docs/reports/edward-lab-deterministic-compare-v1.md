# Edward Lab: normal vs forced zero-LLM comparison (v1)

An experimental comparison capability, not a redesign. Edward already has a
deterministic execution and composition path; nothing about it was rebuilt.
What was added is a per-request switch that removes the model for exactly one
turn, plus the Lab surface and the eval harness needed to put the two runs
side by side.

## Baseline

| | |
| --- | --- |
| Platform baseline SHA | `a062592f93ab2a290d2603e4614006aa9b5ba7ab` (`docs: report Edward parity integration v2`) |
| Platform branch | `feat/edward-lab-deterministic-compare` (worktree `Audentra-platform.s3-lab`) |
| Platform commit | `970c34e feat(lab): add forced zero-LLM Edward execution mode` |
| Portals baseline SHA | `9c2126533ca6a338d97e23d16b12f8a7200b2053` |
| Portals branch | `feat/edward-lab-deterministic-compare` (worktree `Audentra-portals.s3-lab`) |
| Portals commit | `4f4217c feat(lab): compare normal and deterministic Edward side by side` |
| Merged from other branches | none |
| `main` modified | no |

## Existing deterministic architecture (recon)

`AssistantPipeline.execute` (`apps/api/src/audentra/integrations/assistant/pipeline.py`)
is already deterministic-first. In execution order:

1. `normalize_request` — deterministic text normalization, follow-up detection,
   mutation and sensitive-data flags.
2. `classify(request)` — deterministic classifier. Returns `None` when it has
   no confident route.
3. Model planner — **optional**, and only reached when `classify` returned
   `None` and the request is not a mutation.
4. `select_tool_reads` / the validated model plan → `execute_tool_reads` over
   the canonical `AssistantToolHost`.
5. `derive_student_state` — deterministic domain derivation.
6. `resolve_dependency_reads` — one bounded, deterministically planned extra
   read round when derivation surfaced an unexplained gate. Never a second
   model-planned expansion.
7. `compose_deterministic` — the deterministic composer; produces the message,
   the structured blocks, and the evidence corpus.
8. `_maybe_rewrite` — **optional** model prose over that evidence, re-checked
   by `guard_grounded_answer`, with the deterministic draft as the floor.

Both model hooks are constructor parameters (`model_planner`, `model_composer`)
and both are already `None` in any composition without a provider key — the
eval host documents exactly this. `AssistantPipeline(host)` with no hooks is a
complete, working Edward. The staff pipeline has the identical shape.

Both guards (`assistant/guard.py`, `staff_assistant/guard.py`) and both safety
gates (`ai/edward_safety.py`, `staff_assistant/safety.py`) are pure regex and
comparison code. There is no model-backed guard anywhere in the request path.

## Every model-call point in an Edward turn

Exhaustive, for both assistants:

| # | Call site | Reached when | Provider entry point |
| --- | --- | --- | --- |
| 1 | `AssistantPipeline` planner (`pipeline.py:116`) | deterministic classification returned `None` and the turn is not a mutation | `StudentAIGateway.plan_assistant_tool_reads` |
| 2 | `AssistantPipeline` composer (`pipeline.py:354`), up to 2 attempts | a composer hook exists, the route is rewritable, and the draft has evidence | `StudentAIGateway.write_grounded_answer` |
| 3 | `StaffAssistantPipeline` planner (`staff_assistant/pipeline.py:169`) | staff classification returned `None` | `StudentAIGateway.plan_staff_tool_reads` |
| 4 | `StaffAssistantPipeline` composer (`staff_assistant/pipeline.py:528`) | composer hook exists, route is rewritable, draft has evidence | `StudentAIGateway.write_staff_grounded_answer` |

Everything downstream of those four funnels into one place,
`CompletionClient.complete` in `integrations/ai/provider.py`, which is the only
code in the process that issues a provider HTTP request for an assistant turn.

Checked and confirmed **not** in the turn path: the deterministic pre-pipeline
safety gate, the claim guards, `ai/guided.py` (its zero-token chat guidance was
removed with the legacy `ask_edward` path; only the document-extraction
fallback remains — no provider call), document extraction, transcription, and
Action Center enrichment. There is no hidden fallback provider call: when a
hook is absent the pipeline returns the deterministic draft, and when a hook
raises, the failure is recorded and the deterministic draft is returned.

## Mechanism: forced zero-LLM execution

The smallest clean change was to remove the two hooks for one request, rather
than to add suppression logic inside the pipeline. A pipeline with neither hook
has no code path that reaches a provider, which is a structural guarantee
rather than a conditional one.

**New module** `apps/api/src/audentra/core/assistant_execution.py`:

- `AssistantExecutionMode` (`StrEnum`): `default` | `deterministic`.
- `ASSISTANT_EXECUTION_MODE_HEADER = "x-edward-mode"`.
- `ResolvedAssistantExecutionMode` — the effective mode plus the raw
  `ignored_request` when the environment refused to honour it.
- `resolve_assistant_execution_mode(raw, *, lab_controls_enabled)`.
- `lab_execution_controls_enabled(environment, assistant_trace_debug_enabled)`.
- `model_hook(mode, hook)` — returns the hook, or `None` in deterministic mode.

**Wiring**, four lines of behaviour in total:

- `routes.py` resolves the header once per assistant turn (`_assistant_execution_mode`)
  and passes it through `_dispatch`. Only the two Edward routes pass it; every
  other route dispatches with the untouched default.
- `ServiceCall` carries one new typed field, `assistant_execution`, defaulting
  to `DEFAULT_ASSISTANT_EXECUTION`.
- `InMemoryPlatformService` and `PostgresPlatformService` build their pipelines
  through `model_hook(execution.mode, …)`. That is the single place a turn can
  acquire a model, so it is also the single place the mode is enforced.
- `AssistantTurnTrace` gains `execution_mode` and
  `ignored_execution_mode_request`.

Why a header rather than a request-body field: the body is the public
`AskEdwardRequest` contract, and a development control does not belong in a
published contract. The header is resolved into a typed value at the boundary
and never travels as a string past `routes.py`.

### Development/evaluation only

`lab_execution_controls_enabled` requires **both** that assistant trace
debugging is enabled *and* that the environment is not `production`. The
environment check is deliberately independent of settings composition, so even
a hand-built production `HttpSettings` with trace debugging forced on cannot
open the control up. (`RuntimeSettings.from_environment` already rejects
`ASSISTANT_TRACE_DEBUG_ENABLED` in production; this is a second, independent
gate.)

Behaviour where the control is **not** honoured: the header is **ignored**, not
rejected. The turn takes the ordinary production path, the response is
unchanged, and nothing in the response reveals that a mode control exists. The
requested-but-ignored value is written to the trace as
`ignoredExecutionModeRequest`, so a misconfigured environment is visible in
observability instead of silently pretending to have run deterministically.

Behaviour where the control **is** honoured: an unrecognised value is a `400
INVALID_ASSISTANT_EXECUTION_MODE`, so a Lab typo fails loudly instead of
quietly grading the wrong execution path.

CORS advertises `x-edward-mode` only where the control is honoured, so a
deployed API never lists it in preflight. Verified live against the eval host:

```
access-control-allow-headers: Accept, …, X-VV-Worker-Token, x-edward-mode
```

Session 4's routing experiment is **not** on this header. `AssistantExecutionMode`
has exactly two members and the module docstring states that other experimental
request-scoped modes must not be folded into it.

### Proof of zero provider calls

`tests/test_assistant_execution_mode.py` proves it at the transport, not with a
stub of the gateway. The service is composed with a real `StudentAIGateway` over
a real `CompletionClient` whose `httpx` transport counts and records every
request. Deterministic mode:

```
assert provider.requests == []          # no provider request was issued
assert trace["executionMode"] == "deterministic"
assert trace["modelCalls"] == []
assert trace["provider"] == "guided"
assert trace["usage"] is None
assert trace["toolCalls"]               # still a full pipeline, not a refusal
assert trace["evidence"]
```

The same fixture proves default mode still reaches `api.openai.com` for the
same question, so the comparison is against real current Edward and not a
crippled one. The staff assistant has the same proof over the staff dispatch.

At the harness level, `compare-modes.mjs` exits non-zero unless *every*
deterministic run is confirmed zero-LLM by its own trace. The v1 batch:
**13/13 deterministic runs, 0 model calls, 13/13 confirmed by trace.**

## Trace changes

`AssistantTurnTrace` gains two fields, serialized as `executionMode` and
`ignoredExecutionModeRequest`. `executionMode` is also carried in the trace list
summaries, so the Lab's recent-trace list distinguishes the two kinds of run
without opening each one.

Nothing was removed. Existing consumers (`inspect.mjs`, the trace inspector,
the eval runner's `gradedResponse`) are unaffected.

## Lab UI changes (Portals)

The existing `/dev/edward` Lab gains a two-tab header: **Chat + trace** (the
original dashboard, unchanged) and **Normal vs deterministic**.

The comparison view takes one question — typed, or picked from eight one-click
experiment chips — and runs it twice, sequentially, against the real platform.
Both runs are conversation-less, so the platform persists nothing and the second
run reads precisely the state the first one did; follow-up experiments replay
their prior turns as client history so both sides get identical context. Runs
are sequential on purpose: two concurrent turns share the same process and would
muddy the latency each reports.

Per side, from the trace only: final message, block types, route and
classification source, executed tools (with dependency-round count), evidence
count, response source, provider, model, model-call count, token usage,
estimated cost, server duration, client latency, failure codes, and an
unsupported/refusal flag. A **Differences** strip reports same-message,
same-route, same-tools, same-blocks, the per-tool asymmetry in both directions,
the server-duration delta, and the tokens and cost the deterministic run avoided.

Two honesty rules are enforced in the UI:

- A run whose trace does not come back, or whose trace reports a different
  mode than the one requested, is labelled **unverified** rather than presented
  as a confirmed zero-LLM turn.
- Cost is estimated only for models in an explicit price table. An unpriced
  model reports no cost rather than a fabricated one.

`askEdward` gained an opt-in third argument, `{ executionMode }`, which adds
the header only when passed. Every product call site omits it, so ordinary
portal traffic is byte-identical to before. The header name is declared as a
literal inside `api-client.ts` so no product module takes a runtime dependency
on Lab code; a test pins that literal to the Lab's exported constant, and
another test asserts no component other than the comparison panel mentions
`executionMode`.

The Lab reaches traces only through the existing same-origin
`/api/edward-lab/*` proxy, which holds the worker credential server-side. No
new credential path was added.

## Comparison methodology

`tools/edward-eval/compare-modes.mjs` + `src/mode-experiments.mjs`, run via
`npm run eval:edward:compare-modes`.

- One `audentra-eval-api` process per persona — the real FastAPI app over the
  in-memory platform composition, the same host the Edward eval suite uses.
- Each experiment names the persona whose canonical state gives its question a
  definite answer, so a difference between modes is a difference in *execution*,
  never in the record.
- Two conversation-less turns per experiment, `default` then `deterministic`.
- Nothing is judged. The harness reports what the two traces recorded and the
  raw answers, and refuses to call a run deterministic unless the trace says so.
- Model configuration for the v1 batch: `openai:gpt-4o-mini`, pricing table
  `2026-08-12`.
- Artifacts: `artifacts/mode-comparisons/modes-v1/{comparisons.json,comparisons.md}`
  (git-ignored; regenerate with the command above).

Student turns only. The staff assistant shares the identical mechanism and has
its own zero-call proof in the test suite, but this host serves a single-persona
cohort, so a staff comparison here would describe the fixture rather than Edward.

## Results (batch `modes-v1`, 13 experiments)

| Case | Category | Persona | Same answer | Same route | Same tools | Normal calls / tokens | Det. calls | Normal ms | Det. ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| md-direct-simple | Direct simple | transcript_under_review | no | yes | yes | 1 / 1184 | 0 | 1286 | 2 |
| md-direct-financial | Direct financial | new_admit | no | yes | yes | 1 / 1499 | 0 | 996 | 2 |
| md-cross-domain | Cross-domain | payment_pending | no | yes | yes | 1 / 2179 | 0 | 1272 | 4 |
| md-cross-domain-blocked | Cross-domain | new_admit | no | yes | yes | 1 / 2076 | 0 | 1747 | 4 |
| md-aggregation | Aggregation | deadline_passed | no | yes | yes | 1 / 1127 | 0 | 1425 | 1 |
| md-ambiguous | Ambiguous | nearly_complete | no | **no** | **no** | 2 / 3538 | 0 | 2696 | 1 |
| md-ambiguous-vague | Ambiguous | new_admit | no | **no** | **no** | 2 / 4074 | 0 | 3833 | 2 |
| md-multi-intent | Multi-intent | new_admit | no | yes | yes | 1 / 1197 | 0 | 1111 | 2 |
| md-multi-intent-aid | Multi-intent | aid_verification_outstanding | no | yes | yes | 1 / 1963 | 0 | 1601 | 4 |
| md-follow-up | Follow-up | deadline_passed | no | yes | yes | 1 / 1120 | 0 | 1200 | 2 |
| md-follow-up-why | Follow-up | new_admit | no | **no** | **no** | 2 / 4196 | 0 | 2297 | 2 |
| md-unsupported-record | Unsupported | new_admit | no | yes | yes | 1 / 1391 | 0 | 1157 | 2 |
| md-unsupported-future | Unsupported | new_admit | no | yes | yes | 1 / 1099 | 0 | 1276 | 2 |

Aggregates: same route 10/13, same tool reads 10/13, same evidence corpus
10/13, same block shape 8/13, same final message 0/13.

### Where deterministic matches normal

**Retrieval and grounding are identical wherever the deterministic classifier
is confident.** In 10 of 13 experiments both modes selected the same route,
executed the same tool reads in the same order, and produced a byte-identical
evidence corpus. That covers every direct question, both cross-domain causation
cases, aggregation, both multi-intent cases, the contextual follow-up, and both
unsupported questions.

This is the study's most important structural finding, and it is not a surprise
once the architecture is read: the model planner is only consulted when
`classify` returns `None`. When the deterministic classifier is confident, the
planner is never called, so the two modes are running *the same retrieval code*.
The comparison isolates the rewrite; it does not test two retrieval systems.

The bounded dependency second read also behaves identically in both modes,
because it is deterministically planned from derived gate codes.

### Where deterministic fails

**Exactly three cases — all of them classifier fall-through.** The failing three
are the two ambiguous questions and the bare `"Why?"` follow-up. In each, the
deterministic classifier returned `None`, the pipeline fell back to
`Classification("general_question", 0.5, source="safe_fallback")`, and the trace
recorded `classification_fallback`. The model planner, given the same input,
produced a specific route (`enrollment_state`, `housing_status`) and the
targeted reads that go with it.

The failure is honestly exposed — `toolSelectionSource: "safe_fallback"` and
`failureCodes: ["classification_fallback"]` appear on the trace and as a red
chip in the Lab — but it is exposed *in the trace, not in the answer*. The
student-visible text is a confident-sounding "Your next step: Pay the enrollment
deposit." with no signal that Edward did not understand the question. For
`md-follow-up-why` the deterministic answer is simply about the wrong thing:
asked "Why?" after "Can I apply for housing? — Your housing application is not
open yet", it answered with the next checklist step instead of the cause.

Two further deterministic weaknesses, both in composition rather than routing:

- **Aggregation truncates.** `md-aggregation`: deterministic answered "6
  checklist step(s) still need attention: … and more." Normal enumerated all six
  and named the actionable next one. Same evidence, 174 vs 407 characters.
- **Multi-intent answers the primary intent only.** `md-multi-intent` asked for
  transcript status *and* outstanding balance. Both modes read
  `getStudentAccountSummary`; only the model's answer used it. The deterministic
  composer routed to `document_status` and answered documents alone — the
  balance was retrieved and then dropped.

### Where the LLM materially improves behaviour

- **Ambiguity resolution.** The planner turns "Am I good to go?" and "Is
  everything okay with my stuff?" into a real route instead of a broad fallback.
- **Multi-intent merging.** With one evidence corpus covering several domains,
  the rewrite answers all the intents present; the deterministic composer emits
  one route's template.
- **Bare follow-ups.** `"Why?"` with prior context is the clearest single win.
- **Scope honesty on out-of-record questions.** `md-unsupported-record` ("What
  was my roommate's high school GPA?") is the sharpest result in the batch. Both
  modes classified it `housing_status` — the deterministic classifier and the
  planner made the *same* routing mistake. But the model's rewrite refused:
  "I can't provide your roommate's high school GPA, as that information isn't
  included in your record." The deterministic composer answered the question it
  had routed to, delivering a fluent, accurate, and entirely irrelevant
  paragraph about housing blockers. The refusal came from the rewrite layer,
  not from routing.
- **Prose quality and actionability.** Deterministic answers read as rendered
  records ("4 item(s) on your record are blocking registration…"), often with
  duplicated clauses when several deterministic sections concatenate — see
  `md-cross-domain`, where the deterministic answer states the same four
  blockers twice in 575 characters. Model prose is shorter, non-repetitive, and
  consistently ends with a concrete next action.

### Where the LLM made things worse

`md-unsupported-future` ("Will I get more scholarship money next year?"): the
deterministic answer stated the aid facts and stopped. The model answered "You
may not receive more scholarship money next year because your aid file shows
that you have two requirements that still need attention… Completing these
requirements could impact your eligibility for future aid." That is a causal
claim about future aid eligibility which no canonical record supports, and the
claim guard passed it. One case is not a rate, but it is a real counter-example
and it points at a guard gap rather than a prompt gap.

### Attributing the value

The brief asked to separate these, and the data does:

| Layer | Contribution observed |
| --- | --- |
| Deterministic retrieval | Everything. Identical in 10/13, and the source of the evidence the model rewrites in the other 3. |
| Planner | Value in exactly the 3 classifier fall-through cases (ambiguous ×2, bare follow-up ×1). Zero elsewhere — it is not invoked. |
| Rewrite / composition | Value in every case: multi-intent merging, aggregation completeness, scope refusal, and readability. Also the source of the one observed regression. |
| Ambiguity handling | Planner. |
| Multi-intent handling | Rewrite, over retrieval both modes already share. |
| Multi-step reasoning | Neither, in this batch. The bounded dependency read is deterministic and ran identically in both modes; no case required reasoning the composer could not template. |
| Natural-language quality | Rewrite, uncontested. |

## Latency and cost

- Normal: median **1286 ms** server-side (min 996, max 3833).
- Deterministic: median **2 ms** server-side (min 1, max 4).
- Tokens: 26,643 across 13 normal turns (~2,050/turn); 0 deterministic.
- Cost: **$0.00448** across 13 normal turns (~$0.00034/turn) at `gpt-4o-mini`;
  $0 deterministic.
- Two-model-call turns (planner + composer) cost roughly twice a one-call turn
  in both tokens and latency — 3,538–4,196 tokens and 2.3–3.8 s.

**This latency comparison is heavily flattered by the host.** The in-memory
eval store answers every tool read in microseconds, so the deterministic side
is essentially pure CPU. Against Postgres, the deterministic side would be
dominated by 3–6 real queries and the honest number is "database latency" not
"2 ms". The *difference* — roughly 1.0–1.3 s of provider round trip per model
call — transfers; the ratio does not.

## Limitations

1. **The deterministic composer already participates in normal Edward as the
   draft and the floor.** Deterministic-only performance therefore looks
   surprisingly strong by construction, and the two modes are not independent
   systems. This experiment measures *the delta the model adds on top of the
   deterministic answer*, nothing more. It is not evidence that LLMs add no
   value, and it is not evidence that they do.
2. **In-memory, single-persona host.** No Postgres, no cohort, no concurrency.
   Latency conclusions do not transfer (see above), and staff comparisons were
   deliberately omitted rather than fabricated.
3. **13 experiments, one model, one run each.** `gpt-4o-mini` at temperature
   0.2 is not deterministic; a second run would give different prose and could
   give different planner routes on the ambiguous cases. Nothing here is a rate.
4. **No quality judging.** The eval suite's judge was not run. "Better" in this
   report means an inspectable, quoted difference, not a score.
5. **Answer quality is assessed by reading.** Where the report says the model's
   answer is better, the deterministic and model answers are both quoted in
   `artifacts/mode-comparisons/modes-v1/comparisons.md`; readers can disagree.
6. **The unsupported category is under-tested.** Two questions, and they
   disagreed with each other about which mode behaved better.

## Tests, lint, typecheck

Platform (`apps/api`):

- `pytest` — **797 passed, 3 skipped** with a disposable PostgreSQL configured;
  the 3 skips are S3-only integrations. Without a database: 753 passed, 47
  skipped.
- `pytest -m postgres` — **14 passed** against the disposable database.
- New file `tests/test_assistant_execution_mode.py` — **16 passed**, covering
  the resolver, zero-provider-call proof for both assistants, production
  ignore-and-record, unknown-mode rejection in the Lab, CORS advertisement,
  credential non-leakage, and the unchanged default execution regression.
- `ruff check src tests` — clean. `ruff format --check src tests` — clean.
- `mypy src tests` — clean, 187 files.

Portals (`apps/web`):

- `npm run test` (production build + node:test) — **68 passed, 0 failed**,
  including 8 new comparison tests and the pre-existing production-gating and
  credential-boundary tests.
- `npm run typecheck` — clean. `npm run lint` — 0 errors (15 pre-existing
  `<img>` warnings, untouched).

Database policy: a disposable `postgres:17.5-alpine` container
(`audentra-s3lab-pg`, database `edward_lab_s3`, bound to `127.0.0.1:55433`) was
created, migrated through the full chain, used, and then removed. `vv_enrollment`
was never touched, and no developer, preview, or shared database was used.

End-to-end verification was also run manually: `audentra-eval-api` on :45610
plus the portal dev server, with both turns issued from the portal origin and
both traces fetched back through the portal's `/api/edward-lab/*` proxy —
`executionMode: default` / 1 model call / 1381 ms against `executionMode:
deterministic` / 0 model calls / 3 ms, same 6 tool reads.

## Files changed

Platform — `970c34e`, 14 files:

```
A  apps/api/src/audentra/core/assistant_execution.py
M  apps/api/src/audentra/core/ports.py
M  apps/api/src/audentra/core/__init__.py
M  apps/api/src/audentra/interfaces/http/routes.py
M  apps/api/src/audentra/interfaces/http/app.py
M  apps/api/src/audentra/application/platform_service.py
M  apps/api/src/audentra/infrastructure/postgres/postgres_service.py
M  apps/api/src/audentra/integrations/assistant/trace.py
A  apps/api/tests/test_assistant_execution_mode.py
A  tools/edward-eval/compare-modes.mjs
A  tools/edward-eval/src/mode-experiments.mjs
M  tools/edward-eval/src/runner.mjs
M  tools/edward-eval/README.md
M  package.json
```

Portals — `4f4217c`, 6 files:

```
A  apps/web/app/components/edward-lab-compare.tsx
M  apps/web/app/components/edward-lab.tsx
M  apps/web/app/components/edward-lab.module.css
M  apps/web/app/lib/edward-lab.ts
M  apps/web/app/lib/api-client.ts
M  apps/web/tests/edward-lab.test.mjs
```

### Boundaries respected

- `integrations/assistant/compose.py`, `integrations/staff_assistant/compose.py`,
  `integrations/assistant/guard.py`, and `integrations/staff_assistant/guard.py`
  are **untouched** — `git diff` against the baseline is empty for all four.
  Response composition was not redesigned.
- No routing experiment was implemented, and `AssistantExecutionMode` has no
  routing member.
- Neither branch was merged; `main` was not modified in either repository.

## Recommendation

The experiment does not support "drop the LLM" and does not support "the LLM is
carrying Edward". It supports three concrete, separable changes.

1. **Treat the deterministic classifier's fall-through rate as the metric that
   decides the planner's worth.** Every case where the planner earned its cost
   was a case where `classify` returned `None`; in every other case the planner
   was never invoked. If fall-through is driven down, the planner's remaining
   value shrinks to near zero and the second model call can be retired for most
   traffic. `classification_fallback` is already recorded on every trace, so
   this is measurable today across the whole eval suite rather than from 13
   cases.

2. **Close the two deterministic composition gaps directly, because they are
   template problems, not reasoning problems.** Aggregation truncates to "and
   more" when the evidence lists all six steps, and multi-intent drops a domain
   that was already retrieved. Both are fixable in the deterministic composer
   with the data already in hand, and both would remove a reason the rewrite is
   currently load-bearing. (That work belongs to whoever owns
   `compose.py` — this session did not touch it.)

3. **Do not remove the rewrite for out-of-record questions until the
   deterministic path can decline.** The sharpest failure in the batch is that
   deterministic Edward answered a question about a roommate's GPA with a fluent
   paragraph about housing blockers. Routing was equally wrong in both modes;
   only the rewrite refused. Until the deterministic path has an explicit
   "the record does not contain this" outcome, the rewrite is the only thing
   standing between a mis-route and a confidently irrelevant answer.

One guard follow-up, independent of the above: the claim guard accepted "your
aid file shows … could impact your eligibility for future aid" — a causal claim
about a future period with no supporting evidence line. `build_causal_guards`
covers registration, housing, and disbursement gates; forward-looking eligibility
claims appear to be outside it.

Finally, keep the mode control. It cost one typed field on `ServiceCall`, two
trace fields, and a `model_hook` call at four pipeline construction sites, and
it makes "what is the model actually contributing to this turn?" a question
anyone can answer in the Lab in about four seconds.
