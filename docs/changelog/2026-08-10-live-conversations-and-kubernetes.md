# Live support conversations and portable Kubernetes - 2026-08-10

## What changed

Support is now modeled as a canonical `student_inquiry` conversation rather
than a one-way notification plus a separate staff note. The student Help page,
staff Messages workspace, and Action Center portal outreach all use the same
thread history.

Every participant message commits the inquiry update, reply history, any
student inbox delivery, work-item history, durable realtime hint, and
five-day expiry refresh before a request returns. Slow AI enrichment remains
asynchronous behind the existing quiet window, so an LLM/provider delay cannot
block a human conversation.

## Five-day inactive lifecycle

- `0032_support_conversation_lifecycle.sql` adds `last_message_at`,
  `expires_at`, and `archived_at` to `student_inquiry`.
- The scheduled worker claims expired rows with `FOR UPDATE SKIP LOCKED`, marks
  them `archived`, retains their messages/history, writes a staff work log, and
  creates durable staff and student realtime invalidations.
- Archive means the thread disappears from active inboxes and rejects a late
  hidden send. It is intentionally not a hard delete: protected historical
  evidence remains auditable and a future retention policy can purge it under
  institution-approved rules.
- Links are followed in addition to legacy work-item source IDs, so reopening a
  completed action still keeps its next staff reply and eventual archive on the
  correct conversation.

## Realtime architecture

PostgreSQL remains authoritative. `student_realtime_event` and
`staff_realtime_event` are append-only replay journals; SSE only tells a portal
to fetch canonical data again. Cursors make reconnects safe across multiple API
replicas and avoid Firebase or another client-writable realtime database.

## Interactive engineering atlas

`docs/architecture/audentra-system-flow-explorer.html` is a self-contained,
interactive employee-onboarding document. Its searchable catalog covers all 57
flows from the authoritative generated inventory: 24 student, 12 staff, 14
platform/AI/reliability/integration, and 7 leadership flows. Six cross-system
tours cover live support, transcript recovery, Action Center enrichment,
scheduled follow-up, conditional journeys, and content publication.

Every selectable stage identifies the exact execution class—student frontend,
staff frontend, Platform API, PostgreSQL, object storage, durable worker,
external provider, or CI/deployment runtime—plus its input, output, durable
state, sync/async timing, failure guarantee, and primary implementation
neighborhood. Filters expose persona, implementation status, trigger type, and
execution location; URL hashes deep-link to a flow and stage.

The selected flow is drawn as an interactive swimlane graph. Every node is
positioned inside its owning runtime lane, solid arrows represent synchronous
hand-offs, dashed arrows represent queued/asynchronous work, and selecting a
node updates the detailed boundary inspector below the graph.

The atlas deliberately has no product route, authentication dependency, API
call, or runtime state. Open it directly in a browser or serve the `docs`
directory with any static file server.

## Kubernetes delivery contract

`infra/kubernetes` provides a standard Kustomize base with separate API and
worker deployments, service accounts, services, probes, HPA/PDB, a generic
Ingress, a network-policy template, and a standalone migration Job. GKE and
EKS overlays only change the immutable image registry shape.

The cluster supplies the `audentra-platform-runtime` secret through a managed
secret mechanism such as GKE Workload Identity + Secret Manager or EKS IRSA +
AWS Secrets Manager. PostgreSQL and object storage stay managed/private
dependencies, never single-node Kubernetes state.
