"""Isolated local conference runtime. Never writes the hosted deployment."""

import os
from pathlib import Path
import json
import asyncio
import uvicorn
from audentra.bootstrap.api import create_production_app
from audentra.bootstrap.settings import RuntimeSettings
from audentra.integrations.ai.provider import CompletionClient, ProviderCompletionError

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts/conference"


def environment():
    local = dict(
        line.split("=", 1)
        for line in (ROOT.parent / "handoff-platform-2oct/.env.handoff").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    return {
        **os.environ,
        "AUDENTRA_ENV": "development",
        "AUTH_MODE": "demo",
        "BROWSER_AUTH_REQUIRED": "true",
        "DATABASE_URL": f"postgresql://{local['POSTGRES_USER']}:{local['POSTGRES_PASSWORD']}@127.0.0.1:5548/audentra_university_conference_oct8",
        "DEMO_TENANT_ID": "00000000-0000-7000-8000-000000000003",
        "DEMO_STUDENT_ALLOWLIST": "SYN-000061",
        "DEMO_STAFF_ALLOWLIST": "AU-55ff7e408818",
        "WEB_ORIGIN": "http://localhost:3012,http://127.0.0.1:3012",
        "OBJECT_STORAGE_ENDPOINT": "http://127.0.0.1:9148",
        "OBJECT_STORAGE_BUCKET": "conference-oct8",
        "OBJECT_STORAGE_ACCESS_KEY": local["MINIO_ROOT_USER"],
        "OBJECT_STORAGE_SECRET_KEY": local["MINIO_ROOT_PASSWORD"],
        "OBJECT_STORAGE_FORCE_PATH_STYLE": "true",
        "OBJECT_STORAGE_PROVIDER": "s3",
        "DOCUMENT_WORKER_TOKEN": local["DOCUMENT_WORKER_TOKEN"],
        "API_INTERNAL_URL": "http://127.0.0.1:4112",
        "OPENAI_MODEL": "gpt-6-luna",
        "OPENAI_DOCUMENT_MODEL": os.environ.get("CONFERENCE_DOCUMENT_MODEL", "gpt-6-luna"),
        "OPENAI_DOCUMENT_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
        "DOCUMENT_PROVIDER": "openai",
        "OPENROUTER_API_KEY": "",
        "GROQ_API_KEY": "",
        "DEMO_RESET_TEMPLATE": "",
        "OPENROUTER_DOCUMENT_MAX_TOKENS": "6000",
        "ASSISTANT_TRACE_DEBUG_ENABLED": "false",
        "SMTP_HOST": "",
        "EDWARD_READ_PLANNER": "model",
    }


# All Python Edward and document requests share the same provider client and ledger.
# A missing usage receipt reserves the full conservative bound rather than claiming zero cost.
original_complete = CompletionClient.complete
budget_lock = asyncio.Lock()


async def budgeted_complete(self, body, transport, context=None):
    async with budget_lock:
        path = ARTIFACTS / "spend.json"
        ledger = (
            json.loads(path.read_text())
            if path.exists()
            else {"limitUsd": 2, "calls": [], "chargedUpperBoundUsd": 0}
        )
        rates = {"gpt-6-luna": (0.1, 0.5), "gpt-4.1-mini": (0.4, 1.6)}
        model = body.get("model", "")
        if transport.provider != "openai" or model not in rates:
            raise ProviderCompletionError("Conference budget blocks unpriced model")
        reserve = 0.20
        if ledger["chargedUpperBoundUsd"] + reserve > 2:
            raise ProviderCompletionError("Conference API testing budget exhausted")
        row = {
            "model": model,
            "operation": context.runtime.operation if context else "unknown",
            "reservedUsd": reserve,
            "status": "reserved",
        }
        ledger["calls"].append(row)
        ledger["chargedUpperBoundUsd"] += reserve
        path.write_text(json.dumps(ledger, indent=2))
        try:
            result = await original_complete(self, body, transport, context)
            usage = result.get("usage", {})
            row["usage"] = usage
            row["status"] = "completed"
            if usage:
                cost = (
                    usage.get("prompt_tokens", 0) * rates[model][0]
                    + usage.get("completion_tokens", 0) * rates[model][1]
                ) / 1e6
                row["calculatedUsd"] = cost
                ledger["chargedUpperBoundUsd"] += cost - reserve
            path.write_text(json.dumps(ledger, indent=2))
            return result
        except Exception:
            row["status"] = "failed_without_usage"
            path.write_text(json.dumps(ledger, indent=2))
            raise


if __name__ == "__main__":
    CompletionClient.complete = budgeted_complete
    uvicorn.run(
        create_production_app(RuntimeSettings.from_environment(environment())),
        host="127.0.0.1",
        port=4112,
        timeout_graceful_shutdown=2,
    )
