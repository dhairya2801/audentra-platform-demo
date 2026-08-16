"""Production application service composed from PostgreSQL repositories and adapters."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import text

from audentra.application.staff_workspace import (
    compose_staff_workspace,
    draft_managed_configuration,
    preview_edward,
    simulate_outreach,
)
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
from audentra.domain.student_state import derive_deposit_state
from audentra.infrastructure.documents.processing import (
    NormalizedImageRegion,
    SignatureBox,
    SigningInput,
    create_signed_onboarding_pdf,
    extract_student_document_image_region,
)
from audentra.infrastructure.storage.s3 import StorageError
from audentra.integrations.ai.edward_safety import (
    EdwardActionAuthority,
    guarded_response,
    normalize_response,
)
from audentra.integrations.ai.extraction import match_document_to_student_context
from audentra.integrations.assistant.classify import REQUEST_TYPES
from audentra.integrations.assistant.pipeline import AssistantPipeline, ModelComposer, ModelPlanner
from audentra.integrations.assistant.planner import TOOL_DESCRIPTIONS
from audentra.integrations.assistant.tools import AssistantToolHost
from audentra.integrations.assistant.trace import (
    AssistantTurnTrace,
    get_assistant_trace_recorder,
)
from audentra.integrations.staff_assistant.catalog import STAFF_TOOL_DESCRIPTIONS
from audentra.integrations.staff_assistant.classify import STAFF_REQUEST_TYPES
from audentra.integrations.staff_assistant.pipeline import (
    ModelComposer as StaffModelComposer,
)
from audentra.integrations.staff_assistant.pipeline import (
    ModelPlanner as StaffModelPlanner,
)
from audentra.integrations.staff_assistant.pipeline import (
    StaffAssistantPipeline,
)
from audentra.integrations.staff_assistant.safety import guarded_staff_response
from audentra.integrations.staff_assistant.tools import StaffAssistantToolHost

from .managed_configuration_repository import PostgresManagedConfigurationRepository
from .platform_repository import PostgresPlatformRepository
from .portal_repository import PostgresPortalRepository
from .staff_assistant_repository import PostgresStaffAssistantRepository
from .staff_repository import PostgresStaffRepository
from .tenant_repository import PostgresTenantRepository

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

ASTER_TENANT_ID = "00000000-0000-7000-8000-000000000001"
HARVARD_TENANT_ID = "00000000-0000-7000-8000-000000000002"
SIGNED_TEMPLATE_TENANT_PREFIXES = {
    ASTER_TENANT_ID: "aster",
    HARVARD_TENANT_ID: "harvard",
}

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


def _assistant_page_context(value: object) -> tuple[str | None, str | None]:
    """Accept both the legacy string form and the structured page context."""

    if isinstance(value, Mapping):
        path = str(value.get("path") or "").strip() or None
        label = str(value.get("label") or "").strip() or None
        return path, label
    if isinstance(value, str) and value.strip():
        return value.strip(), None
    return None, None


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
    tenant: PostgresTenantRepository | None = None
    staff_assistant: PostgresStaffAssistantRepository | None = None


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
        tenant_prefix = SIGNED_TEMPLATE_TENANT_PREFIXES.get(auth.tenant_id)
        if tenant_prefix is None:
            raise ApiError(
                503,
                "ONBOARDING_TEMPLATE_NOT_PROVISIONED",
                "Signed onboarding templates are not provisioned for this university",
            )
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
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.ai = ai
        self.signed_documents = signed_documents
        self.worker_token = worker_token

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
        if operation == "public.get_tenant_bootstrap":
            tenant = self.repository.tenant
            if tenant is None:
                raise ApiError(
                    503, "TENANT_CONFIGURATION_UNAVAILABLE", "Tenant configuration is unavailable"
                )
            slug = call.path_params.get("slug")
            if slug is not None:
                return await tenant.get_public_by_slug(str(slug))
            return await tenant.get_active_by_id(self._path(call, "tenantId"))

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
        if operation == "student.defer_experience_updates":
            if self.repository.managed is None:
                raise NotFoundError(
                    "STUDENT_EXPERIENCE_UPDATE_NOT_FOUND",
                    "The student experience update was not found",
                )
            return await self.repository.managed.defer_student_updates(
                auth,
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
        if operation == "student.get_realtime_events":
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
            return await portal.get_student_realtime_events(auth, raw_cursor, raw_limit)
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
        if operation == "student.create_assistant_conversation":
            page = _assistant_page_context(payload.get("pageContext"))
            return await portal.create_assistant_conversation(
                auth, page_path=page[0], page_label=page[1]
            )
        if operation == "student.get_assistant_conversation_messages":
            return await portal.get_assistant_conversation_messages(
                auth, self._path(call, "conversationId", "id")
            )
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
            return compose_staff_workspace(
                auth,
                action_center=action_center,
                student=student,
                campus_life=campus_life,
                inquiries=cast(Sequence[Mapping[str, Any]], inquiries),
                cohort=cast(Sequence[Mapping[str, Any]], cohort),
                managed_content=managed_content,
                configurations=configurations,
                generated_at=self._timestamp(),
            )
        if operation == "staff.get_tenant_configuration":
            tenant = self.repository.tenant
            if tenant is None:
                raise ApiError(
                    503, "TENANT_CONFIGURATION_UNAVAILABLE", "Tenant configuration is unavailable"
                )
            return await tenant.get_staff(auth)
        if operation == "staff.update_tenant_configuration":
            tenant = self.repository.tenant
            if tenant is None:
                raise ApiError(
                    503, "TENANT_CONFIGURATION_UNAVAILABLE", "Tenant configuration is unavailable"
                )
            return await tenant.update_staff(auth, payload, call.request_id)
        if operation == "staff.upload_portal_media":
            return await self._upload_staff_portal_media(auth, call)
        if operation == "staff.get_managed_configuration":
            return await self._managed_configuration(auth, self._path(call, "kind"))
        if operation == "staff.update_managed_configuration":
            kind = self._path(call, "kind")
            if self.repository.managed is None:
                raise NotFoundError(
                    "MANAGED_CONFIGURATION_REPOSITORY_UNAVAILABLE",
                    "The PostgreSQL managed configuration repository is unavailable",
                )
            return await self.repository.managed.publish(auth, kind, payload, call.request_id)
        if operation == "staff.draft_managed_configuration":
            kind = str(payload.get("kind") or "")
            current = await self._managed_configuration(auth, kind)
            return draft_managed_configuration(auth, current, payload)
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
            return await staff.update_inquiry(
                auth,
                inquiry_id,
                payload,
                call.request_id,
            )
        if operation == "staff.get_inquiry_thread":
            return await portal.get_staff_inquiry_thread(
                auth,
                self._path(call, "inquiryId", "id"),
            )
        if operation == "staff.create_club":
            return await staff.create_club(auth, payload, call.request_id)
        if operation == "staff.update_club":
            return await staff.update_club(
                auth, self._path(call, "clubId", "id"), payload, call.request_id
            )
        if operation == "staff.simulate_outreach":
            return simulate_outreach(auth, payload)
        if operation == "staff.preview_edward":
            return preview_edward(auth, payload)
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
        if operation == "staff.ask_edward":
            return await self._ask_staff_edward(auth, payload, call.request_id)
        if operation == "staff.create_assistant_conversation":
            return await self._staff_assistant_repo().create_conversation(auth)
        if operation == "staff.get_assistant_conversation_messages":
            return await self._staff_assistant_repo().get_conversation_messages(
                auth, self._path(call, "conversationId", "id")
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
        if operation == "staff.get_document_content":
            return await self._get_staff_document_content(
                auth,
                self._path(call, "documentId", "id", "document_id"),
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

    async def _managed_configuration(self, auth: AuthContext, kind: str) -> JsonDict:
        if self.repository.managed is None:
            raise NotFoundError(
                "MANAGED_CONFIGURATION_REPOSITORY_UNAVAILABLE",
                "The PostgreSQL managed configuration repository is unavailable",
            )
        return await self.repository.managed.get(auth, kind)

    async def _managed_configurations(self, auth: AuthContext) -> dict[str, JsonDict]:
        if self.repository.managed is None:
            raise NotFoundError(
                "MANAGED_CONFIGURATION_REPOSITORY_UNAVAILABLE",
                "The PostgreSQL managed configuration repository is unavailable",
            )
        return await self.repository.managed.list_active(auth)

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

    async def _get_staff_document_content(
        self,
        auth: AuthContext,
        document_id: str,
    ) -> BinaryPayload:
        reference = await self.repository.staff.get_document_content_reference(auth, document_id)
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
            cache_control="private, no-store",
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

    def _assistant_host(self, auth: AuthContext) -> AssistantToolHost:
        """Primitive live reads for the assistant pipeline, per request.

        Every primitive is the same repository read the portal page uses, so
        Edward's answer is exactly as fresh as the page a student would open.
        """

        portal = self.repository.portal
        platform = self.repository.platform
        return AssistantToolHost(
            {
                "profile": lambda: portal.get_student_profile(auth),
                "requirements": lambda: portal.get_student_requirements(auth),
                "documents": lambda: portal.get_student_documents(auth),
                "payments": lambda: portal.get_student_payments(auth),
                "financials": lambda: portal.get_student_financials(auth),
                "dashboard": lambda: platform.get_student_dashboard(auth),
                "onboarding": lambda: portal.get_student_onboarding(auth),
                "housing_plan": lambda: portal.get_student_housing_plan(auth),
                "appointments": lambda: portal.get_student_appointments(auth),
                "help": lambda: portal.get_student_help(auth),
                "academics": lambda: portal.get_student_academics(auth),
                "campus_life": lambda: portal.get_campus_life(auth),
                "messages": lambda: portal.get_student_messages(auth),
            }
        )

    async def _ask_edward(
        self, auth: AuthContext, payload: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        message = str(payload.get("message", ""))
        page_path, page_label = _assistant_page_context(payload.get("pageContext"))
        conversation_id = payload.get("conversationId")
        client_message_id = payload.get("clientMessageId")
        persist = conversation_id is not None or client_message_id is not None
        trace = AssistantTurnTrace(
            trace_id=request_id,
            tenant_id=auth.tenant_id,
            student_id=auth.student_id,
            conversation_id=str(conversation_id) if conversation_id else None,
            input_mode=str(payload.get("inputMode") or "text"),
            user_message=message,
        )

        if isinstance(client_message_id, str) and client_message_id:
            replay = await self.repository.portal.find_assistant_exchange_by_client_id(
                auth, client_message_id
            )
            if replay is not None:
                trace.path = "idempotent_replay"
                trace.final_message = str(replay.get("message") or "")
                get_assistant_trace_recorder().record(trace)
                return replay

        guarded = guarded_response(message)
        if guarded is not None:
            response = dict(guarded)
            trace.path = "pre_pipeline_safety_gate"
            trace.response_source = "deterministic"
            trace.provider = str(response.get("provider") or "guided")
        else:
            history: Sequence[Mapping[str, Any]]
            if conversation_id is not None:
                history = await self.repository.portal.get_recent_assistant_history(
                    auth, str(conversation_id)
                )
                trace.history_source = "server"
            else:
                client_history = payload.get("history", [])
                history = client_history if isinstance(client_history, list) else []
                trace.history_source = "client_fallback" if history else "none"
            pipeline = AssistantPipeline(
                self._assistant_host(auth),
                model_composer=self._assistant_composer(auth, request_id),
                model_planner=self._assistant_planner(auth, request_id),
            )
            result = await pipeline.execute(
                message=message,
                history=history,
                page_path=page_path,
                page_label=page_label,
                trace=trace,
            )
            response = {
                "message": result.message,
                "blocks": result.blocks,
                "provider": result.provider,
                "model": result.model,
                "usage": result.usage,
                "suggestedActions": result.suggested_actions,
                "contextReceipts": result.context_receipts,
                "widgets": [
                    {"type": "deposit_payment"},
                    {"type": "document_upload"},
                    {"type": "appointment"},
                ],
            }
            response = normalize_response(
                response, await self._edward_action_authority(auth, message)
            )
            response["contextReceipts"] = result.context_receipts

        if persist:
            stored = await self.repository.portal.append_assistant_exchange(
                auth,
                conversation_id=str(conversation_id) if conversation_id else None,
                page_path=page_path,
                page_label=page_label,
                user_message={
                    "content": message,
                    "clientMessageId": client_message_id,
                    "inputMode": payload.get("inputMode") or "text",
                },
                assistant_message={
                    "content": response.get("message"),
                    "provider": response.get("provider"),
                    "model": response.get("model"),
                    "usage": response.get("usage"),
                    "blocks": response.get("blocks"),
                    "contextReceipts": response.get("contextReceipts"),
                    "suggestedActions": response.get("suggestedActions"),
                    "widgets": response.get("widgets"),
                },
                request_id=request_id,
            )
            response.update(stored)
            trace.conversation_id = str(stored.get("conversationId") or "") or trace.conversation_id
            trace.user_message_id = str(stored.get("userMessageId") or "") or None
            trace.assistant_message_id = str(stored.get("assistantMessageId") or "") or None
        response["requestId"] = request_id
        trace.final_message = trace.final_message or str(response.get("message") or "")
        get_assistant_trace_recorder().record(trace)
        return response

    async def _edward_action_authority(
        self, auth: AuthContext, message: str
    ) -> EdwardActionAuthority:
        """Server-side authority over action widgets, loaded only when an
        action intent appears in the message."""

        wants_deposit = bool(_EDWARD_DEPOSIT_ACTION.search(message))
        wants_document = bool(_EDWARD_DOCUMENT_ACTION.search(message))
        wants_appointment = bool(_EDWARD_APPOINTMENT_ACTION.search(message))
        offer_id = ""
        deposit_amount = 0
        deposit_paid = False
        if wants_deposit:
            dashboard, payments = await asyncio.gather(
                self.repository.platform.get_student_dashboard(auth),
                self.repository.portal.get_student_payments(auth),
            )
            offer_id = str(_mapping(dashboard.get("offer")).get("id") or "")
            state = derive_deposit_state(dashboard=dashboard, payments=payments)
            deposit_amount = state.amount_cents
            # A pending deposit must not produce a second payment widget.
            deposit_paid = state.paid or state.pending
        document_upload_category = None
        if wants_document:
            document_upload_category = (
                "transcript" if "transcript" in message.lower() else "financial_aid"
            )
        appointment_type = None
        if wants_appointment:
            appointment_type = (
                "financial_aid"
                if _EDWARD_FINANCIAL_APPOINTMENT.search(message)
                else "enrollment_support"
            )
        return EdwardActionAuthority(
            offer_id=offer_id,
            deposit_amount_cents=deposit_amount,
            deposit_paid=deposit_paid,
            allow_deposit_payment=bool(offer_id and wants_deposit),
            document_upload_category=document_upload_category,
            appointment_type=appointment_type,
        )

    def _assistant_planner(self, auth: AuthContext, request_id: str) -> ModelPlanner | None:
        planner = getattr(self.ai, "plan_assistant_tool_reads", None)
        if planner is None:
            return None

        async def plan(
            *,
            message: str,
            page_label: str | None = None,
            page_path: str | None = None,
        ) -> Mapping[str, Any] | None:
            return cast(
                Mapping[str, Any] | None,
                await planner(
                    message=message,
                    page_label=page_label,
                    page_path=page_path,
                    allowed_request_types=REQUEST_TYPES,
                    available_tools=TOOL_DESCRIPTIONS,
                    tenant_id=auth.tenant_id,
                    student_id=auth.student_id,
                    request_id=request_id,
                ),
            )

        return plan

    def _assistant_composer(self, auth: AuthContext, request_id: str) -> ModelComposer | None:
        writer = getattr(self.ai, "write_grounded_answer", None)
        if writer is None:
            return None

        async def compose(
            *,
            question: str,
            evidence_texts: list[str],
            draft_answer: str,
            presented_blocks: list[str] | None = None,
            feedback: str | None = None,
        ) -> Mapping[str, Any] | None:
            return cast(
                Mapping[str, Any] | None,
                await writer(
                    question=question,
                    evidence_texts=evidence_texts,
                    draft_answer=draft_answer,
                    presented_blocks=presented_blocks,
                    feedback=feedback,
                    tenant_id=auth.tenant_id,
                    student_id=auth.student_id,
                    request_id=request_id,
                    attempt=2 if feedback else 1,
                ),
            )

        return compose

    # Staff Edward (read-only staff assistant)
    # ------------------------------------------------------------------

    def _staff_assistant_repo(self) -> PostgresStaffAssistantRepository:
        if self.repository.staff_assistant is None:
            raise ApiError(
                503,
                "STAFF_ASSISTANT_UNAVAILABLE",
                "The staff assistant is not configured on this host",
            )
        return self.repository.staff_assistant

    def _staff_assistant_host(self, auth: AuthContext) -> StaffAssistantToolHost:
        """Primitive live reads for the staff assistant, per request.

        Every primitive is a pure, tenant-scoped read. Student-scoped
        primitives rebind the student id only after the pipeline has resolved
        and validated it against the tenant roster; the SQL underneath
        filters on the authenticated tenant regardless. Nothing here touches
        the preview workspace repository or any synthetic risk field, and
        nothing here writes.
        """

        portal = self.repository.portal
        staff = self.repository.staff
        assistant = self._staff_assistant_repo()

        def student_auth(student_id: str) -> AuthContext:
            # This is an internal, read-only projection after the staff tenant
            # and student referent have been validated. Portal repositories
            # still enforce student read semantics; tenant and staff actor id
            # remain server-bound.
            return replace(auth, student_id=student_id, actor_type="student")

        async def cohort_result(cohort: Any, limit: int) -> Mapping[str, Any]:
            result = await assistant.find_students(auth, cohort, limit=limit)
            return result.as_json()

        async def student_overview(student_id: str) -> Mapping[str, Any]:
            overview = await assistant.get_student_overview(auth, student_id)
            return overview or {}

        async def inquiries() -> Mapping[str, Any]:
            items = await portal.list_staff_help_requests(auth)
            return {"items": items}

        return StaffAssistantToolHost(
            {
                "search_students": lambda **kwargs: assistant.search_students(auth, **kwargs),
                # The domain has already validated the cohort vocabulary;
                # tenant and staff identities remain bound to `auth` here.
                "find_students": lambda cohort, limit: cohort_result(cohort, limit),
                "summarize_students": lambda cohort, group_by, limit: assistant.summarize_students(
                    auth,
                    cohort,
                    group_by=group_by,
                    limit=limit,
                ),
                "student_overview": lambda student_id: student_overview(student_id),
                "student_requirements": lambda student_id: portal.get_student_requirements(
                    student_auth(student_id)
                ),
                "student_documents": lambda student_id: portal.get_student_documents(
                    student_auth(student_id)
                ),
                "student_financials": lambda student_id: portal.get_student_financials(
                    student_auth(student_id)
                ),
                "student_payments": lambda student_id: portal.get_student_payments(
                    student_auth(student_id)
                ),
                "student_housing_plan": lambda student_id: portal.get_student_housing_plan(
                    student_auth(student_id)
                ),
                "student_appointments": lambda student_id: portal.get_student_appointments(
                    student_auth(student_id)
                ),
                "student_work_items": lambda student_id: assistant.get_student_work_items(
                    auth, student_id
                ),
                "communication_history": (
                    lambda student_id, channel=None: assistant.get_student_communication_history(
                        auth, student_id, channel=channel
                    )
                ),
                "engagement": lambda student_id: assistant.get_student_engagement_signals(
                    auth, student_id
                ),
                "timeline": lambda student_id, limit=40: assistant.get_student_timeline(
                    auth, student_id, limit=limit
                ),
                "attention": lambda limit=15: assistant.get_students_needing_attention(
                    auth, limit=limit
                ),
                # Pure queue read: never the mutating get_action_center path.
                "work_queue": lambda: staff.get_work_queue(auth),
                "work_item_detail": lambda work_item_id: staff.get_work_item_detail(
                    auth, work_item_id, ensure_document_work_items=False
                ),
                "inquiries": inquiries,
                "inquiry_thread": lambda inquiry_id: portal.get_staff_inquiry_thread(
                    auth, inquiry_id
                ),
                "guidance": lambda: assistant.get_staff_guidance(auth),
                "action_rules": lambda: staff.get_action_rules(auth),
            },
            staff_member_id=auth.actor_id,
        )

    async def _ask_staff_edward(
        self, auth: AuthContext, payload: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        if auth.actor_type != "staff":
            raise UnauthorizedError("Staff authentication is required")
        repo = self._staff_assistant_repo()
        message = str(payload.get("message", ""))
        conversation_id = payload.get("conversationId")
        client_message_id = payload.get("clientMessageId")
        persist = conversation_id is not None or client_message_id is not None
        trace = AssistantTurnTrace(
            trace_id=request_id,
            tenant_id=auth.tenant_id,
            assistant_kind="staff",
            actor_type="staff",
            staff_member_id=auth.actor_id,
            conversation_id=str(conversation_id) if conversation_id else None,
            user_message=message,
        )

        if isinstance(client_message_id, str) and client_message_id:
            replay = await repo.find_exchange_by_client_id(auth, client_message_id)
            if replay is not None:
                trace.path = "idempotent_replay"
                trace.final_message = str(replay.get("message") or "")
                get_assistant_trace_recorder().record(trace)
                return replay

        guarded = guarded_staff_response(message)
        resolved_student_id: str | None = None
        if guarded is not None:
            response = dict(guarded)
            trace.path = "pre_pipeline_safety_gate"
            trace.response_source = "deterministic"
            trace.provider = str(response.get("provider") or "guided")
        else:
            # The durable conversation store is the only history source for
            # staff turns, and it also carries the active student referent so
            # "what is she missing?" resolves server-side.
            history: Sequence[Mapping[str, Any]] = []
            context_student_id: str | None = None
            if conversation_id is not None:
                recent = await repo.get_recent_history(auth, str(conversation_id))
                history = list(recent.get("history", []))
                context_student_id = recent.get("activeStudentId")
                trace.history_source = "server"
            pipeline = StaffAssistantPipeline(
                self._staff_assistant_host(auth),
                model_composer=self._staff_assistant_composer(auth, request_id),
                model_planner=self._staff_assistant_planner(auth, request_id),
            )
            result = await pipeline.execute(
                message=message,
                history=history,
                context_student_id=context_student_id,
                trace=trace,
            )
            resolved_student_id = result.resolved_student_id
            trace.student_id = resolved_student_id
            response = {
                "message": result.message,
                "blocks": result.blocks,
                "provider": result.provider,
                "model": result.model,
                "usage": result.usage,
                "contextReceipts": result.context_receipts,
                "resolvedStudent": (
                    {
                        "id": result.resolved_student_id,
                        "name": result.resolved_student_name,
                    }
                    if result.resolved_student_id
                    else None
                ),
            }

        if persist:
            stored = await repo.append_exchange(
                auth,
                conversation_id=str(conversation_id) if conversation_id else None,
                user_message={
                    "content": message,
                    "clientMessageId": client_message_id,
                },
                assistant_message={
                    "content": response.get("message"),
                    "provider": response.get("provider"),
                    "model": response.get("model"),
                    "usage": response.get("usage"),
                    "blocks": response.get("blocks"),
                    "contextReceipts": response.get("contextReceipts"),
                },
                referenced_student_id=resolved_student_id,
                request_id=request_id,
            )
            response.update(stored)
            trace.conversation_id = str(stored.get("conversationId") or "") or trace.conversation_id
            trace.user_message_id = str(stored.get("userMessageId") or "") or None
            trace.assistant_message_id = str(stored.get("assistantMessageId") or "") or None
        response["requestId"] = request_id
        trace.final_message = trace.final_message or str(response.get("message") or "")
        get_assistant_trace_recorder().record(trace)
        return response

    def _staff_assistant_planner(
        self, auth: AuthContext, request_id: str
    ) -> StaffModelPlanner | None:
        planner = getattr(self.ai, "plan_staff_tool_reads", None)
        if planner is None:
            return None

        async def plan(*, message: str, student_resolved: bool = False) -> Mapping[str, Any] | None:
            return cast(
                Mapping[str, Any] | None,
                await planner(
                    message=message,
                    allowed_request_types=STAFF_REQUEST_TYPES,
                    available_tools=STAFF_TOOL_DESCRIPTIONS,
                    student_resolved=student_resolved,
                    tenant_id=auth.tenant_id,
                    staff_member_id=auth.actor_id,
                    request_id=request_id,
                ),
            )

        return plan

    def _staff_assistant_composer(
        self, auth: AuthContext, request_id: str
    ) -> StaffModelComposer | None:
        writer = getattr(self.ai, "write_staff_grounded_answer", None)
        if writer is None:
            return None

        async def compose(
            *,
            question: str,
            evidence_texts: list[str],
            draft_answer: str,
            presented_blocks: list[str] | None = None,
            feedback: str | None = None,
        ) -> Mapping[str, Any] | None:
            return cast(
                Mapping[str, Any] | None,
                await writer(
                    question=question,
                    evidence_texts=evidence_texts,
                    draft_answer=draft_answer,
                    presented_blocks=presented_blocks,
                    feedback=feedback,
                    tenant_id=auth.tenant_id,
                    staff_member_id=auth.actor_id,
                    request_id=request_id,
                    attempt=2 if feedback else 1,
                ),
            )

        return compose
