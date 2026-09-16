"""Task Board context stays scoped, calendar-correct and unambiguous for Edward."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from test_assistant_read_loop import _ME, _STUDENT, _scripted, _staff_host

from audentra.contracts.requests import AskStaffEdwardRequest
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.domain.edward_action_catalog import coerce_fields
from audentra.domain.edward_action_recognizer import parse_staff_action
from audentra.infrastructure.postgres.edward_action_gateway import _task_due_at
from audentra.infrastructure.postgres.task_board_assistant import TaskBoardAssistant, filter_board
from audentra.integrations.assistant.read_loop import LoopCall
from audentra.integrations.assistant.trace import AssistantTurnTrace
from audentra.integrations.staff_assistant.pipeline import StaffAssistantPipeline

NOW = datetime(2026, 9, 17, 1, tzinfo=UTC)  # Wednesday evening in the board timezone.
OTHER_STUDENT = "ff941683-dc6e-4aa4-bfc3-fcff3a9868c2"


def card(
    key: str,
    *,
    student: str = "Ada Example",
    due: str | None = None,
    status: str = "todo",
    priority: str = "medium",
) -> dict[str, Any]:
    return {
        "id": key,
        "key": key,
        "title": "Review transcript",
        "board": "en-docs",
        "priority": priority,
        "status": status,
        "dueAt": due,
        "nextStep": None,
        "version": 1,
        "description": "Review a document",
        "workType": "document_review",
        "documents": [],
        "conversations": [],
        "student": {
            "id": _STUDENT if student.startswith("Ada") else OTHER_STUDENT,
            "externalRef": student,
            "name": student,
            "preferredName": student.split()[0],
            "program": "Data Science",
        },
    }


def host_board(cards: list[dict[str, Any]]) -> TaskBoardAssistant:
    staff = AsyncMock()
    staff.get_work_item_detail.return_value = {"workItem": {}}
    board = TaskBoardAssistant(staff, AuthContext("tenant", "student", _ME, "staff"))
    board._configured = True
    board._snapshot = {
        "cards": cards,
        "staff": {"id": _ME},
        "studentCount": len({c["student"]["id"] for c in cards}),
    }
    return board


def test_calendar_windows_do_not_confuse_utc_today_week_or_completed_work() -> None:
    cards = [
        card("ENR-1", due="2026-09-17T02:00:00+00:00"),  # Still today local
        card("ENR-2", due="2026-09-17T05:00:00+00:00"),
        card("ENR-3", due="2026-09-21T03:59:59+00:00"),  # Sunday local
        card("ENR-4", due="2026-09-21T04:00:00+00:00"),  # Next Monday
        card("ENR-5", due="2026-09-16T18:00:00+00:00", status="done"),
        card("ENR-6", due="2026-09-16T18:00:00+00:00"),
    ]

    def keys(due: str) -> set[str]:
        return {c["key"] for c in filter_board(cards, {"due": due}, NOW)}

    assert keys("today") == {"ENR-1", "ENR-6"}
    assert keys("this_week") == {"ENR-1", "ENR-2", "ENR-3", "ENR-6"}
    assert keys("seven_days") == {"ENR-1", "ENR-2", "ENR-3", "ENR-4"}
    assert keys("overdue") == {"ENR-6"}


def test_counts_are_full_filtered_membership_and_pages_do_not_duplicate() -> None:
    async def run() -> None:
        board = host_board(
            [card(f"ENR-{n}", priority="urgent" if n < 4 else "low") for n in range(10)]
        )
        first = await board.read(priority="urgent", limit=2)
        second = await board.read(priority="urgent", limit=2, offset=2)
        assert first["boardTotal"] == 10 and first["counts"]["total"] == 4
        assert first["page"]["hasMore"] and not second["page"]["hasMore"]
        assert {c["key"] for c in first["cards"]}.isdisjoint(c["key"] for c in second["cards"])
        assert (await board.read(search="Ada", status="open"))["counts"]["total"] == 10
        with pytest.raises(ApiError):
            await board.page_context({"workItemKey": "ENR-999"})
        selected = await board.page_context({"workItemKey": "ENR-1"})
        assert selected["selectedTask"]["student"]["id"] == _STUDENT

    asyncio.run(run())


def test_task_write_references_never_choose_the_first_item_in_a_comparison() -> None:
    async def run() -> None:
        board = host_board([card("ENR-1"), card("FIN-2"), card("ENR-3", student="Wren Example")])
        history = [{"role": "assistant", "content": "Compare task ENR-1 with FIN-2."}]
        key, candidates = await board.reference("Make it urgent", history)
        assert key is None and len(candidates) == 2
        assert (await board.reference("Make it urgent", history, "ENR-3"))[0] is None
        assert (await board.reference("Make this task urgent", history, "ENR-3"))[0] == "ENR-3"
        picks = [{"role": "assistant", "content": "Which task should I update? ENR-1; FIN-2"}]
        assert (await board.reference("The second one", picks))[0] == "FIN-2"
        assert (await board.reference("Change FIN-2 to urgent", history, "ENR-3"))[0] == "FIN-2"
        assert (await board.reference("Make Ada's task urgent", [], "ENR-3"))[0] is None
        assert (await board.read(studentId=_STUDENT))["counts"]["total"] == 2
        assert (await board.read(studentId=OTHER_STUDENT))["cards"][0]["key"] == "ENR-3"
        assert (await board.search_students(query="Ada"))["total"] == 1
        assert (await board.search_students(query="Unknown"))["total"] == 0
        with pytest.raises(ApiError):
            await board.task("FIN-999")

    asyncio.run(run())


def test_page_context_cannot_supply_identity_or_arbitrary_surface() -> None:
    valid = AskStaffEdwardRequest.model_validate(
        {
            "message": "this task",
            "pageContext": {"surface": "task_board", "project": "en-docs", "workItemKey": "ENR-1"},
        }
    )
    assert valid.page_context and valid.page_context.work_item_key == "ENR-1"
    for extra in (
        {"studentId": _STUDENT},
        {"staffId": _ME},
        {"surface": "unknown"},
        {"workItemKey": "../../other"},
    ):
        with pytest.raises(ValidationError):
            AskStaffEdwardRequest.model_validate(
                {"message": "this task", "pageContext": {"surface": "task_board", **extra}}
            )


def test_task_dates_and_relative_edit_intent_use_existing_action_catalog() -> None:
    lowered = parse_staff_action("Lower it to low priority.", task_board=True)
    assert lowered is not None and lowered.fields == {"priority": "low"}
    request = parse_staff_action("Make it urgent.", task_board=True)
    assert request is not None and request.action == "operations.work_item.update"
    assert parse_staff_action("What should I do today?", task_board=True) is None
    assert (
        parse_staff_action(
            "Compare FIN-1 and ENR-2: the student names, priorities and deadlines.", task_board=True
        )
        is None
    )
    create = parse_staff_action("Create a follow-up for Ada due Friday.", task_board=True)
    assert create is not None and create.action == "operations.follow_up.create"
    note = parse_staff_action(
        "Set ENR-1 next step to: Follow up with the registrar Friday.", task_board=True
    )
    assert note is not None and note.fields == {"nextStep": "Follow up with the registrar Friday"}
    assert coerce_fields("operations.work_item.update", {"dueOn": "2026-09-25"}) == {
        "dueOn": "2026-09-25"
    }
    assert coerce_fields("operations.work_item.update", {"dueOn": "2026-02-30"}) == {}
    due = _task_due_at("2026-09-25", NOW)
    assert due and due.isoformat() == "2026-09-25T17:00:00-04:00"
    assert _task_due_at("today", NOW).isoformat() == "2026-09-16T17:00:00-04:00"  # type: ignore[union-attr]
    assert _task_due_at("friday", NOW).isoformat() == "2026-09-18T17:00:00-04:00"  # type: ignore[union-attr]


def test_same_round_task_details_keep_distinct_results_and_verified_student_handles() -> None:
    async def run() -> None:
        host = _staff_host([])

        async def detail(key: str) -> dict[str, Any]:
            return {
                "task": {"key": key},
                "student": {
                    "id": _STUDENT if key == "ENR-1" else OTHER_STUDENT,
                    "name": "Ada Example" if key == "ENR-1" else "Wren Example",
                },
            }

        host._primitives["task_board_task"] = detail
        host._primitives["task_board"] = AsyncMock(return_value={})
        host._primitives["university_record"] = AsyncMock(return_value={})
        step, seen = _scripted(
            [
                {
                    "reasoning": "compare",
                    "calls": [
                        {"tool": "getTaskBoardTask", "arguments": '{"key":"ENR-1"}'},
                        {"tool": "getTaskBoardTask", "arguments": '{"key":"ENR-2"}'},
                    ],
                    "answer": None,
                },
                {
                    "reasoning": "answer",
                    "calls": [],
                    "answer": "ENR-1 is for Ada Example; ENR-2 is for Wren Example.",
                },
            ]
        )
        pipeline = StaffAssistantPipeline(host, read_loop_step=step, read_planner="model")
        trace = AssistantTurnTrace(trace_id="board-test", assistant_kind="staff")
        result = await pipeline.execute(message="Compare the tasks on my board", trace=trace)
        assert result.provider != "deterministic", result.message
        assert result.resolved_student_id is None
        import json

        payload = json.loads(seen[1]["messages"][1]["content"].split(">", 1)[1].rsplit("<", 1)[0])
        reads = payload["stepsSoFar"][0]["reads"]
        assert [r["result"]["task"]["key"] for r in reads] == ["ENR-1", "ENR-2"]
        assert [r["result"]["studentHandle"] for r in reads] == ["student:ENR-1", "student:ENR-2"]
        bad = LoopCall(
            tool="getStudentStaffSummary", arguments={"studentId": OTHER_STUDENT}, round_index=0
        )
        assert await pipeline._bind_loop_arguments(bad, {"student:ENR-1": _STUDENT}) is None
        bad_board = LoopCall(tool="getTaskBoard", arguments={"studentId": "me"}, round_index=0)
        assert await pipeline._bind_loop_arguments(bad_board, {"me": _ME}) is None
        assert "staff handle" in str(bad_board.reason)

    asyncio.run(run())


def test_board_page_cannot_plan_reads_from_the_broader_work_queue() -> None:
    async def run() -> None:
        import json

        host = _staff_host([])
        host.task_board_context = {"surface": "task_board"}
        host._primitives["task_board"] = AsyncMock(
            return_value={"boardTotal": 64, "studentCount": 10}
        )
        host._primitives["university_record"] = AsyncMock(return_value={})
        step, seen = _scripted(
            [
                {
                    "reasoning": "read board",
                    "calls": [{"tool": "getTaskBoard", "arguments": "{}"}],
                    "answer": None,
                },
                {
                    "reasoning": "answer",
                    "calls": [],
                    "answer": "Your Task Board has 64 tasks for 10 students.",
                },
            ]
        )
        result = await StaffAssistantPipeline(
            host, read_loop_step=step, read_planner="model"
        ).execute(message="What should I do today?")
        assert "64" in result.message
        payload = json.loads(seen[0]["messages"][1]["content"].split(">", 1)[1].rsplit("<", 1)[0])
        available = {tool["name"] for tool in payload["tools"]}
        assert {"getTaskBoard", "getTaskBoardTask"} <= available
        assert not available.intersection(
            {
                "getStaffWorkQueue",
                "getMorningBriefing",
                "searchWorkQueue",
                "summarizeWorkQueue",
                "getUniversityWorkBoard",
                "getStaffProfile",
            }
        )
        assert "Current assigned workload" not in payload["context"]["signedIn"]

    asyncio.run(run())


def test_student_task_index_reaches_the_planner_before_project_selection() -> None:
    async def run() -> None:
        import json

        host = _staff_host([])
        host.task_board_context = {"surface": "task_board"}
        host._primitives["search_students"] = AsyncMock(
            return_value={
                "items": [{"id": _STUDENT, "name": "Ada Example", "preferredName": "Ada Example"}],
                "total": 1,
            }
        )
        board = host_board([card("ENR-1"), {**card("FIN-2"), "board": "fa-docs"}])
        host._primitives["task_board"] = board.read
        step, seen = _scripted(
            [
                {
                    "reasoning": "read candidate details",
                    "calls": [{"tool": "getTaskBoard", "arguments": '{"studentId":"student"}'}],
                    "answer": None,
                },
                {
                    "reasoning": "ambiguous",
                    "calls": [],
                    "answer": "Ada Example has ENR-1 and FIN-2. Which task do you mean?",
                },
            ]
        )
        await StaffAssistantPipeline(host, read_loop_step=step, read_planner="model").execute(
            message="Tell me about Ada's document task."
        )
        payload = json.loads(seen[0]["messages"][1]["content"].split(">", 1)[1].rsplit("<", 1)[0])
        index = payload["context"]["resolvedStudentBoard"]
        assert {c["key"] for c in index["cards"]} == {"ENR-1", "FIN-2"}
        assert index["counts"]["byWorkType"] == {"document_review": 2}
        assert index["appliedFilters"]["studentId"] == _STUDENT

    asyncio.run(run())


def test_this_task_uses_the_open_card_instead_of_an_older_conversation_task() -> None:
    async def run() -> None:
        import json

        host = _staff_host([])
        host.task_board_context = {"surface": "task_board", "selectedTask": {"key": "ENR-1"}}
        step, seen = _scripted(
            [
                {"reasoning": "read open card", "calls": [], "answer": "Which detail do you need?"},
            ]
        )
        await StaffAssistantPipeline(host, read_loop_step=step, read_planner="model").execute(
            message="What is this task about?",
            history=[{"role": "assistant", "content": "FIN-2 is a financial aid review."}],
        )
        payload = json.loads(seen[0]["messages"][1]["content"].split(">", 1)[1].rsplit("<", 1)[0])
        assert payload["question"] == "What is task ENR-1 about?"

    asyncio.run(run())
