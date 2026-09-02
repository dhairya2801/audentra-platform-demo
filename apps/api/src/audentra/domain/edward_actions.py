"""Closed, semantic action vocabulary for Edward.

This module understands *requests*. It does not resolve IDs, authorize an
actor, or claim that anything happened. Those decisions belong to the
server-side action gateway after canonical reads.

It is now a façade over two modules that separate concerns V1 had fused:

* `edward_action_catalog` — what the seven actions are, which fields they
  accept, which values those fields may take, and what to say when something
  is out of reach. One description, read by the recognizer, the responder and
  the gateway, so they cannot disagree about what Edward can do.
* `edward_action_recognizer` — how a sentence becomes one of those requests,
  deterministically where the phrasing is unambiguous and through a bounded,
  schema-constrained model call where it is not.

Importers keep the V1 surface: `parse_student_action`, `parse_staff_action`,
`SemanticActionRequest`, `ActionName` and `canonical_digest` mean exactly what
they meant before.
"""

from __future__ import annotations

from audentra.domain.edward_action_catalog import (
    ACTIONS,
    BROAD_STUDENT_CAPABILITY,
    BY_NAME,
    CAPABILITY_BY_ACTION,
    STAFF_MASTER_CAPABILITY,
    ActionDefinition,
    ActionField,
    ActionName,
    ActorKind,
    Boundary,
    actions_for,
    asks_edward_to_act,
    boundary_answer,
    boundary_for,
    coerce_fields,
    describe_capabilities,
    missing_required,
    preference_question,
)
from audentra.domain.edward_action_recognizer import (
    RECOGNIZER_SYSTEM_PROMPT,
    SemanticActionRequest,
    amend_pending_action,
    canonical_digest,
    has_question_sentence,
    looks_like_a_change_request,
    named_day,
    parse_staff_action,
    parse_student_action,
    preference_fields_incomplete,
    question_sentences,
    recognize_with_model,
    recognizer_catalog,
    recognizer_schema,
    repeats_action_for_another,
    untrusted_action_framing,
)


def _contains_untrusted_action_instructions(message: str) -> bool:
    """Retained V1 name: fail closed on quoted content or a control bypass."""

    return untrusted_action_framing(message) is not None


__all__ = [
    "ACTIONS",
    "BROAD_STUDENT_CAPABILITY",
    "BY_NAME",
    "CAPABILITY_BY_ACTION",
    "RECOGNIZER_SYSTEM_PROMPT",
    "STAFF_MASTER_CAPABILITY",
    "ActionDefinition",
    "ActionField",
    "ActionName",
    "ActorKind",
    "Boundary",
    "SemanticActionRequest",
    "actions_for",
    "amend_pending_action",
    "asks_edward_to_act",
    "boundary_answer",
    "boundary_for",
    "canonical_digest",
    "coerce_fields",
    "describe_capabilities",
    "has_question_sentence",
    "looks_like_a_change_request",
    "missing_required",
    "named_day",
    "parse_staff_action",
    "parse_student_action",
    "preference_fields_incomplete",
    "preference_question",
    "question_sentences",
    "recognize_with_model",
    "recognizer_catalog",
    "recognizer_schema",
    "repeats_action_for_another",
    "untrusted_action_framing",
]
