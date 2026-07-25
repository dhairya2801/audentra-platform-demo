# Student Portal Feature Flows

## 1. Portal information architecture

The existing guided onboarding becomes the enrollment area of a persistent
portal:

```text
/dashboard       Readiness, next action, deadlines, messages
/enrollment      Guided enrollment journey
/tasks           All requirements and statuses
/documents       Requests, uploads, reviews
/payments        Deposit and other permitted transactions
/messages        Institution conversations and notices
/profile         Student-controlled profile fields
/permissions     Family/delegate access
/help            Policy help and support cases
```

The portal shell is persistent. A student can leave a flow, return later, and
resume from the server-confirmed state.

## 2. Shared interaction rules

All feature flows follow these rules:

- Authentication and authorization occur on every API request.
- The API returns authoritative state and available actions.
- The UI does not infer whether an action is permitted.
- Forms can keep local drafts, but completion is server-confirmed.
- Every mutation has an idempotency key.
- Mutable resources use optimistic concurrency.
- Consequential mutations create an audit event and domain event.
- Activity tracking never blocks the business operation.
- AI output is optional guidance, not the state transition mechanism.

## 3. Authentication and first entry

### Happy path

```text
Student opens portal
  -> web redirects to OIDC provider
  -> provider authenticates student
  -> callback validates state, nonce, and token
  -> API resolves tenant + user identity + student relationship
  -> session is established
  -> portal requests GetStudentDashboard
  -> dashboard is rendered
```

### Exceptions

| Condition | Behavior |
|---|---|
| Identity exists but has no student link | Show account-linking support state |
| Student belongs to multiple institutions | Require tenant selection |
| Offer has expired | Show read-only offer and support path |
| Account is locked/disabled | Deny access and show identity-provider support |
| Session expires during form entry | Preserve local draft and reauthenticate |

### Events

```text
Activity: ui.portal_session_started.v1
Audit: authentication.succeeded / authentication.failed
Domain: none
Agent: none
```

## 4. Dashboard and next best action

### Flow

```text
Student opens dashboard
  -> API reads student_portal_projection
  -> API verifies projection freshness/version
  -> response includes:
       overall progress
       next required action
       blocking requirements
       deadlines
       unread messages
       deposit/signature status
       allowed actions
  -> UI renders dashboard
```

The deterministic workflow engine selects the next action. The agent may
explain the action only after the action is known.

### Next-action ordering

1. Expired or urgent blocking requirement
2. Rejected submission requiring correction
3. Required action with nearest deadline
4. Required action unblocking the most dependencies
5. Optional recommended action

### Events

```text
Activity: ui.dashboard_viewed.v1
Domain: none
Agent: optional explanation on explicit request
```

## 5. Admission offer decision

### Accept flow

```text
Student views offer
  -> API returns current offer and allowed decisions
  -> student selects Accept
  -> UI shows official confirmation language
  -> student confirms
  -> AcceptAdmissionOffer command
  -> transaction:
       validate offer is active
       update offer to accepted
       create enrollment journey
       instantiate applicable requirements
       append audit record
       append outbox events
  -> API returns journey summary
  -> UI enters enrollment flow
```

### Decline/still-deciding flow

- "Still deciding" does not modify the offer.
- Decline requires confirmation and optional structured reason.
- Free-text decline notes are not required for analytics.
- A declined offer can be reversed only through an explicit institution policy
  and audited command.

### Events

```text
Activity: ui.admission_offer_viewed.v1
Activity: ui.admission_decision_started.v1
Domain: admission.offer_accepted.v1
Domain: enrollment.journey_created.v1
Audit: admission_offer.accepted
```

## 6. Profile verification and autosave

### Flow

```text
Student opens profile step
  -> API returns authoritative values, source, editability, and version
  -> student edits permitted fields
  -> browser stores temporary local draft
  -> debounce expires
  -> SaveStudentProfile command with expected version + idempotency key
  -> server validates field rules
  -> transaction saves accepted values and audit metadata
  -> API returns new version and field-level results
  -> UI displays All changes saved
```

### Field ownership

Every field returned to the UI includes:

```text
value
source
editable
verification_status
last_updated_at
```

Some official fields may require a change request instead of direct editing.

### Conflict flow

```text
Save uses version 12
Server already has version 13
  -> API returns 409 conflict with safe current values
  -> local draft is preserved
  -> UI auto-merges non-overlapping fields
  -> overlapping sensitive fields require student review
```

### Tracking

Track step duration, save success, and safe validation codes. Do not track field
values or keystrokes.

## 7. Requirement applicability and dependencies

### Instantiation flow

```text
Journey is created or relevant profile fact changes
  -> rules engine selects applicable definition versions
  -> dependency graph is evaluated
  -> student_requirement records are created/updated
  -> ready and blocked states are recalculated
  -> projection is updated
```

### Student flow

```text
Student opens requirement
  -> API returns status, reason, deadline, policy source, allowed actions
  -> student starts work
  -> status becomes in_progress when a meaningful save occurs
  -> student submits
  -> server validates required inputs and dependencies
  -> status becomes submitted or under_review
```

The agent can explain why a requirement applies. It cannot mark the requirement
not applicable, complete, or waived.

## 8. Document upload and review

### Upload flow

