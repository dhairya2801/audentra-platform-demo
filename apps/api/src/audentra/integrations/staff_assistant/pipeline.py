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

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from audentra.core.assistant_execution import ReadPlanner, resolve_read_planner
from audentra.integrations.assistant.blocks import describe_blocks_for_prompt, text_block
from audentra.integrations.assistant.guard import ungrounded_tokens
from audentra.integrations.assistant.read_loop import (
    LoopCall,
    LoopTool,
    ReadLoopResult,
    bound_result,
    run_read_loop,
)
from audentra.integrations.assistant.trace import AssistantTurnTrace
from audentra.integrations.assistant.university_catalog import policy_evidence_blocks
from audentra.integrations.staff_assistant.catalog import (
    STAFF_TOOL_ARGUMENTS,
    STAFF_TOOL_DESCRIPTIONS,
    STAFF_TOOL_NAMES,
)
from audentra.integrations.staff_assistant.classify import (
    STAFF_REQUIRED_REQUEST_TYPES,
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
from audentra.integrations.staff_assistant.entities import (
    Ambiguity,
    EntityResolution,
    ResolvedEntity,
    extract_mentions,
    has_self_reference,
    resolve_entities,
)
from audentra.integrations.staff_assistant.guard import guard_staff_grounded_answer
from audentra.integrations.staff_assistant.identity import (
    StaffIdentity,
    identity_from_profile,
)
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
    is_globally_scoped,
    may_inherit_referent,
    referent_action,
    refers_back,
    scope_of,
    uses_singular_anaphora,
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
ReadLoopStep = Callable[..., Awaitable[Mapping[str, Any] | None]]

# The staff read loop leaves these to the deterministic composer: refusals,
# drafts (reviewed text), mailbox content (never forwarded to a rewrite
# model) and the yes/no membership statement.
_LOOP_SKIP_REQUEST_TYPES = frozenset(
    {
        "greeting",
        "capability_overview",
        "action_request",
        "supported_action_request",
        "unsupported_metric",
        "unsupported_or_out_of_scope",
        "draft_email",
        "draft_sms",
        "draft_call_points",
        "mailbox_read",
        "not_found",
    }
)
_LOOP_HYBRID_REQUEST_TYPES = frozenset({"general_question"})

KNOWLEDGE_TOOL = "searchInstitutionalKnowledge"
# Institutional language inside a person-scoped question (see the student
# pipeline's _POLICY_MARKERS): consequences, permissions, exceptions, dates,
# service levels, ownership.
_STAFF_POLICY_MARKERS = re.compile(
    r"\bwhat happens (?:if|when|now|after)\b|\bpolic(?:y|ies)\b|\brules?\b|\bprocedure\b"
    r"|\bhandbook\b|\ballowed\b|\bpermitted\b|\bexempt|\bwaiv|\bpenalt|\blate fee\b"
    r"|\brefund|\bforfeit|\bappeal|\bextension\b|\bextend\b|\bdeadline (?:for|to)\b"
    r"|\bgrace period\b|\beligib|\bqualif|\bwhat counts as\b|\bservice levels?\b|\bsla\b"
    r"|\bescalat|\bwho (?:handles|owns|should handle|is responsible|decides|approves|covers)\b"
    r"|\bwhich office\b|\bhow long (?:does|do|should)\b|\bturnaround\b"
    r"|\bappl(?:y|ies) to\b|\brequirement\b|\b(?:is|are) (?:he|she|they|the student) "
    r"(?:required|exempt|eligible|allowed)\b|\bif (?:he|she|they) (?:don'?t|do not|miss|misses)\b"
    r"|\bcan (?:he|she|they|the student|we)\b|\bauthori[sz]e\b|\bwhat do i need (?:before|to)\b"
    r"|\bdrop to \d+ credits\b|\bcredits\b|\bmedical reasons?\b|\bwhat do i tell\b"
    r"|\bstill (?:register|apply|get|live|enrol|enroll|move)\b"
    r"|\bwhen (?:is|are|does|do) (?:the )?(?:orientation|move[- ]?in|classes|registration"
    r"|add|drop|census|tuition|bills?|finals|break|commencement)\b",
    re.IGNORECASE,
)

# Identity arguments the model never supplies; the executor binds them from
# the turn's handles (see `_bind_loop_arguments`).
_LOOP_IDENTITY_ARGUMENTS = frozenset(
    {"studentId", "staffId", "staffIds", "workItemId", "inquiryId"}
)

# Intents whose deterministic answer is the deliverable: refusals stay
# canned, and a draft is reviewed text — a model rewrite of either could only
# soften a boundary or drift a fact.
_SKIP_REWRITE = frozenset(
    {
        "greeting",
        "capability_overview",
        "action_request",
        "supported_action_request",
        "unsupported_metric",
        "unsupported_or_out_of_scope",
        "draft_email",
        "draft_sms",
        "draft_call_points",
        # Mailbox content stays inside the platform's deterministic composer;
        # it is never forwarded to a configured third-party rewrite model.
        "mailbox_read",
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
    identity: StaffIdentity | None = None
    entities: EntityResolution | None = None


# Intents whose answer is about a person on staff: "her", "she", "his" after
# one of these refers to that person, not to a student.
_STAFF_REFERENT_TYPES = STAFF_REQUIRED_REQUEST_TYPES


class StaffAssistantPipeline:
    def __init__(
        self,
        host: StaffAssistantToolHost,
        *,
        model_composer: ModelComposer | None = None,
        model_planner: ModelPlanner | None = None,
        tool_timeout_seconds: float = DEFAULT_STAFF_TOOL_TIMEOUT_SECONDS,
        now: Callable[[], datetime] | None = None,
        read_loop_step: ReadLoopStep | None = None,
        read_planner: str | ReadPlanner | None = None,
        read_loop_max_rounds: int = 3,
    ) -> None:
        self._host = host
        self._model_composer = model_composer
        self._model_planner = model_planner
        self._read_loop_step = read_loop_step
        self._read_planner = (
            read_planner
            if isinstance(read_planner, ReadPlanner)
            else resolve_read_planner(read_planner)
        )
        self._read_loop_max_rounds = max(1, min(int(read_loop_max_rounds), 6))
        self._tool_timeout_seconds = tool_timeout_seconds
        self._now = now or (lambda: datetime.now(UTC))

    async def execute(
        self,
        *,
        message: str,
        history: Sequence[Mapping[str, str]] = (),
        context_student_id: str | None = None,
        trace: AssistantTurnTrace | None = None,
        action_is_supported: bool = False,
    ) -> StaffAssistantPipelineResult:
        failure_codes: list[str] = []
        stage_started = time.perf_counter()
        request = normalize_staff_request(
            message, history=history, action_is_supported=action_is_supported
        )
        if trace is not None:
            trace.user_message = request.text
            trace.history_messages = len(request.history)
            trace.set_history_preview(request.history)
            trace.add_stage(
                "normalize",
                (time.perf_counter() - stage_started) * 1_000,
                isFollowUp=request.is_follow_up or None,
                candidateStudentName=request.candidate_student_name,
                workItemKey=request.work_item_key,
                actionKind=request.action_kind,
            )

        execution = StaffToolExecution()

        # --- Identity: who is asking ---------------------------------------
        stage_started = time.perf_counter()
        identity = await self._load_identity(execution, trace)
        if trace is not None:
            trace.identity = identity.as_trace() if identity else None
            trace.add_stage("load_identity", (time.perf_counter() - stage_started) * 1_000)

        # --- Entities: who or what the turn is about -------------------------
        stage_started = time.perf_counter()
        entities = await self._resolve_entities(request, execution, trace)
        if trace is not None:
            trace.entities = entities.as_trace()
            trace.add_stage(
                "resolve_entities",
                (time.perf_counter() - stage_started) * 1_000,
                staff=[e.name for e in entities.staff],
                students=[e.name for e in entities.students],
                departments=[e.name for e in entities.departments],
                ambiguous=[a.mention for a in entities.ambiguities],
            )

        # A follow-up that refers back ("When is her next open slot?", "How
        # many students does she have?") inherits the colleague the previous
        # turn was about — before classification, so the turn is routed as a
        # staff question rather than a roster count.
        if not entities.staff and not entities.students and not entities.departments:
            carried = await self._carry_staff_referent(request, entities, execution, trace)
            if carried is not None:
                entities.staff.append(carried)
                if trace is not None:
                    trace.entities = entities.as_trace()

        stage_started = time.perf_counter()
        classification = classify_staff_request(request, entities)
        classification = _rescope_pronoun_follow_up(
            classification, request, entities, context_student_id
        )
        tool_selection_source = "deterministic" if classification is not None else None
        ambiguity_answer = self._entity_ambiguity_answer(classification, entities, request)
        if ambiguity_answer is not None:
            state = derive_staff_state(execution)
            if trace is not None:
                trace.classification = _classification_dict(classification)
                trace.response_source = "deterministic"
                trace.failure_codes = list(failure_codes)
                trace.final_message = ambiguity_answer.message
            return StaffAssistantPipelineResult(
                message=ambiguity_answer.message,
                blocks=ambiguity_answer.blocks,
                provider="guided",
                model=None,
                usage=None,
                context_receipts=_receipt_sources(execution.receipts),
                classification=classification,
                derived=state,
                failure_codes=failure_codes,
                identity=identity,
                entities=entities,
            )

        # --- Referent resolution -------------------------------------------
        resolution = await self._resolve_student_referent(
            request, classification, context_student_id, execution, trace, entities
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
                identity=identity,
                entities=entities,
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
        staff_resolved = bool(entities.staff) or identity is not None

        if trace is not None:
            trace.read_planner = self._read_planner.value
        # --- Model read loop -------------------------------------------------
        # Runs after identity, entities and the referent are settled, so every
        # identity argument the loop binds is one the deterministic resolver
        # validated. A loop that answers returns here; otherwise the turn
        # continues on the deterministic route with the reason recorded.
        if (
            self._read_loop_step is not None
            and not request.action_is_supported
            and (
                self._loop_applies(classification, request.text)
                or (
                    self._host.supports("university_record")
                    and self._read_planner is not ReadPlanner.DETERMINISTIC
                )
            )
        ):
            loop_response = await self._run_read_loop(
                request,
                classification,
                resolution,
                entities,
                identity,
                execution,
                failure_codes,
                trace,
            )
            if loop_response is not None:
                return loop_response

        if self._host.supports("university_record"):
            message = (
                "University record guidance is temporarily unavailable. "
                "You can review the student's university record and "
                "operational tasks in the portal."
            )
            if trace is not None:
                trace.response_source = "university_planner_unavailable"
                trace.failure_codes = ["university_planner_required"]
                trace.final_message = message
            return StaffAssistantPipelineResult(
                message=message,
                blocks=[text_block(message)],
                provider="deterministic",
                model=None,
                usage=None,
                context_receipts=[],
                classification=classification,
                failure_codes=["university_planner_required"],
            )

        # --- Planning -------------------------------------------------------
        planned_calls: list[PlannedToolCall] | None = None
        if classification is None and self._model_planner is not None:
            planner_started = time.perf_counter()
            planner_outcome = "invalid_plan"
            try:
                candidate = await self._model_planner(
                    message=request.resolved_text,
                    student_resolved=student_resolved,
                    context=_planner_context(identity, entities),
                )
            except Exception:
                candidate = None
                planner_outcome = "model_error"
                failure_codes.append("planner_model_failure")
            validated = (
                validate_staff_model_plan(
                    candidate,
                    student_resolved=student_resolved,
                    staff_resolved=staff_resolved,
                )
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
            if entities.staff:
                who = entities.staff[0].name
                answer_text = (
                    f"{who} is a member of staff, not a student, so I can't read a student "
                    f"record for them. Ask me about {who}'s caseload, queue, appointments or "
                    "availability instead — or name the student you mean."
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
                identity=identity,
                entities=entities,
            )

        if planned_calls is None:
            planned_calls = [
                PlannedToolCall(tool=tool, arguments=_cohort_arguments(tool, classification))
                for tool in select_staff_tools(classification, student_resolved=student_resolved)
            ]
            # Institutional language on a record question adds the approved
            # corpus read: "what happens if Milo misses the deposit deadline"
            # reads Milo's deadlines and the deposit policy.
            if (
                self._host.supports("institution_knowledge")
                and KNOWLEDGE_TOOL not in {call.tool for call in planned_calls}
                and classification.request_type not in _LOOP_SKIP_REQUEST_TYPES
                and classification.request_type
                not in {"action_request", "supported_action_request"}
                and _STAFF_POLICY_MARKERS.search(request.text)
            ):
                planned_calls.append(PlannedToolCall(tool=KNOWLEDGE_TOOL, arguments={}))
                if trace is not None:
                    trace.add_stage(
                        "policy_augment", 0.0, tool=KNOWLEDGE_TOOL, reason="policy_markers"
                    )
        planned_calls = await self._bind_identity_arguments(
            planned_calls, request, resolution, execution, trace, entities, identity
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
        # A refusal or canned answer about a named student ("what's Petra's
        # melt risk?") still puts Petra on the table: the resolver placed her,
        # the answer read nothing, and the follow-up should not have to name
        # her again.
        entity_referent_id = entity_referent_name = None
        if (
            resolution.student_id is None
            and len(entities.students) == 1
            and classification.request_type == "unsupported_metric"
        ):
            entity_referent_id = str(entities.students[0].id)
            entity_referent_name = entities.students[0].name

        # --- Compose + optional rewrite ---------------------------------------
        stage_started = time.perf_counter()
        draft = compose_staff_deterministic(classification, state)
        if identity is not None and classification.request_type in {
            "my_work",
            "my_profile",
            "team_overview",
        }:
            draft = ComposedStaffAnswer(
                message=draft.message,
                blocks=draft.blocks,
                evidence_texts=[identity.describe(), *draft.evidence_texts],
                required_phrases=draft.required_phrases,
            )
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
        draft = ComposedStaffAnswer(
            message=draft.message,
            blocks=draft.blocks,
            evidence_texts=[_today_line(self._now()), *draft.evidence_texts],
            required_phrases=draft.required_phrases,
        )
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
            resolved_student_id=resolution.student_id or entity_referent_id,
            resolved_student_name=resolution.student_name or entity_referent_name,
            referent_action=(
                "set"
                if (resolution.student_id or entity_referent_id or queue_referent)
                else referent_action(
                    resolved_student_id=None,
                    request_type=classification.request_type,
                )
            ),
            next_referent_student_id=resolution.student_id or entity_referent_id or queue_referent,
            identity=identity,
            entities=entities,
        )

    # ------------------------------------------------------------------
    # Model read loop
    # ------------------------------------------------------------------

    def _loop_applies(self, classification: StaffClassification | None, text: str = "") -> bool:
        if self._read_planner is ReadPlanner.DETERMINISTIC:
            return False
        if classification is None:
            return True
        if classification.request_type in _LOOP_SKIP_REQUEST_TYPES:
            return False
        if self._read_planner is ReadPlanner.MODEL:
            return True
        if (
            classification.source == "safe_fallback"
            or classification.request_type in _LOOP_HYBRID_REQUEST_TYPES
        ):
            return True
        # A question that spans two or more domains ("does she have an
        # appointment with her adviser before her earliest overdue
        # requirement") is a single-intent route for the classifier and a
        # cross-source read for the loop.
        # Aggregates and cohort searches carry a deterministic filter parsed
        # from the question ("overdue document reviews in the Registrar's
        # office"); the classifier's SQL-backed count beats a model reading a
        # filtered page, so those stay deterministic even when two domain
        # words appear.
        if classification.request_type in _AGGREGATE_REQUEST_TYPES:
            return False
        return len(_domains_mentioned(text)) >= 2

    async def _run_read_loop(
        self,
        request: NormalizedStaffRequest,
        classification: StaffClassification | None,
        resolution: StaffAssistantPipeline._Resolution,
        entities: EntityResolution,
        identity: StaffIdentity | None,
        execution: StaffToolExecution,
        failure_codes: list[str],
        trace: AssistantTurnTrace | None,
    ) -> StaffAssistantPipelineResult | None:
        """Model-planned staff reads with server-bound identity, guarded.

        Handles the model may use: ``student`` (the resolved referent),
        ``me`` (the signed-in member), ``staff:N`` (colleagues the entity
        resolver placed) and a work-item key. The executor maps handles to
        identifiers; a raw identifier or an unbound handle rejects the call
        with a visible reason rather than a guessed read.
        """

        assert self._read_loop_step is not None
        stage_started = time.perf_counter()
        handles: dict[str, str] = {}
        if resolution.student_id:
            handles["student"] = str(resolution.student_id)
        if self._host.staff_member_id:
            handles["me"] = str(self._host.staff_member_id)
        staff_handles: list[JsonDict] = []
        for index, member in enumerate(entities.staff[:4], start=1):
            handles[f"staff:{index}"] = str(member.id)
            staff_handles.append({"handle": f"staff:{index}", "name": member.name})
        host = self._host
        timeout = self._tool_timeout_seconds
        moment = self._now()
        discovered: dict[str, JsonDict] = {}

        async def executor(calls: Sequence[LoopCall]) -> None:
            planned: list[tuple[LoopCall, PlannedToolCall]] = []
            for call in calls:
                if call.status != "planned":
                    continue
                bound = await self._bind_loop_arguments(call, handles)
                if bound is None:
                    continue
                planned.append((call, bound))
            if not planned:
                return
            round_result = await execute_staff_tool_reads(
                [bound for _, bound in planned],
                host,
                timeout_seconds=timeout,
                now=moment,
                receipt_offset=len(execution.receipts),
            )
            _merge_execution(execution, round_result)
            rejected = {item["tool"]: item for item in round_result.rejected_arguments}
            for call, bound in planned:
                read = round_result.reads.get(bound.tool)
                if read is None:
                    call.status = "rejected"
                    problem = rejected.get(bound.tool)
                    call.reason = str(problem.get("detail")) if problem else "not_executed"
                    continue
                call.status = str(read.get("status", "unavailable"))
                call.reason = read.get("reason")
                call.result = read.get("data")
                call.duration_ms = read.get("durationMs")
                # A canonical roster search that found exactly one student
                # binds the `student` handle for the rest of the turn — the
                # identifier still comes from the search result, never from
                # the model — so a name the entity resolver did not catch
                # (lower-case, a nickname) can still be read about.
                if (
                    bound.tool == "searchStudents"
                    and "student" not in handles
                    and call.status == "available"
                    and isinstance(call.result, Mapping)
                ):
                    items = _search_items(call.result)
                    quality = str(call.result.get("matchQuality") or "exact")
                    if len(items) == 1 and items[0].get("id") and quality != "fuzzy":
                        handles["student"] = str(items[0]["id"])
                        discovered["student"] = {
                            "id": str(items[0]["id"]),
                            "name": str(
                                items[0].get("preferredName") or items[0].get("name") or ""
                            ),
                        }

        from audentra.integrations.assistant.university_catalog import (
            UNIVERSITY_CONTEXT,
            UNIVERSITY_TOOLS,
        )

        university_only = {
            *UNIVERSITY_TOOLS,
            "getUniversityOperations",
            "getUniversityCasework",
            "getUniversityCohort",
            "searchUniversityPolicies",
        }
        legacy_student_facts = {
            "getStudentFinancialState",
            "getStudentBlockers",
            "getStudentHousingState",
            "getStudentTimeline",
            "getStudentCommunicationHistory",
            "getStudentOwnership",
            "searchInstitutionalKnowledge",
            "getStudentStaffSummary",
            "getStudentEngagementSignals",
        }
        tools = [
            LoopTool(
                name=name,
                description=STAFF_TOOL_DESCRIPTIONS.get(name, ""),
                arguments=_describe_loop_arguments(name),
                identity_arguments=tuple(
                    arg
                    for arg in STAFF_TOOL_ARGUMENTS.get(name, {})
                    if arg in _LOOP_IDENTITY_ARGUMENTS
                ),
            )
            for name in STAFF_TOOL_NAMES
            if (self._host.supports("university_record") or name not in university_only)
            and (not self._host.supports("university_record") or name not in legacy_student_facts)
        ]
        context: JsonDict = {
            "university": UNIVERSITY_CONTEXT if self._host.supports("university_record") else None,
            "actor": "staff",
            "today": moment.date().isoformat(),
            "signedIn": identity.describe() if identity is not None else None,
            "handles": {
                "me": "the signed-in staff member" if "me" in handles else None,
                "student": (
                    f"{resolution.student_name} (the student this turn is about)"
                    if resolution.student_id
                    else "no student is resolved for this turn; searchStudents first "
                    "if the question names one, or ask the person to name one"
                ),
                "staff": staff_handles or None,
            },
            "resolvedEntities": entities.as_trace(),
            "cohortFilterVocabulary": _COHORT_FILTER_GUIDE,
            "institutionalKnowledge": (
                "searchInstitutionalKnowledge reads approved policies, procedures, the "
                "calendar and the office directory by a text query; combine it with the "
                "student's record reads for 'does this apply to them' and 'what happens if' "
                "questions."
                + (
                    " This question uses institutional language: read it in the first step."
                    if _STAFF_POLICY_MARKERS.search(request.text)
                    else ""
                )
                if self._host.supports("institution_knowledge")
                else None
            ),
        }
        result: ReadLoopResult = await run_read_loop(
            question=request.resolved_text if request.is_follow_up else request.text,
            history=request.history,
            context=context,
            tools=tools,
            model_step=self._read_loop_step,
            executor=executor,
            max_rounds=self._read_loop_max_rounds,
        )
        if trace is not None:
            for call in result.calls:
                trace.add_tool_call(
                    tool=call.tool,
                    status=call.status,
                    duration_ms=call.duration_ms,
                    reason=call.reason,
                    result=call.result,
                    model_result=bound_result(call.result) if call.status == "available" else None,
                    round_name=f"loop-{call.round_index + 1}",
                    arguments=call.arguments,
                )
            for entry in result.model_calls:
                trace.add_model_call(
                    operation=str(entry.get("operation") or "assistant_read_loop"),
                    attempt=int(entry.get("attempt") or 1),
                    duration_ms=float(entry.get("durationMs") or 0),
                    outcome=str(entry.get("outcome") or "step"),
                    provider=entry.get("provider"),
                    model=entry.get("model"),
                    usage=entry.get("usage"),
                    detail=entry.get("detail"),
                )
        state = derive_staff_state(execution)
        state.search_results = state.search_results or resolution.search_results
        verdict = None
        if result.answer:
            evidence = list(result.evidence_texts)
            if identity is not None:
                evidence.append(identity.describe())
            verdict = guard_staff_grounded_answer(answer=result.answer, evidence_texts=evidence)
        accepted = verdict is not None and verdict.accepted
        guard_label = (
            "accepted"
            if accepted
            else (verdict.reason_code if verdict is not None else result.outcome)
        )
        if trace is not None:
            trace.read_loop = {
                "rounds": result.rounds,
                "outcome": result.outcome,
                "reads": [call.tool for call in result.calls],
                "reasoning": list(result.reasoning),
                "steps": list(result.steps),
                "guard": guard_label,
                **(
                    {
                        "ungrounded": ungrounded_tokens(result.answer, result.evidence_texts),
                        "rejectedAnswer": result.answer[:600],
                    }
                    if result.answer and not accepted
                    else {}
                ),
            }
            trace.add_stage(
                "read_loop",
                (time.perf_counter() - stage_started) * 1_000,
                rounds=result.rounds,
                outcome=guard_label,
            )
        if not accepted:
            failure_codes.append(f"read_loop_fallback:{guard_label}")
            if host.supports("university_record"):
                from audentra.integrations.assistant.university_catalog import (
                    university_read_failure,
                )

                message, response_source = university_read_failure(str(guard_label))
                if trace is not None:
                    trace.response_source = response_source
                    trace.failure_codes = list(failure_codes)
                    trace.final_message = message
                return StaffAssistantPipelineResult(
                    message=message,
                    blocks=[text_block(message)],
                    provider="deterministic",
                    model=None,
                    usage=_sum_usage(result.model_calls),
                    context_receipts=[],
                    classification=classification,
                    derived=state,
                    failure_codes=failure_codes,
                )
            return None
        assert verdict is not None
        resolved = classification or StaffClassification(
            "general_question", 0.6, source="model_loop"
        )
        last_call = next(
            (entry for entry in reversed(result.model_calls) if entry.get("model")), None
        )
        usage_total = _sum_usage(result.model_calls)
        queue_referent = _queue_head_student_id(resolved, state)
        found = discovered.get("student")
        student_id = resolution.student_id or (found["id"] if found else None)
        student_name = resolution.student_name or (found["name"] if found else None)
        if trace is not None:
            trace.classification = _classification_dict(resolved)
            trace.tool_selection_source = "model_loop"
            trace.selected_tools = list(dict.fromkeys(call.tool for call in result.calls))
            trace.evidence = list(result.evidence_texts[:48])
            trace.provider = str((last_call or {}).get("provider") or "openai")
            trace.model = (last_call or {}).get("model")
            trace.usage = usage_total
            trace.response_source = "model_loop"
            trace.failure_codes = list(failure_codes)
            trace.final_message = verdict.answer
        return StaffAssistantPipelineResult(
            message=verdict.answer,
            blocks=[text_block(verdict.answer), *policy_evidence_blocks(result.calls)],
            provider=str((last_call or {}).get("provider") or "openai"),
            model=(last_call or {}).get("model"),
            usage=usage_total,
            context_receipts=_receipt_sources(execution.receipts),
            classification=resolved,
            derived=state,
            failure_codes=failure_codes,
            resolved_student_id=student_id,
            resolved_student_name=student_name,
            referent_action=(
                "set"
                if (student_id or queue_referent)
                else referent_action(resolved_student_id=None, request_type=resolved.request_type)
            ),
            next_referent_student_id=student_id or queue_referent,
            identity=identity,
            entities=entities,
        )

    async def _bind_loop_arguments(
        self, call: LoopCall, handles: Mapping[str, str]
    ) -> PlannedToolCall | None:
        """Replace handles with validated identifiers; refuse anything else."""

        schema = STAFF_TOOL_ARGUMENTS.get(call.tool, {})
        arguments: JsonDict = {}
        for name, value in call.arguments.items():
            if name not in schema:
                call.status, call.reason = "rejected", f"unknown argument {name}"
                return None
            if name not in _LOOP_IDENTITY_ARGUMENTS:
                arguments[name] = value
                continue
            if name == "staffIds":
                if isinstance(value, str):
                    value = [item.strip() for item in value.split(",")]
                if not isinstance(value, list):
                    call.status, call.reason = "rejected", "staffIds must be a list of handles"
                    return None
                ids: list[str] = []
                for handle in value:
                    resolved_id = handles.get(str(handle))
                    if resolved_id is None:
                        call.status, call.reason = "rejected", f"unbound handle {handle}"
                        return None
                    ids.append(resolved_id)
                arguments[name] = ids
                continue
            if name == "workItemId":
                key = str(value).strip().upper()
                found = None
                if self._host.supports("work_item_by_key"):
                    try:
                        found = await self._host.read("work_item_by_key", key=key)
                    except Exception:
                        found = None
                if not found or not found.get("id"):
                    call.status, call.reason = "rejected", f"no work item with key {key}"
                    return None
                arguments[name] = str(found["id"])
                continue
            resolved_id = handles.get(str(value))
            if resolved_id is None:
                call.status, call.reason = "rejected", f"unbound handle {value}"
                return None
            arguments[name] = resolved_id
        # Identity arguments the model omitted default to the obvious handle:
        # a student tool reads the resolved student, a staff tool reads "me".
        for name in schema:
            if name in arguments or name not in _LOOP_IDENTITY_ARGUMENTS:
                continue
            fallback = None
            if name == "studentId":
                fallback = handles.get("student")
            elif name == "staffId":
                fallback = handles.get("staff:1") or handles.get("me")
            if fallback is None:
                if schema[name].get("optional"):
                    continue
                call.status, call.reason = "rejected", f"{name} has no bound handle this turn"
                return None
            arguments[name] = fallback
        return PlannedToolCall(tool=call.tool, arguments=arguments)

    # ------------------------------------------------------------------
    # Identity and entities
    # ------------------------------------------------------------------

    async def _run_read(
        self,
        call: PlannedToolCall,
        execution: StaffToolExecution,
        trace: AssistantTurnTrace | None,
        round_name: str,
    ) -> Mapping[str, Any] | None:
        round_result = await execute_staff_tool_reads(
            [call],
            self._host,
            timeout_seconds=self._tool_timeout_seconds,
            now=self._now(),
            receipt_offset=len(execution.receipts),
        )
        _merge_execution(execution, round_result)
        if trace is not None:
            _trace_round(trace, round_result, round_name)
        read = round_result.reads.get(call.tool, {})
        data = read.get("data")
        return data if isinstance(data, Mapping) else None

    async def _load_identity(
        self, execution: StaffToolExecution, trace: AssistantTurnTrace | None
    ) -> StaffIdentity | None:
        """The signed-in staff member, from the bounded profile read."""

        if not self._host.staff_member_id or not self._host.supports("staff_profile"):
            return None
        # Read through the host cache directly rather than as a tool call: a
        # tool read lands in the derived state as *the subject's* profile,
        # and the signed-in member is the reader, not the subject.
        try:
            profile = await asyncio.wait_for(
                self._host.read("staff_profile", staff_member_id=self._host.staff_member_id),
                timeout=self._tool_timeout_seconds,
            )
        except Exception:
            return None
        return identity_from_profile(profile if isinstance(profile, Mapping) else None)

    async def _resolve_entities(
        self,
        request: NormalizedStaffRequest,
        execution: StaffToolExecution,
        trace: AssistantTurnTrace | None,
    ) -> EntityResolution:
        components: list[str] = []
        if self._host.supports("list_components"):
            # The component list is tiny and cached per request; it is read
            # directly rather than through a tool so the trace stays about
            # the names the turn actually resolved.
            try:
                raw = await self._host.read("list_components")
                components = [
                    str(item.get("component"))
                    for item in raw.get("items", [])
                    if isinstance(item, Mapping) and item.get("component")
                ]
            except Exception:
                components = []

        async def read(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any] | None:
            if tool == "searchStaff" and not self._host.supports("search_staff"):
                return None
            return await self._run_read(
                PlannedToolCall(tool=tool, arguments=dict(arguments)), execution, trace, "entities"
            )

        prefer = "student" if request.is_draft_request else None
        return await resolve_entities(request, read, prefer_kind=prefer, components=components)

    async def _carry_staff_referent(
        self,
        request: NormalizedStaffRequest,
        entities: EntityResolution,
        execution: StaffToolExecution,
        trace: AssistantTurnTrace | None,
    ) -> ResolvedEntity | None:
        """A follow-up that refers back inherits the colleague of the prior turn.

        Only the most recent user turn that named someone is consulted, and
        only when that name resolved to a staff member; a prior *student*
        stays with the student referent machinery.
        """

        if not request.history or has_explicit_entity(request):
            return None
        if not (request.uses_pronoun_referent or refers_back(request)):
            return None
        # "tasks on my board", "how many do I have" after a turn about a
        # colleague are about the signed-in member: a self-reference never
        # inherits the colleague.
        if has_self_reference(request.text) and not request.uses_pronoun_referent:
            return None
        for item in reversed(request.history):
            if item["role"] != "user":
                continue
            mentions = [m for m in extract_mentions(item["content"]) if m.kind_hint == "person"]
            if not mentions:
                continue
            # The prior turn's own words decide who its name was ("How many
            # students does Elena Larkspur advise?" is about the adviser even
            # though four students share her name).
            prior = await resolve_entities(
                normalize_staff_request(item["content"]),
                lambda tool, arguments: self._run_read(
                    PlannedToolCall(tool=tool, arguments=dict(arguments)),
                    execution,
                    trace,
                    "entities",
                ),
            )
            if prior.staff:
                return prior.staff[0]
            # Only the most recent user turn that named someone counts.
            break
        return None

    def _entity_ambiguity_answer(
        self,
        classification: StaffClassification | None,
        entities: EntityResolution,
        request: NormalizedStaffRequest,
    ) -> ComposedStaffAnswer | None:
        """Ask instead of guessing when a name is ambiguous or unknown.

        Only when the question actually needs the person: a refusal or a
        canned answer names nobody and must not be preempted by a lookup.
        """

        if classification is not None and classification.request_type in _NO_RESOLUTION_TYPES:
            return None
        if request.reference_token:
            # "Lucia Zephyrine SYN-001278" — the pasted ID is decisive; referent
            # resolution looks it up on the roster (then the work-item
            # namespace), and a same-name list would only ask what the message
            # already said.
            return None
        if entities.staff or entities.students:
            # Something resolved; the remaining ambiguities (if any) are
            # secondary mentions and the answer proceeds on what resolved.
            return None
        for ambiguity in entities.ambiguities:
            if ambiguity.reason == "staff_and_student":
                return _staff_or_student_answer(ambiguity)
            if ambiguity.reason == "several_staff":
                return _several_staff_answer(ambiguity)
            if ambiguity.reason == "several_students":
                if (
                    classification is not None
                    and scope_of(classification.request_type) is not STUDENT_SCOPE
                    and classification.request_type not in STAFF_REQUIRED_REQUEST_TYPES
                ):
                    continue
                return _disambiguation_answer(ambiguity.mention, ambiguity.students)
            if ambiguity.reason == "fuzzy" and (
                classification is None
                or classification.request_type in STUDENT_REQUIRED_REQUEST_TYPES
                or classification.request_type in STAFF_REQUIRED_REQUEST_TYPES
            ):
                return _fuzzy_suggestion_answer(ambiguity.mention, ambiguity.students)
            if ambiguity.reason == "not_found" and (
                classification is None
                or classification.request_type in STUDENT_REQUIRED_REQUEST_TYPES
                or classification.request_type in STAFF_REQUIRED_REQUEST_TYPES
            ):
                return _nobody_found_answer(ambiguity.mention, entities.staff_context)
        return None

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
        entities: EntityResolution | None = None,
    ) -> StaffAssistantPipeline._Resolution:
        resolution = StaffAssistantPipeline._Resolution()
        if classification is not None and classification.request_type in _NO_RESOLUTION_TYPES:
            # A refusal or canned answer reads nothing — resolving a name
            # first would let a lookup failure preempt the refusal itself
            # ("Mark X's transcript as accepted" must refuse, not disambiguate).
            return resolution
        if entities is not None and entities.students and not request.reference_token:
            # The entity resolver already placed the name on the roster
            # (exactly one student); no second search is needed.
            student = entities.students[0]
            resolution.student_id = student.id
            resolution.student_name = student.name
            resolution.search_results = [dict(student.data)] if student.data else []
            return resolution
        if (
            entities is not None
            and (entities.staff or entities.departments)
            and not request.reference_token
            and not entities.students
        ):
            # The turn is about a colleague or a department; a student-scoped
            # intent will be told so rather than searching the roster.
            if (
                classification is not None
                and scope_of(classification.request_type) is not STUDENT_SCOPE
            ):
                return resolution
            if entities.staff and not request.uses_pronoun_referent:
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
                # One indexed row, never the board: the key either exists or
                # it does not.
                try:
                    found = await self._host.read("work_item_by_key", key=reference_token)
                except Exception:
                    found = None
                if found and found.get("id"):
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

        if (
            explicit_name is not None
            and entities is not None
            and (
                entities.staff
                or any(
                    a.reason in {"not_found", "fuzzy", "several_students", "staff_and_student"}
                    for a in entities.ambiguities
                )
            )
        ):
            # The resolver already searched this name and either placed it on
            # staff or reported it; the ambiguity answer path handles the rest.
            explicit_name = None
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
            if len(items) > 1 and (request.action_is_supported or request.is_draft_request):
                # Same-name candidates on a write-action turn narrow to the
                # asker's own caseload: the gateway would deny every other one
                # anyway, so a unique on-caseload match is the student meant.
                # See `_add_students` in entities.py for the full argument.
                in_scope = [item for item in items if item.get("onCaseload")]
                if len(in_scope) >= 1:
                    items = in_scope
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
        entities: EntityResolution | None = None,
        identity: StaffIdentity | None = None,
    ) -> list[PlannedToolCall]:
        """Bind server-side identity arguments; drop calls that cannot bind."""

        from audentra.integrations.staff_assistant.catalog import (
            STAFF_SCOPED_TOOLS,
            STUDENT_SCOPED_TOOLS,
        )

        staff_entity = entities.primary_staff if entities is not None else None
        # "Me" binds to the signed-in member when the turn names no colleague.
        self_id = identity.id if identity is not None else self._host.staff_member_id
        bound: list[PlannedToolCall] = []
        for call in planned_calls:
            arguments = dict(call.arguments)
            arguments.pop("studentId", None)
            arguments.pop("workItemId", None)
            arguments.pop("inquiryId", None)
            arguments.pop("staffId", None)
            arguments.pop("staffIds", None)
            if call.tool in STAFF_SCOPED_TOOLS:
                target = staff_entity.id if staff_entity is not None else self_id
                if target is None:
                    continue
                arguments["staffId"] = target
            if call.tool == "compareStaff":
                ids = [e.id for e in (entities.staff if entities else []) if e.id]
                if len(ids) < 2:
                    continue
                arguments["staffIds"] = ids[:4]
            if call.tool in {
                "searchWorkQueue",
                "summarizeWorkQueue",
                "searchInquiries",
                "summarizeInquiries",
            }:
                if staff_entity is not None and not arguments.get("ownership"):
                    arguments["staffId"] = staff_entity.id
                elif (
                    entities is not None
                    and entities.self_reference
                    and not staff_entity
                    and not arguments.get("ownership")
                    and not arguments.get("component")
                ):
                    arguments["ownership"] = "mine"
                if (
                    entities is not None
                    and entities.primary_department is not None
                    and len(entities.departments) == 1
                    and not arguments.get("component")
                    and arguments.get("groupBy") != "component"
                    and not staff_entity
                ):
                    arguments["component"] = entities.primary_department.name
            if (
                call.tool == "searchStaff"
                and arguments.get("absentNow")
                and entities is not None
                and entities.self_team
                and identity is not None
                and identity.component
            ):
                # "Who on my team is away?" — the signed-in member's component.
                arguments["component"] = identity.component
            if call.tool == "getComponentSummary" and not arguments.get("component"):
                if entities is not None and entities.primary_department is not None:
                    arguments["component"] = entities.primary_department.name
                elif identity is not None and identity.component:
                    arguments["component"] = identity.component
                else:
                    continue
            if call.tool in STUDENT_SCOPED_TOOLS:
                if resolution.student_id is None:
                    continue
                arguments["studentId"] = resolution.student_id
            if call.tool == KNOWLEDGE_TOOL:
                # The question itself is the search text; the resolved student
                # (never a model-chosen one) scopes applicability.
                arguments.setdefault("query", request.resolved_text or request.text)
                if resolution.student_id is not None:
                    arguments["studentId"] = resolution.student_id
                else:
                    arguments.pop("studentId", None)
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
        try:
            found = await self._host.read("work_item_by_key", key=request.work_item_key)
        except Exception:
            found = None
        if found and found.get("id"):
            return str(found["id"])
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
    if tool == "getStaffWorkQueue" and classification.cohort_filter:
        allowed = {"ownership", "component", "status", "dueWindow", "topic"}
        return {k: v for k, v in dict(classification.cohort_filter).items() if k in allowed}
    if tool == "searchStaff" and classification.request_type == "staff_directory":
        return {"absentNow": True, "limit": 25}
    if tool == "getStaffCaseload" and classification.cohort_filter:
        allowed = {
            "advisingStatus",
            "depositState",
            "withOpenWork",
            "withOverdueWork",
            "role",
            "limit",
        }
        return {k: v for k, v in dict(classification.cohort_filter).items() if k in allowed}
    if tool == "getStaffAppointments" and classification.cohort_filter:
        window = dict(classification.cohort_filter).get("window")
        return {"window": window} if window else {}
    if tool in {"summarizeWorkQueue", "searchWorkQueue"}:
        # The queue filters were parsed from the staff member's own words by
        # the deterministic classifier (unassigned, urgent, overdue, stale,
        # a department, a grouping); a page never carries the grouping.
        filters = dict(classification.cohort_filter or {})
        if classification.request_type == "inquiry_aggregate":
            filters = dict((classification.additional_filters or {}).get("queue_aggregate") or {})
        if tool == "searchWorkQueue":
            filters.pop("groupBy", None)
            filters.setdefault("limit", 10)
        return filters
    if tool in {"summarizeInquiries", "searchInquiries"}:
        filters = dict(classification.cohort_filter or {})
        if classification.request_type != "inquiry_aggregate":
            # A compound ask ("how many items are unassigned and how many
            # inquiries are awaiting a reply") carries the inquiry clause's
            # own filters; the queue clause's filters must not leak over.
            filters = dict((classification.additional_filters or {}).get("inquiry_aggregate") or {})
        if tool == "searchInquiries":
            filters.pop("groupBy", None)
            filters.setdefault("limit", 8)
        else:
            filters.pop("sort", None)
        return filters
    return {}


def _planner_context(identity: StaffIdentity | None, entities: EntityResolution) -> JsonDict:
    """What the model planner is told about who asks and who is named."""

    context: JsonDict = {}
    if identity is not None:
        context["signedInStaff"] = {
            "name": identity.name,
            "title": identity.title,
            "component": identity.component,
            "isManager": identity.is_manager,
            "directReports": identity.direct_reports,
            "primaryAdvisees": identity.primary_advisees,
        }
    context["resolvedEntities"] = {
        "staff": [
            {"name": e.name, "title": e.data.get("title"), "component": e.data.get("component")}
            for e in entities.staff
        ],
        "students": [{"name": e.name} for e in entities.students],
        "departments": [e.name for e in entities.departments],
        "ambiguous": [a.mention for a in entities.ambiguities],
        "selfReference": entities.self_reference,
    }
    return context


def _staff_or_student_answer(ambiguity: Ambiguity) -> ComposedStaffAnswer:
    from audentra.integrations.assistant.blocks import bullet_list_block

    staff = ambiguity.staff[0] if ambiguity.staff else {}
    students = ambiguity.students
    message = (
        f"“{ambiguity.mention}” matches both a member of staff — {staff.get('name')}, "
        f"{staff.get('title') or 'staff'} in {staff.get('component')} — and "
        f"{len(students)} student{'s' if len(students) != 1 else ''} on the roster. Which do you "
        f"mean? "
        "Say “the adviser” for the staff member, or pick a student by ID."
    )
    block = bullet_list_block(
        [{"text": _candidate_line(item)} for item in students[:8]],
        title="Students with this name",
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message), block])


def _several_staff_answer(ambiguity: Ambiguity) -> ComposedStaffAnswer:
    from audentra.integrations.assistant.blocks import bullet_list_block

    message = (
        f"Several staff members match “{ambiguity.mention}” — which one do you mean? "
        "A full name settles it."
    )
    block = bullet_list_block(
        [
            {
                "text": f"{item.get('name')} — {item.get('title') or item.get('roleCode')}, "
                f"{item.get('component')} ({item.get('employmentStatus')})"
            }
            for item in ambiguity.staff[:8]
        ],
        title="Staff matches",
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message), block])


