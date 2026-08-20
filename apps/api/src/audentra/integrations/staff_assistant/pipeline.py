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

import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from audentra.integrations.assistant.blocks import describe_blocks_for_prompt, text_block
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
    extract_candidate_name,
    normalize_staff_request,
)
from audentra.integrations.staff_assistant.planner import (
    resolve_dependency_tools,
    select_staff_tools,
    validate_staff_model_plan,
)
from audentra.integrations.staff_assistant.scope import (
    STUDENT_SCOPE,
    has_explicit_entity,
    may_inherit_referent,
    referent_action,
    refers_back,
    scope_of,
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
        # Membership is a yes/no honesty statement ("X is / is not in the
        # Action Center"); a rewrite could only soften or invert it.
        "student_action_center",
    }
)

# A follow-up that picks one candidate from a just-offered disambiguation
# list ("the one in Civil Engineering", "the second one", "SYN-000123").
_SELECTION_PHRASE = re.compile(
    r"^(?:the\s+)?(?:one|first|second|third|fourth|fifth|sixth|seventh|eighth"
    r"|last|\d(?:st|nd|rd|th))\b|^the one\b|^that one\b",
    re.IGNORECASE,
)
_ORDINAL_WORDS: Mapping[str, int] = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
    "fourth": 3,
    "4th": 3,
    "fifth": 4,
    "5th": 4,
    "sixth": 5,
    "6th": 5,
    "seventh": 6,
    "7th": 6,
    "eighth": 7,
    "8th": 7,
}
_DISAMBIGUATION_MARKER = "which one do you mean"

