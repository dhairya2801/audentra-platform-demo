# Changelog

All notable changes to the VV Edgent student portal are documented here.

The project is pre-release software and does not yet promise semantic-version
compatibility.

## [Unreleased]

### Added

- Added tenant-owned course-resource libraries and student-club event calendars,
  including licensed open PDF textbooks and dedicated tenant-prefixed club pages
- Added club membership, meeting-schedule, contact, social, and upcoming-event
  presentation backed by reusable database fields and event records
- Added tenant-owned academic-program, course-catalog, campus-event, and
  student-organization source metadata, with Harvard CS and campus-life preview
  content seeded independently from Aster
- Added official-source links, course availability/instructor/meeting details,
  campus source labels, organization social links, and explicit synthetic-event
  labels to the student UI
- Added Edward academic and campus-life context domains, scoped retrieval
  receipts, and direct navigation actions for My Classrooms and My Campus Life
- Added tenant-managed campus-event visual themes, accessible image metadata,
  and three optimized original event backgrounds for the featured carousel
- Added deterministic portal regression journeys for event theming, responsive
  overflow, tenant isolation, club calendars, course PDFs, enrollment actions,
  document layout, secure sign-out, JPEG limits, Edward context minimization,
  and bounded conversation history
- Added path-based Aster and Harvard preview tenants with tenant-prefixed
  routing, tenant-scoped credential/state stores, tenant-aware branding, and a
  documented hostname-based Kubernetes production target
- Added persistent transcript-processing leases, bounded polling, stale-job
  expiration, upload-bundle locking, and retryable timeout recovery
- Added background portal process commands for starting, stopping, and
  inspecting the local web/API pair without blocking the terminal
- Added My Documents to the primary portal navigation and requirement-specific
  checklist actions such as Upload transcript, Select housing, and Pay deposit

### Changed

- Made the campus-event carousel more vibrant and converted organization cards
  into image-backed, event-specific themes; organization cards now open
  navigable club experiences with full event calendars
- Fixed the web command wrapper so forwarded `--host` and `--port` arguments
  reach vinext, preventing isolated test/dev servers from silently colliding
  with the portal already running on port 3000
- Hardened the browser-test launcher so completed-student fixtures apply to
  every tenant and isolated Windows process trees are cleaned up explicitly
- Restyled the student document center as a submitted-and-signed document
  library, added padded processing guidance, and improved the profile sign-out
  security card
- Rebuilt My Classrooms around tenant-managed recommendations, program paths,
  transcript matches, searchable catalog cards, and source-aware course dialogs
- Polished My Campus Life with readable responsive club cards, fixed image
  bounds, overview metrics, empty search states, and expandable sourced details
- Edward now selects only the record domains relevant to the question instead
  of loading every student projection into every model call
- Onboarding now gates every portal route until final completion; admission
  offer acceptance, identity/contact details, housing path, emergency contact,
  and document signing cannot be skipped
- Campus-life preferences and the deposit step remain deferrable without
  allowing the student to leave incomplete onboarding
- Transcript parsing can route independently through OpenRouter or Groq, uses
  provider-specific multimodal segmentation, and deterministically conserves
  course rows across segment merges
- The local tenant preview now persists parsing state across navigation and
  prevents competing uploads while extraction is active
- Enrollment checklist buttons now describe the concrete action instead of
  using the generic “Open task” label
- Added browser-level misuse acceptance tests for authentication and document
  workflows, with screenshots/videos/traces retained on failure
- Added Edward adversarial browser tests for code-execution requests, prompt
  injection, cross-student access, XSS, malicious provider links/actions,
  forged payment widgets, and oversized prompts
- Added persistent client-side authentication validation and password
  confirmation without weakening API validation
- Financial-aid uploads now use classification-only AI checks: unrelated files
  cannot advance the requirement and extracted financial fields are discarded
- Document-type mismatches now render as warnings and classification-only
  uploads no longer show a field-confirmation panel
