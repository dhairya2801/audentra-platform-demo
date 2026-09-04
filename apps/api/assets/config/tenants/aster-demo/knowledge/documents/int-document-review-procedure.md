---
code: int-document-review-procedure
kind: internal
title: Document Review Procedure (staff) — service levels, rejection reason codes, escalation
summary: Reviewers decide each uploaded document within the office's service level (Registrar and Financial Aid 5 business days, Student Health 7, Enrollment Services 1), record exactly one rejection reason code, never accept on extraction alone, and escalate a second rejection to the director.
owner_office: REG
audience: internal
status: published
version: "2026.1"
effective_from: 2026-05-01
effective_until: null
supersedes: null
applies_to: {}
related: [document-upload-standards, offer-acceptance-and-conditional-admission, immunization-requirement, int-verification-processing, int-escalation-matrix-and-service-levels]
keywords: [document review, reviewer, rejection code, rejection reason, accept document, review queue, transcript evaluation queue, SLA, escalate rejected document, action center document review]
---
## Queue and ownership

Every upload creates a **document review** work item in the Action Center
for the owning office (transcript and identity → Registrar; verification
and tax documents → Financial Aid; immunization and health → Student
Health; profile evidence → Enrollment Services), assigned to the office's
review pool. Items carry the checklist due date; overdue items sort first.

## Service levels (business days from upload)

| Office | Decision due | Note |
| --- | --- | --- |
| Enrollment Services | 1 | |
| Registrar (identity, transcripts) | 5 | Transfer-credit posting: 15 from the official transcript |
| Financial Aid (verification documents) | 5 | Full verification review: 10 after the file is complete |
| Student Health | 7 | Most within 3 |

## Decision rules

1. Open the document, not only the extraction. Extracted fields are
   suggestions; a decision requires the reviewer to have seen the pages.
2. **Accept** when the document is the one requested, legible, complete,
   valid on the review date, and its identifying details match the student
   record (name, date of birth, school). Record any extracted discrepancy
   you corrected.
3. **Reject** with exactly **one reason code** from the tenant list — the
   student sees the label and guidance verbatim:

   | Code | Label | Use when |
   | --- | --- | --- |
   | `illegible` | Image or text is unclear | Cannot read a required field |
   | `incomplete` | Document is incomplete | Pages, signatures or fields missing (name what is missing in the note) |
   | `wrong_document` | Wrong document | Not the requested document type |
   | `expired` | Document is expired | Past its validity date |
   | `information_mismatch` | Information does not match | Name, DOB or institution differs |
   | `unsupported_evidence` | Evidence cannot be accepted | Unofficial or self-attested where an official source is required |

   Add a one-line reviewer note in plain language (it is shown to the
   student). Never reject for a reason not on the list; if none fits, ask
   the director before deciding.
4. **Needs review** (hold) only when a department decision is pending
   (transfer credit with no equivalency rule, an unusual exemption); set the
   follow-up date.
5. A rejection returns the checklist item to *ready* and **does not move the
   due date**; do not extend due dates in the requirement, use the
   extension procedure of the owning office.

## Second rejections and escalation

A document rejected twice is escalated to the office director, who
schedules a call or virtual meeting with the student within three business
days and, for identity documents, may verify in person. Three rejections of
the same requirement are flagged in the engagement scan as an attention
signal for the primary adviser.

## Official transcripts

An uploaded transcript is reviewed and may be accepted *for evaluation*,
but the requirement is completed only when the Records Specialist logs
receipt of the official copy (electronic service or sealed). Log receipts
the day they arrive; the add/drop-deadline hold sweep reads that log.
