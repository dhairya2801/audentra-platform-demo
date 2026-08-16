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
from dataclasses import dataclass, field

from audentra.integrations.staff_assistant.normalize import NormalizedStaffRequest

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
    "attention_ranking",
    "recommendation",
    "work_queue",
    "work_item_detail",
    "inquiries",
    "playbook_lookup",
    "action_rules",
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
    r"|\bprioriti[sz]e (?:my )?(?:students|outreach)\b|\bwho(?:'s| is) (?:slipping|stuck)\b"
    r"|\brank (?:the )?students\b|\bwhich students?\b.{0,32}\b(?:first|most|urgent)\b",
    re.IGNORECASE,
)
# Metrics where ranking intent should fall through to the honest attention
# queue instead of a bare refusal.
_RANKABLE_METRICS = frozenset(
    {"melt_risk", "enrollment_probability", "recovery_likelihood", "risk_score", "student_value"}
)

_DOCUMENT_ENTITY = re.compile(
    r"\btranscript\b|\bimmuni[sz]\w*\b|\bresidency\b|\bidentity doc\w*\b|\bdocuments?\b"
    r"|\bupload\w*\b|\bhealth record\b",
    re.IGNORECASE,
)
_FINANCIAL_ENTITY = re.compile(
    r"\bfinancial[- ]?aid\b|\bfafsa\b|\baid\b|\bdeposit\b|\bbalance\b|\baward\b|\bpayment\b"
    r"|\bsap\b|\bverification\b|\bscholarship\b|\bloan\b",
    re.IGNORECASE,
)
_HOUSING_ENTITY = re.compile(r"\bhousing\b|\bdorm\w*\b|\bresidence\b", re.IGNORECASE)


def classify_staff_request(request: NormalizedStaffRequest) -> StaffClassification | None:
    text = request.comparable_text
    raw = request.text

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

    if re.search(
        r"\bwhat should i (?:work on|do) (?:first|today|next)\b(?!\s*(?:for|about|with)\b)"
        r"|\bmy (?:queue|tasks|work items?)\b"
        r"|\bwhat(?:'s| is) (?:on|in) (?:my|the) (?:queue|board|plate)\b"
        r"|\b(?:open|urgent|overdue) (?:tasks|work items?)\b|\btask board\b"
        r"|\bwork queue\b|\bunassigned (?:tasks|items|work)\b",
        text,
    ):
        return StaffClassification("work_queue", 1)

    if request.work_item_key is not None or re.search(
        r"\bwork item\b|\bwhat happened (?:on|with) (?:the )?(?:task|item|case)\b", text
    ):
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
    # student — the pipeline resolves the referent separately.
    has_referent_language = (
        request.candidate_student_name is not None
        or request.candidate_student_id is not None
        or request.uses_pronoun_referent
        or request.is_follow_up
    )

    if re.search(
        r"\bwhat should (?:i|we) do (?:next |first |today )?(?:for|about|with)\b"
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
        r"|\bheard (?:back|from)\b",
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
        r"|\bstate\b|\bwhere (?:is|are)\b|\bsubmitted\b|\bmissing\b",
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
