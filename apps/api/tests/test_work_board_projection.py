from typing import Any

from audentra.domain.work_board import board_card


def item() -> dict[str, Any]:
    return {
        "id": "work",
        "key": "ENR-1",
        "title": "Review evidence",
        "description": "Review the actual document",
        "status": "done",
        "priority": "high",
        "actionType": "document_review",
        "component": "Financial Aid",
        "student": {
            "id": "student",
            "name": "Student One",
            "programName": "Biology",
            "classYear": 2030,
        },
        "assignee": None,
        "dueAt": None,
        "updatedAt": "2026-09-08",
        "createdAt": "2026-09-01",
        "startedAt": None,
        "version": 3,
        "escalated": False,
        "history": [],
        "signals": {},
        "source": None,
    }


def test_document_stage_cannot_be_completed_by_operational_status() -> None:
    card = board_card(item(), {"document": {"status": "under_review"}})
    assert card["status"] == "exceptions"
    assert card["operationalStatus"] == "done"
    assert card["board"] == "fa-docs"


def test_payment_card_uses_settlement_evidence_and_retains_failure() -> None:
    row = item()
    row["actionType"] = "reachout"
    for state, stage in [
        ("pending", "processing"),
        ("posted", "completed"),
        ("failed", "exception"),
        ("reversed", "exception"),
    ]:
        card = board_card(row, {"payment": {"status": state, "amount_cents": 10001}})
        assert card["status"] == stage
        assert card["amountCents"] == 10001
        assert card["board"] == "fa-payments"


def test_case_dependencies_are_visible_and_not_completed_by_projection() -> None:
    row = item()
    row["actionType"] = "reachout"
    case = {"incomplete_steps": 2, "status": "waiting"}
    card = board_card(row, {"case": case})
    assert card["case"]["incomplete_steps"] == 2
    assert case == {"incomplete_steps": 2, "status": "waiting"}