def _nobody_found_answer(name: str, staff_context: bool) -> ComposedStaffAnswer:
    message = (
        f"I couldn't find anyone called “{name}” — not on staff and not on the student "
        "roster. A full name works best; I can also look up a student ID."
    )
    return ComposedStaffAnswer(message=message, blocks=[text_block(message)])


_COHORT_FILTER_GUIDE = (
    "findStudents/summarizeStudents `filter` object keys: query, program, classYear, "
    "offerStatus (offered|accepted|declined|expired), depositState (paid|pending|unpaid), "
    "onboardingStatus (not_started|in_progress|completed), requirementCode, "
    "requirementState (open|blocked|in_review|complete|overdue|due_soon|any), "
    "documentCategory, documentState (missing|submitted|under_review|accepted|rejected), "
    "aidDocumentState (outstanding|verified|action_required|in_review), housingState "
    "(blocked|actionable|selected|no_step), hasOpenWorkItem, hasOverdueRequirement, "
    "hasOpenBlockingRequirement (booleans), residencyStatus, citizenshipStatus, "
    "adviserState (none|assigned|active|on_leave|departed). summarizeStudents groupBy: "
    "offer_status|deposit_state|onboarding_status|program|class_year|assigned_staff|"
    "blocking_requirement|housing_state|primary_adviser|adviser_state."
)


