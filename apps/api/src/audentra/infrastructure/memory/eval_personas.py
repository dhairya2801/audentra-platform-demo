"""Evaluation personas for the canonical Python Edward.

The Edward eval harness (tools/edward-eval) boots one in-memory API per
persona. Each persona is a student state ported from the legacy demo-api
seed personas — the *states* carry over (deposit unpaid, transcript under
review, aid finalized, …), not the demo constants. Where the platform has no
canonical record (registrar holds, room assignments, disbursement schedules)
the persona does not fake one: Edward's honest answer for that gap is exactly
what the evaluation should grade.

Dates are computed relative to now so deadline buckets (overdue / this week /
this month) are stable regardless of when the evaluation runs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from audentra.infrastructure.memory.store import DEMO_IDS, InMemoryPlatformStore

JsonDict = dict[str, Any]


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _requirement(
    code: str,
    title: str,
    *,
    status: str = "ready",
    blocking: bool = True,
    due_at: str | None = None,
    document_category: str | None = None,
    office: str = "Enrollment Services",
) -> JsonDict:
    return {
        "id": str(uuid4()),
        "slug": code.replace("_", "-"),
        "journeyId": DEMO_IDS["student_id"],
        "code": code,
        "title": title,
        "description": title,
        "status": status,
        "blocking": blocking,
        "priority": 0,
        "order": 10,
        "dueAt": due_at,
        "progressPercent": 100 if status in {"completed", "waived"} else 0,
        "submissionType": "document" if document_category else "form",
        "documentCategory": document_category,
        "responsibleOffice": office,
        "dependencyCodes": [],
    }


def _course(code: str, title: str, credits: int, level: int, prerequisites: list[str]) -> JsonDict:
    return {
        "id": str(uuid4()),
        "code": code,
        "title": title,
        "description": title,
        "credits": credits,
        "level": level,
        "prerequisites": prerequisites,
    }


def apply_accepted_student(store: InMemoryPlatformStore, *, now: datetime | None = None) -> None:
    """The baseline every persona builds on: offer accepted, checklist open."""

    moment = now or datetime.now(UTC)
    soon = _iso(moment + timedelta(days=15))
    later = _iso(moment + timedelta(days=25))

    store.dashboard["offer"]["status"] = "accepted"
    store.dashboard["offer"]["responseDeadline"] = _iso(moment + timedelta(days=10))[:10]
    store.dashboard["journey"] = {
        "id": str(uuid4()),
        "status": "in_progress",
        "completionPercent": 10,
        "nextAction": {
            "code": "enrollment_deposit",
            "label": "Pay your enrollment deposit",
            "href": "/payments",
            "kind": "pay_deposit",
        },
        "requirements": [],
    }
    store.requirements = [
        _requirement(
            "profile_verification",
            "Verify your profile",
            status="completed",
            blocking=True,
        ),
        _requirement(
            "enrollment_deposit",
            "Pay the enrollment deposit",
            due_at=_iso(moment + timedelta(days=10)),
        ),
        _requirement(
            "identity_document",
            "Upload an identity document",
            due_at=soon,
            document_category="identity",
        ),
        _requirement(
            "official_transcript",
            "Submit your official transcript",
            due_at=soon,
            document_category="transcript",
            office="Registrar",
        ),
        _requirement(
            "immunization_record",
            "Submit your immunization record",
            due_at=later,
            document_category="health",
            office="Health Services",
        ),
        _requirement(
            "financial_aid_verification",
            "Complete financial aid verification",
            # Aid completeness is not an enrollment/registration gate; it holds
            # the aid package open, not the checklist.
            blocking=False,
            due_at=later,
            office="Financial Aid",
        ),
        _requirement(
            "housing_preference",
            "Select your housing preference",
            status="blocked",
            blocking=False,
            due_at=later,
            office="Housing Office",
        ),
    ]
    store.documents = []
    store.appointments = []
    store.payments = []
    # Institution-wide campus-life data shared by every persona. The point of
    # the fixture is intent coverage: the same club list must serve discovery
    # ("what clubs are there?"), recommendation ("what should I join?"), and
    # comparison ("ACM or Robotics for ML?") without changing shape.
    store.campus_overrides = {
        "events": [
            {
                "title": "Welcome Week Kickoff",
                "startsAt": _iso(moment + timedelta(days=18)),
                "location": "Main Quad",
                "category": "orientation",
            },
            {
                "title": "Student Club Fair",
                "startsAt": _iso(moment + timedelta(days=21)),
                "location": "Student Center",
                "category": "involvement",
            },
            {
                "title": "Jazz Ensemble Auditions",
                "startsAt": _iso(moment + timedelta(days=24)),
                "location": "Music Hall",
                "category": "arts",
            },
        ],
        "clubs": [
            {
                "name": "ACM Student Chapter",
                "category": "academic",
                "description": (
                    "Computer science society: programming contests, tech talks, an "
                    "annual hackathon, and a machine-learning special interest group."
                ),
                "nextActivity": "Intro meeting during Welcome Week",
            },
            {
                "name": "Robotics Club",
                "category": "academic",
                "description": (
                    "Hands-on robot design and competition team covering embedded "
                    "programming and computer vision; beginners welcome."
                ),
                "nextActivity": "Build-night open house",
            },
            {
                "name": "Data Science Society",
                "category": "academic",
                "description": "Workshops on statistics, Python, and real datasets.",
                "nextActivity": "Kaggle night",
            },
            {
                "name": "University Jazz Ensemble",
                "category": "arts",
                "description": "Auditioned big band performing once per term.",
                "nextActivity": "Auditions",
            },
            {
                "name": "A Cappella Society",
                "category": "arts",
                "description": "Student-run vocal groups; no audition required to join.",
                "nextActivity": "Open sing",
            },
            {
                "name": "Outdoor Adventure Club",
                "category": "recreation",
                "description": "Weekend hikes, climbing trips, and gear rental.",
                "nextActivity": "Trailhead day hike",
            },
            {
                "name": "International Students Association",
                "category": "cultural",
                "description": "Community and events for international and exchange students.",
                "nextActivity": "Welcome dinner",
            },
            {
                "name": "Intramural Soccer",
                "category": "athletics",
                "description": "Co-ed recreational league, all skill levels.",
                "nextActivity": "Team registration",
            },
        ],
    }
    # A small but non-trivial academic plan: one eligible course, one course
    # blocked on a prerequisite, and one suggested exemption, so prerequisite
    # and exemption questions have canonical answers. Data the platform does
    # not model (registration windows, section times) stays absent on purpose.
    store.academics_overrides = {
        "exemptionRecommendations": [
            {
                "targetCourseCode": "ENG 110",
                "reason": "AP English credit on file suggests an exemption request.",
            }
        ],
        "plan": [
            {
                "course": _course("CS 101", "Programming Fundamentals", 4, 100, []),
                "category": "major_core",
                "recommendedTerm": 1,
                "status": "eligible",
                "satisfiedPrerequisiteCodes": [],
                "missingPrerequisiteCodes": [],
            },
            {
                "course": _course("MATH 140", "Calculus I", 4, 100, []),
                "category": "major_core",
                "recommendedTerm": 1,
                "status": "eligible",
                "satisfiedPrerequisiteCodes": [],
                "missingPrerequisiteCodes": [],
            },
            {
                "course": _course("ENG 110", "College Writing", 3, 100, []),
                "category": "general_education",
                "recommendedTerm": 1,
                "status": "eligible",
                "satisfiedPrerequisiteCodes": [],
                "missingPrerequisiteCodes": [],
            },
            {
                "course": _course("CS 201", "Data Structures", 4, 200, ["CS 101"]),
                "category": "major_core",
                "recommendedTerm": 2,
                # Plain-English status: course status strings render verbatim
                # in the academic-plan table.
                "status": "blocked",
                "satisfiedPrerequisiteCodes": [],
                "missingPrerequisiteCodes": ["CS 101"],
            },
        ],
    }
    store.financial_overrides = {
        "academicYear": "2027-2028",
        "awards": [
            {
                "name": "Federal Pell Grant",
                "type": "grant",
                "status": "accepted",
                "offeredAmountCents": 739_500,
                "acceptedAmountCents": 739_500,
                "requiresAction": False,
            },
            {
                "name": "Achievement Scholarship",
                "type": "scholarship",
                "status": "accepted",
                "offeredAmountCents": 800_000,
                "acceptedAmountCents": 800_000,
                "requiresAction": False,
            },
            {
                "name": "Direct Subsidized Loan",
                "type": "loan",
                "status": "offered",
                "offeredAmountCents": 350_000,
                "acceptedAmountCents": 0,
                "requiresAction": True,
            },
            {
                "name": "Federal Work-Study",
                "type": "work_study",
                "status": "pending",
                "offeredAmountCents": 250_000,
                "acceptedAmountCents": 0,
                "requiresAction": False,
            },
        ],
        "requiredDocuments": [
            {
                "code": "fafsa",
                "title": "FAFSA",
                "status": "verified",
                "dueAt": None,
                "href": "/financials",
            },
            {
                "code": "verification_worksheet",
                "title": "Verification worksheet",
                "status": "action_required",
                "dueAt": _iso(moment + timedelta(days=20)),
                "href": "/financials",
            },
            {
                "code": "award_acceptance",
                "title": "Award acceptance",
                "status": "not_started",
                "dueAt": _iso(moment + timedelta(days=30)),
                "href": "/financials",
            },
        ],
    }


def _complete(store: InMemoryPlatformStore, code: str) -> None:
    for requirement in store.requirements:
        if requirement["code"] == code:
            requirement["status"] = "completed"
            requirement["progressPercent"] = 100
    # Completing the deposit clears the derived deposit blocker everywhere.
    # The checklist step is not the receipt: in the real platform the step
    # completes *because* a payment posted, so the fixture records the payment
    # too. Setting a `depositPaid` flag on the dashboard instead — which the
    # Postgres projection never emits — is what let evals pass against state
    # production could not produce.
    if code == "enrollment_deposit":
        if not any(
            item.get("type") == "enrollment_deposit" and item.get("status") == "succeeded"
            for item in store.payments
        ):
            store.payments.append(
                {
                    "id": str(uuid4()),
                    "type": "enrollment_deposit",
                    "offerId": store.dashboard["offer"]["id"],
                    "amountCents": store.dashboard["offer"]["depositAmountCents"],
                    "status": "succeeded",
                    "processor": "dummy",
                    "processorReference": "fixture_deposit",
                    "createdAt": store.dashboard["generatedAt"],
                }
            )
        store.dashboard["journey"]["nextAction"] = {
            "code": "identity_document",
            "label": "Upload an identity document",
            "href": "/enrollment/requirements/identity-document",
        }
    # The housing step unblocks once the deposit is in.
    for requirement in store.requirements:
        if requirement["code"] == "housing_preference" and requirement["status"] == "blocked":
            deposit_done = any(
                item["code"] == "enrollment_deposit" and item["status"] == "completed"
                for item in store.requirements
            )
            if deposit_done:
                requirement["status"] = "ready"


def _set_due(store: InMemoryPlatformStore, code: str, due_at: str) -> None:
    for requirement in store.requirements:
        if requirement["code"] == code:
            requirement["dueAt"] = due_at


def _aid_documents(store: InMemoryPlatformStore) -> list[JsonDict]:
    documents = store.financial_overrides.setdefault("requiredDocuments", [])
    assert isinstance(documents, list)
    return documents


def _persona_new_admit(store: InMemoryPlatformStore, now: datetime) -> None:
    """Baseline exactly: deposit unpaid, everything open."""


def _persona_deposit_posted(store: InMemoryPlatformStore, now: datetime) -> None:
    _complete(store, "enrollment_deposit")
    store.payments = [
        {
            "id": str(uuid4()),
            "type": "enrollment_deposit",
            "offerId": store.dashboard["offer"]["id"],
            "amountCents": 50_000,
            "status": "succeeded",
            "createdAt": _iso(now - timedelta(days=2)),
        }
    ]


def _persona_payment_pending(store: InMemoryPlatformStore, now: datetime) -> None:
    store.payments = [
        {
            "id": str(uuid4()),
            "type": "enrollment_deposit",
            "offerId": store.dashboard["offer"]["id"],
            "amountCents": 50_000,
            "status": "pending",
            "createdAt": _iso(now - timedelta(hours=4)),
        }
    ]


def _persona_nearly_complete(store: InMemoryPlatformStore, now: datetime) -> None:
    for code in (
        "enrollment_deposit",
        "identity_document",
        "official_transcript",
        "immunization_record",
    ):
        _complete(store, code)
    store.payments = [
        {
            "id": str(uuid4()),
            "type": "enrollment_deposit",
            "offerId": store.dashboard["offer"]["id"],
            "amountCents": 50_000,
            "status": "succeeded",
            "createdAt": _iso(now - timedelta(days=9)),
        }
    ]
    store.appointments = [
        {
            "id": str(uuid4()),
            "type": "enrollment_support",
            "startsAt": _iso(now - timedelta(days=3)),
            "notes": "Advising session",
            "status": "completed",
            "createdAt": _iso(now - timedelta(days=10)),
        }
    ]


def _persona_official_hold(store: InMemoryPlatformStore, now: datetime) -> None:
    # The platform operates no registrar hold system, so "on_hold" journeys
    # cannot exist canonically. This persona keeps the deposit paid and the
    # rest open; Edward's honest answer is that no official hold exists.
    _persona_deposit_posted(store, now)


def _persona_deadline_passed(store: InMemoryPlatformStore, now: datetime) -> None:
    _set_due(store, "enrollment_deposit", _iso(now - timedelta(days=35)))
    _set_due(store, "identity_document", _iso(now - timedelta(days=30)))
    _set_due(store, "official_transcript", _iso(now - timedelta(days=28)))


def _persona_advising_booked(store: InMemoryPlatformStore, now: datetime) -> None:
    _persona_deposit_posted(store, now)
    store.appointments = [
        {
            "id": str(uuid4()),
            "type": "enrollment_support",
            "startsAt": _iso(now + timedelta(days=13)),
            "notes": "Advising session, Room 210",
            "status": "scheduled",
            "createdAt": _iso(now - timedelta(days=1)),
        }
    ]


def _persona_housing_assigned(store: InMemoryPlatformStore, now: datetime) -> None:
    # The platform records a housing preference, not a room assignment; the
    # honest state is a completed housing step with a selected preference.
    _persona_deposit_posted(store, now)
    _complete(store, "housing_preference")
    store.onboarding["data"].update(
        {
            "housingPreference": "on_campus",
            "housingResidenceOption": "residence_hall",
            "housingRoomType": "double",
        }
    )


def _persona_fafsa_missing(store: InMemoryPlatformStore, now: datetime) -> None:
    for document in _aid_documents(store):
        if document["code"] == "fafsa":
            document["status"] = "not_started"
    store.financial_overrides["awards"] = []
    store.financial_overrides["acceptedAidCents"] = 0
    store.financial_overrides["pendingAidCents"] = 0


def _persona_aid_verification_outstanding(store: InMemoryPlatformStore, now: datetime) -> None:
    """The baseline aid state already has the worksheet outstanding."""


def _persona_aid_finalized(store: InMemoryPlatformStore, now: datetime) -> None:
    for document in _aid_documents(store):
        document["status"] = "verified"
    awards = store.financial_overrides.get("awards", [])
    assert isinstance(awards, list)
    for award in awards:
        award["status"] = "accepted"
        award["acceptedAmountCents"] = award["offeredAmountCents"]
        award["requiresAction"] = False
    store.financial_overrides["acceptedAidCents"] = sum(
        award["acceptedAmountCents"] for award in awards
    )
    store.financial_overrides["pendingAidCents"] = 0
    _complete(store, "financial_aid_verification")


def _persona_aid_ready_to_disburse(store: InMemoryPlatformStore, now: datetime) -> None:
    _persona_aid_finalized(store, now)
    _persona_deposit_posted(store, now)


def _persona_aid_refund_due(store: InMemoryPlatformStore, now: datetime) -> None:
    _persona_aid_ready_to_disburse(store, now)
    store.financial_overrides["costOfAttendanceCents"] = 1_200_000
    accepted = store.financial_overrides.get("acceptedAidCents", 0)
    assert isinstance(accepted, int)
    store.financial_overrides["remainingBalanceCents"] = 1_200_000 - accepted


def _persona_no_aid(store: InMemoryPlatformStore, now: datetime) -> None:
    store.financial_overrides["awards"] = []
    store.financial_overrides["requiredDocuments"] = []
    store.financial_overrides["acceptedAidCents"] = 0
    store.financial_overrides["pendingAidCents"] = 0


def _persona_transcript_under_review(store: InMemoryPlatformStore, now: datetime) -> None:
    transcript = next(item for item in store.requirements if item["code"] == "official_transcript")
    transcript["status"] = "submitted"
    store.documents = [
        {
            "id": str(uuid4()),
            "fileName": "official-transcript.pdf",
            "mimeType": "application/pdf",
            "sizeBytes": 4096,
            "category": "transcript",
            "status": "under_review",
            "requirementId": transcript["id"],
            "createdAt": _iso(now - timedelta(days=1)),
        }
    ]


def _persona_document_needs_resubmission(store: InMemoryPlatformStore, now: datetime) -> None:
    _persona_transcript_under_review(store, now)
    store.documents[0]["status"] = "needs_resubmission"
    store.documents[0]["extraction"] = {"status": "failed"}
    for requirement in store.requirements:
        if requirement["code"] == "official_transcript":
            requirement["status"] = "rejected"


# Developer-facing labels for persona pickers (Edward Lab, eval tooling).
# Honest about platform gaps: no registrar holds or room assignments exist.
PERSONA_LABELS: dict[str, str] = {
    "new_admit": "New admit — deposit unpaid, housing blocked",
    "deposit_posted": "Deposit posted",
    "payment_pending": "Deposit paid but not posted",
    "nearly_complete": "Nearly complete — housing step left",
    "official_hold": "Deposit posted (no hold system exists)",
    "deadline_passed": "Multiple blockers — deadlines missed",
    "advising_booked": "Advising appointment booked",
    "housing_assigned": "Housing preference completed",
    "fafsa_missing": "No FAFSA on file",
    "aid_verification_outstanding": "Financial aid incomplete — verification open",
    "aid_finalized": "Financial aid finalized",
    "aid_ready_to_disburse": "Aid finalized and deposit posted",
    "aid_refund_due": "Aid exceeds cost — refund due",
    "no_aid": "No financial aid at all",
    "transcript_under_review": "Transcript under review",
    "document_needs_resubmission": "Transcript returned for resubmission",
}

PERSONAS: dict[str, Callable[[InMemoryPlatformStore, datetime], None]] = {
    "new_admit": _persona_new_admit,
    "deposit_posted": _persona_deposit_posted,
    "payment_pending": _persona_payment_pending,
    "nearly_complete": _persona_nearly_complete,
    "official_hold": _persona_official_hold,
    "deadline_passed": _persona_deadline_passed,
    "advising_booked": _persona_advising_booked,
    "housing_assigned": _persona_housing_assigned,
    "fafsa_missing": _persona_fafsa_missing,
    "aid_verification_outstanding": _persona_aid_verification_outstanding,
    "aid_finalized": _persona_aid_finalized,
    "aid_ready_to_disburse": _persona_aid_ready_to_disburse,
    "aid_refund_due": _persona_aid_refund_due,
    "no_aid": _persona_no_aid,
    "transcript_under_review": _persona_transcript_under_review,
    "document_needs_resubmission": _persona_document_needs_resubmission,
}


def apply_persona(store: InMemoryPlatformStore, name: str, *, now: datetime | None = None) -> None:
    if name not in PERSONAS:
        raise ValueError(
            f"Unknown eval persona {name!r}; known personas: {', '.join(sorted(PERSONAS))}"
        )
    moment = now or datetime.now(UTC)
    apply_accepted_student(store, now=moment)
    PERSONAS[name](store, moment)
