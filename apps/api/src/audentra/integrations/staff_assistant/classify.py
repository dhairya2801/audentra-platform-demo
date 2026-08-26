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

from audentra.integrations.staff_assistant.entities import EntityResolution
from audentra.integrations.staff_assistant.normalize import (
    NormalizedStaffRequest,
    normalize_staff_request,
)
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
    "mailbox_read",
    "daily_briefing",
    # Staff-aware intents: about a colleague, about the signed-in member,
    # about a team or department, or an aggregate over the queue/inquiries.
    "staff_profile",
    "staff_workload",
    "staff_availability",
    "staff_caseload",
    "staff_appointments",
    "staff_comparison",
    "my_work",
    "my_profile",
    "team_overview",
    "department_operations",
    "queue_aggregate",
    "inquiry_aggregate",
    "staff_directory",
    "not_found",
    "general_question",
    "unsupported_or_out_of_scope",
)

# Intents that cannot be answered without a resolved staff referent.
STAFF_REQUIRED_REQUEST_TYPES = frozenset(
    {
        "staff_profile",
        "staff_workload",
        "staff_availability",
        "staff_caseload",
        "staff_appointments",
        "staff_comparison",
    }
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
    # Filters for an *additional* intent in a compound question, keyed by
    # request type ("how many items are unassigned and how many inquiries are
    # awaiting a reply" carries one filter set per clause).
    additional_filters: Mapping[str, Mapping[str, Any]] | None = None


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
    r"^\s*count\b|\bcount (?:the|all|of|up)\b|\bnumber of\b|\btally\b"
    r"|\bhow many\b|\bhow (?:big|large)\b|\bwhat (?:is|'s) the (?:number|count|breakdown"
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
            r"|\bnon[- ]?depositors?\b|\bundeposited\b"
            r"|\b(?:haven'?t|have not|hasn'?t|has not|not|never) deposited\b",
            re.I,
        ),
        {"depositState": "unpaid"},
    ),
    (
        re.compile(
            r"\b(?:paid|posted|settled)\b[^.?]{0,20}\bdeposits?\b"
            r"|\bdeposit(?:ed)?s?\b[^.?]{0,16}\b(?:paid|posted)\b"
            r"|(?<!not )(?<!never )(?<!n't )\bdeposited\b",
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
    (
        re.compile(
            r"\b(?:no|without an?|lack(?:ing)? an?|not (?:been )?assigned an?|don'?t have an?"
            r"|have no|has no|missing an?) (?:primary |academic |assigned )?advis(?:er|or)s?\b"
            r"|\bno advis(?:er|or) at all\b|\bunassigned advis(?:er|or)\b|\badvis(?:er|or)less\b"
            r"|\b(?:don'?t|do not|doesn'?t|does not|without) (?:have )?(?:anyone|anybody|someone"
            r"|an? advis(?:er|or)) advising\b|\bno ?(?:one|body) advising\b|\bunadvised\b"
            r"|\bnot (?:yet )?(?:been )?assigned (?:to )?(?:an? )?advis(?:er|or)\b",
            re.I,
        ),
        {"adviserState": "none"},
    ),
    (
        re.compile(
            r"\badvis(?:er|or)s? (?:who|that) (?:has |have |had )?(?:left|departed|resigned|quit)\b"
            r"|\badvis(?:er|or)s? (?:who|that) (?:is|are) no longer\b|\bdeparted advis(?:er|or)\b"
            r"|\badvis(?:er|or)s? (?:who|that) (?:has|have) (?:left|departed)\b"
            r"|\bformer advis(?:er|or)\b|\badvis(?:er|or) (?:has |who )?left the university\b",
            re.I,
        ),
        {"adviserState": "departed"},
    ),
    (
        re.compile(
            r"\badvis(?:er|or)s? (?:who|that) (?:is|are) (?:currently |out )?on leave\b"
            r"|\badvis(?:er|or)s? (?:currently )?on leave\b|\bon[- ]leave advis(?:er|or)\b",
            re.I,
        ),
        {"adviserState": "on_leave"},
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
    if not filters and _OPEN_WORK_PHRASE.search(text):
        filters = {"hasOpenWorkItem": True}
    if not filters and counting and _UNRECOGNISED_QUALIFIER.search(text):
        # "How many students <something we did not understand>?" must not
        # become the roster size. Leave it to the model planner (or an honest
        # "I couldn't work out that filter") rather than answering 2,576.
        return None
    group_by = next(
        (dimension for pattern, dimension in _COHORT_GROUP_PHRASES if pattern.search(text)),
        None,
    )
    if counting:
        if group_by is None and filters.get("adviserState") in {"departed", "on_leave"}:
            # "students whose adviser has left" — the adviser's name is the
            # useful breakdown, not the offer status.
            group_by = "primary_adviser"
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


_OPEN_WORK_PHRASE = re.compile(
    r"\bopen (?:action center |action |staff )?work\b|\bopen (?:work )?items?\b", re.I
)
# A count question whose subject carries a qualifier the predicate table did
# not recognise: a relative clause or a preposition after "students".
_UNRECOGNISED_QUALIFIER = re.compile(
    r"\bstudents?\b\s+(?:who|that|whose|with|without|in|from|assigned|advised|on|under|needing"
    r"|does|do|did|is|are|has|have)\b",
    re.I,
)

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
    (
        "staff_performance_rating",
        re.compile(
            r"\brate\b.{0,40}\bperformance\b|\bperformance (?:rating|score|review|grade)\b"
            r"|\bout of (?:ten|10|five|5|100)\b|\bsatisfaction (?:score|rating|survey)s?\b"
            r"|\brank (?:the |our |my )?(?:advis(?:er|or)s|staff|counsel(?:l)?ors|team) by\b",
            re.I,
        ),
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
    (
        "inquiry_aggregate",
        re.compile(
            r"\binquir(?:y|ies)\b|\b(?:student |support )?requests?\b.{0,24}\b(?:awaiting|reply"
            r"|unanswered)\b",
            re.I,
        ),
    ),
    (
        "queue_aggregate",
        re.compile(
            r"\bhow many (?:open |unassigned |urgent |overdue )?(?:work )?(?:items|tasks|cases)\b"
            r"|\bunassigned\b",
            re.I,
        ),
    ),
)


_STAFF_CLAUSE_INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "staff_caseload",
        re.compile(r"\badvis(?:e|es|ees?)\b|\bcaseload\b|\bstudents (?:does|do)\b|\bcap\b", re.I),
    ),
    (
        "staff_workload",
        re.compile(
            r"\b(?:open |overdue |urgent )?(?:work )?items?\b|\bqueue\b|\bworkload\b|\bbacklog\b",
            re.I,
        ),
    ),
    ("staff_availability", re.compile(r"\bslots?\b|\bavailab\w*\b|\bbook\w*\b", re.I)),
    ("staff_appointments", re.compile(r"\bappointments?\b|\bcalendar\b", re.I)),
)


def detect_additional_intents(
    request: NormalizedStaffRequest, primary: StaffClassification
) -> tuple[str, ...]:
    """The other supported asks in a multi-clause question."""

    from audentra.integrations.staff_assistant.scope import (
        COHORT_SCOPE,
        QUEUE_SCOPE,
        STAFF_SCOPE,
        STUDENT_SCOPE,
        scope_of,
    )

    scope = scope_of(primary.request_type)
    if scope is STUDENT_SCOPE:
        table = _CLAUSE_INTENTS
    elif scope in (COHORT_SCOPE, QUEUE_SCOPE):
        table = _OPERATIONAL_CLAUSE_INTENTS
    elif scope is STAFF_SCOPE and primary.request_type in STAFF_REQUIRED_REQUEST_TYPES:
        table = _STAFF_CLAUSE_INTENTS
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


def classify_staff_request(
    request: NormalizedStaffRequest, entities: EntityResolution | None = None
) -> StaffClassification | None:
    classification = _classify_primary(request, entities)
    if classification is None:
        return None
    if classification.additional_request_types:
        # The compound classifier already split the clauses.
        return classification
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
        additional_filters=classification.additional_filters,
    )


