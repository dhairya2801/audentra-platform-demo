from audentra.domain.conference_documents import validate_evidence
from audentra.domain.document_evidence import review_checks


def field(key: str, value: str, confidence: float = 0.99) -> dict[str, object]:
    return {"key": key, "value": value, "confidence": confidence}


def test_wrong_type_is_pending_retry_not_a_passport_submission() -> None:
    result = validate_evidence({"status": "completed", "documentType": "transcript"}, "passport")
    assert result["outcome"] == "wrong_type"
    assert result["message"] == "This appears to be a transcript. Please upload your passport."


def test_provider_failure_does_not_discard_original_or_claim_detected_type() -> None:
    result = validate_evidence({"status": "failed", "documentType": "identity"}, "passport")
    assert result["outcome"] == "failed"
    assert "original is saved" in result["message"]
    checks = review_checks({"status": "failed"}, "identity", "Ada Kettleby")
    assert next(c for c in checks if c["key"] == "type")["status"] == "attention"


def test_repeated_vaccination_rows_satisfy_extraction_without_summary_field() -> None:
    extraction = {
        "status": "completed",
        "documentType": "immunization",
        "studentName": "Ada Kettleby",
        "institutionName": "Willow Creek Clinic",
        "fields": [field("date_of_birth", "2007-03-08"), field("vaccination_1", "2008-04-10")],
    }
    assert validate_evidence(extraction, "immunization")["outcome"] == "matched"
    extraction["fields"] = [field("date_of_birth", "2007-03-08")]
    assert "vaccination_records" in validate_evidence(extraction, "immunization")["missingFields"]


def test_incomplete_and_low_confidence_values_are_not_filled_in() -> None:
    extraction = {
        "status": "completed",
        "documentType": "transcript",
        "studentName": "Ada",
        "institutionName": "Cedar Vale",
        "courses": [],
        "fields": [field("gpa", "3.75", 0.4)],
    }
    result = validate_evidence(extraction, "transcript")
    assert result["outcome"] == "incomplete"
    assert result["missingFields"] == ["courses", "gpa"]
    assert extraction["courses"] == []


def test_unrecognized_content_is_distinct_from_known_wrong_type() -> None:
    result = validate_evidence({"status": "completed", "documentType": "other"}, "transcript")
    assert result["outcome"] == "unrecognized"
    assert "could not identify" in result["message"]


def test_expiry_is_a_derived_warning_separate_from_facts() -> None:
    extraction = {
        "status": "completed",
        "documentType": "identity",
        "studentName": "Ada",
        "fields": [field("expiry_date", "2001-01-01")],
    }
    checks = review_checks(extraction, "identity", "Ada")
    assert next(c for c in checks if c["key"] == "expiry")["status"] == "attention"
    assert extraction["fields"] == [field("expiry_date", "2001-01-01")]


def test_type_check_leaves_fields_empty_until_staff_parsing() -> None:
    evidence = {
        "status": "pending_staff",
        "documentType": "identity",
        "fields": [],
        "classification": {"documentSubtype": "passport", "readability": "readable"},
    }
    result = validate_evidence(evidence, "passport")
    assert result["outcome"] == "matched"
    assert result["missingFields"] == []
    assert evidence["fields"] == []
    assert "Awaiting staff parsing" in result["message"]
    evidence["classification"] = {"documentSubtype": "other", "readability": "readable"}
    assert validate_evidence(evidence, "passport")["outcome"] == "wrong_type"


def test_type_check_distinguishes_unreadable_from_ambiguous() -> None:
    evidence = {
        "status": "pending_staff",
        "documentType": "other",
        "classification": {"readability": "unreadable"},
    }
    assert validate_evidence(evidence, "passport")["outcome"] == "unreadable"
    evidence["classification"] = {"readability": "ambiguous"}
    assert validate_evidence(evidence, "passport")["outcome"] == "incomplete"
