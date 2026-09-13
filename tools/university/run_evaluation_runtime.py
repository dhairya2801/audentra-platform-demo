"""Loopback Edward host for isolated banks, sharing the persistent spend ceiling.

Uses the normal API and Luna model read planner. No worker or live-source
connection is started. The process lock excludes the other paid evaluator.
"""

import argparse
import asyncio
import fcntl
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import uvicorn

from audentra.bootstrap.api import create_production_app
from evaluate_runtime import BudgetClient, OUT
from run_runtime import runtime_settings


class SerialBudgetClient(BudgetClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget_lock = asyncio.Lock()

    async def complete(self, *args, **kwargs):
        async with self.budget_lock:
            return await super().complete(*args, **kwargs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--port", type=int, default=45639)
    args = parser.parse_args()
    parsed = urlparse(args.database_url)
    if parsed.port != 55591 or not parsed.path.startswith("/audentra_university_test_vnext_"):
        parser.error("Use an explicitly isolated vNext test database on 55591")
    settings = runtime_settings(args.database_url)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "runner.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        app = create_production_app(settings)
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(app):
            async with original_lifespan(app):
                resources = app.state.runtime_resources
                resources.service.ai._completions = SerialBudgetClient(
                    resources.http_client,
                    recorder=resources.service.repository.platform.record_ai_provider_response,
                )
                yield

        app.router.lifespan_context = lifespan
        uvicorn.run(app, host="127.0.0.1", port=args.port)
