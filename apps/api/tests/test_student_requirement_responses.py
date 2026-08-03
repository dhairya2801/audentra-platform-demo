from __future__ import annotations

import pytest

from audentra.core.errors import ApiError
from audentra.infrastructure.postgres.portal_repository import (
    _bounded_response_value,
    _map_requirement,
    _normalize_requirement_response,
    _strict_response_object,
)


def test_requirement_response_contract_validates_every_generic_interaction() -> None:
    assert _normalize_requirement_response("information", {}, {"acknowledged": True}) == {
        "acknowledged": True
    }
    assert _normalize_requirement_response("approval", {}, {"approved": True}) == {"approved": True}
    assert _normalize_requirement_response(
        "single_select",
        {"options": ["A", "B"]},
        {"selectedOption": "B"},
    ) == {"selectedOption": "B"}
    assert _normalize_requirement_response(
        "multiple_select",
        {"options": ["A", "B", "C"], "maximumSelections": 2},
        {"selectedOptions": ["A", "C"]},
    ) == {"selectedOptions": ["A", "C"]}
    assert _normalize_requirement_response(
        "signature",
        {"signatureProvider": "built_in"},
        {"accepted": True, "signerName": "Alex Morgan"},
    ) == {
        "accepted": True,
        "signerName": "Alex Morgan",
        "signatureMethod": "typed",
    }


def test_selection_flow_enforces_required_conditional_types_options_and_maximum() -> None:
    config = {
        "flow": [
            {
                "id": "living_plan",
                "field_type": "single_select",
                "required": True,
                "options": ["campus", "commute"],
            },
            {
                "id": "residence",
                "field_type": "multiple_select",
                "required": True,
                "options": ["north", "south"],
                "maximum_selections": 1,
                "when": {"field": "living_plan", "equals": "campus"},
            },
        ]
    }

    assert _normalize_requirement_response(
        "selection_flow",
        config,
        {"values": {"living_plan": "commute"}},
    ) == {"values": {"living_plan": "commute"}}
    with pytest.raises(ApiError, match="residence is required"):
        _normalize_requirement_response(
            "selection_flow",
            config,
            {"values": {"living_plan": "campus"}},
        )
    with pytest.raises(ApiError, match="too many selections"):
        _normalize_requirement_response(
            "selection_flow",
            config,
            {
                "values": {
                    "living_plan": "campus",
                    "residence": ["north", "south"],
                }
            },
        )


@pytest.mark.parametrize(
    ("interaction_type", "config", "response"),
    [
        ("single_select", {"options": ["A"]}, {"selectedOption": "B"}),
        (
            "multiple_select",
            {"options": ["A", "B"], "maximumSelections": 1},
            {"selectedOptions": ["A", "B"]},
        ),
        ("information", {}, {"acknowledged": False}),
        ("approval", {}, {"approved": False}),
        ("form", {}, {"values": {"notes": "x" * 2_001}}),
        ("form", {}, {"values": {"nested": {"a": {"b": {"c": {"d": {"e": 1}}}}}}}),
    ],
)
def test_requirement_response_rejects_invalid_or_unbounded_payloads(
    interaction_type: str,
    config: dict[str, object],
    response: dict[str, object],
) -> None:
    with pytest.raises(ApiError) as error:
        _normalize_requirement_response(interaction_type, config, response)

    assert error.value.code == "INVALID_REQUIREMENT_RESPONSE"


def test_requirement_mapping_exposes_optimistic_version_and_latest_response() -> None:
    mapped = _map_requirement(
        {
            "id": "00000000-0000-7000-8000-000000000001",
            "journey_id": "00000000-0000-7000-8000-000000000002",
            "code": "meal_plan",
            "title": "Choose a meal plan",
            "description": "Select one.",
            "status": "completed",
            "version": 4,
            "blocking": True,
            "due_at": None,
            "progress_percent": 100,
            "submission_type": "form",
            "flow_kind": "enrollment",
            "interaction_type": "single_select",
            "input_config": {"options": ["Standard", "Vegetarian"]},
            "display_order": 10,
            "responsible_office": "Dining",
            "depends_on_codes": [],
            "response_id": "00000000-0000-7000-8000-000000000003",
            "response_interaction_type": "single_select",
            "response_data": {"selectedOption": "Vegetarian"},
            "response_version": 1,
            "response_submitted_at": "2028-01-15T12:00:00Z",
        }
    )

    assert mapped["version"] == 4
    assert mapped["response"]["data"] == {"selectedOption": "Vegetarian"}


