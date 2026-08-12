# Journey and form template runtime

Date: 2026-08-10

## Outcome

The platform accepts staff-authored multi-page forms and scaffolded dependency
graphs through the existing versioned journey publication boundary. Templates
remain a portal authoring aid: the platform receives and validates the complete
canonical document exactly as it would for manually created work.

## Canonical form contract

- `form.version` is currently `1`.
- A form contains one to twenty ordered pages.
- Every page has a stable unique ID, title, optional description, and bounded
  list of fields.
- Field IDs are unique across the whole form, including between pages.
- Every field still passes the existing type, label, choice, required-state,
  and conditional-visibility validation.
- Number fields additionally validate finite minimum, maximum, and positive
  step constraints; student responses must remain inside that published range
  and align to its step.
- The paged `form` object is canonical; flattened `fields` remains a temporary
  compatibility projection for existing consumers.

## Runtime behavior

- The student submission endpoint flattens the canonical pages server-side and
  validates the complete response against that definition.
- First-time onboarding keeps its protected system screens. Custom onboarding
  and enrollment actions both use durable student requirements afterward.
- Completing a scaffolded form uses the existing atomic response, status,
  reward, notification, audit, and dependency-recalculation path.
- Slow agent work is not introduced into form publication or submission.

## Conditional routing

- A target task may define `activation.match` as `all` or `any` and attach one
  or more answer rules to its prerequisite edges.
- Route sources must be direct prerequisites. Supported answer sources are
  task-level single/multiple selection, checkboxes, and required selection or
  bounded number fields inside a canonical form.
- Switch routes use equality or set membership, default routes use exclusion,
  and thresholds use finite ordered comparisons. Options and numeric bounds are
  checked against the published source field before the graph can commit.
- Completing the decision records the response first, then reconciles every
  mutable task in the journey in one transaction. Matching paths become ready;
  false paths become `not_applicable` with complete progress but no reward.
- Ordinary nodes after a skipped branch inherit `not_applicable`. A merge with
  at least one completed selected branch and skipped alternatives becomes ready.
- The same reconciler runs after generic responses, document/payment completion,
  staff document acceptance, and journey publication so no completion channel
  can bypass a conditional edge.

## Safety

- Expected-version writes prevent one staff session from overwriting another.
- Unknown prerequisites, inactive dependencies, duplicate IDs, unsupported
  field types, and dependency cycles are rejected before publication commits.
- Template-generated IDs are not trusted specially and receive the same tenant,
  authorization, and bounded-input checks as manually authored IDs.
- Existing completed work is retained while newly published active requirements
  are reconciled into current student journeys.
- Started, rejected, submitted, under-review, waived, expired, and completed
  evidence is not erased when staff later changes a route. A stale open student
  page fails its version/status check instead of recreating a skipped task.
- Scheduler lifecycle events publish realtime staff updates in the same
  transaction as their durable notifications. Later AI enrichment remains a
  separate, explicitly labeled event rather than masquerading as a replayed
  student inquiry.

## Validation

- Targeted route, managed-configuration, response, and staff-review tests pass.
- Ruff formatting and lint checks pass.
- Mypy passes across 131 source files.
- The full backend suite passes with 524 Python tests passing and 15 optional
  service-backed tests skipped; all document-preprocessing, state-effects, and
  demo API Node suites also pass.
- A live PostgreSQL/browser walkthrough confirmed that a Yes response selects
  the campus-housing path, skips the other two paths without rewards or data
  loss, and unlocks the shared arrival step after the selected branch completes.