def _classify_primary(
    request: NormalizedStaffRequest, entities: EntityResolution | None = None
) -> StaffClassification | None:
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

    if re.search(
        r"\b(?:read|show|list|search|find|scan|summari[sz]e|sort|prioriti[sz]e)\b"
        r".{0,48}\b(?:my |our |the )?(?:emails?|e-mails?|inbox|mailbox|mail)\b"
        r"|\b(?:emails?|e-mails?|inbox|mailbox)\b.{0,48}"
        r"\b(?:urgent|important|priority|recent|new|unread)\b",
        text,
    ):
        return StaffClassification("mailbox_read", 0.99)

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

    # Staff-aware routing: a colleague, the signed-in member, a team, a
    # department, or an aggregate over the queue/inquiries. Settled before the
    # ranking, Action Center and cohort branches because those branches read
    # the student roster and the board, which is exactly where a staff
    # question must never land.
    staff_scope = _classify_staff_scope(request, entities, text)
    if staff_scope is not None:
        return staff_scope

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
        r"|\bassigned to (?:whom|who)\b|\bwhose (?:case|student)\b|\badvis[eo]rs?\b|\bcaseload\b"
        r"|\bcounsel(?:l)?ors?\b|\bdso\b|\bwho (?:is|are) (?:looking after|responsible for"
        r"|assigned)\b"
        r"|\bbook(?:ing)? an? (?:advising )?(?:appointment|slot|meeting)\b"
        r"|\bfind an? (?:advising |open )?slot\b|\bcan(?:'t|not)? (?:\w+ )?(?:book|find|get) an?\b"
        r"|\badvising slot\b|\blooks? after\b|\bwho (?:handles|is handling|takes care of)\b"
        r"|\badvising side\b|\bwho (?:supports|works with)\b|\bpoint of contact\b",
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

    if re.search(
        r"\bappointments?\b|\badvising session\b|\bmeeting(?:s)? (?:with|booked|on)\b"
        r"|\bwho (?:is|are) \w+(?: \w+)? meeting\b|\bmeeting on\b|\bbooked (?:for|on|with|in)\b"
        r"|\b(?:see|seeing|meet|meeting) (?:anyone|someone|anybody)\b|\bscheduled to (?:see|meet)\b"
        r"|\bcoming in\b|\bbooked in\b",
        text,
    ):
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


