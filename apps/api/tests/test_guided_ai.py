from audentra.integrations.ai.guided import (
    deterministic_response,
    guided_response,
    infer_document_type,
    pending_extraction,
)

CONTEXT = {
    "offerId": "offer-1",
    "depositAmountCents": 50_000,
    "depositPaid": False,
    "nextAction": {"title": "Upload transcript", "description": "Use Documents."},
}


def test_known_transactional_intent_uses_zero_token_response() -> None:
    response = deterministic_response("How do I pay my deposit?", CONTEXT)
    assert response is not None
    assert response["provider"] == "guided"
    assert response["widgets"][0]["offerId"] == "offer-1"


def test_open_question_is_left_for_configured_provider() -> None:
    assert deterministic_response("Explain course equivalency policy", CONTEXT) is None


def test_unconfigured_provider_returns_reviewable_pending_extraction() -> None:
    result = pending_extraction("fall-transcript.pdf", "transcript", "groq")
    assert result["status"] == "pending_configuration"
    assert result["documentType"] == "transcript"
    assert result["retryable"] is True
    assert "GROQ_API_KEY" in result["summary"]


def test_guided_document_action_never_claims_verification() -> None:
    result = guided_response("Upload my transcript", CONTEXT)
    assert result["widgets"][0]["category"] == "transcript"
    assert "verified" in result["message"]
    assert infer_document_type("passport.png") == "identity"
