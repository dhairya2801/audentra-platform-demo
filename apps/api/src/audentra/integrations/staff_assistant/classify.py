"""Deterministic staff request classification.

Priority order mirrors the student classifier's philosophy: conversational
openers first, then the two staff-specific policy families — action requests
(read-only refusal with a drafting offer) and unsupported metrics (honest
"that data does not exist") — then drafting, then the domain branches.
Returns None only when a model planner might route better; callers fall back
to ``general_question``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from audentra.integrations.staff_assistant.normalize import NormalizedStaffRequest
from audentra.integrations.staff_assistant.scope import (
    has_singular_student_reference,
    is_globally_scoped,
)

STAFF_REQUEST_TYPES = (
    "greeting",
    "capability_overview",
    "action_request",
    "unsupported_metric",
    "draft_email",
    "draft_sms",
    "draft_call_points",
    "student_overview",
    "student_missing_items",
    "student_blockers",
    "student_documents",
    "student_deadlines",
    "student_financials",
    "student_housing",
    "student_appointments",
    "student_communications",
    "student_engagement",
    "student_timeline",
    "student_ownership",
    "student_action_center",
    "cohort_search",
    "cohort_aggregate",
    "attention_ranking",
    "recommendation",
    "work_queue",
    "work_item_detail",
    "inquiries",
    "playbook_lookup",
    "action_rules",
    "daily_briefing",
    "general_question",
    "unsupported_or_out_of_scope",
)

# Intents that cannot be answered without a resolved student referent.
STUDENT_REQUIRED_REQUEST_TYPES = frozenset(
    {
        "student_overview",
        "student_missing_items",
        "student_blockers",
        "student_documents",
        "student_deadlines",
        "student_financials",
        "student_housing",
        "student_appointments",
        "student_communications",
        "student_engagement",
        "student_timeline",
        "student_ownership",
        "student_action_center",
        "recommendation",
        "draft_email",
        "draft_sms",
        "draft_call_points",
    }
)


@dataclass(frozen=True)
class StaffClassification:
    request_type: str
    confidence: float
    source: str = "deterministic"
    # A stable sub-reference: which metric was unsupported, which action was
    # requested, which document was named.
    reference: str | None = None
    additional_request_types: tuple[str, ...] = field(default_factory=tuple)
    # A cohort question carries its own selection: the deterministic path must
    # be able to answer "which admitted students haven't paid?" with no model
    # in the loop, and the filter is part of the classification, not a
    # separate guess made later.
    cohort_filter: Mapping[str, Any] | None = None
    cohort_group_by: str | None = None


# --- cohort recognition -----------------------------------------------------
#
# Staff cohort questions are highly patterned ("which/how many <adjective>
# students <predicate>"). Recognising them deterministically keeps the
# capability working with no model in the loop, and gives the model planner a
# correct baseline to improve on rather than invent from.

_COHORT_SUBJECT = re.compile(
    r"\b(?:students?|applicants?|admits?|admitted|cohort|class|population|people|roster"
    r"|caseload|intake)\b",
    re.IGNORECASE,
)
_COHORT_LIST_INTENT = re.compile(
    r"^\s*(?:which|who|show|list|find|give me|pull|get me)\b|\bwhich students\b"
    r"|\blist (?:the |all )?students\b|\bshow me\b",
    re.IGNORECASE,
)
_COHORT_COUNT_INTENT = re.compile(
    r"\bhow many\b|\bhow (?:big|large)\b|\bwhat (?:is|'s) the (?:number|count|breakdown"
    r"|split|size|total)\b"
    r"|\bcount of\b|\bmost common\b|\bbreak\s?down\b|\bdistribution\b"
    r"|\bsplit (?:of|by|the)\b|\bwhat percentage\b|\bwhat share\b"
    # "Which programs have the most students with unpaid deposits?" is a
    # grouped count, not a student list.
    r"|\bwhich (?:programs?|class years?|years?|counsell?ors?|advisors?|components?)\b"
    r"|\bhow does .{0,24}break\s?down\b",
    re.IGNORECASE,
)

# Each predicate maps a phrase to canonical filter fields. Order matters only
# in that every match contributes; the filter is conjunctive by construction.
_COHORT_PREDICATES: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    (
        re.compile(r"\badmitted\b|\baccepted (?:their )?(?:offer|admission)\b", re.I),
        {"offerStatus": "accepted"},
    ),
    (
        re.compile(
            r"\bstill (?:only )?offered\b|\bhaven'?t (?:accepted|responded)\b"
            r"|\bno response to (?:their )?offer\b",
            re.I,
        ),
        {"offerStatus": "offered"},
    ),
    (re.compile(r"\bdeclined\b", re.I), {"offerStatus": "declined"}),
    (
        re.compile(
            r"(?:haven'?t|have not|not|no|without|missing|outstanding|unpaid|owe|owing)"
            r"[^.?]{0,28}\bdeposits?\b"
            r"|\bdeposits?\b[^.?]{0,20}(?:unpaid|outstanding|not paid|missing)"
            # The enrollment deposit is the only payment a cohort filter knows,
            # so "haven't paid up" / "still owe" in a question about students
            # means the deposit. Without this the filter silently drops and the
            # whole population is reported as the answer.
            r"|(?:haven'?t|have not|hasn'?t|has not|not|still)[^.?]{0,20}"
            r"\bpaid(?:\s+(?:up|yet|in))?\b"
            # Only when no other object is named: "still owe an immunization
            # record" is a requirement question, not a deposit question.
            r"|\bstill (?:owe|owing|to pay|need to pay)\b"
            r"(?![^.?]{0,32}\b(?:transcript|immuni\w*|document|record|form|worksheet"
            r"|verification|orientation|housing)\b)"
            r"|\bnon[- ]?depositors?\b|\bundeposited\b",
            re.I,
        ),
        {"depositState": "unpaid"},
    ),
    (
        re.compile(
            r"\b(?:paid|posted|settled)\b[^.?]{0,20}\bdeposits?\b"
            r"|\bdeposit(?:ed)?s?\b[^.?]{0,16}\b(?:paid|posted)\b"
            r"|\bdeposited students\b",
            re.I,
        ),
        {"depositState": "paid"},
    ),
    (
        re.compile(
            r"\b(?:missing|no|without|owes?|owing|awaiting|await|lacking"
            r"|haven'?t (?:sent|submitted|uploaded)|still (?:need|owe|missing)"
            r"|waiting (?:on|for)|yet to (?:send|submit|upload))"
            r"[^.?]{0,28}\btranscripts?\b"
            r"|\btranscripts?\b[^.?]{0,24}\b(?:missing|outstanding|not (?:in|received)"
            r"|still (?:missing|outstanding|owed))\b",
            re.I,
        ),
        {"documentCategory": "transcript", "documentState": "missing"},
    ),
    (
        re.compile(r"\b(?:missing|no|without)[^.?]{0,24}\bidentity document\b", re.I),
        {"documentCategory": "identity", "documentState": "missing"},
    ),
    (
        re.compile(
            r"\bimmuni[sz]ation\b[^.?]{0,28}(?:missing|outstanding|incomplete|not|open)"
            r"|(?:missing|without|no|owes?|owing|still (?:owe|need|missing)|awaiting"
            r"|waiting (?:on|for)|haven'?t (?:sent|submitted|uploaded))"
            r"[^.?]{0,24}\bimmuni[sz]ation\b",
            re.I,
        ),
        {"requirementCode": "immunization_record", "requirementState": "open"},
    ),
    (
        re.compile(
            r"\b(?:incomplete|outstanding|unverified|missing|pending)[^.?]{0,32}"
            r"\b(?:financial aid|aid)\b[^.?]{0,20}\bverification\b"
            r"|\bverification\b[^.?]{0,24}\b(?:incomplete|outstanding|not (?:done|complete))\b"
            r"|\b(?:incomplete|outstanding)[^.?]{0,16}\bfinancial aid\b",
            re.I,
        ),
        {"aidDocumentState": "outstanding"},
    ),
    (
        re.compile(
            r"\bhousing\b[^.?]{0,24}\b(?:blocked|blocker|can'?t apply|cannot apply|locked)\b"
            r"|\b(?:blocked|blocker)\b[^.?]{0,20}\bhousing\b"
            # "cannot apply for housing" puts the obstacle before the domain.
            r"|\b(?:can'?t|cannot|unable to|not able to)\b[^.?]{0,20}"
            r"\b(?:apply|select|choose|pick|sign up)\b[^.?]{0,16}\bhousing\b",
            re.I,
        ),
        {"housingState": "blocked"},
    ),
    (re.compile(r"\binternational\b", re.I), {"residencyStatus": "international"}),
    (re.compile(r"\bdomestic\b|\bin[- ]state\b", re.I), {"residencyStatus": "domestic"}),
    (
        re.compile(r"\boverdue\b|\bpast due\b|\bmissed (?:a )?deadline\b", re.I),
        {"hasOverdueRequirement": True},
    ),
    (
        re.compile(
            r"\bonboarding (?:is )?(?:in ?complete|not (?:done|complete)|unfinished)\b"
            r"|\b(?:haven'?t|have not|hasn'?t|has not|not)\b[^.?]{0,20}"
            r"\b(?:finished|completed|done)\b[^.?]{0,12}\bonboarding\b"
            r"|\bstill (?:in )?onboarding\b",
            re.I,
        ),
        {"onboardingStatus": "in_progress"},
    ),
    (
        re.compile(r"\bfinished onboarding\b|\bonboarding complete\b", re.I),
        {"onboardingStatus": "completed"},
    ),
    (
        re.compile(
            r"\bopen (?:action center |action )?(?:item|task|work)\b"
            r"|\bassigned work\b"
            # "students in the Action Center" means students with open staff
            # work — the same membership rule the Staff Portal renders.
            r"|\b(?:in|on) (?:the |my |your )?action cent(?:er|re)\b",
            re.I,
        ),
        {"hasOpenWorkItem": True},
    ),
)

_COHORT_GROUP_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bmost common\b[^.?]{0,24}\b(?:blockers?|blocking|obstacles?|issues?)\b"
            r"|\bblockers?\b[^.?]{0,20}\b(?:breakdown|distribution|common)\b"
            r"|\bwhat(?:'s| is)? blocking\b",
            re.I,
        ),
        "blocking_requirement",
    ),
    (
        re.compile(
            r"\bby program\b|\bper program\b|\bacross programs\b|\bwhich programs?\b"
            r"|\bprograms? (?:have|has|with) the (?:most|fewest|highest|lowest)\b",
            re.I,
        ),
        "program",
    ),
    (
        re.compile(r"\bby (?:class )?year\b|\bper class year\b|\bwhich (?:class )?years?\b", re.I),
        "class_year",
    ),
    (
        re.compile(
            r"\bby (?:counselor|counsellor|advisor|staff|owner)\b"
            r"|\bper (?:counselor|advisor|staff)\b",
            re.I,
        ),
        "assigned_staff",
    ),
    (re.compile(r"\bby (?:offer|admission) status\b", re.I), "offer_status"),
    (re.compile(r"\bby deposit\b|\bdeposit (?:breakdown|split)\b", re.I), "deposit_state"),
    (re.compile(r"\bby onboarding\b", re.I), "onboarding_status"),
    (re.compile(r"\bby housing\b", re.I), "housing_state"),
)


def _cohort_predicates(text: str) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    for pattern, fields in _COHORT_PREDICATES:
        if pattern.search(text):
            for key, value in fields.items():
                filters.setdefault(key, value)
    return filters


def classify_cohort_question(text: str) -> StaffClassification | None:
    """Recognise a cohort list/count question and build its filter.

    Returns `None` unless the question is about a *group* of students: a
    question naming one student stays with the student-referent branches, and
    a cohort question with no recognisable predicate still classifies (the
    unfiltered cohort is a legitimate answer to "how many students are there").
    """

    if not _COHORT_SUBJECT.search(text):
        return None
    counting = bool(_COHORT_COUNT_INTENT.search(text))
    listing = bool(_COHORT_LIST_INTENT.search(text))
    if not counting and not listing:
        return None
    filters = _cohort_predicates(text)
    if not filters and _ACTION_CENTER.search(text):
        # "…and how many students do they cover?" asks about the population
        # behind the queue. An empty filter here would count the whole roster
        # and present it as the answer, which is the worst failure this
        # classifier can produce.
        filters = {"hasOpenWorkItem": True}
    group_by = next(
        (dimension for pattern, dimension in _COHORT_GROUP_PHRASES if pattern.search(text)),
        None,
    )
    if counting:
        return StaffClassification(
            "cohort_aggregate",
            0.95,
            cohort_filter=filters,
            # Counting without a named dimension is still a grouped count; the
            # offer status split is the least surprising default and the
            # answer reports the total alongside it.
            cohort_group_by=group_by or "offer_status",
        )
    if not filters and not group_by:
        return None
    return StaffClassification("cohort_search", 0.95, cohort_filter=filters)


_GREETING_ONLY = re.compile(
    r"^(?:hi|hiya|hello|hey|yo|good (?:morning|afternoon|evening)|howdy|greetings)"
    r"(?:[ ,!.]*(?:there|edward|again))?[ !.?]*$",
    re.IGNORECASE,
)
_CAPABILITY_QUESTION = re.compile(
    r"^(?:so |ok |okay )?(?:what (?:can|do) you (?:do|help(?: me)? with)|how can you help"
    r"|what (?:can|could|should) i ask(?: you)?|what are you(?: able to do)?"
    r"|who are you|what is this)\s*[?!.]*$",
    re.IGNORECASE,
)

# Metrics the platform genuinely does not have. Each maps to a stable
# reference so composition can explain exactly what is missing and what
# related, real data exists instead.
_UNSUPPORTED_METRICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("melt_risk", re.compile(r"\bmelt(?:ing)?\b|\bmelt (?:risk|score|probability)\b", re.I)),
    (
        "enrollment_probability",
        re.compile(
            r"\b(?:probability|likelihood|odds|chance)s? (?:of|that|she'?ll|he'?ll|they'?ll)"
            r".{0,24}\b(?:enroll|matriculat|show(?:ing)? up|yield)"
            r"|\b(?:probability|likelihood|odds|chance)s?\b.{0,40}"
            r"\b(?:enrolls?|matriculates?|shows? up|yields?)\b"
            r"|\b(?:likely|going) to (?:enroll|matriculate|show up)\b"
            r"|\byield (?:rate|probability|score)\b|\benroll(?:ment)? probability\b",
            re.I,
        ),
    ),
    (
        "recovery_likelihood",
        re.compile(r"\brecovery (?:likelihood|probability|score|chance)\b", re.I),
    ),
    ("risk_score", re.compile(r"\brisk (?:score|band|rating|model)\b|\bpropensity\b", re.I)),
    (
        "student_value",
        re.compile(
            r"\b(?:student|lifetime|tuition) value\b|\bhighest[- ]value students?\b"
            r"|\bstudents?\b.{0,40}\b(?:highest|most|greatest) value\b"
            r"|\bmost valuable students?\b"
            r"|\brevenue (?:per|from) student\b|\brank(?:ed)? by value\b",
            re.I,
        ),
    ),
    (
        "email_tracking",
        re.compile(
            r"\bopen(?:ed)? (?:my|our|the|her|his|their)(?: last| latest| recent)? email\b"
            r"|\bemail open(?:s| rate)?\b"
            r"|\bclick(?:ed)? (?:the|a|our) (?:link|email)\b|\bclick[- ]?through\b"
            r"|\bread (?:my|our|the)(?: last| latest| recent)? email\b",
            re.I,
        ),
    ),
    (
        "campaign_performance",
        re.compile(
            r"\bcampaigns?\b.{0,24}\bperform\w*\b|\bcampaign (?:results|stats|metrics)\b"
            r"|\bhow did (?:the|our|that)\b.{0,32}\bcampaign\b|\bopen rate\b",
            re.I,
        ),
    ),
    (
        "sla_compliance",
        re.compile(r"\bsla\b|\bservice[- ]level\b|\bresponse[- ]time target\b", re.I),
    ),
    (
        "room_assignment",
        re.compile(r"\broom (?:assignment|number)\b|\bwhich (?:room|dorm) (?:is|was)\b", re.I),
    ),
    (
        "disbursement_schedule",
        re.compile(
            r"\bdisburse(?:ment|d)?\b.{0,32}\b(?:schedule|date|when)\b|\bdisbursement\b",
            re.I,
        ),
    ),
    (
        "registration_window",
        re.compile(
            r"\bregistration (?:window|opens?|date)\b|\bwhen (?:does|can).{0,24}register\b",
            re.I,
        ),
    ),
    (
        "demographics",
        re.compile(
            r"\b(?:demographic|ethnicity|race|gender|date of birth|citizenship|first[- ]gen"
            r"|home address|mailing address|admit type)\b",
            re.I,
        ),
    ),
    (
        "stage_duration",
        re.compile(
            r"\bhow long (?:did|does|has).{0,40}\b(?:stage|status|review|requirement)\b"
            r"|\btime (?:in|from) (?:status|in[- ]progress)\b|\bstage duration\b",
            re.I,
        ),
    ),
)

_RANKING_LANGUAGE = re.compile(
    r"\bwho (?:should|do) i (?:contact|call|reach|work (?:on|with)|start with|prioriti[sz]e)\b"
    r"|\bwhich students? (?:need|require|deserve)s? (?:my )?attention\b"
    r"|\bwho needs (?:help|attention|outreach)\b"
    r"|\bat[- ]risk students?\b|\bstudents?\b.{0,32}\bat risk\b"
    r"|\bstudents?\b.{0,24}\b(?:highest|high|most)[- ]risk\b"
    r"|\b(?:highest|high|most)[- ]risk\b.{0,24}\bstudents?\b"
    r"|\bstudents?\b.{0,24}\b(?:riskiest|most concerning)\b"
    r"|\bprioriti[sz]e (?:my )?(?:students|outreach)\b|\bwho(?:'s| is) (?:slipping|stuck)\b"
    r"|\brank (?:the )?students\b|\bwhich students?\b.{0,32}\b(?:first|most|urgent)\b"
    r"|\btop (?:attention|at[- ]risk) student\b"
    r"|\bwhy is the top\b.{0,32}\b(?:student|case)\b.{0,16}\bflagged\b"
    r"|\bwho(?:'s| is) (?:the )?(?:most )?(?:at risk|slipping|stuck|falling behind)\b"
    r"|\bstudents? (?:who )?need(?:ing)? (?:the most )?attention\b",
    re.IGNORECASE,
)
# Metrics where ranking intent should fall through to the honest attention
# queue instead of a bare refusal.
_RANKABLE_METRICS = frozenset(
    {"melt_risk", "enrollment_probability", "recovery_likelihood", "risk_score", "student_value"}
)

# One board, many names. A membership question phrased "is X on my task
# board?" or "why does X have a task on him?" must reach the same canonical
# work-item read as "is X in the Action Center?".
_ACTION_CENTER = re.compile(
    r"\baction cent(?:er|re)\b|\btask board\b|\bwork board\b"
    r"|\b(?:have|has|got)\s+(?:(?:a|an|any|some|open|other|outstanding|staff)\s+)*"
    r"(?:task|work item|ticket)s?\b"
    r"|\b(?:task|work item|ticket)s?\s+(?:on|for|against)\s+(?:her|him|them|the student"
    r"|(?-i:[A-Z][a-z]+))\b",
    re.IGNORECASE,
)

_DOCUMENT_ENTITY = re.compile(
    r"\btranscript\b|\bimmuni[sz]\w*\b|\bresidency\b|\bidentity doc\w*\b|\bdocuments?\b"
    r"|\bupload\w*\b|\bhealth record\b",
    re.IGNORECASE,
)
_FINANCIAL_ENTITY = re.compile(
    r"\bfinancial[- ]?aid\b|\bfafsa\b|\baid\b|\bdeposit\b|\bbalance\b|\baward\b|\bpayment\b"
    r"|\bsap\b|\bverification\b|\bscholarship\b|\bloan\b"
    r"|\bfinancials?\b|\bfinancial (?:situation|state|picture|standing|position)\b"
    r"|\bbilling\b|\bcharges?\b",
    re.IGNORECASE,
)
_HOUSING_ENTITY = re.compile(r"\bhousing\b|\bdorm\w*\b|\bresidence\b", re.IGNORECASE)


# --- multi-intent decomposition --------------------------------------------
#
# One staff message often carries more than one information need ("Is Maya in
# my Action Center, what's blocking her, and has anyone contacted her?").
# Forcing it into a single intent answered one clause and let the composer
# improvise the rest — which is how an unread domain became a confident
# "there is no record of anyone contacting her". Each recognised extra ask
# becomes an *additional* request type, whose canonical reads are executed and
# whose deterministic answer is appended. Bounded to two extras: this is a
# decomposition, not an agent loop.

MAX_ADDITIONAL_INTENTS = 2

_CLAUSE_SPLIT = re.compile(r"\s*(?:,|;|\band\b|\bplus\b|\balso\b|\bthen\b)\s*", re.IGNORECASE)

# Per-domain probes, ordered. Each is a *sufficient* signal that the clause
# asks about that domain; identity resolution is unchanged and shared.
_CLAUSE_INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "student_communications",
        re.compile(
            r"\b(?:contacted|emailed|called|texted|messaged|reached out|replied|responded"
            r"|heard back|outreach|communication)\b",
            re.I,
        ),
    ),
    (
        "student_action_center",
        re.compile(r"\baction cent(?:er|re)\b|\bwork item\b|\bstaff task\b|\bmy queue\b", re.I),
    ),
    (
        "student_blockers",
        re.compile(r"\bblock(?:ed|ing|ers?)\b|\bstuck\b|\bholding (?:her|him|them) back\b", re.I),
    ),
    (
        "student_documents",
        re.compile(
            r"\bdocuments?\b|\btranscripts?\b|\bimmuni[sz]\w*\b|\buploads?\b|\bon file\b",
            re.I,
        ),
    ),
    (
        "student_deadlines",
        re.compile(
            r"\bdeadlines?\b|\bwhen (?:is|are|was) (?:it|they|that|this)\b|\bdue\b|\boverdue\b",
            re.I,
        ),
    ),
    (
        "student_financials",
        re.compile(r"\bdeposit\b|\bfinancial\b|\baid\b|\bbalance\b|\bpayment\b", re.I),
    ),
    ("student_housing", re.compile(r"\bhousing\b|\bdorm\w*\b|\bresidence\b", re.I)),
    ("student_missing_items", re.compile(r"\bmissing\b|\bstill needs?\b|\boutstanding\b", re.I)),
)


# The operational counterpart: a queue/cohort question can also carry more
# than one ask ("How many items do I have, and how many students do they
# cover?"). Same bound, same rule — each clause maps to a canonical read.
_OPERATIONAL_CLAUSE_INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "work_queue",
        re.compile(
            r"\b(?:items?|tasks?|cases?)\b|\bqueue\b|\bboard\b|\bplate\b"
            r"|\baction cent(?:er|re)\b",
            re.I,
        ),
    ),
    (
        "cohort_aggregate",
        re.compile(r"\bhow many students\b|\bstudents (?:do|does) (?:they|it|these)\b", re.I),
    ),
    ("attention_ranking", re.compile(r"\bwho should i\b|\bneeds? attention\b|\bat risk\b", re.I)),
)


def detect_additional_intents(
    request: NormalizedStaffRequest, primary: StaffClassification
) -> tuple[str, ...]:
    """The other supported asks in a multi-clause question."""

    from audentra.integrations.staff_assistant.scope import (
        COHORT_SCOPE,
        QUEUE_SCOPE,
        STUDENT_SCOPE,
        scope_of,
    )

    scope = scope_of(primary.request_type)
    if scope is STUDENT_SCOPE:
        table = _CLAUSE_INTENTS
    elif scope in (COHORT_SCOPE, QUEUE_SCOPE):
        table = _OPERATIONAL_CLAUSE_INTENTS
    else:
        return ()
    text = request.comparable_full_text
    clauses = [clause for clause in _CLAUSE_SPLIT.split(text) if len(clause.strip()) > 3]
    if len(clauses) < 2:
        return ()
    found: list[str] = []
    for clause in clauses:
        for request_type, pattern in table:
            if request_type == primary.request_type or request_type in found:
                continue
            if pattern.search(clause):
                found.append(request_type)
                break
        if len(found) >= MAX_ADDITIONAL_INTENTS:
            break
    return tuple(found[:MAX_ADDITIONAL_INTENTS])


# --- conversational cohort refinement ---------------------------------------
#
# "How many students have unpaid deposits?" → "Break that down by program."
# The refinement carries no predicate of its own; the cohort it refines is the
# previous turn's. Deterministic: the prior user question is re-read and its
# filter reused, with the new grouping applied.

_COHORT_REFINEMENT = re.compile(
    r"^(?:and |ok(?:ay)?[,.]? |now )?(?:break|split|group|slice)\s+(?:that|it|this|them|those)?"
    r"\s*(?:down)?\s*by\b"
    r"|^(?:and |ok(?:ay)?[,.]? |now )?by (?:program|class year|year|counsell?or|advisor)\b"
    r"|^what about by\b|^same (?:thing )?by\b",
    re.IGNORECASE,
)


def classify_cohort_refinement(request: NormalizedStaffRequest) -> StaffClassification | None:
    """A follow-up that re-groups the previous turn's cohort."""

    if not request.history or not _COHORT_REFINEMENT.search(request.text.strip()):
        return None
    group_by = next(
        (dimension for pattern, dimension in _COHORT_GROUP_PHRASES if pattern.search(request.text)),
        None,
    )
    if group_by is None:
        return None
    for item in reversed(request.history):
        if item["role"] != "user":
            continue
        prior = classify_cohort_question(item["content"].lower())
        if prior is None:
            continue
        return StaffClassification(
            "cohort_aggregate",
            0.95,
            source="cohort_refinement",
            cohort_filter=prior.cohort_filter,
            cohort_group_by=group_by,
        )
    return None


