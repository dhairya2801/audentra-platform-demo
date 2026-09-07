"""Read evidence and run bounded actions against a fork. This is a test-world adapter, not Edward."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from build import CLOCK, insert, stable


class Rejected(ValueError):
    pass


def connect(path, writable=False):
    db = sqlite3.connect(
        f"file:{Path(path).resolve()}?mode={'rw' if writable else 'ro'}",
        uri=True,
        timeout=10,
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def rows(db, query, args=()):
    return [dict(r) for r in db.execute(query, args)]


def one(db, query, args=()):
    result = db.execute(query, args).fetchone()
    return dict(result) if result else None


def evidence(db, student_id, role="student"):
    if role not in ("student", "staff"):
        raise Rejected("Unknown evidence audience")
    student = one(db, "SELECT * FROM student WHERE id=?", (student_id,))
    if not student:
        raise Rejected("Unknown student")
    result = {
        "student": student,
        "snapshot_at": CLOCK,
        "snapshot_semantics": "Current records at the fixed world clock. Use timeline for historical knowledge; do not treat current records as historical snapshots.",
    }
    queries = {
        "applications": "SELECT * FROM application WHERE student_id=?",
        "enrollments": "SELECT e.*,c.code,c.title,c.credits,s.term_id,s.weekday,s.start_minute,s.end_minute,s.room,s.modality FROM enrollment e JOIN section s ON s.id=e.section_id JOIN course c ON c.id=s.course_id WHERE e.student_id=? ORDER BY s.term_id,e.id",
        "documents": "SELECT * FROM document WHERE student_id=? ORDER BY category",
        "document_revisions": "SELECT r.* FROM document_revision r JOIN document d ON d.id=r.document_id WHERE d.student_id=? ORDER BY r.recorded_at",
        "holds": "SELECT h.*,o.name AS office_name FROM hold h JOIN office o ON o.id=h.office_id WHERE student_id=? ORDER BY placed_at",
        "ledger": "SELECT * FROM ledger WHERE student_id=? ORDER BY posted_at,id",
        "payments": "SELECT * FROM payment WHERE student_id=? ORDER BY submitted_at",
        "awards": "SELECT a.*,f.name,f.posts_to_account FROM award a JOIN fund f ON f.id=a.fund_id WHERE student_id=?",
        "disbursements": "SELECT d.*,f.name FROM disbursement d JOIN award a ON a.id=d.award_id JOIN fund f ON f.id=a.fund_id WHERE a.student_id=? ORDER BY scheduled_at",
        "housing": "SELECT h.*,r.name AS residence_name,b.accessible FROM housing h LEFT JOIN bed b ON b.id=h.bed_id LEFT JOIN residence r ON r.id=b.residence_id WHERE student_id=? ORDER BY h.starts_at DESC",
        "advisers": "SELECT a.*,s.name,s.office_id,s.status,s.email FROM assignment a JOIN staff s ON s.id=a.staff_id WHERE student_id=?",
        "exceptions": "SELECT e.*,o.name AS office_name FROM exception e JOIN office o ON o.id=e.office_id WHERE student_id=?",
        "transfers": "SELECT t.*,c.code FROM transfer_credit t JOIN course c ON c.id=t.course_id WHERE student_id=?",
        "consents": "SELECT * FROM consent WHERE student_id=?",
        "appointments": "SELECT a.*,s.name AS staff_name FROM appointment a JOIN staff s ON s.id=a.staff_id WHERE student_id=?",
        "waitlists": "SELECT w.*,c.code FROM waitlist w JOIN section s ON s.id=w.section_id JOIN course c ON c.id=s.course_id WHERE student_id=?",
        "balances": "SELECT * FROM account_balance WHERE student_id=?",
        "loads": "SELECT * FROM current_load WHERE student_id=?",
        "sap_evaluations": "SELECT * FROM sap_evaluation WHERE student_id=? ORDER BY evaluated_at",
        "academic_progress": "SELECT * FROM academic_progress WHERE student_id=?",
    }
    for key, query in queries.items():
        result[key] = rows(db, query, (student_id,))
    result["communications"] = rows(
        db,
        "SELECT * FROM communication WHERE student_id=?"
        + (" AND audience='student'" if role == "student" else "")
        + " ORDER BY sent_at",
        (student_id,),
    )
    result["coverage"] = rows(
        db,
        "SELECT a.*,s.name AS covering_name FROM staff_absence a JOIN staff s ON s.id=a.covering_staff_id JOIN assignment x ON x.staff_id=a.staff_id WHERE x.student_id=? AND x.ends_at IS NULL AND a.starts_at<=? AND a.ends_at>?",
        (student_id, CLOCK, CLOCK),
    )
    if role == "staff":
        result["workflows"] = rows(
            db,
            "SELECT w.*,s.name AS owner_name FROM workflow w JOIN staff s ON s.id=w.owner_id WHERE student_id=?",
            (student_id,),
        )
        result["steps"] = rows(
            db,
            "SELECT s.* FROM workflow_step s JOIN workflow w ON w.id=s.workflow_id WHERE w.student_id=?",
            (student_id,),
        )
        result["dependencies"] = rows(
            db,
            "SELECT d.* FROM step_dependency d JOIN workflow_step s ON s.id=d.step_id JOIN workflow w ON w.id=s.workflow_id WHERE w.student_id=?",
            (student_id,),
        )
    return result


def timeline(db, student_id, known_at=CLOCK, effective_at=CLOCK, role="student"):
    if role not in ("student", "staff"):
        raise Rejected("Unknown evidence audience")
    return rows(
        db,
        "SELECT * FROM event WHERE student_id=? AND recorded_at<=? AND effective_at<=?"
        + (" AND visibility='student'" if role == "student" else "")
        + " ORDER BY effective_at,recorded_at,id",
        (student_id, known_at, effective_at),
    )


def policies(
    db,
    query="",
    role="student",
    at=CLOCK,
    known_at=CLOCK,
    student_id=None,
    history=False,
):
    if role not in ("student", "staff"):
        raise Rejected("Unknown policy audience")
    clauses = ["published_at<=?"]
    args = [known_at]
    if not history:
        clauses += [
            "effective_from<=?",
            "(effective_until IS NULL OR effective_until>?)",
        ]
        args += [at, at]
    if role == "student":
        clauses.append("audience IN ('student','all')")
    if query:
        clauses.append("(title LIKE ? OR body LIKE ? OR code LIKE ?)")
        args += [f"%{query}%"] * 3
    found = rows(
        db,
        "SELECT * FROM policy WHERE "
        + " AND ".join(clauses)
        + " ORDER BY code,effective_from",
        args,
    )
    student = (
        one(db, "SELECT * FROM student WHERE id=?", (student_id,))
        if student_id
        else None
    )
    for p in found:
        facets = json.loads(p["applies_json"])
        verdict = "applies"
        basis = []
        for key, allowed in facets.items():
            actual = None
            if student:
                actual = {
                    "program": student["program_id"],
                    "residency": "international"
                    if student["residency"] == "international"
                    else "domestic",
                    "citizenship": "international"
                    if student["residency"] == "international"
                    else None,
                    "class_standing": student["admit_type"]
                    if student["admit_type"] in ("first_year", "transfer")
                    else None,
                    "admit_term": db.execute(
                        "SELECT name FROM term WHERE id=?", (student["admit_term"],)
                    ).fetchone()[0],
                    "department": db.execute(
                        "SELECT department FROM program WHERE id=?",
                        (student["program_id"],),
                    ).fetchone()[0],
                }.get(key)
            if actual is None:
                if verdict != "does_not_apply":
                    verdict = "unknown"
                basis.append(f"{key}: not established")
            elif actual not in allowed:
                verdict = "does_not_apply"
                basis.append(f"{key}: {actual} excluded")
            else:
                basis.append(f"{key}: {actual}")
        p["applicability"] = verdict
        p["basis"] = basis
    return found


def what_if_drop(db, student_id, enrollment_id, at=CLOCK):
    record = one(
        db,
        "SELECT e.*,s.term_id,c.credits,c.code,t.add_drop_at,t.census_at FROM enrollment e JOIN section s ON s.id=e.section_id JOIN course c ON c.id=s.course_id JOIN term t ON t.id=s.term_id WHERE e.id=? AND e.student_id=? AND e.status='enrolled'",
        (enrollment_id, student_id),
    )
    if not record:
        raise Rejected("Choose an active enrollment belonging to this student")
    student = one(db, "SELECT * FROM student WHERE id=?", (student_id,))
    load = db.execute(
        "SELECT credits FROM current_load WHERE student_id=? AND term_id=?",
        (student_id, record["term_id"]),
    ).fetchone()[0]
    after = load - record["credits"]
    approval = one(
        db,
        "SELECT * FROM exception WHERE student_id=? AND kind='reduced_course_load' AND status='approved' AND starts_at<=? AND ends_at>?",
        (student_id, at, at),
    )
    international = student["residency"] == "international"
    consequences = [
        f"{record['code']}: {load} → {after} registered credits",
        "Drop without transcript record"
        if at <= record["add_drop_at"]
        else "Withdrawal petition and W rules apply; registration drop window closed",
    ]
    if after < 12:
        consequences.append(
            "Aid review required; exact award adjustment is not modeled"
        )
        if international:
            consequences.append(
                "Valid ISS reduced-load approval exists; confirm proposed course is within its scope"
                if approval
                else "DSO approval required before falling below 12 credits"
            )
    dependents = rows(
        db,
        "SELECT c.code FROM prerequisite p JOIN course c ON c.id=p.course_id WHERE p.required_course_id=(SELECT course_id FROM section WHERE id=?)",
        (record["section_id"],),
    )
    return dict(
        student_id=student_id,
        enrollment_id=enrollment_id,
        as_of=at,
        before_credits=load,
        after_credits=after,
        consequences=consequences,
        dependent_courses=[r["code"] for r in dependents],
        exception_id=approval["id"] if approval else None,
        mutated=False,
        limitations=[
            "No exact aid recalculation, tuition refund, graduation or immigration outcome is inferred."
        ],
        policy_ids=[
            "registration-policy@2026.1",
            "f1-enrollment-and-reduced-course-load@2026.1",
            "v3-award-packaging@2026.1",
        ],
    )


def cohort(db):
    denominator = db.execute(
        "SELECT count(DISTINCT s.id) FROM student s JOIN current_load l ON l.student_id=s.id AND l.term_id='2026FA' WHERE s.status='enrolled'"
    ).fetchone()[0]
    members = rows(
        db,
        """SELECT DISTINCT s.id,s.name,s.external_ref,b.balance_cents FROM student s JOIN current_load l ON l.student_id=s.id AND l.term_id='2026FA' JOIN account_balance b ON b.student_id=s.id AND b.term_id='2026FA' WHERE s.status='enrolled' AND EXISTS(SELECT 1 FROM hold h WHERE h.student_id=s.id AND h.kind='financial' AND h.released_at IS NULL) AND EXISTS(SELECT 1 FROM payment p WHERE p.student_id=s.id AND p.term_id='2026FA' AND p.status='pending') ORDER BY s.external_ref""",
    )
    return dict(
        clock=CLOCK,
        definition="Enrolled students with Fall 2026 registrations, an active financial hold and a pending Fall payment. Failed payments excluded; students counted once.",
        denominator=denominator,
        count=len(members),
        members=members,
    )


def release_hold(db, payload):
    required = {
        "hold_id",
        "actor_id",
        "expected_version",
        "confirmed",
        "idempotency_key",
    }
    if set(payload) != required:
        raise Rejected("Expected exactly: " + ", ".join(sorted(required)))
    if payload["confirmed"] is not True:
        raise Rejected("Explicit confirmation is required")
    if not isinstance(payload["expected_version"], int) or isinstance(
        payload["expected_version"], bool
    ):
        raise Rejected("Expected version must be an integer")
    key = payload["idempotency_key"]
    if not isinstance(key, str) or not 8 <= len(key) <= 120:
        raise Rejected("Idempotency key must contain 8–120 characters")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    try:
        db.execute("BEGIN IMMEDIATE")
        receipt = one(
            db, "SELECT * FROM action_receipt WHERE idempotency_key=?", (key,)
        )
        if receipt:
            if receipt["request_json"] != canonical:
                raise Rejected("Idempotency key reused for a different request")
            db.rollback()
            return json.loads(receipt["result_json"])
        actor = one(db, "SELECT * FROM staff WHERE id=?", (payload["actor_id"],))
        hold = one(db, "SELECT * FROM hold WHERE id=?", (payload["hold_id"],))
        if not hold:
            raise Rejected("Unknown hold")
        if (
            not actor
            or actor["status"] != "active"
            or actor["office_id"] != "SA"
            or hold["office_id"] != "SA"
            or hold["kind"] != "financial"
        ):
            raise Rejected(
                "Only active Student Accounts staff may release a financial hold"
            )
        if hold["version"] != payload["expected_version"]:
            raise Rejected("Stale version; refresh before confirming")
        if hold["released_at"]:
            raise Rejected("Hold already released; use original receipt for retries")
        # Baseline's billed term is Fall; sum only currently due obligations. Future Spring promises do not offset it.
        balance = db.execute(
            "SELECT COALESCE(SUM(amount_cents),0) FROM ledger WHERE student_id=? AND term_id=? AND posted_at<=? AND (kind<>'charge' OR due_at<=?)",
            (hold["student_id"], hold["term_id"], CLOCK, CLOCK),
        ).fetchone()[0]
        if balance > 25000:
            raise Rejected(
                f"Posted balance ${balance / 100:,.2f} still exceeds $250; pending payments do not count"
            )
        version = hold["version"] + 1
        db.execute(
            "UPDATE hold SET released_at=?,version=? WHERE id=?",
            (CLOCK, version, hold["id"]),
        )
        eid = stable("release", key)
        insert(
            db,
            "event",
            id=eid,
            student_id=hold["student_id"],
            entity_type="hold",
            entity_id=hold["id"],
            effective_at=CLOCK,
            recorded_at=CLOCK,
            actor=actor["id"],
            from_state="active",
            to_state="released",
            description="Student Accounts confirmed settlement and released financial hold",
            visibility="student",
            correlation_id=key,
        )
        result = dict(
            receipt_id=eid,
            hold_id=hold["id"],
            status="released",
            version=version,
            at=CLOCK,
            balance_cents=balance,
            actor_id=actor["id"],
        )
        insert(
            db,
            "action_receipt",
            idempotency_key=key,
            request_json=canonical,
            result_json=json.dumps(result, sort_keys=True),
            created_at=CLOCK,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
