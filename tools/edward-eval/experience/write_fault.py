"""Fail a confirmed write before mutation and inspect its durable failed receipt."""

import asyncio
import json
from uuid import uuid4

import httpx

from run import DEFAULT_STUDENT, ROOT, TENANT


async def main():
    headers = {
        "x-demo-tenant-id": TENANT,
        "x-demo-student-id": DEFAULT_STUDENT,
        "x-demo-actor-id": DEFAULT_STUDENT,
    }
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:45610", timeout=60, headers=headers
    ) as c:
        before = (await c.get("/v1/student/profile")).json()
        conv = (
            await c.post(
                "/v1/student/assistant/conversations",
                json={"pageContext": {"path": "/profile", "label": "Profile"}},
            )
        ).json()
        response = (
            await c.post(
                "/v1/student/assistant/messages",
                json={
                    "conversationId": conv["id"],
                    "clientMessageId": str(uuid4()),
                    "message": "Change my preferred name to Sentinel",
                    "pageContext": {"path": "/profile", "label": "Profile"},
                },
            )
        ).json()
        intent = response["actionIntents"][0]
        path = "/v1/student/assistant/action-intents/" + intent["id"]
        body = {
            "expectedVersion": intent["version"],
            "contentSha256": intent["contentSha256"],
        }
        failed = await c.post(
            path + "/confirm", json=body, headers={"x-edward-eval-fault": "write:error"}
        )
        current = (await c.get(path)).json()
        after = (await c.get("/v1/student/profile")).json()
        replay = (await c.post(path + "/confirm", json=body)).json()
        assert failed.status_code == 400
        assert (
            current["status"] == "failed" and current["receipt"]["affectedCount"] == 0
        )
        assert before == after
        assert replay == current["receipt"] and replay["status"] == "failed"
        out = {
            "checks": {
                "write_returns_failure": True,
                "durable_failed_receipt": True,
                "no_profile_change": True,
                "retry_cannot_execute_failed_intent": True,
            },
            "intent": current,
        }
        (ROOT / "artifacts/edward-experience/write-fault.json").write_text(
            json.dumps(out, indent=2)
        )
        print(out["checks"])


if __name__ == "__main__":
    asyncio.run(main())
