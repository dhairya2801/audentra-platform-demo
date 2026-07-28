# VV Enrollment Platform — Engineering Design

This directory is the engineering specification for the VV student enrollment
platform. It translates the product PDFs and the existing onboarding experience
into implementable system boundaries, workflows, events, data ownership, and
delivery phases.

## Status

- Architecture status: implemented baseline with an explicit production target
- Implementation status: functional student portal preview under active development
- Current product surface: student portal
- Not yet implemented: counselor, director, enrollment leader, VP of enrollment
- Local runtime target: Docker Compose
- Production runtime target: Kubernetes
- Deployed preview: hardened Google Compute Engine `e2-micro`

## Architecture in one paragraph

VV starts as a modular monolith with three independently deployable processes:
a Next.js web application, a NestJS application API, and a background worker.
PostgreSQL is the source of truth, S3-compatible object storage holds documents,
and a transactional outbox connects business changes to notifications,
integrations, read models, and controlled AI workflows. Modules are designed so
that a boundary can be extracted into a microservice later when scaling,
security, ownership, or deployment pressure justifies it.

## Documentation map

1. [System architecture](./01-system-architecture.md)
2. [Domain data and workflow engine](./02-domain-data-and-workflow.md)
3. [Student portal feature flows](./03-student-portal-feature-flows.md)
4. [Tracking, observability, and audit](./04-tracking-observability-and-audit.md)
5. [Agentic flows and cost controls](./05-agentic-flows-and-cost-controls.md)
6. [Staff, leader, and VP flows](./06-staff-leader-vp-flows.md)
7. [Integrations and dummy-to-real strategy](./07-integrations-and-dummy-to-real.md)
8. [Security, privacy, and authorization](./08-security-privacy-and-authorization.md)
9. [Deployment, testing, and implementation roadmap](./09-deployment-testing-and-roadmap.md)
10. [Student portal functional acceptance](./10-student-portal-acceptance.md)
11. [Agentic runtime: Edward and document extraction](./11-agentic-runtime.md)
12. [Student domain state map](./12-student-domain-state-map.md)
13. [CRM Change Graph](./13-crm-change-graph.md)
14. [Current implementation map](./14-current-implementation-map.md)
15. [Domain model and object reference](./15-domain-model-reference.md)
16. [Deployment, security, and CI/CD runbook](./16-deployment-security-and-cicd.md)
17. [Onboarding question alignment](./17-onboarding-question-alignment.md)
18. [Browser misuse and edge testing](./18-browser-misuse-and-edge-testing.md)
19. [Course exemption runtime skill](./agent-skills/course-exemption-skill.md)
20. [Multi-tenant student portal deployment](./architecture/multi-tenant-student-portal.md)

For a new engineer, read documents 14, 15, and 16 first. Documents 1–13
explain the architectural decisions, domain workflows, and longer-term design
in greater depth.

## Codex continuation artifacts

- [Portable continuation handoff](../CODEX_RESUME.md)
- [Sanitized visible session history](./codex-session-visible-history.md)

Use the handoff first on another computer. The history is a reference for
earlier product discussions; it is not a replacement for the current code and
architecture documents.

## Non-negotiable engineering principles

1. The browser never decides official enrollment state.
2. AI never decides eligibility, permissions, payments, or official status.
3. Every official change is validated and committed by the application API.
4. Every consequential change is auditable.
5. Every external operation is idempotent and retry-safe.
6. Dummy behavior is implemented through adapters and seeded database
   scenarios, not hard-coded component data.
7. Student, staff, leader, and VP views are projections of the same underlying
   records and events.
8. Tenant isolation and relationship-based authorization are enforced at every
   sensitive boundary.
9. Operational data, product analytics, audit records, technical telemetry, and
   AI execution history remain separate.
10. Kubernetes readiness is achieved through stateless containers and external
    state, not by prematurely splitting the domain into microservices.

## Terminology

- **Activity event:** An untrusted or low-trust interaction signal, such as
  viewing a page or opening help.
- **Domain event:** A trusted server-generated fact, such as an accepted offer
  or verified requirement.
- **Audit event:** An append-only record of sensitive access or a consequential
  action.
- **Projection/read model:** A query-optimized view derived from operational
  records and domain events.
- **Agent trigger:** A deterministic rule that identifies a candidate AI use.
- **Intervention:** A message, recommendation, support action, or other attempt
  to improve a student outcome.
- **Requirement:** A versioned enrollment prerequisite that may apply to a
  student based on deterministic rules.

## Source material

- `../VV Enrollment Summary.pdf`
- `../(2) VV Enrollment Roadmap @Today _ Notion.pdf`
- `../VV Product Notes-Zaibis (2).pdf`
- Existing live onboarding prototype supplied by the product owner
