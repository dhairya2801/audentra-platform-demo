"""What Edward says when it is not going to propose an action.

Between "understood, here is the preview" and "understood, here is why not"
there used to be nothing, so every other outcome fell through to a read
pipeline that answered a different question, or to a stock sentence that said
Edward was read-only. Measured against the write suite, those two outcomes
together accounted for more failures than every policy and execution defect
combined — and the read-only sentence was not merely unhelpful, it was false.

Four kinds of answer live here, in the order they are tried:

1. **Clarification.** The request is understood and one thing is missing:
   which student, which task, which name. One short question is worth more
   than any refusal, because the person already told Edward what they want.
2. **Boundary.** The request is understood and Edward genuinely cannot do it.
   Say what specifically is out of reach and what actually does it. Never
   "read-only": that is a fact about Edward, and the person asked about the
   university.
3. **Denial.** Policy said no. Say which rule, in the actor's own terms, and
   what would change the answer.
4. **Recall.** The person is asking about something Edward just did. The
   receipts for this conversation are server-issued facts; answering from
   them is both honest and the only way a multi-turn write conversation makes
   sense.

None of this decides anything. Authority stays in the gateway; this module
only turns an outcome into a sentence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from audentra.domain.edward_action_catalog import (
    BY_NAME,
    ActionName,
    ActorKind,
    boundary_answer,
    boundary_for,
    describe_capabilities,
    missing_required,
    preference_question,
)

JsonDict = dict[str, Any]

STUDENT_SUPPORT_OFFER = (
    "If you'd like, I can open a support request so someone picks it up — just say so."
)
STAFF_SUPPORT_OFFER = ""


def _answer(message: str, *, code: str) -> JsonDict:
    """A complete deterministic response payload in the shape both pipelines use."""

    return {
        "message": message,
        "blocks": [{"type": "text", "text": message, "fallbackText": message}],
        "provider": "guided",
        "model": None,
        "usage": None,
        "suggestedActions": [],
        "contextReceipts": [],
        "widgets": [],
        "actionIntents": [],
        "actionReceipts": [],
        "actionResponse": {"kind": code},
    }


# ---------------------------------------------------------------------------
# 1. Clarification
# ---------------------------------------------------------------------------

_WORK_ITEM_FIELDS = (
    "I can change a work item's status, move it to you, set a follow-up date, or "
    "record the next step."
)


def clarification(
    action: ActionName,
    fields: Mapping[str, Any],
    *,
    message: str,
    has_student: bool = True,
    has_work_item: bool = True,
    has_cohort: bool = True,
    has_draft: bool = True,
) -> JsonDict | None:
    """The one question that would make this request actionable.

    Deliberately one question, not a form. A person who typed "create a
    follow-up" wants to be asked which student, not to be handed the schema.
    """

    definition = BY_NAME.get(action)
    if definition is None:
        return None

    if definition.student_question and not has_student:
        return _answer(definition.student_question, code="clarify_student")
    if definition.work_item_question and not has_work_item:
        return _answer(definition.work_item_question, code="clarify_work_item")
    if definition.cohort_question and not has_cohort:
        return _answer(definition.cohort_question, code="clarify_cohort")
    if definition.draft_question and not has_draft:
        return _answer(definition.draft_question, code="clarify_draft")

    for spec in missing_required(action, fields):
        if spec.question:
            return _answer(spec.question, code="clarify_field")

    if action == "student.preferences.update" and not fields:
        question = preference_question(fields, message)
        if question:
            return _answer(question, code="clarify_field")
        return _answer(
            "I can change your preferred name, your pronouns, your mobile number, or "
            "whether we contact you by email or text. Which one, and what should it be?",
            code="clarify_field",
        )

    if action == "operations.work_item.update" and not fields:
        return _answer(
            f"{_WORK_ITEM_FIELDS} Which of those did you want? Priority and escalation "
            "are set on the item itself in the Action Center.",
            code="clarify_field",
        )

    if action == "student.requirement.submit_response" and not fields:
        return _answer("Which step do you mean?", code="clarify_field")

    if action == "student.support.contact" and not _has_a_topic(message):
        # "I need help" routed as-is becomes a ticket that says "I need help",
        # which costs a person a round trip to find out what it is about. One
        # question here is worth more than the request it replaces.
        return _answer(
            "I can put this in front of a person — what do you need help with? "
            "If it's one of your open steps, naming it gets it to the right team faster.",
            code="clarify_field",
        )

    return None


#: Something in the message a support request can be routed on.
_SUPPORT_TOPIC = re.compile(
    r"\b(?:transcript|immuni[sz]ation|vaccin|aid|fafsa|scholarship|grant|loan|award|"
    r"deposit|payment|tuition|bill|balance|housing|room|dorm|orientation|document|"
    r"upload|requirement|step|checklist|deadline|due|registration|register|course|"
    r"class|account|advis|appointment|meeting|form|application|visa|i-20|passport|"
    r"id|identity|health|insurance|hold|blocker|verification|file|record)\b",
    re.I,
)


def _has_a_topic(message: str) -> bool:
    if _SUPPORT_TOPIC.search(message):
        return True
    # A long message is its own topic: the student explained the problem.
    return len(message.split()) >= 12


# ---------------------------------------------------------------------------
# 2. Boundary
# ---------------------------------------------------------------------------


#: A student saying they are stuck or overwhelmed. They have not asked for a
#: person, so opening a request for them would be presumptuous — but leaving
#: the offer unsaid is the difference between an answer and help.
_DISTRESS = re.compile(
    r"\b(?:stress(?:ed|ing)?|overwhelm|anxious|panic|confus(?:ed|ing)|lost|struggling"
    r"|freaking out|don'?t (?:know|understand) what|no idea what|can'?t cope"
    r"|going in circles|nothing (?:is )?working)\b",
    re.I,
)


def offers_support_route(message: str) -> str | None:
    """The one sentence a stuck student should always be given."""

    return STUDENT_SUPPORT_OFFER if _DISTRESS.search(message) else None


#: Two or more students named in one action request — by reference or by full
#: name. "Create follow-ups for Petra Yarrowby and Greta Oakenshaw" is a
#: plural request that is really two singular ones, and proposing for the first
#: while never mentioning the second loses half of it silently.
_MANY_REFERENCES = re.compile(r"\bSYN-\d{4,}\b", re.I)
_FULL_NAMES = re.compile(r"\b[A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,20}\b")


def several_students_named(message: str) -> JsonDict | None:
    """Answer a list of students without pretending it is a cohort.

    "Create follow-ups for SYN-000386, SYN-000061 and SYN-000098" is the most
    natural way a lead asks for a handful of tasks, and it is neither one
    student nor a filter. Saying so — and naming them back — is better than
    "which student is the follow-up for?" about a message that named three.
    """

    references: list[str] = _MANY_REFERENCES.findall(message)
    if len(references) < 2:
        names = [name for name in _FULL_NAMES.findall(message) if name not in _NOT_A_NAME]
        if len(names) < 2:
            return None
        references = names
    listed = ", ".join(references[:10])
    return _answer(
        f"You named {len(references)} students: {listed}. I create follow-ups one student "
        "at a time unless the group is described as a filter — a program, a class year, a "
        f"requirement. Say the word and I'll start with {references[0]}, or describe the "
        "group and I'll count it first.",
        code="several_students",
    )


#: The sentence that introduces a proposal. One generic line for every action
#: made the card do all the work; naming what is about to happen — and, for a
#: support request, who receives it — is the difference between "review the
#: details below" and an answer.
def proposal_message(action: str, preview: Mapping[str, Any]) -> str:
    if action == "student.support.contact":
        raw_routes = preview.get("routesTo")
        routes: Mapping[str, Any] = raw_routes if isinstance(raw_routes, Mapping) else {}
        raw_adviser = routes.get("adviser")
        adviser: Mapping[str, Any] | None = (
            raw_adviser if isinstance(raw_adviser, Mapping) else None
        )
        office = routes.get("office")
        destination = None
        if adviser and adviser.get("name"):
            title = adviser.get("title")
            destination = f"your adviser, {adviser['name']}" + (f" ({title})" if title else "")
        elif office:
            destination = str(office)
        raw_requirement = preview.get("requirement")
        requirement: Mapping[str, Any] | None = (
            raw_requirement if isinstance(raw_requirement, Mapping) else None
        )
        about = f" about {requirement['title']}" if requirement and requirement.get("title") else ""
        who = f" It goes to {destination}." if destination else ""
        return (
            f"I can open a support request{about}.{who} "
            "Nothing has been sent yet — confirm below and I'll open it."
        )
    if action == "student.preferences.update":
        raw_changes = preview.get("changes")
        described = (
            [
                f"your {_humanize_field(str(item.get('field')))} to {item.get('after')}"
                for item in raw_changes
                if isinstance(item, Mapping)
            ]
            if isinstance(raw_changes, list)
            else []
        )
        raw_warnings = preview.get("warnings")
        warnings = (
            " ".join(str(item) for item in raw_warnings) if isinstance(raw_warnings, list) else ""
        )
        if len(described) == 1:
            listed = described[0]
        elif described:
            listed = f"{', '.join(described[:-1])} and {described[-1]}"
        else:
            listed = ""
        body = f"I can set {listed}." if listed else "Here is the exact change."
        tail = f" {warnings}" if warnings else ""
        return (
            f"{body} Nothing has been saved yet — check the before and after below and "
            f"confirm if it looks right.{tail}"
        )
    if action == "student.requirement.submit_response":
        return (
            "I can record that against the step below. Nothing has changed yet — confirm "
            "and I'll submit your response."
        )
    return (
        "I prepared an exact preview for you. Nothing has changed yet — review the details "
        "below and confirm if they look right."
    )


#: Capitalised pairs that are institutional nouns, not people.
_NOT_A_NAME = frozenset(
    {"Action Center", "Student Health", "Student Accounts", "Academic Advising"}
)


def boundary(actor: ActorKind, message: str) -> JsonDict | None:
    """An honest, specific "no" with the route that says yes."""

    entry = boundary_for(actor, message)
    if entry is None:
        return None
    offer = STUDENT_SUPPORT_OFFER if actor == "student" else STAFF_SUPPORT_OFFER
    return _answer(boundary_answer(entry, support_offer=offer), code=f"boundary:{entry.code}")


# ---------------------------------------------------------------------------
# 3. Denial
# ---------------------------------------------------------------------------

#: What each gateway denial means to the person who hit it, and what changes
#: the answer. The gateway's own message is the fact; this is the sentence.
_DENIAL_TEXT: Mapping[str, str] = {
    "EDWARD_STUDENT_SCOPE_FORBIDDEN": (
        "I can only act on students assigned to you. {student} isn't on your caseload, so "
        "I won't create work on their record. Their assigned adviser or a department lead "
        "can do it, and I can still show you what's happening with them."
    ),
    "EDWARD_WORK_ITEM_SCOPE_FORBIDDEN": (
        "That work item isn't yours to change — it belongs to another owner and another "
        "component. I can show you its current state and who holds it."
    ),
    "EDWARD_ACTION_CAPABILITY_REQUIRED": (
        "You don't have the permission that covers this one. Bulk follow-ups and acting "
        "outside your own caseload are limited to department leads. I can do it one "
        "student at a time on your own caseload, or list the group for a lead to action."
    ),
    "EDWARD_COHORT_LIMIT_EXCEEDED": (
        "That group is larger than the 25 students Edward will act on at once. Narrow it — "
        "a program, a class year, a single requirement — and I'll count it again before "
        "doing anything."
    ),
    "EDWARD_COHORT_UNCONSTRAINED": (
        "I won't act on an unconstrained group. Tell me which students — a program, a class "
        "year, a requirement, a deposit state — and I'll count them first."
    ),
    "EDWARD_COHORT_EMPTY": (
        "That filter matches no students right now, so there is nothing to create."
    ),
    "EDWARD_COHORT_CONTEXT_REQUIRED": (
        "I don't have a group in front of me. Ask me to count or list the students first, "
        "then I'll create follow-ups for exactly that group."
    ),
    "EDWARD_COHORT_DRIFTED": (
        "The group changed between the preview and the confirmation, so I stopped before "
        "writing anything. Ask me to count it again and I'll build a fresh preview."
    ),
    "EDWARD_EMAIL_DRAFT_REQUIRED": (
        "I need the draft first. Ask me to draft the email and I'll prepare it from exactly "
        "the text you reviewed."
    ),
    "EDWARD_EMAIL_MAILBOX_REQUIRED": (
        "I need exactly one active mailbox you can send from, and right now that isn't the "
        "case. Choose one in Mail and I'll prepare the message against it."
    ),
    "EDWARD_EMAIL_RECIPIENT_UNAVAILABLE": (
        "{student} has no email address on file, so there's nothing for me to prepare a "
        "message to. I can draft the text for you, or create a follow-up instead."
    ),
    "EDWARD_STUDENT_REQUIRED": "Which student is this for?",
    "EDWARD_WORK_ITEM_REQUIRED": (
        "Which work item? Give me its key — they look like AST-01234 — or ask me to show "
        "your queue and pick one from it."
    ),
    "EDWARD_WORK_ITEM_CHANGE_REQUIRED": (
        f"{_WORK_ITEM_FIELDS} Nothing in that message changed any of them, so there is "
        "nothing for me to preview."
    ),
    "EDWARD_ACTION_NO_CHANGE": (
        "That's already the value on record, so there's nothing to change."
    ),
    "EDWARD_BLOCKER_REASON_REQUIRED": (
        "I can move it to blocked, but a blocked item with no reason tells the next "
        "person nothing. What is it waiting on?"
    ),
    "EDWARD_CANCEL_REASON_REQUIRED": (
        "I can cancel it — what should the record say the reason was?"
    ),
    "EDWARD_PREFERENCE_FIELDS_INVALID": (
        "I can change your preferred name, your pronouns, your mobile number, or whether we "
        "contact you by email or text. Tell me which one and what it should be."
    ),
    # Both of these are raised with the step's own name in them, so the
    # gateway's sentence is the better one and this table stays out of the way.
    "EDWARD_SUPPORT_MESSAGE_INVALID": "Tell me in a sentence or two what you need help with.",
    "EDWARD_FOLLOW_UP_DATE_REQUIRED": (
        "A follow-up needs a date and a next step. Tell me when to come back to it and what "
        "happens next."
    ),
    "EDWARD_ACTION_INTENT_EXPIRED": (
        "That preview expired before it was confirmed, so nothing happened. Ask again and "
        "I'll build a fresh one."
    ),
    "EDWARD_ACTION_INTENT_CHANGED": (
        "The details changed after you reviewed them, so I stopped rather than commit "
        "something you didn't see. Ask again for a fresh preview."
    ),
    "EDWARD_ACTION_NOT_FOUND": "I can't find that preview any more.",
}

_INJECTION_TEXT: Mapping[str, str] = {
    "control_override": (
        "I won't skip a confirmation, disable a check, or act on an instruction to ignore my "
        "own rules. Every change I make is previewed and confirmed by you first. Ask me "
        "plainly for the change and I'll show you exactly what it would do."
    ),
    "quoted_content": (
        "I won't act on instructions quoted from a document, an email, or another system — "
        "text inside a file can't authorise a change to a record. Tell me in your own words "
        "what you want changed and I'll prepare it."
    ),
    "foreign_institution": (
        "I can only work inside your own institution's records. I won't act on a student at "
        "another university, and I won't quietly use a same-named student here instead."
    ),
}


def denial(
    code: str,
    fallback: str,
    *,
    student_name: str | None = None,
) -> JsonDict:
    template = _DENIAL_TEXT.get(code)
    text = fallback if template is None else template.format(student=student_name or "That student")
    payload = _answer(text, code=f"denied:{code}")
    payload["actionError"] = {"code": code, "message": text}
    return payload


def injection_refusal(reason: str) -> JsonDict:
    return _answer(
        _INJECTION_TEXT.get(reason, _INJECTION_TEXT["control_override"]),
        code=f"refused:{reason}",
    )


# ---------------------------------------------------------------------------
# 4. Capability answer
# ---------------------------------------------------------------------------

#: A question *about* Edward, not a request with an object. "Can you change it
#: to Nell" names the change and must never be answered with a capability tour;
#: "can you change anything?" is the question this is for. The difference is
#: whether a concrete object follows the verb.
_CAPABILITY_QUESTION = re.compile(
    r"\bwhat (?:can|could|are) you\b"
    r"|\bcan you (?:actually |even |really )?(?:do|change|help|act)\b"
    r"(?!\s+(?:it|that|this|them|my|the|him|her|me\b))"
    r"|\b(?:do|can) (?:you|i) have to do everything (?:myself|yourself)\b"
    r"|\bare you (?:just |only )?(?:a )?(?:read[- ]?only|search box|lookup)\b"
    r"|\bonly look things up\b|\bdo anything for me\b|\bhow can you help\b",
    re.I,
)


def is_capability_question(message: str) -> bool:
    return bool(_CAPABILITY_QUESTION.search(message))


def capability_answer(
    actor: ActorKind,
    capabilities: Sequence[str] = (),
    *,
    reads: str,
) -> JsonDict:
    """The honest answer to "what can you actually do?".

    It is generated from the catalogue rather than written down, so an action
    added or a capability withdrawn changes this sentence automatically. A
    hand-written list is exactly how the previous answer became false.
    """

    clauses = describe_capabilities(actor, capabilities)
    if not clauses:
        text = (
            f"{reads} I can't change anything on your behalf in this account — "
            "no action permissions are granted to it."
        )
        return _answer(text, code="capability")
    actions = clauses[0] if len(clauses) == 1 else f"{', '.join(clauses[:-1])}, and {clauses[-1]}"
    text = (
        f"{reads} I can also make changes for you: I can {actions}. "
        "Every change is previewed first — I show you the exact effect and nothing "
        "happens until you confirm it."
    )
    return _answer(text, code="capability")


# ---------------------------------------------------------------------------
# 5. Recall — answering from this conversation's receipts
# ---------------------------------------------------------------------------

_RECALL_QUESTION = re.compile(
    r"\b(?:did|have) you (?:actually |really |even )?"
    r"(?:create|make|add|open|log|update|change|save|send|do)\b"
    r"|\bwhat did you (?:just )?(?:do|change|create|update)\b"
    # "did that ACTUALLY go through?" — the adverb is exactly how people ask
    # when they doubt it, and missing it sent the question to a read plane
    # that answered "No, that did not go through" about a committed change.
    # Found by browser E2E.
    r"|\b(?:did|has|is) (?:that|it|this|the change|my change|the update)\s+"
    r"(?:actually |really |even |already )?(?:go|gone|went) (?:through|thru)\b"
    r"|\bdid (?:that|it|this|the change) (?:actually |really |even )?"
    r"(?:work|happen|save|stick|take|apply|get (?:created|saved|applied))\b"
    r"|\bis (?:that|it|this|the change) (?:actually |really )?(?:saved|applied|in|done now)\b"
    r"|\bdid (?:you|edward) (?:actually |really )?(?:save|apply) (?:that|it|this)\b"
    r"|\bwhere did (?:that|it) (?:end up|go)\b"
    r"|\bhas it (?:gone out|been sent|sent)\b"
    r"|\bwhat have you changed\b|\bwhat did you change\b"
    r"|\bis (?:that|it) done\b",
    re.I,
)
_UNDO_QUESTION = re.compile(r"\bundo\b|\brevert\b|\btake (?:that|it) back\b|\bunsend\b", re.I)
_ABANDON = re.compile(
    r"^(?:no[,.]?\s*)?(?:actually\s*)?(?:cancel|forget|drop|scrap|never ?mind|don'?t)\b"
    r"|\bcancel that\b|\bforget (?:that|it)\b|\bdon'?t (?:do|create|make) (?:that|it)\b",
    re.I,
)


def is_recall_question(message: str) -> bool:
    return bool(_RECALL_QUESTION.search(message))


def is_undo_request(message: str) -> bool:
    return bool(_UNDO_QUESTION.search(message))


def is_abandon_request(message: str) -> bool:
    return bool(_ABANDON.search(message))


def _describe_receipt(receipt: Mapping[str, Any]) -> str:
    action = str(receipt.get("action") or "")
    raw_result = receipt.get("result")
    result: Mapping[str, Any] = raw_result if isinstance(raw_result, Mapping) else {}
    raw_target = receipt.get("target")
    target: Mapping[str, Any] = raw_target if isinstance(raw_target, Mapping) else {}
    status = str(receipt.get("status") or "")
    key = str(result.get("key") or "")
    title = str(result.get("title") or "")
    count = int(receipt.get("affectedCount") or receipt.get("affected_count") or 0)

    if status == "failed":
        return "an attempt that failed, so nothing changed"
    if action == "operations.follow_up.create":
        label = f"{key} — {title}" if key and title else key or title or "a follow-up"
        return f"created the follow-up {label}"
    if action == "operations.work_item.update":
        label = key or str(target.get("resourceId") or "the work item")
        status_after = str(result.get("status") or "")
        suffix = f" to {status_after.replace('_', ' ')}" if status_after else ""
        return f"updated {label}{suffix}"
    if action == "operations.cohort.create_follow_ups":
        return f"created {count} follow-up{'s' if count != 1 else ''} across the group"
    if action == "communications.email.prepare":
        return (
            "prepared the email for a final send review — it has not been sent, and it "
            "will not be until you confirm the send"
        )
    if action == "student.preferences.update":
        changes = result.get("changes")
        if isinstance(changes, Sequence) and changes:
            parts = [
                f"your {_humanize_field(str(item.get('field')))} to {item.get('after')}"
                for item in changes
                if isinstance(item, Mapping)
            ]
            if parts:
                return f"changed {', and '.join(parts)}"
        return "updated your profile"
    if action == "student.support.contact":
        return "opened a support request — someone from the team picks it up from there"
    if action == "student.requirement.submit_response":
        return "recorded your response against that step"
    return f"completed {action}"


def _humanize_field(name: str) -> str:
    return {
        "preferredName": "preferred name",
        "pronouns": "pronouns",
        "mobilePhone": "mobile number",
        "communicationPreference": "contact preference",
    }.get(name, name)


def recall_answer(
    receipts: Sequence[Mapping[str, Any]],
    *,
    pending: Sequence[Mapping[str, Any]] = (),
) -> JsonDict | None:
    """Answer "did that go through?" from server-issued receipts only.

    A receipt is the one artefact that proves a side effect happened, so it is
    also the only thing allowed to make Edward say it did. A pending intent
    proves the opposite, and saying so is just as important: "thanks" is not a
    confirmation, and a person who walks away believing a task exists is worse
    off than one who was told nothing.
    """

    committed = [item for item in receipts if str(item.get("status")) in {"succeeded", "partial"}]
    if committed:
        described = [_describe_receipt(item) for item in committed[-3:]]
        # "Has it gone out?" answered with "Yes — I prepared the email" is a
        # contradiction the reader has to unpick. Preparing is the one action
        # whose success is still a "not yet" to the question people ask about
        # it, so it never opens with "Yes".
        prepared_only = all(
            str(item.get("action")) == "communications.email.prepare" for item in committed
        )
        opener = "Not yet — I" if prepared_only else "Yes — I"
        body = f"{opener} {', then '.join(described)}."
        return _answer(body, code="recall")
    failed = [item for item in receipts if str(item.get("status")) == "failed"]
    if failed:
        return _answer(
            "No. I tried, and it failed before anything was written, so the record is "
            "unchanged. Ask again and I'll build a fresh preview.",
            code="recall",
        )
    if pending:
        return _answer(
            "Not yet — it's still waiting on your confirmation. Nothing has been written, "
            "and nothing will be until you confirm the preview.",
            code="recall",
        )
    return _answer(
        "Nothing — I haven't changed anything in this conversation.",
        code="recall",
    )


def undo_answer(receipts: Sequence[Mapping[str, Any]]) -> JsonDict | None:
    """There is no undo. Say so, and name the real remedy."""

    committed = [item for item in receipts if str(item.get("status")) in {"succeeded", "partial"}]
    if not committed:
        return _answer(
            "There's nothing to undo — I haven't changed anything in this conversation.",
            code="undo",
        )
    last = committed[-1]
    action = str(last.get("action") or "")
    raw_result = last.get("result")
    result: Mapping[str, Any] = raw_result if isinstance(raw_result, Mapping) else {}
    key = str(result.get("key") or "")
    if action == "operations.cohort.create_follow_ups":
        count = int(last.get("affectedCount") or 0)
        many = f"the {count} follow-ups" if count else "the follow-ups"
        return _answer(
            f"I can't undo a batch once it's committed; the record keeps what happened and "
            f"who did it. The remedy is cancelling {many} that were created — each one "
            "individually, from the Action Center, or ask me to cancel one by its key.",
            code="undo",
        )
    if action == "operations.follow_up.create":
        label = f" — {key}" if key else ""
        return _answer(
            f"I can't undo a change once it's committed; the record keeps what happened and "
            f"who did it. What I can do is cancel the work item that was created{label}. Say "
            '"cancel it" and I\'ll prepare that change for you to confirm.',
            code="undo",
        )
    if action == "operations.work_item.update":
        label = key or "the item"
        return _answer(
            f"I can't undo a committed change. I can move {label} back — tell me which "
            "status it should return to and I'll preview it.",
            code="undo",
        )
    if action == "student.preferences.update":
        return _answer(
            "I can't undo a committed change, but I can change it back — tell me the value "
            "you want and I'll preview it.",
            code="undo",
        )
    if action == "communications.email.prepare":
        return _answer(
            "Nothing has been sent, so there's nothing to undo. The prepared message is "
            "waiting on the send confirmation; leave it unconfirmed and it expires.",
            code="undo",
        )
    return _answer(
        "I can't undo a committed change — the record keeps what happened. Tell me the "
        "state you want and I'll prepare that as a new change.",
        code="undo",
    )


def abandon_answer(pending: Sequence[Mapping[str, Any]]) -> JsonDict | None:
    """Acknowledge an abandoned proposal, so silence never reads as consent."""

    if not pending:
        return None
    return _answer(
        "Dropped — I won't create it. Nothing was written.",
        code="abandoned",
    )


__all__ = [
    "abandon_answer",
    "boundary",
    "capability_answer",
    "clarification",
    "denial",
    "injection_refusal",
    "is_abandon_request",
    "is_capability_question",
    "is_recall_question",
    "is_undo_request",
    "recall_answer",
    "undo_answer",
]