```text
Student opens a readable requirement route (for example transcript-upload)
  -> requirement page embeds the upload component
  -> requirement UUID stays internal and is sent as upload context
  -> student chooses a file; no manual document-category field is shown
  -> browser validates safe size/type hints
  -> API authorizes the student and the referenced document requirement
  -> original is stored under an opaque object key
  -> parser classifies from file contents using requirement type only as context
  -> strict schema extraction returns fields, courses, warnings, and confidence
  -> if content classification disagrees with requirement context:
       keep the requirement unchanged
       store the document and mismatch warning for review
  -> if classification matches:
       document becomes needs_review
       requirement becomes under_review
  -> designated verifier accepts or rejects
  -> confirmed transcript courses enter deterministic equivalency evaluation
```

### Failure flows

| Failure | Behavior |
|---|---|
| Upload interrupted | Resume/retry with same upload record where supported |
| File type rejected | Return safe validation code |
| Malware detected | Quarantine; no further processing or download |
| OCR uncertain | Route to human review |
| Wrong document | Preserve original, add a structured mismatch warning, and do not advance the requirement |
| Duplicate upload callback | Deduplicate by upload/object/provider ID |

### AI boundary

AI may classify or extract candidate metadata. A deterministic validator or
authorized human confirms consequential results. Raw documents are not placed in
general-purpose model memory.

## 9. E-signature

### Flow

```text
Student reaches signature requirement
  -> CreateSignaturePacket command
  -> signature adapter creates mock/real provider packet
  -> API stores provider link and pending status
  -> student enters embedded/redirected signing flow
  -> provider sends signed webhook
  -> webhook signature is verified and event deduplicated
  -> worker updates packet status
  -> requirement becomes completed when all required participants signed
```

The portal never marks a document signed based only on the browser returning
from the provider.

## 10. Deposit/payment

### Flow

```text
Student opens deposit
  -> API calculates/display permitted amount from authoritative configuration
  -> CreateDepositIntent command
  -> payment adapter creates intent
  -> browser completes provider-controlled payment UI
  -> provider webhook arrives
  -> signature + event ID verified
  -> worker updates payment transaction
  -> successful payment satisfies deposit requirement
  -> receipt becomes available
```

### Payment statuses

```text
created
requires_action
processing
succeeded
failed
cancelled
refunded
```

No card data passes through VV servers. A UI success callback is not proof of
payment; the verified webhook/provider query is authoritative.

## 11. Family permissions and FERPA delegation

### Grant flow

```text
Student opens permissions
  -> API returns current delegates and available scopes
  -> student identifies delegate
  -> student chooses explicit scopes and optional expiration
  -> UI displays consent summary
  -> GrantFamilyPermission command
  -> server validates institution policy
  -> grant is created and audited
  -> delegate invitation is sent asynchronously
```

Example scopes:

```text
view_enrollment_progress
view_financial_summary
receive_deadline_notifications
communicate_with_assigned_staff
```

### Revoke flow

Revocation takes effect immediately in authorization checks. Existing sessions
for the delegate are invalidated or forced to refresh authorization.

Agents cannot grant, expand, or revoke permissions.

## 12. Messages and notifications

### Message flow

```text
System/staff creates message
  -> message is persisted
  -> notification event is written to outbox
  -> worker selects permitted channel
  -> delivery provider is called with idempotency key
  -> delivery result is recorded
  -> portal remains the canonical full-message destination
```

Sensitive details should not be included in SMS or email previews. External
channels point the student back to the authenticated portal.

### Preferences

Operationally required notices are distinguished from optional engagement
communications. Preference changes are audited where required.

## 13. Help and support case

### Deterministic help

```text
Student opens help
  -> UI displays requirement-specific FAQ and support options
  -> no model call is required
```

### Agent-assisted explanation

```text
Student asks a policy/requirement question
  -> request enters controlled agent gateway
  -> relevant requirement + policy are retrieved
  -> short cited explanation is generated
  -> response is validated and shown
  -> agent run and usage are recorded
```

### Support escalation

```text
Student selects Contact support
  -> agent may draft summary with student approval
  -> student reviews draft
  -> CreateSupportCase command
  -> case enters staff queue
  -> student receives case reference
```

## 14. Resume, offline, and session recovery

- The server remains the source of confirmed progress.
- The browser can store encrypted-at-rest-by-platform temporary drafts for
  non-sensitive fields.
- Sensitive uploads and payment/signature processes are not emulated offline.
- When connectivity returns, queued saves are replayed with idempotency keys.
- Conflicts are resolved before a workflow is advanced.
- Session expiration preserves the draft and requires reauthentication.
- "All changes saved" appears only after server confirmation.

## 15. Accessibility and usability requirements

- Full keyboard navigation
- Visible focus and error association
- Screen-reader step/status announcements
- No color-only status communication
- Accessible timeout/session-expiry warning
- Form errors summarized and linked to fields
- Responsive behavior down to phone widths
- Reduced-motion support
- Plain-language requirement explanations
- Save/resume without losing completed work

## 16. Student-flow acceptance criteria

A feature is not complete until:

- data survives refresh and a new session;
- server validation exists;
- authorization is tested;
- idempotency is tested;
- audit/domain events are verified where applicable;
- activity events contain no prohibited data;
- loading, empty, error, conflict, and retry states exist;
- keyboard and screen-reader behavior is tested;
- a mock adapter can be replaced without changing the UI or use case.
