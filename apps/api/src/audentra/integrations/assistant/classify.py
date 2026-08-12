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
    "housing_status",
    "housing_options",
    "housing_remaining_steps",
    "housing_next_action",
    "housing_support",
    "registration_status",
    "student_account",
    "appointments",
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
    r"|what are you(?: able to do)?|who are you|what is this|help)\s*[?!.]*$",
    re.IGNORECASE,
)
_OPEN_ENDED_HELP = re.compile(
    r"(?:i (?:don'?t|do not) know where to (?:start|begin)|where (?:do|should) i (?:start|begin)"
    r"|i'?m (?:lost|overwhelmed|confused|not sure what to do)|help me get started"
    r"|what should i be doing)",
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


def classify(request: NormalizedRequest) -> Classification | None:
    text = request.comparable_text

    if not request.is_follow_up:
        if _GREETING_ONLY.search(text):
            return Classification("greeting", 1)
        if _CAPABILITY_QUESTION.search(text):
            return Classification("capability_overview", 0.99)
        if _OPEN_ENDED_HELP.search(text):
            return Classification("general_help", 0.98)

    if request.requests_other_person_contact:
        return Classification(
            "unsupported_or_out_of_scope", 1, requirement_reference="other_person_contact_details"
        )
    aid_entity = bool(_AID_ENTITY.search(text))
    if request.is_mutation_request:
        return Classification(
            "unsupported_or_out_of_scope",
            1,
            requirement_reference=(
                "housing_write_unavailable"
                if _HOUSING_ENTITY.search(text)
                else "financial_aid_write_unavailable"
                if aid_entity
                else "write_unavailable"
            ),
        )
    if request.contains_sensitive_financial_data:
        return Classification(
            "unsupported_or_out_of_scope", 1, requirement_reference="sensitive_financial_data"
        )

    housing = bool(_HOUSING_ENTITY.search(text))
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
        if re.search(r"(?:can|could) i (?:apply|sign up)|why can'?t i apply|eligib", text):
            return Classification(
                "housing_status", 1, additional_request_types=("holds_and_blockers",)
            )
        return Classification("housing_status", 1)

    if aid_entity:
        if re.search(
            r"disburse|paid out|pay out"
            r"|when.{0,32}(?:money|funds|aid).{0,24}(?:arrive|come|available|applied)"
            r"|(?:money|funds|aid).{0,32}(?:hasn'?t|has not|not).{0,24}"
            r"(?:arrive|come|been (?:paid|applied|disbursed))|refund check",
            text,
        ):
            return Classification("aid_disbursement", 1)
        if re.search(
            r"(?:cover|covers|enough to (?:cover|pay))\b"
            r".{0,32}(?:tuition|cost|bill|charges|balance)"
            r"|(?:tuition|cost of attendance|bill|balance).{0,32}(?:covered|after (?:aid|my aid))"
            r"|how much.{0,24}(?:will i|do i|would i).{0,16}(?:still )?(?:owe|pay)"
            r"|what.{0,16}(?:will|do) i (?:still )?owe|remaining balance|left to pay"
            r"|out of pocket",
            text,
        ):
            return Classification("aid_coverage", 1)
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

    if re.search(r"\bhold(s)?\b|\bblock(?:ed|ing|er)?s?\b|\bstopping me\b|\bprevent", text):
        if re.search(r"\bregist", text):
            return Classification(
                "registration_status", 1, additional_request_types=("holds_and_blockers",)
            )
        return Classification("holds_and_blockers", 1)
    if re.search(r"\bregist(?:er|ering|ration)\b", text):
        return Classification("registration_status", 1)

    if re.search(r"document|upload|transcript|immuni[sz]|ferpa|identity|residency", text):
        if re.search(
            r"(?:missing|still need|need to (?:send|submit|upload)"
            r"|haven'?t (?:sent|submitted|uploaded))",
            text,
        ):
            return Classification("missing_documents", 1)
        if re.search(
            r"(?:status|reviewed?|under review|accepted|received|got|uploaded|submitted|have i)",
            text,
        ):
            return Classification("document_status", 1)
        return Classification("document_status", 0.9)

    if re.search(r"deadline|due date|\bdue\b|overdue|by when|how long do i have", text):
        return Classification("deadlines", 1)

    if re.search(
        r"balance|owe|owing|bill|billing|charges|tuition cost|payment plan|installment|deposit",
        text,
    ):
        return Classification("student_account", 0.95)

    if re.search(
        r"appointment|advisor|advising|meet with|talk to (?:someone|a person|a human)", text
    ):
        return Classification("appointments", 0.95)

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
    if re.search(r"onboarding|enrollment status|where am i in", text):
        return Classification("onboarding_status", 0.95)

    return None
