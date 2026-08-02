"""Strict Pydantic representation of the durable domain-event envelope."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class _EnvelopeModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        strict=True,
    )


class DomainEventActor(_EnvelopeModel):
    type: NonEmptyString
    id: NonEmptyString | None = None


class EventLineage(_EnvelopeModel):
    correlation_id: NonEmptyString
    effect_registry_version: Annotated[int, Field(ge=1)] = 1
    effect_id: NonEmptyString | None = None
    trace_id: NonEmptyString | None = None
    span_id: NonEmptyString | None = None


class DomainEventEnvelope(_EnvelopeModel):
    event_id: NonEmptyString
    event_name: NonEmptyString
    occurred_at: datetime
    tenant_id: NonEmptyString
    aggregate_type: NonEmptyString
    aggregate_id: NonEmptyString
    aggregate_version: Annotated[int, Field(ge=0)] | None = None
    actor: DomainEventActor | None = None
    correlation_id: NonEmptyString | None = None
    causation_id: NonEmptyString | None = None
    lineage: EventLineage | None = None
    data: dict[str, JsonValue]


class ClaimedOutboxEvent(DomainEventEnvelope):
    outbox_id: NonEmptyString
    attempts: Annotated[int, Field(ge=0)]


def parse_outbox_event(row: Mapping[str, object]) -> ClaimedOutboxEvent:
    """Parse both current outbox rows and the legacy embedded-envelope layout."""

    payload = _object_value(_first(row, "payload", "data", default={}), "payload")
    embedded_data = (
        payload if "data" not in payload else _object_value(payload["data"], "payload.data")
    )
    occurred_at = _first(
        row,
        "occurred_at",
        default=_first(payload, "occurredAt", "occurred_at", default=row.get("created_at")),
    )
    outbox_id = _required_string(row.get("id"), "id")
    actor = _actor_value(row, payload)
    aggregate_version_value = _first(
        row,
        "aggregate_version",
        default=_first(payload, "aggregateVersion", "aggregate_version", default=None),
    )
    lineage_value = payload.get("lineage")

    return ClaimedOutboxEvent.model_validate(
        {
            "outbox_id": outbox_id,
            "event_id": _required_string(
                _first(
                    row,
                    "event_id",
                    default=_first(payload, "eventId", "event_id", default=outbox_id),
                ),
                "event_id",
            ),
            "event_name": _required_string(
                _first(
                    row,
                    "event_name",
                    "event_type",
                    default=_first(payload, "eventName", "event_name"),
                ),
                "event_name",
            ),
            "occurred_at": _datetime_value(occurred_at, "occurred_at"),
            "tenant_id": _required_string(
                _first(
                    row,
                    "tenant_id",
                    default=_first(payload, "tenantId", "tenant_id"),
                ),
                "tenant_id",
            ),
            "aggregate_type": _required_string(
                _first(
                    row,
                    "aggregate_type",
                    default=_first(payload, "aggregateType", "aggregate_type"),
                ),
                "aggregate_type",
            ),
            "aggregate_id": _required_string(
                _first(
                    row,
                    "aggregate_id",
                    default=_first(payload, "aggregateId", "aggregate_id"),
                ),
                "aggregate_id",
            ),
            "aggregate_version": (
                None
                if aggregate_version_value is None
                else _non_negative_integer(aggregate_version_value, "aggregate_version")
            ),
            "actor": actor,
            "correlation_id": _nullable_string(
                _first(
                    row,
                    "correlation_id",
                    default=_first(payload, "correlationId", "correlation_id", default=None),
                )
            ),
            "causation_id": _nullable_string(
                _first(
                    row,
                    "causation_id",
                    default=_first(payload, "causationId", "causation_id", default=None),
                )
            ),
            "lineage": (
                None
                if lineage_value is None
                else EventLineage.model_validate(_object_value(lineage_value, "lineage"))
            ),
            "data": embedded_data,
            "attempts": _non_negative_integer(
                _first(row, "attempts", "attempt_count", default=0),
                "attempts",
            ),
        }
    )


def get_event_string(data: Mapping[str, JsonValue], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _actor_value(
    row: Mapping[str, object], payload: Mapping[str, object]
) -> DomainEventActor | None:
    if "actor_type" in row:
        return DomainEventActor(
            type=_required_string(row.get("actor_type"), "actor_type"),
            id=_nullable_string(row.get("actor_id")),
        )
    value = _first(row, "actor", default=payload.get("actor"))
    if value is None:
        return None
    actor = _object_value(value, "actor")
    return DomainEventActor(
        type=_required_string(actor.get("type"), "actor.type"),
        id=_nullable_string(actor.get("id")),
    )


def _object_value(value: object, field: str) -> dict[str, Any]:
    parsed = value
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid outbox event: {field} contains malformed JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"Invalid outbox event: {field} must be a JSON object")
    return {str(key): item for key, item in parsed.items()}


def _first(
    source: Mapping[str, object],
    *keys: str,
    default: object = None,
) -> object:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


def _required_string(value: object, field: str) -> str:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid outbox event: {field} must be a non-empty string")
    return value


def _nullable_string(value: object) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    return value if isinstance(value, str) and value.strip() else None


def _non_negative_integer(value: object, field: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        numeric = value
    elif isinstance(value, str) and value.isdigit():
        numeric = int(value)
    else:
        raise ValueError(f"Invalid outbox event: {field} must be a non-negative integer")
    if numeric < 0:
        raise ValueError(f"Invalid outbox event: {field} must be a non-negative integer")
    return numeric


def _datetime_value(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid outbox event: {field} is not a valid timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Invalid outbox event: {field} is not a valid timestamp") from error
