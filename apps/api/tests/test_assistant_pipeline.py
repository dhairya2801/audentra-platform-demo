"""Behavioral tests for the Edward assistant pipeline.

Ported intents from VV_Edgent-voice student-assistant-core tests: the
conversational gate answers without state reads, plans stay inside the
allowlist, multi-domain questions read every domain they name, the claim
guard rejects ungrounded model prose, and answers stay fresh against the
record read at question time.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from audentra.application.platform_service import InMemoryPlatformService
from audentra.core.auth import AuthContext
from audentra.core.ports import ServiceCall
from audentra.infrastructure.memory.store import DEMO_IDS
from audentra.integrations.assistant.classify import Classification
from audentra.integrations.assistant.guard import build_causal_guards, guard_grounded_answer
from audentra.integrations.assistant.pipeline import AssistantPipeline, AssistantPipelineResult
from audentra.integrations.assistant.planner import validate_model_tool_plan
from audentra.integrations.assistant.tools import AssistantToolHost, PrimitiveRead


class RecordingHost(AssistantToolHost):
    """A host whose primitive reads are recorded, for read-boundary tests."""

    def __init__(self, primitives: dict[str, dict[str, Any]]) -> None:
        self.read_primitives: list[str] = []

        def reader(name: str, value: dict[str, Any]) -> PrimitiveRead:
            async def read() -> dict[str, Any]:
                self.read_primitives.append(name)
                return value

            return read

        super().__init__({name: reader(name, value) for name, value in primitives.items()})


def _full_primitives() -> dict[str, dict[str, Any]]:
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
                    "dueAt": "2026-08-20T00:00:00Z",
                    "documentCategory": "transcript",
                },
                {
                    "id": "req-2",
                    "code": "housing_preference",
                    "slug": "housing-preference",
                    "title": "Housing preference",
                    "status": "completed",
                    "blocking": False,
                    "documentCategory": None,
                },
            ]
        },
        "documents": {"items": []},
        "payments": {"items": []},
        "financials": {
            "academicYear": "2026-2027",
            "costOfAttendanceCents": 3_200_000,
            "acceptedAidCents": 1_500_000,
            "pendingAidCents": 250_000,
            "paymentsCents": 100_000,
            "remainingBalanceCents": 1_600_000,
            "awards": [
                {
                    "name": "Aster Grant",
                    "type": "grant",
                    "status": "accepted",
                    "offeredAmountCents": 1_000_000,
                    "acceptedAmountCents": 1_000_000,
                    "requiresAction": False,
                },
                {
                    "name": "Merit Scholarship",
                    "type": "scholarship",
                    "status": "offered",
                    "offeredAmountCents": 250_000,
                    "acceptedAmountCents": 0,
                    "requiresAction": True,
                },
            ],
            "requiredDocuments": [
                {
                    "code": "fafsa",
                    "title": "FAFSA",
                    "status": "received",
                    "dueAt": None,
                    "href": "/financials",
                },
                {
                    "code": "verification_worksheet",
                    "title": "Verification worksheet",
                    "status": "pending",
                    "dueAt": "2026-08-25T00:00:00Z",
                    "href": "/financials",
                },
            ],
            "paymentSchedule": [],
        },
        "dashboard": {
            "offer": {"id": "offer-1", "depositAmountCents": 50_000, "depositPaid": False},
            "journey": {"nextAction": {"kind": "pay_deposit"}},
        },
        "housing_plan": {"preference": "on_campus", "residences": []},
        "appointments": {"items": []},
        "help": {"articles": []},
        "academics": {
            "selectedProgram": {"name": "Computer Science", "degree": "Bachelor of Science"},
            "catalogVersion": "2026",
            "plan": [
                {
                    "course": {"code": "CS 101", "title": "Programming Fundamentals"},
                    "recommendedTerm": "Fall 2026",
                    "status": "planned",
                    "missingPrerequisiteCodes": [],
                },
                {
                    "course": {"code": "CS 210", "title": "Data Structures"},
                    "recommendedTerm": "Spring 2027",
                    "status": "planned",
                    "missingPrerequisiteCodes": ["CS 101"],
                },
            ],
            "exemptionRecommendations": [],
        },
        "campus_life": {
            "events": [
                {
                    "title": "Welcome Week Fair",
                    "startsAt": "2026-08-24T17:00:00Z",
                    "location": "Main Quad",
                    "category": "social",
                }
            ],
            "clubs": [
                {
                    "name": "Robotics Club",
                    "category": "engineering",
                    "description": "Build robots.",
                    "nextActivity": None,
                }
            ],
        },
        "messages": {
            "items": [
                {
                    "id": "msg-1",
                    "subject": "Orientation schedule posted",
                    "sentAt": "2026-08-10T12:00:00Z",
                    "readAt": None,
                },
                {
                    "id": "msg-2",
                    "subject": "Welcome to Aster",
                    "sentAt": "2026-08-01T12:00:00Z",
                    "readAt": "2026-08-02T12:00:00Z",
                },
            ],
            "unreadCount": 1,
        },
    }


def _run(
    pipeline: AssistantPipeline,
    message: str,
    history: list[dict[str, str]] | None = None,
) -> AssistantPipelineResult:
    return asyncio.run(pipeline.execute(message=message, history=history or []))


def test_greeting_reads_only_the_profile() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "Hi")

    assert result.classification is not None
    assert result.classification.request_type == "greeting"
    assert host.read_primitives == ["profile"]
    assert "Alex" in result.message
    assert result.provider == "guided"


def test_capability_overview_reads_nothing() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "What can you do?")

    assert host.read_primitives == []
    assert "checklist" in result.message


def test_academic_plan_question_reads_academics_not_the_checklist() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(
        AssistantPipeline(host), "Which classes and prerequisites are in my academic plan?"
    )

    assert result.classification is not None
    assert result.classification.request_type == "academic_plan"
    assert host.read_primitives == ["academics"]
    assert [receipt["source"] for receipt in result.context_receipts] == ["academics"]
    assert "Computer Science" in result.message
    table = next(block for block in result.blocks if block["type"] == "table")
    assert any(row.get("code") == "CS 210" for row in table["rows"])
    assert any("CS 101" in row.get("prerequisites", "") for row in table["rows"])


def test_campus_life_question_reads_campus_life_only() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "Which clubs and campus events can I join?")

    assert result.classification is not None
    assert result.classification.request_type == "campus_life"
    assert host.read_primitives == ["campus_life"]
    assert [receipt["source"] for receipt in result.context_receipts] == ["campus_life"]
    assert "Robotics Club" in "".join(str(block) for block in result.blocks)
    assert "Welcome Week Fair" in "".join(str(block) for block in result.blocks)


def test_unread_messages_question_reads_messages_only() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "Do I have unread messages?")

    assert result.classification is not None
    assert result.classification.request_type == "messages_unread"
    assert host.read_primitives == ["messages"]
    assert [receipt["source"] for receipt in result.context_receipts] == ["messages"]
    assert "1 unread message" in result.message
    assert "Orientation schedule posted" in "".join(str(block) for block in result.blocks)


def test_model_tool_plan_is_allowlist_filtered_not_discarded() -> None:
    validated = validate_model_tool_plan(
        {
            "requestType": "aid_summary",
            "confidence": 0.9,
            "toolNames": [
                "getFinancialAidSummary",
                "getStudentHousingStatus",  # outside aid_summary's allowlist
            ],
        }
    )
    assert validated is not None
    classification, tools = validated
    assert classification.request_type == "aid_summary"
    assert "getStudentHousingStatus" not in tools
    assert tools == ["getFinancialAidSummary"]


def test_model_tool_plan_rejects_low_confidence_and_unknown_tools() -> None:
    assert (
        validate_model_tool_plan(
            {
                "requestType": "aid_summary",
                "confidence": 0.4,
                "toolNames": ["getFinancialAidSummary"],
            }
        )
        is None
    )
    assert (
        validate_model_tool_plan(
            {"requestType": "aid_summary", "confidence": 0.9, "toolNames": ["dropTables"]}
        )
        is None
    )


def test_multi_domain_question_reads_both_domains() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "I paid my deposit. Why can't I apply for housing?")

    sources = {receipt["source"] for receipt in result.context_receipts}
    assert {"housing", "holds"} <= sources
    # The blocker answer names what actually blocks and how to clear it.
    assert result.derived is not None
    blocker_titles = [b["title"] for b in result.derived.derived_blockers]
    assert any("deposit" in title.lower() for title in blocker_titles)


def test_financial_aid_summary_renders_award_table_from_the_record() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "What financial aid do I have?")

    table = next(block for block in result.blocks if block["type"] == "table")
    award_names = {row["award"] for row in table["rows"]}
    assert award_names == {"Aster Grant", "Merit Scholarship"}
    assert any("$10,000" in row["accepted"] for row in table["rows"])


def test_aid_verification_and_fafsa_questions_answer_from_requirements() -> None:
    host = RecordingHost(_full_primitives())
    fafsa = _run(AssistantPipeline(host), "Has my FAFSA been received?")
    assert "received" in fafsa.message.lower()

    verification = _run(
        AssistantPipeline(RecordingHost(_full_primitives())),
        "What is the status of my verification worksheet?",
    )
    assert "verification worksheet" in verification.message.lower()
    assert "pending" in verification.message.lower()


def test_claim_guard_rejects_invented_amount_and_falls_back() -> None:
    async def lying_composer(**_kwargs: Any) -> dict[str, Any]:
        return {"answer": "Your aid package totals $99,999 and everything is complete."}

    host = RecordingHost(_full_primitives())
    result = _run(
        AssistantPipeline(host, model_composer=lying_composer), "What financial aid do I have?"
    )

    assert "99,999" not in result.message
    assert result.provider == "guided"
    assert any(code.startswith("written_answer_rejected") for code in result.failure_codes)


def test_claim_guard_accepts_grounded_prose() -> None:
    async def honest_composer(**kwargs: Any) -> dict[str, Any]:
        return {
            "answer": "You've accepted the Aster Grant worth $10,000.",
            "provider": "openrouter",
            "model": "test-model",
        }

    host = RecordingHost(_full_primitives())
    result = _run(
        AssistantPipeline(host, model_composer=honest_composer), "What financial aid do I have?"
    )

    assert result.provider == "openrouter"
    assert "$10,000" in result.message
    # Structured blocks from the deterministic draft ride along with the prose.
    assert any(block["type"] == "table" for block in result.blocks)


def test_invented_registration_causation_is_rejected() -> None:
    guards = build_causal_guards(
        registration_gates=[
            {"code": "final_transcript", "satisfied": False},
            {"code": "enrollment_deposit_posted", "satisfied": True},
        ]
    )
    verdict = guard_grounded_answer(
        answer="You cannot register because your financial aid is incomplete.",
        evidence_texts=["Registration gate (open): Final transcript"],
        causal_guards=guards,
    )
    assert not verdict.accepted
    assert verdict.reason_code == "invented_causation"

    true_cause = guard_grounded_answer(
        answer="You cannot register because your transcript requirement is still open.",
        evidence_texts=["Registration gate (open): Final transcript"],
        causal_guards=guards,
    )
    assert true_cause.accepted


def test_contradicted_document_state_is_rejected() -> None:
    verdict = guard_grounded_answer(
        answer="Your transcript has not been submitted yet.",
        evidence_texts=["Document Final transcript: under review"],
        document_states=[{"title": "Final transcript", "submissionState": "under_review"}],
    )
    assert not verdict.accepted
    assert verdict.reason_code == "contradicted_document_state"


def test_mutation_requests_are_refused_without_model_calls() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "Please submit my transcript for me")

    assert result.classification is not None
    assert result.classification.request_type == "unsupported_or_out_of_scope"
    assert "read-only" in result.message
    assert host.read_primitives == []


def test_unavailable_course_grade_does_not_become_generic_enrollment_advice() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "What grade did I get in my first class?")

    assert result.classification is not None
    assert result.classification.request_type == "unsupported_or_out_of_scope"
    assert "can't check" in result.message
    assert host.read_primitives == []


def test_document_freshness_next_answer_reflects_mutated_state() -> None:
    """The systemic freshness invariant: state mutated between two asks is
    visible in the very next answer, because every read happens at question
    time."""

    primitives = _full_primitives()
    host = RecordingHost(primitives)
    before = _run(AssistantPipeline(host), "Have I uploaded my transcript?")
    assert "not submitted" in before.message.lower()

    # The student uploads a transcript: the document list gains a row and the
    # requirement flips to under review.
    primitives["documents"]["items"].append(
        {
            "id": "doc-1",
            "fileName": "final-transcript.pdf",
            "category": "transcript",
            "status": "uploaded",
            "requirementId": "req-1",
            "createdAt": "2026-08-11T12:00:00Z",
        }
    )
    primitives["requirements"]["items"][0]["status"] = "under_review"

    after = _run(AssistantPipeline(RecordingHost(primitives)), "Have I uploaded my transcript?")
    assert "under review" in after.message.lower()
    assert "not submitted" not in after.message.lower()


AUTH = AuthContext(
    tenant_id=DEMO_IDS["tenant_id"],
    student_id=DEMO_IDS["student_id"],
    actor_id=DEMO_IDS["person_id"],
    actor_type="student",
)


def _ask(
    service: InMemoryPlatformService,
    payload: dict[str, Any],
    request_id: str = "req-1",
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        asyncio.run(
            service.dispatch(
                ServiceCall(
                    operation="student.ask_edward",
                    auth=AUTH,
                    request_id=request_id,
                    payload=payload,
                )
            )
        ),
    )


def test_conversation_persists_turns_and_replays_idempotently() -> None:
    service = InMemoryPlatformService()
    created = cast(
        dict[str, Any],
        asyncio.run(
            service.dispatch(
                ServiceCall(
                    operation="student.create_assistant_conversation",
                    auth=AUTH,
                    request_id="req-0",
                    payload={"pageContext": {"path": "/edward", "label": "Edward"}},
                )
            )
        ),
    )
    assert created["status"] == "active"

    first = _ask(
        service,
        {
            "message": "What documents am I missing?",
            "pageContext": "/edward",
            "conversationId": created["id"],
            "clientMessageId": "client-msg-00000001",
        },
    )
    assert first["conversationId"] == created["id"]
    assert first["userMessageId"] and first["assistantMessageId"]

    replay = _ask(
        service,
        {
            "message": "What documents am I missing?",
            "pageContext": "/edward",
            "conversationId": created["id"],
            "clientMessageId": "client-msg-00000001",
        },
        request_id="req-2",
    )
    assert replay["userMessageId"] == first["userMessageId"]
    assert replay["assistantMessageId"] == first["assistantMessageId"]

    messages = cast(
        dict[str, Any],
        asyncio.run(
            service.dispatch(
                ServiceCall(
                    operation="student.get_assistant_conversation_messages",
                    auth=AUTH,
                    request_id="req-3",
                    payload={},
                    path_params={"conversationId": created["id"]},
                )
            )
        ),
    )
    roles = [item["role"] for item in messages["messages"]]
    assert roles == ["user", "assistant"]


def test_stateless_ask_persists_nothing() -> None:
    service = InMemoryPlatformService()
    response = _ask(service, {"message": "Hi", "pageContext": "/dashboard"})
    assert "conversationId" not in response
    assert service.store.assistant_messages == []


def test_greeting_via_service_reads_no_dashboard() -> None:
    service = InMemoryPlatformService()
    response = _ask(service, {"message": "Hello", "pageContext": "/dashboard"})
    assert response["provider"] == "guided"
    sources = {receipt["source"] for receipt in response["contextReceipts"]}
    assert sources <= {"profile"}


def test_selected_classifications() -> None:
    from audentra.integrations.assistant.classify import classify
    from audentra.integrations.assistant.normalize import normalize_request

    cases = {
        "Hi": "greeting",
        "What can you help me with?": "capability_overview",
        "What do I need to do next?": "next_action",
        "Do I have any holds?": "holds_and_blockers",
        "Why can't I register?": "registration_status",
        "Have I uploaded my transcript?": "document_status",
        "What documents am I missing?": "missing_documents",
        "What aid have I accepted?": "aid_award_acceptance_status",
        "Why is my aid incomplete?": "aid_incomplete_reason",
        "When does my aid disburse?": "aid_disbursement",
        # Without an explicit aid word this is an account-balance question;
        # the composer answers it with the same remaining-balance figure.
        "How much do I still owe?": "student_account",
        "How much do I owe after my aid?": "aid_coverage",
        # Eligibility questions route to the dedicated derived capability so
        # the answer can name what actually gates the housing step.
        "Can I apply for housing?": "housing_eligibility",
        "What housing options are there?": "housing_options",
    }
    for message, expected in cases.items():
        classification = classify(normalize_request(message))
        assert classification is not None, message
        assert classification.request_type == expected, (
            f"{message!r} → {classification.request_type}, expected {expected}"
        )


def test_classification_dataclass_shape() -> None:
    classification = Classification("greeting", 1)
    assert classification.source == "deterministic"
    assert classification.additional_request_types == ()
