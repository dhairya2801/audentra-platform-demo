"""Only explicit, bounded participant messages and original-document decisions are accepted."""

import pytest
from pydantic import ValidationError

from audentra.contracts.requests import DemoDocumentReviewRequest, DemoTaskWriteRequest


@pytest.mark.parametrize(
    "change",
    [
        {"body": "   "},
        {"body": "x" * 501},
        {"kind": "email"},
        {"expectedVersion": 0},
        {"expectedVersion": True},
        {"studentId": "forged"},
        {"startNewConversation": "true"},
    ],
)
def test_demo_message_rejects_invalid_commands(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DemoTaskWriteRequest.model_validate(
            {"kind": "message", "body": "Hello", "expectedVersion": 1, **change}
        )


@pytest.mark.parametrize("confirmation", [False, None, "true", 1])
def test_demo_review_requires_explicit_original_confirmation(confirmation: object) -> None:
    with pytest.raises(ValidationError):
        DemoDocumentReviewRequest.model_validate(
            {
                "workItemId": "00000000-0000-7000-8000-000000000001",
                "expectedWorkItemVersion": 1,
                "decision": "accepted",
                "note": "Reviewed the original",
                "notifyStudent": True,
                "originalReviewed": confirmation,
            }
        )
