# System Architecture

## 1. Architectural decision

Use a modular monolith with the portals deployed from their own repository and
two independently scalable backend processes built from one Python image:

```text
Student / Staff / Leader / VP
              |
              v
   Audentra portals application
              |
              v
        FastAPI HTTP adapter
               |
               v
  Framework-neutral application/domain core
           |       |       |
           v       v       v
     PostgreSQL  Object   Transactional
                 storage  outbox
                            |
                            v
                    Python outbox worker
                      |    |    |
                      v    v    v
                    CRM  Comms  Read models / AI
```

The modular monolith is an operational choice, not permission to mix business
logic together. FastAPI is an inbound adapter; business use cases depend on
Python ports and domain types rather than web-framework objects. PostgreSQL,
S3-compatible storage, AI providers, and the outbox worker are outbound or
process adapters around that core.

## 2. Why this baseline

Enrollment operations contain multi-record transactions. Accepting an offer may
need to update the offer, create a journey, instantiate requirements, write an
audit record, and publish an event as one reliable operation. Keeping those
changes in one PostgreSQL transaction is safer while the domain is still being
discovered.

Starting with microservices would immediately introduce:

- network failures inside core business workflows;
- eventual-consistency behavior visible to students;
- service-to-service identity and authorization;
- distributed tracing and contract versioning;
- duplicate-delivery and ordering concerns;
- more deployment, monitoring, and incident-response surface;
- uncertain boundaries that are expensive to reverse.

The chosen structure preserves future extraction. A module becomes a service
only when an observed need justifies the operational cost.

## 3. Deployable components

### 3.1 Web application

Responsibilities:

- render student, staff, leader, and VP experiences;
- manage authenticated browser sessions;
- call the application API through generated OpenAPI clients;
- provide accessible forms, autosave feedback, and offline-safe drafts;
- emit allowlisted product activity events;
- never contain authoritative enrollment rules.

Proposed technology:

- Next.js and React;
- TypeScript;
- TanStack Query for server state;
- React Hook Form for form state;
- Zod for client-side contract validation;
- shared UI package for the design system.

Protected portal routes are dynamic. Static/CDN caching is reserved for
versioned public assets, not student-specific HTML or API responses.

### 3.2 Application API

Responsibilities:

- authenticate and authorize every request;
- validate API contracts;
- execute business use cases;
- own transactional consistency;
- calculate requirement applicability and readiness;
- issue signed upload URLs;
- record audit events;
- write domain events to the transactional outbox;
- expose query endpoints for portal read models;
- mediate every agent tool call.

Current backend technology:

- Python 3.12 and FastAPI/ASGI with REST endpoints documented through OpenAPI;
- Pydantic v2 request, response, and startup configuration validation;
- PostgreSQL through async SQLAlchemy Core and `asyncpg`, with bounded pools
  and statement timeouts;
- explicit, checksummed SQL migrations executed by `audentra-migrate`;
- framework-neutral application/domain layers suitable for reuse from other
  Python entry points.

Blocking production constraint: the only implemented identity composition is
`AUTH_MODE=demo`. Setting `AUDENTRA_ENV=production` fails closed until an
institutional identity adapter is implemented; the presence of local Keycloak
does not remove that gate.

### 3.3 Background worker

Responsibilities:

- consume outbox events;
- send email/SMS/push notifications;
- synchronize CRM/SIS records;
- process payment and e-signature webhook results;
- scan and classify documents;
- update query projections;
- evaluate deterministic trigger rules;
- schedule controlled agent runs;
- retry transient failures with bounded backoff;
- move permanent failures to a dead-letter/operations queue.

The Python worker uses the same application/domain code and release image as
the API but starts through `audentra-worker`, so it can be scaled and restarted
independently. It is a headless process with no HTTP listener. Blocking S3 SDK
calls and CPU-heavy document preprocessing are moved off the asyncio event loop
with `asyncio.to_thread`.

### 3.4 PostgreSQL

PostgreSQL is used locally and in production. SQLite is not used.

Logical schemas:

```text
core          Tenants, identities, people, programs, terms
admissions    Applications and offers
enrollment    Journeys, requirements, submissions
operations    Support cases, messages, interventions
integrations  External links, inbox, outbox, sync state
audit         Sensitive access and change records
analytics     Activity events and read projections
agent         Runs, tools, usage, recommendations, outcomes
```

These schemas express ownership. They do not require separate databases during
the first product phase.

### 3.5 Object storage

- Local: MinIO
- Production: institution-approved S3-compatible service

The application stores file metadata in PostgreSQL and file bytes in object
storage. The current compatibility route accepts a bounded multipart upload and
the server writes the immutable original through its S3 adapter. Short-lived
signed browser uploads remain a future scaling option. Uploaded content remains
unavailable to users and agents until the applicable review and security gates
complete.

