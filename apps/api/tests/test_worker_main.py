from __future__ import annotations

from typing import Any, cast

import pytest

from audentra.bootstrap.worker import WorkerRuntimeResources
from audentra.interfaces.worker import main as worker_main


class StubCliWorker:
    def __init__(self, *, once_result: int = 0) -> None:
        self.once_result = once_result
        self.once_calls = 0
        self.run_calls = 0

    async def run_once(self) -> int:
        self.once_calls += 1
        return self.once_result

    async def run(self) -> None:
        self.run_calls += 1


class StubCliResources:
    def __init__(self, worker: StubCliWorker) -> None:
        self.worker = worker
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class StubRuntimeSettings:
    value = object()

    @classmethod
    def from_environment(cls) -> object:
        return cls.value


def test_worker_parser_exposes_bounded_once_mode() -> None:
    assert worker_main._parser().parse_args(["--once"]).once is True
    assert worker_main._parser().parse_args([]).once is False


@pytest.mark.anyio
@pytest.mark.parametrize(("once", "expected"), [(True, 4), (False, 0)])
async def test_worker_cli_runs_selected_mode_and_always_closes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    once: bool,
    expected: int,
) -> None:
    worker = StubCliWorker(once_result=4)
    resources = StubCliResources(worker)
    installed: list[StubCliResources] = []

    async def build(settings: object) -> WorkerRuntimeResources:
        assert settings is StubRuntimeSettings.value
        return cast(WorkerRuntimeResources, cast(Any, resources))

    monkeypatch.setattr(worker_main, "RuntimeSettings", StubRuntimeSettings)
    monkeypatch.setattr(worker_main, "build_worker_runtime", build)
    monkeypatch.setattr(worker_main, "_install_signal_handlers", installed.append)

    assert await worker_main._run(once=once) == expected
    assert installed == [resources]
    assert worker.once_calls == int(once)
    assert worker.run_calls == int(not once)
    assert resources.closed is True
