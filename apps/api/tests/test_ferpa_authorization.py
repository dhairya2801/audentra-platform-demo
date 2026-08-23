from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from audentra.contracts.requests import CompleteFerpaAuthorizationRequest
from audentra.core.auth import AuthContext
from audentra.core.delegate_authorization import authorize_delegate_route
from audentra.core.errors import ApiError
from audentra.core.ports import FileUpload, ServiceCall
from audentra.infrastructure.postgres.auth_repository import _delegate_session
from audentra.infrastructure.postgres.ferpa_repository import (
    PostgresFerpaRepository,
    _canonical_delegate_scopes,
    _completion_request_hash,
    _iso,
    _json,
    _list,
    _mapping,
    _normalize_access,
    _nullable_iso,
    _positive_version,
    _timestamp,
    _uuid,
    reconcile_completed_ferpa_requirements,
)
from audentra.infrastructure.postgres.managed_configuration_repository import (
    materialized_journey_tasks,
)
from audentra.infrastructure.postgres.postgres_service import (
    PostgresPlatformService,
    PostgresRepositoryBundle,
    PostgresSignedDocumentGenerator,
)
from audentra.infrastructure.storage.s3 import ObjectNotFoundError
from audentra.interfaces.http.app import create_app

TENANT_ID = "00000000-0000-7000-8000-000000000001"
STUDENT_ID = "00000000-0000-7000-8000-000000000101"
STUDENT_ACTOR_ID = "00000000-0000-7000-8000-000000000100"
DELEGATE_ID = "00000000-0000-7000-8000-000000000601"
AUTHORIZATION_ID = "00000000-0000-7000-8000-000000000501"
REQUIREMENT_ID = "00000000-0000-7000-8000-000000000401"


def _student_auth() -> AuthContext:
    return AuthContext(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        actor_id=STUDENT_ACTOR_ID,
        actor_type="student",
    )


def _delegate_auth(*scopes: str) -> AuthContext:
    return AuthContext(
        tenant_id=TENANT_ID,
        student_id=STUDENT_ID,
        actor_id=DELEGATE_ID,
        actor_type="delegate",
        authentication_method="delegate_link",
        delegate_scopes=frozenset(cast(Any, scopes)),
        delegate_relationship="parent",
        delegate_name="Aster Parent",
        subject_student_name="Aster Student",
    )


def _valid_delegate(**updates: object) -> dict[str, object]:
    delegate: dict[str, object] = {
        "fullName": "Aster Parent",
        "relationship": "parent",
        "email": "parent@example.test",
        "scopes": ["dashboard"],
    }
    delegate.update(updates)
    return delegate


def test_ferpa_value_helpers_normalize_canonical_values() -> None:
    payload = {"accessDecision": "grant", "delegates": [_valid_delegate()]}

    assert _json({"name": "Aster", "enabled": True}) == '{"name":"Aster","enabled":true}'
    assert _mapping('{"name":"Aster"}') == {"name": "Aster"}
    assert _mapping(["not", "a", "mapping"]) == {}
    assert _list(("dashboard", "documents")) == ["dashboard", "documents"]
    assert _list("dashboard") == []
    assert _canonical_delegate_scopes(["onboarding", "dashboard", "onboarding"]) == [
        "enrollment",
        "dashboard",
    ]
    assert _uuid(STUDENT_ID).hex == STUDENT_ID.replace("-", "")
    with pytest.raises(ApiError) as invalid_identifier:
        _uuid("not-a-uuid")
    assert invalid_identifier.value.code == "VALIDATION_ERROR"

    timestamp = _timestamp("2026-08-23T12:34:56Z")
    assert timestamp == datetime(2026, 8, 23, 12, 34, 56, tzinfo=UTC)
    assert _iso(timestamp) == "2026-08-23T12:34:56.000Z"
    assert _nullable_iso(None) is None
    assert _nullable_iso(timestamp) == "2026-08-23T12:34:56.000Z"
    assert _positive_version(2) == 2
    for invalid_version in (0, True, "2"):
        with pytest.raises(ApiError) as invalid_version_error:
            _positive_version(invalid_version)
        assert invalid_version_error.value.code == "VALIDATION_ERROR"

    assert _completion_request_hash(_student_auth(), payload) == _completion_request_hash(
        _student_auth(),
        payload,
    )
    assert _completion_request_hash(_student_auth(), payload) != _completion_request_hash(
        _student_auth(),
        {"accessDecision": "no_access", "delegates": []},
    )


