"""Canonical deep links into the student portal.

Every constant and helper here corresponds to a real page in the
Audentra-portals app router (``apps/web/app``), so a link built from this
module can never invent a route. Composers and tools must build portal hrefs
through this module (or from hrefs the reads themselves supply) rather than
inlining route strings, and the model rewrite is never asked to author a URL.

Verified route inventory (Audentra-portals):
- static pages: /dashboard /enrollment /documents /financials /payments
  /appointments /messages /help /onboarding /profile /classrooms /campus-life
- /enrollment/requirements/{slug} — built from requirement data by
  ``audentra.domain.student_state.requirement_href``
- /documents?document={documentId} — Documents page focused on one upload
- /campus-life/clubs/{clubId} — a club's detail page
The portal client preserves these unscoped paths, queries, and fragments.
"""

from __future__ import annotations

from urllib.parse import quote

DASHBOARD = "/dashboard"
ENROLLMENT = "/enrollment"
DOCUMENTS = "/documents"
FINANCIALS = "/financials"
PAYMENTS = "/payments"
APPOINTMENTS = "/appointments"
MESSAGES = "/messages"
HELP = "/help"
ONBOARDING = "/onboarding"
PROFILE = "/profile"
CLASSROOMS = "/classrooms"
CAMPUS_LIFE = "/campus-life"


def document_page(document_id: object = None) -> str:
    """The Documents page, focused on one uploaded document when its id is known."""

    text = str(document_id or "").strip()
    return f"{DOCUMENTS}?document={quote(text)}" if text else DOCUMENTS


def club_page(club_id: object = None) -> str:
    """A club's detail page when its id is known, else the Campus life page."""

    text = str(club_id or "").strip()
    return f"{CAMPUS_LIFE}/clubs/{quote(text)}" if text else CAMPUS_LIFE
