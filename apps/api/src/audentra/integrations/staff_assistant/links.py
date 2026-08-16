"""Canonical deep links into the staff portal.

The staff portal is one route (``/staff``) whose sub-views are hash fragments
handled client-side (``apps/web/app/staff/staff-portal.tsx``); the portal
client preserves the hash when prefixing the tenant slug. A specific student
or work item cannot be addressed by URL — selection is client-side state — so
per-entity links are deliberately not built here. Composers link to the
owning view instead of inventing a deeper route.
"""

from __future__ import annotations

STAFF_OVERVIEW = "/staff#overview"
STAFF_TASKS = "/staff#tasks"
STAFF_STUDENTS = "/staff#students"
STAFF_OUTREACH = "/staff#outreach"
STAFF_MESSAGES = "/staff#messages"
STAFF_KNOWLEDGE = "/staff#knowledge"
STAFF_CORE_PLAYS = "/staff#core_plays"
