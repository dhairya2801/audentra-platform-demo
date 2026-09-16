"""Opt-in real-model Task Board evaluation. Writes only to an isolated demo copy.

Run with the isolated API and --database-url audentra_university_test_camila_*.
No student messages are sent. Provider responses are kept out of the repository.
"""

from __future__ import annotations
import argparse
import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import httpx


async def check(args):
    parsed = urlparse(args.database_url)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.startswith(
        "/audentra_university_test_camila"
    ), "Use an isolated test copy"
    assert urlparse(args.api).hostname in {"localhost", "127.0.0.1"}
    db = await asyncpg.connect(args.database_url)
    report = []
    async with httpx.AsyncClient(base_url=args.api, timeout=180) as c:

        async def post(path, body):
            r = await c.post(path, json=body)
            r.raise_for_status()
            return r.json()

        await post("/v1/auth/demo/staff/sign-in-as", {"staffRef": args.staff_ref})

        async def board():
            r = await c.get("/v1/staff/demo-task-board")
            r.raise_for_status()
            return r.json()

        initial = await board()
        # Confirm the HTTP API and the explicitly isolated database are the same state.
        first = initial["cards"][0]
        assert (
            await db.fetchval(
                "SELECT version FROM staff_work_item WHERE id=$1", first["id"]
            )
            == first["version"]
        )
        ada = [
            t
            for t in initial["cards"]
            if t["student"]["externalRef"] == args.student_ref
        ]
        target = next(
            t
            for t in ada
            if t["documents"] and t["priority"] != "urgent" and t["status"] != "done"
        )
        other = next(
            t
            for t in initial["cards"]
            if t["documents"]
            and t["student"]["id"] != target["student"]["id"]
            and t["priority"] == "urgent"
        )
        key = target["key"]

        async def conversation():
            return (await post("/v1/staff/assistant/conversations", {}))["id"]

        async def ask(label, question, conv=None, selected=None):
            payload = {
                "message": question,
                "conversationId": conv or await conversation(),
                "clientMessageId": str(uuid4()),
                "pageContext": {
                    "surface": "task_board",
                    **({"workItemKey": selected} if selected else {}),
                },
            }
            b = await post("/v1/staff/assistant/messages", payload)
            trace = await db.fetchval(
                "SELECT trace_payload FROM assistant_turn_trace WHERE trace_id=$1",
                b["requestId"],
            )
            assert trace is not None, (
                "The HTTP API must use the explicitly isolated database"
            )
            trace = json.loads(trace) if isinstance(trace, str) else trace
            row = {"case": label, "question": question, "response": b, "trace": trace}
            report.append(row)
            Path(args.report).write_text(json.dumps(report, indent=2, default=str))
            print(label, b.get("provider"), b["message"], flush=True)
            assert not b.get("actionError"), b.get("actionError")
            return b

        conv = await conversation()
        overview = await ask(
            "overview",
            "What are the things on my task board, and how many students do they cover?",
            conv,
        )
        assert (
            str(initial["total"]) in overview["message"]
            and str(initial["studentCount"]) in overview["message"]
        )
        assert {"source": "task_board"} in overview["contextReceipts"]
        for label, q in [
            ("today", "What should I do today?"),
            ("urgent", "What is most urgent?"),
            ("week", "What is due this week?"),
        ]:
            b = await ask(label, q, conv)
            assert any(
                r["source"] in {"task_board", "task_board_task"}
                for r in b["contextReceipts"]
            )
            calls = report[-1]["trace"]["toolCalls"]
            assert not {call["tool"] for call in calls}.intersection(
                {
                    "getStaffWorkQueue",
                    "getMorningBriefing",
                    "summarizeWorkQueue",
                    "searchWorkQueue",
                    "getUniversityWorkBoard",
                }
            ), "Task Board answers must not substitute the broader work queue"
            assert any(
                call["tool"] in {"getTaskBoard", "getTaskBoardTask"}
                and call["status"] == "available"
                for call in calls
            )
        ambiguity = await ask(
            "student-document-ambiguity",
            f"Tell me about {target['student']['preferredName']}'s document task.",
        )
        assert all(t["key"] in ambiguity["message"] for t in ada if t["documents"])
        financial_document = next((t for t in ada if t["board"] == "fa-docs"), None)
        if financial_document:
            resolved = await ask(
                "student-document-follow-up", "The financial aid one.",
                ambiguity["conversationId"],
            )
            assert financial_document["key"] in resolved["message"]
        detail = await ask(
            "selected-detail",
            "What is this task about, what did the student upload, and what is its current review state?",
            selected=key,
        )
        assert (
            target["documents"][0]["fileName"] in detail["message"]
            and key in detail["message"]
        )
        detail_conv = detail["conversationId"]
        draft = await ask(
            "student-draft",
            "Draft a short portal message to the student about the next step.",
            detail_conv,
            selected=key,
        )
        assert target["student"]["preferredName"] in draft["message"]
        assert not draft.get("actionIntents") and not draft.get("actionReceipts")
        accepted = next(
            (
                t
                for t in ada
                if t["documents"] and t["documents"][0]["status"] == "accepted"
            ),
            None,
        )
        if accepted:
            completed = await ask(
                "accepted-document-draft",
                "Draft a short portal message to this student about what happens next.",
                selected=accepted["key"],
            )
            assert not re.search(
                r"please (?:re.?upload|upload|resubmit)|need (?:you )?to (?:upload|resubmit)",
                completed["message"],
                re.I,
            ), completed["message"]
        compare = await ask(
            "cross-task",
            f"Compare {key} and {other['key']}: students, priorities, uploaded filenames, and which needs my attention first.",
        )
        assert key in compare["message"] and other["key"] in compare["message"]
        assert (
            target["student"]["preferredName"] in compare["message"]
            and other["student"]["preferredName"] in compare["message"]
        )
        ambiguous = await ask(
            "ambiguous-write", "Make it urgent.", compare["conversationId"]
        )
        assert not ambiguous.get("actionIntents") and "?" in ambiguous["message"]
        assert key in ambiguous["message"] and other["key"] in ambiguous["message"]
        selected = await ask("clarification-answer", key, compare["conversationId"])
        assert selected.get("actionIntents"), selected
        intent = selected["actionIntents"][0]
        before = next(t for t in (await board())["cards"] if t["key"] == key)
        assert (
            before["priority"] == target["priority"]
            and before["version"] == target["version"]
        )
        path = f"/v1/staff/assistant/action-intents/{intent['id']}/confirm"
        bad = await c.post(
            path, json={"expectedVersion": intent["version"], "contentSha256": "0" * 64}
        )
        assert bad.status_code in (400, 409, 422)
        receipt = await post(
            path,
            {
                "expectedVersion": intent["version"],
                "contentSha256": intent["contentSha256"],
            },
        )
        replay = await post(
            path,
            {
                "expectedVersion": intent["version"],
                "contentSha256": intent["contentSha256"],
            },
        )
        assert receipt == replay
        after = next(t for t in (await board())["cards"] if t["key"] == key)
        assert (
            after["priority"] == "urgent" and after["version"] == before["version"] + 1
        )
        assert len(after["activity"]) > len(before["activity"])
        assert (
            await db.fetchval(
                "SELECT priority FROM staff_work_item WHERE id=$1", target["id"]
            )
            == "urgent"
        )
        print(
            "PASS confirmation integrity, version increment, durable activity, exact replay, database priority",
            flush=True,
        )

        async def edit(label, q, field, expected):
            b = await ask(label, q, selected=key)
            i = b["actionIntents"][0]
            await post(
                f"/v1/staff/assistant/action-intents/{i['id']}/confirm",
                {"expectedVersion": i["version"], "contentSha256": i["contentSha256"]},
            )
            current = next(t for t in (await board())["cards"] if t["key"] == key)
            assert expected(current[field]), (field, current[field])

        due = (datetime.now() + timedelta(days=10)).date().isoformat()
        await edit(
            "absolute-due-date",
            f"Change this task’s due date to {due}.",
            "dueAt",
            lambda value: (
                datetime.fromisoformat(value)
                .astimezone(ZoneInfo("America/New_York"))
                .isoformat()
                .startswith(due + "T17:00:00")
            ),
        )
        await edit(
            "next-step",
            f"Set {key} next step to: Contact the registrar to confirm receipt.",
            "nextStep",
            lambda value: value and "registrar" in value.lower(),
        )
        follow = await ask(
            "read-after-write",
            f"What are {key}’s priority, due date and next step now?",
        )
        assert (
            "urgent" in follow["message"].lower()
            and "registrar" in follow["message"].lower()
        )
        cancellation = await ask("cancel-preview", f"Change {key} priority to low.")
        i = cancellation["actionIntents"][0]
        await post(
            f"/v1/staff/assistant/action-intents/{i['id']}/cancel",
            {"expectedVersion": i["version"]},
        )
        assert (
            next(t for t in (await board())["cards"] if t["key"] == key)["priority"]
            == "urgent"
        )
        stale = await ask("stale-preview", f"Change {key} priority to medium.")
        i = stale["actionIntents"][0]
        current = next(t for t in (await board())["cards"] if t["key"] == key)
        r = await c.patch(
            f"/v1/staff/work-items/{target['id']}",
            json={
                "expectedVersion": current["version"],
                "nextStep": "Changed by another staff tab",
            },
        )
        r.raise_for_status()
        rejected = await c.post(
            f"/v1/staff/assistant/action-intents/{i['id']}/confirm",
            json={"expectedVersion": i["version"], "contentSha256": i["contentSha256"]},
        )
        assert rejected.status_code == 409 or "succeeded" not in rejected.text
        assert (
            next(t for t in (await board())["cards"] if t["key"] == key)["priority"]
            == "urgent"
        )
        print(
            "PASS cancellation and stale confirmation leave priority unchanged",
            flush=True,
        )
        before_count = await db.fetchval("SELECT count(*) FROM student_inquiry_reply")
        invalid = await c.post(
            "/v1/staff/assistant/messages",
            json={
                "message": "Make this task urgent.",
                "pageContext": {"surface": "task_board", "workItemKey": "ZZZ-999999"},
            },
        )
        assert invalid.status_code == 404
        assert (
            await db.fetchval("SELECT count(*) FROM student_inquiry_reply")
            == before_count
        )
        print(
            "PASS unknown selection rejected; drafts did not send student messages",
            flush=True,
        )
    await db.close()
    print(f"PASS {len(report)} model/action turns; report: {args.report}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:45629")
    parser.add_argument("--staff-ref", default="AU-55ff7e408818")
    parser.add_argument("--student-ref", default="SYN-000061")
    parser.add_argument("--report", default="/tmp/task-board-edward-evaluation.json")
    asyncio.run(check(parser.parse_args()))
