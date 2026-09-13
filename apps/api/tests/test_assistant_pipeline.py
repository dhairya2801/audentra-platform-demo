"""Behavioral tests for the Edward assistant pipeline.

Ported intents from VV_Edgent-voice student-assistant-core tests: the
conversational gate answers without state reads, plans stay inside the
allowlist, multi-domain questions read every domain they name, the claim
guard rejects ungrounded model prose, and answers stay fresh against the
record read at question time.
"""

from __future__ import annotations

import asyncio
import re
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
                    # Intentionally far-future: the campus tool excludes past
                    # events, and this fixture must not decay with wall time.
                    "startsAt": "2099-08-24T17:00:00Z",
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
    # It must decline and name the route, not claim to have submitted anything.
    # Asserting on the words "read-only" would pin a sentence that is now false.
    assert "Documents page" in result.message
    assert not re.search(r"\bI(?:'ve| have)?\s+submitted\b", result.message, re.I)
    assert host.read_primitives == []


def test_questions_about_past_changes_do_not_become_write_requests() -> None:
    from audentra.integrations.assistant.normalize import normalize_request

    for question in (
        "Did anything change on my checklist recently?",
        "Did the office update my requirement?",
        "Was I supposed to upload my transcript?",
    ):
        assert not normalize_request(question).is_mutation_request
    # A later explicit instruction still reaches the action boundary.
    assert normalize_request(
        "Did anything change? Please upload my transcript for me."
    ).is_mutation_request


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


# ---------------------------------------------------------------------------
# Portal navigation: "where do I …" is product help, not a status question.
# Every destination is a route from `links`, so no answer can invent a page.
# ---------------------------------------------------------------------------


def test_where_do_i_upload_names_the_documents_page() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "Where do I upload my immunization records?")

    assert result.classification is not None
    assert result.classification.request_type == "portal_navigation"
    assert "/documents" in _visible(result).lower() or "documents page" in _visible(result).lower()


def test_where_do_i_pay_names_the_payments_page() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "where do i pay the deposit")

    assert result.classification is not None
    assert result.classification.request_type == "portal_navigation"
    assert "payments" in _visible(result).lower()


def test_navigation_answers_carry_a_real_route_only() -> None:
    from audentra.integrations.assistant import links

    known = {
        value
        for name, value in vars(links).items()
        if name.isupper() and isinstance(value, str) and value.startswith("/")
    }
    host = RecordingHost(_full_primitives())
    for question in (
        "Where can I see my checklist?",
        "Where can I check the status of my transcript?",
        "how do i find my advising appointment",
        "where in the portal do i see what i owe",
    ):
        result = _run(AssistantPipeline(host), question)
        hrefs = [
            item.get("href")
            for block in result.blocks
            for item in block.get("items", [])
            if isinstance(item, dict) and item.get("href")
        ]
        assert hrefs, question
        for href in hrefs:
            path = str(href).split("?")[0]
            # `/enrollment/requirements/{slug}` is the documented per-requirement
            # route, built by `requirement_href` from requirement data.
            assert path in known or path.startswith("/enrollment/requirements/"), (
                question,
                href,
            )


def test_housing_assignment_questions_decline_instead_of_answering_the_step() -> None:
    host = RecordingHost(_full_primitives())
    for question in ("Will I get into my first choice dorm?", "Who is my roommate?"):
        result = _run(AssistantPipeline(host), question)
        assert result.classification is not None
        assert result.classification.request_type == "unsupported_or_out_of_scope"
        assert result.classification.requirement_reference == "housing_assignment_unavailable"
        message = result.message.lower()
        assert "doesn't hold room assignments" in message or "room assignment" in message


def test_future_aid_outcomes_decline_without_model_calls_or_record_inferences() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(
        AssistantPipeline(host),
        "Will I get more scholarship money next year?",
    )

    assert result.classification is not None
    assert result.classification.request_type == "unsupported_or_out_of_scope"
    assert result.classification.requirement_reference == "future_aid_unavailable"
    assert "can't predict or promise" in result.message.lower()
    assert host.read_primitives == []


def test_housing_eligibility_still_answers_from_the_record() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "Why can't I apply for housing?")

    assert result.classification is not None
    assert result.classification.request_type in {
        "housing_eligibility",
        "housing_status",
        "housing_remaining_steps",
    }


def _visible(result: AssistantPipelineResult) -> str:
    extras = [str(block.get("fallbackText") or "") for block in result.blocks]
    return "\n".join([result.message, *extras])


