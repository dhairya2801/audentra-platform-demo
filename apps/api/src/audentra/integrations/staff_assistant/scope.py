"""Turn scope: what the *current* staff turn is about, before any history.

The conversational bug this module exists to remove: a durable conversation
that has resolved a student once must not keep answering about that student.
The old rule was "if the intent needs a student and none was named, use the
conversation's active referent" — with `is_follow_up` true for any short
message, that turned every later question into a question about the same
student, and the durable `active_student_id` column was never cleared.

The rule now:

1. Parse the current turn on its own. Its *scope* comes from the intent it
   classifies to, plus explicit scope language in the turn itself.
2. Only a **student-scoped** turn may inherit a referent at all. A cohort,
   queue, ranking, institution, or conversational turn never does.
3. A student-scoped turn inherits only when it supplies no entity of its own
   **and** its language actually refers back — a pronoun, a demonstrative, a
   continuation opener, or a bare topical fragment ("What about housing?").
4. The referent has a lifecycle: a turn that resolves a student sets it, a
   turn that is explicitly not about a student clears it, and everything else
   leaves it alone. Inheritance is evidence, never a standing scope.

Nothing here talks to a model. Scope is a property of the sentence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from audentra.integrations.staff_assistant.normalize import NormalizedStaffRequest

# --- Scope of each classified intent ---------------------------------------

STUDENT_SCOPE = "student"
COHORT_SCOPE = "cohort"
QUEUE_SCOPE = "queue"
RANKING_SCOPE = "ranking"
INSTITUTION_SCOPE = "institution"
CONVERSATIONAL_SCOPE = "conversational"
# A turn about a person on staff, the signed-in member, a team or a
# department. Never inherits a *student* referent; may inherit a staff one.
STAFF_SCOPE = "staff"

REQUEST_TYPE_SCOPE: Mapping[str, str] = {
    "greeting": CONVERSATIONAL_SCOPE,
    "capability_overview": CONVERSATIONAL_SCOPE,
    "action_request": CONVERSATIONAL_SCOPE,
    "unsupported_metric": CONVERSATIONAL_SCOPE,
    "unsupported_or_out_of_scope": CONVERSATIONAL_SCOPE,
    "general_question": CONVERSATIONAL_SCOPE,
    "draft_email": STUDENT_SCOPE,
    "draft_sms": STUDENT_SCOPE,
    "draft_call_points": STUDENT_SCOPE,
    "student_overview": STUDENT_SCOPE,
    "student_missing_items": STUDENT_SCOPE,
    "student_blockers": STUDENT_SCOPE,
    "student_documents": STUDENT_SCOPE,
    "student_deadlines": STUDENT_SCOPE,
    "student_financials": STUDENT_SCOPE,
    "student_housing": STUDENT_SCOPE,
    "student_appointments": STUDENT_SCOPE,
    "student_communications": STUDENT_SCOPE,
    "student_engagement": STUDENT_SCOPE,
    "student_timeline": STUDENT_SCOPE,
    "student_ownership": STUDENT_SCOPE,
    "student_action_center": STUDENT_SCOPE,
    "recommendation": STUDENT_SCOPE,
    "cohort_search": COHORT_SCOPE,
    "cohort_aggregate": COHORT_SCOPE,
    "attention_ranking": RANKING_SCOPE,
    "work_queue": QUEUE_SCOPE,
    "work_item_detail": QUEUE_SCOPE,
    "daily_briefing": QUEUE_SCOPE,
    "inquiries": QUEUE_SCOPE,
    "playbook_lookup": INSTITUTION_SCOPE,
    "action_rules": INSTITUTION_SCOPE,
    "staff_profile": STAFF_SCOPE,
    "staff_workload": STAFF_SCOPE,
    "staff_availability": STAFF_SCOPE,
    "staff_caseload": STAFF_SCOPE,
    "staff_appointments": STAFF_SCOPE,
    "staff_comparison": STAFF_SCOPE,
    "my_work": STAFF_SCOPE,
    "my_profile": STAFF_SCOPE,
    "team_overview": STAFF_SCOPE,
    "department_operations": STAFF_SCOPE,
    "queue_aggregate": QUEUE_SCOPE,
    "inquiry_aggregate": QUEUE_SCOPE,
    "staff_directory": STAFF_SCOPE,
    "not_found": CONVERSATIONAL_SCOPE,
}

# --- Language that decides scope on the turn itself -------------------------

# A singular third-person referent: "she", "this student", "that case".
# Deliberately excludes bare "they/them" when the sentence also has a plural
# subject, because "how many students do they cover?" refers to the items,
# not to a person.
_SINGULAR_PRONOUN = re.compile(
    r"\b(?:she|he|her|him|hers|his)\b|\b(?:this|that|the) (?:student|case|applicant|person)\b",
    re.IGNORECASE,
)
_PLURAL_PRONOUN = re.compile(r"\b(?:they|them|their|theirs)\b", re.IGNORECASE)
_PLURAL_SUBJECT = re.compile(
    r"\b(?:students?|items?|cases?|tasks?|applicants?|admits?|records?|programs?"
    r"|deadlines?|documents?|blockers?|requirements?)\b",
    re.IGNORECASE,
)

# Team / institution scope stated in the turn: this can never be one student.
GLOBAL_SCOPE_LANGUAGE = re.compile(
    r"\bmy (?:team|group|office|unit|caseload|queue|board|plate|desk)\b"
    r"|\bthe (?:team|office|whole class|entire class|cohort|roster|institution|university)\b"
    r"|\bacross (?:the )?(?:class|cohort|programs?|institution|university|board|everyone)\b"
    r"|\b(?:overall|institution[- ]wide|university[- ]wide|tenant[- ]wide)\b"
    r"|\b(?:all|every|most|which|how many) (?:of (?:our|the) )?(?:students|applicants|admits)\b"
    r"|\bwe (?:should|need to) (?:care|worry|focus)\b"
    r"|\b(?:action cent(?:er|re)|work queue|task board)\b",
    re.IGNORECASE,
)

# A continuation that plausibly refers back to the previous turn's entity.
_CONTINUATION_OPENER = re.compile(
    r"^(?:and\b|also\b|so\b|then\b|what about\b|how about\b|now\b|next\b|ok(?:ay)?[,.]?\s"
    r"|plus\b)",
    re.IGNORECASE,
)
# A bare topical fragment with no verb of its own ("Her housing?", "Blockers?").
_BARE_FRAGMENT = re.compile(r"^[a-z][\w' -]{0,40}\??$", re.IGNORECASE)

# How short a mid-conversation turn has to be to read as a continuation.
CONTINUATION_LENGTH = 60


def has_singular_student_reference(request: NormalizedStaffRequest) -> bool:
    """Does the turn point at exactly one student, by name, id, or pronoun?"""

    if request.candidate_student_name or request.candidate_student_id:
        return True
    if request.reference_token:
        return True
    return uses_singular_anaphora(request)


def uses_singular_anaphora(request: NormalizedStaffRequest) -> bool:
    """A pronoun or demonstrative that stands for one previously named student."""

    text = request.text
    if _SINGULAR_PRONOUN.search(text):
        return True
    # "them"/"their" with no plural noun in the sentence is a singular they,
    # standing for the student named in the previous turn.
    return bool(_PLURAL_PRONOUN.search(text)) and not _PLURAL_SUBJECT.search(text)


def is_globally_scoped(request: NormalizedStaffRequest) -> bool:
    """Turn language that is explicitly about the team, queue, or population."""

    if has_explicit_entity(request):
        return False
    return bool(GLOBAL_SCOPE_LANGUAGE.search(request.text))


def has_explicit_entity(request: NormalizedStaffRequest) -> bool:
    """A student named or pasted in *this* turn."""

    return bool(
        request.candidate_student_name or request.candidate_student_id or request.reference_token
    )


def refers_back(request: NormalizedStaffRequest) -> bool:
    """Does the turn's language plausibly point at the previous turn's entity?

    Anaphora, a continuation opener, or a bare topical fragment — not merely
    "the message is short", which is what made every later question sticky.
    """

    if not request.history:
        return False
    if uses_singular_anaphora(request):
        return True
    text = request.text.strip()
    if _CONTINUATION_OPENER.search(text):
        return True
    if _BARE_FRAGMENT.match(text):
        return True
    # A short turn mid-conversation that names nobody and states no scope of
    # its own ("Draft the email.", "Blockers?") continues the previous one.
    # This is only reached for *student-scoped* intents — the scope gate has
    # already excluded cohort, queue, ranking and institution turns — so it
    # can no longer turn a population question into a question about one
    # student, which is what the old unconditional length test did.
    return len(text) <= CONTINUATION_LENGTH


def scope_of(request_type: str | None) -> str:
    if request_type is None:
        return CONVERSATIONAL_SCOPE
    return REQUEST_TYPE_SCOPE.get(request_type, CONVERSATIONAL_SCOPE)


def may_inherit_referent(request: NormalizedStaffRequest, request_type: str | None) -> bool:
    """The whole inheritance policy, in one place.

    Inherit only when the turn is student-scoped, supplies no entity of its
    own, and actually refers back. A cohort/queue/ranking/institution turn
    never inherits, no matter what the conversation holds.
    """

    if has_explicit_entity(request):
        return False
    if scope_of(request_type) is not STUDENT_SCOPE:
        return False
    if is_globally_scoped(request):
        return False
    return refers_back(request)


def referent_action(*, resolved_student_id: str | None, request_type: str | None) -> str:
    """What the durable conversation referent should do after this turn.

    ``set`` a resolved student, ``clear`` when the turn was explicitly not
    about a student, and ``keep`` for turns that neither resolved nor
    contradicted the referent (a refusal, a greeting, a failed lookup).
    """

    if resolved_student_id is not None:
        return "set"
    scope = scope_of(request_type)
    if scope in (COHORT_SCOPE, QUEUE_SCOPE, RANKING_SCOPE, STAFF_SCOPE):
        # The staff member has moved to population or queue work; keeping a
        # stale student on the conversation is what made the next question
        # snap back to them.
        return "clear"
    return "keep"
