"""Deterministic Edward safety policy, independent of FastAPI and providers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EdwardActionAuthority:
    offer_id: str
    deposit_amount_cents: int
    deposit_paid: bool
    allow_deposit_payment: bool
    document_upload_category: str | None
    appointment_type: str | None


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
    r"(?:ignore|override|bypass|disregard|reveal).{0,48}(?:system prompt|"
    r"developer message|safety (?:rule|policy)|access control|authorization|hidden prompt)|"
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
_CROSS_STUDENT_ACCESS = re.compile(
    r"(?:another|other|different|all).{0,24}students?.{0,48}(?:record|profile|document|payment|grade|email|phone|data)|(?:record|profile|document|payment|grade|email|phone|data).{0,48}(?:another|other|different|all).{0,24}students?",
    re.I,
)
_SENSITIVE_OPERATION_VERB = re.compile(
    r"\b(?:get|read|show|print|dump|steal|send|post|copy|expose|reveal|access|extract)\b",
    re.I,
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ACTIVE_MARKUP = re.compile(
    r"<\s*/?\s*(?:script|style|iframe|object|embed|link|meta)\b[^>]*>", re.I
)
_MARKDOWN_LINK = re.compile(
    r"\[([^\]\r\n]{1,240})\]\(\s*(?:(?:[a-z][a-z0-9+.-]*:)|//|/)[^\s)]*\s*\)",
    re.I,
)
_URI = re.compile(
    r"(?:https?://|www\.|//|(?:javascript|vbscript|data|mailto|tel|file|blob):)[^\s<>()\]]+",
    re.I,
)

_PORTAL_PAGE_CONTEXTS = frozenset(
    {
        "/dashboard",
        "/enrollment",
        "/enrollment/requirements",
        "/financials",
        "/classrooms",
        "/campus-life",
        "/edward",
        "/profile",
        "/documents",
        "/payments",
        "/appointments",
        "/help",
    }
)
_PORTAL_ACTION_HREFS = frozenset(_PORTAL_PAGE_CONTEXTS - {"/enrollment/requirements"})
_REQUIREMENT_PATH = re.compile(r"^/enrollment/requirements/[a-z0-9][a-z0-9-]{0,80}$", re.I)
_FALLBACK_PAGE_CONTEXT = "/dashboard"


def normalize_page_context(value: object) -> str:
    if not isinstance(value, str):
        return _FALLBACK_PAGE_CONTEXT
    candidate = value.strip()
    if not candidate or len(candidate) > 120 or _CONTROL.search(candidate):
        return _FALLBACK_PAGE_CONTEXT
    if len(candidate) > 1:
        candidate = candidate.rstrip("/")
    if candidate in _PORTAL_PAGE_CONTEXTS:
        return candidate
    if _REQUIREMENT_PATH.fullmatch(candidate):
        return "/enrollment/requirements"
    return _FALLBACK_PAGE_CONTEXT


def normalize_action_href(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 160 or _CONTROL.search(candidate):
        return None
    if len(candidate) > 1:
        candidate = candidate.rstrip("/")
    if candidate in _PORTAL_ACTION_HREFS or _REQUIREMENT_PATH.fullmatch(candidate):
        return candidate
    return None


def guarded_response(message: object) -> dict[str, Any] | None:
    """Reject capabilities Edward does not possess before any provider call."""
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
    if _CROSS_STUDENT_ACCESS.search(message):
        reason = (
            "I can only use the signed-in student\u2019s permission-scoped institution record. "
            "I can\u2019t access or reveal another student\u2019s information."
        )
    elif _FORGED_RECORD_MUTATION.search(message):
        reason = (
            "I can\u2019t forge, approve, or mark payments and student records complete from "
            "chat. Use the authorized portal workflow so validation, idempotency, and the "
            "audit trail are preserved."
        )
    elif (
        code_execution
        or sensitive_operation
        or _AUTHORITY_BYPASS.search(message)
        or _MALICIOUS_CODE_REQUEST.search(message)
    ):
        reason = (
            "I can\u2019t run code or commands, access server files or secrets, bypass safeguards, "
            "or attack systems. Edward has no shell, Python, filesystem, or arbitrary network "
            "tools."
        )
    if reason is None:
        return None
    return {
        "message": reason,
        "provider": "guided",
        "model": None,
        "usage": None,
        "suggestedActions": [],
        "contextReceipts": [],
        "widgets": [],
    }


def sanitize_prose(value: object) -> str:
    text = value if isinstance(value, str) else ""
    normalized = _ACTIVE_MARKUP.sub("", text)
    normalized = _MARKDOWN_LINK.sub(r"\1", normalized)
    normalized = _URI.sub("", normalized)
    normalized = _UNSAFE_CONTROL.sub("", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized).strip()
    return normalized[:2500] or "I prepared the relevant portal action below."


def normalize_response(
    response: dict[str, Any], authority: EdwardActionAuthority | None = None
) -> dict[str, Any]:
    actions: list[dict[str, str]] = []
    raw_actions = response.get("suggestedActions")
    if isinstance(raw_actions, list):
        for raw in raw_actions:
            if not isinstance(raw, dict):
                continue
            href = normalize_action_href(raw.get("href"))
            label_value = raw.get("label")
            label = label_value.strip()[:80] if isinstance(label_value, str) else ""
            if href and label:
                actions.append({"label": label, "href": href})
    return {
        **response,
        "message": sanitize_prose(response.get("message")),
        "suggestedActions": actions[:4],
        "widgets": _normalize_widgets(response.get("widgets"), authority),
    }


def _normalize_widgets(
    value: object, authority: EdwardActionAuthority | None
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for widget in value:
        if not isinstance(widget, dict):
            continue
        kind = widget.get("type")
        if kind == "deposit_payment" and authority and authority.allow_deposit_payment:
            normalized.append(
                {
                    "type": "deposit_payment",
                    "id": "edward-deposit-payment",
                    "title": "Enrollment deposit",
                    "description": (
                        "Your enrollment deposit is recorded as paid."
                        if authority.deposit_paid
                        else "Complete the simulated enrollment deposit securely here."
                    ),
                    "offerId": authority.offer_id,
                    "amountCents": authority.deposit_amount_cents,
                    "status": "completed" if authority.deposit_paid else "ready",
                }
            )
        elif kind == "document_upload" and authority and authority.document_upload_category:
            normalized.append(
                {
                    "type": "document_upload",
                    "id": "edward-document-upload",
                    "title": "Upload a document",
                    "description": (
                        "Add a PDF, JPEG, or PNG through the protected document workflow."
                    ),
                    "category": authority.document_upload_category,
                    "href": "/documents",
                }
            )
        elif kind == "appointment" and authority and authority.appointment_type:
            normalized.append(
                {
                    "type": "appointment",
                    "id": "edward-advisor-appointment",
                    "title": "Meet with a student advisor",
                    "description": "Choose a time with the team best suited to your question.",
                    "appointmentType": authority.appointment_type,
                    "href": "/appointments",
                }
            )
    return normalized[:2]
