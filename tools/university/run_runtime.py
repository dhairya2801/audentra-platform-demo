"""Run the local API against an explicitly chosen imported university DB.

No worker is started. OpenAI is off unless --enable-openai is passed. The paid
regression runner has its own persistent ceiling; normal interactive usage is
not an evaluation-budgeted process.
"""

import argparse
import os
from urllib.parse import urlparse

import uvicorn

from audentra.bootstrap.api import create_production_app
from audentra.bootstrap.settings import RuntimeSettings
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--port", type=int, default=45609)
    parser.add_argument("--enable-openai", action="store_true")
    args = parser.parse_args()
    parsed = urlparse(args.database_url)
    if parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.path.startswith(
        "/audentra_university"
    ):
        raise ValueError("Use an explicit local audentra_university database")
    values = {
        **os.environ,
        "AUDENTRA_ENV": "development",
        "AUTH_MODE": "demo",
        "DATABASE_URL": args.database_url,
        "DEMO_TENANT_ID": SYNTHETIC_TENANT_ID,
        "DEMO_STUDENT_ID": "ac2fa509-b4e3-402d-900b-ffb8440fc430",
        "DEMO_ACTOR_ID": "ac2fa509-b4e3-402d-900b-ffb8440fc430",
        "DEMO_STAFF_ACTOR_ID": "01973261-954a-5019-8e9e-24a699abea7b",
        "WEB_ORIGIN": "http://127.0.0.1:3009,http://localhost:3009,http://localhost:3000,http://127.0.0.1:3000",
        "BROWSER_AUTH_REQUIRED": "false",
        "ASSISTANT_TRACE_DEBUG_ENABLED": "true",
        "OPENAI_MODEL": "gpt-5.6-luna",
        "EDWARD_READ_PLANNER": "model",
        "OPENROUTER_API_KEY": "",
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "")
        if args.enable_openai
        else "",
    }
    app = create_production_app(RuntimeSettings.from_environment(values))
    uvicorn.run(app, host="127.0.0.1", port=args.port)
