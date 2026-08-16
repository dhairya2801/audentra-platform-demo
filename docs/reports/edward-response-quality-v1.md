# Edward Response Quality v1 — Session 2 report

Date: 2026-08-16
Branch (both repos): `feat/edward-response-quality`

## Starting baseline SHAs

| Repo | SHA | Branch |
| --- | --- | --- |
| Audentra-platform | `a062592f93ab2a290d2603e4614006aa9b5ba7ab` | feat/edward-response-quality |
| Audentra-portals | `9c2126533ca6a338d97e23d16b12f8a7200b2053` | feat/edward-response-quality |

## Current composition architecture (as found)

Both assistants share one shape, in
`apps/api/src/audentra/integrations/{assistant,staff_assistant}/`:

```
normalize → classify (deterministic; model planner fallback) → tool reads
→ derive state → compose_deterministic (message + typed blocks + evidence bundle)
→ optional model rewrite of the PROSE ONLY (gateway.write_[staff_]grounded_answer)
→ claim guard over the same evidence bundle
→ final blocks = [model text block, *non-text deterministic blocks]
```

Key properties that were preserved untouched:

- The deterministic draft is the floor; every guard rejection falls back to it.
- The evidence bundle rendered by the composer is the exact corpus the claim
  guard checks model prose against.
- Blocks are typed data, never markdown; the portal renderer
  (`Audentra-portals apps/web/app/components/assistant-blocks.tsx`) renders
  text / bullet_list / numbered_list / next_steps / table / (staff) draft,
  with `fallbackText` for forward compatibility.

## Root causes of the repetition problem

1. **Deterministic prose enumerated what its own block then repeated.** Most
   collection composers built the message as
   `"N item(s): title, title, title and title."` via `_join_titles`, then
   attached a table/list block with the same rows (checklist, missing
   documents, document status, holds/blockers, aid requirements, registration
   gates, housing gates, staff blockers, staff work queue first row, staff
   cohort list).
2. **The rewrite model never saw the blocks.** `write_grounded_answer`
   received question + evidence lines + draft prose — nothing told it a table
   would render under its reply, so it could not know the rows were already
   carried once.
3. **The system prompt actively instructed enumeration.** The student prompt
   said "name the specific items by name — never 'two remaining
   requirements'", which is correct for pure-prose answers and exactly wrong
   above a table.
4. **Tables could not carry links.** The block contract allowed `href` on
   list items only; table rows were plain strings, and the staff side used no
   hrefs at all. Route strings were scattered as literals through composers
   and tools.

## Design principles chosen

- **Prose answers and synthesizes; blocks carry collections exactly once.**
  The prose gives: the direct answer (count / state / yes-no), the shape of
  the situation (how many need the reader's action vs. waiting on the
  university), and the exceptions worth naming (overdue, needs attention,
  payment pending). The block carries the rows.
- **Naming budget instead of a ban.** When the causes of a "why" question are
  1–2 items, naming them *is* the direct answer, even if a block repeats them
  with actions attached. Three or more → count + "listed below".
- **Table policy by information weight.** One item is a sentence (plus at
  most a single action link), never a one-row table. Empty results are prose
  only. Collections of ≥2 structured entities keep their table/list.
- **Deterministic enforcement over prompting wherever a guarantee matters.**
  The rewrite prompt teaches the policy, but row links, route validity,
  single-item collapsing, and honesty-caveat preservation are all enforced in
  code.
- **Links are data, never model output.** The model is explicitly told never
  to write a URL; every href is built server-side from a canonical route
  module and validated to be an internal portal route at the block layer and
  again in the renderer.

## Deterministic formatting changes (platform)

`integrations/assistant/compose.py` (student):

- Checklist (remaining + completed): count-based synthesis with a
  processing-pending exception note; titles live only in the next-steps
  block. Single open step is named in prose.
- Documents (status): prose gives per-state counts and names only
  needs-attention items and the specifically-asked-about document; the table
  carries rows and now deep-links each row to its requirement page. A single
  document requirement renders as a sentence plus one action link — no table.
