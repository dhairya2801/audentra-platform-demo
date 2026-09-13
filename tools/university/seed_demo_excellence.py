"""Add the handcrafted Wren/Camila demo once to an imported local vNext world.

Deterministic IDs and an atomic, versioned marker make retries harmless. This is
seed input, never a runtime store. Rebuild against a fresh imported database to
reset the story; running this again deliberately preserves subsequent UI edits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID, NAMESPACE_URL, uuid5

import asyncpg

TENANT = "00000000-0000-7000-8000-000000000003"
STUDENT = "ac2fa509-b4e3-402d-900b-ffb8440fc430"
STAFF = "01973261-954a-5019-8e9e-24a699abea7b"
STAFF_WORLD = "55ff7e408818cef52c5c5bb8"
VERSION = "demo-excellence-v1"
STAMP = "2026-09-08T15:00:00Z"


def identity(key):
    return str(uuid5(NAMESPACE_URL, f"audentra:{VERSION}:{key}"))


def history_entity(key):
    return {
        "returned": "ac575cdd711ad48bae6b1403",
        "pell": identity("pell-award"),
        "plan": identity("agreement"),
        "support": identity("support-case"),
    }[key]


async def seed_history_links(db):
    marker = VERSION + "-history-links"
    if await db.fetchval(
        "SELECT value FROM university.meta WHERE tenant_id=$1 AND key=$2", UUID(TENANT), marker
    ):
        return
    for key in ("returned", "pell", "plan", "support"):
        await db.execute(
            "UPDATE university.event SET entity_id=$3 WHERE tenant_id=$1 AND id=$2 AND actor='demo_persona_seed'",
            UUID(TENANT),
            identity("history-" + key),
            history_entity(key),
        )
    await db.execute(
        "INSERT INTO university.meta (tenant_id,key,value) VALUES ($1,$2,'complete')",
        UUID(TENANT),
        marker,
    )


async def seed_booking_blocks(db):
    """Mirror this persona's four meetings into the existing booking authority once."""
    marker = VERSION + "-booking-blocks"
    if await db.fetchval(
        "SELECT value FROM university.meta WHERE tenant_id=$1 AND key=$2", UUID(TENANT), marker
    ):
        return
    for i in range(4):
        await db.execute(
            "INSERT INTO public.staff_time_off "
            "(id,tenant_id,staff_member_id,starts_at,ends_at,kind,note,blocks_bookings) "
            "SELECT $1,$2,$3,starts_at::timestamptz,ends_at::timestamptz,'blocked',title,true "
            "FROM university.staff_calendar_event WHERE tenant_id=$2 AND id=$4 "
            "ON CONFLICT (id) DO NOTHING",
            UUID(identity(f"meeting-booking-block-{i}")),
            UUID(TENANT),
            UUID(STAFF),
            identity(f"meeting-{i}"),
        )
    await db.execute(
        "INSERT INTO university.meta (tenant_id,key,value) VALUES ($1,$2,'complete')",
        UUID(TENANT),
        marker,
    )


