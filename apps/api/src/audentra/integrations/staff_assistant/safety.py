"""Deterministic Staff Edward safety policy.

Deliberately a separate module from the student policy
(`audentra.integrations.ai.edward_safety`): the student rule that blocks
cross-student access is exactly what a staff assistant must permit inside the
authenticated tenant. What stays forbidden here:

- anything outside the tenant (cross-tenant reads or enumeration),
- system-prompt/credential/secret extraction and authority bypass,
- code execution and malicious-code requests,
- forging record state from chat (the read-only boundary's hard edge).

Action *requests* ("send Maria an email") are not handled here — they get a
helpful read-only explanation from classification/composition, not a canned
refusal.
"""

from __future__ import annotations

import re
from typing import Any

# The execution/secrets/bypass families intentionally match the student
# policy's shapes (kept in sync by test_staff_edward_safety); the policies
# are separate on purpose, so these are owned copies, not imports.
_UNSAFE_EXECUTION_TARGET = re.compile(
    r"\b(?:python|shell|bash|zsh|powershell|terminal|command|script|node(?:\.js)?|"
    r"curl|wget|reverse shell|remote code)\b",
    re.I,
)
_UNSAFE_EXECUTION_VERB = re.compile(
    r"\b(?:run|execute|launch|invoke|spawn|eval|install|upload and run|write and run)\b",
    re.I,
)
_SENSITIVE_OR_DESTRUCTIVE_TARGET = re.compile(
    r"(?:\.env\b|environment variables?|\bapi[-_ ]?keys?\b|\bsecrets?\b|\bpasswords?\b|"
    r"\bcredentials?\b|\bauth tokens?\b|169\.254\.169\.254|metadata service|"
    r"instance metadata|read (?:a )?file|filesystem|exfiltrat|\bdelete\b|"
    r"\bdrop (?:the )?(?:database|table)\b|\bransomware\b|\bmalware\b)",
    re.I,
)
_AUTHORITY_BYPASS = re.compile(
    r"(?:ignore|override|bypass|disregard|reveal|show me).{0,48}(?:system prompt|"
    r"developer message|safety (?:rule|policy)|access control|authorization|hidden prompt|"
    r"your (?:prompt|instructions))|"
    r"(?:jailbreak|prompt injection)",
    re.I,
)
_MALICIOUS_CODE_REQUEST = re.compile(
    r"(?:write|create|generate|provide|give me).{0,48}(?:python|shell|bash|powershell|code|"
    r"script).{0,80}(?:hack|attack|exploit|steal|exfiltrat|bypass|reverse shell|malware|"
    r"ransomware)|(?:hack|attack|exploit|steal|exfiltrat|bypass|reverse shell|malware|"
    r"ransomware).{0,80}(?:python|shell|bash|powershell|code|script)",
    re.I,
)
_FORGED_RECORD_MUTATION = re.compile(
    r"(?:mark|set|change|make|pretend|forge).{0,48}(?:deposit|payment|requirement|"
    r"application|record|course|grade|aid).{0,48}(?:paid|complete|completed|approved|"
    r"verified|accepted|waived)|(?:paid|complete|completed|approved|verified|accepted|"
    r"waived).{0,48}(?:without paying|without approval|without authorization)",
    re.I,
)
_SENSITIVE_OPERATION_VERB = re.compile(
    r"\b(?:get|read|show|print|dump|steal|send|post|copy|expose|reveal|access|extract)\b",
    re.I,
)
# Staff-specific: reaching outside the authenticated institution.
_CROSS_TENANT_ACCESS = re.compile(
    r"(?:other|another|different|every|all)\s+(?:tenant|institution|universit\w+|college|"
    r"school)s?\b.{0,64}\b(?:student|record|data|roster|list|cohort)"
    r"|\b(?:student|record|data|roster)s?\b.{0,48}\b(?:other|another|different|every|all)\s+"
    r"(?:tenant|institution|universit\w+|college|school)s?\b"
    r"|\bacross (?:all )?(?:tenants|institutions|universities|schools)\b"
    r"|\bswitch (?:me )?(?:to|into) (?:another|a different) (?:tenant|institution)\b",
    re.I,
)
_ARBITRARY_DATA_ACCESS = re.compile(
    r"\b(?:run|execute|write)\b.{0,24}\b(?:sql|query|select)\b"
    r"|\bquery the (?:database|db|tables?)\b|\bdump (?:the )?(?:table|database|db)\b"
    r"|\braw (?:table|database|sql) access\b",
    re.I,
)


def guarded_staff_response(message: object) -> dict[str, Any] | None:
    """Reject capabilities Staff Edward must never exercise, before any model
    call. Returns a complete deterministic response payload, or None."""

    if not isinstance(message, str):
        return None
    code_execution = bool(
        _UNSAFE_EXECUTION_VERB.search(message)
        and (
            _UNSAFE_EXECUTION_TARGET.search(message)
            or _SENSITIVE_OR_DESTRUCTIVE_TARGET.search(message)
        )
    )
    sensitive_operation = bool(
        _SENSITIVE_OR_DESTRUCTIVE_TARGET.search(message)
        and _SENSITIVE_OPERATION_VERB.search(message)
    )
    reason: str | None = None
    if _CROSS_TENANT_ACCESS.search(message):
        reason = (
            "I can only read records inside your own institution. I can't access, "
            "enumerate, or compare another institution's students or data."
        )
    elif _FORGED_RECORD_MUTATION.search(message):
        reason = (
            "I can't mark, forge, or alter record state from chat — I'm read-only, and "
            "record changes must go through the authorized portal workflow so validation "
            "and the audit trail are preserved."
        )
    elif _ARBITRARY_DATA_ACCESS.search(message):
        reason = (
            "I can't run queries or access the database directly. I read through a fixed "
            "set of governed, tenant-scoped tools — ask me about a student, the work "
            "queue, or communications and I'll use those."
        )
    elif (
        code_execution
        or sensitive_operation
        or _AUTHORITY_BYPASS.search(message)
        or _MALICIOUS_CODE_REQUEST.search(message)
    ):
        reason = (
            "I can't run code or commands, access server files, secrets, or credentials, "
            "reveal system instructions, or bypass safeguards."
        )
    if reason is None:
        return None
    return {
        "message": reason,
        "provider": "guided",
        "model": None,
        "usage": None,
        "contextReceipts": [],
        "blocks": [{"type": "text", "fallbackText": reason, "text": reason}],
    }
