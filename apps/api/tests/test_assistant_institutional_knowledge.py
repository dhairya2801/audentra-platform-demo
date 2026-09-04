"""Institutional knowledge inside Edward: routing, evidence, composition.

The corpus is a read like any other: it enters a turn through the planner
(policy questions), through the policy augment (institutional language on a
record question), or through the model read loop. These tests pin the
deterministic parts — when the read is selected, what the evidence says, what
the deterministic composer renders — without a database or a model.
"""

from __future__ import annotations

import asyncio
from typing import Any

from audentra.integrations.assistant.classify import Classification
from audentra.integrations.assistant.compose import (
    _compose_policy_lookup,
    institutional_evidence_lines,
)
from audentra.integrations.assistant.derive import DerivedState
from audentra.integrations.assistant.pipeline import (
    _POLICY_MARKERS,
    KNOWLEDGE_TOOL,
    AssistantPipeline,
)
from audentra.integrations.assistant.planner import select_tool_reads
from audentra.integrations.assistant.read_loop import _parse_arguments
from audentra.integrations.assistant.tools import AssistantToolHost, PrimitiveRead
from audentra.integrations.staff_assistant.catalog import (
    STAFF_TOOL_NAMES,
    ToolArgumentError,
    validate_tool_arguments,
)
from audentra.integrations.staff_assistant.classify import (
    StaffClassification,
    classify_staff_request,
)
from audentra.integrations.staff_assistant.normalize import normalize_staff_request
from audentra.integrations.staff_assistant.planner import select_staff_tools

KNOWLEDGE_RESULT: dict[str, Any] = {
    "query": "what happens if I miss the deposit deadline",
    "documents": [
        {
            "code": "enrollment-deposit-policy",
            "kind": "policy",
            "title": "Enrollment Deposit — amount, deadline, refund and forfeiture",
            "summary": "The $500 deposit reserves a place.",
            "version": "2026.2",
            "effectiveFrom": "2026-04-01",
            "effectiveUntil": None,
            "audience": "student",
            "owner": {
                "code": "ADM",
                "name": "Office of Admissions",
                "shortName": "Admissions",
                "location": "Larkin Hall 100",
                "email": "admissions@synthetic.aster.example",
                "hours": "Mon-Fri 8:30-17:00",
                "known": True,
                "staffRecordsInPlatform": True,
            },
            "applicability": {
                "verdict": "applies",
                "facets": [],
                "basis": "applies to every student",
            },
            "matchedKeywords": ["deposit deadline"],
            "sections": [
                {
                    "heading": "Missing the deposit deadline",
                    "text": (
                        "A student who misses it keeps the offer, but the housing "
                        "application stays closed."
                    ),
                }
            ],
            "related": [],
        }
    ],
    "calendar": [
        {
            "code": "fall2026-tuition-due",
            "label": "Fall tuition due",
            "startsOn": "2026-08-28",
            "relativeToToday": "7 day(s) ago — this date has passed",
            "ownerOffice": "SA",
        }
    ],
    "offices": [],
    "studentFacets": {"residency": "domestic", "basis": {"residency": "domestic"}},
    "totalMatches": 1,
}


class KnowledgeHost(AssistantToolHost):
    """A host with the institutional-knowledge primitive and a minimal record."""

    def __init__(self, *, with_knowledge: bool = True) -> None:
        self.queries: list[str] = []

        def reader(value: dict[str, Any]) -> PrimitiveRead:
            async def read() -> dict[str, Any]:
                return value

            return read

        primitives: dict[str, Any] = {
            "profile": reader({"preferredName": "Petra"}),
            "requirements": reader(
                {
                    "items": [
                        {
                            "id": "req-1",
                            "code": "enrollment_deposit",
                            "title": "Pay your enrollment deposit",
                            "status": "ready",
                            "blocking": True,
                            "dueAt": "2026-08-20T00:00:00Z",
                        }
                    ]
                }
            ),
            "documents": reader({"items": []}),
            "payments": reader({"items": []}),
            "dashboard": reader({"nextAction": None}),
            "onboarding": reader({"status": "in_progress"}),
        }
        if with_knowledge:

            async def knowledge(query: str) -> dict[str, Any]:
                self.queries.append(query)
                return dict(KNOWLEDGE_RESULT, query=query)

            primitives["institution_knowledge"] = knowledge
        super().__init__(primitives)


def _run(pipeline: AssistantPipeline, message: str) -> Any:
    return asyncio.run(pipeline.execute(message=message))


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_policy_lookup_reads_the_corpus_and_enough_record_to_apply_it() -> None:
    selected = select_tool_reads(Classification("policy_lookup", 0.9))
    assert selected[0] == KNOWLEDGE_TOOL
    assert "getEnrollmentState" in selected


def test_policy_markers_cover_consequence_permission_amount_and_ownership_language() -> None:
    for text in (
        "what happens if I miss the deposit deadline?",
        "can freshmen live off campus?",
        "am I allowed to work on campus on an F-1?",
        "how much is tuition for me",
        "who do I contact about a hold",
        "if I don't pay my deposit will I lose my housing spot?",
        "does the residency rule apply to me",
    ):
        assert _POLICY_MARKERS.search(text), text
    for text in ("what's my balance", "show my checklist", "hi edward", "did my transcript arrive"):
        assert not _POLICY_MARKERS.search(text), text


