"""Staff Edward: staff identity, entity resolution and staff-scoped routing.

The mock university deliberately gives a staff member and several students
the same full name, and gives staff surnames that hundreds of students share.
These tests pin the resolver's decisions and the classifier's staff-aware
routing with an in-memory directory — no database, no model.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from audentra.integrations.staff_assistant.classify import classify_staff_request
from audentra.integrations.staff_assistant.entities import (
    EntityResolution,
    extract_mentions,
    resolve_entities,
)
from audentra.integrations.staff_assistant.identity import identity_from_profile
from audentra.integrations.staff_assistant.normalize import normalize_staff_request

STAFF: dict[str, dict[str, Any]] = {
    "elena larkspur": {
        "id": "staff-elena",
        "name": "Elena Larkspur",
        "title": "Academic Adviser",
        "component": "Academic Advising",
        "employmentStatus": "active",
        "matchQuality": "exact_name",
    },
    "junia pemberwell": {
        "id": "staff-junia",
        "name": "Junia Pemberwell",
        "title": "Senior Academic Adviser",
        "component": "Academic Advising",
        "employmentStatus": "on_leave",
        "matchQuality": "exact_name",
    },
    "vera": {
        "id": "staff-vera",
        "name": "Vera Jessamy",
        "title": "Transfer Success Adviser",
        "component": "Academic Advising",
        "employmentStatus": "active",
        "matchQuality": "first_name",
    },
}
STUDENTS: dict[str, list[dict[str, Any]]] = {
    "elena larkspur": [
        {"id": f"student-elena-{i}", "name": "Elena Larkspur", "preferredName": "Elena"}
        for i in range(4)
    ],
    "junia pemberwell": [
        {"id": f"student-junia-{i}", "name": "Junia Pemberwell", "preferredName": "Junia"}
        for i in range(3)
    ],
    "tobias": [{"id": "student-tobias", "name": "Tobias Quillfeather", "preferredName": "Toby"}],
    "tobias quillfeather": [
        {"id": "student-tobias", "name": "Tobias Quillfeather", "preferredName": "Toby"}
    ],
    "vera": [
        {"id": f"student-vera-{i}", "name": f"Vera Ashgrove{i}", "preferredName": "Vera"}
        for i in range(5)
    ],
    "caleb dunmire": [
        {"id": f"student-caleb-{i}", "name": "Caleb Dunmire", "preferredName": "Caleb"}
        for i in range(8)
    ],
}
COMPONENTS = [
    "Academic Advising",
    "Registrar",
    "Financial Aid",
    "Housing",
    "International Student Services",
]


async def _read(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any] | None:
    query = str(arguments.get("query") or "").lower()
    if tool == "searchStaff":
        hit = STAFF.get(query)
        return {"items": [hit] if hit else []}
    return {"items": STUDENTS.get(query, []), "matchQuality": "exact"}


async def _resolve(question: str) -> tuple[Any, EntityResolution]:
    request = normalize_staff_request(question)
    entities = await resolve_entities(request, _read, components=COMPONENTS)
    return classify_staff_request(request, entities), entities


@pytest.mark.anyio
async def test_staff_name_with_staff_language_resolves_to_staff_not_roster() -> None:
    classification, entities = await _resolve("How many students does Elena Larkspur advise?")
    assert [e.name for e in entities.staff] == ["Elena Larkspur"]
    assert entities.students == []
    assert classification is not None and classification.request_type == "staff_caseload"


@pytest.mark.anyio
async def test_shared_full_name_with_generic_lookup_is_reported_ambiguous() -> None:
    _classification, entities = await _resolve("Tell me about Elena Larkspur.")
    assert entities.staff == [] and entities.students == []
    assert [a.reason for a in entities.ambiguities] == ["staff_and_student"]


@pytest.mark.anyio
async def test_possessive_staff_noun_breaks_the_tie_towards_staff() -> None:
    classification, entities = await _resolve(
        "List Elena Larkspur's advisees who haven't completed advising and have no upcoming "
        "appointment."
    )
    assert [e.name for e in entities.staff] == ["Elena Larkspur"]
    assert classification is not None
    assert classification.request_type == "staff_caseload"
    assert dict(classification.cohort_filter or {}) == {"advisingStatus": "no_booking"}


@pytest.mark.anyio
async def test_booking_verb_before_a_shared_name_means_the_colleague() -> None:
    classification, entities = await _resolve("Can a student book Junia Pemberwell right now?")
    assert [e.name for e in entities.staff] == ["Junia Pemberwell"]
    assert classification is not None and classification.request_type == "staff_availability"


@pytest.mark.anyio
async def test_first_name_only_student_resolves_when_unique() -> None:
    classification, entities = await _resolve(
        "Draft a short message to Tobias about his missing transcript."
    )
    assert [e.id for e in entities.students] == ["student-tobias"]
    assert classification is not None and classification.request_type == "draft_email"


@pytest.mark.anyio
async def test_first_name_only_staff_with_staff_language() -> None:
    classification, entities = await _resolve("How many students does Vera advise?")
    assert [e.name for e in entities.staff] == ["Vera Jessamy"]
    assert classification is not None and classification.request_type == "staff_caseload"


@pytest.mark.anyio
async def test_duplicate_students_are_a_disambiguation_not_a_guess() -> None:
    _classification, entities = await _resolve("What's going on with Caleb Dunmire?")
    assert entities.students == []
    assert [a.reason for a in entities.ambiguities] == ["several_students"]
    assert len(entities.ambiguities[0].students) == 8


@pytest.mark.anyio
async def test_department_name_routes_to_department_not_roster() -> None:
    classification, entities = await _resolve(
        "Is anyone in Academic Advising on leave or departed?"
    )
    assert [d.name for d in entities.departments] == ["Academic Advising"]
    assert entities.students == [] and entities.staff == []
    assert classification is not None
    assert classification.request_type in {"department_operations", "team_overview"}


@pytest.mark.anyio
async def test_compound_count_does_not_narrow_the_first_ask() -> None:
    classification, _ = await _resolve(
        "How many open items does International Student Services have, and how many are overdue?"
    )
    assert classification is not None and classification.request_type == "queue_aggregate"
    assert dict(classification.cohort_filter or {}) == {
        "component": "International Student Services"
    }


@pytest.mark.anyio
async def test_two_departments_compare_by_component() -> None:
    classification, _ = await _resolve(
        "Which has more overdue items, the Registrar or Financial Aid?"
    )
    assert classification is not None and classification.request_type == "queue_aggregate"
    filters = dict(classification.cohort_filter or {})
    assert filters["groupBy"] == "component" and filters["dueWindow"] == "overdue"
    assert classification.reference == "compare:Registrar|Financial Aid"


@pytest.mark.anyio
async def test_tenant_wide_most_overdue_is_a_grouped_queue_count() -> None:
    classification, _ = await _resolve("Which staff members have the most overdue work?")
    assert classification is not None and classification.request_type == "queue_aggregate"
    assert dict(classification.cohort_filter or {}) == {
        "dueWindow": "overdue",
        "groupBy": "assignee",
    }


@pytest.mark.anyio
async def test_self_reference_routes_to_my_work_with_filters() -> None:
    classification, entities = await _resolve("Show me the overdue items assigned to me.")
    assert entities.self_reference
    assert classification is not None and classification.request_type == "my_work"
    assert dict(classification.cohort_filter or {}).get("dueWindow") == "overdue"


@pytest.mark.anyio
async def test_inquiry_count_never_counts_the_roster() -> None:
    classification, _ = await _resolve(
        "How many student requests are still awaiting a first reply?"
    )
    assert classification is not None and classification.request_type == "inquiry_aggregate"
    assert dict(classification.cohort_filter or {}).get("status") == "awaiting_first_reply"


@pytest.mark.anyio
async def test_unrecognised_student_qualifier_defers_instead_of_counting_everyone() -> None:
    classification, _ = await _resolve("How many students does she have?")
    assert classification is None


@pytest.mark.anyio
async def test_operational_follow_up_inherits_the_previous_scope() -> None:
    request = normalize_staff_request(
        "How many of those are overdue?",
        history=[
            {"role": "user", "content": "How many open items do I have?"},
            {"role": "assistant", "content": "You have 12 open items."},
        ],
    )
    entities = await resolve_entities(request, _read, components=COMPONENTS)
    classification = classify_staff_request(request, entities)
    assert classification is not None and classification.request_type == "queue_aggregate"
    filters = dict(classification.cohort_filter or {})
    assert filters == {"ownership": "mine", "dueWindow": "overdue"}


def test_mention_extraction_skips_product_and_department_phrases() -> None:
    mentions = extract_mentions("How many transcript items are open in the Action Center?")
    assert mentions == []
    mentions = extract_mentions(
        "Which of Vera Jessamy's work items have been in progress for more than a week?"
    )
    assert [(m.text, m.possessive) for m in mentions] == [("Vera Jessamy", True)]


def test_identity_from_profile_reads_role_and_caseload() -> None:
    identity = identity_from_profile(
        {
            "id": "staff-1",
            "name": "Leandro Hartigan",
            "title": "Director of Academic Advising",
            "roleCode": "director",
            "component": "Academic Advising",
            "employmentStatus": "active",
            "manager": {"name": "Amara Abernathy"},
            "directReports": 10,
            "caseload": {"primaryAdvisees": 0, "cap": None},
            "work": {"open": 21, "overdue": 17},
        }
    )
    assert identity is not None
    assert identity.is_manager and not identity.is_adviser
    assert "Leandro Hartigan" in identity.describe() and "10 direct report" in identity.describe()


@pytest.mark.parametrize(
    "label", ["Morning Brew", "Task Board", "Action Center", "Student Accounts"]
)
@pytest.mark.parametrize(
    "question",
    [
        "Which students have an open financial aid verification, and what is each waiting on?",
        "Which deposited students have not registered for orientation?",
    ],
)
def test_navigation_prefix_is_not_a_student_in_either_resolution_path(
    label: str, question: str
) -> None:
    request = normalize_staff_request(
        f"This question comes from the {label} demo. Its displayed figures are not institutional "
        f"evidence. Answer using canonical university records only.\n\n{question}"
    )
    assert request.candidate_student_name is None
    assert all(
        mention.text != label
        for mention in extract_mentions(request.text)
        if mention.kind_hint == "person"
    )
