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

import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from audentra.core.assistant_execution import ReadPlanner, resolve_read_planner
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
from audentra.integrations.assistant.guard import (
    build_causal_guards,
    guard_grounded_answer,
    ungrounded_tokens,
)
from audentra.integrations.assistant.planner import (
    REQUIREMENT_GATE_CODES,
    TOOL_DESCRIPTIONS,
    resolve_dependency_reads,
    select_tool_reads,
    validate_model_tool_plan,
)
from audentra.integrations.assistant.read_loop import (
    LoopCall,
    LoopTool,
    ReadLoopResult,
    bound_result,
    run_read_loop,
)
from audentra.integrations.assistant.tools import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    STUDENT_TOOL_ARGUMENTS,
    AssistantToolHost,
    ToolExecution,
    execute_tool_reads,
)
from audentra.integrations.assistant.trace import AssistantTurnTrace
from audentra.integrations.assistant.university_catalog import policy_evidence_blocks

JsonDict = dict[str, Any]

ModelComposer = Callable[..., Awaitable[Mapping[str, Any] | None]]
ModelPlanner = Callable[..., Awaitable[Mapping[str, Any] | None]]
ReadLoopStep = Callable[..., Awaitable[Mapping[str, Any] | None]]

# Intents whose deterministic answer is the deliverable even when the model
# read loop is on: greetings and refusals read nothing, and a rewrite could
# only soften a boundary.
_LOOP_SKIP_REQUEST_TYPES = frozenset(
    {
        "greeting",
        "capability_overview",
        "conversational_ack",
        "assistant_identity",
        "unsupported_or_out_of_scope",
    }
)
# In hybrid mode the classifier keeps every confident, well-covered intent;
# the loop takes the turns that used to land on the broad safe fallback.
_LOOP_HYBRID_REQUEST_TYPES = frozenset({"general_question", "general_help"})