_AGGREGATE_REQUEST_TYPES = frozenset(
    {
        "queue_aggregate",
        "cohort_aggregate",
        "cohort_search",
        "department_operations",
        "inquiry_aggregate",
        "work_queue",
        "attention_ranking",
    }
)

# Staff-question domains; two or more in one message marks a cross-source ask.
_STAFF_DOMAIN_HINTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "appointments",
        re.compile(r"\bappointments?\b|\bmeetings?\b|\bbooked\b|\bcalendar\b|\bslots?\b", re.I),
    ),
    (
        "requirements",
        re.compile(
            r"\brequirements?\b|\boverdue\b|\bdeadlines?\b|\bblock(?:ed|ing|ers?)\b"
            r"|\bwaiting on\b|\bmissing\b",
            re.I,
        ),
    ),
    ("documents", re.compile(r"\bdocuments?\b|\btranscript\b|\bimmuni\w*\b|\bupload\w*\b", re.I)),
    (
        "ownership",
        re.compile(
            r"\bown(?:s|er|ed)?\b|\bhandl(?:es|ing)\b|\bassigned to\b|\bresponsible\b", re.I
        ),
    ),
    (
        "advising",
        re.compile(
            r"\badvis(?:e|o)rs?\b|\bcounsel(?:l)?ors?\b|\bcoordinator\b|\bon leave\b"
            r"|\bbook(?:able|ed)?\b",
            re.I,
        ),
    ),
    (
        "queue",
        re.compile(
            r"\bmy (?:queue|plate|board|items?|tasks?|work)\b|\burgent\b|\bescalated\b"
            r"|\baction cent(?:er|re)\b",
            re.I,
        ),
    ),
    ("financial", re.compile(r"\bdeposit\b|\bfinancial\b|\baid\b|\bbalance\b|\bpaid\b", re.I)),
)


