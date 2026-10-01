"""End-to-end Edward action guarantees on the production PostgreSQL path."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from audentra.bootstrap.api import create_production_app
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, ConflictError, NotFoundError
from audentra.domain.edward_actions import parse_staff_action, parse_student_action
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.db.migrations import resolve_migrations_directory, run_migrations
from audentra.infrastructure.postgres.edward_action_gateway import EdwardActionGateway
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository
from audentra.infrastructure.postgres.staff_assistant_repository import (
    PostgresStaffAssistantRepository,
)
from audentra.infrastructure.postgres.staff_repository import PostgresStaffRepository
from audentra.infrastructure.seeding.relational import (
    HARVARD_PERSON_ID,
    HARVARD_STAFF_ID,
    HARVARD_STUDENT_ID,
    HARVARD_TENANT_ID,
    reset_relational_data,
    seed_relational_data,
)
from audentra.integrations.assistant.trace import get_assistant_trace_recorder

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

HARVARD_OTHER_STAFF_ID = "80000000-0000-7000-8000-000000000902"


def _database_url() -> str:
    value = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not value:
        pytest.skip("AUDENTRA_TEST_DATABASE_URL is not configured")
    return value


def _student(student_id: str = HARVARD_STUDENT_ID) -> AuthContext:
    return AuthContext(
        tenant_id=HARVARD_TENANT_ID,
        student_id=student_id,
        actor_id=student_id,
        actor_type="student",
        tenant_slug="harvard",
    )


def _staff(staff_id: str = HARVARD_STAFF_ID) -> AuthContext:
    return AuthContext(
        tenant_id=HARVARD_TENANT_ID,
        student_id=HARVARD_STUDENT_ID,
        actor_id=staff_id,
        actor_type="staff",
        tenant_slug="harvard",
    )


def _production_settings(url: str) -> RuntimeSettings:
    return RuntimeSettings.from_environment(
        {
            "AUDENTRA_ENV": "test",
            "DATABASE_URL": url,
            "OBJECT_STORAGE_ENDPOINT": "http://127.0.0.1:1",
            "OBJECT_STORAGE_BUCKET": "edward-v1-tests",
            "OBJECT_STORAGE_ACCESS_KEY": "edward_v1",
            "OBJECT_STORAGE_SECRET_KEY": "edward_v1_test_secret",
            "DOCUMENT_WORKER_TOKEN": "edward-v1-test-worker-token",
            "DEMO_TENANT_ID": HARVARD_TENANT_ID,
            "DEMO_STUDENT_ID": HARVARD_STUDENT_ID,
            "DEMO_ACTOR_ID": HARVARD_PERSON_ID,
            "DEMO_STAFF_ACTOR_ID": HARVARD_STAFF_ID,
        }
    )


def _run(
    scenario: Callable[[Any, EdwardActionGateway, PostgresPortalRepository], Awaitable[None]],
) -> None:
    async def main() -> None:
        url = _database_url()
        await run_migrations(url, resolve_migrations_directory())
        engine = create_database_engine(url)
        try:
            await reset_relational_data(engine, environment="test", completed_onboarding=True)
            await seed_relational_data(engine, environment="test")
            portal = PostgresPortalRepository(engine)
            staff = PostgresStaffRepository(engine, portal)
            staff_assistant = PostgresStaffAssistantRepository(engine)
            gateway = EdwardActionGateway(engine, portal, staff, staff_assistant)
            await scenario(engine, gateway, portal)
        finally:
            await engine.dispose()

    asyncio.run(main())


def test_student_preview_is_actor_bound_tamper_evident_and_replay_safe() -> None:
    async def scenario(
        engine: Any, gateway: EdwardActionGateway, portal: PostgresPortalRepository
    ) -> None:
        auth = _student()
        conversation = await portal.create_assistant_conversation(auth)
        request = parse_student_action("Change my preferred name to Sam.")
        assert request is not None
        intent = await gateway.propose_student(
            auth,
            request,
            conversation_id=str(conversation["id"]),
            trace_id="student-action-integration",
        )
        get_assistant_trace_recorder().record_payload(
            {"traceId": "student-action-integration", "actionProposed": intent["action"]}
        )
        assert intent["preview"]["changes"] == [
            {"field": "preferredName", "before": "Alex", "after": "Sam"}
        ]

        async with engine.connect() as connection:
            other_student_id = await connection.scalar(
                text(
                    """SELECT id FROM student
                       WHERE tenant_id=:tenant_id AND id<>:student_id ORDER BY id LIMIT 1"""
                ),
                {"tenant_id": HARVARD_TENANT_ID, "student_id": HARVARD_STUDENT_ID},
            )
        assert other_student_id is not None
        with pytest.raises(NotFoundError):
            await gateway.get_intent(_student(str(other_student_id)), str(intent["id"]))

        with pytest.raises(ConflictError) as changed:
            await gateway.confirm(
                auth,
                str(intent["id"]),
                expected_version=int(intent["version"]),
                content_sha256="b" * 64,
                request_id="student-action-tamper",
            )
        assert changed.value.code == "EDWARD_ACTION_INTENT_CHANGED"

        receipt = await gateway.confirm(
            auth,
            str(intent["id"]),
            expected_version=int(intent["version"]),
            content_sha256=str(intent["contentSha256"]),
            request_id="student-action-commit",
        )
        replay = await gateway.confirm(
            auth,
            str(intent["id"]),
            expected_version=int(intent["version"]),
            content_sha256=str(intent["contentSha256"]),
            request_id="student-action-replay",
        )
        assert receipt == replay
        assert receipt["status"] == "succeeded"
        assert receipt["auditEventIds"]
        assert len(str(receipt["receiptSha256"])) == 64
        assert (await portal.get_student_profile(auth))["preferredName"] == "Sam"
        action_trace = get_assistant_trace_recorder().get("student-action-integration")
        assert action_trace is not None
        assert action_trace["actionExecutionResult"] == "succeeded"
        assert (
            cast(Mapping[str, Any], action_trace["actionReceipt"])["receiptSha256"]
            == receipt["receiptSha256"]
        )

        # Even a privileged database-side edit cannot silently retain the old
        # confirmation hash. This pins the stored effect to what was shown.
        second = parse_student_action("Set my pronouns to they/them.")
        assert second is not None
        second_intent = await gateway.propose_student(
            auth,
            second,
            conversation_id=str(conversation["id"]),
            trace_id="student-action-server-tamper",
        )
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """UPDATE agent_action_intent
                       SET resolved_payload=jsonb_set(
                         resolved_payload, '{pronouns}', to_jsonb(CAST(:pronouns AS text))
                       ) WHERE id=:id"""
                ),
                {"id": second_intent["id"], "pronouns": "he/him"},
            )
        with pytest.raises(ConflictError) as stored_tamper:
            await gateway.confirm(
                auth,
                str(second_intent["id"]),
                expected_version=int(second_intent["version"]),
                content_sha256=str(second_intent["contentSha256"]),
                request_id="student-action-server-tamper-confirm",
            )
        assert stored_tamper.value.code == "EDWARD_ACTION_INTENT_CHANGED"

        async with engine.connect() as connection:
            receipt_count = await connection.scalar(
                text("SELECT COUNT(*) FROM agent_action_receipt WHERE action_intent_id=:id"),
                {"id": intent["id"]},
            )
        assert receipt_count == 1

    _run(scenario)


def test_unknown_commit_outcome_recovers_without_repeating_versioned_write() -> None:
    async def scenario(
        _engine: Any, gateway: EdwardActionGateway, portal: PostgresPortalRepository
    ) -> None:
        auth = _student()
        before = await portal.get_student_profile(auth)
        conversation = await portal.create_assistant_conversation(auth)
        request = parse_student_action("Change my preferred name to Casey.")
        assert request is not None
        intent = await gateway.propose_student(
            auth,
            request,
            conversation_id=str(conversation["id"]),
            trace_id="student-timeout-recovery",
        )
        execute = gateway._execute

        async def commit_then_lose_response(*args: Any, **kwargs: Any) -> Any:
            await execute(*args, **kwargs)
            raise RuntimeError("simulated connection loss after commit")

        gateway._execute = commit_then_lose_response  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="connection loss"):
            await gateway.confirm(
                auth,
                str(intent["id"]),
                expected_version=int(intent["version"]),
                content_sha256=str(intent["contentSha256"]),
                request_id="student-timeout-first-attempt",
            )
        uncertain = await gateway.get_intent(auth, str(intent["id"]))
        assert uncertain["status"] == "executing"
        assert "receipt" not in uncertain
        with pytest.raises(ConflictError) as still_running:
            await gateway.confirm(
                auth,
                str(intent["id"]),
                expected_version=int(intent["version"]),
                content_sha256=str(intent["contentSha256"]),
                request_id="student-timeout-immediate-retry",
            )
        assert still_running.value.code == "EDWARD_ACTION_IN_PROGRESS"

        gateway._execute = execute  # type: ignore[method-assign]
        gateway._clock = lambda: datetime.now(UTC) + timedelta(minutes=3)
        receipt = await gateway.confirm(
            auth,
            str(intent["id"]),
            expected_version=int(intent["version"]),
            content_sha256=str(intent["contentSha256"]),
            request_id="student-timeout-recovery-attempt",
        )
        after = await portal.get_student_profile(auth)
        assert receipt["status"] == "succeeded"
        assert after["preferredName"] == "Casey"
        assert after["version"] == int(before["version"]) + 1

    _run(scenario)


def test_expired_confirmation_is_durable_and_never_executes() -> None:
    async def scenario(
        _engine: Any, gateway: EdwardActionGateway, portal: PostgresPortalRepository
    ) -> None:
        auth = _student()
        before = await portal.get_student_profile(auth)
        next_preference = "sms" if before["communicationPreference"] != "sms" else "email"
        conversation = await portal.create_assistant_conversation(auth)
        request = parse_student_action(f"Set my communication preference to {next_preference}.")
        assert request is not None
        intent = await gateway.propose_student(
            auth,
            request,
            conversation_id=str(conversation["id"]),
            trace_id="student-expired-confirmation",
        )
        gateway._clock = lambda: (
            datetime.fromisoformat(str(intent["expiresAt"])) + timedelta(seconds=1)
        )
        with pytest.raises(ConflictError) as expired:
            await gateway.confirm(
                auth,
                str(intent["id"]),
                expected_version=int(intent["version"]),
                content_sha256=str(intent["contentSha256"]),
                request_id="student-expired-confirmation-attempt",
            )
        assert expired.value.code == "EDWARD_ACTION_INTENT_EXPIRED"
        stored = await gateway.get_intent(auth, str(intent["id"]))
        assert stored["status"] == "expired"
        assert (await portal.get_student_profile(auth))["communicationPreference"] == before[
            "communicationPreference"
        ]

    _run(scenario)


def test_staff_follow_up_uses_capabilities_scope_and_canonical_idempotency() -> None:
    async def scenario(
        engine: Any, gateway: EdwardActionGateway, _portal: PostgresPortalRepository
    ) -> None:
        auth = _staff()
        assistant = gateway._staff_assistant
        conversation = await assistant.create_conversation(auth)
        request = parse_staff_action(
            "Create a follow-up for Alex, assign it to me, and make it high priority."
        )
        assert request is not None
        intent = await gateway.propose_staff(
            auth,
            request,
            conversation_id=str(conversation["id"]),
            trace_id="staff-follow-up-integration",
            resolved_student_id=HARVARD_STUDENT_ID,
        )
        assert intent["authorizationCapability"] == "edward.follow_up.create"
        assert intent["preview"]["student"]["id"] == HARVARD_STUDENT_ID

        with pytest.raises(NotFoundError):
            await gateway.get_intent(_staff(HARVARD_OTHER_STAFF_ID), str(intent["id"]))
        with pytest.raises(NotFoundError):
            await gateway.confirm(
                _staff(HARVARD_OTHER_STAFF_ID),
                str(intent["id"]),
                expected_version=int(intent["version"]),
                content_sha256=str(intent["contentSha256"]),
                request_id="staff-follow-up-wrong-actor",
            )

        async with engine.connect() as connection:
            before = int(await connection.scalar(text("SELECT COUNT(*) FROM staff_work_item")) or 0)
        receipt = await gateway.confirm(
            auth,
            str(intent["id"]),
            expected_version=int(intent["version"]),
            content_sha256=str(intent["contentSha256"]),
            request_id="staff-follow-up-confirm",
        )
        replay = await gateway.confirm(
            auth,
            str(intent["id"]),
            expected_version=int(intent["version"]),
            content_sha256=str(intent["contentSha256"]),
            request_id="staff-follow-up-retry",
        )
        async with engine.connect() as connection:
            after = int(await connection.scalar(text("SELECT COUNT(*) FROM staff_work_item")) or 0)
            audit_count = int(
                await connection.scalar(
                    text(
                        """SELECT COUNT(*) FROM audit_event
                               WHERE tenant_id=:tenant_id AND action='staff_work_item.created'"""
                    ),
                    {"tenant_id": HARVARD_TENANT_ID},
                )
                or 0
            )
        assert receipt == replay
        assert receipt["auditEventIds"]
        assert after == before + 1
        assert audit_count >= 1
        recent_key = await gateway.recent_work_item_key(auth, str(conversation["id"]))
        assert recent_key == receipt["result"]["key"]

        update_request = parse_staff_action(
            "Move that task to follow-up and assign it to me for Friday."
        )
        assert update_request is not None
        update_intent = await gateway.propose_staff(
            auth,
            update_request,
            conversation_id=str(conversation["id"]),
            trace_id="staff-work-update-integration",
            resolved_student_id=HARVARD_STUDENT_ID,
            work_item_key=recent_key,
        )
        update_receipt = await gateway.confirm(
            auth,
            str(update_intent["id"]),
            expected_version=int(update_intent["version"]),
            content_sha256=str(update_intent["contentSha256"]),
            request_id="staff-work-update-confirm",
        )
        assert update_receipt["status"] == "succeeded"
        assert update_receipt["result"]["status"] == "follow_up_required"

        cohort_request = parse_staff_action("Create follow-ups for those students.")
        assert cohort_request is not None
        with pytest.raises(ApiError) as forbidden:
            await gateway.propose_staff(
                _staff(HARVARD_OTHER_STAFF_ID),
                cohort_request,
                conversation_id=str(
                    (await assistant.create_conversation(_staff(HARVARD_OTHER_STAFF_ID)))["id"]
                ),
                trace_id="staff-cohort-forbidden",
                resolved_student_id=None,
                cohort_filter={"depositState": "unpaid"},
            )
        assert forbidden.value.code == "EDWARD_ACTION_CAPABILITY_REQUIRED"

    _run(scenario)


def test_stale_student_state_fails_closed_with_a_failed_receipt() -> None:
    async def scenario(
        _engine: Any, gateway: EdwardActionGateway, portal: PostgresPortalRepository
    ) -> None:
        auth = _student()
        conversation = await portal.create_assistant_conversation(auth)
        before = await portal.get_student_profile(auth)
        request = parse_student_action("Change my preferred name to Rowan.")
        assert request is not None
        intent = await gateway.propose_student(
            auth,
            request,
            conversation_id=str(conversation["id"]),
            trace_id="student-stale-version",
        )
        await portal.update_student_profile(
            auth,
            {"expectedVersion": before["version"], "pronouns": "they/them"},
            "student-concurrent-profile-update",
        )
        with pytest.raises(ConflictError) as stale:
            await gateway.confirm(
                auth,
                str(intent["id"]),
                expected_version=int(intent["version"]),
                content_sha256=str(intent["contentSha256"]),
                request_id="student-stale-version-confirm",
            )
        assert stale.value.code == "VERSION_CONFLICT"
        stored = await gateway.get_intent(auth, str(intent["id"]))
        assert stored["status"] == "failed"
        assert stored["receipt"]["status"] == "failed"
        assert stored["receipt"]["result"]["errorCode"] == "VERSION_CONFLICT"
        assert (await portal.get_student_profile(auth))["preferredName"] == before["preferredName"]

    _run(scenario)


def test_production_http_flow_proposes_confirms_and_resolves_that_task() -> None:
    async def scenario() -> None:
        url = _database_url()
        await run_migrations(url, resolve_migrations_directory())
        engine = create_database_engine(url)
        try:
            await reset_relational_data(engine, environment="test", completed_onboarding=True)
            await seed_relational_data(engine, environment="test")
        finally:
            await engine.dispose()

        app = create_production_app(_production_settings(url))
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://edward.test") as client,
        ):
            student_proposal = await client.post(
                "/v1/student/assistant/messages",
                json={
                    "message": "Change my preferred name to Rowan.",
                    "clientMessageId": str(uuid4()),
                    "pageContext": {"path": "/profile", "label": "Profile"},
                },
            )
            assert student_proposal.status_code == 200, student_proposal.text
            student_body = student_proposal.json()
            student_intent = student_body["actionIntents"][0]
            assert student_intent["action"] == "student.preferences.update"
            student_receipt = await client.post(
                f"/v1/student/assistant/action-intents/{student_intent['id']}/confirm",
                json={
                    "expectedVersion": student_intent["version"],
                    "contentSha256": student_intent["contentSha256"],
                },
            )
            assert student_receipt.status_code == 200, student_receipt.text
            assert student_receipt.json()["status"] == "succeeded"

            staff_headers = {"X-Demo-Actor-Type": "staff"}
            staff_proposal = await client.post(
                "/v1/staff/assistant/messages",
                headers=staff_headers,
                json={
                    "message": "Create a follow-up for Alex and assign it to me.",
                    "clientMessageId": str(uuid4()),
                },
            )
            assert staff_proposal.status_code == 200, staff_proposal.text
            staff_body = staff_proposal.json()
            staff_intent = staff_body["actionIntents"][0]
            assert staff_intent["action"] == "operations.follow_up.create"
            staff_trace = get_assistant_trace_recorder().get(staff_body["requestId"])
            assert staff_trace is not None
            assert staff_trace["modelCalls"] == []
            staff_receipt = await client.post(
                f"/v1/staff/assistant/action-intents/{staff_intent['id']}/confirm",
                headers=staff_headers,
                json={
                    "expectedVersion": staff_intent["version"],
                    "contentSha256": staff_intent["contentSha256"],
                },
            )
            assert staff_receipt.status_code == 200, staff_receipt.text
            assert staff_receipt.json()["status"] == "succeeded"

            update_proposal = await client.post(
                "/v1/staff/assistant/messages",
                headers=staff_headers,
                json={
                    "conversationId": staff_body["conversationId"],
                    "message": "Move that task to follow-up and assign it to me for Friday.",
                    "clientMessageId": str(uuid4()),
                },
            )
            assert update_proposal.status_code == 200, update_proposal.text
            update_intent = update_proposal.json()["actionIntents"][0]
            assert update_intent["action"] == "operations.work_item.update"
            assert update_intent["preview"]["changes"]
            update_trace = get_assistant_trace_recorder().get(update_proposal.json()["requestId"])
            assert update_trace is not None
            assert update_trace["modelCalls"] == []

            stale_referent_attempt = await client.post(
                "/v1/staff/assistant/messages",
                headers=staff_headers,
                json={
                    "conversationId": staff_body["conversationId"],
                    "message": "Create a follow-up.",
                    "clientMessageId": str(uuid4()),
                },
            )
            assert stale_referent_attempt.status_code == 200
            denied = stale_referent_attempt.json()
            # The property under test is that a bare "Create a follow-up" does
            # not silently inherit the previous student: no intent may exist.
            # The surface answer changed from a gateway denial to a clarifying
            # question when the responder shipped — a complete intent missing
            # one noun gets the question, and the target is still never
            # inherited.
            assert denied["actionIntents"] == []
            assert "which student" in str(denied["message"]).lower()

    asyncio.run(scenario())


def test_student_support_and_information_acknowledgement_use_canonical_mutations() -> None:
    async def scenario(
        engine: Any, gateway: EdwardActionGateway, portal: PostgresPortalRepository
    ) -> None:
        auth = _student()
        conversation = await portal.create_assistant_conversation(auth)
        support = parse_student_action(
            "I need help with my financial aid verification requirement. Can you ask someone?"
        )
        assert support is not None
        support_intent = await gateway.propose_student(
            auth,
            support,
            conversation_id=str(conversation["id"]),
            trace_id="student-support-integration",
            page_path="/requirements/financial-aid-verification",
        )
        support_receipt = await gateway.confirm(
            auth,
            str(support_intent["id"]),
            expected_version=int(support_intent["version"]),
            content_sha256=str(support_intent["contentSha256"]),
            request_id="student-support-confirm",
        )
        assert support_receipt["status"] == "succeeded"
        assert support_receipt["result"]["status"] in {"new", "open"}

        # The demo journey has no information-only task. Convert one fixture
        # definition locally so this test exercises the real canonical response
        # transaction (validation, version, audit and outbox), not a mock.
        async with engine.begin() as connection:
            changed = await connection.execute(
                text(
                    """UPDATE requirement_definition_version
                       SET interaction_type='information', input_config='{}'::jsonb
                       WHERE tenant_id=:tenant_id AND code='financial_aid_verification'"""
                ),
                {"tenant_id": HARVARD_TENANT_ID},
            )
        assert changed.rowcount > 0
        refreshed = await portal.get_student_requirements(auth)
        refreshed_requirement = next(
            item for item in refreshed["items"] if item["code"] == "financial_aid_verification"
        )
        assert refreshed_requirement["interactionType"] == "information"
        done = parse_student_action("I finished that requirement already. Can you update it?")
        assert done is not None
        done_intent = await gateway.propose_student(
            auth,
            done,
            conversation_id=str(conversation["id"]),
            trace_id="student-requirement-integration",
            page_path="/requirements/financial-aid-verification",
        )
        done_receipt = await gateway.confirm(
            auth,
            str(done_intent["id"]),
            expected_version=int(done_intent["version"]),
            content_sha256=str(done_intent["contentSha256"]),
            request_id="student-requirement-confirm",
        )
        assert done_receipt["status"] == "succeeded"
        assert done_receipt["result"]["status"] == "completed"
        assert done_receipt["auditEventIds"]

    _run(scenario)


def test_cohort_execution_aborts_on_drift_then_executes_one_validated_population() -> None:
    async def scenario(
        engine: Any, gateway: EdwardActionGateway, portal: PostgresPortalRepository
    ) -> None:
        auth = _staff()
        conversation = await gateway._staff_assistant.create_conversation(auth)
        request = parse_staff_action(
            "Create follow-ups for those students and assign them to me for Friday."
        )
        assert request is not None

        async def propose(trace_id: str) -> dict[str, Any]:
            return await gateway.propose_staff(
                auth,
                request,
                conversation_id=str(conversation["id"]),
                trace_id=trace_id,
                resolved_student_id=None,
                cohort_filter={"offerStatus": "accepted", "depositState": "unpaid"},
            )

        drifted = await propose("cohort-drift-preview")
        preview = cast(Mapping[str, Any], drifted["preview"])["cohort"]
        assert 0 < int(preview["count"]) <= 25
        changed_student_id = str(preview["sample"][0]["id"])
        async with engine.connect() as connection:
            offer_id = await connection.scalar(
                text(
                    """SELECT id FROM admission_offer
                       WHERE tenant_id=:tenant_id AND student_id=:student_id
                         AND status='accepted' ORDER BY created_at DESC LIMIT 1"""
                ),
                {"tenant_id": HARVARD_TENANT_ID, "student_id": changed_student_id},
            )
        assert offer_id is not None
        await portal.create_deposit_payment(
            _student(changed_student_id),
            {"offerId": str(offer_id)},
            f"cohort-drift-deposit-{uuid4()}",
            "cohort-drift-deposit",
        )
        async with engine.connect() as connection:
            before = int(await connection.scalar(text("SELECT COUNT(*) FROM staff_work_item")) or 0)
        with pytest.raises(ConflictError) as drift:
            await gateway.confirm(
                auth,
                str(drifted["id"]),
                expected_version=int(drifted["version"]),
                content_sha256=str(drifted["contentSha256"]),
                request_id="cohort-drift-confirm",
            )
        assert drift.value.code == "EDWARD_COHORT_DRIFTED"
        failed = await gateway.get_intent(auth, str(drifted["id"]))
        assert failed["receipt"]["status"] == "failed"
        assert failed["receipt"]["affectedCount"] == 0

        fresh = await propose("cohort-fresh-preview")
        expected_count = int(fresh["preview"]["cohort"]["count"])
        receipt = await gateway.confirm(
            auth,
            str(fresh["id"]),
            expected_version=int(fresh["version"]),
            content_sha256=str(fresh["contentSha256"]),
            request_id="cohort-fresh-confirm",
        )
        assert receipt["status"] == "succeeded"
        assert receipt["affectedCount"] == expected_count
        async with engine.connect() as connection:
            after = int(await connection.scalar(text("SELECT COUNT(*) FROM staff_work_item")) or 0)
            batch_rows = int(
                await connection.scalar(
                    text("SELECT COUNT(*) FROM agent_action_batch_item WHERE action_intent_id=:id"),
                    {"id": fresh["id"]},
                )
                or 0
            )
        assert after == before + expected_count
        assert batch_rows == expected_count

    _run(scenario)


def test_card_scope_excludes_old_pending_actions_but_retains_new_actions() -> None:
    async def scenario(
        engine: Any, gateway: EdwardActionGateway, portal: PostgresPortalRepository
    ) -> None:
        auth = _staff()
        repository = gateway._staff_assistant
        conversation = await repository.create_conversation(auth)
        conversation_id = str(conversation["id"])
        request = parse_staff_action("Create a follow-up for Alex, assign it to me.")
        assert request is not None
        ids = []
        for anchor in ("old-scope", "new-scope"):
            intent = await gateway.propose_staff(
                auth,
                request,
                conversation_id=conversation_id,
                trace_id=anchor,
                resolved_student_id=HARVARD_STUDENT_ID,
            )
            ids.append(intent["id"])
            await repository.append_exchange(
                auth,
                conversation_id=conversation_id,
                user_message={"content": "Create a follow-up", "clientMessageId": anchor},
                assistant_message={
                    "content": "Review this action",
                    "provider": "guided",
                    "actionIntents": [intent],
                },
                referenced_student_id=HARVARD_STUDENT_ID,
                request_id=anchor,
            )
        all_actions = await gateway.conversation_actions(auth, conversation_id)
        scoped = await gateway.conversation_actions(
            auth, conversation_id, history_after="new-scope"
        )
        absent = await gateway.conversation_actions(auth, conversation_id, history_after="not-sent")
        assert len(all_actions["pending"]) == 2
        assert len(scoped["pending"]) == 1
        assert scoped["pending"][0]["intentId"] == ids[1]
        assert absent["pending"] == []

    _run(scenario)
