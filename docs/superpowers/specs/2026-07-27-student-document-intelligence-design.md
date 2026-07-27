# Student Document Intelligence Design

## Purpose

Provide complete, explainable transcript intelligence and configurable
immunization compliance using the tenant AI prompt runtime. Long transcripts
must not silently stop after the first text window. Exemption and health
policies must be changeable per university without deploying application code.

Every LLM call uses a dedicated versioned prompt. Model output is a structured,
reviewable proposal; official academic exemptions and health clearance remain
human/institutional decisions.

## Transcript pipeline

### Provider

`TRANSCRIPT_PARSING=openrouter` is the local and initial deployment selection.
OpenRouter receives locally extracted page text plus rendered page images as
untrusted evidence.

### Page-aware segmentation

The preprocessor returns page-level text and images, not only one concatenated
text string. The transcript is divided into ordered segments bounded by the
active tenant operation configuration:

- page range;
- extracted-character budget;
- image count and dimensions;
- provider input/output token budgets.

Every page in the configured document limit is represented by text, an image,
or an explicit unreadable-page marker. A six-page transcript therefore cannot
be reduced to the first 10,000 characters.

### Segment extraction prompt

`transcript_segment_extraction` receives:

- document and segment identifiers;
- page numbers;
- expected document type as routing context;
- page text and images;
- tenant field policy;
- a strict schema requiring course code, title, credits, grade/score, term,
  confidence, and source-page evidence.

It instructs the model to return every readable course row and never infer
missing facts.

### Merge prompt

`transcript_extraction_merge` receives only normalized segment outputs and
their provenance. It:

- deduplicates rows repeated across page boundaries;
- preserves materially different attempts/terms;
- retains source pages and original labels;
- reports gaps/unreadable segments;
- cannot add a course absent from segment evidence.

The server validates that returned rows reference real segment rows.

### Completeness

The normalized extraction records:

- pages expected, processed, unreadable, and omitted;
- segment count and terminal status;
- course count;
- whether any input was bounded;
- warnings tied to page ranges.

The UI distinguishes complete, partial, and failed extraction. Partial output
is visible for review but cannot be described as a complete course list.

## Exemption context and prompt

### Tenant policy

Extend the versioned academic model so a university can change:

- catalog versions and effective dates;
- program requirements;
- course descriptions and prerequisites;
- source-course/exam equivalency rules;
- score/grade/credit thresholds;
- natural-language policy clauses;
- exclusions and staff-review requirements.

Existing `course_equivalency_rule` remains a versioned source of context, but
SQL no longer generates recommendations by exact string match. Policy mutation
increments `tenant_ai_runtime_revision` for `course_exemption_mapping`.

### Context selection

For a student and transcript, the runtime supplies:

- selected program and active catalog version;
- required and prerequisite courses;
- active equivalency-policy version and relevant rules;
- normalized transcript courses with original labels and evidence;
- prior approved, denied, superseded, or suggested recommendations.

Large inputs are batched deterministically. Candidate retrieval may use course
codes, normalized labels, descriptions, prerequisites, and rule references to
bound each prompt, but retrieval never makes the final recommendation.

### Mapping prompt and output

`course_exemption_mapping` asks the model to assess source evidence against the
supplied tenant context. Structured output includes:

- source transcript-credit identifier;
- proposed target course identifier/code;
- applied rule identifier when present;
- evidence course/page identifiers;
- policy clause/rationale;
- confidence;
- result: `suggested`, `needs_review`, `no_match`, or `policy_gap`;
- missing evidence and staff-review reason.

The output is rejected if it references a source course, target course, rule,
or policy version not present in its context bundle.

`suggested` and `needs_review` are advisory. Only a staff workflow can approve
or deny credit.

### Policy changes

Activating a new catalog or equivalency-policy version:

1. increments the runtime revision;
2. emits a policy-change outbox event;
3. marks affected unapproved recommendations superseded;
4. queues re-evaluation against the new policy;
5. preserves prior recommendations and run receipts for audit.

## Immunization pipeline

### Tenant policy model

Add:

`immunization_policy_version`

- tenant, code, version, effective dates, active state, explanatory text,
  timestamps

`immunization_requirement`

- policy version
- stable vaccine code and display name
- required dose count
- minimum/maximum spacing rules when applicable
- recency/expiry rule when applicable
- accepted evidence types
- waiver/exemption handling
- student population applicability
- display order

