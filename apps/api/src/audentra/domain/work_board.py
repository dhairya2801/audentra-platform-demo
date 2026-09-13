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
        if item["type"] == "communication" or item["actionType"] == "communication_response"
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
        draft = links.get("outreachDraft") or {}
        communication = links.get("communication") or {}
        if operational in ("done", "cancelled"):
            stage = "completed"
        elif draft.get("status") == "draft":
            stage = "drafting"
        elif communication.get("direction") == "inbound":
            stage = "responded"
        elif communication.get("deliveryStatus") == "delivered":
            stage = "waiting"
        else:
            # Operational blocking is not an approval decision or a sent message.
            stage = "drafting" if operational == "in_progress" else "identified"
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
        "attention": (
            document["status"] in {"under_review", "needs_review"}
            if document
            else payment["status"] in {"failed", "reversed"}
            if payment
            else operational == "blocked"
        ),
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
        "outreachDraft": links.get("outreachDraft"),
        "communication": links.get("communication"),
        "signals": item["signals"],
        "source": item["source"],
        "exceptions": [],
        "resolved": [],
        "messages": [],
        "amountCents": payment["amount_cents"] if payment else None,
    }
