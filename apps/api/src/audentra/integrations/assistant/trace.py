"""Per-turn assistant execution traces.

One `AssistantTurnTrace` is created per Edward turn by the hosting service,
threaded through `AssistantPipeline.execute`, and recorded when the turn
finishes. The trace answers "why did Edward give this answer?" in one place:
which gate or planner chose the intent, which tools ran with what latency and
result, what the model was asked to do, why its prose was accepted or
rejected, and what the student finally received.

Boundaries:

- The trace ID is the request ID the middleware already issues and echoes in
  `x-request-id`, so a browser network tab, an `assistant_message` row, an
  `ai_provider_response_attempt` row, and this trace all join on one value.
- Recorded tool results are sanitized copies: strings are bounded, long lists
  are truncated, and value keys that commonly carry contact or credential
  material are redacted. Provider credentials never appear because tools and
  model calls never receive them.
- Traces are held in a bounded in-process ring buffer, emitted as structured
  logs, and the hosting service stores this same sanitized representation in
  `assistant_turn_trace` for feedback drill-down after the buffer rolls over.
  Durable provider-call telemetry remains `ai_provider_response_attempt`;
  durable conversation state remains the assistant conversation/message tables.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

logger = logging.getLogger("audentra.assistant.trace")

JsonDict = dict[str, Any]

MAX_TRACE_STRING_CHARACTERS = 600
MAX_TRACE_LIST_ITEMS = 24
MAX_TRACE_DEPTH = 6
DEFAULT_TRACE_BUFFER_SIZE = 200

# Value keys whose contents are contact, identity, or credential material.
# The record-shaped diagnostics keep structure and counts; these values are
# replaced so a trace can be shared in a bug report without leaking them.
_REDACTED_KEY_FRAGMENTS = (
    "email",
    "phone",
    "password",
    "secret",
    "token",
    "apikey",
    "api_key",
    "authorization",
    "ssn",
    "socialsecurity",
    "accountnumber",
    "routingnumber",
)


def sanitize_trace_value(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact one tool result or model payload for the trace."""

    if depth >= MAX_TRACE_DEPTH:
        return "…"
    if isinstance(value, str):
        if len(value) <= MAX_TRACE_STRING_CHARACTERS:
            return value
        return value[:MAX_TRACE_STRING_CHARACTERS] + "…"
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Mapping):
        sanitized: JsonDict = {}
        for key, item in list(value.items())[:MAX_TRACE_LIST_ITEMS]:
            key_text = str(key)
            lowered = key_text.replace("-", "").replace("_", "").lower()
            if any(fragment in lowered for fragment in _REDACTED_KEY_FRAGMENTS):
                sanitized[key_text] = "[redacted]"
            else:
                sanitized[key_text] = sanitize_trace_value(item, depth=depth + 1)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        items = [
            sanitize_trace_value(item, depth=depth + 1) for item in value[:MAX_TRACE_LIST_ITEMS]
        ]
        if len(value) > MAX_TRACE_LIST_ITEMS:
            items.append(f"… {len(value) - MAX_TRACE_LIST_ITEMS} more")
        return items
    return sanitize_trace_value(str(value), depth=depth)


