# Tenant AI Prompt Runtime Design

## Purpose

VV Edgent must support many universities whose prompts, academic catalogs,
equivalency policies, immunization requirements, and model choices change
without an application deployment. PostgreSQL is the source of truth for this
configuration. Application instances may cache compiled configuration, but
every AI call must verify that the cached revision is still current.

This design covers the shared runtime used by transcript extraction, transcript
merge, course-label normalization, exemption mapping, immunization evaluation,
and Edward student guidance.

## Core decisions

- Every AI operation has its own versioned system prompt, user prompt template,
  JSON output schema, context-selection policy, provider/model policy, and
  safety limits.
- Configuration is scoped by `tenant_id`; a prompt or policy from one
  university can never be selected for another.
- The runtime checks one lightweight database revision row before every model
  call.
- Cache invalidation compares a monotonically increasing revision and the
  database `updated_at`. Revision is authoritative; the timestamp is
  operational evidence.
- The full configuration is reloaded only when the revision differs from the
  cached revision.
- Each call freezes one validated configuration snapshot. A concurrent update
  affects the next call, never half of the current call.
- SQL persists configuration, context, and model results. Academic or health
  decisions are not encoded in SQL matching statements.
- High-impact operations fail closed when current configuration cannot be
  verified. Low-risk Edward guidance may use a bounded last-known-good snapshot
  and must label the run receipt as stale.

## Operations

The initial operation codes are:

| Code | Purpose |
|---|---|
| `student_document_classification` | Identify the actual document type from evidence |
| `transcript_segment_extraction` | Extract every readable course from a bounded page segment |
| `transcript_extraction_merge` | Deduplicate page-segment output without inventing courses |
| `course_label_normalization` | Normalize ambiguous source labels while preserving originals |
| `course_exemption_mapping` | Compare transcript evidence with tenant catalog and policy context |
| `immunization_record_extraction` | Extract vaccine, dose, and administration-date evidence |
| `immunization_compliance_evaluation` | Compare evidence with the active tenant health policy |
| `edward_student_guidance` | Answer permission-scoped student portal questions |

Adding an operation requires a new database configuration and an application
handler that supplies an allowlisted context shape. It does not require prompt
text in source code.

## Data model

### `ai_operation_config`

One active head row per tenant and operation:

- `tenant_id`
- `operation_code`
- `active_prompt_version_id`
- `active_context_policy_version_id`
- `active_output_schema_version_id`
- `provider`
- `model`
- `temperature`
- operation-specific token, page, image, and timeout limits
- `config_revision bigint`
- `updated_at`
- `updated_by`
- `enabled`

Unique key: `(tenant_id, operation_code)`.

`config_revision` increments in the same transaction whenever the active
prompt, schema, model policy, or relevant context bundle changes.

### `ai_prompt_template_version`

Immutable prompt content:

- `id`
- `tenant_id`
- `operation_code`
- `version`
- `system_template`
- `user_template`
- `change_summary`
- `created_by`
- `created_at`
- `activated_at`

Unique key: `(tenant_id, operation_code, version)`.

Prompt versions are never updated in place. Activating a new version changes
the operation head and increments `config_revision`.

### `ai_context_policy_version`

Immutable rules describing which context fields are allowed and their limits:

- `id`
- `tenant_id`
- `operation_code`
- `version`
- `policy jsonb`
- `created_at`

The policy defines sources, allowlisted fields, maximum rows/characters,
ordering, redactions, and whether the context may contain student-sensitive
data.

### `ai_output_schema_version`

Immutable JSON Schema used both in the provider request when supported and at
the server response boundary:

- `id`
- `tenant_id`
- `operation_code`
- `version`
- `schema jsonb`
- `created_at`

### `tenant_ai_runtime_revision`

A small, hot table used for per-call invalidation:

- `tenant_id`
- `operation_code`
- `revision bigint`
- `updated_at`
- `change_source`

Prompt activation and policy/catalog changes update affected rows in the same
transaction. Database triggers provide defense in depth for direct changes to
versioned academic and immunization configuration.

### `ai_runtime_config_checkpoint`

Operational visibility for application instances:

- `instance_id`
- `tenant_id`
- `operation_code`
- `last_seen_revision`
- `last_checked_at`
- `last_loaded_at`

Correctness does not depend on this table because an instance can disappear at
any time. It supports diagnostics requested by operators. Checkpoint writes are
coalesced so the application does not write once per model call.

