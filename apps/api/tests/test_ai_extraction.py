import pytest

from audentra.integrations.ai.extraction import (
    adapt_legacy_identity_extraction,
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


@pytest.mark.parametrize(
    ("metadata", "sensitive_keys"),
    [
        (
            {
                "date_of_birth": "2000-01-01",
                "document_number": "DOC-123456",
                "expiry_date": "2030-01-01",
                "father_name": "Parent One",
                "gender": "F",
                "identity_card_number": "CARD-654321",
                "issued_by": "Civil Registry",
                "mother_name": "Parent Two",
                "name": "Ada Example",
                "nationality": "Turkish",
            },
            {"document_number", "identity_card_number"},
        ),
        (
            {
                "date_of_birth": "2000-01-01",
                "document_number": "DOC-123456",
                "father_name": "Parent One",
                "gender": "F",
                "identity_number": "10000000146",
                "issued_by": "Civil Registry",
                "mother_name": "Parent Two",
                "name": "Ada Example",
                "nationality": "Turkish",
                "valid_until": "2030-01-01",
            },
            {"document_number", "identity_number"},
        ),
    ],
)
def test_adapts_production_identity_metadata_wrapper_without_identifier_fields(
    metadata: dict[str, str], sensitive_keys: set[str]
) -> None:
    warning = "Sensitive identity numbers were not retained for student review."
    adapted = adapt_legacy_identity_extraction(
        {"metadata": metadata, "warning": warning}, "identity"
    )
    result = normalize_extraction(adapted, "test/model")

    assert result["documentType"] == "identity"
    assert result["studentName"] == "Ada Example"
    assert result["institutionName"] == "Civil Registry"
    assert result["warnings"] == [warning]
    field_keys = {field["key"] for field in result["fields"]}
    assert field_keys.isdisjoint(sensitive_keys)
    assert {"date_of_birth", "name", "nationality"} <= field_keys
    assert not any(value in str(result) for value in ("DOC-123456", "CARD-654321", "10000000146"))


def test_canonical_identity_fields_drop_document_identifier_aliases() -> None:
    result = normalize_extraction(
        {
            "documentType": "identity",
            "summary": "Identity number 100 000 001 46 was visible.",
            "studentName": "Ada Example 100-000-001-46",
            "institutionName": "Registry AB1234567",
            "fields": [
                {
                    "key": "governmentIdNumber",
                    "label": "Government ID number",
                    "value": "ID-123",
                },
                {"key": "passport_no", "label": "Passport no", "value": "P-456"},
                {
                    "key": "tc_kimlik_numarasi",
                    "label": "TC Kimlik Numarasi",
                    "value": "10000000146",
                },
                {"key": "nationality", "label": "Nationality", "value": "Turkish"},
                {
                    "key": "reference",
                    "label": "Reference",
                    "value": "AB1234567",
                },
            ],
            "warnings": [
                "Identity number 100 000 001 46 requires manual review.",
                "Passport AB1234567 was visible.",
            ],
        },
        "test/model",
    )

    assert result["summary"] == (
        "Identity document details were extracted and are ready for review."
    )
    assert result["studentName"] == "[sensitive value redacted]"
    assert result["institutionName"] == "[sensitive value redacted]"
    assert result["fields"] == [
        {"key": "nationality", "label": "Nationality", "value": "Turkish", "confidence": 0.0}
    ]
    assert result["warnings"] == [
        "[sensitive value redacted]",
        "[sensitive value redacted]",
    ]


def test_legacy_identity_adapter_discards_unexpected_metadata_fields() -> None:
    adapted = adapt_legacy_identity_extraction(
        {
            "metadata": {
                "name": "Ada Example",
                "nationality": "Turkish",
                "signature": "raw-signature-data",
                "diagnosis": "private diagnosis",
                "account_number": "ACCT-123",
                "biometric_template": "private-biometric-data",
                "unexpected": "untrusted extra value",
            },
            "warning": "Review the extracted values.",
        },
        "identity",
    )

    assert {field["key"] for field in adapted["fields"]} == {"name", "nationality"}
    assert "raw-signature-data" not in str(adapted)
    assert "private diagnosis" not in str(adapted)
    assert "ACCT-123" not in str(adapted)
    assert "private-biometric-data" not in str(adapted)
    assert "untrusted extra value" not in str(adapted)


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


def test_useful_extraction_rejects_expected_document_type_mismatch() -> None:
    assert not useful_extraction(
        {"documentType": "other", "fields": [], "courses": []},
        "identity",
    )
    assert not useful_extraction(
        {"documentType": "identity", "fields": [], "courses": []}, "identity"
    )
    assert useful_extraction(
        {
            "documentType": "identity",
            "fields": [{"key": "nationality", "label": "Nationality", "value": "Turkish"}],
            "courses": [],
        },
        "identity",
    )
    result = evidence_mismatch("ferpa", "transcript")
    assert result["documentType"] == "transcript"
    assert result["provider"] == "local"


@pytest.mark.parametrize("document_type", ["immunization", "ferpa", "residency", "other"])
def test_non_financial_extraction_requires_material_evidence(document_type: str) -> None:
    empty = {
        "documentType": document_type,
        "summary": "The document was parsed and is ready for review.",
        "fields": [],
        "courses": [],
    }
    assert not useful_extraction(empty, document_type)
    assert useful_extraction(
        {
            **empty,
            "fields": [
                {
                    "key": "document_date",
                    "label": "Document date",
                    "value": "2026-08-05",
                }
            ],
        },
        document_type,
    )
    assert useful_extraction({**empty, "institutionName": "Aster University"}, document_type)


def test_field_free_financial_classification_remains_useful() -> None:
    assert useful_extraction(
        {"documentType": "financial_aid", "fields": [], "courses": []},
        "financial_aid",
    )


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
