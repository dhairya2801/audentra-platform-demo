"""Edward's grounded assistant pipeline.

Ported from VV_Edgent-voice `packages/student-assistant-core` (TypeScript) into
platform idioms. The pipeline is deterministic-first: safety and conversational
gates settle before any model call, tool reads are repository reads executed at
question time (never cached), and every model-written answer is re-checked
against the evidence by a deterministic claim guard before a student sees it.

Ported from student-assistant-core; trimmed: approved-policy retrieval and
policy search (the platform has no policy corpus yet — those reads report
honestly unavailable), the academic-calendar read, LiveKit voice sessions, and
the model tool-planner (classification is deterministic-first with a model
fallback through the existing gateway).
"""

from audentra.integrations.assistant.pipeline import AssistantPipeline, AssistantPipelineResult

__all__ = ["AssistantPipeline", "AssistantPipelineResult"]
