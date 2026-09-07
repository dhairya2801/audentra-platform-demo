"""Operational context derived from actual needs, without inflating the roster."""

from datetime import datetime, timedelta

from build import CLOCK, insert, local_instant, stable


def add_operations(db, policies, event):
    # The approval must state its numeric scope; a generic accommodation policy
    # cannot supply the floor for an individual ISS authorization.
    db.execute(
        "UPDATE exception SET term_id='2026FA',minimum_credits=8 WHERE kind='reduced_course_load' AND status='approved'"
    )
    db.execute(
        "UPDATE exception SET term_id='2026FA',maximum_credits=21 WHERE kind='overload'"
    )
    staff = [dict(r) for r in db.execute("SELECT * FROM staff ORDER BY id")]
    offices = {r["id"]: dict(r) for r in db.execute("SELECT * FROM office")}
    by_office = {}
    for member in staff:
        if member["status"] != "departed":
            by_office.setdefault(member["office_id"], []).append(member)
        for day in range(5):
            for start, end in [(9 * 60, 12 * 60), (13 * 60, 17 * 60)]:
                insert(
                    db,
                    "staff_availability",
                    id=stable("hours", member["id"], day, start),
                    staff_id=member["id"],
                    weekday=day,
                    start_minute=start,
                    end_minute=end,
                    timezone="America/New_York",
                    location=offices[member["office_id"]]["location"]
                    or "Campus office",
                )
        # A future office meeting occupies real calendar time. No calendar event
        # is described as a student appointment or completed case.
        insert(
            db,
            "staff_calendar_event",
            id=stable("meeting", member["id"]),
            staff_id=member["id"],
            title=f"{offices[member['office_id']]['name']} case review",
            starts_at=local_instant("2026-09-10", "12:00"),
            ends_at=local_instant("2026-09-10", "12:30"),
            location=offices[member["office_id"]]["location"] or "Campus office",
            blocks_bookings=1,
        )

    # Named coverage in every office, but preserve the curated absence exactly.
    absent = {r[0] for r in db.execute("SELECT staff_id FROM staff_absence")}
    for index, (office, people) in enumerate(sorted(by_office.items())):
        if len(people) < 2 or people[-1]["id"] in absent:
            continue
        start = datetime(2026, 9, 14) + timedelta(days=index % 5)
        insert(
            db,
            "staff_absence",
            id=stable("training-leave", office),
            staff_id=people[-1]["id"],
            starts_at=local_instant(start.date().isoformat(), "09:00"),
            ends_at=local_instant(start.date().isoformat(), "17:00"),
            covering_staff_id=next(
                p["id"]
                for p in people
                if p["status"] == "active" and p["id"] != people[-1]["id"]
            ),
            reason="Professional development; named service coverage",
        )

    role_office = {
        "admissions_counselor": "ADM",
        "financial_aid_counselor": "FA",
        "international_adviser": "ISS",
        "housing_coordinator": "HRL",
    }
    # Office codes come from the existing institutional directory.
    for index, student in enumerate(
        db.execute("SELECT * FROM student ORDER BY external_ref").fetchall()
    ):
        for role, office in role_office.items():
            if office not in by_office:
                continue
            if (
                role == "international_adviser"
                and student["residency"] != "international"
            ):
                continue
            if (
                role == "housing_coordinator"
                and not db.execute(
                    "SELECT 1 FROM housing WHERE student_id=?", (student["id"],)
                ).fetchone()
            ):
                continue
            member = by_office[office][index % len(by_office[office])]
            insert(
                db,
                "assignment",
                id=stable("assignment", student["id"], role),
                student_id=student["id"],
                staff_id=member["id"],
                role=role,
                starts_at="2026-08-01T13:00:00Z",
                ends_at=None,
                reason="Named service owner for this student's institutional relationship",
            )

    # Operational cases only when evidence identifies unfinished work. Preserve
    # all curated handoff DAGs and avoid generating competing cases for them.
    needs = db.execute("""SELECT d.*,s.external_ref FROM document d JOIN student s ON s.id=d.student_id
      WHERE d.status IN ('UNDER_REVIEW','REJECTED','NEEDS_RESUBMISSION')
      AND NOT EXISTS(SELECT 1 FROM workflow w WHERE w.student_id=d.student_id)
      ORDER BY s.external_ref,d.id LIMIT 160""").fetchall()
    for index, doc in enumerate(needs):
        people = by_office[doc["office_id"]]
        owner = people[index % len(people)]
        wid = stable("document-case", doc["id"])
        pending_review = doc["status"] == "UNDER_REVIEW"
        insert(
            db,
            "workflow",
            id=wid,
            student_id=doc["student_id"],
            title=f"{doc['category'].replace('_', ' ').title()} follow-up",
            kind="document_review",
            office_id=doc["office_id"],
            owner_id=owner["id"],
            status="open" if pending_review else "waiting",
            opened_at="2026-09-08T13:00:00Z",
            due_at="2026-09-11T21:00:00Z",
            resolved_at=None,
            policy_id=policies["int-document-review-procedure"],
            version=1,
        )
        insert(
            db,
            "workflow_step",
            id=stable(wid, "review"),
            workflow_id=wid,
            title="Review submitted evidence"
            if pending_review
            else "Await corrected student evidence",
            office_id=doc["office_id"],
            status="ready" if pending_review else "blocked",
            completed_at=None,
            evidence=None,
        )
        event(
            doc["student_id"],
            "workflow",
            wid,
            CLOCK,
            "open" if pending_review else "waiting",
            "Document follow-up opened from canonical review status",
            visibility="internal",
        )