def test_normalize_access_canonicalizes_a_valid_delegate() -> None:
    decision, delegates = _normalize_access(
        {
            "accessDecision": "grant",
            "delegates": [
                _valid_delegate(
                    id=DELEGATE_ID,
                    fullName="  Aster Parent  ",
                    email="PARENT@EXAMPLE.TEST ",
                    scopes=["dashboard", "documents"],
                )
            ],
        },
        allow_ids=True,
    )

    assert decision == "grant"
    assert delegates == [
        {
            "id": DELEGATE_ID,
            "fullName": "Aster Parent",
            "relationship": "parent",
            "email": "parent@example.test",
            "scopes": ["dashboard", "documents"],
            "displayOrder": 0,
        }
    ]


@pytest.mark.parametrize(
    ("payload", "allow_ids", "code"),
    (
        ({"accessDecision": "invalid", "delegates": []}, True, "INVALID_FERPA_ACCESS_DECISION"),
        ({"accessDecision": "grant", "delegates": "not-a-list"}, True, "INVALID_FERPA_DELEGATES"),
        (
            {"accessDecision": "grant", "delegates": [_valid_delegate()] * 5},
            True,
            "FERPA_DELEGATE_LIMIT",
        ),
        ({"accessDecision": "grant", "delegates": []}, True, "FERPA_DELEGATE_REQUIRED"),
        (
            {"accessDecision": "no_access", "delegates": [_valid_delegate()]},
            True,
            "FERPA_NO_ACCESS_DELEGATES",
        ),
        (
            {"accessDecision": "grant", "delegates": ["not-a-mapping"]},
            True,
            "INVALID_FERPA_DELEGATES",
        ),
        (
            {"accessDecision": "grant", "delegates": [_valid_delegate(fullName="")]},
            True,
            "INVALID_FERPA_DELEGATE_NAME",
        ),
        (
            {"accessDecision": "grant", "delegates": [_valid_delegate(relationship="student")]},
            True,
            "INVALID_FERPA_DELEGATE_RELATIONSHIP",
        ),
        (
            {"accessDecision": "grant", "delegates": [_valid_delegate(email="invalid-email")]},
            True,
            "INVALID_FERPA_DELEGATE_EMAIL",
        ),
        (
            {"accessDecision": "grant", "delegates": [_valid_delegate(scopes=[])]},
            True,
            "INVALID_FERPA_DELEGATE_SCOPES",
        ),
        (
            {
                "accessDecision": "grant",
                "delegates": [_valid_delegate(scopes=["dashboard", "dashboard"])],
            },
            True,
            "INVALID_FERPA_DELEGATE_SCOPES",
        ),
        (
            {"accessDecision": "grant", "delegates": [_valid_delegate(scopes=["unknown"])]},
            True,
            "INVALID_FERPA_DELEGATE_SCOPES",
        ),
        (
            {"accessDecision": "grant", "delegates": [_valid_delegate(id=DELEGATE_ID)]},
            False,
            "INVALID_FERPA_DELEGATE_ID",
        ),
    ),
)
def test_normalize_access_rejects_invalid_decisions_and_delegates(
    payload: Mapping[str, object],
    allow_ids: bool,
    code: str,
) -> None:
    with pytest.raises(ApiError) as error:
        _normalize_access(payload, allow_ids=allow_ids)
    assert error.value.code == code


@pytest.mark.parametrize(
    ("row_updates", "expected_code"),
    (
        ({"authorization_version": 2}, "VERSION_CONFLICT"),
        ({"authorization_status": "completed"}, "FERPA_ALREADY_COMPLETED"),
        ({"requirement_status": "blocked"}, "FERPA_REQUIREMENT_NOT_ACTIONABLE"),
        (
            {"input_config": {"signatureProvider": "docusign"}},
            "DOCUSIGN_EXECUTION_NOT_CONFIGURED",
        ),
    ),
)
def test_completion_row_validation_fails_closed_for_stale_or_unavailable_tasks(
    row_updates: Mapping[str, object],
    expected_code: str,
) -> None:
    row: dict[str, object] = {
        "authorization_version": 1,
        "authorization_status": "incomplete",
        "requirement_status": "ready",
        "input_config": {},
    }
    row.update(row_updates)

    with pytest.raises(ApiError) as error:
        PostgresFerpaRepository._validate_completion_row(row, 1)
    assert error.value.code == expected_code


def test_completion_row_validation_accepts_a_current_actionable_task() -> None:
    PostgresFerpaRepository._validate_completion_row(
        {
            "authorization_version": 1,
            "authorization_status": "incomplete",
            "requirement_status": "ready",
            "input_config": {},
        },
        1,
    )


