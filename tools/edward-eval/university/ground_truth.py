"""Deterministic ground truth for the Staff Edward university benchmark.

Reads the *product's own* database (a frozen snapshot of the tenant the mock
university was deployed into) with plain SQL, plus — optionally — the
Explorer database as the oracle for materialised appointment slots. Nothing
here asks a model what the answer should be: every expected fact is a row
count, a name, a date or a status the product itself stores.

    DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:55432/vv_enrollment_staff_eval \
    EXPLORER_DATABASE_URL=postgres://explorer:explorer_local_password@localhost:5439/aster_university \
    uv run --directory apps/api --locked python tools/edward-eval/university/ground_truth.py \
      > artifacts/university-eval/ground-truth.json

Time-relative facts (overdue, today, this week) are computed with the same
``now()`` Edward sees, so regenerate the file right before a run.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

TENANT = os.environ.get("STAFF_EVAL_TENANT_ID", "00000000-0000-7000-8000-000000000003")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://vv:vv_local_password@127.0.0.1:55432/vv_enrollment_staff_eval"
)
EXPLORER_URL = os.environ.get(
    "EXPLORER_DATABASE_URL",
    "postgres://explorer:explorer_local_password@127.0.0.1:5439/aster_university",
)

OPEN = "status NOT IN ('done','cancelled')"

# The people the benchmark signs in as, or asks about. External refs are the
# mock university's stable ids; names are looked up, never assumed.
STAFF_REFS = [
    "SYN-STF-VP",
    "SYN-STF-ADV-DIR",
    "SYN-STF-ADV-AD1",
    "SYN-STF-ADM-AD",
    "SYN-STF-ADM-DIR",
    "SYN-STF-REG-DIR",
    "SYN-STF-REG-EV2",
    "SYN-STF-REG-TCE",
    "SYN-STF-ISS-DSO1",
    "SYN-STF-ISS-DSO2",
    "SYN-STF-ISS-DIR",
    "SYN-STF-FA-DIR",
    "SYN-STF-ADM-OPS2",
    "SYN-STF-HRL-ASG",
    "SYN-STF-SHS-REC1",
    "SYN-STF-ES-DIR",
    "SYN-ADV-000",
    "SYN-ADV-001",
    "SYN-ADV-003",
    "SYN-ADV-005",
    "SYN-ADV-008",
    "SYN-ADV-009",
    "SYN-ADV-012",
    "SYN-ADV-013",
    "SYN-STF-ADV-025",
]
STUDENT_NAMES = ["Ivo Netherby", "Junia Calderwood"]
STUDENT_REFS = [f"SYN-{n:06d}" for n in range(0, 10)] + [
    "SYN-000013",
    "SYN-000023",
    "SYN-000034",
    "SYN-000039",
]


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def _date(value: Any) -> str | None:
    text = _iso(value)
    return text[:10] if text else None


async def _val(conn: asyncpg.Connection, sql: str, *args: Any) -> Any:
    return await conn.fetchval(sql, *args)


async def queue_truth(conn: asyncpg.Connection, tenant: str, now: datetime) -> dict[str, Any]:
    row = await conn.fetchrow(
        f"""
        SELECT
          COUNT(*) FILTER (WHERE {OPEN}) AS open,
          COUNT(*) FILTER (WHERE {OPEN} AND assignee_id IS NULL) AS unassigned,
          COUNT(*) FILTER (WHERE {OPEN} AND priority='urgent') AS urgent,
          COUNT(*) FILTER (WHERE {OPEN} AND escalated) AS escalated,
          COUNT(*) FILTER (WHERE {OPEN} AND due_at IS NOT NULL AND due_at < $2::timestamptz) AS overdue,
          COUNT(*) FILTER (WHERE {OPEN} AND due_at IS NOT NULL AND due_at >= $2::timestamptz
                             AND due_at < $2::timestamptz + interval '7 days') AS due_next_7_days,
          COUNT(*) FILTER (WHERE {OPEN} AND due_at IS NOT NULL
                             AND due_at::date = ($2::timestamptz AT TIME ZONE 'UTC')::date) AS due_today,
          COUNT(*) FILTER (WHERE status='in_progress' AND updated_at < $2::timestamptz - interval '10 days')
            AS stale_in_progress,
          COUNT(*) FILTER (WHERE {OPEN} AND priority IN ('urgent','high')) AS urgent_or_high,
          COUNT(DISTINCT student_id) FILTER (WHERE {OPEN}) AS distinct_students,
          COUNT(*) FILTER (WHERE status='done') AS done,
          COUNT(*) AS total
        FROM staff_work_item WHERE tenant_id=$1
        """,
        tenant,
        now,
    )
    by_component = await conn.fetch(
        f"""
        SELECT component,
          COUNT(*) FILTER (WHERE {OPEN}) AS open,
          COUNT(*) FILTER (WHERE {OPEN} AND assignee_id IS NULL) AS unassigned,
          COUNT(*) FILTER (WHERE {OPEN} AND due_at < $2::timestamptz) AS overdue,
          COUNT(*) FILTER (WHERE {OPEN} AND priority='urgent') AS urgent,
          COUNT(*) FILTER (WHERE {OPEN} AND priority='urgent' AND assignee_id IS NULL)
            AS unassigned_urgent,
          COUNT(*) FILTER (WHERE {OPEN} AND escalated) AS escalated,
          COUNT(*) FILTER (WHERE {OPEN} AND work_type='document_review') AS open_document_reviews,
          COUNT(*) FILTER (WHERE {OPEN} AND work_type='document_review' AND due_at < $2::timestamptz)
            AS overdue_document_reviews
        FROM staff_work_item WHERE tenant_id=$1 GROUP BY component ORDER BY open DESC
        """,
        tenant,
        now,
    )
    by_assignee = await conn.fetch(
        f"""
        SELECT m.display_name AS name, m.id::text AS id, m.external_ref, m.component,
          COUNT(*) FILTER (WHERE {OPEN}) AS open,
          COUNT(*) FILTER (WHERE {OPEN} AND w.due_at < $2::timestamptz) AS overdue,
          COUNT(*) FILTER (WHERE w.status='in_progress' AND w.updated_at < $2::timestamptz - interval '10 days')
            AS stale
        FROM staff_work_item w JOIN staff_member m ON m.id=w.assignee_id
        WHERE w.tenant_id=$1 GROUP BY m.id ORDER BY overdue DESC, open DESC
        """,
        tenant,
        now,
    )
    by_action_type = await conn.fetch(
        f"SELECT action_type, COUNT(*) AS n FROM staff_work_item WHERE tenant_id=$1 AND {OPEN} "
        "GROUP BY 1 ORDER BY 2 DESC",
        tenant,
    )
    head = await conn.fetchrow(
        f"""
        SELECT w.key, w.title, w.priority, w.due_at, person.first_name||' '||person.last_name AS student
        FROM staff_work_item w
        JOIN student s ON s.id=w.student_id JOIN person ON person.id=s.person_id
        WHERE w.tenant_id=$1 AND w.{OPEN}
        ORDER BY CASE w.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2
                 ELSE 3 END, w.due_at NULLS LAST, w.updated_at DESC LIMIT 1
        """,
        tenant,
    )
    unassigned_students = await _val(
        conn,
        f"SELECT COUNT(DISTINCT student_id) FROM staff_work_item WHERE tenant_id=$1 AND {OPEN} "
        "AND assignee_id IS NULL",
        tenant,
    )
    transcript_topic = await _val(
        conn,
        f"SELECT COUNT(*) FROM staff_work_item WHERE tenant_id=$1 AND {OPEN} "
        "AND (title ILIKE '%transcript%' OR description ILIKE '%transcript%')",
        tenant,
    )
    return {
        **{key: int(row[key]) for key in row.keys()},
        "byComponent": {
            r["component"]: {k: int(r[k]) for k in r.keys() if k != "component"}
            for r in by_component
        },
        "mostOpenComponent": by_component[0]["component"] if by_component else None,
        "mostUnassignedComponent": max(by_component, key=lambda r: r["unassigned"])["component"]
        if by_component
        else None,
        "mostOverdueComponent": max(by_component, key=lambda r: r["overdue"])["component"]
        if by_component
        else None,
        "byAssigneeTop": [dict(r) for r in by_assignee[:12]],
        "mostOverdueAssignee": dict(by_assignee[0]) if by_assignee else None,
        "byActionType": {r["action_type"]: int(r["n"]) for r in by_action_type},
        "head": dict(head) if head else None,
        "transcriptTopicOpen": int(transcript_topic or 0),
        "unassignedDistinctStudents": int(unassigned_students or 0),
    }


async def inquiry_truth(conn: asyncpg.Connection, tenant: str, now: datetime) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT
          COUNT(*) FILTER (WHERE status='new' AND archived_at IS NULL) AS awaiting_first_reply,
          COUNT(*) FILTER (WHERE status IN ('new','open','waiting_on_student') AND archived_at IS NULL)
            AS open,
          COUNT(*) FILTER (WHERE status='open' AND archived_at IS NULL) AS open_status,
          COUNT(*) FILTER (WHERE status='waiting_on_student' AND archived_at IS NULL)
            AS waiting_on_student,
          COUNT(*) FILTER (WHERE status IN ('new','open') AND archived_at IS NULL
                             AND assignee_id IS NULL) AS unassigned_open,
          COUNT(*) FILTER (WHERE status='new' AND archived_at IS NULL
                             AND created_at < $2::timestamptz - interval '24 hours') AS awaiting_over_24h,
          COUNT(*) FILTER (WHERE status='resolved') AS resolved,
          COUNT(*) FILTER (WHERE archived_at IS NOT NULL) AS archived,
          COUNT(*) FILTER (WHERE status='new' AND archived_at IS NULL AND priority='urgent')
            AS awaiting_urgent
        FROM student_inquiry WHERE tenant_id=$1
        """,
        tenant,
        now,
    )
    oldest = await conn.fetchrow(
        """
        SELECT i.subject, i.created_at, i.priority, i.topic_code,
               person.first_name||' '||person.last_name AS student, s.external_ref
        FROM student_inquiry i JOIN student s ON s.id=i.student_id JOIN person ON person.id=s.person_id
        WHERE i.tenant_id=$1 AND i.status='new' AND i.archived_at IS NULL
        ORDER BY i.created_at, i.id LIMIT 1
        """,
        tenant,
    )
    by_topic = await conn.fetch(
        "SELECT topic_code, COUNT(*) AS n FROM student_inquiry WHERE tenant_id=$1 AND status='new' "
        "AND archived_at IS NULL GROUP BY 1 ORDER BY 2 DESC",
        tenant,
    )
    by_assignee = await conn.fetch(
        """
        SELECT m.display_name AS name, m.external_ref, COUNT(*) AS n
        FROM student_inquiry i JOIN staff_member m ON m.id=i.assignee_id
        WHERE i.tenant_id=$1 AND i.status IN ('new','open','waiting_on_student') AND i.archived_at IS NULL
        GROUP BY 1,2 ORDER BY 3 DESC
        """,
        tenant,
    )
    return {
        **{key: int(row[key]) for key in row.keys()},
        "oldestAwaiting": {
            "subject": oldest["subject"],
            "createdAt": _iso(oldest["created_at"]),
            "ageDays": round((now - oldest["created_at"]).total_seconds() / 86400, 1),
            "priority": oldest["priority"],
            "topic": oldest["topic_code"],
            "student": oldest["student"],
            "studentExternalRef": oldest["external_ref"],
        }
        if oldest
        else None,
        "awaitingByTopic": {r["topic_code"]: int(r["n"]) for r in by_topic},
        "openByAssignee": [dict(r) for r in by_assignee[:10]],
    }


