"""Executable institutional promises. Checks must also reject corrupted worlds."""

import json
import sqlite3
from pathlib import Path


def validate(db):
    db.row_factory = sqlite3.Row
    errors = []
    clock = db.execute("SELECT value FROM meta WHERE key='clock'").fetchone()[0]
    for row in db.execute("PRAGMA foreign_key_check"):
        errors.append(f"foreign key: {tuple(row)}")
    checks = {
        "census differs from published calendar": "SELECT t.id FROM term t JOIN calendar c ON c.term_label=t.name WHERE c.id IN ('fall2026-census','spring2027-census') AND t.census_at<>c.starts_at",
        "approved reduced load lacks explicit scope": "SELECT id FROM exception WHERE kind='reduced_course_load' AND status='approved' AND (term_id IS NULL OR minimum_credits IS NULL)",
        "degree requirements do not total program credits": """SELECT p.id FROM program p JOIN requirement r ON r.program_id=p.id GROUP BY p.id HAVING SUM(r.credits)<>p.degree_credits""",
        "section over capacity": "SELECT s.id FROM section s JOIN enrollment e ON e.section_id=s.id AND e.status='enrolled' GROUP BY s.id HAVING COUNT(*)>s.capacity",
        "student schedule conflict": """SELECT a.id FROM enrollment a JOIN enrollment b ON a.student_id=b.student_id AND a.id<b.id JOIN section sa ON sa.id=a.section_id JOIN section sb ON sb.id=b.section_id WHERE a.status='enrolled' AND b.status='enrolled' AND sa.term_id=sb.term_id AND sa.weekday=sb.weekday AND sa.start_minute<sb.end_minute AND sb.start_minute<sa.end_minute""",
        "international ineligible fund": "SELECT a.id FROM award a JOIN student s ON s.id=a.student_id JOIN fund f ON f.id=a.fund_id WHERE s.residency='international' AND f.international_eligible=0",
        "annual award exceeds cap": "SELECT a.id FROM award a JOIN fund f ON f.id=a.fund_id WHERE a.offered_cents>f.annual_cap_cents",
        "disbursements exceed accepted award": "SELECT a.id FROM award a JOIN disbursement d ON d.award_id=a.id AND d.status<>'reversed' GROUP BY a.id HAVING SUM(d.amount_cents)>a.accepted_cents",
        "work authorization used as a disbursement": "SELECT d.id FROM disbursement d JOIN award a ON a.id=d.award_id JOIN fund f ON f.id=a.fund_id WHERE f.posts_to_account=0",
        "posted aid lacks exact ledger evidence": "SELECT d.id FROM disbursement d LEFT JOIN ledger l ON l.disbursement_id=d.id AND l.kind='aid' WHERE d.status='posted' AND (l.id IS NULL OR l.amount_cents<>-d.amount_cents OR l.posted_at<>d.posted_at)",
        "unposted aid credited": "SELECT l.id FROM ledger l JOIN disbursement d ON d.id=l.disbursement_id WHERE l.kind='aid' AND d.status NOT IN ('posted','reversed')",
        "aid credited to wrong student": "SELECT l.id FROM ledger l JOIN disbursement d ON d.id=l.disbursement_id JOIN award a ON a.id=d.award_id WHERE l.student_id<>a.student_id OR l.term_id<>d.term_id",
        "payment lacks exact ledger evidence": "SELECT p.id FROM payment p LEFT JOIN ledger l ON l.payment_id=p.id AND l.kind='payment' WHERE p.status IN ('posted','reversed') AND (l.id IS NULL OR l.amount_cents<>-p.amount_cents OR l.posted_at<>p.settled_at)",
        "unsettled payment credited": "SELECT l.id FROM ledger l JOIN payment p ON p.id=l.payment_id WHERE l.kind='payment' AND p.status NOT IN ('posted','reversed')",
        "payment credited to wrong student": "SELECT l.id FROM ledger l JOIN payment p ON p.id=l.payment_id WHERE l.student_id<>p.student_id OR l.term_id<>p.term_id",
        "reversal lacks opposite original": "SELECT r.id FROM ledger r LEFT JOIN ledger l ON l.id=r.reverses_id WHERE r.kind IN ('reversal','credit_adjustment') AND (l.id IS NULL OR r.amount_cents<>-l.amount_cents OR r.student_id<>l.student_id OR r.term_id<>l.term_id)",
        "document latest revision disagrees": "SELECT d.id FROM document d JOIN document_revision r ON r.document_id=d.id WHERE r.revision=(SELECT MAX(revision) FROM document_revision WHERE document_id=d.id) AND r.status<>d.status",
        "document lacks history": "SELECT d.id FROM document d LEFT JOIN document_revision r ON r.document_id=d.id WHERE r.id IS NULL",
        "document history runs backward": "SELECT a.id FROM document_revision a JOIN document_revision b ON a.document_id=b.document_id AND a.revision<b.revision WHERE a.recorded_at>b.recorded_at",
        "assigned housing lacks bed": "SELECT id FROM housing WHERE status='assigned' AND bed_id IS NULL",
        "waitlisted housing has bed": "SELECT id FROM housing WHERE status='waitlisted' AND bed_id IS NOT NULL",
        "residence inventory disagrees with capacity": "SELECT r.id FROM residence r JOIN bed b ON b.residence_id=r.id GROUP BY r.id HAVING COUNT(*)<>r.capacity",
        "completed step without satisfied prerequisites": "SELECT s.id FROM workflow_step s JOIN step_dependency d ON d.step_id=s.id JOIN workflow_step p ON p.id=d.prerequisite_id WHERE s.status IN ('complete','ready') AND p.status NOT IN ('complete','waived')",
        "dependency crosses cases": "SELECT d.step_id FROM step_dependency d JOIN workflow_step a ON a.id=d.step_id JOIN workflow_step b ON b.id=d.prerequisite_id WHERE a.workflow_id<>b.workflow_id",
        "resolved case has unfinished steps": "SELECT DISTINCT w.id FROM workflow w JOIN workflow_step s ON s.workflow_id=w.id WHERE w.status='resolved' AND s.status NOT IN ('complete','waived')",
        "exception approver wrong office": "SELECT e.id FROM exception e JOIN staff s ON s.id=e.approver_id WHERE s.office_id<>e.office_id",
        "absence coverage not active": "SELECT a.id FROM staff_absence a JOIN staff s ON s.id=a.covering_staff_id WHERE s.status<>'active'",
        "duplicate appointment time": "SELECT a.id FROM appointment a JOIN appointment b ON a.staff_id=b.staff_id AND a.id<b.id AND a.starts_at<b.ends_at AND b.starts_at<a.ends_at WHERE a.status IN ('scheduled','completed') AND b.status IN ('scheduled','completed')",
        "current enrollment before admission term": "SELECT e.id FROM enrollment e JOIN section sec ON sec.id=e.section_id JOIN term t ON t.id=sec.term_id JOIN student s ON s.id=e.student_id JOIN term a ON a.id=s.admit_term WHERE t.starts_on<a.starts_on",
        "non-enrolling student has registrations": "SELECT e.id FROM enrollment e JOIN student s ON s.id=e.student_id WHERE s.status IN ('denied','applicant','withdrawn') AND e.status='enrolled'",
        "registration lacks completed advising": "SELECT e.id FROM enrollment e WHERE e.status='enrolled' AND NOT EXISTS (SELECT 1 FROM appointment a WHERE a.student_id=e.student_id AND a.status='completed' AND a.ends_at<e.enrolled_at)",
        "registration lacks settled deposit": "SELECT e.id FROM enrollment e WHERE e.status='enrolled' AND NOT EXISTS (SELECT 1 FROM ledger l WHERE l.student_id=e.student_id AND l.kind='payment' AND l.description='Enrollment deposit credited to tuition' AND l.posted_at<=e.enrolled_at)",
        "registration lacks health clearance at enrollment": "SELECT e.id FROM enrollment e WHERE e.status='enrolled' AND NOT EXISTS (SELECT 1 FROM document d JOIN document_revision r ON r.document_id=d.id WHERE d.student_id=e.student_id AND d.category='immunization' AND r.status IN ('ACCEPTED','WAIVED') AND r.recorded_at<=e.enrolled_at)",
        "refund creates a debt": "SELECT l.id FROM ledger l WHERE l.kind='refund' AND (SELECT SUM(x.amount_cents) FROM ledger x WHERE x.student_id=l.student_id AND x.term_id=l.term_id AND x.posted_at<=l.posted_at)>0",
        "reversed payment lacks reversing entry": "SELECT p.id FROM payment p JOIN ledger l ON l.payment_id=p.id AND l.kind='payment' WHERE p.status='reversed' AND NOT EXISTS(SELECT 1 FROM ledger r WHERE r.reverses_id=l.id AND r.kind='reversal')",
        "waitlist offers exceed remaining seats": "SELECT s.id FROM section s WHERE (SELECT COUNT(*) FROM waitlist w WHERE w.section_id=s.id AND w.status='offered')+(SELECT COUNT(*) FROM enrollment e WHERE e.section_id=s.id AND e.status='enrolled')>s.capacity",
        "waitlist exceeds ten people": "SELECT section_id FROM waitlist WHERE status IN ('offered','waiting') GROUP BY section_id HAVING COUNT(*)>10",
        "SAP attempted credits disagree with transcript": "SELECT a.id FROM sap_evaluation a JOIN term t ON t.id=a.term_id WHERE a.attempted_credits<>COALESCE((SELECT SUM(c.credits) FROM enrollment e JOIN section sec ON sec.id=e.section_id JOIN course c ON c.id=sec.course_id JOIN term et ON et.id=sec.term_id WHERE e.student_id=a.student_id AND e.status IN ('completed','withdrawn') AND et.ends_on<=t.ends_on),0)",
        "SAP maximum disagrees with program length": "SELECT a.id FROM sap_evaluation a JOIN student s ON s.id=a.student_id JOIN program p ON p.id=s.program_id WHERE a.maximum_attempted_credits<>p.degree_credits*3/2",
        "policy version windows overlap": "SELECT a.id FROM policy a JOIN policy b ON a.code=b.code AND a.id<b.id WHERE a.effective_from<COALESCE(b.effective_until,'9999') AND b.effective_from<COALESCE(a.effective_until,'9999')",
    }
    for label, sql in checks.items():
        rows = db.execute(sql).fetchall()
        if rows:
            errors.append(f"{label}: {len(rows)} ({rows[0][0]})")
    for table, col in [
        ("sap_evaluation", "evaluated_at"),
        ("ledger", "posted_at"),
        ("document_revision", "recorded_at"),
        ("event", "recorded_at"),
        ("payment", "settled_at"),
        ("disbursement", "posted_at"),
        ("workflow", "opened_at"),
        ("transfer_credit", "evaluated_at"),
    ]:
        n = db.execute(
            f"SELECT count(*) FROM {table} WHERE {col}>?", (clock,)
        ).fetchone()[0]
        if n:
            errors.append(f"{table}.{col}: {n} facts recorded after clock")
    # All academic prerequisites require accepted transfer or a qualifying earlier completed attempt.
    for e in db.execute(
        "SELECT e.id,e.student_id,e.enrolled_at,s.course_id FROM enrollment e JOIN section s ON s.id=e.section_id WHERE e.status IN ('enrolled','completed','withdrawn')"
    ):
        for p in db.execute(
            "SELECT * FROM prerequisite WHERE course_id=?", (e["course_id"],)
        ):
            grades = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
            required = grades.get(p["minimum_grade"], 2)
            attempts = db.execute(
                "SELECT e.grade FROM enrollment e JOIN section s ON s.id=e.section_id WHERE e.student_id=? AND s.course_id=? AND e.status='completed' AND e.ended_at<?",
                (e["student_id"], p["required_course_id"], e["enrolled_at"]),
            ).fetchall()
            transfers = db.execute(
                "SELECT grade FROM transfer_credit WHERE student_id=? AND course_id=? AND status='accepted' AND evaluated_at<?",
                (e["student_id"], p["required_course_id"], e["enrolled_at"]),
            ).fetchall()
            if not any(grades.get(r[0], -1) >= required for r in attempts + transfers):
                errors.append(
                    f"unsatisfied prerequisite: {e['id']}/{p['required_course_id']}"
                )
                break
    for table, left, right in [
        ("prerequisite", "course_id", "required_course_id"),
        ("step_dependency", "step_id", "prerequisite_id"),
    ]:
        graph = {}
        for a, b in db.execute(f"SELECT {left},{right} FROM {table}"):
            graph.setdefault(a, []).append(b)
        done = set()
        visiting = set()

        def walk(node):
            if node in visiting:
                return False
            if node in done:
                return True
            visiting.add(node)
            if not all(walk(child) for child in graph.get(node, [])):
                return False
            visiting.remove(node)
            done.add(node)
            return True

        if not all(walk(node) for node in graph):
            errors.append(f"{table}: dependency cycle")
    return errors


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    with sqlite3.connect(f"file:{args.database}?mode=ro", uri=True) as db:
        errors = validate(db)
    print(json.dumps({"errors": errors, "valid": not errors}, indent=2))
    raise SystemExit(bool(errors))
