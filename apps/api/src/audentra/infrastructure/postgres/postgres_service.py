"""Production application service composed from PostgreSQL repositories and adapters."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, NotFoundError, UnauthorizedError
from audentra.core.ports import BinaryPayload, ServiceCall
from audentra.domain.documents import (
    can_retry_extraction,
    classify_extraction_failure,
    deterministic_document_id,
    document_type_for_category,
    failed_extraction,
    validate_document_upload,
)
from audentra.infrastructure.documents.processing import (
    NormalizedImageRegion,
    SignatureBox,
    SigningInput,
    create_signed_onboarding_pdf,
    extract_student_document_image_region,
)
from audentra.infrastructure.preview.staff_workspace import PreviewStaffWorkspaceRepository
from audentra.infrastructure.storage.s3 import StorageError
from audentra.integrations.ai.edward_safety import (
    EdwardActionAuthority,
    guarded_response,
    normalize_response,
)
from audentra.integrations.ai.extraction import match_document_to_student_context

from .managed_configuration_repository import PostgresManagedConfigurationRepository
from .platform_repository import PostgresPlatformRepository
from .portal_repository import PostgresPortalRepository
from .staff_repository import PostgresStaffRepository

JsonDict = dict[str, Any]
LOGGER = logging.getLogger(__name__)

SIGNED_TEMPLATES = (
    {
        "code": "ferpa_release",
        "title": "FERPA Information Release",
        "fileName": "ferpa-information-release-signed.pdf",
        "sourceSuffix": "ferpa-release.pdf",
        "signatureBox": {"x": 0.098, "y": 0.488, "width": 0.53, "height": 0.054},
    },
    {
        "code": "enrollment_acknowledgment",
        "title": "Enrollment Information Acknowledgment",
        "fileName": "enrollment-information-acknowledgment-signed.pdf",
        "sourceSuffix": "enrollment-acknowledgment.pdf",
        "signatureBox": {"x": 0.098, "y": 0.447, "width": 0.53, "height": 0.054},
    },
)

HARVARD_TENANT_ID = "00000000-0000-7000-8000-000000000002"

_EDWARD_DOCUMENT_TOPIC = re.compile(r"document|upload|transcript|fafsa|ferpa|verification", re.I)
_EDWARD_ONBOARDING_TOPIC = re.compile(
    r"onboarding|offer|housing|roommate|emergency contact|sign", re.I
)
_EDWARD_PAYMENT_TOPIC = re.compile(r"payment|deposit|pay|balance|billing|financial|aid|loan", re.I)
_EDWARD_MESSAGE_TOPIC = re.compile(r"message|inbox|notification|unread", re.I)
_EDWARD_ACADEMIC_TOPIC = re.compile(
    r"academic|class|classroom|course|catalog|major|program|prerequisite|exempt|credit",
    re.I,
)
_EDWARD_FINANCIAL_TOPIC = re.compile(
    r"financial|aid|fafsa|loan|award|balance|billing|payment plan|sap", re.I
)
_EDWARD_CAMPUS_TOPIC = re.compile(
    r"campus|club|event|activity|activities|organization|community|social life", re.I
)
_EDWARD_DEPOSIT_ACTION = re.compile(
    r"(?:pay|make|complete).{0,24}deposit|deposit.{0,24}(?:pay|payment)", re.I
)
_EDWARD_DOCUMENT_ACTION = re.compile(r"upload|transcript|fafsa|verification", re.I)
_EDWARD_APPOINTMENT_ACTION = re.compile(r"appointment|advisor|counselor|human", re.I)
_EDWARD_FINANCIAL_APPOINTMENT = re.compile(r"financial|aid|fafsa|loan", re.I)
_PORTAL_MEDIA_MIME_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_PORTAL_MEDIA_FILE = re.compile(
    r"^(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\."
    r"(?P<extension>jpg|png|webp)$"
)
_CALL_RECORDING_MIME_EXTENSIONS = {
    "audio/flac": "flac",
    "audio/m4a": "m4a",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "webm",
    "audio/x-m4a": "m4a",
    "video/mp4": "mp4",
    "video/webm": "webm",
}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _integer(value: object) -> int:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return 0


def _safe_upload_file_name(value: str) -> str:
    file_name = value.replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = "".join(character for character in file_name if ord(character) >= 32)
    return (cleaned or "call-recording")[:255]


def _academic_summary(value: Mapping[str, Any]) -> JsonDict:
    selected = _mapping(value.get("selectedProgram"))
    recommendations = _sequence(value.get("exemptionRecommendations"))
    plan: list[JsonDict] = []
    for raw in _sequence(value.get("plan"))[:16]:
        item = _mapping(raw)
        course = _mapping(item.get("course"))
        plan.append(
            {
                "code": str(course.get("code") or ""),
                "title": str(course.get("title") or ""),
                "recommendedTerm": item.get("recommendedTerm"),
                "status": item.get("status"),
                "missingPrerequisites": list(_sequence(item.get("missingPrerequisiteCodes"))[:12]),
            }
        )
    return {
        "selectedProgram": str(selected.get("name") or ""),
        "degree": str(selected.get("degree") or ""),
        "catalogVersion": str(value.get("catalogVersion") or ""),
        "suggestedExemptions": [
            str(_mapping(item).get("targetCourseCode") or "")
            for item in recommendations[:16]
            if _mapping(item).get("targetCourseCode")
        ],
        "plan": plan,
    }


def _financial_summary(value: Mapping[str, Any]) -> JsonDict:
    sap = _mapping(value.get("sap"))
    required_documents = [
        _mapping(item)
        for item in _sequence(value.get("requiredDocuments"))
        if _mapping(item).get("status") == "action_required"
    ]
    return {
        "remainingBalanceCents": _integer(value.get("remainingBalanceCents")),
        "acceptedAidCents": _integer(value.get("acceptedAidCents")),
        "actionRequiredDocuments": [
            str(item.get("code") or "") for item in required_documents[:16] if item.get("code")
        ],
        "sapStatus": str(sap.get("status") or ""),
    }


def _campus_life_summary(value: Mapping[str, Any]) -> JsonDict:
    return {
        "upcomingEvents": [
            {key: item.get(key) for key in ("title", "startsAt", "location", "category")}
            for raw in _sequence(value.get("events"))[:8]
            if (item := _mapping(raw))
        ],
        "clubs": [
            {key: item.get(key) for key in ("name", "category", "description", "nextActivity")}
            for raw in _sequence(value.get("clubs"))[:16]
            if (item := _mapping(raw))
        ],
    }


def _student_document_context(
    profile: Mapping[str, Any],
    onboarding: Mapping[str, Any],
    requirements: Mapping[str, Any],
) -> JsonDict:
    """Build bounded, authenticated-student candidates for local matching.

    Existing values are deliberately excluded. Only missing field names and
    actionable upload requirements are retained, and this object never leaves
    the application process.
    """

    candidates: list[JsonDict] = []
    safe_profile_fields = {
        "preferredName": profile.get("preferredName"),
        "pronouns": profile.get("pronouns"),
        "mobilePhone": profile.get("mobilePhone"),
    }
    missing_profile_fields = [
        key for key, value in safe_profile_fields.items() if value in (None, "")
    ]
    if missing_profile_fields:
        candidates.append(
            {
                "targetType": "profile",
                "targetId": "profile",
                "title": "Your profile",
                "fieldKeys": missing_profile_fields,
                "href": "/profile",
            }
        )

    if onboarding.get("status") != "completed":
        onboarding_data = _mapping(onboarding.get("data"))
        onboarding_fields = (
            "firstName",
            "lastName",
            "preferredName",
            "personalEmail",
            "mobilePhone",
            "citizenshipStatus",
            "streetAddress",
            "city",
            "stateOrProvince",
            "postalCode",
            "country",
            "residencyVerificationPath",
        )
        missing_onboarding_fields = [
            key for key in onboarding_fields if onboarding_data.get(key) in (None, "", [])
        ]
        if missing_onboarding_fields:
            candidates.append(
                {
                    "targetType": "onboarding",
                    "targetId": "about_you",
                    "title": "Onboarding profile details",
                    "fieldKeys": missing_onboarding_fields,
                    "href": "/onboarding",
                }
            )

    document_types = {
        "identity": "identity",
        "transcript": "transcript",
        "financial_aid": "financial_aid",
        "health": "immunization",
        "consent": "ferpa",
        "residency": "residency",
    }
    for raw in _sequence(requirements.get("items"))[:64]:
        requirement = _mapping(raw)
        if (
            requirement.get("submissionType") != "document"
            or requirement.get("interactionType") != "upload_file"
            or requirement.get("status") not in {"ready", "in_progress", "rejected"}
        ):
            continue
        category = requirement.get("documentCategory")
        if not isinstance(category, str):
            configured = _sequence(
                _mapping(requirement.get("inputConfig")).get("documentCategories")
            )
            category = next((item for item in configured if isinstance(item, str)), None)
        expected_type = document_types.get(str(category))
        if expected_type is None:
            continue
        requirement_id = requirement.get("id")
        if not isinstance(requirement_id, str):
            continue
        slug = str(requirement.get("slug") or requirement_id)
        candidates.append(
            {
                "targetType": "requirement",
                "targetId": requirement_id,
                "title": str(requirement.get("title") or "Enrollment document"),
                "fieldKeys": [],
                "expectedDocumentType": expected_type,
                "href": f"/enrollment/requirements/{slug}",
            }
        )
    return {"version": 1, "candidates": candidates[:32]}


class ObjectStorage(Protocol):
    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        sha256: str | None = None,
    ) -> str: ...

    async def get(self, key: str) -> bytes: ...


class StudentAI(Protocol):
    async def ask_edward(
        self,
        *,
        message: str,
        page_context: str,
        history: Sequence[Mapping[str, Any]],
        student_context: Mapping[str, Any],
        tenant_id: str | None = None,
        student_id: str | None = None,
        request_id: str | None = None,
    ) -> JsonDict: ...

    async def extract_document(
        self,
        *,
        file_name: str,
        mime_type: str,
        content: bytes,
        expected_document_type: str | None = None,
        tenant_id: str | None = None,
        student_id: str | None = None,
        document_id: str | None = None,
        request_id: str | None = None,
        attempt: int = 1,
    ) -> JsonDict: ...

    async def evaluate_course_exemptions(
        self,
        *,
        tenant_id: str,
        student_id: str,
        document_id: str,
        request_id: str,
        courses: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> JsonDict: ...

    async def evaluate_immunization_compliance(
        self,
        *,
        tenant_id: str,
        student_id: str,
        document_id: str,
        request_id: str,
        extraction: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> JsonDict: ...


@dataclass(frozen=True, slots=True)
class PostgresRepositoryBundle:
    """Repositories sharing one application-owned engine lifecycle."""

    platform: PostgresPlatformRepository
    portal: PostgresPortalRepository
    staff: PostgresStaffRepository
    managed: PostgresManagedConfigurationRepository | None = None


class SignedDocumentGenerator(Protocol):
    async def ensure(
        self,
        *,
        auth: AuthContext,
        onboarding: Mapping[str, Any],
        repository: PostgresPortalRepository,
        storage: ObjectStorage,
        request_id: str,
    ) -> int: ...


class PostgresSignedDocumentGenerator:
    """Idempotently materialize onboarding signature packets in S3 and PostgreSQL."""

    def __init__(self, template_directory: str | Path | None = None) -> None:
        configured = template_directory or os.environ.get("ONBOARDING_DOCUMENT_TEMPLATE_DIR")
        self._template_roots = tuple(
            dict.fromkeys(
                path.resolve()
                for path in (
                    Path(configured).expanduser() if configured else None,
                    Path(__file__).resolve().parents[4] / "assets" / "onboarding",
                )
                if path is not None
            )
        )

    def _read_template(self, file_name: str) -> bytes:
        missing: FileNotFoundError | None = None
        for root in self._template_roots:
            try:
                return (root / file_name).read_bytes()
            except FileNotFoundError as error:
                missing = error
        if missing is not None:
            raise missing
        raise FileNotFoundError(f"Missing onboarding template {file_name}")

    async def ensure(
        self,
        *,
        auth: AuthContext,
        onboarding: Mapping[str, Any],
        repository: PostgresPortalRepository,
        storage: ObjectStorage,
        request_id: str,
    ) -> int:
        data = onboarding.get("data")
        if (
            onboarding.get("status") != "completed"
            or not onboarding.get("completedAt")
            or not isinstance(data, Mapping)
            or data.get("signatureConsent") is not True
            or not data.get("signatureFullName")
            or not data.get("signatureMethod")
            or not isinstance(data.get("signedDocumentIds"), Sequence)
        ):
            return 0
        existing = await repository.get_student_documents(auth)
        version = int(onboarding["version"])
        existing_codes = {
            signature.get("templateCode")
            for item in cast(list[JsonDict], existing.get("items", []))
            if isinstance(item, dict)
            and isinstance((signature := item.get("signature")), dict)
            and signature.get("onboardingVersion") == version
        }
        requested = set(data["signedDocumentIds"])
        tenant_prefix = "harvard" if auth.tenant_id == HARVARD_TENANT_ID else "aster"
        created = 0
        for template in SIGNED_TEMPLATES:
            if template["code"] not in requested or template["code"] in existing_codes:
                continue
            document_id = deterministic_document_id(
                f"{auth.tenant_id}:{auth.student_id}:{template['code']}:{version}"
            )
            signed_at = str(onboarding["completedAt"])
            box = cast(dict[str, float], template["signatureBox"])
            pdf = await create_signed_onboarding_pdf(
                SigningInput(
                    template_bytes=self._read_template(
                        f"{tenant_prefix}-{template['sourceSuffix']}"
                    ),
                    signer_name=str(data["signatureFullName"]),
                    signature_method=cast(Any, data["signatureMethod"]),
                    signature_image_data=(
                        str(data["signatureImageData"])
                        if data.get("signatureMethod") == "drawn" and data.get("signatureImageData")
                        else None
                    ),
                    signed_at=signed_at,
                    audit_receipt=(f"onboarding.{version}.{template['code']}.{auth.student_id}"),
                    signature_box=SignatureBox(
                        x=box["x"],
                        y=box["y"],
                        width=box["width"],
                        height=box["height"],
                    ),
                )
            )
            digest = hashlib.sha256(pdf).hexdigest()
            storage_key = f"{auth.tenant_id}/{auth.student_id}/signed-onboarding/{document_id}.pdf"
            await storage.put(
                storage_key,
                pdf,
                content_type="application/pdf",
                sha256=digest,
            )
            await repository.save_student_signed_document(
                auth,
                {
                    "id": document_id,
                    "templateCode": template["code"],
                    "onboardingVersion": version,
                    "title": template["title"],
                    "fileName": template["fileName"],
                    "sizeBytes": len(pdf),
                    "storageKey": storage_key,
                    "sha256": digest,
                    "signerName": data["signatureFullName"],
                    "signatureMethod": data["signatureMethod"],
                    "signedAt": signed_at,
                },
                request_id,
            )
            created += 1
        return created


class PostgresPlatformService:
    """Dispatch canonical and preview operations without coupling them to FastAPI."""

    def __init__(
        self,
        repository: PostgresRepositoryBundle,
        storage: ObjectStorage,
        ai: StudentAI,
        signed_documents: SignedDocumentGenerator,
        worker_token: str,
        staff_preview: PreviewStaffWorkspaceRepository | None = None,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.ai = ai
        self.signed_documents = signed_documents
        self.worker_token = worker_token
        self.staff_preview = staff_preview

    async def dispatch(self, call: ServiceCall) -> object:
        operation = call.operation
        if operation == "health.liveness":
            return {"status": "ok", "service": "vv-api", "timestamp": self._timestamp()}
        if operation == "health.readiness":
            try:
                async with self.repository.portal.engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            except Exception as error:
                raise ApiError(
                    503, "DATABASE_UNAVAILABLE", "The API database is not ready"
                ) from error
            return {"status": "ready", "service": "vv-api", "timestamp": self._timestamp()}
        if operation == "public.get_portal_media":
            return await self._get_portal_media(self._path(call, "mediaFile", "file"))

        auth = self._auth(call)
        payload = dict(call.payload)
        key = call.idempotency_key
        portal = self.repository.portal
        platform = self.repository.platform
        staff = self.repository.staff

        if operation == "student.get_dashboard":
            return await platform.get_student_dashboard(auth)
        if operation == "student.get_academics":
            return await portal.get_student_academics(auth)
        if operation == "catalog.search_courses":
            return await portal.search_catalog_courses(
                auth, str(call.query_params.get("query", ""))
            )
        if operation == "student.get_financials":
            return await portal.get_student_financials(auth)
        if operation == "student.select_payment_plan":
            return await portal.select_financial_payment_plan(
                auth, str(payload["planId"]), self._key(key), call.request_id
            )
        if operation == "student.get_campus_life":
            return await portal.get_campus_life(auth)
        if operation == "student.register_campus_event":
            return await portal.register_campus_event(
                auth,
                self._path(call, "eventId", "id"),
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "admission.accept_offer":
            return await platform.accept_admission_offer(
                auth,
                self._path(call, "offerId", "offer_id"),
                self._key(key),
                call.request_id,
            )
        if operation == "activity.ingest_batch":
            events = payload.get("events")
            if not isinstance(events, list):
                raise BadRequestError("VALIDATION_ERROR", "events must be an array")
            return await platform.ingest_activity_events(auth, events, call.request_id)
        if operation == "student.get_bootstrap":
            bootstrap = await portal.get_student_bootstrap(auth)
            bootstrap["experienceUpdates"] = (
                await self.repository.managed.list_student_updates(auth)
                if self.repository.managed is not None
                else []
            )
            return bootstrap
        if operation == "student.decide_experience_update":
            if self.repository.managed is None:
                raise NotFoundError(
                    "STUDENT_EXPERIENCE_UPDATE_NOT_FOUND",
                    "The student experience update was not found",
                )
            return await self.repository.managed.decide_student_update(
                auth,
                self._path(call, "updateId", "id"),
                payload,
                call.request_id,
            )
        if operation == "student.get_onboarding":
            return await portal.get_student_onboarding(auth)
        if operation == "student.update_onboarding":
            return await portal.update_student_onboarding(auth, payload, call.request_id)
        if operation == "student.complete_onboarding":
            result = await portal.complete_student_onboarding(
                auth, payload, self._key(key), call.request_id
            )
            await self._ensure_signed(auth, result, call.request_id)
            return result
        if operation == "student.get_housing_plan":
            return await portal.get_student_housing_plan(auth)
        if operation == "student.update_housing_plan":
            return await portal.update_student_housing_plan(auth, payload, call.request_id)
        if operation == "student.list_requirements":
            return await portal.get_student_requirements(auth)
        if operation == "student.get_requirement":
            return await portal.get_student_requirement(
                auth, self._path(call, "requirementId", "id", "requirement_id")
            )
        if operation == "student.submit_requirement_response":
            return await portal.submit_student_requirement_response(
                auth,
                self._path(call, "requirementId", "id", "requirement_id"),
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "student.list_messages":
            return await portal.get_student_messages(auth)
        if operation == "student.mark_message_read":
            return await portal.mark_student_message_read(
                auth, self._path(call, "messageId", "id", "message_id"), call.request_id
            )
        if operation == "student.list_documents":
            await self._ensure_signed(
                auth, await portal.get_student_onboarding(auth), call.request_id
            )
            return await portal.get_student_documents(auth)
        if operation == "student.create_document":
            return await portal.create_student_document(
                auth, payload, self._key(key), call.request_id
            )
        if operation == "student.upload_document":
            return await self._upload_document(auth, call)
        if operation == "student.get_document_content":
            return await self._get_document_content(
                auth,
                self._path(call, "documentId", "id", "document_id"),
                cache_control="private, no-store",
            )
        if operation == "student.get_document_profile_photo":
            return await self._get_document_profile_photo(
                auth, self._path(call, "documentId", "id", "document_id")
            )
        if operation == "student.confirm_document_extraction":
            return await portal.confirm_student_document_extraction(
                auth,
                self._path(call, "documentId", "id", "document_id"),
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "student.retry_document_extraction":
            return await self._retry_document_extraction(
                auth,
                self._path(call, "documentId", "id", "document_id"),
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "internal.process_document_extraction":
            return await self._process_document_extraction(
                auth,
                self._path(call, "documentId", "id", "document_id"),
                call.request_id,
            )
        if operation == "internal.recover_document_extraction_reservation":
            return await self._recover_document_extraction_reservation(
                auth, self._path(call, "documentId", "id", "document_id"), call.request_id
            )
        if operation == "student.ask_edward":
            return await self._ask_edward(auth, payload, call.request_id)
        if operation == "student.list_appointments":
            return await portal.get_student_appointments(auth)
        if operation == "student.create_appointment":
            return await portal.create_student_appointment(
                auth, payload, self._key(key), call.request_id
            )
        if operation == "student.list_payments":
            return await portal.get_student_payments(auth)
        if operation == "student.create_deposit":
            return await portal.create_deposit_payment(
                auth, payload, self._key(key), call.request_id
            )
        if operation == "student.get_profile":
            return await portal.get_student_profile(auth)
        if operation == "student.update_profile":
            return await portal.update_student_profile(auth, payload, call.request_id)
        if operation == "student.get_help":
            return await portal.get_student_help(auth)
        if operation == "student.create_help_request":
            return await portal.create_student_help_request(
                auth, payload, self._key(key), call.request_id
            )
        if operation == "student.create_inquiry_message":
            return await portal.create_student_inquiry_message(
                auth,
                self._path(call, "inquiryId", "id", "inquiry_id"),
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "staff.get_workspace":
            configurations = await self._managed_configurations(auth)
            await self._preview().sync_managed_configurations(auth, configurations)
            (
                action_center,
                student,
                campus_life,
                inquiries,
                cohort,
                managed_content,
            ) = await asyncio.gather(
                staff.get_action_center(auth),
                staff.get_student_record(auth, auth.student_id),
                portal.get_campus_life(auth),
                portal.list_staff_help_requests(auth),
                staff.get_student_roster(auth),
                staff.get_managed_content(auth),
            )
            return await self._preview().get_workspace(
                auth,
                action_center=action_center,
                student=student,
                campus_life=campus_life,
                canonical_inquiries=inquiries,
                canonical_cohort=cohort,
                canonical_knowledge=cast(
                    Sequence[Mapping[str, Any]], managed_content.get("knowledgeBase", [])
                ),
                canonical_core_plays=cast(
                    Sequence[Mapping[str, Any]], managed_content.get("corePlays", [])
                ),
            )
        if operation == "staff.upload_portal_media":
            return await self._upload_staff_portal_media(auth, call)
        if operation == "staff.get_managed_configuration":
            return await self._managed_configuration(auth, self._path(call, "kind"))
        if operation == "staff.update_managed_configuration":
            kind = self._path(call, "kind")
            fallback = await self._preview().get_managed_configuration(auth, kind)
            if self.repository.managed is None:
                return await self._preview().update_managed_configuration(auth, kind, payload)
            published = await self.repository.managed.publish(
                auth,
                kind,
                payload,
                fallback,
                call.request_id,
            )
            await self._preview().sync_managed_configurations(auth, {kind: published})
            return published
        if operation == "staff.draft_managed_configuration":
            kind = str(payload.get("kind") or "")
            current = await self._managed_configuration(auth, kind)
            await self._preview().sync_managed_configurations(auth, {kind: current})
            return await self._preview().draft_managed_configuration(auth, payload)
        if operation == "staff.create_knowledge_card":
            return await staff.create_knowledge_card(auth, payload, call.request_id)
        if operation == "staff.update_knowledge_card":
            return await staff.update_knowledge_card(
                auth, self._path(call, "cardId", "id"), payload, call.request_id
            )
        if operation == "staff.create_core_play":
            return await staff.create_core_play(auth, payload, call.request_id)
        if operation == "staff.update_core_play":
            return await staff.update_core_play(
                auth, self._path(call, "playId", "id"), payload, call.request_id
            )
        if operation == "staff.update_inquiry":
            inquiry_id = self._path(call, "inquiryId", "id")
            canonical = await staff.update_inquiry(
                auth,
                inquiry_id,
                payload,
                call.request_id,
            )
            if canonical is not None:
                return canonical
            center = await staff.get_action_center(auth)
            return await self._preview().update_inquiry(
                auth,
                inquiry_id,
                payload,
                staff=cast(list[Mapping[str, Any]], center.get("staff", [])),
            )
        if operation == "staff.create_club":
            return await staff.create_club(auth, payload, call.request_id)
        if operation == "staff.update_club":
            return await staff.update_club(
                auth, self._path(call, "clubId", "id"), payload, call.request_id
            )
        if operation == "staff.simulate_outreach":
            return await self._preview().simulate_outreach(auth, payload)
        if operation == "staff.preview_edward":
            return await self._preview().preview_edward(auth, payload)
        if operation == "staff.get_action_center":
            return await staff.get_action_center(auth)
        if operation == "staff.create_work_item":
            return await staff.create_work_item(
                auth,
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "staff.get_realtime_events":
            raw_cursor = payload.get("afterCursor")
            raw_limit = payload.get("limit", 100)
            if raw_cursor is not None and (
                isinstance(raw_cursor, bool) or not isinstance(raw_cursor, int)
            ):
                raise BadRequestError(
                    "INVALID_EVENT_CURSOR",
                    "The realtime event cursor must be a non-negative integer",
                )
            if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
                raise BadRequestError(
                    "INVALID_EVENT_LIMIT",
                    "The realtime event limit must be an integer",
                )
            return await staff.get_realtime_events(auth, raw_cursor, raw_limit)
        if operation == "staff.get_work_item_detail":
            return await staff.get_work_item_detail(
                auth,
                self._path(call, "workItemId", "id", "work_item_id"),
            )
        if operation == "staff.update_work_item":
            return await staff.update_work_item(
                auth,
                self._path(call, "workItemId", "id", "work_item_id"),
                payload,
                call.request_id,
            )
        if operation == "staff.create_work_comment":
            return await staff.add_work_comment(
                auth,
                self._path(call, "workItemId", "id", "work_item_id"),
                payload,
                self._key(call.idempotency_key),
                call.request_id,
            )
        if operation == "staff.start_interaction":
            return await staff.start_interaction(
                auth,
                self._path(call, "workItemId", "id", "work_item_id"),
                payload,
                self._key(call.idempotency_key),
                call.request_id,
            )
        if operation == "staff.record_interaction_communication":
            return await staff.record_interaction_communication(
                auth,
                self._path(call, "interactionId", "id", "interaction_id"),
                payload,
                self._key(call.idempotency_key),
                call.request_id,
            )
        if operation == "staff.upload_call_recording":
            return await self._upload_staff_call_recording(auth, call)
        if operation == "staff.get_call_recording_content":
            return await self._get_staff_call_recording_content(
                auth,
                self._path(call, "recordingId", "id", "recording_id"),
            )
        if operation == "staff.retry_call_transcription":
            return await staff.retry_call_transcription(
                auth,
                self._path(call, "recordingId", "id", "recording_id"),
                payload,
            )
        if operation == "staff.complete_interaction":
            return await staff.complete_interaction(
                auth,
                self._path(call, "interactionId", "id", "interaction_id"),
                payload,
                call.request_id,
            )
        if operation == "staff.request_ai_refresh":
            return await staff.request_ai_refresh(
                auth,
                self._path(call, "workItemId", "id", "work_item_id"),
                payload,
                call.request_id,
            )
        if operation == "staff.get_action_rules":
            return await staff.get_action_rules(auth)
        if operation == "staff.get_notifications":
            return await staff.get_notifications(auth)
        if operation == "staff.mark_notification_read":
            return await staff.mark_notification_read(
                auth,
                self._path(call, "notificationId", "id", "notification_id"),
            )
        if operation == "staff.create_action_rule":
            return await staff.create_action_rule(auth, payload, call.request_id)
        if operation == "staff.update_action_rule":
            return await staff.update_action_rule(
                auth,
                self._path(call, "ruleId", "id", "rule_id"),
                payload,
                call.request_id,
            )
        if operation == "staff.get_student":
            return await staff.get_student_record(
                auth, self._path(call, "studentId", "id", "student_id")
            )
        if operation == "staff.update_student_preferences":
            return await staff.update_student_preferences(
                auth,
                self._path(call, "studentId", "id", "student_id"),
                payload,
                call.request_id,
            )
        if operation == "staff.review_document":
            return await staff.review_document(
                auth,
                self._path(call, "documentId", "id", "document_id"),
                payload,
                call.request_id,
            )
        raise ApiError(
            500,
            "PLATFORM_OPERATION_NOT_IMPLEMENTED",
            f"Unsupported operation: {operation}",
        )

    @staticmethod
    def _auth(call: ServiceCall) -> AuthContext:
        if call.auth is None:
            raise UnauthorizedError()
        return call.auth

    def _preview(self) -> PreviewStaffWorkspaceRepository:
        if self.staff_preview is None:
            raise NotFoundError(
                "STAFF_PREVIEW_DISABLED",
                "The extended staff preview is disabled in this environment",
            )
        return self.staff_preview

    async def _managed_configuration(self, auth: AuthContext, kind: str) -> JsonDict:
        fallback = await self._preview().get_managed_configuration(auth, kind)
        if self.repository.managed is None:
            return fallback
        return await self.repository.managed.get(auth, kind, fallback)

    async def _managed_configurations(self, auth: AuthContext) -> dict[str, JsonDict]:
        kinds = ("journeys", "campus_life", "academics")
        configurations = await asyncio.gather(
            *(self._managed_configuration(auth, kind) for kind in kinds)
        )
        return dict(zip(kinds, configurations, strict=True))

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _key(value: str | None) -> str:
        if value is None:
            raise BadRequestError(
                "IDEMPOTENCY_KEY_REQUIRED", "The Idempotency-Key header is required"
            )
        return value

    @staticmethod
    def _path(call: ServiceCall, *names: str) -> str:
        for name in names:
            value = call.path_params.get(name)
            if value:
                return str(value)
        raise BadRequestError("VALIDATION_ERROR", f"Missing path parameter {names[0]}")

    async def _ensure_signed(
        self, auth: AuthContext, onboarding: Mapping[str, Any], request_id: str
    ) -> int:
        return await self.signed_documents.ensure(
            auth=auth,
            onboarding=onboarding,
            repository=self.repository.portal,
            storage=self.storage,
            request_id=request_id,
        )

    async def _upload_document(self, auth: AuthContext, call: ServiceCall) -> JsonDict:
        upload = call.upload
        if upload is None:
            raise BadRequestError("FILE_REQUIRED", "Choose a document file to upload")
        category = upload.category or "other"
        file_name = validate_document_upload(
            upload.file_name, upload.mime_type, category, upload.content
        )
        digest = hashlib.sha256(upload.content).hexdigest()
        reserved = await self.repository.portal.reserve_student_document_upload(
            auth,
            {
                "fileName": file_name,
                "mimeType": upload.mime_type,
                "sizeBytes": len(upload.content),
                "category": category,
                "sha256": digest,
                "uploadBundleId": upload.upload_bundle_id,
            },
            self._key(call.idempotency_key),
            call.request_id,
            upload.requirement_id,
        )
        reference = await self.repository.portal.get_student_document_content_reference(
            auth, str(reserved["id"])
        )
        try:
            await self.storage.put(
                str(reference["storageKey"]),
                upload.content,
                content_type=upload.mime_type,
                sha256=digest,
            )
        except (OSError, StorageError) as error:
            raise ApiError(
                503,
                "DOCUMENT_STORAGE_UNAVAILABLE",
                "Your document record was saved, but the original could not be stored yet. "
                "Please retry this upload.",
            ) from error
        await self.repository.portal.claim_student_document_processing(
            auth, str(reserved["id"]), request_id=call.request_id
        )
        return await self.repository.portal.get_student_document(auth, str(reserved["id"]))

    async def _upload_staff_portal_media(self, auth: AuthContext, call: ServiceCall) -> JsonDict:
        if auth.actor_type != "staff":
            raise UnauthorizedError("Staff authentication is required")
        upload = call.upload
        if upload is None:
            raise BadRequestError("FILE_REQUIRED", "Choose an event image to upload")
        extension = _PORTAL_MEDIA_MIME_EXTENSIONS.get(upload.mime_type)
        if extension is None:
            raise BadRequestError(
                "UNSUPPORTED_MEDIA_TYPE",
                "Event images must be JPEG, PNG, or WebP",
            )
        if not upload.content or len(upload.content) > 5 * 1024 * 1024:
            raise BadRequestError(
                "INVALID_MEDIA_SIZE",
                "Event images must be between 1 byte and 5 MB",
            )
        media_file = f"{uuid4()}.{extension}"
        storage_key = f"public/portal-media/{media_file}"
        digest = hashlib.sha256(upload.content).hexdigest()
        try:
            await self.storage.put(
                storage_key,
                upload.content,
                content_type=upload.mime_type,
                sha256=digest,
            )
        except (OSError, StorageError) as error:
            raise ApiError(
                503,
                "MEDIA_STORAGE_UNAVAILABLE",
                "The event image could not be stored. Please retry the upload.",
            ) from error
        public_base_url = str(call.payload.get("publicBaseUrl") or "").rstrip("/")
        public_path = f"/v1/media/{media_file}"
        return {
            "fileName": upload.file_name,
            "mimeType": upload.mime_type,
            "sizeBytes": len(upload.content),
            "sha256": digest,
            "publicPath": public_path,
            "publicUrl": f"{public_base_url}{public_path}" if public_base_url else public_path,
        }

    async def _upload_staff_call_recording(
        self,
        auth: AuthContext,
        call: ServiceCall,
    ) -> JsonDict:
        if auth.actor_type != "staff":
            raise UnauthorizedError("Staff authentication is required")
        upload = call.upload
        if upload is None:
            raise BadRequestError("FILE_REQUIRED", "Choose a call recording to upload")
        extension = _CALL_RECORDING_MIME_EXTENSIONS.get(upload.mime_type)
        if extension is None or not 1 <= len(upload.content) <= 10 * 1024 * 1024:
            raise BadRequestError(
                "INVALID_CALL_RECORDING",
                "Use a supported call recording no larger than 10 MB",
            )
        interaction_id = self._path(call, "interactionId", "id", "interaction_id")
        recording_id = str(uuid4())
        digest = hashlib.sha256(upload.content).hexdigest()
        storage_key = (
            f"{auth.tenant_id}/staff-call-recordings/{interaction_id}/{recording_id}.{extension}"
        )
        reservation = await self.repository.staff.begin_call_recording(
            auth,
            interaction_id,
            recording_id=recording_id,
            request_key=self._key(call.idempotency_key),
            file_name=_safe_upload_file_name(upload.file_name),
            mime_type=upload.mime_type,
            size_bytes=len(upload.content),
            storage_key=storage_key,
            sha256=digest,
        )
        recording_id = str(reservation["id"])
        work_item_id = str(reservation["workItemId"])
        if bool(reservation["shouldUpload"]):
            try:
                await self.storage.put(
                    str(reservation["storageKey"]),
                    upload.content,
                    content_type=upload.mime_type,
                    sha256=digest,
                )
            except (OSError, StorageError) as error:
                await self.repository.staff.fail_call_recording_upload(
                    auth,
                    recording_id,
                    error,
                )
                return cast(
                    JsonDict,
                    await self.repository.staff.get_work_item_detail(auth, work_item_id),
                )
            work_item_id = await self.repository.staff.confirm_call_recording_upload(
                auth,
                recording_id,
                call.request_id,
            )
        return cast(
            JsonDict,
            await self.repository.staff.get_work_item_detail(auth, work_item_id),
        )

    async def _get_staff_call_recording_content(
        self,
        auth: AuthContext,
        recording_id: str,
    ) -> BinaryPayload:
        reference = await self.repository.staff.get_call_recording_reference(auth, recording_id)
        try:
            content = await self.storage.get(str(reference["storageKey"]))
        except StorageError as error:
            raise ApiError(
                503,
                "CALL_RECORDING_STORAGE_UNAVAILABLE",
                "The original call recording is temporarily unavailable",
            ) from error
        return BinaryPayload(
            data=content,
            media_type=str(reference["mimeType"]),
            file_name=str(reference["fileName"]),
            cache_control="private, no-store",
        )

    async def _get_portal_media(self, media_file: str) -> BinaryPayload:
        match = _PORTAL_MEDIA_FILE.fullmatch(media_file.lower())
        if match is None:
            raise NotFoundError("PORTAL_MEDIA_NOT_FOUND", "The requested media was not found")
        mime_type = {
            "jpg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
        }[match.group("extension")]
        try:
            content = await self.storage.get(f"public/portal-media/{media_file.lower()}")
        except (OSError, StorageError) as error:
            raise NotFoundError(
                "PORTAL_MEDIA_NOT_FOUND", "The requested media was not found"
            ) from error
        return BinaryPayload(
            data=content,
            media_type=mime_type,
            file_name=media_file.lower(),
            cache_control="public, max-age=31536000, immutable",
        )

    async def _get_document_content(
        self, auth: AuthContext, document_id: str, *, cache_control: str
    ) -> BinaryPayload:
        reference = await self.repository.portal.get_student_document_content_reference(
            auth, document_id
        )
        try:
            content = await self.storage.get(str(reference["storageKey"]))
        except (OSError, StorageError) as error:
            raise ApiError(
                503,
                "DOCUMENT_STORAGE_UNAVAILABLE",
                "The document content is temporarily unavailable",
            ) from error
        return BinaryPayload(
            data=content,
            media_type=str(reference["mimeType"]),
            file_name=str(reference["fileName"]),
            cache_control=cache_control,
        )

    async def _get_document_profile_photo(
        self, auth: AuthContext, document_id: str
    ) -> BinaryPayload:
        document = await self.repository.portal.get_student_document(auth, document_id)
        extraction = document.get("extraction")
        regions = extraction.get("visualRegions", []) if isinstance(extraction, dict) else []
        region = next(
            (
                item
                for item in regions
                if isinstance(item, dict) and item.get("kind") == "profile_photo"
            ),
            None,
        )
        if document.get("category") != "identity" or region is None:
            raise ApiError(
                404,
                "DOCUMENT_PROFILE_PHOTO_NOT_FOUND",
                "No profile photo was identified in this document",
            )
        binary = await self._get_document_content(
            auth, document_id, cache_control="private, max-age=300"
        )
        try:
            jpeg = await extract_student_document_image_region(
                binary.data,
                binary.media_type,
                NormalizedImageRegion(
                    x=float(region["x"]),
                    y=float(region["y"]),
                    width=float(region["width"]),
                    height=float(region["height"]),
                    page_number=(
                        int(region["pageNumber"]) if region.get("pageNumber") is not None else None
                    ),
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ApiError(
                404,
                "DOCUMENT_PROFILE_PHOTO_NOT_FOUND",
                "No profile photo was identified in this document",
            ) from error
        return BinaryPayload(
            data=jpeg,
            media_type="image/jpeg",
            file_name=f"profile-photo-{document_id}.jpg",
            cache_control="private, max-age=300",
        )

    async def _retry_document_extraction(
        self,
        auth: AuthContext,
        document_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        if payload:
            raise BadRequestError(
                "RETRY_EXTRACTION_BODY_NOT_ALLOWED",
                "Document extraction retry does not accept a request body",
            )
        current = await self.repository.portal.get_student_document(auth, document_id)
        if not can_retry_extraction(current.get("extraction")):
            return current
        await self.repository.portal.claim_student_document_processing(
            auth,
            document_id,
            retry=True,
            request_id=request_id,
            retry_idempotency_key=idempotency_key,
        )
        return await self.repository.portal.get_student_document(auth, document_id)

    async def _process_document_extraction(
        self, auth: AuthContext, document_id: str, request_id: str
    ) -> JsonDict:
        document = await self.repository.portal.get_student_document(auth, document_id)
        current_extraction = document.get("extraction")
        if (
            document.get("status") != "processing"
            or not isinstance(current_extraction, dict)
            or current_extraction.get("status") != "processing"
        ):
            return document
        try:
            binary = await self._get_document_content(
                auth, document_id, cache_control="private, no-store"
            )
            expected_type = document_type_for_category(str(document["category"]))
            extraction = await self._extract_with_single_retry(
                file_name=binary.file_name,
                mime_type=binary.media_type,
                content=binary.data,
                expected_document_type=expected_type,
                tenant_id=auth.tenant_id,
                student_id=auth.student_id,
                document_id=document_id,
                request_id=request_id,
            )
            if extraction.get("status") == "completed" and extraction.get("courses"):
                try:
                    courses = cast(list[Mapping[str, Any]], extraction["courses"])
                    context = await self.repository.portal.get_course_exemption_context(
                        auth, courses
                    )
                    if context is not None:
                        extraction[
                            "courseExemptionEvaluation"
                        ] = await self.ai.evaluate_course_exemptions(
                            tenant_id=auth.tenant_id,
                            student_id=auth.student_id,
                            document_id=document_id,
                            request_id=request_id,
                            courses=courses,
                            context=context,
                        )
                except BaseException as error:
                    extraction.setdefault("warnings", []).append(
                        "Course exemption matching is awaiting staff review."
                    )
                    if not classify_extraction_failure(error).retryable:
                        raise
            if expected_type == "immunization" and extraction.get("status") == "completed":
                policy = await self.repository.portal.get_immunization_policy_context(auth)
                if policy is not None:
                    try:
                        extraction[
                            "immunizationCompliance"
                        ] = await self.ai.evaluate_immunization_compliance(
                            tenant_id=auth.tenant_id,
                            student_id=auth.student_id,
                            document_id=document_id,
                            request_id=request_id,
                            extraction=extraction,
                            context=policy,
                        )
                    except BaseException:
                        extraction.setdefault("warnings", []).append(
                            "Immunization compliance is awaiting staff review."
                        )
            if document["category"] == "financial_aid" and extraction.get("status") == "completed":
                extraction.update(
                    {
                        "studentName": None,
                        "institutionName": None,
                        "issueDate": None,
                        "academicTerm": None,
                        "fields": [],
                        "courses": [],
                        "visualRegions": [],
                    }
                )
            if (
                document["category"] != "other"
                and extraction.get("status") == "completed"
                and extraction.get("documentType") != expected_type
            ):
                warning = (
                    f"This file was uploaded for a {expected_type.replace('_', ' ')} requirement, "
                    "but its contents look like "
                    f"{str(extraction.get('documentType')).replace('_', ' ')}. "
                    "The requirement was not advanced automatically."
                )
                extraction["warnings"] = [warning, *extraction.get("warnings", [])][:12]
            if extraction.get("status") == "completed":
                try:
                    profile, onboarding, requirements = await asyncio.gather(
                        self.repository.portal.get_student_profile(auth),
                        self.repository.portal.get_student_onboarding(auth),
                        self.repository.portal.get_student_requirements(auth),
                    )
                    extraction["contextMatches"] = match_document_to_student_context(
                        extraction,
                        _student_document_context(profile, onboarding, requirements),
                    )
                except Exception:
                    extraction["contextMatches"] = []
                    extraction.setdefault("warnings", []).append(
                        "The document was extracted, but automatic record matching is awaiting "
                        "staff review."
                    )
        except BaseException as error:
            failure = classify_extraction_failure(error)
            LOGGER.warning(
                "document_extraction_failed document_id=%s request_id=%s "
                "failure_code=%s exception_type=%s",
                document_id,
                request_id,
                failure.code,
                type(error).__name__,
            )
            extraction = cast(
                JsonDict,
                failed_extraction(str(document["fileName"]), str(document["category"]), error),
            )
        return await self.repository.portal.complete_student_document_extraction(
            auth, document_id, extraction, request_id
        )

    async def _extract_with_single_retry(self, **kwargs: Any) -> JsonDict:
        try:
            return await self.ai.extract_document(**kwargs, attempt=1)
        except BaseException as first:
            if not classify_extraction_failure(first).automatic_retryable:
                raise
            return await self.ai.extract_document(**kwargs, attempt=2)

    async def _recover_document_extraction_reservation(
        self, auth: AuthContext, document_id: str, request_id: str
    ) -> JsonDict:
        document = await self.repository.portal.get_student_document(auth, document_id)
        if document.get("status") != "uploaded" or document.get("extraction"):
            return document
        await self._get_document_content(auth, document_id, cache_control="private, no-store")
        await self.repository.portal.claim_student_document_processing(
            auth, document_id, request_id=request_id
        )
        return await self.repository.portal.get_student_document(auth, document_id)

    async def _ask_edward(
        self, auth: AuthContext, payload: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        message = str(payload.get("message", ""))
        page_context = str(payload.get("pageContext", ""))
        guarded = guarded_response(message)
        if guarded is not None:
            return guarded

        search = f"{message} {page_context}"
        dashboard, profile = await asyncio.gather(
            self.repository.platform.get_student_dashboard(auth),
            self.repository.portal.get_student_profile(auth),
        )
        offer = _mapping(dashboard.get("offer"))
        journey = _mapping(dashboard.get("journey"))
        offer_id = str(offer.get("id") or "")
        context: JsonDict = {
            "dashboard": dashboard,
            "profile": profile,
            "offerId": offer_id,
            "depositAmountCents": _integer(offer.get("depositAmountCents")),
            "depositPaid": False,
            "nextAction": journey.get("nextAction"),
        }
        sources = ["dashboard", "profile"]
        loads: list[tuple[str, Awaitable[JsonDict]]] = []
        if _EDWARD_DOCUMENT_TOPIC.search(search):
            loads.append(("documents", self.repository.portal.get_student_documents(auth)))
        if _EDWARD_ONBOARDING_TOPIC.search(search):
            loads.append(("onboarding", self.repository.portal.get_student_onboarding(auth)))
        if _EDWARD_PAYMENT_TOPIC.search(search):
            loads.append(("payments", self.repository.portal.get_student_payments(auth)))
        if _EDWARD_MESSAGE_TOPIC.search(search):
            loads.append(("messages", self.repository.portal.get_student_messages(auth)))
        if _EDWARD_ACADEMIC_TOPIC.search(search):
            loads.append(("academics", self.repository.portal.get_student_academics(auth)))
        if _EDWARD_FINANCIAL_TOPIC.search(search):
            loads.append(("financials", self.repository.portal.get_student_financials(auth)))
        if _EDWARD_CAMPUS_TOPIC.search(search):
            loads.append(("campus_life", self.repository.portal.get_campus_life(auth)))

        loaded = await asyncio.gather(*(call for _, call in loads))
        for (source, _), value in zip(loads, loaded, strict=True):
            sources.append(source)
            if source in {"documents", "onboarding", "payments"}:
                context[source] = value
            if source == "payments":
                context["depositPaid"] = any(
                    _mapping(item).get("type") == "enrollment_deposit"
                    and _mapping(item).get("status") == "succeeded"
                    and str(_mapping(item).get("offerId") or "") == offer_id
                    for item in _sequence(value.get("items"))
                )
            elif source == "messages":
                context["unreadMessages"] = _integer(value.get("unreadCount"))
            elif source == "academics":
                context["academicSummary"] = _academic_summary(value)
            elif source == "financials":
                context["financialSummary"] = _financial_summary(value)
            elif source == "campus_life":
                context["campusLifeSummary"] = _campus_life_summary(value)
        context["contextReceipts"] = [{"source": source} for source in sources]

        history = payload.get("history", [])
        response = await self.ai.ask_edward(
            message=message,
            page_context=page_context,
            history=history if isinstance(history, list) else [],
            student_context=context,
            tenant_id=auth.tenant_id,
            student_id=auth.student_id,
            request_id=request_id,
        )
        document_upload_category = None
        if _EDWARD_DOCUMENT_ACTION.search(message):
            document_upload_category = (
                "transcript" if "transcript" in message.lower() else "financial_aid"
            )
        appointment_type = None
        if _EDWARD_APPOINTMENT_ACTION.search(message):
            appointment_type = (
                "financial_aid"
                if _EDWARD_FINANCIAL_APPOINTMENT.search(message)
                else "enrollment_support"
            )
        normalized = normalize_response(
            response,
            EdwardActionAuthority(
                offer_id=offer_id,
                deposit_amount_cents=_integer(offer.get("depositAmountCents")),
                deposit_paid=bool(context["depositPaid"]),
                allow_deposit_payment=bool(offer_id and _EDWARD_DEPOSIT_ACTION.search(message)),
                document_upload_category=document_upload_category,
                appointment_type=appointment_type,
            ),
        )
        normalized["contextReceipts"] = [{"source": source} for source in sources]
        return normalized
