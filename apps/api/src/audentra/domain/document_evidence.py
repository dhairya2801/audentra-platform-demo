"""Deterministic review checks. Extracted text is evidence, never authority."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from audentra.domain.documents import document_type_for_category


def review_checks(
    extraction: Mapping[str, Any], category: str, student_name: str
) -> list[dict[str, str]]:
    checks = []

    def check(key: str, label: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "key": key,
                "label": label,
                "status": "pass" if passed else "attention",
                "detail": detail,
            }
        )

    check(
        "readable",
        "Readable extraction",
        extraction.get("status") == "completed",
        "Extraction must finish. Retry processing or request a readable replacement.",
    )
    detected = (
        extraction.get("documentType", "unknown")
        if extraction.get("status") == "completed"
        else "not determined"
    )
    check(
        "type",
        "Document type",
        extraction.get("status") == "completed"
        and extraction.get("documentType") == document_type_for_category(category),
        f"Required: {document_type_for_category(category)}; detected: {detected}.",
    )

    def normalize(value: object) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())

    check(
        "identity",
        "Student name",
        bool(extraction.get("studentName"))
        and normalize(extraction.get("studentName")) == normalize(student_name),
        f"Compare with student record: {student_name}.",
    )
    fields = {
        str(f.get("key")): str(f.get("value") or "")
        for f in extraction.get("fields", [])
        if isinstance(f, Mapping)
    }
    if category == "transcript":
        check(
            "institution",
            "Institution present",
            bool(extraction.get("institutionName")),
            "Record the institution printed on the evidence.",
        )
        check(
            "coursework",
            "Coursework present",
            bool(extraction.get("courses")),
            "At least one course row is needed for review. Parsing does not award credit.",
        )
    if category == "identity":
        expires = next(
            (
                v
                for k, v in fields.items()
                if k in {"expiry_date", "expiration_date", "valid_until"}
            ),
            "",
        )
        check(
            "expiry_present",
            "Expiry recorded",
            bool(expires),
            "Record the printed expiry date. Eligibility is a staff decision.",
        )
    if category == "health":
        check(
            "health_evidence",
            "Immunization evidence present",
            any(
                v
                for k, v in fields.items()
                if re.search(r"vaccin|immun|dose|mmr|measles|mumps|rubella", k, re.I)
            ),
            "Review recorded vaccine/dose evidence. No medical eligibility rule is inferred.",
        )
    validation = extraction.get("validation", {})
    if isinstance(validation, Mapping):
        check(
            "required_fields",
            "Required fields",
            not validation.get("missingFields"),
            "Missing or uncertain: " + ", ".join(validation.get("missingFields", []))
            if validation.get("missingFields")
            else ("Required fields were extracted; verify them against the original."),
        )
    from datetime import datetime

    for key in ("expiry_date", "expiration_date"):
        if fields.get(key):
            for fmt in ("%Y-%m-%d", "%d %b %Y", "%B %d, %Y"):
                try:
                    expiry_date = datetime.strptime(fields[key], fmt).date()
                    check(
                        "expiry",
                        "Expiry date (derived check)",
                        expiry_date >= date.today(),
                        "Expired as of today."
                        if expiry_date < date.today()
                        else "The printed expiry date is in the future.",
                    )
                    break
                except ValueError:
                    continue
    issue = str(extraction.get("issueDate") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue):
        try:
            check(
                "issue_date",
                "Issue date",
                date.fromisoformat(issue) <= date.today(),
                "Issue date must not be in the future.",
            )
        except ValueError:
            check("issue_date", "Issue date", False, "The extracted date is invalid.")
    decision = extraction.get("fieldDecisions", {}).get("studentName", {})
    if decision.get("action") == "accept" and extraction.get("studentName"):
        for item in checks:
            if item["key"] == "identity":
                item["status"] = "pass"
                item["detail"] = "Name variation accepted by staff. " + str(
                    decision.get("note", "")
                )
    return checks
