"""CLI for the independently deployable Python outbox worker."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from audentra.bootstrap.settings import RuntimeSettings
from audentra.bootstrap.worker import WorkerRuntimeResources, build_worker_runtime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Audentra durable outbox worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="claim at most one batch and then exit",
    )
    return parser


def _install_signal_handlers(resources: WorkerRuntimeResources) -> None:
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        candidate = getattr(signal, name, None)
        if candidate is None:
            continue
        try:
            loop.add_signal_handler(candidate, resources.worker.stop)
        except (NotImplementedError, RuntimeError):
            # Windows' default event loop does not expose add_signal_handler.
            continue


async def _run(*, once: bool) -> int:
    resources = await build_worker_runtime(RuntimeSettings.from_environment())
    _install_signal_handlers(resources)
    try:
        if once:
            return await resources.worker.run_once()
        await resources.worker.run()
        return 0
    finally:
        await resources.close()


def run() -> None:
    args = _parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(_run(once=args.once))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    run()
