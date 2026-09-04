#!/usr/bin/env python3
"""Ground truth for the institutional-knowledge suite: SQL over the synthetic
university database, one entry per persona, plus the corpus constants the
answers must quote (read from the packaged corpus so they cannot drift).

    uv run --directory apps/api python ../../tools/edward-eval/knowledge/ground_truth.py \
        --database postgresql://vv:vv_local_password@127.0.0.1:5433/vv_enrollment_synthu

Writes artifacts/knowledge-eval/ground-truth.json. Regenerate right before a
run: deposit due dates and overdue buckets move with now().
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
TENANT = "00000000-0000-7000-8000-000000000003"
OUT = REPO_ROOT / "artifacts" / "knowledge-eval" / "ground-truth.json"
CORPUS = REPO_ROOT / "apps" / "api" / "assets" / "config" / "tenants" / "aster-demo" / "knowledge"

STUDENT_REFS = [
    "SYN-001217",  # Petra Oakenshaw: domestic first-year, unpaid overdue deposit, undecided housing
    "SYN-001278",  # Lucia Zephyrine: international first-year, on campus, BS-EE
    "SYN-000631",  # Kwame Oakenshaw: international, rejected immunization document
    "SYN-000061",  # Ada Kettleby: domestic first-year, off-campus plan, BS-DS
    "SYN-001478",  # Ivo Ravensworth: domestic transfer (class of 2028), on campus, BS-CE
    "SYN-000897",  # Noor Zephyrine: adviser on leave
    "SYN-000728",  # Adria Kettleby: international, adviser departed
    "SYN-001726",  # Omar Vellacourt: Spring 2027 admit, no adviser, deposit not yet due
    "SYN-001645",  # Bruno Stonebrook: international Spring admit, completed journey
    "SYN-000023",  # Milo Ironwood: BSN first-year, adviser over cap
    "SYN-000039",  # Ximena Vellacourt: Spring 2027 first-year, BBA, adviser on leave
    "SYN-002720",  # Gustav Fennwick: everything overdue, no adviser
    "SYN-001566",  # Camila Calderwood: Spring admit, rejected immunization
    "SYN-000665",  # Greta Everlyn: international Spring admit
    "SYN-001030",  # Hana Mossbank: international, BS-ME, deposit pending
]
STAFF_REFS = [
    "SYN-ADV-001",  # Hana Dunmire, Senior Academic Adviser
    "SYN-STF-ADV-DIR",  # Leandro Hartigan, Director of Academic Advising
    "SYN-STF-ADM-AD",  # Priya Shah, Associate Director of Admissions
    "SYN-STF-FA-C06",  # Greta Radcliffe, Financial Aid Counselor
    "SYN-STF-ISS-DSO2",  # Matthias Gunnarsson, DSO
    "SYN-STF-REG-DIR",  # Aurelio Abernathy, University Registrar
    "SYN-STF-HRL-DIR",  # Ulysses Abernathy, Director of Housing
    "SYN-STF-ES-DIR",  # Uma Jokinen, Director of Enrollment Services
    "SYN-STF-SHS-MGR",  # Zubin Njoku, Health Compliance Manager
    "SYN-ADV-012",  # Elena Larkspur, over-cap adviser
]

_DONE = {"completed", "waived", "not_applicable", "expired"}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _date_words(value: date | None) -> str | None:
    """A regex that accepts '11 September 2026', 'September 11, 2026', '2026-09-11', 'Sept 11'."""

    if value is None:
        return None
    month = value.strftime("%B")
    short = month[:3]
    day = value.day
    return (
        rf"(?:{day}(?:st|nd|rd|th)? {month}|{month} {day}(?:st|nd|rd|th)?|{short}\.? {day}\b"
        rf"|{value.isoformat()}|{value.month}/{day}/{value.year})"
    )


def _standing(class_year: int | None, term_start: date | None) -> str | None:
    if class_year is None or term_start is None:
        return None
    expected = term_start.year + (4 if term_start.month >= 6 else 3)
    return "first_year" if class_year >= expected else "transfer"


async def student_entry(conn: asyncpg.Connection, ref: str, now: datetime) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT s.id, s.person_id, s.class_year, s.external_ref,
               COALESCE(NULLIF(p.preferred_name, ''), p.first_name) AS first_name, p.last_name,
               o.payload->>'residencyStatus' AS residency,
               o.payload->>'citizenshipStatus' AS citizenship,
               o.payload->>'housingPreference' AS housing_plan,
               o.payload->>'housingResidenceOption' AS residence,
               o.status AS onboarding_status,
               t.name AS term_name, t.starts_on AS term_starts_on,
               pr.code AS program_code, pr.name AS program_name, pr.department,
               ao.response_deadline, ao.deposit_amount_cents
        FROM student s
        JOIN person p ON p.id = s.person_id
        LEFT JOIN student_onboarding o ON o.student_id = s.id AND o.tenant_id = s.tenant_id
        LEFT JOIN LATERAL (
          SELECT * FROM admission_offer ao WHERE ao.student_id = s.id AND ao.tenant_id = s.tenant_id
          ORDER BY ao.created_at DESC LIMIT 1
        ) ao ON true
        LEFT JOIN academic_term t ON t.id = ao.academic_term_id
        LEFT JOIN program pr ON pr.id = ao.program_id
        WHERE s.tenant_id = $1 AND s.external_ref = $2
        """,
        TENANT,
        ref,
    )
    if row is None:
        raise SystemExit(f"no student {ref}")
    student_id = row["id"]
    requirements = await conn.fetch(
        """
        SELECT d.code, d.title, r.status, r.due_at, d.responsible_office
        FROM student_requirement r
        JOIN requirement_definition_version d ON d.id = r.requirement_definition_version_id
        JOIN enrollment_journey j ON j.id = r.journey_id
        WHERE r.tenant_id = $1 AND j.student_id = $2 AND r.retired_at IS NULL
        ORDER BY d.display_order
        """,
        TENANT,
        student_id,
    )
    by_code = {str(r["code"]): r for r in requirements}
    overdue = [
        str(r["title"])
        for r in requirements
        if str(r["status"]) not in _DONE and r["due_at"] is not None and r["due_at"] < now
    ]
    deposit_req = by_code.get("enrollment_deposit")
    deposit_paid = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM payment_transaction pt
          WHERE pt.tenant_id = $1 AND pt.student_id = $2 AND pt.status = 'succeeded'
        )
        """,
        TENANT,
        student_id,
    )
    deposit_pending = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM payment_transaction pt
          WHERE pt.tenant_id = $1 AND pt.student_id = $2 AND pt.status IN ('pending', 'processing')
        )
        """,
        TENANT,
        student_id,
    )
    documents = await conn.fetch(
        """
        SELECT category, status, created_at FROM document_record
        WHERE tenant_id = $1 AND student_id = $2 ORDER BY created_at DESC
        """,
        TENANT,
        student_id,
    )
    latest_by_category: dict[str, str] = {}
    for doc in documents:
        latest_by_category.setdefault(str(doc["category"]), str(doc["status"]))
    adviser = await conn.fetchrow(
        """
        SELECT sm.display_name, sm.title, sm.employment_status, sm.leave_until, sm.external_ref,
               sm.email_normalized
        FROM student_staff_assignment a
        JOIN staff_member sm ON sm.id = a.staff_member_id
        WHERE a.tenant_id = $1 AND a.student_id = $2 AND a.role = 'primary_advisor'
          AND a.ended_at IS NULL
        ORDER BY a.assigned_at DESC LIMIT 1
        """,
        TENANT,
        student_id,
    )
    awards = await conn.fetch(
        """
        SELECT name, source, type, status, offered_amount_cents, accepted_amount_cents
        FROM student_financial_award WHERE tenant_id = $1 AND student_id = $2 ORDER BY name
        """,
        TENANT,
        student_id,
    )
    sap = await conn.fetchrow(
        """
        SELECT status, cumulative_gpa, completion_rate_percent, attempted_credits,
               maximum_attempted_credits
        FROM student_sap_status WHERE tenant_id = $1 AND student_id = $2
        """,
        TENANT,
        student_id,
    )
    standing = _standing(row["class_year"], row["term_starts_on"])
    deposit_due = deposit_req["due_at"] if deposit_req else None
    return {
        "ref": ref,
        "id": str(student_id),
        "personId": str(row["person_id"]),
        "name": f"{row['first_name']} {row['last_name']}",
        "firstName": str(row["first_name"]),
        "classYear": row["class_year"],
        "residency": row["residency"],
        "citizenship": row["citizenship"],
        "isInternational": row["residency"] == "international",
        "classStanding": standing,
        "isFirstYear": standing == "first_year",
        "isTransfer": standing == "transfer",
        "admitTerm": row["term_name"],
        "admitTermStartsOn": _iso(row["term_starts_on"]),
        "isSpringAdmit": (row["term_name"] or "").startswith("Spring"),
        "housingPlan": row["housing_plan"],
        "residence": row["residence"],
        "programCode": row["program_code"],
        "programName": row["program_name"],
        "department": row["department"],
        "onboardingStatus": row["onboarding_status"],
        "deposit": {
            "amountUsd": (row["deposit_amount_cents"] or 0) // 100,
            "paid": bool(deposit_paid),
            # No pending payment rows exist in the population; the requirement
            # engine's in_progress is the "submitted, not posted" signal.
            "pending": (bool(deposit_pending) or (deposit_req is not None and str(deposit_req["status"]) == "in_progress"))
            and not bool(deposit_paid),
            "status": str(deposit_req["status"]) if deposit_req else None,
            "dueAt": _iso(deposit_due),
            "dueDatePattern": _date_words(deposit_due.date() if deposit_due else None),
            "overdue": bool(
                deposit_req
                and str(deposit_req["status"]) not in _DONE
                and deposit_due is not None
                and deposit_due < now
            ),
        },
        "requirements": {
            str(r["code"]): {
                "title": str(r["title"]),
                "status": str(r["status"]),
                "dueAt": _iso(r["due_at"]),
                "overdue": str(r["status"]) not in _DONE
                and r["due_at"] is not None
                and r["due_at"] < now,
                "office": r["responsible_office"],
            }
            for r in requirements
        },
        "overdueTitles": overdue,
        "overdueCount": len(overdue),
        "documents": latest_by_category,
        "adviser": (
            {
                "name": str(adviser["display_name"]),
                "title": adviser["title"],
                "status": adviser["employment_status"],
                "leaveUntil": _iso(adviser["leave_until"]),
                "ref": adviser["external_ref"],
                "email": adviser["email_normalized"],
            }
            if adviser
            else None
        ),
        "hasAdviser": adviser is not None,
        "awards": [
            {
                "name": str(a["name"]),
                "source": str(a["source"]),
                "type": str(a["type"]),
                "status": str(a["status"]),
                "offeredUsd": (a["offered_amount_cents"] or 0) // 100,
            }
            for a in awards
        ],
        "awardNames": [str(a["name"]) for a in awards],
        "hasFederalAid": any(str(a["source"]) == "federal" for a in awards),
        "sap": (
            {
                "status": str(sap["status"]),
                "gpa": float(sap["cumulative_gpa"]),
                "completionRate": float(sap["completion_rate_percent"]),
                "attempted": float(sap["attempted_credits"]),
                "maximum": float(sap["maximum_attempted_credits"]),
            }
            if sap
            else None
        ),
    }


