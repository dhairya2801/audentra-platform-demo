from __future__ import annotations

import asyncio

import pytest

from tools.asgi_benchmark import percentile_ms, run_benchmark


def test_percentile_uses_deterministic_nearest_rank() -> None:
    samples_ns = [5_000_000, 1_000_000, 3_000_000, 2_000_000, 4_000_000]

    assert percentile_ms(samples_ns, 0.50) == 3.0
    assert percentile_ms(samples_ns, 0.95) == 5.0


@pytest.mark.parametrize("percentile", [0.0, -0.1, 1.01])
def test_percentile_rejects_invalid_ranges(percentile: float) -> None:
    with pytest.raises(ValueError, match="Percentile"):
        percentile_ms([1], percentile)


def test_asgi_benchmark_smoke_reports_latency_without_a_hard_threshold() -> None:
    result = asyncio.run(run_benchmark(requests=8, warmup_requests=2))

    assert result.endpoint == "/health"
    assert result.requests == 8
    assert result.warmup_requests == 2
    assert result.p50_ms >= 0
    assert result.p95_ms >= result.p50_ms
    assert result.mean_ms >= 0
    assert result.requests_per_second > 0
