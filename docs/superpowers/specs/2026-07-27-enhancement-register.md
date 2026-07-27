# VV Edgent Enhancement Register

## Purpose

This register captures improvements discovered while designing the approved
onboarding, content-media, signing, prompt-runtime, transcript, exemption, and
immunization work. Items required by the approved delivery remain in their
respective design specifications. The items below are follow-on capabilities
that should be prioritized deliberately rather than hidden inside unrelated
changes.

## Priority 1: Needed before a real multi-university production launch

### University administration workspace

The runtime will support tenant-versioned prompts and policies, but the current
application has no staff authentication or administration UI. Add:

- university staff roles and permission scopes;
- prompt drafting, fixture evaluation, approval, activation, rollback, and
  comparison;
- academic catalog and equivalency-policy editing;
- immunization-policy editing with effective dates and population rules;
- document-template and signature-field placement editing;
- housing, club, event, and media management;
- maker/checker approval for high-impact policy changes.

### Prompt evaluation and release gates

- tenant-owned golden datasets without real student PII;
- schema, safety, hallucination, completeness, latency, and cost evaluations;
- current-versus-candidate prompt comparisons;
- minimum acceptance thresholds before activation;
- canary rollout and one-click rollback;
- drift monitoring by prompt/model/policy version.

### Production identity and authorization

- institutional OIDC/SAML;
- verified invitations and account recovery;
- staff/student role separation;
- step-up authentication for policy administration and signing-template
  changes;
- session and access-event reporting.

### Document and media security

- malware scanning and quarantine before parsing or serving uploads;
- content-disarm/reconstruction for PDFs;
- image metadata stripping;
- private-object access expiry;
- retention, legal hold, deletion, and student data-export policies;
- storage/CDN replication and backup verification.

### Signing governance

- institution-counsel approval for templates and consent language;
- configurable retention and void/re-sign workflows;
- signer evidence export;
- accessibility review of electronic-signature consent;
- real DocuSign/Adobe Sign provider adapters when an institution requires
  externally certified envelopes.

### Health-data governance

- institution-approved vaccine codes and clinical rules;
- waiver, religious/medical exemption, and appeal workflows;
- minimum-necessary access controls;
- health-record retention and staff-review queues;
- explicit prohibition on using the model for diagnoses or medical advice.

## Priority 2: Accuracy, operations, and staff efficiency

### Transcript/OCR expansion

- evaluated OCR for textless and low-quality scans;
- rotated-page and multi-column table handling;
- handwritten annotation detection;
- provider fallback by page/segment;
- duplicate transcript/version reconciliation;
- institution-specific grading-scale context.

### Academic-review workspace

- registrar queue for suggested, policy-gap, and ambiguous mappings;
- side-by-side transcript evidence and target-course policy;
- approve, deny, request evidence, and supersede actions;
- reviewer feedback captured as future prompt-evaluation examples;
- bulk re-evaluation after catalog or policy changes.

### Immunization-review workspace

- staff queue organized by missing/uncertain requirement;
- dose timeline and evidence viewer;
- waiver/document requests;
- policy-change re-evaluation;
- student notifications tied to due dates.

### Runtime observability

- metrics by tenant, operation, prompt version, provider, model, and policy
  version;
- latency, token, cost, failure, retry, partial-result, and cache-reload
  dashboards;
- provider budgets and circuit breakers;
- alerts for invalid outputs, completeness regressions, and stale
  last-known-good use;
- trace links from student-visible results to safe run receipts.

### Configuration propagation

- PostgreSQL `LISTEN/NOTIFY` or a durable event bus to warm caches immediately
  after activation while retaining the per-call revision check;
- multi-region revision propagation;
- cache size and eviction policy;
- configuration snapshot export for incident recovery.

### Content operations

- license/attribution review and expiry tracking;
- broken-image and object-integrity monitoring;
- responsive image variants and CDN transformations;
- moderation and accessibility review;
- university-specific branding and fallback packs.

## Priority 3: Student experience and product maturity

### Onboarding

- save-as-draft inside a long stage without advancing;
- per-field autosave with clear server-confirmed state;
- change history for student-edited stages;
- translated content and locale-aware address/phone forms;
- reduced-motion and high-contrast verification;
- analytics for abandonment without collecting sensitive field values.

### Housing

- room-level photo galleries, floor plans, pricing, capacity, and
  availability;
- roommate invitations and mutual confirmation;
- roommate privacy/consent controls;
- accessibility filters;
- application deadlines and waitlist state.

### Campus life

- club membership requests and event registration;
- personalized recommendations with transparent preference controls;
- calendar integration;
- content freshness/ownership workflows;
- student-reporting and moderation.

### Documents

- thumbnails, page search, rotation, and annotations;
- accessible HTML alternatives to image-only PDFs;
- replacement/version history;
- student-visible processing timeline;
- staff requests for a corrected page rather than a full re-upload.

### Academic insights

- degree-what-if scenarios for changing programs;
- transfer-institution articulation history;
- explainable prerequisite propagation;
- student appeals and additional evidence;
- notification when a recommendation changes after policy updates.

### Notifications

- in-product and email notifications for offer deadlines, missing documents,
  signing requests, policy re-evaluations, and staff decisions;
- per-channel preferences and quiet hours;
- deduplication and delivery receipts.

## Engineering quality improvements

- Continue splitting the 1,821-line onboarding page into focused modules.
- Replace source-text assertions with behavior-level component tests where
  practical.
- Add visual-regression coverage for onboarding, media cards, PDF viewing, and
  signature placement.
- Add contract compatibility tests between production and preview APIs.
- Add migration rollback rehearsals and representative large-tenant fixtures.
- Add load tests for per-call configuration revision checks and concurrent
  prompt reloads.
- Add accessibility testing with keyboard-only, screen-reader semantics, zoom,
  and mobile viewport matrices.
- Remove remaining mojibake characters from historical UI copy and fixtures.

## Prioritization rule

Do not start a Priority 2 or 3 item merely because it is adjacent to current
code. Pull it into a delivery only when it is required for that delivery's
acceptance criteria or the product owner explicitly reprioritizes it.