async def staff_entry(conn: asyncpg.Connection, ref: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT id, display_name, title, component, employment_status, leave_until, office_location
        FROM staff_member WHERE tenant_id = $1 AND external_ref = $2
        """,
        TENANT,
        ref,
    )
    if row is None:
        raise SystemExit(f"no staff {ref}")
    return {
        "ref": ref,
        "id": str(row["id"]),
        "name": str(row["display_name"]),
        "title": row["title"],
        "component": row["component"],
        "status": row["employment_status"],
        "leaveUntil": _iso(row["leave_until"]),
        "office": row["office_location"],
    }


def corpus_constants() -> dict[str, Any]:
    """Facts the answers must quote, pulled from the packaged corpus files."""

    import yaml  # type: ignore[import-untyped]

    calendar = yaml.safe_load((CORPUS / "calendar.yaml").read_text(encoding="utf-8"))
    entries = {entry["code"]: entry for entry in calendar["entries"]}
    offices = yaml.safe_load((CORPUS / "offices.yaml").read_text(encoding="utf-8"))
    office_map = {office["code"]: office for office in offices["offices"]}

    def when(code: str) -> str:
        value = entries[code]["starts_on"]
        return (value if isinstance(value, date) else date.fromisoformat(str(value))).isoformat()

    documents = {}
    for path in sorted((CORPUS / "documents").glob("*.md")):
        match = re.match(r"\A---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
        meta = yaml.safe_load(match.group(1)) if match else {}
        documents[path.stem] = {"title": meta.get("title"), "version": str(meta.get("version"))}
    return {
        "calendar": {code: when(code) for code in entries},
        "calendarLabels": {code: entry["label"] for code, entry in entries.items()},
        "offices": {
            code: {
                "name": office["name"],
                "email": office.get("email"),
                "location": office["location"],
                "sla": office.get("sla_business_days"),
            }
            for code, office in office_map.items()
        },
        "documents": documents,
        "amounts": {
            "deposit": "$500",
            "holdThreshold": "$250",
            "paymentPlanFee": "$45",
            "lateFee": "$75",
            "insurance": "$1,850",
            "tuitionInState": "$18,400",
            "tuitionOutOfState": "$28,900",
            "tuitionInternational": "$31,200",
            "coaInternational": "$47,400",
            "orientationFee": "$175",
            "transcriptFee": "$8",
        },
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--out", type=Path, default=OUT)
    arguments = parser.parse_args()
    now = datetime.now(UTC)
    conn = await asyncpg.connect(arguments.database)
    try:
        students = {ref: await student_entry(conn, ref, now) for ref in STUDENT_REFS}
        staff = {ref: await staff_entry(conn, ref) for ref in STAFF_REFS}
    finally:
        await conn.close()
    truth = {
        "generatedAt": now.isoformat(),
        "tenantId": TENANT,
        "students": students,
        "staff": {"byRef": staff},
        "corpus": corpus_constants(),
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(truth, indent=2, default=str), encoding="utf-8")
    print(f"wrote {arguments.out} ({len(students)} students, {len(staff)} staff)", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