def test_record_question_with_institutional_language_adds_the_corpus_read() -> None:
    host = KnowledgeHost()
    result = _run(AssistantPipeline(host), "what happens if I miss the deposit deadline?")
    assert host.queries == ["what happens if I miss the deposit deadline?"]
    assert result.derived is not None and result.derived.institution_knowledge is not None
    assert KNOWLEDGE_TOOL in result.derived.available_reads


def test_plain_status_question_does_not_read_the_corpus() -> None:
    host = KnowledgeHost()
    _run(AssistantPipeline(host), "what's still open on my checklist?")
    assert host.queries == []


def test_host_without_a_corpus_is_never_asked() -> None:
    host = KnowledgeHost(with_knowledge=False)
    result = _run(AssistantPipeline(host), "what happens if I miss the deposit deadline?")
    assert KNOWLEDGE_TOOL not in (result.derived.available_reads if result.derived else [])
    assert not any(
        item.get("source") == KNOWLEDGE_TOOL
        for item in (result.derived.unavailable_data if result.derived else [])
    )


def test_policy_question_answers_from_the_document_with_provenance() -> None:
    host = KnowledgeHost()
    result = _run(AssistantPipeline(host), "what is the policy on deposit refunds?")
    assert result.classification is not None
    assert result.classification.request_type == "policy_lookup"
    assert "Enrollment Deposit" in result.message
    assert "version 2026.2" in result.message
    assert "Office of Admissions" in result.message


# ---------------------------------------------------------------------------
# Evidence and composition
# ---------------------------------------------------------------------------


def test_evidence_lines_carry_provenance_applicability_and_calendar() -> None:
    lines = institutional_evidence_lines(KNOWLEDGE_RESULT)
    joined = "\n".join(lines)
    assert "code enrollment-deposit-policy" in joined
    assert "version 2026.2" in joined and "effective from 2026-04-01" in joined
    assert "Office of Admissions, Larkin Hall 100, admissions@synthetic.aster.example" in joined
    assert "APPLIES to this student" in joined
    assert "Policy text — Enrollment Deposit" in joined
    assert "Academic calendar (all terms): Fall tuition due — 2026-08-28" in joined
    assert "cite the document by name" in joined


def test_evidence_says_so_when_nothing_matched() -> None:
    lines = institutional_evidence_lines({"documents": [], "calendar": [], "offices": []})
    assert any("no approved policy" in line for line in lines)


def test_deterministic_policy_composer_falls_back_honestly_without_documents() -> None:
    state = DerivedState()
    answer = _compose_policy_lookup(Classification("policy_lookup", 0.9), state, None)
    assert "approved sources" in answer.message
    state.institution_knowledge = {"documents": []}
    answer = _compose_policy_lookup(Classification("policy_lookup", 0.9), state, None)
    assert "couldn't find" in answer.message


def test_deterministic_policy_composer_states_non_applicability() -> None:
    state = DerivedState()
    document = dict(KNOWLEDGE_RESULT["documents"][0])
    document["applicability"] = {"verdict": "does_not_apply", "facets": [{}], "basis": "transfer"}
    state.institution_knowledge = {**KNOWLEDGE_RESULT, "documents": [document]}
    answer = _compose_policy_lookup(Classification("policy_lookup", 0.9), state, None)
    assert "does not apply to you" in answer.message


# ---------------------------------------------------------------------------
# Read loop argument shapes
# ---------------------------------------------------------------------------


def test_loop_accepts_a_bare_query_pair_as_the_single_argument() -> None:
    assert _parse_arguments('{"query": "deposit refund"}') == {"query": "deposit refund"}
    assert _parse_arguments("query: can I work on campus?") == {"query": "can I work on campus?"}
    assert _parse_arguments("not an argument at all") is None


# ---------------------------------------------------------------------------
# Staff side
# ---------------------------------------------------------------------------


def _staff(text: str) -> StaffClassification | None:
    return classify_staff_request(normalize_staff_request(text))


def test_staff_institutional_questions_route_to_the_corpus() -> None:
    for text in (
        "what is the policy on deposit extensions?",
        "what's the SLA for financial aid document review?",
        "how long does verification review take?",
        "who covers advisees when an adviser is on leave?",
        "when is the add/drop deadline?",
    ):
        classification = _staff(text)
        assert classification is not None and classification.request_type == "playbook_lookup", text
    policy = _staff("what is the policy on deposit extensions?")
    assert policy is not None
    selected = select_staff_tools(policy, student_resolved=False)
    assert selected[0] == "searchInstitutionalKnowledge"


def test_staff_sla_compliance_is_still_an_unsupported_metric() -> None:
    classification = _staff("are we meeting our SLA on transcript reviews this week?")
    assert classification is not None and classification.request_type == "unsupported_metric"


def test_staff_knowledge_tool_arguments_are_validated() -> None:
    assert "searchInstitutionalKnowledge" in STAFF_TOOL_NAMES
    cleaned = validate_tool_arguments(
        "searchInstitutionalKnowledge", {"query": "deposit extension", "limit": 3}
    )
    assert cleaned == {"query": "deposit extension", "limit": 3}
    try:
        validate_tool_arguments(
            "searchInstitutionalKnowledge", {"query": "x", "studentName": "Milo"}
        )
    except ToolArgumentError as error:
        assert error.code == "unknown_argument"
    else:  # pragma: no cover - the assertion above is the test
        raise AssertionError("unknown arguments must be rejected")
