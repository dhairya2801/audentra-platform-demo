# ruff: noqa: S608 -- schema identifiers are validated before interpolation.
"""Canonical answer-driven routing for materialized student journeys."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

JsonDict = dict[str, Any]
RouteState = Literal["applicable", "not_applicable", "unresolved"]

_SATISFIED_STATUSES = frozenset({"completed", "waived", "not_applicable"})
_MUTABLE_STATUSES = frozenset({"blocked", "ready", "not_applicable"})
_MISSING = object()
_SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class JourneyRouteTransition:
    requirement_id: str
    code: str
    previous_status: str
    status: str


def _mapping(value: object) -> JsonDict:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: object) -> list[object]:
    return (
        list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else []
    )


def _answer(response: Mapping[str, Any], field_id: str) -> object:
    if field_id == "$answer":
        if "selectedOption" in response:
            return response["selectedOption"]
        if "selectedOptions" in response:
            return response["selectedOptions"]
        return _MISSING
    values = _mapping(response.get("values"))
    return values.get(field_id, _MISSING)


def _rule_result(rule: Mapping[str, Any], responses: Mapping[str, JsonDict]) -> bool | None:
    source_task_id = str(rule.get("sourceTaskId") or "")
    response = responses.get(source_task_id)
    if response is None:
        return None
    answer = _answer(response, str(rule.get("fieldId") or ""))
    if answer is _MISSING:
        return None
    expected = rule.get("value")
    operator = str(rule.get("operator") or "equals")
    if operator == "equals":
        return answer == expected
    if operator == "not_equals":
        return answer != expected
    if operator == "one_of":
        return isinstance(expected, list) and answer in expected
    if operator == "none_of":
        return isinstance(expected, list) and answer not in expected
    if operator == "contains":
        return isinstance(answer, list) and expected in answer
    if operator == "not_contains":
        return isinstance(answer, list) and expected not in answer
    if operator in {
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
    }:
        if (
            not isinstance(answer, (int, float))
            or isinstance(answer, bool)
            or not isinstance(expected, (int, float))
            or isinstance(expected, bool)
            or not math.isfinite(float(answer))
            or not math.isfinite(float(expected))
        ):
            return None
        actual_number = float(answer)
        expected_number = float(expected)
        if operator == "greater_than":
            return actual_number > expected_number
        if operator == "greater_than_or_equal":
            return actual_number >= expected_number
        if operator == "less_than":
            return actual_number < expected_number
        return actual_number <= expected_number
    return None


def evaluate_journey_activation(
    activation: Mapping[str, Any],
    responses: Mapping[str, JsonDict],
) -> RouteState:
    """Resolve one task's route without guessing when an answer is unavailable."""

    rules = [_mapping(rule) for rule in _list(activation.get("rules"))]
    if not rules:
        return "applicable"
    results = [_rule_result(rule, responses) for rule in rules]
    if str(activation.get("match") or "all") == "any":
        if any(result is True for result in results):
            return "applicable"
        if all(result is False for result in results):
            return "not_applicable"
        return "unresolved"
    if any(result is False for result in results):
        return "not_applicable"
    if all(result is True for result in results):
        return "applicable"
    return "unresolved"