# --- staff scope ---------------------------------------------------------------
#
# A question whose subject is a person on staff, the signed-in member, a team
# or a department. The facet (workload / caseload / availability /
# appointments / profile) is read from the turn's own words; identity comes
# from the resolver, never from the classifier.

_FACET_AVAILABILITY = re.compile(
    r"\bslots?\b|\bavailab\w*\b|\bavailable\b|\bbook(?:able|ed|ing)?\b|\bopen(?:ing)?s? (?:in"
    r"|on) (?:the|her|his|their)? ?(?:calendar|schedule)\b"
    r"|\b(?:which|what) days\b|\bhours\b|\bon leave\b|\bvacation\b|\bout (?:this|next) week\b"
    r"|\bout of (?:the )?office\b|\baway\b|\bfree (?:this|next|tomorrow|today)\b"
    r"|\bcan (?:a student|students|someone|i) (?:see|book|meet)\b"
    r"|\bstill here\b|\bstill (?:with|at) (?:the university|us)\b"
    r"|\banything open\b|\bopenings?\b|\bopen (?:time|times)\b|\bearliest\b|\bsoonest\b"
    r"|\bget in with\b|\bget in to see\b|\bnext (?:time|opening|availability)\b",
    re.I,
)
_FACET_APPOINTMENTS = re.compile(
    r"\bappointments?\b|\bcalendar\b|\bmeetings?\b|\bclosed? out\b|\boutcome\b|\bno[- ]shows?\b",
    re.I,
)
_FACET_CASELOAD = re.compile(
    r"\badvis(?:e|es|ing|ees?)\b|\bcaseload\b|\bcap\b|\bstudents (?:does|do|did|is|are|has|have)\b"
    r"|\b(?:her|his|their) students\b|\bstudents? (?:assigned|belong)\b|\btake over\b"
    r"|\btook (?:over|on)\b"
    r"|\babsorbed\b|\bstill (?:have|has) students\b|\bassigned to (?:him|her|them)\b"
    r"|\bhow many students\b"
    r"|\bof [A-Z][a-z]+(?: [A-Z][a-z]+)?'s (?:students|advisees)\b|\bpaid their deposit\b"
    r"|\bcompleted (?:their )?advising\b",
    re.I,
)
_FACET_WORKLOAD = re.compile(
    r"\bwork items?\b|\bitems?\b|\btasks?\b|\bcases?\b|\bqueue\b|\bplate\b|\bworkload\b|\bbacklog\b"
    r"|\boverdue\b|\bin progress\b|\bstale\b|\bopen work\b|\bwork on\b|\bstart with\b"
    r"|\bwork first\b"
    r"|\bbehind\b|\bfalling behind\b|\bdoing on\b",
    re.I,
)
_FACET_PROFILE = re.compile(
    r"\bwho is\b|\btell me about\b|\bmanager\b|\breports? to\b|\bdirect reports?\b|\btitle\b"
    r"|\brole\b"
    r"|\bwhat does\b|\bprofile\b|\bpull up\b|\blook ?up\b|\bstatus\b|\bsummar\w*\b|\bworkload\b",
    re.I,
)
_COMPARISON = re.compile(
    r"\bcompare\b|\bcomparison\b|\bversus\b|\bvs\.?\b|\bwho has (?:more|fewer|the (?:bigger|larger"
    r"|smaller|heavier|lighter))\b"
    r"|\b(?:more|fewer|bigger|larger|heavier|lighter) .{0,40}\b(?:than|or)\b|\bside by side\b"
    r"|\bbetween\b",
    re.I,
)
_TEAM_LANGUAGE = re.compile(
    r"\bmy (?:team|direct reports|reports|people|advisers|advisors|staff|group|unit)\b"
    r"|\bwho reports to me\b"
    r"|\bthe (?:advising|admissions|registrar|housing|financial aid|aid) team\b|\badvising team\b"
    r"|\b(?:which|what|any|the|all|how many) (?:of (?:my|the|our) )?(?:active |busy "
    r"|available )?(?:advis(?:er|or)s?|counsel(?:l)?ors?|evaluators?|coordinators?|specialists?"
    r"|staff members?|team members?|people)\b"
    r"|\banyone (?:on|in) (?:the|my|our) (?:team|department|office)\b|\bwho on (?:the|my"
    r"|our) (?:team|department|office)\b"
    r"|\bthe team'?s?\b|\bteam'?s capacity\b|\bspare capacity\b|\bover (?:their"
    r"|the) (?:caseload )?cap\b",
    re.I,
)
_DEPARTMENT_OPS = re.compile(
    r"\bsituation\b|\bdoing\b|\bhow is\b|\bhow'?s\b|\bsummar\w*\b|\bstatus\b|\boverview\b"
    r"|\bpicture\b"
    r"|\bwho in\b|\bwho is out\b|\bout this week\b|\bon leave\b|\bdeparted\b|\bstaff\b|\bpeople\b"
    r"|\bwork in\b"
    r"|\brisk\b|\bbiggest\b|\bworried\b|\bconcern\w*\b|\bcatch me up\b|\bhow many staff\b|\bteam\b",
    re.I,
)
_QUEUE_COUNT = re.compile(
    r"\bhow (?:many|much)\b|\bcount\b|\bnumber of\b|\bhow big\b|\btotal\b|\bare there (?:any"
    r"|still)\b|\bany\b.{0,24}\b(?:left|remaining|open)\b"
    r"|\bwhich (?:component|department|team|office|staff|person|people|assignee|owner|member)s?\b"
    r"|\bwho (?:has|have|owns?|holds?)\b"
    r"|\bmost (?:overdue|unassigned|open|urgent|work|items)\b|\bbreak\s?down\b|\bby (?:component"
    r"|department|assignee|owner|priority|status)\b",
    re.I,
)
_QUEUE_NOUN = re.compile(
    r"\b(?:work )?items?\b|\btasks?\b|\bcases?\b|\btickets?\b|\breviews?\b|\bfollow[- ]?ups?\b"
    r"|\bwork\b"
    r"|\baction cent(?:er|re)\b|\bqueue\b|\bboard\b|\bbacklog\b",
    re.I,
)
_INQUIRY_NOUN = re.compile(
    r"\binquir(?:y|ies)\b|\b(?:student |support |help )?requests?\b(?!\s+(?:to|for) )"
    r"|\bsupport (?:cases?|tickets?|threads?)\b"
    r"|\bmessages? from students\b|\bunanswered (?:messages?|questions?)\b|\bstudent questions?\b"
    r"|\bhelp requests?\b|\bwaiting (?:the )?longest\b|\b(?:answer|reply to"
    r"|respond to) (?:them|him|her)\b"
    r"|\bwaiting for (?:an answer|a reply|a response|someone)\b|\bwritten (?:in|to us)\b",
    re.I,
)


