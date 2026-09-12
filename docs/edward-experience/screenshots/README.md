# Browser evidence

All screenshots show fictional university records from real local API responses.
The before/after screenshots demonstrate layout and scrolling changes; they are
not a controlled visual A/B test with identical wording. Matched conversational
comparisons are in `../representative-transcripts.json`.

| State | Screenshot |
|---|---|
| Original student answer: scrolled past the primary answer | [Before](student-before-answer.png) |
| Contextual student starting state | [Desktop](student-desktop-empty.png) |
| Student aid explanation and next action | [Desktop](student-desktop-answer.png), [Mobile](student-mobile-answer.png) |
| Inspectable policy passages | [Sources](student-desktop-sources.png) |
| Staff starting state | [Desktop](staff-desktop-empty.png) |
| Staff case answer | [Desktop](staff-desktop-answer.png), [Mobile](staff-mobile-answer.png) |
| Recipient-facing draft | [Draft](staff-draft.png) |
| Unavailable mailbox; no fake send action | [Boundary](staff-mailbox-unavailable.png) |
| Consequential action review | [Confirmation](student-confirmation.png) |
| Confirmed profile change, with actual saved value | [Receipt](student-receipt.png) |
| Stop waiting and honest retry | [Stopped](student-stopped.png) |
| Actual university architecture | [Architecture](lab-architecture.png) |
| Inspectable semantic payload and provenance | [Trace](lab-semantic-trace.png) |

Browser scripts also assert focus return, reopening, primary-answer visibility,
no horizontal overflow, no runtime errors, and no axe WCAG A/AA violations in the
four student/staff desktop/mobile dialog states. See `../browser-evidence.json`.
These are Chromium checks, not a full screen-reader or physical-device study.
