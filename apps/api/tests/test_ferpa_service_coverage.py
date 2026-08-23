from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from audentra.application.platform_service import InMemoryPlatformService
from audentra.core.auth import AuthContext, PortalScope
from audentra.core.delegate_authorization import (
    authorize_delegate_route,
    require_delegate_scope,
    require_student_ferpa_control,
)
from audentra.core.errors import ApiError
from audentra.core.ports import ServiceCall
from audentra.infrastructure.memory.store import DEMO_IDS

STUDENT_AUTH = AuthContext(
    tenant_id=DEMO_IDS["tenant_id"],
    student_id=DEMO_IDS["student_id"],
    actor_id=DEMO_IDS["person_id"],
    actor_type="student",
)


def _delegate_auth(*scopes: PortalScope) -> AuthContext:
    return AuthContext(
        tenant_id=STUDENT_AUTH.tenant_id,
        student_id=STUDENT_AUTH.student_id,
        actor_id="10000000-0000-7000-8000-000000000601",
        actor_type="delegate",
        authentication_method="delegate_link",
        delegate_scopes=frozenset(scopes),
        delegate_relationship="parent",
        delegate_name="Aster Parent",
        subject_student_name="Aster Student",
    )


def _call(
    operation: str,
    *,
    auth: AuthContext | None = STUDENT_AUTH,
    payload: dict[str, Any] | None = None,
    path: dict[str, str] | None = None,
    idempotency_key: str | None = None,
) -> ServiceCall:
    return ServiceCall(
        operation=operation,
        auth=auth,
        request_id=f"request-{operation}",
        payload=payload or {},
        path_params=path or {},
        idempotency_key=idempotency_key,
    )


class _RequirementStore:
    def __init__(self, requirement: dict[str, Any]) -> None:
        self.requirement = requirement
        self.appointments = [
            {"id": "scheduled", "status": "scheduled"},
            {"id": "rescheduled", "status": "rescheduled"},
            {"id": "cancelled", "status": "cancelled"},
        ]
        self.created_appointments: list[tuple[dict[str, Any], str]] = []
        self.profile_updates: list[dict[str, Any]] = []

    def get_requirement(self, auth: AuthContext, identifier: str) -> dict[str, Any]:
        assert auth == STUDENT_AUTH
        assert identifier == "requirement-1"
        return dict(self.requirement)

    def get_appointments(self, auth: AuthContext) -> dict[str, Any]:
        assert auth == STUDENT_AUTH
        return {"items": list(self.appointments), "total": len(self.appointments)}

    def create_appointment(
        self, auth: AuthContext, body: dict[str, Any], key: str
    ) -> dict[str, Any]:
        assert auth == STUDENT_AUTH
        self.created_appointments.append((body, key))
        return {"id": "appointment-1", **body}

    def update_profile(self, auth: AuthContext, body: dict[str, Any]) -> dict[str, Any]:
        assert auth == STUDENT_AUTH
        self.profile_updates.append(body)
        return {"updated": True, **body}


def _service_with_requirement(
    requirement: dict[str, Any],
) -> tuple[InMemoryPlatformService, _RequirementStore]:
    store = _RequirementStore(requirement)
    return InMemoryPlatformService(store=cast(Any, store)), store


def _scheduling_requirement(*, status: str = "ready") -> dict[str, Any]:
    return {
        "id": "requirement-1",
        "flowKind": "enrollment",
        "code": "advising_appointment",
        "interactionType": "scheduling",
        "status": status,
    }


def _profile_requirement(*, status: str = "ready") -> dict[str, Any]:
    return {
        "id": "requirement-1",
        "flowKind": "enrollment",
        "code": "profile_verification",
        "interactionType": "form",
        "status": status,
    }


def test_delegate_guards_bypass_students_but_fail_closed_for_parent_control() -> None:
    authorize_delegate_route(
        STUDENT_AUTH,
        "POST",
        "/v1/student/ferpa-authorizations/current/complete",
    )

    with pytest.raises(ApiError) as voice_error:
        authorize_delegate_route(
            _delegate_auth("edward"),
            "POST",
            "/v1/student/assistant/voice-sessions",
        )
    assert voice_error.value.code == "DELEGATE_ROUTE_NOT_ALLOWED"

    with pytest.raises(ApiError) as scope_error:
        require_delegate_scope(_delegate_auth("dashboard"), "profile")
    assert scope_error.value.code == "DELEGATE_SCOPE_REQUIRED"

    require_delegate_scope(_delegate_auth("profile"), "profile")
    require_delegate_scope(STUDENT_AUTH, "profile")

    with pytest.raises(ApiError) as ferpa_error:
        require_student_ferpa_control(_delegate_auth("enrollment"))
    assert ferpa_error.value.code == "FERPA_STUDENT_CONTROL_REQUIRED"
    require_student_ferpa_control(STUDENT_AUTH)


def test_delegate_policy_rejects_wrong_method_for_a_read_only_scope() -> None:
    with pytest.raises(ApiError) as error:
        authorize_delegate_route(
            _delegate_auth("financials"),
            "POST",
            "/v1/student/financials",
        )
    assert error.value.code == "DELEGATE_ROUTE_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("scope", "method", "path", "operation", "response_key"),
    (
        (
            "classrooms",
            "GET",
            "/v1/student/academics",
            "student.get_academics",
            "selectedProgram",
        ),
        (
            "campus_life",
            "GET",
            "/v1/student/campus-life",
            "student.get_campus_life",
            "events",
        ),
        (
            "appointments",
            "GET",
            "/v1/student/appointments",
            "student.list_appointments",
            "items",
        ),
    ),
)
def test_allowed_delegate_reads_reach_the_application_service(
    scope: PortalScope,
    method: str,
    path: str,
    operation: str,
    response_key: str,
) -> None:
    auth = _delegate_auth(scope)
    authorize_delegate_route(auth, method, path)

    result = asyncio.run(InMemoryPlatformService().dispatch(_call(operation, auth=auth)))

    assert response_key in cast(dict[str, Any], result)


