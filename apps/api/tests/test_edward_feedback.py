"""Response-scoped Student/Staff Edward feedback and Lab visibility."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from audentra.application.platform_service import InMemoryPlatformService
from audentra.infrastructure.memory.store import DEMO_IDS
from audentra.interfaces.http.app import create_app
from audentra.interfaces.http.config import HttpSettings

WORKER_TOKEN = "local-development-document-worker-token"  # noqa: S105
STAFF_HEADERS = {
    "x-demo-actor-type": "staff",
    "x-demo-actor-id": DEMO_IDS["staff_advisor_id"],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app(
        service=InMemoryPlatformService(),
        settings=HttpSettings(assistant_trace_debug_enabled=True),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as http_client:
        yield http_client


async def _student_turn(client: AsyncClient, question: str, suffix: str) -> dict[str, object]:
    conversation = await client.post(
        "/v1/student/assistant/conversations",
        json={"pageContext": {"path": "/edward", "label": "Edward"}},
    )
    assert conversation.status_code == 201
    response = await client.post(
        "/v1/student/assistant/messages",
        json={
            "message": question,
            "pageContext": "/edward",
            "conversationId": conversation.json()["id"],
            "clientMessageId": f"student-feedback-{suffix}",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


async def _staff_turn(client: AsyncClient, question: str, suffix: str) -> dict[str, object]:
    conversation = await client.post("/v1/staff/assistant/conversations", headers=STAFF_HEADERS)
    assert conversation.status_code == 201
    response = await client.post(
        "/v1/staff/assistant/messages",
        headers=STAFF_HEADERS,
        json={
            "message": question,
            "conversationId": conversation.json()["id"],
            "clientMessageId": f"staff-feedback-{suffix}",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    return body


@pytest.mark.anyio
async def test_student_rating_comment_and_change_update_one_exact_record(
    client: AsyncClient,
) -> None:
    question = "What should I do next?"
    turn = await _student_turn(client, question, "one")
    path = f"/v1/student/assistant/messages/{turn['assistantMessageId']}/feedback"

    positive = await client.patch(
        path,
        json={"traceId": turn["requestId"], "rating": "positive"},
    )
    assert positive.status_code == 200
    feedback_id = positive.json()["id"]
    assert positive.json()["rating"] == "positive"
    assert positive.json()["question"] == question
    assert positive.json()["response"] == turn["message"]
    assert positive.json()["traceId"] == turn["requestId"]

    negative = await client.patch(
        path,
        json={"traceId": turn["requestId"], "rating": "negative"},
    )
    assert negative.status_code == 200
    assert negative.json()["id"] == feedback_id
    assert negative.json()["rating"] == "negative"

    commented = await client.patch(
        path,
        json={
            "traceId": turn["requestId"],
            "writtenFeedback": "The answer did not explain the deadline.",
        },
    )
    assert commented.status_code == 200
    assert commented.json()["id"] == feedback_id
    assert commented.json()["rating"] == "negative"
    assert commented.json()["writtenFeedback"] == ("The answer did not explain the deadline.")

    listing = await client.get(
        "/internal/assistant/feedback?assistantKind=student"
        "&from=2020-01-01T00:00:00Z&to=2030-01-01T00:00:00Z",
        headers={"X-VV-Worker-Token": WORKER_TOKEN},
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == feedback_id


@pytest.mark.anyio
async def test_written_only_feedback_and_exact_trace_drill_down(client: AsyncClient) -> None:
    first = await _student_turn(client, "What documents am I missing?", "first")
    second = await _student_turn(client, "What is my balance?", "second")
    path = f"/v1/student/assistant/messages/{first['assistantMessageId']}/feedback"

    mismatched = await client.patch(
        path,
        json={
            "traceId": second["requestId"],
            "writtenFeedback": "This must not attach to the first response.",
        },
    )
    assert mismatched.status_code == 404

    written = await client.patch(
        path,
        json={
            "traceId": first["requestId"],
            "writtenFeedback": "Please name the required document.",
        },
    )
    assert written.status_code == 200
    assert written.json()["rating"] is None
    assert written.json()["assistantMessageId"] == first["assistantMessageId"]

    trace = await client.get(
        f"/internal/assistant/traces/{written.json()['traceId']}",
        headers={"X-VV-Worker-Token": WORKER_TOKEN},
    )
    assert trace.status_code == 200
    assert trace.json()["assistantMessageId"] == first["assistantMessageId"]
    assert trace.json()["userMessageId"] == written.json()["userMessageId"]
    assert trace.json()["userMessage"] == "What documents am I missing?"


@pytest.mark.anyio
async def test_staff_feedback_is_owned_and_separated_in_lab(client: AsyncClient) -> None:
    student = await _student_turn(client, "What should I do next?", "student-separation")
    await client.patch(
        f"/v1/student/assistant/messages/{student['assistantMessageId']}/feedback",
        json={"traceId": student["requestId"], "rating": "positive"},
    )
    staff = await _staff_turn(client, "What needs my attention today?", "staff-one")
    submitted = await client.patch(
        f"/v1/staff/assistant/messages/{staff['assistantMessageId']}/feedback",
        headers=STAFF_HEADERS,
        json={
            "traceId": staff["requestId"],
            "rating": "negative",
            "writtenFeedback": "The queue ordering needs more explanation.",
        },
    )
    assert submitted.status_code == 200
    assert submitted.json()["assistantKind"] == "staff"
    assert submitted.json()["actorId"] == DEMO_IDS["staff_advisor_id"]

    staff_listing = await client.get(
        "/internal/assistant/feedback?assistantKind=staff&rating=negative&hasWritten=true",
        headers={"X-VV-Worker-Token": WORKER_TOKEN},
    )
    student_listing = await client.get(
        "/internal/assistant/feedback?assistantKind=student",
        headers={"X-VV-Worker-Token": WORKER_TOKEN},
    )
    assert staff_listing.status_code == 200
    assert staff_listing.json()["total"] == 1
    assert staff_listing.json()["items"][0]["traceId"] == staff["requestId"]
    assert student_listing.json()["total"] == 1
    assert all(item["assistantKind"] == "student" for item in student_listing.json()["items"])

    # The student route cannot mutate a staff-owned assistant response.
    wrong_surface = await client.patch(
        f"/v1/student/assistant/messages/{staff['assistantMessageId']}/feedback",
        json={"traceId": staff["requestId"], "rating": "positive"},
    )
    assert wrong_surface.status_code == 404


@pytest.mark.anyio
async def test_feedback_lab_endpoint_requires_internal_auth(client: AsyncClient) -> None:
    response = await client.get("/internal/assistant/feedback?assistantKind=student")
    assert response.status_code == 403