# Intents whose answers read nothing student-scoped: resolving a mentioned
# name first would only let a lookup outcome preempt the canned answer.
_NO_RESOLUTION_TYPES = frozenset(
    {
        "greeting",
        "capability_overview",
        "action_request",
        "unsupported_metric",
        "unsupported_or_out_of_scope",
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
    # What the durable conversation referent should do after this turn:
    # "set" (this turn resolved a student or put one on the table), "clear"
    # (this turn was explicitly about the population or the attention scan),
    # or "keep".
    referent_action: str = "keep"
    # The student the conversation should carry forward. Usually the resolved
    # referent; for a queue turn it is the head item's student, so
    # "What else is blocking that student?" has something to refer to. It is
    # deliberately NOT reported as the turn's resolved student — the queue
    # answer is about the queue.
    next_referent_student_id: str | None = None


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
        if resolution.treat_as_work_item:
            # The pasted token is a work-item key after all; answer the work
            # item rather than failing a student lookup.
            classification = StaffClassification(
                "work_item_detail",
                0.9,
                source="reference_resolution",
                reference=request.work_item_key,
            )
            tool_selection_source = "deterministic"
        if resolution.prior_classification is not None and (
            classification is None or classification.request_type == "student_overview"
        ):
            # A disambiguation follow-up answers the question that triggered
            # the disambiguation, not a generic overview.
            classification = StaffClassification(
                resolution.prior_classification.request_type,
                resolution.prior_classification.confidence,
                source="follow_up_selection",
                reference=resolution.prior_classification.reference,
            )
            tool_selection_source = "deterministic"
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
                PlannedToolCall(tool=tool, arguments=_cohort_arguments(tool, classification))
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

        # A queue turn puts one case — and so one student — on the table. That
        # student becomes the conversation's current object so a demonstrative
        # follow-up has an antecedent, without the queue answer itself
        # claiming to be about a student.
        queue_referent = _queue_head_student_id(classification, state)

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
        # The follow-up preamble (the previous question and answer) exists so a
        # genuine continuation reads coherently. Attaching it to a turn that
        # *changed scope* is how the previous student got back into the prose
        # of a queue answer — the reads were right, the rewrite was told to
        # "resolve what this refers to from the turn before it". Hand the
        # composer the bare question unless this turn really is about the
        # previous turn's student.
        continues_prior_turn = scope_of(classification.request_type) is STUDENT_SCOPE and (
            resolution.student_id is not None and not has_explicit_entity(request)
        )
        composer_question = request.resolved_text if continues_prior_turn else request.text
        message_text, blocks, provider, model, usage = await self._maybe_rewrite(
            classification, composer_question, draft, failure_codes, trace=trace
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
            referent_action=(
                "set"
                if (resolution.student_id or queue_referent)
                else referent_action(
                    resolved_student_id=None,
                    request_type=classification.request_type,
                )
            ),
            next_referent_student_id=resolution.student_id or queue_referent,
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
        # The pasted reference turned out to be a staff work-item key, not a
        # student — execute() re-routes the turn to work-item detail.
        treat_as_work_item: bool = False
        # Set when a disambiguation follow-up picked a candidate; carries the
        # intent of the question that triggered the disambiguation.
        prior_classification: StaffClassification | None = None

    async def _resolve_student_referent(
        self,
        request: NormalizedStaffRequest,
        classification: StaffClassification | None,
        context_student_id: str | None,
        execution: StaffToolExecution,
        trace: AssistantTurnTrace | None,
    ) -> StaffAssistantPipeline._Resolution:
        resolution = StaffAssistantPipeline._Resolution()
        if classification is not None and classification.request_type in _NO_RESOLUTION_TYPES:
            # A refusal or canned answer reads nothing — resolving a name
            # first would let a lookup failure preempt the refusal itself
            # ("Mark X's transcript as accepted" must refuse, not disambiguate).
            return resolution
        needs_student = classification is None or (
            classification.request_type in STUDENT_REQUIRED_REQUEST_TYPES
        )
        explicit_name = request.candidate_student_name
        explicit_id = request.candidate_student_id
        reference_token = request.reference_token
        classified_work_item = (
            classification is not None and classification.request_type == "work_item_detail"
        )
        # Inheriting the conversation's active student is conditional on the
        # CURRENT turn: it must be student-scoped, name nobody itself, and
        # actually refer back. An unclassified turn (the model-planner path)
        # inherits only when its language refers back — otherwise a global
        # question would silently become a question about the last student.
        may_inherit = (
            may_inherit_referent(
                request, classification.request_type if classification is not None else None
            )
            if classification is not None
            else (refers_back(request) and not has_explicit_entity(request))
        )
        # A pick from a just-offered disambiguation list ("The second one.")
        # is its own resolution path: it names nobody and refers back to a
        # *list*, not to a student, so the inheritance gate must not close on
        # it before the candidate selector runs.
        is_candidate_selection = (
            explicit_id is None
            and request.is_follow_up
            and _looks_like_candidate_selection(request, explicit_name)
        )
        if (
            not (explicit_name or explicit_id or reference_token)
            and not may_inherit
            and not is_candidate_selection
        ):
            return resolution
        if not (
            needs_student
            or explicit_name
            or explicit_id
            or reference_token
            or is_candidate_selection
        ):
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

        def resolve_item(item: Mapping[str, Any]) -> None:
            resolution.student_id = str(item.get("id"))
            resolution.student_name = str(item.get("preferredName") or item.get("name") or "")

        if explicit_id is not None:
            overview = await run_referent_read(
                PlannedToolCall(tool="getStudentStaffSummary", arguments={"studentId": explicit_id})
            )
            if overview is None or not overview.get("id"):
                resolution.short_circuit = _not_found_answer()
                return resolution
            resolve_item(overview)
            return resolution

        # A pasted PREFIX-SUFFIX token: the roster's external reference is
        # checked first (SIS/registrar IDs are how staff cite students); a
        # miss falls back to the work-item key namespace.
        if reference_token is not None and not classified_work_item:
            search = await run_referent_read(
                PlannedToolCall(
                    tool="searchStudents",
                    arguments={"externalRef": reference_token},
                )
            )
            items = _search_items(search)
            if len(items) == 1:
                resolve_item(items[0])
                return resolution
            if explicit_name is None:
                queue = await run_referent_read(PlannedToolCall(tool="getStaffWorkQueue"))
                if queue is not None and any(
                    str(_as_mapping(item).get("key")) == reference_token
                    for item in queue.get("items", [])
                ):
                    resolution.treat_as_work_item = True
                    return resolution
                resolution.short_circuit = _unknown_reference_answer(reference_token)
                return resolution

        # A disambiguation follow-up ("the one in Civil Engineering") picks a
        # candidate deterministically from the re-run canonical search — the
        # model never chooses identity.
        if is_candidate_selection:
            selected = await self._resolve_candidate_selection(request, run_referent_read)
            if selected is not None:
                return selected

        if explicit_name is not None:
            search = await run_referent_read(
                PlannedToolCall(
                    tool="searchStudents",
                    arguments={"query": explicit_name, "limit": 12},
                )
            )
            items = _search_items(search)
            resolution.search_results = items
            match_quality = str((search or {}).get("matchQuality") or "exact")
            if match_quality == "fuzzy":
                # Close spellings are suggestions to confirm, never a silent
                # resolution — the staff member typed something else.
                resolution.short_circuit = _fuzzy_suggestion_answer(explicit_name, items)
                return resolution
            if len(items) == 1:
                resolve_item(items[0])
                return resolution
            if not items:
                resolution.short_circuit = _not_found_answer(explicit_name)
            else:
                resolution.short_circuit = _disambiguation_answer(explicit_name, items)
            return resolution

        if may_inherit and context_student_id is None:
            # A student named in the previous *answer* — "Show me the top
            # transcript case." → "What else is blocking that student?" The
            # queue turn resolved no referent (it is not student-scoped), so
            # the demonstrative has to resolve against what was said. The name
            # still goes through the canonical roster search; the model never
            # supplies identity.
            carried = _name_from_prior_answer(request)
            if carried is not None:
                search = await run_referent_read(
                    PlannedToolCall(tool="searchStudents", arguments={"query": carried, "limit": 4})
                )
                items = _search_items(search)
                if len(items) == 1:
                    resolve_item(items[0])
                    return resolution

        if context_student_id is not None and may_inherit:
            overview = await run_referent_read(
                PlannedToolCall(
                    tool="getStudentStaffSummary",
                    arguments={"studentId": context_student_id},
                )
            )
            if overview is not None and overview.get("id"):
                resolve_item(overview)
        return resolution

    async def _resolve_candidate_selection(
        self,
        request: NormalizedStaffRequest,
        run_referent_read: Callable[[PlannedToolCall], Awaitable[Mapping[str, Any] | None]],
    ) -> StaffAssistantPipeline._Resolution | None:
        """Resolve "the one in Civil Engineering" after a disambiguation.

        Deterministic: the prior turn's name is searched again (same query,
        same canonical ordering the list was presented in), and the reply's
        constraint — ordinal, program, class year — filters the candidates.
        Exactly one survivor resolves; anything else re-asks honestly.
        """

        offered = any(
            item["role"] == "assistant" and _DISAMBIGUATION_MARKER in item["content"].lower()
            for item in request.history
        )
        if not offered:
            return None
        prior_name = next(
            (
                name
                for item in reversed(request.history)
                if item["role"] == "user"
                and (name := extract_candidate_name(item["content"])) is not None
            ),
            None,
        )
        if prior_name is None:
            return None
        search = await run_referent_read(
            PlannedToolCall(tool="searchStudents", arguments={"query": prior_name, "limit": 12})
        )
        items = _search_items(search)
        if not items:
            return None
        resolution = StaffAssistantPipeline._Resolution()
        text = request.text.lower()

        filtered = items
        constrained = False
        ordinal = next(
            (index for word, index in _ORDINAL_WORDS.items() if re.search(rf"\b{word}\b", text)),
            None,
        )
        if re.search(r"\blast\b", text):
            ordinal = len(items) - 1
        if ordinal is not None:
            constrained = True
            filtered = [items[ordinal]] if 0 <= ordinal < len(items) else []
        else:
            by_program = [
                item
                for item in filtered
                if str(item.get("programName") or "").lower() in text
                and str(item.get("programName") or "").strip()
            ]
            if by_program:
                constrained = True
                filtered = by_program
            year = re.search(r"\b(20\d\d)\b", text)
            if year:
                constrained = True
                filtered = [
                    item for item in filtered if str(item.get("classYear")) == year.group(1)
                ]
        if not constrained:
            return None
        if len(filtered) == 1:
            resolution.student_id = str(filtered[0].get("id"))
            resolution.student_name = str(
                filtered[0].get("preferredName") or filtered[0].get("name") or ""
            )
            resolution.prior_classification = self._classify_prior_turn(request)
            return resolution
        resolution.search_results = filtered or items
        resolution.short_circuit = _disambiguation_answer(prior_name, filtered or items)
        return resolution

    @staticmethod
    def _classify_prior_turn(request: NormalizedStaffRequest) -> StaffClassification | None:
        """The intent of the question that triggered the disambiguation, so
        "What's blocking Alex?" → "the one in Design" answers blockers."""

        prior_question = next(
            (item["content"] for item in reversed(request.history) if item["role"] == "user"),
            None,
        )
        if not prior_question:
            return None
        prior = classify_staff_request(normalize_staff_request(prior_question))
        if prior is not None and prior.request_type in STUDENT_REQUIRED_REQUEST_TYPES:
            return prior
        return None

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
            if (
                call.tool == "searchStudents"
                and not arguments.get("query")
                and not arguments.get("externalRef")
            ):
                if request.candidate_student_name:
                    arguments["query"] = request.candidate_student_name
                elif request.reference_token:
                    arguments["externalRef"] = request.reference_token
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
        # The model prose renders above the draft's structured blocks; telling
        # it what those blocks already show is what stops row-by-row restating.
        presented_blocks = describe_blocks_for_prompt(draft.blocks)
        try:
            result = await self._model_composer(
                question=question,
                evidence_texts=draft.evidence_texts,
                draft_answer=draft.message,
                presented_blocks=presented_blocks or None,
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
            answer_text = _restore_dropped_caveats(answer_text, draft.message)
            answer_text = _restore_required_phrases(answer_text, draft)
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


def _restore_required_phrases(answer: str, draft: ComposedStaffAnswer) -> str:
    """Put back a must-survive fact the rewrite dropped.

    Identity is the case this exists for: an overview whose rewrite loses the
    program and class year still reads fluently, and tells the staff member
    nothing about *which* student they are looking at.
    """

    lowered = answer.lower()
    for phrase, restoration in draft.required_phrases:
        if phrase.lower() in lowered:
            continue
        if restoration in answer:
            continue
        return f"{restoration} {answer}".strip()
    return answer


# Honesty caveats the deterministic draft states that a rewrite must not
# soften away: (marker in the draft, marker that must survive in the answer,
# sentence restored when it did not). Deterministic, because prompting alone
# demonstrably lets a paraphrase drop them.
_PRESERVED_CAVEATS: tuple[tuple[str, str, str], ...] = (
    (
        "registrar hold system",
        "hold system",
        "(No registrar hold system exists — these derived blockers are the complete list.)",
    ),
    (
        "engagement scan",
        "engagement scan",
        "(This view comes from the rule-based engagement scan, not a risk model.)",
    ),
)


def _restore_dropped_caveats(answer_text: str, draft_message: str) -> str:
    """Re-append any honesty caveat the deterministic draft carried and the
    accepted rewrite lost."""

    draft_lowered = draft_message.lower()
    answer_lowered = answer_text.lower()
    for draft_marker, answer_marker, sentence in _PRESERVED_CAVEATS:
        if draft_marker in draft_lowered and answer_marker not in answer_lowered:
            answer_text = f"{answer_text} {sentence}"
            answer_lowered = answer_text.lower()
    return answer_text


def _queue_head_student_id(
    classification: StaffClassification, state: StaffDerivedState
) -> str | None:
    """The student on the head of the queue slice this turn presented."""

    if classification.request_type not in {"work_queue", "work_item_detail"}:
        return None
    if classification.request_type == "work_item_detail":
        student = _as_mapping((state.work_item or {}).get("student"))
        return str(student.get("id")) if student.get("id") else None
    for item in (state.queue or {}).get("items", []):
        student = _as_mapping(_as_mapping(item).get("student"))
        if student.get("id"):
            return str(student["id"])
    return None


def _name_from_prior_answer(request: NormalizedStaffRequest) -> str | None:
    """The student name Edward's own previous answer put on the table."""

    for item in reversed(request.history):
        if item["role"] != "assistant" or not item["content"]:
            continue
        return extract_candidate_name(item["content"])
    return None


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


def _search_items(search: Mapping[str, Any] | None) -> list[JsonDict]:
    if not search:
        return []
    return [dict(item) for item in search.get("items", []) if isinstance(item, dict)]


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _looks_like_candidate_selection(
    request: NormalizedStaffRequest, explicit_name: str | None
) -> bool:
    if _SELECTION_PHRASE.search(request.text):
        return True
    # "The one in Civil Engineering." extracts "Civil Engineering" as if it
    # were a name; a follow-up whose "name" appears after a selection opener
    # is a pick, not a person.
    return explicit_name is not None and bool(
        re.search(
            rf"\b(?:one|that|the)\s+(?:in|from|with)\s+{re.escape(explicit_name)}",
            request.text,
            re.IGNORECASE,
        )
    )


def _candidate_line(item: Mapping[str, Any]) -> str:
    ref = str(item.get("externalRef") or "").strip()
    ref_part = f"ID {ref} — " if ref else ""
    return (
        f"{item.get('name')} — {ref_part}{item.get('programName')}, class of "
        f"{item.get('classYear')}"
    )


def _not_found_answer(name: str | None = None) -> ComposedStaffAnswer:
    who = f" matching “{name}”" if name else ""
    message = (
        f"I couldn't find a student{who} in your institution. A full name "
        "usually works best; I can also look up a student ID or search by "
        "program."
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message)])


def _unknown_reference_answer(token: str) -> ComposedStaffAnswer:
    message = (
        f"“{token}” doesn't match any student ID or work-item key in your "
        "institution. If it's a student, a full name works too."
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message)])


