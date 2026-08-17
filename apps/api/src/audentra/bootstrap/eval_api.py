"""Evaluation host for the canonical Python Edward.

Boots the real FastAPI app over the in-memory platform composition with one
evaluation persona applied, so the Edward eval harness exercises the exact
production request path — HTTP boundary, demo authentication binding,
durable-conversation history, `AssistantPipeline`, tool execution,
derivation, grounded composition, the claim guard, and per-turn tracing —
without requiring Postgres.

With `OPENAI_API_KEY` (or `OPENROUTER_API_KEY`) set, the same
`StudentAIGateway` production uses supplies the model planner and composer;
without a key the pipeline runs fully deterministically. Trace debug
endpoints are always enabled so the harness can attach the
`AssistantTurnTrace` for every graded case.

Usage:
    uv run --directory apps/api audentra-eval-api --port 45601 --persona new_admit

Set `WEB_ORIGIN` when driving this host from a browser (the Edward Lab), so
the portal dev server's actual origin is allowed:

    WEB_ORIGIN=http://localhost:3001 uv run --directory apps/api \\
        audentra-eval-api --port 45601 --persona new_admit
"""

from __future__ import annotations

import argparse
import os

import httpx
import uvicorn
from fastapi import FastAPI

from audentra.application.platform_service import InMemoryPlatformService
from audentra.bootstrap.settings import RuntimeSettings
from audentra.infrastructure.memory.eval_personas import PERSONAS, apply_persona
from audentra.infrastructure.memory.store import InMemoryPlatformStore
from audentra.integrations.ai.gateway import StudentAIGateway
from audentra.integrations.ai.provider import CompletionClient
from audentra.interfaces.http.app import create_app
from audentra.interfaces.http.config import HttpSettings

# Primitive reads the assistant tool host draws on; each may be faulted.
FAULTABLE_PRIMITIVES = (
    "profile",
    "requirements",
    "documents",
    "payments",
    "financials",
    "dashboard",
    "onboarding",
    "housing_plan",
    "appointments",
    "help",
    "academics",
    "campus_life",
    "messages",
)
FAULT_MODES = ("error", "timeout", "empty")


def _parse_faults(raw: list[str]) -> dict[str, str]:
    faults: dict[str, str] = {}
    for item in raw:
        name, _, mode = item.partition("=")
        if name not in FAULTABLE_PRIMITIVES or mode not in FAULT_MODES:
            raise SystemExit(
                f"Invalid --fault {item!r}. Expected <primitive>=<mode> with primitive in "
                f"{', '.join(FAULTABLE_PRIMITIVES)} and mode in {', '.join(FAULT_MODES)}."
            )
        faults[name] = mode
    return faults


def _apply_faults(store: InMemoryPlatformStore, faults: dict[str, str]) -> None:
    """Wrap store readers so selected assistant tool reads fail on purpose.

    `timeout` raises TimeoutError, which the tool executor reports as a timed-
    out read; `error` becomes an unavailable read; `empty` keeps the read
    available but strips every record list, modelling a missing record. Only
    the wrapped reader changes — the rest of the persona state stays canonical
    so ground truth for unaffected domains remains valid.
    """

    def _emptied(value: object) -> object:
        if isinstance(value, dict):
            return {key: ([] if isinstance(item, list) else item) for key, item in value.items()}
        return value

    for name, mode in faults.items():
        attribute = f"get_{name}"
        original = getattr(store, attribute)

        def faulty(
            *args: object, _mode: str = mode, _original: object = original, **kwargs: object
        ) -> object:
            if _mode == "timeout":
                raise TimeoutError("injected evaluation fault: timeout")
            if _mode == "error":
                raise RuntimeError("injected evaluation fault: read error")
            return _emptied(_original(*args, **kwargs))  # type: ignore[operator]

        setattr(store, attribute, faulty)


def _web_origins() -> tuple[str, ...]:
    """Browser origins this host serves, from `WEB_ORIGIN`.

    The Edward Lab drives this host from a portal dev server, and that server
    lands on whichever port is free — so a hard-coded single origin makes the
    Lab unusable whenever the default port is taken by another checkout.
    Same parsing as `HttpSettings.from_environment`, same default.
    """

    configured = tuple(
        origin.strip()
        for origin in os.getenv("WEB_ORIGIN", "http://localhost:3000").split(",")
        if origin.strip()
    )
    return configured or ("http://localhost:3000",)


def build_eval_app(persona: str, faults: dict[str, str] | None = None) -> tuple[FastAPI, str]:
    """The FastAPI app plus a description of the model configuration."""

    store = InMemoryPlatformStore()
    apply_persona(store, persona)
    if faults:
        _apply_faults(store, faults)

    ai: object | None = None
    model_description = "deterministic (no provider key)"
    if os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("OPENROUTER_API_KEY", "").strip():
        settings = RuntimeSettings.from_environment()
        http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            timeout=httpx.Timeout(45.0, connect=10.0, pool=5.0),
            follow_redirects=False,
        )
        ai = StudentAIGateway(settings.ai, CompletionClient(http_client))
        model_description = (
            f"openai:{settings.ai.openai_model}"
            if settings.ai.openai_api_key
            else f"openrouter:{settings.ai.openrouter_model}"
        )

    service = (
        InMemoryPlatformService(store=store, ai=ai)
        if ai is not None
        else InMemoryPlatformService(store=store)
    )
    app = create_app(
        service=service,
        settings=HttpSettings(
            assistant_trace_debug_enabled=True,
            web_origins=_web_origins(),
        ),
    )
    return app, model_description


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Edward evaluation API host")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--persona",
        default="new_admit",
        choices=sorted(PERSONAS),
        help="Evaluation student state to serve",
    )
    parser.add_argument(
        "--fault",
        action="append",
        default=[],
        metavar="PRIMITIVE=MODE",
        help=(
            "Inject a read fault, e.g. --fault documents=timeout. "
            f"Primitives: {', '.join(FAULTABLE_PRIMITIVES)}; modes: {', '.join(FAULT_MODES)}."
        ),
    )
    args = parser.parse_args(argv)

    faults = _parse_faults(list(args.fault))
    app, model_description = build_eval_app(args.persona, faults)
    # One parseable readiness line for the harness that spawned this process.
    fault_note = f" faults={','.join(f'{k}={v}' for k, v in faults.items())}" if faults else ""
    print(
        f"edward-eval-api persona={args.persona} model={model_description}{fault_note} "
        f"listening on http://{args.host}:{args.port}",
        flush=True,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
