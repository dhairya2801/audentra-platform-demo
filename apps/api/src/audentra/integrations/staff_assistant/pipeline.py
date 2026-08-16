"""The staff assistant pipeline: gate → resolve → plan → read → derive →
compose → guard.

Deterministic-first orchestration in the student pipeline's mold, with one
staff-specific stage: **referent resolution**. Staff questions are about
arbitrary students, so before any student-scoped read runs the pipeline
resolves *which* student — from an explicit name (via the roster search), a
pasted id, or the conversation's durable active referent — and validates the
result against the authenticated tenant. The model never chooses identity:
it can propose tool names and non-identity filters, and everything else is
bound server-side after validation.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from audentra.integrations.assistant.blocks import text_block
from audentra.integrations.assistant.trace import AssistantTurnTrace
from audentra.integrations.staff_assistant.classify import (
    STUDENT_REQUIRED_REQUEST_TYPES,
    StaffClassification,
    classify_staff_request,
)
from audentra.integrations.staff_assistant.compose import (
    ComposedStaffAnswer,
    compose_staff_deterministic,
)
from audentra.integrations.staff_assistant.derive import (
    StaffDerivedState,
    derive_staff_state,
)
from audentra.integrations.staff_assistant.guard import guard_staff_grounded_answer
from audentra.integrations.staff_assistant.normalize import (
    NormalizedStaffRequest,
    normalize_staff_request,
)
from audentra.integrations.staff_assistant.planner import (
    resolve_dependency_tools,
    select_staff_tools,
    validate_staff_model_plan,
)
from audentra.integrations.staff_assistant.tools import (
    DEFAULT_STAFF_TOOL_TIMEOUT_SECONDS,
    PlannedToolCall,
    StaffAssistantToolHost,
    StaffToolExecution,
    execute_staff_tool_reads,
)

JsonDict = dict[str, Any]

ModelComposer = Callable[..., Awaitable[Mapping[str, Any] | None]]
ModelPlanner = Callable[..., Awaitable[Mapping[str, Any] | None]]

# Intents whose deterministic answer is the deliverable: refusals stay
# canned, and a draft is reviewed text — a model rewrite of either could only
# soften a boundary or drift a fact.
_SKIP_REWRITE = frozenset(
    {
        "greeting",
        "capability_overview",
        "action_request",
        "unsupported_metric",
        "unsupported_or_out_of_scope",
        "draft_email",
        "draft_sms",
        "draft_call_points",
    }
)


@dataclass
class StaffAssistantPipelineResult:
    message: str
    blocks: list[JsonDict]
    provider: str
    model: str | None
    usage: JsonDict | None
    context_receipts: list[JsonDict]
    classification: StaffClassification | None = None
    derived: StaffDerivedState | None = None
    failure_codes: list[str] = field(default_factory=list)
    resolved_student_id: str | None = None
    resolved_student_name: str | None = None


class StaffAssistantPipeline:
    def __init__(
        self,
        host: StaffAssistantToolHost,
        *,
        model_composer: ModelComposer | None = None,
        model_planner: ModelPlanner | None = None,
        tool_timeout_seconds: float = DEFAULT_STAFF_TOOL_TIMEOUT_SECONDS,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._host = host
        self._model_composer = model_composer
        self._model_planner = model_planner
        self._tool_timeout_seconds = tool_timeout_seconds
        self._now = now or (lambda: datetime.now(UTC))

    async def execute(
        self,
        *,
        message: str,
        history: Sequence[Mapping[str, str]] = (),
        context_student_id: str | None = None,
        trace: AssistantTurnTrace | None = None,
    ) -> StaffAssistantPipelineResult:
        failure_codes: list[str] = []
        stage_started = time.perf_counter()
        request = normalize_staff_request(message, history=history)
        if trace is not None:
            trace.user_message = request.text
            trace.history_messages = len(request.history)
            trace.add_stage(
                "normalize",
                (time.perf_counter() - stage_started) * 1_000,
                isFollowUp=request.is_follow_up or None,
                candidateStudentName=request.candidate_student_name,
                workItemKey=request.work_item_key,
                actionKind=request.action_kind,
            )

        stage_started = time.perf_counter()
        classification = classify_staff_request(request)
        tool_selection_source = "deterministic" if classification is not None else None

        # --- Referent resolution -------------------------------------------
        execution = StaffToolExecution()
        resolution = await self._resolve_student_referent(
            request, classification, context_student_id, execution, trace
        )
        if resolution.short_circuit is not None:
            state = derive_staff_state(execution)
            state.search_results = resolution.search_results
            answer = resolution.short_circuit
            if trace is not None:
                trace.classification = _classification_dict(classification)
                trace.evidence = list(answer.evidence_texts)
                trace.response_source = "deterministic"
                trace.failure_codes = list(failure_codes)
                trace.final_message = answer.message
            return StaffAssistantPipelineResult(
                message=answer.message,
                blocks=answer.blocks,
                provider="guided",
                model=None,
                usage=None,
                context_receipts=_receipt_sources(execution.receipts),
                classification=classification,
                derived=state,
                failure_codes=failure_codes,
            )
        student_resolved = resolution.student_id is not None

        # --- Planning -------------------------------------------------------
        planned_calls: list[PlannedToolCall] | None = None
        if classification is None and self._model_planner is not None:
            planner_started = time.perf_counter()
            planner_outcome = "invalid_plan"
            try:
                candidate = await self._model_planner(
                    message=request.resolved_text,
                    student_resolved=student_resolved,
                )
            except Exception:
                candidate = None
                planner_outcome = "model_error"
                failure_codes.append("planner_model_failure")
            validated = (
                validate_staff_model_plan(candidate, student_resolved=student_resolved)
                if candidate is not None
                else None
            )
            if validated is not None:
                classification, planned_calls = validated
                tool_selection_source = "model_plan"
                planner_outcome = "accepted"
            if trace is not None:
                planner_usage = candidate.get("usage") if isinstance(candidate, Mapping) else None
                trace.add_model_call(
                    operation="staff_assistant_planner",
                    attempt=1,
                    duration_ms=(time.perf_counter() - planner_started) * 1_000,
                    outcome=planner_outcome,
                    provider=(
                        str(candidate.get("provider"))
                        if isinstance(candidate, Mapping) and candidate.get("provider")
                        else None
                    ),
                    model=(
                        candidate.get("model")
                        if isinstance(candidate, Mapping)
                        and isinstance(candidate.get("model"), str)
                        else None
                    ),
                    usage=planner_usage if isinstance(planner_usage, Mapping) else None,
                )
        if classification is None:
            classification = StaffClassification("general_question", 0.5, source="safe_fallback")
            tool_selection_source = tool_selection_source or "safe_fallback"
            failure_codes.append("classification_fallback")

        # An intent that needs a student but has none short-circuits into an
        # honest ask instead of a guessy tenant-wide read.
        if classification.request_type in STUDENT_REQUIRED_REQUEST_TYPES and not student_resolved:
            answer_text = (
                "Tell me which student you mean — a full name works best, and "
                "I'll pull their record."
            )
            if trace is not None:
                trace.classification = _classification_dict(classification)
                trace.tool_selection_source = tool_selection_source
                trace.response_source = "deterministic"
                trace.failure_codes = list(failure_codes)
                trace.final_message = answer_text
            return StaffAssistantPipelineResult(
                message=answer_text,
                blocks=[text_block(answer_text)],
                provider="guided",
                model=None,
                usage=None,
                context_receipts=_receipt_sources(execution.receipts),
                classification=classification,
                derived=derive_staff_state(execution),
                failure_codes=failure_codes,
            )

        if planned_calls is None:
            planned_calls = [
                PlannedToolCall(tool=tool)
                for tool in select_staff_tools(classification, student_resolved=student_resolved)
            ]
        planned_calls = await self._bind_identity_arguments(
            planned_calls, request, resolution, execution, trace
        )
        if trace is not None:
            trace.classification = _classification_dict(classification)
            trace.tool_selection_source = tool_selection_source or classification.source
            trace.selected_tools = [call.tool for call in planned_calls]
            trace.add_stage("classify_and_plan", (time.perf_counter() - stage_started) * 1_000)

        # --- Reads ------------------------------------------------------------
        stage_started = time.perf_counter()
        already_read = set(execution.executed_tools)
        first_round = await execute_staff_tool_reads(
            [call for call in planned_calls if call.tool not in already_read],
            self._host,
            timeout_seconds=self._tool_timeout_seconds,
            now=self._now(),
            receipt_offset=len(execution.receipts),
        )
        _merge_execution(execution, first_round)
        if trace is not None:
            _trace_round(trace, first_round, "initial")
            trace.add_stage("execute_tool_reads", (time.perf_counter() - stage_started) * 1_000)

        stage_started = time.perf_counter()
        state = derive_staff_state(execution)
        state.search_results = state.search_results or resolution.search_results
        if trace is not None:
            trace.add_stage("derive_staff_state", (time.perf_counter() - stage_started) * 1_000)

        # --- Bounded dependency round ----------------------------------------
        if resolution.student_id is not None:
            open_codes = [str(blocker.get("code") or "") for blocker in state.blockers]
            dependency_tools, dependency_reasons = resolve_dependency_tools(
                execution.executed_tools, open_codes
            )
            if dependency_tools:
                stage_started = time.perf_counter()
                second = await execute_staff_tool_reads(
                    [
                        PlannedToolCall(tool=tool, arguments={"studentId": resolution.student_id})
                        for tool in dependency_tools
                    ],
                    self._host,
                    timeout_seconds=self._tool_timeout_seconds,
                    now=self._now(),
                    receipt_offset=len(execution.receipts),
                )
                _merge_execution(execution, second)
                state = derive_staff_state(execution)
                state.search_results = state.search_results or resolution.search_results
                if trace is not None:
                    _trace_round(trace, second, "dependency")
                    trace.second_read = {
                        "triggeredBy": dependency_reasons,
                        "tools": list(second.executed_tools),
                    }
                    trace.add_stage(
                        "dependency_reads",
                        (time.perf_counter() - stage_started) * 1_000,
                        tools=list(second.executed_tools),
                    )

        # --- Compose + optional rewrite ---------------------------------------
        stage_started = time.perf_counter()
        draft = compose_staff_deterministic(classification, state)
        if trace is not None:
            trace.add_stage(
                "compose_deterministic",
                (time.perf_counter() - stage_started) * 1_000,
                evidenceFacts=len(draft.evidence_texts),
            )
            trace.evidence = list(draft.evidence_texts)

        stage_started = time.perf_counter()
        message_text, blocks, provider, model, usage = await self._maybe_rewrite(
            classification, request.resolved_text, draft, failure_codes, trace=trace
        )
        if trace is not None:
            trace.add_stage("model_rewrite", (time.perf_counter() - stage_started) * 1_000)
            trace.provider = provider
            trace.model = model
            trace.usage = usage
            trace.response_source = "model_prose" if provider != "guided" else "deterministic"
            trace.failure_codes = list(failure_codes)
            trace.final_message = message_text
        return StaffAssistantPipelineResult(
            message=message_text,
            blocks=blocks,
            provider=provider,
            model=model,
            usage=usage,
            context_receipts=_receipt_sources(execution.receipts),
            classification=classification,
            derived=state,
            failure_codes=failure_codes,
            resolved_student_id=resolution.student_id,
            resolved_student_name=resolution.student_name,
        )

    # ------------------------------------------------------------------
    # Referent resolution
    # ------------------------------------------------------------------

    @dataclass
    class _Resolution:
        student_id: str | None = None
        student_name: str | None = None
        search_results: list[JsonDict] = field(default_factory=list)
        short_circuit: ComposedStaffAnswer | None = None

    async def _resolve_student_referent(
        self,
        request: NormalizedStaffRequest,
        classification: StaffClassification | None,
        context_student_id: str | None,
        execution: StaffToolExecution,
        trace: AssistantTurnTrace | None,
    ) -> StaffAssistantPipeline._Resolution:
        resolution = StaffAssistantPipeline._Resolution()
        needs_student = classification is None or (
            classification.request_type in STUDENT_REQUIRED_REQUEST_TYPES
        )
        explicit_name = request.candidate_student_name
        explicit_id = request.candidate_student_id
        if not (needs_student or explicit_name or explicit_id):
            return resolution

        async def run_referent_read(call: PlannedToolCall) -> Mapping[str, Any] | None:
            round_result = await execute_staff_tool_reads(
                [call],
                self._host,
                timeout_seconds=self._tool_timeout_seconds,
                now=self._now(),
                receipt_offset=len(execution.receipts),
            )
            _merge_execution(execution, round_result)
            if trace is not None:
                _trace_round(trace, round_result, "referent")
            read = round_result.reads.get(call.tool, {})
            data = read.get("data")
            return data if isinstance(data, Mapping) else None

        if explicit_id is not None:
            overview = await run_referent_read(
                PlannedToolCall(tool="getStudentStaffSummary", arguments={"studentId": explicit_id})
            )
            if overview is None or not overview.get("id"):
                resolution.short_circuit = _not_found_answer()
                return resolution
            resolution.student_id = str(overview["id"])
            resolution.student_name = str(
                overview.get("preferredName") or overview.get("name") or ""
            )
            return resolution

        if explicit_name is not None:
            search = await run_referent_read(
                PlannedToolCall(
                    tool="searchStudents",
                    arguments={"query": explicit_name, "limit": 8},
                )
            )
            items = (
                [dict(item) for item in search.get("items", []) if isinstance(item, dict)]
                if search
                else []
            )
            resolution.search_results = items
            if len(items) == 1:
                resolution.student_id = str(items[0].get("id"))
                resolution.student_name = str(
                    items[0].get("preferredName") or items[0].get("name") or ""
                )
                return resolution
            if not items:
                resolution.short_circuit = _not_found_answer(explicit_name)
            else:
                resolution.short_circuit = _disambiguation_answer(explicit_name, items)
            return resolution

        if context_student_id is not None:
            overview = await run_referent_read(
                PlannedToolCall(
                    tool="getStudentStaffSummary",
                    arguments={"studentId": context_student_id},
                )
            )
            if overview is not None and overview.get("id"):
                resolution.student_id = str(overview["id"])
                resolution.student_name = str(
                    overview.get("preferredName") or overview.get("name") or ""
                )
        return resolution

    async def _bind_identity_arguments(
        self,
        planned_calls: list[PlannedToolCall],
        request: NormalizedStaffRequest,
        resolution: StaffAssistantPipeline._Resolution,
        execution: StaffToolExecution,
        trace: AssistantTurnTrace | None,
    ) -> list[PlannedToolCall]:
        """Bind server-side identity arguments; drop calls that cannot bind."""

        from audentra.integrations.staff_assistant.catalog import STUDENT_SCOPED_TOOLS

        bound: list[PlannedToolCall] = []
        for call in planned_calls:
            arguments = dict(call.arguments)
            arguments.pop("studentId", None)
            arguments.pop("workItemId", None)
            arguments.pop("inquiryId", None)
            if call.tool in STUDENT_SCOPED_TOOLS:
                if resolution.student_id is None:
                    continue
                arguments["studentId"] = resolution.student_id
            if call.tool == "searchStudents" and not arguments.get("query"):
                if request.candidate_student_name:
                    arguments["query"] = request.candidate_student_name
                else:
                    continue
            if call.tool == "getWorkItemDetail":
                work_item_id = await self._resolve_work_item_id(request, execution, trace)
                if work_item_id is None:
                    continue
                arguments["workItemId"] = work_item_id
            if call.tool == "getInquiryThread":
                if request.candidate_student_id is None:
                    continue
                arguments["inquiryId"] = request.candidate_student_id
            bound.append(PlannedToolCall(tool=call.tool, arguments=arguments))
        return bound

    async def _resolve_work_item_id(
        self,
        request: NormalizedStaffRequest,
        execution: StaffToolExecution,
        trace: AssistantTurnTrace | None,
    ) -> str | None:
        """Resolve a human key like ENR-104 to its id via the pure queue read."""

        if request.candidate_student_id is not None and request.work_item_key is None:
            # A bare UUID in a work-item question is the id itself.
            return request.candidate_student_id
        if request.work_item_key is None:
            return None
        round_result = await execute_staff_tool_reads(
            [PlannedToolCall(tool="getStaffWorkQueue")],
            self._host,
            timeout_seconds=self._tool_timeout_seconds,
            now=self._now(),
            receipt_offset=len(execution.receipts),
        )
        _merge_execution(execution, round_result)
        if trace is not None:
            _trace_round(trace, round_result, "referent")
        read = round_result.reads.get("getStaffWorkQueue", {})
        data = read.get("data")
        if not isinstance(data, Mapping):
            return None
        for item in data.get("items", []):
            if isinstance(item, Mapping) and str(item.get("key")) == request.work_item_key:
                return str(item.get("id"))
        return None

    # ------------------------------------------------------------------
    # Optional model rewrite, re-checked by the staff claim guard
    # ------------------------------------------------------------------

    async def _maybe_rewrite(
        self,
        classification: StaffClassification,
        question: str,
        draft: ComposedStaffAnswer,
        failure_codes: list[str],
        trace: AssistantTurnTrace | None = None,
    ) -> tuple[str, list[JsonDict], str, str | None, JsonDict | None]:
        deterministic = (draft.message, draft.blocks, "guided", None, None)
        if (
            self._model_composer is None
            or classification.request_type in _SKIP_REWRITE
            or not draft.evidence_texts
        ):
            return deterministic
        attempt_started = time.perf_counter()
        try:
            result = await self._model_composer(
                question=question,
                evidence_texts=draft.evidence_texts,
                draft_answer=draft.message,
            )
        except Exception:
            failure_codes.append("composition_model_failure")
            if trace is not None:
                trace.add_model_call(
                    operation="staff_assistant_composer",
                    attempt=1,
                    duration_ms=(time.perf_counter() - attempt_started) * 1_000,
                    outcome="model_error",
                )
            return deterministic
        if not isinstance(result, Mapping) or not str(result.get("answer", "")).strip():
            failure_codes.append("composition_invalid_model_result")
            if trace is not None:
                trace.add_model_call(
                    operation="staff_assistant_composer",
                    attempt=1,
                    duration_ms=(time.perf_counter() - attempt_started) * 1_000,
                    outcome="invalid_model_result",
                )
            return deterministic
        verdict = guard_staff_grounded_answer(
            answer=str(result["answer"]),
            evidence_texts=draft.evidence_texts,
        )
        if trace is not None:
            usage = result.get("usage")
            trace.add_model_call(
                operation="staff_assistant_composer",
                attempt=1,
                duration_ms=(time.perf_counter() - attempt_started) * 1_000,
                outcome="accepted" if verdict.accepted else "guard_rejected",
                provider=str(result.get("provider")) if result.get("provider") else None,
                model=result.get("model") if isinstance(result.get("model"), str) else None,
                usage=usage if isinstance(usage, Mapping) else None,
                detail=None if verdict.accepted else verdict.reason_code,
            )
        if verdict.accepted:
            answer_text = verdict.answer
            # Provenance is structural, not stylistic: a recommendation must
            # carry its "my suggestion, not policy" label even when the model
            # rewrite dropped it.
            if (
                classification.request_type == "recommendation"
                and "not institutional policy" not in answer_text.lower()
            ):
                answer_text = (
                    f"{answer_text} (This is my recommendation from the record, "
                    "not institutional policy.)"
                )
            blocks = [
                {"type": "text", "fallbackText": answer_text, "text": answer_text},
                *[block for block in draft.blocks if block.get("type") != "text"],
            ]
            usage = result.get("usage")
            return (
                answer_text,
                blocks,
                str(result.get("provider") or "openrouter"),
                result.get("model") if isinstance(result.get("model"), str) else None,
                dict(usage) if isinstance(usage, Mapping) else None,
            )
        failure_codes.append(f"written_answer_rejected:{verdict.reason_code}")
        return deterministic


def _classification_dict(
    classification: StaffClassification | None,
) -> JsonDict | None:
    if classification is None:
        return None
    return {
        "requestType": classification.request_type,
        "confidence": classification.confidence,
        "source": classification.source,
        "reference": classification.reference,
        "additionalRequestTypes": list(classification.additional_request_types),
    }


def _not_found_answer(name: str | None = None) -> ComposedStaffAnswer:
    who = f" matching “{name}”" if name else ""
    message = (
        f"I couldn't find a student{who} in your institution. A full name "
        "usually works best; I can also search by program."
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message)])


def _disambiguation_answer(name: str, items: list[JsonDict]) -> ComposedStaffAnswer:
    from audentra.integrations.assistant.blocks import bullet_list_block

    message = f"I found {len(items)} students matching “{name}” — which one do you mean?"
    block = bullet_list_block(
        [
            {
                "text": f"{item.get('name')} — {item.get('programName')}, class of "
                f"{item.get('classYear')}"
            }
            for item in items[:8]
        ],
        title="Matches",
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message), block])


def _merge_execution(target: StaffToolExecution, source: StaffToolExecution) -> None:
    target.reads.update(source.reads)
    target.receipts.extend(source.receipts)
    target.executed_tools.extend(source.executed_tools)
    target.unavailable_data.extend(source.unavailable_data)
    target.rejected_arguments.extend(source.rejected_arguments)


def _trace_round(trace: AssistantTurnTrace, execution: StaffToolExecution, round_name: str) -> None:
    for tool in execution.executed_tools:
        read = execution.reads.get(tool, {})
        receipt = read.get("receipt", {})
        trace.add_tool_call(
            tool=tool,
            status=str(read.get("status", "unknown")),
            duration_ms=read.get("durationMs"),
            record_count=receipt.get("recordCount"),
            reason=read.get("reason"),
            result=read.get("data"),
            round_name=round_name,
            arguments=receipt.get("arguments"),
            validation="accepted",
        )
    for rejected in execution.rejected_arguments:
        trace.add_tool_call(
            tool=str(rejected.get("tool")),
            status="rejected",
            duration_ms=0,
            reason=str(rejected.get("code")),
            round_name=round_name,
            arguments=None,
            validation=str(rejected.get("detail")),
        )


def _receipt_sources(receipts: Sequence[Mapping[str, Any]]) -> list[JsonDict]:
    seen: set[str] = set()
    unique: list[JsonDict] = []
    for receipt in receipts:
        source = str(receipt.get("source"))
        if source not in seen:
            seen.add(source)
            unique.append({"source": source})
    return unique