def test_delegate_policy_is_default_deny_and_ferpa_is_always_student_controlled() -> None:
    with pytest.raises(ApiError) as ferpa_error:
        authorize_delegate_route(
            _delegate_auth("enrollment"),
            "GET",
            "/v1/student/ferpa-authorizations/current",
        )
    assert ferpa_error.value.status_code == 403
    assert ferpa_error.value.code == "FERPA_STUDENT_CONTROL_REQUIRED"

    with pytest.raises(ApiError) as default_error:
        authorize_delegate_route(
            _delegate_auth("dashboard"),
            "GET",
            "/v1/student/events",
        )
    assert default_error.value.code == "DELEGATE_ROUTE_NOT_ALLOWED"

    with pytest.raises(ApiError) as scope_error:
        authorize_delegate_route(
            _delegate_auth("profile"),
            "GET",
            "/v1/student/messages",
        )
    assert scope_error.value.code == "DELEGATE_SCOPE_REQUIRED"


@pytest.mark.parametrize(
    ("scope", "method", "path"),
    (
        ("enrollment", "GET", "/v1/student/dashboard"),
        ("dashboard", "GET", "/v1/student/financials"),
        ("dashboard", "GET", "/v1/student/academics"),
        ("dashboard", "GET", "/v1/student/campus-life"),
        ("payments", "GET", "/v1/student/dashboard"),
        ("enrollment", "GET", "/v1/student/profile"),
        ("enrollment", "GET", "/v1/student/academics"),
        ("enrollment", "GET", "/v1/catalog/courses"),
        ("enrollment", "GET", f"/v1/student/documents/{DELEGATE_ID}/content"),
        ("enrollment", "POST", "/v1/student/payments/deposit"),
        (
            "enrollment",
            "POST",
            f"/v1/student/requirements/{REQUIREMENT_ID}/appointments",
        ),
        ("enrollment", "POST", f"/v1/student/requirements/{REQUIREMENT_ID}/responses"),
    ),
)
def test_delegate_policy_allows_reviewed_cross_page_dependencies(
    scope: str, method: str, path: str
) -> None:
    authorize_delegate_route(_delegate_auth(scope), method, path)


def test_legacy_onboarding_delegate_scope_is_presented_as_my_enrollment() -> None:
    session = _delegate_session(
        {
            "id": DELEGATE_ID,
            "student_id": STUDENT_ID,
            "scopes": ["onboarding"],
            "relationship": "parent",
            "full_name": "Daniel Chen",
            "student_name": "Maya Chen",
            "student_preferred_name": "Maya",
            "email_normalized": "daniel.chen@example.test",
        },
        TENANT_ID,
        "aster",
    )
    assert session.context.delegate_scopes == frozenset({"enrollment"})


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("POST", "/v1/student/financials/payment-plan"),
        ("POST", f"/v1/student/campus-life/events/{REQUIREMENT_ID}/register"),
    ),
)
def test_dashboard_dependency_scope_does_not_authorize_cross_page_mutations(
    method: str, path: str
) -> None:
    with pytest.raises(ApiError) as error:
        authorize_delegate_route(_delegate_auth("dashboard"), method, path)
    assert error.value.code == "DELEGATE_SCOPE_REQUIRED"


@pytest.mark.parametrize("scope", ("dashboard", "profile", "documents"))
def test_requirement_response_requires_enrollment_scope(scope: str) -> None:
    with pytest.raises(ApiError) as error:
        authorize_delegate_route(
            _delegate_auth(scope),
            "POST",
            f"/v1/student/requirements/{REQUIREMENT_ID}/responses",
        )

    assert error.value.code == "DELEGATE_SCOPE_REQUIRED"


@pytest.mark.parametrize("scope", ("documents", "profile"))
def test_profile_photo_allows_documents_or_profile_scope(scope: str) -> None:
    authorize_delegate_route(
        _delegate_auth(scope),
        "GET",
        f"/v1/student/documents/{REQUIREMENT_ID}/profile-photo",
    )


def test_profile_photo_denies_enrollment_only_scope() -> None:
    with pytest.raises(ApiError) as error:
        authorize_delegate_route(
            _delegate_auth("enrollment"),
            "GET",
            f"/v1/student/documents/{REQUIREMENT_ID}/profile-photo",
        )

    assert error.value.code == "DELEGATE_SCOPE_REQUIRED"