The fictional Aster seed may include configurable examples such as MMR,
varicella, Tdap, meningococcal, and COVID-19. These are sample tenant data, not
claims about another institution's current medical policy.

### Processing mode

Health/immunization uploads move from direct manual review to an agentic
evidence-extraction path with stricter privacy controls. The original remains
durable before processing. The model receives only the minimum pages/text
needed for vaccine evidence.

### Extraction prompt

`immunization_record_extraction` returns:

- vaccine name/code as written;
- dose number when shown;
- administration date;
- provider/source label when safe;
- confidence and source page;
- unreadable/ambiguous warnings.

It must not extract diagnoses, unrelated medications, full insurance IDs, or
other unrelated medical history.

### Compliance prompt

`immunization_compliance_evaluation` receives normalized evidence and the
active tenant policy. For each configured requirement it returns:

- `met`;
- `missing`;
- `uncertain`;
- `not_applicable`;
- evidence references;
- explanation;
- missing dose/date/waiver information;
- staff-review requirement.

The UI shows a checklist of met and missing items plus explicit uncertainty.
The result never represents final institutional health clearance until staff
review is complete.

Policy activation uses the same revision, supersession, re-evaluation, and
audit behavior as academic policy changes.

## Upload formats

All document surfaces consistently advertise and accept:

- PDF (`application/pdf`);
- JPG/JPEG (`image/jpeg`, `.jpg`, `.jpeg`);
- PNG (`image/png`, `.png`).

The browser `accept` attribute, drag/drop validation, API signature validation,
server MIME detection, error copy, and tests must agree. Extension alone is
never trusted.

## Student experience

### Transcript

- Show processing progress by segment/page.
- Show complete versus partial coverage.
- Show institution, term, page coverage, and total course count.
- Allow expansion to every extracted course with its original source label.
- Display generated exemption recommendations next to their evidence and
  identify the active catalog/policy version.
- Label all recommendations “Prediction only—registrar approval required.”
- Explain `no_match` separately from parsing failure.

### Immunization

- Show the active university requirement checklist before upload.
- After processing, show met, missing, uncertain, and not-applicable states.
- Link each met/uncertain result to the relevant uploaded evidence.
- Keep staff-review and privacy language visible.

## Failure behavior

- A segment failure does not discard successful segments; retry only failed
  segments.
- Merge failure leaves segment output durable and retryable.
- Missing/unreadable pages make the extraction partial, not complete.
- Prompt or policy revision changes create a new evaluation rather than
  rewriting prior evidence.
- Invalid model references fail schema/domain validation.
- Provider unavailability retains originals and exposes a safe retry action.
- Tenant-policy absence prevents exemption/compliance evaluation and explains
  that institutional policy is not configured.

## Testing and local verification

Automated tests cover:

- page segmentation and merge provenance;
- a synthetic transcript with more than 50 courses across multiple pages;
- no silent 10,000-character truncation;
- duplicate rows across segment boundaries;
- incomplete/unreadable pages;
- prompt/config reload after policy changes;
- tenant-isolated catalogs and immunization policies;
- invalid model references;
- supersession and re-evaluation;
- JPG, JPEG, PNG, and PDF validation;
- immunization met/missing/uncertain results;
- advisory-only academic and health state transitions.

After mocked deterministic tests pass, run a live OpenRouter smoke test with a
synthetic, non-PII multi-page transcript and report only page/course counts,
timing, token usage, and completeness. Run the full API, preview, web,
Playwright, typecheck, lint, and CRM graph verification boundary afterward.

## Acceptance criteria

- A long transcript is processed page-by-page and does not stop at one text
  window.
- The synthetic long-transcript test returns every expected course or reports
  exact missing page segments; it never claims incomplete output is complete.
- Exemption recommendations are produced by a dedicated tenant-versioned
  prompt using current catalog and policy context.
- SQL no longer contains the exact-match recommendation decision.
- Updating academic policy in PostgreSQL causes the next mapping call to use
  the new revision without an application restart.
- Immunization requirements are tenant-configurable and visible before upload.
- Immunization results explicitly identify met, missing, uncertain, and
  not-applicable requirements with evidence.
- Every model result is traceable to prompt, schema, context, and policy
  versions.

