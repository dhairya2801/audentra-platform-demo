"""Production HTTP path on an isolated DB, with a task-wide $10 spend ceiling.

Run from platform with PYTHONPATH=apps/api/src apps/api/.venv/bin/python
tools/edward-eval/experience/serve.py. All interactive and evaluation calls share
the same locked ledger. Provider bodies/responses are never written by this tool.
"""

import fcntl
import json
import os
import sys
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/university"))
import uvicorn
from audentra.bootstrap.api import create_production_app
from audentra.core.errors import BadRequestError
from audentra.infrastructure.postgres.edward_action_gateway import EdwardActionGateway
from audentra.integrations.ai.provider import CompletionClient
from audentra.integrations.assistant.tools import AssistantToolHost
from audentra.integrations.staff_assistant.tools import StaffAssistantToolHost
from run_runtime import runtime_settings

OUT = ROOT / "artifacts/edward-experience"
OUT.mkdir(parents=True, exist_ok=True)
LEDGER = OUT / "spend.json"
original_complete = CompletionClient.complete


def update(entry=None, *, settle=None):
    with (OUT / "spend.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
        if settle:
            for existing in entries:
                if existing["id"] == settle["id"]:
                    existing.update(settle)
        elif entry:
            if sum(item["usd"] for item in entries) + entry["usd"] > 9.8:
                raise RuntimeError(
                    "Task evaluation budget reached; no provider request sent"
                )
            entries.append(entry)
        temporary = LEDGER.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries, indent=2))
        temporary.replace(LEDGER)


async def metered_complete(self, body, transport, context=None):
    if transport.provider != "openai" or body.get("model") != "gpt-6-luna":
        raise RuntimeError("This evaluation prices only OpenAI GPT-6 Luna")
    # UTF-8 bytes conservatively bound text tokens; headroom covers schema framing.
    # Uncertain/failed requests retain their reservation. Cached tokens are charged
    # at full input price, so the reported spend is a conservative upper estimate.
    entry = {
        "id": str(uuid4()),
        "requestId": context.request_id if context else None,
        "operation": context.runtime.operation if context else None,
        "model": body["model"],
        "usd": (
            (len(json.dumps(body).encode()) + 4096) * 0.1
            + int(body.get("max_completion_tokens", body.get("max_tokens", 4096))) * 0.5
        )
        / 1e6,
        "reserved": True,
    }
    update(entry)
    result = await original_complete(self, body, transport, context)
    usage = result.get("usage", {})
    if "prompt_tokens" in usage and "completion_tokens" in usage:
        update(
            settle={
                "id": entry["id"],
                "usage": usage,
                "reserved": False,
                "usd": (usage["prompt_tokens"] * 0.1 + usage["completion_tokens"] * 0.5)
                / 1e6,
            }
        )
    return result


CompletionClient.complete = metered_complete
fault = ContextVar("evaluation_fault", default="")


def inject_reads(host_type):
    original_read = host_type.read

    async def read(self, primitive, **arguments):
        if fault.get() == primitive + ":error":
            raise TimeoutError("Injected evaluation read timeout")
        if fault.get() == primitive + ":empty":
            return {}
        return await original_read(self, primitive, **arguments)

    host_type.read = read


original_execute = EdwardActionGateway._execute


async def execute_action(self, auth, row, request_id):
    if fault.get() == "write:error":
        raise BadRequestError(
            "EVALUATION_WRITE_FAILURE", "Injected failure before write"
        )
    return await original_execute(self, auth, row, request_id)


EdwardActionGateway._execute = execute_action
inject_reads(AssistantToolHost)
inject_reads(StaffAssistantToolHost)
settings = runtime_settings(os.environ["EDWARD_EXPERIENCE_DATABASE_URL"])
if __name__ == "__main__":
    app = create_production_app(settings)

    @app.middleware("http")
    async def evaluation_fault(request, call_next):
        token = fault.set(request.headers.get("x-edward-eval-fault", ""))
        try:
            return await call_next(request)
        finally:
            fault.reset(token)

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("EDWARD_EXPERIENCE_PORT", "45609")),
    )