def _domains_mentioned(text: str) -> list[str]:
    return [name for name, pattern in _STAFF_DOMAIN_HINTS if pattern.search(text)]


# Queue/cohort intents a singular pronoun can pull back onto the active student.
_PRONOUN_RESCOPE: Mapping[str, str] = {
    "queue_aggregate": "student_action_center",
    "work_queue": "student_action_center",
    "cohort_search": "student_overview",
    "cohort_aggregate": "student_overview",
    "attention_ranking": "student_overview",
}
_OWNER_WORDS = re.compile(
    r"\b(?:own|owns|owner|owned|handling|handles|assigned|responsible)\b", re.I
)


def _rescope_pronoun_follow_up(
    classification: StaffClassification | None,
    request: NormalizedStaffRequest,
    entities: EntityResolution,
    context_student_id: str | None,
) -> StaffClassification | None:
    """ "who owns her housing item?" after a turn about Noor is about Noor.

    The queue classifier sees "item" and files the turn under the queue; the
    pronoun says otherwise. With an active student on the conversation, no
    entity of its own and no global-scope language, a singular pronoun moves
    the turn back into student scope so the referent inherits.
    """

    if classification is None or context_student_id is None:
        return classification
    target = _PRONOUN_RESCOPE.get(classification.request_type)
    if target is None or entities.students or entities.staff or has_explicit_entity(request):
        return classification
    if not uses_singular_anaphora(request) or is_globally_scoped(request):
        return classification
    if _OWNER_WORDS.search(request.text):
        target = "student_ownership"
    return StaffClassification(
        target,
        classification.confidence,
        source="pronoun_rescope",
        reference=classification.reference,
    )