def classify_staff_request(request: NormalizedStaffRequest) -> StaffClassification | None:
    classification = _classify_primary(request)
    if classification is None:
        return None
    additional = detect_additional_intents(request, classification)
    if not additional:
        return classification
    cohort_filter = classification.cohort_filter
    if (
        "cohort_aggregate" in additional
        and not cohort_filter
        and _ACTION_CENTER.search(request.comparable_full_text)
    ):
        # The supplementary student count behind a queue question is queue
        # membership, not the whole roster.
        cohort_filter = {"hasOpenWorkItem": True}
    return StaffClassification(
        classification.request_type,
        classification.confidence,
        source=classification.source,
        reference=classification.reference,
        additional_request_types=additional,
        cohort_filter=cohort_filter,
        cohort_group_by=classification.cohort_group_by,
    )


def _classify_primary(request: NormalizedStaffRequest) -> StaffClassification | None:
    text = request.comparable_text
    raw = request.text

    refinement = classify_cohort_refinement(request)
    if refinement is not None:
        return refinement

    if not request.is_follow_up:
        if _GREETING_ONLY.search(raw):
            return StaffClassification("greeting", 1)
        if _CAPABILITY_QUESTION.search(raw):
            return StaffClassification("capability_overview", 0.99)

    # Drafting is allowed and takes precedence over the action gate: "draft an
    # email" is a read-only deliverable, "send an email" is not.
    if request.is_draft_request:
        # Carry the named subject (a document/requirement keyword) so the
        # draft is about what the staff member asked for, not merely the
        # most urgent open item.
        subject = _document_reference(text)
        if re.search(r"\bsms\b|\btext(?: message)?\b", text):
            return StaffClassification("draft_sms", 1, reference=subject)
        if re.search(r"\btalking points?\b|\bcall (?:script|points|notes|prep)\b|\bprep\b", text):
            return StaffClassification("draft_call_points", 1, reference=subject)
        return StaffClassification("draft_email", 1, reference=subject)

    if request.action_kind is not None:
        return StaffClassification("action_request", 1, reference=request.action_kind)

    ranking_language = bool(_RANKING_LANGUAGE.search(text))
    for reference, pattern in _UNSUPPORTED_METRICS:
        if pattern.search(text):
            # "Which students are most at risk of melting?" deserves more
            # than a refusal: disclaim the missing model, then answer with
            # the deterministic attention queue. The reference carries the
            # metric so composition leads with the disclaimer.
            if ranking_language and reference in _RANKABLE_METRICS:
                return StaffClassification("attention_ranking", 0.97, reference=reference)
            return StaffClassification("unsupported_metric", 0.97, reference=reference)

    # Ranking / attention questions (never phrased as scores by us).
    if ranking_language:
        return StaffClassification("attention_ranking", 1)

    # Action Center questions route deterministically to the same canonical
    # queue the Staff Portal renders — never to a model-guessed filter.
    if _ACTION_CENTER.search(text):
        # A *singular* student reference makes this a membership question.
        # The plain pronoun test used to be enough, which made "…and how many
        # students do they cover?" a question about one student.
        has_referent = has_singular_student_reference(request)
        if has_referent:
            # "Why is X in my Action Center?" / "Is X in the Action Center?"
            # — a membership question about one student, answered from that
            # student's actual open work items, never inferred from blockers.
            return StaffClassification("student_action_center", 0.97)
        topic = _queue_topic(text)
        if topic is not None:
            # "transcript items in my Action Center" — the canonical queue,
            # filtered by topic.
            return StaffClassification("work_queue", 0.97, reference=f"topic:{topic}")
        # Only "how many/which *students* are in the Action Center" is a
        # membership count. The cohort subject must sit in the same clause as
        # the Action Center mention — otherwise a compound question whose
        # second clause happens to say "students" turns an item count into a
        # student count.
        action_clause = next(
            (clause for clause in _CLAUSE_SPLIT.split(text) if _ACTION_CENTER.search(clause)),
            text,
        )
        if not _COHORT_SUBJECT.search(action_clause):
            return StaffClassification("work_queue", 0.95)
        # A membership count falls through to the cohort classifier, which
        # owns per-student membership.

    # Cohort questions are settled before the student-referent branches: a
    # question about a *group* must not be answered by resolving one student
    # who happens to match a word in it.
    cohort = classify_cohort_question(text)
    if cohort is not None:
        return cohort

    if re.search(
        r"\bwhat should i (?:work on|do) (?:first|today|next)\b(?!\s*(?:for|about|with)\b)"
        r"|\bmy (?:queue|tasks|work items?|board|plate|desk)\b"
        r"|\bwhat(?:'s| is) (?:on|in) (?:my|the) (?:queue|board|plate|desk)\b"
        r"|\b(?:on|in) (?:the|my) queue\b|\bon (?:the|my) board\b|\bon my plate\b"
        r"|\b(?:open|urgent|overdue) (?:tasks|work items?)\b|\btask board\b"
        r"|\bwork queue\b|\bunassigned (?:tasks|items|work)\b"
        r"|\bhow (?:much|many).{0,24}\b(?:on my plate|in my queue|on my board)\b",
        text,
    ):
        topic = _queue_topic(text)
        return StaffClassification("work_queue", 1, reference=f"topic:{topic}" if topic else None)

    # A briefing question is the Morning Brew the Staff Portal already renders.
    if re.search(
        r"\bmorning brew\b|\b(?:my |the |today'?s )?(?:morning |daily |weekly )?brief(?:ing)?\b"
        r"|\bwhat changed (?:in the )?(?:last |past )?(?:24 ?hours?|day|overnight)\b"
        r"|\bwhat happened (?:overnight|since yesterday|in the last (?:24 ?hours?|day))\b"
        r"|\bstart of day\b|\bcatch me up\b|\bwhere do things stand (?:today|overall)\b",
        text,
    ):
        return StaffClassification("daily_briefing", 0.97)

    # A pasted PREFIX-SUFFIX token is only a work-item question when the
    # message talks about work; the same shape is also how staff paste a
    # student ID, and that resolution happens against the roster instead.
    if re.search(
        r"\bwork item\b|\bwhat happened (?:on|with) (?:the )?(?:task|item|case)\b", text
    ) or (request.work_item_key is not None and re.search(r"\b(?:task|item|case|ticket)\b", text)):
        return StaffClassification("work_item_detail", 0.97, reference=request.work_item_key)

    if re.search(
        r"\binquir(?:y|ies)\b|\bsupport (?:cases?|threads?|messages?|requests?)\b"
        r"|\bwaiting on (?:the )?student\b|\bnew messages? from students\b",
        text,
    ):
        return StaffClassification("inquiries", 0.95)

    if re.search(r"\bplaybooks?\b|\bcore plays?\b|\bknowledge (?:base|cards?)\b", text):
        return StaffClassification("playbook_lookup", 1)

    if re.search(r"\b(?:action|automation) rules?\b|\bautomations?\b", text):
        return StaffClassification("action_rules", 0.95)

    # Student-scoped branches. These fire whether or not the message names the
    # student — the pipeline resolves the referent separately — but a turn
    # whose own language is about the team, the queue, or the population must
    # never fall into them. Without this guard, "What deadlines should my team
    # care about today?" became "student_deadlines" and then inherited
    # whichever student the conversation had touched last.
    if is_globally_scoped(request) and not has_singular_student_reference(request):
        global_intent = _classify_global_scope(text)
        if global_intent is not None:
            return global_intent

    has_referent_language = (
        request.candidate_student_name is not None
        or request.candidate_student_id is not None
        or request.reference_token is not None
        or request.uses_pronoun_referent
        or request.is_follow_up
    )

    if re.search(
        r"\bwhat should (?:i|we) do (?:next |first |today )?(?:for|about|with)\b"
        r"|\bwhat should (?:i|we) (?:contact|call|email|tell|ask|say to|discuss with|raise with)\b"
        r"|\bwhat should (?:i|we) follow up\b|\bfollow up (?:with|on)\b.{0,48}\babout\b"
        r"|\brecommend\b|\bbest (?:next )?(?:step|action)\b"
        r"|\bnext (?:step|action|move) for\b|\bhow (?:do|should) (?:i|we) help\b"
        r"|\bmost suitable intervention\b|\bintervention\b",
        text,
    ):
        return StaffClassification("recommendation", 0.97)

    if re.search(
        r"\bhas (?:she|he|they|the student|[a-z]+(?: [a-z]+)?) (?:responded|replied|answered"
        r"|written back|gotten back)\b|\bresponse (?:from|history)\b|\breplied\b"
        r"|\b(?:communication|outreach|contact) history\b|\brecorded (?:communications?|outreach)\b"
        r"|\blast (?:time )?(?:we|anyone) (?:contacted|emailed|texted|called)\b"
        r"|\bhas anyone\b.{0,32}\b(?:emailed|contacted|called|texted|messaged"
        r"|reached out)\b"
        r"|\b(?:have|has) (?:we|anyone|our office|the office)\b.{0,24}"
        r"\b(?:emailed|contacted|called|texted|reached out)\b"
        r"|\bheard (?:back|from)\b"
        r"|\b(?:did|has|have)\b[^.?]{0,40}\b(?:ever )?(?:repl(?:y|ied)|respond(?:ed)?"
        r"|answer(?:ed)?|get(?:ten)? back|written back)\b"
        r"|\bwhen did (?:we|anyone|the office)\b[^.?]{0,24}"
        r"\b(?:email|contact|call|text|message|reach)\b"
        r"|\b(?:any|what) (?:outreach|contact|communication)\b"
        r"|\boutreach history\b",
        text,
    ):
        return StaffClassification("student_communications", 0.97)

    if re.search(
        r"\bwho (?:owns?|is assigned|is responsible|is working|handles?)\b|\bownership\b"
        r"|\bassigned to (?:whom|who)\b|\bwhose (?:case|student)\b|\badvisor\b|\bcaseload\b",
        text,
    ):
        return StaffClassification("student_ownership", 0.95)

    if re.search(
        r"\btimeline\b|\bhistory\b|\bwhat(?:'s| has) happened (?:with|to|so far)\b"
        r"|\bactivity log\b|\bchronolog\w*\b",
        text,
    ):
        return StaffClassification("student_timeline", 0.9)

    if re.search(
        r"\bengag\w+\b|\b(?:in)?active\b|\bactivity\b|\blast (?:log(?:ged)? ?in|active|seen)\b"
        r"|\bprogress(?:ing)? (?:lately|recently)\b|\bmomentum\b",
        text,
    ):
        return StaffClassification("student_engagement", 0.9)

    if re.search(r"\bwhy (?:is|are)\b.{0,40}\b(?:blocked|stuck|held|not progressing)\b", text) or (
        re.search(r"\bblock(?:ed|ing|ers?)?\b|\bstuck\b|\bhold(?:s|ing)?\b|\bstopping\b", text)
    ):
        return StaffClassification("student_blockers", 0.97)

    if re.search(r"\bdeadlines?\b|\bdue (?:date|this|next|soon|week|by)\b|\boverdue\b", text):
        return StaffClassification("student_deadlines", 0.9)

    if _DOCUMENT_ENTITY.search(text) and re.search(
        r"\bstatus\b|\bwhat happened\b|\breceived\b|\brejected\b|\baccepted\b|\bunder review\b"
        r"|\bstate\b|\bwhere (?:is|are)\b|\bsubmitted\b|\bmissing\b"
        r"|\bon file\b|\bdo we have\b|\buploaded\b|\bwhat documents\b"
        r"|\bwhich documents\b",
        text,
    ):
        if re.search(r"\bmissing\b|\bstill need\b|\bnot (?:yet )?submitted\b", text):
            return StaffClassification(
                "student_missing_items", 0.95, reference=_document_reference(text)
            )
        return StaffClassification("student_documents", 0.95, reference=_document_reference(text))

    if re.search(
        r"\bmissing\b|\bstill (?:needs?|owes?|outstanding)\b|\bwhat(?:'s| is) (?:left|remaining)\b"
        r"|\bincomplete\b|\boutstanding (?:items?|requirements?)\b|\bchecklist\b",
        text,
    ):
        return StaffClassification("student_missing_items", 0.95)

    if _DOCUMENT_ENTITY.search(text) and has_referent_language:
        # A bare topical follow-up ("And his documents?") is a document
        # question about the referent the pipeline resolves separately.
        return StaffClassification("student_documents", 0.85, reference=_document_reference(text))

    if _FINANCIAL_ENTITY.search(text):
        return StaffClassification("student_financials", 0.9)

    if _HOUSING_ENTITY.search(text):
        return StaffClassification("student_housing", 0.9)

    if re.search(r"\bappointments?\b|\badvising session\b|\bmeeting(?:s)? (?:with|booked)\b", text):
        return StaffClassification("student_appointments", 0.9)

    if has_referent_language and re.search(
        r"\btell me about\b|\bwho is\b|\bsummar(?:y|ize)\b|\boverview\b|\bpull up\b"
        r"|\bopen\b|\bshow me\b|\blook up\b|\bprofile\b",
        text,
    ):
        return StaffClassification("student_overview", 0.97)

    if re.search(
        r"\b(?:find|search|look ?up)\b.{0,40}\bstudents?\b|\bstudents? (?:named|called|in)\b"
        r"|\bwhich students?\b|\blist (?:the )?students\b|\bshow (?:me )?(?:all )?students\b",
        text,
    ):
        return StaffClassification("student_overview", 0.9)

    if request.candidate_student_name is not None:
        # A bare name ("Maria Alvarez?") is an overview request.
        return StaffClassification("student_overview", 0.85)

    if request.reference_token is not None:
        # A bare pasted ID ("SYN-000123?") is an overview request too; the
        # pipeline resolves whether the token is a student or a work item.
        return StaffClassification("student_overview", 0.7)

    return None