### AI run receipt

Extend the existing provider-attempt journal, or add a linked
`ai_operation_run`, with:

- tenant, student, document, request, and operation identifiers
- prompt, context-policy, and output-schema version identifiers
- configuration revision and context-bundle version identifiers
- canonical context hash
- provider, requested model, response model, usage, finish reason, and timing
- `cache_status`: `reused`, `reloaded`, or `stale_last_known_good`
- normalized result hash and terminal status

Raw secrets are never included. Existing rules governing raw provider-response
retention continue to apply.

## Per-call algorithm

1. Resolve the authenticated tenant and operation.
2. Query `tenant_ai_runtime_revision` for the tenant/operation.
3. Compare the returned revision with the in-memory cache key
   `(tenant_id, operation_code)`.
4. If equal, reuse the immutable compiled snapshot.
5. If different or absent, read the operation head, prompt, context policy,
   output schema, and relevant policy heads in one repeatable-read transaction.
6. Validate templates, variables, schema, provider limits, and tenant
   ownership.
7. Compile and atomically replace the cache entry.
8. Select and bound context using the compiled policy.
9. Render the operation-specific prompt with explicit untrusted-data
   delimiters.
10. Call the provider and validate the structured response.
11. Persist the run receipt and normalized domain proposal.

A call that began before a configuration transaction commits may finish with
the prior revision. Its receipt identifies that revision. The next call sees
the committed revision and reloads.

## Context engineering

- Templates may reference only declared variables.
- Each operation has a typed context builder; arbitrary database rows cannot be
  interpolated.
- Student-provided text, document text, chat history, catalog descriptions,
  and administrator-authored policy prose are delimited and labeled as data.
- Academic context includes the selected program, active catalog version,
  program requirements, prerequisites, active equivalency-policy version,
  relevant rules, prior recommendation outcomes, and transcript evidence.
- Immunization context includes only the active policy and minimum vaccine
  evidence required for evaluation. Diagnoses and unrelated medical data are
  excluded.
- Large catalogs and transcripts are divided into deterministic batches. Each
  batch records its source identifiers and is merged by a separate prompt.
- The model may propose; it cannot approve an exemption, alter a record, or
  finalize health clearance.

## Configuration mutation

An activation service validates a new prompt/configuration before switching the
active head:

1. Validate template syntax and allowed variables.
2. Validate JSON Schema and provider compatibility.
3. Run stored contract fixtures for the operation.
4. Insert immutable version rows.
5. Switch the active head and increment runtime revision transactionally.
6. Emit an audit and outbox event.

The current project has only student authentication. A university-facing admin
console is outside this delivery. Seed scripts and an internal service boundary
will use the same activation path so a future staff application does not bypass
validation.

## Failure behavior

- Missing or disabled operation: do not call a model; return an
  operation-specific unavailable state.
- Invalid newly selected version: reject activation, retain the prior active
  version, and emit an operational alert.
- Database unavailable with no cache: fail the operation.
- Database unavailable with cache:
  - exemption and immunization operations fail closed;
  - document extraction pauses and remains retryable;
  - Edward may use last-known-good configuration only within its configured
    maximum stale interval.
- Provider failure: retain the immutable source, record the attempt, and expose
  a retryable or non-retryable student-safe status.
- Invalid structured output: reject it at the boundary; never partially import
  it.

## Security and tenancy

- Every configuration and policy query includes `tenant_id`.
- Context hashes and receipts allow reconstruction without exposing secrets in
  logs.
- Prompt templates cannot reference environment variables, credentials,
  filesystem paths, or unrestricted URLs.
- Provider API keys remain server configuration; universities select an
  allowlisted provider/model policy, not raw credentials through prompts.
- Prompt changes, activations, and model calls create audit records.

## Acceptance criteria

- Changing an active prompt or policy in PostgreSQL increments the affected
  revision.
- The first call after commit loads the new revision without restarting an
  application instance.
- Unchanged calls reuse the compiled configuration after the lightweight head
  check.
- Concurrent tenants can use different prompt and policy versions without
  leakage.
- Each model result identifies its prompt, schema, context-policy, catalog,
  equivalency-policy, or immunization-policy versions as applicable.
- Invalid configuration cannot replace the last valid active version.
- Tests prove cache reuse, reload, failure behavior, multi-tenant isolation,
  and run-receipt completeness.

