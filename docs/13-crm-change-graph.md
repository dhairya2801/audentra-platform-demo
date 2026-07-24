# CRM Change Graph

## Decision

The project uses three complementary layers:

1. **CodeGraphContext** indexes TypeScript files, symbols, imports, call chains,
   and structural dependencies for developer and AI navigation.
2. **The typed State Effect Registry** is the authoritative declaration of CRM
   field ownership, reads, writes, emitted or consumed events, idempotency, and
   transaction boundaries.
3. **OpenTelemetry, `audit_event`, and `outbox_event`** provide runtime evidence
   for what actually happened to a student record.

CodeGraphContext is deliberately not treated as runtime data lineage. Static
code can show that a handler *may* call another function. Only a correlated
trace, audit record, and outbox envelope can prove that a particular execution
changed a particular student.

## Architecture

```mermaid
flowchart LR
  TS["TypeScript source"] --> CGC["CodeGraphContext static graph"]
  REG["Typed State Effect Registry"] --> CHECKS["CI semantic checks"]
  REG --> JSON["Generated JSON graph"]
  REG --> MMD["Generated Mermaid graph"]
  API["Runtime command"] --> OTEL["OpenTelemetry span"]
  API --> TX["Domain transaction"]
  TX --> AUDIT["audit_event"]
  TX --> OUTBOX["outbox_event"]
  OTEL -. "correlationId + traceId + effectId" .-> AUDIT
  OTEL -. "correlationId + traceId + effectId" .-> OUTBOX
  CGC -. "possible call paths" .-> REVIEW["Developer / AI review"]
  JSON --> REVIEW
  AUDIT --> REVIEW
  OUTBOX --> REVIEW
```

## Authoritative registry

The versioned source is
`packages/state-effects/src/registry.ts`. Each state effect declares:

- one stable effect ID and owning domain;
- its concrete TypeScript handler;
- fields read and written;
- events emitted and consumed;
- synchronous effect calls;
- the deduplication or optimistic-concurrency contract;
- the transaction/outbox boundary.

The generated artifacts are:

- [`generated/crm-change-graph.json`](./generated/crm-change-graph.json), for
  internal CRM visualization and bounded AI context;
- [`generated/crm-change-graph.mmd`](./generated/crm-change-graph.mmd), for
  architecture documentation.

Run `npm run crm:graph` after editing the registry. CI runs
`npm run crm:graph:check` and fails when:

- two domains claim the same field;
- the synchronous effect graph contains a cycle;
- an event handler has no event-scoped deduplication key;
- an effect writes a field without a declared owner;
- generated graph artifacts are stale.

## Runtime correlation

The API accepts `X-Correlation-Id` (falling back to `X-Request-Id` or a
server-generated ID), starts an OpenTelemetry server span, and returns:

- `X-Correlation-Id`
- `X-Request-Id`
- `X-Trace-Id`

Consequential commands commit the domain change, audit record, and outbox
record together. Audit metadata and outbox envelopes carry a `lineage` object:

```json
{
  "correlationId": "request-or-workflow-id",
  "traceId": "OpenTelemetry trace id",
  "spanId": "OpenTelemetry span id",
  "effectRegistryVersion": 1,
  "effectId": "financials.selectPaymentPlan"
}
```

This is safe linkage metadata, not student content. A support or CRM
investigation can start with any one of these IDs and reconcile the trace,
append-only audit fact, and publishable domain event.

## CodeGraphContext workflow

The repository includes `.cgcignore` and uses a local KuzuDB graph under the
gitignored `tmp/` directory:

```bash
npm run cgc:index
npm run cgc:update
npm run cgc:report
```

The first command creates the initial index, the second refreshes an existing
index after code changes, and the third writes a static architecture report to
`docs/generated/codegraphcontext-report.md`. Developers can also use
CodeGraphContext CLI/MCP call-chain and dependency queries while reviewing a
registry change.

CodeGraphContext remains advisory: a static edge never authorizes a cross-domain
write, replaces an idempotency key, or proves runtime execution.

## Evolution

Keep this as a versioned package while the domain boundaries are changing. A
graph database for business lineage is justified later only if the registry and
runtime evidence become too large for generated JSON and ordinary SQL queries.
At that point Neo4j can ingest the same stable IDs; it should not replace the
registry or the audit/outbox facts.
