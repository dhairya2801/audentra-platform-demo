"""Run the local API against an explicitly chosen imported university DB.

No worker is started. OpenAI is enabled by default and requires OPENAI_API_KEY.
Use --disable-openai only for intentional offline portal checks. The paid
regression runner has its own ceiling; interactive usage is separate.
"""

import argparse
import os
from collections.abc import Mapping
from urllib.parse import urlparse

import uvicorn

from audentra.bootstrap.api import create_production_app
from audentra.bootstrap.settings import RuntimeSettings
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID


def runtime_settings(
    database_url: str,
    *,
    enable_openai: bool = True,
    environ: Mapping[str, str] | None = None,
) -> RuntimeSettings:
    values = dict(os.environ if environ is None else environ)
    key = values.get("OPENAI_API_KEY", "").strip()
    if enable_openai and not key:
        raise ValueError(
            "OPENAI_API_KEY is required for Edward. Set it before starting the API, "
            "or use --disable-openai for intentional offline portal checks."
        )
    parsed = urlparse(database_url)
    if parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.path.startswith(
        "/audentra_university"
    ):
        raise ValueError("Use an explicit local audentra_university database")
    values = {
        **values,
        "AUDENTRA_ENV": "development",
        "AUTH_MODE": "demo",
        "DATABASE_URL": database_url,
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
        "OPENAI_API_KEY": key if enable_openai else "",
    }
    return RuntimeSettings.from_environment(values)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--port", type=int, default=45609)
    provider = parser.add_mutually_exclusive_group()
    provider.add_argument("--enable-openai", dest="enable_openai", action="store_true")
    provider.add_argument(
        "--disable-openai", dest="enable_openai", action="store_false"
    )
    parser.set_defaults(enable_openai=True)
    args = parser.parse_args()
    try:
        settings = runtime_settings(args.database_url, enable_openai=args.enable_openai)
    except ValueError as error:
        parser.error(str(error))
    print(
        "Edward: OpenAI GPT-5.6 Luna enabled"
        if args.enable_openai
        else "Edward: explicitly offline; university chat unavailable",
        flush=True,
    )
    app = create_production_app(settings)
    uvicorn.run(app, host="127.0.0.1", port=args.port)
