"""PostgreSQL guarantees for durable Staff Edward conversations.

These tests deliberately exercise the repository with concurrent connections.
They are opt-in because they reset a disposable migrated database.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import BadRequestError, NotFoundError
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.postgres.staff_assistant_repository import (
    PostgresStaffAssistantRepository,
)
from audentra.infrastructure.seeding.relational import (
    ASTER_STUDENT_ID,
    ASTER_TENANT_ID,
    HARVARD_STAFF_ID,
    HARVARD_STUDENT_ID,
    HARVARD_TENANT_ID,
    reset_relational_data,
    seed_relational_data,
)

pytestmark = pytest.mark.integration

HARVARD_OTHER_STAFF_ID = "80000000-0000-7000-8000-000000000902"
ASTER_STAFF_ID = "00000000-0000-7000-8000-000000000901"

HARVARD_STAFF = AuthContext(
    tenant_id=HARVARD_TENANT_ID,
    student_id=HARVARD_STUDENT_ID,
    actor_id=HARVARD_STAFF_ID,
    actor_type="staff",
    tenant_slug="harvard",
)
HARVARD_OTHER_STAFF = AuthContext(
    tenant_id=HARVARD_TENANT_ID,
    student_id=HARVARD_STUDENT_ID,
    actor_id=HARVARD_OTHER_STAFF_ID,
    actor_type="staff",
    tenant_slug="harvard",
)
ASTER_STAFF = AuthContext(
    tenant_id=ASTER_TENANT_ID,
    student_id=ASTER_STUDENT_ID,
    actor_id=ASTER_STAFF_ID,
    actor_type="staff",
    tenant_slug="aster",
)


def _database_url() -> str:
    value = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not value:
        pytest.skip("AUDENTRA_TEST_DATABASE_URL is not configured")
    return value


def _run(scenario: Callable[[Any, PostgresStaffAssistantRepository], Awaitable[None]]) -> None:
    async def main() -> None:
        engine = create_database_engine(_database_url())
        try:
            await reset_relational_data(engine, environment="test", completed_onboarding=True)
            await seed_relational_data(engine, environment="test")
            await scenario(engine, PostgresStaffAssistantRepository(engine))
        finally:
            await engine.dispose()

    asyncio.run(main())


async def _append(
    repository: PostgresStaffAssistantRepository,
    *,
    auth: AuthContext = HARVARD_STAFF,
    conversation_id: str | None = None,
    client_message_id: str,
    user_content: str = "What is Jordan missing?",
    assistant_content: str = "Jordan has one open requirement.",
    referenced_student_id: str | None = HARVARD_STUDENT_ID,
) -> dict[str, Any]:
    return await repository.append_exchange(
        auth,
        conversation_id=conversation_id,
        user_message={"content": user_content, "clientMessageId": client_message_id},
        assistant_message={
            "content": assistant_content,
            "provider": "guided",
            "blocks": [{"type": "text", "text": assistant_content}],
            "contextReceipts": [{"source": "student_overview"}],
        },
        referenced_student_id=referenced_student_id,
        request_id=f"request-{client_message_id}",
    )


async def _row_counts(engine: Any) -> tuple[int, int]:
    async with engine.connect() as connection:
        conversations = await connection.scalar(
            text("SELECT COUNT(*) FROM staff_assistant_conversation")
        )
        messages = await connection.scalar(text("SELECT COUNT(*) FROM staff_assistant_message"))
    return int(conversations or 0), int(messages or 0)


def test_retry_replays_the_exact_durable_exchange() -> None:
    async def scenario(engine: Any, repository: PostgresStaffAssistantRepository) -> None:
        first = await _append(repository, client_message_id="staff-retry-1")
        replay = await _append(
            repository,
            client_message_id="staff-retry-1",
            assistant_content="This response must never replace the first one.",
        )

        assert replay["conversationId"] == first["conversationId"]
        assert replay["userMessageId"] == first["userMessageId"]
        assert replay["assistantMessageId"] == first["assistantMessageId"]
        assert replay["message"] == "Jordan has one open requirement."
        assert await _row_counts(engine) == (1, 2)

    _run(scenario)


def test_concurrent_retry_creates_no_unused_conversations_or_messages() -> None:
    async def scenario(engine: Any, repository: PostgresStaffAssistantRepository) -> None:
        results = await asyncio.gather(
            *(
                _append(
                    repository,
                    client_message_id="staff-concurrent-retry",
                    assistant_content=f"candidate response {index}",
                )
                for index in range(8)
            )
        )

        assert len({result["conversationId"] for result in results}) == 1
        assert len({result["userMessageId"] for result in results}) == 1
        assert len({result["assistantMessageId"] for result in results}) == 1
        assert await _row_counts(engine) == (1, 2)

    _run(scenario)


def test_concurrent_distinct_turns_replay_their_own_assistant_message() -> None:
    async def scenario(engine: Any, repository: PostgresStaffAssistantRepository) -> None:
        conversation = await repository.create_conversation(HARVARD_STAFF)
        conversation_id = str(conversation["id"])
        expected = {f"staff-turn-{index}": f"answer {index}" for index in range(6)}

        await asyncio.gather(
            *(
                _append(
                    repository,
                    conversation_id=conversation_id,
                    client_message_id=client_id,
                    assistant_content=answer,
                )
                for client_id, answer in expected.items()
            )
        )

        for client_id, answer in expected.items():
            replay = await repository.find_exchange_by_client_id(HARVARD_STAFF, client_id)
            assert replay is not None
            assert replay["conversationId"] == conversation_id
            assert replay["message"] == answer
        assert await _row_counts(engine) == (1, 12)

    _run(scenario)


def test_conversation_and_replay_are_owned_by_tenant_and_staff_member() -> None:
    async def scenario(engine: Any, repository: PostgresStaffAssistantRepository) -> None:
        stored = await _append(repository, client_message_id="staff-owner-1")
        conversation_id = str(stored["conversationId"])

        for foreign_auth in (HARVARD_OTHER_STAFF, ASTER_STAFF):
            with pytest.raises(NotFoundError) as error:
                await repository.get_conversation_messages(foreign_auth, conversation_id)
            assert error.value.code == "STAFF_ASSISTANT_CONVERSATION_NOT_FOUND"
            assert (
                await repository.find_exchange_by_client_id(foreign_auth, "staff-owner-1") is None
            )
            with pytest.raises(NotFoundError):
                await _append(
                    repository,
                    auth=foreign_auth,
                    conversation_id=conversation_id,
                    client_message_id=f"foreign-{foreign_auth.actor_id}",
                    referenced_student_id=(
                        HARVARD_STUDENT_ID
                        if foreign_auth.tenant_id == HARVARD_TENANT_ID
                        else ASTER_STUDENT_ID
                    ),
                )

        assert await _row_counts(engine) == (1, 2)

    _run(scenario)


def test_cross_tenant_referent_rolls_back_implicit_conversation_creation() -> None:
    async def scenario(engine: Any, repository: PostgresStaffAssistantRepository) -> None:
        with pytest.raises(BadRequestError) as error:
            await _append(
                repository,
                client_message_id="staff-cross-tenant-referent",
                referenced_student_id=ASTER_STUDENT_ID,
            )
        assert error.value.code == "STAFF_ASSISTANT_STUDENT_REFERENT_INVALID"
        assert await _row_counts(engine) == (0, 0)

    _run(scenario)


def test_valid_referent_is_durable_and_available_to_follow_ups() -> None:
    async def scenario(engine: Any, repository: PostgresStaffAssistantRepository) -> None:
        stored = await _append(repository, client_message_id="staff-referent-1")
        conversation_id = str(stored["conversationId"])

        history = await repository.get_recent_history(HARVARD_STAFF, conversation_id)
        transcript = await repository.get_conversation_messages(HARVARD_STAFF, conversation_id)
        assert history["activeStudentId"] == HARVARD_STUDENT_ID
        assert [entry["role"] for entry in history["history"]] == ["user", "assistant"]
        assert transcript["activeStudentId"] == HARVARD_STUDENT_ID
        assert {message["referencedStudentId"] for message in transcript["messages"]} == {
            HARVARD_STUDENT_ID
        }

    _run(scenario)


def test_card_scope_boundary_retains_transcript_but_excludes_old_referents() -> None:
    async def scenario(engine: Any, repository: PostgresStaffAssistantRepository) -> None:
        first = await _append(repository, client_message_id="old-student")
        conversation_id = str(first["conversationId"])
        empty = await repository.get_recent_history(
            HARVARD_STAFF, conversation_id, history_after="new-card"
        )
        assert empty["history"] == []
        assert empty["activeStudentId"] is None
        assert empty["activeCohortFilter"] is None
        await _append(
            repository,
            conversation_id=conversation_id,
            client_message_id="new-card",
            user_content="Which students are affected?",
        )
        scoped = await repository.get_recent_history(
            HARVARD_STAFF, conversation_id, history_after="new-card"
        )
        assert len(scoped["history"]) == 2
        assert scoped["history"][0]["content"] == "Which students are affected?"
        transcript = await repository.get_conversation_messages(HARVARD_STAFF, conversation_id)
        assert len(transcript["messages"]) == 4
        foreign = await repository.get_recent_history(
            HARVARD_OTHER_STAFF, conversation_id, history_after="new-card"
        )
        assert foreign["history"] == []

    _run(scenario)