async def staff_truth(
    conn: asyncpg.Connection, explorer: asyncpg.Connection | None, tenant: str, now: datetime
) -> dict[str, Any]:
    rows = await conn.fetch(
        f"""
        SELECT m.id::text AS id, m.external_ref, m.display_name AS name, m.title, m.role_code,
               m.component, m.employment_status, m.employment_type, m.leave_until, m.ended_at,
               m.started_at, m.caseload_cap, m.timezone, m.active, m.student_facing,
               mgr.display_name AS manager_name, mgr.external_ref AS manager_ref,
               (SELECT COUNT(*) FROM staff_member r WHERE r.tenant_id=m.tenant_id AND r.manager_id=m.id)
                 AS direct_reports,
               (SELECT COUNT(*) FROM student_staff_assignment a WHERE a.tenant_id=m.tenant_id
                  AND a.staff_member_id=m.id AND a.role='primary_advisor' AND a.ended_at IS NULL)
                 AS primary_advisees,
               (SELECT COUNT(*) FROM student_staff_assignment a WHERE a.tenant_id=m.tenant_id
                  AND a.staff_member_id=m.id AND a.ended_at IS NULL) AS assignments,
               (SELECT COUNT(*) FROM student_staff_assignment a WHERE a.tenant_id=m.tenant_id
                  AND a.staff_member_id=m.id AND a.ended_at IS NOT NULL) AS ended_assignments,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN}) AS open_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN} AND w.due_at < $2::timestamptz) AS overdue_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN} AND w.priority='urgent') AS urgent_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status='in_progress'
                  AND w.updated_at < $2::timestamptz - interval '10 days') AS stale_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status='in_progress'
                  AND w.updated_at < $2::timestamptz - interval '7 days') AS in_progress_over_week,
               (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at < $2::timestamptz - interval '3 days') AS awaiting_outcome,
               (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at >= $2::timestamptz AND ap.starts_at < $2::timestamptz + interval '7 days') AS scheduled_next_7,
               (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at::date = ($2::timestamptz AT TIME ZONE 'UTC')::date) AS scheduled_today,
               (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at >= $2::timestamptz AND ap.starts_at < $2::timestamptz + interval '14 days') AS scheduled_next_14,
               (SELECT COUNT(*) FROM student_inquiry i WHERE i.tenant_id=m.tenant_id
                  AND i.assignee_id=m.id AND i.status IN ('new','open','waiting_on_student')
                  AND i.archived_at IS NULL) AS open_inquiries,
               (SELECT MIN(t.ends_at) FROM staff_time_off t WHERE t.tenant_id=m.tenant_id
                  AND t.staff_member_id=m.id AND t.starts_at <= $2::timestamptz AND t.ends_at > $2::timestamptz
                  AND t.blocks_bookings) AS current_absence_ends,
               (SELECT t.kind FROM staff_time_off t WHERE t.tenant_id=m.tenant_id
                  AND t.staff_member_id=m.id AND t.starts_at <= $2::timestamptz AND t.ends_at > $2::timestamptz
                  AND t.blocks_bookings ORDER BY t.ends_at DESC LIMIT 1) AS current_absence_kind,
               (SELECT COUNT(*) FROM staff_availability av WHERE av.tenant_id=m.tenant_id
                  AND av.staff_member_id=m.id) AS availability_rules,
               (SELECT string_agg(DISTINCT av.weekday::text, ',') FROM staff_availability av
                  WHERE av.tenant_id=m.tenant_id AND av.staff_member_id=m.id) AS weekdays
        FROM staff_member m LEFT JOIN staff_member mgr ON mgr.id=m.manager_id
        WHERE m.tenant_id=$1
        ORDER BY m.external_ref
        """,
        tenant,
        now,
    )
    caseload_detail = {}
    for ref in ("SYN-ADV-012", "SYN-ADV-003", "SYN-ADV-009", "SYN-ADV-008", "SYN-ADV-005"):
        member = next((r for r in rows if r["external_ref"] == ref), None)
        if member is None:
            continue
        detail = await conn.fetchrow(
            f"""
            SELECT
              COUNT(*) AS advisees,
              COUNT(*) FILTER (WHERE NOT EXISTS (
                 SELECT 1 FROM student_appointment ap WHERE ap.tenant_id=a.tenant_id
                   AND ap.student_id=a.student_id AND ap.type='academic_advising'
                   AND ap.status='completed')) AS advising_not_completed,
              COUNT(*) FILTER (WHERE NOT EXISTS (
                 SELECT 1 FROM student_appointment ap WHERE ap.tenant_id=a.tenant_id
                   AND ap.student_id=a.student_id AND ap.type='academic_advising'
                   AND ap.status='completed')
                 AND NOT EXISTS (
                 SELECT 1 FROM student_appointment ap WHERE ap.tenant_id=a.tenant_id
                   AND ap.student_id=a.student_id AND ap.type='academic_advising'
                   AND ap.status='scheduled' AND ap.starts_at >= $3::timestamptz)) AS advising_incomplete_no_booking,
              COUNT(*) FILTER (WHERE EXISTS (
                 SELECT 1 FROM staff_work_item w WHERE w.tenant_id=a.tenant_id
                   AND w.student_id=a.student_id AND w.{OPEN})) AS with_open_work,
              COUNT(*) FILTER (WHERE EXISTS (
                 SELECT 1 FROM staff_work_item w WHERE w.tenant_id=a.tenant_id
                   AND w.student_id=a.student_id AND w.{OPEN} AND w.due_at < $3::timestamptz)) AS with_overdue_work,
              COUNT(*) FILTER (WHERE EXISTS (
                 SELECT 1 FROM payment_transaction p WHERE p.tenant_id=a.tenant_id
                   AND p.student_id=a.student_id AND p.type='enrollment_deposit'
                   AND p.status='succeeded')) AS deposited,
              COUNT(*) FILTER (WHERE NOT EXISTS (
                 SELECT 1 FROM payment_transaction p WHERE p.tenant_id=a.tenant_id
                   AND p.student_id=a.student_id AND p.type='enrollment_deposit'
                   AND p.status='succeeded')) AS not_deposited
            FROM student_staff_assignment a
            WHERE a.tenant_id=$1 AND a.staff_member_id=$2::uuid AND a.role='primary_advisor'
              AND a.ended_at IS NULL
            """,
            tenant,
            member["id"],
            now,
        )
        sample = await conn.fetch(
            """
            SELECT person.first_name||' '||person.last_name AS name, s.external_ref
            FROM student_staff_assignment a JOIN student s ON s.id=a.student_id
            JOIN person ON person.id=s.person_id
            WHERE a.tenant_id=$1 AND a.staff_member_id=$2::uuid AND a.role='primary_advisor'
              AND a.ended_at IS NULL ORDER BY person.last_name, person.first_name LIMIT 5
            """,
            tenant,
            member["id"],
        )
        caseload_detail[ref] = {
            **{k: int(detail[k]) for k in detail.keys()},
            "sample": [dict(r) for r in sample],
        }

    slots: dict[str, Any] = {}
    if explorer is not None:
        slot_rows = await explorer.fetch(
            """
            SELECT s.external_ref,
              (SELECT MIN(sl.starts_at) FROM appointment_slots sl WHERE sl.staff_id=s.id
                 AND sl.status='open' AND sl.starts_at > $1) AS next_open,
              (SELECT COUNT(*) FROM appointment_slots sl WHERE sl.staff_id=s.id
                 AND sl.status='open' AND sl.starts_at > $1
                 AND sl.starts_at < $1 + interval '14 days') AS open_14d
            FROM staff s
            """,
            now,
        )
        slots = {
            r["external_ref"]: {"nextOpenSlotAt": _iso(r["next_open"]), "openSlots14d": int(r["open_14d"])}
            for r in slot_rows
        }

    staff: dict[str, Any] = {}
    for r in rows:
        ref = r["external_ref"]
        entry = {
            "id": r["id"],
            "name": r["name"],
            "firstName": r["name"].split()[0],
            "lastName": r["name"].split()[-1],
            "title": r["title"],
            "roleCode": r["role_code"],
            "component": r["component"],
            "employmentStatus": r["employment_status"],
            "employmentType": r["employment_type"],
            "leaveUntil": _date(r["leave_until"]),
            "endedAt": _date(r["ended_at"]),
            "startedAt": _date(r["started_at"]),
            "caseloadCap": r["caseload_cap"],
            "active": r["active"],
            "managerName": r["manager_name"],
            "managerRef": r["manager_ref"],
            "directReports": int(r["direct_reports"]),
            "primaryAdvisees": int(r["primary_advisees"]),
            "assignments": int(r["assignments"]),
            "endedAssignments": int(r["ended_assignments"]),
            "overCap": bool(r["caseload_cap"] and int(r["primary_advisees"]) > int(r["caseload_cap"])),
            "openItems": int(r["open_items"]),
            "overdueItems": int(r["overdue_items"]),
            "urgentItems": int(r["urgent_items"]),
            "staleItems": int(r["stale_items"]),
            "inProgressOverWeek": int(r["in_progress_over_week"]),
            "awaitingOutcome": int(r["awaiting_outcome"]),
            "scheduledNext7": int(r["scheduled_next_7"]),
            "scheduledToday": int(r["scheduled_today"]),
            "scheduledNext14": int(r["scheduled_next_14"]),
            "openInquiries": int(r["open_inquiries"]),
            "currentAbsenceEnds": _date(r["current_absence_ends"]),
            "currentAbsenceKind": r["current_absence_kind"],
            "availabilityRules": int(r["availability_rules"]),
            "weekdays": r["weekdays"],
            **slots.get(ref, {}),
        }
        if ref in caseload_detail:
            entry["caseload"] = caseload_detail[ref]
        staff[ref or r["id"]] = entry
    by_name = {entry["name"]: ref for ref, entry in staff.items()}
    advisers = [
        e for e in staff.values() if e["roleCode"] in ("academic_adviser", "transfer_adviser")
    ]
    active_advisers = [e for e in advisers if e["employmentStatus"] == "active"]
    over_cap = sorted((e["name"] for e in advisers if e["overCap"]))
    spare = sorted(
        e["name"]
        for e in active_advisers
        if e["caseloadCap"]
        and e["primaryAdvisees"] < 0.5 * e["caseloadCap"]
        and (e.get("openSlots14d") or 0) >= 40
    )
    no_slots = sorted(
        e["name"]
        for e in active_advisers
        if e["availabilityRules"] > 0 and (e.get("openSlots14d") or 0) == 0
    )
    lowest = min(
        (e for e in active_advisers if e["caseloadCap"]),
        key=lambda e: (e["primaryAdvisees"] / e["caseloadCap"], e["name"]),
    )
    unclosed = sorted(
        (e for e in staff.values() if e["awaitingOutcome"] > 0),
        key=lambda e: (-e["awaitingOutcome"], e["name"]),
    )
    absent = sorted(
        (e for e in staff.values() if e["currentAbsenceKind"] or e["employmentStatus"] == "on_leave"),
        key=lambda e: (-e["openItems"], e["name"]),
    )
    first_names: dict[str, int] = {}
    for e in staff.values():
        first_names[e["firstName"]] = first_names.get(e["firstName"], 0) + 1
    return {
        "byRef": staff,
        "refByName": by_name,
        "overCapAdvisers": over_cap,
        "spareCapacityAdvisers": spare,
        "noOpenSlotAdvisers": no_slots,
        "lowestLoadAdviser": {
            "name": lowest["name"],
            "primaryAdvisees": lowest["primaryAdvisees"],
            "caseloadCap": lowest["caseloadCap"],
        },
        "unclosedAppointments": [
            {"name": e["name"], "awaitingOutcome": e["awaitingOutcome"]} for e in unclosed[:6]
        ],
        "absentStaff": [
            {
                "name": e["name"],
                "kind": e["currentAbsenceKind"] or "leave",
                "until": e["currentAbsenceEnds"] or e["leaveUntil"],
                "openItems": e["openItems"],
            }
            for e in absent
        ],
        "staffFirstNameCounts": first_names,
    }


