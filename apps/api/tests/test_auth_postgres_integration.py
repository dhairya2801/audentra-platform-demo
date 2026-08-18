from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from audentra.bootstrap.api import create_production_app
from audentra.bootstrap.settings import RuntimeSettings
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.db.migrations import (
    resolve_migrations_directory,
    run_migrations,
)

pytestmark = pytest.mark.integration

ASTER_TENANT_ID = "00000000-0000-7000-8000-000000000001"
HARVARD_TENANT_ID = "00000000-0000-7000-8000-000000000002"


def test_real_postgres_browser_auth_and_deterministic_reset() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run PostgreSQL auth integration tests")

    async def scenario() -> None:
        await run_migrations(database_url, resolve_migrations_directory())
        settings = RuntimeSettings.from_environment(
            {
                "AUDENTRA_ENV": "test",
                "BROWSER_AUTH_REQUIRED": "true",
                "DATABASE_URL": database_url,
                "OBJECT_STORAGE_ENDPOINT": os.getenv(
                    "AUDENTRA_TEST_S3_ENDPOINT", "http://127.0.0.1:9000"
                ),
                "OBJECT_STORAGE_BUCKET": "vv-documents",
                "OBJECT_STORAGE_ACCESS_KEY": "vv_minio",
                "OBJECT_STORAGE_SECRET_KEY": "vv_minio_password",
                "DOCUMENT_WORKER_TOKEN": "integration-document-worker-token",
                "VV_STAFF_INVITATION_CODE": "integration-private-staff-access-code-2027",
            }
        )
        app = create_production_app(settings)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://integration.test",
            ) as client:
                denied = await client.get("/v1/student/bootstrap")
                assert denied.status_code == 401, denied.text

                reset = await client.post(
                    "/v1/auth/demo/start-guided-onboarding",
                    json={},
                )
                assert reset.status_code == 200, reset.text
                verification_engine = create_database_engine(database_url)
                try:
                    async with verification_engine.connect() as connection:
                        outbox_count = await connection.scalar(
                            text("SELECT COUNT(*) FROM outbox_event")
                        )
                        migration_count = await connection.scalar(
                            text("SELECT COUNT(*) FROM vv_schema_migration")
                        )
                    assert outbox_count == 0
                    assert migration_count is not None and migration_count >= 21
                finally:
                    await verification_engine.dispose()
                aster = await client.get(
                    "/v1/student/bootstrap",
                )
                assert aster.status_code == 200, aster.text
                assert aster.json()["initialRoute"] == "/onboarding"

                harvard = await client.get(
                    "/v1/student/bootstrap",
                    headers={"X-Demo-Tenant-Id": HARVARD_TENANT_ID},
                )
                assert harvard.status_code == 200, harvard.text
                assert harvard.json()["student"]["id"].startswith("80000000-")
                campus = await client.get(
                    "/v1/student/campus-life",
                    headers={"X-Demo-Tenant-Id": HARVARD_TENANT_ID},
                )
                assert campus.status_code == 200, campus.text

            unique = uuid4().hex
            email = f"browser.{unique}@example.com"
            phone = f"+1555{int(unique[:8], 16) % 10_000_000:07d}"
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://integration.test",
            ) as credential_client:
                signup = await credential_client.post(
                    "/v1/auth/sign-up",
                    json={
                        "email": f"  {email.upper()}  ",
                        "phone": phone,
                        "password": "Browser-student-123",
                    },
                )
                assert signup.status_code == 201, signup.text
                student_id = signup.json()["student"]["id"]
                bootstrap = await credential_client.get("/v1/student/bootstrap")
                assert bootstrap.status_code == 200, bootstrap.text
                assert bootstrap.json()["student"]["id"] == student_id
                financials = await credential_client.get("/v1/student/financials")
                assert financials.status_code == 200, financials.text
                assert financials.json()["academicYear"] == "2027\N{EN DASH}2028"

                duplicate = await credential_client.post(
                    "/v1/auth/sign-up",
                    json={
                        "email": email,
                        "phone": "+15551239999",
                        "password": "Browser-student-123",
                    },
                )
                assert duplicate.status_code == 409, duplicate.text
                assert duplicate.json()["error"]["code"] == "AUTH_EMAIL_EXISTS"

                signed_out = await credential_client.post("/v1/auth/sign-out")
                assert signed_out.status_code == 200, signed_out.text
                after_sign_out = await credential_client.get("/v1/student/bootstrap")
                assert after_sign_out.status_code == 401, after_sign_out.text

                invalid = await credential_client.post(
                    "/v1/auth/sign-in",
                    json={"email": email, "password": "Wrong-password-123"},
                )
                assert invalid.status_code == 401, invalid.text
                assert invalid.json()["error"]["message"] == "Email or password is incorrect"

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://integration.test",
            ) as staff_client:
                unclaimed_staff = await staff_client.post(
                    "/v1/auth/staff/sign-in",
                    json={
                        "email": "priya.shah@aster.example.edu",
                        "password": "Individual-Aster-staff-2027",
                    },
                )
                assert unclaimed_staff.status_code == 401
                harvard_identity_in_aster = await staff_client.post(
                    "/v1/auth/staff/sign-up",
                    json={
                        "email": "priya.shah@harvard.example.edu",
                        "password": "Individual-Aster-staff-2027",
                        "institutionAccessCode": "integration-private-staff-access-code-2027",
                    },
                )
                assert harvard_identity_in_aster.status_code == 401
                staff_signup = await staff_client.post(
                    "/v1/auth/staff/sign-up",
                    json={
                        "email": "priya.shah@aster.example.edu",
                        "password": "Individual-Aster-staff-2027",
                        "institutionAccessCode": "integration-private-staff-access-code-2027",
                    },
                )
                assert staff_signup.status_code == 201, staff_signup.text
                action_center = await staff_client.get(
                    "/v1/staff/action-center",
                    headers={"X-Demo-Actor-Type": "staff"},
                )
                assert action_center.status_code == 200, action_center.text
                aster_session_in_harvard = await staff_client.get(
                    "/v1/staff/workspace",
                    headers={
                        "X-Demo-Tenant-Id": HARVARD_TENANT_ID,
                        "X-Demo-Actor-Type": "staff",
                    },
                )
                assert aster_session_in_harvard.status_code == 401
                await staff_client.post("/v1/auth/staff/sign-out")
                denied_staff = await staff_client.get(
                    "/v1/staff/action-center",
                    headers={"X-Demo-Actor-Type": "staff"},
                )
                assert denied_staff.status_code == 401, denied_staff.text
                duplicate = await staff_client.post(
                    "/v1/auth/staff/sign-up",
                    json={
                        "email": "priya.shah@aster.example.edu",
                        "password": "Individual-Aster-staff-2027",
                        "institutionAccessCode": "integration-private-staff-access-code-2027",
                    },
                )
                assert duplicate.status_code == 409, duplicate.text
                invalid_password = await staff_client.post(
                    "/v1/auth/staff/sign-in",
                    json={
                        "email": "priya.shah@aster.example.edu",
                        "password": "Wrong-individual-password-2027",
                    },
                )
                assert invalid_password.status_code == 401
                staff_sign_in = await staff_client.post(
                    "/v1/auth/staff/sign-in",
                    json={
                        "email": "priya.shah@aster.example.edu",
                        "password": "Individual-Aster-staff-2027",
                    },
                )
                assert staff_sign_in.status_code == 200, staff_sign_in.text
                await staff_client.post("/v1/auth/staff/sign-out")

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://integration.test",
                headers={"X-Demo-Tenant-Id": HARVARD_TENANT_ID},
            ) as harvard_staff_client:
                aster_identity_in_harvard = await harvard_staff_client.post(
                    "/v1/auth/staff/sign-up",
                    json={
                        "email": "priya.shah@aster.example.edu",
                        "password": "Individual-Harvard-staff-2027",
                        "institutionAccessCode": "integration-private-staff-access-code-2027",
                    },
                )
                assert aster_identity_in_harvard.status_code == 401
                harvard_signup = await harvard_staff_client.post(
                    "/v1/auth/staff/sign-up",
                    json={
                        "email": "priya.shah@harvard.example.edu",
                        "password": "Individual-Harvard-staff-2027",
                        "institutionAccessCode": "integration-private-staff-access-code-2027",
                    },
                )
                assert harvard_signup.status_code == 201, harvard_signup.text
                assert harvard_signup.json()["staff"]["id"].startswith("80000000-")

                harvard_workspace = await harvard_staff_client.get(
                    "/v1/staff/workspace",
                    headers={"X-Demo-Actor-Type": "staff"},
                )
                assert harvard_workspace.status_code == 200, harvard_workspace.text
                workspace = harvard_workspace.json()
                assert workspace["currentStaff"]["email"] == ("priya.shah@harvard.example.edu")
                assert workspace["student"]["student"]["id"].startswith("80000000-")
                assert all(
                    member["email"] != "priya.shah@aster.example.edu"
                    for member in workspace["actionCenter"]["staff"]
                )

                harvard_session_in_aster = await harvard_staff_client.get(
                    "/v1/staff/workspace",
                    headers={
                        "X-Demo-Tenant-Id": ASTER_TENANT_ID,
                        "X-Demo-Actor-Type": "staff",
                    },
                )
                assert harvard_session_in_aster.status_code == 401
                await harvard_staff_client.post("/v1/auth/staff/sign-out")

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://integration.test",
            ) as completed_client:
                completed = await completed_client.post(
                    "/v1/auth/demo/start-guided-onboarding",
                    json={"completedOnboarding": True},
                )
                assert completed.status_code == 200, completed.text
                bootstrap = await completed_client.get("/v1/student/bootstrap")
                assert bootstrap.status_code == 200, bootstrap.text
                assert bootstrap.json()["initialRoute"] == "/dashboard"
                requirements = await completed_client.get("/v1/student/requirements")
                assert requirements.status_code == 200, requirements.text
                assert len(requirements.json()["items"]) == 8

                harvard = await completed_client.get(
                    "/v1/student/bootstrap",
                    headers={"X-Demo-Tenant-Id": HARVARD_TENANT_ID},
                )
                assert harvard.status_code == 200, harvard.text
                assert harvard.json()["initialRoute"] == "/dashboard"
                harvard_financials = await completed_client.get(
                    "/v1/student/financials",
                    headers={"X-Demo-Tenant-Id": HARVARD_TENANT_ID},
                )
                assert harvard_financials.status_code == 200, harvard_financials.text

    asyncio.run(scenario())