- Missing documents: count in prose; each next-step action links to the
  requirement page; imperative checklist titles are converted to noun phrases
  ("Upload your official transcript", not "Upload your Upload an identity
  document" — a pre-existing presentation bug).
- Deadlines: overdue items are the exception prose names; the table carries
  all rows with per-row links. A single deadline is a sentence with its date.
- Holds/blockers, registration gates, housing gates: prose gives the count
  and the you-vs-university split; items with clearing actions render once,
  in the block. Causes are still named when there are ≤2.
- Financial aid: award/requirement counts in prose, details in the awards
  table and open-requirements block; ≤2 open causes are named for
  "why is my aid incomplete".
- Campus life: "N clubs you can join, covering <categories> and M upcoming
  events. Here's a breakdown:" — descriptions appear once, in the list, and
  each club links to its real detail page.
- New helpers: `_count` (real pluralization — no more "4 club(s)"),
  `_document_noun` (imperative title → noun phrase).

`integrations/staff_assistant/compose.py` (staff):

- Blockers: count + ownership split in prose ("2 need action from the
  student; 1 is waiting on university review"); titles and clearing actions
  once, in the block; ≤2 blockers still named.
- Work queue: totals + urgent/escalated counts + head-of-queue key only; the
  table carries the rows; a next-step links to the real task board.
- Cohort list: converted from a bullet list to a table (Student, Program,
  Deposit, Open blocking) with a link to the staff student directory;
  truncation phrasing kept.
- Cohort aggregate: buckets render as a table instead of a bullet list;
  counts-represent caveat kept.
- Attention ranking: count-based lead retained, "(s)" shorthand removed.

`integrations/assistant/blocks.py` (shared block builders):

- `table_block(..., row_hrefs=[...])` — optional per-row internal route,
  aligned by index; anything not starting with `/` becomes `None`; the field
  is omitted unless at least one row links.
- `describe_blocks_for_prompt(blocks)` — one line per non-text block (kind,
  caption/title, row/item count, columns), deliberately without row contents.

`integrations/assistant/tools.py`: the campus-life read now carries each
club's `id` so club links are grounded in read data (it previously projected
the id away).

## Rewrite-layer changes

- Both pipelines now pass `presented_blocks` — the output of
  `describe_blocks_for_prompt(draft.blocks)` — through the composer adapters
  (`platform_service.py`, `postgres_service.py`) into
  `gateway.write_grounded_answer` / `write_staff_grounded_answer`, where it
  is included in the bounded input as `blocksShownAfterReply`. The model
  finally knows what renders under its prose.
- Student system prompt: new "Presentation blocks" section (synthesize above
  a block, single out exceptions, never restate every row, never write a URL,
  refer to "the table below" only when one exists); the "name items by name"
  rule now carries an explicit exception when a block lists them.
- Staff system prompt: same presentation section; the "keep not-tracked
  caveats" rule now requires the fact's own plain words ("no model for melt
  risk exists"), which fixed the one pre-existing staff eval failure.
- **Deterministic caveat preservation** (staff pipeline,
  `_restore_dropped_caveats`): when the deterministic draft states an honesty
  caveat ("no registrar hold system", "engagement scan" provenance) and an
  accepted rewrite paraphrases it away, the caveat sentence is re-appended —
  the same structural pattern as the existing recommendation-provenance
  label. Prompting alone demonstrably let gpt-4o-mini drop these.

## Deep-link architecture

- **Canonical route modules**, one per assistant:
  - `integrations/assistant/links.py` — student portal constants
    (`/dashboard /enrollment /documents /financials /payments /appointments
    /messages /help /onboarding /profile /classrooms /campus-life`) plus
    `document_page(id)` → `/documents?document=<id>` and `club_page(id)` →
    `/campus-life/clubs/<id>`. Requirement links continue to come from read
    data via `audentra.domain.student_state.requirement_href`
    (`/enrollment/requirements/<slug>`).
  - `integrations/staff_assistant/links.py` — the staff portal is one route
    with hash views; constants for `/staff#overview #tasks #students
    #outreach #messages #knowledge #core_plays`.
- All route literals in the student composer now go through the module; the
  staff composer links only to hash views that exist.
- **Routes verified against the portals app router** (see below); a test-side
  allowlist regex (`tests/test_assistant_response_quality.py::_REAL_ROUTE`)
  asserts every href the composers produce matches a real route family — an
  invented route fails the suite.

### Routes mapped (verified in Audentra-portals `apps/web/app/`)

| Surface | Route |
| --- | --- |
| Enrollment checklist / requirements / deadlines | `/enrollment`, `/enrollment/requirements/[slug]` |
| Documents (incl. transcript category) | `/documents`, `/documents?document=<id>` |
| Financial aid / financials | `/financials` |
| Payments / deposit | `/payments` |
| Appointments / advising | `/appointments` |
| Campus life / clubs | `/campus-life`, `/campus-life/clubs/[clubId]` |
| Messages, Help, Onboarding, Profile, Academics | `/messages`, `/help` (`?conversation=`), `/onboarding`, `/profile`, `/classrooms` |
| Staff portal views | `/staff#overview #tasks #students #outreach #messages #knowledge #core_plays` (hash-routed single page) |

A specific student or work item in the staff portal is **not**
URL-addressable (selection is client-side React state), so staff per-row
deep links are deliberately not emitted — documented here rather than
invented. If the staff portal ever gains `?student=<id>` routing, the cohort
table can adopt `rowHrefs` with no contract change.

## Contract changes (both repos, kept identical)

`packages/contracts/src/index.ts` — the `table` block variant gained:

```ts
/**
 * Read-only portal route per row, aligned by index with `rows` (null
 * for rows without a destination); renderers link the row's first cell.
 * Never an action or an external link.
 */
rowHrefs?: (string | null)[];
```

The platform and portals contract files were byte-identical before the
change and remain byte-identical after (verified with `diff`). The field is
optional, so pre-change clients render the same table and simply skip the
links (`fallbackText` untouched).

## Portals renderer changes

`apps/web/app/components/assistant-blocks.tsx`:

- Table rows with a `rowHrefs` entry link their first cell through
  `TenantLink` (tenant prefixing, query/hash preservation, client-side nav).
- All assistant hrefs — list items and row links — are now validated through
  `safePortalDestination` and only *internal* results render as links;
  external or malformed hrefs render as plain text. (The renderer previously
  linked `item.href` unsanitized; server-side blocks already enforced the
  `/`-prefix rule, this adds client-side depth.)

## Table policy

| Situation | Rendering |
| --- | --- |
| Collection of ≥2 structured entities (requirements, documents, deadlines, awards, queue items, cohort rows, clubs) | Table (comparison fields) or list (narrative items), once; prose synthesizes |
| Exactly one item | A sentence (with the date/state inline), plus at most one action link; never a one-row table |
| Empty result | Prose only, stating the true empty state |
| Yes/no, single status, short explanation | Prose only |
| Actionable steps | `next_steps` list with per-item deep links and owner badges |

## Student before/after examples

**Clubs (deterministic draft)**

Before:
> "Campus life currently lists 4 club(s) and 1 upcoming event(s)."
> — followed by a bullet list repeating each club with its description.

After:
> "Campus life currently lists 4 clubs you can join, covering academic,
> engineering, music and sports and 1 upcoming event. Here's a breakdown:"
> — descriptions appear once, in the list; each club links to
> `/campus-life/clubs/<id>`.

**Missing documents (deterministic draft)**

Before:
> "3 document(s) still need to be uploaded: Submit your official transcript,
> Upload an identity document and Submit your immunization record."
> — followed by a next-steps list with the same three items, labelled
> "Upload your Upload an identity document".

After:
> "3 documents still need to be uploaded — each step below opens the right
> page:" — the list carries "Upload your official transcript" → 
> `/enrollment/requirements/transcript-upload`, etc.

**Financial aid (live rewrite, eval case u2)**

Before:
> "Your aid file lists 4 award(s), and 2 requirement(s) still need attention:
> Verification worksheet and Award acceptance." (awards table + requirements
> list rendered below with the same rows)

