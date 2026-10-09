"""Content-derived validation, separate from institutional approval."""

from collections.abc import Mapping
from typing import Any

REQUIRED_FIELDS = {
    "transcript": ("studentName", "institutionName", "courses"),
    "identity": ("studentName", "date_of_birth"),
    "passport": (
        "studentName",
        "date_of_birth",
        "nationality",
        "issuing_country",
        "passport_number",
        "issueDate",
        "expiry_date",
    ),
    "immunization": ("studentName", "date_of_birth", "institutionName", "vaccination_records"),
    "financial_aid": (
        "studentName",
        "academicTerm",
        "household_size",
        "tax_year",
        "household_income",
    ),
}


def validate_evidence(extraction: Mapping[str, Any], expected: str) -> dict[str, Any]:
    values = {
        str(f.get("key")): f.get("value")
        for f in extraction.get("fields", [])
        if isinstance(f, Mapping)
    }
    values.update(
        {
            k: extraction.get(k)
            for k in ("studentName", "institutionName", "issueDate", "academicTerm", "courses")
        }
    )
    if not values.get("vaccination_records"):
        values["vaccination_records"] = [
            value for key, value in values.items() if key.startswith("vaccination_") and value
        ]
    missing = [k for k in REQUIRED_FIELDS.get(expected, ()) if not values.get(k)]
    uncertain = [
        str(f.get("key"))
        for f in extraction.get("fields", [])
        if isinstance(f, Mapping)
        and isinstance(f.get("confidence"), (int, float))
        and f["confidence"] < 0.7
    ]
    missing = list(dict.fromkeys([*missing, *uncertain]))
    actual = extraction.get("documentType")
    status = extraction.get("status")
    outcome = "matched"
    message = "Received for staff review. Parsing is not approval."
    if status == "pending_staff":
        classification = extraction.get("classification", {})
        actual = extraction.get("documentType")
        subtype = classification.get("documentSubtype")
        if classification.get("readability") == "unreadable":
            return {
                "outcome": "unreadable",
                "missingFields": [],
                "message": "We could not read this file. Please upload a clear copy.",
            }
        if classification.get("readability") == "ambiguous":
            return {
                "outcome": "incomplete",
                "missingFields": [],
                "message": "Original received. Staff must confirm the document type.",
            }
        if actual != ("identity" if expected == "passport" else expected) or (
            expected == "passport" and subtype == "other"
        ):
            label = (
                "an unrelated document"
                if actual == "other"
                else "a " + str(actual).replace("_", " ")
            )
            return {
                "outcome": "wrong_type",
                "missingFields": [],
                "message": (
                    f"This appears to be {label}. Please upload your {expected.replace('_', ' ')}."
                ),
            }
        return {
            "outcome": "matched",
            "missingFields": [],
            "message": "Original received. Awaiting staff parsing and review.",
        }
    if status != "completed":
        outcome = "failed"
        message = "Your original is saved. Processing needs a retry or staff review."
    elif actual == "other":
        outcome = "unrecognized"
        message = (
            "We could not identify this document. Please upload a clear, complete copy of your "
            + expected.replace("_", " ")
            + "."
        )
    elif actual != ("identity" if expected == "passport" else expected):
        outcome = "wrong_type"
        message = (
            f"This appears to be a {str(actual).replace('_', ' ')}. "
            f"Please upload your {expected.replace('_', ' ')}."
        )
    elif expected == "passport" and str(values.get("document_subtype", "")).lower() != "passport":
        outcome = "incomplete"
        message = (
            "We could not confirm that this identity document is a passport. "
            "Staff needs to review the original."
        )
    elif missing:
        outcome = "incomplete"
        message = (
            "Original received. Some required information could not be read; "
            "staff review is needed."
        )
    return {"outcome": outcome, "message": message, "missingFields": missing}