_SECOND_ASK = re.compile(r"(?:,|;|\band\b)\s*(?:how many|how much|which|what)\b.*$", re.I)


def _queue_filters_from_text(text: str, entities: EntityResolution | None) -> dict[str, Any]:
    # "How many open items … and how many are overdue?" — the second ask is
    # answered by the summary's own overdue count; it must not narrow the
    # first.
    if re.search(r"\bhow many\b.*(?:,|;|\band\b)\s*how many\b", text, re.I):
        text = _SECOND_ASK.sub("", text)
    filters: dict[str, Any] = {}
    if re.search(
        r"\bunassigned\b|\bno owner\b|\bnobody owns\b|\bno one owns\b|\bwithout an? (?:owner"
        r"|assignee)\b|\bunowned\b",
        text,
        re.I,
    ):
        filters["ownership"] = "unassigned"
    if re.search(r"\burgent\b", text, re.I):
        filters["priority"] = "urgent"
    if re.search(r"\bescalated\b", text, re.I):
        filters["escalated"] = True
    if re.search(
        r"\boverdue\b|\bpast due\b|\bpast (?:their |the )?(?:deadline|sla|due date)\b|\blate\b",
        text,
        re.I,
    ):
        filters["dueWindow"] = "overdue"
    elif re.search(r"\bdue today\b|\btoday'?s deadlines?\b", text, re.I):
        filters["dueWindow"] = "today"
    elif re.search(
        r"\bdue (?:this|next) week\b|\bdue in the next (?:seven|7) days\b|\bnext (?:seven"
        r"|7) days\b|\bthis week\b|\bcoming (?:up|due)\b",
        text,
        re.I,
    ):
        filters["dueWindow"] = "seven_days"
    stale = re.search(
        r"\bin progress\b.{0,60}\b(?:more than|over|longer than|for)\s+(?:a|an|\d+)\s*(week|weeks"
        r"|day|days|month)\b",
        text,
        re.I,
    )
    if stale:
        number = re.search(r"\b(\d+)\b", stale.group(0))
        unit = stale.group(1).lower()
        count = int(number.group(1)) if number else 1
        days = count * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
        filters["inProgressOverDays"] = days
    elif re.search(
        r"\bstale\b|\bno update\b|\bnot (?:been )?(?:touched|updated)\b|\buntouched\b"
        r"|\bsitting in progress\b",
        text,
        re.I,
    ):
        filters["stale"] = True
    if re.search(r"\bdocument reviews?\b", text, re.I):
        filters["workType"] = "document_review"
    topic = _queue_topic(text.lower())
    if topic and not filters.get("workType"):
        filters["topic"] = topic
    if re.search(r"\bclosed\b|\bdone\b|\bcompleted\b", text, re.I) and not re.search(
        r"\bopen\b", text, re.I
    ):
        filters["status"] = "closed"
    if entities is not None and entities.primary_department is not None:
        filters["component"] = entities.primary_department.name
    group_by = None
    if re.search(
        r"\bwhich (?:component|department|team|office|unit)s?\b|\bby (?:component|department"
        r"|team)\b|\bper (?:component|department|team)\b|\b(?:component|department|team) (?:has"
        r"|with) the most\b",
        text,
        re.I,
    ):
        group_by = "component"
    elif re.search(
        r"\bwhich (?:staff|person|people|assignee|owner|member|adviser|advisor|counsel"
        r"|evaluator)\b|\bwho (?:has|have|owns?|holds?)\b|\bby (?:assignee|owner|staff"
        r"|person)\b|\bper (?:assignee|owner|staff|person)\b|\bstaff members? (?:with|have|has)\b",
        text,
        re.I,
    ):
        group_by = "assignee"
    elif re.search(r"\bby priority\b", text, re.I):
        group_by = "priority"
    elif re.search(r"\bby status\b", text, re.I):
        group_by = "status"
    if group_by:
        filters["groupBy"] = group_by
    return filters


