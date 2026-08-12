"""Deterministic claim guard for model-written answers.

The port of `guardGroundedAnswer` from student-assistant-core `answer.ts`.
Dates, multi-digit numbers, and contact details in a written answer must all
be traceable to the evidence text; a first-person write claim, a leaked
identifier, a causal claim the gate data contradicts, or a document state the
record contradicts each reject the answer outright. The most serious failure
this assistant can produce is a confident, specific, wrong reason — worse
than a vague answer, because a student will act on it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

MAX_ANSWER_CHARACTERS = 1_200

_WRITE_CLAIM = re.compile(
    r"\bi(?:'ve|'ll| have| will| can| am going to| just)?\s+(?:go ahead and\s+)?"
    r"(?:submitted?|paid?|pay|updated?|update|changed?|change|removed?|remove|"
    r"cancell?ed?|cancel|registered?|register|applied|apply|uploaded?|upload|"
    r"approved?|approve|waived?|waive|scheduled?|schedule|booked?|book|"
    r"enrolled?|enroll|posted?|post|cleared?|clear|fixed?|fix)\b",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(
    r"\breceipt-\d+|\bcontext:|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", re.IGNORECASE
)
_CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|https?://\S+|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_PROSE_DATE = re.compile(
    r"\b(" + "|".join([*_MONTHS, *(month[:3] for month in _MONTHS)]) + r")\.?\s+"
    r"(\d{1,2})(?:\s*,?\s*(\d{4}))?",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]{2,}(?:\.\d+)?\b|\b\d+(?:\.\d+)?%")
_CAUSAL_CONNECTIVE = re.compile(
    r"\b(?:because|since|due to|owing to|as a result of|caused by|is blocking|"
    r"are blocking|blocks|prevents?|preventing)\b"
)

_CAUSAL_TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("balance", re.compile(r"\b(?:balance|past[- ]due|owe|owing|unpaid charges?)\b")),
    ("deposit", re.compile(r"\bdeposit\b")),
    ("immunisation", re.compile(r"\bimmunis\w*|\bimmuniz\w*|\bhealth (?:record|clearance)\b")),
    ("advising", re.compile(r"\badvis(?:ing|er|or)\b")),
    ("transcript", re.compile(r"\btranscript\b")),
    (
        "financial_aid",
        re.compile(r"\bfinancial[- ]aid\b|\baid (?:package|award|is|isn'?t|being)\b"),
    ),
    ("verification", re.compile(r"\bverification\b|\bworksheet\b")),
    ("fafsa", re.compile(r"\bfafsa\b")),
    ("housing", re.compile(r"\bhousing (?:plan|preference|application)\b")),
    ("identity", re.compile(r"\bidentity document\w*\b")),
    ("orientation", re.compile(r"\borientation\b")),
    ("hold", re.compile(r"\bhold\b")),
)

_TOPIC_BY_GATE_CODE: Mapping[str, str] = {
    "account_balance": "balance",
    "enrollment_deposit_posted": "deposit",
    "enrollment_confirmed": "deposit",
    "immunization_cleared": "immunisation",
    "advising_complete": "advising",
    "final_transcript": "transcript",
    "housing_preference_selected": "housing",
    "fafsa_received": "fafsa",
    "verification_complete": "verification",
    "award_decisions": "financial_aid",
}

_REGISTRATION_OUTCOME = re.compile(
    r"(?:can(?:no|')?t|cannot|can not|unable to|not able to)\s+(?:currently\s+)?register\b"
    r"|\bregistration (?:is|remains|stays) (?:currently )?"
    r"(?:blocked|closed|unavailable|not (?:open|available|possible))\b"
    r"|\byou (?:are|'re) (?:currently )?(?:blocked|prevented) from registering\b"
)


@dataclass(frozen=True)
class CausalGuard:
    outcome: re.Pattern[str]
    open_topics: frozenset[str]


@dataclass(frozen=True)
class GuardResult:
    accepted: bool
    reason_code: str | None
    answer: str


def build_causal_guards(
    *,
    registration_gates: Sequence[Mapping[str, object]] | None = None,
) -> list[CausalGuard]:
    """Build guards from whatever gated capabilities the reads returned.

    A capability that was not read produces no guard, so the check never fires
    on evidence Edward did not have.
    """

    guards: list[CausalGuard] = []
    if registration_gates is not None:
        open_topics = frozenset(
            _TOPIC_BY_GATE_CODE[str(gate.get("code"))]
            for gate in registration_gates
            if not gate.get("satisfied") and str(gate.get("code")) in _TOPIC_BY_GATE_CODE
        )
        guards.append(CausalGuard(outcome=_REGISTRATION_OUTCOME, open_topics=open_topics))
    return guards


def guard_grounded_answer(
    *,
    answer: str,
    evidence_texts: Sequence[str],
    causal_guards: Sequence[CausalGuard] = (),
    document_states: Sequence[Mapping[str, str]] = (),
) -> GuardResult:
    normalized = re.sub(r"\s+", " ", answer).strip()

    def reject(reason: str) -> GuardResult:
        return GuardResult(accepted=False, reason_code=reason, answer=normalized)

    if not normalized:
        return reject("empty")
    if len(normalized) > MAX_ANSWER_CHARACTERS:
        return reject("too_long")
    if _WRITE_CLAIM.search(normalized):
        return reject("claimed_write")
    if _IDENTIFIER.search(normalized):
        return reject("leaked_identifier")

    corpus = "\n".join(evidence_texts).lower()
    allowed_dates = _collect_dates(corpus)
    allowed_numbers = _collect_numbers(_mask_dates_and_contacts(corpus))
    allowed_contacts = _collect_contacts(corpus)
    lowered = normalized.lower()

    for contact in _collect_contacts(lowered):
        if contact not in allowed_contacts:
            return reject("ungrounded_contact")
    for date in _collect_dates(lowered):
        if date not in allowed_dates:
            return reject("ungrounded_date")
    for value in _collect_numbers(_mask_dates_and_contacts(lowered)):
        if value not in allowed_numbers:
            return reject("ungrounded_number")
    if _detect_invented_causation(lowered, causal_guards):
        return reject("invented_causation")
    if _detect_contradicted_document_state(lowered, document_states):
        return reject("contradicted_document_state")
    return GuardResult(accepted=True, reason_code=None, answer=normalized)


def _mask_dates_and_contacts(text: str) -> str:
    text = _CONTACT.sub(" ", text)
    text = _ISO_DATE.sub(" ", text)
    return _PROSE_DATE.sub(" ", text)


def _collect_dates(text: str) -> set[str]:
    dates: set[str] = set()
    for match in _ISO_DATE.finditer(text):
        year, month, day = match.groups()
        dates.add(f"{int(month)}-{int(day)}")
        dates.add(f"{int(month)}-{int(day)}-{year}")
    for match in _PROSE_DATE.finditer(text):
        token = match.group(1).replace(".", "").lower()
        month_index = next(
            (index for index, month in enumerate(_MONTHS) if month.startswith(token)), None
        )
        if month_index is None:
            continue
        day = int(match.group(2))
        dates.add(f"{month_index + 1}-{day}")
        if match.group(3):
            dates.add(f"{month_index + 1}-{day}-{match.group(3)}")
    return dates


def _collect_numbers(text: str) -> set[str]:
    """Amounts canonicalised by numeric value.

    Evidence renders currency as "$500.00" while a natural reply says "$500";
    comparing raw strings rejected correct answers. Bare one- and two-digit
    integers are allowed because they are almost always counts the model
    derived from the evidence list itself.
    """

    numbers: set[str] = set()
    for match in _NUMBER.finditer(text):
        token = match.group(0).replace(" ", "").replace(",", "").replace("$", "")
        if token.endswith("%"):
            numbers.add(f"{_canonical_number(token[:-1])}%")
        else:
            numbers.add(_canonical_number(token))
    return numbers


def _canonical_number(token: str) -> str:
    try:
        return str(float(token)).removesuffix(".0")
    except ValueError:
        return token


def _collect_contacts(text: str) -> set[str]:
    return {match.group(0).rstrip(".,;)") for match in _CONTACT.finditer(text)}


def _detect_invented_causation(lowered: str, causal_guards: Sequence[CausalGuard]) -> bool:
    """Any claim of the form "X is blocked because Y" is checked against
    whether Y is actually an open gate of X."""

    for guard in causal_guards:
        if not guard.outcome.search(lowered):
            continue
        for sentence in re.split(r"[.!?]", lowered):
            if not guard.outcome.search(sentence) or not _CAUSAL_CONNECTIVE.search(sentence):
                continue
            for topic, pattern in _CAUSAL_TOPICS:
                if pattern.search(sentence) and topic not in guard.open_topics:
                    return True
    return False


def _detect_contradicted_document_state(
    lowered: str, document_states: Sequence[Mapping[str, str]]
) -> bool:
    """ "Your transcript has not been submitted" while a transcript sits in
    review is a contradiction of the record, not a style problem."""

    for state in document_states:
        title = str(state.get("title") or "").lower()
        submission_state = str(state.get("submissionState") or "")
        if not title:
            continue
        keyword = next(
            (
                word
                for word in ("transcript", "immunization", "identity", "fafsa", "residency")
                if word in title
            ),
            None,
        )
        if keyword is None or keyword not in lowered:
            continue
        claims_not_submitted = re.search(
            rf"\b{keyword}\b[^.!?]{{0,64}}\b(?:has ?n[o']t been (?:submitted|uploaded|received)"
            rf"|not (?:yet )?(?:submitted|uploaded|received)|is missing|still missing"
            rf"|needs? to be (?:submitted|uploaded))",
            lowered,
        )
        claims_accepted = re.search(
            rf"\b{keyword}\b[^.!?]{{0,64}}\b(?:accepted|approved|verified|cleared)\b", lowered
        )
        if submission_state in {"under_review", "accepted"} and claims_not_submitted:
            return True
        if submission_state in {"not_submitted", "under_review"} and claims_accepted:
            return True
    return False
