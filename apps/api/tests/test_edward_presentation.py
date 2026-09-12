"""Safety and fidelity of semantic UI construction; no provider required."""

from audentra.domain.edward_action_recognizer import parse_staff_action, parse_student_action
from audentra.integrations.assistant.guard import guard_grounded_answer
from audentra.integrations.assistant.presentation import response_blocks
from audentra.integrations.assistant.read_loop import (
    LoopCall,
    ReadLoopResult,
    evidence_from_calls,
)


def test_record_values_are_projected_and_unread_views_cannot_invent_components() -> None:
    result = ReadLoopResult(
        answer="Your payment is pending.",
        rounds=1,
        evidence_texts=[],
        calls=[
            LoopCall(
                "getUniversityAccount",
                {},
                status="available",
                result={
                    "snapshotAt": "2026-09-08T16:00:00Z",
                    "balances": [{"term_id": "2026FA", "balance_cents": 100001}],
                    "payments": [
                        {"method": "bank_transfer", "status": "pending", "amount_cents": 100000}
                    ],
                },
            )
        ],
        presentation={
            "answer": "Your payment is pending.",
            "nextStep": "Ask Student Accounts to verify settlement.",
            "views": ["account", "documents", "arbitrary_html"],
        },
    )
    blocks = response_blocks(result, actor="student")
    assert [b["type"] for b in blocks] == ["answer", "next_action", "facts", "record_context"]
    facts = blocks[2]
    assert facts["items"][0]["value"] == "$1,000.01"
    assert facts["items"][1]["value"] == "$1,000.00"
    assert facts["provenance"]["tool"] == "getUniversityAccount"
    assert all(b["fallbackText"] for b in blocks)
    assert "href" not in blocks[1]  # prose cannot create an action button


def test_failed_reads_and_inapplicable_sources_never_become_trust_cards() -> None:
    result = ReadLoopResult(
        answer="Not verified.",
        rounds=1,
        evidence_texts=[],
        calls=[
            LoopCall(
                "getUniversityAccount",
                {},
                status="unavailable",
                result={"balances": [{"balance_cents": 0}]},
            ),
            LoopCall(
                "getInstitutionalPolicies",
                {},
                status="available",
                result={
                    "sources": [
                        {
                            "citation": "wrong",
                            "content_hash": "hash",
                            "applicability": "does_not_apply",
                        },
                        {"citation": "no provenance", "applicability": "applies"},
                        {
                            "citation": "correct@1",
                            "content_hash": "hash",
                            "applicability": "applies",
                            "body": "Verified text",
                        },
                    ]
                },
            ),
        ],
        presentation={"answer": "Not verified.", "views": ["account"]},
    )
    blocks = response_blocks(result, actor="student")
    assert [b["type"] for b in blocks] == ["answer", "sources"]
    assert [s["citation"] for s in blocks[1]["items"]] == ["correct@1"]


def test_percentage_evidence_allows_exact_formatting_without_relaxing_guard() -> None:
    evidence = evidence_from_calls(
        [LoopCall("getStudentRequirements", {}, status="available", result={"progressPercent": 0})]
    )
    assert guard_grounded_answer(answer="Progress is 0%.", evidence_texts=evidence).accepted
    assert not guard_grounded_answer(answer="Progress is 100%.", evidence_texts=evidence).accepted


def test_contact_question_is_a_read_but_explicit_outreach_remains_an_action() -> None:
    assert parse_student_action("who do i talk to then") is None
    assert parse_student_action("Who should I speak to about my payment?") is None
    request = parse_student_action("Please have someone check my pending payment")
    assert request is not None and request.action == "student.support.contact"


def test_negated_send_is_not_an_email_preparation_request() -> None:
    assert parse_staff_action("don't send it. what else is missing?") is None
    assert parse_staff_action("Do not prepare that email") is None
    request = parse_staff_action("send it")
    assert request is not None and request.action == "communications.email.prepare"


def test_account_totals_keep_pending_held_posted_and_future_terms_separate() -> None:
    from audentra.infrastructure.postgres.university_repository import account_amount_totals

    totals = account_amount_totals(
        [
            {"term_id": "2026FA", "status": "held", "amount_cents": 200000},
            {"term_id": "2026FA", "status": "held", "amount_cents": 250000},
            {"term_id": "2026FA", "status": "posted", "amount_cents": 300000},
            {"term_id": "2027SP", "status": "scheduled", "amount_cents": 200000},
        ],
        "status",
    )
    assert totals == [
        {"term_id": "2026FA", "status": "held", "amount_cents": 450000},
        {"term_id": "2026FA", "status": "posted", "amount_cents": 300000},
        {"term_id": "2027SP", "status": "scheduled", "amount_cents": 200000},
    ]