def _inquiry_filters_from_text(text: str) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if re.search(
        r"\bawaiting\b|\bunanswered\b|\bfirst repl\w*\b|\bnot (?:yet )?(?:been )?(?:answered"
        r"|replied|responded)\b|\bno (?:reply|response)\b|\bhaven'?t (?:been )?(?:answered|replied"
        r"|responded)\b|\bwaiting for (?:a|an|our|their first) (?:reply|response"
        r"|answer)\b|\bnew\b|\bwithout a reply\b|\bstill waiting\b",
        text,
        re.I,
    ):
        filters["status"] = "awaiting_first_reply"
    if re.search(r"\bwaiting on (?:the )?students?\b", text, re.I):
        filters["status"] = "waiting_on_student"
    if re.search(
        r"\bunassigned\b|\bnobody assigned\b|\bno one assigned\b|\bno assignee\b"
        r"|\bwithout an? (?:owner|assignee)\b",
        text,
        re.I,
    ):
        filters["ownership"] = "unassigned"
    if re.search(r"\bmine\b|\bmy\b|\bassigned to me\b", text, re.I):
        filters["ownership"] = "mine"
    if re.search(r"\burgent\b", text, re.I):
        filters["priority"] = "urgent"
    hours = re.search(
        r"\b(?:more than|over|older than|past)\s+(\d+)\s*(hours?|days?)\b", text, re.I
    )
    if hours:
        n = int(hours.group(1))
        filters["olderThanHours"] = n * (24 if hours.group(2).lower().startswith("day") else 1)
    elif re.search(
        r"\bover a day\b|\bmore than a day\b|\bpast (?:the )?24[- ]hour\b|\b24 ?h\b", text, re.I
    ):
        filters["olderThanHours"] = 24
    if re.search(
        r"\bmostly about\b|\bby topic\b|\bwhat (?:are|were) .* about\b|\btopics?\b|\bcategories\b",
        text,
        re.I,
    ):
        filters["groupBy"] = "topic"
    elif re.search(
        r"\bby assignee\b|\bwho (?:has|is handling)\b|\bper (?:person|staff)\b", text, re.I
    ):
        filters["groupBy"] = "assignee"
    if re.search(r"\boldest\b|\blongest\b|\bwaiting (?:the )?longest\b", text, re.I):
        filters["sort"] = "oldest"
    return filters


def _caseload_filters_from_text(text: str) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if re.search(
        r"\b(?:haven'?t|have not|hasn'?t|has not|not|never|still need to|yet to) (?:yet )?"
        r"(?:completed|done|had|finished|attended)\b[^.?]{0,20}\badvising\b"
        r"|\badvising (?:is )?(?:not |in)complete\b|\bwithout (?:an? )?advising\b"
        r"|\bno advising (?:appointment|meeting)\b",
        text,
        re.I,
    ):
        filters["advisingStatus"] = "not_completed"
    if re.search(
        r"\bno (?:upcoming |advising |future )?(?:appointment|meeting|booking)s?(?: booked"
        r"| scheduled)?\b"
        r"|\bnothing (?:booked|scheduled)\b|\bnot (?:yet )?booked\b"
        r"|\bwithout (?:an? )?(?:upcoming |advising )?(?:appointment|booking)\b"
        r"|\bno next appointment\b",
        text,
        re.I,
    ):
        filters["advisingStatus"] = "no_booking"
    if (
        re.search(r"\bcompleted (?:their )?advising\b|\badvising (?:is )?complete\b", text, re.I)
        and "advisingStatus" not in filters
    ):
        filters["advisingStatus"] = "completed"
    if re.search(r"\bmissed\b|\bno[- ]show", text, re.I) and "advisingStatus" not in filters:
        filters["advisingStatus"] = "missed"
    if re.search(
        r"\b(?:haven'?t|have not|hasn'?t|has not|not|no|without|unpaid|owe"
        r"|owing)\b[^.?]{0,20}\bdeposits?\b"
        r"|\bdeposits?\b[^.?]{0,16}\b(?:unpaid|outstanding|not paid)\b",
        text,
        re.I,
    ):
        filters["depositState"] = "unpaid"
    elif re.search(
        r"\bpaid (?:their |the )?deposit\b|\bdeposited\b|\bdeposit (?:is )?paid\b", text, re.I
    ):
        filters["depositState"] = "paid"
    if re.search(
        r"\boverdue (?:staff )?(?:work|items?|tasks?)\b|\bwith overdue\b|\bpast due\b", text, re.I
    ):
        filters["withOverdueWork"] = True
    elif re.search(
        r"\bopen (?:staff )?(?:work|items?|tasks?)\b|\bin the action cent(?:er|re)\b|\bwith open\b",
        text,
        re.I,
    ):
        filters["withOpenWork"] = True
    return filters


def _appointment_window_from_text(text: str) -> str | None:
    if re.search(r"\bclosed? out\b|\boutcome\b|\bnever closed\b", text, re.I):
        return "awaiting_outcome"
    if re.search(r"\btoday\b|\bthis morning\b|\bthis afternoon\b", text, re.I):
        return "today"
    if re.search(r"\btomorrow\b", text, re.I):
        return "tomorrow"
    if re.search(
        r"\btwo weeks\b|\b2 weeks\b|\bfortnight\b|\b14 days\b|\bnext couple of weeks\b", text, re.I
    ):
        return "two_weeks"
    if re.search(r"\bpast week\b|\blast week\b|\blast (?:seven|7) days\b", text, re.I):
        return "past_week"
    if re.search(
        r"\bthis week\b|\bnext (?:seven|7) days\b|\bnext week\b|\bcoming week\b|\bweek\b",
        text,
        re.I,
    ):
        return "week"
    return None


