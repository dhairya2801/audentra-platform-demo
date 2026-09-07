---
id: v3-settlement-release@2026.2
code: v3-settlement-release
version: '2026.2'
title: Account settlement and hold release
owner: SA
audience: all
authority: procedure
effective_from: '2026-09-01T04:00:00Z'
published_at: '2026-08-28T14:00:00Z'
supersedes: v3-settlement-release@2026.1
---
## Release requirements
From 1 September, Student Accounts may release a financial hold after confirming the posted overdue balance is at most $250. Release is a separate recorded action. Payment initiation, screenshots and an accepted aid offer do not satisfy the requirement. No financial release clears a health, conduct, academic or advising hold.

A release request must identify the hold, authorized actor, expected current version, explicit confirmation and idempotency key. Duplicate retries with the same payload return the original receipt. A stale version or a reused key with different content is rejected. A student or another office may request review but cannot approve release. The evaluation sandbox represents this boundary with validated actor IDs; it is not production authentication.

## Time and service
The office's business calendar excludes weekends, Labor Day (7 September 2026), and Thanksgiving closure (26–27 November 2026). The escalation procedure's business-day service levels count from the next business day and end at 17:00 local time. Elapsed time does not imply approval.
