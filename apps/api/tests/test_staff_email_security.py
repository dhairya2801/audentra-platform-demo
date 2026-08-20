from __future__ import annotations

import asyncio
import inspect

import pytest

from audentra.infrastructure.postgres.staff_email_service import (
    canonical_staff_return_path,
)
from audentra.integrations.staff_assistant.classify import classify_staff_request
from audentra.integrations.staff_assistant.normalize import normalize_staff_request
from audentra.integrations.staff_assistant.pipeline import _SKIP_REWRITE
from audentra.integrations.staff_assistant.tools import (
    PlannedToolCall,
    StaffAssistantToolHost,
    execute_staff_tool_reads,
)
from audentra.interfaces.http.mail_routes import complete_staff_sso


@pytest.mark.parametrize(
    "candidate",
    [
        "https://attacker.example/steal",
        "//attacker.example/steal",
        "/staff/../../attacker",
        "/staff/../student",
        "/staff%2f%2fattacker.example",
        "/staff%252f%252fattacker.example",
        "/staff\\attacker",
        "/harvard/staff/../../attacker",
        "/aster/staff/../harvard/staff",
        "/harvard/staff%2f%2fattacker.example",
        "/harvard/staff%252f%252fattacker.example",
        "/harvard/staff\\attacker",
        "/harvard/student",
        "/aster/staff",
    ],
)
def test_staff_return_path_rejects_external_cross_tenant_and_ambiguous_paths(
    candidate: str,
) -> None:
    assert canonical_staff_return_path("harvard", candidate) == "/staff"


def test_staff_return_path_preserves_only_canonical_staff_portal_routes() -> None:
    assert (
        canonical_staff_return_path("harvard", "/staff/tasks?selected=123&tab=next_step")
        == "/staff/tasks?selected=123&tab=next_step"
    )


def test_sso_callback_has_no_tenant_selector() -> None:
    parameters = inspect.signature(complete_staff_sso).parameters
    assert "tenant_slug" not in parameters
    assert "tenantSlug" not in parameters
    assert {"provider", "state", "code"}.issubset(parameters)


def test_oauth_migration_binds_state_to_expected_tenant() -> None:
    migration = (
        __import__("pathlib").Path(__file__).parents[1]
        / "migrations"
        / "0040_staff_sso_and_email.sql"
    ).read_text(encoding="utf-8")
    assert "state_hash char(64) NOT NULL UNIQUE" in migration
    assert "tenant_id uuid NOT NULL REFERENCES tenant(id)" in migration
    assert "expected_provider_tenant varchar(255) NOT NULL" in migration
    assert "consumed_at timestamptz" in migration


def test_edward_routes_inbox_reads_without_turning_them_into_send_actions() -> None:
    classification = classify_staff_request(
        normalize_staff_request("Read my inbox and prioritize the top 10 emails")
    )
    assert classification is not None
    assert classification.request_type == "mailbox_read"
    assert "mailbox_read" in _SKIP_REWRITE


def test_edward_mailbox_tool_preserves_acl_host_result_and_receipt() -> None:
    async def mailbox_messages(*, query: str, limit: int) -> dict[str, object]:
        assert query == ""
        assert limit == 10
        return {
            "items": [
                {
                    "id": "message-1",
                    "sender": "student@example.edu",
                    "subject": "Urgent deadline question",
                    "priorityScore": 2,
                }
            ],
            "total": 1,
        }

    execution = asyncio.run(
        execute_staff_tool_reads(
            [PlannedToolCall(tool="getMailboxMessages")],
            StaffAssistantToolHost({"mailbox_messages": mailbox_messages}),
        )
    )
    assert execution.executed_tools == ["getMailboxMessages"]
    assert execution.reads["getMailboxMessages"]["status"] == "available"
    assert execution.receipts[0]["source"] == "authorized_mailboxes"
