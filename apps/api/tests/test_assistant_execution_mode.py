"""The Lab's forced zero-LLM execution mode.

Three things have to hold at once for the deterministic/normal comparison to
mean anything:

1. `X-Edward-Mode: deterministic` makes exactly zero provider requests, proven
   at the HTTP transport the gateway would use, not by trusting a stub;
2. the same request without the header still reaches the provider, so the
   comparison is against real current Edward and not a crippled one;
3. the control is development/evaluation only — a production composition
   ignores the header entirely and takes the ordinary path.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from audentra.application.platform_service import InMemoryPlatformService
from audentra.core.assistant_execution import (
    ASSISTANT_EXECUTION_MODE_HEADER,
    DEFAULT_ASSISTANT_EXECUTION,
    AssistantExecutionMode,
    ResolvedAssistantExecutionMode,
    lab_execution_controls_enabled,
    model_hook,
    resolve_assistant_execution_mode,
)
from audentra.core.auth import AuthContext
from audentra.core.errors import BadRequestError
from audentra.core.ports import ServiceCall
from audentra.infrastructure.memory.eval_personas import apply_persona
from audentra.infrastructure.memory.store import DEMO_IDS, InMemoryPlatformStore
from audentra.integrations.ai.gateway import GatewaySettings, StudentAIGateway
from audentra.integrations.ai.provider import CompletionClient
from audentra.integrations.assistant.trace import get_assistant_trace_recorder
from audentra.interfaces.http.app import ALLOWED_HEADERS, create_app
from audentra.interfaces.http.config import HttpSettings

WORKER_TOKEN = "local-development-document-worker-token"  # noqa: S105

# A question with real evidence behind it, so the composer is actually offered
# work in default mode: a turn with no evidence skips the rewrite anyway and
# would prove nothing about suppression.
QUESTION = "What do I still have to do?"
# A canonical evaluation persona with open checklist work, so both modes have
# real records to answer from.
PERSONA = "new_admit"


def _ask_body(message: str = QUESTION) -> dict[str, Any]:
    return {
        "message": message,
        "pageContext": {"path": "/enrollment", "label": "Enrollment"},
    }


class CountingProvider:
    """An httpx transport standing in for every model provider.

    Requests are counted and their URLs recorded, so a test can assert both
    "no call happened" and "the call that happened went where we expected".
    """

    def __init__(self) -> None:
        self.requests: list[str] = []

    def transport(self) -> httpx.MockTransport:
        async def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"answer": "Here is what is left on your checklist."}
                                )
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 40,
                        "total_tokens": 160,
                    },
                },
            )

        return httpx.MockTransport(handle)


def _gateway(provider: CountingProvider) -> StudentAIGateway:
    return StudentAIGateway(
        GatewaySettings(openai_api_key="test-openai-key", openai_model="gpt-4o-mini"),
        CompletionClient(httpx.AsyncClient(transport=provider.transport())),
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _lab_client(
    provider: CountingProvider,
    *,
    environment: str = "development",
    trace_debug: bool = True,
) -> tuple[AsyncClient, InMemoryPlatformService]:
    store = InMemoryPlatformStore()
    apply_persona(store, PERSONA)
    service = InMemoryPlatformService(store=store, ai=_gateway(provider))
    app = create_app(
        service=service,
        settings=HttpSettings(
            environment=environment,  # type: ignore[arg-type]
            assistant_trace_debug_enabled=trace_debug,
        ),
    )
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    return client, service


async def _ask(client: AsyncClient, mode: str | None = None) -> dict[str, Any]:
    headers = {ASSISTANT_EXECUTION_MODE_HEADER: mode} if mode is not None else {}
    response = await client.post(
        "/v1/student/assistant/messages",
        json=_ask_body(),
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _trace(client: AsyncClient, request_id: str) -> dict[str, Any]:
    response = await client.get(
        f"/internal/assistant/traces/{request_id}",
        headers={"X-VV-Worker-Token": WORKER_TOKEN},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


# --- the resolver ---------------------------------------------------------


def test_absent_and_default_headers_resolve_to_production_execution() -> None:
    for raw in (None, "", "  ", "default", "DEFAULT"):
        resolved = resolve_assistant_execution_mode(raw, lab_controls_enabled=True)
        assert resolved.mode is AssistantExecutionMode.DEFAULT
        assert resolved.ignored_request is None
        assert resolved.allows_model_calls is True


def test_deterministic_mode_is_honoured_only_where_lab_controls_are_enabled() -> None:
    enabled = resolve_assistant_execution_mode("deterministic", lab_controls_enabled=True)
    assert enabled.mode is AssistantExecutionMode.DETERMINISTIC
    assert enabled.allows_model_calls is False

    ignored = resolve_assistant_execution_mode("deterministic", lab_controls_enabled=False)
    assert ignored.mode is AssistantExecutionMode.DEFAULT
    assert ignored.ignored_request == "deterministic"
    assert ignored.allows_model_calls is True


def test_unknown_mode_is_rejected_in_the_lab_and_ignored_elsewhere() -> None:
    with pytest.raises(BadRequestError):
        resolve_assistant_execution_mode("experimental_router", lab_controls_enabled=True)

    ignored = resolve_assistant_execution_mode("experimental_router", lab_controls_enabled=False)
    assert ignored.mode is AssistantExecutionMode.DEFAULT
    assert ignored.ignored_request == "experimental_router"


def test_lab_controls_require_both_trace_debug_and_a_non_production_environment() -> None:
    assert lab_execution_controls_enabled(
        environment="development", assistant_trace_debug_enabled=True
    )
    assert lab_execution_controls_enabled(environment="test", assistant_trace_debug_enabled=True)
    assert not lab_execution_controls_enabled(
        environment="development", assistant_trace_debug_enabled=False
    )
    # Even a hand-built production HttpSettings with trace debugging switched
    # on cannot open the control up.
    assert not lab_execution_controls_enabled(
        environment="production", assistant_trace_debug_enabled=True
    )


def test_model_hook_is_the_single_place_a_turn_acquires_a_model() -> None:
    sentinel = object()
    assert model_hook(AssistantExecutionMode.DEFAULT, sentinel) is sentinel
    assert model_hook(AssistantExecutionMode.DETERMINISTIC, sentinel) is None


def test_service_call_defaults_to_production_execution() -> None:
    call = ServiceCall(operation="student.ask_edward", auth=None, request_id="req-1")
    assert call.assistant_execution is DEFAULT_ASSISTANT_EXECUTION
    assert call.assistant_execution.mode is AssistantExecutionMode.DEFAULT


# --- zero provider calls --------------------------------------------------


@pytest.mark.anyio
async def test_deterministic_mode_makes_zero_provider_requests() -> None:
    provider = CountingProvider()
    client, _ = await _lab_client(provider)
    async with client:
        body = await _ask(client, "deterministic")
        trace = await _trace(client, body["requestId"])

    assert provider.requests == [], "deterministic mode must not reach any provider"
    assert trace["executionMode"] == "deterministic"
    assert trace["modelCalls"] == []
    assert trace["modelIterations"] == 0
    assert trace["provider"] == "guided"
    assert trace["model"] is None
    assert trace["usage"] is None
    assert trace["responseSource"] == "deterministic"
    assert body["message"].strip(), "deterministic Edward still has to answer"
    # The deterministic path is still the full pipeline: it read canonical
    # tools and derived evidence, it did not degrade to a refusal.
    assert trace["toolCalls"], "deterministic mode must still execute canonical reads"
    assert trace["evidence"], "deterministic mode must still derive evidence"
    assert trace["toolSelectionSource"] != "model_plan"


@pytest.mark.anyio
async def test_default_mode_still_reaches_the_provider_for_the_same_question() -> None:
    provider = CountingProvider()
    client, _ = await _lab_client(provider)
    async with client:
        body = await _ask(client)
        trace = await _trace(client, body["requestId"])

    assert provider.requests, "default mode must keep its current model behaviour"
    assert all("api.openai.com" in url for url in provider.requests)
    assert trace["executionMode"] == "default"
    assert trace["ignoredExecutionModeRequest"] is None
    assert [call["operation"] for call in trace["modelCalls"]] == ["assistant_composer"]
    assert trace["modelCalls"][0]["usage"]["totalTokens"] == 160


@pytest.mark.anyio
async def test_both_modes_answer_the_same_question_from_the_same_state() -> None:
    """The comparison is only valid if nothing but the mode differs."""

    provider = CountingProvider()
    client, service = await _lab_client(provider)
    async with client:
        normal = await _ask(client, "default")
        deterministic = await _ask(client, "deterministic")
        normal_trace = await _trace(client, normal["requestId"])
        deterministic_trace = await _trace(client, deterministic["requestId"])

    # Conversation-less turns never persist, so the second run sees exactly the
    # state the first one did.
    assert service.store.assistant_messages == []
    assert [call["tool"] for call in normal_trace["toolCalls"]] == [
        call["tool"] for call in deterministic_trace["toolCalls"]
    ]
    assert normal_trace["evidence"] == deterministic_trace["evidence"]
    assert len(provider.requests) == 1, "only the default-mode turn may call a provider"


@pytest.mark.anyio
async def test_staff_deterministic_mode_makes_zero_provider_requests() -> None:
    """Staff Edward suppresses the same two hooks over the same host."""

    provider = CountingProvider()
    store = InMemoryPlatformStore()
    apply_persona(store, PERSONA)
    service = InMemoryPlatformService(store=store, ai=_gateway(provider))
    auth = AuthContext(
        tenant_id=DEMO_IDS["tenant_id"],
        student_id=DEMO_IDS["student_id"],
        actor_id=DEMO_IDS["staff_advisor_id"],
        actor_type="staff",
    )

    async def ask(request_id: str, mode: AssistantExecutionMode) -> None:
        await service.dispatch(
            ServiceCall(
                operation="staff.ask_edward",
                auth=auth,
                request_id=request_id,
                payload={"message": "Why is Alex Morgan blocked?"},
                assistant_execution=ResolvedAssistantExecutionMode(mode),
            )
        )

    await ask("staff-deterministic", AssistantExecutionMode.DETERMINISTIC)
    assert provider.requests == []
    deterministic = get_assistant_trace_recorder().get("staff-deterministic")
    assert deterministic is not None
    assert deterministic["executionMode"] == "deterministic"
    assert deterministic["modelCalls"] == []

    await ask("staff-default", AssistantExecutionMode.DEFAULT)
    assert provider.requests, "staff default mode keeps its current model behaviour"


# --- production safety ----------------------------------------------------


@pytest.mark.anyio
async def test_production_ignores_the_mode_header_and_records_the_attempt() -> None:
    provider = CountingProvider()
    # Trace debugging is force-enabled here precisely to show that the
    # environment check alone is enough to refuse the control.
    client, _ = await _lab_client(provider, environment="production", trace_debug=True)
    async with client:
        body = await _ask(client, "deterministic")
        trace = await _trace(client, body["requestId"])

    assert provider.requests, "a production turn keeps its ordinary model behaviour"
    assert trace["executionMode"] == "default"
    assert trace["ignoredExecutionModeRequest"] == "deterministic"


@pytest.mark.anyio
async def test_production_rejects_nothing_and_leaks_nothing_for_an_unknown_mode() -> None:
    provider = CountingProvider()
    client, _ = await _lab_client(provider, environment="production", trace_debug=False)
    async with client:
        response = await client.post(
            "/v1/student/assistant/messages",
            json=_ask_body(),
            headers={ASSISTANT_EXECUTION_MODE_HEADER: "experimental_router"},
        )
    # No 400, no hint that a mode control exists: the turn simply runs normally.
    assert response.status_code == 200
    assert ASSISTANT_EXECUTION_MODE_HEADER not in {key.lower() for key in response.headers}


@pytest.mark.anyio
async def test_lab_rejects_an_unknown_mode_loudly() -> None:
    provider = CountingProvider()
    client, _ = await _lab_client(provider)
    async with client:
        response = await client.post(
            "/v1/student/assistant/messages",
            json=_ask_body(),
            headers={ASSISTANT_EXECUTION_MODE_HEADER: "experimental_router"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ASSISTANT_EXECUTION_MODE"
    assert provider.requests == []


def test_cors_advertises_the_mode_header_only_where_the_control_is_honoured() -> None:
    assert ASSISTANT_EXECUTION_MODE_HEADER not in [item.lower() for item in ALLOWED_HEADERS]

    def cors_headers(environment: str, trace_debug: bool) -> set[str]:
        app = create_app(
            settings=HttpSettings(
                environment=environment,  # type: ignore[arg-type]
                assistant_trace_debug_enabled=trace_debug,
            )
        )
        middleware = next(
            item
            for item in app.user_middleware
            if getattr(item.cls, "__name__", "") == "CORSMiddleware"
        )
        allowed: Any = middleware.kwargs["allow_headers"]
        return {str(value).lower() for value in list(allowed)}

    assert ASSISTANT_EXECUTION_MODE_HEADER in cors_headers("development", True)
    assert ASSISTANT_EXECUTION_MODE_HEADER not in cors_headers("production", True)
    assert ASSISTANT_EXECUTION_MODE_HEADER not in cors_headers("development", False)


@pytest.mark.anyio
async def test_no_credential_material_reaches_the_lab_surface() -> None:
    """Neither the wire response nor the trace may carry a provider key."""

    provider = CountingProvider()
    client, _ = await _lab_client(provider)
    async with client:
        body = await _ask(client)
        trace = await _trace(client, body["requestId"])

    for payload in (body, trace):
        serialized = json.dumps(payload)
        assert "test-openai-key" not in serialized
        assert "api_key" not in serialized.lower().replace("apikey", "api_key")


# --- the ordinary production path is untouched ----------------------------


class RecordingService:
    def __init__(self) -> None:
        self.calls: list[ServiceCall] = []

    async def dispatch(self, call: ServiceCall) -> object:
        self.calls.append(call)
        if call.operation == "public.get_tenant_bootstrap":
            return {
                "tenantId": "00000000-0000-7000-8000-000000000001",
                "slug": "audentra-lab",
            }
        return {"message": "ok"}


@pytest.fixture
async def recording_client() -> AsyncIterator[tuple[AsyncClient, RecordingService]]:
    service = RecordingService()
    app = create_app(service=service)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client, service


@pytest.mark.anyio
async def test_an_ordinary_request_carries_the_unchanged_default_execution(
    recording_client: tuple[AsyncClient, RecordingService],
) -> None:
    client, service = recording_client
    for path, payload in (
        ("/v1/student/assistant/messages", _ask_body()),
        ("/v1/staff/assistant/messages", {"message": "Find Jordan Lee"}),
    ):
        response = await client.post(path, json=payload, headers={"X-Demo-Actor-Type": "staff"})
        assert response.status_code == 200
        assert service.calls[-1].assistant_execution is DEFAULT_ASSISTANT_EXECUTION

    # Every other operation is dispatched with the same untouched default.
    await client.get("/v1/student/dashboard")
    assert service.calls[-1].assistant_execution is DEFAULT_ASSISTANT_EXECUTION
