"""Staff Edward: authorization boundaries, read-only guarantees, and the
fabricated-data regression net.

These tests run against the in-memory composition (the same store the eval
host uses) plus the FastAPI adapter, so they cover the full request path:
route auth → dispatch → referent resolution → tools → composition.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from audentra.application.platform_service import InMemoryPlatformService
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, NotFoundError
from audentra.core.ports import ServiceCall
from audentra.infrastructure.memory.eval_personas import apply_persona
from audentra.infrastructure.memory.store import DEMO_IDS, InMemoryPlatformStore
from audentra.integrations.assistant.trace import get_assistant_trace_recorder
from audentra.integrations.staff_assistant.catalog import (
    ToolArgumentError,
    validate_tool_arguments,
)
from audentra.integrations.staff_assistant.guard import guard_staff_grounded_answer
from audentra.interfaces.http.app import create_app

STAFF_HEADERS = {
    "x-demo-actor-type": "staff",
    "x-demo-actor-id": DEMO_IDS["staff_advisor_id"],
}
FOREIGN_TENANT_ID = "00000000-0000-7000-8000-00000000feed"
FOREIGN_STUDENT_ID = "00000000-0000-7000-8000-00000000dead"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _service(persona: str = "new_admit") -> InMemoryPlatformService:
    store = InMemoryPlatformStore()
    apply_persona(store, persona)
    return InMemoryPlatformService(store=store)


def _staff_auth(
    *,
    tenant_id: str = DEMO_IDS["tenant_id"],
    actor_id: str = DEMO_IDS["staff_advisor_id"],
) -> AuthContext:
    return AuthContext(
        tenant_id=tenant_id,
        student_id=DEMO_IDS["student_id"],
        actor_id=actor_id,
        actor_type="staff",
    )


def _student_auth() -> AuthContext:
    return AuthContext(
        tenant_id=DEMO_IDS["tenant_id"],
        student_id=DEMO_IDS["student_id"],
        actor_id=DEMO_IDS["student_id"],
        actor_type="student",
    )


async def _ask(
    service: InMemoryPlatformService,
    message: str,
    *,
    auth: AuthContext | None = None,
    conversation_id: str | None = None,
    client_message_id: str | None = None,
    request_id: str = "staff-edward-test",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message}
    if conversation_id is not None:
        payload["conversationId"] = conversation_id
    if client_message_id is not None:
        payload["clientMessageId"] = client_message_id
    result = await service.dispatch(
        ServiceCall(
            operation="staff.ask_edward",
            auth=auth or _staff_auth(),
            payload=payload,
            path_params={},
            query_params={},
            request_id=request_id,
            idempotency_key=None,
            upload=None,
        )
    )
    assert isinstance(result, dict)
    return result


async def _conversation(service: InMemoryPlatformService) -> str:
    created = await service.dispatch(
        ServiceCall(
            operation="staff.create_assistant_conversation",
            auth=_staff_auth(),
            payload={},
            path_params={},
            query_params={},
            request_id="conversation",
            idempotency_key=None,
            upload=None,
        )
    )
    assert isinstance(created, dict)
    return str(created["id"])


@pytest.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app(service=_service()))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ---------------------------------------------------------------------------
# Authorization boundaries
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_endpoint_requires_staff_identity(http_client: AsyncClient) -> None:
    response = await http_client.post("/v1/staff/assistant/messages", json={"message": "hello"})
    assert response.status_code == 401


@pytest.mark.anyio
async def test_endpoint_accepts_staff_identity(http_client: AsyncClient) -> None:
    response = await http_client.post(
        "/v1/staff/assistant/messages",
        json={"message": "What should I work on first today?"},
        headers=STAFF_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["message"]
    assert body["requestId"]


@pytest.mark.anyio
@pytest.mark.parametrize("identity_field", ["tenantId", "staffMemberId", "studentId"])
async def test_endpoint_rejects_client_supplied_identity(
    http_client: AsyncClient, identity_field: str
) -> None:
    response = await http_client.post(
        "/v1/staff/assistant/messages",
        json={
            "message": "Tell me about Alex Morgan",
            identity_field: FOREIGN_STUDENT_ID,
        },
        headers=STAFF_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.anyio
async def test_student_identity_cannot_call_staff_edward(http_client: AsyncClient) -> None:
    # A caller carrying the student demo identity on the staff route is
    # rejected at the auth layer: staff routes demand the staff identity.
    response = await http_client.post(
        "/v1/staff/assistant/messages",
        json={"message": "Tell me about Alex Morgan"},
        headers={"x-demo-actor-type": "student"},
    )
    assert response.status_code == 401
    # And the student endpoint never dispatches the staff operation: the
    # answer is about the signed-in student's own record, with no staff
    # resolution fields.
    student_response = await http_client.post(
        "/v1/student/assistant/messages",
        json={"message": "What should I do next?", "pageContext": "/dashboard"},
    )
    assert student_response.status_code == 200
    assert "resolvedStudent" not in student_response.json()


@pytest.mark.anyio
async def test_student_actor_rejected_by_service() -> None:
    service = _service()
    with pytest.raises(ApiError) as excinfo:
        await _ask(service, "Tell me about Alex Morgan", auth=_student_auth())
    assert excinfo.value.status_code == 403


@pytest.mark.anyio
async def test_cross_tenant_staff_rejected() -> None:
    service = _service()
    with pytest.raises(NotFoundError):
        await _ask(
            service,
            "Tell me about Alex Morgan",
            auth=_staff_auth(tenant_id=FOREIGN_TENANT_ID),
        )


@pytest.mark.anyio
async def test_unknown_staff_actor_rejected() -> None:
    service = _service()
    with pytest.raises(ApiError) as excinfo:
        await _ask(
            service,
            "Tell me about Alex Morgan",
            auth=_staff_auth(actor_id=FOREIGN_STUDENT_ID),
        )
    assert excinfo.value.code == "STAFF_IDENTITY_NOT_CONFIGURED"


@pytest.mark.anyio
async def test_unknown_student_id_is_invisible_not_leaked() -> None:
    """A pasted foreign/unknown student id resolves to an honest not-found."""

    service = _service()
    result = await _ask(service, f"Tell me about {FOREIGN_STUDENT_ID}")
    assert "couldn't find" in result["message"].lower()
    assert result.get("resolvedStudent") is None


@pytest.mark.anyio
async def test_unknown_student_name_is_honest_not_found() -> None:
    service = _service()
    result = await _ask(service, "Tell me about Maria Alvarez")
    assert "couldn't find" in result["message"].lower()


@pytest.mark.anyio
async def test_conversations_do_not_leak_between_staff_members() -> None:
    service = _service()
    conversation_id = await _conversation(service)
    await _ask(service, "Tell me about Alex Morgan", conversation_id=conversation_id)
    other_staff = _staff_auth(actor_id=DEMO_IDS["staff_reviewer_id"])
    recent = service.store.get_recent_staff_assistant_history(other_staff, conversation_id)
    assert recent == {"history": [], "activeStudentId": None}
    with pytest.raises(NotFoundError):
        service.store.get_staff_assistant_conversation_messages(other_staff, conversation_id)


# ---------------------------------------------------------------------------
# Read-only guarantees
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "message",
    [
        "Send Alex the reminder email now.",
        "Assign this case to Marcus.",
        "Escalate this to the director.",
        "Create a task for the transcript follow-up.",
        "Mark his transcript requirement complete.",
        "Schedule an advising appointment for Alex.",
    ],
)
async def test_action_requests_never_mutate(message: str) -> None:
    service = _service()
    snapshot = {
        "work_items": deepcopy(service.store.work_items),
        "payments": deepcopy(service.store.payments),
        "documents": deepcopy(service.store.documents),
        "help_requests": deepcopy(service.store.help_requests),
        "requirements": deepcopy(service.store.requirements),
        "messages": deepcopy(service.store.messages),
    }
    result = await _ask(service, message)
    assert "read-only" in result["message"] or "can't" in result["message"].lower()
    assert service.store.work_items == snapshot["work_items"]
    assert service.store.payments == snapshot["payments"]
    assert service.store.documents == snapshot["documents"]
    assert service.store.help_requests == snapshot["help_requests"]
    assert service.store.requirements == snapshot["requirements"]
    assert service.store.messages == snapshot["messages"]


@pytest.mark.anyio
async def test_draft_is_labelled_and_sends_nothing() -> None:
    service = _service()
    snapshot = deepcopy(service.store.messages)
    result = await _ask(service, "Draft an email to Alex Morgan about his missing transcript.")
    draft_blocks = [block for block in result["blocks"] if block.get("type") == "draft"]
    assert draft_blocks, "a draft block must be returned"
    assert "nothing has been sent" in result["message"].lower()
    assert "transcript" in str(draft_blocks[0].get("subject", "")).lower()
    assert service.store.messages == snapshot


@pytest.mark.anyio
async def test_work_queue_read_is_pure() -> None:
    """Staff Edward's queue read must not trigger the document work-item
    reconciliation write that the portal's action-center GET performs."""

    service = _service()
    service.store.documents.append(
        {
            "id": "00000000-0000-7000-8000-00000000c0de",
            "fileName": "transcript.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": 1024,
            "category": "transcript",
            "status": "needs_review",
            "createdAt": "2026-07-24T12:00:00.000Z",
        }
    )
    before = len(service.store.work_items)
    await _ask(service, "What should I work on first today?")
    assert len(service.store.work_items) == before, "queue read must not create work items"
    # Contrast: the portal action-center read reconciles document work items.
    service.store.get_action_center(_staff_auth())
    assert len(service.store.work_items) == before + 1


