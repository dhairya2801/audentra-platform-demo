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


def normalize_extraction(
    value: Mapping[str, Any],
    model: str,
    evidence_document_type: str | None = None,
    provider: str = "openrouter",
) -> dict[str, Any]:
    raw_fields = _as_list(value.get("fields"))
    fields: list[dict[str, Any]] = []
    for index, candidate in enumerate(raw_fields[:24]):
        field = candidate if isinstance(candidate, Mapping) else {}
        fields.append(
            {
                "key": _safe_text(field.get("key"), f"field_{index + 1}", 80),
                "label": _safe_text(field.get("label"), f"Field {index + 1}", 120),
                "value": _redact(_safe_text(field.get("value"), "", 500)),
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
    warnings = [text for item in raw_warnings[:12] if (text := _safe_text(item, "", 400))]
    return {
        "status": "completed",
        "documentType": normalize_document_type(value.get("documentType"), evidence_document_type),
        "summary": _safe_text(
            value.get("summary"), "The document was parsed and is ready for review.", 800
        ),
        "studentName": _nullable_text(value.get("studentName"), 160),
        "institutionName": _nullable_text(value.get("institutionName"), 200),
        "issueDate": _nullable_text(value.get("issueDate"), 80),
        "academicTerm": _nullable_text(value.get("academicTerm"), 120),
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
    if re.search(r"\b\d{3}-?\d{2}-?\d{4}\b", value) or re.search(r"\b(?:\d[ -]*?){13,19}\b", value):
        return "[sensitive value redacted]"
    return value


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
