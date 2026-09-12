"""The model read loop: bounded rounds, handle-bound identity, guarded answers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from audentra.core.assistant_execution import (
    ReadPlanner,
    resolve_assistant_execution_mode,
    resolve_read_planner,
)
from audentra.integrations.assistant.pipeline import AssistantPipeline
from audentra.integrations.assistant.read_loop import (
    LoopCall,
    LoopTool,
    ModelStep,
    bound_result,
    evidence_from_calls,
    loop_step_schema,
    run_read_loop,
)
from audentra.integrations.assistant.tools import AssistantToolHost
from audentra.integrations.assistant.trace import AssistantTurnTrace


def _tools() -> list[LoopTool]:
    return [
        LoopTool(name="getA", description="read A"),
        LoopTool(name="getB", description="read B"),
    ]


def _scripted(steps: Sequence[Mapping[str, Any]]) -> tuple[ModelStep, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    queue = list(steps)

    async def step(*, messages: Any, schema: Any) -> Mapping[str, Any] | None:
        calls.append({"messages": messages, "schema": schema})
        if not queue:
            return {"reasoning": "done", "calls": [], "answer": "fallback answer"}
        return queue.pop(0)

    return step, calls


async def _executor_ok(calls: Sequence[LoopCall]) -> None:
    for call in calls:
        if call.status != "planned":
            continue
        call.status = "available"
        call.result = {"tool": call.tool, "count": 3, "dueAt": "2026-09-10"}
        call.duration_ms = 4


def test_loop_reads_then_answers_and_flattens_evidence() -> None:
    step, seen = _scripted(
        [
            {"reasoning": "need A", "calls": [{"tool": "getA", "arguments": "{}"}], "answer": None},
            {"reasoning": "answer", "calls": [], "answer": "You have 3 items due 2026-09-10."},
        ]
    )
    result = asyncio.run(
        run_read_loop(
            question="q",
            history=[],
            context={"actor": "student"},
            tools=_tools(),
            model_step=step,
            executor=_executor_ok,
        )
    )
    assert result.answer == "You have 3 items due 2026-09-10."
    assert [call.tool for call in result.calls] == ["getA"]
    assert "getA.count: 3" in result.evidence_texts
    assert result.rounds == 2 and result.outcome == "answered"
    # The trace-facing step log mirrors the rounds as they ran.
    assert [step["outcome"] for step in result.steps] == ["reads", "answered"]
    assert result.steps[0]["reads"][0]["tool"] == "getA"
    # The second step saw the first step's result in its transcript.
    second_message = json.loads(
        seen[1]["messages"][1]["content"].split(">", 1)[1].rsplit("<", 1)[0]
    )
    assert second_message["stepsSoFar"][0]["reads"][0]["result"]["count"] == 3


def test_loop_is_bounded_and_forces_an_answer() -> None:
    endless = {"reasoning": "more", "calls": [{"tool": "getB", "arguments": "{}"}], "answer": None}
    step, seen = _scripted([endless, endless, endless, endless, endless])
    result = asyncio.run(
        run_read_loop(
            question="q",
            history=[],
            context={},
            tools=_tools(),
            model_step=step,
            executor=_executor_ok,
            max_rounds=2,
        )
    )
    # Two plan rounds, then a forced answer round whose schema forbids calls.
    assert len(seen) == 3
    assert seen[2]["schema"]["properties"]["calls"]["maxItems"] == 0
    assert result.rounds == 3
    # The forced round's scripted step still carried a call; it is ignored and
    # the missing answer is reported honestly.
    assert result.answer is None and result.outcome == "no_answer"


def test_loop_rejects_raw_identifiers_and_bad_arguments() -> None:
    step, _ = _scripted(
        [
            {
                "reasoning": "x",
                "calls": [
                    {
                        "tool": "getA",
                        "arguments": '{"studentId": "8f1a2b3c-1111-2222-3333-444444444444"}',
                    },
                    {"tool": "getB", "arguments": "not json at all (("},
                    {"tool": "getB", "arguments": "{staffId: 'me'}"},
                ],
                "answer": None,
            },
            {"reasoning": "answer", "calls": [], "answer": "ok"},
        ]
    )
    executed: list[LoopCall] = []

    async def executor(calls: Sequence[LoopCall]) -> None:
        executed.extend(call for call in calls if call.status == "planned")
        await _executor_ok(calls)

    result = asyncio.run(
        run_read_loop(
            question="q", history=[], context={}, tools=_tools(), model_step=step, executor=executor
        )
    )
    statuses = [(call.tool, call.status) for call in result.calls]
    assert statuses[0] == ("getA", "rejected")
    assert result.calls[0].reason == "identifier_in_arguments"
    assert statuses[1] == ("getB", "rejected")
    assert "JSON object" in str(result.calls[1].reason)
    # Loose near-JSON is repaired rather than refused.
    assert statuses[2] == ("getB", "available")
    assert executed[0].arguments == {"staffId": "me"}


def test_loop_reports_provider_failure_as_outcome() -> None:
    async def failing(**_: Any) -> Mapping[str, Any] | None:
        raise RuntimeError("boom")

    result = asyncio.run(
        run_read_loop(
            question="q",
            history=[],
            context={},
            tools=_tools(),
            model_step=failing,
            executor=_executor_ok,
        )
    )
    assert result.answer is None and result.outcome == "model_error"
    assert result.model_calls[0]["outcome"] == "model_error"


def test_bound_result_trims_lists_structurally() -> None:
    value = {"items": [{"n": i, "text": "x" * 50} for i in range(400)]}
    bounded = bound_result(value, limit=3_000)
    assert isinstance(bounded["items"], list)
    assert bounded["items"][-1].get("truncatedItems")
    assert len(json.dumps(bounded)) <= 3_000


def test_step_schema_is_strict() -> None:
    schema = loop_step_schema(["getA"], allow_calls=True)
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    items = schema["properties"]["calls"]["items"]
    assert set(items["required"]) == set(items["properties"])


def test_money_is_humanized_before_the_model_and_the_guard_see_it() -> None:
    call = LoopCall(
        tool="getStudentAccountSummary",
        arguments={},
        status="available",
        result={"remainingBalanceCents": 1700500, "depositAmountCents": 50000, "feeCents": 1250},
    )
    lines = evidence_from_calls([call])
    assert "getStudentAccountSummary.remainingBalance: $17,005" in lines
    assert "getStudentAccountSummary.depositAmount: $500" in lines
    assert "getStudentAccountSummary.fee: $12.50" in lines
    # The raw cents integer is gone, so "$1,700,500" (cents read as dollars)
    # has nothing to match and the guard rejects it.
    from audentra.integrations.assistant.guard import ungrounded_tokens

    assert ungrounded_tokens("You owe $17,005.00; the $500 deposit and a $12.50 fee.", lines) == {
        "contacts": [],
        "dates": [],
        "numbers": [],
    }
    assert ungrounded_tokens("You owe $1,700,500.", lines)["numbers"] == ["1700500"]
    assert bound_result({"items": [{"amountCents": 999}]}) == {"items": [{"amount": "$9.99"}]}


def test_evidence_lines_skip_empty_values() -> None:
    call = LoopCall(
        tool="t",
        arguments={},
        status="available",
        result={"a": None, "b": "", "c": [1, {"d": "x"}]},
    )
    assert evidence_from_calls([call]) == ["t.c[0]: 1", "t.c[1].d: x"]


# --- read planner resolution ---------------------------------------------


def test_read_planner_header_is_honoured_only_with_lab_controls() -> None:
    resolved = resolve_assistant_execution_mode(
        None, lab_controls_enabled=True, read_planner_raw="model"
    )
    assert resolved.read_planner is ReadPlanner.MODEL
    ignored = resolve_assistant_execution_mode(
        None, lab_controls_enabled=False, read_planner_raw="model"
    )
    assert ignored.read_planner is None
    assert resolve_read_planner("HYBRID") is ReadPlanner.HYBRID
    assert resolve_read_planner("nonsense", default="model") is ReadPlanner.MODEL
    assert resolve_read_planner(None) is ReadPlanner.DETERMINISTIC


# --- student pipeline integration ------------------------------------------


def _student_host() -> AssistantToolHost:
    async def profile() -> Mapping[str, Any]:
        return {
            "preferredName": "Hana",
            "firstName": "Hana",
            "lastName": "Dunmire",
            "email": "hana@example.edu",
            "emailVerified": True,
            "phoneVerified": False,
            "communicationPreference": "email",
        }

    async def advising() -> Mapping[str, Any]:
        return {
            "primaryAdviser": {
                "role": "primary_advisor",
                "staff": {
                    "name": "Ada Ashgrove",
                    "title": "Academic Adviser",
                    "email": "ada.ashgrove@aster.example",
                    "officeLocation": "Advising Centre, Room 200",
                    "employmentStatus": "active",
                },
                "availability": {"bookable": True, "nextOpenSlotAt": "2026-09-08T14:00:00Z"},
            },
            "advisers": [],
            "gaps": [],
        }

    async def appointments() -> Mapping[str, Any]:
        return {"items": []}

    return AssistantToolHost(
        {"profile": profile, "advising": advising, "appointments": appointments}
    )


def _loop_pipeline(
    steps: Sequence[Mapping[str, Any]], planner: str
) -> tuple[AssistantPipeline, list[dict[str, Any]]]:
    step, seen = _scripted(steps)
    return (
        AssistantPipeline(_student_host(), read_loop_step=step, read_planner=planner),
        seen,
    )


def test_student_loop_answers_from_tool_results_with_guard() -> None:
    pipeline, seen = _loop_pipeline(
        [
            {
                "reasoning": "read advising",
                "calls": [{"tool": "getStudentAdvising", "arguments": "{}"}],
                "answer": None,
            },
            {
                "reasoning": "answer",
                "calls": [],
                "answer": (
                    "Your primary adviser is Ada Ashgrove; you can reach her at "
                    "ada.ashgrove@aster.example."
                ),
            },
        ],
        "model",
    )
    trace = AssistantTurnTrace(trace_id="t1")
    result = asyncio.run(
        pipeline.execute(message="Who is my advisor and how do I email them?", trace=trace)
    )
    assert "Ada Ashgrove" in result.message and "ada.ashgrove@aster.example" in result.message
    assert trace.tool_selection_source == "model_loop"
    assert trace.read_planner == "model"
    assert trace.read_loop is not None and trace.read_loop["guard"] == "accepted"
    assert [call["tool"] for call in trace.tool_calls] == ["getStudentAdvising"]
    # The loop saw the real tool catalog, advising included.
    catalog = json.loads(seen[0]["messages"][1]["content"].split(">", 1)[1].rsplit("<", 1)[0])
    assert any(tool["name"] == "getStudentAdvising" for tool in catalog["tools"])


def test_student_loop_guard_rejects_ungrounded_contact_and_falls_back() -> None:
    pipeline, _ = _loop_pipeline(
        [
            {
                "reasoning": "read",
                "calls": [{"tool": "getStudentAdvising", "arguments": "{}"}],
                "answer": None,
            },
            {
                "reasoning": "answer",
                "calls": [],
                "answer": "Email your adviser at someone.else@aster.example.",
            },
        ],
        "model",
    )
    trace = AssistantTurnTrace(trace_id="t2")
    result = asyncio.run(pipeline.execute(message="Who is my advisor?", trace=trace))
    assert "someone.else@aster.example" not in result.message
    assert any(
        code.startswith("read_loop_fallback:ungrounded_contact") for code in result.failure_codes
    )
    # The deterministic route answered from the same read.
    assert "Ada Ashgrove" in result.message or "Ada Ashgrove" in " ".join(trace.evidence)


def test_hybrid_planner_keeps_confident_classification_deterministic() -> None:
    pipeline, seen = _loop_pipeline(
        [{"reasoning": "never", "calls": [], "answer": "model answer"}], "hybrid"
    )
    result = asyncio.run(pipeline.execute(message="Who is my advisor?"))
    assert seen == []  # the classifier placed it as appointments; no loop call
    assert result.classification is not None and result.classification.source == "deterministic"


def test_deterministic_planner_never_calls_the_loop() -> None:
    pipeline, seen = _loop_pipeline(
        [{"reasoning": "never", "calls": [], "answer": "model answer"}], "deterministic"
    )
    asyncio.run(pipeline.execute(message="walk me through where things stand"))
    assert seen == []


def test_loop_skips_greetings_even_in_model_mode() -> None:
    pipeline, seen = _loop_pipeline(
        [{"reasoning": "never", "calls": [], "answer": "model answer"}], "model"
    )
    result = asyncio.run(pipeline.execute(message="hi there"))
    assert seen == [] and result.message


# --- staff pipeline integration: handle binding -----------------------------

from audentra.integrations.staff_assistant.pipeline import StaffAssistantPipeline  # noqa: E402
from audentra.integrations.staff_assistant.tools import StaffAssistantToolHost  # noqa: E402

_ME = "1c244cfb-c8e8-5833-9786-4309835441bf"
_STUDENT = "ff941683-dc6e-4aa4-bfc3-fcff3a9868c1"


def _staff_host(seen: list[tuple[str, dict[str, Any]]]) -> StaffAssistantToolHost:
    async def staff_profile(**kwargs: Any) -> Mapping[str, Any]:
        seen.append(("staff_profile", kwargs))
        return {
            "id": _ME,
            "name": "Greta Radcliffe",
            "title": "Financial Aid Counselor",
            "roleCode": "fa_counselor",
            "component": "Financial Aid",
            "employmentStatus": "active",
            "caseload": {"primaryAdvisees": 0},
            "directReports": 0,
        }

    async def staff_caseload(**kwargs: Any) -> Mapping[str, Any]:
        seen.append(("staff_caseload", kwargs))
        return {
            "staff": {"id": _ME, "name": "Greta Radcliffe"},
            "items": [
                {
                    "student": {"id": _STUDENT, "name": "Lucia Zephyrine"},
                    "role": "financial_aid_counselor",
                    "advising": {"status": "scheduled"},
                    "work": {"open": 2, "overdue": 1},
                }
            ],
            "total": 1,
        }

    async def search_students(**kwargs: Any) -> Mapping[str, Any]:
        seen.append(("search_students", kwargs))
        return {"items": [], "total": 0}

    async def search_staff(**kwargs: Any) -> Mapping[str, Any]:
        seen.append(("search_staff", kwargs))
        return {"items": [], "total": 0}

    async def work_item_by_key(**kwargs: Any) -> Mapping[str, Any]:
        seen.append(("work_item_by_key", kwargs))
        return {"id": "5c3c5f5e-0b1a-4c7e-9d2f-6a7b8c9d0e1f", "key": kwargs.get("key")}

    async def work_item_detail(**kwargs: Any) -> Mapping[str, Any]:
        seen.append(("work_item_detail", kwargs))
        return {"item": {"key": "AST-00102", "title": "Transcript review", "status": "todo"}}

    return StaffAssistantToolHost(
        {
            "staff_profile": staff_profile,
            "staff_caseload": staff_caseload,
            "search_students": search_students,
            "search_staff": search_staff,
            "work_item_by_key": work_item_by_key,
            "work_item_detail": work_item_detail,
        },
        staff_member_id=_ME,
    )


def test_staff_loop_binds_me_handle_and_defaults_missing_identity() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    step, _ = _scripted(
        [
            {
                "reasoning": "caseload",
                "calls": [
                    {"tool": "getStaffCaseload", "arguments": '{"withOverdueWork": true}'},
                    {"tool": "getWorkItemDetail", "arguments": '{"workItemId": "ast-00102"}'},
                ],
                "answer": None,
            },
            {
                "reasoning": "answer",
                "calls": [],
                "answer": "One of your students, Lucia Zephyrine, has 1 overdue item with you.",
            },
        ]
    )
    pipeline = StaffAssistantPipeline(_staff_host(seen), read_loop_step=step, read_planner="model")
    trace = AssistantTurnTrace(trace_id="s1", assistant_kind="staff")
    result = asyncio.run(
        pipeline.execute(
            message="Give me a rough sense of which of my students are slipping with me",
            trace=trace,
        )
    )
    assert "Lucia Zephyrine" in result.message
    caseload_reads = [kwargs for name, kwargs in seen if name == "staff_caseload"]
    assert caseload_reads and caseload_reads[0]["staff_member_id"] == _ME
    assert caseload_reads[0]["role"] is None  # every assignment role, not only primary
    detail_reads = [kwargs for name, kwargs in seen if name == "work_item_detail"]
    assert (
        detail_reads and detail_reads[0]["work_item_id"] == "5c3c5f5e-0b1a-4c7e-9d2f-6a7b8c9d0e1f"
    )
    assert trace.tool_selection_source == "model_loop"
    bound = [call for call in trace.tool_calls if call["round"].startswith("loop")]
    assert {call["tool"] for call in bound} == {"getStaffCaseload", "getWorkItemDetail"}


def test_staff_loop_refuses_unbound_student_handle() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    step, _ = _scripted(
        [
            {
                "reasoning": "read a student that was never resolved",
                "calls": [{"tool": "getStudentBlockers", "arguments": '{"studentId": "student"}'}],
                "answer": None,
            },
            {
                "reasoning": "answer",
                "calls": [],
                "answer": "I could not read a student record this turn.",
            },
        ]
    )
    pipeline = StaffAssistantPipeline(_staff_host(seen), read_loop_step=step, read_planner="model")
    trace = AssistantTurnTrace(trace_id="s2", assistant_kind="staff")
    asyncio.run(pipeline.execute(message="tell me about the situation", trace=trace))
    rejected = [call for call in trace.tool_calls if call["tool"] == "getStudentBlockers"]
    assert rejected and rejected[0]["status"] == "rejected"
    assert "unbound handle" in str(rejected[0]["reason"])
    assert not any(name == "student_blockers" for name, _ in seen)


@pytest.mark.parametrize("role", ["student", "staff"])
@pytest.mark.parametrize(
    ("failure", "source", "message_part"),
    [
        ("no_provider", "university_provider_unavailable", "not configured or is disabled"),
        ("model_error", "university_provider_error", "could not reach its AI service"),
    ],
)
def test_university_provider_failure_is_not_reported_as_missing_evidence(
    role: str, failure: str, source: str, message_part: str
) -> None:
    async def unavailable(**kwargs: Any) -> Mapping[str, Any] | None:
        if failure == "model_error":
            raise RuntimeError("Provider transport failed")
        return None

    async def university_record(**kwargs: Any) -> Mapping[str, Any]:
        pytest.fail("An unavailable planner should not execute reads")

    trace = AssistantTurnTrace(trace_id="provider-failure", assistant_kind=role)
    if role == "student":
        student_pipeline = AssistantPipeline(
            AssistantToolHost({"university_record": university_record}),
            read_loop_step=unavailable,
            read_planner="model",
        )
        result = asyncio.run(
            student_pipeline.execute(message="What documents do I still need?", trace=trace)
        )
    else:
        staff_pipeline = StaffAssistantPipeline(
            StaffAssistantToolHost({"university_record": university_record}, staff_member_id=_ME),
            read_loop_step=unavailable,
            read_planner="model",
        )
        staff_result = asyncio.run(
            staff_pipeline.execute(message="What should I do today?", trace=trace)
        )
        assert message_part in staff_result.message
        assert staff_result.failure_codes == [f"read_loop_fallback:{failure}"]
    if role == "student":
        assert message_part in result.message
        assert result.failure_codes == [f"read_loop_fallback:{failure}"]
    assert trace.response_source == source
    assert "narrow the question" not in trace.final_message
    assert trace.read_loop is not None
    assert trace.read_loop["outcome"] == failure


def test_followup_policy_read_does_not_ground_a_previous_account_amount() -> None:
    step, seen = _scripted(
        [
            {"calls": [{"tool": "getA", "arguments": "{}"}], "answer": None},
            {"calls": [], "answer": "Your pending payment is $1,000."},
            {"calls": [{"tool": "getB", "arguments": "{}"}], "answer": None},
            {"calls": [], "answer": "Your pending payment is $1,000."},
        ]
    )

    async def execute(calls: Sequence[LoopCall]) -> None:
        for call in calls:
            call.status = "available"
            call.result = (
                {"rule": "Pending payments do not post"}
                if call.tool == "getA"
                else {"amount_cents": 100000, "status": "pending"}
            )

    result = asyncio.run(
        run_read_loop(
            question="My friend says it clears instantly",
            history=[{"role": "assistant", "content": "Your payment was $1,000."}],
            context={},
            tools=_tools(),
            model_step=step,
            executor=execute,
        )
    )
    assert result.answer == "Your pending payment is $1,000."
    assert len(seen) == 4  # Still within the existing three reads + final cap.
    assert [s["outcome"] for s in result.steps] == [
        "reads",
        "evidence_required",
        "reads",
        "answered",
    ]
    assert "getB.amount: $1,000" in result.evidence_texts