def _classify_global_scope(text: str) -> StaffClassification | None:
    """Where a plainly team/population-scoped turn should go instead.

    Deadlines, priorities and "what should we care about" at team scope are
    the work queue and the attention scan; anything counting students is a
    cohort question. Returning None lets the ordinary branches continue, so
    the guard can only ever redirect a question it recognises.
    """

    cohort = classify_cohort_question(text)
    if cohort is not None:
        return cohort
    if re.search(
        r"\bdeadlines?\b|\bdue\b|\boverdue\b|\bpast due\b|\bthis week\b|\btoday\b"
        r"|\bprioriti[sz]e\b|\bfocus on\b|\bcare about\b|\bwatch (?:out )?for\b"
        r"|\bworry about\b|\bmost urgent\b",
        text,
    ):
        return StaffClassification("work_queue", 0.9, reference="team_scope")
    if _ACTION_CENTER.search(text) or re.search(r"\bqueue\b|\bboard\b|\bplate\b", text):
        return StaffClassification("work_queue", 0.9)
    return None


def _document_reference(text: str) -> str | None:
    for keyword, reference in (
        ("transcript", "transcript"),
        ("immuni", "immunization"),
        ("residency", "residency"),
        ("identity", "identity"),
        ("health", "immunization"),
    ):
        if keyword in text:
            return reference
    return None


def _queue_topic(text: str) -> str | None:
    """What a queue question is filtered by.

    Broader than the document vocabulary: staff slice their board by the work
    itself ("deposit cases", "orientation follow-ups"), not only by document
    category.
    """

    document = _document_reference(text)
    if document is not None:
        return document
    for keyword, reference in (
        ("deposit", "deposit"),
        ("orientation", "orientation"),
        ("housing", "housing"),
        ("financial aid", "aid"),
        ("aid", "aid"),
        ("payment", "payment"),
        ("enrollment", "enrollment"),
    ):
        if keyword in text:
            return reference
    return None
