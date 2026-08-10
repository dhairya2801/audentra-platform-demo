from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from audentra.infrastructure.postgres.journey_routing import (
    evaluate_journey_activation,
    reconcile_student_journey_routes,
)


class _Mappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Mappings:
        return _Mappings(self._rows)


class _RecordingConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.updates: list[dict[str, object]] = []

    async def execute(self, statement: object, params: dict[str, object]) -> _Result:
        if "SELECT requirement.id" in str(statement):
            return _Result(self.rows)
        self.updates.append(dict(params))
        return _Result([])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({}, "unresolved"),
        ({"living_plan": {"selectedOption": "Yes"}}, "applicable"),
        ({"living_plan": {"selectedOption": "No"}}, "not_applicable"),
    ],
)
def test_single_answer_route_is_deterministic(
    response: dict[str, dict[str, object]],
    expected: str,
) -> None:
    activation = {
        "match": "all",
        "rules": [
            {
                "sourceTaskId": "living_plan",
                "fieldId": "$answer",
                "operator": "equals",
                "value": "Yes",
            }
        ],
    }

    assert evaluate_journey_activation(activation, response) == expected


def test_route_reads_form_fields_and_multiple_selection_membership() -> None:
    responses = {
        "preferences": {
            "values": {
                "needs_support": True,
                "interests": ["Housing", "Financial aid"],
            }
        }
    }
    activation = {
        "match": "all",
        "rules": [
            {
                "sourceTaskId": "preferences",
                "fieldId": "needs_support",
                "operator": "equals",
                "value": True,
            },
            {
                "sourceTaskId": "preferences",
                "fieldId": "interests",
                "operator": "contains",
                "value": "Housing",
            },
        ],
    }

    assert evaluate_journey_activation(activation, responses) == "applicable"


@pytest.mark.parametrize(
    ("operator", "value", "answer", "expected"),
    [
        ("one_of", ["Off campus", "With family"], "With family", "applicable"),
        ("one_of", ["Off campus", "With family"], "On campus", "not_applicable"),
        ("none_of", ["On campus", "Off campus"], "Not sure", "applicable"),
        ("none_of", ["On campus", "Off campus"], "On campus", "not_applicable"),
    ],
)
def test_switch_case_and_default_routes_are_deterministic(
    operator: str,
    value: list[str],
    answer: str,
    expected: str,
) -> None:
    activation = {
        "match": "all",
        "rules": [
            {
                "sourceTaskId": "living_plan",
                "fieldId": "$answer",
                "operator": operator,
                "value": value,
            }
        ],
    }

    assert (
        evaluate_journey_activation(
            activation,
            {"living_plan": {"selectedOption": answer}},
        )
        == expected
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [(49, "not_applicable"), (50, "applicable"), (79.5, "applicable"), (80, "not_applicable")],
)
def test_threshold_range_uses_multiple_numeric_rules(score: float, expected: str) -> None:
    activation = {
        "match": "all",
        "rules": [
            {
                "sourceTaskId": "assessment",
                "fieldId": "readiness_score",
                "operator": "greater_than_or_equal",
                "value": 50,
            },
            {
                "sourceTaskId": "assessment",
                "fieldId": "readiness_score",
                "operator": "less_than",
                "value": 80,
            },
        ],
    }

    assert (
        evaluate_journey_activation(
            activation,
            {"assessment": {"values": {"readiness_score": score}}},
        )
        == expected
    )


def test_any_route_waits_for_unresolved_answers_before_excluding_a_path() -> None:
    activation = {
        "match": "any",
        "rules": [
            {
                "sourceTaskId": "living_plan",
                "fieldId": "$answer",
                "operator": "equals",
                "value": "Yes",
            },
            {
                "sourceTaskId": "support_plan",
                "fieldId": "$answer",
                "operator": "equals",
                "value": "Yes",
            },
        ],
    }

    assert (
        evaluate_journey_activation(
            activation,
            {"living_plan": {"selectedOption": "No"}},
        )
        == "unresolved"
    )
    assert (
        evaluate_journey_activation(
            activation,
            {
                "living_plan": {"selectedOption": "No"},
                "support_plan": {"selectedOption": "No"},
            },
        )
        == "not_applicable"
    )


@pytest.mark.anyio
async def test_route_reconciliation_skips_false_branch_and_safely_converges() -> None:
    decision_response = {"selectedOption": "Yes"}
    rows: list[dict[str, object]] = [
        {
            "id": "decision-id",
            "code": "living_plan",
            "status": "completed",
            "progress_percent": 100,
            "depends_on_codes": [],
            "activation_rules": {"match": "all", "rules": []},
            "response_data": decision_response,
        },
        {
            "id": "housing-id",
            "code": "housing_application",
            "status": "blocked",
            "progress_percent": 0,
            "depends_on_codes": ["living_plan"],
            "activation_rules": {
                "match": "all",
                "rules": [
                    {
                        "sourceTaskId": "living_plan",
                        "fieldId": "$answer",
                        "operator": "equals",
                        "value": "Yes",
                    }
                ],
            },
            "response_data": None,
        },
        {
            "id": "commuter-id",
            "code": "commuter_setup",
            "status": "blocked",
            "progress_percent": 0,
            "depends_on_codes": ["living_plan"],
            "activation_rules": {
                "match": "all",
                "rules": [
                    {
                        "sourceTaskId": "living_plan",
                        "fieldId": "$answer",
                        "operator": "equals",
                        "value": "No",
                    }
                ],
            },
            "response_data": None,
        },
        {
            "id": "merge-id",
            "code": "arrival_plan",
            "status": "blocked",
            "progress_percent": 0,
            "depends_on_codes": [
                "living_plan",
                "housing_application",
                "commuter_setup",
            ],
            "activation_rules": {"match": "all", "rules": []},
            "response_data": None,
        },
    ]
    connection = _RecordingConnection(rows)

    first = await reconcile_student_journey_routes(
        cast(AsyncConnection, cast(Any, connection)),
        tenant_id="tenant-id",
        student_id="student-id",
        journey_id="journey-id",
    )

    assert {(item.code, item.status) for item in first} == {
        ("housing_application", "ready"),
        ("commuter_setup", "not_applicable"),
    }
    skipped_update = next(
        update for update in connection.updates if update.get("status") == "not_applicable"
    )
    assert skipped_update["progress"] == 100

    rows[1]["status"] = "completed"
    rows[1]["progress_percent"] = 100
    rows[2]["status"] = "not_applicable"
    connection = _RecordingConnection(rows)
    second = await reconcile_student_journey_routes(
        cast(AsyncConnection, cast(Any, connection)),
        tenant_id="tenant-id",
        student_id="student-id",
        journey_id="journey-id",
    )

    assert [(item.code, item.status) for item in second] == [("arrival_plan", "ready")]
