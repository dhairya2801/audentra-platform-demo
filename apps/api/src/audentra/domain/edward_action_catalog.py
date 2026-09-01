"""What Edward can and cannot change, in one place.

Before this module the same knowledge lived in three tables that had no way of
agreeing with each other: the request normalizers decided that a message was
*action-shaped*, the composers decided what to say about it, and the action
parsers decided whether the action existed. When the write plane gained real
capabilities the composers were not updated, so Edward answered "I can't create
tasks — I'm read-only in this version" to a request it was fully able to
perform. A user cannot tell a policy denial from a stale sentence, so a wrong
sentence is a wrong product.

The catalogue is therefore the source of truth for four different consumers:

* the **recognizer**, which may only propose one of these names and may only
  fill these fields with these values;
* the **clarifier**, which asks for exactly the one required field a
  recognized request is missing;
* the **responder**, which explains a boundary using the real reason and the
  real route rather than a blanket refusal;
* the **gateway**, which reads capability, risk and confirmation mode from
  here instead of from its own private constants.

Nothing here performs, authorizes, or resolves anything. It is a description.
Authority stays where it was: capabilities, scope and canonical resolution in
`EdwardActionGateway`, execution in the existing domain services.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

ActionName = Literal[
    "student.requirement.submit_response",
    "student.preferences.update",
    "student.support.contact",
    "operations.follow_up.create",
    "operations.work_item.update",
    "operations.cohort.create_follow_ups",
    "communications.email.prepare",
]

ActorKind = Literal["student", "staff"]

FieldKind = Literal["text", "enum", "phone", "day", "flag"]


@dataclass(frozen=True, slots=True)
class ActionField:
    """One value a request may carry from language into policy.

    Fields never carry identifiers. A student id, a work-item id, a mailbox or
    a cohort membership is resolved by the gateway from canonical state; the
    only thing language contributes is a name, a key the user typed, or a
    small enumerated value.
    """

    name: str
    kind: FieldKind
    describe: str
    values: tuple[str, ...] = ()
    max_length: int = 120
    required: bool = False
    #: Asked back when a recognized action is missing this field.
    question: str = ""


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    name: ActionName
    actor: ActorKind
    #: Per-action staff capability; students act on their own record instead.
    capability: str | None
    risk_class: int
    confirmation_mode: Literal["immediate", "confirm", "strong_confirm", "external_confirm"]
    #: One line, in the second person, used both in the recognizer prompt and
    #: in the sentence a user reads when they ask what Edward can do.
    summary: str
    fields: tuple[ActionField, ...] = ()
    #: Targets Edward must resolve itself, and the question to ask when it
    #: cannot. `None` means the action does not need that kind of target.
    student_question: str | None = None
    work_item_question: str | None = None
    cohort_question: str | None = None
    draft_question: str | None = None

    def field(self, name: str) -> ActionField | None:
        return next((item for item in self.fields if item.name == name), None)


_PRIORITIES = ("urgent", "high", "medium", "low")
_WORK_STATUSES = ("todo", "in_progress", "blocked", "follow_up_required", "done", "cancelled")
_DAYS = (
    "today",
    "tomorrow",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "next_week",
)
"""The whole natural-language date vocabulary. Deliberately tiny: a due date
that the user did not clearly say is worse than no due date, and the gateway
resolves these against the server clock and time zone rather than trusting a
parsed calendar date from prose."""

PREFERENCE_FIELDS = ("preferredName", "pronouns", "mobilePhone", "communicationPreference")


ACTIONS: tuple[ActionDefinition, ...] = (
    ActionDefinition(
        name="student.preferences.update",
        actor="student",
        capability=None,
        risk_class=1,
        confirmation_mode="confirm",
        summary=(
            "update your own preferred name, pronouns, mobile number, or whether the "
            "university contacts you by email or text"
        ),
        fields=(
            ActionField(
                "preferredName",
                "text",
                "the name the student wants to be called",
                max_length=80,
                question="What would you like your preferred name to be?",
            ),
            ActionField(
                "pronouns",
                "text",
                "the student's pronouns, exactly as they gave them",
                max_length=40,
                question="Which pronouns would you like on your record?",
            ),
            ActionField(
                "mobilePhone",
                "phone",
                "the student's new mobile number",
                max_length=32,
                question="What number should I put on your record?",
            ),
            ActionField(
                "communicationPreference",
                "enum",
                "how the student wants to be contacted",
                values=("email", "sms"),
                question="Would you prefer email or text messages?",
            ),
        ),
    ),
    ActionDefinition(
        name="student.support.contact",
        actor="student",
        capability=None,
        risk_class=2,
        confirmation_mode="confirm",
        summary="open a support request so a member of staff picks up your question",
        fields=(
            ActionField(
                "message",
                "text",
                "what the student needs help with, in their own words",
                max_length=500,
                required=True,
                question="What should I tell them you need help with?",
            ),
        ),
    ),
    ActionDefinition(
        name="student.requirement.submit_response",
        actor="student",
        capability=None,
        risk_class=2,
        confirmation_mode="confirm",
        summary="record that you have completed a checklist step that only needs your word",
        fields=(
            ActionField(
                "requirement",
                "text",
                "the checklist step the student says they finished, as they named it",
                max_length=120,
                question="Which step do you mean?",
            ),
        ),
        student_question=None,
    ),
    ActionDefinition(
        name="operations.follow_up.create",
        actor="staff",
        capability="edward.follow_up.create",
        risk_class=2,
        confirmation_mode="confirm",
        summary=(
            "create a new internal Action Center follow-up task on one student — a reminder "
            "to yourself or your team; it does not contact the student"
        ),
        fields=(
            ActionField(
                "subject",
                "text",
                (
                    "the requirement or topic the follow-up is about, if they named one "
                    "— never the student's name, which is resolved separately"
                ),
                max_length=120,
            ),
            ActionField("priority", "enum", "how urgent it is", values=_PRIORITIES),
            ActionField("due", "day", "when it is due, if a day was named", values=_DAYS),
            ActionField(
                "assignToMe", "flag", "true when the staff member wants to own it themselves"
            ),
        ),
        student_question="Which student is the follow-up for?",
    ),
    ActionDefinition(
        name="operations.work_item.update",
        actor="staff",
        capability="edward.work_item.update",
        risk_class=3,
        confirmation_mode="strong_confirm",
        summary=(
            "change the status, owner, follow-up date or next step of an existing tracked "
            "work item — the ones with keys like AST-01234"
        ),
        fields=(
            ActionField("status", "enum", "the status it should move to", values=_WORK_STATUSES),
            ActionField(
                "followUp", "day", "the day to come back to it, if one was named", values=_DAYS
            ),
            ActionField(
                "nextStep",
                "text",
                "what happens next, when the staff member said it",
                max_length=200,
            ),
            ActionField("assignToMe", "flag", "true when they want to take ownership"),
        ),
        work_item_question=(
            "Which work item? Give me its key — they look like AST-01234 — or ask me to "
            "show your queue and pick one from it."
        ),
    ),
    ActionDefinition(
        name="operations.cohort.create_follow_ups",
        actor="staff",
        capability="edward.cohort.follow_up.create",
        risk_class=3,
        confirmation_mode="strong_confirm",
        summary="create the same follow-up for a small, filtered group of students at once",
        fields=(
            ActionField("priority", "enum", "how urgent they are", values=_PRIORITIES),
            ActionField("due", "day", "when they are due", values=_DAYS),
            ActionField("assignToMe", "flag", "true when they should all be assigned to them"),
        ),
        cohort_question=(
            "Which students? Describe the group — a program, class year, requirement or "
            "deposit state — and I will count it before doing anything."
        ),
    ),
    ActionDefinition(
        name="communications.email.prepare",
        actor="staff",
        capability="edward.email.prepare",
        risk_class=4,
        confirmation_mode="external_confirm",
        summary=(
            "prepare an email to one student for a final send review — Edward never sends it itself"
        ),
        fields=(),
        student_question="Who should the email go to?",
        draft_question="I need a draft first — ask me to draft it and I will prepare it next.",
    ),
)

BY_NAME: Mapping[ActionName, ActionDefinition] = {action.name: action for action in ACTIONS}

CAPABILITY_BY_ACTION: Mapping[str, str] = {
    action.name: action.capability for action in ACTIONS if action.capability
}

#: Held by every staff member who may act at all; per-action capability is
#: checked on top of it.
STAFF_MASTER_CAPABILITY = "edward.act"

#: Broad student scope. Without it a staff member acts only on their own
#: caseload, their own work items, or their own component.
BROAD_STUDENT_CAPABILITY = "edward.student.any"


def actions_for(actor: ActorKind, capabilities: Sequence[str] = ()) -> tuple[ActionDefinition, ...]:
    """The actions this actor could actually reach right now.

    Capability filtering happens here so the recognizer is never shown an
    action the actor cannot perform: a proposal that is certain to be denied
    is worse than not recognizing it, because the user reads a refusal
    instead of a route.
    """

    held = set(capabilities)
    if actor == "student":
        return tuple(action for action in ACTIONS if action.actor == "student")
    if STAFF_MASTER_CAPABILITY not in held:
        return ()
    return tuple(
        action
        for action in ACTIONS
        if action.actor == "staff" and (action.capability is None or action.capability in held)
    )


def describe_capabilities(actor: ActorKind, capabilities: Sequence[str] = ()) -> list[str]:
    """One clause per action, for answering "what can you actually do?"."""

    return [action.summary for action in actions_for(actor, capabilities)]


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Boundary:
    """Something Edward is asked to do, cannot do, and can explain.

    `boundary` says what specifically is not possible — never "I am
    read-only", which is now false and was always uninformative. `route` says
    what actually does it, because a person who asked for a change wants the
    change, not a taxonomy of Edward.
    """

    code: str
    actor: ActorKind | Literal["both"]
    pattern: re.Pattern[str]
    boundary: str
    route: str
    #: True when opening a support request is a genuinely useful next move.
    offer_support: bool = False


STUDENT_BOUNDARIES: tuple[Boundary, ...] = (
    Boundary(
        code="forge_state",
        actor="student",
        pattern=re.compile(
            r"\b(?:mark|set|record|make|log|update)\b[^.!?]{0,40}"
            r"\b(?:deposit|payment|tuition|bill|balance|transcript|document|immuni[sz]ation)\b"
            r"[^.!?]{0,40}\b(?:as\s+)?(?:paid|complete|completed|done|received|accepted|verified)\b"
            r"|\bi (?:paid|sent|submitted|uploaded)\b[^.!?]{0,60}"
            r"\b(?:mark|record|update)\b[^.!?]{0,24}\bit\b",
            re.I,
        ),
        boundary=(
            "I can't record a payment or a document as received. Those states come from the "
            "systems that produce them — the payment processor, the document review — so a "
            "note from me would make your record say something nobody verified."
        ),
        route=(
            "A deposit posts on its own once the payment clears, usually within a day or two, "
            "and a document changes state when it is reviewed."
        ),
        offer_support=True,
    ),
    Boundary(
        code="payment",
        actor="student",
        pattern=re.compile(
            r"\b(?:pay|paying|settle|charge)\b.{0,40}\b(?:deposit|tuition|bill|balance|fee)\b"
            r"|\b(?:deposit|tuition|bill|balance)\b.{0,32}\b(?:for me|on my behalf)\b",
            re.I,
        ),
        boundary="I can't make a payment for you — Edward never moves money.",
        route="The Payments page takes the deposit, and the amount there is the one that counts.",
    ),
    Boundary(
        code="document_upload",
        actor="student",
        pattern=re.compile(
            r"\b(?:upload|attach|submit|send)\b.{0,40}"
            r"\b(?:document|transcript|immuni[sz]ation|record|form|id|passport|file|pdf)\b"
            r"|\bi'?m attaching\b|\bhere'?s my (?:transcript|document|form)\b",
            re.I,
        ),
        boundary=(
            "I can't take a file through chat — there's no way for me to prove which "
            "document arrived or that it is yours."
        ),
        route="The Documents page uploads it and matches it to the right requirement.",
    ),
    Boundary(
        code="appointment",
        actor="student",
        pattern=re.compile(
            r"\b(?:book|schedule|reschedule|cancel|set up|arrange)\b.{0,40}"
            r"\b(?:appointment|meeting|advising|session|slot)\b"
            # "Book me in with my adviser thursday at 3" names no appointment
            # noun; the adviser is the appointment.
            r"|\b(?:book|schedule)\b.{0,24}\bwith (?:my |the |an? )?(?:advis|counsel)",
            re.I,
        ),
        boundary="I can't book, move, or cancel an appointment.",
        route="The Appointments page shows your adviser's open times and books them.",
    ),
    Boundary(
        code="housing_assignment",
        actor="student",
        pattern=re.compile(
            r"\b(?:room|roommate|dorm|hall|single|shared|housing)\b.{0,48}"
            r"\b(?:change|swap|move|assign|pick|choose|put me)\b"
            r"|\b(?:change|swap|move|assign|put)\b.{0,32}\b(?:room|dorm|hall|housing)\b",
            re.I,
        ),
        boundary="I can't assign or change a room — Audentra doesn't hold room allocations.",
        route=(
            "Housing preferences go through the Housing step; allocation is the housing office's."
        ),
        offer_support=True,
    ),
    Boundary(
        code="program_change",
        actor="student",
        pattern=re.compile(
            r"\b(?:change|switch|move)\b.{0,24}\b(?:my )?"
            r"(?:major|programme|program|course of study)\b"
            r"|\b(?:major|programme|program)\b.{0,16}\bto\b",
            re.I,
        ),
        boundary="I can't change your program of study.",
        route="A program change goes through your adviser and the registrar.",
        offer_support=True,
    ),
    Boundary(
        code="legal_name",
        actor="student",
        pattern=re.compile(r"\b(?:legal|official|surname|last|family|birth)\s+name\b", re.I),
        boundary=(
            "I can only change the name you like to be called. Your legal name on the "
            "official record isn't something I can edit."
        ),
        route=(
            "A legal-name change is a records request with the registrar, usually with documents."
        ),
        offer_support=True,
    ),
    Boundary(
        code="waiver",
        actor="student",
        pattern=re.compile(
            r"\b(?:waive|waiver|exempt|exemption|excuse me from|skip)\b.{0,40}"
            r"\b(?:requirement|step|immuni[sz]|document|hold|deadline)\b"
            r"|\b(?:requirement|hold|deadline)\b.{0,24}\b(?:waive[dr]?|exempt)\b",
            re.I,
        ),
        boundary="I can't waive a requirement or grant an exemption — that's a staff decision.",
        route="The office that owns the requirement decides it.",
        offer_support=True,
    ),
    Boundary(
        code="deadline_change",
        actor="student",
        pattern=re.compile(
            r"\b(?:extend|push|delay|move|shift)\b[^.!?]{0,40}\b(?:deadline|due date)\b"
            r"|\b(?:deadline|due date)\b[^.!?]{0,32}"
            r"\b(?:extension|extended|later|back|out|pushed?)\b"
            r"|\bmore time\b[^.!?]{0,24}\b(?:for|on|to)\b",
            re.I,
        ),
        boundary="I can't move a deadline.",
        route="Extensions are decided by the office that set the deadline.",
        offer_support=True,
    ),
    Boundary(
        code="aid_amount",
        actor="student",
        pattern=re.compile(
            r"\b(?:increase|raise|add|more|change|adjust|appeal)\b.{0,32}"
            r"\b(?:aid|award|scholarship|grant|package)\b",
            re.I,
        ),
        boundary="I can't change a financial-aid award.",
        route="Your financial aid counselor handles appeals and revisions.",
        offer_support=True,
    ),
    Boundary(
        code="enrollment_status",
        actor="student",
        pattern=re.compile(
            r"\b(?:cancel|withdraw|defer|drop|drop out|quit|delete)\b.{0,32}"
            # "enroll?ment" covers both spellings; the earlier "enrol?ment"
            # could never match the double-L American "enrollment" at all.
            r"\b(?:enroll?ment|admission|offer|place|account|my application)\b"
            r"|\bdelete my account\b",
            re.I,
        ),
        boundary="I can't withdraw, defer, or close an enrollment.",
        route=(
            "That decision goes through enrollment services, and it has consequences worth "
            "talking through first."
        ),
        offer_support=True,
    ),
    Boundary(
        code="other_person_record",
        actor="student",
        pattern=re.compile(
            r"\b(?:my )?(?:roommate|friend|classmate|brother|sister|partner)\b.{0,48}"
            r"\b(?:update|change|set|submit|pay)\b"
            r"|\b(?:update|change|set)\b.{0,40}"
            r"\b(?:for (?:him|her|them)|on (?:his|her|their) behalf)\b",
            re.I,
        ),
        boundary="I can only change your own record.",
        route="They can sign in and do it themselves, or ask their own adviser.",
    ),
    Boundary(
        code="external_email",
        actor="student",
        pattern=re.compile(
            r"\b(?:email|send|forward|mail|show|give)\b.{0,40}"
            r"\b(?:my|to my)\s+(?:mum|mom|mother|dad|father|parent|guardian|family)\b"
            # "My dad wants to see my bill" is the same request from the other
            # direction, and it is the more common way a student phrases it.
            r"|\bmy\s+(?:mum|mom|mother|dad|father|parents?|guardian|family)\b"
            r"[^.!?]{0,40}\b(?:wants?|needs?|asked|would like)\b[^.!?]{0,24}"
            r"\b(?:see|access|view|copy|know|check)\b"
            r"|\b(?:mum|mom|dad|parent|guardian)\b.{0,32}\ba copy\b",
            re.I,
        ),
        boundary=(
            "I can't email your records to someone else. Sharing them with a parent or "
            "guardian is a decision only you can record."
        ),
        route=(
            "Parent and guardian access is granted from your own account, and then they "
            "see it directly."
        ),
    ),
)


STAFF_BOUNDARIES: tuple[Boundary, ...] = (
    Boundary(
        code="bulk_email",
        actor="staff",
        pattern=re.compile(
            r"\b(?:email|message|mail|write to)\b.{0,32}"
            r"\b(?:every|all|each)\b.{0,32}\b(?:students?|advisees?|people|them)\b"
            # The other word order: "every advisee of mine … emailed today".
            r"|\b(?:every|all|each)\b.{0,64}\b(?:e-?mailed|messaged|mailed|contacted)\b"
            r"|\b(?:bulk|mass|campaign)\b.{0,16}\b(?:e-?mail|message|mail)\b",
            re.I,
        ),
        boundary=(
            "I don't send bulk email. Every message Edward prepares goes to one named "
            "student and gets its own send confirmation."
        ),
        route=(
            "I can count and list the group, and create follow-ups for up to 25 of them so "
            "the outreach is tracked."
        ),
    ),
    Boundary(
        code="send_message",
        actor="staff",
        pattern=re.compile(
            # "Send that email" points at a draft Edward already wrote, and the
            # answer to it is a prepared send intent, not a boundary. Only a
            # request to send something *new* runs into this limit.
            r"\bsend\b(?!\s+(?:that|this|it|the one)\b)"
            r"[^.!?]{0,24}\b(?:email|message|note|reminder)\b"
            r"|\b(?:email|text|message)\b\s+(?:her|him|them|the student)\b"
            r"|\bfire off\b|\bdispatch\b",
            re.I,
        ),
        boundary=(
            "I don't send messages myself — an email leaves the institution, so a person "
            "confirms it."
        ),
        route=(
            "Ask me to draft it and then prepare it: you get the exact text, the sender and "
            "the recipient, and one final send confirmation."
        ),
    ),
    Boundary(
        code="sms",
        actor="staff",
        pattern=re.compile(
            # A named student is the far commoner object than a pronoun:
            # "whatsapp Greta Oakenshaw a reminder" must be this boundary, not
            # a follow-up conjured from the word "reminder".
            r"\b(?:text|sms|whatsapp)\b\s*(?:her|him|them|the student|(?-i:[A-Z][a-z]+))\b",
            re.I,
        ),
        boundary="I can't send an SMS — email is the only channel Edward can prepare.",
        route="I can draft the text for you to send, or prepare an email instead.",
    ),
    Boundary(
        code="place_call",
        actor="staff",
        pattern=re.compile(
            # "(?<!to )" keeps "make a note to call Petra" and "remind me to
            # ring her" where they belong — those delegate the *note*, and the
            # call stays the staff member's own.
            r"(?<!to )\b(?:call|ring|phone) (?:her|him|them|the student|(?-i:[A-Z][a-z]+))\b"
            r"|\bplace (?:a|the) call\b|\bdial\b",
            re.I,
        ),
        boundary="I can't place a call.",
        route=(
            "I can prepare talking points from the student's record, and log the follow-up "
            "afterwards."
        ),
    ),
    Boundary(
        code="schedule",
        actor="staff",
        pattern=re.compile(
            r"\b(?:schedule|book|arrange|set up)\b.{0,40}"
            r"\b(?:appointment|meeting|session|advising|slot)\b",
            re.I,
        ),
        boundary="I can't book an appointment.",
        route="Booking happens on the student's advising page; I can show you the open slots.",
    ),
    Boundary(
        code="forge_record",
        actor="staff",
        pattern=re.compile(
            r"\b(?:mark|set|make|record)\b.{0,40}"
            r"\b(?:deposit|payment|tuition|balance)\b.{0,24}"
            r"\b(?:paid|cleared|settled|received)\b"
            r"|\b(?:mark|set)\b.{0,40}\b(?:requirement|document|transcript|immuni[sz]ation)\b"
            r".{0,24}\b(?:complete|completed|accepted|approved|verified|waived)\b",
            re.I,
        ),
        boundary=(
            "I can't set a payment or a document decision from chat — those states come "
            "from the systems that produce them, and an assistant writing them would make "
            "the record unverifiable."
        ),
        route=(
            "A payment posts from the payment system; a document decision is made in the "
            "review workflow, which keeps the reviewer and the reason."
        ),
    ),
    Boundary(
        code="reassign_other",
        actor="staff",
        pattern=re.compile(
            r"\b(?:assign|reassign|hand|give|route|move)\b.{0,40}\bto\b\s+"
            r"(?!me\b)(?:[A-Z][a-z]+|another|a different|the)\b.{0,24}"
            r"\b(?:advis|counsel|team|desk|queue|caseload)\b",
            re.I,
        ),
        boundary="I can only assign work to you, not to a colleague.",
        route=(
            "Reassigning someone else's queue is done in the Action Center, where their "
            "load is visible."
        ),
    ),
)


#: The person is asking *Edward* to do it: an imperative, an explicit
#: delegation, or a polite request. Everything else is a statement about their
#: own plans or a question about the world.
_DELEGATED_TO_EDWARD = re.compile(
    r"\b(?:can|could|will|would)\s+you\b"
    r"|\bfor me\b|\bon my behalf\b|\bplease\b"
    # A third party delegating through the person in front of Edward. It reads
    # as a request even though the verb is reported speech, and it is exactly
    # the shape the other-person boundary exists for.
    r"|\basked me to\b|\bfor (?:him|her|them)\b|\bon (?:his|her|their) behalf\b"
    r"|^\s*(?:hey |hi |ok(?:ay)? |so )?(?:just\s+|please\s+)?"
    r"(?:pay|upload|attach|submit|send|email|text|whatsapp|call|ring|book|schedule|"
    r"reschedule|cancel|"
    r"withdraw|defer|delete|remove|change|switch|move|swap|set|update|edit|correct|"
    r"fix|waive|approve|reject|extend|push|increase|raise|assign|reassign|escalate|"
    r"close|mark|record|put|give|forward|make|route|hand|write|sort|handle|deal|drop)\b"
    # A trailing imperative after a statement: "…, sort that out", "…, handle it".
    r"|\b(?:sort|handle|deal with|take care of|see to)\s+(?:that|it|this)\b"
    # A passive request: "I need every advisee emailed", "I want it updated".
    r"|\b(?:need|want|would like)\b[^.!?]{0,64}"
    r"\b(?:e-?mailed|messaged|contacted|sent|updated|changed|created|closed|assigned"
    r"|booked|scheduled|cancelled|removed|added|recorded|marked)\b",
    re.I,
)
#: The person is describing their own intention or asking how something works.
#: "I want to pay my deposit" is a question the payment page answers, not a
#: request for Edward to move money — and answering it with a boundary loses
#: the widget that actually helps.
_OWN_INTENTION = re.compile(
    r"^\s*(?:hey |hi |ok(?:ay)? |so )?"
    r"(?:i\s+(?:want|need|would like|'d like|am going|'m going|plan|intend|have|'ve)\b"
    r"|how (?:do|can|would) i\b|where (?:do|can) i\b|when (?:do|can) i\b"
    r"|what (?:do|happens|if)\b|can i\b|should i\b|am i\b|is (?:it|there)\b)",
    re.I,
)


#: Hand-offs strong enough to survive an own-intention opener.
_EXPLICIT_DELEGATION = re.compile(
    r"\bfor me\b|\bon my behalf\b|\b(?:can|could|will|would)\s+you\b|\bplease\b"
    r"|\basked me to\b|\bfor (?:him|her|them)\b"
    r"|\b(?:sort|handle|deal with|take care of|see to)\s+(?:that|it|this)\b"
    r"|\b(?:need|want|would like)\b[^.!?]{0,64}"
    r"\b(?:e-?mailed|messaged|contacted|sent|updated|changed|created|closed|assigned"
    r"|booked|scheduled|cancelled|removed|added|recorded|marked)\b",
    re.I,
)


def asks_edward_to_act(message: str) -> bool:
    """Whether this message delegates an action rather than describing one.

    The distinction decides whether a boundary is the right answer at all. A
    boundary that fires on any mention of paying, booking or uploading turns
    the most ordinary questions a student asks — "I want to pay my deposit",
    "how do I upload my transcript?" — into refusals, and takes the product
    surface that answers them off the table.
    """

    text = message.strip()
    if not text:
        return False
    if _OWN_INTENTION.match(text):
        # An opener like "I want" or "how do I" describes the person's own
        # plan, so only an explicit hand-off turns it back into a request.
        # A bare verb further along the sentence is not enough: "I want to pay
        # my deposit" contains "pay" and is not a request for Edward to pay.
        return bool(_EXPLICIT_DELEGATION.search(text))
    return bool(_DELEGATED_TO_EDWARD.search(text))


def boundary_for(actor: ActorKind, message: str) -> Boundary | None:
    """The most specific boundary this message runs into, if any."""

    if not asks_edward_to_act(message):
        return None
    table = STUDENT_BOUNDARIES if actor == "student" else STAFF_BOUNDARIES
    for entry in table:
        if entry.pattern.search(message):
            return entry
    return None


def boundary_answer(entry: Boundary, *, support_offer: str | None = None) -> str:
    """The sentence a user reads: what is not possible, and what is."""

    parts = [entry.boundary, entry.route]
    if entry.offer_support and support_offer:
        parts.append(support_offer)
    return " ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------

_PHONE = re.compile(r"^\+?[0-9][0-9 ()+.-]{5,31}$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z .'À-ɏ-]{0,79}$")
_PRONOUNS = re.compile(r"^[A-Za-z]{1,12}(?:\s*/\s*[A-Za-z]{1,12}){0,3}$")


def coerce_fields(action: ActionName, raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only fields this action declares, with values it declares.

    Applied to every recognizer result, whichever tier produced it. A model
    that invents a field name, an enum value or a 4,000-character "next step"
    contributes nothing: the value is dropped, and the gateway then resolves
    the action from canonical state as it would have anyway.
    """

    definition = BY_NAME.get(action)
    if definition is None:
        return {}
    cleaned: dict[str, Any] = {}
    for spec in definition.fields:
        if spec.name not in raw:
            continue
        value = raw[spec.name]
        if value is None:
            continue
        if spec.kind == "flag":
            if isinstance(value, bool) and value:
                cleaned[spec.name] = True
            continue
        text = str(value).strip()
        if not text or len(text) > spec.max_length:
            continue
        if spec.kind == "enum" or spec.kind == "day":
            lowered = text.lower().replace(" ", "_")
            if lowered in spec.values:
                cleaned[spec.name] = lowered
            continue
        if spec.kind == "phone":
            if _PHONE.match(text):
                cleaned[spec.name] = text
            continue
        if spec.name == "preferredName" and not _NAME.match(text):
            continue
        if spec.name == "pronouns" and not _PRONOUNS.match(text):
            continue
        cleaned[spec.name] = text
    return cleaned


def missing_required(action: ActionName, fields: Mapping[str, Any]) -> tuple[ActionField, ...]:
    definition = BY_NAME.get(action)
    if definition is None:
        return ()
    return tuple(spec for spec in definition.fields if spec.required and spec.name not in fields)


def preference_question(fields: Mapping[str, Any], message: str) -> str | None:
    """The one question that turns a vague preference request into an action.

    "Change my name" is a complete intent with a missing value. Refusing it,
    or answering it with the enrollment checklist, both fail the student for
    the same reason: the question they asked has a one-line answer.
    """

    if fields:
        return None
    lowered = message.lower()
    for spec, cue in (
        ("preferredName", r"\bname\b"),
        ("pronouns", r"\bpronoun"),
        ("mobilePhone", r"\b(?:phone|mobile|number|cell)\b"),
        ("communicationPreference", r"\b(?:contact|email|text|sms|notification|preference)\b"),
    ):
        if re.search(cue, lowered):
            found = BY_NAME["student.preferences.update"].field(spec)
            if found is not None:
                return found.question
    return None