def test_requirement_appointment_listing_returns_only_active_bookings() -> None:
    service, _ = _service_with_requirement(_scheduling_requirement())

    result = asyncio.run(
        service.dispatch(
            _call(
                "student.list_requirement_appointments",
                path={"requirementId": "requirement-1"},
            )
        )
    )

    assert result == {
        "items": [
            {"id": "scheduled", "status": "scheduled"},
            {"id": "rescheduled", "status": "rescheduled"},
        ],
        "total": 2,
    }


def test_actionable_scheduling_requirement_creates_an_appointment() -> None:
    service, store = _service_with_requirement(_scheduling_requirement(status="rejected"))

    result = asyncio.run(
        service.dispatch(
            _call(
                "student.create_requirement_appointment",
                path={"requirement_id": "requirement-1"},
                payload={"startsAt": "2026-08-24T09:00:00Z"},
                idempotency_key="appointment-key-123",
            )
        )
    )

    assert result == {
        "id": "appointment-1",
        "startsAt": "2026-08-24T09:00:00Z",
    }
    assert store.created_appointments == [
        ({"startsAt": "2026-08-24T09:00:00Z"}, "appointment-key-123")
    ]


def test_non_scheduling_requirement_rejects_appointment_management() -> None:
    service, _ = _service_with_requirement(_profile_requirement())

    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.dispatch(
                _call(
                    "student.list_requirement_appointments",
                    path={"id": "requirement-1"},
                )
            )
        )

    assert error.value.code == "REQUIREMENT_SCHEDULING_REQUIRED"


def test_completed_scheduling_requirement_rejects_new_appointment() -> None:
    service, store = _service_with_requirement(_scheduling_requirement(status="completed"))

    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.dispatch(
                _call(
                    "student.create_requirement_appointment",
                    path={"id": "requirement-1"},
                    idempotency_key="appointment-key-456",
                )
            )
        )

    assert error.value.code == "STUDENT_REQUIREMENT_NOT_ACTIONABLE"
    assert store.created_appointments == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("flowKind", "orientation"),
        ("code", "family_permissions"),
        ("interactionType", "scheduling"),
    ),
)
def test_profile_update_requires_the_canonical_enrollment_profile_task(
    field: str, value: str
) -> None:
    requirement = _profile_requirement()
    requirement[field] = value
    service, store = _service_with_requirement(requirement)

    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.dispatch(
                _call(
                    "student.update_requirement_profile",
                    path={"requirementId": "requirement-1"},
                    payload={"preferredName": "Alex"},
                )
            )
        )

    assert error.value.code == "REQUIREMENT_PROFILE_UPDATE_REQUIRED"
    assert store.profile_updates == []


def test_completed_profile_requirement_rejects_mutation() -> None:
    service, store = _service_with_requirement(_profile_requirement(status="completed"))

    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.dispatch(
                _call(
                    "student.update_requirement_profile",
                    path={"id": "requirement-1"},
                    payload={"preferredName": "Alex"},
                )
            )
        )

    assert error.value.code == "STUDENT_REQUIREMENT_NOT_ACTIONABLE"
    assert store.profile_updates == []


def test_actionable_profile_requirement_updates_the_student_profile() -> None:
    service, store = _service_with_requirement(_profile_requirement(status="help_requested"))

    result = asyncio.run(
        service.dispatch(
            _call(
                "student.update_requirement_profile",
                path={"requirementId": "requirement-1"},
                payload={"preferredName": "Alex"},
            )
        )
    )

    assert result == {"updated": True, "preferredName": "Alex"}
    assert store.profile_updates == [{"preferredName": "Alex"}]


def test_in_memory_dispatch_fails_closed_for_invalid_requests() -> None:
    service = InMemoryPlatformService()
    readiness = asyncio.run(service.dispatch(_call("health.readiness", auth=None)))
    assert readiness == {"status": "ready", "service": "vv-api"}

    with pytest.raises(ApiError) as tenant_error:
        asyncio.run(
            service.dispatch(
                _call(
                    "public.get_tenant_bootstrap",
                    auth=None,
                    path={"tenantId": "unknown-tenant"},
                )
            )
        )
    assert tenant_error.value.code == "TENANT_NOT_FOUND"

    with pytest.raises(ApiError) as auth_error:
        asyncio.run(service.dispatch(_call("student.get_dashboard", auth=None)))
    assert auth_error.value.code == "UNAUTHORIZED"

    with pytest.raises(ApiError) as path_error:
        asyncio.run(service.dispatch(_call("student.get_requirement")))
    assert path_error.value.code == "VALIDATION_ERROR"

    with pytest.raises(ApiError) as key_error:
        asyncio.run(
            service.dispatch(_call("student.select_payment_plan", payload={"planId": "standard"}))
        )
    assert key_error.value.code == "IDEMPOTENCY_KEY_REQUIRED"

    with pytest.raises(ApiError) as operation_error:
        asyncio.run(service.dispatch(_call("student.unsupported_operation")))
    assert operation_error.value.code == "PLATFORM_OPERATION_NOT_IMPLEMENTED"
