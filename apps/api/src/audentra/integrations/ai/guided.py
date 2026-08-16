"""Configuration-safe document-extraction fallbacks.

The legacy zero-token Edward chat guidance that used to live here was removed
with the legacy `ask_edward` gateway path: the canonical assistant runtime is
the deterministic-first pipeline in `audentra.integrations.assistant`, which
answers without a model on its own. What remains is the extraction fallback
used when no document-AI provider key is configured.
"""

from __future__ import annotations

from typing import Any


def infer_document_type(file_name: str) -> str:
    name = file_name.lower()
    if "transcript" in name:
        return "transcript"
    if "fafsa" in name or "financial" in name:
        return "financial_aid"
    if "ferpa" in name or "release" in name:
        return "ferpa"
    if "immun" in name or "vaccine" in name:
        return "immunization"
    if "passport" in name or "license" in name:
        return "identity"
    if "residen" in name:
        return "residency"
    return "other"


def pending_extraction(
    file_name: str, expected_document_type: str | None, provider: str = "openrouter"
) -> dict[str, Any]:
    provider_name = "Groq" if provider == "groq" else "OpenRouter"
    key_name = "GROQ_API_KEY" if provider == "groq" else "OPENROUTER_API_KEY"
    return {
        "status": "pending_configuration",
        "documentType": expected_document_type or infer_document_type(file_name),
        "summary": f"File stored securely. Add {key_name} to run structured extraction.",
        "studentName": None,
        "institutionName": None,
        "issueDate": None,
        "academicTerm": None,
        "fields": [],
        "courses": [],
        "warnings": [
            f"Agentic parsing is waiting for a {provider_name} API key.",
            "No extracted value will update the student profile without review.",
        ],
        "model": None,
        "provider": "local",
        "processedAt": None,
        "verifiedAt": None,
        "retryable": True,
    }
