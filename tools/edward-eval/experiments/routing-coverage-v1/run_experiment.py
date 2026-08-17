"""Routing-coverage experiment: default routing vs the experimental coverage gate.

Runs a hand-labeled corpus of single-intent, compound, context-mention,
follow-up, ambiguous, and unsupported requests through the real
`AssistantPipeline` over canonical eval personas, in three modes:

  default  - routing exactly as production ships it
  augment  - EXPERIMENTAL deterministic coverage gate (zero model calls)
  planner  - EXPERIMENTAL gate escalating gaps to the model planner

Grades every case on three levels per material ask:
  planned  - the route itself selects a read that answers the ask
  executed - an answering read ran (dependency round included)
  answered - the final message states the asked-for facts

Deterministic by default (no provider key touched). `--provider` wires the
real `StudentAIGateway` **planner only** (composition stays deterministic so
answer grading is stable), which is the intentionally-enabled paid part.

Usage:
    uv run --directory apps/api python \
        ../../tools/edward-eval/experiments/routing-coverage-v1/run_experiment.py
    ... --provider          # include real planner calls (spends tokens)
    ... --mode augment      # restrict modes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from pathlib import Path
from typing import Any

from audentra.core.auth import AuthContext
from audentra.infrastructure.memory.eval_personas import apply_persona
from audentra.infrastructure.memory.store import DEMO_IDS, InMemoryPlatformStore
from audentra.integrations.assistant.pipeline import AssistantPipeline
from audentra.integrations.assistant.tools import AssistantToolHost
from audentra.integrations.assistant.trace import AssistantTurnTrace

# A domain is answered by these reads (mirrors the gate's own table; duplicated
# here on purpose so the harness cannot inherit a bug in the thing it grades).
ANSWERING_TOOLS: dict[str, tuple[str, ...]] = {
    "deadlines": ("getStudentDeadlines",),
    "documents": ("getDocumentStatuses",),
    "aid": (
        "getFinancialAidStatus",
        "getFinancialAidSummary",
        "getAidDisbursements",
        "getFinancialAidSupportOptions",
    ),
    "account": ("getStudentAccountSummary",),
    "housing": ("getStudentHousingStatus", "getStudentHousingEligibility", "getHousingOptions"),
    "registration": ("getRegistrationStatus",),
    "academics": ("getAcademicPlan", "getAcademicStanding"),
    "campus": ("getCampusLife",),
    "holds": ("getEnrollmentHolds",),
}

# Message-level evidence that an ask was actually answered.
ANSWER_MARKERS: dict[str, str] = {
    "documents": r"transcript|document|immuni",
    "account": r"\$|balance|deposit",
    "aid": r"\baid\b|fafsa|grant|scholarship|verification",
    "housing": r"housing|residence|dorm",
    "registration": r"regist",
    "campus": r"club|event|campus",
    "deadlines": r"\bdue\b|deadline|overdue",
    "academics": r"course|academic plan|prerequisite",
    "holds": r"hold|blocker|blocking",
}

CORPUS: list[dict[str, Any]] = [
    {
        "id": "ss-1",
        "category": "single-simple",
        "message": "What is my transcript status?",
        "asks": ["documents"],
    },
    {
        "id": "sc-1",
        "category": "single-complex",
        "message": "Why can't I apply for housing?",
        "asks": ["housing"],
    },
    {
        "id": "mdo-1",
        "category": "multi-explicit-operator",
        "message": "Show my housing and financial aid status.",
        "asks": ["housing", "aid"],
    },
    {
        "id": "mno-1",
        "category": "multi-no-operator",
        "message": (
            "I paid my deposit. Why can't I apply for housing and what documents "
            "am I still missing?"
        ),
        "asks": ["housing", "documents"],
    },
    {
        "id": "mno-2",
        "category": "multi-no-operator",
        "message": "What's my account balance and what clubs can I join?",
        "asks": ["account", "campus"],
    },
    {
        "id": "mno-3",
        "category": "multi-no-operator",
        "message": "How much do I owe and are my documents all in?",
        "asks": ["account", "documents"],
    },
    {
        "id": "nc-1",
        "category": "natural-compound",
        "message": (
            "Am I ready for classes or is there anything with aid, housing, or "
            "documents I still need to handle?"
        ),
        "asks": ["aid", "housing", "documents"],
    },
    {
        "id": "um-1",
        "category": "unrelated-multi-intent",
        "message": "Do I have any holds and what clubs can I join?",
        "asks": ["holds", "campus"],
    },
    {
        "id": "id-1",
        "category": "implicit-dependency",
        "message": "Why can't I register even though I paid?",
        "persona": "deposit_posted",
        "asks": ["registration"],
    },
    {
        "id": "fu-1",
        "category": "follow-up",
        "message": "What about housing?",
        "history": [
            {"role": "user", "content": "What's my financial aid status?"},
            {"role": "assistant", "content": "Your FAFSA is received; verification is pending."},
        ],
        "asks": ["housing"],
    },
    {
        "id": "am-1",
        "category": "ambiguous",
        "message": "Am I all set?",
        "asks": [],
    },
    {
        "id": "ds-1",
        "category": "direct-status",
        "message": "Did my transcript arrive?",
        "asks": ["documents"],
    },
    {
        "id": "un-1",
        "category": "unsupported",
        "message": "What's my roommate's phone number?",
        "asks": [],
        "expect_refusal": True,
    },
    {
        "id": "tr-1",
        "category": "multi-no-operator",
        "message": "What's my balance, what clubs can I join, and did my transcript arrive?",
        "asks": ["account", "campus", "documents"],
    },
    {
        "id": "hd-1",
        "category": "multi-no-operator",
        "message": "Why can't I apply for housing? What documents am I still missing?",
        "asks": ["housing", "documents"],
    },
    {
        "id": "dd-1",
        "category": "multi-no-operator",
        "message": "What documents am I missing and when are they due?",
        "asks": ["documents", "deadlines"],
    },
]

MODES = ("default", "augment", "planner")
GATE_BY_MODE = {"default": "off", "augment": "augment", "planner": "planner"}

AUTH = AuthContext(
    tenant_id=DEMO_IDS["tenant_id"],
    student_id=DEMO_IDS["student_id"],
    actor_id=DEMO_IDS["person_id"],
    actor_type="student",
)

PRIMITIVES = (
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


def build_host(store: InMemoryPlatformStore) -> AssistantToolHost:
    """The same 13-primitive wiring `platform_service` gives the pipeline."""

    def sync_read(reader: Any) -> Any:
        async def read() -> dict[str, Any]:
            return reader(AUTH)

        return read

    return AssistantToolHost(
        {name: sync_read(getattr(store, f"get_{name}")) for name in PRIMITIVES}
    )


def build_provider_planner() -> Any:
    """The real gateway planner, wired exactly as platform_service wires it."""

    import httpx

    from audentra.bootstrap.settings import RuntimeSettings
    from audentra.integrations.ai.gateway import StudentAIGateway
    from audentra.integrations.ai.provider import CompletionClient
    from audentra.integrations.assistant.classify import REQUEST_TYPES
    from audentra.integrations.assistant.planner import TOOL_DESCRIPTIONS

    settings = RuntimeSettings.from_environment()
    client = httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0))
    gateway = StudentAIGateway(settings.ai, CompletionClient(client))

    async def plan(
        *, message: str, page_label: str | None = None, page_path: str | None = None
    ) -> Any:
        return await gateway.plan_assistant_tool_reads(
            message=message,
            page_label=page_label,
            page_path=page_path,
            allowed_request_types=REQUEST_TYPES,
            available_tools=TOOL_DESCRIPTIONS,
            tenant_id=AUTH.tenant_id,
            student_id=AUTH.student_id,
            request_id="routing-coverage-experiment",
        )

    return plan


def grade(case: dict[str, Any], message: str, trace: AssistantTurnTrace) -> dict[str, Any]:
    executed = [call["tool"] for call in trace.tool_calls]
    selected = list(trace.selected_tools)
    per_ask: dict[str, dict[str, bool]] = {}
    for ask in case["asks"]:
        tools = ANSWERING_TOOLS[ask]
        per_ask[ask] = {
            "planned": any(tool in selected for tool in tools),
            "executed": any(tool in executed for tool in tools),
            "answered": bool(re.search(ANSWER_MARKERS[ask], message, re.IGNORECASE)),
        }
    # Strict: the route itself plans an answering read for every ask, and the
    # answer states it — persona-independent. Experienced: on this persona the
    # evidence got read (dependency round included) and the answer states it —
    # a dependency round can mask a routing gap when a gate happens to name
    # the same fact, so both are reported.
    strict = all(v["planned"] and v["answered"] for v in per_ask.values())
    experienced = all(v["executed"] and v["answered"] for v in per_ask.values())
    return {
        "perAsk": per_ask,
        "fullIntentCoverage": strict if case["asks"] else None,
        "experiencedCoverage": experienced if case["asks"] else None,
        "missedAsks": [ask for ask, v in per_ask.items() if not (v["planned"] and v["answered"])],
    }


async def run_case(case: dict[str, Any], mode: str, planner: Any) -> dict[str, Any]:
    store = InMemoryPlatformStore()
    apply_persona(store, case.get("persona", "new_admit"))
    pipeline = AssistantPipeline(
        build_host(store), model_planner=planner, coverage_gate=GATE_BY_MODE[mode]
    )
    trace = AssistantTurnTrace(trace_id=f"{case['id']}-{mode}")
    started = time.perf_counter()
    result = await pipeline.execute(
        message=case["message"], history=case.get("history", []), trace=trace
    )
    wall_ms = (time.perf_counter() - started) * 1_000
    classification = result.classification
    gate_stage = next((s for s in trace.stages if s["stage"] == "coverage_gate"), None)
    usage_tokens = sum(
        int((call.get("usage") or {}).get("totalTokens") or 0) for call in trace.model_calls
    )
    record = {
        "id": case["id"],
        "category": case["category"],
        "mode": mode,
        "requestType": classification.request_type if classification else None,
        "additionalRequestTypes": (
            list(classification.additional_request_types) if classification else []
        ),
        "classificationSource": classification.source if classification else None,
        "selectedTools": list(trace.selected_tools),
        "executedTools": [call["tool"] for call in trace.tool_calls],
        "gate": gate_stage,
        "modelCalls": len(trace.model_calls),
        "modelTokens": usage_tokens,
        "wallMs": round(wall_ms, 1),
        "message": result.message,
        "failureCodes": list(result.failure_codes),
    }
    record.update(grade(case, result.message, trace))
    if case.get("expect_refusal"):
        record["refusedAsExpected"] = (
            classification is not None
            and classification.request_type == "unsupported_or_out_of_scope"
        )
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for mode in sorted({r["mode"] for r in records}):
        rows = [r for r in records if r["mode"] == mode]
        graded = [r for r in rows if r["fullIntentCoverage"] is not None]
        summary[mode] = {
            "cases": len(rows),
            "gradedCases": len(graded),
            "fullIntentCoverage": sum(1 for r in graded if r["fullIntentCoverage"]),
            "experiencedCoverage": sum(1 for r in graded if r["experiencedCoverage"]),
            "missedAsks": sum(len(r["missedAsks"]) for r in graded),
            "gateFired": sum(
                1
                for r in rows
                if r["gate"]
                and r["gate"].get("action")
                in ("augmented", "planner_plan_accepted", "fallback_augmented")
            ),
            "modelCalls": sum(r["modelCalls"] for r in rows),
            "modelTokens": sum(r["modelTokens"] for r in rows),
            "meanWallMs": round(statistics.mean(r["wallMs"] for r in rows), 1),
            "p95WallMs": round(
                sorted(r["wallMs"] for r in rows)[max(0, int(len(rows) * 0.95) - 1)], 1
            ),
        }
    return summary


def render_markdown(records: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        "# Routing-coverage experiment results",
        "",
        "| mode | graded cases | strict coverage | experienced coverage | missed asks "
        "| gate fired | model calls | tokens | mean ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for mode, s in summary.items():
        lines.append(
            f"| {mode} | {s['gradedCases']} | {s['fullIntentCoverage']} "
            f"| {s['experiencedCoverage']} | {s['missedAsks']} "
            f"| {s['gateFired']} | {s['modelCalls']} | {s['modelTokens']} "
            f"| {s['meanWallMs']} | {s['p95WallMs']} |"
        )
    lines += ["", "## Per-case coverage (planned/answered per ask)", ""]
    lines.append("| case | mode | classification | asks | missed |")
    lines.append("|---|---|---|---|---|")
    for r in records:
        asks = ", ".join(
            f"{ask}:{'P' if v['planned'] else '-'}{'A' if v['answered'] else '-'}"
            for ask, v in r["perAsk"].items()
        )
        extras = f" +{r['additionalRequestTypes']}" if r["additionalRequestTypes"] else ""
        lines.append(
            f"| {r['id']} | {r['mode']} | {r['requestType']}{extras} | {asks or '—'} "
            f"| {', '.join(r['missedAsks']) or '—'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", action="store_true", help="wire the real model planner")
    parser.add_argument("--mode", choices=MODES, action="append", help="restrict modes")
    parser.add_argument("--out", default=None, help="output directory")
    args = parser.parse_args()

    modes = tuple(args.mode) if args.mode else MODES
    planner = build_provider_planner() if args.provider else None
    records: list[dict[str, Any]] = []
    for mode in modes:
        for case in CORPUS:
            record = asyncio.run(run_case(case, mode, planner))
            records.append(record)
            status = "ok" if record.get("fullIntentCoverage") in (True, None) else "MISSED"
            print(f"[{mode:8s}] {record['id']:6s} {status:6s} missed={record['missedAsks']}")

    summary = summarize(records)
    out_dir = Path(args.out) if args.out else Path(__file__).parent
    suffix = "provider" if args.provider else "deterministic"
    (out_dir / f"results-{suffix}.json").write_text(
        json.dumps({"summary": summary, "records": records}, indent=2) + "\n"
    )
    (out_dir / f"RESULTS-{suffix}.md").write_text(render_markdown(records, summary))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
