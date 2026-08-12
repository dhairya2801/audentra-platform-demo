"""Zero-token Edward routing and configuration-safe extraction fallbacks."""

from __future__ import annotations

import re
from collections.abc import Mapping
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


def deterministic_response(message: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    text = message.lower()
    predictable = bool(
        re.search(r"(?:what (?:should|do) i do next|next (?:step|action)|what'?s next)", text)
        or re.search(
            r"document|upload|transcript|fafsa|ferpa|payment|deposit|pay|profile|phone|name|contact|appointment|advisor|person|human|deadline|due|when",
            text,
        )
    )
    return guided_response(message, context) if predictable else None


def guided_response(message: str, context: Mapping[str, Any]) -> dict[str, Any]:
    text = message.lower()
    if re.search(r"(?:what (?:should|do) i do next|next (?:step|action)|what'?s next)", text):
        response = _next_action_guidance(context.get("nextAction"))
    elif re.search(r"class|classroom|course|catalog|major|program|prerequisite|academic", text):
        response = _academic_guidance(context)
    elif re.search(r"campus|club|event|activity|organization|social life", text):
        response = _campus_life_guidance(context)
    elif re.search(r"document|upload|transcript|fafsa|ferpa", text):
        response = (
            "Open Documents to upload a PDF, JPEG, or PNG. Your institution stores the "
            "original file and prepares structured fields for your review. Nothing extracted "
            "is treated as verified until you approve it."
        )
    elif re.search(r"deadline|due|when", text):
        response = (
            "Your dashboard shows the nearest enrollment deadlines. Open Enrollment for the "
            "complete checklist and the status of each requirement."
        )
    elif re.search(r"payment|deposit|pay", text):
        response = (
            "Open Payments to review the enrollment deposit. Payment details should only be "
            "entered in the secure processor—not in this chat."
        )
    elif re.search(r"profile|phone|name|contact", text):
        response = (
            "You can update changeable contact preferences from Profile. Legal identity "
            "changes may require supporting documentation and staff review."
        )
    elif re.search(r"appointment|advisor|person|human", text):
        response = (
            "Open Appointments to schedule enrollment, admissions, or financial-aid support "
            "with a staff member."
        )
    else:
        response = (
            "I can help with enrollment, documents, academics, classes, financial aid, campus "
            "life, appointments, and profile settings. Ask one specific question and I'll use "
            "only the relevant part of your university record."
        )
    return {
        "message": response,
        "provider": "guided",
        "model": None,
        "usage": None,
        "suggestedActions": suggested_actions(message),
        "contextReceipts": [],
        "widgets": widgets(message, context),
    }


def suggested_actions(message: str) -> list[dict[str, str]]:
    text = message.lower()
    if re.search(r"class|classroom|course|catalog|major|program|prerequisite|academic", text):
        return [{"label": "Open My Classrooms", "href": "/classrooms"}]
    if re.search(r"campus|club|event|activity|organization|social life", text):
        return [{"label": "Open My Campus Life", "href": "/campus-life"}]
    if re.search(r"document|upload|transcript|fafsa|ferpa", text):
        return [{"label": "Open documents", "href": "/documents"}]
    if re.search(r"payment|deposit|pay", text):
        return [{"label": "Open payments", "href": "/payments"}]
    if re.search(r"appointment|advisor|human", text):
        return [{"label": "Book an appointment", "href": "/appointments"}]
    if re.search(r"profile|phone|name|contact", text):
        return [{"label": "Open profile", "href": "/profile"}]
    return [
        {"label": "View enrollment", "href": "/enrollment"},
        {"label": "Get support", "href": "/help"},
    ]


def widgets(message: str, context: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = message.lower()
    if re.search(r"(?:pay|make|complete).{0,24}deposit|deposit.{0,24}(?:pay|payment)", text):
        paid = bool(context.get("depositPaid"))
        return [
            {
                "type": "deposit_payment",
                "id": "edward-deposit-payment",
                "title": "Enrollment deposit",
                "description": (
                    "Your enrollment deposit is recorded as paid."
                    if paid
                    else "Complete the simulated enrollment deposit securely here."
                ),
                "offerId": str(context.get("offerId", "")),
                "amountCents": int(context.get("depositAmountCents", 0)),
                "status": "completed" if paid else "ready",
            }
        ]
    if re.search(r"upload|transcript|fafsa|verification", text):
        return [
            {
                "type": "document_upload",
                "id": "edward-document-upload",
                "title": "Upload a document",
                "description": (
                    "Add a PDF, JPEG, or PNG and review extracted fields before they reach "
                    "your student record."
                ),
                "category": "transcript" if "transcript" in text else "financial_aid",
                "href": "/documents",
            }
        ]
    if re.search(r"appointment|advisor|counselor|human", text):
        return [
            {
                "type": "appointment",
                "id": "edward-advisor-appointment",
                "title": "Meet with a student advisor",
                "description": "Choose a time with the team best suited to your question.",
                "appointmentType": (
                    "financial_aid"
                    if re.search(r"financial|aid|fafsa|loan", text)
                    else "enrollment_support"
                ),
                "href": "/appointments",
            }
        ]
    return []


def _next_action_guidance(value: object) -> str:
    if not isinstance(value, Mapping):
        return "Open Enrollment to review the next available step in your checklist."
    raw_title = value.get("title")
    raw_description = value.get("description")
    title = raw_title.strip()[:180] if isinstance(raw_title, str) else ""
    description = raw_description.strip()[:300] if isinstance(raw_description, str) else ""
    if not title:
        return "Open Enrollment to review the next available step in your checklist."
    return (
        f"Your next step is {title}. {description}"
        if description
        else f"Your next step is {title}. Open Enrollment to continue."
    )


def _academic_guidance(context: Mapping[str, Any]) -> str:
    academics = context.get("academicSummary")
    if not isinstance(academics, Mapping):
        return "Open My Classrooms to review your academic plan and searchable course catalog."
    raw_plan = academics.get("plan")
    plan = raw_plan if isinstance(raw_plan, list) else []
    options: list[str] = []
    for item in plan:
        if not isinstance(item, Mapping) or item.get("status") not in {
            "eligible",
            "required",
            "in_progress",
        }:
            continue
        options.append(f"{item.get('code', '')} {item.get('title', '')}".strip())
        if len(options) == 3:
            break
    suffix = f" Your next available plan options include {', '.join(options)}." if options else ""
    return (
        f"Your {academics.get('selectedProgram', '')} plan is using catalog "
        f"{academics.get('catalogVersion', '')}.{suffix} Open My Classrooms for requirement "
        "status, prerequisites, and official source details."
    )


def _campus_life_guidance(context: Mapping[str, Any]) -> str:
    campus = context.get("campusLifeSummary")
    if not isinstance(campus, Mapping):
        return "Open My Campus Life to explore upcoming events and student organizations."
    raw_events = campus.get("upcomingEvents")
    raw_clubs = campus.get("clubs")
    events = (
        [str(x.get("title")) for x in raw_events[:2] if isinstance(x, Mapping)]
        if isinstance(raw_events, list)
        else []
    )
    clubs = (
        [str(x.get("name")) for x in raw_clubs[:3] if isinstance(x, Mapping)]
        if isinstance(raw_clubs, list)
        else []
    )
    event_text = f" Upcoming: {' and '.join(events)}." if events else ""
    club_text = f" Featured groups include {', '.join(clubs)}." if clubs else ""
    return (
        f"{event_text}{club_text} Open My Campus Life to search the full tenant-managed directory."
    ).strip()