KNOWLEDGE_TOOL = "getInstitutionalPolicies"
# Language that asks about the institution's rules rather than (only) the
# student's own record: consequences, permissions, exceptions, amounts,
# calendar dates, who owns a step. A domain intent that carries it keeps its
# record reads and adds the approved-knowledge read, so "what happens if I
# miss the deposit deadline" answers from both the deposit's state and the
# deposit policy. Deliberately conservative: a bare status question never
# triggers it.
_POLICY_MARKERS = re.compile(
    r"\bwhat happens (?:if|when|now|after)\b|\bwhat (?:if|are the consequences)\b"
    r"|\bif i (?:don'?t|do not|can'?t|cannot|miss|forget|never|skip|fail)\b"
    r"|\bwill i (?:lose|still|get|have|be able|need)\b|\bappl(?:y|ies) to me\b"
    r"|\bwho do i (?:meet|see|book|go to)\b|\bwho covers\b|\bcovers? for\b"
    r"|\bdo i (?:still |even )?(?:have|need) to\b|\bhow long (?:does|do|will|should)\b"
    r"|\bfile (?:types?|formats?)\b|\bwhat does (?:that|this|it|my [a-z ]{0,20}status) mean\b"
    r"|\bprobation\b|\bsap\b|\bpay ?out\b|\bdisburs|\btransfer\b.{0,24}\bcredits?\b"
    r"|\bcredits?\b.{0,24}\btransfer\b|\bminimum grade\b|\bwhat happens to me\b"
    r"|\bleft the university\b|\bon leave\b|\bdeparted\b|\bferpa\b"
    r"|\b(?:mom|dad|mother|father|parents?|guardian|family)\b.{0,40}"
    r"\b(?:access|see|view)\b"
    r"|\b(?:access|see|view)\b.{0,40}"
    r"\b(?:mom|dad|mother|father|parents?|guardian)\b"
    r"|\bwhen (?:does|do|will|is|are)\b.{0,30}"
    r"\b(?:pay|disburs|due|open|close|start|begin|end|placed)\b"
    r"|\bstill have to\b|\bhave to live\b|\blast day to\b|\bfirst day of\b"
    r"|\boffer (?:gone|cancel|rescind|withdrawn|revoked|still (?:valid|good|stand))"
    r"|\bstill have a (?:place|spot|seat)\b|\bis my offer\b"
    r"|\bwho (?:fixes|deals with|takes care of)\b"
    r"|\b(?:adviser|advisor|counselor) (?:is|'s) (?:away|out|gone|on leave)\b|\bwho decides\b"
    r"|\bwhen did i need to\b|\bmedical reasons?\b|\bsingle room\b"
    r"|\bcredits?\b.{0,20}\b(?:drop|below|minimum|full[- ]time)\b"
    r"|\bpolic(?:y|ies)\b|\brules?\b|\bregulation|\bhandbook\b|\bprocedure\b"
    r"|\ballowed\b|\bpermitted\b|\bexempt|\bwaiv|\bpenalt|\blate fee\b|\bfine\b"
    r"|\brefund|\bforfeit|\bnon-?refundable\b|\bappeal|\bextension\b|\bextend\b"
    r"|\b(?:is|was) there a deadline\b|\bdeadline (?:for|to)\b|\bhow long do i have\b"
    r"|\bgrace period\b|\b(?:am i|are we|do i have to|must i|required to|do i need to)\b"
    r"|\beligib|\bqualif|\bwhat counts as\b|\bminimum\b|\bmaximum\b|\bhow many credits\b"
    r"|\bcan i (?:still|get|live|work|drop|defer|withdraw|appeal|change|switch|take|use|keep)\b"
    r"|\bcan (?:freshmen|freshman|first[- ]years?|transfers?|international students)\b"
    r"|\bhow much (?:is|does|do|are)\b|\bcost of\b|\btuition\b|\bfees?\b"
    r"|\bwhen (?:is|are|does|do|will) (?:the )?(?:orientation|move[- ]?in|classes|the term|"
    r"the semester|registration|add|drop|finals|break|commencement|tuition|bills?)\b"
    r"|\bwho (?:handles|owns|do i (?:contact|talk to|ask|email)"
    r"|should i (?:contact|talk to|ask|email)|"
    r"is responsible|decides|approves)\b|\bwhich office\b|\bwhere (?:is|do i go)\b"
    r"|\boffice hours\b|\bhours\b|\bcalendar\b|\bsyllabus\b|\bprerequisite|\bcurriculum\b"
    r"|\bmajor requirements?\b|\bdegree requirements?\b|\bgeneral education\b|\bgen ed\b",
    re.IGNORECASE,
)


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
        # The full-request coverage gate, `augment` by default: a confident
        # first-match classification is checked for asks it does not answer,
        # and each gap is closed deterministically. None defers to
        # AUDENTRA_ASSISTANT_COVERAGE_GATE, which a deployment or the eval
        # host can set to "off" to get plain first-match routing back.
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
        # The institutional-knowledge read searches by the question itself.
        self._host.question = request.resolved_text
        self._host.tool_arguments = {}
        if trace is not None:
            trace.user_message = request.text
            trace.page_path = request.page_path
            trace.page_label = request.page_label
            trace.history_messages = len(request.history)
            trace.set_history_preview(request.history)
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
        if trace is not None:
            trace.read_planner = self._read_planner.value
        # The model read loop (hybrid: the turns the classifier could not
        # place; model: every readable turn). A loop that answers returns
        # here; one that fails or is guard-rejected falls through to the
        # deterministic route with the reason recorded.
        if (
            self._read_loop_step is not None
            # Action recognition/confirmation already ran in the service. A
            # question about a past cancellation is still a safe university read.
            and (not request.is_mutation_request or self._host.supports("university_record"))
            and (
                self._loop_applies(classification)
                or (
                    self._host.supports("university_record")
                    and self._read_planner is not ReadPlanner.DETERMINISTIC
                )
            )
        ):
            loop_response = await self._run_read_loop(request, classification, failure_codes, trace)
            if loop_response is not None:
                return loop_response

        if self._host.supports("university_record"):
            message = (
                "University record guidance is temporarily unavailable. "
                "You can review your current records in the portal."
            )
            if trace is not None:
                trace.response_source = "university_planner_unavailable"
                trace.failure_codes = ["university_planner_required"]
                trace.final_message = message
            return AssistantPipelineResult(
                message=message,
                blocks=[{"type": "text", "text": message, "fallbackText": message}],
                provider="deterministic",
                model=None,
                usage=None,
                suggested_actions=[],
                context_receipts=[],
                classification=classification or Classification("unknown", 0.5),
                derived=derive_student_state(ToolExecution()),
                failure_codes=["university_planner_required"],
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
        knowledge_augmented = False
        if (
            KNOWLEDGE_TOOL not in selected
            and self._host.supports("institution_knowledge")
            and classification.request_type not in _LOOP_SKIP_REQUEST_TYPES
            and not request.is_mutation_request
            and _POLICY_MARKERS.search(request.resolved_text)
        ):
            selected = [*selected, KNOWLEDGE_TOOL]
            knowledge_augmented = True
        if trace is not None:
            if knowledge_augmented:
                trace.add_stage("policy_augment", 0.0, tool=KNOWLEDGE_TOOL, reason="policy_markers")
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

        # Advising is context on the support routes; when that read is not
        # available (some hosts never expose it) the answer must not become
        # an apology about the adviser record.
        _prune_context_unavailability(state, classification)
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
        # The composer reasons about "this week", "before Friday" and "already
        # passed" only if it knows today; the draft's facts never carry it.
        draft = ComposedAnswer(
            message=draft.message,
            blocks=draft.blocks,
            evidence_texts=[_today_line(self._now()), *draft.evidence_texts],
        )
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

    def _loop_applies(self, classification: Classification | None) -> bool:
        if self._read_planner is ReadPlanner.DETERMINISTIC:
            return False
        if classification is None:
            return True
        if classification.request_type in _LOOP_SKIP_REQUEST_TYPES:
            return False
        if self._read_planner is ReadPlanner.MODEL:
            return True
        return (
            classification.source == "safe_fallback"
            or classification.request_type in _LOOP_HYBRID_REQUEST_TYPES
        )

    async def _run_read_loop(
        self,
        request: Any,
        classification: Classification | None,
        failure_codes: list[str],
        trace: AssistantTurnTrace | None,
    ) -> AssistantPipelineResult | None:
        """Model-planned reads over the student's own record, guarded.

        Student tools take no arguments and read only the signed-in student,
        so the loop's identity discipline is trivially satisfied: the host
        binds the student before any tool exists. The model chooses *which*
        records to read and writes the answer from them; the claim guard
        checks that answer against the flattened results exactly as it checks
        a rewrite against deterministic evidence.
        """

        assert self._read_loop_step is not None
        stage_started = time.perf_counter()
        execution = ToolExecution()
        host = self._host
        timeout = self._tool_timeout_seconds
        moment = self._now()

        async def executor(calls: Sequence[LoopCall]) -> None:
            # Sequential execution preserves each call's argument/evidence pairing,
            # including two refinements of the same policy search in one round.
            for call in calls:
                if call.status != "planned":
                    continue
                if call.tool in STUDENT_TOOL_ARGUMENTS:
                    allowed = STUDENT_TOOL_ARGUMENTS[call.tool]
                    host.tool_arguments[call.tool] = {
                        str(name): value
                        for name, value in dict(call.arguments or {}).items()
                        if name in allowed and value is not None
                    }
                    execution.reads.pop(call.tool, None)
                if call.tool not in execution.reads:
                    fresh = await execute_tool_reads(
                        [call.tool],
                        host,
                        timeout_seconds=timeout,
                        now=moment,
                        receipt_offset=len(execution.receipts),
                    )
                    execution.reads.update(fresh.reads)
                    execution.receipts.extend(fresh.receipts)
                    execution.executed_tools.extend(fresh.executed_tools)
                    execution.unavailable_data.extend(fresh.unavailable_data)
                read = execution.reads.get(call.tool)
                if read is None:
                    call.status, call.reason = "unavailable", "not_supported"
                    continue
                call.status = str(read.get("status", "unavailable"))
                call.reason = read.get("reason")
                call.result = read.get("data")
                call.duration_ms = read.get("durationMs")

        from audentra.integrations.assistant.university_catalog import (
            UNIVERSITY_CONTEXT,
            UNIVERSITY_TOOLS,
        )

        tools = [
            LoopTool(
                name=name,
                description=description,
                arguments=(
                    "; ".join(
                        f"{arg}: {text}" for arg, text in STUDENT_TOOL_ARGUMENTS[name].items()
                    )
                    if name in STUDENT_TOOL_ARGUMENTS
                    else "none"
                ),
            )
            for name, description in TOOL_DESCRIPTIONS.items()
            if (name != KNOWLEDGE_TOOL or host.supports("institution_knowledge"))
            and (name not in UNIVERSITY_TOOLS or host.supports("university_record"))
            and (
                not host.supports("university_record")
                or name in UNIVERSITY_TOOLS
                or name
                in {
                    KNOWLEDGE_TOOL,
                    "getStudentProfile",
                    "getOnboardingChecklist",
                    "getStudentAdvising",
                    "getStudentMessages",
                    "getStudentSupportRequests",
                    "getCampusLife",
                    "getSupportOptions",
                }
            )
        ]
        context = {
            "university": UNIVERSITY_CONTEXT if host.supports("university_record") else None,
            "actor": "student",
            "today": moment.date().isoformat(),
            "page": {"path": request.page_path, "label": request.page_label},
            "institutionalHint": (
                "This question uses institutional language (a rule, a consequence, an "
                "amount, a date, an office). Read getInstitutionalPolicies in the first "
                "step, alongside the student's own record, and answer from both."
                if host.supports("institution_knowledge")
                and _POLICY_MARKERS.search(request.resolved_text)
                else None
            ),
            "identity": (
                "Every tool reads the signed-in student's own record; no tool takes an "
                "identity argument. getInstitutionalPolicies accepts an optional `query` — "
                "use it for questions about rules, deadlines, consequences, amounts, "
                "calendar dates, exemptions or which office handles something, and read "
                "the student's own record alongside it when the question is about them."
            ),
        }
        result: ReadLoopResult = await run_read_loop(
            question=request.resolved_text,
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
                    arguments=dict(call.arguments or {}),
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
        state = derive_student_state(execution)
        verdict = None
        if result.answer:
            disbursements = (state.financial_aid or {}).get("disbursements")
            verdict = guard_grounded_answer(
                answer=result.answer,
                evidence_texts=result.evidence_texts,
                causal_guards=build_causal_guards(
                    registration_gates=state.registration_gates or None,
                    housing_gates=(
                        (state.housing_eligibility or {}).get("gates")
                        if state.housing_eligibility
                        else None
                    ),
                    disbursement_gates=(disbursements or {}).get("gates")
                    if disbursements
                    else None,
                ),
                document_states=state.document_states,
                no_official_holds=(
                    "getEnrollmentHolds" in state.available_reads and not state.official_holds
                ),
                unavailable_sources=[str(item.get("source")) for item in state.unavailable_data],
                deposit_payment_pending=bool((state.account or {}).get("depositPaymentPending")),
            )
        accepted = verdict is not None and verdict.accepted
        # A bare verdict ("No, you cannot.") after reading a policy is not an
        # answer a student can act on; the deterministic route carries the
        # rule, so hand it the turn.
        if (
            accepted
            and verdict is not None
            and result.answer
            and len(result.answer.strip()) < 80
            and re.match(r"^\s*(?:yes|no)\b", result.answer, re.IGNORECASE)
            and any(call.tool == KNOWLEDGE_TOOL for call in result.calls)
        ):
            accepted = False
            verdict = replace(verdict, accepted=False, reason_code="too_terse")
        loop_trace = {
            "rounds": result.rounds,
            "outcome": result.outcome,
            "reads": [call.tool for call in result.calls],
            "reasoning": list(result.reasoning),
            "steps": list(result.steps),
            "guard": (
                "accepted"
                if accepted
                else (verdict.reason_code if verdict is not None else result.outcome)
            ),
        }
        if result.answer and not accepted:
            loop_trace["ungrounded"] = ungrounded_tokens(result.answer, result.evidence_texts)
            loop_trace["rejectedAnswer"] = result.answer[:600]
        if trace is not None:
            trace.read_loop = loop_trace
            trace.add_stage(
                "read_loop",
                (time.perf_counter() - stage_started) * 1_000,
                rounds=result.rounds,
                outcome=loop_trace["guard"],
            )
        if not accepted:
            failure_codes.append(f"read_loop_fallback:{loop_trace['guard']}")
            if host.supports("university_record"):
                from audentra.integrations.assistant.university_catalog import (
                    university_read_failure,
                )

                message, response_source = university_read_failure(str(loop_trace["guard"]))
                if trace is not None:
                    trace.response_source = response_source
                    trace.failure_codes = list(failure_codes)
                    trace.final_message = message
                return AssistantPipelineResult(
                    message=message,
                    blocks=[{"type": "text", "text": message, "fallbackText": message}],
                    provider="deterministic",
                    model=None,
                    usage=_sum_usage(result.model_calls),
                    suggested_actions=[],
                    context_receipts=[],
                    classification=classification or Classification("unknown", 0.5),
                    derived=state,
                    failure_codes=failure_codes,
                )
            return None
        assert verdict is not None
        last_call = next(
            (entry for entry in reversed(result.model_calls) if entry.get("model")), None
        )
        usage_total = _sum_usage(result.model_calls)
        resolved = classification or Classification(
            _intent_for_reads([call.tool for call in result.calls]), 0.6, source="model_loop"
        )
        if trace is not None:
            trace.classification = {
                "requestType": resolved.request_type,
                "confidence": resolved.confidence,
                "source": resolved.source,
                "additionalRequestTypes": list(resolved.additional_request_types),
                "requirementReference": resolved.requirement_reference,
            }
            trace.tool_selection_source = "model_loop"
            trace.selected_tools = list(dict.fromkeys(call.tool for call in result.calls))
            trace.evidence = list(result.evidence_texts[:48])
            trace.provider = str((last_call or {}).get("provider") or "openai")
            trace.model = (last_call or {}).get("model")
            trace.usage = usage_total
            trace.response_source = "model_loop"
            trace.failure_codes = list(failure_codes)
            trace.final_message = verdict.answer
        return AssistantPipelineResult(
            message=verdict.answer,
            blocks=[
                {"type": "text", "fallbackText": verdict.answer, "text": verdict.answer},
                *policy_evidence_blocks(result.calls),
            ],
            provider=str((last_call or {}).get("provider") or "openai"),
            model=(last_call or {}).get("model"),
            usage=usage_total,
            suggested_actions=list(state.suggested_actions),
            context_receipts=[
                {"source": receipt["source"]} for receipt in _unique_sources(execution.receipts)
            ],
            classification=resolved,
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


# The read that names a loop turn's intent, for traces and evals that key on
# request types: the first tool the model chose owns the question.
_INTENT_BY_READ: Mapping[str, str] = {
    "getCampusLife": "campus_life",
    "getDocumentStatuses": "document_status",
    "getStudentDeadlines": "deadlines",
    "getEnrollmentHolds": "holds_and_blockers",
    "getOnboardingChecklist": "remaining_steps",
    "getEnrollmentState": "enrollment_state",
    "getStudentAdvising": "appointments",
    "getStudentAppointments": "appointments",
    "getStudentAccountSummary": "student_account",
    "getFinancialAidStatus": "aid_status",
    "getFinancialAidSummary": "aid_summary",
    "getAidDisbursements": "aid_disbursement",
    "getStudentHousingStatus": "housing_status",
    "getStudentHousingEligibility": "housing_eligibility",
    "getRegistrationStatus": "registration_status",
    "getAcademicStanding": "academic_standing",
    "getAcademicPlan": "academic_plan",
    "getStudentMessages": "messages_unread",
    "getStudentProfile": "personal_information",
    "getOnboardingResponses": "personal_information",
    "getStudentSupportRequests": "support_requests",
    "getSupportOptions": "request_support",
}


def _intent_for_reads(tools: Sequence[str]) -> str:
    for tool in tools:
        if tool in _INTENT_BY_READ:
            return _INTENT_BY_READ[tool]
    return "general_question"


# Reads that are context on some routes: their unavailability is not the
# answer there ("how do I get help from a real person" names support, not the
# adviser record).
_CONTEXT_ONLY_READS: Mapping[str, frozenset[str]] = {
    "getStudentAdvising": frozenset(
        {"request_support", "general_help", "support", "housing_support", "aid_support"}
    ),
}


def _prune_context_unavailability(state: DerivedState, classification: Classification) -> None:
    state.unavailable_data = [
        item
        for item in state.unavailable_data
        if classification.request_type
        not in _CONTEXT_ONLY_READS.get(str(item.get("source")), frozenset())
    ]


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


def _today_line(now: datetime) -> str:
    return (
        f"Today is {now:%A %d %B %Y} (ISO {now:%Y-%m-%d}); dates before this have already passed."
    )
