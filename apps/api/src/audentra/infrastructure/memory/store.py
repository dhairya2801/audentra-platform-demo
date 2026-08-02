"""A tenant-safe in-memory behavioral replica of the Nest platform stores."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError
from audentra.domain.documents import can_retry_extraction
from audentra.domain.onboarding import (
    ONBOARDING_STEPS,
    is_skippable_onboarding_step,
    validate_onboarding_step,
)

DEMO_IDS: dict[str, str] = {
    "tenant_id": "00000000-0000-7000-8000-000000000001",
    "person_id": "00000000-0000-7000-8000-000000000100",
    "student_id": "00000000-0000-7000-8000-000000000101",
    "program_id": "00000000-0000-7000-8000-000000000130",
    "offer_id": "00000000-0000-7000-8000-000000000201",
    "welcome_message_id": "00000000-0000-7000-8000-000000000501",
    "reminder_message_id": "00000000-0000-7000-8000-000000000502",
    "sample_document_id": "00000000-0000-7000-8000-000000000601",
    "sample_appointment_id": "00000000-0000-7000-8000-000000000701",
    "help_getting_started_id": "00000000-0000-7000-8000-000000000801",
    "staff_advisor_id": "00000000-0000-7000-8000-000000000901",
    "staff_reviewer_id": "00000000-0000-7000-8000-000000000902",
    "staff_onboarding_work_item_id": "00000000-0000-7000-8000-000000000911",
    "staff_outreach_work_item_id": "00000000-0000-7000-8000-000000000912",
    "staff_onboarding_log_id": "00000000-0000-7000-8000-000000000921",
    "staff_outreach_log_id": "00000000-0000-7000-8000-000000000922",
}

FIXED_TIME = "2026-07-24T12:00:00.000Z"
INITIAL_TIME = "2026-07-24T00:00:00.000Z"
T = TypeVar("T")


def _clone(value: T) -> T:
    return deepcopy(value)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _requirement_code(identifier: str) -> str:
    mapping = {
        "profile-verification": "profile_verification",
        "identity-document-upload": "identity_document",
        "transcript-upload": "official_transcript",
        "financial-aid-verification": "financial_aid_verification",
        "immunization-upload": "immunization_record",
        "enrollment-deposit": "enrollment_deposit",
    }
    return mapping.get(identifier, identifier.lower().replace("-", "_"))


def _requirement_slug(code: str) -> str:
    mapping = {
        "profile_verification": "profile-verification",
        "identity_document": "identity-document-upload",
        "official_transcript": "transcript-upload",
        "financial_aid_verification": "financial-aid-verification",
        "immunization_record": "immunization-upload",
        "enrollment_deposit": "enrollment-deposit",
    }
    return mapping.get(code, code.lower().replace("_", "-"))


@dataclass(slots=True)
class Effects:
    journeys: int = 0
    requirement_sets: int = 0
    audit_events: int = 0
    outbox_events: int = 0


class InMemoryPlatformStore:
    """Stateful repository with the same observable rules as the Nest test store."""

    def __init__(self) -> None:
        self.effects = Effects()
        self.accepted_response: dict[str, Any] | None = None
        self.offer_replays: dict[str, tuple[str, dict[str, Any]]] = {}
        self.help_request_replays: dict[str, tuple[dict[str, str], dict[str, Any]]] = {}
        self.idempotency: dict[str, Any] = {}
        self.activity_event_ids: set[str] = set()
        self.document_storage_keys: dict[str, str] = {}
        self.onboarding: dict[str, Any] = {
            "studentId": DEMO_IDS["student_id"],
            "status": "in_progress",
            "currentStep": "offer",
            "completedSteps": [],
            "data": {},
            "version": 1,
            "completedAt": None,
            "updatedAt": INITIAL_TIME,
        }
        self.requirements: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = [
            {
                "id": DEMO_IDS["welcome_message_id"],
                "subject": "Welcome to your enrollment portal",
                "body": "Your portal keeps every enrollment action in one place.",
                "senderName": "Enrollment Services",
                "sentAt": "2026-07-24T09:00:00.000Z",
                "readAt": None,
            },
            {
                "id": DEMO_IDS["reminder_message_id"],
                "subject": "Your next enrollment step",
                "body": "Review your admission offer and continue when you are ready.",
                "senderName": "Admissions Office",
                "sentAt": "2026-07-24T10:00:00.000Z",
                "readAt": None,
            },
        ]
        self.documents: list[dict[str, Any]] = [
            {
                "id": DEMO_IDS["sample_document_id"],
                "fileName": "identity-document-placeholder.pdf",
                "mimeType": "application/pdf",
                "sizeBytes": 2048,
                "category": "identity",
                "status": "placeholder",
                "createdAt": INITIAL_TIME,
            }
        ]
        self.appointments: list[dict[str, Any]] = [
            {
                "id": DEMO_IDS["sample_appointment_id"],
                "type": "enrollment_support",
                "startsAt": "2027-08-05T14:00:00.000Z",
                "notes": "Welcome and enrollment planning",
                "status": "scheduled",
                "createdAt": INITIAL_TIME,
            }
        ]
        self.payments: list[dict[str, Any]] = []
        self.help_requests: list[dict[str, Any]] = []
        self.profile: dict[str, Any] = {
            "studentId": DEMO_IDS["student_id"],
            "preferredName": "Alex",
            "pronouns": None,
            "mobilePhone": None,
            "communicationPreference": "email",
            "version": 1,
            "updatedAt": INITIAL_TIME,
        }
        self.dashboard: dict[str, Any] = {
            "student": {
                "id": DEMO_IDS["student_id"],
                "preferredName": "Alex",
                "fullName": "Alex Morgan",
                "classYear": 2027,
            },
            "offer": {
                "id": DEMO_IDS["offer_id"],
                "programName": "Computer Science",
                "termName": "Fall 2027",
                "campusName": "Main Campus",
                "responseDeadline": "2027-08-15",
                "depositAmountCents": 50_000,
                "status": "offered",
            },
            "journey": {
                "id": None,
                "status": "not_started",
                "completionPercent": 0,
                "nextAction": {
                    "code": "accept_offer",
                    "label": "Review and accept your offer",
                    "href": "/offer",
                },
                "requirements": [],
            },
            "unreadMessageCount": 0,
            "projectionVersion": 1,
            "generatedAt": INITIAL_TIME,
        }
        self.staff_members = [
            {
                "id": DEMO_IDS["staff_reviewer_id"],
                "name": "Marcus Lee",
                "email": "marcus.lee@aster.example.edu",
                "component": "Registrar",
            },
            {
                "id": DEMO_IDS["staff_advisor_id"],
                "name": "Priya Shah",
                "email": "priya.shah@aster.example.edu",
                "component": "Admissions",
            },
        ]
        self.work_items = self._initial_work_items()

    def _initial_work_items(self) -> list[dict[str, Any]]:
        student = self._staff_student_summary()
        advisor = next(x for x in self.staff_members if x["id"] == DEMO_IDS["staff_advisor_id"])
        return [
            {
                "id": DEMO_IDS["staff_onboarding_work_item_id"],
                "key": "ENR-104",
                "title": "Review Alex's onboarding support choices",
                "description": (
                    "Confirm residency, housing, and accommodation follow-up choices before "
                    "the next enrollment milestone."
                ),
                "status": "todo",
                "priority": "high",
                "type": "enrollment",
                "component": "Admissions",
                "dueAt": "2027-07-29T17:00:00.000Z",
                "escalated": False,
                "version": 1,
                "createdAt": INITIAL_TIME,
                "updatedAt": INITIAL_TIME,
                "assignee": _clone(advisor),
                "student": student,
                "source": {"type": "onboarding", "id": DEMO_IDS["student_id"]},
                "history": [
                    {
                        "id": DEMO_IDS["staff_onboarding_log_id"],
                        "action": "created",
                        "message": "Created from the enrollment onboarding queue.",
                        "actorName": "VV workflow",
                        "occurredAt": INITIAL_TIME,
                    }
                ],
            },
            {
                "id": DEMO_IDS["staff_outreach_work_item_id"],
                "key": "COM-208",
                "title": "Follow up on enrollment communication preference",
                "description": "Confirm the best channel for time-sensitive enrollment reminders.",
                "status": "in_progress",
                "priority": "medium",
                "type": "communication",
                "component": "Admissions",
                "dueAt": "2027-08-01T17:00:00.000Z",
                "escalated": False,
                "version": 1,
                "createdAt": INITIAL_TIME,
                "updatedAt": INITIAL_TIME,
                "assignee": _clone(advisor),
                "student": _clone(student),
                "source": {"type": "message", "id": DEMO_IDS["reminder_message_id"]},
                "history": [
                    {
                        "id": DEMO_IDS["staff_outreach_log_id"],
                        "action": "created",
                        "message": "Created from the student communication queue.",
                        "actorName": "VV workflow",
                        "occurredAt": INITIAL_TIME,
                    }
                ],
            },
        ]

    def authorize(self, auth: AuthContext, *, bootstrap: bool = False) -> None:
        if auth.tenant_id != DEMO_IDS["tenant_id"] or auth.student_id != DEMO_IDS["student_id"]:
            if bootstrap:
                raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
            raise NotFoundError(
                "STUDENT_DASHBOARD_NOT_FOUND", "No dashboard is available for this student"
            )

    def require_staff(self, auth: AuthContext) -> None:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_ACCESS_REQUIRED", "This route requires a staff identity")
        if auth.tenant_id != DEMO_IDS["tenant_id"]:
            raise NotFoundError("STAFF_WORK_ITEM_NOT_FOUND", "The work item was not found")
        if not any(member["id"] == auth.actor_id for member in self.staff_members):
            raise ApiError(
                403,
                "STAFF_IDENTITY_NOT_CONFIGURED",
                "The staff identity is not configured for this university",
            )

    def get_dashboard(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return _clone(self.dashboard)

    def get_academics(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        course = {
            "id": "10000000-0000-7000-8000-000000000201",
            "code": "CS 101",
            "title": "Programming Fundamentals",
            "description": "Problem solving and introductory software development.",
            "credits": 4,
            "level": 100,
            "prerequisites": [],
        }
        return {
            "selectedProgram": {
                "id": DEMO_IDS["program_id"],
                "code": "BS-CS",
                "name": "Computer Science",
                "degree": "Bachelor of Science",
                "totalCredits": 120,
                "description": "Computer science degree program.",
            },
            "availablePrograms": [],
            "transcriptCredits": [],
            "exemptionRecommendations": [],
            "plan": [
                {
                    "course": course,
                    "category": "major_core",
                    "recommendedTerm": 1,
                    "status": "eligible",
                    "satisfiedPrerequisiteCodes": [],
                    "missingPrerequisiteCodes": [],
                }
            ],
            "progress": {
                "completedCredits": 0,
                "exemptedCredits": 0,
                "requiredCredits": 120,
                "percent": 0,
            },
            "catalogVersion": "2027-2028.v1",
            "generatedAt": INITIAL_TIME,
        }

    def search_courses(self, auth: AuthContext, query: str) -> dict[str, Any]:
        academics = self.get_academics(auth)
        courses = [entry["course"] for entry in academics["plan"]]
        lowered = query.lower()
        items = [x for x in courses if lowered in f"{x['code']} {x['title']}".lower()]
        return {"items": items, "total": len(items), "catalogVersion": academics["catalogVersion"]}

    def get_financials(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return {
            "academicYear": "2027-2028",
            "costOfAttendanceCents": 3_240_000,
            "acceptedAidCents": 1_539_500,
            "pendingAidCents": 350_000,
            "paymentsCents": 0,
            "remainingBalanceCents": 1_700_500,
            "awards": [],
            "requiredDocuments": [],
            "paymentPlans": [],
            "sap": {
                "status": "meeting",
                "cumulativeGpa": 3.42,
                "minimumGpa": 2,
                "completionRatePercent": 78,
                "minimumCompletionRatePercent": 67,
                "attemptedCredits": 28,
                "maximumAttemptedCredits": 180,
            },
            "generatedAt": INITIAL_TIME,
        }

    def get_campus_life(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return {"events": [], "clubs": [], "generatedAt": INITIAL_TIME}

    def accept_offer(self, auth: AuthContext, offer_id: str, key: str) -> dict[str, Any]:
        self.authorize(auth)
        replay_key = f"{auth.tenant_id}:{auth.actor_id}:{key}"
        replay = self.offer_replays.get(replay_key)
        if replay:
            if replay[0] != offer_id:
                raise ConflictError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "This idempotency key was already used for a different request",
                )
            return _clone(replay[1])
        if offer_id != DEMO_IDS["offer_id"]:
            raise NotFoundError("ADMISSION_OFFER_NOT_FOUND", "The admission offer was not found")
        if self.accepted_response is None:
            journey_id = str(uuid4())
            self.accepted_response = {
                "offerId": offer_id,
                "offerStatus": "accepted",
                "journeyId": journey_id,
                "journeyStatus": "in_progress",
                "projectionVersion": 2,
                "acceptedAt": FIXED_TIME,
                "onboardingRequired": True,
                "initialRoute": "/onboarding",
            }
            self.effects.journeys += 1
            self.effects.requirement_sets += 1
            self.effects.audit_events += 1
            self.effects.outbox_events += 2
            self.dashboard["offer"]["status"] = "accepted"
            self.dashboard["journey"] = {
                "id": journey_id,
                "status": "in_progress",
                "completionPercent": 0,
                "nextAction": {
                    "code": "profile_verification",
                    "label": "Verify your profile",
                    "href": "/enrollment?requirement=profile_verification",
                },
                "requirements": [],
            }
            self.dashboard["projectionVersion"] = 2
            self.requirements = [
                {
                    "id": str(uuid4()),
                    "slug": _requirement_slug("profile_verification"),
                    "journeyId": journey_id,
                    "code": "profile_verification",
                    "title": "Verify your profile",
                    "description": "Confirm your personal and contact information.",
                    "status": "ready",
                    "blocking": True,
                    "dueAt": "2026-07-31T12:00:00.000Z",
                    "progressPercent": 0,
                    "submissionType": "form",
                    "documentCategory": None,
                    "responsibleOffice": "Enrollment Services",
                    "dependencyCodes": [],
                }
            ]
        self.offer_replays[replay_key] = (offer_id, _clone(self.accepted_response))
        return _clone(self.accepted_response)

    def get_bootstrap(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth, bootstrap=True)
        required = self.onboarding["status"] != "completed"
        return {
            "authenticated": True,
            "student": {
                "id": DEMO_IDS["student_id"],
                "preferredName": self.profile["preferredName"],
                "fullName": "Alex Morgan",
            },
            "onboarding": {
                "required": required,
                "status": self.onboarding["status"],
                "currentStep": self.onboarding["currentStep"],
                "version": self.onboarding["version"],
            },
            "unreadMessageCount": sum(x["readAt"] is None for x in self.messages),
            "initialRoute": "/onboarding" if required else "/dashboard",
            "generatedAt": FIXED_TIME,
        }

    def get_onboarding(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return _clone(self.onboarding)

    def update_onboarding(self, auth: AuthContext, update: dict[str, Any]) -> dict[str, Any]:
        self.authorize(auth)
        if self.onboarding["status"] == "completed":
            raise ConflictError(
                "ONBOARDING_ALREADY_COMPLETED", "Completed onboarding cannot be changed"
            )
        if update.get("expectedVersion") != self.onboarding["version"]:
            raise ConflictError("VERSION_CONFLICT", "Onboarding changed in another session")
        target = update.get("currentStep")
        active = self.onboarding["currentStep"]
        editing_completed = target in self.onboarding["completedSteps"]
        if (
            target not in ONBOARDING_STEPS
            or active not in ONBOARDING_STEPS
            or (target != active and not editing_completed)
            or ONBOARDING_STEPS.index(target) > ONBOARDING_STEPS.index(active)
        ):
            raise ConflictError(
                "ONBOARDING_STEP_OUT_OF_ORDER",
                f"The next required onboarding step is {active}",
            )
        skip = update.get("skip") is True
        if skip and not is_skippable_onboarding_step(target):
            raise BadRequestError(
                "ONBOARDING_STEP_REQUIRED",
                "This onboarding step is required before you can continue",
            )
        merged = {**self.onboarding["data"], **dict(update.get("data", {}))}
        skipped = list(merged.get("skippedSteps", []))
        if skip and target not in skipped:
            skipped.append(target)
        if not skip:
            skipped = [step for step in skipped if step != target]
            validate_onboarding_step(target, merged)
        merged["skippedSteps"] = skipped
        if target == "offer" and not skip and not self.accepted_response:
            raise ConflictError(
                "ACCEPTED_OFFER_REQUIRED",
                "Accept the admission offer before completing this step",
            )
        if target == "deposit" and merged.get("depositChoice") == "pay_now" and not self.payments:
            raise ConflictError(
                "DEPOSIT_REQUIRED", "Complete the enrollment deposit before saving this step"
            )
        advancing = target == active
        if advancing:
            current_index = ONBOARDING_STEPS.index(active)
            next_step = ONBOARDING_STEPS[min(current_index + 1, len(ONBOARDING_STEPS) - 1)]
            self.onboarding["currentStep"] = next_step
            self.onboarding["completedSteps"].append(active)
        self.onboarding.update(
            {
                "status": "in_progress",
                "data": merged,
                "version": self.onboarding["version"] + 1,
                "updatedAt": FIXED_TIME,
            }
        )
        return _clone(self.onboarding)

    def complete_onboarding(
        self, auth: AuthContext, update: dict[str, Any], key: str
    ) -> dict[str, Any]:
        self.authorize(auth)
        replay_key = f"onboarding:{key}"
        if replay_key in self.idempotency:
            return _clone(self.idempotency[replay_key])
        if self.onboarding["status"] != "completed":
            if update.get("expectedVersion") != self.onboarding["version"]:
                raise ConflictError("VERSION_CONFLICT", "Onboarding changed in another session")
            if len(self.onboarding["completedSteps"]) != len(ONBOARDING_STEPS) or any(
                not is_skippable_onboarding_step(step)
                for step in self.onboarding["data"].get("skippedSteps", [])
            ):
                raise ConflictError(
                    "ONBOARDING_INCOMPLETE",
                    "Every required onboarding step must be completed in order",
                )
            self.onboarding.update(
                {
                    "status": "completed",
                    "version": self.onboarding["version"] + 1,
                    "completedAt": FIXED_TIME,
                    "updatedAt": FIXED_TIME,
                }
            )
        self.idempotency[replay_key] = _clone(self.onboarding)
        return _clone(self.onboarding)

    def get_housing_plan(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        preference = self.onboarding["data"].get("housingPreference")
        residence = self.onboarding["data"].get("housingResidenceOption")
        if preference not in {"on_campus", "off_campus", "commuting", "undecided", "family"}:
            preference = None
        if residence not in {"aster_residence_hall", "aster_apartments", "student_village"}:
            residence = None
        return {
            "preference": preference,
            "residenceOption": residence,
            "residences": [],
            "version": self.onboarding["version"],
            "updatedAt": self.onboarding["updatedAt"],
        }

    def update_housing_plan(self, auth: AuthContext, update: dict[str, Any]) -> dict[str, Any]:
        self.authorize(auth)
        if update.get("expectedVersion") != self.onboarding["version"]:
            raise ConflictError("VERSION_CONFLICT", "Your housing plan changed in another session")
        preference = update["preference"]
        residence = update.get("residenceOption") if preference == "on_campus" else None
        self.onboarding["data"].update(
            {"housingPreference": preference, "housingResidenceOption": residence}
        )
        self.onboarding["version"] += 1
        self.onboarding["updatedAt"] = FIXED_TIME
        for requirement in self.requirements:
            if requirement["code"] == "housing_preference":
                requirement.update({"status": "completed", "progressPercent": 100})
        return self.get_housing_plan(auth)

    def get_requirements(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return {"items": _clone(self.requirements), "total": len(self.requirements)}

    def get_requirement(self, auth: AuthContext, identifier: str) -> dict[str, Any]:
        self.authorize(auth)
        code = _requirement_code(identifier)
        item = next(
            (x for x in self.requirements if x["id"] == identifier or x["code"] == code), None
        )
        if item is None:
            raise NotFoundError("STUDENT_REQUIREMENT_NOT_FOUND", "The requirement was not found")
        return _clone(item)

    def get_messages(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return {
            "items": _clone(self.messages),
            "unreadCount": sum(x["readAt"] is None for x in self.messages),
        }

    def mark_message_read(self, auth: AuthContext, message_id: str) -> dict[str, Any]:
        self.authorize(auth)
        message = next((x for x in self.messages if x["id"] == message_id), None)
        if message is None:
            raise NotFoundError("STUDENT_MESSAGE_NOT_FOUND", "The message was not found")
        if message["readAt"] is None:
            message["readAt"] = FIXED_TIME
        return _clone(message)

    def get_documents(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return {"items": _clone(self.documents), "total": len(self.documents)}

    def get_document(self, auth: AuthContext, document_id: str) -> dict[str, Any]:
        self.authorize(auth)
        document = next((x for x in self.documents if x["id"] == document_id), None)
        if document is None:
            raise NotFoundError("STUDENT_DOCUMENT_NOT_FOUND", "The document was not found")
        return _clone(document)

    def create_document(self, auth: AuthContext, body: dict[str, Any], key: str) -> dict[str, Any]:
        self.authorize(auth)
        replay_key = f"document:{key}"
        if replay_key in self.idempotency:
            return _clone(self.idempotency[replay_key])
        document = {
            "id": str(uuid4()),
            **_clone(body),
            "status": "placeholder",
            "createdAt": FIXED_TIME,
        }
        self.documents.insert(0, document)
        self.idempotency[replay_key] = _clone(document)
        return _clone(document)

    def reserve_document_upload(
        self,
        auth: AuthContext,
        document: dict[str, Any],
        key: str,
        requirement_id: str | None,
    ) -> dict[str, Any]:
        self.authorize(auth)
        replay_key = f"document-upload:{key}"
        if replay_key in self.idempotency:
            return _clone(self.idempotency[replay_key])
        requirement = None
        if requirement_id:
            requirement = next(
                (
                    x
                    for x in self.requirements
                    if x["id"] == requirement_id and x["submissionType"] == "document"
                ),
                None,
            )
            if requirement is None:
                raise NotFoundError(
                    "DOCUMENT_REQUIREMENT_NOT_FOUND", "The document requirement was not found"
                )
        document_id = str(uuid4())
        extension = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}[
            document["mimeType"]
        ]
        category_map = {
            "identity_document": "identity",
            "official_transcript": "transcript",
            "financial_aid_verification": "financial_aid",
            "immunization_record": "health",
        }
        stored = {
            "id": document_id,
            **({"requirementId": requirement_id} if requirement_id else {}),
            "fileName": document["fileName"],
            "mimeType": document["mimeType"],
            "sizeBytes": document["sizeBytes"],
            "category": category_map.get(requirement["code"], document["category"])
            if requirement
            else document["category"],
            "status": "uploaded",
            "sha256": document["sha256"],
            "contentUrl": f"/v1/student/documents/{document_id}/content",
            "createdAt": FIXED_TIME,
        }
        self.documents.insert(0, stored)
        self.document_storage_keys[document_id] = (
            f"{auth.tenant_id}/{auth.student_id}/{document_id}{extension}"
        )
        self.idempotency[replay_key] = _clone(stored)
        self.effects.audit_events += 1
        self.effects.outbox_events += 1
        return _clone(stored)

    def get_content_reference(self, auth: AuthContext, document_id: str) -> dict[str, str]:
        document = self.get_document(auth, document_id)
        storage_key = self.document_storage_keys.get(document_id)
        if not storage_key:
            raise NotFoundError(
                "STUDENT_DOCUMENT_CONTENT_NOT_FOUND", "The uploaded document content was not found"
            )
        return {
            "storageKey": storage_key,
            "fileName": document["fileName"],
            "mimeType": document["mimeType"],
        }

    def claim_document_processing(
        self,
        auth: AuthContext,
        document_id: str,
        *,
        retry: bool = False,
        retry_key: str | None = None,
    ) -> bool:
        self.authorize(auth)
        if retry and not retry_key:
            raise BadRequestError(
                "IDEMPOTENCY_KEY_REQUIRED", "The Idempotency-Key header is required"
            )
        replay_key = f"document-extraction-retry:{document_id}:{retry_key}" if retry else None
        if replay_key and replay_key in self.idempotency:
            return False
        document = next((x for x in self.documents if x["id"] == document_id), None)
        can_claim = (
            can_retry_extraction(document.get("extraction"))
            if document and retry
            else bool(document and not document.get("extraction"))
        )
        if not document or document["status"] != "uploaded" or not can_claim:
            return False
        document["status"] = "processing"
        document["extraction"] = {
            "status": "processing",
            "documentType": "other",
            "summary": (
                "The original file is safely stored. Edward is preparing a reviewable record."
            ),
            "studentName": None,
            "institutionName": None,
            "issueDate": None,
            "academicTerm": None,
            "fields": [],
            "courses": [],
            "visualRegions": [],
            "warnings": [],
            "model": None,
            "provider": "local",
            "processedAt": None,
            "verifiedAt": None,
        }
        if replay_key:
            self.idempotency[replay_key] = {"documentId": document_id, "status": "processing"}
        self.effects.audit_events += 1
        self.effects.outbox_events += 1
        return True

    def release_document_processing(self, auth: AuthContext, document_id: str) -> None:
        self.authorize(auth)
        document = next((x for x in self.documents if x["id"] == document_id), None)
        if not document or document["status"] != "processing":
            raise ConflictError(
                "DOCUMENT_PROCESSING_STATE_CHANGED",
                "The document processing state changed before the upload could be retried",
            )
        document["status"] = "uploaded"
        document.pop("extraction", None)

    def complete_document_extraction(
        self, auth: AuthContext, document_id: str, extraction: dict[str, Any]
    ) -> dict[str, Any]:
        self.authorize(auth)
        document = next((x for x in self.documents if x["id"] == document_id), None)
        if not document or document["status"] != "processing":
            raise ConflictError(
                "DOCUMENT_PROCESSING_STATE_CHANGED",
                "The document processing state changed before completion",
            )
        document["extraction"] = _clone(extraction)
        document["status"] = "needs_review" if extraction["status"] == "completed" else "uploaded"
        return _clone(document)

    def confirm_document_extraction(
        self, auth: AuthContext, document_id: str, confirmation: dict[str, Any]
    ) -> dict[str, Any]:
        document = self.get_document(auth, document_id)
        extraction = document.get("extraction")
        if not extraction or extraction.get("status") != "completed":
            raise ConflictError(
                "DOCUMENT_EXTRACTION_NOT_READY", "Document extraction is not ready for review"
            )
        available = {field["key"] for field in extraction.get("fields", [])}
        accepted = confirmation.get("acceptedFieldKeys", [])
        if any(key not in available for key in accepted):
            raise BadRequestError(
                "UNKNOWN_EXTRACTED_FIELD",
                "One or more extracted fields do not belong to this document",
            )
        stored = next(x for x in self.documents if x["id"] == document_id)
        stored["extraction"].update(
            {"acceptedFieldKeys": list(dict.fromkeys(accepted)), "verifiedAt": FIXED_TIME}
        )
        stored["status"] = "under_review"
        return _clone(stored)

    def save_signed_document(
        self, auth: AuthContext, document: dict[str, Any], storage_key: str
    ) -> dict[str, Any]:
        self.authorize(auth)
        existing = next(
            (
                x
                for x in self.documents
                if x.get("signature", {}).get("templateCode") == document["templateCode"]
                and x.get("signature", {}).get("onboardingVersion") == document["onboardingVersion"]
            ),
            None,
        )
        if existing:
            return _clone(existing)
        stored = {
            "id": document["id"],
            "fileName": document["fileName"],
            "mimeType": "application/pdf",
            "sizeBytes": document["sizeBytes"],
            "category": "other",
            "processingMode": "generated",
            "status": "accepted",
            "contentUrl": f"/v1/student/documents/{document['id']}/content",
            "sha256": document["sha256"],
            "signature": {
                "templateCode": document["templateCode"],
                "title": document["title"],
                "signerName": document["signerName"],
                "method": document["signatureMethod"],
                "signedAt": document["signedAt"],
                "onboardingVersion": document["onboardingVersion"],
            },
            "createdAt": document["signedAt"],
        }
        self.documents.insert(0, stored)
        self.document_storage_keys[document["id"]] = storage_key
        return _clone(stored)

    def get_appointments(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return {"items": _clone(self.appointments), "total": len(self.appointments)}

    def create_appointment(
        self, auth: AuthContext, body: dict[str, Any], key: str
    ) -> dict[str, Any]:
        self.authorize(auth)
        try:
            starts_at = datetime.fromisoformat(body["startsAt"].replace("Z", "+00:00"))
        except (KeyError, ValueError) as error:
            raise BadRequestError(
                "VALIDATION_ERROR", "Appointment time must be an ISO timestamp"
            ) from error
        if starts_at <= datetime.now(UTC):
            raise BadRequestError(
                "APPOINTMENT_MUST_BE_FUTURE", "Appointment time must be in the future"
            )
        replay_key = f"appointment:{key}"
        if replay_key in self.idempotency:
            return _clone(self.idempotency[replay_key])
        appointment = {
            "id": str(uuid4()),
            "type": body["type"],
            "startsAt": body["startsAt"],
            "notes": body.get("notes"),
            "status": "scheduled",
            "createdAt": FIXED_TIME,
        }
        self.appointments.append(appointment)
        self.idempotency[replay_key] = _clone(appointment)
        return _clone(appointment)

    def get_payments(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return {"items": _clone(self.payments), "total": len(self.payments)}

    def create_deposit(self, auth: AuthContext, body: dict[str, Any], key: str) -> dict[str, Any]:
        self.authorize(auth)
        if not self.accepted_response or body.get("offerId") != DEMO_IDS["offer_id"]:
            raise ConflictError(
                "ACCEPTED_OFFER_REQUIRED",
                "An accepted admission offer is required before paying a deposit",
            )
        replay_key = f"payment:{key}"
        if replay_key in self.idempotency:
            return _clone(self.idempotency[replay_key])
        if self.payments:
            return _clone(self.payments[0])
        payment_id = str(uuid4())
        payment = {
            "id": payment_id,
            "offerId": body["offerId"],
            "type": "enrollment_deposit",
            "amountCents": 50_000,
            "status": "succeeded",
            "processor": "dummy",
            "processorReference": f"dummy_{payment_id.replace('-', '')}",
            "createdAt": FIXED_TIME,
        }
        self.payments.append(payment)
        self.idempotency[replay_key] = _clone(payment)
        return _clone(payment)

    def get_profile(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return _clone(self.profile)

    def update_profile(self, auth: AuthContext, update: dict[str, Any]) -> dict[str, Any]:
        self.authorize(auth)
        if update.get("expectedVersion") != self.profile["version"]:
            raise ConflictError("VERSION_CONFLICT", "The profile changed in another session")
        changes = {key: value for key, value in update.items() if key != "expectedVersion"}
        if not changes:
            raise BadRequestError(
                "PROFILE_UPDATE_EMPTY", "At least one profile field must be supplied"
            )
        self.profile.update(changes)
        self.profile["version"] += 1
        self.profile["updatedAt"] = FIXED_TIME
        return _clone(self.profile)

    def get_help(self, auth: AuthContext) -> dict[str, Any]:
        self.authorize(auth)
        return {
            "articles": [
                {
                    "id": DEMO_IDS["help_getting_started_id"],
                    "category": "getting_started",
                    "question": "Where should I begin?",
                    "answer": "Start with the next action shown on your dashboard.",
                }
            ],
            "support": {
                "email": "enrollment-support@vv.example",
                "phone": "+1 555 010 2027",
                "hours": "Monday-Friday, 09:00-17:00",
            },
        }

    def create_help_request(
        self, auth: AuthContext, body: dict[str, Any], key: str
    ) -> dict[str, Any]:
        self.authorize(auth)
        topic_code = body.get("topicCode")
        if not isinstance(topic_code, str) or topic_code not in {
            "getting_started",
            "documents",
            "payments",
            "support",
        }:
            raise BadRequestError("VALIDATION_ERROR", "The help topic is invalid")
        raw_message = body.get("message")
        if not isinstance(raw_message, str):
            raise BadRequestError("VALIDATION_ERROR", "The help request message is invalid")
        message = raw_message.strip()
        if not 1 <= len(message) <= 500:
            raise BadRequestError(
                "VALIDATION_ERROR", "The help request message must contain 1-500 characters"
            )
        request_payload = {"topicCode": topic_code, "message": message}
        replay = self.help_request_replays.get(key)
        if replay is not None:
            previous_payload, response = replay
            if previous_payload != request_payload:
                raise ConflictError(
                    "IDEMPOTENCY_KEY_REUSED",
                    "This idempotency key was already used for a different request",
                )
            return _clone(response)
        created_at = _now()
        request = {
            "id": str(uuid4()),
            "topicCode": topic_code,
            "subject": f"Student question about {topic_code.replace('_', ' ')}",
            "message": message,
            "status": "new",
            "priority": "high" if topic_code == "support" else "medium",
            "assigneeId": None,
            "createdAt": created_at,
            "updatedAt": created_at,
            "version": 1,
        }
        self.help_requests.append(request)
        self.help_request_replays[key] = (_clone(request_payload), _clone(request))
        self.effects.audit_events += 1
        self.effects.outbox_events += 1
        return _clone(request)

    def _staff_student_summary(self) -> dict[str, Any]:
        return {
            "id": DEMO_IDS["student_id"],
            "name": "Alex Morgan",
            "preferredName": self.profile["preferredName"],
            "programName": "Computer Science",
            "classYear": 2027,
        }

    def ensure_document_work_items(self) -> None:
        for document in self.documents:
            if document["status"] not in {"needs_review", "under_review"}:
                continue
            if any(
                x.get("source") == {"type": "document", "id": document["id"]}
                for x in self.work_items
            ):
                continue
            component = (
                "Financial Aid"
                if document["category"] == "financial_aid"
                else "Student Health"
                if document["category"] == "health"
                else "Registrar"
            )
            assignee = next(
                (x for x in self.staff_members if x["component"] == component),
                self.staff_members[0],
            )
            work_item_id = str(uuid4())
            self.work_items.append(
                {
                    "id": work_item_id,
                    "key": f"DOC-{document['id'].replace('-', '')[:8].upper()}",
                    "title": f"Review {document['fileName']}",
                    "description": (
                        "Verify the stored original and make the official staff decision."
                    ),
                    "status": "todo",
                    "priority": "urgent" if document["category"] == "financial_aid" else "high",
                    "type": "document_review",
                    "component": component,
                    "dueAt": "2026-07-26T12:00:00.000Z",
                    "escalated": False,
                    "version": 1,
                    "createdAt": FIXED_TIME,
                    "updatedAt": FIXED_TIME,
                    "assignee": _clone(assignee),
                    "student": self._staff_student_summary(),
                    "source": {"type": "document", "id": document["id"]},
                    "history": [
                        {
                            "id": str(uuid4()),
                            "action": "created",
                            "message": "Created when the student document entered staff review.",
                            "actorName": "VV workflow",
                            "occurredAt": FIXED_TIME,
                        }
                    ],
                }
            )

    def get_action_center(self, auth: AuthContext) -> dict[str, Any]:
        self.require_staff(auth)
        self.ensure_document_work_items()
        items = sorted(
            self.work_items,
            key=lambda x: (
                {"urgent": 0, "high": 1, "medium": 2, "low": 3}[x["priority"]],
                x["dueAt"] or "",
            ),
        )
        return {
            "items": _clone(items),
            "staff": _clone(self.staff_members),
            "counts": {
                "todo": sum(x["status"] == "todo" for x in items),
                "inProgress": sum(x["status"] == "in_progress" for x in items),
                "done": sum(x["status"] == "done" for x in items),
                "urgent": sum(x["priority"] == "urgent" for x in items),
                "escalated": sum(bool(x["escalated"]) for x in items),
            },
            "generatedAt": _now(),
        }

    def _work_item(self, work_item_id: str) -> dict[str, Any]:
        item = next((x for x in self.work_items if x["id"] == work_item_id), None)
        if item is None:
            raise NotFoundError("STAFF_WORK_ITEM_NOT_FOUND", "The work item was not found")
        return item

    def update_work_item(
        self, auth: AuthContext, work_item_id: str, update: dict[str, Any]
    ) -> dict[str, Any]:
        self.require_staff(auth)
        item = self._work_item(work_item_id)
        if update.get("expectedVersion") != item["version"]:
            raise ConflictError(
                "VERSION_CONFLICT", "This work item changed in another staff session"
            )
        changes: list[tuple[str, str]] = []
        if "status" in update and update["status"] != item["status"]:
            changes.append(
                (
                    "status_changed",
                    f"Moved from {item['status'].replace('_', ' ')} to "
                    f"{update['status'].replace('_', ' ')}.",
                )
            )
            item["status"] = update["status"]
        if "assigneeId" in update:
            assignee_id = update["assigneeId"]
            assignee = (
                next((x for x in self.staff_members if x["id"] == assignee_id), None)
                if assignee_id
                else None
            )
            if assignee_id and assignee is None:
                raise NotFoundError("STAFF_MEMBER_NOT_FOUND", "The assignee was not found")
            old_id = item["assignee"]["id"] if item["assignee"] else None
            if assignee_id != old_id:
                item["assignee"] = _clone(assignee)
                changes.append(
                    (
                        "assigned",
                        f"Assigned to {assignee['name']}." if assignee else "Removed the assignee.",
                    )
                )
        if "escalated" in update and update["escalated"] != item["escalated"]:
            item["escalated"] = update["escalated"]
            changes.append(
                (
                    "escalated",
                    "Marked as escalated." if item["escalated"] else "Cleared the escalation flag.",
                )
            )
        if str(update.get("note", "")).strip():
            changes.append(("commented", str(update["note"]).strip()))
        if not changes:
            raise BadRequestError(
                "STAFF_WORK_ITEM_NO_CHANGES",
                "Choose a status, assignee, escalation state, or note to update",
            )
        actor = next(x for x in self.staff_members if x["id"] == auth.actor_id)
        for action, message in changes:
            item["history"].insert(
                0,
                {
                    "id": str(uuid4()),
                    "action": action,
                    "message": message,
                    "actorName": actor["name"],
                    "occurredAt": FIXED_TIME,
                },
            )
        item["version"] += 1
        item["updatedAt"] = FIXED_TIME
        return _clone(item)

    def get_student_record(self, auth: AuthContext, student_id: str) -> dict[str, Any]:
        self.require_staff(auth)
        if student_id != DEMO_IDS["student_id"]:
            raise NotFoundError("STAFF_STUDENT_NOT_FOUND", "The student was not found")
        student_auth = AuthContext(
            tenant_id=auth.tenant_id,
            student_id=student_id,
            actor_id=auth.actor_id,
            actor_type="staff",
        )
        return {
            "student": self._staff_student_summary(),
            "onboarding": self.get_onboarding(student_auth),
            "profile": self.get_profile(student_auth),
            "requirements": self.get_requirements(student_auth),
            "documents": self.get_documents(student_auth),
        }

    def update_student_preferences(
        self, auth: AuthContext, student_id: str, update: dict[str, Any]
    ) -> dict[str, Any]:
        self.require_staff(auth)
        if student_id != DEMO_IDS["student_id"]:
            raise NotFoundError("STAFF_STUDENT_NOT_FOUND", "The student was not found")
        if (
            update.get("expectedOnboardingVersion") != self.onboarding["version"]
            or update.get("expectedProfileVersion") != self.profile["version"]
        ):
            raise ConflictError("VERSION_CONFLICT", "The student record changed in another session")
        self.onboarding["data"].update(
            {
                "communicationPreference": update["communicationPreference"],
                "housingPreference": update["housingPreference"],
                "accommodationInterest": update["accommodationInterest"],
                "residencyVerificationPath": update["residencyVerificationPath"],
            }
        )
        self.onboarding["version"] += 1
        self.onboarding["updatedAt"] = FIXED_TIME
        self.profile["communicationPreference"] = update["communicationPreference"]
        self.profile["version"] += 1
        self.profile["updatedAt"] = FIXED_TIME
        item = next(
            (x for x in self.work_items if x.get("source", {}).get("type") == "onboarding"), None
        )
        if item:
            item["version"] += 1
            item["history"].insert(
                0,
                {
                    "id": str(uuid4()),
                    "action": "student_preferences_updated",
                    "message": str(update.get("note") or "Updated student onboarding preferences."),
                    "actorName": next(x for x in self.staff_members if x["id"] == auth.actor_id)[
                        "name"
                    ],
                    "occurredAt": FIXED_TIME,
                },
            )
        if update.get("notifyStudent"):
            self.messages.insert(
                0,
                {
                    "id": str(uuid4()),
                    "subject": "Your enrollment preferences were updated",
                    "body": str(
                        update.get("note") or "Enrollment Services updated your preferences."
                    ),
                    "senderName": "Enrollment Team",
                    "sentAt": FIXED_TIME,
                    "readAt": None,
                },
            )
        return self.get_student_record(auth, student_id)

    def review_document(
        self, auth: AuthContext, document_id: str, review: dict[str, Any]
    ) -> dict[str, Any]:
        self.require_staff(auth)
        self.ensure_document_work_items()
        item = self._work_item(review["workItemId"])
        if item.get("source") != {"type": "document", "id": document_id}:
            raise NotFoundError(
                "STAFF_WORK_ITEM_NOT_FOUND", "The document review work item was not found"
            )
        if review.get("expectedWorkItemVersion") != item["version"]:
            raise ConflictError(
                "VERSION_CONFLICT", "This document review changed in another staff session"
            )
        document = next((x for x in self.documents if x["id"] == document_id), None)
        if document is None:
            raise NotFoundError("STAFF_DOCUMENT_NOT_FOUND", "The document was not found")
        if document["status"] not in {"needs_review", "under_review"}:
            raise ConflictError(
                "DOCUMENT_REVIEW_ALREADY_DECIDED",
                "This document already has an official staff decision",
            )
        decision = review["decision"]
        document["status"] = decision
        if document.get("requirementId"):
            requirement = next(
                (x for x in self.requirements if x["id"] == document["requirementId"]), None
            )
            if requirement:
                requirement["status"] = "completed" if decision == "accepted" else "rejected"
                requirement["progressPercent"] = 100 if decision == "accepted" else 60
        item["status"] = "done"
        item["version"] += 1
        item["updatedAt"] = FIXED_TIME
        verb = "Accepted" if decision == "accepted" else "Requested changes to"
        item["history"].insert(
            0,
            {
                "id": str(uuid4()),
                "action": "document_decided",
                "message": f"{verb} {document['fileName']}: {str(review['note']).strip()}",
                "actorName": next(x for x in self.staff_members if x["id"] == auth.actor_id)[
                    "name"
                ],
                "occurredAt": FIXED_TIME,
            },
        )
        notification = None
        if review.get("notifyStudent"):
            notification = {
                "id": str(uuid4()),
                "subject": f"{document['fileName']} was accepted"
                if decision == "accepted"
                else f"{document['fileName']} needs changes",
                "body": str(review["note"]).strip(),
                "senderName": "Enrollment Team",
                "sentAt": FIXED_TIME,
                "readAt": None,
            }
            self.messages.insert(0, notification)
        return {
            "document": _clone(document),
            "workItem": _clone(item),
            "notification": _clone(notification),
        }
