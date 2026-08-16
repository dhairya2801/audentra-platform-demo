"""Tests for the EXPERIMENTAL full-request coverage gate.

First the failure mode itself: at default settings a confident deterministic
classification covers only the first intent of a compound question, and the
second ask silently disappears from routing. Then the gate: with
`coverage_gate="augment"` (or the planner mode) every material ask is routed,
while single-intent fast paths, context mentions, follow-ups, and safety
refusals stay byte-identical to default routing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from test_assistant_pipeline import RecordingHost, _full_primitives

from audentra.integrations.assistant.classify import classify
from audentra.integrations.assistant.coverage import (
    COVERAGE_GATE_ENV_FLAG,
    CoverageAssessment,
    assess_request_coverage,
    resolve_coverage_gate_mode,
)
from audentra.integrations.assistant.normalize import normalize_request
from audentra.integrations.assistant.pipeline import AssistantPipeline, AssistantPipelineResult
from audentra.integrations.assistant.trace import AssistantTurnTrace

COMPOUND_HOUSING_DOCS = (
    "I paid my deposit. Why can't I apply for housing and what documents am I missing?"
)
COMPOUND_BALANCE_CLUBS = "What's my account balance and what clubs can I join?"


def _run(
    message: str,
    *,
    coverage_gate: str | None = None,
    model_planner: Any = None,
    history: list[dict[str, str]] | None = None,
) -> tuple[AssistantPipelineResult, AssistantTurnTrace]:
    host = RecordingHost(_full_primitives())
    pipeline = AssistantPipeline(host, model_planner=model_planner, coverage_gate=coverage_gate)
    trace = AssistantTurnTrace(trace_id="t-1")
    result = asyncio.run(pipeline.execute(message=message, history=history or [], trace=trace))
    return result, trace


def _assess(message: str, history: list[dict[str, str]] | None = None) -> CoverageAssessment:
    request = normalize_request(message, history=history or [])
    classification = classify(request)
    assert classification is not None, message
    return assess_request_coverage(request, classification)


# --- The failure mode, pinned exactly as it ships today -----------------------


def test_default_routing_drops_the_documents_ask_from_a_compound_question() -> None:
    result, trace = _run(COMPOUND_HOUSING_DOCS)

    assert result.classification is not None
    assert result.classification.request_type == "housing_eligibility"
    # The documents ask is not routed: no document intent, no planned
    # documents read. (A dependency round may still fetch documents to explain
    # a gate, but nothing in the route answers the question that was asked.)
    assert not any("document" in extra for extra in result.classification.additional_request_types)
    assert "getDocumentStatuses" not in trace.selected_tools


def test_default_routing_drops_the_balance_ask_entirely() -> None:
    result, _trace = _run(COMPOUND_BALANCE_CLUBS)

    assert result.classification is not None
    assert result.classification.request_type == "campus_life"
    assert result.classification.additional_request_types == ()
    sources = {receipt["source"] for receipt in result.context_receipts}
    assert "account" not in sources
    # The confident-but-incomplete answer: clubs are answered, the balance the
    # student asked about is never read and never stated.
    assert "$" not in result.message


# --- Recovery with the augment gate ------------------------------------------


def test_augment_gate_recovers_the_documents_ask() -> None:
    result, trace = _run(COMPOUND_HOUSING_DOCS, coverage_gate="augment")

    assert result.classification is not None
    assert result.classification.source == "coverage_gate"
    assert "missing_documents" in result.classification.additional_request_types
    assert "getDocumentStatuses" in trace.selected_tools
    gate_stages = [s for s in trace.stages if s["stage"] == "coverage_gate"]
    assert gate_stages and gate_stages[0]["action"] == "augmented"
    assert gate_stages[0]["uncoveredDomains"] == ["documents"]


def test_augment_gate_recovers_the_balance_ask_and_answers_it() -> None:
    result, _ = _run(COMPOUND_BALANCE_CLUBS, coverage_gate="augment")

    assert result.classification is not None
    assert "student_account" in result.classification.additional_request_types
    sources = {receipt["source"] for receipt in result.context_receipts}
    assert {"campus_life", "account"} <= sources
    # Both asks answered: the fixture's balance figure and its club.
    assert "$16,000" in result.message
    assert "club" in result.message.lower()


def test_augment_gate_makes_no_model_calls() -> None:
    async def exploding_planner(**_: Any) -> None:
        raise AssertionError("the augment gate must never call a model")

    result, trace = _run(
        COMPOUND_BALANCE_CLUBS, coverage_gate="augment", model_planner=exploding_planner
    )
    assert result.classification is not None
    assert "student_account" in result.classification.additional_request_types
    assert trace.model_calls == []


# --- Fast paths and context mentions stay untouched ---------------------------


def test_gate_is_silent_on_covered_and_conversational_requests() -> None:
    cases: list[tuple[str, list[dict[str, str]]]] = [
        ("What is my transcript status?", []),
        # Deposit is context in a declarative sentence, not a second ask —
        # the existing single-intent route is complete and must not widen.
        ("I paid my deposit. Why can't I apply for housing?", []),
        ("Why can't I register even though I paid?", []),
        ("What about housing?", [{"role": "user", "content": "am I on track?"}]),
        ("Am I all set?", []),
        ("Hi Edward!", []),
    ]
    for message, history in cases:
        default_result, default_trace = _run(message, history=history)
        gated_result, gated_trace = _run(message, coverage_gate="augment", history=history)
        assert gated_trace.selected_tools == default_trace.selected_tools, message
        assert gated_result.classification == default_result.classification, message
        assert gated_result.message == default_result.message, message


def test_gate_never_widens_a_write_refusal() -> None:
    message = "Please upload my transcript for me and pay my housing deposit."
    result, trace = _run(message, coverage_gate="augment")
    assert result.classification is not None
    assert result.classification.request_type == "unsupported_or_out_of_scope"
    assert trace.selected_tools == []


# --- Assessment unit behaviour ------------------------------------------------


def test_context_clause_inside_a_question_is_not_an_ask() -> None:
    assessment = _assess("Why can't I register even though I paid my deposit?")
    assert assessment.ask_domains == ("registration",)
    assert assessment.uncovered_domains == ()


def test_supplements_keep_per_intent_precision() -> None:
    # The second segment classifies as missing_documents, not a generic
    # document read — structured intent extraction without a model.
    assessment = _assess(COMPOUND_HOUSING_DOCS)
    assert assessment.supplements == ("missing_documents",)


def test_supplement_budget_is_capped_and_drops_are_recorded() -> None:
    assessment = _assess(
        "What's my balance, what clubs can I join, what courses am I taking, "
        "and did my transcript arrive?"
    )
    assert len(assessment.supplements) == 2
    assert assessment.dropped_domains != ()


# --- Planner escalation mode --------------------------------------------------


def _valid_compound_plan() -> dict[str, Any]:
    return {
        "requestType": "housing_eligibility",
        "additionalRequestTypes": ["missing_documents"],
        "confidence": 0.9,
        "requirementReference": None,
        "toolNames": [
            "getStudentHousingEligibility",
            "getOnboardingChecklist",
            "getDocumentStatuses",
        ],
    }


def test_planner_mode_uses_a_covering_plan() -> None:
    calls: list[str] = []

    async def planner(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["message"])
        return _valid_compound_plan()

    result, trace = _run(COMPOUND_HOUSING_DOCS, coverage_gate="planner", model_planner=planner)
    assert calls == [COMPOUND_HOUSING_DOCS]
    assert result.classification is not None
    assert result.classification.source == "model_plan"
    assert "getDocumentStatuses" in trace.selected_tools
    gate_stages = [s for s in trace.stages if s["stage"] == "coverage_gate"]
    assert gate_stages and gate_stages[0]["action"] == "planner_plan_accepted"


def test_planner_mode_is_not_called_without_a_coverage_gap() -> None:
    calls: list[str] = []

    async def planner(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["message"])
        return _valid_compound_plan()

    _run("What is my transcript status?", coverage_gate="planner", model_planner=planner)
    assert calls == []


def test_planner_mode_falls_back_to_augment_when_the_plan_does_not_cover() -> None:
    async def planner(**_: Any) -> dict[str, Any]:
        # Validates fine, but reads nothing that answers the documents ask.
        return {
            "requestType": "housing_eligibility",
            "additionalRequestTypes": [],
            "confidence": 0.9,
            "requirementReference": None,
            "toolNames": ["getStudentHousingEligibility"],
        }

    result, trace = _run(COMPOUND_HOUSING_DOCS, coverage_gate="planner", model_planner=planner)
    assert result.classification is not None
    assert result.classification.source == "coverage_gate"
    assert "missing_documents" in result.classification.additional_request_types
    gate_stages = [s for s in trace.stages if s["stage"] == "coverage_gate"]
    assert gate_stages and gate_stages[0]["action"] == "augmented"


def test_planner_mode_survives_a_planner_crash() -> None:
    async def planner(**_: Any) -> dict[str, Any]:
        raise RuntimeError("provider down")

    result, _ = _run(COMPOUND_HOUSING_DOCS, coverage_gate="planner", model_planner=planner)
    assert result.classification is not None
    assert "missing_documents" in result.classification.additional_request_types
    assert "planner_model_failure" in result.failure_codes


def test_planner_mode_without_a_planner_still_augments() -> None:
    result, _ = _run(COMPOUND_HOUSING_DOCS, coverage_gate="planner")
    assert result.classification is not None
    assert "missing_documents" in result.classification.additional_request_types


# --- Activation is opt-in only ------------------------------------------------


def test_gate_is_off_by_default_and_env_flag_enables_it(monkeypatch: Any) -> None:
    monkeypatch.delenv(COVERAGE_GATE_ENV_FLAG, raising=False)
    assert resolve_coverage_gate_mode(None) == "off"
    assert resolve_coverage_gate_mode("augment") == "augment"
    assert resolve_coverage_gate_mode("nonsense") == "off"

    monkeypatch.setenv(COVERAGE_GATE_ENV_FLAG, "augment")
    assert resolve_coverage_gate_mode(None) == "augment"
    # An explicit argument always beats the environment.
    assert resolve_coverage_gate_mode("off") == "off"

    result, _ = _run(COMPOUND_BALANCE_CLUBS)  # env flag set by monkeypatch
    assert result.classification is not None
    assert "student_account" in result.classification.additional_request_types


def test_default_pipeline_ignores_the_gate_entirely(monkeypatch: Any) -> None:
    monkeypatch.delenv(COVERAGE_GATE_ENV_FLAG, raising=False)
    result, trace = _run(COMPOUND_BALANCE_CLUBS)
    assert result.classification is not None
    assert result.classification.additional_request_types == ()
    assert all(stage["stage"] != "coverage_gate" for stage in trace.stages)