@dataclass
class AssistantTurnTrace:
    """Everything observable about one Edward turn, in execution order."""

    trace_id: str
    tenant_id: str | None = None
    student_id: str | None = None
    conversation_id: str | None = None
    # Which assistant produced this turn and who asked. The student pipeline
    # leaves the defaults; the staff pipeline sets assistant_kind="staff",
    # actor_type="staff", staff_member_id=<actor>, and uses student_id for
    # the resolved student *referent* of the turn (if any).
    assistant_kind: str = "student"
    actor_type: str = "student"
    staff_member_id: str | None = None
    input_mode: str = "text"
    user_message: str = ""
    page_path: str | None = None
    page_label: str | None = None
    history_messages: int = 0
    # "server" = durable conversation store; "client_fallback" = request body
    # (first conversation-less turn only); "none" = no prior context.
    history_source: str = "none"
    # The prior turns the pipeline actually handed the model, bounded and
    # sanitized: role plus the opening of each message. A follow-up answered
    # from history alone has no other input worth inspecting.
    history_preview: list[JsonDict] = field(default_factory=list)
    # "pipeline" is the normal path; "pre_pipeline_safety_gate" is a guarded
    # refusal before the pipeline ran; "idempotent_replay" returned a stored
    # exchange without executing anything.
    path: str = "pipeline"
    # How this turn was allowed to execute. "default" is production. The Lab's
    # "deterministic" mode removes the model planner and the prose composer for
    # one turn, so `modelCalls` must be empty whenever it appears here.
    execution_mode: str = "default"
    # A mode a caller asked for that this environment refused to honour. Set
    # only where the Lab control is disabled, and it never changes execution —
    # its purpose is to make an ignored control visible instead of silent.
    ignored_execution_mode_request: str | None = None
    stages: list[JsonDict] = field(default_factory=list)
    classification: JsonDict | None = None
    tool_selection_source: str | None = None
    selected_tools: list[str] = field(default_factory=list)
    tool_calls: list[JsonDict] = field(default_factory=list)
    model_calls: list[JsonDict] = field(default_factory=list)
    # Populated when the bounded dependency round ran: which open gates
    # triggered it and which verifying reads it fetched.
    second_read: JsonDict | None = None
    # Which read planner the turn used (deterministic / hybrid / model) and,
    # when the model read loop ran, its rounds, outcome, reasoning notes and
    # what the answer fell back to if the guard rejected it.
    read_planner: str | None = None
    read_loop: JsonDict | None = None
    # Staff turns: who is signed in (role, component, team) and how every
    # name in the message resolved (staff / student / department / ambiguous).
    identity: JsonDict | None = None
    entities: JsonDict | None = None
    # The deterministic evidence sentences the composer offered the model and
    # the claim guard checked prose against — the facts Edward reasoned from.
    # Already student-safe by construction (they are rendered into answers);
    # bounded here for the trace. Never chain-of-thought.
    evidence: list[str] = field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    usage: JsonDict | None = None
    response_source: str | None = None
    failure_codes: list[str] = field(default_factory=list)
    # Action-plane observability. These are server decisions, never model
    # chain-of-thought: requested semantic action, policy result, immutable
    # intent identity and the typed provenance shown in its preview.
    action_requested: str | None = None
    # Which recognition stage produced (or declined) the request: "pattern",
    # "continuation", "model", "model_none" (tier 1 ran and found nothing),
    # or None when no recognition stage was consulted at all. The suites need
    # this to attribute a recognition to a tier — a tier invisible in the
    # trace is a tier whose regressions cannot be attributed.
    action_recognition_source: str | None = None
    action_proposed: str | None = None
    action_policy_result: str | None = None
    action_denial_reason: str | None = None
    action_intent_id: str | None = None
    action_confirmation_mode: str | None = None
    action_authorization_capability: str | None = None
    action_blast_radius: int | None = None
    action_provenance: list[JsonDict] = field(default_factory=list)
    action_receipt: JsonDict | None = None
    action_execution_result: str | None = None
    action_latency_ms: int | None = None
    final_message: str = ""
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    error: str | None = None
    started_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds")
    )
    duration_ms: int | None = None
    _started_clock: float = field(default_factory=time.perf_counter, repr=False)

    def started_from(self, clock: float) -> None:
        """Re-anchor the turn clock to work that ran before this trace existed.

        The hosting service recognizes actions and consults the model tier
        before it knows whether a turn needs a conversation, so the trace is
        created after that work. Without this the gated paths report 0 ms.
        """

        elapsed = max(0.0, time.perf_counter() - clock)
        self._started_clock = clock
        self.started_at = datetime.fromtimestamp(
            datetime.now(UTC).timestamp() - elapsed, UTC
        ).isoformat(timespec="milliseconds")

    def set_history_preview(
        self, history: Sequence[Mapping[str, Any]], *, limit: int = 8, characters: int = 280
    ) -> None:
        """Keep the tail of the history the model saw, bounded for the trace."""

        tail = list(history)[-limit:]
        self.history_preview = [
            {
                "role": str(entry.get("role") or ""),
                "content": (
                    str(entry.get("content") or "")[:characters]
                    + ("…" if len(str(entry.get("content") or "")) > characters else "")
                ),
            }
            for entry in tail
            if isinstance(entry, Mapping)
        ]

    def add_stage(self, name: str, duration_ms: float, **details: Any) -> None:
        self.stages.append(
            {
                "stage": name,
                "durationMs": round(max(0.0, duration_ms)),
                **{key: value for key, value in details.items() if value is not None},
            }
        )

    def add_tool_call(
        self,
        *,
        tool: str,
        status: str,
        duration_ms: float | None,
        record_count: int | None = None,
        reason: str | None = None,
        result: Any = None,
        round_name: str = "initial",
        arguments: Any = None,
        validation: str | None = None,
        model_result: Any = None,
    ) -> None:
        entry: JsonDict = {"tool": tool, "status": status, "round": round_name}
        if duration_ms is not None:
            entry["durationMs"] = round(max(0.0, duration_ms))
        if record_count is not None:
            entry["recordCount"] = record_count
        if reason is not None:
            entry["reason"] = reason
        if result is not None:
            entry["result"] = sanitize_trace_value(result)
        if model_result is not None:
            entry["modelResult"] = sanitize_trace_value(model_result)
        # Staff tools take validated arguments; recording them (sanitized)
        # plus the validation outcome makes every read auditable.
        if arguments is not None:
            entry["arguments"] = sanitize_trace_value(arguments)
        if validation is not None:
            entry["validation"] = validation
        self.tool_calls.append(entry)

    def add_model_call(
        self,
        *,
        operation: str,
        attempt: int,
        duration_ms: float,
        outcome: str,
        provider: str | None = None,
        model: str | None = None,
        usage: Mapping[str, Any] | None = None,
        detail: str | None = None,
    ) -> None:
        entry: JsonDict = {
            "operation": operation,
            "attempt": attempt,
            "durationMs": round(max(0.0, duration_ms)),
            "outcome": outcome,
        }
        if provider is not None:
            entry["provider"] = provider
        if model is not None:
            entry["model"] = model
        if usage is not None:
            entry["usage"] = dict(usage)
        if detail is not None:
            entry["detail"] = detail
        self.model_calls.append(entry)

    def finalize(self) -> None:
        if self.duration_ms is None:
            self.duration_ms = round((time.perf_counter() - self._started_clock) * 1_000)

    def to_dict(self) -> JsonDict:
        self.finalize()
        return {
            "traceId": self.trace_id,
            "tenantId": self.tenant_id,
            "studentId": self.student_id,
            "conversationId": self.conversation_id,
            "assistantKind": self.assistant_kind,
            "actorType": self.actor_type,
            "staffMemberId": self.staff_member_id,
            "inputMode": self.input_mode,
            "path": self.path,
            "executionMode": self.execution_mode,
            "ignoredExecutionModeRequest": self.ignored_execution_mode_request,
            "userMessage": sanitize_trace_value(self.user_message),
            "pagePath": self.page_path,
            "pageLabel": self.page_label,
            "historyMessages": self.history_messages,
            "historySource": self.history_source,
            "historyPreview": [sanitize_trace_value(entry) for entry in self.history_preview],
            "classification": self.classification,
            "toolSelectionSource": self.tool_selection_source,
            "selectedTools": list(self.selected_tools),
            "toolCalls": list(self.tool_calls),
            "secondRead": self.second_read,
            "readPlanner": self.read_planner,
            "readLoop": self.read_loop,
            "identity": self.identity,
            "entities": self.entities,
            "evidence": [sanitize_trace_value(line) for line in self.evidence[:48]],
            "modelCalls": list(self.model_calls),
            "modelIterations": len(self.model_calls),
            "provider": self.provider,
            "model": self.model,
            "usage": self.usage,
            "responseSource": self.response_source,
            "failureCodes": list(self.failure_codes),
            "actionRequested": self.action_requested,
            "actionRecognitionSource": self.action_recognition_source,
            "actionProposed": self.action_proposed,
            "actionPolicyResult": self.action_policy_result,
            "actionDenialReason": self.action_denial_reason,
            "actionIntentId": self.action_intent_id,
            "actionConfirmationMode": self.action_confirmation_mode,
            "actionAuthorizationCapability": self.action_authorization_capability,
            "actionBlastRadius": self.action_blast_radius,
            "actionProvenance": sanitize_trace_value(self.action_provenance),
            "actionReceipt": sanitize_trace_value(self.action_receipt),
            "actionExecutionResult": self.action_execution_result,
            "actionLatencyMs": self.action_latency_ms,
            "finalMessage": sanitize_trace_value(self.final_message),
            "userMessageId": self.user_message_id,
            "assistantMessageId": self.assistant_message_id,
            "stages": list(self.stages),
            "error": self.error,
            "startedAt": self.started_at,
            "durationMs": self.duration_ms,
        }