After:
> "Your aid file lists 4 awards, and 2 requirements still need attention —
> the details are below."

**One deadline (table policy)**

Before: a one-row table under "You have 1 upcoming deadline(s)."
After: "You have one deadline: Submit your immunization record, due
2026-08-20." plus a single linked action — no table.

## Staff before/after examples

**Blockers**

Before:
> "Maya is blocked by 3 item(s): Submit your official transcript, Pay the
> enrollment deposit and Submit your immunization record. Each lists who
> clears it. (No registrar hold system exists — …)" — next-steps block
> repeats all three with clearing actions.

After:
> "Maya is blocked by 3 items — listed below with who clears each. 2 need
> action from the student; 1 is waiting on university review. (No registrar
> hold system exists — these derived blockers are the complete list.)"

**Work queue**

Before:
> "8 open item(s) in canonical order (priority, then due date). First up:
> ENR-104 — Chase missing transcript for Maya Chen (urgent, due 2026-08-18).
> 2 urgent and 1 escalated overall." — the table's first row restated
> verbatim.

After:
> "8 open items in canonical order (priority, then due date) — 2 urgent, 1
> escalated. First up: ENR-104 for Maya Chen. The queue:" — plus a
> "Work the queue from the task board" link to `/staff#tasks`.

**Cohort list**

