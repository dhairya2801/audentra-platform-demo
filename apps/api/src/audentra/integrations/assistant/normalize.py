"""Request normalization and deterministic safety detections.

Ported from student-assistant-core `nodes.ts` (normalizeRequestNode and the
safety detectors). Bounds are enforced before anything else reads the text so
no downstream node ever sees an unbounded message or history.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

MAX_MESSAGE_CHARACTERS = 2_000
MAX_HISTORY_CHARACTERS = 1_200
MAX_PAGE_CONTEXT_CHARACTERS = 240
DEFAULT_HISTORY_LIMIT = 6

_REFERENTIAL_TAIL = re.compile(
    r"\b(?:that|this|those|these|it|them|the first one|the other one)\b\s*[?.!]?$"
    r"|^(?:how|what|why|when|who)\b[^?]{0,48}\b(?:that|this|those|these|it|them)\b"
    r"|^(?:why|how|what|when|where|who|really|and)[?!. ]*$",
    re.IGNORECASE,
)
_FOLLOW_UP_OPENER = re.compile(
    # A leading interjection ("Great — so…", "Okay, then…") does not stop a
    # turn from being a follow-up; skip it before testing the opener.
    r"^(?:(?:great|nice|cool|perfect|awesome|ok(?:ay)?|right|fine|well|hmm|alright|"
    r"thanks|got it)[,!.\s\u2014\u2013-]+)*"
    r"(?:and |also |so |then |what about|how about|those|them|it\b)",
    re.IGNORECASE,
)

_MUTATION_REQUEST = re.compile(
    # "change my mind" is an idiom, not a request to change a record.
    r"\b(?:submit|upload|pay|update|change(?! (?:my|your|his|her|their) mind)|edit|cancel|"
    r"remove|delete|register me|"
    r"enroll me|sign me up|apply for me|book|schedule|reschedule|move|redo|"
    r"waive|approve|mark)\b"
    r"[^.?]{0,48}\b(?:for me|my|it|this|that|the)\b"
    r"|^(?:please\s+)?(?:submit|upload|pay|update|change|cancel|remove|delete|"
    r"reschedule|move|redo|waive|approve)\b",
    re.IGNORECASE,
)
# Asking Edward to act, explicitly: "can you pay it", "pay it for me".
_DELEGATED_ACTION = re.compile(
    r"\b(?:can|could|will|would) you\b[^.?]{0,48}"
    r"\b(?:submit|upload|pay|update|change|cancel|remove|delete|book|schedule|"
    r"reschedule|move|redo|waive|approve|mark|register|enroll|apply)\b"
    r"|\b(?:submit|upload|pay|update|change|cancel|remove|delete|book|schedule|"
    r"reschedule|move|redo|waive|approve|mark)\b[^.?]{0,40}\bfor me\b"
    r"|\bon my behalf\b",
    re.IGNORECASE,
)
# Asking HOW/WHERE/WHETHER an action can be done is a question about the
# world, not a request to perform the action. "Where do I upload it?" and
# "Can I still pay?" must never hit the write refusal.
_INFORMATIONAL_FRAME = re.compile(
    # A leading context clause ("my payment is still processing, do I need
    # to pay again?") does not make the question a request.
    r"^(?:so |and |but |ok(?:ay)? |hey |hi |please |um |well )*(?:[^,?]{0,80},\s*)?"
    r"(?:where|how|when|what|why|who|which|whether"
    r"|can i|could i|should i|may i|do i|does|is (?:it|the|my|this|that|there)|are there"
    r"|am i|will i|would i|must i|need i|did|was|were"
    r"|if i|if my|what if|when i|suppose|say i|assuming)\b",
    re.IGNORECASE,
)
_COMPLETED_ACTION_REPORT = re.compile(
    r"\bi(?:'ve| have| just| already| did)\s+(?:submit(?:ted)?|upload(?:ed)?|paid|pay|"
    r"sent|send|complet(?:ed)?|signed|sign|finish(?:ed)?|accept(?:ed)?|"
    r"appl(?:y|ied)|register(?:ed)?)\b",
    re.IGNORECASE,
)
_SENSITIVE_FINANCIAL_DATA = re.compile(
    r"\b\d{3}-\d{2}-\d{4}\b"  # SSN
    r"|\b(?:ssn|social security)\b[^.?]{0,24}\d"
    r"|\b\d{13,19}\b"  # card/account numbers
    r"|\brouting number\b[^.?]{0,24}\d",
    re.IGNORECASE,
)
_OTHER_PERSON_CONTACT = re.compile(
    r"\b(?:roommate|room-?mate|classmate|another student|other students?|someone else|"
    r"my friend|another person)(?:'|\u2019)?s?\b[^.?]{0,40}"
    r"\b(?:phone|number|mobile|cell|email|e-mail|address|contact details?|contact info\w*)\b"
    r"|\b(?:phone|number|email|address|contact details?)\b[^.?]{0,32}"
    r"\b(?:roommate|room-?mate|classmate|another student)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NormalizedRequest:
    text: str
    resolved_text: str
    comparable_text: str
    history: tuple[dict[str, str], ...]
    page_path: str | None
    page_label: str | None
    is_follow_up: bool
    is_mutation_request: bool
    contains_sensitive_financial_data: bool
    requests_other_person_contact: bool
    reports_completed_action: bool


def normalize_request(
    message: str,
    *,
    history: Sequence[Mapping[str, str]] = (),
    page_path: str | None = None,
    page_label: str | None = None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> NormalizedRequest:
    limit = max(0, min(history_limit, 12))
    text = _normalize_text(message, MAX_MESSAGE_CHARACTERS)
    bounded_history = tuple(
        {
            "role": "assistant" if item.get("role") == "assistant" else "user",
            "content": _normalize_text(str(item.get("content", "")), MAX_HISTORY_CHARACTERS),
        }
        for item in list(history)[-limit:]
    )
    is_follow_up = bool(bounded_history) and bool(
        _FOLLOW_UP_OPENER.search(text) or _is_referential(text)
    )
    return NormalizedRequest(
        text=text,
        resolved_text=_resolve_referent(text, bounded_history, is_follow_up),
        comparable_text=text.lower(),
        history=bounded_history,
        page_path=_normalize_text(page_path, MAX_PAGE_CONTEXT_CHARACTERS) if page_path else None,
        page_label=(
            _normalize_text(page_label, MAX_PAGE_CONTEXT_CHARACTERS) if page_label else None
        ),
        is_follow_up=is_follow_up,
        is_mutation_request=_detects_mutation_request(text),
        contains_sensitive_financial_data=bool(_SENSITIVE_FINANCIAL_DATA.search(text)),
        requests_other_person_contact=bool(_OTHER_PERSON_CONTACT.search(text.lower())),
        reports_completed_action=bool(_COMPLETED_ACTION_REPORT.search(text)),
    )


def _normalize_text(value: str | None, maximum: int) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:maximum]


def _is_referential(text: str) -> bool:
    """A short question whose subject is a pronoun names nothing on its own."""

    return len(text) <= 80 and bool(_REFERENTIAL_TAIL.search(text))


def _resolve_referent(text: str, history: Sequence[Mapping[str, str]], is_follow_up: bool) -> str:
    """Resolve what a follow-up refers to deterministically.

    Both halves of the previous turn matter, and the assistant's half matters
    more: after "why can't I register?" answered with two named blockers, the
    "that" in "how do I fix that?" points at those blockers, not the question.
    """

    if not is_follow_up:
        return text
    prior_question = next(
        (item for item in reversed(history) if item["role"] == "user" and item["content"].strip()),
        None,
    )
    prior_answer = next(
        (
            item
            for item in reversed(history)
            if item["role"] == "assistant" and item["content"].strip()
        ),
        None,
    )
    if prior_question is None and prior_answer is None:
        return text
    lines = [
        text,
        "",
        "This is a follow-up. Resolve what it refers to from the turn before it, "
        "and answer only that rather than restating everything.",
    ]
    if prior_question is not None:
        lines.append(f'Their previous question: "{prior_question["content"][:240]}"')
    if prior_answer is not None:
        lines.append(f'Your previous answer: "{prior_answer["content"][:400]}"')
    return "\n".join(lines)


def _detects_mutation_request(text: str) -> bool:
    """A request for Edward to perform a write — never a question about one.

    Three frames, in precedence order: a report that the student already
    acted is not a request; asking Edward directly ("can you pay it",
    "for me") always is; an interrogative frame ("where do I upload…",
    "can I still pay…") is information-seeking even though it names an
    action verb. Only then does the imperative pattern decide.
    """

    if _COMPLETED_ACTION_REPORT.search(text):
        return False
    if _DELEGATED_ACTION.search(text):
        return True
    # Frames are judged per sentence: "My deadline passed. Can I still pay
    # it?" carries its action verb inside an interrogative sentence.
    for sentence in re.split(r"(?<=[.!?])\s+|\s+\u2014\s+|\s+\u2013\s+", text):
        stripped = sentence.strip()
        if not stripped or not _MUTATION_REQUEST.search(stripped):
            continue
        if _INFORMATIONAL_FRAME.match(stripped):
            continue
        return True
    return False
