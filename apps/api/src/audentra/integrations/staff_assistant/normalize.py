"""Staff request normalization: bounds, referents, and deterministic detections.

The staff-specific work here is *referent extraction*: a staff message may
name a student ("Tell me about Maria Alvarez"), paste an id, cite a work-item
key ("what happened on ENR-104?"), or lean on the conversation's active
student ("what is she missing?"). Extraction only produces *candidates* — the
pipeline resolves and tenant-validates every candidate against the canonical
roster before any read uses it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

MAX_MESSAGE_CHARACTERS = 2_000
MAX_HISTORY_CHARACTERS = 1_200
DEFAULT_HISTORY_LIMIT = 6

_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
# One shared shape for human-pasted keys: student external references
# (registrar/SIS ids such as SYN-000123) and staff work-item keys (ENR-104,
# DOC-8194D568) both look like PREFIX-SUFFIX. Which kind a token actually is
# can only be decided against the database, so normalization extracts the
# token and the pipeline resolves it — student external reference first,
# work item second.
_REFERENCE_TOKEN = re.compile(r"\b([A-Z]{2,6}-[A-Z0-9][A-Z0-9._-]{0,12})\b")
#: People type "ast-00507" from a phone as readily as the uppercase key; the
#: canonical form is restored on extraction. Mixed case ("Top-10") stays out —
#: that is prose, not a key.
_WORK_ITEM_KEY = re.compile(r"\b([A-Z]{2,6}-\d{1,6}|[a-z]{2,6}-\d{1,6})\b")

# Words that start sentences or commands and must never be read as a name.
_NAME_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "action",
        "after",
        "all",
        "also",
        "and",
        "answer",
        "any",
        "anyone",
        "are",
        "as",
        "ask",
        "at",
        "audentra",
        "aster",
        "before",
        "blocked",
        "call",
        "can",
        "check",
        "compare",
        "compose",
        "could",
        "create",
        "did",
        "do",
        "does",
        "draft",
        "edward",
        "email",
        "explain",
        "find",
        "first",
        "for",
        "from",
        "get",
        "give",
        "has",
        "have",
        "hello",
        "help",
        "her",
        "hey",
        "hi",
        "his",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "list",
        "look",
        "me",
        "messages",
        "missing",
        "my",
        "next",
        "no",
        "now",
        "of",
        "ok",
        "okay",
        "on",
        "open",
        "our",
        "please",
        "prep",
        "pull",
        "rank",
        "recommend",
        "review",
        "search",
        "send",
        "share",
        "show",
        "should",
        "sms",
        "so",
        "student",
        "students",
        "summarize",
        "talk",
        "tell",
        "text",
        "thanks",
        "the",
        "their",
        "them",
        "then",
        "they",
        "this",
        "to",
        "today",
        "up",
        "update",
        "was",
        "we",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "write",
        "yes",
        "you",
        "your",
        # Imperatives that open a sentence and would otherwise read as a
        # first name ("Rate Ada Ashgrove", "Move Bianca Kettleby").
        "rate",
        "move",
        "bring",
        "count",
        "assign",
        "reassign",
        "transfer",
        "put",
        "book",
        "schedule",
        "escalate",
        "flag",
        "mark",
        "add",
        "remove",
        "contact",
        "reach",
        "notify",
        "remind",
        "evaluate",
        "score",
        "grade",
        "sort",
        "summarise",
        "describe",
        "earliest",
        "whose",
    }
)

_PRONOUN_REFERENT = re.compile(
    r"\b(?:she|he|they|her|him|them|hers|his|theirs|this student|that student"
    r"|the student|this case|that case)\b",
    re.IGNORECASE,
)
_FOLLOW_UP_OPENER = re.compile(
    r"^(?:and |also |so |then |what about|how about|now |next[,.]? )", re.IGNORECASE
)

# A write/action request, split by kind so the refusal can offer the right
# read-only alternative. Order matters: drafting verbs are handled by
# classification *before* this is consulted.
_ACTION_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "send_message",
        re.compile(
            # (?-i:...) keeps the capitalized-name alternates case-sensitive
            # inside an otherwise case-insensitive pattern.
            r"\bsend\b|\bemail (?:her|him|them|the student|(?-i:[A-Z][a-z]+))\b"
            r"|\btext (?:her|him|them|the student|(?-i:[A-Z][a-z]+))\b"
            r"|\breach out to\b.{0,40}\b(?:now|today|for me)\b"
            r"|\bfire off\b|\bdispatch\b",
            re.IGNORECASE,
        ),
    ),
    (
        "place_call",
        re.compile(
            r"\bcall (?:her|him|them|the student|(?-i:[A-Z][a-z]+))\b"
            r"|\bplace (?:a|the) call\b"
            r"|\bdial\b|\bring (?:her|him|them)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "create_task",
        re.compile(
            r"\b(?:create|open|add|make|log)\b.{0,32}\b(?:task|work item|ticket|to-?do)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "assign",
        re.compile(
            r"\b(?:assign|reassign|hand (?:this|it) (?:to|off)|give (?:this|it) to"
            r"|route (?:this|it) to|take ownership)\b"
            r"|\b(?:move|transfer|switch|shift)\b.{0,48}\b(?:to|into|onto|under)\b.{0,40}"
            r"\b(?:caseload|team|queue|advis(?:er|or)|counsel(?:l)?or|desk)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "escalate",
        re.compile(r"\bescalate\b|\braise (?:this|it) to\b|\bflag for leader\b", re.IGNORECASE),
    ),
    (
        "update_record",
        re.compile(
            r"\b(?:update|change|edit|set|mark|waive|clear|complete|close|resolve"
            r"|reopen|cancel)\b.{0,48}\b(?:record|requirement|status|preference|deposit"
            r"|hold|blocker|item|task|inquiry|case|document|transcript|upload"
            r"|immuni[sz]ation)\b"
            r"|\b(?:approve|reject)\b.{0,32}\b(?:document|transcript|upload|it|this)\b"
            r"|\bmark\b.{0,40}\bas (?:accepted|rejected|complete[d]?|waived|paid"
            r"|resolved|done)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "schedule",
        re.compile(
            r"\b(?:schedule|book|set up|arrange)\b.{0,40}\b(?:appointment|meeting|session|call"
            r"|advising)\b",
            re.IGNORECASE,
        ),
    ),
)

# A question that reports a premise before asking the actual question
# ("Her transcript is complete. What is preventing housing?"). Intent must come
# from the ask, not from the premise — otherwise the premise's vocabulary wins.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# An interrogative opener: a question *about* an action ("When did we last
# email her?") is not a request to perform it.
_INTERROGATIVE_OPENER = re.compile(
    r"^(?:did|do|does|when|has|have|had|was|were|is|are|who|whom|whose|which|why|how"
    r"|can|could|may|would|will|should|is it possible)\b",
    re.IGNORECASE,
)

_DRAFT_REQUEST = re.compile(
    r"\b(?:draft|compose|write(?:\s+me)?(?:\s+up)?|prepare|prep|give me|put together"
    r"|make me)\b.{0,60}"
    r"\b(?:email|e-mail|message|sms|text|reply|response|note|talking points?"
    r"|call (?:script|points|notes)|outreach)\b"
    # "Email Elena about her aid verification" names a recipient and a subject
    # and no draft exists yet. Edward cannot send, so the only thing that
    # sentence can mean is "write it" — and answering "I need a draft first"
    # to a request that *is* the draft request wastes a turn.
    r"|\b(?:e-?mail|message|write to)\s+(?-i:[A-Z][a-z]+)\b[^.!?]{0,40}\babout\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NormalizedStaffRequest:
    text: str
    # The sentence that carries the ask, when the message states a premise
    # first. Classification reads this; entity extraction reads the whole text.
    focus_text: str
    resolved_text: str
    # Lowercased focus sentence — what the domain branches classify on.
    comparable_text: str
    # Lowercased whole message — what the multi-intent scan reads.
    comparable_full_text: str
    history: tuple[dict[str, str], ...]
    is_follow_up: bool
    uses_pronoun_referent: bool
    candidate_student_name: str | None
    candidate_student_id: str | None
    # A PREFIX-SUFFIX token that may be a student external reference OR a
    # work-item key; the pipeline resolves which against the database.
    reference_token: str | None
    work_item_key: str | None
    action_kind: str | None
    #: True when the write plane recognized a supported action for this turn.
    #: Classification then resolves entities normally instead of answering with
    #: an action boundary the action plane is about to replace anyway.
    action_is_supported: bool
    is_draft_request: bool


def normalize_staff_request(
    message: str,
    *,
    history: Sequence[Mapping[str, str]] = (),
    history_limit: int = DEFAULT_HISTORY_LIMIT,
    action_is_supported: bool = False,
) -> NormalizedStaffRequest:
    limit = max(0, min(history_limit, 12))
    text = _normalize_text(message, MAX_MESSAGE_CHARACTERS)
    bounded_history = tuple(
        {
            "role": "assistant" if item.get("role") == "assistant" else "user",
            "content": _normalize_text(str(item.get("content", "")), MAX_HISTORY_CHARACTERS),
        }
        for item in list(history)[-limit:]
    )
    is_draft = bool(_DRAFT_REQUEST.search(text))
    action_kind = None
    if not is_draft and not _INTERROGATIVE_OPENER.match(text):
        # "When did we last email Marisol?" asks about history; only an
        # imperative or a request to act is an action request.
        for kind, pattern in _ACTION_KINDS:
            if pattern.search(text):
                action_kind = kind
                break
    uses_pronoun = bool(_PRONOUN_REFERENT.search(text))
    is_follow_up = bool(bounded_history) and (
        bool(_FOLLOW_UP_OPENER.search(text)) or uses_pronoun or len(text) <= 60
    )
    return NormalizedStaffRequest(
        text=text,
        focus_text=question_focus(text),
        resolved_text=_resolve_referent(text, bounded_history, is_follow_up),
        comparable_text=question_focus(text).lower(),
        comparable_full_text=text.lower(),
        history=bounded_history,
        is_follow_up=is_follow_up,
        uses_pronoun_referent=uses_pronoun,
        candidate_student_name=extract_candidate_name(text),
        candidate_student_id=_extract_uuid(text),
        reference_token=_extract_reference_token(text),
        work_item_key=_extract_work_item_key(text),
        action_kind=action_kind,
        action_is_supported=action_is_supported,
        is_draft_request=is_draft,
    )


def question_focus(text: str) -> str:
    """The sentence that carries the ask.

    A staff member often states what they believe before asking
    ("Marisol paid her deposit. What is actually blocking her?"). Classifying
    the whole message lets the premise's vocabulary ("deposit") outrank the
    question's ("blocking"). The last interrogative sentence — or, failing
    that, the last sentence — is the ask; entity extraction still reads the
    whole message so the premise's name is not lost.
    """

    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    if len(sentences) < 2:
        return text
    questions = [part for part in sentences if part.endswith("?")]
    if questions:
        return questions[-1]
    return sentences[-1]


def extract_candidate_name(text: str) -> str | None:
    """The most plausible student-name mention in the message, if any.

    Multi-word capitalized runs win; a single capitalized word counts only
    when introduced by a referring preposition ("about Maria"). Stopwords are
    stripped from the front of a run so "Tell Maria Alvarez" yields the name,
    not the verb.
    """

    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text):
        words = match.group(1).split()
        while words and words[0].lower() in _NAME_STOPWORDS:
            words = words[1:]
        words = [word for word in words if word.lower() not in _NAME_STOPWORDS]
        if len(words) >= 2:
            return " ".join(words[:3])
        if len(words) == 1 and _is_referred_single_name(text, words[0]):
            return words[0]
    single = re.search(
        r"\b(?:about|for|on|regarding|student|named|called)\s+([A-Z][a-z]{2,})\b", text
    )
    if single and single.group(1).lower() not in _NAME_STOPWORDS:
        return single.group(1)
    # A lookup verb followed by one capitalized word is a name too ("Pull up
    # Quillfeather.") — surname-only lookups are normal staff shorthand, and
    # the roster search decides how many students it matches.
    verb_led = re.search(
        # The verb may open the sentence ("Pull up ..."), so it matches
        # case-insensitively; the (?-i:) group keeps the name capitalized.
        # A trailing courtesy ("… for me.", "… please") must not hide the name.
        r"\b(?i:pull up|look ?up|open|find|show me|search for|pull)\s+([A-Z][a-z]{2,})\b"
        r"(?i:\s+(?:for me|please|please\.|now))?\s*[.!?]?$",
        text,
    )
    if verb_led and verb_led.group(1).lower() not in _NAME_STOPWORDS:
        return verb_led.group(1)
    return None


def _is_referred_single_name(text: str, word: str) -> bool:
    return bool(
        re.search(rf"\b(?:about|for|on|regarding|student|named|called)\s+{re.escape(word)}\b", text)
    )


def _extract_uuid(text: str) -> str | None:
    match = _UUID_PATTERN.search(text)
    return match.group(0).lower() if match else None


def _extract_reference_token(text: str) -> str | None:
    match = _REFERENCE_TOKEN.search(text)
    return match.group(1) if match else None


def _extract_work_item_key(text: str) -> str | None:
    match = _WORK_ITEM_KEY.search(text)
    return match.group(1).upper() if match else None


def _normalize_text(value: str | None, maximum: int) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:maximum]


def _resolve_referent(text: str, history: Sequence[Mapping[str, str]], is_follow_up: bool) -> str:
    """Attach the prior turn to a follow-up so downstream prose stays coherent.

    Entity resolution (which *student* "she" is) happens in the pipeline from
    the durable conversation referent — this only preserves linguistic
    context for the composer.
    """

    if not is_follow_up:
        return text
    prior_question = next(
        (item for item in reversed(history) if item["role"] == "user" and item["content"]),
        None,
    )
    prior_answer = next(
        (item for item in reversed(history) if item["role"] == "assistant" and item["content"]),
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