def _fuzzy_suggestion_answer(name: str, items: list[JsonDict]) -> ComposedStaffAnswer:
    from audentra.integrations.assistant.blocks import bullet_list_block

    if not items:
        return _not_found_answer(name)
    if len(items) == 1:
        only = items[0]
        message = (
            f"I couldn't find “{name}” exactly — did you mean "
            f"{_candidate_line(only)}? Say the name or ID and I'll pull the "
            "record."
        )
        return ComposedStaffAnswer(message=message, blocks=[text_block(message)])
    message = (
        f"I couldn't find “{name}” exactly. Closest matches on the roster — which one do you mean?"
    )
    block = bullet_list_block(
        [{"text": _candidate_line(item)} for item in items[:8]],
        title="Closest matches",
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message), block])


def _disambiguation_answer(name: str, items: list[JsonDict]) -> ComposedStaffAnswer:
    from audentra.integrations.assistant.blocks import bullet_list_block

    shown = items[:8]
    message = f"I found {len(items)} students matching “{name}” — which one do you mean?"
    if len(items) > len(shown):
        message += f" Showing the first {len(shown)}; an ID narrows it fastest."
    block = bullet_list_block(
        [{"text": _candidate_line(item)} for item in shown],
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


def _cohort_arguments(tool: str, classification: StaffClassification) -> JsonDict:
    """Carry a recognised cohort selection onto its tool call.

    Only the cohort tools take these, and only from the classification — the
    filter is derived from the staff member's own words, never from a model
    guess about who they meant.
    """

    if tool == "findStudents":
        return {"filter": dict(classification.cohort_filter or {})}
    if tool == "summarizeStudents":
        return {
            "filter": dict(classification.cohort_filter or {}),
            "groupBy": classification.cohort_group_by or "offer_status",
        }
    if (
        tool == "getStaffWorkQueue"
        and classification.reference is not None
        and classification.reference.startswith("topic:")
    ):
        # "transcript items in the Action Center" — the topic came from the
        # staff member's own words via the deterministic classifier.
        return {"topic": classification.reference.removeprefix("topic:")}
    return {}
