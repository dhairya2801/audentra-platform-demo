# Tracking, Observability, and Audit

## 1. Tracking model

The platform keeps five types of records separate:

```text
1. Activity events       What the user appeared to do in the UI
2. Domain events         What officially happened in the business domain
3. Audit events          Who accessed or changed sensitive resources
4. Technical telemetry  How the software behaved
5. Agent records         Why and how an AI execution occurred
```

Mixing these categories creates security, analytics, and product errors. A page
view cannot prove completion; an application log is not an audit trail; an
agent response is not an official decision.

## 2. End-to-end tracking flow

```text
Browser interaction
  -> typed tracking hook
  -> in-memory event buffer
  -> POST /v1/activity-events/batch
  -> server authenticates and enriches identity/tenant
  -> event schema and property allowlist validation
  -> analytics.activity_event
  -> engagement projector
  -> student_engagement_snapshot
  -> deterministic trigger evaluation
  -> optional intervention candidate
```

Business mutations follow a separate path:

```text
Browser command
  -> application API
  -> authorization + validation
  -> database transaction
       -> domain change
       -> audit event
       -> outbox domain event
  -> worker/projectors/integrations
```

## 3. Activity event envelope

Browser-submitted fields:

```json
{
  "event_id": "019...",
  "event_name": "ui.enrollment_step_viewed.v1",
  "occurred_at": "2026-07-24T14:30:00Z",
  "session_id": "session_...",
  "page_instance_id": "page_...",
  "correlation_id": "request_...",
  "properties": {
    "step_code": "identity_and_address",
    "entry_point": "dashboard_next_action"
  }
}
```

The ingestion API derives and attaches:

```text
tenant_id
actor_type
actor_id
student_id where authorized
application version
device class
received_at
trust_level = client_signal
```

The browser cannot assert tenant, identity, role, official status, or
authorization scope.

## 4. Initial activity event catalog

### Session and navigation

```text
ui.portal_session_started.v1
ui.portal_session_ended.v1
ui.page_viewed.v1
ui.dashboard_viewed.v1
ui.navigation_selected.v1
```

### Offer and enrollment

```text
ui.admission_offer_viewed.v1
ui.admission_decision_started.v1
ui.enrollment_started.v1
ui.enrollment_step_viewed.v1
ui.enrollment_step_save_started.v1
ui.enrollment_step_save_succeeded.v1
ui.enrollment_step_save_failed.v1
ui.requirement_viewed.v1
ui.requirement_submission_started.v1
```

### Forms and friction

```text
ui.form_validation_failed.v1
ui.save_conflict_presented.v1
ui.upload_started.v1
ui.upload_failed.v1
ui.help_opened.v1
ui.faq_viewed.v1
ui.support_escalation_started.v1
```

### Messages and payments

```text
ui.message_opened.v1
ui.notification_preference_viewed.v1
ui.payment_started.v1
ui.payment_provider_returned.v1
```

Event schemas define an explicit property allowlist. Unknown properties are
rejected or stripped, recorded as a telemetry error, and never silently stored.

## 5. Prohibited activity data

Do not capture:

- passwords, tokens, payment details, or secrets;
- raw keystrokes;
- complete form field values;
- addresses, emails, phone numbers, or government identifiers;
- document contents or filenames containing PII;
- unrestricted DOM snapshots or session replay;
- cross-site browsing activity;
- free-text support or message content in analytics;
- model prompts/outputs in general application logs.

Use safe codes:

```text
postal_code_invalid
file_type_not_supported
dependency_incomplete
payment_provider_timeout
```

Do not include the invalid value.

## 6. Frontend hook design

Proposed hooks:

```text
usePageTracking
useEnrollmentTracking
useRequirementTracking
useDocumentTracking
useHelpTracking
```

Rules:

- Hooks call a single typed tracking client.
- Event names are generated from a shared contract package.
- Components cannot add arbitrary properties.
- Events are buffered and sent in batches.
- Tracking failure never blocks an enrollment command.
- Duplicate event IDs are ignored by ingestion.
- Page events fire once per logical navigation/page instance.
- Duration metrics use monotonic browser timing but are stored as rounded
  durations, not fine-grained behavioral fingerprints.