async def student_truth(conn: asyncpg.Connection, tenant: str, now: datetime) -> dict[str, Any]:
    out: dict[str, Any] = {}
    refs = list(STUDENT_REFS)
    for name in STUDENT_NAMES:
        found = await conn.fetch(
            "SELECT s.external_ref FROM student s JOIN person ON person.id=s.person_id "
            "WHERE s.tenant_id=$1 AND person.first_name||' '||person.last_name=$2",
            tenant,
            name,
        )
        if len(found) == 1:
            refs.append(found[0]["external_ref"])
    for ref in refs:
        row = await conn.fetchrow(
            f"""
            SELECT s.id::text AS id, s.external_ref, s.class_year,
                   person.first_name, person.last_name,
                   COALESCE(profile.preferred_name, person.preferred_name, person.first_name) AS preferred,
                   (SELECT program.name FROM admission_offer o JOIN program ON program.id=o.program_id
                      WHERE o.student_id=s.id ORDER BY o.created_at DESC LIMIT 1) AS program,
                   (SELECT o.status FROM admission_offer o WHERE o.student_id=s.id
                      ORDER BY o.created_at DESC LIMIT 1) AS offer_status,
                   EXISTS (SELECT 1 FROM payment_transaction p WHERE p.student_id=s.id
                      AND p.type='enrollment_deposit' AND p.status='succeeded') AS deposit_paid,
                   (SELECT COUNT(*) FROM staff_work_item w WHERE w.student_id=s.id AND w.{OPEN})
                     AS open_work_items,
                   (SELECT COUNT(*) FROM student_inquiry i WHERE i.student_id=s.id AND i.status='new'
                      AND i.archived_at IS NULL) AS new_inquiries,
                   (SELECT COUNT(*) FROM student_inquiry i WHERE i.student_id=s.id
                      AND i.status IN ('new','open','waiting_on_student') AND i.archived_at IS NULL)
                     AS open_inquiries,
                   adv.display_name AS adviser_name, adv.external_ref AS adviser_ref,
                   adv.employment_status AS adviser_status, adv.leave_until AS adviser_leave_until,
                   adv.title AS adviser_title,
                   (SELECT ap.starts_at FROM student_appointment ap WHERE ap.student_id=s.id
                      AND ap.status='scheduled' AND ap.starts_at >= $2::timestamptz ORDER BY ap.starts_at LIMIT 1)
                     AS next_appointment_at,
                   (SELECT m.display_name FROM student_appointment ap
                      LEFT JOIN staff_member m ON m.id=ap.staff_member_id WHERE ap.student_id=s.id
                      AND ap.status='scheduled' AND ap.starts_at >= $2::timestamptz ORDER BY ap.starts_at LIMIT 1)
                     AS next_appointment_with,
                   (SELECT ap.type FROM student_appointment ap WHERE ap.student_id=s.id
                      AND ap.status='scheduled' AND ap.starts_at >= $2::timestamptz ORDER BY ap.starts_at LIMIT 1)
                     AS next_appointment_type,
                   (SELECT COUNT(*) FROM student_appointment ap WHERE ap.student_id=s.id
                      AND ap.type='academic_advising' AND ap.status='completed') AS advising_completed,
                   (SELECT COUNT(*) FROM student_appointment ap WHERE ap.student_id=s.id
                      AND ap.status='no_show') AS no_shows
            FROM student s JOIN person ON person.id=s.person_id
            LEFT JOIN student_profile profile ON profile.student_id=s.id
            LEFT JOIN student_staff_assignment a ON a.student_id=s.id AND a.role='primary_advisor'
              AND a.ended_at IS NULL
            LEFT JOIN staff_member adv ON adv.id=a.staff_member_id
            WHERE s.tenant_id=$1 AND s.external_ref=$3
            """,
            tenant,
            now,
            ref,
        )
        if row is None:
            continue
        requirements = await conn.fetch(
            """
            SELECT d.code, d.title, r.status, d.blocking, r.due_at
            FROM student_requirement r
            JOIN enrollment_journey j ON j.id=r.journey_id
            JOIN requirement_definition_version d ON d.id=r.requirement_definition_version_id
            WHERE j.student_id=$1 AND r.retired_at IS NULL
            ORDER BY d.title
            """,
            row["id"],
        )
        open_reqs = [
            dict(r) for r in requirements if r["status"] not in ("completed", "waived", "not_applicable")
        ]
        counselors = await conn.fetch(
            """
            SELECT a.role, m.display_name AS name, m.external_ref
            FROM student_staff_assignment a JOIN staff_member m ON m.id=a.staff_member_id
            WHERE a.student_id=$1 AND a.ended_at IS NULL ORDER BY a.role
            """,
            row["id"],
        )
        work = await conn.fetch(
            f"""
            SELECT w.key, w.title, w.status, w.priority, w.due_at, m.display_name AS assignee
            FROM staff_work_item w LEFT JOIN staff_member m ON m.id=w.assignee_id
            WHERE w.student_id=$1 AND w.{OPEN} ORDER BY w.due_at NULLS LAST
            """,
            row["id"],
        )
        inquiries = await conn.fetch(
            "SELECT subject, status, priority, created_at FROM student_inquiry WHERE student_id=$1 "
            "AND archived_at IS NULL ORDER BY created_at DESC",
            row["id"],
        )
        out[ref] = {
            "inquiries": [
                {"subject": i["subject"], "status": i["status"], "priority": i["priority"]}
                for i in inquiries
            ],
            "id": row["id"],
            "name": f"{row['first_name']} {row['last_name']}",
            "firstName": row["first_name"],
            "lastName": row["last_name"],
            "preferredName": row["preferred"],
            "program": row["program"],
            "classYear": row["class_year"],
            "offerStatus": row["offer_status"],
            "depositPaid": bool(row["deposit_paid"]),
            "openWorkItems": int(row["open_work_items"]),
            "openWork": [
                {**dict(w), "due_at": _date(w["due_at"])} for w in work
            ],
            "newInquiries": int(row["new_inquiries"]),
            "openInquiries": int(row["open_inquiries"]),
            "adviserName": row["adviser_name"],
            "adviserRef": row["adviser_ref"],
            "adviserStatus": row["adviser_status"],
            "adviserLeaveUntil": _date(row["adviser_leave_until"]),
            "adviserTitle": row["adviser_title"],
            "nextAppointmentAt": _iso(row["next_appointment_at"]),
            "nextAppointmentDate": _date(row["next_appointment_at"]),
            "nextAppointmentWith": row["next_appointment_with"],
            "nextAppointmentType": row["next_appointment_type"],
            "advisingCompleted": int(row["advising_completed"]) > 0,
            "noShows": int(row["no_shows"]),
            "openRequirements": [
                {
                    "code": r["code"],
                    "title": r["title"],
                    "status": r["status"],
                    "blocking": r["blocking"],
                    "dueAt": _date(r["due_at"]),
                }
                for r in open_reqs
            ],
            "openBlocking": sum(1 for r in open_reqs if r["blocking"]),
            "requirementsTotal": len(requirements),
            "requirementsCompleted": len(requirements) - len(open_reqs),
            "counselors": [dict(c) for c in counselors],
        }
    return out