_POSSESSIVE_CASELOAD = re.compile(
    r"[A-Z][a-z]+(?:'s|\u2019s)\s+(?:advisees|students|caseload)\b"
    r"|\bof [A-Z][a-z]+(?: [A-Z][a-z]+)?(?:'s|\u2019s) (?:advisees|students)\b",
)


def _staff_facet(text: str, staff_count: int = 1) -> str:
    """Which facet of a staff member the turn asks about."""

    if staff_count >= 2 and _COMPARISON.search(text):
        return "comparison"
    if re.search(r"\bcompare\b|\bversus\b|\bvs\.?\b|\bside by side\b", text, re.I):
        return "comparison"
    if _POSSESSIVE_CASELOAD.search(text):
        return "caseload"
    if _FACET_AVAILABILITY.search(text):
        return "availability"
    if _FACET_CASELOAD.search(text):
        return "caseload"
    if _FACET_APPOINTMENTS.search(text):
        return "appointments"
    if _FACET_WORKLOAD.search(text):
        return "workload"
    return "profile"


_FACET_REQUEST_TYPE = {
    "comparison": "staff_comparison",
    "availability": "staff_availability",
    "caseload": "staff_caseload",
    "appointments": "staff_appointments",
    "workload": "staff_workload",
    "profile": "staff_profile",
}


_ABSENCE_LANGUAGE = re.compile(
    r"\baway\b|\bout (?:this|next|of the) (?:week|office)\b|\bon (?:vacation|leave|holiday)\b"
    r"|\babsent\b|\boff (?:this|next) week\b|\bout sick\b|\bout today\b",
    re.I,
)

_OPERATIONAL_ANAPHORA = re.compile(
    r"\bof (?:those|them|these)\b|\bthose\b|\bthese\b|\bthem\b|\bthat (?:number|count|group|set)\b",
    re.I,
)


def _inherit_operational_context(
    request: NormalizedStaffRequest, entities: EntityResolution | None
) -> StaffClassification | None:
    """ "How many of those are overdue?" — the cohort is the previous turn's.

    Deterministic: the prior user question is re-classified (no model, no
    directory reads) and its queue/inquiry filters are carried into this
    turn, with this turn's own qualifiers layered on top.
    """

    if not request.history or not _OPERATIONAL_ANAPHORA.search(request.text):
        return None
    if entities is not None and (entities.staff or entities.students):
        return None
    prior_question = next(
        (item["content"] for item in reversed(request.history) if item["role"] == "user"),
        None,
    )
    if not prior_question:
        return None
    prior_request = normalize_staff_request(prior_question)
    prior_entities = EntityResolution(
        text=prior_question,
        self_reference=bool(re.search(r"\b(?:my|me|i)\b", prior_question, re.I)),
    )
    prior = _classify_staff_scope(prior_request, prior_entities, prior_request.comparable_text)
    if prior is None or prior.request_type not in {
        "queue_aggregate",
        "my_work",
        "inquiry_aggregate",
        "work_queue",
    }:
        return None
    raw = request.text
    if prior.request_type == "inquiry_aggregate":
        filters = dict(prior.cohort_filter or {})
        filters.update(_inquiry_filters_from_text(raw))
        return StaffClassification(
            "inquiry_aggregate",
            0.9,
            source="operational_follow_up",
            reference="inquiries",
            cohort_filter=filters,
        )
    filters = dict(prior.cohort_filter or {})
    if prior.request_type == "my_work" or prior_entities.self_reference:
        filters.setdefault("ownership", "mine")
    filters.pop("groupBy", None)
    filters.update(_queue_filters_from_text(raw, None))
    return StaffClassification(
        "queue_aggregate",
        0.9,
        source="operational_follow_up",
        reference="queue",
        cohort_filter=filters,
    )