## 7. Meaningful action versus activity

`last_active_at` changes when the student interacts with the portal.

`last_meaningful_action_at` changes only when the server confirms progress, for
example:

```text
offer accepted
profile successfully saved
requirement submitted
document upload completed
permission grant created
deposit completed
support case created
```

A student can be active every day without advancing. Staff prioritization must
not treat page views as progress.

## 8. Engagement projection

`student_engagement_snapshot` contains derived, explainable fields:

```text
student_id
journey_id
last_active_at
last_meaningful_action_at
current_step_code
completion_percentage
blocking_requirement_count
next_deadline
days_to_next_deadline
consecutive_safe_validation_failures
recent_upload_failures
help_requested
open_support_case_count
last_intervention_at
projection_version
```

It does not store raw sensitive answers.

## 9. Friction detection

Friction is determined by deterministic rules over safe signals.

Examples:

```text
same safe validation error >= 3 times in 20 minutes
two upload failures for the same requirement
blocking requirement rejected and not reopened within 48 hours
deadline within 7 days with no meaningful action
student explicitly opens contextual help
student returns to the same blocked requirement in 3 sessions
```

Each rule produces an `intervention_candidate`, not an automatic model run.

The intervention policy decides:

```text
ignore
show deterministic inline help
send template reminder
offer agent explanation
create staff-review candidate
escalate to support
```

## 10. Domain event versus audit event

Example: counselor waives a requirement.

Domain event:

```text
requirement.waived.v1
Data needed by downstream business consumers
```

Audit event:

```text
Actor, tenant, student, requirement, action, timestamp,
authorization basis, reason code, request/correlation ID
```

The audit event may identify changed field names but should avoid copying
sensitive before/after values unless policy explicitly requires and protects
them.

## 11. Audit coverage

Audit at minimum:

- authentication and account-linking outcomes;
- sensitive student record reads by non-students;
- profile and official record changes;
- requirement status changes and waivers;
- document access and review;
- permission grants, scope changes, and revocation;
- payment and signature state synchronization;
- staff assignment changes;
- exports and bulk operations;
- agent recommendations and accepted/rejected actions;
- configuration and policy-definition changes.

Audit storage is append-only to application roles. Retention is institution and
contract policy, not a UI setting.

## 12. Technical telemetry

Use OpenTelemetry-compatible correlation across:

```text
browser request
web server
application API
PostgreSQL
outbox event
worker job
external integration
agent/model call
```

Required telemetry:

- request rate, latency, and error rate;
- database pool saturation and slow-query metrics;
- outbox lag and consumer retry counts;
- dead-letter queue size;
- integration availability and webhook age;
- upload scan/processing latency;
- projection freshness;
- agent latency, token usage, cost, and failure rate.

Structured logs contain:

```text
timestamp
level
service
environment
request_id
correlation_id
tenant pseudonymous identifier where required
operation
result
error_code
duration
```

No raw student payload is logged.

## 13. Storage and scaling

Initial storage:

```text
PostgreSQL operational schemas
PostgreSQL partitioned analytics.activity_event
PostgreSQL audit schema with restricted role
Object storage for approved archives
```

When volume requires it:

```text
Operational + audit facts -> PostgreSQL
High-volume activity      -> event stream -> ClickHouse/warehouse
Historical raw archive    -> object storage
```

The tracking client and event contracts stay unchanged when storage changes.

## 14. Retention and deletion

Retention classes are configurable per tenant and policy:

```text
debug/technical logs
product activity analytics
official domain records
audit records
uploaded documents
agent prompts/outputs
aggregated de-identified metrics
```

Deletion/anonymization workflows must preserve records that the institution is
legally required to retain while removing data no longer needed for the stated
purpose. AI prompts and outputs default to shorter retention than official
records.

## 15. Tracking quality checks

Automated tests verify:

- every event matches its schema;
- prohibited keys are rejected;
- tenant/actor values come from the server;
- duplicates are idempotently ignored;
- tracking failures do not block commands;
- domain events are emitted only after successful transactions;
- audit records cannot be modified by application roles;
- correlation IDs connect request, event, worker, and agent records;
- projection rebuilds reproduce expected engagement state.