async def cohort_truth(conn: asyncpg.Connection, tenant: str, now: datetime) -> dict[str, Any]:
    deposit = (
        "EXISTS (SELECT 1 FROM payment_transaction p WHERE p.tenant_id=s.tenant_id "
        "AND p.student_id=s.id AND p.type='enrollment_deposit' AND p.status='succeeded')"
    )
    accepted = (
        "EXISTS (SELECT 1 FROM admission_offer o WHERE o.tenant_id=s.tenant_id "
        "AND o.student_id=s.id AND o.status='accepted')"
    )
    has_adviser = (
        "EXISTS (SELECT 1 FROM student_staff_assignment a WHERE a.tenant_id=s.tenant_id "
        "AND a.student_id=s.id AND a.role='primary_advisor' AND a.ended_at IS NULL)"
    )
    adviser_status = (
        "(SELECT m.employment_status FROM student_staff_assignment a JOIN staff_member m "
        "ON m.id=a.staff_member_id WHERE a.tenant_id=s.tenant_id AND a.student_id=s.id "
        "AND a.role='primary_advisor' AND a.ended_at IS NULL)"
    )
    open_work = (
        f"EXISTS (SELECT 1 FROM staff_work_item w WHERE w.tenant_id=s.tenant_id "
        f"AND w.student_id=s.id AND w.{OPEN})"
    )
    overdue_req = (
        "EXISTS (SELECT 1 FROM student_requirement r JOIN enrollment_journey j ON j.id=r.journey_id "
        "WHERE j.tenant_id=s.tenant_id AND j.student_id=s.id AND r.retired_at IS NULL "
        "AND r.status NOT IN ('completed','waived','not_applicable') AND r.due_at < $2::timestamptz)"
    )
    international = (
        "EXISTS (SELECT 1 FROM student_staff_assignment a WHERE a.tenant_id=s.tenant_id "
        "AND a.student_id=s.id AND a.role='international_adviser' AND a.ended_at IS NULL)"
    )
    row = await conn.fetchrow(
        f"""
        SELECT COUNT(*) AS students,
          COUNT(*) FILTER (WHERE {deposit}) AS deposited,
          COUNT(*) FILTER (WHERE NOT {deposit}) AS unpaid_deposit,
          COUNT(*) FILTER (WHERE {accepted}) AS accepted,
          COUNT(*) FILTER (WHERE NOT {has_adviser}) AS no_primary_adviser,
          COUNT(*) FILTER (WHERE {accepted} AND NOT {has_adviser}) AS accepted_no_primary_adviser,
          COUNT(*) FILTER (WHERE {deposit} AND NOT {has_adviser}) AS deposited_no_primary_adviser,
          COUNT(*) FILTER (WHERE {adviser_status}='departed') AS adviser_departed,
          COUNT(*) FILTER (WHERE {adviser_status}='on_leave') AS adviser_on_leave,
          COUNT(*) FILTER (WHERE {open_work}) AS with_open_work,
          COUNT(*) FILTER (WHERE {deposit} AND {overdue_req}) AS deposited_with_overdue_requirement,
          COUNT(*) FILTER (WHERE {overdue_req}) AS with_overdue_requirement,
          COUNT(*) FILTER (WHERE {international}) AS international_assigned,
          COUNT(*) FILTER (WHERE {international} AND {open_work}) AS international_with_open_work
        FROM student s WHERE s.tenant_id=$1
        """,
        tenant,
        now,
    )
    dup = await conn.fetch(
        """
        SELECT person.first_name||' '||person.last_name AS name, COUNT(*) AS n
        FROM student s JOIN person ON person.id=s.person_id WHERE s.tenant_id=$1
        GROUP BY 1 HAVING COUNT(*) > 1 ORDER BY 2 DESC, 1 LIMIT 12
        """,
        tenant,
    )
    first_names = {}
    for name in ("Tobias", "Ingrid", "Ines", "Georgina", "Marisol", "Wren", "Elena", "Vera", "Hana"):
        first_names[name] = int(
            await _val(
                conn,
                "SELECT COUNT(*) FROM student s JOIN person ON person.id=s.person_id "
                "WHERE s.tenant_id=$1 AND person.first_name=$2",
                tenant,
                name,
            )
        )
    staff_student_overlap = await conn.fetch(
        """
        SELECT m.display_name AS staff_name, m.external_ref,
          (SELECT COUNT(*) FROM student s JOIN person ON person.id=s.person_id
             WHERE s.tenant_id=m.tenant_id AND person.last_name=split_part(m.display_name,' ',2))
            AS students_sharing_surname,
          (SELECT COUNT(*) FROM student s JOIN person ON person.id=s.person_id
             WHERE s.tenant_id=m.tenant_id AND person.first_name||' '||person.last_name=m.display_name)
            AS students_sharing_full_name
        FROM staff_member m WHERE m.tenant_id=$1
        ORDER BY students_sharing_surname DESC LIMIT 10
        """,
        tenant,
    )
    return {
        **{k: int(row[k]) for k in row.keys()},
        "duplicateStudentNames": [dict(r) for r in dup],
        "studentsByFirstName": first_names,
        "staffStudentSurnameOverlap": [dict(r) for r in staff_student_overlap],
    }


