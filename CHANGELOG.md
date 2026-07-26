# Changelog

All notable changes to the VV Edgent student portal are documented here.

The project is pre-release software and does not yet promise semantic-version
compatibility.

## [Unreleased]

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
