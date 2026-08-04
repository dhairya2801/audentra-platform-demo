from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from audentra.bootstrap.api import create_production_app
from audentra.bootstrap.settings import RuntimeSettings

pytestmark = pytest.mark.integration


def _integration_settings(database_url: str, storage_endpoint: str) -> RuntimeSettings:
    return RuntimeSettings.from_environment(
        {
            "AUDENTRA_ENV": "test",
            "DATABASE_URL": database_url,
            "OBJECT_STORAGE_ENDPOINT": storage_endpoint,
            "OBJECT_STORAGE_BUCKET": os.getenv("AUDENTRA_TEST_S3_BUCKET", "vv-documents"),
            "OBJECT_STORAGE_ACCESS_KEY": os.getenv("AUDENTRA_TEST_S3_ACCESS_KEY", "vv_minio"),
            "OBJECT_STORAGE_SECRET_KEY": os.getenv(
                "AUDENTRA_TEST_S3_SECRET_KEY", "vv_minio_password"
            ),
            "DOCUMENT_WORKER_TOKEN": "integration-document-worker-token",
        }
    )


def test_real_production_composition_reads_seeded_student_and_staff_flows() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Audentra PostgreSQL integration settings are not configured")
    storage_endpoint = os.getenv("AUDENTRA_TEST_S3_ENDPOINT", "http://127.0.0.1:1")

    async def scenario() -> None:
        settings = _integration_settings(database_url, storage_endpoint)
        app = create_production_app(settings)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://integration.test",
            ) as client,
        ):
            student_paths = (
                "/health",
                "/health/ready",
                "/v1/student/bootstrap",
                "/v1/student/dashboard",
                "/v1/student/academics",
                "/v1/catalog/courses?query=computer",
                "/v1/student/financials",
                "/v1/student/campus-life",
                "/v1/student/onboarding",
                "/v1/student/housing-plan",
                "/v1/student/requirements",
                "/v1/student/messages",
                "/v1/student/documents",
                "/v1/student/appointments",
                "/v1/student/payments",
                "/v1/student/profile",
                "/v1/student/help",
            )
            for path in student_paths:
                response = await client.get(path)
                assert response.status_code == 200, (path, response.text)

            staff_headers = {"X-Demo-Actor-Type": "staff"}
            for path in (
                "/v1/staff/action-center",
                "/v1/staff/students/00000000-0000-7000-8000-000000000101",
            ):
                response = await client.get(path, headers=staff_headers)
                assert response.status_code == 200, (path, response.text)

    asyncio.run(scenario())


def test_real_production_composition_executes_core_mutations_idempotently() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL")
    storage_endpoint = os.getenv("AUDENTRA_TEST_S3_ENDPOINT")
    if not database_url or not storage_endpoint:
        pytest.skip("Audentra PostgreSQL and S3 integration settings are not configured")
    source_pdf = (
        Path(__file__).resolve().parents[1] / "assets" / "onboarding" / "aster-ferpa-release.pdf"
    ).read_bytes()

    async def scenario() -> None:
        app = create_production_app(_integration_settings(database_url, storage_endpoint))
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://integration.test",
            ) as client,
        ):

            def key(label: str) -> str:
                return f"{label}-{uuid4()}"

            offer_id = "00000000-0000-7000-8000-000000000201"
            accept_key = key("accept")
            accepted = await client.post(
                f"/v1/admission-offers/{offer_id}/accept",
                headers={"Idempotency-Key": accept_key},
            )
            assert accepted.status_code == 200, accepted.text
            replayed = await client.post(
                f"/v1/admission-offers/{offer_id}/accept",
                headers={"Idempotency-Key": accept_key},
            )
            assert replayed.status_code == 200, replayed.text
            assert replayed.json() == accepted.json()

            profile = (await client.get("/v1/student/profile")).json()
            updated_profile = await client.patch(
                "/v1/student/profile",
                json={
                    "expectedVersion": profile["version"],
                    "preferredName": "Alex Integration",
                    "communicationPreference": "email",
                },
            )
            assert updated_profile.status_code == 200, updated_profile.text
            assert updated_profile.json()["preferredName"] == "Alex Integration"

            activity = await client.post(
                "/v1/activity-events/batch",
                json={
                    "events": [
                        {
                            "eventId": str(uuid4()),
                            "eventName": "ui.dashboard_viewed.v1",
                            "occurredAt": datetime.now(UTC).isoformat(),
                            "sessionId": key("session"),
                            "pageInstanceId": key("page"),
                            "properties": {
                                "projection_version": 1,
                                "journey_status": "active",
                            },
                        }
                    ]
                },
            )
            assert activity.status_code == 202, activity.text

            messages = (await client.get("/v1/student/messages")).json()["items"]
            if messages:
                read = await client.post(f"/v1/student/messages/{messages[0]['id']}/read")
                assert read.status_code == 200, read.text

            appointment_key = key("appointment")
            appointment_body = {
                "type": "enrollment_support",
                "startsAt": (datetime.now(UTC) + timedelta(days=45)).isoformat(),
                "notes": "FastAPI integration verification",
            }
            appointment = await client.post(
                "/v1/student/appointments",
                headers={"Idempotency-Key": appointment_key},
                json=appointment_body,
            )
            assert appointment.status_code == 201, appointment.text
            appointment_replay = await client.post(
                "/v1/student/appointments",
                headers={"Idempotency-Key": appointment_key},
                json=appointment_body,
            )
            assert appointment_replay.status_code == 201, appointment_replay.text
            assert appointment_replay.json() == appointment.json()

            guided = await client.post(
                "/v1/student/assistant/messages",
                json={
                    "message": "What should I do next?",
                    "pageContext": "/dashboard",
                    "history": [],
                },
            )
            assert guided.status_code == 200, guided.text
            assert guided.json()["provider"] == "guided"

            upload = await client.post(
                "/v1/student/documents/upload",
                headers={"Idempotency-Key": key("upload")},
                files={"file": ("integration-ferpa.pdf", source_pdf, "application/pdf")},
                data={"category": "other"},
            )
            assert upload.status_code == 201, upload.text
            document_id = upload.json()["id"]
            content = await client.get(f"/v1/student/documents/{document_id}/content")
            assert content.status_code == 200, content.text
            assert content.content == source_pdf

            requirements_response = await client.get("/v1/student/requirements")
            assert requirements_response.status_code == 200, requirements_response.text
            transcript_requirement = next(
                item
                for item in requirements_response.json()["items"]
                if item["code"] == "official_transcript"
            )
            contextual_upload = await client.post(
                "/v1/student/documents/upload",
                headers={"Idempotency-Key": key("requirement-upload")},
                files={"file": ("integration-transcript.pdf", source_pdf, "application/pdf")},
                data={
                    "category": "other",
                    "requirementId": transcript_requirement["id"],
                    "uploadBundleId": str(uuid4()),
                },
            )
            assert contextual_upload.status_code == 201, contextual_upload.text
            assert contextual_upload.json()["requirementId"] == transcript_requirement["id"]
            assert contextual_upload.json()["category"] == "transcript"

            processed = await client.post(
                f"/v1/student/internal/document-extractions/{document_id}",
                headers={"X-VV-Worker-Token": "integration-document-worker-token"},
            )
            assert processed.status_code == 200, processed.text

    asyncio.run(scenario())