async def department_truth(conn: asyncpg.Connection, tenant: str, now: datetime) -> dict[str, Any]:
    rows = await conn.fetch(
        f"""
        SELECT m.component,
          COUNT(*) AS staff,
          COUNT(*) FILTER (WHERE m.employment_status='active') AS active,
          COUNT(*) FILTER (WHERE m.employment_status='on_leave') AS on_leave,
          COUNT(*) FILTER (WHERE m.employment_status='departed') AS departed,
          string_agg(m.display_name, ', ' ORDER BY m.display_name)
            FILTER (WHERE m.employment_status='on_leave') AS on_leave_names,
          string_agg(m.display_name, ', ' ORDER BY m.display_name)
            FILTER (WHERE m.employment_status='departed') AS departed_names,
          (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
             AND w.component=m.component AND w.{OPEN}) AS open_items,
          (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
             AND w.component=m.component AND w.{OPEN} AND w.due_at < $2::timestamptz) AS overdue_items,
          (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
             AND w.component=m.component AND w.{OPEN} AND w.assignee_id IS NULL) AS unassigned_items,
          (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
             AND w.component=m.component AND w.{OPEN} AND w.priority='urgent') AS urgent_items,
          (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
             AND w.component=m.component AND w.{OPEN}
             AND (w.assignee_id IS NULL OR EXISTS (SELECT 1 FROM staff_member x
                  WHERE x.id=w.assignee_id AND x.employment_status <> 'active'))) AS uncovered_items,
          (SELECT SUM(c) FROM (SELECT COUNT(*) AS c FROM student_staff_assignment a
             JOIN staff_member x ON x.id=a.staff_member_id
             WHERE a.tenant_id=m.tenant_id AND x.component=m.component AND a.role='primary_advisor'
               AND a.ended_at IS NULL) t) AS primary_advisees
        FROM staff_member m WHERE m.tenant_id=$1 GROUP BY m.tenant_id, m.component
        """,
        tenant,
        now,
    )
    return {
        r["component"]: {
            k: (int(r[k]) if isinstance(r[k], int) else r[k]) for k in r.keys() if k != "component"
        }
        for r in rows
    }


