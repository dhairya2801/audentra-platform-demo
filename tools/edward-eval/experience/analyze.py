"""Deterministic telemetry and review aggregation, never an automatic truth judge."""

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def analyze(batch, review=None):
    records = json.loads(
        (ROOT / "artifacts/edward-experience" / batch / "transcript.json").read_text()
    )
    source = Counter()
    tools = Counter()
    failures = Counter()
    blocks = Counter()
    latency = []
    calls = []
    words = []
    primary = []
    ids = set()
    checks = Counter()
    for record in records:
        response = record["response"]
        trace = record["trace"] or {}
        source[trace.get("responseSource", "missing")] += 1
        tools.update(c["tool"] for c in trace.get("toolCalls", []))
        failures.update(trace.get("failureCodes", []))
        blocks.update(b["type"] for b in response.get("blocks", []))
        latency.append(record["latencyMs"])
        calls.append(len(trace.get("modelCalls", [])))
        words.append(len(response.get("message", "").split()))
        primary.append(
            len(
                next(
                    (
                        b.get("text", "")
                        for b in response.get("blocks", [])
                        if b["type"] == "answer"
                    ),
                    response.get("message", ""),
                ).split()
            )
        )
        ids.add(trace.get("traceId"))
        checks["http_ok"] += record["status"] == 200
        checks["trace_present"] += bool(trace.get("traceId"))
        checks["no_unconfirmed_receipt"] += not response.get("actionReceipts")
        for block in response.get("blocks", []):
            if block["type"] in ["facts", "checklist", "contacts", "timeline"]:
                checks["record_component"] += 1
                checks["record_component_has_provenance"] += bool(
                    block.get("provenance", {}).get("tool")
                )
            if block["type"] == "next_action":
                checks["next_step_has_no_model_url"] += "href" not in block
    ledger = json.loads((ROOT / "artifacts/edward-experience/spend.json").read_text())
    spend = sum(x["usd"] for x in ledger if x.get("requestId") in ids)
    result = {
        "batch": batch,
        "turns": len(records),
        "httpAndProjectionChecks": dict(checks),
        "responseSources": dict(source),
        "tools": dict(tools),
        "failureCodes": dict(failures),
        "blockTypes": dict(blocks),
        "medianLatencyMs": statistics.median(latency),
        "p90LatencyMs": sorted(latency)[int((len(latency) - 1) * 0.9)],
        "meanModelCalls": round(statistics.mean(calls), 2),
        "medianResponseWords": statistics.median(words),
        "medianPrimaryAnswerWords": statistics.median(primary),
        "conservativeOpenAIUsd": round(spend, 6),
    }
    if review:
        reviews = json.loads(Path(review).read_text())["reviews"]
        assert {(r["case"], r["turn"]) for r in reviews} == {
            (r["case"], r["turn"]) for r in records
        }, "Every turn needs an explicit review"
        result["qualitativeOutcomes"] = dict(Counter(r["outcome"] for r in reviews))
        result["qualitativeIssues"] = dict(
            Counter(i for r in reviews for i in r["issues"])
        )
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch", required=True)
    p.add_argument("--review")
    p.add_argument("--output")
    a = p.parse_args()
    output = json.dumps(analyze(a.batch, a.review), indent=2) + "\n"
    if a.output:
        Path(a.output).write_text(output)
    print(output)
