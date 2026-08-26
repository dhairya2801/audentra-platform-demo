"""Adviser relationships, availability-aware booking and demo staff login on PostgreSQL.

Runs against the compact Aster fixture after a guided reset, then layers a
small advising office on top: one adviser with published hours and a blocked
hour, one adviser on leave, one who left. The assertions are the product
promises: a student sees who their adviser is and what that person's state
means for them; booking cannot conflict, cannot fall outside hours and cannot
land on a person who is away; cancel/reschedule keep the calendar honest; a
staff member opened through the development "log in as" panel sees exactly
their own identity, caseload, calendar and team.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import text

from audentra.bootstrap.api import create_production_app
from audentra.bootstrap.settings import RuntimeSettings
from audentra.infrastructure.db.engine import create_database_engine
from audentra.infrastructure.db.migrations import resolve_migrations_directory, run_migrations

pytestmark = [pytest.mark.integration, pytest.mark.postgres]

TENANT_ID = "00000000-0000-7000-8000-000000000001"
HARVARD_TENANT_ID = "00000000-0000-7000-8000-000000000002"
DEMO_STUDENT_ID = "00000000-0000-7000-8000-000000000101"
PRIYA_ID = "00000000-0000-7000-8000-000000000901"  # Admissions, will manage the advisers
MARCUS_ID = "00000000-0000-7000-8000-000000000902"  # becomes the active adviser
HARVARD_STAFF_ID = "80000000-0000-7000-8000-000000000901"
DEPARTED_ID = str(uuid4())
ON_LEAVE_ID = str(uuid4())
ET = ZoneInfo("America/New_York")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _tomorrow_at(hour: int, minute: int = 0) -> datetime:
    local = datetime.now(ET) + timedelta(days=1)
    return datetime(local.year, local.month, local.day, hour, minute, tzinfo=ET).astimezone(UTC)


async def _install_advising_office(database_url: str) -> tuple[str, str]:
    """Enrich the compact fixture and return two extra Aster student ids."""

    engine = create_database_engine(database_url)
    try:
        async with engine.begin() as connection:
            students = await connection.execute(
                text(
                    """
                    SELECT id FROM student
                    WHERE tenant_id=:tenant_id AND id<>:demo
                    ORDER BY created_at, id LIMIT 2
                    """
                ),
                {"tenant_id": TENANT_ID, "demo": DEMO_STUDENT_ID},
            )
            orphaned, on_leave_student = [str(row[0]) for row in students.all()]
            await connection.execute(
                text(
                    """
                    UPDATE staff_member
                    SET title='Associate Director of Admissions', role_code='associate_director',
                        timezone='America/New_York', external_ref='SYN-STF-ADM-AD',
                        appointment_types=ARRAY['admissions_counseling']
                    WHERE tenant_id=:tenant_id AND id=:id
                    """
                ),
                {"tenant_id": TENANT_ID, "id": PRIYA_ID},
            )
            await connection.execute(
                text(
                    """
                    UPDATE staff_member
                    SET title='Academic Adviser', role_code='academic_adviser',
                        manager_id=:manager, timezone='America/New_York',
                        external_ref='SYN-ADV-902', caseload_cap=2, office_location='Advising 12',
                        appointment_types=ARRAY['academic_advising']
                    WHERE tenant_id=:tenant_id AND id=:id
                    """
                ),
                {"tenant_id": TENANT_ID, "id": MARCUS_ID, "manager": PRIYA_ID},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_member (
                      id, tenant_id, display_name, email_normalized, component, active,
                      title, role_code, manager_id, employment_status, ended_at, timezone,
                      caseload_cap, appointment_types, external_ref
                    ) VALUES
                    (:departed, :tenant_id, 'Quentin Zephyrine', 'quentin@aster.example.edu',
                     'Academic Advising', false, 'Academic Adviser', 'academic_adviser', :manager,
                     'departed', CURRENT_DATE - 20, 'America/New_York', 100,
                     ARRAY['academic_advising'], 'SYN-ADV-DEPARTED'),
                    (:on_leave, :tenant_id, 'Junia Pemberwell', 'junia@aster.example.edu',
                     'Academic Advising', true, 'Senior Academic Adviser', 'academic_adviser',
                     :manager, 'on_leave', NULL, 'America/New_York', 100,
                     ARRAY['academic_advising'], 'SYN-ADV-LEAVE')
                    """
                ),
                {
                    "departed": DEPARTED_ID,
                    "on_leave": ON_LEAVE_ID,
                    "tenant_id": TENANT_ID,
                    "manager": PRIYA_ID,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE staff_member SET leave_until=CURRENT_DATE + 30
                    WHERE tenant_id=:tenant_id AND id=:id
                    """
                ),
                {"tenant_id": TENANT_ID, "id": ON_LEAVE_ID},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO student_staff_assignment (
                      id, tenant_id, student_id, staff_member_id, role, source
                    ) VALUES
                    (gen_random_uuid(), :tenant_id, :demo, :marcus, 'primary_advisor', 'test'),
                    (gen_random_uuid(), :tenant_id, :orphaned, :departed, 'primary_advisor',
                     'test'),
                    (gen_random_uuid(), :tenant_id, :leave_student, :on_leave, 'primary_advisor',
                     'test'),
                    (gen_random_uuid(), :tenant_id, :demo, :priya, 'admissions_counselor', 'test')
                    """
                ),
                {
                    "tenant_id": TENANT_ID,
                    "demo": DEMO_STUDENT_ID,
                    "marcus": MARCUS_ID,
                    "orphaned": orphaned,
                    "departed": DEPARTED_ID,
                    "leave_student": on_leave_student,
                    "on_leave": ON_LEAVE_ID,
                    "priya": PRIYA_ID,
                },
            )
            for weekday in range(7):
                await connection.execute(
                    text(
                        """
                        INSERT INTO staff_availability (
                          id, tenant_id, staff_member_id, weekday, start_minute, end_minute,
                          modality, location, appointment_types, slot_minutes
                        ) VALUES (gen_random_uuid(), :tenant_id, :staff, :weekday, 540, 1020,
                                  'either', 'Advising 12', ARRAY['academic_advising'], 30)
                        """
                    ),
                    {"tenant_id": TENANT_ID, "staff": MARCUS_ID, "weekday": weekday},
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_time_off (
                      id, tenant_id, staff_member_id, starts_at, ends_at, kind, note
                    ) VALUES (gen_random_uuid(), :tenant_id, :staff, :starts, :ends, 'blocked',
                              'Department meeting')
                    """
                ),
                {
                    "tenant_id": TENANT_ID,
                    "staff": MARCUS_ID,
                    "starts": _tomorrow_at(14),
                    "ends": _tomorrow_at(15),
                },
            )
        return orphaned, on_leave_student
    finally:
        await engine.dispose()


def test_advising_relationships_booking_and_demo_staff_login() -> None:
    database_url = os.getenv("AUDENTRA_TEST_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set AUDENTRA_TEST_DATABASE_URL to run the advising integration test")

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
                transport=transport, base_url="http://integration.test"
            ) as student:
                reset = await student.post("/v1/auth/demo/start-guided-onboarding", json={})
                assert reset.status_code == 200, reset.text
                orphaned, leave_student = await _install_advising_office(database_url)
                await _student_side(student, orphaned, leave_student)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://integration.test"
            ) as staff:
                await _staff_side(staff)

    asyncio.run(scenario())


async def _student_side(client: httpx.AsyncClient, orphaned: str, leave_student: str) -> None:
    ten = _tomorrow_at(10)
    advising = await client.get("/v1/student/advising")
    assert advising.status_code == 200, advising.text
    body = advising.json()
    assert body["primaryAdviser"]["staff"]["id"] == MARCUS_ID
    assert body["primaryAdviser"]["staff"]["title"] == "Academic Adviser"
    assert body["primaryAdviser"]["availability"]["nextOpenSlotAt"] is not None
    assert body["gaps"] == []
    assert {entry["role"] for entry in body["advisers"]} == {
        "primary_advisor",
        "admissions_counselor",
    }
    assert body["advising"]["status"] == "none"

    availability = await client.get(
        "/v1/student/appointments/availability", params={"type": "academic_advising"}
    )
    assert availability.status_code == 200, availability.text
    offer = availability.json()["staff"][0]
    assert offer["id"] == MARCUS_ID
    assert offer["relationship"] == "primary_advisor"
    starts = {slot["startsAt"] for slot in offer["slots"]}
    assert _iso(ten) in starts
    assert _iso(_tomorrow_at(14)) not in starts  # blocked hour
    assert _iso(_tomorrow_at(14, 30)) not in starts
    assert _iso(_tomorrow_at(17)) not in starts  # hours end at 17:00

    booking = await client.post(
        "/v1/student/appointments",
        json={"type": "academic_advising", "startsAt": _iso(ten), "notes": "Course plan"},
        headers={"Idempotency-Key": "advising-book-1"},
    )
    assert booking.status_code == 201, booking.text
    appointment = booking.json()
    assert appointment["staff"]["id"] == MARCUS_ID
    assert appointment["endsAt"] == _iso(ten + timedelta(minutes=30))
    assert appointment["location"] == "Advising 12"
    assert appointment["modality"] == "in_person"

    replay = await client.post(
        "/v1/student/appointments",
        json={"type": "academic_advising", "startsAt": _iso(ten), "notes": "Course plan"},
        headers={"Idempotency-Key": "advising-book-1"},
    )
    assert replay.status_code == 201 and replay.json()["id"] == appointment["id"]

    conflict = await client.post(
        "/v1/student/appointments",
        json={"type": "academic_advising", "startsAt": _iso(ten)},
        headers={"Idempotency-Key": "advising-book-2"},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "APPOINTMENT_SLOT_TAKEN"

    for key, when, code in (
        ("advising-book-3", _tomorrow_at(3), "OUTSIDE_WORKING_HOURS"),
        ("advising-book-4", _tomorrow_at(12, 15), "APPOINTMENT_OFF_GRID"),
        ("advising-book-5", _tomorrow_at(14), "STAFF_UNAVAILABLE"),
    ):
        refused = await client.post(
            "/v1/student/appointments",
            json={"type": "academic_advising", "startsAt": _iso(when)},
            headers={"Idempotency-Key": key},
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == code, refused.text

    wrong_person = await client.post(
        "/v1/student/appointments",
        json={"type": "financial_aid", "startsAt": _iso(ten), "staffMemberId": MARCUS_ID},
        headers={"Idempotency-Key": "advising-book-6"},
    )
    assert wrong_person.status_code == 409
    assert wrong_person.json()["error"]["code"] == "STAFF_DOES_NOT_OFFER_TYPE"

    foreign = await client.get(
        "/v1/student/appointments/availability",
        params={"type": "academic_advising", "staffMemberId": HARVARD_STAFF_ID},
    )
    assert foreign.status_code == 404

    after = await client.get(
        "/v1/student/appointments/availability", params={"type": "academic_advising"}
    )
    assert _iso(ten) not in {slot["startsAt"] for slot in after.json()["staff"][0]["slots"]}

    advising = await client.get("/v1/student/advising")
    assert advising.json()["advising"]["status"] == "scheduled"
    assert advising.json()["advising"]["nextAppointment"]["id"] == appointment["id"]

    eleven = _tomorrow_at(11)
    rescheduled = await client.post(
        f"/v1/student/appointments/{appointment['id']}/reschedule",
        json={"startsAt": _iso(eleven)},
        headers={"Idempotency-Key": "advising-resched-1"},
    )
    assert rescheduled.status_code == 200, rescheduled.text
    replacement = rescheduled.json()
    assert replacement["id"] != appointment["id"]
    assert replacement["rescheduledFromId"] == appointment["id"]
    assert replacement["staff"]["id"] == MARCUS_ID
    assert replacement["notes"] == "Course plan"

    listing = await client.get("/v1/student/appointments")
    by_id = {item["id"]: item for item in listing.json()["items"]}
    assert by_id[appointment["id"]]["status"] == "rescheduled"
    assert by_id[appointment["id"]]["rescheduledToId"] == replacement["id"]
    assert by_id[replacement["id"]]["status"] == "scheduled"

    stale = await client.post(
        f"/v1/student/appointments/{appointment['id']}/cancel", json={"reason": "late"}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "APPOINTMENT_NOT_ACTIVE"

    cancelled = await client.post(
        f"/v1/student/appointments/{replacement['id']}/cancel", json={"reason": "Clash"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancelReason"] == "Clash"

    freed = await client.get(
        "/v1/student/appointments/availability", params={"type": "academic_advising"}
    )
    assert _iso(eleven) in {slot["startsAt"] for slot in freed.json()["staff"][0]["slots"]}

    final = await client.post(
        "/v1/student/appointments",
        json={"type": "academic_advising", "startsAt": _iso(eleven), "notes": "Course plan"},
        headers={"Idempotency-Key": "advising-book-7"},
    )
    assert final.status_code == 201, final.text

    # A student whose adviser is on leave: the state is named, and booking is refused.
    switched = await client.post("/v1/auth/demo/sign-in-as", json={"studentRef": leave_student})
    assert switched.status_code == 200, switched.text
    advising = await client.get("/v1/student/advising")
    assert [gap["code"] for gap in advising.json()["gaps"]] == ["adviser_on_leave"]
    assert advising.json()["primaryAdviser"]["staff"]["employmentStatus"] == "on_leave"
    assert advising.json()["primaryAdviser"]["availability"]["reason"] == "on_leave"
    refused = await client.post(
        "/v1/student/appointments",
        json={"type": "academic_advising", "startsAt": _iso(ten)},
        headers={"Idempotency-Key": "advising-book-8"},
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "STAFF_MEMBER_ON_LEAVE"

    # A student whose adviser left: the gap is visible, not silently ignored.
    switched = await client.post("/v1/auth/demo/sign-in-as", json={"studentRef": orphaned})
    assert switched.status_code == 200, switched.text
    advising = await client.get("/v1/student/advising")
    assert [gap["code"] for gap in advising.json()["gaps"]] == ["adviser_departed"]
    refused = await client.post(
        "/v1/student/appointments",
        json={"type": "academic_advising", "startsAt": _iso(ten)},
        headers={"Idempotency-Key": "advising-book-9"},
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "STAFF_MEMBER_DEPARTED"

    # Without any adviser for the type, the legacy unassigned booking still works.
    legacy = await client.post(
        "/v1/student/appointments",
        json={"type": "enrollment_support", "startsAt": _iso(ten)},
        headers={"Idempotency-Key": "advising-book-10"},
    )
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["staff"] is None


async def _staff_side(client: httpx.AsyncClient) -> None:
    eleven = _tomorrow_at(11)
    anonymous = await client.get("/v1/staff/me", headers={"X-Demo-Actor-Type": "staff"})
    assert anonymous.status_code == 401

    directory = await client.get("/v1/auth/demo/staff/directory", params={"q": "advis"})
    assert directory.status_code == 200, directory.text
    entries = {item["id"]: item for item in directory.json()["items"]}
    assert entries[MARCUS_ID]["caseload"] == {"primaryAdvisees": 1, "cap": 2}
    assert entries[DEPARTED_ID]["canSignIn"] is False
    assert entries[ON_LEAVE_ID]["employmentStatus"] == "on_leave"
    assert entries[MARCUS_ID]["managerName"] == "Priya Shah"

    departed = await client.post(
        "/v1/auth/demo/staff/sign-in-as", json={"staffRef": "SYN-ADV-DEPARTED"}
    )
    assert departed.status_code == 409
    assert departed.json()["error"]["code"] == "STAFF_MEMBER_DEPARTED"

    signed_in = await client.post(
        "/v1/auth/demo/staff/sign-in-as", json={"staffRef": "marcus.lee@aster.example.edu"}
    )
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["mode"] == "demo"
    assert signed_in.json()["staff"]["title"] == "Academic Adviser"

    me = await client.get("/v1/staff/me", headers={"X-Demo-Actor-Type": "staff"})
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["staff"]["id"] == MARCUS_ID
    assert body["staff"]["roleCode"] == "academic_adviser"
    assert body["manager"]["id"] == PRIYA_ID
    assert body["caseload"]["primaryAdvisees"] == 1
    assert body["caseload"]["cap"] == 2
    assert body["caseload"]["utilization"] == 0.5
    assert len(body["availability"]["weekly"]) == 7
    assert body["availability"]["openSlotsNext14Days"] > 100
    assert any(entry["kind"] == "blocked" for entry in body["availability"]["timeOff"])
    assert body["directReports"] == []

    workspace = await client.get("/v1/staff/workspace", headers={"X-Demo-Actor-Type": "staff"})
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["currentStaff"]["id"] == MARCUS_ID

    caseload = await client.get("/v1/staff/caseload", headers={"X-Demo-Actor-Type": "staff"})
    assert caseload.status_code == 200, caseload.text
    items = caseload.json()["items"]
    assert [item["student"]["id"] for item in items] == [DEMO_STUDENT_ID]
    assert items[0]["role"] == "primary_advisor"
    assert items[0]["advising"]["status"] == "scheduled"
    assert items[0]["advising"]["nextAppointmentAt"] == _iso(eleven)

    calendar = await client.get(
        "/v1/staff/appointments",
        headers={"X-Demo-Actor-Type": "staff"},
        params={"from": _iso(eleven - timedelta(days=1)), "to": _iso(eleven + timedelta(days=1))},
    )
    assert calendar.status_code == 200, calendar.text
    scheduled = [item for item in calendar.json()["items"] if item["status"] == "scheduled"]
    assert len(scheduled) == 1
    assert scheduled[0]["student"]["id"] == DEMO_STUDENT_ID
    appointment_id = scheduled[0]["id"]

    done = await client.patch(
        f"/v1/staff/appointments/{appointment_id}",
        headers={"X-Demo-Actor-Type": "staff"},
        json={"status": "completed", "outcomeNote": "Registered for fall"},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"
    assert done.json()["outcomeNote"] == "Registered for fall"

    again = await client.patch(
        f"/v1/staff/appointments/{appointment_id}",
        headers={"X-Demo-Actor-Type": "staff"},
        json={"status": "no_show"},
    )
    assert again.status_code == 409

    caseload = await client.get("/v1/staff/caseload", headers={"X-Demo-Actor-Type": "staff"})
    assert caseload.json()["items"][0]["advising"]["status"] == "completed"
    assert caseload.json()["summary"]["advising"]["completed"] == 1

    # The director's chair: the whole advising team with its states and gaps.
    director = await client.post("/v1/auth/demo/staff/sign-in-as", json={"staffRef": PRIYA_ID})
    assert director.status_code == 200, director.text
    me = await client.get("/v1/staff/me", headers={"X-Demo-Actor-Type": "staff"})
    assert me.status_code == 200, me.text
    body = me.json()
    reports = {entry["id"]: entry for entry in body["directReports"]}
    assert set(reports) == {MARCUS_ID, DEPARTED_ID, ON_LEAVE_ID}
    assert "departed_with_caseload" in reports[DEPARTED_ID]["flags"]
    assert "on_leave_with_caseload" in reports[ON_LEAVE_ID]["flags"]
    assert reports[ON_LEAVE_ID]["availability"]["reason"] == "on_leave"
    assert reports[MARCUS_ID]["availability"]["nextOpenSlotAt"] is not None
    summary = body["componentSummary"]
    assert summary["membersDeparted"] == 1
    assert summary["membersOnLeave"] == 1
    assert summary["studentsWithDepartedAdviser"] == 1
    assert summary["studentsWithAdviserOnLeave"] == 1

    # A manager may close out a report's appointment; a stranger may not.
    other = await client.get(
        "/v1/staff/appointments",
        headers={"X-Demo-Actor-Type": "staff"},
        params={"staffMemberId": MARCUS_ID},
    )
    assert other.status_code == 200
    assert other.json()["staff"]["id"] == MARCUS_ID

    elena = await client.post(
        "/v1/auth/demo/staff/sign-in-as", json={"staffRef": "elena.torres@aster.example.edu"}
    )
    assert elena.status_code == 200, elena.text
    forbidden = await client.patch(
        f"/v1/staff/appointments/{appointment_id}",
        headers={"X-Demo-Actor-Type": "staff"},
        json={"status": "cancelled"},
    )
    assert forbidden.status_code in {403, 409}, forbidden.text

    signed_out = await client.post("/v1/auth/staff/sign-out")
    assert signed_out.status_code == 200
    denied = await client.get("/v1/staff/me", headers={"X-Demo-Actor-Type": "staff"})
    assert denied.status_code == 401