async def main() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    explorer = None
    try:
        explorer = await asyncpg.connect(EXPLORER_URL)
    except Exception as error:  # noqa: BLE001
        print(f"explorer oracle unavailable: {error}", file=sys.stderr)
    now = await conn.fetchval("SELECT now()")
    try:
        truth = {
            "generatedAt": _iso(datetime.now(UTC)),
            "now": _iso(now),
            "today": _date(now),
            "tenantId": TENANT,
            "queue": await queue_truth(conn, TENANT, now),
            "inquiries": await inquiry_truth(conn, TENANT, now),
            "staff": await staff_truth(conn, explorer, TENANT, now),
            "students": await student_truth(conn, TENANT, now),
            "cohorts": await cohort_truth(conn, TENANT, now),
            "departments": await department_truth(conn, TENANT, now),
        }
        if explorer is not None:
            scenarios = await explorer.fetch("SELECT key, title, category, checks FROM test_scenarios")
            truth["scenarios"] = [
                {
                    "key": r["key"],
                    "title": r["title"],
                    "category": r["category"],
                    "checks": json.loads(r["checks"]) if isinstance(r["checks"], str) else r["checks"],
                }
                for r in scenarios
            ]
    finally:
        await conn.close()
        if explorer is not None:
            await explorer.close()
    json.dump(truth, sys.stdout, indent=2, default=str)


if __name__ == "__main__":
    asyncio.run(main())
