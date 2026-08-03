import pytest

from audentra.core.errors import BadRequestError
from audentra.domain.onboarding import validate_onboarding_step

ABOUT_YOU_IDENTITY = {
    "firstName": "Alex",
    "lastName": "Morgan",
    "preferredName": "Alex",
    "personalEmail": "alex@example.edu",
    "mobilePhone": "+1555010300",
    "citizenshipStatus": "us_citizen",
    "communicationPreference": "email",
    "residencyStatus": "domestic",
}


def test_about_you_address_and_residency_are_optional_by_default() -> None:
    validate_onboarding_step("about_you", ABOUT_YOU_IDENTITY)


def test_staff_configured_about_you_field_is_enforced() -> None:
    with pytest.raises(BadRequestError, match="marked as required"):
        validate_onboarding_step(
            "about_you",
            ABOUT_YOU_IDENTITY,
            about_you_required_fields=("firstName", "streetAddress"),
        )


def test_staff_configured_custom_about_you_field_is_enforced() -> None:
    with pytest.raises(BadRequestError, match="marked as required"):
        validate_onboarding_step(
            "about_you",
            {**ABOUT_YOU_IDENTITY, "customFields": {}},
            about_you_required_custom_fields=("pronouns",),
        )

    validate_onboarding_step(
        "about_you",
        {
            **ABOUT_YOU_IDENTITY,
            "customFields": {"pronouns": "they/them"},
        },
        about_you_required_custom_fields=("pronouns",),
    )
