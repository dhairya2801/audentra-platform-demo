"""Real HTTP confirmation, idempotency and cross-actor checks on isolated eval DB."""

import asyncio
import json
import os
from uuid import uuid4

import httpx

from run import DEFAULT_STUDENT, ROOT, STAFF, TENANT


async def main():
    results = []
    async with httpx.AsyncClient(
        base_url=os.environ.get("EDWARD_EXPERIENCE_ORIGIN", "http://127.0.0.1:45609"),
        timeout=120,
    ) as client:
        for role, message in [
            ("student", "Change my preferred name to River"),
            (
                "staff",
                "Create an internal follow-up for student "
                + DEFAULT_STUDENT
                + " titled Review enrollment evidence",
            ),
        ]:
            headers = {
                "x-demo-tenant-id": TENANT,
                "x-demo-student-id": DEFAULT_STUDENT,
                "x-demo-actor-type": role,
                "x-demo-actor-id": STAFF if role == "staff" else DEFAULT_STUDENT,
            }
            path = "/v1/" + role + "/assistant"
            conv = await client.post(
                path + "/conversations",
                headers=headers,
                json={"pageContext": {"path": "/dashboard", "label": "Dashboard"}}
                if role == "student"
                else {},
            )
            conv.raise_for_status()
            cid = conv.json()["id"]
            body = {
                "conversationId": cid,
                "clientMessageId": str(uuid4()),
                "message": message,
            }
            if role == "student":
                body["pageContext"] = {"path": "/dashboard", "label": "Dashboard"}
            response = await client.post(path + "/messages", headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
            intents = payload.get("actionIntents") or []
            if not intents:
                results.append(
                    {
                        "role": role,
                        "check": "propose supported action",
                        "pass": False,
                        "response": payload,
                    }
                )
                continue
            intent = intents[0]
            action = path + "/action-intents/" + intent["id"]
            foreign = {
                **headers,
                "x-demo-actor-id": "e7131a9c-25a0-5437-a2a9-4672c9f62b43",
            }
            if role == "student":
                foreign["x-demo-student-id"] = "bf50a4bd-cc1c-4473-89d6-6ceab014714c"
                foreign["x-demo-actor-id"] = foreign["x-demo-student-id"]
            for check, endpoint in [
                ("foreign conversation", path + "/conversations/" + cid + "/messages"),
                ("foreign intent", action),
            ]:
                result = await client.get(endpoint, headers=foreign)
                results.append(
                    {
                        "role": role,
                        "check": check,
                        "status": result.status_code,
                        "pass": result.status_code in (403, 404),
                    }
                )
            confirm = {
                "expectedVersion": intent["version"],
                "contentSha256": intent["contentSha256"],
            }
            for check, values in [
                (
                    "stale version",
                    {**confirm, "expectedVersion": intent["version"] + 1},
                ),
                ("tampered preview", {**confirm, "contentSha256": "0" * 64}),
            ]:
                result = await client.post(
                    action + "/confirm", headers=headers, json=values
                )
                results.append(
                    {
                        "role": role,
                        "check": check,
                        "status": result.status_code,
                        "pass": result.status_code == 409,
                    }
                )
            first = await client.post(
                action + "/confirm", headers=headers, json=confirm
            )
            second = await client.post(
                action + "/confirm", headers=headers, json=confirm
            )
            receipt = first.json()
            results.append(
                {
                    "role": role,
                    "check": "confirmed exactly once",
                    "pass": first.is_success
                    and second.is_success
                    and receipt == second.json()
                    and receipt.get("status") == "succeeded",
                    "receipt": receipt,
                }
            )
            if role == "student" and receipt.get("status") == "succeeded":
                # Restore the original value through the same reviewed action protocol.
                original = intent["preview"]["changes"][0]["before"]
                body.update(
                    message="Change my preferred name to " + str(original),
                    clientMessageId=str(uuid4()),
                )
                restore = (
                    await client.post(path + "/messages", headers=headers, json=body)
                ).json()["actionIntents"][0]
                restored = await client.post(
                    path + "/action-intents/" + restore["id"] + "/confirm",
                    headers=headers,
                    json={
                        "expectedVersion": restore["version"],
                        "contentSha256": restore["contentSha256"],
                    },
                )
                results.append(
                    {
                        "role": role,
                        "check": "restore preference",
                        "pass": restored.is_success,
                    }
                )
    out = ROOT / "artifacts/edward-experience/boundaries.json"
    out.write_text(json.dumps(results, indent=2))
    for r in results:
        print(r["role"], r["check"], r["pass"])
    assert all(r["pass"] for r in results), "See " + str(out)


if __name__ == "__main__":
    asyncio.run(main())
