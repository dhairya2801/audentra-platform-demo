"""Rebuild Aster v3 from repository sources. No production services or model calls."""

from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ASSETS = ROOT / "apps/api/assets/config/tenants/aster-demo"
DEFAULT_OUTPUT = ROOT / "artifacts/university-v3"
CLOCK = "2026-09-08T16:00:00Z"
SEED = 20260908


def stable(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:24]


def insert(db, table, **row):
    db.execute(
        f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
        tuple(row.values()),
    )
    return row.get("id")


def local_instant(day, time="23:59"):
    return (
        datetime.fromisoformat(f"{day}T{time}")
        .replace(tzinfo=ZoneInfo("America/New_York"))
        .astimezone(ZoneInfo("UTC"))
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def business_due(start, days):
    current = date.fromisoformat(start[:10])
    holidays = {date(2026, 9, 7), date(2026, 11, 26), date(2026, 11, 27)}
    while days:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in holidays:
            days -= 1
    return local_instant(current.isoformat(), "17:00")


def source_audit(u):
    students = {s["id"]: s for s in u["students"]}
    beds = collections.Counter(
        (a["hallId"], a["roomLabel"]) for a in u["housingAssignments"]
    )
    return [
        (
            "aid",
            "International funding mismatch",
            f"{sum(students[a['studentId']]['residency'] == 'international' and a['source'] in ('federal', 'state') for a in u['aidAwards'])} federal/state awards to international students in the packaged source.",
            "Repackage by explicit fund eligibility and annual ceilings; rebuild disbursements and ledger together.",
        ),
        (
            "housing",
            "Duplicate occupied beds",
            f"{sum(n - 1 for n in beds.values() if n > 1)} duplicate hall/bed labels; legacy random labels were not inventory.",
            "Physical bed inventory, unique active occupancy per term, and waitlists.",
        ),
        (
            "time",
            "Future completed disbursements",
            f"{sum(a['status'] == 'disbursed' and a['disbursedAt'] > u['meta']['generatedFor'] for a in u['disbursements'])} posted disbursements after the source clock.",
            "Fixed 8 September clock; scheduled rows separate from posted facts; explicit effective and recorded times.",
        ),
        (
            "academics",
            "Two disconnected course catalogs",
            "Packaged source has 42 courses and 90 sections; institutional catalog has 107 courses, no actual enrollment mapping.",
            "Use institutional catalog as authority; generate sections, enrollments, completed attempts and evaluated transfer credit.",
        ),
        (
            "academics",
            "Partial degree requirements",
            "Named program courses cover only 28–54 credits of degrees requiring 120–130.",
            "Preserve named requirements; add explicit remaining-credit elective/core buckets. Do not assert graduation readiness from credit totals alone.",
        ),
        (
            "finance",
            "Annual and semester amounts conflated",
            "Annual tuition is charged against a single term; work-study can become an aid credit; awards include Pell $29,406.",
            "Semester charges in integer cents; work-study never posts; capped packages; pending/failed/reversed payments and linked postings.",
        ),
        (
            "history",
            "Single-version institution",
            "87 documents use 2026.1, one uses 2026.2; no operational version comparison or executable exceptions.",
            "Retain corpus; add dated operating rules, superseded guidance, scoped approvals, publication time and explicit source precedence.",
        ),
        (
            "reproducibility",
            "Runtime and source are different worlds",
            "3,000 source students; v2 reported 2,577 tenant students and 88 staff. Product import intentionally drops unsupported source domains.",
            "v3 is a versioned local evaluation database with source hashes, stable identity mapping and a documented product adapter boundary.",
        ),
    ]


def build(output=DEFAULT_OUTPUT, seed=SEED):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "university.sqlite"
    pending = output / "university.building.sqlite"
    if pending.exists():
        pending.unlink()
    db = sqlite3.connect(pending)
    db.row_factory = sqlite3.Row
    for migration in sorted((HERE / "migrations").glob("*.sql")):
        db.executescript(migration.read_text())
    raw = ROOT / "apps/api/assets/demo/synthetic-university-v1.json.gz"
    u = json.loads(gzip.decompress(raw.read_bytes()))
    rng = random.Random(seed)
    sources = [
        raw,
        *sorted(ASSETS.glob("*.yaml")),
        *sorted((ASSETS / "knowledge").rglob("*.yaml")),
        *sorted((ASSETS / "knowledge/documents").glob("*.md")),
        *sorted(HERE.glob("*.py")),
        *sorted((HERE / "fixtures").glob("*.json")),
        *sorted((HERE / "policies").glob("*.md")),
        *sorted((HERE / "migrations").glob("*.sql")),
    ]
    hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sources
    }
    for k, v in dict(
        version="aster-world-v3",
        clock=CLOCK,
        seed=str(seed),
        timezone="America/New_York",
        tenant="aster-demo",
        source_hashes=json.dumps(hashes, sort_keys=True),
        synthetic="true",
    ).items():
        insert(db, "meta", key=k, value=v)
    for office in yaml.safe_load((ASSETS / "knowledge/offices.yaml").read_text())[
        "offices"
    ]:
        insert(
            db,
            "office",
            id=office["code"],
            name=office["name"],
            location=office["location"],
            email=office["email"],
            sla_days=office.get("sla_business_days"),
            description=office["description"],
        )
    # Preserve the 25 source adviser identities. Build the rest of the institutional roster locally.
    for a in u["advisors"]:
        insert(
            db,
            "staff",
            id=a["id"],
            name=f"{a['firstName']} {a['lastName']}",
            office_id="ADV",
            title=a["title"],
            status="active",
            capacity=a["caseloadCap"],
            department=a["department"],
            email=a["email"],
        )
    offices = [dict(r) for r in db.execute("SELECT * FROM office ORDER BY id")]
    first = [
        "Amara",
        "Bennett",
        "Camila",
        "Desmond",
        "Elowen",
        "Farid",
        "Greta",
        "Hollis",
        "Imani",
        "Jasper",
        "Keziah",
        "Leandro",
        "Maeve",
        "Nikhil",
        "Orla",
        "Priyanka",
        "Quentin",
        "Rosalind",
        "Soren",
        "Tamsin",
        "Ulysses",
    ]
    last = [
        "Abernathy",
        "Blackwood",
        "Castellanos",
        "Dunmore",
        "Ellery",
        "Fairweather",
        "Galloway",
        "Hartigan",
        "Ibarra",
        "Jennings",
        "Kilbride",
    ]
    for i in range(63):
        office = offices[i % len(offices)]
        sid = stable("staff", i)
        insert(
            db,
            "staff",
            id=sid,
            name=f"{first[i % len(first)]} {last[i // len(first)]}",
            office_id=office["id"],
            title=("Director" if i < len(offices) else "Case specialist")
            + ", "
            + office["name"],
            status="active",
            capacity=80 if i < len(offices) else 120,
            email=f"staff.{i}@synthetic.aster.example",
        )
        if i >= len(offices):
            db.execute(
                "UPDATE staff SET manager_id=? WHERE id=?",
                (stable("staff", i % len(offices)), sid),
            )

    def owner(office):
        return db.execute(
            "SELECT id FROM staff WHERE office_id=? ORDER BY id LIMIT 1", (office,)
        ).fetchone()[0]

    advisers = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM staff WHERE office_id='ADV' AND department IS NOT NULL ORDER BY id"
        )
    ]
    absent, cover = advisers[0]["id"], advisers[1]["id"]
    db.execute("UPDATE staff SET status='leave' WHERE id=?", (absent,))
    insert(
        db,
        "staff_absence",
        id="absence-adviser",
        staff_id=absent,
        starts_at="2026-09-01T04:00:00Z",
        ends_at="2026-09-15T04:00:00Z",
        covering_staff_id=cover,
        reason="Scheduled leave; temporary coverage, original ownership retained",
    )
    terms = [
        ("2024FA", "Fall 2024", "2024-08-26", "2024-12-13", "2024-08-12", "2024-09-06"),
        (
            "2025SP",
            "Spring 2025",
            "2025-01-21",
            "2025-05-09",
            "2024-11-04",
            "2025-01-31",
        ),
        ("2025FA", "Fall 2025", "2025-09-02", "2025-12-19", "2025-04-07", "2025-09-12"),
        (
            "2026SP",
            "Spring 2026",
            "2026-01-20",
            "2026-05-08",
            "2025-11-03",
            "2026-01-30",
        ),
        ("2026FA", "Fall 2026", "2026-08-31", "2026-12-18", "2026-08-17", "2026-09-11"),
        (
            "2027SP",
            "Spring 2027",
            "2027-01-19",
            "2027-05-07",
            "2026-11-02",
            "2027-01-29",
        ),
        ("2027FA", "Fall 2027", "2027-08-30", "2027-12-17", "2027-04-05", "2027-09-10"),
    ]
    for tid, name, start, end, opens, close in terms:
        insert(
            db,
            "term",
            id=tid,
            name=name,
            starts_on=start,
            ends_on=end,
            registration_opens=local_instant(opens, "09:00"),
            add_drop_at=local_instant(close),
            census_at=local_instant(close),
        )
    for p in u["programs"]:
        insert(
            db,
            "program",
            id=p["code"],
            name=p["name"],
            department=p["department"],
            degree_credits=p.get("creditsRequired", p.get("requiredCredits", 120)),
        )
    catalog = yaml.safe_load((ASSETS / "academics.yaml").read_text())["courses"]
    course_by_code = {c["code"]: c for c in catalog}
    for c in catalog:
        insert(
            db,
            "course",
            id=c["id"],
            code=c["code"],
            title=c["title"],
            credits=c["credits"],
            level=c["level"],
            description=c["description"],
        )
    for c in catalog:
        for p in c.get("prerequisites", []):
            insert(
                db,
                "prerequisite",
                course_id=c["id"],
                required_course_id=course_by_code[p["course_code"]]["id"],
                minimum_grade=p["minimum_grade"],
            )
    plans = yaml.safe_load((ASSETS / "knowledge/programs.yaml").read_text())["programs"]
    for p in plans:
        named = 0
        for i, r in enumerate(p["requirements"]):
            c = course_by_code[r["course"]]
            named += c["credits"]
            insert(
                db,
                "requirement",
                id=stable("req", p["code"], i),
                program_id=p["code"],
                course_id=c["id"],
                category=r["category"],
                recommended_term=r["term"],
                credits=c["credits"],
                description=c["title"],
            )
        total = db.execute(
            "SELECT degree_credits FROM program WHERE id=?", (p["code"],)
        ).fetchone()[0]
        insert(
            db,
            "requirement",
            id=stable("req", p["code"], "remaining"),
            program_id=p["code"],
            category="remaining_core_and_electives",
            credits=total - named,
            description="Remaining degree credits: adviser-approved general education, major electives and free electives. Distribution audit requires human review; this bucket is not automatic completion.",
        )
    policies = {}
    related = {}
    for path in sorted((ASSETS / "knowledge/documents").glob("*.md")):
        _, fm, body = path.read_text().split("---", 2)
        p = yaml.safe_load(fm)
        pid = f"{p['code']}@{p['version']}"
        policies[p["code"]] = pid
        related[pid] = p.get("related", [])
        insert(
            db,
            "policy",
            id=pid,
            code=p["code"],
            version=p["version"],
            title=p["title"],
            owner_id=p["owner_office"],
            audience=p["audience"],
            authority="policy"
            if p["kind"] == "policy"
            else "procedure"
            if p["kind"] in ("procedure", "internal")
            else "guide",
            effective_from=str(p["effective_from"]) + "T04:00:00Z",
            effective_until=str(p["effective_until"]) + "T04:00:00Z"
            if p.get("effective_until")
            else None,
            published_at=str(p["effective_from"]) + "T04:00:00Z",
            applies_json=json.dumps(p.get("applies_to", {})),
            body=body.strip(),
            source_path=str(path.relative_to(ROOT)),
            content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    for pid, refs in related.items():
        for code in refs:
            insert(db, "policy_link", policy_id=pid, related_id=policies[code])
    for path in sorted((HERE / "policies").glob("*.md")):
        _, fm, body = path.read_text().split("---", 2)
        p = yaml.safe_load(fm)
        pid = p["id"]
        insert(
            db,
            "policy",
            id=pid,
            code=p["code"],
            version=p["version"],
            title=p["title"],
            owner_id=p["owner"],
            audience=p["audience"],
            authority=p["authority"],
            effective_from=p["effective_from"],
            effective_until=p.get("effective_until"),
            published_at=p["published_at"],
            supersedes_id=p.get("supersedes"),
            applies_json=json.dumps(p.get("applies_to", {})),
            body=body.strip(),
            source_path=str(path.relative_to(ROOT)),
            content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        policies[p["code"]] = pid
    for c in yaml.safe_load((ASSETS / "knowledge/calendar.yaml").read_text())[
        "entries"
    ]:
        insert(
            db,
            "calendar",
            id=c["code"],
            term_label=c["term"],
            title=c["label"],
            starts_at=local_instant(str(c["starts_on"]), c.get("starts_at", "23:59")),
            ends_at=local_instant(str(c["ends_on"]), c.get("ends_at", "23:59"))
            if c.get("ends_on")
            else None,
            office_id=c["owner_office"],
            policy_id=policies[c["document"]],
            audience=c["audience"],
            category=c["category"],
        )
    db.execute(
        "UPDATE term SET census_at=(SELECT starts_at FROM calendar WHERE calendar.term_label=term.name AND calendar.id IN ('fall2026-census','spring2027-census')) WHERE name IN ('Fall 2026','Spring 2027')"
    )
    for h in u["residenceHalls"]:
        insert(
            db,
            "residence",
            id=h["code"],
            name=h["name"],
            style=h["style"],
            capacity=h.get("capacity", h.get("beds", 0)),
        )
        capacity = db.execute(
            "SELECT capacity FROM residence WHERE id=?", (h["code"],)
        ).fetchone()[0]
        for i in range(capacity):
            insert(
                db,
                "bed",
                id=f"{h['code']}-{101 + i // 2}{'AB'[i % 2]}",
                residence_id=h["code"],
                room=f"{101 + i // 2}",
                accessible=int(i < 12),
            )
    fund_defs = [
        ("PELL", "Federal Pell Grant", "federal", 739500, 1, 0),
        ("MERIT", "Aster Merit Scholarship", "institutional", 1200000, 1, 1),
        ("ACCESS", "Aster Access Scholarship", "institutional", 1600000, 1, 0),
        ("GLOBAL", "Aster Global Scholarship", "institutional", 2000000, 1, 1),
        ("WORK", "Campus employment authorization", "employment", 300000, 0, 1),
    ]
    for fid, name, source, cap, posts, intl in fund_defs:
        insert(
            db,
            "fund",
            id=fid,
            name=name,
            source=source,
            annual_cap_cents=cap,
            posts_to_account=posts,
            international_eligible=intl,
            policy_id="v3-award-packaging@2026.1",
        )
    # Section placement is stable by course/slot. Capacity expands by opening another section, never overbooking.
    occupancy = collections.Counter()

    def section(course_id, term, slot, historical=False):
        base = f"{course_id}-{term}-{slot}"
        number = occupancy[base] // 28
        sec = f"{base}-{number + 1}"
        if not db.execute("SELECT 1 FROM section WHERE id=?", (sec,)).fetchone():
            insert(
                db,
                "section",
                id=sec,
                course_id=course_id,
                term_id=term,
                label=f"{number + 1:02d}",
                capacity=28,
                weekday=slot // 3,
                start_minute=540 + (slot % 3) * 120,
                end_minute=630 + (slot % 3) * 120,
                room=f"Academic Commons {stable(sec)[:6].upper()}",
                modality="in_person",
                status="open",
            )
        occupancy[base] += 1
        return sec

    def event(
        student,
        entity,
        eid,
        at,
        to,
        description,
        old=None,
        recorded=None,
        visibility="student",
    ):
        insert(
            db,
            "event",
            id=stable("event", student, entity, eid, at, to),
            student_id=student,
            entity_type=entity,
            entity_id=eid,
            effective_at=at,
            recorded_at=recorded or at,
            actor="source-system",
            from_state=old,
            to_state=to,
            description=description,
            visibility=visibility,
            correlation_id=stable(student, entity, eid),
        )

    doc_by_student = collections.defaultdict(list)
    for d in u["documents"]:
        doc_by_student[d["studentId"]].append(d)
    apps = {a["studentId"]: a for a in u["applications"]}
    plan_by_id = {p["code"]: p for p in plans}
    available_beds = [
        r[0] for r in db.execute("SELECT id FROM bed ORDER BY accessible,id")
    ]
    bed_cursor = 0
    scenario_students = []
    appointment_counts = collections.Counter()
    for index, s in enumerate(u["students"]):
        sid = s["id"]
        curated = index < 30
        scenario_students.append(sid) if curated else None
        continuing = not curated and index % 4 == 0
        admit = "2024FA" if continuing else s["cohort"]
        status = (
            "enrolled"
            if continuing or curated
            else {
                "enrolled": "enrolled",
                "admitted": "admitted",
                "applicant": "applicant",
                "denied": "denied",
                "withdrawn": "withdrawn",
            }.get(s["enrollmentStatus"], "admitted")
        )
        if apps[sid]["decision"] == "denied" and not curated and not continuing:
            status = "denied"
        if curated:
            admit = "2026FA"
        residency = (
            "international"
            if index in (4, 5)
            else "in_state"
            if curated
            else s["residency"]
        )
        program = "BS-CS" if curated else s["programCode"]
        insert(
            db,
            "student",
            id=sid,
            external_ref=s["externalRef"],
            name=f"{s['firstName']} {s['lastName']}",
            preferred_name=s["preferredName"],
            email=s["email"],
            program_id=program,
            admit_term=admit,
            residency=residency,
            admit_type="continuing"
            if continuing
            else "international"
            if residency == "international"
            else "transfer"
            if index == 6
            else "first_year"
            if curated
            else s["admitType"],
            status=status,
            birth_date=f"{2004 if continuing else 2008 if index == 17 else 2007}-03-{index % 27 + 1:02d}",
        )
        decided = "2024-04-15T14:00:00Z" if continuing else apps[sid]["decidedAt"]
        insert(
            db,
            "application",
            id=apps[sid]["id"],
            student_id=sid,
            term_id=admit,
            status="admitted" if status == "enrolled" else apps[sid]["decision"],
            submitted_at="2023-11-01T14:00:00Z"
            if continuing
            else apps[sid]["submittedAt"],
            decided_at=decided,
            respond_by="2026-12-15T04:59:00Z"
            if admit == "2027SP"
            else "2026-08-15T03:59:00Z"
            if not continuing
            else "2024-05-01T03:59:00Z",
        )
        event(
            sid,
            "application",
            apps[sid]["id"],
            decided or apps[sid]["submittedAt"],
            "admitted" if status == "enrolled" else apps[sid]["decision"],
            "Admission decision recorded",
        )
        matching = [
            a
            for a in advisers
            if a["department"]
            == db.execute(
                "SELECT department FROM program WHERE id=?", (program,)
            ).fetchone()[0]
        ] or advisers
        advisor = absent if index == 9 else matching[index % len(matching)]["id"]
        insert(
            db,
            "assignment",
            id=stable("assignment", sid),
            student_id=sid,
            staff_id=advisor,
            role="academic_adviser",
            starts_at="2024-08-01T14:00:00Z" if continuing else "2026-08-10T14:00:00Z",
            reason="Department matching; temporary leave covered by office roster",
        )
        # Preserve original source document IDs and histories, but advance curated active students coherently.
        for d in doc_by_student[sid]:
            doc_status = "ACCEPTED" if curated else d["status"]
            if index == 2 and d["category"] == "transcript":
                doc_status = "UNDER_REVIEW"
            if index == 3 and d["category"] == "transcript":
                doc_status = "REJECTED"
            if doc_status not in (
                "NOT_SUBMITTED",
                "UPLOADED",
                "UNDER_REVIEW",
                "ACCEPTED",
                "REJECTED",
                "EXPIRED",
                "WAIVED",
                "NEEDS_RESUBMISSION",
            ):
                doc_status = "UNDER_REVIEW"
            office = (
                "SHS"
                if d["category"] == "immunization"
                else "FA"
                if "verification" in d["category"] or "tax" in d["category"]
                else "ISS"
                if d["category"] == "i20_support"
                else "REG"
            )
            insert(
                db,
                "document",
                id=d["id"],
                student_id=sid,
                category=d["category"],
                status=doc_status,
                office_id=office,
            )
            histories = (
                d["statusHistory"]
                if not curated
                else [
                    {
                        "status": "UPLOADED",
                        "at": "2026-08-10T14:00:00Z",
                        "actor": "student",
                    },
                    {
                        "status": doc_status,
                        "at": "2026-08-14T14:00:00Z",
                        "actor": office,
                    },
                ]
            )
            for revision, h in enumerate(histories, 1):
                insert(
                    db,
                    "document_revision",
                    id=stable(d["id"], revision),
                    document_id=d["id"],
                    revision=revision,
                    status=h["status"],
                    effective_at=h["at"],
                    recorded_at=h["at"],
                    reason="Missing awarding-school seal; resubmit official copy"
                    if h["status"] == "REJECTED"
                    else "Source workflow transition",
                    source=h["actor"],
                )
                event(
                    sid,
                    "document",
                    d["id"],
                    h["at"],
                    h["status"],
                    d["label"] + ": " + h["status"],
                    old=histories[revision - 2]["status"] if revision > 1 else None,
                )
        if status in ("denied", "withdrawn", "applicant") or admit == "2027SP":
            continue
        health_clear = curated or any(
            d["category"] == "immunization" and d["status"] in ("ACCEPTED", "WAIVED")
            for d in doc_by_student[sid]
        )
        if not health_clear:
            db.execute("UPDATE student SET status='admitted' WHERE id=?", (sid,))
            hid = stable("health-hold", sid)
            insert(
                db,
                "hold",
                id=hid,
                student_id=sid,
                kind="health",
                office_id="SHS",
                blocks_registration=1,
                placed_at="2026-08-17T13:00:00Z",
                reason="Immunization clearance outstanding before initial registration",
                policy_id=policies["immunization-requirement"],
            )
            event(
                sid,
                "hold",
                hid,
                "2026-08-17T13:00:00Z",
                "active",
                "Health clearance required before initial registration",
            )
        else:
            db.execute("UPDATE student SET status='enrolled' WHERE id=?", (sid,))
        # Completed course attempts establish prerequisites, rather than random GPA snapshots.
        completed = set()
        if continuing:
            for n in range(1, 5):
                term = terms[n - 1][0]
                preterm_completed = set(completed)
                candidates = [
                    course_by_code[r["course"]]
                    for r in plan_by_id[program]["requirements"]
                    if r["term"] == n
                ]
                for slot, c in enumerate(candidates):
                    if any(
                        course_by_code[p["course_code"]]["id"] not in preterm_completed
                        for p in c.get("prerequisites", [])
                    ):
                        continue
                    sec = section(c["id"], term, slot, True)
                    eid = stable("enrollment", sid, sec)
                    grade = rng.choices(
                        ["A", "B", "C", "D", "F", "W"], [25, 35, 25, 5, 6, 4]
                    )[0]
                    insert(
                        db,
                        "enrollment",
                        id=eid,
                        student_id=sid,
                        section_id=sec,
                        status="withdrawn" if grade == "W" else "completed",
                        enrolled_at=local_instant(
                            (
                                date.fromisoformat(terms[n - 1][2]) - timedelta(days=4)
                            ).isoformat(),
                            "10:00",
                        ),
                        ended_at=local_instant(terms[n - 1][3], "17:00"),
                        grade=grade,
                    )
                    if grade in ("A", "B", "C"):
                        completed.add(c["id"])
                    event(
                        sid,
                        "enrollment",
                        eid,
                        local_instant(terms[n - 1][3], "17:00"),
                        grade,
                        f"{c['code']} final outcome: {grade}",
                    )
        candidates = [
            course_by_code[r["course"]]
            for r in plan_by_id[program]["requirements"]
            if course_by_code[r["course"]]["id"] not in completed
            and all(
                course_by_code[p["course_code"]]["id"] in completed
                for p in course_by_code[r["course"]].get("prerequisites", [])
            )
        ]
        # Fill with catalog electives without inventing prerequisite waivers.
        candidates += [
            c
            for c in catalog
            if not c.get("prerequisites")
            and c["level"] >= 100
            and c["id"] not in completed
            and c not in candidates
        ]
        load = 0
        for slot, c in enumerate(candidates if health_clear else []):
            if load >= 12 or slot >= 6:
                break
            sec = section(c["id"], "2026FA", slot)
            eid = stable("enrollment", sid, sec)
            insert(
                db,
                "enrollment",
                id=eid,
                student_id=sid,
                section_id=sec,
                status="enrolled",
                enrolled_at="2026-08-20T14:00:00Z",
            )
            event(
                sid,
                "enrollment",
                eid,
                "2026-08-20T14:00:00Z",
                "enrolled",
                f"Registered for {c['code']}",
            )
            load += c["credits"]
        # Unique beds across physical capacity. Late demand waits, never shares a bed accidentally.
        residential = curated or (index % 5 < 2)
        housing_cost = 0
        if residential:
            bed_id = (
                available_beds[bed_cursor] if bed_cursor < len(available_beds) else None
            )
            bed_cursor += 1
            hid = stable("housing", sid)
            insert(
                db,
                "housing",
                id=hid,
                student_id=sid,
                term_id="2026FA",
                bed_id=bed_id,
                status="assigned" if bed_id else "waitlisted",
                starts_at="2026-08-22T14:00:00Z",
                reason="Priority assignment"
                if bed_id
                else "Inventory exhausted; Housing owns next offer",
            )
            if bed_id:
                housing_cost = 362500 + 145000
            event(
                sid,
                "housing",
                hid,
                "2026-08-22T14:00:00Z",
                "assigned" if bed_id else "waitlisted",
                "Residence allocation"
                + (": " + bed_id if bed_id else ": no available bed"),
            )
        tuition = {
            "in_state": 920000,
            "out_of_state": 1445000,
            "international": 1560000,
        }[residency]
        charges = [
            ("Tuition — Fall semester", tuition),
            ("Mandatory fees — Fall semester", 72500),
        ]
        if housing_cost:
            charges += [
                ("Housing — Fall semester", 362500),
                ("Unlimited meals — Fall semester", 145000),
            ]
        for label, cents in charges:
            insert(
                db,
                "ledger",
                id=stable("charge", sid, label),
                student_id=sid,
                term_id="2026FA",
                kind="charge",
                amount_cents=cents,
                posted_at="2026-08-10T14:00:00Z",
                due_at="2026-09-04T21:00:00Z",
                description=label,
            )
        aid_total = 0
        selected_funds = (
            ["GLOBAL", "MERIT"]
            if residency == "international"
            else ["PELL", "MERIT"]
            if index % 3
            else ["ACCESS"]
        )
        for fund_id in selected_funds + (["WORK"] if index % 7 == 0 else []):
            fund = db.execute("SELECT * FROM fund WHERE id=?", (fund_id,)).fetchone()
            amount = min(
                fund["annual_cap_cents"],
                400000
                if fund_id == "MERIT"
                else 600000
                if fund_id in ("ACCESS", "GLOBAL")
                else 300000
                if fund_id == "WORK"
                else 500000,
            )
            aid = stable("award", sid, fund_id)
            insert(
                db,
                "award",
                id=aid,
                student_id=sid,
                fund_id=fund_id,
                aid_year="2026-2027",
                offered_cents=amount,
                accepted_cents=amount,
                status="accepted",
            )
            if not fund["posts_to_account"]:
                continue
            disb = stable("disbursement", aid, "2026FA")
            held = index in (7, 8) or (not curated and index % 13 == 0)
            insert(
                db,
                "disbursement",
                id=disb,
                award_id=aid,
                term_id="2026FA",
                amount_cents=amount // 2,
                status="held" if held else "posted",
                scheduled_at="2026-09-04T14:00:00Z",
                posted_at=None if held else "2026-09-04T14:00:00Z",
                reason="Verification outstanding" if held else None,
            )
            insert(
                db,
                "disbursement",
                id=stable("disbursement", aid, "2027SP"),
                award_id=aid,
                term_id="2027SP",
                amount_cents=amount // 2,
                status="scheduled",
                scheduled_at="2027-01-22T15:00:00Z",
                reason="Future semester; recheck enrollment and eligibility",
            )
            if not held:
                aid_total += amount // 2
                insert(
                    db,
                    "ledger",
                    id=stable("aid-ledger", disb),
                    student_id=sid,
                    term_id="2026FA",
                    kind="aid",
                    amount_cents=-amount // 2,
                    posted_at="2026-09-04T14:00:00Z",
                    disbursement_id=disb,
                    description=fund["name"],
                )
                event(
                    sid,
                    "disbursement",
                    disb,
                    "2026-09-04T14:00:00Z",
                    "posted",
                    fund["name"] + " posted to account",
                )
        balance = sum(v for _, v in charges) - aid_total
        unpaid = (
            100000
            if index in (1, 10, 11)
            else 25000
            if index == 12
            else 25001
            if index == 13
            else 75000
            if not curated and index % 11 == 0
            else 0
        )
        deposit = stable("deposit", sid)
        insert(
            db,
            "payment",
            id=deposit,
            student_id=sid,
            term_id="2026FA",
            amount_cents=50000,
            status="posted",
            submitted_at="2026-08-09T14:00:00Z",
            settled_at="2026-08-10T14:00:00Z",
            method="card",
            idempotency_key=deposit,
        )
        insert(
            db,
            "ledger",
            id=deposit,
            student_id=sid,
            term_id="2026FA",
            kind="payment",
            amount_cents=-50000,
            posted_at="2026-08-10T14:00:00Z",
            payment_id=deposit,
            description="Enrollment deposit credited to tuition",
        )
        amount = balance - unpaid - 50000
        payment = stable("payment", sid)
        settled_at = (
            "2026-09-08T13:00:00Z" if index in (22, 29) else "2026-09-03T14:00:00Z"
        )
        insert(
            db,
            "payment",
            id=payment,
            student_id=sid,
            term_id="2026FA",
            amount_cents=amount,
            status="posted",
            submitted_at="2026-09-02T14:00:00Z",
            settled_at=settled_at,
            method="bank_transfer",
            idempotency_key=stable("payment-key", sid),
        )
        # The separate deposit credit has already reduced this settlement amount.
        insert(
            db,
            "ledger",
            id=stable("payment-ledger", payment),
            student_id=sid,
            term_id="2026FA",
            kind="payment",
            amount_cents=-amount,
            posted_at=settled_at,
            payment_id=payment,
            description="Settled semester payment",
        )
        event(
            sid,
            "payment",
            payment,
            settled_at,
            "posted",
            "Payment settled and credited to account",
        )
        if unpaid > 25000:
            hid = stable("financial-hold", sid)
            insert(
                db,
                "hold",
                id=hid,
                student_id=sid,
                kind="financial",
                office_id="SA",
                blocks_registration=1,
                placed_at="2026-09-05T13:00:00Z",
                reason=f"Past-due balance ${unpaid / 100:,.2f} exceeds $250",
                policy_id=policies["billing-and-payment-policy"],
            )
            event(
                sid,
                "hold",
                hid,
                "2026-09-05T13:00:00Z",
                "active",
                "Student Accounts placed a financial hold",
            )
        if index in (1, 10, 11):
            pending_id = stable("unsettled", sid)
            insert(
                db,
                "payment",
                id=pending_id,
                student_id=sid,
                term_id="2026FA",
                amount_cents=unpaid,
                status="failed" if index == 11 else "pending",
                submitted_at="2026-09-08T13:00:00Z",
                method="bank_transfer",
                idempotency_key=stable("unsettled-key", sid),
            )
            event(
                sid,
                "payment",
                pending_id,
                "2026-09-08T13:00:00Z",
                "failed" if index == 11 else "pending",
                "Bank rejected transfer: insufficient funds"
                if index == 11
                else "Transfer submitted; no ledger posting yet",
            )
        if health_clear:
            slot_index = appointment_counts[advisor]
            appointment_counts[advisor] += 1
            appointment_day = date(2026, 8, 3)
            days = slot_index // 14
            while days:
                appointment_day += timedelta(days=1)
                if appointment_day.weekday() < 5:
                    days -= 1
            minute = 540 + (slot_index % 14) * 30
            aid = stable("appointment", sid)
            at = local_instant(
                appointment_day.isoformat(), f"{minute // 60:02d}:{minute % 60:02d}"
            )
            end = local_instant(
                appointment_day.isoformat(),
                f"{(minute + 30) // 60:02d}:{(minute + 30) % 60:02d}",
            )
            insert(
                db,
                "appointment",
                id=aid,
                student_id=sid,
                staff_id=advisor,
                starts_at=at,
                ends_at=end,
                status="completed",
                purpose="Initial registration advising",
            )
            event(
                sid,
                "appointment",
                aid,
                end,
                "completed",
                "Initial academic advising completed",
            )
    # SAP is derived from completed attempts at each term boundary, with prior evaluations retained.
    grade_points = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    for student in db.execute(
        "SELECT s.id,p.degree_credits FROM student s JOIN program p ON p.id=s.program_id WHERE s.admit_type='continuing'"
    ).fetchall():
        previous = None
        previous_status = None
        for term in terms[:4]:
            attempts = db.execute(
                "SELECT e.grade,c.credits FROM enrollment e JOIN section sec ON sec.id=e.section_id JOIN course c ON c.id=sec.course_id JOIN term t ON t.id=sec.term_id WHERE e.student_id=? AND e.status IN ('completed','withdrawn') AND t.ends_on<=?",
                (student["id"], term[3]),
            ).fetchall()
            attempted = sum(r["credits"] for r in attempts)
            earned = sum(
                r["credits"]
                for r in attempts
                if r["grade"] in ("A", "B", "C", "D", "P")
            )
            denominator = sum(
                r["credits"] for r in attempts if r["grade"] in grade_points
            )
            gpa = (
                round(
                    sum(
                        r["credits"] * grade_points.get(r["grade"], 0) for r in attempts
                    )
                    / denominator,
                    3,
                )
                if denominator
                else None
            )
            pace = earned / attempted if attempted else None
            maximum = student["degree_credits"] * 3 // 2
            passing = (
                attempted <= maximum and gpa is not None and gpa >= 2 and pace >= 0.67
            )
            state = (
                "not_evaluated"
                if not attempted
                else "meeting"
                if passing
                else "warning"
                if previous_status in (None, "meeting", "not_evaluated")
                else "suspension"
            )
            eid = stable("sap", student["id"], term[0])
            at = local_instant(term[3], "18:00")
            insert(
                db,
                "sap_evaluation",
                id=eid,
                student_id=student["id"],
                term_id=term[0],
                attempted_credits=attempted,
                earned_credits=earned,
                gpa=gpa,
                completion_rate=pace,
                maximum_attempted_credits=maximum,
                status=state,
                previous_id=previous,
                evaluated_at=at,
                policy_id=policies["satisfactory-academic-progress-policy"],
            )
            event(
                student["id"],
                "sap_evaluation",
                eid,
                at,
                state,
                f"SAP evaluated: {earned}/{attempted} credits; GPA {gpa}",
                old=previous_status,
            )
            previous = eid
            previous_status = state
        # A suspended evaluation holds aid, not registration; remove the unreleased posting coherently.
        if previous_status == "suspension":
            disbursements = db.execute(
                "SELECT d.id FROM disbursement d JOIN award a ON a.id=d.award_id WHERE a.student_id=? AND d.term_id='2026FA' AND d.status='posted'",
                (student["id"],),
            ).fetchall()
            for disb in disbursements:
                db.execute("DELETE FROM ledger WHERE disbursement_id=?", (disb["id"],))
                db.execute(
                    "UPDATE disbursement SET status='held',posted_at=NULL,reason='SAP suspension; successful appeal required before release' WHERE id=?",
                    (disb["id"],),
                )
                db.execute(
                    "DELETE FROM event WHERE entity_type='disbursement' AND entity_id=?",
                    (disb["id"],),
                )
            balance = (
                db.execute(
                    "SELECT SUM(amount_cents) FROM ledger WHERE student_id=? AND term_id='2026FA'",
                    (student["id"],),
                ).fetchone()[0]
                or 0
            )
            existing = db.execute(
                "SELECT id FROM hold WHERE student_id=? AND kind='financial' AND released_at IS NULL",
                (student["id"],),
            ).fetchone()
            if existing:
                db.execute(
                    "UPDATE hold SET reason=? WHERE id=?",
                    (
                        f"Past-due balance ${balance / 100:,.2f}; SAP prevented scheduled aid posting",
                        existing["id"],
                    ),
                )
            if balance > 25000 and not existing:
                hid = stable("sap-balance-hold", student["id"])
                insert(
                    db,
                    "hold",
                    id=hid,
                    student_id=student["id"],
                    kind="financial",
                    office_id="SA",
                    blocks_registration=1,
                    placed_at="2026-09-05T13:00:00Z",
                    reason="Past-due balance after SAP prevented scheduled aid posting",
                    policy_id=policies["billing-and-payment-policy"],
                )
                event(
                    student["id"],
                    "hold",
                    hid,
                    "2026-09-05T13:00:00Z",
                    "active",
                    "Financial hold: aid did not post after SAP review",
                )
    from scenarios import add_scenarios

    scenarios = add_scenarios(db, scenario_students, owner, policies, event)
    from operations import add_operations

    add_operations(db, policies, event)
    for i, (domain, title, finding, resolution) in enumerate(source_audit(u)):
        insert(
            db,
            "source_issue",
            id=f"audit-{i + 1}",
            severity="high",
            domain=domain,
            title=title,
            finding=finding,
            resolution=resolution,
            intentional=0,
        )
    for i, (domain, title, finding) in enumerate(
        [
            (
                "communications",
                "Stale advice is evidence, not authority",
                "A delivered message quotes an old hold-release promise; the active operating policy and ledger prevail.",
            ),
            (
                "time",
                "Late-arriving grade correction",
                "The grade is effective 7 May but not known until 4 September. Historical answers must use recorded_at.",
            ),
            (
                "staff",
                "Owner on leave",
                "Case ownership remains stable while the explicit covering adviser handles time-sensitive requests.",
            ),
            (
                "privacy",
                "Revoked parent authorization",
                "A known delegate with revoked billing consent may not receive a balance or transcript.",
            ),
        ]
    ):
        insert(
            db,
            "source_issue",
            id=f"intentional-{i + 1}",
            severity="info",
            domain=domain,
            title=title,
            finding=finding,
            resolution="Retained and linked to an evaluator scenario.",
            intentional=1,
        )
    from product_world import add_product_world

    add_product_world(db, CLOCK)
    db.commit()
    from validate import validate

    errors = validate(db)
    if errors:
        db.close()
        raise ValueError("\n".join(errors[:30]))
    counts = {
        r[0]: db.execute(f'SELECT count(*) FROM "{r[0]}"').fetchone()[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    }
    db.execute("VACUUM")
    db.close()
    pending.replace(target)
    manifest = dict(
        version="aster-world-v3",
        seed=seed,
        clock=CLOCK,
        source_hashes=hashes,
        counts=counts,
        validation_errors=errors,
        scenarios=len(scenarios),
        database_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    from product_content import publications

    (output / "campus-content.json").write_text(
        json.dumps({"clubs": publications()}, indent=2) + "\n"
    )
    (output / "oracle.json").write_text(json.dumps(scenarios, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in manifest.items() if k != "source_hashes"}, indent=2
        )
    )
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    build(args.output, args.seed)
