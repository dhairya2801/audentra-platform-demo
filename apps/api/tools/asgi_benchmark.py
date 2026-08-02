"""Run a small, in-process ASGI latency smoke benchmark.

This is intentionally a diagnostic rather than a pass/fail performance gate. It
uses a fixed number of sequential requests so CI and developer machines can
compare p50/p95 output without introducing a flaky latency threshold.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import asdict, dataclass

from httpx import ASGITransport, AsyncClient

from audentra.core.ports import ServiceCall
from audentra.interfaces.http.app import create_app


class BenchmarkPlatformService:
    """Minimal injected service that keeps the benchmark focused on the ASGI path."""

    async def dispatch(self, call: ServiceCall) -> object:
        if call.operation.startswith("health."):
            return {"status": "ok", "service": "audentra-benchmark"}
        return {"operation": call.operation}


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    endpoint: str
    requests: int
    warmup_requests: int
    p50_ms: float
    p95_ms: float
    mean_ms: float
    requests_per_second: float

    def to_dict(self) -> dict[str, str | int | float]:
        return asdict(self)


def percentile_ms(samples_ns: list[int], percentile: float) -> float:
    """Return a nearest-rank percentile in milliseconds."""

    if not samples_ns:
        raise ValueError("At least one latency sample is required")
    if not 0 < percentile <= 1:
        raise ValueError("Percentile must be in the interval (0, 1]")
    ordered = sorted(samples_ns)
    index = min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index] / 1_000_000


async def run_benchmark(
    *,
    requests: int = 200,
    warmup_requests: int = 20,
    endpoint: str = "/health",
) -> BenchmarkResult:
    """Measure one route through FastAPI middleware and routing in process."""

    if requests < 1:
        raise ValueError("requests must be at least 1")
    if warmup_requests < 0:
        raise ValueError("warmup_requests cannot be negative")
    if not endpoint.startswith("/"):
        raise ValueError("endpoint must be an absolute ASGI path")

    app = create_app(service=BenchmarkPlatformService())
    transport = ASGITransport(app=app)
    samples_ns: list[int] = []

    async with AsyncClient(transport=transport, base_url="http://benchmark") as client:
        for _ in range(warmup_requests):
            response = await client.get(endpoint)
            response.raise_for_status()

        wall_start_ns = time.perf_counter_ns()
        for _ in range(requests):
            request_start_ns = time.perf_counter_ns()
            response = await client.get(endpoint)
            response.raise_for_status()
            samples_ns.append(time.perf_counter_ns() - request_start_ns)
        wall_elapsed_ns = time.perf_counter_ns() - wall_start_ns

    total_sample_ns = sum(samples_ns)
    return BenchmarkResult(
        endpoint=endpoint,
        requests=requests,
        warmup_requests=warmup_requests,
        p50_ms=percentile_ms(samples_ns, 0.50),
        p95_ms=percentile_ms(samples_ns, 0.95),
        mean_ms=(total_sample_ns / requests) / 1_000_000,
        requests_per_second=requests / (wall_elapsed_ns / 1_000_000_000),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--endpoint", default="/health")
    arguments = parser.parse_args()
    result = asyncio.run(
        run_benchmark(
            requests=arguments.requests,
            warmup_requests=arguments.warmup,
            endpoint=arguments.endpoint,
        )
    )
    print(json.dumps(result.to_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
