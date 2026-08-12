"""The assistant model layer: provider selection, planning, and written prose.

Parity intents from VV_Edgent-voice: an OPENAI_API_KEY short-circuits
OpenRouter for every assistant chat operation (`openrouter.js` chatProvider),
the planner's untrusted output is validated and filtered rather than trusted
(`model.ts` validateModelToolPlan), model prose is re-checked by the claim
guard with a deterministic floor, and conversational turns never reach a
model at all.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from audentra.integrations.ai.gateway import (
    ASSISTANT_ANSWER_SYSTEM_PROMPT,
    ASSISTANT_TOOL_PLANNING_SYSTEM_PROMPT,
    GatewaySettings,
    StudentAIGateway,
)
from audentra.integrations.ai.provider import CompletionClient
from audentra.integrations.assistant.classify import REQUEST_TYPES
from audentra.integrations.assistant.pipeline import AssistantPipeline
from audentra.integrations.assistant.planner import TOOL_DESCRIPTIONS
from audentra.integrations.assistant.tools import AssistantToolHost


def _chat_payload(content: object, model: str = "gpt-4o-mini") -> dict[str, Any]:
    return {
        "model": model,
        "usage": {"prompt_tokens": 41, "completion_tokens": 13, "total_tokens": 54},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": content if isinstance(content, str) else json.dumps(content),
                },
            }
        ],
    }


def _gateway_with_capture(
    settings: GatewaySettings, content: object
) -> tuple[StudentAIGateway, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=_chat_payload(content), request=request)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return StudentAIGateway(settings, CompletionClient(http)), captured


# --- provider selection -----------------------------------------------------


def test_writer_prefers_direct_openai_over_openrouter() -> None:
    gateway, captured = _gateway_with_capture(
        GatewaySettings(
            openai_api_key="openai-key",
            openai_model="gpt-4o-mini",
            openrouter_api_key="openrouter-key",
        ),
        {"answer": "Your FAFSA has been received."},
    )
    result = asyncio.run(
        gateway.write_grounded_answer(
            question="Has my FAFSA been received?",
            evidence_texts=["Aid requirement FAFSA: status received"],
            draft_answer="FAFSA: received.",
        )
    )
    assert result is not None
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer openai-key"
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-4o-mini"
    assert result["answer"] == "Your FAFSA has been received."
    assert result["usage"] == {"promptTokens": 41, "completionTokens": 13, "totalTokens": 54}
    # api.openai.com rejects unknown body fields; the OpenRouter routing hint
    # must never be sent there.
    assert "provider" not in captured["body"]
    assert captured["body"]["response_format"]["json_schema"]["strict"] is True


def test_writer_falls_back_to_openrouter_without_openai_key() -> None:
    gateway, captured = _gateway_with_capture(
        GatewaySettings(openrouter_api_key="openrouter-key"),
        {"answer": "Grounded."},
    )
    result = asyncio.run(
        gateway.write_grounded_answer(
            question="q",
            evidence_texts=["fact"],
            draft_answer="draft",
        )
    )
    assert result is not None
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert result["provider"] == "openrouter"
    assert captured["body"]["provider"] == {"require_parameters": True}


def test_writer_returns_none_without_any_chat_key() -> None:
    gateway, _ = _gateway_with_capture(GatewaySettings(), {"answer": "never"})
    result = asyncio.run(
        gateway.write_grounded_answer(question="q", evidence_texts=["fact"], draft_answer="d")
    )
    assert result is None


def test_writer_uses_the_ported_answer_prompt() -> None:
    gateway, captured = _gateway_with_capture(GatewaySettings(openai_api_key="k"), {"answer": "ok"})
    asyncio.run(
        gateway.write_grounded_answer(question="q", evidence_texts=["fact"], draft_answer="d")
    )
    system_prompt = captured["body"]["messages"][0]["content"]
    assert system_prompt == ASSISTANT_ANSWER_SYSTEM_PROMPT
    assert "Ground every claim in the supplied verified facts" in system_prompt
    user_content = captured["body"]["messages"][1]["content"]
    assert user_content.startswith("<untrusted_student_answer_input>")


# --- planner ------------------------------------------------------------------


def test_planner_emits_bounded_schema_and_parses_plan() -> None:
    plan = {
        "requestType": "aid_summary",
        "additionalRequestTypes": ["student_account"],
        "confidence": 0.9,
        "requirementReference": None,
        "toolNames": ["getFinancialAidSummary", "getStudentAccountSummary"],
    }
    gateway, captured = _gateway_with_capture(GatewaySettings(openai_api_key="k"), plan)
    result = asyncio.run(
        gateway.plan_assistant_tool_reads(
            message="How much aid do I have and what do I still owe?",
            page_label="Financials",
            page_path="/financials",
            allowed_request_types=REQUEST_TYPES,
            available_tools=TOOL_DESCRIPTIONS,
        )
    )
    assert result == plan
    body = captured["body"]
    assert body["temperature"] == 0.0
    assert body["messages"][0]["content"] == ASSISTANT_TOOL_PLANNING_SYSTEM_PROMPT
    schema = body["response_format"]["json_schema"]
    assert schema["name"] == "student_assistant_tool_plan"
    assert schema["schema"]["properties"]["toolNames"]["items"]["enum"] == sorted(TOOL_DESCRIPTIONS)
    assert schema["schema"]["properties"]["requestType"]["enum"] == list(REQUEST_TYPES)
    user_content = body["messages"][1]["content"]
    assert user_content.startswith("<untrusted_tool_planning_input>")
    assert "getFinancialAidSummary" in user_content


def test_planner_returns_none_without_any_chat_key() -> None:
    gateway, _ = _gateway_with_capture(GatewaySettings(), {})
    result = asyncio.run(
        gateway.plan_assistant_tool_reads(
            message="anything",
            allowed_request_types=REQUEST_TYPES,
            available_tools=TOOL_DESCRIPTIONS,
        )
    )
    assert result is None


# --- pipeline integration -----------------------------------------------------


class RecordingHost(AssistantToolHost):
    def __init__(self, primitives: dict[str, dict[str, Any]]) -> None:
        self.read_primitives: list[str] = []

        def reader(name: str, value: dict[str, Any]):
            async def read() -> dict[str, Any]:
                self.read_primitives.append(name)
                return value

            return read

        super().__init__({name: reader(name, value) for name, value in primitives.items()})


def _minimal_primitives() -> dict[str, dict[str, Any]]:
    return {
        "profile": {"preferredName": "Alex"},
        "requirements": {
            "items": [
                {
                    "id": "req-1",
                    "code": "final_transcript",
                    "slug": "final-transcript",
                    "title": "Final transcript",
                    "status": "ready",
                    "blocking": True,
                    "documentCategory": "transcript",
                }
            ]
        },
        "documents": {"items": []},
        "payments": {"items": []},
        "financials": {
            "academicYear": "2026-2027",
            "costOfAttendanceCents": 3_200_000,
            "acceptedAidCents": 1_500_000,
            "pendingAidCents": 0,
            "paymentsCents": 0,
            "remainingBalanceCents": 1_700_000,
            "awards": [
                {
                    "name": "Aster Grant",
                    "type": "grant",
                    "status": "accepted",
                    "offeredAmountCents": 1_500_000,
                    "acceptedAmountCents": 1_500_000,
                    "requiresAction": False,
                }
            ],
            "requiredDocuments": [],
            "paymentSchedule": [],
        },
        "dashboard": {
            "offer": {"id": "offer-1", "depositAmountCents": 50_000},
            "journey": {"nextAction": None},
        },
    }


class RecordingModel:
    """A fake planner/composer pair that records whether it was consulted."""

    def __init__(
        self,
        plan: dict[str, Any] | None = None,
        answers: list[str] | None = None,
    ) -> None:
        self.plan = plan
        self.answers = list(answers or [])
        self.planner_calls: list[dict[str, Any]] = []
        self.composer_calls: list[dict[str, Any]] = []

    async def planner(self, **kwargs: Any) -> dict[str, Any] | None:
        self.planner_calls.append(kwargs)
        return self.plan

    async def composer(self, **kwargs: Any) -> dict[str, Any] | None:
        self.composer_calls.append(kwargs)
        if not self.answers:
            return None
        return {
            "answer": self.answers.pop(0),
            "provider": "openai",
            "model": "gpt-4o-mini",
            "usage": {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15},
        }


def test_greeting_and_capability_never_reach_the_model() -> None:
    for message in ("Hi", "What can you do?"):
        model = RecordingModel(answers=["should never be used"])
        pipeline = AssistantPipeline(
            RecordingHost(_minimal_primitives()),
            model_composer=model.composer,
            model_planner=model.planner,
        )
        result = asyncio.run(pipeline.execute(message=message))
        assert model.planner_calls == []
        assert model.composer_calls == []
        assert result.provider == "guided"
        assert result.model is None


def test_model_plan_filters_out_of_allowlist_reads() -> None:
    # "aid_summary" allows aid reads; getCampusLife belongs to campus_life and
    # must be dropped from the plan rather than executed or fatal.
    model = RecordingModel(
        plan={
            "requestType": "aid_summary",
            "additionalRequestTypes": [],
            "confidence": 0.9,
            "requirementReference": None,
            "toolNames": ["getFinancialAidSummary", "getCampusLife"],
        }
    )
    host = RecordingHost(_minimal_primitives())
    pipeline = AssistantPipeline(host, model_planner=model.planner)
    # A message the deterministic classifier cannot place, so the planner runs.
    result = asyncio.run(pipeline.execute(message="Walk me through where the money side stands"))
    assert len(model.planner_calls) == 1
    assert result.classification is not None
    assert result.classification.source == "model_plan"
    assert result.classification.request_type == "aid_summary"
    sources = [receipt["source"] for receipt in result.context_receipts]
    assert "financial_aid" in sources
    assert "campus_life" not in sources
    assert "campus_life" not in host.read_primitives


def test_grounded_rewrite_surfaces_provider_model_and_usage() -> None:
    model = RecordingModel(answers=["You have accepted $15,000.00 in aid — the Aster Grant."])
    pipeline = AssistantPipeline(
        RecordingHost(_minimal_primitives()),
        model_composer=model.composer,
    )
    result = asyncio.run(pipeline.execute(message="What financial aid do I have?"))
    assert len(model.composer_calls) == 1
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini"
    assert result.usage == {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15}
    assert result.message == "You have accepted $15,000.00 in aid — the Aster Grant."
    # Structured blocks stay deterministic under model prose.
    assert any(block["type"] != "text" for block in result.blocks)


def test_ungrounded_rewrite_falls_back_to_the_deterministic_answer() -> None:
    model = RecordingModel(answers=["You have accepted $99,999.00 in aid."])
    pipeline = AssistantPipeline(
        RecordingHost(_minimal_primitives()),
        model_composer=model.composer,
    )
    result = asyncio.run(pipeline.execute(message="What financial aid do I have?"))
    assert result.provider == "guided"
    assert result.model is None
    assert "$99,999.00" not in result.message
    assert any(code.startswith("written_answer_rejected") for code in result.failure_codes)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (GatewaySettings(openai_api_key="a", openrouter_api_key="b"), "gpt-4o-mini"),
        (GatewaySettings(openrouter_api_key="b"), "openai/gpt-4o-mini"),
    ],
)
def test_chat_model_selection_follows_provider_order(
    settings: GatewaySettings, expected: str
) -> None:
    gateway = StudentAIGateway(settings, CompletionClient(httpx.AsyncClient()))
    assert gateway._chat_model() == expected
