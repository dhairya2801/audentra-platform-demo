# Deployment, Testing, and Implementation Roadmap

## 1. Environments

```text
local        Developer machine with disposable local dependencies
test         Automated integration and end-to-end tests
preview      Per-change review environment where practical
staging      Production-like integrations and configuration
production   Institution-approved runtime and data
```

The same container images are promoted between environments. Environment
differences are runtime configuration and secrets, not code branches.

## 2. Local Docker Compose

Backend repository services:

```text
postgres
minio
minio-init
migrate (one shot)
api (FastAPI)
worker (headless Python process)
keycloak
mailpit
```

Run the portals from the separately deployed `Audentra-portals` repository.
They are not built into this Compose stack.

Optional development profiles may add:

```text
mock-webhook-sender
metrics/dashboard stack
document scanner
```

Local startup requirements:

- one documented command;
- dependency health checks and API liveness/readiness;
- checksummed schema migration before API/worker startup;
- no cloud credentials;
- realistic external-provider mock behavior.

## 3. Container requirements

- Multi-stage, reproducible builds
- Non-root runtime user
- Read-only root filesystem where practical
- No persistent state in container filesystem
- Runtime configuration validation
- Graceful SIGTERM shutdown
- API readiness and liveness endpoints
- Headless worker process supervision and outbox-lag/retry metrics
- Bounded memory/CPU behavior
- Structured stdout/stderr logging
- Software bill of materials and vulnerability scan

## 4. Kubernetes shape

```text
Portal ingress / deployment (owned by Audentra-portals)

Backend ingress / gateway
  -> FastAPI Deployment

Python worker Deployment (no HTTP Service)
migration Job (same image, audentra-migrate command)
scheduled maintenance CronJobs where needed

External/managed:
  PostgreSQL
  object storage
  identity provider
  secret manager
  telemetry backend
```

Kubernetes resources:

- Deployments for API and worker; portal resources are released separately
- an API Service and Ingress (the worker is headless)
- ConfigMaps for non-secret runtime configuration
- ExternalSecret/approved secret integration
- HorizontalPodAutoscalers
- Pod disruption budgets where required
- NetworkPolicies
- resource requests and limits
- service accounts with least privilege

Database migrations run once as a deployment job using the same immutable
Python image as the API, development seed, and worker entry points. They do not
run independently inside every API replica. The compatibility seed is limited
to development/test environments and fails closed in production.

## 5. Database migration policy

- Migrations are committed and reviewed.
- Production migrations are forward-compatible with the currently running
  application during rolling deployment.
- Destructive changes use expand/migrate/contract phases.
- Large backfills run as monitored jobs.
- Every release tests migration from the previous production schema.
- Rollback plans distinguish application rollback from irreversible data
  migration.

## 6. CI pipeline

Configured repository CI:

```text
Ruff lint and formatting check
strict mypy over src and tests
checksummed migration and deterministic seed application against PostgreSQL 17
pytest unit, contract, PostgreSQL/MinIO integration, OpenAPI/parity, and coverage tests
remaining Node contract/tool typecheck, tests, graph drift check, and build
production Node dependency audit
shared Python image build, entry-point check, and non-root-user check
```

The release pipeline should additionally add an image/SBOM vulnerability scan.
End-to-end browser and accessibility suites are owned by `Audentra-portals`; a
release should run their contract-compatible suite against the candidate
backend.

No deployment proceeds when generated API clients, migrations, or event schemas
are inconsistent.

## 7. Test strategy

### Unit tests

- workflow rules and transitions;
- due-date calculations;
- permission policies;
- priority calculations;
- event schema validation;
- agent trigger/routing policies.

### Integration tests

- repositories against real PostgreSQL;
- row-level security;
- transactional outbox atomicity;
- object storage signing/metadata;
- identity/session integration;
- webhook verification and deduplication;
- adapter contract behavior.

### End-to-end tests

Critical student paths:

```text
sign in
accept offer
save/resume profile
complete prerequisite
upload/review document
grant/revoke permission
complete mock deposit
create support case
```

Future staff paths:

```text
open assigned queue
review student case
create intervention
send approved message
view team/cohort projections
```

### Resilience tests

- duplicate request/event;
- out-of-order webhook;
- provider timeout;
- worker restart;
- stale optimistic-concurrency version;
- projection delay/rebuild;
- model outage/budget exhaustion;
- identity session expiration during save.

## 8. Observability and service objectives

Initial service indicators:

```text
portal/API availability
API latency/error rate
successful save rate
outbox processing lag
projection freshness
integration success/latency
upload processing duration
agent success/cost/grounding
```

Critical alerts:

- cross-tenant authorization anomaly;
- audit write failure;
- payment/signature webhook backlog;
- outbox lag above threshold;
- projection stale beyond threshold;
- excessive integration retries/dead letters;
- model cost or validation circuit breaker;
- database capacity/backup failure.

