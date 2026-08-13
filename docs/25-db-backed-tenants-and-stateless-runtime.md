# Database-backed tenants and stateless runtime

## Decision

The API and worker are disposable compute. Restarting, replacing, or horizontally scaling either
process must not change tenant-visible state. PostgreSQL is the authority for structured state,
and object storage is the authority for uploaded or generated binary objects. A process may cache
read data only when cache loss is harmless and every write is committed durably first.

This definition does not move protocol and security invariants into data. UUID syntax, MIME-type
allowlists, request-size ceilings, event names, and other immutable implementation constraints
remain in code. Tenant-owned identity, content, branding, contacts, policies, templates, and
feature availability are data and must be exposed through tenant-scoped, authorized CRUD APIs.

## Tenant configuration

Each tenant has an immutable slug and a versioned portal configuration containing:

- display, legal, and short names;
- logo, dark-logo, favicon, and hero asset references with accessible alternative text;
- brand colors;
- locale, IANA time zone, ISO 4217 currency, and ISO 3166 country code;
- academic-year, current-term, and default-campus labels;
- support, admissions, and financial-aid contacts;
- capability flags; and
- public links.

The public portal reads this configuration by slug. Staff updates use optimistic concurrency with
`expectedVersion`; an accepted update increments the version and writes audit and outbox records in
the same transaction. Slugs are not editable through the configuration endpoint because they are
routing identities.

Binary logos and documents should be durable object-storage objects. The database stores their
tenant ownership, object key or public URL, media metadata, version, and lifecycle state.

## Runtime request paths

1. Resolve the tenant slug through PostgreSQL rather than a compiled slug-to-UUID map.
2. Resolve the authenticated actor within that tenant.
3. Read and mutate tenant-scoped PostgreSQL records.
4. Commit the domain change, audit entry, and outbox event atomically.
5. Return a projection; never depend on a later in-process synchronization step.

Student course reads use materialized catalog tables. Managed configuration publication may accept
YAML as an explicit import compatibility format, but the request path does not read repository YAML
files. Its parsed JSON document, publication history, and materialized rows are stored in PostgreSQL.
A missing publication is a provisioning error, not permission to create process-local state.

## Provisioning and migration

Checked-in demo fixtures are import inputs for development and preview environments only. Import is
an explicit migration or seed operation, not a request-time fallback. Production tenant creation
must create the tenant and its required configuration transactionally through an administrative
workflow. The current staff role is tenant-scoped and therefore must not create tenants or change
their routing slugs. Until a separately authorized platform-operator role or CLI exists, tenant
provisioning remains a controlled migration/operator action. Suspension and deactivation are soft
lifecycle states; tenant records and their audit history are not hard-deleted. Seed reruns must not
overwrite staff-managed records.

## Follow-up data domains

The same boundary applies to the remaining audited tenant-owned domains: legal document templates,
onboarding schemas, housing inventory and choices, support resources, staff playbooks, routing and
engagement policies, payment-provider configuration, and AI presentation copy. Each should receive
a tenant-scoped schema, version/concurrency contract where appropriate, staff authorization, audit
records, outbox events, and an explicit bootstrap/import path before repository or frontend
fallbacks are removed.

Until legal-document template CRUD is implemented, the signed-onboarding generator fails closed for
tenants without an explicit reviewed asset mapping. It must never fall back to another university's
legal template.

## Delivery scope and remaining work

This change makes tenant bootstrap, staff managed configuration, course materialization, and staff
workspace composition restart-safe and PostgreSQL-backed. It does not claim that every historical
tenant-owned policy has already been converted. The next deliveries should move the domains listed
above behind ordinary tenant-scoped tables and APIs, then remove their remaining repository assets
or code defaults.

Two transitional constraints are explicit:

- demo seed/reset commits its base relational fixture before replaying each managed projection, so a
  failed replay is safely retryable but the entire multi-kind seed is not one atomic transaction;
- staff authentication still supplies the first active tenant student as a legacy workspace context.
  A later contract change should separate staff identity from student context and support an empty
  tenant before its first student is provisioned.

The legacy Node `tools/demo-api` remains a development-only compatibility adapter and still owns
file-backed preview state. It is not part of the FastAPI, worker, or deployed platform runtime and
must not be used as the canonical integration backend; retire it once its remaining compatibility
tests have moved to FastAPI.
