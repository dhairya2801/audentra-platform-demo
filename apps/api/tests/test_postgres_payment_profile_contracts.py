from __future__ import annotations

import asyncio
import os
from contextlib import AbstractAsyncContextManager
from typing import cast
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from audentra.contracts.requests import UpdateStudentProfileRequest
from audentra.core.auth import AuthContext
from audentra.core.errors import BadRequestError
from audentra.infrastructure.db.engine import normalize_database_url
from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository


class _BorrowedConnectionContext(AbstractAsyncContextManager[AsyncConnection]):
    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> AsyncConnection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _TransactionBoundEngine:
    """Keep repository writes inside the integration test's rollback boundary."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection

    def connect(self) -> _BorrowedConnectionContext:
        return _BorrowedConnectionContext(self.connection)

    def begin(self) -> _BorrowedConnectionContext:
        return _BorrowedConnectionContext(self.connection)


def _database_url() -> str:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if database_url:
        return database_url
    if os.getenv("CI", "").strip().lower() in {"1", "true", "yes"}:
        pytest.fail(
            "AUDENTRA_TEST_DATABASE_URL or TEST_DATABASE_URL is required for PostgreSQL "
            "contract tests in CI"
        )
    pytest.skip(
        "Set AUDENTRA_TEST_DATABASE_URL or TEST_DATABASE_URL to run PostgreSQL contract tests"
    )


def test_update_profile_request_rejects_explicit_null_and_preserves_omission() -> None:
    omitted = UpdateStudentProfileRequest.model_validate({"expectedVersion": 3})
    assert omitted.public_payload() == {"expectedVersion": 3}

    with pytest.raises(ValidationError):
        UpdateStudentProfileRequest.model_validate({"expectedVersion": 3, "preferredName": None})

    boundary_name = "N" * 120
    accepted = UpdateStudentProfileRequest.model_validate(
        {"expectedVersion": 3, "preferredName": boundary_name}
    )
    assert accepted.public_payload() == {
        "expectedVersion": 3,
        "preferredName": boundary_name,
    }

    with pytest.raises(ValidationError):
        UpdateStudentProfileRequest.model_validate(
            {"expectedVersion": 3, "preferredName": "N" * 121}
        )


@pytest.mark.postgres
def test_payment_plan_selection_preserves_partial_unique_and_aggregate_versions() -> None:
    database_url = _database_url()
    tenant_id = uuid4()
    person_id = uuid4()
    student_id = uuid4()
    switch_target_id = uuid4()
    enrolled_plan_id = uuid4()
    unchanged_sibling_id = uuid4()
    first_selection_id = uuid4()
    first_selection_sibling_id = uuid4()

    async def scenario() -> None:
        engine = create_async_engine(normalize_database_url(database_url))
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(
                        text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
                        {"id": tenant_id, "name": f"Payment contract {tenant_id}"},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO person (
                              id, tenant_id, preferred_name, first_name, last_name
                            ) VALUES (
                              :id, :tenant_id, 'Payment', 'Payment', 'Contract'
                            )
                            """
                        ),
                        {"id": person_id, "tenant_id": tenant_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student (id, tenant_id, person_id, class_year)
                            VALUES (:id, :tenant_id, :person_id, 2031)
                            """
                        ),
                        {
                            "id": student_id,
                            "tenant_id": tenant_id,
                            "person_id": person_id,
                        },
                    )

                    # Insert the switch target before the currently enrolled plan. The old
                    # one-statement UPDATE encountered this heap tuple first and attempted
                    # to create a second enrolled row before clearing the existing one.
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student_payment_plan (
                              id, tenant_id, student_id, academic_year, name,
                              installment_count, enrollment_fee_cents, status, version
                            ) VALUES (
                              :id, :tenant_id, :student_id, '2030-2031',
                              'Switch target', 4, 1000, 'available', 4
                            )
                            """
                        ),
                        {
                            "id": switch_target_id,
                            "tenant_id": tenant_id,
                            "student_id": student_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student_payment_plan (
                              id, tenant_id, student_id, academic_year, name,
                              installment_count, enrollment_fee_cents, status,
                              enrolled_at, version
                            ) VALUES (
                              :id, :tenant_id, :student_id, '2030-2031',
                              'Currently enrolled', 6, 1500, 'enrolled', NOW(), 7
                            )
                            """
                        ),
                        {
                            "id": enrolled_plan_id,
                            "tenant_id": tenant_id,
                            "student_id": student_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student_payment_plan (
                              id, tenant_id, student_id, academic_year, name,
                              installment_count, enrollment_fee_cents, status, version
                            ) VALUES (
                              :id, :tenant_id, :student_id, '2030-2031',
                              'Unchanged sibling', 10, 2000, 'available', 11
                            )
                            """
                        ),
                        {
                            "id": unchanged_sibling_id,
                            "tenant_id": tenant_id,
                            "student_id": student_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student_payment_plan (
                              id, tenant_id, student_id, academic_year, name,
                              installment_count, enrollment_fee_cents, status, version
                            ) VALUES (
                              :selected_id, :tenant_id, :student_id, '2031-2032',
                              'First selection', 4, 1000, 'available', 1
                            ), (
                              :sibling_id, :tenant_id, :student_id, '2031-2032',
                              'First selection sibling', 8, 1000, 'available', 9
                            )
                            """
                        ),
                        {
                            "selected_id": first_selection_id,
                            "sibling_id": first_selection_sibling_id,
                            "tenant_id": tenant_id,
                            "student_id": student_id,
                        },
                    )

                    heap_order_result = await connection.execute(
                        text(
                            """
                            SELECT array_agg(id ORDER BY ctid) AS ids
                            FROM student_payment_plan
                            WHERE tenant_id=:tenant_id AND student_id=:student_id
                              AND academic_year='2030-2031'
                            """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )
                    heap_order = list(heap_order_result.mappings().one()["ids"])
                    assert heap_order.index(switch_target_id) < heap_order.index(enrolled_plan_id)

                    repository = PostgresPortalRepository(
                        cast(AsyncEngine, _TransactionBoundEngine(connection))
                    )
                    auth = AuthContext(
                        tenant_id=str(tenant_id),
                        student_id=str(student_id),
                        actor_id=str(student_id),
                        actor_type="student",
                    )

                    first_result = await repository.select_financial_payment_plan(
                        auth,
                        str(first_selection_id),
                        "payment-first-selection",
                        "request-payment-first-selection",
                    )
                    assert first_result == {
                        "planId": str(first_selection_id),
                        "status": "enrolled",
                    }

                    first_rows_result = await connection.execute(
                        text(
                            """
                            SELECT id, status, version
                            FROM student_payment_plan
                            WHERE tenant_id=:tenant_id AND student_id=:student_id
                              AND academic_year='2031-2032'
                            """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )
                    first_rows = {
                        row["id"]: (row["status"], int(row["version"]))
                        for row in first_rows_result.mappings().all()
                    }
                    assert first_rows == {
                        first_selection_id: ("enrolled", 2),
                        first_selection_sibling_id: ("available", 9),
                    }

                    switch_result = await repository.select_financial_payment_plan(
                        auth,
                        str(switch_target_id),
                        "payment-switch-selection",
                        "request-payment-switch-selection",
                    )
                    assert switch_result == {
                        "planId": str(switch_target_id),
                        "status": "enrolled",
                    }

                    switched_rows_result = await connection.execute(
                        text(
                            """
                            SELECT id, status, enrolled_at, version
                            FROM student_payment_plan
                            WHERE tenant_id=:tenant_id AND student_id=:student_id
                              AND academic_year='2030-2031'
                            """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )
                    switched_rows = {
                        row["id"]: {
                            "status": row["status"],
                            "enrolled_at": row["enrolled_at"],
                            "version": int(row["version"]),
                        }
                        for row in switched_rows_result.mappings().all()
                    }
                    assert switched_rows[switch_target_id]["status"] == "enrolled"
                    assert switched_rows[switch_target_id]["enrolled_at"] is not None
                    assert switched_rows[switch_target_id]["version"] == 5
                    assert switched_rows[enrolled_plan_id] == {
                        "status": "available",
                        "enrolled_at": None,
                        "version": 8,
                    }
                    assert switched_rows[unchanged_sibling_id] == {
                        "status": "available",
                        "enrolled_at": None,
                        "version": 11,
                    }
                    assert sum(row["status"] == "enrolled" for row in switched_rows.values()) == 1

                    events_result = await connection.execute(
                        text(
                            """
                            SELECT correlation_id, aggregate_id, aggregate_version
                            FROM outbox_event
                            WHERE tenant_id=:tenant_id
                              AND event_name='student_financial.payment_plan_selected.v1'
                            """
                        ),
                        {"tenant_id": tenant_id},
                    )
                    events = {
                        row["correlation_id"]: (
                            row["aggregate_id"],
                            int(row["aggregate_version"]),
                        )
                        for row in events_result.mappings().all()
                    }
                    assert events == {
                        "request-payment-first-selection": (first_selection_id, 2),
                        "request-payment-switch-selection": (switch_target_id, 5),
                    }

                    replay_result = await repository.select_financial_payment_plan(
                        auth,
                        str(switch_target_id),
                        "payment-already-enrolled-new-key",
                        "request-payment-already-enrolled",
                    )
                    assert replay_result == switch_result

                    after_noop_result = await connection.execute(
                        text(
                            """
                            SELECT id, status, version
                            FROM student_payment_plan
                            WHERE tenant_id=:tenant_id AND student_id=:student_id
                              AND academic_year='2030-2031'
                            """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )
                    after_noop = {
                        row["id"]: (row["status"], int(row["version"]))
                        for row in after_noop_result.mappings().all()
                    }
                    assert after_noop == {
                        switch_target_id: ("enrolled", 5),
                        enrolled_plan_id: ("available", 8),
                        unchanged_sibling_id: ("available", 11),
                    }
                    no_op_event_result = await connection.execute(
                        text(
                            """
                            SELECT COUNT(*) AS count
                            FROM outbox_event
                            WHERE tenant_id=:tenant_id
                              AND event_name='student_financial.payment_plan_selected.v1'
                            """
                        ),
                        {"tenant_id": tenant_id},
                    )
                    assert int(no_op_event_result.mappings().one()["count"]) == 2
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.postgres
def test_profile_preferred_name_rejects_null_without_mutation_and_persists_boundary() -> None:
    database_url = _database_url()
    tenant_id = uuid4()
    person_id = uuid4()
    student_id = uuid4()
    boundary_name = "P" * 120

    async def scenario() -> None:
        engine = create_async_engine(normalize_database_url(database_url))
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(
                        text("INSERT INTO tenant (id, name) VALUES (:id, :name)"),
                        {"id": tenant_id, "name": f"Profile contract {tenant_id}"},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO person (
                              id, tenant_id, preferred_name, first_name, last_name
                            ) VALUES (
                              :id, :tenant_id, 'Original person', 'Profile', 'Contract'
                            )
                            """
                        ),
                        {"id": person_id, "tenant_id": tenant_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student (id, tenant_id, person_id, class_year)
                            VALUES (:id, :tenant_id, :person_id, 2031)
                            """
                        ),
                        {
                            "id": student_id,
                            "tenant_id": tenant_id,
                            "person_id": person_id,
                        },
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO student_profile (
                              tenant_id, student_id, preferred_name,
                              communication_preference, version
                            ) VALUES (
                              :tenant_id, :student_id, 'Original profile', 'email', 3
                            )
                            """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )

                    repository = PostgresPortalRepository(
                        cast(AsyncEngine, _TransactionBoundEngine(connection))
                    )
                    auth = AuthContext(
                        tenant_id=str(tenant_id),
                        student_id=str(student_id),
                        actor_id=str(student_id),
                        actor_type="student",
                    )

                    with pytest.raises(BadRequestError) as raised:
                        await repository.update_student_profile(
                            auth,
                            {"expectedVersion": 3, "preferredName": None},
                            "request-profile-null",
                        )
                    assert raised.value.code == "INVALID_PREFERRED_NAME"

                    unchanged_result = await connection.execute(
                        text(
                            """
                            SELECT profile.preferred_name AS profile_name, profile.version,
                                   person.preferred_name AS person_name
                            FROM student_profile profile
                            JOIN student ON student.id=profile.student_id
                              AND student.tenant_id=profile.tenant_id
                            JOIN person ON person.id=student.person_id
                              AND person.tenant_id=student.tenant_id
                            WHERE profile.tenant_id=:tenant_id
                              AND profile.student_id=:student_id
                            """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )
                    unchanged = unchanged_result.mappings().one()
                    assert unchanged["profile_name"] == "Original profile"
                    assert int(unchanged["version"]) == 3
                    assert unchanged["person_name"] == "Original person"

                    null_side_effects_result = await connection.execute(
                        text(
                            """
                            SELECT
                              (SELECT COUNT(*) FROM audit_event
                               WHERE tenant_id=:tenant_id
                                 AND request_id='request-profile-null') AS audit_count,
                              (SELECT COUNT(*) FROM outbox_event
                               WHERE tenant_id=:tenant_id
                                 AND correlation_id='request-profile-null') AS outbox_count
                            """
                        ),
                        {"tenant_id": tenant_id},
                    )
                    null_side_effects = null_side_effects_result.mappings().one()
                    assert int(null_side_effects["audit_count"]) == 0
                    assert int(null_side_effects["outbox_count"]) == 0

                    request = UpdateStudentProfileRequest.model_validate(
                        {"expectedVersion": 3, "preferredName": boundary_name}
                    )
                    updated = await repository.update_student_profile(
                        auth,
                        request.public_payload(),
                        "request-profile-boundary",
                    )
                    assert updated["preferredName"] == boundary_name
                    assert updated["version"] == 4

                    persisted_result = await connection.execute(
                        text(
                            """
                            SELECT profile.preferred_name AS profile_name, profile.version,
                                   person.preferred_name AS person_name
                            FROM student_profile profile
                            JOIN student ON student.id=profile.student_id
                              AND student.tenant_id=profile.tenant_id
                            JOIN person ON person.id=student.person_id
                              AND person.tenant_id=student.tenant_id
                            WHERE profile.tenant_id=:tenant_id
                              AND profile.student_id=:student_id
                            """
                        ),
                        {"tenant_id": tenant_id, "student_id": student_id},
                    )
                    persisted = persisted_result.mappings().one()
                    assert persisted["profile_name"] == boundary_name
                    assert int(persisted["version"]) == 4
                    assert persisted["person_name"] == boundary_name

                    event_result = await connection.execute(
                        text(
                            """
                            SELECT aggregate_id, aggregate_version
                            FROM outbox_event
                            WHERE tenant_id=:tenant_id
                              AND correlation_id='request-profile-boundary'
                              AND event_name='student.profile_updated.v1'
                            """
                        ),
                        {"tenant_id": tenant_id},
                    )
                    event = event_result.mappings().one()
                    assert event["aggregate_id"] == student_id
                    assert int(event["aggregate_version"]) == 4
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