# ---------------------------------------------------------------------------
# Fabricated-data regression net
# ---------------------------------------------------------------------------

_FORBIDDEN_TOKENS = (
    # The preview workspace's synthetic vocabulary. Staff Edward code must
    # never reference these fields or that repository.
    "meltLikelihoodPercent",
    "recoveryLikelihoodPercent",
    "deterministic-preview-v1",
    "PreviewStaffWorkspaceRepository",
    "recommendedToday",
    "syntheticSeed",
)


def test_staff_assistant_code_never_touches_fabricated_risk_fields() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "audentra"
    sources = [
        *(root / "integrations" / "staff_assistant").glob("*.py"),
        root / "infrastructure" / "postgres" / "staff_assistant_repository.py",
    ]
    assert sources
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for token in _FORBIDDEN_TOKENS:
            assert token not in text, f"{source.name} references forbidden token {token!r}"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "message",
    [
        "What is Alex Morgan's melt risk?",
        "What's his probability of enrolling?",
        "What is his recovery likelihood?",
        "Did Alex open our email?",
        "How did the deposit campaign perform?",
        "Which students have the highest value?",
    ],
)
async def test_unsupported_metrics_refused_without_numbers(message: str) -> None:
    service = _service()
    result = await _ask(service, message)
    text = result["message"]
    assert re.search(r"don't have|no .*model|not tracked|no campaigns|aren't tracked", text, re.I)
    # An unsupported-metric refusal must not carry an invented score.
    assert not re.search(r"\d{1,3}\s?%", text)


