"""Entity resolution for staff turns: who or what is this question about?

A proper name in a staff question can be a student, a colleague, or — as a
phrase — a department. The old pipeline searched the student roster for every
capitalised run, which is how "How many students does Elena Larkspur advise?"
became "I found 4 students matching Elena Larkspur" (the mock university
deliberately gives a staff member and four students the same full name, and
120 students her surname — a realistic overlap).

This module extracts *mentions* (people, departments, "me") and resolves each
against the staff directory, the student roster and the component list,
deciding the kind from the evidence:

* a hit in exactly one directory decides it;
* a hit in both is decided by the question's own role language — advising,
  caseload, slots, queues, leave are staff talk; blockers, deposits,
  transcripts, requirements are student talk — and is otherwise reported as
  ambiguous rather than guessed;
* nothing found is reported as not found, with close-spelling suggestions.

Every lookup goes through the traced tool layer, so the trace shows what was
searched and what came back. Identity is never chosen by a model.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from audentra.integrations.staff_assistant.normalize import (
    _NAME_STOPWORDS,
    NormalizedStaffRequest,
)

JsonDict = dict[str, Any]

# --- department lexicon -------------------------------------------------------
#
# Component names differ between institutions; the deploy maps each mock
# department to the product's component string. The lexicon here is the
# *generic* vocabulary (registrar, financial aid, housing, …) mapped onto the
# tenant's actual component list at resolution time by substring, so a new
# tenant's components resolve without code changes when their names contain
# the ordinary words.
_DEPARTMENT_PHRASES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\bregistrar(?:'s)?\b", re.I), ("registrar",)),
    (
        re.compile(
            r"\bfinancial[- ]aid\b(?!\s+(?:verification|document|counsel))|\bfin aid\b"
            r"|\bfa office\b",
            re.I,
        ),
        ("financial aid",),
    ),
    (re.compile(r"\badmissions\b(?!\s+counsel)", re.I), ("admissions",)),
    (
        re.compile(
            r"\bhousing\b(?:\s+(?:office|team|department|&|and)\b)?(?!\s+(?:preference|plan"
            r"|requirement|application|state|waitlist|coordinator))|\bresidence life\b",
            re.I,
        ),
        ("housing",),
    ),
    (
        re.compile(
            r"\binternational student services\b|\biss\b|\bdsos?\b|\binternational (?:office|team"
            r"|services)\b",
            re.I,
        ),
        ("international",),
    ),
    (
        re.compile(r"\bstudent health\b|\bhealth (?:services|records|compliance|office)\b", re.I),
        ("student health", "health"),
    ),
    (
        re.compile(r"\bstudent accounts\b|\bbursar\b|\bbilling office\b", re.I),
        ("student accounts",),
    ),
    (
        re.compile(
            r"\b(?:academic )?advising (?:team|office|center|centre|department|staff)\b"
            r"|\badvising team\b|\bthe advis(?:er|or)s\b|\bacademic advising\b",
            re.I,
        ),
        ("advising",),
    ),
    (
        re.compile(r"\bnew student programs\b|\borientation (?:team|office)\b", re.I),
        ("new student programs",),
    ),
    (
        re.compile(r"\benrollment (?:services|support)\b|\bfront desk\b", re.I),
        ("enrollment support", "enrollment services"),
    ),
    (re.compile(r"\bstudent life\b", re.I), ("student life",)),
    (
        re.compile(r"\benrollment (?:management|leadership)\b", re.I),
        ("leadership", "enrollment management"),
    ),
)

# Words that never start or form a person mention (in addition to the
# normalizer's stopwords): product nouns and department words.
_NON_NAME_PHRASES = re.compile(
    r"\b(?:Action Cent(?:er|re)|Morning Brew|Task Board|Work Queue|Student Accounts|Student Health"
    r"|Student Life|Financial Aid|Academic Advising|International Student Services"
    r"|New Student Programs|Enrollment (?:Services|Support|Management|Leadership)"
    r"|Residence Life|Registrar|Admissions|Housing|Health Services|Fall|Spring|Summer"
    r"|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
    r"|January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Edward|Audentra|Aster|Chemistry|Psychology|Biology|Engineering|Nursing|Business"
    r"|Computer Science|Civil Engineering|English Literature|Mathematics|Physics|Economics"
    r"|History|Sociology|Political Science|Environmental Science|Health Sciences|Arts"
    r"|Humanities|Education|Communication|Design|Music|Philosophy|Kinesiology"
    r"|Accounting|Finance|Marketing|Statistics|Architecture|Anthropology|Geography"
    r"|Transcript|Immunization|Deposit|Orientation|Verification|FAFSA|SEVIS|I-20)\b"
)

_STAFF_CONTEXT = re.compile(
    r"\badvis(?:e|es|ing|ees?)\b(?!\s+(?:appointment|session|meeting|slot|hold))"
    r"|\badvis(?:er|or)s?\b(?:'s)?(?!\s+(?:is|are|assigned|listed|has|have)\b)"
    r"|\bcaseload\b|\bcap\b|\bslots?\b|\bavailab\w*\b|\bavailable\b|\bcalendar\b|\bbookable\b"
    r"|\bhours\b|\bwork items?\b|\bqueue\b|\bworkload\b|\bbacklog\b|\bopen items?\b"
    r"|\boverdue items?\b|\bin progress\b|\bmanager\b|\breports? to\b|\breport(?:s|ing)? to\b"
    r"|\bdirect reports?\b|\bteam\b|\bon leave\b|\bvacation\b|\bout (?:this|next|of) (?:week"
    r"|office)\b"
    r"|\baway\b|\btitle\b|\brole\b|\bwhat does \w+ \w+ do\b|\bsalary\b|\bclosed? out\b"
    r"|\boutcome\b|\bcompare\b|\bworkloads?\b|\bstudents (?:does|do|did)\b|\btake over\b"
    r"|\btook over\b|\btook on\b|\babsorbed\b|\bbooked with\b|\bappointments? (?:does|do|did)\b"
    r"|\bappointments? (?:booked )?with\b|\bcolleague\b|\bstaff\b|\bcounsel(?:l)?or\b(?!\s+(?:is"
    r"|are)\b)"
    r"|\bstill (?:have|has) students\b|\bleft the university\b|\bstill here\b|\bdeparted\b"
    r"|\bmelt risk\b|\bsatisfaction\b|\baverage time\b|\bhow many (?:open |overdue )?(?:items"
    r"|appointments|students)\b"
    r"|\bcan (?:a student|students|someone|anyone|i|we) (?:see|book|meet|schedule with"
    r"|get in with)\b"
    r"|\bget in (?:with|to see)\b|\bversus\b|\bvs\.?\b|\btake on\b|\broom\b|\bcapacity\b"
    r"|\bperformance\b|\bhow loaded\b|\bloaded\b|\bwhat does \w+(?: \w+)? do\b",
    re.I,
)
_STUDENT_CONTEXT = re.compile(
    r"\bblock(?:ed|ing|ers?)\b|\bdeposit\b|\btranscript\b|\bimmuni\w*\b|\brequirements?\b"
    r"|\bdocuments?\b|\bonboarding\b|\bfafsa\b|\bhousing (?:preference|plan|state)\b|\bchecklist\b"
    r"|\bholds?\b|\bwaiting on\b|\benroll\w*\b|\bcounsel(?:l)?ors? (?:is|are|assigned)\b"
    r"|\badvis(?:er|or) (?:is|listed)\b|\b\w+(?: \w+)?'s (?:academic "
    r"|primary )?advis(?:er|or)\b|\bwritten to us\b"
    r"|\binquir\w*\b|\ball set\b|\bsituation\b|\bmissing\b|\boutstanding\b|\bnext appointment\b"
    r"|\bupcoming appointment\b|\bmeeting on\b|\bbook an? (?:advising )?appointment\b"
    r"|\bfind an? (?:advising )?slot\b|\bpaid\b|\bbalance\b|\bpull up\b|\bshow me\b"
    r"|\btell me about\b"
    r"|\blook ?up\b|\brecord\b|\bgoing on with\b|\bdraft\b|\bemail\b|\bmessage\b|\btext\b"
    r"|\bsms\b|\breminder\b|\bfinancial aid\b|\bverification\b|\bstudent\b|\bdeadlines?\b"
    r"|\bclass of\b|\bprogram\b|\bhas anyone\b|\bcontacted\b|\breplied\b|\bin the action cent(?:er"
    r"|re)\b",
    re.I,
)
_GENERIC_LOOKUP = re.compile(
    r"\btell me about\b|\bwho is\b|\bpull up\b|\blook ?up\b|\bshow me\b|\bopen\b|\bfind\b"
    r"|\bsearch for\b"
    r"|\bwhat(?:'s| is) (?:going on|up) with\b|\bstudent\b|\bprofile\b|\brecord\b",
    re.I,
)
# Student talk that names a *student-side* fact, not just a lookup verb.
_STUDENT_CONTEXT_SPECIFIC = re.compile(
    r"\bblock(?:ed|ing|ers?)\b|\bdeposit\b|\btranscript\b|\bimmuni\w*\b|\brequirements?\b"
    r"|\bdocuments?\b|\bonboarding\b|\bfafsa\b|\bchecklist\b|\bholds?\b|\bwaiting on\b"
    r"|\benroll\w*\b"
    r"|\badvis(?:er|or) (?:is|listed)\b|\b\w+(?: \w+)?'s (?:academic "
    r"|primary )?advis(?:er|or)\b|\bwritten to us\b"
    r"|\binquir\w*\b"
    r"|\ball set\b|\bsituation\b|\bmissing\b|\boutstanding\b|\bnext appointment\b"
    r"|\bupcoming appointment\b"
    r"|\bbook an? (?:advising )?appointment\b|\bfind an? (?:advising )?slot\b|\bpaid\b|\bbalance\b"
    r"|\bdraft\b"
    r"|\bemail\b|\bmessage\b|\bsms\b|\breminder\b|\bfinancial aid\b|\bverification\b|\bdeadlines?\b"
    r"|\bclass of\b|\bprogram\b|\bhas anyone\b|\bcontacted\b|\breplied\b|\bin the action cent(?:er"
    r"|re)\b|\bcounsel(?:l)?ors? (?:is|are|assigned)\b",
    re.I,
)

_SELF_REFERENCE = re.compile(
    r"\b(?:my|me|mine|myself)\b|\bi\b(?!-)|\bi'm\b|\bi've\b|\bam i\b|\bdo i\b|\bshould i\b",
    re.I,
)
_SELF_TEAM = re.compile(
    r"\bmy (?:team|group|office|unit|department|staff|advisers|advisors|direct reports|people"
    r"|reports)\b"
    r"|\b(?:my|our) (?:team|department|office)\b|\bwho reports to me\b|\bmy reports\b",
    re.I,
)
_POSSESSIVE = re.compile(r"(?:'s|\u2019s|s')$")

_LEADING_REFERENCE = re.compile(
    r"\b(?:to|about|for|with|from|on|regarding|does|do|did|is|was|of|by|and|or|than"
    r"|vs\.?|versus|between)\s+"
    r"([A-Z][a-z]{2,})(?:'s|\u2019s)?\b"
)
_VERB_LED = re.compile(
    r"\b(?i:pull up|look ?up|open|find|show me|search for|pull|compare)\s+([A-Z][a-z]{2,})(?:'s"
    r"|\u2019s)?\b"
)
_STUDENT_ID_TOKEN = re.compile(r"\b[A-Z]{2,6}-[A-Z0-9][A-Z0-9._-]{0,12}\b")


@dataclass(frozen=True)
class Mention:
    text: str
    kind_hint: str  # "person" | "department" | "self_team" | "student_id"
    possessive: bool = False
    #: A lower-case candidate ("petra oakenshaw") that only counts if the
    #: roster or directory has an exact match; never reported as not found.
    speculative: bool = False


@dataclass
class ResolvedEntity:
    kind: str  # "staff" | "student" | "department"
    id: str | None
    name: str
    mention: str
    match_quality: str = "exact"
    data: JsonDict = field(default_factory=dict)

    def as_trace(self) -> JsonDict:
        return {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "mention": self.mention,
            "matchQuality": self.match_quality,
        }


@dataclass
class Ambiguity:
    mention: str
    staff: list[JsonDict]
    students: list[JsonDict]
    reason: (
        str  # "staff_and_student" | "several_staff" | "several_students" | "not_found" | "fuzzy"
    )
    fuzzy: bool = False


@dataclass
class EntityResolution:
    text: str = ""
    mentions: list[Mention] = field(default_factory=list)
    staff: list[ResolvedEntity] = field(default_factory=list)
    students: list[ResolvedEntity] = field(default_factory=list)
    departments: list[ResolvedEntity] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)
    self_reference: bool = False
    self_team: bool = False
    staff_context: bool = False
    student_context: bool = False

    @property
    def primary_staff(self) -> ResolvedEntity | None:
        return self.staff[0] if self.staff else None

    @property
    def primary_student(self) -> ResolvedEntity | None:
        return self.students[0] if self.students else None

    @property
    def primary_department(self) -> ResolvedEntity | None:
        return self.departments[0] if self.departments else None

    @property
    def has_person(self) -> bool:
        return bool(self.staff or self.students)

    def as_trace(self) -> JsonDict:
        return {
            "mentions": [
                {"text": m.text, "kindHint": m.kind_hint, "possessive": m.possessive}
                for m in self.mentions
            ],
            "staff": [e.as_trace() for e in self.staff],
            "students": [e.as_trace() for e in self.students],
            "departments": [e.as_trace() for e in self.departments],
            "ambiguities": [
                {
                    "mention": a.mention,
                    "reason": a.reason,
                    "staff": [s.get("name") for s in a.staff][:6],
                    "students": [s.get("name") for s in a.students][:6],
                }
                for a in self.ambiguities
            ],
            "selfReference": self.self_reference,
            "selfTeam": self.self_team,
            "staffContext": self.staff_context,
            "studentContext": self.student_context,
        }


# --- mention extraction ---------------------------------------------------------


def extract_mentions(text: str, components: Sequence[str] = ()) -> list[Mention]:
    """People and departments named in the message, in order of appearance.

    ``components`` are the tenant's own component names; any of them written
    out in the message is a department mention regardless of the generic
    lexicon, so a tenant with unusual department names still resolves.
    """

    mentions: list[Mention] = []
    seen: set[str] = set()

    def add(candidate: str, hint: str, possessive: bool = False) -> None:
        key = (hint, candidate.lower())
        if candidate and str(key) not in seen:
            seen.add(str(key))
            mentions.append(Mention(candidate, hint, possessive))

    if _SELF_TEAM.search(text):
        add("my team", "self_team")
    for component in components:
        if component and re.search(rf"\b{re.escape(component)}\b", text, re.I):
            add(component, "department")
    for pattern, _ in _DEPARTMENT_PHRASES:
        match = pattern.search(text)
        if match and not any(m.kind_hint == "department" for m in mentions):
            add(match.group(0), "department")

    # Person mentions are extracted from the text with department and product
    # phrases blanked out, so "Financial Aid" never becomes a person.
    scrubbed = _NON_NAME_PHRASES.sub(lambda m: " " * len(m.group(0)), text)
    for component in components:
        if component:
            scrubbed = re.sub(
                rf"\b{re.escape(component)}\b",
                lambda m: " " * len(m.group(0)),
                scrubbed,
                flags=re.I,
            )
    scrubbed = _STUDENT_ID_TOKEN.sub(lambda m: " " * len(m.group(0)), scrubbed)
    for match in re.finditer(
        r"\b([A-Z][a-z]+(?:[-'\u2019][A-Z][a-z]+)?(?:\s+[A-Z][a-z]+(?:[-'\u2019][A-Z][a-z]+)?)+)(\u2019s"
        r"|'s)?\b",
        scrubbed,
    ):
        words = match.group(1).split()
        while words and words[0].lower() in _NAME_STOPWORDS:
            words = words[1:]
        words = [w for w in words if w.lower() not in _NAME_STOPWORDS]
        if len(words) >= 2:
            add(" ".join(words[:3]), "person", bool(match.group(2)))
        elif len(words) == 1:
            add(words[0], "person", bool(match.group(2)))
    for pattern in (_LEADING_REFERENCE, _VERB_LED):
        for match in pattern.finditer(scrubbed):
            word = match.group(1)
            if word.lower() in _NAME_STOPWORDS:
                continue
            if any(
                word.lower() in m.text.lower().split() for m in mentions if m.kind_hint == "person"
            ):
                continue
            possessive = bool(re.search(rf"\b{re.escape(word)}(?:'s|\u2019s)\b", scrubbed))
            add(word, "person", possessive)
    # A bare possessive first name anywhere ("Vera's items") when nothing else named it.
    for match in re.finditer(r"\b([A-Z][a-z]{2,})(?:'s|\u2019s)\b", scrubbed):
        word = match.group(1)
        if word.lower() in _NAME_STOPWORDS:
            continue
        if any(word.lower() in m.text.lower().split() for m in mentions if m.kind_hint == "person"):
            continue
        add(word, "person", True)
    # A pasted student reference ("SYN-001278") is a mention in its own
    # right; the resolver looks it up on the roster before the work-item
    # namespace gets a chance.
    for token in _STUDENT_ID_TOKEN.findall(text):
        add(token.upper(), "student_id")
    # People type names in lower case from a phone. When nothing capitalised
    # was found, lower-case word pairs (and possessive single words) become
    # speculative mentions: they resolve only on an exact roster or directory
    # hit and are otherwise dropped silently, so ordinary prose never turns
    # into a "nobody named X" answer.
    if not any(m.kind_hint == "person" for m in mentions):
        for candidate in _speculative_name_candidates(scrubbed):
            mentions.append(Mention(candidate, "person", False, speculative=True))
    return mentions


# Ordinary words that never form a person's name in a staff question; the
# normalizer's stopwords cover the rest.
_COMMON_WORDS = frozenset(
    [
        "leave",
        "right",
        "now",
        "well",
        "soon",
        "late",
        "early",
        "ever",
        "never",
        "actually",
        "really",
        "quite",
        "very",
        "much",
        "little",
        "long",
        "short",
        "back",
        "over",
        "under",
        "out",
        "off",
        "away",
        "ago",
        "past",
        "due",
        "date",
        "time",
        "day",
        "days",
        "month",
        "year",
        "week",
        "weeks",
        "vacation",
        "sick",
        "holiday",
        "office",
        "hours",
        "queue",
        "about",
        "accepted",
        "accounts",
        "action",
        "admission",
        "admissions",
        "advisee",
        "advisees",
        "adviser",
        "advisers",
        "advisor",
        "advisors",
        "after",
        "afternoon",
        "again",
        "aid",
        "all",
        "already",
        "also",
        "and",
        "another",
        "answer",
        "any",
        "anyone",
        "anything",
        "appointment",
        "appointments",
        "are",
        "assign",
        "assigned",
        "availability",
        "available",
        "before",
        "between",
        "blocked",
        "blocker",
        "blockers",
        "blocking",
        "board",
        "calendar",
        "call",
        "can",
        "case",
        "caseload",
        "cases",
        "center",
        "centre",
        "check",
        "class",
        "contact",
        "could",
        "counsellor",
        "counselor",
        "counselors",
        "count",
        "deadline",
        "deadlines",
        "department",
        "deposit",
        "desk",
        "detail",
        "details",
        "did",
        "do",
        "document",
        "documents",
        "does",
        "done",
        "during",
        "each",
        "email",
        "enrollment",
        "enrolment",
        "evening",
        "every",
        "everything",
        "fall",
        "financial",
        "find",
        "first",
        "for",
        "from",
        "get",
        "give",
        "had",
        "handle",
        "handles",
        "handling",
        "has",
        "have",
        "health",
        "hello",
        "help",
        "her",
        "here",
        "hey",
        "hi",
        "him",
        "his",
        "housing",
        "how",
        "info",
        "information",
        "international",
        "into",
        "is",
        "it",
        "item",
        "items",
        "its",
        "just",
        "last",
        "latest",
        "list",
        "look",
        "mail",
        "many",
        "may",
        "me",
        "meeting",
        "meetings",
        "member",
        "members",
        "message",
        "might",
        "missing",
        "more",
        "morning",
        "most",
        "much",
        "my",
        "new",
        "newest",
        "next",
        "nothing",
        "number",
        "office",
        "ok",
        "okay",
        "old",
        "oldest",
        "onboarding",
        "one",
        "ones",
        "only",
        "onto",
        "open",
        "other",
        "our",
        "outstanding",
        "overdue",
        "own",
        "owner",
        "owns",
        "phone",
        "picture",
        "plate",
        "please",
        "profile",
        "program",
        "pull",
        "question",
        "queue",
        "recent",
        "recently",
        "record",
        "records",
        "registrar",
        "rejected",
        "requirement",
        "requirements",
        "review",
        "reviewed",
        "reviewing",
        "rough",
        "second",
        "see",
        "send",
        "sense",
        "services",
        "should",
        "show",
        "since",
        "slot",
        "slots",
        "some",
        "someone",
        "something",
        "spring",
        "staff",
        "state",
        "status",
        "still",
        "student",
        "students",
        "summary",
        "summer",
        "support",
        "task",
        "tasks",
        "team",
        "tell",
        "text",
        "than",
        "thank",
        "thanks",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "third",
        "this",
        "those",
        "today",
        "todo",
        "tomorrow",
        "total",
        "transcript",
        "until",
        "urgent",
        "us",
        "verification",
        "verified",
        "verify",
        "was",
        "week",
        "were",
        "what",
        "when",
        "where",
        "whether",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        "will",
        "with",
        "work",
        "would",
        "year",
        "yesterday",
        "yet",
        "you",
        "your",
    ]
)


def _speculative_name_candidates(scrubbed: str, limit: int = 3) -> list[str]:
    """Lower-case word pairs (and possessive single words) that could be names.

    A sliding window over the tokens — not a regex scan — so "which of petra
    oakenshaw's items" still offers "petra oakenshaw" after "of petra" is
    rejected as ordinary prose.
    """

    candidates: list[str] = []
    seen: set[str] = set()

    def consider(candidate: str) -> None:
        key = candidate.lower()
        if key in seen or len(candidates) >= limit:
            return
        seen.add(key)
        candidates.append(candidate)

    def plausible(word: str, minimum: int) -> bool:
        return (
            len(word) >= minimum
            and word.isalpha()
            and word not in _COMMON_WORDS
            and word not in _NAME_STOPWORDS
        )

    tokens = re.findall(r"[a-z]+(?:[-'\u2019][a-z]+)?(?:'s|\u2019s)?", scrubbed.lower())
    words = [re.sub(r"(?:'s|\u2019s)$", "", token) for token in tokens]
    for index in range(len(words) - 1):
        first, second = words[index], words[index + 1]
        if plausible(first, 2) and plausible(second, 3):
            consider(f"{first} {second}")
    for token, word in zip(tokens, words, strict=True):
        if token != word and plausible(word, 3) and not any(word in c.split() for c in candidates):
            consider(word)
    return candidates


def has_staff_context(text: str) -> bool:
    return bool(_STAFF_CONTEXT.search(text))


def has_student_context(text: str) -> bool:
    return bool(_STUDENT_CONTEXT.search(text))


def has_self_reference(text: str) -> bool:
    return bool(_SELF_REFERENCE.search(text))


# --- resolution -----------------------------------------------------------------

Reader = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any] | None]]


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _exact_student_matches(items: Sequence[Mapping[str, Any]], mention: str) -> list[JsonDict]:
    needle = _norm(mention)
    tokens = needle.split()
    exact: list[JsonDict] = []
    for raw in items:
        item = dict(raw)
        name = _norm(item.get("name"))
        parts = name.split()
        preferred = _norm(item.get("preferredName"))
        if len(tokens) >= 2:
            candidates = {name, f"{preferred} {parts[-1]}" if parts else preferred}
            if needle in candidates:
                exact.append(item)
        else:
            if parts and (parts[0] == needle or parts[-1] == needle or preferred == needle):
                exact.append(item)
    return exact


def _staff_hits(items: Sequence[Mapping[str, Any]], mention: str) -> list[JsonDict]:
    tokens = _norm(mention).split()
    hits: list[JsonDict] = []
    for raw in items:
        item = dict(raw)
        quality = str(item.get("matchQuality") or "partial")
        if (len(tokens) >= 2 and quality == "exact_name") or (
            len(tokens) == 1 and quality in {"first_name", "last_name", "exact_name"}
        ):
            hits.append(item)
    return hits


async def resolve_entities(
    request: NormalizedStaffRequest,
    read: Reader,
    *,
    prefer_kind: str | None = None,
    components: Sequence[str] = (),
) -> EntityResolution:
    """Resolve every mention in the turn against the directories.

    ``read(tool, arguments)`` executes one traced tool call and returns its
    data. ``prefer_kind`` ("student" for a drafting request, "staff" for a
    team question) breaks a tie only when the directories disagree.
    """

    text = request.text
    resolution = EntityResolution(
        text=text,
        mentions=extract_mentions(text, components),
        self_reference=has_self_reference(text),
        self_team=bool(_SELF_TEAM.search(text)),
        staff_context=has_staff_context(text),
        student_context=has_student_context(text),
    )
    for mention in resolution.mentions:
        if mention.kind_hint == "department":
            component = _match_component(mention.text, components)
            if component is not None:
                resolution.departments.append(
                    ResolvedEntity("department", component, component, mention.text)
                )
            continue
        if mention.kind_hint == "student_id":
            found = await read("searchStudents", {"externalRef": mention.text})
            items = [dict(i) for i in _items(found)]
            if len(items) == 1 and not any(
                str(student.id) == str(items[0].get("id")) for student in resolution.students
            ):
                _add_students(resolution, items, mention)
            continue
        if mention.kind_hint != "person":
            continue
        await _resolve_person(
            mention,
            resolution,
            read,
            prefer_kind,
            # Draft turns get the same narrowing: "write a note to Rosa
            # Mossbank" from Rosa's adviser means their Rosa.
            narrow_to_caseload=request.action_is_supported or request.is_draft_request,
        )
    return resolution


def _match_component(phrase: str, components: Sequence[str]) -> str | None:
    lowered = phrase.lower()
    keys: tuple[str, ...] = ()
    for pattern, candidates in _DEPARTMENT_PHRASES:
        if pattern.search(phrase):
            keys = candidates
            break
    if not keys:
        keys = (lowered,)
    for key in keys:
        for component in components:
            if key in component.lower():
                return component
    # No component list (in-memory host): return the generic label so the
    # composer can still say which department was meant.
    return keys[0].title() if not components else None


async def _resolve_person(
    mention: Mention,
    resolution: EntityResolution,
    read: Reader,
    prefer_kind: str | None,
    *,
    narrow_to_caseload: bool = False,
) -> None:
    staff_search = await read("searchStaff", {"query": mention.text, "limit": 8})
    staff_items = [dict(i) for i in _items(staff_search)]
    staff_hits = _staff_hits(staff_items, mention.text)
    student_search = await read("searchStudents", {"query": mention.text, "limit": 12})
    student_items = [dict(i) for i in _items(student_search)]
    student_quality = str((student_search or {}).get("matchQuality") or "exact")
    student_exact = (
        _exact_student_matches(student_items, mention.text) if student_quality != "fuzzy" else []
    )
    if not student_exact and student_quality != "fuzzy" and len(mention.text.split()) >= 2:
        # Conjunctive substring matches on a two-word name are as good as
        # exact when nothing exact exists ("Toby Quillfeather").
        student_exact = student_items

    if mention.speculative and not staff_hits and not student_exact:
        # A lower-case guess that matched nobody exactly is just prose.
        return
    staff_unique = len(staff_hits) == 1
    if staff_hits and not student_exact:
        if staff_unique:
            _add_staff(resolution, staff_hits[0], mention)
        else:
            resolution.ambiguities.append(Ambiguity(mention.text, staff_hits, [], "several_staff"))
        return
    if student_exact and not staff_hits:
        _add_students(resolution, student_exact, mention, narrow_to_caseload=narrow_to_caseload)
        return
    if staff_hits and student_exact:
        # A generic lookup ("tell me about X", "who is X", "pull up X") is
        # student talk only by habit; when the name is also a colleague's it
        # must not silently pick the roster.
        specific_student_context = bool(
            _STUDENT_CONTEXT_SPECIFIC.search(_GENERIC_LOOKUP.sub(" ", resolution_text(resolution)))
        )
        wants_staff = resolution.staff_context and not specific_student_context
        # A student-side fact in the question ("latest enrollment status for
        # Greta Everlyn", "who is Greta Everlyn's academic adviser") settles a
        # tie in the roster's favour unless the name is followed by a
        # staff-side noun (handled below).
        wants_student = specific_student_context
        # "Elena Larkspur's advisees", "of Vera's students", "book Junia":
        # a possessive followed by a staff-side noun, or a booking verb
        # before the name, is unmistakably about the colleague.
        name_pattern = re.escape(mention.text)
        possessive_staff = re.search(
            rf"{name_pattern}(?:'s|\u2019s)\s+(?:advisees|students|caseload|work|items|queue|slots?"
            r"|calendar|appointments|team|manager|availability|next|advising|overdue|open|hours"
            r"|title|role|status|workload|reports|direct|schedule|day|week|morning|afternoon"
            r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday|phone|email|office"
            r"|extension|desk|time off|leave|vacation)\b",
            resolution_text(resolution),
            re.I,
        ) or re.search(
            rf"\b(?:book|see|meet with|schedule with|booked with|reports? to|report to|managed by"
            rf"|advised by|assigned to)\s+{name_pattern}\b",
            resolution_text(resolution),
            re.I,
        )
        if possessive_staff and staff_unique:
            wants_staff, wants_student = True, False
        if prefer_kind == "staff" and not wants_student:
            wants_staff = True
        if prefer_kind == "student" and not wants_staff:
            wants_student = True
        if wants_staff and staff_unique:
            _add_staff(resolution, staff_hits[0], mention)
            return
        if wants_student:
            _add_students(resolution, student_exact, mention, narrow_to_caseload=narrow_to_caseload)
            return
        resolution.ambiguities.append(
            Ambiguity(mention.text, staff_hits, student_exact, "staff_and_student")
        )
        return
    # Nothing exact anywhere: fuzzy student suggestions, or not found.
    fuzzy_items = student_items if student_quality == "fuzzy" else []
    resolution.ambiguities.append(
        Ambiguity(
            mention.text,
            [],
            fuzzy_items,
            "fuzzy" if fuzzy_items else "not_found",
            bool(fuzzy_items),
        )
    )


def resolution_text(resolution: EntityResolution) -> str:
    return resolution.text


def _add_staff(resolution: EntityResolution, item: Mapping[str, Any], mention: Mention) -> None:
    resolution.staff.append(
        ResolvedEntity(
            "staff",
            str(item.get("id")),
            str(item.get("name") or ""),
            mention.text,
            str(item.get("matchQuality") or "exact"),
            dict(item),
        )
    )


def _add_students(
    resolution: EntityResolution,
    items: Sequence[Mapping[str, Any]],
    mention: Mention,
    *,
    narrow_to_caseload: bool = False,
) -> None:
    if len(items) > 1 and narrow_to_caseload:
        # A write-action turn. A narrow-scope staff member saying "create a
        # follow-up for Anton Pemberwell" means *their* Anton: when exactly one
        # of the same-named students is on the asker's caseload, that is the
        # one every other candidate would be denied for anyway, so asking
        # "which of these students (most of whom I won't act on)?" wastes the
        # turn. Several on-caseload matches still ask — between two advisees
        # the guess would be real. Identity remains canonical: the roster
        # search supplied the candidates and the gateway still re-resolves and
        # re-authorizes the target. Read turns never take this branch.
        in_scope = [item for item in items if item.get("onCaseload")]
        if len(in_scope) == 1:
            items = in_scope
        elif len(in_scope) > 1:
            resolution.ambiguities.append(
                Ambiguity(mention.text, [], [dict(i) for i in in_scope], "several_students")
            )
            return
    if len(items) > 1:
        # An inline qualifier — "the one in Economics", "(the Economics one)",
        # "class of 2031", a pasted ID — picks among same-name candidates
        # deterministically from the candidates' own canonical fields.
        qualified = _qualified_candidates(resolution.text, items)
        if len(qualified) == 1:
            items = qualified
    if len(items) == 1:
        item = items[0]
        resolution.students.append(
            ResolvedEntity(
                "student",
                str(item.get("id")),
                str(item.get("preferredName") or item.get("name") or ""),
                mention.text,
                "exact",
                dict(item),
            )
        )
        return
    resolution.ambiguities.append(
        Ambiguity(mention.text, [], [dict(i) for i in items], "several_students")
    )


def _qualified_candidates(text: str, items: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    lowered = text.lower()
    matches: list[Mapping[str, Any]] = []
    for item in items:
        program = str(item.get("program") or item.get("programName") or "").lower()
        class_year = str(item.get("classYear") or "")
        external = str(item.get("externalRef") or "").lower()
        hit = bool(program) and program in lowered
        hit = hit or bool(class_year and re.search(rf"\b{re.escape(class_year)}\b", lowered))
        hit = hit or (bool(external) and external in lowered)
        if hit:
            matches.append(item)
    return matches


def _items(value: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not value:
        return []
    raw = value.get("items")
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