def test_bounded_response_accepts_json_scalars_and_rejects_hostile_shapes() -> None:
    assert _bounded_response_value(
        {"none": None, "flag": True, "count": 2, "ratio": 1.5, "items": ["a"]}
    ) == {"none": None, "flag": True, "count": 2, "ratio": 1.5, "items": ["a"]}

    hostile_values: list[object] = [
        float("inf"),
        {f"key_{index}": index for index in range(101)},
        {"": "blank key"},
        list(range(101)),
        object(),
    ]
    for value in hostile_values:
        with pytest.raises(ApiError) as error:
            _bounded_response_value(value)
        assert error.value.code == "INVALID_REQUIREMENT_RESPONSE"

    with pytest.raises(ApiError, match="too many values"):
        _bounded_response_value(list(range(100)), counter=[101])
    with pytest.raises(ApiError, match="response must be an object"):
        _strict_response_object(["not", "an", "object"])
    with pytest.raises(ApiError, match="too large"):
        _strict_response_object({f"field_{index}": "x" * 2_000 for index in range(26)})


@pytest.mark.parametrize(
    ("fields", "values", "message"),
    [
        ([{"title": "Missing id"}], {}, "invalid field"),
        (
            [{"id": "known", "field_type": "text"}],
            {"unknown": "x"},
            "unknown field",
        ),
        (
            [{"id": "name", "field_type": "text", "required": True}],
            {"name": 42},
            "must be text",
        ),
        (
            [{"id": "email", "field_type": "email", "required": True}],
            {"email": "not-an-email"},
            "must be an email",
        ),
        (
            [{"id": "birthday", "field_type": "date", "required": True}],
            {"birthday": "tomorrow"},
            "must be an ISO date",
        ),
        (
            [{"id": "consent", "field_type": "checkbox", "required": True}],
            {"consent": "yes"},
            "must be true or false",
        ),
        (
            [{"id": "campus", "field_type": "single_select", "options": ["north"]}],
            {"campus": "south"},
            "invalid option",
        ),
        (
            [{"id": "clubs", "field_type": "multiple_select", "options": ["art"]}],
            {"clubs": "art"},
            "selection list",
        ),
        (
            [{"id": "clubs", "field_type": "multiple_select", "options": ["art"]}],
            {"clubs": ["art", "art"]},
            "invalid selections",
        ),
        (
            [{"id": "score", "field_type": "number"}],
            {"score": 1},
            "unsupported published type",
        ),
    ],
)
def test_form_field_validation_rejects_schema_and_scalar_mismatches(
    fields: list[dict[str, object]],
    values: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ApiError, match=message) as error:
        _normalize_requirement_response("form", {"fields": fields}, {"values": values})

    assert error.value.code == "INVALID_REQUIREMENT_RESPONSE"


def test_form_conditions_and_optional_fields_are_applied_without_stale_values() -> None:
    fields = [
        {
            "id": "kind",
            "field_type": "single_select",
            "required": True,
            "options": ["yes", "no"],
        },
        {
            "id": "details",
            "field_type": "text",
            "when": {"field": "kind", "equals": "yes"},
        },
        {"id": "nickname", "field_type": "text", "required": False},
    ]
    assert _normalize_requirement_response(
        "form",
        {"fields": fields},
        {"values": {"kind": "no"}},
    ) == {"values": {"kind": "no"}}
    with pytest.raises(ApiError, match="not applicable"):
        _normalize_requirement_response(
            "form",
            {"fields": fields},
            {"values": {"kind": "no", "details": "stale hidden value"}},
        )


def test_generic_contract_rejects_wrong_keys_types_signature_and_specialized_flows() -> None:
    invalid_cases = [
        ("information", {}, {"acknowledged": True, "extra": True}),
        ("form", {}, {"values": []}),
        ("multiple_select", {"options": ["A"]}, {"selectedOptions": "A"}),
        ("signature", {}, {"accepted": False, "signerName": "Alex"}),
        ("signature", {}, {"accepted": True, "signerName": ""}),
        (
            "signature",
            {},
            {"accepted": True, "signerName": "Alex", "signatureMethod": "stamp"},
        ),
        ("scheduling", {}, {"appointmentId": "not-a-uuid"}),
        ("upload_file", {}, {}),
    ]
    for interaction_type, config, response in invalid_cases:
        with pytest.raises(ApiError):
            _normalize_requirement_response(interaction_type, config, response)

    appointment_id = "00000000-0000-7000-8000-000000000900"
    assert _normalize_requirement_response("scheduling", {}, {"appointmentId": appointment_id}) == {
        "appointmentId": appointment_id
    }
