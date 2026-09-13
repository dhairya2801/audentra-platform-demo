"""Deterministic claim guard for model-written staff answers.

The grounding rules mirror the student guard — dates, multi-digit numbers,
and contact details must be traceable to the evidence bundle — plus two
staff-specific families:

- **Action claims.** A read-only assistant that says "I've sent the email"
  or "I assigned this to John" has committed the worst staff failure: an
  action that never happened, reported as done.
- **Fabricated-metric language.** Melt risk, recovery likelihood, risk
  scores, and email opens do not exist in the platform; if the rewrite
  introduces that vocabulary and the evidence doesn't contain it, the answer
  is rejected and the deterministic draft stands.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from audentra.integrations.action_receipts import action_claim_has_receipt
from audentra.integrations.assistant.guard import (
    collect_contacts,
    collect_dates,
    collect_numbers,
    mask_dates_and_contacts,
)

MAX_STAFF_ANSWER_CHARACTERS = 1_600

_ACTION_CLAIM = re.compile(
    r"\bi(?:'ve|'ll| have| will| just| went ahead and)?\s+"
    r"(?:sent|send|emailed|email|texted|text|messaged|message|called|call|"
    r"assigned|assign|reassigned|escalated|escalate|created|create|opened|open|"
    r"scheduled|schedule|booked|book|updated|update|changed|change|marked|mark|"
    r"approved|approve|rejected|reject|waived|waive|resolved|resolve|closed|close|"
    r"completed|complete|cancelled|cancel|notified|notify|submitted|submit)\b"
    r"(?![^.!?]{0,32}\bdraft)",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(
    r"\breceipt-\d+|\bcontext:|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", re.IGNORECASE
)
_FABRICATED_METRIC = re.compile(
    r"\bmelt(?:ing)? (?:risk|score|likelihood|probability)\b"
    r"|\brecovery likelihood\b|\brisk score\b|\bpropensity\b"
    r"|\bprobability of (?:enrolling|enrollment|melt)\b"
    r"|\b\d{1,3}% (?:melt|risk|recovery|likely to enroll)\b"
    r"|\bopened (?:the|your|our|his|her|their) email\b"
    r"|\bclicked (?:the|a|our) (?:link|email)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StaffGuardResult:
    accepted: bool
    reason_code: str | None
    answer: str


def guard_staff_grounded_answer(
    *,
    answer: str,
    evidence_texts: Sequence[str],
    action_receipts: Sequence[Mapping[str, object]] = (),
) -> StaffGuardResult:
    normalized = re.sub(r"\s+", " ", answer).strip()

    def reject(reason: str) -> StaffGuardResult:
        return StaffGuardResult(accepted=False, reason_code=reason, answer=normalized)

    if not normalized:
        return reject("empty")
    if len(normalized) > MAX_STAFF_ANSWER_CHARACTERS:
        return reject("too_long")
    if _ACTION_CLAIM.search(normalized) and not action_claim_has_receipt(
        normalized, action_receipts
    ):
        return reject("claimed_action")
    if _IDENTIFIER.search(normalized):
        return reject("leaked_identifier")

    corpus = "\n".join(evidence_texts).lower()
    lowered = normalized.lower()

    if _FABRICATED_METRIC.search(lowered):
        matched = _FABRICATED_METRIC.search(lowered)
        if matched is not None and matched.group(0) not in corpus:
            return reject("fabricated_metric_language")

    allowed_dates = collect_dates(corpus)
    allowed_numbers = collect_numbers(mask_dates_and_contacts(corpus)) | set(
        re.findall(r"\b(?:19|20)\d{2}(?=-\d{2}-\d{2})", corpus)
    )
    allowed_contacts = collect_contacts(corpus)

    for contact in collect_contacts(lowered):
        if contact not in allowed_contacts:
            return reject("ungrounded_contact")
    for date in collect_dates(lowered):
        if date not in allowed_dates:
            return reject("ungrounded_date")
    for value in collect_numbers(mask_dates_and_contacts(lowered)):
        if value not in allowed_numbers:
            return reject("ungrounded_number")
    return StaffGuardResult(accepted=True, reason_code=None, answer=normalized)
