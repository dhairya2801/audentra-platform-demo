"""Per-request Edward execution mode — the Lab's zero-LLM comparison control.

Edward is deterministic-first: classification, tool selection, derivation, and
composition all run without a model, and the model's only two jobs are the
optional tool planner and the optional prose rewrite. `deterministic` mode
removes both for exactly one request, so the Lab can ask the same question of
the same state twice and see what the model actually contributed.

Boundaries this module enforces:

- The mode is a *development and evaluation* control. It is honoured only
  where assistant trace debugging is already enabled, and never in a
  production environment, independent of how settings were composed.
- Where the control is not honoured, an inbound mode header is ignored rather
  than rejected: the turn takes the ordinary production path and nothing about
  the response changes. The requested-but-ignored value is still recorded on
  the turn trace so a misconfigured environment is visible in observability
  instead of silently pretending to be deterministic.
- Where the control *is* honoured, an unrecognised value is a 400 so a Lab
  typo fails loudly instead of quietly grading the wrong execution path.

This module owns the student/staff assistant execution mode only. Other
experimental request-scoped modes must not be folded into this header.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from .errors import BadRequestError

HookT = TypeVar("HookT")

#: The HTTP header the Lab sends. Lower-case because header lookup is
#: case-insensitive and every comparison in this module is done lower-cased.
ASSISTANT_EXECUTION_MODE_HEADER = "x-edward-mode"


class AssistantExecutionMode(StrEnum):
    """How one Edward turn is allowed to execute."""

    #: Production behaviour: the model planner and prose composer may run.
    DEFAULT = "default"
    #: Lab/eval only: no provider or model call of any kind for this turn.
    DETERMINISTIC = "deterministic"

    @property
    def allows_model_calls(self) -> bool:
        return self is AssistantExecutionMode.DEFAULT


class ReadPlanner(StrEnum):
    """Who plans the reads for a question the safety gates let through.

    ``deterministic`` is the regex classifier with its static tool table and
    the model planner as the fallback; ``hybrid`` keeps the classifier for
    confident, well-covered intents and hands everything else to the model
    read loop; ``model`` sends every readable question through the loop. The
    deployment default comes from ``EDWARD_READ_PLANNER``; the Lab header
    ``x-edward-read-planner`` overrides it per request where Lab controls are
    honoured, so one host can serve an A/B.
    """

    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"
    MODEL = "model"


READ_PLANNER_HEADER = "x-edward-read-planner"


def resolve_read_planner(raw: str | None, *, default: str | None = None) -> ReadPlanner:
    """The read planner named by a header/env value, else the deployment default."""

    for candidate in (raw, default):
        value = (candidate or "").strip().lower()
        if value:
            try:
                return ReadPlanner(value)
            except ValueError:
                continue
    return ReadPlanner.DETERMINISTIC


@dataclass(frozen=True, slots=True)
class ResolvedAssistantExecutionMode:
    """The effective mode, plus the raw request when it was not honoured."""

    mode: AssistantExecutionMode
    #: The raw header value when it asked for something this environment
    #: refused to apply; `None` whenever the effective mode is what was asked.
    ignored_request: str | None = None
    #: The Lab's per-request read-planner choice; `None` means "the
    #: deployment default" and is what every non-Lab request carries.
    read_planner: ReadPlanner | None = None

    @property
    def allows_model_calls(self) -> bool:
        return self.mode.allows_model_calls


#: What every non-Lab request carries: ordinary production execution.
DEFAULT_ASSISTANT_EXECUTION = ResolvedAssistantExecutionMode(AssistantExecutionMode.DEFAULT)


def resolve_assistant_execution_mode(
    raw: str | None,
    *,
    lab_controls_enabled: bool,
    read_planner_raw: str | None = None,
) -> ResolvedAssistantExecutionMode:
    """Resolve the inbound mode header into the mode this turn may use.

    `lab_controls_enabled` must already combine the deployment environment
    with the trace-debug switch; see `lab_execution_controls_enabled`. The
    read-planner header follows the same rule: honoured only where Lab
    controls are, ignored (never rejected) elsewhere.
    """

    value = (raw or "").strip().lower()
    planner: ReadPlanner | None = None
    planner_value = (read_planner_raw or "").strip().lower()
    if planner_value and lab_controls_enabled:
        try:
            planner = ReadPlanner(planner_value)
        except ValueError as error:
            raise BadRequestError(
                "INVALID_ASSISTANT_READ_PLANNER",
                "Edward read planner must be deterministic, hybrid or model",
            ) from error
    if not value or value == AssistantExecutionMode.DEFAULT:
        if planner is None:
            return DEFAULT_ASSISTANT_EXECUTION
        return ResolvedAssistantExecutionMode(AssistantExecutionMode.DEFAULT, read_planner=planner)
    if not lab_controls_enabled:
        # Ignored, not rejected: an unexpected header must never change how a
        # deployed turn behaves, and must never reveal that the control exists.
        return ResolvedAssistantExecutionMode(AssistantExecutionMode.DEFAULT, value)
    try:
        mode = AssistantExecutionMode(value)
    except ValueError as error:
        raise BadRequestError(
            "INVALID_ASSISTANT_EXECUTION_MODE",
            "Edward execution mode must be default or deterministic",
        ) from error
    return ResolvedAssistantExecutionMode(mode, read_planner=planner)


def model_hook(mode: AssistantExecutionMode, hook: HookT) -> HookT | None:
    """The model planner or composer this turn may use.

    Every assistant host builds its pipeline through this call, so the single
    place a turn can acquire a model hook is also the single place the mode is
    enforced: `deterministic` hands the pipeline `None` for both, and a
    pipeline with neither hook has no code path that reaches a provider.
    """

    return hook if mode.allows_model_calls else None


def lab_execution_controls_enabled(
    *, environment: str, assistant_trace_debug_enabled: bool
) -> bool:
    """Whether request-scoped Lab execution controls may be honoured here.

    Both conditions are required, and the environment check is independent of
    settings composition so a hand-built production `HttpSettings` with trace
    debugging switched on still cannot open the control up.
    """

    return assistant_trace_debug_enabled and environment != "production"