async def reconcile_student_journey_routes(
    connection: AsyncConnection,
    *,
    tenant_id: object,
    student_id: object,
    journey_id: object,
    schema: str = "public",
) -> list[JourneyRouteTransition]:
    """Reconcile mutable requirements after a response or journey publication.

    Started and terminal work is preserved. A false branch becomes
    ``not_applicable``; an unconditional descendant of only not-applicable
    prerequisites inherits that state, while a merge with at least one selected
    completed branch can continue.
    """

    if not _SQL_IDENTIFIER.fullmatch(schema):
        raise ValueError("schema is not a safe PostgreSQL identifier")
    requirement = f"{schema}.student_requirement"
    journey = f"{schema}.enrollment_journey"
    definition = f"{schema}.requirement_definition_version"
    definition_link = f"{schema}.journey_requirement_definition"
    response = f"{schema}.student_requirement_response"
    experience_update = f"{schema}.student_experience_update"

    result = await connection.execute(
        text(
            f"""
            SELECT requirement.id, requirement.status, requirement.progress_percent,
                   evidence_definition.code, current_definition.depends_on_codes,
                   current_definition.activation_rules, response.response_data
            FROM {requirement} requirement
            JOIN {journey} journey
              ON journey.id=requirement.journey_id
             AND journey.tenant_id=requirement.tenant_id
            JOIN {definition} evidence_definition
              ON evidence_definition.id=requirement.requirement_definition_version_id
             AND evidence_definition.tenant_id=requirement.tenant_id
            JOIN {definition_link} current_link
              ON current_link.journey_definition_version_id=
                 journey.journey_definition_version_id
            JOIN {definition} current_definition
              ON current_definition.id=current_link.requirement_definition_version_id
             AND current_definition.tenant_id=requirement.tenant_id
             AND current_definition.code=evidence_definition.code
            LEFT JOIN LATERAL (
              SELECT submitted.response_data
              FROM {response} submitted
              WHERE submitted.tenant_id=requirement.tenant_id
                AND submitted.requirement_id=requirement.id
              ORDER BY submitted.version DESC
              LIMIT 1
            ) response ON true
            WHERE requirement.tenant_id=:tenant_id
              AND journey.student_id=:student_id
              AND requirement.retired_at IS NULL
              AND journey.id=:journey_id
            ORDER BY current_definition.display_order, current_definition.code
            FOR UPDATE OF requirement
            """
        ),
        {
            "tenant_id": tenant_id,
            "student_id": student_id,
            "journey_id": journey_id,
        },
    )
    rows = [dict(row) for row in result.mappings().all()]
    responses = {
        str(row["code"]): _mapping(row.get("response_data"))
        for row in rows
        if isinstance(row.get("response_data"), Mapping)
    }
    current_status = {str(row["code"]): str(row["status"]) for row in rows}
    desired_status = dict(current_status)
    route_states = {
        str(row["code"]): evaluate_journey_activation(
            _mapping(row.get("activation_rules")), responses
        )
        for row in rows
    }

    # Resolve explicit conditional routes first so downstream propagation and
    # convergence see the selected and unselected branches in one transaction.
    for row in rows:
        code = str(row["code"])
        status = current_status[code]
        if status not in _MUTABLE_STATUSES or int(row.get("progress_percent") or 0) > 0:
            continue
        state = route_states[code]
        if state == "not_applicable":
            desired_status[code] = "not_applicable"
        elif state == "unresolved":
            desired_status[code] = "blocked"

    # A branch may contain ordinary descendants after its decision edge. Repeat
    # until not-applicable propagation and merge readiness reach a fixed point.
    for _ in range(max(1, len(rows))):
        changed = False
        for row in rows:
            code = str(row["code"])
            status = current_status[code]
            if status not in _MUTABLE_STATUSES or int(row.get("progress_percent") or 0) > 0:
                continue
            state = route_states[code]
            if state == "not_applicable":
                next_status = "not_applicable"
            elif state == "unresolved":
                next_status = "blocked"
            else:
                dependencies = [str(item) for item in _list(row.get("depends_on_codes"))]
                dependency_statuses = [desired_status.get(item, "blocked") for item in dependencies]
                if dependencies and all(item == "not_applicable" for item in dependency_statuses):
                    next_status = "not_applicable"
                elif all(item in _SATISFIED_STATUSES for item in dependency_statuses):
                    next_status = "ready"
                else:
                    next_status = "blocked"
            if desired_status[code] != next_status:
                desired_status[code] = next_status
                changed = True
        if not changed:
            break

    transitions: list[JourneyRouteTransition] = []
    for row in rows:
        code = str(row["code"])
        previous = current_status[code]
        desired = desired_status[code]
        if previous == desired:
            continue
        desired_progress = (
            100
            if desired == "not_applicable"
            else 0
            if previous == "not_applicable"
            else int(row.get("progress_percent") or 0)
        )
        await connection.execute(
            text(
                f"""
                UPDATE {requirement}
                SET status=CAST(:status AS varchar), progress_percent=:progress,
                    version=version+1, updated_at=NOW()
                WHERE id=:requirement_id AND tenant_id=:tenant_id
                """
            ),
            {
                "status": desired,
                "progress": desired_progress,
                "requirement_id": row["id"],
                "tenant_id": tenant_id,
            },
        )
        if desired == "not_applicable":
            await connection.execute(
                text(
                    f"""
                    UPDATE {experience_update}
                    SET status='acknowledged', acknowledged_at=NOW(),
                        version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND requirement_id=:requirement_id
                      AND status IN ('pending','deferred')
                    """
                ),
                {"tenant_id": tenant_id, "requirement_id": row["id"]},
            )
        transitions.append(
            JourneyRouteTransition(
                requirement_id=str(row["id"]),
                code=code,
                previous_status=previous,
                status=desired,
            )
        )
    return transitions
