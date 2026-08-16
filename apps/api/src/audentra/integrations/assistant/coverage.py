"""EXPERIMENTAL full-request coverage gate — not part of production routing.

The deterministic classifier answers "which single intent fits best?"; it has
no notion of "did I account for everything the student asked?". A confident
first-match therefore silently drops the second half of a compound question
("…why can't I apply for housing and what documents am I missing?" loses the
documents ask entirely), which is worse than a miss: the answer is correct,
confident, and incomplete.

This module makes coverage a checkable property. A request's *ask domains*
are the classifier's own domain vocabulary matched only inside interrogative
or imperative segments (a declarative "I paid my deposit." is context, never
an ask, and a concessive clause inside a question — "even though I paid" —
is stripped the same way). A classification *covers* a domain when its
selected reads include one of that domain's answering tools. Uncovered ask
domains are a coverage gap; each gap is resolved back through the
deterministic classifier on the uncovered segment alone, so "what documents
am I missing?" supplements as `missing_documents`, not a generic document
read.

Activation is opt-in only: the `AssistantPipeline(coverage_gate=...)`
argument, or the non-production environment flag
`AUDENTRA_EXPERIMENTAL_COVERAGE_GATE` (`augment` | `planner`). Unset means
off, and production wiring passes nothing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

# Private-by-convention imports are deliberate: the gate must share the
# classifier's exact domain vocabulary, or the two drift into disagreeing
# about what a domain is.
from audentra.integrations.assistant.classify import (
    _DOMAIN_HINTS,
    _DOMAIN_PRIMARY_TYPE,
    Classification,
    classify,
)
from audentra.integrations.assistant.normalize import NormalizedRequest, normalize_request
from audentra.integrations.assistant.planner import select_tool_reads

COVERAGE_GATE_MODES = ("off", "augment", "planner")
COVERAGE_GATE_ENV_FLAG = "AUDENTRA_EXPERIMENTAL_COVERAGE_GATE"

# The classifier's hint vocabulary is tuned for the operator path; the gate
# extends it locally (never touching production detection) where an ask has no
# operator-era phrasing: "when are they due?" names the deadlines domain.
_EXTRA_DOMAIN_HINTS: dict[str, re.Pattern[str]] = {
    "deadlines": re.compile(r"\bdue\b|how long do i have", re.IGNORECASE),
}

# A supplement can never widen a refusal or a social turn, and the broad
# checklist fallbacks already read cross-domain context on purpose.
_GATE_EXEMPT_TYPES = frozenset(
    {
        "greeting",
        "capability_overview",
        "general_help",
        "conversational_ack",
        "assistant_identity",
        "unsupported_or_out_of_scope",
        "policy_lookup",
        "general_question",
    }
)

# A domain is answered by these reads and only these; universal context reads
# (checklist, holds, deadlines on some intents) carry context, but only the
# owning read carries the facts the ask is about.
_DOMAIN_ANSWERING_TOOLS: dict[str, tuple[str, ...]] = {
    "deadlines": ("getStudentDeadlines",),
    "documents": ("getDocumentStatuses",),
    "aid": (
        "getFinancialAidStatus",
        "getFinancialAidSummary",
        "getAidDisbursements",
        "getFinancialAidSupportOptions",
    ),
    "account": ("getStudentAccountSummary",),
    "housing": (
        "getStudentHousingStatus",
        "getStudentHousingEligibility",
        "getHousingOptions",
    ),
    "registration": ("getRegistrationStatus",),
    "academics": ("getAcademicPlan", "getAcademicStanding"),
    "campus": ("getCampusLife",),
    "checklist": ("getOnboardingChecklist", "getEnrollmentState"),
}

# Two additional intents is the platform-wide bound on one turn (the operator
# path and the model-plan validator both stop there); the gate never exceeds
# what a model plan would be allowed to propose.
_MAX_ADDITIONAL_REQUEST_TYPES = 2

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\s*(?:;|\u2014|\u2013)\s*")
# "…and what documents am I missing" is a coordinated second question;
# "housing and financial aid" is one noun phrase. Split only when the
# conjunction introduces a new interrogative/imperative clause.
_COORDINATED_ASK_SPLIT = re.compile(
    r",?\s+\b(?:and|or|plus|also)\b\s+"
    r"(?=(?:what|why|how|when|where|which|who|whose|can|could|do|does|did|is|are|am|was"
    r"|will|would|should|have|has|show|tell|list|give|check)\b)",
    re.IGNORECASE,
)
_ASK_OPENER = re.compile(
    r"^(?:(?:so|ok|okay|and|but|also|hey|hi|please|just|um|well|now)\b[,\s]*)*"
    r"(?:what|why|how|when|where|which|who|whose|can|could|do|does|did|is|are|am|was"
    r"|will|would|should|have|has|need|any|anything"
    r"|show|tell|list|give|check|remind|explain)\b",
    re.IGNORECASE,
)
# Inside a question, a concessive/causal clause carries context the student
# asserts, not something they ask about: "why can't I register even though I
# paid?" asks about registration only. The dependency read round is what
# verifies the asserted fact.
_CONTEXT_CLAUSE = re.compile(
    r"\b(?:even though|even if|although|though|because|since|now that|given that"
    r"|seeing as|considering)\b[^,;.?!]*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CoverageAssessment:
    """What the request asks about versus what the classification will read."""

    ask_domains: tuple[str, ...]
    covered_domains: tuple[str, ...]
    uncovered_domains: tuple[str, ...]
    # Request types that would close each gap, resolved per uncovered segment
    # through the deterministic classifier, bounded by the additional-intent cap.
    supplements: tuple[str, ...]
    # Uncovered domains the cap forced the gate to drop, kept for the trace.
    dropped_domains: tuple[str, ...]


_EMPTY_ASSESSMENT = CoverageAssessment((), (), (), (), ())


def resolve_coverage_gate_mode(explicit: str | None) -> str:
    """Explicit argument first; otherwise the experimental env flag; else off."""

    if explicit is not None:
        return explicit if explicit in COVERAGE_GATE_MODES else "off"
    value = os.getenv(COVERAGE_GATE_ENV_FLAG, "").strip().lower()
    return value if value in ("augment", "planner") else "off"


def _ask_segments(text: str) -> list[str]:
    segments: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        for part in _COORDINATED_ASK_SPLIT.split(sentence):
            stripped = part.strip()
            if not stripped:
                continue
            if stripped.endswith("?") or _ASK_OPENER.match(stripped):
                segments.append(stripped)
    return segments


def _domains_in(text: str) -> list[tuple[str, int]]:
    """Domains hit in one segment with their first-match offsets."""

    hits: list[tuple[str, int]] = []
    for name, pattern in _DOMAIN_HINTS:
        match = pattern.search(text)
        if match is None and name in _EXTRA_DOMAIN_HINTS:
            match = _EXTRA_DOMAIN_HINTS[name].search(text)
        if match is not None:
            hits.append((name, match.start()))
    return hits


def _covered_domains(classification: Classification) -> tuple[str, ...]:
    selected = set(select_tool_reads(classification))
    return tuple(
        domain for domain, tools in _DOMAIN_ANSWERING_TOOLS.items() if selected.intersection(tools)
    )


def _supplement_for(domain: str, segment: str) -> str | None:
    """The intent that answers this segment's uncovered domain.

    The segment re-enters the full deterministic classifier on its own, so a
    two-part question keeps the classifier's per-domain precision for its
    second half. The result is used only when it actually reads the uncovered
    domain; otherwise the domain's primary read type is the honest floor.
    """

    candidate = classify(normalize_request(segment))
    if (
        candidate is not None
        and candidate.request_type not in _GATE_EXEMPT_TYPES
        and set(select_tool_reads(candidate)).intersection(_DOMAIN_ANSWERING_TOOLS.get(domain, ()))
    ):
        return candidate.request_type
    return _DOMAIN_PRIMARY_TYPE.get(domain)


def assess_request_coverage(
    request: NormalizedRequest,
    classification: Classification,
    *,
    allow_exempt_primary: bool = False,
) -> CoverageAssessment:
    """`allow_exempt_primary` is for the safe-fallback route only: a
    `general_question` fallback for "show my housing and aid status" is
    exactly the kind of under-covering route the gate exists to widen, while
    a deterministically classified general/conversational turn stays exempt."""

    if request.is_mutation_request or (
        classification.request_type in _GATE_EXEMPT_TYPES and not allow_exempt_primary
    ):
        return _EMPTY_ASSESSMENT
    segments = _ask_segments(request.text)
    if not segments:
        return _EMPTY_ASSESSMENT

    # Ask domains ordered by where the student raised them, each mapped to the
    # first segment that raised it so supplements classify the right fragment.
    ordered: list[str] = []
    first_segment: dict[str, str] = {}
    offset = 0
    positions: dict[str, int] = {}
    for segment in segments:
        comparable = _CONTEXT_CLAUSE.sub(" ", segment)
        for domain, start in _domains_in(comparable):
            if domain not in positions or offset + start < positions[domain]:
                positions[domain] = offset + start
                first_segment.setdefault(domain, segment)
        offset += len(segment) + 1
    ordered = sorted(positions, key=lambda domain: positions[domain])
    if not ordered:
        return _EMPTY_ASSESSMENT

    covered = _covered_domains(classification)
    uncovered = tuple(domain for domain in ordered if domain not in covered)
    if not uncovered:
        return CoverageAssessment(tuple(ordered), covered, (), (), ())

    budget = _MAX_ADDITIONAL_REQUEST_TYPES - len(classification.additional_request_types)
    supplements: list[str] = []
    dropped: list[str] = []
    taken = {classification.request_type, *classification.additional_request_types}
    for domain in uncovered:
        supplement = _supplement_for(domain, first_segment[domain])
        if supplement is None or supplement in taken:
            continue
        if len(supplements) >= max(0, budget):
            dropped.append(domain)
            continue
        supplements.append(supplement)
        taken.add(supplement)
    return CoverageAssessment(
        tuple(ordered), covered, uncovered, tuple(supplements), tuple(dropped)
    )


def augment_classification(
    classification: Classification, assessment: CoverageAssessment
) -> Classification:
    """The deterministic route widened to cover what it missed — never narrowed."""

    if not assessment.supplements:
        return classification
    return replace(
        classification,
        source="coverage_gate",
        additional_request_types=(
            *classification.additional_request_types,
            *assessment.supplements,
        ),
    )


def plan_covers_domains(tool_names: list[str], domains: tuple[str, ...]) -> bool:
    """Whether a validated model plan reads every previously uncovered domain."""

    selected = set(tool_names)
    return all(selected.intersection(_DOMAIN_ANSWERING_TOOLS.get(domain, ())) for domain in domains)
