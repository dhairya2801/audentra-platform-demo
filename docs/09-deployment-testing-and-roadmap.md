# Deployment, Testing, and Implementation Roadmap

## 1. Environments

```text
local        Developer machine, deterministic seed data
test         Automated integration and end-to-end tests
preview      Per-change review environment where practical
staging      Production-like integrations and configuration
production   Institution-approved runtime and data
```

The same container images are promoted between environments. Environment
differences are runtime configuration and secrets, not code branches.

## 2. Local Docker Compose

Initial services:

```text
web
api
worker
postgres
minio
keycloak
mailpit
otel-collector
```

Optional development profiles may add:

```text
mock-webhook-sender
metrics/dashboard stack
document scanner
```

Local startup requirements:

- one documented command;
- health checks;
- schema migration;
- deterministic seed selection;
- no cloud credentials;
- realistic external-provider mock behavior.

## 3. Container requirements

- Multi-stage, reproducible builds
- Non-root runtime user
- Read-only root filesystem where practical
- No persistent state in container filesystem
- Runtime configuration validation
- Graceful SIGTERM shutdown
- Readiness and liveness endpoints
- Bounded memory/CPU behavior
- Structured stdout/stderr logging
- Software bill of materials and vulnerability scan

## 4. Kubernetes shape

```text
Ingress / gateway
  -> web Deployment
  -> api Deployment

worker Deployment
migration Job
scheduled maintenance CronJobs where needed

External/managed:
  PostgreSQL
  object storage
  identity provider
  secret manager
  telemetry backend
```

Kubernetes resources:

- Deployments for web/API/worker
- Services
- Ingress
- ConfigMaps for non-secret runtime configuration
- ExternalSecret/approved secret integration
- HorizontalPodAutoscalers
- Pod disruption budgets where required
- NetworkPolicies
- resource requests and limits
- service accounts with least privilege

Database migrations run once as a deployment job. They do not run independently
inside every API replica.

## 5. Database migration policy

- Migrations are committed and reviewed.
- Production migrations are forward-compatible with the currently running
  application during rolling deployment.
- Destructive changes use expand/migrate/contract phases.
- Large backfills run as monitored jobs.
- Every release tests migration from the previous production schema.
- Rollback plans distinguish application rollback from irreversible data
  migration.
- Seed data never runs in production.

## 6. CI pipeline

Recommended checks:

```text
format/lint
TypeScript typecheck
unit tests
architecture/boundary tests
API contract generation drift
database migration validation
integration tests with real PostgreSQL
adapter contract tests
end-to-end browser tests
accessibility tests
dependency/security scanning
container build and scan
```

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

### Phase 0 — Specification and project foundation

Deliverables:

- architecture and logic-flow documentation;
- monorepo;
- formatting/lint/typecheck;
- Docker Compose;
- PostgreSQL migrations;
- typed configuration;
- health endpoints;
- CI baseline.

Exit:

```text
web, api, worker, and dependencies start locally
empty migration succeeds
health checks pass
CI runs without product functionality
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

## 10. First build sequence

Recommended first implementation order:

1. Create pnpm/Turborepo workspace.
2. Add `apps/web`, `apps/api`, and `apps/worker`.
3. Add shared configuration, contracts, and observability packages.
4. Add Docker Compose with PostgreSQL, MinIO, Keycloak, and Mailpit.
5. Add first migration: tenant, person, identity, student, application, offer.
6. Add seed scenario for one admitted student.
7. Implement mock sign-in/session resolution.
8. Implement `GetStudentDashboard`.
9. Implement `AcceptAdmissionOffer` transaction.
10. Add audit and outbox records.
11. Add worker projector for the student dashboard.
12. Add typed activity event ingestion.
13. Build the first end-to-end test.

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