def _classify_staff_scope(
    request: NormalizedStaffRequest, entities: EntityResolution | None, text: str
) -> StaffClassification | None:
    if entities is None:
        return None
    inherited = _inherit_operational_context(request, entities)
    if inherited is not None:
        return inherited
    raw = request.text
    staff = list(entities.staff)
    department = entities.primary_department
    explicit_person = bool(
        staff or entities.students or any(a.reason != "not_found" for a in entities.ambiguities)
    )
    staff_ambiguous = any(
        a.reason in {"several_staff", "staff_and_student"} for a in entities.ambiguities
    )

    # --- a named colleague ---------------------------------------------------
    if staff:
        facet = _staff_facet(raw, len(staff))
        if (
            len(staff) >= 2
            and facet != "comparison"
            and re.search(r"\bor\b|\band\b|\bthan\b", raw, re.I)
        ):
            facet = "comparison"
        if facet == "comparison" and len(staff) == 1:
            facet = "profile"
        filters: dict[str, Any] = {}
        if facet == "workload":
            filters = _queue_filters_from_text(raw, None)
        elif facet == "caseload":
            filters = _caseload_filters_from_text(raw)
        elif facet == "appointments":
            window = _appointment_window_from_text(raw)
            filters = {"window": window} if window else {}
        return StaffClassification(
            _FACET_REQUEST_TYPE[facet],
            0.96,
            reference=facet,
            cohort_filter=filters or None,
        )
    if staff_ambiguous and not entities.students:
        # The resolver could not decide between people; the pipeline asks.
        facet = _staff_facet(raw)
        return StaffClassification(_FACET_REQUEST_TYPE[facet], 0.9, reference=facet)

    # --- grouped counts over the whole board ---------------------------------
    # "Which staff members have the most overdue work?" ranks assignees across
    # the tenant; it is a queue aggregate, not a question about my team.
    if (
        not explicit_person
        and not entities.self_team
        and not _ABSENCE_LANGUAGE.search(raw)
        and _QUEUE_NOUN.search(raw)
        and re.search(
            r"\b(?:the )?most\b|\bfewest\b|\bhighest\b|\blowest\b|\bby (?:assignee|owner|staff"
            r"|component|department|team)\b",
            raw,
            re.I,
        )
        and re.search(r"\bwhich\b|\bwho\b|\bby\b", raw, re.I)
        and not re.search(r"\bmy (?:team|direct reports|reports|people)\b", raw, re.I)
    ):
        filters = _queue_filters_from_text(raw, entities)
        if filters.get("groupBy"):
            return StaffClassification(
                "queue_aggregate", 0.95, reference="queue", cohort_filter=filters
            )

    # --- who is away / out / on leave (the directory) --------------------------
    if (
        not explicit_person
        and not department
        and re.search(
            r"\bwho\b|\bwhich (?:staff|people|colleagues?|members?)\b|\banyone\b"
            r"|\bstaff members?\b",
            raw,
            re.I,
        )
        and _ABSENCE_LANGUAGE.search(raw)
    ):
        return StaffClassification("staff_directory", 0.94, reference="absent")

    # --- the signed-in member and their team ---------------------------------
    if entities.self_team or (_TEAM_LANGUAGE.search(raw) and not explicit_person):
        if (
            department is not None
            and not entities.self_team
            and not re.search(r"\bteam\b|\badvis(?:er|or)s\b", raw, re.I)
        ):
            return StaffClassification("department_operations", 0.93, reference=department.name)
        return StaffClassification("team_overview", 0.95, reference=_team_focus(raw))

    if entities.self_reference and not explicit_person and not department:
        if re.search(
            r"\bwhat should i (?:work on|do|start with|tackle|focus on|prioriti[sz]e)\b"
            r"|\bwhere (?:do|should) i (?:start|begin)\b"
            r"|\bwhat(?:'s| is) (?:first|most urgent|top)\b",
            raw,
            re.I,
        ):
            return StaffClassification("my_work", 0.96, reference="priorities")
        if re.search(
            r"\bwho (?:do|should) i report to\b|\bmy (?:manager|boss|supervisor|title|role|job)\b"
            r"|\bwho reports to me\b|\bwhat(?:'s| is) my role\b"
            r"|\bhow many (?:people )?report to me\b|\bam i (?:a|the|an)\b",
            raw,
            re.I,
        ):
            return StaffClassification("my_profile", 0.96, reference="profile")
        facet = _staff_facet(raw)
        if facet in {"caseload", "availability", "appointments", "workload"}:
            filters = {}
            if facet == "workload":
                filters = _queue_filters_from_text(raw, None)
            elif facet == "caseload":
                filters = _caseload_filters_from_text(raw)
            elif facet == "appointments":
                window = _appointment_window_from_text(raw)
                filters = {"window": window} if window else {}
            return StaffClassification(
                "my_work", 0.95, reference=facet, cohort_filter=filters or None
            )
        if re.search(
            r"\bmy (?:plate|desk|day|queue|board|work|items|tasks|caseload|calendar|schedule"
            r"|advisees|students|appointments|inbox)\b|\bon my plate\b|\bwhat(?:'s"
            r"| is) on my\b|\bassigned to me\b|\bi have\b|\bdo i have\b|\bam i\b|\bmy (?:overdue"
            r"|open)\b",
            raw,
            re.I,
        ):
            return StaffClassification(
                "my_work", 0.95, reference=facet if facet != "profile" else "workload"
            )

    # --- compound: items … and inquiries … ---------------------------------------
    compound = re.match(
        r"^(?P<first>.*?\bhow many\b.*?)(?:,|;| and)\s*(?P<second>how many\b.*)$", raw, re.I
    )
    if compound and not explicit_person:
        first, second = compound.group("first"), compound.group("second")
        first_is_queue = bool(_QUEUE_NOUN.search(first)) and not _INQUIRY_NOUN.search(first)
        second_is_inquiry = bool(_INQUIRY_NOUN.search(second))
        first_is_inquiry = bool(_INQUIRY_NOUN.search(first))
        second_is_queue = bool(_QUEUE_NOUN.search(second)) and not second_is_inquiry
        if first_is_queue and second_is_inquiry:
            return StaffClassification(
                "queue_aggregate",
                0.95,
                reference="queue",
                additional_request_types=("inquiry_aggregate",),
                cohort_filter=_queue_filters_from_text(first, entities),
                additional_filters={"inquiry_aggregate": _inquiry_filters_from_text(second)},
            )
        if first_is_inquiry and second_is_queue:
            return StaffClassification(
                "inquiry_aggregate",
                0.95,
                reference="inquiries",
                additional_request_types=("queue_aggregate",),
                cohort_filter=_inquiry_filters_from_text(first),
                additional_filters={"queue_aggregate": _queue_filters_from_text(second, entities)},
            )

    # --- inquiries as an aggregate --------------------------------------------
    if _INQUIRY_NOUN.search(raw) and (
        _QUEUE_COUNT.search(raw)
        or re.search(r"\boldest\b|\blist\b|\bshow\b|\bwhich\b|\bany\b|\bwhat are\b", raw, re.I)
    ):
        filters = _inquiry_filters_from_text(raw)
        return StaffClassification(
            "inquiry_aggregate", 0.95, reference="inquiries", cohort_filter=filters
        )

    # --- a department -----------------------------------------------------------
    if len(entities.departments) >= 2 and not explicit_person and _QUEUE_NOUN.search(raw):
        filters = _queue_filters_from_text(raw, None)
        filters.pop("topic", None)
        filters["groupBy"] = "component"
        names = "|".join(d.name for d in entities.departments)
        return StaffClassification(
            "queue_aggregate", 0.95, reference=f"compare:{names}", cohort_filter=filters
        )
    if department is not None and not explicit_person:
        if re.search(
            r"\bhow many (?:staff|people|members|advis(?:er|or)s|counsel(?:l)?ors|evaluators"
            r"|employees)\b"
            r"|\bwho (?:works|is) in\b|\bwho in\b",
            raw,
            re.I,
        ):
            return StaffClassification("department_operations", 0.94, reference=department.name)
        if _QUEUE_COUNT.search(raw) and _QUEUE_NOUN.search(raw):
            filters = _queue_filters_from_text(raw, entities)
            return StaffClassification(
                "queue_aggregate", 0.95, reference=department.name, cohort_filter=filters
            )
        if re.search(
            r"\bunassigned\b|\boverdue\b|\burgent\b|\bshow me\b|\blist\b", raw, re.I
        ) and _QUEUE_NOUN.search(raw):
            filters = _queue_filters_from_text(raw, entities)
            return StaffClassification(
                "work_queue", 0.93, reference=f"component:{department.name}", cohort_filter=filters
            )
        if _DEPARTMENT_OPS.search(raw) or True:
            return StaffClassification("department_operations", 0.93, reference=department.name)

    # --- "which department should we look at first" ----------------------------
    if (
        not explicit_person
        and re.search(r"\bwhich (?:department|team|office|component|unit)\b", raw, re.I)
        and re.search(
            r"\bfirst\b|\bmost\b|\bworst\b|\battention\b|\blook at\b|\bfocus\b|\bstruggl\w*\b"
            r"|\bbehind\b|\bpriorit\w*\b|\bworr\w*\b",
            raw,
            re.I,
        )
    ):
        filters = _queue_filters_from_text(raw, None)
        filters.setdefault("dueWindow", "overdue")
        filters["groupBy"] = "component"
        return StaffClassification("queue_aggregate", 0.9, reference="queue", cohort_filter=filters)

    # --- the queue as an aggregate ---------------------------------------------
    if (
        not explicit_person
        and _QUEUE_NOUN.search(raw)
        and _QUEUE_COUNT.search(raw)
        and not re.search(r"\bstudents?\b(?!\s+(?:do they|does it|they cover))", raw, re.I)
    ):
        filters = _queue_filters_from_text(raw, entities)
        return StaffClassification(
            "queue_aggregate", 0.95, reference="queue", cohort_filter=filters
        )
    if not explicit_person and re.search(
        r"\bhow many (?:open )?(?:work )?items?\b|\bhow (?:many|much) .{0,30}\b(?:unassigned"
        r"|overdue|urgent|escalated|stale)\b",
        raw,
        re.I,
    ):
        filters = _queue_filters_from_text(raw, entities)
        return StaffClassification(
            "queue_aggregate", 0.95, reference="queue", cohort_filter=filters
        )
    return None


def _team_focus(text: str) -> str:
    if re.search(
        r"\bover (?:their |the )?(?:caseload )?cap\b|\bover[- ]?loaded\b|\bcapacity\b|\bspare\b"
        r"|\bcaseload\b|\blightest\b|\bleast loaded\b|\bcan take\b",
        text,
        re.I,
    ):
        return "capacity"
    if re.search(
        r"\bslots?\b|\bavailab\w*\b|\bsee (?:someone|a student|tomorrow)\b|\bbook\b|\btomorrow\b",
        text,
        re.I,
    ):
        return "availability"
    if re.search(r"\bclosed? out\b|\bappointments?\b|\boutcome\b", text, re.I):
        return "appointments"
    if re.search(
        r"\bon leave\b|\bdeparted\b|\bleft\b|\bout this week\b|\baway\b|\bvacation\b", text, re.I
    ):
        return "absence"
    if re.search(
        r"\battention\b|\bworried\b|\bbehind\b|\bflag\w*\b|\bconcern\w*\b|\bstruggl\w*\b",
        text,
        re.I,
    ):
        return "attention"
    return "overview"


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
