"""Semantic response projection from guarded prose and successful canonical reads.

The model selects a small named view, never markup, URLs, amounts or row data.
Only this module constructs record components. Every component has a transcript
fallback and observable provenance. Unknown/unavailable views are omitted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from audentra.integrations.assistant.read_loop import ReadLoopResult

JsonDict = dict[str, Any]


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [r for r in value if isinstance(r, Mapping)] if isinstance(value, list) else []


def _label(value: Any) -> str:
    return str(value or "Not recorded").replace("_", " ").capitalize()


def _money(cents: Any) -> str:
    return f"${cents / 100:,.2f}" if isinstance(cents, int | float) else "Not recorded"


def response_blocks(result: ReadLoopResult, *, actor: str) -> list[JsonDict]:
    content = dict(result.presentation or {"answer": result.answer, "views": []})
    answer = str(content.get("answer") or result.answer or "")
    # Preserve guarded text while keeping long primary prose progressively disclosed.
    # Decimal amounts are not sentence boundaries; short Yes/No prefixes stay attached.
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", answer)
    if len(answer) > 280 and len(sentences) > 1:
        primary = sentences.pop(0)
        if len(primary) < 25 and sentences:
            primary += " " + sentences.pop(0)
        if sentences:
            content["details"] = " ".join([*sentences, str(content.get("details") or "")]).strip()
            answer = primary
    blocks: list[JsonDict] = [{"type": "answer", "text": answer, "fallbackText": answer}]
    if content.get("nextStep"):
        blocks.append(
            {
                "type": "next_action",
                "text": content["nextStep"],
                "fallbackText": "Next step: " + content["nextStep"],
            }
        )
    if content.get("details"):
        blocks.append(
            {
                "type": "explanation",
                "title": "Why this matters",
                "text": content["details"],
                "fallbackText": content["details"],
            }
        )
    calls = {
        call.tool: call
        for call in result.calls
        if call.status == "available" and isinstance(call.result, Mapping)
    }
    for view in dict.fromkeys(v for v in content.get("views", []) if isinstance(v, str)):
        candidates = {
            "account": ("getUniversityAccount",),
            "documents": ("getUniversityDocuments",),
            "checklist": ("getOnboardingChecklist", "getStudentRequirements"),
            "advisers": ("getStudentAdvising", "getUniversityRelationships"),
            "academics": ("getUniversityAcademics",),
            "history": ("getUniversityHistory",),
            "work_queue": ("searchWorkQueue", "getWorkQueue", "getUniversityCasework"),
        }.get(view, ())
        call = next((calls[name] for name in candidates if name in calls), None)
        if call is None:
            continue
        data = call.result
        block = _record_view(view, data, actor)
        if block is not None:
            block["provenance"] = {
                "kind": "institution_record",
                "tool": call.tool,
                "asOf": data.get("snapshotAt") or data.get("asOf"),
            }
            blocks.append(block)
        if view == "account":
            stages = _rows(data.get("serviceProgress"))
            if stages:
                items = [
                    {
                        "title": str(r.get("title") or "Review"),
                        "status": _label(r.get("status")),
                        "owner": str(r.get("office_name") or "University"),
                        "detail": "University review — not a new submission requirement.",
                    }
                    for r in stages[:8]
                ]
                blocks.append(
                    {
                        "type": "checklist",
                        "title": "Waiting on university review",
                        "items": items,
                        "total": len(stages),
                        "provenance": {
                            "kind": "institution_record",
                            "tool": call.tool,
                            "asOf": data.get("snapshotAt"),
                        },
                        "fallbackText": "\n".join(
                            f"{i['title']}: {i['status']} — {i['owner']}" for i in items
                        ),
                    }
                )

    sources: dict[str, JsonDict] = {}
    for call in result.calls:
        if call.status != "available" or not isinstance(call.result, Mapping):
            continue
        for source in _rows(call.result.get("sources")):
            citation = source.get("citation")
            if (
                not citation
                or not source.get("content_hash")
                or source.get("applicability") == "does_not_apply"
            ):
                continue
            sources[str(citation)] = {
                "title": str(source.get("title") or source.get("code") or citation),
                "citation": str(citation),
                "version": str(source.get("version") or ""),
                "section": str(source.get("heading") or ""),
                "excerpt": str(source.get("body") or "")[:2400],
                "applicability": str(source.get("applicability") or "unknown"),
            }
    if sources:
        items = list(sources.values())[:5]
        blocks.append(
            {
                "type": "sources",
                "items": items,
                "fallbackText": "Policy evidence retrieved: "
                + "; ".join(s["citation"] for s in items),
            }
        )
    snapshots = sorted(
        {str(call.result["snapshotAt"]) for call in calls.values() if call.result.get("snapshotAt")}
    )
    if snapshots:
        blocks.append(
            {
                "type": "record_context",
                "asOf": snapshots[-1],
                "label": "University record",
                "fallbackText": "University record as of " + snapshots[-1],
            }
        )
    return blocks


def _record_view(view: str, data: Mapping[str, Any], actor: str) -> JsonDict | None:
    if view == "account":
        facts = [
            {
                "label": str(r.get("term_id") or "Account") + " posted balance",
                "value": _money(r.get("balance_cents")),
            }
            for r in _rows(data.get("balances"))
        ]
        facts += [
            {
                "label": _label(r.get("method")) + " · " + _label(r.get("status")),
                "value": _money(r.get("amount_cents")),
            }
            for r in _rows(data.get("payments"))
            if r.get("status") in {"pending", "failed", "reversed"}
        ]
        facts += [
            {"label": _label(r.get("kind")) + " hold", "value": str(r.get("reason") or "Active")}
            for r in _rows(data.get("holds"))
            if not r.get("released_at")
        ]
        return _facts(
            "Account status",
            facts,
            "Pending payments do not reduce the posted balance. "
            "A credit balance does not confirm a refund.",
        )
    if view in {"documents", "checklist"}:
        rows = _rows(data.get("documents" if view == "documents" else "items"))
        items = []
        # Open work first, preserving canonical order within each group.
        rows.sort(
            key=lambda r: r.get("status") in {"completed", "accepted", "waived", "not_applicable"}
        )
        for row in rows[:8]:
            item: JsonDict = {
                "title": str(row.get("title") or _label(row.get("category"))),
                "status": _label(row.get("status")),
                "owner": str(row.get("responsibleOffice") or row.get("office_name") or ""),
                "detail": str(row.get("rejection_reason") or row.get("readinessNote") or ""),
            }
            # Paths are copied only from trusted tool projections; never model output.
            if (
                actor == "student"
                and isinstance(row.get("href"), str)
                and row["href"].startswith("/")
            ):
                item["href"] = row["href"]
            items.append(item)
        if not items:
            return None
        return {
            "type": "checklist",
            "title": "Documents"
            if view == "documents"
            else "Your requirements"
            if actor == "student"
            else "Student requirements",
            "items": items,
            "total": len(rows),
            "fallbackText": "\n".join(
                f"{i['title']}: {i['status']}. {i['detail']} {i['owner']}" for i in items
            ),
        }
    if view == "advisers":
        people = []
        for row in _rows(data.get("coverage")):
            people.append(
                {
                    "name": str(row.get("covering_name") or row.get("name") or ""),
                    "role": "Covering adviser",
                    "email": str(row.get("covering_email") or row.get("email") or ""),
                    "office": str(row.get("office_name") or ""),
                }
            )
        for row in _rows(data.get("advisers") or data.get("assignments")):
            if row.get("ends_at"):
                continue
            people.append(
                {
                    "name": str(row.get("name") or ""),
                    "role": _label(row.get("role")),
                    "email": str(row.get("email") or ""),
                    "office": str(row.get("component") or row.get("office_name") or ""),
                    "status": _label(row.get("employmentStatus") or row.get("status")),
                }
            )
        people = [p for p in people if p["name"]][:5]
        if people:
            return {
                "type": "contacts",
                "title": "People who can help",
                "items": people,
                "fallbackText": "\n".join(
                    f"{p['name']} · {p['role']} · {p['email']}" for p in people
                ),
            }
    if view == "academics":
        return _facts(
            "Registered courses",
            [
                {
                    "label": str(r.get("code")) + " · " + str(r.get("title")),
                    "value": f"{r.get('credits')} credits",
                }
                for r in _rows(data.get("attempts"))
                if r.get("status") == "enrolled"
            ],
        )
    if view == "history":
        items = [
            {
                "title": _label(r.get("entity_type")) + ": " + _label(r.get("to_state")),
                "at": str(r.get("effective_at") or ""),
                "detail": "Recorded " + str(r.get("recorded_at") or ""),
            }
            for r in _rows(data.get("events"))[:8]
        ]
        if items:
            return {
                "type": "timeline",
                "title": "Record history",
                "items": items,
                "fallbackText": "\n".join(
                    f"{i['title']} · {i['at']} · {i['detail']}" for i in items
                ),
            }
    if view == "work_queue":
        rows = _rows(data.get("items") or data.get("steps"))
        return _facts(
            "Work to review",
            [
                {"label": str(r.get("title") or "Task"), "value": _label(r.get("status"))}
                for r in rows[:8]
            ],
            f"Showing {min(len(rows), 8)} returned items; this is not a total workload count.",
        )
    return None


def _facts(title: str, rows: list[JsonDict], note: str = "") -> JsonDict | None:
    if not rows:
        return None
    return {
        "type": "facts",
        "title": title,
        "items": rows[:8],
        "note": note,
        "fallbackText": title
        + "\n"
        + "\n".join(f"{r['label']}: {r['value']}" for r in rows[:8])
        + ("\n" + note if note else ""),
    }