@pytest.mark.anyio
async def test_melt_ranking_answers_with_attention_disclaimer() -> None:
    service = _service()
    result = await _ask(service, "Which admitted students are most at risk of melting?")
    assert re.search(r"no model for melt risk", result["message"], re.I)
    assert not re.search(r"\d{1,3}\s?%", result["message"])


def test_guard_rejects_action_claims() -> None:
    verdict = guard_staff_grounded_answer(
        answer="I've sent the email to Alex and he should reply soon.",
        evidence_texts=["Blocker: official transcript"],
    )
    assert not verdict.accepted
    assert verdict.reason_code == "claimed_action"


def test_guard_rejects_fabricated_metric_language() -> None:
    verdict = guard_staff_grounded_answer(
        answer="Her melt risk is high and her recovery likelihood is 40%.",
        evidence_texts=["Open requirement: official transcript (status ready)"],
    )
    assert not verdict.accepted
    assert verdict.reason_code == "fabricated_metric_language"


def test_guard_rejects_ungrounded_numbers_and_dates() -> None:
    evidence = ["Deposit amount: $500.00", "Deadline: transcript due 2027-08-15"]
    ungrounded_number = guard_staff_grounded_answer(
        answer="The deposit is $999.", evidence_texts=evidence
    )
    assert not ungrounded_number.accepted
    assert ungrounded_number.reason_code == "ungrounded_number"
    ungrounded_date = guard_staff_grounded_answer(
        answer="The transcript is due on 2027-09-01.", evidence_texts=evidence
    )
    assert not ungrounded_date.accepted
    assert ungrounded_date.reason_code == "ungrounded_date"
    grounded = guard_staff_grounded_answer(
        answer="The $500 deposit is unpaid and the transcript is due 2027-08-15.",
        evidence_texts=evidence,
    )
    assert grounded.accepted