def test_ferpa_request_contract_enforces_access_and_signature_invariants() -> None:
    unsigned = CompleteFerpaAuthorizationRequest.model_validate(
        {
            "expectedVersion": 1,
            "accessDecision": "no_access",
            "delegates": [],
        }
    )
    assert unsigned.signature is None

    with pytest.raises(ValidationError):
        CompleteFerpaAuthorizationRequest.model_validate(
            {
                "expectedVersion": 1,
                "accessDecision": "grant",
                "delegates": [],
            }
        )


def test_active_specialized_ferpa_task_cannot_be_optional() -> None:
    document = {
        "flows": [
            {
                "kind": "onboarding",
                "status": "published",
                "tasks": [
                    {
                        "id": "family_permissions",
                        "title": "FERPA release and parent access",
                        "description": "Sign FERPA and choose access.",
                        "task_type": "ferpa",
                        "required": False,
                    }
                ],
            }
        ]
    }
    with pytest.raises(ApiError) as error:
        materialized_journey_tasks(document)
    assert error.value.code == "INVALID_MANAGED_FERPA_TASK"


def test_active_ferpa_task_cannot_publish_unavailable_docusign_execution() -> None:
    document = {
        "flows": [
            {
                "kind": "onboarding",
                "status": "published",
                "tasks": [
                    {
                        "id": "family_permissions",
                        "title": "FERPA release and parent access",
                        "description": "Sign FERPA and choose access.",
                        "task_type": "ferpa",
                        "required": True,
                        "student_step": "family_permissions",
                        "signature_provider": "docusign",
                    }
                ],
            }
        ]
    }
    with pytest.raises(ApiError) as error:
        materialized_journey_tasks(document)
    assert error.value.code == "MANAGED_DOCUSIGN_NOT_CONFIGURED"
    with pytest.raises(ValidationError):
        CompleteFerpaAuthorizationRequest.model_validate(
            {
                "expectedVersion": 1,
                "accessDecision": "no_access",
                "delegates": [
                    {
                        "fullName": "Aster Parent",
                        "relationship": "parent",
                        "email": "parent@example.test",
                        "scopes": ["dashboard"],
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        CompleteFerpaAuthorizationRequest.model_validate(
            {
                "expectedVersion": 1,
                "signature": {
                    "accepted": True,
                    "signerName": "Aster Student",
                    "signatureMethod": "drawn",
                },
                "accessDecision": "no_access",
                "delegates": [],
            }
        )


class _Result:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self.rows = rows or []

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self.rows

    def first(self) -> dict[str, object] | None:
        return self.rows[0] if self.rows else None


class _AuthorizationProjectionConnection:
    async def execute(
        self,
        statement: object,
        _parameters: Mapping[str, object] | None = None,
    ) -> _Result:
        sql = " ".join(str(statement).split())
        if "FROM ferpa_signed_document" in sql:
            return _Result(
                [
                    {
                        "id": "00000000-0000-7000-8000-000000000701",
                        "title": "FERPA Release",
                        "file_name": "ferpa-release.pdf",
                        "signer_name": "Aster Student",
                        "signature_method": "typed",
                        "signed_at": "2026-08-23T12:00:00Z",
                    }
                ]
            )
        assert "FROM ferpa_delegate delegate" in sql
        return _Result(
            [
                {
                    "id": DELEGATE_ID,
                    "full_name": "Aster Parent",
                    "relationship": "parent",
                    "email_normalized": "parent@example.test",
                    "scopes": ["onboarding", "dashboard"],
                    "legacy_review_required": True,
                    "link_status": "active",
                    "issued_at": "2026-08-23T12:01:00Z",
                    "rotated_at": None,
                    "last_used_at": None,
                    "link_updated_at": "2026-08-23T12:02:00Z",
                    "updated_at": "2026-08-23T12:03:00Z",
                }
            ]
        )


def test_authorization_projection_preserves_signed_evidence_and_canonical_scopes() -> None:
    repository = PostgresFerpaRepository(cast(Any, None))
    authorization = asyncio.run(
        repository._map_authorization(
            cast(Any, _AuthorizationProjectionConnection()),
            _student_auth(),
            {
                "authorization_id": AUTHORIZATION_ID,
                "requirement_id": REQUIREMENT_ID,
                "requirement_version": 3,
                "flow_kind": "enrollment",
                "authorization_status": "completed",
                "access_decision": "grant",
                "authorization_version": 4,
                "completed_at": "2026-08-23T12:00:00Z",
                "updated_at": "2026-08-23T12:03:00Z",
                "input_config": {"signatureProvider": "built_in"},
            },
        )
    )

    assert authorization["document"] == {
        "status": "signed",
        "signedDocumentId": "00000000-0000-7000-8000-000000000701",
        "title": "FERPA Release",
        "fileName": "ferpa-release.pdf",
        "signedAt": "2026-08-23T12:00:00.000Z",
        "signerName": "Aster Student",
        "signatureMethod": "typed",
    }
    assert authorization["delegates"] == [
        {
            "id": DELEGATE_ID,
            "fullName": "Aster Parent",
            "relationship": "parent",
            "email": "parent@example.test",
            "scopes": ["enrollment", "dashboard"],
            "legacyReviewRequired": True,
            "link": {
                "status": "active",
                "issuedAt": "2026-08-23T12:01:00.000Z",
                "rotatedAt": None,
                "lastUsedAt": None,
                "updatedAt": "2026-08-23T12:02:00.000Z",
            },
        }
    ]
    assert authorization["capabilities"] == {
        "canSign": False,
        "canManageAccess": True,
        "canManageLinks": True,
    }


class _DelegateSyncConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(
        self, statement: object, _parameters: Mapping[str, object] | None = None
    ) -> _Result:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if sql.startswith("SELECT id, full_name, relationship, email_normalized"):
            return _Result(
                [
                    {
                        "id": DELEGATE_ID,
                        "full_name": "Aster Parent",
                        "relationship": "parent",
                        "email_normalized": "parent@example.test",
                    }
                ]
            )
        return _Result()


def _delegate_update(*, email: str = "parent@example.test") -> list[dict[str, object]]:
    return [
        {
            "id": DELEGATE_ID,
            "fullName": "Aster Parent",
            "relationship": "parent",
            "email": email,
            "scopes": ["dashboard", "enrollment"],
            "displayOrder": 0,
        }
    ]


def test_delegate_identity_change_revokes_link_and_sessions_but_scope_edit_does_not() -> None:
    repository = PostgresFerpaRepository(cast(Any, None))
    row = {
        "authorization_id": AUTHORIZATION_ID,
        "tenant_id": TENANT_ID,
        "student_id": STUDENT_ID,
    }

    identity_change = _DelegateSyncConnection()
    asyncio.run(
        repository._sync_delegates(
            cast(Any, identity_change),
            row,
            decision="grant",
            delegates=_delegate_update(email="new-parent@example.test"),
        )
    )
    assert any("UPDATE ferpa_delegate_link" in sql for sql in identity_change.statements)
    assert any("UPDATE ferpa_delegate_session" in sql for sql in identity_change.statements)

    scope_only = _DelegateSyncConnection()
    asyncio.run(
        repository._sync_delegates(
            cast(Any, scope_only),
            row,
            decision="grant",
            delegates=_delegate_update(),
        )
    )
    assert not any("UPDATE ferpa_delegate_link" in sql for sql in scope_only.statements)
    assert not any("UPDATE ferpa_delegate_session" in sql for sql in scope_only.statements)


class _RecordingRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.responses: dict[str, object] = {}

    def __getattr__(self, name: str) -> Callable[..., Coroutine[object, object, object]]:
        async def record(*args: object, **kwargs: object) -> object:
            self.calls.append((name, args, kwargs))
            return self.responses.get(name, {})

        return record


class _FerpaHttpService:
    def __init__(self) -> None:
        self.calls: list[ServiceCall] = []

    async def dispatch(self, call: ServiceCall) -> object:
        self.calls.append(call)
        if call.operation == "public.get_tenant_bootstrap":
            return {"tenantId": TENANT_ID, "slug": "aster"}
        return {"operation": call.operation}


@pytest.mark.anyio
async def test_ferpa_http_routes_dispatch_student_controlled_commands() -> None:
    service = _FerpaHttpService()
    headers = {
        "X-Demo-Tenant-Id": TENANT_ID,
        "X-Demo-Student-Id": STUDENT_ID,
        "X-Demo-Actor-Id": STUDENT_ACTOR_ID,
    }
    transport = ASGITransport(app=create_app(service=service))

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        current = await client.get("/v1/student/ferpa-authorizations/current", headers=headers)
        complete = await client.post(
            f"/v1/student/requirements/{REQUIREMENT_ID}/ferpa/complete",
            headers={**headers, "Idempotency-Key": "ferpa-complete-route-0001"},
            json={
                "expectedVersion": 1,
                "accessDecision": "no_access",
                "delegates": [],
                "signature": {
                    "accepted": True,
                    "signerName": "Aster Student",
                    "signatureMethod": "typed",
                },
            },
        )
        access = await client.patch(
            f"/v1/student/ferpa-authorizations/{AUTHORIZATION_ID}/access",
            headers=headers,
            json={
                "expectedVersion": 2,
                "accessDecision": "grant",
                "delegates": [
                    {
                        "id": DELEGATE_ID,
                        "fullName": "Aster Parent",
                        "relationship": "parent",
                        "email": "parent@example.test",
                        "scopes": ["dashboard", "enrollment"],
                    }
                ],
            },
        )
        link = await client.post(
            f"/v1/student/ferpa-authorizations/{AUTHORIZATION_ID}/delegates/{DELEGATE_ID}/link",
            headers={**headers, "Idempotency-Key": "ferpa-link-route-0001"},
            json={"expectedVersion": 3},
        )
        revoke = await client.post(
            f"/v1/student/ferpa-authorizations/{AUTHORIZATION_ID}/delegates/{DELEGATE_ID}/link/revoke",
            headers=headers,
            json={"expectedVersion": 4},
        )

    assert [response.status_code for response in (current, complete, access, link, revoke)] == [
        200,
        200,
        200,
        200,
        200,
    ]
    calls = [call for call in service.calls if call.operation != "public.get_tenant_bootstrap"]
    assert [call.operation for call in calls] == [
        "student.get_ferpa_authorization",
        "student.complete_ferpa_authorization",
        "student.update_ferpa_access",
        "student.issue_ferpa_delegate_link",
        "student.revoke_ferpa_delegate_link",
    ]
    assert all(
        call.auth is not None
        and call.auth.tenant_id == TENANT_ID
        and call.auth.student_id == STUDENT_ID
        and call.auth.actor_id == STUDENT_ACTOR_ID
        and call.auth.actor_type == "student"
        for call in calls
    )
    assert calls[1].path_params == {"requirementId": REQUIREMENT_ID}
    assert calls[1].idempotency_key == "ferpa-complete-route-0001"
    assert calls[2].path_params == {"authorizationId": AUTHORIZATION_ID}
    assert calls[2].payload["delegates"] == [
        {
            "id": DELEGATE_ID,
            "fullName": "Aster Parent",
            "relationship": "parent",
            "email": "parent@example.test",
            "scopes": ["dashboard", "enrollment"],
        }
    ]
    assert calls[3].path_params == {
        "authorizationId": AUTHORIZATION_ID,
        "delegateId": DELEGATE_ID,
    }
    assert calls[3].idempotency_key == "ferpa-link-route-0001"
    assert calls[4].path_params == calls[3].path_params


class _Storage:
    async def put(self, *_args: object, **_kwargs: object) -> str:
        return "stored"

    async def get(self, _key: str) -> bytes:
        return b"stored"


class _SignedDocuments:
    def __init__(self) -> None:
        self.ferpa_calls = 0

    async def ensure(self, **_kwargs: object) -> int:
        return 0

    async def create_ferpa(self, **_kwargs: object) -> dict[str, object]:
        self.ferpa_calls += 1
        return {"id": DELEGATE_ID}


def _service_with_ferpa() -> tuple[PostgresPlatformService, _RecordingRepository, _SignedDocuments]:
    platform = _RecordingRepository()
    portal = _RecordingRepository()
    staff = _RecordingRepository()
    ferpa = _RecordingRepository()
    signed = _SignedDocuments()
    bundle = PostgresRepositoryBundle(
        platform=cast(Any, platform),
        portal=cast(Any, portal),
        staff=cast(Any, staff),
        ferpa=cast(Any, ferpa),
    )
    return (
        PostgresPlatformService(
            bundle,
            cast(Any, _Storage()),
            cast(Any, object()),
            signed,
            "worker-token",
        ),
        ferpa,
        signed,
    )


def _call(
    operation: str,
    *,
    auth: AuthContext,
    payload: Mapping[str, Any],
    upload: FileUpload | None = None,
) -> ServiceCall:
    return ServiceCall(
        operation=operation,
        auth=auth,
        request_id="request-1",
        payload=payload,
        path_params={"requirementId": REQUIREMENT_ID},
        idempotency_key="ferpa-idempotency-0001",
        upload=upload,
    )


def test_cached_ferpa_completion_replays_before_document_generation() -> None:
    service, ferpa, signed = _service_with_ferpa()
    cached = {"authorization": {"id": AUTHORIZATION_ID, "version": 2}}
    ferpa.responses["preflight_completion"] = {"cachedResponse": cached}

    result = asyncio.run(
        service.dispatch(
            _call(
                "student.complete_ferpa_authorization",
                auth=_student_auth(),
                payload={
                    "expectedVersion": 1,
                    "signature": {
                        "accepted": True,
                        "signerName": "Aster Student",
                        "signatureMethod": "typed",
                    },
                    "accessDecision": "no_access",
                    "delegates": [],
                },
            )
        )
    )

    assert result == cached
    assert signed.ferpa_calls == 0
    assert not any(name == "complete" for name, _args, _kwargs in ferpa.calls)


@pytest.mark.parametrize(
    ("auth", "expected_code"),
    (
        (_student_auth(), "REQUIREMENT_SPECIALIZED_SUBMISSION_REQUIRED"),
        (_delegate_auth("enrollment"), "FERPA_STUDENT_CONTROL_REQUIRED"),
    ),
)
def test_generic_requirement_response_cannot_bypass_ferpa(
    auth: AuthContext, expected_code: str
) -> None:
    service, ferpa, _signed = _service_with_ferpa()
    ferpa.responses["requirement_context"] = {
        "interactionType": "ferpa",
        "flowKind": "enrollment",
    }

    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.dispatch(
                _call(
                    "student.submit_requirement_response",
                    auth=auth,
                    payload={"expectedVersion": 1, "response": {"approved": True}},
                )
            )
        )
    assert error.value.code == expected_code


def test_generic_upload_cannot_be_attached_to_ferpa() -> None:
    service, ferpa, _signed = _service_with_ferpa()
    ferpa.responses["requirement_flow"] = "ferpa"

    with pytest.raises(ApiError) as error:
        asyncio.run(
            service.dispatch(
                _call(
                    "student.upload_document",
                    auth=_student_auth(),
                    payload={},
                    upload=FileUpload(
                        file_name="release.pdf",
                        mime_type="application/pdf",
                        content=b"%PDF-release",
                        requirement_id=REQUIREMENT_ID,
                    ),
                )
            )
        )
    assert error.value.code == "REQUIREMENT_SPECIALIZED_SUBMISSION_REQUIRED"


async def _dashboard() -> dict[str, object]:
    return {
        "student": {"id": STUDENT_ID, "preferredName": "Aster"},
        "offer": {
            "id": "offer-1",
            "programName": "Computer Science",
            "termName": "Fall",
            "campusName": "Main",
            "responseDeadline": "2026-09-01",
            "depositAmountCents": 50_000,
            "status": "accepted",
        },
        "journey": {"id": "journey-1", "requirements": []},
        "unreadMessageCount": 3,
    }


def test_dashboard_projection_preserves_each_page_runtime_dependency() -> None:
    service, _ferpa, _signed = _service_with_ferpa()

    dashboard = asyncio.run(service._delegate_dashboard(_delegate_auth("dashboard"), _dashboard()))
    assert dashboard["offer"]["programName"] == "Computer Science"
    assert "journey" in dashboard
    assert dashboard["unreadMessageCount"] == 3

    payments = asyncio.run(service._delegate_dashboard(_delegate_auth("payments"), _dashboard()))
    assert payments["offer"]["programName"] == "Computer Science"
    assert "journey" not in payments
    assert payments["unreadMessageCount"] == 0

    enrollment = asyncio.run(
        service._delegate_dashboard(_delegate_auth("enrollment"), _dashboard())
    )
    assert enrollment["offer"]["id"] == "offer-1"
    assert "journey" in enrollment


def test_dashboard_dependency_projections_do_not_disclose_full_page_records() -> None:
    service, _ferpa, _signed = _service_with_ferpa()
    auth = _delegate_auth("dashboard")
    financials = service._delegate_financials(
        auth,
        {
            "academicYear": "2026-27",
            "acceptedAidCents": 1,
            "paymentsCents": 2,
            "remainingBalanceCents": 3,
            "requiredDocuments": [],
            "paymentPlans": [],
            "awards": [{"name": "Private award"}],
            "sap": {"cumulativeGpa": 4.0},
        },
    )
    assert "awards" not in financials
    assert "sap" not in financials

    academics = service._delegate_academics(
        auth,
        {
            "selectedProgram": {"degree": "BS"},
            "exemptionRecommendations": [],
            "transcriptCredits": [{"title": "Private transcript"}],
        },
    )
    assert "transcriptCredits" not in academics

    campus = service._delegate_campus_life(
        auth,
        {"events": [], "clubs": [{"name": "Private club detail"}]},
    )
    assert "clubs" not in campus


def test_edward_action_authority_never_reads_or_offers_ungranted_page_actions() -> None:
    service, _ferpa, _signed = _service_with_ferpa()
    authority = asyncio.run(
        service._edward_action_authority(
            _delegate_auth("edward"),
            "Pay my deposit, upload a transcript, and schedule an appointment",
        )
    )
    assert authority.allow_deposit_payment is False
    assert authority.document_upload_category is None
    assert authority.appointment_type is None
    assert service._delegate_edward_actions(
        _delegate_auth("edward"),
        [
            {"label": "Payments", "href": "/payments"},
            {"label": "Edward", "href": "/edward"},
        ],
    ) == [{"label": "Edward", "href": "/edward"}]


class _LifecycleConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(
        self, statement: object, _parameters: Mapping[str, object] | None = None
    ) -> _Result:
        sql = " ".join(str(statement).split())
        self.statements.append(sql)
        if sql.startswith("UPDATE student_requirement requirement"):
            return _Result(
                [
                    {
                        "id": REQUIREMENT_ID,
                        "journey_id": "00000000-0000-7000-8000-000000000301",
                        "student_id": STUDENT_ID,
                    }
                ]
            )
        return _Result()


def test_completed_authorization_reconciles_a_replacement_requirement() -> None:
    connection = _LifecycleConnection()
    updated = asyncio.run(
        reconcile_completed_ferpa_requirements(
            cast(Any, connection),
            tenant_id=TENANT_ID,
            student_id=STUDENT_ID,
        )
    )
    assert updated == 1
    assert any("status='completed', progress_percent=100" in sql for sql in connection.statements)
    assert any("UPDATE enrollment_journey journey" in sql for sql in connection.statements)


def test_runtime_legacy_import_is_review_only_and_never_grants_scopes_or_a_link() -> None:
    connection = _LifecycleConnection()
    repository = PostgresFerpaRepository(cast(Any, None))
    asyncio.run(
        repository._import_legacy_delegates(
            cast(Any, connection),
            _student_auth(),
            authorization_id=AUTHORIZATION_ID,
        )
    )
    statement = connection.statements[0]
    assert "scopes, display_order" in statement
    assert "'{}'" in statement
    assert "legacy_review_required" in statement


def test_same_payload_can_resume_signing_reservation_with_a_new_key() -> None:
    repository = PostgresFerpaRepository(cast(Any, None))
    row = {
        "actor_id": STUDENT_ACTOR_ID,
        "operation": f"ferpa.complete:{REQUIREMENT_ID}",
        "idempotency_key": "first-key",
        "request_hash": "a" * 64,
    }
    repository._validate_signing_reservation_request(
        row,
        _student_auth(),
        operation=f"ferpa.complete:{REQUIREMENT_ID}",
        idempotency_key="second-key",
        request_hash="a" * 64,
    )
    with pytest.raises(ApiError) as error:
        repository._validate_signing_reservation_request(
            row,
            _student_auth(),
            operation=f"ferpa.complete:{REQUIREMENT_ID}",
            idempotency_key="first-key",
            request_hash="b" * 64,
        )
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


class _RetryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls = 0

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise ObjectNotFoundError(key)
        return self.objects[key]

    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        sha256: str | None = None,
    ) -> str:
        del content_type, sha256
        self.put_calls += 1
        self.objects[key] = body
        return key


def test_signing_retry_reuses_stable_evidence_without_overwriting_storage() -> None:
    storage = _RetryStorage()
    generator = PostgresSignedDocumentGenerator()
    reservation = {
        "documentId": "00000000-0000-7000-8000-000000000701",
        "signedAt": "2026-08-20T12:00:00.000Z",
        "storageKey": (
            f"{TENANT_ID}/{STUDENT_ID}/signed-ferpa/{AUTHORIZATION_ID}/"
            "00000000-0000-7000-8000-000000000701.pdf"
        ),
    }
    kwargs = {
        "auth": _student_auth(),
        "authorization": {"id": AUTHORIZATION_ID},
        "signature": {
            "accepted": True,
            "signerName": "Aster Student",
            "signatureMethod": "typed",
        },
        "reservation": reservation,
        "storage": cast(Any, storage),
    }
    first = asyncio.run(generator.create_ferpa(**kwargs))
    second = asyncio.run(generator.create_ferpa(**kwargs))
    assert storage.put_calls == 1
    assert first == second
    assert first["id"] == reservation["documentId"]
    assert first["signedAt"] == reservation["signedAt"]
