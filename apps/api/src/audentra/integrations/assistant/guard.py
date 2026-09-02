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

from audentra.integrations.action_receipts import action_claim_has_receipt

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
    r"are blocking|blocks|prevents?|preventing|until|unless|once)\b"
    # "X stops/keeps you from doing Y" asserts the same causation with the
    # claimed cause as the sentence's subject. Only positively-inflected
    # forms: "does not stop" must never read as a causal claim.
    r"|\b(?:stops|stopping|keeps|keeping|(?:does|will|would|can|could) "
    r"(?:stop|keep|prevent))\b[^.!?]{0,16}\bfrom\b"
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
    (
        "checklist",
        re.compile(r"\bchecklist\b|\brequired steps\b|\benrollment (?:steps|items|requirements)\b"),
    ),
)

_TOPIC_BY_GATE_CODE: Mapping[str, str] = {
    "account_balance": "balance",
    "enrollment_deposit_posted": "deposit",
    "enrollment_confirmed": "deposit",
    "enrollment_deposit": "deposit",
    "immunization_cleared": "immunisation",
    "immunization_record": "immunisation",
    "advising_complete": "advising",
    "final_transcript": "transcript",
    "official_transcript": "transcript",
    "housing_preference_selected": "housing",
    "housing_preference": "housing",
    "fafsa_received": "fafsa",
    "fafsa": "fafsa",
    "verification_complete": "verification",
    "verification_worksheet": "verification",
    "financial_aid_verification": "verification",
    "award_acceptance": "financial_aid",
    "identity_document": "identity",
    "orientation_registration": "orientation",
    "orientation_complete": "orientation",
    "award_decisions": "financial_aid",
}

_REGISTRATION_OUTCOME = re.compile(
    r"(?:can(?:no|')?t|cannot|can not|unable to|not able to)\s+(?:currently\s+)?register\b"
    r"|\bregistration (?:is|remains|stays) (?:currently )?"
    r"(?:blocked|closed|unavailable|not (?:open|available|possible))\b"
    r"|\byou (?:are|'re) (?:currently )?(?:blocked|prevented) from registering\b"
    r"|\b(?:stops|stopping|prevents|preventing|keeps|keeping|"
    r"(?:does|will|would|can|could) (?:stop|prevent|keep))\b"
    r"[^.!?]{0,32}\bfrom\b[^.!?]{0,24}\bregister"
)

_HOUSING_OUTCOME = re.compile(
    r"(?:can(?:no|')?t|cannot|can not|unable to|not able to)\s+(?:currently\s+)?"
    r"(?:apply for|act on|complete|select) (?:your )?housing\b"
    r"|\bhousing (?:step|application)? ?(?:is|remains) (?:currently )?"
    r"(?:blocked|closed|unavailable|not (?:open|available|possible))\b"
    # "…stops/keeps you from applying for housing" is the same blocked
    # outcome with the invented cause as subject. Positive inflections only,
    # so "does not stop you from applying" never matches.
    r"|\b(?:stops|stopping|prevents|preventing|keeps|keeping|"
    r"(?:does|will|would|can|could) (?:stop|prevent|keep))\b"
    r"[^.!?]{0,32}\bfrom\b[^.!?]{0,32}\bhousing\b"
)

