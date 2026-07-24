# Security, Privacy, and Authorization

## 1. Security model

VV processes sensitive educational and identity data. Security is designed into
the domain and API boundaries, not added only at deployment time.

Core controls:

```text
external authentication
tenant isolation
role + relationship authorization
field/data minimization
append-only audit
encryption
short-lived credentials
safe document processing
AI data controls
retention and deletion
continuous security testing
```

## 2. Authentication

- OIDC authorization-code flow with PKCE where applicable
- Secure, HTTP-only, same-site cookies
- State and nonce validation
- Short-lived sessions/tokens
- Refresh/token rotation according to provider support
- Step-up authentication for defined sensitive actions
- Session revocation on permission/role changes where required
- No identity-provider tokens in browser local storage

Authentication proves identity. It does not establish authorization by itself.

## 3. Authorization model

Authorization combines:

```text
tenant
actor role
actor-to-resource relationship
purpose/action
resource state
permission/consent scope
institution policy
```

Examples:

- Student reads their own journey.
- Delegate reads only granted scopes for the named student.
- Counselor reads students assigned to their queue/team.
- Director reads configured campus/team scope.
- Leader reads permitted cohorts and aggregates.
- VP reads institution aggregates; student drill-down is separately checked.
- Service identity performs only named worker/integration actions.

## 4. Permission matrix baseline

| Action | Student | Delegate | Counselor | Director | Leader/VP |
|---|---|---|---|---|---|
| Read own/authorized progress | Own | Granted scope | Assigned | Scoped | Aggregate by default |
| Edit student profile | Allowed fields | No by default | Controlled correction | No by default | No |
| Submit requirement | Own | No by default | Staff-only workflow | No | No |
| Waive requirement | No | No | Policy-dependent | Policy-dependent | No direct |
| Grant/revoke delegate | Own | No | No | No | No |
| Send student message | N/A | N/A | Assigned | Scoped | Campaign/playbook only |
| View sensitive document | Own/policy | Explicit scope | Assigned + purpose | Scoped + purpose | No by default |
| View aggregate analytics | Own only | No | Assigned cohort | Team/campus | Institution scope |

The final matrix is tenant configurable within platform safety constraints.

## 5. Tenant isolation

- Every tenant-owned table contains `tenant_id`.
- Repository methods require tenant context.
- Composite uniqueness includes tenant where applicable.
- PostgreSQL row-level security provides defense in depth.
- Background jobs carry signed/validated tenant context.
- Cache keys include tenant.
- Object storage keys/buckets enforce tenant separation.
- Agent retrieval indexes and budgets are tenant-scoped.
- Cross-tenant tests run automatically.

Application roles do not own protected tables, so they cannot bypass row-level
security through ownership.

## 6. Sensitive-field handling

Classify fields:

```text
public
institution internal
education record
sensitive identity
financial
authentication secret
document content
```

Classification controls:

- who may read/write;
- whether a value can appear in logs/events;
- encryption requirements;
- retention;
- whether AI processing is allowed;
- export behavior;
- masking in support/operations interfaces.

## 7. Document security

```text
Upload authorization
  -> short-lived signed upload URL
  -> private object
  -> size/type verification
  -> malware quarantine/scan
  -> optional OCR/classification in approved boundary
  -> human/system review
  -> short-lived signed download for authorized actor
```

Controls:

- no public buckets;
- unpredictable object keys;
- content-disposition and safe MIME handling;
- scan status checked before download;
- download access audited;
- file retention tied to policy;
- document content excluded from general logs and analytics.

## 8. Audit and non-repudiation

Audit records include:

```text
actor and actor type
tenant
action
resource type/ID
student subject where applicable
authorization basis
reason code
timestamp
request/correlation ID
result
source IP/device metadata only as policy permits
```

Application users cannot update/delete audit records. Administrative access to
the audit system is separately controlled and monitored.

## 9. Secrets and configuration

- Typed configuration fails startup when required values are absent.
- Secrets are never committed to source control.
- Local secrets use ignored environment files or local secret tooling.
- Kubernetes uses an approved external secret manager.
- Credentials are scoped per environment and integration.
- Rotation is supported without rebuilding application images.
- Browser-exposed configuration is explicitly allowlisted.

## 10. API security

- Strict request/response schemas
- Body and upload limits
- Rate limits by endpoint, tenant, and actor
- CSRF protection for cookie-authenticated mutations
- CORS allowlist
- Idempotency for mutation/external-operation endpoints
- Optimistic concurrency
- Safe error codes without stack traces or sensitive details
- Dependency and container vulnerability scanning
- Security headers and reverse proxy protections

## 11. AI privacy and safety

- Agent calls originate only from the server gateway.
- Tenant policy controls which features/models/providers are allowed.
- Student snapshots contain minimum fields.
- Student PII is not stored in the policy vector index.
- Prompts and outputs have explicit retention.
- Provider training/data-use settings follow institutional requirements.
- Agent tools re-check authorization on every invocation.
- Model output is untrusted input and schema validated.
- Prompt-injection content from documents/policies cannot grant tools or expand
  permissions.
- Consequential actions require named application commands and approval.

## 12. Logging and analytics privacy

Technical logs and product analytics exclude:

- form values;
- free-text messages;
- document contents;
- secrets/tokens;
- payment data;
- raw model prompts/outputs;
- sensitive identifiers where a pseudonymous ID suffices.

Session replay is disabled by default. If introduced later, it requires
institution approval, masking validation, strict scope, and a separate privacy
assessment.

## 13. Retention, export, and deletion

Retention is defined by data class, institution policy, contract, and legal
obligation. The platform supports:

- policy-based expiration;
- litigation/records holds where required;
- student data export within authorized scope;
- deletion or irreversible anonymization;
- propagation to object storage and derived analytics;
- tombstones where needed to prevent re-import;
- preservation of required minimal audit evidence.

## 14. Security testing

Required automated checks:

- tenant isolation
- horizontal/vertical authorization
- delegate scope and revocation
- staff assignment boundaries
- CSRF and session behavior
- upload type/size/access controls
- webhook signature and replay prevention
- SQL/injection-safe repository use
- event-property allowlists
- log redaction
- agent tool authorization
- dependency/container/static analysis

High-risk flows receive manual threat modeling before production.

## 15. Incident readiness

Operational runbooks cover:

- suspected cross-tenant exposure;
- compromised identity/session;
- malicious upload;
- leaked integration credential;
- webhook replay/fraud;
- AI data leakage or unsafe output;
- audit pipeline failure;
- backup restore and point-in-time recovery;
- provider outage and circuit-breaker activation.

The platform must be able to disable an integration or agent feature without
disabling core deterministic enrollment access.