Before: prose count + a bullet list packing name/program/deposit/blocking
into each bullet. After: the same count prose above a real table (Student /
Program / Deposit / Open blocking) and a link to the student directory
(`/staff#students`). Aggregate buckets likewise became a table.

## Grounding

Nothing in this round weakens the guard path — it was strengthened:

- Claim-guard checks (ungrounded number/date/contact, invented causation,
  contradicted document state, invented hold, pay-again contradiction,
  missing unavailability note, staff action claims, fabricated metrics) are
  all unchanged or extended; the deterministic draft remains the floor.
- **Guard extension:** the housing/registration causal outcome patterns now
  also catch positively-inflected "X stops/keeps/prevents you from applying
  for housing / registering" — a full-tier eval diff caught a model reply
  asserting "your unfinished aid verification does stop you from applying
  for housing" against evidence that explicitly excluded verification from
  the housing gates, and the old patterns missed the phrasing. The truthful
  negation ("does not stop you…") remains acceptable (unit-tested).
- **Retry instead of a wrong-topic fallback:** ungrounded number/date/contact
  rejections now get the same single retry-with-feedback invented causation
  always had. Previously a rejected rewrite fell straight back to the
  deterministic draft, which (for planner-routed general questions like
  "what's a grant vs a loan?") can be a correct-but-off-topic aid summary.
- Tenant authorization, tool evidence, deterministic fallback, staff
  identity binding: untouched.
- Rewrite-model URLs: the prompt now forbids them outright, the block layer
  drops any href not starting with `/`, and the renderer re-validates via
  `safePortalDestination`. Links only ever originate from the canonical
  route modules or from read data.

## Eval before/after

All runs on this machine (artifacts are gitignored), same env, provider key
present so planner + composer run live (gpt-4o-mini, temp 0.2). Because
model-in-loop checks flip 1–4 non-critical cases per run, each side was run
multiple times and judged on stable sets.

| Suite | Baseline (`a062592`) | After (branch head) |
| --- | --- | --- |
| Student smoke (45 cases, deterministic checks) | 45/45 | 45/45 (twice); `compare.mjs`: "No deterministic regressions" |
| Staff (36 cases / 41 turns) | **35/36** — `staff-melt-ranking-009` (critical) failed: rewrite paraphrased away "no model for melt risk" | **36/36** (twice) — fixed by the caveat-preservation rule + prompt |
| Student full tier (376 cases / 446 turns, deterministic checks, no judge) | 11 fails (run 1) / 12 fails (run 2) | 14 (run 1) → 15 (run 2, pre-fix) → **11 (final run): exactly the baseline-stable failure set, zero regressions** |

Failure-set detail (full tier):

- Stable pre-existing failures, unchanged before and after (all
  non-critical): `amb-003, amb-008, enr-009, enr-013, enr-018, enr-028,
  hou-009, mt-002, mt-030, mt-041, xd-009`.