_DISBURSEMENT_OUTCOME = re.compile(
    r"\b(?:aid|funds|money)\b[^.!?]{0,48}\b(?:will not|won'?t|cannot|can'?t|has(?:n'?t| not))"
    r"[^.!?]{0,24}\bdisburs"
    r"|\bdisbursement (?:is|remains) (?:currently )?(?:held|on hold|blocked|waiting)\b"
    r"|\bnot (?:be )?disbursed (?:until|unless|before)\b"
    # A positive conditional ("your aid will be disbursed once you…") asserts
    # the same dependency and must face the same gate evidence.
    r"|\b(?:aid|funds|money)\b[^.!?]{0,48}\b(?:will|can) (?:be )?"
    r"(?:disbursed?|paid out|released)\b[^.!?]{0,32}\b(?:once|when|after|as soon as)\b"
    r"|\bdisburs\w+[^.!?]{0,32}\b(?:once|after|when) you\b"
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
    housing_gates: Sequence[Mapping[str, object]] | None = None,
    disbursement_gates: Sequence[Mapping[str, object]] | None = None,
) -> list[CausalGuard]:
    """Build guards from whatever gated capabilities the reads returned.

    A capability that was not read produces no guard, so the check never fires
    on evidence Edward did not have.
    """

    def open_topics(gates: Sequence[Mapping[str, object]]) -> frozenset[str]:
        topics = {
            _TOPIC_BY_GATE_CODE[str(gate.get("code"))]
            for gate in gates
            if not gate.get("satisfied") and str(gate.get("code")) in _TOPIC_BY_GATE_CODE
        }
        # Any open gate legitimises "your checklist" as a stated cause; a
        # capability with every gate satisfied legitimises none.
        if any(not gate.get("satisfied") for gate in gates):
            topics.add("checklist")
        return frozenset(topics)

    guards: list[CausalGuard] = []
    if registration_gates is not None:
        guards.append(
            CausalGuard(outcome=_REGISTRATION_OUTCOME, open_topics=open_topics(registration_gates))
        )
    if housing_gates is not None:
        guards.append(CausalGuard(outcome=_HOUSING_OUTCOME, open_topics=open_topics(housing_gates)))
    if disbursement_gates is not None:
        guards.append(
            CausalGuard(outcome=_DISBURSEMENT_OUTCOME, open_topics=open_topics(disbursement_gates))
        )
    return guards


_HOLD_AFFIRMATION = re.compile(
    r"\b(?:you (?:do )?(?:have|currently have)|there(?:'s| is| is currently))\b"
    r"[^.!?]{0,40}\bholds?\b"
    r"|\bhold (?:has been|was) placed\b"
    r"|\byour (?:record|account) (?:has|shows)[^.!?]{0,24}\bholds?\b",
    re.IGNORECASE,
)
_HOLD_NEGATION = re.compile(
    r"\b(?:no|not|isn'?t|aren'?t|don'?t|do not|doesn'?t|does not|without|never|"
    r"rather than|instead of|nothing)\b"
    r"|\bno (?:official|registrar)\b",
    re.IGNORECASE,
)

# Language that acknowledges something could not be checked this turn.
_UNAVAILABILITY_ACK = re.compile(
    r"couldn'?t|could not|can'?t (?:check|verify|confirm|read|see)|"
    r"cannot (?:check|verify|confirm|read|see)|unable to|not available|"
    r"unavailable|didn'?t load|did not load|wasn'?t able|was not able|"
    r"right now|this moment|try (?:again|asking again)|temporarily",
    re.IGNORECASE,
)


def guard_grounded_answer(
    *,
    answer: str,
    evidence_texts: Sequence[str],
    causal_guards: Sequence[CausalGuard] = (),
    document_states: Sequence[Mapping[str, str]] = (),
    no_official_holds: bool = False,
    unavailable_sources: Sequence[str] = (),
    deposit_payment_pending: bool = False,
    action_receipts: Sequence[Mapping[str, object]] = (),
) -> GuardResult:
    normalized = re.sub(r"\s+", " ", answer).strip()

    def reject(reason: str) -> GuardResult:
        return GuardResult(accepted=False, reason_code=reason, answer=normalized)

    if not normalized:
        return reject("empty")
    if len(normalized) > MAX_ANSWER_CHARACTERS:
        return reject("too_long")
    if _WRITE_CLAIM.search(normalized) and not action_claim_has_receipt(
        normalized, action_receipts
    ):
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
    # The holds read said no official hold exists (the platform operates no
    # hold system) — affirming one is an invented record, full stop.
    if no_official_holds and _detect_invented_hold(lowered):
        return reject("invented_hold")
    # The student's deposit payment is submitted and pending. Telling them to
    # pay (again) contradicts the processing state on record.
    if deposit_payment_pending and _detect_pay_again_instruction(lowered):
        return reject("contradicted_processing_state")
    # A read this turn depended on failed. The reply must carry the honesty
    # marker; a confident answer with no acknowledgment reads a dead domain
    # as fact, so it falls back to the deterministic draft (which carries the
    # unavailability note by construction).
    if unavailable_sources and not _UNAVAILABILITY_ACK.search(lowered):
        return reject("missing_unavailability_note")
    return GuardResult(accepted=True, reason_code=None, answer=normalized)