async def seed(url):
    parsed = urlparse(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.startswith(
        "/audentra_university"
    ):
        raise ValueError("Choose an explicit local audentra_university database")
    db = await asyncpg.connect(url)
    try:
        async with db.transaction():
            await db.execute("SELECT set_config('audentra.tenant_id',$1,true)", TENANT)
            await db.execute("SELECT pg_advisory_xact_lock(hashtext($1))", VERSION)
            if await db.fetchval(
                "SELECT value FROM university.meta WHERE tenant_id=$1 AND key=$2",
                UUID(TENANT),
                VERSION,
            ):
                await seed_booking_blocks(db)
                await seed_history_links(db)
                print("Demo already seeded; preserving all subsequent edits.")
                return
            assert (
                await db.fetchval(
                    "SELECT external_ref FROM university.student WHERE tenant_id=$1 AND id=$2",
                    UUID(TENANT),
                    STUDENT,
                )
                == "SYN-000000"
            )
            if not await db.fetchval(
                "SELECT 1 FROM university.rate_catalog WHERE tenant_id=$1 AND id='health-insurance'",
                UUID(TENANT),
            ):
                raise ValueError(
                    "Import the canonical product catalog with import_product_runtime.py first"
                )
            types = {
                (r["table_schema"] + "." + r["table_name"], r["column_name"]): r["data_type"]
                for r in await db.fetch(
                    "SELECT table_schema,table_name,column_name,data_type FROM information_schema.columns WHERE table_schema IN ('public','university')"
                )
            }

            async def add(table, **row):
                row = {"tenant_id": TENANT, **row}
                values = []
                for key, value in row.items():
                    kind = types[table, key]
                    if value is not None:
                        if kind == "uuid":
                            value = UUID(value)
                        elif kind == "timestamp with time zone":
                            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                        elif kind in ("jsonb", "json") and not isinstance(value, str):
                            value = json.dumps(value)
                    values.append(value)
                await db.execute(
                    f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('$' + str(i + 1) for i in range(len(row)))})",
                    *values,
                )

            # Retain the original ledger. The settled family ACH was returned;
            # a positive reversal exactly cancels its earlier posted credit.
            await db.execute(
                "UPDATE university.payment SET status='reversed' WHERE tenant_id=$1 AND id='ac575cdd711ad48bae6b1403' AND student_id=$2",
                UUID(TENANT),
                STUDENT,
            )
            await add(
                "university.ledger",
                id=identity("returned-family-credit"),
                student_id=STUDENT,
                term_id="2026FA",
                kind="reversal",
                amount_cents=1150000,
                posted_at="2026-09-07T14:00:00Z",
                payment_id="ac575cdd711ad48bae6b1403",
                reverses_id="71bc313ee15b3472f9e7315b",
                description="Returned family ACH — original semester credit reversed",
            )
            for key, cents, description in [
                ("insurance", 185000, "Student health insurance — annual coverage"),
                ("agreement-fee", 4500, "Payment plan enrollment fee — Fall semester"),
            ]:
                await add(
                    "university.ledger",
                    id=identity(key),
                    student_id=STUDENT,
                    term_id="2026FA",
                    kind="charge",
                    amount_cents=cents,
                    posted_at="2026-09-07T15:00:00Z",
                    due_at="2026-09-15T21:00:00Z",
                    description=description,
                )
            # Loan is a real fund/award/disbursement in this synthetic institution;
            # no unverified personalized federal rate is invented.
            await add(
                "university.fund",
                id="DEMO-SUB",
                name="Direct Subsidized Loan",
                source="federal_loan",
                annual_cap_cents=350000,
                posts_to_account=1,
                international_eligible=0,
                policy_id="v3-award-packaging@2026.1",
            )
            for key, fund, offer, accepted, status in [
                ("pell", "PELL", 739500, 739500, "accepted"),
                ("loan", "DEMO-SUB", 350000, 350000, "accepted"),
                ("merit", "MERIT", 200000, 0, "offered"),
            ]:
                await add(
                    "university.award",
                    id=identity(key + "-award"),
                    student_id=STUDENT,
                    fund_id=fund,
                    aid_year="2026-2027",
                    offered_cents=offer,
                    accepted_cents=accepted,
                    status=status,
                )
            for key, cents, state, scheduled, posted, reason in [
                (
                    "pell",
                    369750,
                    "posted",
                    "2026-09-04T14:00:00Z",
                    "2026-09-07T15:30:00Z",
                    None,
                ),
                (
                    "loan",
                    175000,
                    "held",
                    "2026-09-15T14:00:00Z",
                    None,
                    "Loan entrance counseling and signed promissory note remain incomplete.",
                ),
            ]:
                await add(
                    "university.disbursement",
                    id=identity(key + "-fall"),
                    award_id=identity(key + "-award"),
                    term_id="2026FA",
                    amount_cents=cents,
                    status=state,
                    scheduled_at=scheduled,
                    posted_at=posted,
                    reason=reason,
                )
                if posted:
                    await add(
                        "university.ledger",
                        id=identity(key + "-credit"),
                        student_id=STUDENT,
                        term_id="2026FA",
                        kind="aid",
                        amount_cents=-cents,
                        posted_at=posted,
                        disbursement_id=identity(key + "-fall"),
                        description="Federal Pell Grant — Fall disbursement",
                    )
            for key, cents, state in [
                ("replacement", 200000, "posted"),
                ("installment", 198250, "pending"),
                ("failed-card", 75000, "failed"),
                ("second-installment", 50000, "failed"),
            ]:
                await add(
                    "university.payment",
                    id=identity(key),
                    student_id=STUDENT,
                    term_id="2026FA",
                    amount_cents=cents,
                    status=state,
                    submitted_at="2026-09-08T13:00:00Z",
                    settled_at="2026-09-08T14:00:00Z" if state == "posted" else None,
                    method="bank_transfer" if state != "failed" else "card",
                    idempotency_key=VERSION + key,
                )
                if state == "posted":
                    await add(
                        "university.ledger",
                        id=identity(key + "-credit"),
                        student_id=STUDENT,
                        term_id="2026FA",
                        kind="payment",
                        amount_cents=-cents,
                        posted_at="2026-09-08T14:00:00Z",
                        payment_id=identity(key),
                        description="Replacement family payment — settled",
                    )
            await add(
                "university.payment_agreement",
                id=identity("agreement"),
                student_id=STUDENT,
                term_id="2026FA",
                principal_cents=590250,
                fee_cents=4500,
                status="active",
                signed_at="2026-09-08T12:00:00Z",
                version=1,
                terms_policy_id="billing-and-payment-policy@2026.1",
            )
            for i, day in enumerate(["2026-09-15", "2026-10-15", "2026-11-15"]):
                await add(
                    "university.payment_installment",
                    id=identity(f"installment-{i}"),
                    agreement_id=identity("agreement"),
                    amount_cents=198250,
                    due_at=day + "T21:00:00Z",
                    payment_id=identity("installment") if i == 0 else None,
                )
            await add(
                "university.insurance_coverage",
                id=identity("coverage"),
                student_id=STUDENT,
                term_id="2026FA",
                catalog_id="health-insurance",
                status="waiver_submitted",
                version=1,
                updated_at=STAMP,
            )
            inputs = {
                "booksCents": 60000,
                "transportCents": 35000,
                "personalCents": 40000,
                "rentCents": 0,
                "groceriesCents": 40000,
                "otherExpensesCents": 0,
                "savingsCents": 150000,
                "familyContributionCents": 250000,
                "employmentIncomeCents": 180000,
                "otherIncomeCents": 0,
            }
            await add(
                "university.financial_plan_input",
                student_id=STUDENT,
                term_id="2026FA",
                version=1,
                inputs_json=json.dumps(inputs, sort_keys=True),
                updated_at=STAMP,
                provenance="student_entered",
            )
            await add(
                "university.financial_scenario",
                id=identity("commuter-scenario"),
                student_id=STUDENT,
                term_id="2026FA",
                name="Compare next term commuting",
                inputs_json=json.dumps(
                    {**inputs, "rentCents": 240000, "groceriesCents": 100000},
                    sort_keys=True,
                ),
                version=1,
                created_at=STAMP,
                updated_at=STAMP,
            )
            await add(
                "university.hold",
                id=identity("billing-hold"),
                student_id=STUDENT,
                term_id="2026FA",
                kind="financial",
                office_id="SA",
                blocks_registration=1,
                placed_at="2026-09-07T16:00:00Z",
                reason="Returned family ACH restored an unpaid balance. Student Accounts must review the signed plan and settlement before releasing this hold.",
                policy_id="registration-holds-policy@2026.1",
                version=1,
            )
            await db.execute(
                "UPDATE public.student_profile SET pronouns='they/them',communication_preference='email',version=version+1 WHERE tenant_id=$1 AND student_id=$2",
                UUID(TENANT),
                UUID(STUDENT),
            )
            # Director takes the primary adviser handoff; preserve the former assignment.
            await db.execute(
                "UPDATE university.assignment SET ends_at='2026-09-07T16:00:00Z' WHERE tenant_id=$1 AND student_id=$2 AND role='academic_adviser' AND ends_at IS NULL",
                UUID(TENANT),
                STUDENT,
            )
            await db.execute(
                "UPDATE public.student_staff_assignment SET ended_at='2026-09-07T16:00:00Z' WHERE tenant_id=$1 AND student_id=$2 AND role='primary_advisor' AND ended_at IS NULL",
                UUID(TENANT),
                UUID(STUDENT),
            )
            await add(
                "university.assignment",
                id=identity("case-manager"),
                student_id=STUDENT,
                staff_id=STAFF_WORLD,
                role="academic_adviser",
                starts_at="2026-09-07T16:00:00Z",
                reason="Coordinating the returned payment, loan requirements and semester plan with the existing advisers.",
            )
            await add(
                "public.student_staff_assignment",
                id=identity("case-manager-public"),
                student_id=STUDENT,
                staff_member_id=STAFF,
                role="primary_advisor",
                assigned_at="2026-09-07T16:00:00Z",
                source="manual",
                note="Handcrafted demo: cross-office semester support coordinator.",
            )
            for i, (course, credits) in enumerate([("hist-110", 3), ("psy-101", 3)]):
                if await db.fetchval(
                    "SELECT 1 FROM university.course WHERE tenant_id=$1 AND id=$2",
                    UUID(TENANT),
                    course,
                ):
                    await add(
                        "university.transfer_credit",
                        id=identity(f"dual-enrollment-{i}"),
                        student_id=STUDENT,
                        course_id=course,
                        institution="Aster Summer Bridge",
                        credits=credits,
                        grade="A",
                        status="accepted",
                        received_at="2026-08-01T14:00:00Z",
                        evaluated_at="2026-08-12T14:00:00Z",
                        evaluator_id=STAFF_WORLD,
                    )
            # A small, explicit cross-office service case, not a new workflow engine.
            await add(
                "university.workflow",
                id=identity("support-case"),
                student_id=STUDENT,
                title="Wren · Fall funding and registration support",
                kind="verification",
                office_id="FA",
                owner_id=STAFF_WORLD,
                status="open",
                opened_at="2026-09-07T16:00:00Z",
                due_at="2026-09-15T21:00:00Z",
                policy_id="v3-award-packaging@2026.1",
                version=1,
            )
            for key, title, state, evidence in [
                (
                    "plan",
                    "Payment agreement signed",
                    "complete",
                    "Signed agreement recorded on September 8; settlement and hold release remain separate.",
                ),
                ("counseling", "Complete loan entrance counseling", "ready", None),
                ("note", "Sign the loan promissory note", "ready", None),
                ("release", "Review loan disbursement release", "blocked", None),
            ]:
                await add(
                    "university.workflow_step",
                    id=identity("step-" + key),
                    workflow_id=identity("support-case"),
                    title=title,
                    office_id="FA",
                    status=state,
                    completed_at=STAMP if state == "complete" else None,
                    evidence=evidence,
                )
            for key in ["counseling", "note"]:
                await add(
                    "university.step_dependency",
                    step_id=identity("step-release"),
                    prerequisite_id=identity("step-" + key),
                )
            # Curated cards span the exact seven deployed projects. Only Wren has
            # a deeply enriched record; other students receive ordinary work.
            students = [STUDENT] + [
                r["id"]
                for r in await db.fetch(
                    "SELECT id FROM university.student WHERE tenant_id=$1 AND id<>$2 AND status='enrolled' ORDER BY external_ref LIMIT 8",
                    UUID(TENANT),
                    STUDENT,
                )
            ]
            for n, sid in enumerate(students[1:]):
                await db.execute(
                    "UPDATE public.student_staff_assignment SET ended_at=$3 WHERE tenant_id=$1 AND student_id=$2 AND role='primary_advisor' AND ended_at IS NULL",
                    UUID(TENANT),
                    UUID(sid),
                    datetime.fromisoformat("2026-09-07T16:00:00+00:00"),
                )
                await db.execute(
                    "UPDATE university.assignment SET ends_at='2026-09-07T16:00:00Z' WHERE tenant_id=$1 AND student_id=$2 AND role='academic_adviser' AND ends_at IS NULL",
                    UUID(TENANT),
                    sid,
                )
                await add(
                    "public.student_staff_assignment",
                    id=identity(f"caseload-{n}"),
                    student_id=sid,
                    staff_member_id=STAFF,
                    role="primary_advisor",
                    assigned_at=STAMP,
                    source="manual",
                    note="September support caseload handoff",
                )
                await add(
                    "university.assignment",
                    id=identity(f"caseload-world-{n}"),
                    student_id=sid,
                    staff_id=STAFF_WORLD,
                    role="academic_adviser",
                    starts_at=STAMP,
                    reason="September support caseload handoff",
                )
            projects = {
                "fa-docs": (
                    "Financial Aid",
                    "document_review",
                    [
                        "Review insurance waiver evidence",
                        "Review scholarship eligibility statement",
                        "Check tax transcript classification",
                        "Request corrected verification worksheet",
                        "Review household-size statement",
                    ],
                ),
                "fa-outreach": (
                    "Financial Aid",
                    "communication_response",
                    [
                        "Explain held loan and next steps",
                        "Follow up on unsigned aid offer",
                        "Schedule verification check-in",
                        "Confirm counseling appointment",
                        "Prepare scholarship reminder",
                    ],
                ),
                "fa-payments": (
                    "Student Accounts",
                    "enrollment_follow_up",
                    [
                        "Review pending first installment",
                        "Follow up on failed card attempt",
                        "Review returned family ACH",
                        "Confirm replacement payment posting",
                        "Review earlier unsuccessful deposit attempt",
                    ],
                ),
                "en-docs": (
                    "Registrar",
                    "document_review",
                    [
                        "Review fall enrollment verification letter",
                        "Check transfer credit evidence",
                        "Review name correction evidence",
                        "Request final graduation certificate",
                        "Review residency supporting statement",
                    ],
                ),
                "en-outreach": (
                    "Admissions",
                    "communication_response",
                    [
                        "Check in after the first week",
                        "Invite student to adviser office hours",
                        "Follow up on orientation question",
                        "Share registration support options",
                        "Confirm campus visit follow-up",
                    ],
                ),
                "en-requests": (
                    "Academic Advising Center",
                    "missing_information",
                    [
                        "Coordinate Wren’s semester support plan",
                        "Review course-planning question",
                        "Arrange adviser handoff",
                        "Clarify prerequisite review next step",
                        "Coordinate accessibility introduction",
                    ],
                ),
                "cl-housing": (
                    "Housing",
                    "missing_information",
                    [
                        "Discuss quiet-study housing preferences",
                        "Review roommate mediation request",
                        "Coordinate room-maintenance follow-up",
                        "Clarify spring housing renewal",
                        "Review residence accessibility request",
                    ],
                ),
            }
            for pindex, (project, (office, action, titles)) in enumerate(projects.items()):
                for i, title in enumerate(titles):
                    key = f"DEMO-{pindex * 5 + i + 101}"
                    wid = identity(key)
                    sid = (
                        STUDENT
                        if i == 0 or project == "fa-payments"
                        else students[(pindex + i) % 8 + 1]
                    )
                    state = (
                        ["in_progress", "todo", "blocked", "done", "todo"]
                        if project == "fa-payments"
                        else [
                            "todo",
                            "in_progress",
                            "blocked",
                            "follow_up_required",
                            "done",
                        ]
                    )[i]
                    description = "Coordinate the recorded next step with the student and responsible office. Operational progress does not settle a payment, decide a document or complete a formal case."
                    if i == 0 and project == "en-requests":
                        description = "Wren is taking 12 credits and lives in Alder Hall. Coordinate the returned family payment, held loan, signed installment plan and insurance waiver review with Student Accounts, Financial Aid and Student Health."
                    did = None
                    if "docs" in project:
                        did = identity(key + "-document")
                        dstatus = [
                            "under_review",
                            "processing",
                            "needs_resubmission",
                            "placeholder",
                            "accepted",
                        ][i]
                        category = (
                            "financial_aid"
                            if project == "fa-docs"
                            else "other"
                            if i == 0
                            else "transcript"
                        )
                        await add(
                            "public.document_record",
                            id=did,
                            student_id=sid,
                            file_name=title.removeprefix("Review ") + ".pdf",
                            mime_type="application/pdf",
                            size_bytes=1024,
                            category=category,
                            status=dstatus,
                            processing_mode="manual_review",
                            created_at="2026-09-07T14:00:00Z",
                            updated_at=STAMP,
                        )
                    if did and i == 0:
                        domain_category = (
                            "insurance_waiver"
                            if project == "fa-docs"
                            else "enrollment_verification"
                        )
                        domain_office = "SHS" if project == "fa-docs" else "REG"
                        await db.execute(
                            "UPDATE university.document SET category=$3,office_id=$4 WHERE tenant_id=$1 AND id=$2",
                            UUID(TENANT),
                            did,
                            domain_category,
                            domain_office,
                        )
                        if project == "fa-docs":
                            await db.execute(
                                "UPDATE university.insurance_coverage SET evidence_document_id=$3 WHERE tenant_id=$1 AND student_id=$2",
                                UUID(TENANT),
                                STUDENT,
                                did,
                            )
                    row = dict(
                        id=wid,
                        student_id=sid,
                        key=key,
                        title=title,
                        description=description,
                        status=state,
                        priority=["high", "medium", "urgent", "low", "medium"][i],
                        work_type="communication"
                        if "outreach" in project
                        else "document_review"
                        if did
                        else "enrollment",
                        component=office,
                        assignee_id=STAFF if i < 3 else None,
                        due_at=f"2026-09-{[14, 15, 11, 17, 8][i]:02d}T21:00:00Z",
                        created_at="2026-09-07T14:00:00Z",
                        updated_at=STAMP,
                        action_type=action,
                        source_type="document" if did else None,
                        source_id=did,
                    )
                    if state == "blocked":
                        row.update(
                            blocker_code="awaiting_dependency",
                            blocker_detail="Awaiting the next required evidence or responsible-office response.",
                        )
                    if state == "follow_up_required":
                        row.update(
                            follow_up_at="2026-09-17T15:00:00Z",
                            next_step="Check for the requested information and contact the student if still missing.",
                        )
                    if state == "done":
                        row.update(
                            outcome_code="staff_confirmed_complete",
                            resolution_code="resolved_by_staff",
                            completed_at=STAMP,
                        )
                    await add("public.staff_work_item", **row)
                    await add(
                        "public.staff_work_log",
                        id=identity(key + "-created"),
                        work_item_id=wid,
                        actor_type="staff",
                        actor_id=STAFF,
                        actor_name="Camila Abernathy",
                        action="created",
                        message="Assigned for the September student-support review.",
                        occurred_at="2026-09-07T14:00:00Z",
                    )
                    if project == "fa-payments":
                        pid = [
                            identity("installment"),
                            identity("failed-card"),
                            "ac575cdd711ad48bae6b1403",
                            identity("replacement"),
                            identity("second-installment"),
                        ][i]
                        await add(
                            "university.runtime_link",
                            kind="payment_work_item",
                            world_id=pid,
                            runtime_id=wid,
                        )
                    if i == 0 and project == "en-requests":
                        await add(
                            "university.runtime_link",
                            kind="workflow",
                            world_id=identity("support-case"),
                            runtime_id=wid,
                        )
                    if "outreach" in project and i < 2:
                        await add(
                            "public.staff_outreach_draft",
                            id=identity(key + "-draft"),
                            work_item_id=wid,
                            student_id=sid,
                            subject=title,
                            body="Hello, I would like to help you review your next step. Please reply with a good time to connect with your adviser. This is a saved draft and has not been sent.",
                            version=1,
                            status="draft",
                            created_by=STAFF,
                            updated_by=STAFF,
                            created_at=STAMP,
                            updated_at=STAMP,
                        )
            # Upcoming appointments and a compact leadership calendar, without
            # changing anybody else's availability or existing assignments.
            for i, (day, hour, title) in enumerate(
                [
                    (14, 14, "Wren · semester funding check-in"),
                    (15, 15, "Wren · academic and campus involvement plan"),
                ]
            ):
                start = f"2026-09-{day}T{hour}:00:00Z"
                end = f"2026-09-{day}T{hour}:30:00Z"
                await add(
                    "public.student_appointment",
                    id=identity(f"appointment-{i}"),
                    student_id=STUDENT,
                    type="academic_advising",
                    starts_at=start,
                    ends_at=end,
                    staff_member_id=STAFF,
                    status="scheduled",
                    modality="in_person",
                    location="Advising Centre, room 204",
                    booked_via="staff",
                    notes=title,
                )
            for i, (day, hour, title) in enumerate(
                [
                    (14, 13, "Student success team huddle"),
                    (14, 16, "Financial Aid and Student Accounts coordination"),
                    (15, 13, "Adviser caseload review"),
                    (16, 18, "Protected student follow-up time"),
                ]
            ):
                await add(
                    "university.staff_calendar_event",
                    id=identity(f"meeting-{i}"),
                    staff_id=STAFF_WORLD,
                    title=title,
                    starts_at=f"2026-09-{day}T{hour}:00:00Z",
                    ends_at=f"2026-09-{day}T{hour}:30:00Z",
                    location="Advising Centre, room 204",
                    blocks_bookings=1,
                )
            for i, (subject, body, sender) in enumerate(
                [
                    (
                        "Your semester support plan",
                        "Your first installment of $1,982.50 is pending. The $1,750 loan disbursement is held for entrance counseling and a signed promissory note. Your billing hold remains until Student Accounts reviews settlement and your agreement.",
                        "Camila Abernathy",
                    ),
                    (
                        "Insurance waiver received",
                        "Your waiver evidence is under review. The $1,850 insurance charge remains posted until an approved waiver and account adjustment are recorded.",
                        "Student Health Services",
                    ),
                    (
                        "Build your campus community",
                        "Explore Aster Robotics in the published club directory and the upcoming Family Weekend. Membership and attendance are your choice; this invitation does not register you.",
                        "Academic Advising Center",
                    ),
                ]
            ):
                await add(
                    "public.student_message",
                    id=identity(f"message-{i}"),
                    student_id=STUDENT,
                    subject=subject,
                    body=body,
                    sender_name=sender,
                    sent_at=STAMP,
                    kind="general",
                )
                if i == 0:
                    # Imported portal history has a real matching inbox message.
                    # This is neither a provider send nor a fabricated email receipt.
                    await add(
                        "public.staff_interaction",
                        id=identity("support-interaction"),
                        student_id=STUDENT,
                        work_item_id=identity("DEMO-106"),
                        objective=subject,
                        status="collecting",
                        selected_channel="portal",
                        created_by=STAFF,
                        request_key=identity("support-interaction-request"),
                        last_activity_at=STAMP,
                        created_at=STAMP,
                        updated_at=STAMP,
                    )
                    await add(
                        "public.communication_event",
                        id=identity("support-communication"),
                        student_id=STUDENT,
                        interaction_id=identity("support-interaction"),
                        channel="portal",
                        direction="outbound",
                        subject=subject,
                        body_excerpt=body,
                        resolution_status="resolved",
                        delivery_status="delivered",
                        source_type="demo_seed_portal_message",
                        source_id=identity("message-0"),
                        metadata={"provenance": VERSION, "studentMessageId": identity("message-0")},
                        occurred_at=STAMP,
                        created_at=STAMP,
                    )
            for key, entity, state, description in [
                (
                    "returned",
                    "payment",
                    "reversed",
                    "The original $11,500 family ACH was returned; its credit was reversed.",
                ),
                (
                    "pell",
                    "award",
                    "posted",
                    "Fall Pell Grant of $3,697.50 posted to the account.",
                ),
                (
                    "plan",
                    "payment_agreement",
                    "active",
                    "Signed a three-installment payment agreement; pending attempts are not settled credits.",
                ),
                (
                    "support",
                    "workflow",
                    "open",
                    "Camila Abernathy became the primary adviser after a recorded handoff.",
                ),
            ]:
                await add(
                    "university.event",
                    id=identity("history-" + key),
                    student_id=STUDENT,
                    entity_type=entity,
                    entity_id=history_entity(key),
                    effective_at=STAMP,
                    recorded_at=STAMP,
                    actor="demo_persona_seed",
                    to_state=state,
                    description=description,
                    visibility="student",
                    correlation_id=VERSION,
                )
            balance = await db.fetchval(
                "SELECT sum(amount_cents) FROM university.ledger WHERE tenant_id=$1 AND student_id=$2 AND term_id='2026FA'",
                UUID(TENANT),
                STUDENT,
            )
            assert balance == 769750, balance
            await seed_booking_blocks(db)
            await seed_history_links(db)
            await add(
                "university.meta",
                key=VERSION,
                value=json.dumps(
                    {
                        "student": STUDENT,
                        "staff": STAFF,
                        "balanceCents": balance,
                        "seedVersion": VERSION,
                    },
                    sort_keys=True,
                ),
            )
        print(
            f"Seeded Wren and Camila atomically. Posted balance: {balance} cents; 35 curated work items."
        )
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    asyncio.run(seed(parser.parse_args().database_url))
