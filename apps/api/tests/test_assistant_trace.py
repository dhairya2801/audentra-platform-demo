"""Per-turn assistant trace: pipeline capture, sanitization, and debug routes."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from audentra.integrations.assistant.pipeline import AssistantPipeline
from audentra.integrations.assistant.tools import AssistantToolHost
from audentra.integrations.assistant.trace import (
    AssistantTraceRecorder,
    AssistantTurnTrace,
    get_assistant_trace_recorder,
    sanitize_trace_value,
)
from audentra.interfaces.http.app import create_app
from audentra.interfaces.http.config import HttpSettings

WORKER_TOKEN = "local-development-document-worker-token"  # noqa: S105


def _host(primitives: dict[str, dict[str, Any]]) -> AssistantToolHost:
    def reader(value: dict[str, Any]) -> Any:
        async def read() -> dict[str, Any]:
            return value

        return read

    return AssistantToolHost({name: reader(value) for name, value in primitives.items()})


def _primitives() -> dict[str, dict[str, Any]]:
    return {
        "profile": {"preferredName": "Alex", "email": "alex@example.edu"},
        "requirements": {
            "items": [
                {
                    "id": "req-1",
                    "code": "final_transcript",
                    "slug": "final-transcript",
                    "title": "Final transcript",
                    "status": "ready",
                    "blocking": True,
                    "dueAt": "2026-08-20T00:00:00Z",
                    "documentCategory": "transcript",
                }
            ]
        },
        "documents": {"items": []},
        "dashboard": {"offer": {"depositAmountCents": 50_000, "depositPaid": False}},
        "financials": {"requiredDocuments": [], "awards": []},
    }


def test_pipeline_records_stages_tools_and_deterministic_response() -> None:
    trace = AssistantTurnTrace(trace_id="req-1", tenant_id="t", student_id="s")
    pipeline = AssistantPipeline(_host(_primitives()))
    result = asyncio.run(pipeline.execute(message="What do I still need to do?", trace=trace))

    assert result.message
    payload = trace.to_dict()
    assert payload["traceId"] == "req-1"
    assert payload["classification"]["requestType"] == "remaining_steps"
    assert payload["toolSelectionSource"] == "deterministic"
    assert payload["selectedTools"] == ["getOnboardingChecklist"]
    tool_call = payload["toolCalls"][0]
    assert tool_call["tool"] == "getOnboardingChecklist"
    assert tool_call["status"] == "available"
    assert tool_call["recordCount"] == 1
    assert isinstance(tool_call["durationMs"], int)
    # The coverage gate runs on every classified turn and records what it saw,
    # so "the route already answered everything" is observable rather than
    # inferred from the absence of a stage. It changed nothing here:
    # `toolSelectionSource` is still deterministic and the reads are unchanged.
    assert [stage["stage"] for stage in payload["stages"]] == [
        "normalize",
        "coverage_gate",
        "classify_and_plan",
        "execute_tool_reads",
        "derive_student_state",
        "compose_deterministic",
        "model_rewrite",
    ]
    gate_stage = next(s for s in payload["stages"] if s["stage"] == "coverage_gate")
    assert gate_stage.get("supplements") is None
    assert gate_stage.get("droppedDomains") is None
    assert payload["responseSource"] == "deterministic"
    assert payload["modelIterations"] == 0
    assert payload["finalMessage"] == result.message
    assert isinstance(payload["durationMs"], int)


def test_pipeline_records_accepted_model_rewrite_with_usage() -> None:
    async def composer(**_kwargs: Any) -> dict[str, Any]:
        return {
            "answer": "Your final transcript is the one item left to send.",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "usage": {"promptTokens": 100, "completionTokens": 20, "totalTokens": 120},
        }

    trace = AssistantTurnTrace(trace_id="req-2")
    pipeline = AssistantPipeline(_host(_primitives()), model_composer=composer)
    result = asyncio.run(pipeline.execute(message="what's left on my checklist?", trace=trace))

    assert "transcript" in result.message.lower()
    payload = trace.to_dict()
    call = payload["modelCalls"][0]
    assert call["operation"] == "assistant_composer"
    assert call["outcome"] == "accepted"
    assert call["provider"] == "openai"
    assert call["usage"]["totalTokens"] == 120
    assert payload["responseSource"] == "model_prose"
    assert payload["provider"] == "openai"


def test_pipeline_records_guard_rejection_reason() -> None:
    async def composer(**_kwargs: Any) -> dict[str, Any]:
        # Invents a date that no evidence supports, so the guard must refuse.
        return {"answer": "Everything is due on 2031-01-31.", "provider": "openai"}

    trace = AssistantTurnTrace(trace_id="req-3")
    pipeline = AssistantPipeline(_host(_primitives()), model_composer=composer)
    asyncio.run(pipeline.execute(message="what's left on my checklist?", trace=trace))

    payload = trace.to_dict()
    call = payload["modelCalls"][0]
    assert call["outcome"] == "guard_rejected"
    assert call["detail"] == "ungrounded_date"
    assert payload["responseSource"] == "deterministic"
    assert any(code.startswith("written_answer_rejected:") for code in payload["failureCodes"])


def test_sanitizer_redacts_contact_keys_and_bounds_values() -> None:
    sanitized = sanitize_trace_value(
        {
            "email": "student@example.edu",
            "supportPhone": "555-123-4567",
            "apiKey": "sk-secret",
            "title": "x" * 700,
            "items": list(range(40)),
        }
    )
    assert sanitized["email"] == "[redacted]"
    assert sanitized["supportPhone"] == "[redacted]"
    assert sanitized["apiKey"] == "[redacted]"
    assert len(sanitized["title"]) <= 601
    assert len(sanitized["items"]) == 25  # 24 items plus the truncation marker
    assert sanitized["items"][-1] == "… 16 more"


def test_recorder_ring_buffer_get_and_list() -> None:
    recorder = AssistantTraceRecorder(buffer_size=2)
    for index in range(3):
        recorder.record(AssistantTurnTrace(trace_id=f"req-{index}"))

    assert recorder.get("req-0") is None  # evicted by the bounded buffer
    assert recorder.get("req-2") is not None
    summaries = recorder.list()
    assert [item["traceId"] for item in summaries] == ["req-2", "req-1"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def debug_client() -> AsyncIterator[AsyncClient]:
    app = create_app(settings=HttpSettings(assistant_trace_debug_enabled=True))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
async def test_debug_routes_return_recorded_trace(debug_client: AsyncClient) -> None:
    trace = AssistantTurnTrace(trace_id="trace-http-1", tenant_id="t", student_id="s")
    trace.user_message = "why can't I register?"
    get_assistant_trace_recorder().record(trace)

    headers = {"X-VV-Worker-Token": WORKER_TOKEN}
    listing = await debug_client.get("/internal/assistant/traces", headers=headers)
    assert listing.status_code == 200
    assert any(item["traceId"] == "trace-http-1" for item in listing.json()["traces"])

    detail = await debug_client.get("/internal/assistant/traces/trace-http-1", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["userMessage"] == "why can't I register?"

    missing = await debug_client.get("/internal/assistant/traces/not-a-trace", headers=headers)
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_debug_routes_require_worker_token(debug_client: AsyncClient) -> None:
    response = await debug_client.get("/internal/assistant/traces")
    assert response.status_code == 403


@pytest.mark.anyio
async def test_debug_routes_hidden_when_disabled() -> None:
    app = create_app(settings=HttpSettings(assistant_trace_debug_enabled=False))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/internal/assistant/traces", headers={"X-VV-Worker-Token": WORKER_TOKEN}
        )
    assert response.status_code == 404


def test_trace_captures_evidence_sentences() -> None:
    trace = AssistantTurnTrace(trace_id="ev-1")
    pipeline = AssistantPipeline(_host(_primitives()))
    asyncio.run(pipeline.execute(message="What do I still need to do?", trace=trace))
    payload = trace.to_dict()
    assert payload["evidence"], "the composer's evidence corpus must be traced"
    assert any("Final transcript" in line for line in payload["evidence"])


@pytest.fixture
async def persona_client() -> AsyncIterator[AsyncClient]:
    from audentra.application.platform_service import InMemoryPlatformService

    app = create_app(
        service=InMemoryPlatformService(),
        settings=HttpSettings(assistant_trace_debug_enabled=True),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.anyio
async def test_dev_persona_endpoints_list_and_switch(persona_client: AsyncClient) -> None:
    headers = {"X-VV-Worker-Token": WORKER_TOKEN}
    listing = await persona_client.get("/internal/assistant/dev/personas", headers=headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["supported"] is True
    assert body["active"] is None
    names = {item["name"] for item in body["personas"]}
    assert {"new_admit", "transcript_under_review", "payment_pending"} <= names
    assert all(item["label"] for item in body["personas"])

    switched = await persona_client.post(
        "/internal/assistant/dev/personas/transcript_under_review", headers=headers
    )
    assert switched.status_code == 200
    assert switched.json()["active"] == "transcript_under_review"

    relisted = await persona_client.get("/internal/assistant/dev/personas", headers=headers)
    assert relisted.json()["active"] == "transcript_under_review"

    missing = await persona_client.post(
        "/internal/assistant/dev/personas/not-a-persona", headers=headers
    )
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_dev_persona_switch_resets_conversations(persona_client: AsyncClient) -> None:
    headers = {"X-VV-Worker-Token": WORKER_TOKEN}
    created = await persona_client.post(
        "/v1/student/assistant/conversations",
        json={"pageContext": {"path": "/edward", "label": "Edward"}},
    )
    conversation_id = created.json()["id"]

    await persona_client.post("/internal/assistant/dev/personas/deposit_posted", headers=headers)
    # The old conversation belongs to the previous student state and is gone.
    messages = await persona_client.get(
        f"/v1/student/assistant/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == 404


@pytest.mark.anyio
async def test_dev_persona_endpoints_require_worker_token_and_debug_flag(
    persona_client: AsyncClient,
) -> None:
    no_token = await persona_client.get("/internal/assistant/dev/personas")
    assert no_token.status_code == 403

    disabled_app = create_app(settings=HttpSettings(assistant_trace_debug_enabled=False))
    async with AsyncClient(
        transport=ASGITransport(app=disabled_app), base_url="http://testserver"
    ) as disabled:
        hidden = await disabled.get(
            "/internal/assistant/dev/personas",
            headers={"X-VV-Worker-Token": WORKER_TOKEN},
        )
    assert hidden.status_code == 404


def test_trace_clock_can_be_anchored_to_earlier_work() -> None:
    import time

    turn_started = time.perf_counter() - 0.25
    trace = AssistantTurnTrace(trace_id="req-anchored")
    trace.started_from(turn_started)
    trace.finalize()
    # Work that ran before the trace existed (recognition, prefilters) counts.
    assert trace.duration_ms is not None and trace.duration_ms >= 240


def test_trace_history_preview_is_bounded_and_serialized() -> None:
    trace = AssistantTurnTrace(trace_id="req-history")
    trace.set_history_preview(
        [{"role": "user", "content": "x" * 1_000}, {"role": "assistant", "content": "short"}],
        characters=40,
    )
    payload = trace.to_dict()
    assert payload["historyPreview"][0]["role"] == "user"
    assert payload["historyPreview"][0]["content"].endswith("…")
    assert len(payload["historyPreview"][0]["content"]) == 41
    assert payload["historyPreview"][1]["content"] == "short"


def test_recorder_list_carries_the_read_planner() -> None:
    recorder = AssistantTraceRecorder(buffer_size=4)
    trace = AssistantTurnTrace(trace_id="req-planner")
    trace.read_planner = "hybrid"
    recorder.record(trace)
    assert recorder.list()[0]["readPlanner"] == "hybrid"


@pytest.mark.anyio
async def test_dev_tools_endpoint_serves_both_catalogues(debug_client: AsyncClient) -> None:
    headers = {"X-VV-Worker-Token": WORKER_TOKEN}
    response = await debug_client.get("/internal/assistant/dev/tools", headers=headers)
    assert response.status_code == 200
    body = response.json()
    student = {tool["name"]: tool for tool in body["student"]}
    staff = {tool["name"]: tool for tool in body["staff"]}
    assert "getOnboardingChecklist" in student
    assert student["getOnboardingChecklist"]["arguments"] is None
    assert staff["getStaffWorkQueue"]["informationClass"] == "operational_state"
    assert "status" in staff["getStaffWorkQueue"]["arguments"]
    assert staff["searchStudents"]["description"]

    forbidden = await debug_client.get("/internal/assistant/dev/tools")
    assert forbidden.status_code == 403
