# System Architecture

## 1. Architectural decision

Build a modular monolith first, with three deployable processes:

```text
Student / Staff / Leader / VP
              |
              v
      Next.js web application
              |
              v
       NestJS application API
          |       |       |
          v       v       v
    PostgreSQL  Object   Transactional
                storage  outbox
                           |
                           v
                    Background worker
                      |    |    |
                      v    v    v
                    CRM  Comms  Read models / AI
```

The modular monolith is an operational choice, not permission to mix business
logic together. Internally, every module has explicit application, domain, and
infrastructure boundaries.

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

Proposed technology:

- NestJS with Fastify;
- REST endpoints documented through OpenAPI;
- PostgreSQL;
- Drizzle plus explicit SQL migrations;
- typed configuration validated at startup.

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

The worker uses the same domain packages as the API but runs independently, so
it can be scaled separately.

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
storage. The browser uploads through short-lived signed URLs. Uploaded content
remains unavailable to users and agents until security scanning completes.

### 3.6 Identity provider

Authentication is externalized behind OIDC.

- Local/demo provider: Keycloak
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

Each module contains:

```text
module/
  domain/           Entities, value objects, policies, domain events
  application/      Commands, queries, use cases, ports
  infrastructure/   SQL repositories, external adapters
  presentation/     HTTP controllers and response mappers
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

## 8. Proposed repository structure

```text
apps/
  web/
  api/
  worker/

packages/
  contracts/
  domain/
  db/
  ui/
  config/
  observability/
  testing/

infra/
  compose/
  kubernetes/

docs/
```

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

