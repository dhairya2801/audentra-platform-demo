"""Receipt gate for first-person claims about side effects."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

_CLAIM_TO_ACTIONS: tuple[tuple[re.Pattern[str], frozenset[str]], ...] = (
    (
        re.compile(r"\b(?:created|opened)\b", re.I),
        frozenset(
            {
                "student.support.contact",
                "operations.follow_up.create",
                "operations.cohort.create_follow_ups",
            }
        ),
    ),
    (
        re.compile(r"\b(?:updated|changed|assigned|reassigned|marked|closed|completed)\b", re.I),
        frozenset({"student.preferences.update", "operations.work_item.update"}),
    ),
    (
        re.compile(r"\bsubmit(?:ted)?\b", re.I),
        frozenset({"student.requirement.submit_response", "student.document.submit"}),
    ),
    (
        re.compile(r"\bprepared\b", re.I),
        frozenset({"communications.email.prepare"}),
    ),
)


def action_claim_has_receipt(answer: str, receipts: Sequence[Mapping[str, object]]) -> bool:
    """True only when every recognized claim has a matching committed receipt.

    Sending, approval, waiver and institutional decisions intentionally have
    no V1 match. An email-preparation receipt can support "I prepared", never
    "I sent".
    """

    committed = {
        str(receipt.get("action"))
        for receipt in receipts
        if str(receipt.get("status")) in {"succeeded", "partial"} and receipt.get("receiptSha256")
    }
    matched = False
    for pattern, actions in _CLAIM_TO_ACTIONS:
        if pattern.search(answer):
            matched = True
            if not (committed & actions):
                return False
    return matched