def test_blocking_and_non_blocking_split_does_not_double_count_the_deposit() -> None:
    """The deposit gate is titled differently in the blocker list ("Enrollment
    deposit not posted") and on the checklist ("Pay the enrollment deposit"),
    so a title-based split lists the same gate as both blocking and open-but-
    not-blocking. The split matches canonical gate codes instead."""

    from audentra.integrations.assistant.compose import compose_deterministic
    from audentra.integrations.assistant.derive import DerivedState

    state = DerivedState(
        remaining_steps=[
            {"code": "enrollment_deposit", "title": "Pay the enrollment deposit"},
            {"code": "housing_preference", "title": "Select your housing preference"},
        ],
        derived_blockers=[
            {
                "code": "enrollment_deposit_posted",
                "title": "Enrollment deposit not posted",
                "owner": "student",
                "clearingAction": "Pay the enrollment deposit from the Payments page.",
                "href": "/payments",
            }
        ],
        available_reads=["getEnrollmentHolds"],
    )
    answer = compose_deterministic(Classification("holds_and_blockers", 1), state)
    assert "not blocking" in answer.message.lower()
    assert "housing" in answer.message.lower()
    assert "deposit" not in answer.message.lower().split("separately,")[-1]


def test_enrollment_position_reads_the_checklist_before_summarising() -> None:
    """The enrollment projection carries no open-item list. Answering from it
    alone leaves a silence a rewrite fills with "nothing outstanding"."""

    from audentra.integrations.assistant.compose import compose_deterministic
    from audentra.integrations.assistant.derive import DerivedState
    from audentra.integrations.assistant.planner import select_tool_reads

    assert "getOnboardingChecklist" in select_tool_reads(Classification("enrollment_state", 1))

    # And when the checklist genuinely was not read, the answer says so rather
    # than leaving a silence that reads as "nothing outstanding".
    unread = DerivedState(
        enrollment={"admission": {"offerStatus": "accepted", "programName": "Computer Science"}},
        available_reads=["getEnrollmentState"],
    )
    answer = compose_deterministic(Classification("enrollment_state", 1), unread)
    assert "haven't checked your open checklist" in answer.message

    read_and_clear = DerivedState(
        enrollment={"admission": {"offerStatus": "accepted", "programName": "Computer Science"}},
        available_reads=["getEnrollmentState", "getOnboardingChecklist"],
    )
    answer = compose_deterministic(Classification("enrollment_state", 1), read_and_clear)
    assert "Every checklist item is complete." in answer.message


def test_a_pending_deposit_is_never_offered_as_a_student_action() -> None:
    from audentra.integrations.assistant.compose import step_action_row

    row = step_action_row(
        {
            "title": "Pay the enrollment deposit",
            "href": "/payments",
            "processingPending": True,
        }
    )
    assert row["owner"] == "university"
    assert "already pending" in row["text"]

    ordinary = step_action_row({"title": "Upload an identity document", "href": "/documents"})
    assert ordinary["owner"] == "student"
    assert ordinary["text"] == "Upload an identity document"


def test_a_returned_document_is_named_among_the_outstanding_ones() -> None:
    """A rejected upload is the student's move, and the one they are most
    likely to think is done — counting only never-submitted documents drops
    it from the answer entirely."""

    from audentra.integrations.assistant.compose import compose_deterministic
    from audentra.integrations.assistant.derive import DerivedState

    state = DerivedState(
        document_states=[
            {
                "title": "Submit your official transcript",
                "submissionState": "needs_resubmission",
                "href": "/documents",
            },
            {
                "title": "Upload an identity document",
                "submissionState": "not_submitted",
                "href": "/documents",
            },
        ],
        missing_documents=[
            {
                "title": "Upload an identity document",
                "submissionState": "not_submitted",
                "href": "/documents",
            }
        ],
        available_reads=["getDocumentStatuses", "getOnboardingChecklist"],
    )
    answer = compose_deterministic(Classification("missing_documents", 1), state)
    visible = "\n".join(
        [answer.message, *[str(block.get("fallbackText") or "") for block in answer.blocks]]
    ).lower()
    assert "returned" in visible
    assert "transcript" in visible
    assert "identity" in visible


def test_a_waiver_claim_is_answered_as_a_claim() -> None:
    """ "I thought that was waived" asks whether a waiver exists. Restating the
    requirement's status without answering that reads as not having listened."""

    host = RecordingHost(_full_primitives())
    result = _run(
        AssistantPipeline(host), "I thought the immunization requirement was waived for me."
    )

    assert result.classification is not None
    assert result.classification.claims_waiver is True
    assert "no waiver or exemption is recorded" in _visible(result).lower()


def test_no_waiver_sentence_when_the_student_did_not_claim_one() -> None:
    host = RecordingHost(_full_primitives())
    result = _run(AssistantPipeline(host), "Which documents am I missing?")

    assert "waiver" not in _visible(result).lower()
