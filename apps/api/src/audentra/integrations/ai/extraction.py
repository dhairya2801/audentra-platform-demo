"""Provider-neutral normalization for untrusted document extraction responses."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .provider import ProviderCompletionError

DOCUMENT_TYPES = frozenset(
    {"transcript", "identity", "financial_aid", "ferpa", "immunization", "residency", "other"}
)
_SHAPE_KEYS = (
    "documentType",
    "summary",
    "studentName",
    "institutionName",
    "issueDate",
    "academicTerm",
    "fields",
    "courses",
    "visualRegions",
    "warnings",
)
_ALLOWED_LEGACY_IDENTITY_FIELD_KEYS = frozenset(
    {
        "date_of_birth",
        "expiry_date",
        "father_name",
        "gender",
        "issued_by",
        "mother_name",
        "name",
        "nationality",
        "valid_until",
    }
)
_ALLOWED_IDENTITY_FIELD_KEYS = frozenset(
    {
        "address",
        "birth_date",
        "city",
        "country",
        "date_of_birth",
        "expiration_date",
        "expiry_date",
        "family_name",
        "father_name",
        "first_name",
        "full_name",
        "gender",
        "given_name",
        "given_names",
        "issued_by",
        "issuing_authority",
        "last_name",
        "mobile_phone",
        "mother_name",
        "name",
        "nationality",
        "place_of_birth",
        "postal_code",
        "preferred_name",
        "sex",
        "state_or_province",
        "street_address",
        "surname",
        "valid_until",
    }
)

# OpenRouter follows the OpenAI response-format contract for strict structured
# output. Keep a provider-independent schema here so tenant prompt wording
# cannot silently change the extraction envelope consumed by the application.
DOCUMENT_EXTRACTION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "documentType": {
            "type": "string",
            "enum": sorted(DOCUMENT_TYPES),
        },
        "summary": {"type": "string"},
        "studentName": {"type": ["string", "null"]},
        "institutionName": {"type": ["string", "null"]},
        "issueDate": {"type": ["string", "null"]},
        "academicTerm": {"type": ["string", "null"]},
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["key", "label", "value", "confidence"],
            },
        },
        "courses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sourceCode": {"type": ["string", "null"]},
                    "title": {"type": "string"},
                    "credits": {"type": ["number", "null"]},
                    "grade": {"type": ["string", "null"]},
                    "score": {"type": ["string", "null"]},
                    "term": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "sourceCode",
                    "title",
                    "credits",
                    "grade",
                    "score",
                    "term",
                    "confidence",
                ],
            },
        },
        "visualRegions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["profile_photo"]},
                    "pageNumber": {"type": ["integer", "null"]},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "width": {"type": "number"},
                    "height": {"type": "number"},
                    "confidence": {"type": "number"},
                },
                "required": [
                    "kind",
                    "pageNumber",
                    "x",
                    "y",
                    "width",
                    "height",
                    "confidence",
                ],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": list(_SHAPE_KEYS),
}


def parse_extraction_json(content: str) -> dict[str, Any]:
    """Pick the most extraction-shaped complete object from provider reasoning text."""
    source = content.strip()
    candidates: list[dict[str, Any]] = []
    start = -1
    depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(source):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    value = json.loads(source[start : index + 1])
                except json.JSONDecodeError:
                    value = None
                if isinstance(value, dict):
                    candidates.append(value)
                start = -1
    if candidates:
        return max(candidates, key=lambda value: sum(key in value for key in _SHAPE_KEYS))
    if depth == 0:
        raise ProviderCompletionError("AI provider returned no valid JSON extraction")
    raise ProviderCompletionError("AI provider returned incomplete JSON extraction")


def adapt_legacy_identity_extraction(
    value: Mapping[str, Any], expected_document_type: str | None
) -> dict[str, Any]:
    """Convert the former ``metadata`` identity envelope at the trust boundary.

    Some published prompt versions asked for ``{metadata, warning}`` instead of
    the canonical extraction contract. Supporting that bounded envelope keeps
    already-running tenants compatible while strict structured output rolls
    forward. Government/document identifiers are deliberately discarded before
    they can enter canonical fields or student-facing persistence.
    """

    metadata = value.get("metadata")
    if (
        expected_document_type != "identity"
        or not isinstance(metadata, Mapping)
        or any(key in value for key in _SHAPE_KEYS)
    ):
        return dict(value)

    student_name = _first_metadata_text(metadata, ("studentName", "student_name", "name"), 160)
    institution_name = _first_metadata_text(
        metadata,
        ("institutionName", "institution_name", "issuedBy", "issued_by"),
        200,
    )
    issue_date = _first_metadata_text(
        metadata,
        ("issueDate", "issue_date", "dateOfIssue", "date_of_issue"),
        80,
    )
    fields: list[dict[str, Any]] = []
    for raw_key, raw_value in metadata.items():
        key = _canonical_metadata_key(raw_key)
        if key not in _ALLOWED_LEGACY_IDENTITY_FIELD_KEYS:
            continue
        text = _safe_text(raw_value, "", 500)
        if not text:
            continue
        fields.append(
            {
                "key": key,
                "label": key.replace("_", " ").title(),
                "value": text,
                "confidence": 0.0,
            }
        )

    warning = value.get("warning")
    raw_warnings = warning if isinstance(warning, list) else [warning]
    warnings = [_redact(text) for item in raw_warnings[:12] if (text := _safe_text(item, "", 400))]
    return {
        "documentType": "identity",
        "summary": "Identity document metadata was extracted and is ready for review.",
        "studentName": student_name,
        "institutionName": institution_name,
        "issueDate": issue_date,
        "academicTerm": None,
        "fields": fields,
        "courses": [],
        "visualRegions": [],
        "warnings": warnings,
    }


def normalize_extraction(
    value: Mapping[str, Any],
    model: str,
    evidence_document_type: str | None = None,
    provider: str = "openrouter",
) -> dict[str, Any]:
    document_type = normalize_document_type(value.get("documentType"), evidence_document_type)
    summary = _safe_text(
        value.get("summary"), "The document was parsed and is ready for review.", 800
    )
    student_name = _nullable_text(value.get("studentName"), 160)
    institution_name = _nullable_text(value.get("institutionName"), 200)
    issue_date = _nullable_text(value.get("issueDate"), 80)
    academic_term = _nullable_text(value.get("academicTerm"), 120)
    if document_type == "identity":
        summary = "Identity document details were extracted and are ready for review."
        student_name = _redact_identity(student_name) if student_name is not None else None
        institution_name = (
            _redact_identity(institution_name) if institution_name is not None else None
        )
        issue_date = _redact_identity(issue_date) if issue_date is not None else None
        academic_term = _redact_identity(academic_term) if academic_term is not None else None
    raw_fields = _as_list(value.get("fields"))
    fields: list[dict[str, Any]] = []
    for index, candidate in enumerate(raw_fields[:24]):
        field = candidate if isinstance(candidate, Mapping) else {}
        key = _safe_text(field.get("key"), f"field_{index + 1}", 80)
        label = _safe_text(field.get("label"), f"Field {index + 1}", 120)
        field_value = _safe_text(field.get("value"), "", 500)
        if document_type == "identity":
            key = _canonical_metadata_key(key)
            if (
                key not in _ALLOWED_IDENTITY_FIELD_KEYS
                or _is_sensitive_identity_key(key)
                or _is_sensitive_identity_key(label)
            ):
                continue
            field_value = _redact_identity(field_value)
        fields.append(
            {
                "key": key,
                "label": label,
                "value": field_value if document_type == "identity" else _redact(field_value),
                "confidence": _normalized_number(field.get("confidence")),
            }
        )
    raw_courses = _as_list(value.get("courses"))
    courses: list[dict[str, Any]] = []
    for candidate in raw_courses[:80]:
        course = candidate if isinstance(candidate, Mapping) else {}
        raw_credits = course.get("credits")
        credits = (
            max(0.0, min(20.0, float(raw_credits)))
            if isinstance(raw_credits, (int, float)) and not isinstance(raw_credits, bool)
            else None
        )
        courses.append(
            {
                "sourceCode": _nullable_text(course.get("sourceCode"), 80),
                "title": _safe_text(course.get("title"), "Untitled course", 180),
                "credits": credits,
                "grade": _nullable_text(course.get("grade"), 32),
                "score": _nullable_text(course.get("score"), 32),
                "term": _nullable_text(course.get("term"), 80),
                "confidence": _normalized_number(course.get("confidence")),
            }
        )
    raw_warnings = _as_list(value.get("warnings"))
    warnings = [
        _redact_identity(text) if document_type == "identity" else text
        for item in raw_warnings[:12]
        if (text := _safe_text(item, "", 400))
    ]
    return {
        "status": "completed",
        "documentType": document_type,
        "summary": summary,
        "studentName": student_name,
        "institutionName": institution_name,
        "issueDate": issue_date,
        "academicTerm": academic_term,
        "fields": fields,
        "courses": courses,
        "visualRegions": normalize_visual_regions(value.get("visualRegions")),
        "warnings": warnings,
        "model": model,
        "provider": provider,
        "processedAt": _utc_now(),
        "verifiedAt": None,
    }


def normalize_visual_regions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    regions: list[dict[str, Any]] = []
    for candidate in value[:4]:
        region = candidate if isinstance(candidate, Mapping) else {}
        if region.get("kind") != "profile_photo":
            continue
        x = _normalized_number(region.get("x"))
        y = _normalized_number(region.get("y"))
        width = min(1 - x, _normalized_number(region.get("width")))
        height = min(1 - y, _normalized_number(region.get("height")))
        if width < 0.02 or height < 0.02:
            continue
        raw_page = region.get("pageNumber")
        page_number = (
            int(raw_page)
            if isinstance(raw_page, int) and not isinstance(raw_page, bool) and 1 <= raw_page <= 8
            else None
        )
        regions.append(
            {
                "kind": "profile_photo",
                "pageNumber": page_number,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "confidence": _normalized_number(region.get("confidence")),
            }
        )
    return regions


def normalize_document_type(value: object, evidence_document_type: str | None = None) -> str:
    normalized = re.sub(r"[\s-]+", "_", str(value or "").lower()).strip()
    if normalized in DOCUMENT_TYPES:
        return (
            evidence_document_type
            if normalized == "other" and evidence_document_type
            else normalized
        )
    patterns = (
        (r"(?:ferpa|authorization.*(?:release|record)|release.*(?:education|record))", "ferpa"),
        (r"(?:transcript|academic_record|grade_report|course_record)", "transcript"),
        (r"(?:identity|passport|driver|national_id|identification)", "identity"),
        (r"(?:financial|aid|fafsa|loan|award)", "financial_aid"),
        (r"(?:immun|vaccin|health_record)", "immunization"),
        (r"(?:residen|address|lease|housing)", "residency"),
    )
    for pattern, document_type in patterns:
        if re.search(pattern, normalized):
            return document_type
    return evidence_document_type or "other"


def infer_type_from_evidence(text: str) -> str | None:
    normalized = text.lower()
    if re.search(r"\bferpa\b", normalized) or (
        "family educational rights" in normalized and "release" in normalized
    ):
        return "ferpa"
    if re.search(r"\b(?:official )?transcript\b", normalized):
        return "transcript"
    if (
        re.search(
            r"\b(?:fafsa|financial aid award|financial aid verification|student aid report)\b",
            normalized,
        )
        or "free application for federal student aid" in normalized
    ):
        return "financial_aid"
    if re.search(r"\b(?:immunization|vaccination)\b", normalized):
        return "immunization"
    if re.search(
        r"\b(?:restaurant menu|appetizers?|desserts?|beverages?|chef special)\b",
        normalized,
    ):
        return "other"
    return None


def evidence_classification(document_type: str) -> dict[str, Any]:
    """Return a field-free result for a clear local classification."""

    human_type = document_type.replace("_", " ")
    return {
        "status": "completed",
        "documentType": document_type,
        "summary": (
            f"The document has a clear {human_type} heading. Only its document type was "
            "retained for staff review."
        ),
        "studentName": None,
        "institutionName": None,
        "issueDate": None,
        "academicTerm": None,
        "fields": [],
        "courses": [],
        "visualRegions": [],
        "warnings": ["No financial fields were extracted or retained."],
        "model": None,
        "provider": "local",
        "processedAt": _utc_now(),
        "verifiedAt": None,
    }


def evidence_mismatch(expected: str, actual: str) -> dict[str, Any]:
    human_expected = expected.replace("_", " ")
    human_actual = actual.replace("_", " ")
    return {
        "status": "completed",
        "documentType": actual,
        "summary": (
            f"The document has a clear {human_actual} heading, so it does not match this "
            f"{human_expected} upload task."
        ),
        "studentName": None,
        "institutionName": None,
        "issueDate": None,
        "academicTerm": None,
        "fields": [],
        "courses": [],
        "visualRegions": [],
        "warnings": [
            "No external AI extraction was run because this clear document mismatch cannot "
            "satisfy the current requirement."
        ],
        "model": None,
        "provider": "local",
        "processedAt": _utc_now(),
        "verifiedAt": None,
    }


def useful_extraction(extraction: Mapping[str, Any], expected_type: str | None = None) -> bool:
    if expected_type and extraction.get("documentType") != expected_type:
        return False
    courses = extraction.get("courses")
    has_courses = isinstance(courses, list) and any(
        isinstance(course, Mapping) and str(course.get("title", "")).strip() for course in courses
    )
    if expected_type == "transcript":
        return has_courses
    if expected_type == "identity":
        fields = extraction.get("fields")
        has_identity_field = isinstance(fields, list) and any(
            isinstance(field, Mapping)
            and (value := str(field.get("value", "")).strip())
            and value != "[sensitive value redacted]"
            for field in fields
        )
        visual_regions = extraction.get("visualRegions")
        has_profile_region = isinstance(visual_regions, list) and any(
            isinstance(region, Mapping) and region.get("kind") == "profile_photo"
            for region in visual_regions
        )
        has_identity_header = any(
            (value := str(extraction.get(key) or "").strip())
            and value != "[sensitive value redacted]"
            for key in ("studentName", "institutionName", "issueDate")
        )
        return has_identity_field or has_profile_region or has_identity_header
    if extraction.get("documentType") != "other":
        return True
    fields = extraction.get("fields")
    if isinstance(fields, list) and any(
        isinstance(field, Mapping) and str(field.get("value", "")).strip() for field in fields
    ):
        return True
    if has_courses:
        return True
    return any(
        bool(str(extraction.get(key) or "").strip())
        for key in ("studentName", "institutionName", "issueDate", "academicTerm")
    )


def merge_transcript_extractions(extractions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not extractions:
        raise ProviderCompletionError("No transcript segments were available to merge")
    fields: dict[str, Mapping[str, Any]] = {}
    courses: dict[str, Mapping[str, Any]] = {}
    warnings: dict[str, None] = {}
    for extraction in extractions:
        for candidate in extraction.get("fields", []):
            if not isinstance(candidate, Mapping):
                continue
            key = f"{candidate.get('key', '')}:{candidate.get('value', '')}".lower()
            previous = fields.get(key)
            if previous is None or _normalized_number(
                candidate.get("confidence")
            ) > _normalized_number(previous.get("confidence")):
                fields[key] = candidate
        for candidate in extraction.get("courses", []):
            if not isinstance(candidate, Mapping):
                continue
            key = (
                "|".join(
                    str(candidate.get(name) or "")
                    for name in ("sourceCode", "title", "term", "grade", "score", "credits")
                )
                .strip()
                .lower()
            )
            previous = courses.get(key)
            if previous is None or _normalized_number(
                candidate.get("confidence")
            ) > _normalized_number(previous.get("confidence")):
                courses[key] = candidate
        for warning in extraction.get("warnings", []):
            if isinstance(warning, str):
                warnings[warning] = None
    first = extractions[0]
    course_count = len(courses)
    return {
        "status": "completed",
        "documentType": "transcript",
        "summary": (
            f"Transcript extracted from {len(extractions)} page segments with {course_count} "
            "distinct course rows."
        ),
        "studentName": _first_present(extractions, "studentName"),
        "institutionName": _first_present(extractions, "institutionName"),
        "issueDate": _first_present(extractions, "issueDate"),
        "academicTerm": _first_present(extractions, "academicTerm"),
        "fields": list(fields.values()),
        "courses": list(courses.values()),
        "visualRegions": [],
        "warnings": [
            warning
            for warning in warnings
            if course_count == 0 or not re.search(r"\bno course rows?\b", warning, re.I)
        ][:12],
        "model": first.get("model"),
        "provider": first.get("provider"),
        "processedAt": _utc_now(),
        "verifiedAt": None,
    }


def normalize_course_exemptions(
    value: Mapping[str, Any],
    courses: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Restrict every decision to identifiers from the active tenant catalog."""
    raw_decisions = _as_list(value.get("decisions"))
    catalog = context.get("catalogCourses")
    catalog_courses = catalog if isinstance(catalog, list) else []
    catalog_ids = {
        str(course.get("id")) for course in catalog_courses if isinstance(course, Mapping)
    }
    raw_rules = context.get("equivalencyRules")
    rules = raw_rules if isinstance(raw_rules, list) else []
    rule_by_id = {str(rule.get("id")): rule for rule in rules if isinstance(rule, Mapping)}
    valid_context_ids: set[str] = set(catalog_ids) | set(rule_by_id)
    for key in ("program", "catalogVersion"):
        record = context.get(key)
        if isinstance(record, Mapping) and record.get("id") is not None:
            valid_context_ids.add(str(record["id"]))
    for key in ("programRequirements", "prerequisites"):
        values = context.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, Mapping):
                continue
            for id_key in ("id", "courseId", "prerequisiteCourseId"):
                if item.get(id_key) is not None:
                    valid_context_ids.add(str(item[id_key]))
    valid_statuses = {"matched", "needs_review", "no_match", "policy_gap"}
    decisions: list[dict[str, Any]] = []
    for index, course in enumerate(courses):
        source_key = f"course:{index + 1}"
        raw = next(
            (
                decision
                for decision in raw_decisions
                if isinstance(decision, Mapping) and decision.get("sourceCourseId") == source_key
            ),
            {},
        )
        decision = raw if isinstance(raw, Mapping) else {}
        status = str(decision.get("status"))
        status = status if status in valid_statuses else "policy_gap"
        target = decision.get("targetCourseId")
        target_id = str(target) if isinstance(target, str) and target in catalog_ids else None
        raw_rule = decision.get("equivalencyRuleId")
        rule_id = str(raw_rule) if isinstance(raw_rule, str) and raw_rule in rule_by_id else None
        rule = rule_by_id.get(rule_id or "")
        if status in {"matched", "needs_review"} and (
            target_id is None
            or rule_id is None
            or not isinstance(rule, Mapping)
            or rule.get("targetCourseId") != target_id
        ):
            status = "policy_gap"
            target_id = None
            rule_id = None
        raw_context_ids = decision.get("contextIds")
        context_ids = (
            [
                item
                for item in raw_context_ids
                if isinstance(item, str) and item in valid_context_ids
            ][:24]
            if isinstance(raw_context_ids, list)
            else []
        )
        decisions.append(
            {
                "sourceCourseKey": source_key,
                "sourceCode": course.get("sourceCode"),
                "sourceTitle": course.get("title"),
                "status": status,
                "targetCourseId": target_id,
                "equivalencyRuleId": rule_id,
                "confidence": _normalized_number(decision.get("confidence")),
                "rationale": _safe_text(
                    decision.get("rationale"),
                    (
                        "No valid decision with current tenant context identifiers was "
                        "returned; staff review is required."
                        if status == "policy_gap"
                        else "Evaluated against the active tenant catalog and exemption policy."
                    ),
                    800,
                ),
                "contextIds": context_ids,
            }
        )
    catalog_version = context.get("catalogVersion")
    version_id = str(catalog_version.get("id")) if isinstance(catalog_version, Mapping) else ""
    raw_warnings = _as_list(value.get("warnings"))
    return {
        "catalogVersionId": version_id,
        "policyVersion": str(context.get("policyVersion", "")),
        "evaluatedCourseCount": len(courses),
        "decisions": decisions,
        "warnings": [warning for item in raw_warnings if (warning := _safe_text(item, "", 400))][
            :12
        ],
        "generatedAt": _utc_now(),
    }