_PAY_INSTRUCTION = re.compile(
    r"\b(?:need to|needs to|should|must|have to|start by|next step is to|"
    r"go ahead and|can) pay(?:ing)?\b[^.!?]{0,32}\bdeposit"
    r"|\bpay the (?:enrollment )?deposit\b",
    re.IGNORECASE,
)
_PENDING_QUALIFIER = re.compile(
    r"pending|processing|posted|posts|clears?|already (?:paid|submitted)|"
    r"again if|if it fails|no (?:new|further) payment",
    re.IGNORECASE,
)


def ungrounded_tokens(answer: str, evidence_texts: Sequence[str]) -> dict[str, list[str]]:
    """Which numbers, dates and contacts in `answer` the evidence does not carry.

    Diagnostic companion to `guard_grounded_answer`: the guard only says
    *that* a claim was ungrounded; traces and evals need to see *which*.
    """

    corpus = "\n".join(evidence_texts).lower()
    lowered = re.sub(r"\s+", " ", answer).strip().lower()
    allowed_dates = _collect_dates(corpus)
    allowed_numbers = _collect_numbers(_mask_dates_and_contacts(corpus))
    allowed_contacts = _collect_contacts(corpus)
    return {
        "contacts": sorted(c for c in _collect_contacts(lowered) if c not in allowed_contacts),
        "dates": sorted(d for d in _collect_dates(lowered) if d not in allowed_dates),
        "numbers": sorted(
            n
            for n in _collect_numbers(_mask_dates_and_contacts(lowered))
            if n not in allowed_numbers
        ),
    }


def _detect_pay_again_instruction(lowered: str) -> bool:
    for sentence in re.split(r"[.!?]", lowered):
        if _PAY_INSTRUCTION.search(sentence) and not _PENDING_QUALIFIER.search(sentence):
            return True
    return False


def _detect_invented_hold(lowered: str) -> bool:
    for sentence in re.split(r"[.!?]", lowered):
        if _HOLD_AFFIRMATION.search(sentence) and not _HOLD_NEGATION.search(sentence):
            return True
    return False


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


# Public aliases: the staff assistant guard shares these grounding-token
# helpers instead of maintaining a third copy of the date/number/contact
# canonicalization rules.
collect_dates = _collect_dates
collect_numbers = _collect_numbers
collect_contacts = _collect_contacts
mask_dates_and_contacts = _mask_dates_and_contacts


def _detect_invented_causation(lowered: str, causal_guards: Sequence[CausalGuard]) -> bool:
    """Any claim of the form "X is blocked because Y" is checked against
    whether Y is actually an open gate of X."""

    for guard in causal_guards:
        if not guard.outcome.search(lowered):
            continue
        for sentence in re.split(r"[.!?]", lowered):
            if not guard.outcome.search(sentence) or not _CAUSAL_CONNECTIVE.search(sentence):
                continue
            matched = {topic for topic, pattern in _CAUSAL_TOPICS if pattern.search(sentence)}
            # Topic vocabularies overlap ("financial aid verification" matches
            # both financial_aid and verification). The sentence names an
            # invented cause only when *none* of its topics is an open gate —
            # a sentence citing at least one true open gate is a true cause.
            if matched and matched.isdisjoint(guard.open_topics):
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
