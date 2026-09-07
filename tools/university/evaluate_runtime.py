"""Exercise the actual PostgreSQL Edward service with a persistent spend ceiling.

Only usage and redacted local diagnostics are written under ignored artifacts.
The evaluator oracle is used to choose actors/questions, never as model context.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from audentra.bootstrap.api import build_api_runtime
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.core.ports import ServiceCall
from audentra.integrations.ai.provider import CompletionClient
from audentra.integrations.assistant.trace import get_assistant_trace_recorder

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/university-runtime/evaluation"
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID

TENANT = SYNTHETIC_TENANT_ID


class BudgetClient(CompletionClient):
    """Reserve upper-bound cost before sending; uncertain calls keep reservation.

    This harness is sequential; an exclusive process lock prevents two runners.
    UTF-8 bytes plus 4096 bound prompt tokens conservatively. Every call must
    use the priced model, including classifiers and retries.
    """

    async def complete(self, body, transport, context=None):
        if body.get("model") != "gpt-5.6-luna" or transport.provider != "openai":
            raise RuntimeError("Evaluation permits only OpenAI GPT-5.6 Luna")
        ledger = OUT / "spend.json"
        entries = json.loads(ledger.read_text()) if ledger.exists() else []
        reserve = (
            (len(json.dumps(body).encode()) + 4096) * 0.2
            + int(body.get("max_completion_tokens", body.get("max_tokens", 4096))) * 1.2
        ) / 1_000_000
        if sum(e["usd"] for e in entries) + reserve > 4.5:
            raise RuntimeError("Evaluation spend ceiling reached; no request sent")
        entry = {
            "operation": context.runtime.operation if context else None,
            "usd": reserve,
            "reserved": True,
        }
        entries.append(entry)
        ledger.write_text(json.dumps(entries, indent=2))
        result = await super().complete(body, transport, context)
        usage = result.get("usage", {})
        if "prompt_tokens" in usage and "completion_tokens" in usage:
            entry.update(
                usd=(usage["prompt_tokens"] * 0.2 + usage["completion_tokens"] * 1.2)
                / 1_000_000,
                reserved=False,
                usage=usage,
            )
            ledger.write_text(json.dumps(entries, indent=2))
        return result


async def run(args):
    import fcntl

    OUT.mkdir(parents=True, exist_ok=True)
    lock = (OUT / "runner.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    settings = RuntimeSettings.from_environment(
        {
            "DATABASE_URL": args.database_url,
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
            "OPENAI_MODEL": "gpt-5.6-luna",
            "AI_CHAT_PROVIDER": "openai",
            "ASSISTANT_TRACE_DEBUG_ENABLED": "true",
            "EDWARD_READ_PLANNER": "model",
        }
    )
    runtime = await build_api_runtime(settings)
    service = runtime.service
    service.ai._completions = BudgetClient(
        runtime.http_client,
        recorder=service.repository.platform.record_ai_provider_response,
    )
    try:
        oracle = json.loads((ROOT / "artifacts/university-v3/oracle.json").read_text())
        selected = [c for c in oracle if c["id"] in args.cases.split(",")]
        if args.role == "staff":
            from sqlalchemy import text

            async with runtime.engine.connect() as c:
                actor = str(
                    await c.scalar(
                        text(
                            "SELECT id FROM staff_member WHERE tenant_id=:tenant AND role_code='operations_lead' AND active ORDER BY id LIMIT 1"
                        ),
                        {"tenant": TENANT},
                    )
                )
        for case in selected:
            auth = AuthContext(
                TENANT,
                case["student_id"],
                actor if args.role == "staff" else case["student_id"],
                args.role,
            )
            question = args.question or case["prompt"]
            if (
                args.role == "staff"
                and not args.question
                and case["id"] not in ("staff-cohort", "identity-collision")
            ):
                from sqlalchemy import text

                async with runtime.engine.connect() as c:
                    ref = await c.scalar(
                        text(
                            "SELECT external_ref FROM student WHERE tenant_id=:tenant AND id=:sid"
                        ),
                        {"tenant": TENANT, "sid": case["student_id"]},
                    )
                question = f"For student {ref}: {question}"
            rid = str(uuid4())
            try:
                result = await service.dispatch(
                    ServiceCall(
                        args.role + ".ask_edward",
                        auth,
                        rid,
                        payload={
                            "message": question,
                            "pagePath": "/dashboard",
                            "clientMessageId": str(uuid4()),
                        },
                    )
                )
                trace = get_assistant_trace_recorder().get(rid)
                (OUT / f"{case['id']}-{rid}.json").write_text(
                    json.dumps(
                        {"case": case["id"], "response": result, "trace": trace},
                        indent=2,
                        default=str,
                    )
                )
                print(
                    json.dumps({"case": case["id"], "response": result}, default=str),
                    flush=True,
                )
            except Exception as error:
                print(
                    json.dumps({"case": case["id"], "error": repr(error)}), flush=True
                )
                raise
    finally:
        await runtime.close()
        lock.close()
    print(
        "Reserved total USD:",
        sum(e["usd"] for e in json.loads((OUT / "spend.json").read_text()))
        if (OUT / "spend.json").exists()
        else 0,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--question")
    parser.add_argument("--cases", default="clear-control")
    parser.add_argument("--role", choices=["student", "staff"], default="student")
    asyncio.run(run(parser.parse_args()))
