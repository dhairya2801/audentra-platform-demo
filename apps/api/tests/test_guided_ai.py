from audentra.integrations.ai.guided import infer_document_type, pending_extraction


def test_unconfigured_provider_returns_reviewable_pending_extraction() -> None:
    result = pending_extraction("fall-transcript.pdf", "transcript", "groq")
    assert result["status"] == "pending_configuration"
    assert result["documentType"] == "transcript"
    assert result["retryable"] is True
    assert "GROQ_API_KEY" in result["summary"]


def test_document_type_inference_covers_known_categories() -> None:
    assert infer_document_type("passport.png") == "identity"
    assert infer_document_type("fafsa-2026.pdf") == "financial_aid"
    assert infer_document_type("spring-transcript.pdf") == "transcript"
    assert infer_document_type("immunization-record.pdf") == "immunization"
    assert infer_document_type("mystery.bin") == "other"
