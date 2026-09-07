"""Model-family generation parameters and strict-schema validity.

Two production defects motivated these tests (found 2026-09-02):

- Both assistant planner schemas violated OpenAI's strict-mode rule that
  ``required`` lists every key in ``properties`` (the student schema left
  ``facet`` out of ``required``; the staff schema required a ``facet`` it never
  declared). The provider answered HTTP 400 to every planner call, so the
  model planner never ran and every fallback turn took the safe route.
- The gpt-5.x family rejects ``max_tokens`` and a non-default ``temperature``,
  so a bare model swap failed every assistant operation outright.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import httpx

from audentra.domain.edward_action_catalog import actions_for
from audentra.domain.edward_action_recognizer import (
    RECOGNIZER_SYSTEM_PROMPT,
    recognizer_catalog,
    recognizer_schema,
)
from audentra.integrations.ai.gateway import GatewaySettings, StudentAIGateway
from audentra.integrations.ai.provider import CompletionClient
from audentra.integrations.assistant.classify import REQUEST_TYPES
from audentra.integrations.assistant.planner import TOOL_DESCRIPTIONS
from audentra.integrations.staff_assistant.catalog import STAFF_TOOL_DESCRIPTIONS
from audentra.integrations.staff_assistant.classify import STAFF_REQUEST_TYPES


def _payload(content: object, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps(content)},
            }
        ],
    }


def _gateway(
    settings: GatewaySettings, content: object
) -> tuple[StudentAIGateway, list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        bodies.append(body)
        return httpx.Response(200, json=_payload(content, body["model"]), request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return StudentAIGateway(settings, CompletionClient(http)), bodies


def _assert_strict(schema: Mapping[str, Any], path: str = "$") -> None:
    """OpenAI strict mode: every object lists all its properties as required
    and forbids additional properties, recursively."""

    if schema.get("type") == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        assert required == set(properties), (
            f"{path}: required {sorted(required)} != properties {sorted(properties)}"
        )
        assert schema.get("additionalProperties") is False, f"{path}: additionalProperties"
        for name, child in properties.items():
            _assert_strict(child, f"{path}.{name}")
    if "items" in schema and isinstance(schema["items"], Mapping):
        _assert_strict(schema["items"], f"{path}[]")


def _run_all_operations(settings: GatewaySettings) -> list[dict[str, Any]]:
    gateway, bodies = _gateway(
        settings,
        {
            "answer": "ok",
            "requestType": "greeting",
            "facet": None,
            "additionalRequestTypes": [],
            "confidence": 0.9,
            "requirementReference": None,
            "toolNames": [],
            "toolCalls": [],
            "action": None,
            "fields": {},
        },
    )
    asyncio.run(
        gateway.write_grounded_answer(question="q", evidence_texts=["fact"], draft_answer="draft")
    )
    asyncio.run(
        gateway.plan_assistant_tool_reads(
            message="m",
            allowed_request_types=REQUEST_TYPES,
            available_tools=TOOL_DESCRIPTIONS,
        )
    )
    asyncio.run(
        gateway.write_staff_grounded_answer(
            question="q", evidence_texts=["fact"], draft_answer="draft"
        )
    )
    asyncio.run(
        gateway.plan_staff_tool_reads(
            message="m",
            allowed_request_types=STAFF_REQUEST_TYPES,
            available_tools=STAFF_TOOL_DESCRIPTIONS,
        )
    )
    available = actions_for("staff", ["edward.act", "edward.follow_up.create"])
    asyncio.run(
        gateway.recognize_edward_action(
            message="m",
            system_prompt=RECOGNIZER_SYSTEM_PROMPT,
            catalog=recognizer_catalog(available),
            schema=recognizer_schema(available),
        )
    )
    assert len(bodies) == 5
    return bodies


def test_every_assistant_schema_is_valid_under_openai_strict_mode() -> None:
    bodies = _run_all_operations(GatewaySettings(openai_api_key="k", openai_model="gpt-4o-mini"))
    for body in bodies:
        response_format = body["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        _assert_strict(response_format["json_schema"]["schema"])


def test_classic_chat_models_keep_temperature_and_max_tokens() -> None:
    for body in _run_all_operations(
        GatewaySettings(openai_api_key="k", openai_model="gpt-4o-mini")
    ):
        assert "max_tokens" in body and "temperature" in body
        assert "max_completion_tokens" not in body and "reasoning_effort" not in body


def test_reasoning_models_get_family_specific_parameters() -> None:
    settings = GatewaySettings(
        openai_api_key="k",
        openai_model="gpt-5.6-luna",
        reasoning_effort="low",
        reasoning_effort_overrides="edward_action_recognizer=none, assistant_composer=medium",
    )
    bodies = _run_all_operations(settings)
    by_operation = {body["response_format"]["json_schema"]["name"]: body for body in bodies}
    for body in bodies:
        assert "temperature" not in body and "max_tokens" not in body
        assert isinstance(body["max_completion_tokens"], int)
        assert body["reasoning_effort"] in {"none", "low", "medium", "high", "xhigh"}
    assert by_operation["edward_action_request"]["reasoning_effort"] == "none"
    assert by_operation["student_assistant_written_answer"]["reasoning_effort"] == "medium"
    assert by_operation["student_assistant_tool_plan"]["reasoning_effort"] == "low"
    # Thinking is billed against the same completion limit as the answer, so
    # an effort above "none" widens the budget instead of truncating the JSON.
    assert by_operation["student_assistant_tool_plan"]["max_completion_tokens"] > 520
    assert by_operation["edward_action_request"]["max_completion_tokens"] == 220


def test_unknown_effort_values_fall_back_to_none() -> None:
    settings = GatewaySettings(
        openai_api_key="k", openai_model="gpt-5.6-luna", reasoning_effort="max"
    )
    for body in _run_all_operations(settings):
        assert body["reasoning_effort"] == "none"
