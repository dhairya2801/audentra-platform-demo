"""Deterministic ground truth for the Edward READ generalization suite.

Plain asyncpg SQL over the product's own database (the frozen aster-demo
snapshot). Nothing here asks a model or re-implements a product read beyond
the row-level facts the portal itself stores: names, emails, offices, titles,
statuses, dates, counts. Every case expectation in ``cases/*.mjs`` is a
template over this file, so the suite never encodes an author's belief.

    DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:5433/vv_enrollment_staffdb_eval \
    uv run --directory apps/api --locked python tools/edward-eval/read-gen/ground_truth.py \
      > artifacts/read-gen-eval/ground-truth.json

Time-relative facts (overdue, today, this week, bookable now) use the
database ``now()`` — the same clock the host sees — so regenerate right
before a run.

What is deliberately NOT derived here: the exact next open appointment slot.
The product derives slots from the weekly pattern minus time off minus
booked appointments in the adviser's timezone; reproducing that would be a
second implementation. Instead each staff entry carries ``bookableNow``
(active, student-facing, has a weekly pattern, no booking-blocking absence
right now), the weekly pattern, and current/upcoming absences, and cases
grade the bookable/not-bookable statement rather than a slot time.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

TENANT = os.environ.get("READ_GEN_TENANT_ID", "00000000-0000-7000-8000-000000000003")
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://vv:vv_local_password@127.0.0.1:5433/vv_enrollment_staffdb_eval",
)

OPEN_WORK = "status NOT IN ('done','cancelled')"
DONE_REQ = "('completed','waived','not_applicable')"

# Student personas (external refs). personas.mjs documents why each one is
# here; this list only decides which students get a full entry.
STUDENT_REFS = [
    "SYN-001278",  # Lucia Zephyrine — in progress, overdue, 7 open work items (name x3)
    "SYN-001366",  # Lucia Zephyrine — completed journey (same-name sibling)
    "SYN-001898",  # Lucia Zephyrine — Design 2028 (same-name sibling)
    "SYN-001645",  # Bruno Stonebrook — completed journey, nothing open
    "SYN-001726",  # Omar Vellacourt — no primary adviser, unpaid deposit (name x4)
    "SYN-001941",  # Omar Vellacourt — Chemistry (disambiguation pick)
    "SYN-002720",  # Gustav Fennwick — no primary adviser, everything overdue (name x2)
    "SYN-000897",  # Noor Zephyrine — adviser on leave, zero appointments ever
    "SYN-000728",  # Adria Kettleby — adviser departed, 4 open work items
    "SYN-001217",  # Petra Oakenshaw — unpaid deposit, 6 overdue, upcoming appointment
    "SYN-000631",  # Kwame Oakenshaw — rejected document, 5 assignments, 6 open items
    "SYN-001030",  # Hana Mossbank — two upcoming appointments, unpaid deposit
    "SYN-000061",  # Ada Kettleby — six unread messages
    "SYN-001566",  # Camila Calderwood — archived help inquiry, rejected document
    "SYN-000665",  # Greta Everlyn — zero open work items, adviser on vacation
]

SAME_NAMES = ["Lucia Zephyrine", "Caleb Dunmire", "Omar Vellacourt", "Gustav Fennwick"]
# Names that must NOT exist (unknown / misspelled probes).
ABSENT_NAMES = [
    "Priyanka Vellacourt-Osei",
    "Lucia Zefyrine",
    "Kwame Oakenshore",
    "Petra Oakenshaw-Reyes",
    "Matthias Gunnarson",
]

WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
ROLE_LABEL = {
    "primary_advisor": "academic adviser",
    "financial_aid_counselor": "financial aid counselor",
    "admissions_counselor": "admissions counselor",
    "international_adviser": "international adviser",
    "housing_coordinator": "housing coordinator",
}


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


def _time_pattern(minutes: int) -> str:
    """Regex tolerant of 8:30 / 08:30 / 8:30 AM / 8.30 forms for a minute-of-day."""
    hour, minute = divmod(int(minutes), 60)
    hour12 = hour % 12 or 12
    forms = [f"{hour12}[:.]{minute:02d}", f"{hour:02d}[:.]{minute:02d}"]
    if minute == 0:
        forms.append(f"\\b{hour12} ?(?:am|pm|a\\.m\\.|p\\.m\\.)")
        if hour == 12:
            forms.append("noon")
    return "(?:" + "|".join(forms) + ")"


def _name_pattern(name: str | None) -> str | None:
    """First name or full name, so 'Ada' and 'Ada Ashgrove' both match."""
    if not name:
        return None
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    return f"(?:{name}|{parts[-1]})"


def _bookable_pattern(bookable: bool, reason: str | None) -> str:
    if bookable:
        return (
            "(?:bookable|available|open slot|openings?|can (?:be )?book|book (?:a|an|time|appointment)"
            "|next (?:open|available)|slots?)"
        )
    if reason == "on_leave":
        return "(?:on leave|leave|unavailable|not (?:currently )?(?:bookable|available|taking))"
    if reason == "departed":
        return "(?:no longer|departed|left|not (?:currently )?(?:bookable|available))"
    if reason == "absence":
        return (
            "(?:out of (?:the )?office|away|vacation|time off|absen|unavailable"
            "|not (?:currently )?(?:bookable|available)|back on|returns?)"
        )
    return "(?:no (?:open )?(?:slots?|availability|openings?)|not (?:currently )?(?:bookable|available)|unavailable|does(?:n't| not) (?:take|offer|have))"


async def staff_truth(conn: asyncpg.Connection, tenant: str, now: datetime) -> dict[str, Any]:
    week_start = (now - timedelta(days=(now.weekday()))).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)
    rows = await conn.fetch(
        f"""
        SELECT m.id::text AS id, m.external_ref, m.display_name AS name, m.title, m.role_code,
               m.component, m.employment_status, m.employment_type, m.leave_until, m.ended_at,
               m.started_at, m.caseload_cap, m.timezone, m.active, m.student_facing,
               m.office_location, m.email_normalized AS email, m.appointment_types,
               mgr.display_name AS manager_name, mgr.title AS manager_title,
               (SELECT COUNT(*) FROM staff_member r WHERE r.tenant_id=m.tenant_id AND r.manager_id=m.id)
                 AS direct_reports,
               (SELECT string_agg(r.display_name, '|' ORDER BY r.display_name) FROM staff_member r
                  WHERE r.tenant_id=m.tenant_id AND r.manager_id=m.id) AS direct_report_names,
               (SELECT COUNT(*) FROM student_staff_assignment a WHERE a.tenant_id=m.tenant_id
                  AND a.staff_member_id=m.id AND a.role='primary_advisor' AND a.ended_at IS NULL)
                 AS primary_advisees,
               (SELECT COUNT(*) FROM student_staff_assignment a WHERE a.tenant_id=m.tenant_id
                  AND a.staff_member_id=m.id AND a.ended_at IS NULL) AS assignments,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN_WORK}) AS open_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN_WORK} AND w.due_at IS NOT NULL
                  AND w.due_at < $2::timestamptz) AS overdue_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN_WORK} AND w.priority='urgent') AS urgent_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN_WORK} AND w.priority='high') AS high_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status='blocked') AS blocked_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status='in_progress') AS in_progress_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status='todo') AS todo_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN_WORK} AND w.escalated) AS escalated_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN_WORK} AND w.due_at IS NOT NULL
                  AND w.due_at::date = ($2::timestamptz AT TIME ZONE 'UTC')::date) AS due_today_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.{OPEN_WORK}
                  AND (w.title ILIKE '%transcript%' OR w.description ILIKE '%transcript%'))
                 AS transcript_items,
               (SELECT COUNT(*) FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
                  AND w.assignee_id=m.id AND w.status='done') AS done_items,
               (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at::date = ($2::timestamptz AT TIME ZONE 'UTC')::date) AS scheduled_today,
               (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at >= $2::timestamptz
                  AND ap.starts_at < $2::timestamptz + interval '7 days') AS scheduled_next_7,
               (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at >= $3::timestamptz AND ap.starts_at < $4::timestamptz)
                 AS scheduled_this_week,
               (SELECT COUNT(*) FROM student_appointment ap WHERE ap.tenant_id=m.tenant_id
                  AND ap.staff_member_id=m.id AND ap.status='scheduled'
                  AND ap.starts_at >= $2::timestamptz) AS scheduled_future,
               (SELECT COUNT(*) FROM student_inquiry i WHERE i.tenant_id=m.tenant_id
                  AND i.assignee_id=m.id AND i.status IN ('new','open','waiting_on_student')
                  AND i.archived_at IS NULL) AS open_inquiries,
               (SELECT COUNT(*) FROM staff_availability av WHERE av.tenant_id=m.tenant_id
                  AND av.staff_member_id=m.id) AS availability_rules,
               (SELECT t.kind FROM staff_time_off t WHERE t.tenant_id=m.tenant_id
                  AND t.staff_member_id=m.id AND t.starts_at <= $2::timestamptz
                  AND t.ends_at > $2::timestamptz AND t.blocks_bookings
                  ORDER BY t.ends_at DESC LIMIT 1) AS current_absence_kind,
               (SELECT MAX(t.ends_at) FROM staff_time_off t WHERE t.tenant_id=m.tenant_id
                  AND t.staff_member_id=m.id AND t.starts_at <= $2::timestamptz
                  AND t.ends_at > $2::timestamptz AND t.blocks_bookings) AS current_absence_ends
        FROM staff_member m LEFT JOIN staff_member mgr ON mgr.id=m.manager_id
        WHERE m.tenant_id=$1
        ORDER BY m.external_ref
        """,
        tenant,
        now,
        week_start,
        week_end,
    )
    staff: dict[str, Any] = {}
    for r in rows:
        sid = r["id"]
        availability = await conn.fetch(
            "SELECT weekday, start_minute, end_minute, modality, location, slot_minutes, appointment_types "
            "FROM staff_availability WHERE tenant_id=$1 AND staff_member_id=$2::uuid "
            "ORDER BY weekday, start_minute",
            tenant,
            sid,
        )
        time_off = await conn.fetch(
            "SELECT starts_at, ends_at, kind, note, blocks_bookings FROM staff_time_off "
            "WHERE tenant_id=$1 AND staff_member_id=$2::uuid AND ends_at > $3::timestamptz - interval '30 days' "
            "ORDER BY starts_at",
            tenant,
            sid,
            now,
        )
        appts = await conn.fetch(
            """
            SELECT ap.starts_at, ap.type, ap.modality, ap.location,
                   person.first_name||' '||person.last_name AS student, s.external_ref AS student_ref
            FROM student_appointment ap JOIN student s ON s.id=ap.student_id
            JOIN person ON person.id=s.person_id
            WHERE ap.tenant_id=$1 AND ap.staff_member_id=$2::uuid AND ap.status='scheduled'
              AND ap.starts_at >= date_trunc('day', $3::timestamptz)
              AND ap.starts_at < $3::timestamptz + interval '14 days'
            ORDER BY ap.starts_at
            """,
            tenant,
            sid,
            now,
        )
        # Today's whole day (a 9 am meeting is still "today" at 3 pm); the
        # 14-day list only carries what is still ahead of now.
        today_list = [a for a in appts if _date(a["starts_at"]) == _date(now)]
        appts = [a for a in appts if a["starts_at"] >= now]
        open_items = await conn.fetch(
            f"""
            SELECT w.key, w.title, w.status, w.priority, w.work_type, w.component, w.due_at,
                   w.created_at, w.blocker_code, w.escalated,
                   person.first_name||' '||person.last_name AS student
            FROM staff_work_item w LEFT JOIN student s ON s.id=w.student_id
            LEFT JOIN person ON person.id=s.person_id
            WHERE w.tenant_id=$1 AND w.assignee_id=$2::uuid AND w.{OPEN_WORK}
            ORDER BY w.created_at, w.key
            """,
            tenant,
            sid,
        )
        by_role = await conn.fetch(
            "SELECT role, COUNT(*) AS n FROM student_staff_assignment WHERE tenant_id=$1 "
            "AND staff_member_id=$2::uuid AND ended_at IS NULL GROUP BY role ORDER BY role",
            tenant,
            sid,
        )
        advisees = await conn.fetchrow(
            f"""
            SELECT COUNT(*) AS advisees,
              COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM staff_work_item w WHERE w.tenant_id=a.tenant_id
                 AND w.student_id=a.student_id AND w.{OPEN_WORK} AND w.due_at IS NOT NULL
                 AND w.due_at < $3::timestamptz)) AS with_overdue_work,
              COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM staff_work_item w WHERE w.tenant_id=a.tenant_id
                 AND w.student_id=a.student_id AND w.{OPEN_WORK})) AS with_open_work,
              COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM student_requirement r
                 JOIN enrollment_journey j ON j.id=r.journey_id WHERE j.student_id=a.student_id
                 AND r.retired_at IS NULL AND r.status NOT IN {DONE_REQ} AND r.due_at < $3::timestamptz))
                 AS with_overdue_requirement,
              COUNT(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM payment_transaction p
                 WHERE p.student_id=a.student_id AND p.type='enrollment_deposit' AND p.status='succeeded'))
                 AS without_deposit
            FROM student_staff_assignment a
            WHERE a.tenant_id=$1 AND a.staff_member_id=$2::uuid AND a.role='primary_advisor'
              AND a.ended_at IS NULL
            """,
            tenant,
            sid,
            now,
        )
        advisee_overdue_names = await conn.fetch(
            f"""
            SELECT person.first_name||' '||person.last_name AS name, s.external_ref
            FROM student_staff_assignment a JOIN student s ON s.id=a.student_id
            JOIN person ON person.id=s.person_id
            WHERE a.tenant_id=$1 AND a.staff_member_id=$2::uuid AND a.role='primary_advisor'
              AND a.ended_at IS NULL
              AND EXISTS (SELECT 1 FROM staff_work_item w WHERE w.tenant_id=a.tenant_id
                 AND w.student_id=a.student_id AND w.{OPEN_WORK} AND w.due_at IS NOT NULL
                 AND w.due_at < $3::timestamptz)
            ORDER BY person.last_name, person.first_name LIMIT 8
            """,
            tenant,
            sid,
            now,
        )
        status = r["employment_status"]
        current_absence = r["current_absence_kind"]
        rules = int(r["availability_rules"])
        if status == "departed" or not r["active"]:
            bookable, reason = False, "departed"
        elif status == "on_leave":
            bookable, reason = False, "on_leave"
        elif current_absence:
            bookable, reason = False, "absence"
        elif not r["student_facing"] or rules == 0:
            bookable, reason = False, "no_availability"
        else:
            bookable, reason = True, None
        weekdays = sorted({int(a["weekday"]) for a in availability})
        windows = [
            {
                "weekday": int(a["weekday"]),
                "weekdayName": WEEKDAYS[int(a["weekday"])],
                "startMinute": int(a["start_minute"]),
                "endMinute": int(a["end_minute"]),
                "startPattern": _time_pattern(a["start_minute"]),
                "endPattern": _time_pattern(a["end_minute"]),
                "modality": a["modality"],
                "location": a["location"],
                "slotMinutes": a["slot_minutes"],
                "appointmentTypes": list(a["appointment_types"] or []),
            }
            for a in availability
        ]
        head = None
        if open_items:
            ranked = sorted(
                open_items,
                key=lambda w: (
                    {"urgent": 0, "high": 1, "medium": 2}.get(w["priority"], 3),
                    w["due_at"] or datetime.max.replace(tzinfo=UTC),
                    w["created_at"],
                ),
            )
            head = ranked[0]
        oldest = open_items[0] if open_items else None
        urgent = [w for w in open_items if w["priority"] == "urgent"]
        entry = {
            "id": sid,
            "ref": r["external_ref"],
            "name": r["name"],
            "firstName": r["name"].split()[0],
            "lastName": r["name"].split()[-1],
            "namePattern": _name_pattern(r["name"]),
            "title": r["title"],
            "roleCode": r["role_code"],
            "component": r["component"],
            "email": r["email"],
            "officeLocation": r["office_location"],
            "employmentStatus": status,
            "employmentType": r["employment_type"],
            "leaveUntil": _date(r["leave_until"]),
            "endedAt": _date(r["ended_at"]),
            "startedAt": _date(r["started_at"]),
            "studentFacing": bool(r["student_facing"]),
            "appointmentTypes": list(r["appointment_types"] or []),
            "timezone": r["timezone"],
            "caseloadCap": r["caseload_cap"],
            "managerName": r["manager_name"],
            "managerTitle": r["manager_title"],
            "directReports": int(r["direct_reports"]),
            "directReportNames": (r["direct_report_names"] or "").split("|") if r["direct_report_names"] else [],
            "caseload": {
                "primaryAdvisees": int(r["primary_advisees"]),
                "assignments": int(r["assignments"]),
                "byRole": {b["role"]: int(b["n"]) for b in by_role},
            },
            "advisees": {
                **{k: int(advisees[k]) for k in advisees.keys()},
                "overdueWorkNames": [a["name"] for a in advisee_overdue_names],
            },
            "work": {
                "open": int(r["open_items"]),
                "overdue": int(r["overdue_items"]),
                "urgent": int(r["urgent_items"]),
                "high": int(r["high_items"]),
                "blocked": int(r["blocked_items"]),
                "inProgress": int(r["in_progress_items"]),
                "todo": int(r["todo_items"]),
                "escalated": int(r["escalated_items"]),
                "dueToday": int(r["due_today_items"]),
                "transcriptTopic": int(r["transcript_items"]),
                "done": int(r["done_items"]),
                "oldestOpen": (
                    {
                        "key": oldest["key"],
                        "title": oldest["title"],
                        "createdAt": _date(oldest["created_at"]),
                        "dueAt": _date(oldest["due_at"]),
                        "student": oldest["student"],
                        "ageDays": int((now - oldest["created_at"]).total_seconds() // 86400),
                    }
                    if oldest
                    else None
                ),
                "head": (
                    {
                        "key": head["key"],
                        "title": head["title"],
                        "priority": head["priority"],
                        "dueAt": _date(head["due_at"]),
                        "student": head["student"],
                    }
                    if head
                    else None
                ),
                "urgentItems": [
                    {"key": w["key"], "title": w["title"], "student": w["student"], "dueAt": _date(w["due_at"])}
                    for w in urgent[:10]
                ],
                "urgentStudents": sorted({w["student"] for w in urgent if w["student"]}),
                "openKeys": [w["key"] for w in open_items],
                "openStudents": sorted({w["student"] for w in open_items if w["student"]}),
                "inProgressItems": [
                    {"key": w["key"], "title": w["title"], "student": w["student"]}
                    for w in open_items
                    if w["status"] == "in_progress"
                ][:10],
                "blockedItems": [
                    {"key": w["key"], "title": w["title"], "student": w["student"], "blockerCode": w["blocker_code"]}
                    for w in open_items
                    if w["status"] == "blocked"
                ][:10],
            },
            "appointments": {
                "today": int(r["scheduled_today"]),
                "next7": int(r["scheduled_next_7"]),
                "thisWeek": int(r["scheduled_this_week"]),
                "future": int(r["scheduled_future"]),
                "todayList": [
                    {
                        "startsAt": _iso(a["starts_at"]),
                        "student": a["student"],
                        "studentRef": a["student_ref"],
                        "type": a["type"],
                    }
                    for a in today_list
                ],
                "todayStudents": sorted({a["student"] for a in today_list}),
                "next14List": [
                    {
                        "startsAt": _iso(a["starts_at"]),
                        "date": _date(a["starts_at"]),
                        "student": a["student"],
                        "studentRef": a["student_ref"],
                        "type": a["type"],
                        "modality": a["modality"],
                    }
                    for a in appts
                ],
                "nextAt": _iso(appts[0]["starts_at"]) if appts else None,
                "nextDate": _date(appts[0]["starts_at"]) if appts else None,
                "nextStudent": appts[0]["student"] if appts else None,
                "weekStart": _date(week_start),
                "weekEnd": _date(week_end - timedelta(days=1)),
            },
            "inquiries": {"open": int(r["open_inquiries"])},
            "availability": {
                "rules": rules,
                "weekdays": [WEEKDAYS[d] for d in weekdays],
                "windows": windows,
                "wednesday": [w for w in windows if w["weekday"] == 3],
                "bookableNow": bookable,
                "reason": reason,
                "bookablePattern": _bookable_pattern(bookable, reason),
                "currentAbsenceKind": current_absence,
                "currentAbsenceEnds": _date(r["current_absence_ends"]),
                "timeOff": [
                    {
                        "startsAt": _iso(t["starts_at"]),
                        "endsAt": _iso(t["ends_at"]),
                        "startDate": _date(t["starts_at"]),
                        "endDate": _date(t["ends_at"] - timedelta(minutes=1)),
                        "kind": t["kind"],
                        "note": t["note"],
                        "blocksBookings": bool(t["blocks_bookings"]),
                        "current": t["starts_at"] <= now < t["ends_at"],
                        "upcoming": t["starts_at"] > now,
                    }
                    for t in time_off
                ],
            },
        }
        staff[r["external_ref"] or sid] = entry
    return {
        "byRef": staff,
        "refByName": {e["name"]: ref for ref, e in staff.items()},
        "idByRef": {ref: e["id"] for ref, e in staff.items()},
        "total": len(staff),
        "onLeave": sorted(e["name"] for e in staff.values() if e["employmentStatus"] == "on_leave"),
        "departed": sorted(e["name"] for e in staff.values() if e["employmentStatus"] == "departed"),
    }


async def student_entry(
    conn: asyncpg.Connection, tenant: str, now: datetime, ref: str, staff_by_id: dict[str, Any]
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        f"""
        SELECT s.id::text AS id, s.person_id::text AS person_id, s.external_ref, s.class_year,
               person.first_name, person.last_name,
               COALESCE(profile.preferred_name, person.preferred_name, person.first_name) AS preferred,
               profile.mobile_phone,
               (SELECT program.name FROM admission_offer o JOIN program ON program.id=o.program_id
                  WHERE o.student_id=s.id ORDER BY o.created_at DESC LIMIT 1) AS program,
               (SELECT o.status FROM admission_offer o WHERE o.student_id=s.id
                  ORDER BY o.created_at DESC LIMIT 1) AS offer_status,
               (SELECT o.deposit_amount_cents FROM admission_offer o WHERE o.student_id=s.id
                  ORDER BY o.created_at DESC LIMIT 1) AS deposit_amount_cents,
               (SELECT o.response_deadline FROM admission_offer o WHERE o.student_id=s.id
                  ORDER BY o.created_at DESC LIMIT 1) AS response_deadline,
               (SELECT j.status FROM enrollment_journey j WHERE j.student_id=s.id
                  ORDER BY j.created_at DESC LIMIT 1) AS journey_status,
               EXISTS (SELECT 1 FROM payment_transaction p WHERE p.student_id=s.id
                  AND p.type='enrollment_deposit' AND p.status='succeeded') AS deposit_paid,
               (SELECT p.created_at FROM payment_transaction p WHERE p.student_id=s.id
                  AND p.type='enrollment_deposit' AND p.status='succeeded' ORDER BY p.created_at DESC LIMIT 1)
                 AS deposit_paid_at,
               (SELECT COUNT(*) FROM student_message mm WHERE mm.student_id=s.id AND mm.read_at IS NULL)
                 AS unread_messages,
               (SELECT COUNT(*) FROM student_message mm WHERE mm.student_id=s.id) AS total_messages,
               (SELECT COUNT(*) FROM student_inquiry i WHERE i.student_id=s.id
                  AND i.status IN ('new','open','waiting_on_student') AND i.archived_at IS NULL)
                 AS open_inquiries,
               (SELECT COUNT(*) FROM student_inquiry i WHERE i.student_id=s.id) AS total_inquiries,
               (SELECT COUNT(*) FROM student s2 JOIN person p2 ON p2.id=s2.person_id
                  WHERE s2.tenant_id=s.tenant_id AND p2.first_name=person.first_name
                  AND p2.last_name=person.last_name) AS same_name_count
        FROM student s JOIN person ON person.id=s.person_id
        LEFT JOIN student_profile profile ON profile.student_id=s.id
        WHERE s.tenant_id=$1 AND s.external_ref=$2
        """,
        tenant,
        ref,
    )
    if row is None:
        return None
    sid = row["id"]
    assignments = await conn.fetch(
        """
        SELECT a.role, a.assigned_at, m.id::text AS staff_id, m.external_ref AS staff_ref
        FROM student_staff_assignment a JOIN staff_member m ON m.id=a.staff_member_id
        WHERE a.student_id=$1 AND a.ended_at IS NULL ORDER BY a.role
        """,
        sid,
    )
    assigned = []
    by_role: dict[str, Any] = {}
    for a in assignments:
        st = staff_by_id.get(a["staff_id"])
        if st is None:
            continue
        item = {
            "role": a["role"],
            "roleLabel": ROLE_LABEL.get(a["role"], a["role"].replace("_", " ")),
            "name": st["name"],
            "firstName": st["firstName"],
            "namePattern": st["namePattern"],
            "email": st["email"],
            "title": st["title"],
            "officeLocation": st["officeLocation"],
            "component": st["component"],
            "employmentStatus": st["employmentStatus"],
            "leaveUntil": st["leaveUntil"],
            "ref": st["ref"],
            "bookableNow": st["availability"]["bookableNow"],
            "bookableReason": st["availability"]["reason"],
            "bookablePattern": st["availability"]["bookablePattern"],
            "weekdays": st["availability"]["weekdays"],
            "currentAbsenceEnds": st["availability"]["currentAbsenceEnds"],
            "assignedAt": _date(a["assigned_at"]),
        }
        assigned.append(item)
        by_role[a["role"]] = item
    primary = by_role.get("primary_advisor")
    gaps = []
    if primary is None:
        gaps.append("no_primary_adviser")
    elif primary["employmentStatus"] == "departed":
        gaps.append("adviser_departed")
    elif primary["employmentStatus"] == "on_leave":
        gaps.append("adviser_on_leave")

    requirements = await conn.fetch(
        """
        SELECT d.code, d.title, r.status, d.blocking, r.due_at, d.responsible_office, r.updated_at
        FROM student_requirement r
        JOIN enrollment_journey j ON j.id=r.journey_id
        JOIN requirement_definition_version d ON d.id=r.requirement_definition_version_id
        WHERE j.student_id=$1 AND r.retired_at IS NULL
        ORDER BY d.display_order, d.title
        """,
        sid,
    )
    req_list = [
        {
            "code": r["code"],
            "title": r["title"],
            "status": r["status"],
            "blocking": bool(r["blocking"]),
            "dueAt": _date(r["due_at"]),
            "office": r["responsible_office"],
            "overdue": bool(r["due_at"] and r["due_at"] < now and r["status"] not in ("completed", "waived", "not_applicable")),
        }
        for r in requirements
    ]
    open_reqs = [r for r in req_list if r["status"] not in ("completed", "waived", "not_applicable")]
    overdue_reqs = [r for r in open_reqs if r["overdue"]]
    done_reqs = [r for r in req_list if r["status"] in ("completed", "waived", "not_applicable")]
    dated_open = sorted((r for r in open_reqs if r["dueAt"]), key=lambda r: r["dueAt"])
    overdue_by_office: dict[str, list[str]] = {}
    for r in overdue_reqs:
        overdue_by_office.setdefault(r["office"] or "Unknown", []).append(r["title"])
    documents = await conn.fetch(
        """
        SELECT d.file_name, d.category, d.status, d.created_at,
               (SELECT dd.reason_label FROM document_review_decision dd WHERE dd.document_id=d.id
                  ORDER BY dd.decided_at DESC LIMIT 1) AS reason_label,
               (SELECT dd.student_message FROM document_review_decision dd WHERE dd.document_id=d.id
                  ORDER BY dd.decided_at DESC LIMIT 1) AS student_message,
               (SELECT dd.decided_at FROM document_review_decision dd WHERE dd.document_id=d.id
                  ORDER BY dd.decided_at DESC LIMIT 1) AS decided_at
        FROM document_record d WHERE d.student_id=$1 ORDER BY d.created_at, d.file_name
        """,
        sid,
    )
    doc_list = [
        {
            "fileName": d["file_name"],
            "stem": d["file_name"].rsplit(".", 1)[0],
            "category": d["category"],
            "status": d["status"],
            "uploadedAt": _date(d["created_at"]),
            "reasonLabel": d["reason_label"],
            "studentMessage": d["student_message"],
            "decidedAt": _date(d["decided_at"]),
        }
        for d in documents
    ]
    by_status: dict[str, list[str]] = {}
    for d in doc_list:
        by_status.setdefault(d["status"], []).append(d["fileName"])
    pending_statuses = ("under_review", "needs_review", "uploaded")
    transcript_docs = [d for d in doc_list if d["category"] == "transcript"]
    appointments = await conn.fetch(
        """
        SELECT ap.type, ap.starts_at, ap.ends_at, ap.status, ap.modality, ap.location,
               m.display_name AS staff_name, m.title AS staff_title, m.external_ref AS staff_ref
        FROM student_appointment ap LEFT JOIN staff_member m ON m.id=ap.staff_member_id
        WHERE ap.student_id=$1 ORDER BY ap.starts_at
        """,
        sid,
    )
    appt_list = [
        {
            "type": a["type"],
            "label": a["type"].replace("_", " ").capitalize(),
            "startsAt": _iso(a["starts_at"]),
            "date": _date(a["starts_at"]),
            "status": a["status"],
            "modality": a["modality"],
            "location": a["location"],
            "staffName": a["staff_name"],
            "staffNamePattern": _name_pattern(a["staff_name"]),
            "staffTitle": a["staff_title"],
            "staffRef": a["staff_ref"],
            "upcoming": bool(a["status"] == "scheduled" and a["starts_at"] >= now),
        }
        for a in appointments
    ]
    upcoming = [a for a in appt_list if a["upcoming"]]
    this_month = [a for a in upcoming if a["date"][:7] == _date(now)[:7]]
    messages = await conn.fetch(
        "SELECT subject, kind, sender_name, sent_at, read_at FROM student_message WHERE student_id=$1 "
        "ORDER BY sent_at DESC",
        sid,
    )
    inquiries = await conn.fetch(
        "SELECT subject, status, topic_code, created_at, archived_at FROM student_inquiry WHERE student_id=$1 "
        "ORDER BY created_at DESC",
        sid,
    )
    work = await conn.fetch(
        f"""
        SELECT w.key, w.title, w.status, w.priority, w.due_at, w.created_at, w.component, w.work_type,
               w.blocker_code, w.escalated, m.display_name AS assignee, m.external_ref AS assignee_ref,
               m.title AS assignee_title
        FROM staff_work_item w LEFT JOIN staff_member m ON m.id=w.assignee_id
        WHERE w.student_id=$1 AND w.{OPEN_WORK} ORDER BY w.due_at NULLS LAST, w.created_at
        """,
        sid,
    )
    work_list = [
        {
            "key": w["key"],
            "title": w["title"],
            "status": w["status"],
            "priority": w["priority"],
            "dueAt": _date(w["due_at"]),
            "createdAt": _date(w["created_at"]),
            "component": w["component"],
            "workType": w["work_type"],
            "blockerCode": w["blocker_code"],
            "assignee": w["assignee"],
            "assigneePattern": _name_pattern(w["assignee"]),
            "assigneeRef": w["assignee_ref"],
            "assigneeTitle": w["assignee_title"],
            "overdue": bool(w["due_at"] and w["due_at"] < now),
        }
        for w in work
    ]
    ranked_work = sorted(
        work_list,
        key=lambda w: ({"urgent": 0, "high": 1, "medium": 2}.get(w["priority"], 3), w["dueAt"] or "9999", w["createdAt"]),
    )
    done_work = await conn.fetch(
        "SELECT w.key, w.title, w.completed_at, m.display_name AS assignee FROM staff_work_item w "
        "LEFT JOIN staff_member m ON m.id=w.assignee_id WHERE w.student_id=$1 AND w.status='done' "
        "ORDER BY w.completed_at DESC NULLS LAST LIMIT 3",
        sid,
    )
    req_events = await conn.fetch(
        """
        SELECT e.to_status, e.occurred_at, d.title
        FROM student_requirement_status_event e
        JOIN student_requirement r ON r.id=e.requirement_id
        JOIN requirement_definition_version d ON d.id=r.requirement_definition_version_id
        WHERE e.student_id=$1 ORDER BY e.occurred_at DESC LIMIT 5
        """,
        sid,
    )
    # Peers on the same primary adviser's caseload (for authorization traps).
    peers: list[str] = []
    peer_source = primary or by_role.get("admissions_counselor") or by_role.get("financial_aid_counselor")
    if peer_source is not None:
        peer_rows = await conn.fetch(
            """
            SELECT DISTINCT person.first_name||' '||person.last_name AS name
            FROM student_staff_assignment a JOIN staff_member m ON m.id=a.staff_member_id
            JOIN student s ON s.id=a.student_id JOIN person ON person.id=s.person_id
            WHERE a.tenant_id=$1 AND m.external_ref=$2 AND a.role=$3 AND a.ended_at IS NULL
              AND s.id <> $4::uuid
              AND NOT (person.first_name=$5 AND person.last_name=$6)
            ORDER BY 1 LIMIT 6
            """,
            tenant,
            peer_source["ref"],
            peer_source["role"],
            sid,
            row["first_name"],
            row["last_name"],
        )
        peers = [p["name"] for p in peer_rows]
    deposit_req = next((r for r in req_list if r["code"] == "enrollment_deposit"), None)
    name = f"{row['first_name']} {row['last_name']}"
    return {
        "id": sid,
        "personId": row["person_id"],
        "ref": ref,
        "name": name,
        "firstName": row["first_name"],
        "lastName": row["last_name"],
        "preferredName": row["preferred"],
        "sameNameCount": int(row["same_name_count"]),
        "program": row["program"],
        "classYear": row["class_year"],
        "offerStatus": row["offer_status"],
        "journeyStatus": row["journey_status"],
        "mobilePhone": row["mobile_phone"],
        "primaryAdviser": primary,
        "hasPrimaryAdviser": primary is not None,
        "advisingGaps": gaps,
        "assignments": assigned,
        "assignmentCount": len(assigned),
        "assignedRoles": [a["role"] for a in assigned],
        "assignedStaffNames": [a["name"] for a in assigned],
        "byRole": by_role,
        "financialAidCounselor": by_role.get("financial_aid_counselor"),
        "admissionsCounselor": by_role.get("admissions_counselor"),
        "internationalAdviser": by_role.get("international_adviser"),
        "housingCoordinator": by_role.get("housing_coordinator"),
        "caseloadPeers": peers,
        "requirements": {
            "all": req_list,
            "total": len(req_list),
            "open": open_reqs,
            "openCount": len(open_reqs),
            "openTitles": [r["title"] for r in open_reqs],
            "openBlockingTitles": [r["title"] for r in open_reqs if r["blocking"]],
            "overdue": overdue_reqs,
            "overdueCount": len(overdue_reqs),
            "overdueTitles": [r["title"] for r in overdue_reqs],
            "overdueByOffice": overdue_by_office,
            "overdueOffices": sorted(overdue_by_office.keys()),
            "completedCount": len(done_reqs),
            "completedTitles": [r["title"] for r in done_reqs],
            "earliestOverdue": overdue_reqs and min(overdue_reqs, key=lambda r: r["dueAt"]) or None,
            "nextDue": dated_open[0] if dated_open else None,
            "latestDue": dated_open[-1] if dated_open else None,
            "allDone": len(open_reqs) == 0,
        },
        "deposit": {
            "paid": bool(row["deposit_paid"]),
            "paidAt": _date(row["deposit_paid_at"]),
            "amountCents": row["deposit_amount_cents"],
            "amountDollars": (row["deposit_amount_cents"] or 0) // 100,
            "requirementStatus": deposit_req["status"] if deposit_req else None,
            "requirementDueAt": deposit_req["dueAt"] if deposit_req else None,
            "pattern": (
                "(?:paid|received|posted|complete|settled|✓)"
                if row["deposit_paid"]
                else "(?:not (?:yet )?(?:been )?(?:paid|received|posted|made)|unpaid|outstanding|still (?:due|owe|need)|hasn'?t been (?:paid|received)|pending|due)"
            ),
        },
        "documents": {
            "all": doc_list,
            "total": len(doc_list),
            "byStatus": by_status,
            "fileNames": [d["fileName"] for d in doc_list],
            "stems": [d["stem"] for d in doc_list],
            "acceptedCount": len(by_status.get("accepted", [])),
            "rejectedCount": len(by_status.get("rejected", [])),
            "pendingCount": sum(len(by_status.get(s, [])) for s in pending_statuses),
            "rejected": [d for d in doc_list if d["status"] == "rejected"],
            "pending": [d for d in doc_list if d["status"] in pending_statuses],
            "pendingFileNames": [d["fileName"] for d in doc_list if d["status"] in pending_statuses],
            "pendingStems": [d["stem"] for d in doc_list if d["status"] in pending_statuses],
            "rejectedFileNames": [d["fileName"] for d in doc_list if d["status"] == "rejected"],
            "rejectedStems": [d["stem"] for d in doc_list if d["status"] == "rejected"],
            "rejectedCategories": sorted({d["category"] for d in doc_list if d["status"] == "rejected"}),
            "transcript": transcript_docs[-1] if transcript_docs else None,
            "transcriptStatus": transcript_docs[-1]["status"] if transcript_docs else None,
            "hasRejected": any(d["status"] == "rejected" for d in doc_list),
        },
        "appointments": {
            "all": appt_list,
            "total": len(appt_list),
            "hasAny": len(appt_list) > 0,
            "upcoming": upcoming,
            "upcomingCount": len(upcoming),
            "upcomingStaffNames": sorted({a["staffName"] for a in upcoming if a["staffName"]}),
            "upcomingDates": [a["date"] for a in upcoming],
            "next": upcoming[0] if upcoming else None,
            "thisMonthCount": len(this_month),
            "pastCount": len([a for a in appt_list if not a["upcoming"]]),
            "completedCount": len([a for a in appt_list if a["status"] == "completed"]),
            "noShowCount": len([a for a in appt_list if a["status"] == "no_show"]),
            "cancelledCount": len([a for a in appt_list if a["status"] == "cancelled"]),
            "lastCompleted": next((a for a in reversed(appt_list) if a["status"] == "completed"), None),
            "advisingCompleted": any(a["type"] == "academic_advising" and a["status"] == "completed" for a in appt_list),
        },
        "messages": {
            "unreadCount": int(row["unread_messages"]),
            "total": int(row["total_messages"]),
            "latest": [
                {"subject": m["subject"], "kind": m["kind"], "sender": m["sender_name"], "sentAt": _date(m["sent_at"]), "unread": m["read_at"] is None}
                for m in messages[:8]
            ],
            "latestSubject": messages[0]["subject"] if messages else None,
            "unreadSubjects": [m["subject"] for m in messages if m["read_at"] is None],
        },
        "inquiries": {
            "open": int(row["open_inquiries"]),
            "total": int(row["total_inquiries"]),
            "items": [
                {"subject": i["subject"], "status": i["status"], "topic": i["topic_code"], "createdAt": _date(i["created_at"]), "archivedAt": _date(i["archived_at"])}
                for i in inquiries
            ],
            "subjects": [i["subject"] for i in inquiries],
        },
        "work": {
            "open": work_list,
            "openCount": len(work_list),
            "overdueCount": len([w for w in work_list if w["overdue"]]),
            "keys": [w["key"] for w in work_list],
            "titles": [w["title"] for w in work_list],
            "assignees": sorted({w["assignee"] for w in work_list if w["assignee"]}),
            "assigneePatterns": sorted({w["assigneePattern"] for w in work_list if w["assigneePattern"]}),
            "components": sorted({w["component"] for w in work_list if w["component"]}),
            "blockedCount": len([w for w in work_list if w["status"] == "blocked"]),
            "urgentCount": len([w for w in work_list if w["priority"] == "urgent"]),
            "head": ranked_work[0] if ranked_work else None,
            "oldest": min(work_list, key=lambda w: w["createdAt"]) if work_list else None,
            "recentlyDone": [
                {"key": d["key"], "title": d["title"], "completedAt": _date(d["completed_at"]), "assignee": d["assignee"]}
                for d in done_work
            ],
        },
        "recent": {
            "requirementEvents": [
                {"title": e["title"], "toStatus": e["to_status"], "occurredAt": _date(e["occurred_at"])}
                for e in req_events
            ],
            "latestRequirementEvent": (
                {"title": req_events[0]["title"], "toStatus": req_events[0]["to_status"], "occurredAt": _date(req_events[0]["occurred_at"])}
                if req_events
                else None
            ),
            "latestDocumentDecision": max(
                (d for d in doc_list if d["decidedAt"]), key=lambda d: d["decidedAt"], default=None
            ),
            "latestMessage": (
                {"subject": messages[0]["subject"], "sentAt": _date(messages[0]["sent_at"])} if messages else None
            ),
        },
    }


async def cohort_truth(conn: asyncpg.Connection, tenant: str, now: datetime) -> dict[str, Any]:
    has_adviser = (
        "EXISTS (SELECT 1 FROM student_staff_assignment a WHERE a.tenant_id=s.tenant_id "
        "AND a.student_id=s.id AND a.role='primary_advisor' AND a.ended_at IS NULL)"
    )
    adviser_status = (
        "(SELECT m.employment_status FROM student_staff_assignment a JOIN staff_member m "
        "ON m.id=a.staff_member_id WHERE a.tenant_id=s.tenant_id AND a.student_id=s.id "
        "AND a.role='primary_advisor' AND a.ended_at IS NULL)"
    )
    deposit = (
        "EXISTS (SELECT 1 FROM payment_transaction p WHERE p.tenant_id=s.tenant_id "
        "AND p.student_id=s.id AND p.type='enrollment_deposit' AND p.status='succeeded')"
    )
    row = await conn.fetchrow(
        f"""
        SELECT COUNT(*) AS students,
          COUNT(*) FILTER (WHERE NOT {has_adviser}) AS no_primary_adviser,
          COUNT(*) FILTER (WHERE {adviser_status}='on_leave') AS adviser_on_leave,
          COUNT(*) FILTER (WHERE {adviser_status}='departed') AS adviser_departed,
          COUNT(*) FILTER (WHERE NOT {deposit}) AS unpaid_deposit,
          COUNT(*) FILTER (WHERE {deposit}) AS deposited
        FROM student s WHERE s.tenant_id=$1
        """,
        tenant,
    )
    same: dict[str, Any] = {}
    for name in SAME_NAMES:
        first, last = name.split(" ", 1)
        rows = await conn.fetch(
            """
            SELECT s.external_ref, s.id::text AS id, s.class_year,
                   (SELECT program.name FROM admission_offer o JOIN program ON program.id=o.program_id
                      WHERE o.student_id=s.id ORDER BY o.created_at DESC LIMIT 1) AS program,
                   (SELECT m.display_name FROM student_staff_assignment a JOIN staff_member m ON m.id=a.staff_member_id
                      WHERE a.student_id=s.id AND a.role='primary_advisor' AND a.ended_at IS NULL LIMIT 1) AS adviser
            FROM student s JOIN person ON person.id=s.person_id
            WHERE s.tenant_id=$1 AND person.first_name=$2 AND person.last_name=$3
            ORDER BY s.external_ref
            """,
            tenant,
            first,
            last,
        )
        same[name] = {
            "count": len(rows),
            "entries": [dict(r) for r in rows],
            "programs": sorted({r["program"] for r in rows if r["program"]}),
            "refs": [r["external_ref"] for r in rows],
        }
    absent: dict[str, int] = {}
    for name in ABSENT_NAMES:
        parts = name.split(" ", 1)
        absent[name] = int(
            await conn.fetchval(
                "SELECT COUNT(*) FROM student s JOIN person ON person.id=s.person_id "
                "WHERE s.tenant_id=$1 AND person.first_name=$2 AND person.last_name=$3",
                tenant,
                parts[0],
                parts[1] if len(parts) > 1 else "",
            )
        )
    staff_phone_columns = await conn.fetchval(
        "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='staff_member' "
        "AND column_name ILIKE '%phone%'"
    )
    # Older synthetic worlds have no assessed-risk domain. Report the missing
    # capability explicitly; absence of a table is not a verified zero risk count.
    risk_available = bool(
        await conn.fetchval("SELECT to_regclass('public.student_risk_assessment') IS NOT NULL")
    )
    risk_rows = (
        await conn.fetchval(
            "SELECT COUNT(*) FROM public.student_risk_assessment WHERE tenant_id=$1", tenant
        )
        if risk_available else None
    )
    return {
        **{k: int(row[k]) for k in row.keys()},
        "sameName": same,
        "absentNames": absent,
        "staffPhoneColumns": int(staff_phone_columns or 0),
        "riskAssessmentAvailable": risk_available,
        "riskAssessmentRows": int(risk_rows) if risk_rows is not None else None,
    }


async def main() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        now = await conn.fetchval("SELECT now()")
        staff = await staff_truth(conn, TENANT, now)
        staff_by_id = {e["id"]: e for e in staff["byRef"].values()}
        students: dict[str, Any] = {}
        for ref in STUDENT_REFS:
            entry = await student_entry(conn, TENANT, now, ref, staff_by_id)
            if entry is not None:
                students[ref] = entry
        truth = {
            "generatedAt": _iso(datetime.now(UTC)),
            "now": _iso(now),
            "today": _date(now),
            "tenantId": TENANT,
            "students": students,
            "staff": staff,
            "cohorts": await cohort_truth(conn, TENANT, now),
        }
    finally:
        await conn.close()
    json.dump(truth, sys.stdout, indent=2, default=str)


if __name__ == "__main__":
    asyncio.run(main())
