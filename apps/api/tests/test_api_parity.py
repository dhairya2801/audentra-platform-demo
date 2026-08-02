"""Behavioral parity for the original API vertical-slice E2E specification."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from audentra.application.platform_service import InMemoryPlatformService
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.core.ports import ServiceCall
from audentra.infrastructure.memory.store import DEMO_IDS


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def auth(*, tenant_id: str | None = None) -> AuthContext:
    return AuthContext(
        tenant_id=tenant_id or DEMO_IDS["tenant_id"],
        student_id=DEMO_IDS["student_id"],
        actor_id=DEMO_IDS["person_id"],
        actor_type="student",
    )


def call(
    operation: str,
    *,
    identity: AuthContext | None = None,
    payload: dict[str, Any] | None = None,
    path: dict[str, str] | None = None,
) -> ServiceCall:
    return ServiceCall(
        operation=operation,
        auth=identity if identity is not None else auth(),
        request_id=f"request.{uuid4()}",
        payload=payload or {},
        path_params=path or {},
    )


def test_health_and_typed_dashboard() -> None:
    service = InMemoryPlatformService()
    health = run(
        service.dispatch(
            ServiceCall(operation="health.liveness", auth=None, request_id="request.health")
        )
    )
    dashboard = run(service.dispatch(call("student.get_dashboard")))

    assert health == {"status": "ok", "service": "vv-api"}
    assert dashboard["student"] == {
        "id": DEMO_IDS["student_id"],
        "preferredName": "Alex",
        "fullName": "Alex Morgan",
        "classYear": 2027,
    }
    assert dashboard["offer"]["status"] == "offered"
    assert dashboard["journey"]["nextAction"]["code"] == "accept_offer"
    assert dashboard["projectionVersion"] == 1


def test_tenant_isolation_uses_not_found_without_leaking_data() -> None:
    service = InMemoryPlatformService()
    foreign = auth(tenant_id="00000000-0000-7000-8000-000000009999")

    with pytest.raises(ApiError) as raised:
        run(service.dispatch(call("student.get_dashboard", identity=foreign)))

    assert raised.value.status_code == 404
    assert raised.value.code == "STUDENT_DASHBOARD_NOT_FOUND"


def test_offer_acceptance_is_idempotent_and_rejects_key_reuse() -> None:
    service = InMemoryPlatformService()
    request = call(
        "admission.accept_offer",
        payload={"idempotencyKey": "accept.offer.0001"},
        path={"offer_id": DEMO_IDS["offer_id"]},
    )

    first = run(service.dispatch(request))
    replay = run(service.dispatch(request))

    assert replay == first
    assert first["offerStatus"] == "accepted"
    assert first["journeyStatus"] == "in_progress"
    assert first["projectionVersion"] == 2
    assert service.store.effects.journeys == 1
    assert service.store.effects.requirement_sets == 1
    assert service.store.effects.audit_events == 1
    assert service.store.effects.outbox_events == 2

    with pytest.raises(ApiError) as raised:
        run(
            service.dispatch(
                call(
                    "admission.accept_offer",
                    payload={"idempotencyKey": "accept.offer.0001"},
                    path={"offer_id": "00000000-0000-7000-8000-000000000999"},
                )
            )
        )
    assert raised.value.status_code == 409
    assert raised.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_activity_ingestion_deduplicates_and_enforces_property_policy() -> None:
    service = InMemoryPlatformService()
    event_id = str(uuid4())
    event = {
        "eventId": event_id,
        "eventName": "ui.dashboard_viewed.v1",
        "occurredAt": "2026-07-24T12:00:00.000Z",
        "sessionId": "session.0001",
        "pageInstanceId": "page.0001",
        "properties": {"projection_version": 2, "journey_status": "in_progress"},
    }
    request = call("activity.ingest_batch", payload={"events": [event]})

    assert run(service.dispatch(request)) == {"accepted": 1, "duplicates": 0}
    assert run(service.dispatch(request)) == {"accepted": 0, "duplicates": 1}

    prohibited = {**event, "eventId": str(uuid4()), "properties": {"email": "x@test"}}
    with pytest.raises(ApiError) as raised:
        run(service.dispatch(call("activity.ingest_batch", payload={"events": [prohibited]})))
    assert raised.value.code == "PROHIBITED_ACTIVITY_PROPERTY"


def test_catalog_financials_and_payment_plan_preserve_response_contracts() -> None:
    service = InMemoryPlatformService()
    catalog_call = ServiceCall(
        operation="catalog.search_courses",
        auth=auth(),
        request_id="request.catalog",
        query_params={"query": "programming"},
    )
    catalog = run(service.dispatch(catalog_call))
    financials = run(service.dispatch(call("student.get_financials")))
    plan_id = "10000000-0000-7000-8000-000000000999"
    selected = run(
        service.dispatch(
            call(
                "student.select_payment_plan",
                payload={"planId": plan_id, "idempotencyKey": "payment.plan.0001"},
            )
        )
    )

    assert catalog["total"] == 1
    assert catalog["items"][0]["code"] == "CS 101"
    assert financials["remainingBalanceCents"] == 1_700_500
    assert selected == {"planId": plan_id, "status": "enrolled"}