# ---------------------------------------------------------------------------
# Tool argument validation
# ---------------------------------------------------------------------------


def test_tool_arguments_unknown_argument_rejected() -> None:
    with pytest.raises(ToolArgumentError) as excinfo:
        validate_tool_arguments(
            "getStudentRequirements",
            {"studentId": DEMO_IDS["student_id"], "tenantId": "sneaky"},
        )
    assert excinfo.value.code == "unknown_argument"


def test_tool_arguments_invalid_uuid_rejected() -> None:
    with pytest.raises(ToolArgumentError) as excinfo:
        validate_tool_arguments("getStudentRequirements", {"studentId": "not-a-uuid"})
    assert excinfo.value.code == "invalid_uuid"


def test_tool_arguments_missing_required_rejected() -> None:
    with pytest.raises(ToolArgumentError) as excinfo:
        validate_tool_arguments("getStudentRequirements", {})
    assert excinfo.value.code == "missing_argument"


def test_tool_arguments_enum_and_bounds() -> None:
    with pytest.raises(ToolArgumentError):
        validate_tool_arguments(
            "getStudentCommunicationHistory",
            {"studentId": DEMO_IDS["student_id"], "channel": "carrier-pigeon"},
        )
    cleaned = validate_tool_arguments("getStudentsNeedingAttention", {"limit": 9_999})
    assert cleaned["limit"] == 50


# ---------------------------------------------------------------------------
# Conversation memory + observability
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_multi_turn_referent_resolution() -> None:
    service = _service()
    conversation_id = await _conversation(service)
    first = await _ask(service, "Tell me about Alex Morgan", conversation_id=conversation_id)
    assert first["resolvedStudent"]["id"] == DEMO_IDS["student_id"]
    second = await _ask(service, "What is he missing?", conversation_id=conversation_id)
    assert second["resolvedStudent"]["id"] == DEMO_IDS["student_id"]
    assert re.search(r"open requirement", second["message"], re.I)
    third = await _ask(service, "Has he responded to us?", conversation_id=conversation_id)
    assert re.search(r"recorded", third["message"], re.I)


@pytest.mark.anyio
async def test_student_required_intent_without_referent_asks() -> None:
    service = _service()
    result = await _ask(service, "What is she missing?")
    assert "which student" in result["message"].lower()


@pytest.mark.anyio
async def test_idempotent_replay_returns_stored_exchange() -> None:
    service = _service()
    conversation_id = await _conversation(service)
    first = await _ask(
        service,
        "Tell me about Alex Morgan",
        conversation_id=conversation_id,
        client_message_id="client-msg-0001",
        request_id="replay-first",
    )
    message_count = len(service.store.staff_assistant_messages)
    replay = await _ask(
        service,
        "Tell me about Alex Morgan",
        conversation_id=conversation_id,
        client_message_id="client-msg-0001",
        request_id="replay-second",
    )
    assert replay["assistantMessageId"] == first["assistantMessageId"]
    assert len(service.store.staff_assistant_messages) == message_count


@pytest.mark.anyio
async def test_trace_records_staff_turn_with_tool_arguments() -> None:
    service = _service()
    request_id = "trace-staff-turn-1"
    await _ask(service, "Why is Alex Morgan blocked?", request_id=request_id)
    trace = get_assistant_trace_recorder().get(request_id)
    assert trace is not None
    assert trace["assistantKind"] == "staff"
    assert trace["actorType"] == "staff"
    assert trace["staffMemberId"] == DEMO_IDS["staff_advisor_id"]
    assert trace["studentId"] == DEMO_IDS["student_id"]
    executed = {call["tool"] for call in trace["toolCalls"]}
    assert "getStudentBlockers" in executed
    blocker_call = next(call for call in trace["toolCalls"] if call["tool"] == "getStudentBlockers")
    assert blocker_call["arguments"]["studentId"] == DEMO_IDS["student_id"]
    assert blocker_call["validation"] == "accepted"
    assert trace["evidence"]