def normalize_immunization_compliance(
    value: Mapping[str, Any],
    context: Mapping[str, Any],
    extraction: Mapping[str, Any],
) -> dict[str, Any]:
    """Produce one bounded result for every active tenant policy rule."""
    raw_results = _as_list(value.get("requirements"))
    evidence_keys = {
        str(field.get("key"))
        for field in extraction.get("fields", [])
        if isinstance(field, Mapping)
    }
    policy_version = context.get("policyVersion")
    policy = policy_version if isinstance(policy_version, Mapping) else {}
    raw_requirements = context.get("requirements")
    requirements = raw_requirements if isinstance(raw_requirements, list) else []
    valid_statuses = {"met", "missing", "uncertain", "not_applicable", "expired"}
    normalized: list[dict[str, Any]] = []
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            continue
        candidate = next(
            (
                item
                for item in raw_results
                if isinstance(item, Mapping)
                and (
                    item.get("ruleId") == requirement.get("id")
                    or item.get("code") == requirement.get("code")
                )
            ),
            {},
        )
        result = candidate if isinstance(candidate, Mapping) else {}
        status = str(result.get("status"))
        status = status if status in valid_statuses else "uncertain"
        references = result.get("evidenceKeys")
        if not isinstance(references, list):
            references = result.get("evidenceReferences")
        normalized.append(
            {
                "ruleId": requirement.get("id"),
                "code": requirement.get("code"),
                "name": requirement.get("name"),
                "status": status,
                "rationale": _safe_text(
                    result.get("rationale"),
                    (
                        "The extracted record did not provide enough validated evidence for "
                        "an automatic result."
                        if status == "uncertain"
                        else "Compared with the active tenant immunization policy."
                    ),
                    800,
                ),
                "evidenceKeys": (
                    [
                        item
                        for item in references
                        if isinstance(item, str) and item in evidence_keys
                    ][:20]
                    if isinstance(references, list)
                    else []
                ),
            }
        )
    raw_warnings = _as_list(value.get("warnings"))
    return {
        "policyVersionId": policy.get("id"),
        "policyVersion": f"{policy.get('code')}:v{policy.get('version')}",
        "requirements": normalized,
        "warnings": [warning for item in raw_warnings if (warning := _safe_text(item, "", 400))][
            :12
        ],
        "generatedAt": _utc_now(),
    }