class AssistantTraceRecorder:
    """Bounded in-process trace store plus one JSON log line per turn.

    The ring buffer serves the developer debug endpoint; the log line is the
    machine-readable record for any log pipeline the process already has.
    Recording failures never affect the student-facing response.
    """

    def __init__(self, buffer_size: int = DEFAULT_TRACE_BUFFER_SIZE) -> None:
        self._traces: deque[JsonDict] = deque(maxlen=max(1, buffer_size))
        self._lock = threading.Lock()

    def record(self, trace: AssistantTurnTrace) -> None:
        try:
            payload = trace.to_dict()
        except Exception:  # pragma: no cover - defensive
            logger.exception("assistant trace serialization failed")
            return
        self.record_payload(payload)

    def record_payload(self, payload: Mapping[str, Any]) -> None:
        """Record an already serialized trace for durable-store parity."""

        owned_payload = dict(payload)
        with self._lock:
            self._traces.append(owned_payload)
        try:
            logger.info(
                "assistant_turn %s",
                json.dumps(owned_payload, ensure_ascii=False, default=str),
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("assistant trace logging failed")

    def get(self, trace_id: str) -> JsonDict | None:
        with self._lock:
            for payload in reversed(self._traces):
                if payload.get("traceId") == trace_id:
                    return payload
        return None

    def merge_payload(self, trace_id: str, updates: Mapping[str, Any]) -> bool:
        """Merge later action-plane facts into the original turn trace."""

        safe_updates = cast(JsonDict, sanitize_trace_value(updates))
        with self._lock:
            for payload in reversed(self._traces):
                if payload.get("traceId") == trace_id:
                    payload.update(safe_updates)
                    return True
        return False

    def list(self, limit: int = 50) -> list[JsonDict]:
        """Newest first, summaries only, so the index stays readable."""

        bounded = max(1, min(limit, 200))
        with self._lock:
            recent = list(self._traces)[-bounded:]
        return [
            {
                "traceId": payload.get("traceId"),
                "startedAt": payload.get("startedAt"),
                "path": payload.get("path"),
                "executionMode": payload.get("executionMode", "default"),
                "assistantKind": payload.get("assistantKind", "student"),
                "inputMode": payload.get("inputMode"),
                "conversationId": payload.get("conversationId"),
                "studentId": payload.get("studentId"),
                # Already sanitized/bounded at record time; trimmed for the list.
                "userMessage": str(payload.get("userMessage") or "")[:96],
                "requestType": (payload.get("classification") or {}).get("requestType"),
                "toolSelectionSource": payload.get("toolSelectionSource"),
                "readPlanner": payload.get("readPlanner"),
                "executedTools": [call.get("tool") for call in payload.get("toolCalls", [])],
                "modelIterations": payload.get("modelIterations"),
                "responseSource": payload.get("responseSource"),
                "failureCodes": payload.get("failureCodes"),
                "actionRequested": payload.get("actionRequested"),
                "actionProposed": payload.get("actionProposed"),
                "actionPolicyResult": payload.get("actionPolicyResult"),
                "actionExecutionResult": payload.get("actionExecutionResult"),
                "durationMs": payload.get("durationMs"),
            }
            for payload in reversed(recent)
        ]


_default_recorder = AssistantTraceRecorder()


def get_assistant_trace_recorder() -> AssistantTraceRecorder:
    """The process-wide recorder shared by services and the debug endpoint."""

    return _default_recorder