@pytest.mark.anyio
async def test_blocker_answer_names_hold_system_honestly() -> None:
    service = _service()
    result = await _ask(service, "Does Alex Morgan have any holds?")
    assert re.search(r"no registrar hold system", result["message"], re.I)


# ---------------------------------------------------------------------------
# Deterministic routing added for the database-backed staff eval
# ---------------------------------------------------------------------------


def _classify(message: str) -> Any:
    from audentra.integrations.staff_assistant.classify import classify_staff_request
    from audentra.integrations.staff_assistant.normalize import normalize_staff_request

    return classify_staff_request(normalize_staff_request(message))


def test_action_center_phrases_route_to_the_canonical_queue() -> None:
    for message in (
        "What is in my Action Center?",
        "How many open items are in my Action Center right now?",
        "What are the transcript items in the Action Center?",
    ):
        classification = _classify(message)
        assert classification is not None, message
        assert classification.request_type == "work_queue", message
    topical = _classify("Which students have transcript-related items in my Action Center?")
    assert topical is not None
    assert topical.request_type == "work_queue"
    assert topical.reference == "topic:transcript"


def test_action_center_membership_questions_are_student_scoped() -> None:
    for message in (
        "Why is Marisol Fennwick in my Action Center?",
        "Is Tobias Quillfeather currently in the Action Center?",
    ):
        classification = _classify(message)
        assert classification is not None, message
        assert classification.request_type == "student_action_center", message


def test_action_center_membership_counts_stay_with_the_cohort_classifier() -> None:
    classification = _classify("How many students are in the Action Center?")
    assert classification is not None
    assert classification.request_type == "cohort_aggregate"
    assert classification.cohort_filter == {"hasOpenWorkItem": True}


def test_cohort_deposit_predicate_accepts_the_plural() -> None:
    classification = _classify("How many students have unpaid deposits?")
    assert classification is not None
    assert classification.request_type == "cohort_aggregate"
    assert classification.cohort_filter == {"depositState": "unpaid"}


def test_pasted_student_id_is_not_a_work_item_question() -> None:
    classification = _classify("Show me the student with ID SYN-000004.")
    assert classification is not None
    assert classification.request_type == "student_overview"
    # ...while an explicit task question keeps the work-item route.
    task = _classify("What happened on the task ENR-104?")
    assert task is not None
    assert task.request_type == "work_item_detail"


def test_mark_as_accepted_is_an_action_request() -> None:
    classification = _classify("Mark Devon Ashgrove's transcript as accepted.")
    assert classification is not None
    assert classification.request_type == "action_request"


def test_highest_risk_and_odds_phrasings_stay_honest() -> None:
    ranking = _classify("Which students are highest risk right now?")
    assert ranking is not None
    assert ranking.request_type == "attention_ranking"
    odds = _classify("What are the odds Wren Halloway actually enrolls this fall?")
    assert odds is not None
    assert odds.request_type in {"unsupported_metric", "attention_ranking"}
    assert odds.reference == "enrollment_probability"


def test_has_anyone_emailed_routes_to_communications() -> None:
    classification = _classify("Has anyone from our office emailed Odalys Brightwater recently?")
    assert classification is not None
    assert classification.request_type == "student_communications"


@pytest.mark.anyio
async def test_refusal_intents_never_run_referent_resolution() -> None:
    service = _service()
    response = await _ask(service, "Mark Alex Morgan's transcript as accepted.")
    assert response["resolvedStudent"] is None
    message = response["message"].lower()
    assert "read-only" in message or "can't" in message or "cannot" in message
    assert "which one do you mean" not in message


def test_break_down_phrasing_is_a_cohort_aggregate() -> None:
    classification = _classify("Break down the students still in onboarding by program.")
    assert classification is not None
    assert classification.request_type == "cohort_aggregate"
    assert classification.cohort_filter == {"onboardingStatus": "in_progress"}
    assert classification.cohort_group_by == "program"
