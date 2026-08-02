import pytest

from audentra.integrations.ai.extraction import (
    evidence_classification,
    evidence_mismatch,
    infer_type_from_evidence,
    merge_transcript_extractions,
    normalize_course_exemptions,
    normalize_extraction,
    normalize_immunization_compliance,
    parse_extraction_json,
    useful_extraction,
)
from audentra.integrations.ai.provider import ProviderCompletionError


def test_selects_complete_extraction_after_reasoning_example() -> None:
    value = parse_extraction_json(
        'Reasoning {"example":true}\nFinal {"documentType":"transcript","summary":"ok",'
        '"fields":[],"courses":[{"title":"Math"}],"warnings":[]}'
    )
    assert value["documentType"] == "transcript"


def test_rejects_truncated_json() -> None:
    with pytest.raises(ProviderCompletionError, match="incomplete"):
        parse_extraction_json('{"documentType":"transcript"')


def test_normalizes_and_redacts_untrusted_fields() -> None:
    result = normalize_extraction(
        {
            "documentType": "academic-record",
            "summary": " Parsed ",
            "fields": [{"key": "ssn", "label": "SSN", "value": "123-45-6789", "confidence": 4}],
            "courses": [{"title": "Calculus", "credits": 99, "confidence": -2}],
            "visualRegions": [
                {
                    "kind": "profile_photo",
                    "pageNumber": 1,
                    "x": 0.8,
                    "y": 0.8,
                    "width": 0.7,
                    "height": 0.7,
                    "confidence": 2,
                }
            ],
        },
        "test/model",
    )
    assert result["documentType"] == "transcript"
    assert result["fields"][0]["value"] == "[sensitive value redacted]"
    assert result["fields"][0]["confidence"] == 1
    assert result["courses"][0]["credits"] == 20
    assert result["visualRegions"][0]["width"] == pytest.approx(0.2)


def test_conservation_merge_never_drops_distinct_courses() -> None:
    first = normalize_extraction(
        {"documentType": "transcript", "courses": [{"title": "Math", "confidence": 0.8}]},
        "m",
    )
    second = normalize_extraction(
        {"documentType": "transcript", "courses": [{"title": "History", "confidence": 0.9}]},
        "m",
    )
    merged = merge_transcript_extractions([first, second])
    assert {course["title"] for course in merged["courses"]} == {"Math", "History"}
    assert useful_extraction(merged, "transcript")


def test_evidence_mismatch_is_actionable_without_provider_call() -> None:
    assert infer_type_from_evidence("OFFICIAL TRANSCRIPT") == "transcript"
    assert infer_type_from_evidence("Restaurant Menu - Chef Special") == "other"
    assert infer_type_from_evidence("Free Application for Federal Student Aid") == "financial_aid"
    result = evidence_mismatch("ferpa", "transcript")
    assert result["documentType"] == "transcript"
    assert result["provider"] == "local"


def test_local_evidence_classification_retains_no_financial_fields() -> None:
    result = evidence_classification("financial_aid")
    assert result["status"] == "completed"
    assert result["documentType"] == "financial_aid"
    assert result["fields"] == []
    assert result["courses"] == []
    assert result["provider"] == "local"


def test_exemption_ids_are_restricted_to_active_tenant_context() -> None:
    context = {
        "program": {"id": "program-1"},
        "catalogVersion": {"id": "catalog-1"},
        "policyVersion": "policy-v1",
        "catalogCourses": [{"id": "target-1"}],
        "programRequirements": [],
        "prerequisites": [],
        "equivalencyRules": [{"id": "rule-1", "targetCourseId": "target-1"}],
    }
    result = normalize_course_exemptions(
        {
            "decisions": [
                {
                    "sourceCourseId": "course:1",
                    "status": "matched",
                    "targetCourseId": "invented-target",
                    "equivalencyRuleId": "invented-rule",
                    "contextIds": ["program-1", "foreign-id"],
                }
            ]
        },
        [{"sourceCode": "MATH", "title": "Math"}],
        context,
    )
    assert result["decisions"][0]["status"] == "policy_gap"
    assert result["decisions"][0]["targetCourseId"] is None
    assert result["decisions"][0]["contextIds"] == ["program-1"]


def test_immunization_has_one_result_per_active_rule_and_filters_evidence() -> None:
    result = normalize_immunization_compliance(
        {
            "requirements": [
                {
                    "ruleId": "rule-1",
                    "status": "met",
                    "evidenceKeys": ["vaccine_date", "invented"],
                }
            ]
        },
        {
            "policyVersion": {"id": "policy-1", "code": "health", "version": 3},
            "requirements": [
                {"id": "rule-1", "code": "mmr", "name": "MMR"},
                {"id": "rule-2", "code": "tb", "name": "TB"},
            ],
        },
        {"fields": [{"key": "vaccine_date", "value": "2026-01-01"}]},
    )
    assert len(result["requirements"]) == 2
    assert result["requirements"][0]["evidenceKeys"] == ["vaccine_date"]
    assert result["requirements"][1]["status"] == "uncertain"
