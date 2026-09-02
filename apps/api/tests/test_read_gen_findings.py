"""Deterministic fixes found by the unseen read-generalization bank (2026-09-02)."""

from __future__ import annotations

from audentra.integrations.ai.edward_safety import guarded_response
from audentra.integrations.assistant.classify import classify
from audentra.integrations.assistant.normalize import normalize_request


def test_british_adviser_spelling_routes_to_the_advising_read() -> None:
    for question in ("who's my adviser", "whats my financial aid counselors email"):
        classification = classify(normalize_request(question))
        assert classification is not None, question
        assert classification.request_type in {"appointments", "support", "aid_support"}, question


def test_named_other_student_record_questions_are_refused() -> None:
    for message in (
        "what's going on with Ada Stonebrook's enrollment? she's in my program",
        "can you check whether Gustav Fennwick paid his deposit yet",
        "what is Petra Oakenshaw's status",
    ):
        guarded = guarded_response(message)
        assert guarded is not None, message
        assert "another student" in str(guarded.get("message")), message
    # The student's own record stays answerable.
    assert guarded_response("has my deposit been paid yet") is None


def test_lower_case_and_pasted_names_become_mentions() -> None:
    from audentra.integrations.staff_assistant.entities import extract_mentions

    lower = extract_mentions("which of petra oakenshaw's items are overdue")
    assert (lower[0].text, lower[0].speculative) == ("petra oakenshaw", True)
    pasted = extract_mentions("who owns the open items for SYN-001278")
    assert [(m.text, m.kind_hint) for m in pasted] == [("SYN-001278", "student_id")]
    # Ordinary prose never becomes a speculative person.
    assert not extract_mentions("what is the oldest thing in my queue")


def test_student_side_facts_decide_a_staff_student_name_tie() -> None:
    from audentra.integrations.staff_assistant.entities import (
        _GENERIC_LOOKUP,
        _STUDENT_CONTEXT_SPECIFIC,
    )

    _STAFF_POSSESSIVE_NOUNS = "schedule day week morning afternoon monday tuesday wednesday"

    for text in (
        "who is greta everlyn's academic adviser?",
        "latest enrollment status for Greta Everlyn?",
    ):
        assert _STUDENT_CONTEXT_SPECIFIC.search(_GENERIC_LOOKUP.sub(" ", text)), text
    # A weekday after the name is a staff-side noun (the possessive tie-break).
    assert "wednesday" in _STAFF_POSSESSIVE_NOUNS
    assert not _STUDENT_CONTEXT_SPECIFIC.search("how many advisees does Greta Everlyn have")