- The intermediate after-runs surfaced three findings that were then fixed
  and verified in the final run:
  - `xd-011` — invented-causation phrasing gap → guard extension above.
  - `conv-005` — ungrounded-number rejection falling back to an off-topic
    draft → retry-with-feedback above.
  - `enr-015` — a returned (rejected) transcript no longer surfaced once
    prose stopped enumerating → the checklist draft now names a returned
    step explicitly ("Note: Submit your official transcript was returned
    and needs your attention again.").
- Run-to-run flakes observed on both sides of the change (`aid-008` flipped
  at baseline; `fmt2`/`enr-026` flipped once after) — model variance, not
  regressions; each passed the final run.

**Exact regression count in the final state: 0. Improvements: staff
35→36/36 including one critical case; plus the two guard-hardening fixes.**

Caveat recorded for future sessions: the harness's `mentions*` checks grade
`payload.message` (prose) only, never blocks — so cases like `camp-001`
("mention ≥4 club names") intentionally constrain prose. The rewrite model
still carries names in prose for those list-the-things questions, which is
the correct behavior for an explicit "list…" ask; deterministic drafts keep
collections in blocks.

## Tests / lint / typecheck

- Platform: `pytest` **813 passed, 3 skipped** (final run at branch head) with
  `AUDENTRA_TEST_DATABASE_URL`/`TEST_DATABASE_URL` set (includes the three
  Postgres parity suites; the local test DB `vv_enrollment_test` on
  :55432 was stale/half-migrated at session start — 8 pre-existing failures
  — and was recreated + migrated before baselining, after which the same 8
  passed at the baseline SHA too). `ruff check`/`ruff format --check`:
  clean. `mypy src tests`: clean (188 files).
- New: `tests/test_assistant_response_quality.py` — 32 tests covering the 13
  requested scenarios (four clubs, checklist, documents, aid requirements,
  deadlines, housing options, one item, empty result, cross-domain, staff
  cohort list/aggregate, actionable deep links, prose+table, unavailable/
  unsupported) plus route-allowlist validation, rowHref sanitisation,
  rewrite block-description passing, guard phrasing extension, and staff
  caveat restoration.
- Portals: `npm run typecheck` clean, `npm run lint` 0 errors (15
  pre-existing warnings in untouched files), `npm run build` succeeds.

## Remaining weaknesses

1. The rewrite model still enumerates small collections (3–4 items) in prose
   for some questions despite the block context — gpt-4o-mini compliance is
   imperfect, and for explicit "list my…" questions the eval requires it.
   Structured enforcement (e.g. trimming model prose that duplicates >N block
   rows) is possible but risks fighting the mentions-based eval checks; not
   attempted this round.
2. `_restore_dropped_caveats` covers the two caveat families the evals
   exercise (hold system, engagement scan); other "not tracked" caveats
   (room assignment, disbursement schedule, opens/clicks) rely on the prompt
   plus the fabricated-metric guard.
3. Eval `mentions` checks grade prose only; if a future session moves more
   content into blocks, the harness should learn to grade
   `message + blocks fallbackText` for content checks (contract/formatting
   checks should stay prose-scoped). Left unchanged this round to keep the
   baseline comparison honest.
4. Staff per-entity deep links are impossible until the staff portal gets
   URL-addressable students/work items (`#students` keeps selection in React
   state).
5. Ten pre-existing full-tier failures remain (documented above; all
   non-critical, none touched by this round's scope).
6. The eval CI gate documented in `tools/edward-eval/README.md`
   (`edward-eval.yml`) still does not exist; nothing enforces critical cases
   on PRs.

## Recommended next steps

1. Add `?student=<id>` (and `?workItem=<key>`) URL state to the staff portal,
   then emit cohort/queue `rowHrefs`.
2. Teach the eval harness to grade content checks over prose + block
   fallback text, then tighten the prose checks that currently force
   enumeration.
3. Consider a deterministic post-check that flags model prose repeating >⅔
   of a table's first-column values, feeding the existing retry loop.
4. Extend caveat preservation to the remaining "not tracked" families.
5. Wire the documented eval CI gate.

## Commits / files changed

Audentra-platform (`feat/edward-response-quality`, from `a062592`):

- `57b93e6` feat(assistant): canonical deep-link modules and linkable table
  rows — new `integrations/assistant/links.py`,
  `integrations/staff_assistant/links.py`; `blocks.py` (`row_hrefs`,
  `describe_blocks_for_prompt`); `packages/contracts/src/index.ts`.
- `f279731` feat(assistant): synthesize prose above blocks instead of
  restating rows — `integrations/assistant/compose.py`,
  `integrations/staff_assistant/compose.py`,
  `integrations/assistant/tools.py` (club id), `tests/test_student_cohort.py`.
- `1f6d766` feat(ai): tell the rewrite model what blocks render under its
  prose — `integrations/ai/gateway.py` (prompts + `presented_blocks`),
  both `pipeline.py`, `application/platform_service.py`,
  `infrastructure/postgres/postgres_service.py`.
- `7695ad7` test(assistant): presentation-policy suite —
  `tests/test_assistant_response_quality.py`.
- `f2a89ee` fix(assistant): close guard and draft gaps the eval diff
  surfaced — `integrations/assistant/guard.py`, `pipeline.py`,
  `compose.py`, tests.
- (this report) docs: `docs/reports/edward-response-quality-v1.md`.

Audentra-portals (`feat/edward-response-quality`, from `9c21265`):

- `de92adb` feat(web): linkable assistant table rows, sanitized assistant
  hrefs — `packages/contracts/src/index.ts` (identical to platform),
  `apps/web/app/components/assistant-blocks.tsx`.

Not merged anywhere; `main` untouched in both repos.