- Pinned the supported local/CI runtime to Node.js 22 LTS to avoid a Node 26.5
  native test-runner failure observed on macOS
- Aligned first-session onboarding with the product-owner reference questions
  while keeping document collection in enrollment
- Removed the redundant “Confirm your student record” block and the
  ID/health/access onboarding step
- Replaced emergency-contact and FERPA acknowledgement checkboxes with
  structured student-entered records
- Expanded housing and campus-life onboarding into the reference branches
- Made campus life and deposit skippable for later completion while keeping the
  top-level housing decision required
- Deposit onboarding now pays only after an explicit `pay_now` selection

### Security

- High-risk Edward capability-escalation requests are rejected before student
  context collection or an LLM call
- Edward has an explicit tool boundary with no shell, Python, filesystem,
  secret-store, raw-SQL, or arbitrary-network capability
- Model prose is normalized at the HTTP boundary; active markup, unsafe URI
  schemes, external navigation, and non-allowlisted routes are removed
- Deposit widgets require explicit payment intent and are rebuilt from the
  authoritative offer ID, amount, and payment status instead of trusting
  provider-supplied fields

### Planned

- Institutional OIDC and verified invitation delivery
- Managed PostgreSQL and object storage deployment
- Staff, leader, and VP enrollment workspaces
- Real payment, communications, and registrar integrations
- Production backups, alerting, and Kubernetes manifests

## [0.1.0-preview.1] - 2026-07-26

### Added

- Responsive Aster University student portal with dashboard, enrollment,
  financials, classrooms, campus life, Edward AI, profile, and supporting pages
- Credential signup/sign-in, account-isolated state, hashed passwords, hashed
  sessions, lockout state, and one-time resumable onboarding
- Inline enrollment actions for profile, housing, documents, and deposit
- Multi-file PDF/JPEG/PNG upload with durable originals
- Agentic identity/transcript parsing through OpenRouter or Groq
- PDF text extraction, page rendering, normalized identity-photo regions, and
  provider-response attempt records
- Automatic advisory transcript credit and course-exemption insights
- Manual-review mode for financial-aid and immunization documents
- Seeded Computer Science, Mechanical Engineering, and Business Administration
  academic data plus events and clubs
- Edward bounded context receipts, usage reporting, safe navigation, and typed
  payment/upload/appointment widgets
- PostgreSQL migrations for portal, credentials, documents, and AI attempts
- Transactional outbox worker with retries, leases, dead-letter behavior, and
  projection receipts
- CRM State Effect Registry, generated Mermaid/JSON graph, and CI validation
- Hardened Google Compute Engine preview deployment with automatic HTTPS
- Keyless GitHub Actions CD through OIDC, Workload Identity Federation, IAP,
  OS Login, versioned releases, health checks, and rollback
- Architecture, domain-model, deployment, security, and operations documents

### Changed

- Onboarding now behaves as a one-time application onboarding flow and gates
  the dashboard
- Enrollment tasks use stable human-readable slugs and keep actions on the task
  page
- Documents are persisted before asynchronous parsing begins
- Transcript extraction no longer requires a redundant student confirmation
- Housing preview reflects the current selection and uses real sample imagery
- Dashboard next action derives from enrollment state
- Production cookies are marked Secure
- Preview images install only the dependency graph each runtime needs

### Security

- Public SSH and RDP exposure removed for the portal VM
- Legacy port 8000 blocked while its local service remains available
- Shielded VM Secure Boot, vTPM, integrity monitoring, deletion protection, and
  static address enabled
- OS Login and IAP-only administration enabled; old static SSH key removed
- Host firewall, SSH daemon, kernel networking, auditd, and unattended security
  updates hardened
- Docker group access removed and deployment directories made root-owned
- Containers use read-only filesystems, dropped capabilities,
  no-new-privileges, request/PID/memory limits, and bounded logs
- Caddy image pinned by digest and response security headers expanded
- GitHub Actions dependencies pinned to immutable commits
- Long-lived Google deployment keys prohibited