def _first_present(values: Sequence[Mapping[str, Any]], key: str) -> object | None:
    return next((value[key] for value in values if value.get(key)), None)


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_metadata_text(
    metadata: Mapping[str, Any], keys: Sequence[str], maximum: int
) -> str | None:
    for key in keys:
        value = _nullable_text(metadata.get(key), maximum)
        if value is not None:
            return value
    return None


def _canonical_metadata_key(value: object) -> str:
    raw = str(value or "").strip()
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    return re.sub(r"[^a-z0-9]+", "_", snake_case.lower()).strip("_")[:80]


def _is_sensitive_identity_key(value: object) -> bool:
    key = _canonical_metadata_key(value)
    if not key:
        return False
    compact = key.replace("_", "")
    if compact in {
        "kimliknumarasi",
        "tckn",
        "tckimlikno",
        "tckimliknumarasi",
        "socialsecuritynumber",
        "ssn",
    }:
        return True
    tokens = set(key.split("_"))
    identity_markers = {
        "card",
        "citizen",
        "document",
        "government",
        "id",
        "identification",
        "identity",
        "kimlik",
        "licence",
        "license",
        "national",
        "passport",
        "serial",
    }
    number_markers = {
        "id",
        "identifier",
        "no",
        "num",
        "numarasi",
        "number",
        "serial",
    }
    if tokens & identity_markers and tokens & number_markers:
        return True
    compact_identity_markers = (
        "citizen",
        "document",
        "government",
        "identification",
        "identity",
        "kimlik",
        "licence",
        "license",
        "nationalid",
        "passport",
    )
    has_compact_identity_marker = any(marker in compact for marker in compact_identity_markers)
    if has_compact_identity_marker and compact.endswith(("id", "no", "num")):
        return True
    compact_number_markers = ("identifier", "kimlikno", "numarasi", "number", "serial")
    return has_compact_identity_marker and any(
        marker in compact for marker in compact_number_markers
    )


def _safe_text(value: object, fallback: str, maximum: int) -> str:
    if not isinstance(value, str):
        return fallback
    result = value.strip()[:maximum]
    return result or fallback


def _nullable_text(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    result = _safe_text(value, "", maximum)
    return result or None


def _normalized_number(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number)) if math.isfinite(number) else 0.0


def _redact(value: str) -> str:
    if re.search(r"\b\d{3}-?\d{2}-?\d{4}\b", value) or re.search(r"\b(?:\d[ -]*?){9,19}\b", value):
        return "[sensitive value redacted]"
    return value


def _redact_identity(value: str) -> str:
    if _redact(value) != value or re.search(
        r"\b(?=[A-Za-z0-9-]{6,24}\b)(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)"
        r"[A-Za-z0-9-]+\b",
        value,
    ):
        return "[sensitive value redacted]"
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
