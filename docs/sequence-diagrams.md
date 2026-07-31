# Student and Staff Sequence Diagrams

These diagrams separate the behavior implemented in the first staff slice from
the later event-driven upgrade. PostgreSQL is the production source of truth.
The local preview uses the same contracts and transaction boundaries with a
serialized development store.

## 1. Student upload, background parsing, staff review, and notification

This is the cross-application flow. Navigating away after the upload request
has completed does not cancel parsing because the job belongs to the server,
not to the React page.

```mermaid
sequenceDiagram
  autonumber
  actor Student
  participant StudentUI as Student portal
  participant API
  participant DB as PostgreSQL
  participant Files as Object storage
  participant Worker as Outbox worker
  participant Parser as Document parser
  actor Staff
  participant StaffUI as Staff action center

  Student->>StudentUI: Upload transcript
  StudentUI->>API: POST document upload
  API->>Files: Store original file
  API->>DB: Insert document and parsing event
  DB-->>API: Commit
  API-->>StudentUI: Processing status and document id

  Student->>StudentUI: Navigate to another page
  Note over StudentUI,Worker: Page unmount does not own or cancel the server job

  Worker->>DB: Claim committed parsing event
  Worker->>Files: Read stored original
  Worker->>Parser: Parse and classify
  Parser-->>Worker: Extraction result
  Worker->>DB: Save extraction and needs-review status

  Staff->>StaffUI: Open document-review item
  StaffUI->>API: GET action center and student record
  API->>DB: Read canonical document and work item
  API-->>StaffUI: Review context

  Staff->>StaffUI: Accept document and notify student
  StaffUI->>API: POST decision with expected work-item version
  API->>DB: Lock document and work item
  API->>DB: Update document and checklist requirement
  API->>DB: Complete work item and append work log
  API->>DB: Insert student message, audit, and outbox event
  DB-->>API: Atomic commit
  API-->>StaffUI: Canonical decision result

  loop Every 15 seconds or when window receives focus
    StudentUI->>API: GET lightweight bootstrap
    API->>DB: Count unread messages
    API-->>StudentUI: Updated notification count
  end
  Student->>StudentUI: Open messages or enrollment page
  StudentUI->>API: GET current student state
  API-->>StudentUI: Accepted document and completed requirement
```

## 2. Two staff colleagues using the same board

The first implementation is near-real-time polling, not a collaborative socket.
One staff member's change normally appears in another open board within five
seconds or immediately when that window receives focus.

```mermaid
sequenceDiagram
  autonumber
  actor StaffA as Staff A
  participant BoardA as Board A
  participant API
  participant DB as PostgreSQL
  participant BoardB as Board B
  actor StaffB as Staff B

  StaffA->>BoardA: Move task or change assignee
  BoardA->>API: PATCH work item with expectedVersion 4
  API->>DB: Lock item and compare version
  API->>DB: Update item to version 5
  API->>DB: Append work log, audit, and outbox event
  DB-->>API: Commit
  API-->>BoardA: Canonical item version 5

  loop Every 5 seconds or when window receives focus
    BoardB->>API: GET action center
    API->>DB: Read current board
    API-->>BoardB: Item version 5
  end

  opt Staff B had an old form open
    StaffB->>BoardB: Save stale version 4
    BoardB->>API: PATCH with expectedVersion 4
    API->>DB: Compare with version 5
    API-->>BoardB: 409 VERSION_CONFLICT
    BoardB->>API: Reload current board
  end
```

## 3. Staff edits student onboarding preferences

The staff and student applications do not maintain separate copies of these
values. Both views read the same student records.

```mermaid
sequenceDiagram
  autonumber
  actor Staff
  participant StaffUI as Staff action center
  participant API
  participant DB as PostgreSQL
  participant StudentUI as Student portal

  Staff->>StaffUI: Edit communication, housing, or support preferences
  StaffUI->>API: PATCH preferences with both expected versions
  API->>DB: Lock onboarding and profile records
  alt Both versions are current
    API->>DB: Update onboarding and profile
    API->>DB: Append work log and audit event
    opt Notify student
      API->>DB: Insert unread student message
    end
    API->>DB: Insert outbox event
    DB-->>API: Atomic commit
    API-->>StaffUI: Canonical student record with new versions
    StudentUI->>API: Poll, focus-refresh, or open the page
    API-->>StudentUI: Current preferences and unread count
  else Either record changed elsewhere
    API-->>StaffUI: 409 VERSION_CONFLICT
    StaffUI->>API: Reload current student record
  end
```

## 4. Later upgrade: live invalidation, not live-owned state

When polling becomes too slow or expensive, add server-sent events (SSE) after
the transactional outbox. The live message only says what became stale; the
browser still refetches the canonical record.

```mermaid
sequenceDiagram
  autonumber
  participant API
  participant DB as PostgreSQL
  participant Worker as Outbox worker
  participant Broker
  participant SSE as Live event gateway
  participant StaffUI as Staff browser
  participant StudentUI as Student browser

  API->>DB: Commit business change and outbox event
  Worker->>DB: Claim event
  Worker->>Broker: Publish tenant-scoped invalidation
  Broker->>SSE: Fan out invalidation
  SSE-->>StaffUI: work-item-changed
  SSE-->>StudentUI: student-record-changed
  StaffUI->>API: Refetch affected work item
  StudentUI->>API: Refetch bootstrap or message count
  API->>DB: Read canonical versions
  API-->>StaffUI: Current staff state
  API-->>StudentUI: Current student state
```

## Freshness decision table

| State | First implementation | Why |
| --- | --- | --- |
| Staff status, assignee, and escalation flag | Five-second poll plus focus refresh | Active coordination benefits from quick convergence, but sub-second editing is unnecessary for V1 |
| Student unread notification count | Fifteen-second lightweight poll plus focus refresh | Material decisions become visible without a manual reload |
| Full onboarding and student detail | Refetch on page open and after mutation | Larger payload and lower urgency |
| Document parsing progress | Bounded polling on the document surface | The job is server-owned and continues after navigation |
| Email and SMS delivery | Transactional outbox plus asynchronous provider worker | External delivery must not hold the database transaction open |
| Audit and work history | Returned with the next board refresh | Durability matters more than animation |
| Presence, typing indicators, and live cursors | Not implemented | They do not change business state |

See [Staff Action Center and Shared State](./22-staff-action-center-and-realtime-state.md)
for the data ownership, security boundary, and staged roadmap.
