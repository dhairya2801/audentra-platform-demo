"""Staff Edward: the read-only staff assistant pipeline.

A distinct implementation beside the student assistant
(`audentra.integrations.assistant`) rather than a fork of it, because the two
differ on policy, not plumbing: staff questions refer to arbitrary students in
the authenticated tenant, staff tools take validated arguments, and the
student safety rule that forbids cross-student access is exactly the thing a
staff assistant must permit. Generic infrastructure (trace schema, block
types, grounding-token helpers) is imported from the student package; every
policy surface (classification, safety, tools, composition, guard) is owned
here.

V1 is read-only end to end. No tool in this package can mutate state, and the
data layer it reads through (`PostgresStaffAssistantRepository` plus pure-read
staff repository methods) contains no writes other than the assistant's own
conversation transcript.
"""

from audentra.integrations.staff_assistant.pipeline import (
    StaffAssistantPipeline,
    StaffAssistantPipelineResult,
)

__all__ = ["StaffAssistantPipeline", "StaffAssistantPipelineResult"]
