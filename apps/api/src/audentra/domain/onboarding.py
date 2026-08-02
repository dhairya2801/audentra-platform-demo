"""Ordered onboarding policy shared by every inbound adapter."""

from collections.abc import Mapping
from typing import Any

from audentra.core.errors import BadRequestError

ONBOARDING_STEPS: tuple[str, ...] = (
    "offer",
    "about_you",
    "housing",
    "campus_life",
    "emergency_contacts",
    "family_permissions",
    "review_and_sign",
    "deposit",
)
SKIPPABLE_ONBOARDING_STEPS = frozenset({"campus_life", "deposit"})


def is_skippable_onboarding_step(step: str) -> bool:
    return step in SKIPPABLE_ONBOARDING_STEPS


def validate_onboarding_step(step: str, data: Mapping[str, Any]) -> None:
    """Apply the same cross-field policy as the Nest onboarding service."""

    def invalid(message: str) -> None:
        raise BadRequestError("ONBOARDING_STEP_INVALID", message)

    if step == "offer" or step in {"campus_life", "family_permissions"}:
        return
    if step == "about_you":
        required = (
            "firstName",
            "lastName",
            "preferredName",
            "personalEmail",
            "mobilePhone",
            "citizenshipStatus",
            "streetAddress",
            "city",
            "stateOrProvince",
            "postalCode",
            "country",
            "communicationPreference",
            "residencyStatus",
            "residencyVerificationPath",
        )
        if any(not data.get(field) for field in required):
            invalid(
                "Enter your legal and preferred name, personal contact details, "
                "citizenship status, permanent home address, and residency review path"
            )
        return
    if step == "housing":
        if not data.get("housingPreference"):
            invalid("Choose a housing preference")
        return
    if step == "emergency_contacts":
        if not data.get("emergencyContacts"):
            invalid("Add at least one emergency contact")
        return
    if step == "review_and_sign":
        method = data.get("signatureMethod")
        if (
            not data.get("signatureFullName")
            or not method
            or data.get("signatureConsent") is not True
            or not data.get("signedDocumentIds")
            or (method == "drawn" and not data.get("signatureImageData"))
        ):
            invalid(
                "Review the document packet, choose a signature method, and provide "
                "your electronic signature"
            )
        return
    if step == "deposit" and not data.get("depositChoice"):
        invalid("Choose how you would like to handle the enrollment deposit")
