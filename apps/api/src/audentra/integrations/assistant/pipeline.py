"""The assistant pipeline: gate → plan → read → derive → compose → guard.

Deterministic-first orchestration ported from student-assistant-core
`graph.ts`. Safety and conversational gates settle before any model is asked
anything — a greeting that reaches a planner comes back as a checklist
question, because a planner's whole job is to find a record to read. The
model's only roles are optional: proposing a tool plan and rewriting the
deterministic answer as better prose, and both outputs are validated against
deterministic rules before a student sees them.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from audentra.integrations.assistant.blocks import (
    describe_blocks_for_prompt,
    render_blocks_as_text,
)
from audentra.integrations.assistant.classify import Classification, classify
from audentra.integrations.assistant.compose import ComposedAnswer, compose_deterministic
from audentra.integrations.assistant.coverage import (
    assess_request_coverage,
    augment_classification,
    plan_covers_domains,
    resolve_coverage_gate_mode,
)
from audentra.integrations.assistant.derive import DerivedState, derive_student_state
from audentra.integrations.assistant.guard import build_causal_guards, guard_grounded_answer
from audentra.integrations.assistant.planner import (
    REQUIREMENT_GATE_CODES,
    resolve_dependency_reads,
    select_tool_reads,
    validate_model_tool_plan,
)
from audentra.integrations.assistant.tools import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    AssistantToolHost,
    execute_tool_reads,
)
from audentra.integrations.assistant.trace import AssistantTurnTrace

JsonDict = dict[str, Any]

ModelComposer = Callable[..., Awaitable[Mapping[str, Any] | None]]
ModelPlanner = Callable[..., Awaitable[Mapping[str, Any] | None]]


@dataclass
class AssistantPipelineResult:
    message: str
    blocks: list[JsonDict]
    provider: str
    model: str | None
    usage: JsonDict | None
    suggested_actions: list[JsonDict]
    context_receipts: list[JsonDict]
    classification: Classification | None = None
    derived: DerivedState | None = None
    failure_codes: list[str] = field(default_factory=list)


class AssistantPipeline:
    def __init__(
        self,
        host: AssistantToolHost,
        *,
        model_composer: ModelComposer | None = None,
        model_planner: ModelPlanner | None = None,
        tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        now: Callable[[], datetime] | None = None,
        coverage_gate: str | None = None,
    ) -> None:
        self._host = host
        self._model_composer = model_composer
        self._model_planner = model_planner
        self._tool_timeout_seconds = tool_timeout_seconds
        self._now = now or (lambda: datetime.now(UTC))
        # EXPERIMENTAL, off by default: the full-request coverage gate. None
        # defers to the AUDENTRA_EXPERIMENTAL_COVERAGE_GATE env flag so the
        # eval host can A/B it; production wiring passes nothing and sets
        # nothing, so production routing is unchanged.
        self._coverage_gate = resolve_coverage_gate_mode(coverage_gate)

    async def execute(
        self,
        *,
        message: str,
        history: Sequence[Mapping[str, str]] = (),
        page_path: str | None = None,
        page_label: str | None = None,
        trace: AssistantTurnTrace | None = None,
    ) -> AssistantPipelineResult:
        from audentra.integrations.assistant.normalize import normalize_request

        failure_codes: list[str] = []
        stage_started = time.perf_counter()
        request = normalize_request(
            message, history=history, page_path=page_path, page_label=page_label
        )
        if trace is not None:
            trace.user_message = request.text
            trace.page_path = request.page_path
            trace.page_label = request.page_label
            trace.history_messages = len(request.history)
            trace.add_stage(
                "normalize",
                (time.perf_counter() - stage_started) * 1_000,
                isFollowUp=request.is_follow_up or None,
                isMutationRequest=request.is_mutation_request or None,
                containsSensitiveFinancialData=request.contains_sensitive_financial_data or None,
            )

        # Safety and conversational gates settle ahead of every model call.
        stage_started = time.perf_counter()
        classification = classify(request)
        tool_selection_source = "deterministic" if classification is not None else None
        planned_tools: list[str] | None = None
        # EXPERIMENTAL coverage gate (off in production): a confident
        # deterministic classification may still cover only part of a
        # compound question. When the request asks about domains the selected
        # reads will not answer, widen the route — deterministically, or via
        # the model planner when that mode is on and a planner exists.
        if classification is not None and self._coverage_gate != "off":
            gate_started = time.perf_counter()
            assessment = assess_request_coverage(request, classification)
            gate_action: str | None = "fully_covered"
            if assessment.uncovered_domains:
                gate_action = None
                if (
                    self._coverage_gate == "planner"
                    and self._model_planner is not None
                    and not request.is_mutation_request
                ):
                    validated = await self._invoke_model_planner(
                        request, failure_codes, trace, detail="coverage_gate"
                    )
                    if validated is not None and plan_covers_domains(
                        validated[1], assessment.uncovered_domains
                    ):
                        classification, planned_tools = validated
                        tool_selection_source = "model_plan"
                        gate_action = "planner_plan_accepted"
                if gate_action is None:
                    classification = augment_classification(classification, assessment)
                    tool_selection_source = "coverage_gate"
                    gate_action = "augmented"
            if trace is not None:
                trace.add_stage(
                    "coverage_gate",
                    (time.perf_counter() - gate_started) * 1_000,
                    action=gate_action,
                    askDomains=list(assessment.ask_domains) or None,
                    uncoveredDomains=list(assessment.uncovered_domains) or None,
                    supplements=list(assessment.supplements) or None,
                    droppedDomains=list(assessment.dropped_domains) or None,
                )
        if (
            classification is None
            and self._model_planner is not None
            and not request.is_mutation_request
        ):
            validated = await self._invoke_model_planner(request, failure_codes, trace)
            if validated is not None:
                classification, planned_tools = validated
                tool_selection_source = "model_plan"
        if classification is None:
            classification = Classification("general_question", 0.5, source="safe_fallback")
            tool_selection_source = tool_selection_source or "safe_fallback"
            failure_codes.append("classification_fallback")
            # EXPERIMENTAL: the safe fallback's broad checklist read is the
            # weakest route of all for a compound question — with the gate on,
            # widen it with the domains the request actually named ("show my
            # housing and aid status" gets its housing and aid reads even with
            # no model planner available).
            if self._coverage_gate != "off":
                gate_started = time.perf_counter()
                assessment = assess_request_coverage(
                    request, classification, allow_exempt_primary=True
                )
                if assessment.supplements:
                    classification = augment_classification(classification, assessment)
                    tool_selection_source = "coverage_gate"
                if trace is not None:
                    trace.add_stage(
                        "coverage_gate",
                        (time.perf_counter() - gate_started) * 1_000,
                        action="fallback_augmented" if assessment.supplements else "fallback_bare",
                        askDomains=list(assessment.ask_domains) or None,
                        uncoveredDomains=list(assessment.uncovered_domains) or None,
                        supplements=list(assessment.supplements) or None,
                        droppedDomains=list(assessment.dropped_domains) or None,
                    )

        selected = planned_tools if planned_tools is not None else select_tool_reads(classification)
        if trace is not None:
            trace.classification = {
                "requestType": classification.request_type,
                "confidence": classification.confidence,
                "source": classification.source,
                "additionalRequestTypes": list(classification.additional_request_types),
                "requirementReference": classification.requirement_reference,
            }
            trace.tool_selection_source = tool_selection_source or classification.source
            trace.selected_tools = list(selected)
            trace.add_stage("classify_and_plan", (time.perf_counter() - stage_started) * 1_000)

        stage_started = time.perf_counter()
        execution = await execute_tool_reads(
            selected,
            self._host,
            timeout_seconds=self._tool_timeout_seconds,
            now=self._now(),
        )
        if trace is not None:
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
                )
            trace.add_stage("execute_tool_reads", (time.perf_counter() - stage_started) * 1_000)

        stage_started = time.perf_counter()
        state = derive_student_state(execution)
        if trace is not None:
            trace.add_stage("derive_student_state", (time.perf_counter() - stage_started) * 1_000)

        # Bounded dependency round: when derivation surfaces an open gate whose
        # owning domain was not read, fetch the verifying read so the answer
        # can *explain* the gate instead of merely naming it. Exactly one
        # extra round, deterministically planned, capped at
        # MAX_DEPENDENCY_TOOLS — never a second model-planned expansion.
        dependency_tools, dependency_reasons = resolve_dependency_reads(
            execution.executed_tools, _open_gate_codes(state)
        )
        if dependency_tools:
            stage_started = time.perf_counter()
            second = await execute_tool_reads(
                dependency_tools,
                self._host,
                timeout_seconds=self._tool_timeout_seconds,
                now=self._now(),
                receipt_offset=len(execution.receipts),
            )
            execution.reads.update(second.reads)
            execution.receipts.extend(second.receipts)
            execution.executed_tools.extend(second.executed_tools)
            execution.unavailable_data.extend(second.unavailable_data)
            state = derive_student_state(execution)
            if trace is not None:
                for tool in second.executed_tools:
                    read = second.reads.get(tool, {})
                    receipt = read.get("receipt", {})
                    trace.add_tool_call(
                        tool=tool,
                        status=str(read.get("status", "unknown")),
                        duration_ms=read.get("durationMs"),
                        record_count=receipt.get("recordCount"),
                        reason=read.get("reason"),
                        result=read.get("data"),
                        round_name="dependency",
                    )
                trace.second_read = {
                    "triggeredBy": dependency_reasons,
                    "tools": list(second.executed_tools),
                }
                trace.add_stage(
                    "dependency_reads",
                    (time.perf_counter() - stage_started) * 1_000,
                    tools=list(second.executed_tools),
                )

        preferred_name = None
        if state.profile is not None:
            raw = state.profile.get("preferredName")
            preferred_name = str(raw) if raw else None
        stage_started = time.perf_counter()
        draft = compose_deterministic(classification, state, preferred_name=preferred_name)
        if trace is not None:
            trace.add_stage(
                "compose_deterministic",
                (time.perf_counter() - stage_started) * 1_000,
                evidenceFacts=len(draft.evidence_texts),
            )
            trace.evidence = list(draft.evidence_texts)

        stage_started = time.perf_counter()
        message_text, blocks, provider, model, usage = await self._maybe_rewrite(
            classification, request.resolved_text, state, draft, failure_codes, trace=trace
        )
        if trace is not None:
            trace.add_stage("model_rewrite", (time.perf_counter() - stage_started) * 1_000)
            trace.provider = provider
            trace.model = model
            trace.usage = usage
            trace.response_source = "model_prose" if provider != "guided" else "deterministic"
            trace.failure_codes = list(failure_codes)
            trace.final_message = message_text
        return AssistantPipelineResult(
            message=message_text,
            blocks=blocks,
            provider=provider,
            model=model,
            usage=usage,
            suggested_actions=list(state.suggested_actions),
            context_receipts=[
                {"source": receipt["source"]} for receipt in _unique_sources(execution.receipts)
            ],
            classification=classification,
            derived=state,
            failure_codes=failure_codes,
        )

    async def _invoke_model_planner(
        self,
        request: Any,
        failure_codes: list[str],
        trace: AssistantTurnTrace | None,
        detail: str | None = None,
    ) -> tuple[Classification, list[str]] | None:
        """One traced planner call, validated; shared by the classification
        fallback and the experimental coverage gate so both record identically."""

        planner_started = time.perf_counter()
        planner_outcome = "invalid_plan"
        try:
            candidate = await self._model_planner(  # type: ignore[misc]
                message=request.resolved_text,
                page_label=request.page_label,
                page_path=request.page_path,
            )
        except Exception:
            candidate = None
            planner_outcome = "model_error"
            failure_codes.append("planner_model_failure")
        validated = validate_model_tool_plan(candidate) if candidate is not None else None
        # A planner may misread a bare follow-up ("Why?") as out of scope;
        # with in-scope conversation behind it, the safe fallback's broad
        # reads answer better than a refusal ever can.
        if (
            validated is not None
            and validated[0].request_type == "unsupported_or_out_of_scope"
            and request.is_follow_up
        ):
            validated = None
            planner_outcome = "unsupported_on_follow_up"
        if validated is not None:
            planner_outcome = "accepted"
        if trace is not None:
            planner_usage = candidate.get("usage") if isinstance(candidate, Mapping) else None
            planner_model = candidate.get("model") if isinstance(candidate, Mapping) else None
            planner_provider = candidate.get("provider") if isinstance(candidate, Mapping) else None
            trace.add_model_call(
                operation="assistant_planner",
                attempt=1,
                duration_ms=(time.perf_counter() - planner_started) * 1_000,
                outcome=planner_outcome,
                provider=str(planner_provider) if planner_provider else None,
                model=planner_model if isinstance(planner_model, str) else None,
                usage=planner_usage if isinstance(planner_usage, Mapping) else None,
                detail=detail,
            )
        return validated

    async def _maybe_rewrite(
        self,
        classification: Classification,
        question: str,
        state: DerivedState,
        draft: ComposedAnswer,
        failure_codes: list[str],
        trace: AssistantTurnTrace | None = None,
    ) -> tuple[str, list[JsonDict], str, str | None, JsonDict | None]:
        """Model prose is optional; the deterministic draft is the floor.

        The written answer is re-checked by the claim guard against the same
        evidence the composer used; one retry is allowed on invented
        causation, every other rejection falls back to the deterministic
        message with the reason recorded.
        """

        deterministic = (draft.message, draft.blocks, "guided", None, None)
        skip_rewrite = classification.request_type in {
            "greeting",
            "capability_overview",
            "conversational_ack",
            "assistant_identity",
            "unsupported_or_out_of_scope",
        }
        if self._model_composer is None or skip_rewrite or not draft.evidence_texts:
            return deterministic
        disbursements = (state.financial_aid or {}).get("disbursements")
        causal_guards = build_causal_guards(
            registration_gates=state.registration_gates or None,
            housing_gates=(
                (state.housing_eligibility or {}).get("gates")
                if state.housing_eligibility
                else None
            ),
            disbursement_gates=(disbursements or {}).get("gates") if disbursements else None,
        )
        # The prose the model writes renders above the draft's structured
        # blocks, so the model must know what those blocks already show —
        # otherwise it restates every row a table carries.
        presented_blocks = describe_blocks_for_prompt(draft.blocks)
        attempts = 0
        feedback: str | None = None
        while attempts < 2:
            attempts += 1
            attempt_started = time.perf_counter()

            def record_attempt(
                outcome: str,
                result: Mapping[str, Any] | None = None,
                detail: str | None = None,
                started: float = attempt_started,
                attempt: int = attempts,
            ) -> None:
                if trace is None:
                    return
                usage = result.get("usage") if isinstance(result, Mapping) else None
                trace.add_model_call(
                    operation="assistant_composer",
                    attempt=attempt,
                    duration_ms=(time.perf_counter() - started) * 1_000,
                    outcome=outcome,
                    provider=(str(result.get("provider")) if isinstance(result, Mapping) else None),
                    model=(
                        result.get("model")
                        if isinstance(result, Mapping) and isinstance(result.get("model"), str)
                        else None
                    ),
                    usage=usage if isinstance(usage, Mapping) else None,
                    detail=detail,
                )

            try:
                result = await self._model_composer(
                    question=question,
                    evidence_texts=draft.evidence_texts,
                    draft_answer=draft.message,
                    presented_blocks=presented_blocks or None,
                    feedback=feedback,
                )
            except Exception:
                failure_codes.append("composition_model_failure")
                record_attempt("model_error")
                return deterministic
            if not isinstance(result, Mapping) or not str(result.get("answer", "")).strip():
                failure_codes.append("composition_invalid_model_result")
                record_attempt("invalid_model_result")
                return deterministic
            verdict = guard_grounded_answer(
                answer=str(result["answer"]),
                evidence_texts=draft.evidence_texts,
                causal_guards=causal_guards,
                document_states=state.document_states,
                no_official_holds=(
                    "getEnrollmentHolds" in state.available_reads and not state.official_holds
                ),
                unavailable_sources=[str(item.get("source")) for item in state.unavailable_data],
                deposit_payment_pending=bool((state.account or {}).get("depositPaymentPending")),
            )
            record_attempt(
                "accepted" if verdict.accepted else "guard_rejected",
                result,
                detail=None if verdict.accepted else verdict.reason_code,
            )
            if verdict.accepted:
                blocks = [
                    {"type": "text", "fallbackText": verdict.answer, "text": verdict.answer},
                    *[block for block in draft.blocks if block.get("type") != "text"],
                ]
                usage = result.get("usage")
                return (
                    verdict.answer,
                    blocks,
                    str(result.get("provider") or "openrouter"),
                    result.get("model") if isinstance(result.get("model"), str) else None,
                    dict(usage) if isinstance(usage, Mapping) else None,
                )
            failure_codes.append(f"written_answer_rejected:{verdict.reason_code}")
            # Two rejection families earn the single retry, because a small
            # rewrite usually saves an otherwise-good answer: an invented
            # cause, and an amount/date/contact the evidence doesn't carry
            # (a rejection here falls back to a draft that may not address
            # the question the student actually asked).
            feedback = {
                "invented_causation": (
                    "Your previous answer asserted a cause the record does not "
                    "support. State only causes present in the evidence list."
                ),
                "ungrounded_number": (
                    "Your previous answer contained an amount or number that is "
                    "not in the verified facts. Use only numbers that appear "
                    "there, or leave the number out."
                ),
                "ungrounded_date": (
                    "Your previous answer contained a date that is not in the "
                    "verified facts. Use only dates that appear there, or "
                    "leave the date out."
                ),
                "ungrounded_contact": (
                    "Your previous answer contained contact details that are "
                    "not in the verified facts. Leave them out."
                ),
            }.get(verdict.reason_code or "")
            if feedback is None:
                break
        return deterministic


def _open_gate_codes(state: DerivedState) -> list[str]:
    """Every open gate/blocker code the first derivation surfaced, in order.

    Registration gates first (the most explicit), then housing-eligibility
    gates, then derived enrollment blockers, then aid-disbursement gates —
    the dependency resolver de-duplicates and bounds the resulting reads.
    """

    codes: list[str] = []

    def add(code: object) -> None:
        text = str(code or "")
        if text and text not in codes:
            codes.append(text)

    for gate in state.registration_gates:
        if not gate.get("satisfied"):
            add(gate.get("code"))
    for gate in (state.housing_eligibility or {}).get("gates", []):
        if not gate.get("satisfied"):
            add(gate.get("code"))
    for blocker in state.derived_blockers:
        add(blocker.get("code"))
    disbursements = (state.financial_aid or {}).get("disbursements") or {}
    for gate in disbursements.get("gates", []):
        if not gate.get("satisfied"):
            add(gate.get("code"))
    # A blocked housing step whose gates were never read is an unexplained
    # cause — the eligibility dependency read fetches the actual blockers.
    housing_status = str((state.housing or {}).get("requirementStatus") or "")
    if housing_status == "blocked" and state.housing_eligibility is None:
        add("housing_step_blocked")
    # The open deposit requirement's truth lives in the payments/account read
    # (a pending payment is neither paid nor unpaid); surface its gate even on
    # checklist-only turns so the verifying read runs.
    for step in state.remaining_steps:
        if str(step.get("code") or "").lower() == "enrollment_deposit":
            add(REQUIREMENT_GATE_CODES["enrollment_deposit"])
    return codes


def _unique_sources(receipts: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: set[str] = set()
    unique: list[Mapping[str, Any]] = []
    for receipt in receipts:
        source = str(receipt.get("source"))
        if source not in seen:
            seen.add(source)
            unique.append(receipt)
    return unique


def render_transcript_message(message: str, blocks: Sequence[Mapping[str, Any]]) -> str:
    """The plain-text form persisted for history and voice."""

    rendered = render_blocks_as_text(blocks)
    return rendered if rendered else message
