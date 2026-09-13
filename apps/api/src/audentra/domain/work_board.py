"""Board cards are presentation projections over canonical operational work."""

from __future__ import annotations

from typing import Any


def board_card(item: dict[str, Any], links: dict[str, Any]) -> dict[str, Any]:
    component = str(item["component"]).lower()
    document = links.get("document")
    payment = links.get("payment")
    kind = (
        "document"
        if document or item["actionType"] == "document_review"
        else "payment"
        if payment
        else "outreach"
        if item["actionType"] in ("reachout", "outreach", "follow_up")
        else "request"
    )
    office = (
        "fa"
        if any(word in component for word in ("financial", "accounts", "aid"))
        else "cl"
        if "housing" in component
        else "en"
    )
    board = (
        "fa-payments"
        if kind == "payment"
        else "cl-housing"
        if office == "cl"
        else f"{office}-"
        + {"document": "docs", "outreach": "outreach", "request": "requests"}[kind]
    )
    if board == "fa-requests":
        board = "en-requests"
    operational = item["status"]
    # Workflow evidence, rather than arbitrary board movement, determines domain stage.
    if document:
        stage = {
            "placeholder": "requested",
            "uploaded": "processing",
            "processing": "processing",
            "under_review": "exceptions",
            "needs_review": "exceptions",
            "accepted": "completed",
            "waived": "completed",
            "needs_resubmission": "correction",
            "rejected": "correction",
            "expired": "correction",
        }.get(document["status"], "requested")
    elif payment:
        stage = {
            "pending": "processing",
            "posted": "completed",
            "failed": "exception",
            "reversed": "exception",
        }.get(payment["status"], "requested")
    elif kind == "outreach":
        stage = {
            "todo": "identified",
            "in_progress": "drafting",
            "follow_up_required": "waiting",
            "blocked": "approval",
            "done": "completed",
            "cancelled": "completed",
        }[operational]
    else:
        stage = {
            "todo": "received",
            "in_progress": "reviewing",
            "follow_up_required": "waiting",
            "blocked": "escalated",
            "done": "completed",
            "cancelled": "completed",
        }[operational]
    history = [
        {"text": r["message"], "kind": "history", "who": r["actorName"], "at": r["occurredAt"]}
        for r in item["history"]
    ]
    return {
        "id": item["id"],
        "key": item["key"],
        "title": item["title"],
        "description": item["description"],
        "type": kind,
        "board": board,
        "status": stage,
        "operationalStatus": operational,
        "student": item["student"]["name"],
        "studentId": item["student"]["id"],
        "program": item["student"]["programName"],
        "cohort": str(item["student"]["classYear"]),
        "owner": item["assignee"]["id"] if item["assignee"] else "TEAM",
        "priority": item["priority"].title(),
        "due": item["dueAt"],
        "updated": item["updatedAt"],
        "entered": item["startedAt"] or item["createdAt"],
        "created": item["createdAt"],
        "version": item["version"],
        "labels": [item["component"], operational.replace("_", " ")],
        "category": item["component"],
        "activity": history,
        "visited": [],
        "escalated": item["escalated"],
        "delegation": "Direct assignment" if item["assignee"] else "Team queue",
        "document": document,
        "payment": payment,
        "case": links.get("case"),
        "signals": item["signals"],
        "source": item["source"],
        "exceptions": [],
        "resolved": [],
        "messages": [],
        "amountCents": payment["amount_cents"] if payment else None,
    }