def _describe_loop_arguments(tool: str) -> str:
    """One line per argument: name, kind, allowed values, and which handle binds it."""

    schema = STAFF_TOOL_ARGUMENTS.get(tool, {})
    if not schema:
        return "none"
    parts: list[str] = []
    for name, spec in schema.items():
        kind = str(spec.get("kind"))
        if name in _LOOP_IDENTITY_ARGUMENTS:
            handle = {
                "studentId": "handle `student`",
                "staffId": "handle `me` or `staff:N`",
                "staffIds": "list of handles",
                "workItemId": "a work-item key such as AST-01234",
                "inquiryId": "not available through this loop",
            }[name]
            parts.append(f"{name}: {handle}")
            continue
        detail = kind
        if kind == "enum":
            detail = "one of " + "|".join(str(v) for v in spec.get("values", ()))
        elif kind == "int":
            detail = f"integer {spec.get('minimum', 1)}-{spec.get('maximum', 100)}"
        elif kind == "cohort_filter":
            detail = "object (see cohortFilterVocabulary in context)"
        optional = " (optional)" if spec.get("optional") else ""
        parts.append(f"{name}: {detail}{optional}")
    handle_examples: Mapping[str, Any] = {
        "studentId": "student",
        "staffId": "me",
        "staffIds": ["me"],
        "workItemId": "AST-01234",
    }
    example = {name: handle_examples[name] for name in schema if name in handle_examples}
    if example:
        parts.append("example arguments string: " + json.dumps(example))
    return "; ".join(parts)


def _sum_usage(model_calls: Sequence[Mapping[str, Any]]) -> JsonDict | None:
    prompt = completion = total = 0
    seen = False
    for entry in model_calls:
        usage = entry.get("usage")
        if not isinstance(usage, Mapping):
            continue
        seen = True
        prompt += int(usage.get("promptTokens") or 0)
        completion += int(usage.get("completionTokens") or 0)
        total += int(usage.get("totalTokens") or 0)
    if not seen:
        return None
    return {"promptTokens": prompt, "completionTokens": completion, "totalTokens": total}


def _today_line(now: datetime) -> str:
    return (
        f"Today is {now:%A %d %B %Y} (ISO {now:%Y-%m-%d}); dates before this have already passed."
    )
