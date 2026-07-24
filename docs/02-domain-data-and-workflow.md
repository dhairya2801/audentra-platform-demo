# Domain Data and Workflow Engine

## 1. Domain goal

The domain model must support the student portal first while remaining the
source for future counselor, director, leader, and VP views. It must distinguish
official institutional facts, VV-owned workflow state, external provider state,
interaction signals, and AI recommendations.

## 2. Core entity groups

### Institution

```text
tenant
campus
academic_term
program
institution_policy_version
```

### Identity and people

```text
person
user_identity
student
staff_member
role_assignment
staff_assignment
family_relationship
consent_grant
```

### Admissions

```text
application
admission_offer
external_record_link
```

### Enrollment

```text
journey_definition
journey_definition_version
enrollment_journey
requirement_definition
requirement_definition_version
requirement_dependency
student_requirement
requirement_submission
```

### Documents, signatures, and payments

```text
file_object
document_record
document_review
signature_packet
signature_participant
payment_intent
payment_transaction
```

### Operations

```text
message_thread
message
notification
support_case
intervention
intervention_outcome
```

### Trust and intelligence

```text
audit_event
activity_event
outbox_event
inbox_event
risk_assessment
risk_signal
agent_run
agent_tool_call
model_usage
agent_recommendation
```

## 3. Common record fields

Tenant-owned business records normally include:

```text
id                  UUID/UUIDv7-style identifier
tenant_id           Required tenant boundary
created_at          timestamptz
updated_at          timestamptz
created_by          Actor/service identifier
updated_by          Actor/service identifier
version             Optimistic-concurrency number
```

Externally synchronized records additionally include:

```text
source_system
source_record_id
source_updated_at
source_version
last_synced_at
provenance
```

Core domain fields are typed columns. JSONB is limited to provider payloads,
versioned rule definitions, and non-query-critical extension metadata.

## 4. Data authority map

| Data | Initial authority | VV behavior |
|---|---|---|
| Application | CRM | Read synchronized copy |
| Admission offer | CRM | Display and submit acceptance command |
| Student onboarding profile | VV, with field-level sync rules | Collect drafts and synchronize approved fields |
| Requirement applicability | VV workflow engine | Calculate from versioned deterministic rules |
| Requirement completion | VV or designated institutional system | Record provenance and verification source |
| Official enrolled status | SIS | Never infer or overwrite directly |
| Deposit result | Payment provider | Store synchronized transaction/result |
| Signature status | E-sign provider | Store synchronized packet status |
| Files | VV object storage or institution DMS | Store metadata, security state, and provenance |
| FERPA delegation | VV/institution policy | Enforce scoped grants and revocation |
| Engagement activity | VV | Keep separate from official records |
| Agent recommendation | VV agent ledger | Advisory only until approved/executed |

Authority is defined per field where necessary. A generic "last write wins"
policy is not acceptable for official records.

## 5. Journey model

The student experience may look sequential, but the backend models a graph of
requirements and dependencies.

### Journey statuses

```text
created
in_progress
ready_for_review
submitted
on_hold
completed
cancelled
```

The journey is `completed` only after all required blocking requirements are
satisfied or waived and the official completion rule has been met.

### Requirement statuses

```text
not_applicable
blocked
ready
in_progress
submitted
under_review
completed
waived
rejected
expired
```

Typical transitions:

```text
blocked -> ready
ready -> in_progress
in_progress -> submitted
submitted -> under_review
under_review -> completed
under_review -> rejected
rejected -> in_progress
ready -> waived
completed -> expired       only when policy supports expiration
```

Every transition is performed by a named command and validated against a
transition policy. Direct status updates are prohibited.

## 6. Requirement definitions

A versioned requirement definition contains:

```text
code
title
description
definition_version
applies_when
blocking
dependencies
due_date_rule
submission_type
verification_type
responsible_office
policy_reference
active_from
active_until
```

Example:

```yaml
code: immigration_documentation
definition_version: 3
applies_when:
  all:
    - field: citizenship_status
      operator: equals
      value: international
blocking: true
due_date_rule:
  relative_to: term_start
  offset_days: -45
verification_type: international_office
```

Rules use a limited declarative language. Arbitrary JavaScript, SQL, prompts,
and model output are not accepted as workflow rules.

## 7. Definition versioning

When a journey starts:

1. Select the journey definition for tenant, campus, term, program, and student
   type.
2. Evaluate applicability rules.
3. Instantiate student requirements.
4. Record the exact definition versions used.
5. Preserve those versions for audit and reproducibility.

A later policy change does not silently rewrite active journeys. An explicit,
audited migration command is required.

## 8. Readiness calculation

Readiness is computed, not manually toggled:

```text
ready_for_review =
  every applicable blocking requirement is completed or waived
  AND no required verification is pending
  AND no blocking hold exists
```

The portal can show a completion percentage, but the percentage is not an
official decision. It is calculated from weighted or unweighted requirement
progress defined by the journey version.

## 9. Concurrency and autosave

Mutable records use optimistic concurrency:

```text
Client reads journey version 12
Client sends save command with expected version 12
Server transaction succeeds and returns version 13
```

If another session already produced version 13, the command returns a conflict.
The UI preserves the local draft, reloads current state, and asks the student to
review the conflict where automatic field-level merging is unsafe.

Each mutation includes an idempotency key. Repeating the same request after a
timeout returns the original result rather than duplicating an operation.

## 10. Domain event envelope

```json
{
  "event_id": "019...",
  "event_name": "requirement.submitted.v1",
  "occurred_at": "2026-07-24T14:30:00Z",
  "tenant_id": "tenant_...",
  "aggregate_type": "student_requirement",
  "aggregate_id": "requirement_...",
  "aggregate_version": 6,
  "actor": {
    "type": "student",
    "id": "person_..."
  },
  "correlation_id": "request_...",
  "causation_id": "command_...",
  "data": {
    "journey_id": "journey_...",
    "requirement_code": "identity_document",
    "submission_type": "document"
  }
}
```

Events contain the minimum data consumers require. Sensitive field values and
document contents are not copied into general event payloads.

## 11. Projection strategy

Operational tables remain normalized. Projectors create query-oriented views:

```text
student_portal_projection
student_engagement_snapshot
counselor_work_queue_projection
student_case_summary_projection
cohort_funnel_daily
requirement_bottleneck_daily
executive_enrollment_snapshot
```

Projection consumers are rebuildable from authoritative records/events. A
projection failure cannot change official enrollment state.

## 12. Data quality invariants

- A person may have multiple external identities without creating duplicate
  students.
- An external record link is unique within tenant and source system.
- A student requirement references an immutable definition version.
- A completed requirement records who/what verified it.
- A payment success references a verified provider event.
- A permission grant identifies subject, delegate, scopes, start, expiration,
  and revocation.
- Every agent recommendation references the snapshot and policy versions used.
- Every official status mutation produces an audit event.

