"""Deterministic request classification.

Ported from student-assistant-core `deterministic.ts`, priority-ordered the
same way: conversational openers first (so a greeting never drags a
university-data read behind it), then safety refusals, then housing, financial
aid, documents, holds, deadlines, registration, account, appointments, and the
checklist families. Returns None only when a model classifier may do better;
callers must then fall back to `general_question`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from audentra.integrations.assistant.normalize import NormalizedRequest

REQUEST_TYPES = (
    "greeting",
    "capability_overview",
    "general_help",
    "remaining_steps",
    "completed_steps",
    "next_action",
    "missing_documents",
    "document_status",
    "onboarding_status",
    "enrollment_state",
    "personal_information",
    "academic_standing",
    "support_requests",
    "deposit_status",
    "holds_and_blockers",
    "deadlines",
    "request_support",
    "aid_status",
    "aid_remaining_steps",
    "aid_incomplete_reason",
    "aid_missing_documents",
    "aid_verification_status",
    "aid_award_acceptance_status",
    "aid_summary",
    "aid_application_status",
    "aid_disbursement",
    "aid_coverage",
    "aid_next_action",
    "aid_support",
    "housing_status",
    "housing_options",
    "housing_remaining_steps",
    "housing_next_action",
    "housing_support",
    "housing_eligibility",
    "registration_status",
    "policy_lookup",
    "student_account",
    "appointments",
    "academic_plan",
    "campus_life",
    "messages_unread",
    "conversational_ack",
    "assistant_identity",
    "general_question",
    "unsupported_or_out_of_scope",
)


@dataclass(frozen=True)
class Classification:
    request_type: str
    confidence: float
    source: str = "deterministic"
    requirement_reference: str | None = None
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
# Social turns that carry no information need: gratitude, acknowledgement,
# closing. Must contain no question and stay short — "thanks, but why…" is a
# question, not an ack.
_GRATITUDE_OR_CLOSING = re.compile(
    r"^(?:thanks|thank you|thankyou|thx|ty|much appreciated|appreciate (?:it|that)"
    r"|perfect|great|awesome|amazing|cool|nice|got it|okay|ok|sounds good|will do"
    r"|understood|makes sense|no thanks|nothing else|that'?s (?:all|it|everything)"
    r"|bye|goodbye|see (?:you|ya)|talk (?:later|soon)|later|take care|good night)\b"
    r"[^?]{0,60}$",
    re.IGNORECASE,
)
_IDENTITY_QUESTION = re.compile(
    r"are you (?:a |an )?(?:actual(?:ly)? |real(?:ly)? |truly )?"
    r"(?:real person|real|human|person|bot|ai|robot)"
    r"|am i (?:talking|chatting|speaking) (?:to|with) a (?:human|person|bot|robot|machine)"
    r"|is this a (?:real person|human|bot)",
    re.IGNORECASE,
)

_OPEN_ENDED_HELP = re.compile(
    r"(?:^help[!. ]*$|i (?:don'?t|do not) know where to (?:start|begin)"
    r"|where (?:do|should) i (?:start|begin)"
    r"|i'?m (?:lost|overwhelmed|confused"
    r"|not sure (?:what to do|where to (?:start|begin)))"
    r"|help me get started|what should i be doing"
    r"|how (?:bad|screwed|behind) am i|how bad is (?:my situation|it))",
    re.IGNORECASE,
)

# A question about a rule for a *category* of students, or about what the
# institution does in a hypothetical, is institutional policy — not a read of
# this student's record. Deliberately narrow: "can I apply for housing?" is a
# question about this student and stays in the housing branch.
_POLICY_QUESTION = re.compile(
    r"\b(?:can|are|do|does|must|is|will|should)\s+"
    r"(?:freshmen|freshman|first[- ]?years?(?:\s+students)?|sophomores|transfer students|"
    r"international students|all students|new students|every student|students)\b"
    r"|\bwhat(?:'s| is) the (?:polic\w+|rules?)\b|\bpolic(?:y|ies) (?:on|for|about)\b"
    r"|\brules? (?:about|for|on)\b"
    r"|\bwhat happens (?:if|when)\b"
    r"|\bis it (?:allowed|permitted|possible|mandatory|required)\b"
    r"|\bguaranteed?\b"
    r"|\b(?:am i|are we) (?:allowed|permitted|required) to\b",
    re.IGNORECASE,
)

# Satisfactory academic progress and earned credits: the Financials page's SAP
# card and the Classrooms credit counter. Distinct from the course plan.
_ACADEMIC_STANDING = re.compile(
    r"\bg\.?p\.?a\.?\b|\bgrade point average\b"
    r"|\bsatisfactory academic progress\b|\bacademic (?:standing|probation|progress)\b"
    r"|\bsap\b(?!\w)"
    # Earned credits only. "How many credits am I signed up for / taking"
    # is a course-plan question and must stay with the plan read.
    r"|\b(?:how many|what) credits\b(?![^?]{0,32}\b(?:signed up|taking|enrolled|registered)\b)"
    r"|\bcredits? (?:do i have|earned|completed|so far)\b"
    r"|\bcompletion rate\b|\bam i in good standing\b",
    re.IGNORECASE,
)

# Facts the student themselves supplied during onboarding. Kept narrow so a
# document question about residency proof does not land here.
_PERSONAL_INFORMATION = re.compile(
    r"\bemergency contacts?\b|\bnext of kin\b"
    r"|\b(?:what|which|whose)\b[^?]{0,32}\baddress\b[^?]{0,24}\b(?:on file|do you have|"
    r"did i (?:give|put|enter)|is on my record)\b"
    r"|\b(?:mailing|home|permanent) address\b"
    r"|\bcitizenship (?:status)?\b"
    r"|\b(?:what|which)\b[^?]{0,24}\bresidency status\b"
    r"|\bwhat did i (?:put|enter|answer|select|choose|say)\b"
    r"|\b(?:my )?pronouns\b"
    r"|\b(?:phone number|contact (?:info|details|preference))\b[^?]{0,24}"
    r"\b(?:on file|do you have|is (?:on )?(?:my )?record)\b"
    r"|\bwho (?:can|is allowed to) (?:see|access|talk about)\b[^?]{0,24}\b(?:my )?record\b"
    r"|\bfamily (?:permissions?|access)\b"
    r"|\bdid i sign\b|\bmy signature\b",
    re.IGNORECASE,
)

# The student's own support conversations, not the routing question.
_SUPPORT_REQUESTS = re.compile(
    r"\b(?:my|the) (?:help|support) (?:request|ticket|case|question)s?\b"
    r"|\bdid (?:anyone|anybody|someone|they) (?:answer|reply|respond|get back)\b"
    r"|\bany (?:reply|response|answer) (?:to|on) my\b"
    r"|\bheard back\b|\bfollow(?:ed)? up on my\b"
    r"|\bstatus of my (?:question|request|ticket|inquiry)\b",
    re.IGNORECASE,
)

# The deposit as a payment record: paid or not, when, how much.
_DEPOSIT_STATUS = re.compile(
    r"\bdeposit\b[^?]{0,40}\b(?:paid|posted|received|processed|went through|cleared|"
    r"show(?:ing|n|s)? up|on file|recorded|status)\b"
    r"|\b(?:paid|posted|processed|received|cleared)\b[^?]{0,24}\b(?:my |the )?deposit\b"
    r"|\bdid (?:my|the) deposit\b|\bis (?:my|the) deposit\b|\bhas (?:my|the) deposit\b"
    r"|\bdeposit (?:confirmation|receipt)\b",
    re.IGNORECASE,
)

# Where the student stands: admission decision, program placement, progress.
_ENROLLMENT_STATE = re.compile(
    r"\benrollment status\b|\badmission status\b|\bapplication status\b"
    r"|\bam i (?:admitted|accepted|enrolled|officially (?:in|enrolled))\b"
    r"|\bwhat (?:program|major|degree) am i\b|\bwhich (?:program|campus|term)\b"
    r"|\bwhat (?:term|semester) (?:am i|do i) (?:start|starting|begin)\b"
    r"|\bwhen do i start\b|\bmy (?:starting )?term\b|\bwhat campus\b"
    r"|\bclass of\b|\bclass year\b"
    r"|\bhow far along\b|\bwhere do i stand\b|\bam i all set\b"
    r"|\bmy offer\b|\boffer status\b",
    re.IGNORECASE,
)

# "What's changed?" / "what does that mean?" are consequence questions: they
# span whatever the named event unblocked, so a single-record read under-answers.
_CONSEQUENCE_QUESTION = re.compile(
    r"\bwhat(?:'s| is|s| has| does)?\b[^?]{0,24}\b(?:changed|change|mean|next|now|unlock)"
    r"|\bwhat does that mean\b|\bwhat happens now\b|\bwhere does that leave me\b",
    re.IGNORECASE,
)

# A pronoun with no antecedent anywhere: no history, no noun in the question.
_UNANCHORED_REFERENCE = re.compile(
    r"^(?:so |ok |okay |and )?(?:did|has|is|was|does)\s+(?:it|that|this|they)\s+"
    r"(?:work|go through|come through|process|post|arrive|land|count|register|"
    r"show up|get (?:there|through)|happen|complete)\w*\s*(?:yet)?\s*[?!.]*$",
    re.IGNORECASE,
)

_AID_ENTITY = re.compile(
    r"\bfinancial[- ]?aid\b|\bfafsa\b|\baid\b|\bgrants?\b|\bscholarships?\b|\bloans?\b"
    r"|\bwork[- ]study\b|\bverification worksheet\b|\bdisburse|\baward",
    re.IGNORECASE,
)
_HOUSING_ENTITY = re.compile(
    r"\bhousing\b|\bdorm\w*\b|\bresidence\b|\broommate\b|\broom\b|\bon[- ]campus living\b",
    re.IGNORECASE,
)


# Domain vocabulary for multi-domain detection, in answer-priority order.
_DOMAIN_HINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("deadlines", re.compile(r"deadline|overdue|due date|by when", re.IGNORECASE)),
    ("documents", re.compile(r"document|paperwork|transcript|immuni[sz]|identity", re.IGNORECASE)),
    ("aid", _AID_ENTITY),
    (
        "account",
        re.compile(
            r"\bbalance\b|\bdeposit\b|\bowe\b|\bpayments?\b|\bmoney\b|\bbill\b", re.IGNORECASE
        ),
    ),
    ("housing", _HOUSING_ENTITY),
    ("registration", re.compile(r"\bregist", re.IGNORECASE)),
    ("academics", re.compile(r"\bcourses?\b|academic plan|prerequisite", re.IGNORECASE)),
    ("campus", re.compile(r"\bclubs?\b|campus life|\bevents?\b", re.IGNORECASE)),
    (
        "checklist",
        re.compile(r"checklist|enrollment steps|onboarding|\benrollment\b", re.IGNORECASE),
    ),
)

_DOMAIN_PRIMARY_TYPE: dict[str, str] = {
    "deadlines": "deadlines",
    "documents": "document_status",
    "aid": "aid_status",
    "account": "student_account",
    "housing": "housing_status",
    "registration": "registration_status",
    "academics": "academic_plan",
    "campus": "campus_life",
    "checklist": "onboarding_status",
}

# Aggregation language that marks a question as spanning its named domains.
_MULTI_DOMAIN_OPERATOR = re.compile(
    r"\bsummar|overview|everything|across|full picture|big picture|whole picture"
    r"|all of (?:it|my|them)|\bboth\b|as well as|including|situation\b"
    r"|where do i stand|status of each",
    re.IGNORECASE,
)


def classify(request: NormalizedRequest) -> Classification | None:
    text = request.comparable_text

    if _IDENTITY_QUESTION.search(text):
        return Classification("assistant_identity", 1)
    if _GRATITUDE_OR_CLOSING.search(text.strip()):
        return Classification("conversational_ack", 1)

    if not request.is_follow_up:
        if _GREETING_ONLY.search(text):
            return Classification("greeting", 1)
        if _CAPABILITY_QUESTION.search(text):
            return Classification("capability_overview", 0.99)
        if _OPEN_ENDED_HELP.search(text):
            return Classification("general_help", 0.98)

    # With conversation behind it the pronoun has an antecedent and the
    # follow-up path resolves it; with nothing behind it, any yes/no is a
    # guess about which action the student means.
    if not request.history and _UNANCHORED_REFERENCE.search(text.strip()):
        return Classification("general_question", 0.9)

    if request.requests_other_person_contact:
        return Classification(
            "unsupported_or_out_of_scope", 1, requirement_reference="other_person_contact_details"
        )
    aid_entity = bool(_AID_ENTITY.search(text))
    if request.is_mutation_request:
        if _HOUSING_ENTITY.search(text):
            write_reference = "housing_write_unavailable"
        elif aid_entity:
            write_reference = "financial_aid_write_unavailable"
        elif re.search(r"document|transcript|immuni[sz]|identity|upload", text):
            write_reference = "document_write_unavailable"
        elif re.search(r"\bpay|deposit|tuition|bill|charge", text):
            write_reference = "payment_write_unavailable"
        elif re.search(r"appointment|advis|book|schedule|reschedule|meeting", text):
            write_reference = "appointment_write_unavailable"
        else:
            write_reference = "write_unavailable"
        return Classification(
            "unsupported_or_out_of_scope", 1, requirement_reference=write_reference
        )
    if request.contains_sensitive_financial_data:
        return Classification(
            "unsupported_or_out_of_scope", 1, requirement_reference="sensitive_financial_data"
        )

    # Institutional policy questions are settled before the domain branches so
    # "can freshmen live off campus?" is answered as policy, not as this
    # student's own housing record. A "what happens if" hypothetical about the
    # student's *own* pending item (a worksheet, a document) stays with its
    # domain branch, which answers the consequence from the record.
    if (
        _POLICY_QUESTION.search(text)
        and not re.search(
            r"\b(?:my|me|i)\b.{0,24}\b(?:status|balance|record|checklist|left|remaining)\b",
            text,
        )
        and not re.search(r"\b[a-z]{2,4} ?\d{3}\b|\bdata structures\b|\bprerequisites?\b", text)
        and not (
            re.search(r"what happens (?:if|when)", text)
            and (aid_entity or re.search(r"document|transcript|immuni[sz]|deposit|worksheet", text))
        )
    ):
        return Classification("policy_lookup", 0.9)

    # Institutional calendar dates have no canonical source yet; a specific
    # date here would be invented, so these route to the honest institutional
    # answer instead of a deadline read that tempts a wrong connection.
    if re.search(
        r"\bwhen (?:does|do|will) (?:the )?(?:term|semester|classes)\b"
        r"|\bwhen is (?:orientation|move[- ]?in)\b",
        text,
    ):
        return Classification("policy_lookup", 0.85)

    # An enumerated multi-domain ask ("summarize my documents, money, and
    # housing") must not collapse to the first matching single intent — the
    # answer owes the student every named domain. Detected deterministically:
    # two-plus domains named plus an aggregation operator.
    domain_hits = [name for name, pattern in _DOMAIN_HINTS if pattern.search(text)]
    # Money+aid and money+housing pairs belong to their expert branches, whose
    # selection rules already cross into the account read where needed.
    if set(domain_hits) in ({"aid", "account"}, {"housing", "account"}):
        domain_hits = []
    if len(domain_hits) >= 2 and _MULTI_DOMAIN_OPERATOR.search(text):
        if "deadlines" in domain_hits:
            primary = "deadlines"
            extras = [name for name in domain_hits if name != "deadlines"][:2]
        else:
            primary = domain_hits[0]
            extras = domain_hits[1:3]
        return Classification(
            _DOMAIN_PRIMARY_TYPE[primary],
            0.9,
            additional_request_types=tuple(_DOMAIN_PRIMARY_TYPE[name] for name in extras),
        )

    housing = bool(_HOUSING_ENTITY.search(text))
    if housing and aid_entity:
        # Housing and financial aid in one question is a genuine multi-domain
        # request. Returning None hands it to the model planner, which can
        # select reads across both domains; without a model the safe fallback
        # reads the checklist, holds, and deadlines and the dependency round
        # fetches whatever an open gate then names.
        return None
    if housing and not aid_entity:
        if re.search(
            r"(?:what|which|show|list).{0,28}(?:housing|residence|dorm).{0,20}(?:option|choice)"
            r"|(?:housing|residence|dorm).{0,20}(?:option|choice|available)",
            text,
        ):
            return Classification("housing_options", 1)
        if re.search(
            r"(?:who|where).{0,32}(?:contact|help)|contact.{0,24}housing"
            r"|housing.{0,24}(?:support|office|help)|accommodation|accessible housing",
            text,
        ):
            return Classification("housing_support", 1)
        if re.search(r"(?:what|which).{0,20}(?:next|first)|next action|do next|priority", text):
            return Classification("housing_next_action", 1)
        if re.search(r"(?:left|remain|remaining|outstanding|still (?:have|need)|steps?)", text):
            return Classification("housing_remaining_steps", 1)
        if re.search(
            r"(?:can|could) i (?:apply|register|sign up|pick|choose|select)"
            r"|am i able|able to (?:pick|choose|select|apply)"
            r"|why can'?t i"
            r"|why isn'?t housing|housing.{0,16}isn'?t (?:open|available)"
            r"|(?:housing|section|it).{0,24}(?:blocked|locked|closed|stuck|"
            r"not letting|won'?t let|letting me)"
            r"|why (?:is|does).{0,24}housing"
            r"|stopping|prevent|eligib",
            text,
        ):
            return Classification(
                "housing_eligibility", 1, additional_request_types=("holds_and_blockers",)
            )
        if re.search(r"\bregist", text):
            return Classification(
                "housing_eligibility", 1, additional_request_types=("registration_status",)
            )
        return Classification("housing_status", 1)

    if aid_entity:
        # "Can I register even though my aid isn't complete?" is a question
        # about registration whose answer needs the registration gates, not
        # just the aid file. Reading only aid invited the composer to invent
        # a registration blocker the gate list contradicts.
        if re.search(
            r"\bregist(?:er|ering|ration)\b"
            r"|cleared for class|take classes|start(?:ing)? classes",
            text,
        ):
            return Classification(
                "registration_status", 1, additional_request_types=("aid_status",)
            )
        if re.search(
            r"(?:who|where|how).{0,32}(?:contact|help|talk to)"
            r"|(?:aid|financial aid).{0,16}(?:office|support|advisor|counselor)"
            r"|(?:speak|talk|meet).{0,24}(?:aid|financial)",
            text,
        ):
            return Classification("aid_support", 1)
        if re.search(
            r"disburse|paid out|pay out"
            r"|when.{0,32}(?:money|funds|aid).{0,24}(?:arrive|come|available|applied|"
            r"show up|land|hit)"
            r"|(?:money|funds|aid).{0,32}(?:hasn'?t|has not|not).{0,24}"
            r"(?:arrive|come|been (?:paid|applied|disbursed))|refund check",
            text,
        ):
            return Classification("aid_disbursement", 1)
        if re.search(
            r"(?:aid|award)s?\b[^.?]{0,32}(?:more than|exceeds?|higher than|greater than)"
            r"[^.?]{0,24}(?:cost|charge|bill|tuition)"
            r"|(?:more than|exceeds?)[^.?]{0,16}my costs?"
            r"|(?:cover|covers|enough to (?:cover|pay))\b"
            r".{0,32}(?:tuition|cost|bill|charges|balance)"
            r"|(?:tuition|cost of attendance|bill|balance).{0,32}(?:covered|after (?:aid|my aid))"
            r"|how much.{0,24}(?:will i|do i|would i).{0,16}(?:still )?(?:owe|pay)"
            r"|what.{0,16}(?:will|do) i (?:still )?owe|remaining balance|left to pay"
            r"|out of pocket",
            text,
        ):
            return Classification("aid_coverage", 1)
        # Amount-bearing questions need amount evidence. "How much is my Pell
        # Grant?" answered from a read without amounts produced fabricated
        # figures; the summary read carries every award with its dollars.
        if re.search(
            r"how much (?:is|was|will)\b.{0,48}\b(?:grant|loan|scholarship|work[- ]study|aid|award)"
            r"|\b(?:amount|value|size) of my\b.{0,32}\b(?:grant|loan|scholarship|aid|award)"
            r"|\bmy\b.{0,24}\b(?:grant|loan|scholarship|award)\b.{0,16}(?:amount|worth|how much)",
            text,
        ):
            return Classification("aid_summary", 1)
        if re.search(
            r"\bfafsa\b|(?:application|isir).{0,32}(?:received|status|processed|submitted)"
            r"|(?:received|got).{0,24}my.{0,16}(?:fafsa|application)|selected for verification",
            text,
        ):
            if re.search(r"verification", text):
                return Classification("aid_verification_status", 1)
            return Classification("aid_application_status", 1)
        if re.search(r"verification|worksheet", text):
            return Classification("aid_verification_status", 1)
        if re.search(
            r"(?:accept|accepted|declin).{0,24}(?:award|grant|loan|aid)"
            r"|(?:award|grant|loan|aid|scholarship)s?\b.{0,32}(?:have i )?accepted"
            r"|have i accepted",
            text,
        ):
            return Classification("aid_award_acceptance_status", 1)
        if re.search(
            r"(?:why|reason).{0,32}(?:incomplete|not complete|pending|held up|hold|stuck)"
            r"|aid.{0,24}incomplete|incomplete.{0,24}aid",
            text,
        ):
            return Classification("aid_incomplete_reason", 1)
        if re.search(
            r"(?:missing|outstanding|still need|need to (?:send|submit|upload)|requirement)",
            text,
        ):
            return Classification("aid_missing_documents", 1)
        if not re.search(
            r"\b(?:steps?|left|remaining|outstanding|still need|to do)\b", text
        ) and re.search(
            r"(?:what|which|how much).{0,24}(?:aid|award|grant|scholarship|loan|money|funding)"
            r".{0,24}(?:do i have|am i (?:receiving|getting)|did i get"
            r"|have i (?:got|been (?:offered|awarded)))"
            r"|(?:summary|overview|breakdown).{0,24}(?:aid|award)"
            r"|(?:my|all).{0,12}(?:financial[ -]?aid|awards?)\b\s*[?.]?$|total.{0,16}aid",
            text,
        ):
            return Classification("aid_summary", 1)
        if re.search(r"(?:left|remain|remaining|outstanding|still (?:have|need)|steps?)", text):
            return Classification("aid_remaining_steps", 1)
        if re.search(r"(?:what|which).{0,20}(?:next|first)|next action|do next", text):
            return Classification("aid_next_action", 1)
        if re.search(r"deadline|due", text):
            return Classification("deadlines", 0.95)
        return Classification("aid_status", 0.95)

    # Academic standing is its own record (satisfactory academic progress and
    # earned credits), not the course plan. "What's my GPA?" answered from the
    # plan read has no GPA in evidence and invites the composer to invent one.
    if _ACADEMIC_STANDING.search(text):
        return Classification("academic_standing", 1)
    if re.search(r"\bcredits?\b", text) and re.search(
        r"\b(?:signed up|taking|enrolled|registered)\b", text
    ):
        return Classification("academic_plan", 1)

    # What the student told the university about themselves. Narrow on
    # purpose: "residency documents" is a document question, while "what
    # residency status did I put down" is a read of the onboarding answers.
    if _PERSONAL_INFORMATION.search(text):
        return Classification("personal_information", 1)

    # A question about a specific course or prerequisite needs the academic
    # plan, whatever else it mentions — registration gates cannot say whether
    # CS 201's prerequisite is met.
    if re.search(r"\b[a-z]{2,4} ?\d{3}\b|\bdata structures\b|\bprerequisites?\b", text) or (
        re.search(r"\bcourses?\b", text)
        and re.search(r"\b(?:take|taking|swap|switch|drop|add|start|first term|which|what)\b", text)
    ):
        if re.search(r"\bregist(?:er|ering|ration)\b", text):
            return Classification(
                "registration_status", 1, additional_request_types=("academic_plan",)
            )
        return Classification("academic_plan", 1)

    if re.search(r"\bhold(s)?\b|\bblock(?:ed|ing|er)?s?\b|\bstopping me\b|\bprevent", text):
        if re.search(r"\bregist", text):
            return Classification(
                "registration_status", 1, additional_request_types=("holds_and_blockers",)
            )
        return Classification("holds_and_blockers", 1)
    if re.search(r"\bregist(?:er|ering|ration)\b", text):
        return Classification("registration_status", 1)

    if re.search(r"document|upload|transcript|immuni[sz]|ferpa|identity|residency", text):
        # Remember which document the student named so composition can lead
        # with that document's state instead of a whole-checklist summary.
        document_reference = None
        for keyword, reference in (
            ("transcript", "transcript"),
            ("immuni", "immunization"),
            ("identity", "identity"),
            ("residency", "residency"),
        ):
            if keyword in text:
                document_reference = reference
                break
        if re.search(
            r"(?:missing|still need|need to (?:send|submit|upload)"
            r"|haven'?t (?:sent|submitted|uploaded))",
            text,
        ):
            return Classification("missing_documents", 1, requirement_reference=document_reference)
        if re.search(
            r"(?:status|reviewed?|under review|accepted|received|got|uploaded|submitted|have i)",
            text,
        ):
            return Classification("document_status", 1, requirement_reference=document_reference)
        return Classification("document_status", 0.9, requirement_reference=document_reference)

    if re.search(
        r"\bacademic plan\b|\bdegree plan\b|\bcourse plan\b|\bcurriculum\b|\bcatalog\b"
        r"|\bprerequisites?\b|\b(?:my|which|what) (?:classes|courses)\b|\bclass schedule\b"
        r"|\bexemptions?\b|\bmajor requirements\b",
        text,
    ):
        return Classification("academic_plan", 1)

    if re.search(
        r"\bclubs?\b|\bcampus (?:life|events?|activit\w+)\b|\bstudent organi[sz]ations?\b"
        r"|\bintramurals?\b|\bevents? (?:on campus|this (?:week|month|semester))"
        r"|\bactivities (?:can i|to) join\b|\bwhat.{0,24}(?:events|activities)\b"
        r"|\bwhat (?:should|can|could) i join\b|\bjoin\b.{0,24}(?:on campus|at (?:the )?"
        r"universit|school)"
        r"|\bget(?:ting)? involved\b|\bmake friends\b|\bmaking friends\b|\bmeet people\b"
        r"|\banything fun\b|\bfun (?:things|stuff|events)\b"
        r"|\bthings (?:happening|to do) (?:on|around) campus\b|\baudition\b",
        text,
    ):
        return Classification("campus_life", 1)

    if re.search(r"\bunread\b|\bmessages?\b|\binbox\b|\bnotifications?\b", text) and not re.search(
        r"\bsend\b|\bwrite\b|\breply\b", text
    ):
        return Classification("messages_unread", 1)

    if re.search(r"deadline|due date|\bdue\b|overdue|by when|how long do i have", text):
        return Classification("deadlines", 1)

    # "Is my deposit paid?" is a payment-record question. The generic account
    # branch below answers with a balance, which is not what was asked and
    # leaves the posted-payment fact out of evidence entirely.
    if _DEPOSIT_STATUS.search(text):
        if not _CONSEQUENCE_QUESTION.search(text):
            return Classification("deposit_status", 1)
        # "Now that the deposit posted, what changed?" is about what the
        # payment released, not about the payment. Housing is the step this
        # platform gates on earlier checklist items, so its eligibility is
        # what actually moved; the checklist and holds carry the rest.
        return Classification(
            "next_action",
            1,
            # The holds read already carries the deposit projection, so the
            # account read would add cost without adding a fact.
            additional_request_types=("housing_eligibility",),
        )

    if re.search(
        r"balance|owe|owing|bill|billing|charges|tuition cost|payment plan|installment"
        r"|deposit|\bpay(?:ing|ment)?\b|\bmoney\b",
        text,
    ):
        return Classification("student_account", 0.95)

    if re.search(
        r"appointment|advisor|advising|meet with|talk to (?:someone|a person|a human)", text
    ):
        return Classification("appointments", 0.95)

    # The student's own open conversations, before the generic "how do I get
    # help" route: "did anyone answer me?" is a read, not a routing question.
    if _SUPPORT_REQUESTS.search(text):
        return Classification("support_requests", 1)

    if re.search(
        r"(?:who|where|how).{0,24}(?:contact|help|support)|support office|help desk", text
    ):
        return Classification("request_support", 0.9)

    if re.search(
        r"(?:what|which).{0,24}(?:have i|did i).{0,16}(?:complete|done|finish)"
        r"|completed steps",
        text,
    ):
        return Classification("completed_steps", 1)
    if re.search(
        r"(?:what|which).{0,20}(?:next|first)\b|next (?:step|action)|do next|what now"
        r"|what should i do",
        text,
    ):
        return Classification("next_action", 1)
    if re.search(
        r"(?:left|remain|remaining|outstanding|still (?:have|need)|to[- ]do|checklist|steps)",
        text,
    ):
        return Classification("remaining_steps", 1)
    if _ENROLLMENT_STATE.search(text):
        return Classification("enrollment_state", 1)
    if re.search(r"onboarding|where am i in", text):
        return Classification("onboarding_status", 0.95)

    return None
