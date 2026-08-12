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

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from audentra.integrations.assistant.blocks import render_blocks_as_text
from audentra.integrations.assistant.classify import Classification, classify
from audentra.integrations.assistant.compose import ComposedAnswer, compose_deterministic
from audentra.integrations.assistant.derive import DerivedState, derive_student_state
from audentra.integrations.assistant.guard import build_causal_guards, guard_grounded_answer
from audentra.integrations.assistant.planner import select_tool_reads, validate_model_tool_plan
from audentra.integrations.assistant.tools import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    AssistantToolHost,
    execute_tool_reads,
)

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
        page_path: str | None = None,
        page_label: str | None = None,
    ) -> AssistantPipelineResult:
        from audentra.integrations.assistant.normalize import normalize_request

        failure_codes: list[str] = []
        request = normalize_request(
            message, history=history, page_path=page_path, page_label=page_label
        )

        # Safety and conversational gates settle ahead of every model call.
        classification = classify(request)
        planned_tools: list[str] | None = None
        if (
            classification is None
            and self._model_planner is not None
            and not request.is_mutation_request
        ):
            try:
                candidate = await self._model_planner(
                    message=request.resolved_text, page_label=request.page_label
                )
            except Exception:
                candidate = None
                failure_codes.append("planner_model_failure")
            validated = validate_model_tool_plan(candidate) if candidate is not None else None
            if validated is not None:
                classification, planned_tools = validated
        if classification is None:
            classification = Classification("general_question", 0.5, source="safe_fallback")
            failure_codes.append("classification_fallback")

        selected = planned_tools if planned_tools is not None else select_tool_reads(classification)
        execution = await execute_tool_reads(
            selected,
            self._host,
            timeout_seconds=self._tool_timeout_seconds,
            now=self._now(),
        )
        state = derive_student_state(execution)

        preferred_name = None
        if state.profile is not None:
            raw = state.profile.get("preferredName")
            preferred_name = str(raw) if raw else None
        draft = compose_deterministic(classification, state, preferred_name=preferred_name)

        message_text, blocks, provider, model, usage = await self._maybe_rewrite(
            classification, request.resolved_text, state, draft, failure_codes
        )
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

    async def _maybe_rewrite(
        self,
        classification: Classification,
        question: str,
        state: DerivedState,
        draft: ComposedAnswer,
        failure_codes: list[str],
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
            "unsupported_or_out_of_scope",
        }
        if self._model_composer is None or skip_rewrite or not draft.evidence_texts:
            return deterministic
        causal_guards = build_causal_guards(
            registration_gates=state.registration_gates or None,
        )
        attempts = 0
        feedback: str | None = None
        while attempts < 2:
            attempts += 1
            try:
                result = await self._model_composer(
                    question=question,
                    evidence_texts=draft.evidence_texts,
                    draft_answer=draft.message,
                    feedback=feedback,
                )
            except Exception:
                failure_codes.append("composition_model_failure")
                return deterministic
            if not isinstance(result, Mapping) or not str(result.get("answer", "")).strip():
                failure_codes.append("composition_invalid_model_result")
                return deterministic
            verdict = guard_grounded_answer(
                answer=str(result["answer"]),
                evidence_texts=draft.evidence_texts,
                causal_guards=causal_guards,
                document_states=state.document_states,
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
            if verdict.reason_code != "invented_causation":
                break
            feedback = (
                "Your previous answer asserted a cause the record does not "
                "support. State only causes present in the evidence list."
            )
        return deterministic


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
