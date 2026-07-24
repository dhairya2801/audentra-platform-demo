# Integrations and Dummy-to-Real Strategy

## 1. Objective

The initial system must operate with believable dummy providers while preserving
the same application contracts, transactions, events, and failure behavior that
real integrations will use.

The rule is:

> Mock the external boundary, not the domain.

## 2. Integration ports

Application modules depend on interfaces:

```text
IdentityProviderPort
AdmissionsCrmPort
StudentInformationSystemPort
DocumentStoragePort
DocumentScannerPort
SignaturePort
PaymentPort
MessagingPort
PolicyKnowledgePort
```

Example implementations:

```text
MockAdmissionsCrmAdapter
SlateAdmissionsCrmAdapter
SalesforceAdmissionsCrmAdapter

MockStudentInformationSystemAdapter
BannerStudentInformationSystemAdapter

MockPaymentAdapter
StripePaymentAdapter

MockSignatureAdapter
DocuSignAdapter

MailpitMessagingAdapter
ProductionEmailMessagingAdapter
```

Configuration selects an adapter. Use cases and React components do not branch
on `isDemo`.

## 3. Adapter contract rules

Every adapter:

- accepts/returns application-owned typed contracts;
- translates provider-specific fields internally;
- uses explicit timeouts;
- supports idempotency where the provider permits it;
- maps transient and permanent errors to stable error codes;
- emits metrics and correlation identifiers;
- redacts secrets and sensitive payloads from logs;
- has contract tests shared by mock and production implementations.

## 4. External record mapping

`external_record_link` stores:

```text
tenant_id
system
entity_type
internal_id
external_id
source_version
source_updated_at
last_synced_at
sync_status
```

An external ID is never treated as globally unique. It is scoped by tenant,
source system, and entity type.

## 5. Inbound synchronization flow

```text
Scheduled poll, API import, or provider event
  -> adapter retrieves payload
  -> payload schema is validated
  -> inbox event ID is deduplicated
  -> external identity/record link is resolved
  -> field-level authority rules are applied
  -> valid changes commit in transaction
       -> operational record
       -> audit/provenance
       -> outbox domain event
  -> sync cursor/status advances
```

Malformed records are quarantined with safe diagnostics. A single bad record
does not fail an entire large import unless atomicity is explicitly required.

## 6. Outbound synchronization flow

```text
VV domain event
  -> worker loads sync policy
  -> creates provider command with idempotency key
  -> adapter sends command
  -> provider result is recorded
  -> external link/source version updates
  -> transient failure retries with bounded backoff
  -> permanent failure creates operations task/dead letter
```

UI state distinguishes:

```text
saved_in_vv
sync_pending
synced
sync_failed_requires_attention
```

The student should not be told an institutional record was updated until the
source system confirms it when confirmation is required.

## 7. Webhook flow

```text
Provider sends webhook
  -> dedicated endpoint reads raw body
  -> signature/timestamp are verified
  -> event ID is deduplicated in integration inbox
  -> endpoint stores event and returns quickly
  -> worker processes event
  -> provider object is optionally re-fetched for verification
  -> domain state updates in transaction
  -> audit and outbox records are created
```

Never trust browser redirects as proof of payment or signature completion.

## 8. Conflict resolution

Field authority is configured:

```text
CRM authoritative
SIS authoritative
VV authoritative
student-editable pending verification
most-recent only for explicitly safe fields
manual conflict resolution
```

Conflicts produce a structured record:

```text
entity
field
current source/version
incoming source/version
safe masked comparison
authority rule
resolution status
resolver
resolved_at
```

No generic last-write-wins behavior is used for legal name, enrollment status,
financial status, residency, permissions, or verified requirements.

## 9. Mock adapter behavior

Mocks must model realistic outcomes:

```text
success
timeout then success
duplicate callback
provider validation error
permanent rejection
delayed processing
out-of-order webhook
service unavailable
rate limit
```

Failure modes are selected through deterministic scenario configuration, not
random behavior in ordinary tests.

Example:

```yaml
scenario: deposit_failure_then_success
payment:
  first_attempt: failed
  failure_code: insufficient_funds
  second_attempt: succeeded
  webhook_delay_ms: 500
```

## 10. Seed data versus mock providers

- **Seed data** creates stable institution, student, staff, journey, and event
  histories.
- **Mock providers** simulate external runtime behavior.
- **Test fixtures** set up the smallest state required for an automated test.

These are separate tools and should not be mixed.

No dummy array is embedded in a production page component.

## 11. Adapter replacement checklist

A mock can be replaced with a real provider when:

- the real adapter passes the shared contract test suite;
- sandbox webhook signature verification passes;
- idempotency and duplicate delivery tests pass;
- timeout/retry behavior is configured;
- rate limits are handled;
- data authority mapping is approved;
- error codes map to safe user/staff messages;
- secrets come from the approved secret manager;
- audit and telemetry contain no prohibited payload;
- rollback to the mock/sandbox adapter is operationally documented.

## 12. Initial integration order

1. OIDC identity
2. CRM application and offer import
3. Object storage and malware scanning
4. Messaging
5. Payment provider
6. E-signature provider
7. SIS official enrollment synchronization
8. Additional CRM/SIS vendors

The first product build uses mocks for all external boundaries while keeping
their production contracts.