def test_long_guarded_prose_is_disclosed_without_losing_text() -> None:
    first = "Your submission is complete."
    detail = "The university still needs to review the file. " * 8
    result = ReadLoopResult(answer=first + " " + detail, calls=[], rounds=1, evidence_texts=[])
    blocks = response_blocks(result, actor="student")
    assert blocks[0]["text"] == first
    assert blocks[1]["type"] == "explanation"
    assert blocks[1]["text"] == detail.strip()


def test_sources_include_distinct_queries_of_the_same_tool() -> None:
    calls = [
        LoopCall(
            "getInstitutionalPolicies",
            {"query": query},
            status="available",
            result={
                "sources": [
                    {
                        "citation": query,
                        "content_hash": "hash",
                        "applicability": "applies",
                        "body": query,
                    }
                ]
            },
        )
        for query in ["aid", "housing"]
    ]
    result = ReadLoopResult(
        answer="Both policies matter.", calls=calls, rounds=1, evidence_texts=[]
    )
    block = response_blocks(result, actor="student")[1]
    assert {s["citation"] for s in block["items"]} == {"aid", "housing"}


def test_staff_negative_action_does_not_preempt_a_followup_read() -> None:
    from audentra.integrations.staff_assistant.normalize import normalize_staff_request

    request = normalize_staff_request(
        "don't send it. what else is missing?",
        history=[{"role": "assistant", "content": "Here is the draft about the student's aid."}],
    )
    assert request.action_kind is None
    assert request.is_follow_up
    assert normalize_staff_request("draft a short explanation for them").is_draft_request


def test_blocked_requirement_does_not_invent_a_dependency_on_an_open_aid_case() -> None:
    from audentra.domain.student_state import requirement_readiness

    item = {"status": "blocked", "dependencyCodes": ["deposit", "identity"]}
    complete = [
        {"code": "deposit", "status": "completed"},
        {"code": "identity", "status": "waived"},
        {"code": "aid", "status": "under_review"},
    ]
    result = requirement_readiness(item, complete)
    assert result["unmetDependencyCodes"] == []
    assert "reason is not established" in result["readinessNote"]
    incomplete = requirement_readiness(item, [{"code": "deposit", "status": "completed"}])
    assert incomplete["unmetDependencyCodes"] == ["identity"]
    assert requirement_readiness({"status": "ready"}, complete) == {}


def test_staff_queue_totals_explicitly_separate_institution_and_personal_scope() -> None:
    import asyncio
    from datetime import UTC, datetime

    from audentra.integrations.staff_assistant.tools import StaffAssistantToolHost, _tool_work_queue

    async def read(**_kwargs: object) -> dict[str, object]:
        return {"items": [], "page": {"total": 0}, "counts": {"overdue": 813}}

    host = StaffAssistantToolHost({"work_queue": read}, staff_member_id="staff")
    result = asyncio.run(_tool_work_queue(host, {"ownership": "mine"}, datetime.now(UTC)))
    assert result["filteredTotal"] == 0
    assert result["counts"]["overdue"] == 813
    assert "never personal totals" in result["countScopes"]["counts"]


def test_optional_queue_identity_does_not_silently_narrow_department_totals() -> None:
    import asyncio

    from audentra.integrations.staff_assistant.pipeline import StaffAssistantPipeline
    from audentra.integrations.staff_assistant.tools import StaffAssistantToolHost

    pipeline = StaffAssistantPipeline(StaffAssistantToolHost({}))
    handles = {"me": "staff-me", "student": "previous-student"}
    for tool in ["summarizeWorkQueue", "searchWorkQueue", "summarizeInquiries"]:
        call = LoopCall(tool, {"ownership": "all"})
        bound = asyncio.run(pipeline._bind_loop_arguments(call, handles))
        assert bound is not None
        assert "staffId" not in bound.arguments
        assert "studentId" not in bound.arguments
    requested = asyncio.run(
        pipeline._bind_loop_arguments(LoopCall("summarizeWorkQueue", {"staffId": "me"}), handles)
    )
    assert requested is not None and requested.arguments["staffId"] == "staff-me"
    required = asyncio.run(pipeline._bind_loop_arguments(LoopCall("getStaffProfile", {}), handles))
    assert required is not None and required.arguments["staffId"] == "staff-me"


def test_staff_requirement_totals_do_not_count_blocked_items_as_complete() -> None:
    import asyncio
    from datetime import UTC, datetime

    from audentra.integrations.staff_assistant.tools import (
        StaffAssistantToolHost,
        _tool_student_requirements,
    )

    async def read(**_kwargs: object) -> dict[str, object]:
        return {"items": [{"code": "a", "status": "completed"}, {"code": "b", "status": "blocked"}]}

    host = StaffAssistantToolHost({"student_requirements": read})
    result = asyncio.run(
        _tool_student_requirements(host, {"studentId": "student"}, datetime.now(UTC))
    )
    assert result["completedCount"] == 1
    assert result["openCount"] == 1
    assert result["total"] == 2