### 3.6 Identity provider

Authentication is isolated behind an application port so an OIDC adapter can be
introduced without changing domain use cases.

- Current implementation: explicit demo identity adapter
- Local integration target: Keycloak
- Production: institutional OIDC/SAML federation or an approved identity
  platform

The identity provider proves who the user is. VV remains responsible for tenant
membership, role assignment, student ownership, staff assignment, and delegated
FERPA access.

## 4. Internal module boundaries

Proposed API modules:

```text
Identity and Access
Institution Configuration
Student Profile
Admissions
Enrollment Journey
Requirements
Documents
Signatures
Payments
Relationships and Permissions
Messages and Notifications
Support Cases
Interventions
Integrations
Audit
Analytics Projections
Agent Gateway
```

The current Python package expresses these boundaries as:

```text
apps/api/src/audentra/
  domain/            Entities, policies, and domain behavior
  application/       Framework-neutral use cases
  core/              Ports, authorization context, shared errors
  contracts/         Pydantic request/response contracts
  interfaces/http/   FastAPI routes, middleware, dependency adapters
  interfaces/worker/ Python worker entry point
  infrastructure/   PostgreSQL, storage, outbox, documents, worker adapters
  integrations/ai/  Bounded provider gateways and validation
  bootstrap/         Production composition and validated settings
```

Rules:

- Controllers do not contain business decisions.
- React components do not contain domain decisions.
- Infrastructure adapters do not bypass application authorization.
- Modules do not update another module's tables directly.
- Cross-module behavior occurs through application services or domain events.
- Shared packages contain stable contracts and primitives, not a miscellaneous
  `utils` collection.

## 5. Request lifecycle

Every consequential API request follows:

```text
HTTP request
  -> request/correlation ID
  -> authentication
  -> tenant resolution
  -> role + relationship authorization
  -> schema validation
  -> application use case
  -> database transaction
       -> domain records
       -> audit record
       -> outbox event
  -> response mapping
  -> structured telemetry
```

The domain change, audit record, and outbox event commit together. This prevents
the system from changing official state without leaving an audit/event trail.

## 6. Command and query model

Use explicit commands:

```text
AcceptAdmissionOffer
SaveStudentProfile
SubmitRequirement
GrantFamilyPermission
CreateDocumentUpload
CompleteDocumentUpload
CreateDepositIntent
CreateSupportCase
ApproveIntervention
```

Use explicit queries:

```text
GetStudentDashboard
GetEnrollmentJourney
GetRequirementDetails
GetStudentMessages
GetCounselorWorkQueue
GetCohortFunnel
GetExecutiveEnrollmentSnapshot
```

This is pragmatic command/query separation. It does not require a complex CQRS
framework.

## 7. Event-driven behavior

The transactional outbox holds durable domain events:

```text
admission.offer_accepted.v1
enrollment.journey_created.v1
enrollment.profile_saved.v1
requirement.submitted.v1
requirement.verified.v1
document.upload_completed.v1
payment.completed.v1
consent.granted.v1
support.case_created.v1
intervention.completed.v1
```

Consumers are idempotent. Each consumer records an event/consumer pair so the
same delivery cannot produce duplicate side effects.

## 8. Current backend repository structure

```text
apps/
  api/
    src/audentra/
    assets/
    migrations/
    tests/

packages/
  contracts/                 Retained browser/event compatibility contracts
  state-effects/             Field ownership and effect registry
  document-preprocessing/    Retained compatibility tooling/tests

tools/
  demo-api/                  Development-only preview adapter

infra/
  compose.yaml
  docker/api.Dockerfile      Shared migration/API/worker image

docs/
```

The React/Next.js portals and their browser tests live in the separate
`Audentra-portals` repository. Keeping the HTTP adapter under `interfaces/http`
and the worker under `interfaces/worker` lets future Python frameworks invoke
the same application services without importing FastAPI route objects.

## 9. Microservice extraction rules

A module becomes a microservice when one or more of these is demonstrated:

- it requires independent horizontal scaling;
- it has a materially different security/compliance boundary;
- it must deploy independently at a different cadence;
- it has different availability or latency requirements;
- it is owned by a separate stable engineering team;
- it requires different infrastructure or runtime technology;
- measured performance or reliability cannot be resolved within the modular
  application.

Likely future candidates:

- document scanning/OCR;
- high-volume notifications;
- AI gateway;
- CRM/SIS integration hub;
- analytics pipeline.

Kubernetes alone is not a reason to extract a service.

## 10. Architecture invariants

- All application containers are stateless.
- No uploaded file depends on a container filesystem.
- No background job depends on an API process remaining alive.
- No agent has direct database, general HTTP, or CRM access.
- No browser event can change official status.
- No integration callback is processed without signature verification and
  deduplication.
- No tenant-specific secret is exposed to browser JavaScript.