## 9. Implementation phases

### Phase 0 — Specification and backend foundation

Deliverables:

- architecture and logic-flow documentation;
- separately deployable backend and portals repositories;
- locked Python dependencies plus formatting/lint/typecheck;
- Docker Compose;
- PostgreSQL migrations;
- typed configuration;
- health endpoints;
- independent FastAPI and Python worker entry points from one image;
- CI baseline.

Exit:

```text
api, worker, and dependencies start locally
checksummed migrations succeed
API liveness and PostgreSQL readiness pass
the headless worker drains committed outbox events
backend CI and cross-repository contract checks pass
```

### Phase 1 — First production-quality vertical slice

Scope:

```text
OIDC/mock sign-in
tenant/student resolution
dashboard shell
admission offer
offer acceptance
journey creation
initial requirement projection
audit + outbox
activity ingestion
```

Exit:

```text
accepting an offer survives refresh
PostgreSQL reflects authoritative state
audit and domain event exist
dashboard projection updates
duplicate acceptance is safe
authorization tests pass
```

### Phase 2 — Enrollment workflow

Scope:

```text
versioned journey/requirement definitions
profile verification/autosave
dependency engine
requirement submission/review
documents with mock scan/review
signature mock adapter
payment mock adapter
family permissions
messages/help
```

Exit:

```text
all current onboarding steps use API/database state
no production component contains dummy arrays
save/conflict/retry/offline states are implemented
critical student end-to-end suite passes
```

### Phase 3 — Staff operations and real integrations

Scope:

```text
counselor queue projection
support cases
intervention playbooks
staff messaging/notes
CRM/SIS sandbox adapters
real identity/object storage/messaging as selected
```

Exit:

```text
student action appears in assigned staff queue
staff action is audited
adapter contract tests pass
sync failures are operationally visible
```

### Phase 4 — Controlled agent features

Scope:

```text
agent gateway
policy ingestion/retrieval
requirement explanation
stuck-student help
counselor summary
usage/cost/outcome ledger
budgets and circuit breakers
```

Exit:

```text
no agent can directly mutate official state
grounding and authorization tests pass
cost and usefulness are measurable
deterministic fallback works during provider outage
```

### Phase 5 — Leader and VP intelligence

Scope:

```text
director/team projection
leader funnel/bottleneck views
intervention effectiveness
executive snapshot/forecast inputs
aggregate narrative
```

Exit:

```text
hierarchy totals reconcile
small-cell/privacy rules pass
metrics trace to operational events
AI narratives trace to validated aggregates
```

### Phase 6 — Production hardening and Kubernetes

Scope:

```text
production infrastructure
managed PostgreSQL/object storage
backup/PITR validation
load/resilience/security testing
network/secret policies
autoscaling and disruption behavior
operational runbooks
```

Exit:

```text
staging production-readiness review passes
restore test passes
incident runbooks are exercised
service objectives and alerts are active
```

## 10. FastAPI cutover and release sequence

Use this order to finish and verify the backend rewrite without changing domain
semantics:

1. Resolve the locked Python 3.12 environment with `uv sync --directory
   apps/api --locked --all-groups`.
2. Keep HTTP concerns in `interfaces/http` and domain/application code free of
   FastAPI imports.
3. Apply the existing checksummed SQL migrations with `npm run db:migrate` or
   `audentra-migrate`.
4. Verify all compatibility operations through OpenAPI and route-parity tests.
   The current FastAPI compatibility surface has 42 operations; any additional
   preview-only routes remain a release gap until migrated or deliberately
   versioned out of the contract.
5. Run Ruff, strict mypy, and the complete pytest suite, including performance
   guards and document/AI safety cases.
6. Run integration tests against real PostgreSQL and S3-compatible storage.
7. Exercise duplicate requests, outbox redelivery, worker restart, leases,
   retries, and dead-letter behavior.
8. Build the shared image and start PostgreSQL, MinIO, migration, API, and
   worker roles through Compose.
9. Verify `/health`, PostgreSQL-backed `/health/ready`, graceful shutdown, and
   worker process/outbox-lag monitoring.
10. Run the portals' critical browser flows against the candidate API.
11. Implement and verify the institutional identity adapter. Production must
    continue to fail closed while `AUTH_MODE=demo` is the only composition.
12. Promote exactly the tested image, selecting `audentra-api`,
    `audentra-worker`, or `audentra-migrate` by deployment command.

## 11. Definition of done

A production feature is done only when:

- API/domain behavior is implemented outside the UI;
- database migration exists;
- authorization and tenant isolation are tested;
- idempotency/concurrency behavior is defined;
- audit/domain/activity events are correct;
- failure, retry, conflict, empty, and loading states exist;
- telemetry is present without sensitive data;
- accessibility checks pass;
- mock and real adapter contracts are compatible;
- documentation and OpenAPI contracts are updated;
- end-to-end critical path passes.

