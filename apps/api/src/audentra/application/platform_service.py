"""Compatibility-first orchestration behind the FastAPI dispatch port."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, UnauthorizedError
from audentra.core.ports import BinaryPayload, ServiceCall
from audentra.domain.documents import (
    can_retry_extraction,
    classify_extraction_failure,
    create_signed_pdf,
    deterministic_document_id,
    document_type_for_category,
    failed_extraction,
    validate_document_upload,
)
from audentra.infrastructure.memory.adapters import FakeDocumentStorage, FakeStudentAI
from audentra.infrastructure.memory.store import InMemoryPlatformStore
from audentra.integrations.ai.edward_safety import guarded_response, normalize_response
from audentra.integrations.assistant.pipeline import AssistantPipeline
from audentra.integrations.assistant.tools import AssistantToolHost

ACTIVITY_PROPERTY_ALLOWLISTS: dict[str, frozenset[str]] = {
    "ui.portal_session_started.v1": frozenset({"entry_point"}),
    "ui.dashboard_viewed.v1": frozenset({"projection_version", "journey_status"}),
    "ui.admission_offer_viewed.v1": frozenset({"offer_id", "offer_status"}),
    "ui.admission_decision_started.v1": frozenset({"offer_id", "decision", "entry_point"}),
    "ui.enrollment_started.v1": frozenset({"journey_id", "entry_point"}),
    "ui.enrollment_step_viewed.v1": frozenset({"step_code", "entry_point"}),
    "ui.portal_section_viewed.v1": frozenset({"section", "entry_point"}),
    "ui.enrollment_task_viewed.v1": frozenset({"task_code", "task_status", "entry_point"}),
    "ui.enrollment_task_abandoned.v1": frozenset(
        {"task_code", "task_status", "duration_bucket", "last_interaction"}
    ),
    "ui.financial_aid_viewed.v1": frozenset({"surface", "aid_status"}),
    "ui.course_catalog_searched.v1": frozenset({"query_length_bucket", "result_count"}),
    "ui.course_viewed.v1": frozenset({"course_code", "surface"}),
    "ui.exemption_reviewed.v1": frozenset({"rule_code", "recommendation_status"}),
    "ui.campus_event_viewed.v1": frozenset({"event_id", "surface"}),
    "ui.club_viewed.v1": frozenset({"club_id", "surface"}),
    "ui.edward_context_receipts_received.v1": frozenset({"source_count", "page_context"}),
    "ui.edward_tool_invoked.v1": frozenset({"tool_name", "page_context"}),
    "ui.edward_action_widget_viewed.v1": frozenset({"widget_type", "page_context"}),
    "ui.edward_action_completed.v1": frozenset({"widget_type", "outcome"}),
    "ui.help_opened.v1": frozenset({"context", "surface", "topic_code"}),
}
PROHIBITED_ACTIVITY_TERMS = (
    "password",
    "token",
    "secret",
    "email",
    "phone",
    "address",
    "government",
    "payment",
    "card",
    "ssn",
)

SIGNED_TEMPLATES = (
    {
        "code": "ferpa_release",
        "title": "FERPA Information Release",
        "fileName": "ferpa-information-release-signed.pdf",
    },
    {
        "code": "enrollment_acknowledgment",
        "title": "Enrollment Information Acknowledgment",
        "fileName": "enrollment-information-acknowledgment-signed.pdf",
    },
)


class InMemoryPlatformService:
    """Application service implementing the portal compatibility operations."""

    def __init__(
        self,
        store: InMemoryPlatformStore | None = None,
        storage: FakeDocumentStorage | None = None,
        ai: FakeStudentAI | None = None,
        *,
        worker_token: str = "test-document-worker-token",  # noqa: S107
    ) -> None:
        self.store = store or InMemoryPlatformStore()
        self.storage = storage or FakeDocumentStorage()
        self.ai = ai or FakeStudentAI()
        self.worker_token = worker_token

    async def dispatch(self, call: ServiceCall) -> object:
        operation = call.operation
        if operation == "health.liveness":
            return {"status": "ok", "service": "vv-api"}
        if operation == "health.readiness":
            return {"status": "ready", "service": "vv-api"}

        auth = self._auth(call)
        payload = dict(call.payload)
        if call.idempotency_key:
            payload["idempotencyKey"] = call.idempotency_key

        if operation == "student.get_dashboard":
            return self.store.get_dashboard(auth)
        if operation == "student.get_academics":
            return self.store.get_academics(auth)
        if operation == "catalog.search_courses":
            query = self._query(call, "query", default="")
            return self.store.search_courses(auth, query)
        if operation == "student.get_financials":
            return self.store.get_financials(auth)
        if operation == "student.select_payment_plan":
            self._idempotency_key(payload)
            self.store.authorize(auth)
            return {"planId": payload["planId"], "status": "enrolled"}
        if operation == "student.get_campus_life":
            return self.store.get_campus_life(auth)
        if operation == "admission.accept_offer":
            return self.store.accept_offer(
                auth,
                self._path(call, "offer_id", "offerId"),
                self._idempotency_key(payload),
            )
        if operation == "activity.ingest_batch":
            return self._ingest_activity(auth, payload)
        if operation == "student.get_bootstrap":
            return self.store.get_bootstrap(auth)
        if operation == "student.get_onboarding":
            return self.store.get_onboarding(auth)
        if operation == "student.update_onboarding":
            return self.store.update_onboarding(auth, payload)
        if operation == "student.complete_onboarding":
            result = self.store.complete_onboarding(auth, payload, self._idempotency_key(payload))
            await self._ensure_signed_documents(auth, result)
            return result
        if operation == "student.get_housing_plan":
            return self.store.get_housing_plan(auth)
        if operation == "student.update_housing_plan":
            return self.store.update_housing_plan(auth, payload)
        if operation == "student.list_requirements":
            return self.store.get_requirements(auth)
        if operation == "student.get_requirement":
            return self.store.get_requirement(
                auth, self._path(call, "id", "requirement_id", "requirementId")
            )
        if operation == "student.list_messages":
            return self.store.get_messages(auth)
        if operation == "student.mark_message_read":
            return self.store.mark_message_read(
                auth, self._path(call, "id", "message_id", "messageId")
            )
        if operation == "student.list_documents":
            await self._ensure_signed_documents(auth, self.store.get_onboarding(auth))
            return self.store.get_documents(auth)
        if operation == "student.create_document":
            body = self._without_idempotency(payload)
            return self.store.create_document(auth, body, self._idempotency_key(payload))
        if operation == "student.upload_document":
            return await self._upload_document(auth, call, payload)
        if operation == "student.get_document_content":
            return await self._get_document_content(
                auth,
                self._path(call, "id", "document_id", "documentId"),
                cache_control="private, no-store",
            )
        if operation == "student.get_document_profile_photo":
            return await self._get_document_profile_photo(
                auth, self._path(call, "id", "document_id", "documentId")
            )
        if operation == "student.confirm_document_extraction":
            self._idempotency_key(payload)
            return self.store.confirm_document_extraction(
                auth,
                self._path(call, "id", "document_id", "documentId"),
                self._without_idempotency(payload),
            )
        if operation == "student.retry_document_extraction":
            return self._retry_document_extraction(
                auth,
                self._path(call, "id", "document_id", "documentId"),
                payload,
            )
        if operation == "internal.process_document_extraction":
            return await self._process_document_extraction(
                auth, self._path(call, "id", "document_id", "documentId"), call.request_id
            )
        if operation == "internal.recover_document_extraction_reservation":
            return await self._recover_document_extraction_reservation(
                auth, self._path(call, "id", "document_id", "documentId")
            )
        if operation == "student.ask_edward":
            return await self._ask_edward(auth, payload, call.request_id)
        if operation == "student.create_assistant_conversation":
            self.store.authorize(auth)
            return self.store.create_assistant_conversation(auth, payload.get("pageContext"))
        if operation == "student.get_assistant_conversation_messages":
            self.store.authorize(auth)
            return self.store.get_assistant_conversation_messages(
                auth, self._path(call, "conversationId", "id")
            )
        if operation == "student.list_appointments":
            return self.store.get_appointments(auth)
        if operation == "student.create_appointment":
            return self.store.create_appointment(
                auth, self._without_idempotency(payload), self._idempotency_key(payload)
            )
        if operation == "student.list_payments":
            return self.store.get_payments(auth)
        if operation == "student.create_deposit":
            return self.store.create_deposit(
                auth, self._without_idempotency(payload), self._idempotency_key(payload)
            )
        if operation == "student.get_profile":
            return self.store.get_profile(auth)
        if operation == "student.update_profile":
            return self.store.update_profile(auth, payload)
        if operation == "student.get_help":
            return self.store.get_help(auth)
        if operation == "student.create_help_request":
            return self.store.create_help_request(
                auth, self._without_idempotency(payload), self._idempotency_key(payload)
            )
        if operation == "staff.get_action_center":
            return self.store.get_action_center(auth)
        if operation == "staff.update_work_item":
            return self.store.update_work_item(
                auth, self._path(call, "id", "work_item_id", "workItemId"), payload
            )
        if operation == "staff.get_student":
            return self.store.get_student_record(
                auth, self._path(call, "id", "student_id", "studentId")
            )
        if operation == "staff.update_student_preferences":
            return self.store.update_student_preferences(
                auth, self._path(call, "id", "student_id", "studentId"), payload
            )
        if operation == "staff.review_document":
            return self.store.review_document(
                auth, self._path(call, "id", "document_id", "documentId"), payload
            )
        raise ApiError(
            500, "PLATFORM_OPERATION_NOT_IMPLEMENTED", f"Unsupported operation: {operation}"
        )

    @staticmethod
    def _auth(call: ServiceCall) -> AuthContext:
        if call.auth is None:
            raise UnauthorizedError()
        return call.auth

    @staticmethod
    def _path(call: ServiceCall, *names: str) -> str:
        for name in names:
            value = call.path_params.get(name)
            if value:
                return value
        raise BadRequestError("VALIDATION_ERROR", f"Missing path parameter {names[0]}")

    @staticmethod
    def _query(call: ServiceCall, name: str, *, default: str) -> str:
        value = call.query_params.get(name, default)
        return str(value)

    @staticmethod
    def _idempotency_key(payload: Mapping[str, Any]) -> str:
        for field in ("idempotencyKey", "idempotency_key", "Idempotency-Key"):
            value = payload.get(field)
            if isinstance(value, str) and 8 <= len(value) <= 128:
                return value
        raise BadRequestError("IDEMPOTENCY_KEY_REQUIRED", "The Idempotency-Key header is required")

    @staticmethod
    def _without_idempotency(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: deepcopy(value)
            for key, value in payload.items()
            if key not in {"idempotencyKey", "idempotency_key", "Idempotency-Key"}
        }

    def _ingest_activity(self, auth: AuthContext, payload: Mapping[str, Any]) -> dict[str, int]:
        self.store.authorize(auth)
        events = payload.get("events")
        if not isinstance(events, list):
            raise BadRequestError("VALIDATION_ERROR", "events must be an array")
        accepted = 0
        for event in events:
            if not isinstance(event, dict):
                raise BadRequestError("VALIDATION_ERROR", "events must contain objects")
            event_name = event.get("eventName")
            allowlist = ACTIVITY_PROPERTY_ALLOWLISTS.get(str(event_name))
            if allowlist is None:
                raise BadRequestError("VALIDATION_ERROR", "The activity event name is invalid")
            properties = event.get("properties", {})
            if not isinstance(properties, dict):
                raise BadRequestError("INVALID_ACTIVITY_PROPERTY", "Properties must be an object")
            for key, value in properties.items():
                if any(term in key.lower() for term in PROHIBITED_ACTIVITY_TERMS):
                    raise BadRequestError(
                        "PROHIBITED_ACTIVITY_PROPERTY", f'Property "{key}" may not be captured'
                    )
                if key not in allowlist:
                    raise BadRequestError(
                        "UNKNOWN_ACTIVITY_PROPERTY",
                        f'Property "{key}" is not allowed for {event_name}',
                    )
                if value is not None and not isinstance(value, (str, int, float, bool)):
                    raise BadRequestError(
                        "INVALID_ACTIVITY_PROPERTY", f'Property "{key}" must be a primitive value'
                    )
            event_id = str(event.get("eventId"))
            event_key = f"{auth.tenant_id}:{event_id}"
            if event_key not in self.store.activity_event_ids:
                self.store.activity_event_ids.add(event_key)
                accepted += 1
        return {"accepted": accepted, "duplicates": len(events) - accepted}

    async def _upload_document(
        self, auth: AuthContext, call: ServiceCall, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        upload = call.upload
        if upload is None:
            raise BadRequestError("FILE_REQUIRED", "Choose a document file to upload")
        key = self._idempotency_key(payload)
        category = upload.category or str(payload.get("category") or "other")
        requirement_id = upload.requirement_id or _optional_str(payload.get("requirementId"))
        file_name = validate_document_upload(
            upload.file_name, upload.mime_type, category, upload.content
        )
        sha256 = hashlib.sha256(upload.content).hexdigest()
        reserved = self.store.reserve_document_upload(
            auth,
            {
                "fileName": file_name,
                "mimeType": upload.mime_type,
                "sizeBytes": len(upload.content),
                "category": category,
                "sha256": sha256,
            },
            key,
            requirement_id,
        )
        reference = self.store.get_content_reference(auth, reserved["id"])
        try:
            await self.storage.put(
                reference["storageKey"], upload.content, upload.mime_type, sha256
            )
        except OSError as error:
            raise ApiError(
                503,
                "DOCUMENT_STORAGE_UNAVAILABLE",
                "Your document record was saved, but the original could not be stored yet. "
                "Please retry this upload.",
            ) from error
        self.store.claim_document_processing(auth, reserved["id"])
        return self.store.get_document(auth, reserved["id"])

    async def _get_document_content(
        self, auth: AuthContext, document_id: str, *, cache_control: str
    ) -> BinaryPayload:
        reference = self.store.get_content_reference(auth, document_id)
        try:
            content = await self.storage.get(reference["storageKey"])
        except OSError as error:
            raise ApiError(
                503,
                "DOCUMENT_STORAGE_UNAVAILABLE",
                "The document content is temporarily unavailable",
            ) from error
        return BinaryPayload(
            data=content,
            media_type=reference["mimeType"],
            file_name=reference["fileName"],
            cache_control=cache_control,
        )

    async def _get_document_profile_photo(
        self, auth: AuthContext, document_id: str
    ) -> BinaryPayload:
        document = self.store.get_document(auth, document_id)
        regions = document.get("extraction", {}).get("visualRegions", [])
        region = next((item for item in regions if item.get("kind") == "profile_photo"), None)
        if document["category"] != "identity" or not region:
            raise ApiError(
                404,
                "DOCUMENT_PROFILE_PHOTO_NOT_FOUND",
                "No profile photo was identified in this document",
            )
        content = await self._get_document_content(
            auth, document_id, cache_control="private, max-age=300"
        )
        # The production adapter crops the declared region. The memory fake returns an already-JPEG
        # upload verbatim and otherwise uses a deterministic one-pixel JPEG for contract tests.
        jpeg = content.data if content.media_type == "image/jpeg" else _ONE_PIXEL_JPEG
        return BinaryPayload(
            data=jpeg,
            media_type="image/jpeg",
            file_name=f"profile-photo-{document_id}.jpg",
            cache_control="private, max-age=300",
        )

    def _retry_document_extraction(
        self, auth: AuthContext, document_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        body = self._without_idempotency(payload)
        if body:
            raise BadRequestError(
                "RETRY_EXTRACTION_BODY_NOT_ALLOWED",
                "Document extraction retry does not accept a request body",
            )
        key = self._idempotency_key(payload)
        current = self.store.get_document(auth, document_id)
        if not can_retry_extraction(current.get("extraction")):
            return current
        self.store.claim_document_processing(auth, document_id, retry=True, retry_key=key)
        return self.store.get_document(auth, document_id)

    async def _process_document_extraction(
        self, auth: AuthContext, document_id: str, request_id: str
    ) -> dict[str, Any]:
        document = self.store.get_document(auth, document_id)
        if (
            document["status"] != "processing"
            or document.get("extraction", {}).get("status") != "processing"
        ):
            return document
        try:
            binary = await self._get_document_content(
                auth, document_id, cache_control="private, no-store"
            )
            extraction = await self._extract_with_single_retry(
                tenant_id=auth.tenant_id,
                student_id=auth.student_id,
                document_id=document_id,
                request_id=request_id,
                file_name=binary.file_name,
                mime_type=binary.media_type,
                content=binary.data,
                category=document["category"],
            )
            expected_type = document_type_for_category(document["category"])
            if document["category"] == "financial_aid" and extraction.get("status") == "completed":
                extraction = {
                    **extraction,
                    "studentName": None,
                    "institutionName": None,
                    "issueDate": None,
                    "academicTerm": None,
                    "fields": [],
                    "courses": [],
                    "visualRegions": [],
                }
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
        except BaseException as error:  # provider/storage failures become a student-safe record
            extraction = failed_extraction(document["fileName"], document["category"], error)
        return self.store.complete_document_extraction(auth, document_id, extraction)

    async def _extract_with_single_retry(self, **input_data: object) -> dict[str, Any]:
        try:
            return await self.ai.extract_document(**input_data, attempt=1)
        except BaseException as first:
            if not classify_extraction_failure(first).automatic_retryable:
                raise
            return await self.ai.extract_document(**input_data, attempt=2)

    async def _recover_document_extraction_reservation(
        self, auth: AuthContext, document_id: str
    ) -> dict[str, Any]:
        document = self.store.get_document(auth, document_id)
        if document["status"] != "uploaded" or document.get("extraction"):
            return document
        await self._get_document_content(auth, document_id, cache_control="private, no-store")
        self.store.claim_document_processing(auth, document_id)
        return self.store.get_document(auth, document_id)

    async def _ask_edward(
        self, auth: AuthContext, payload: Mapping[str, Any], request_id: str
    ) -> dict[str, Any]:
        self.store.authorize(auth)
        message = str(payload.get("message", ""))
        conversation_id = payload.get("conversationId")
        client_message_id = payload.get("clientMessageId")
        persist = conversation_id is not None or client_message_id is not None
        if isinstance(client_message_id, str) and client_message_id:
            replay = self.store.find_assistant_exchange_by_client_id(auth, client_message_id)
            if replay is not None:
                return replay

        guarded = guarded_response(message)
        if guarded is not None:
            return dict(guarded)

        page_context = payload.get("pageContext")
        page_path = None
        page_label = None
        if isinstance(page_context, Mapping):
            page_path = str(page_context.get("path") or "") or None
            page_label = str(page_context.get("label") or "") or None
        elif isinstance(page_context, str) and page_context.strip():
            page_path = page_context.strip()

        def sync_read(reader: Callable[[AuthContext], dict[str, Any]]) -> Any:
            async def read() -> dict[str, Any]:
                return reader(auth)

            return read

        host = AssistantToolHost(
            {
                "profile": sync_read(self.store.get_profile),
                "requirements": sync_read(self.store.get_requirements),
                "documents": sync_read(self.store.get_documents),
                "payments": sync_read(self.store.get_payments),
                "financials": sync_read(self.store.get_financials),
                "dashboard": sync_read(self.store.get_dashboard),
                "housing_plan": sync_read(self.store.get_housing_plan),
                "appointments": sync_read(self.store.get_appointments),
                "help": sync_read(self.store.get_help),
            }
        )
        history = payload.get("history", [])
        pipeline = AssistantPipeline(host)
        result = await pipeline.execute(
            message=message,
            history=history if isinstance(history, list) else [],
            page_path=page_path,
            page_label=page_label,
        )
        response: dict[str, Any] = normalize_response(
            {
                "message": result.message,
                "blocks": result.blocks,
                "provider": result.provider,
                "model": result.model,
                "usage": result.usage,
                "suggestedActions": result.suggested_actions,
                "contextReceipts": result.context_receipts,
                "widgets": [],
            }
        )
        response["contextReceipts"] = result.context_receipts
        if persist:
            stored = self.store.append_assistant_exchange(
                auth,
                conversation_id=str(conversation_id) if conversation_id else None,
                page_path=page_path,
                page_label=page_label,
                user_message={
                    "content": message,
                    "clientMessageId": client_message_id,
                    "inputMode": payload.get("inputMode") or "text",
                },
                assistant_message=response,
                request_id=request_id,
            )
            response.update(stored)
            response["requestId"] = request_id
        return response

    async def _ensure_signed_documents(
        self, auth: AuthContext, onboarding: Mapping[str, Any]
    ) -> int:
        data = onboarding.get("data", {})
        if (
            onboarding.get("status") != "completed"
            or not onboarding.get("completedAt")
            or not isinstance(data, Mapping)
            or data.get("signatureConsent") is not True
            or not data.get("signatureFullName")
            or not data.get("signatureMethod")
            or not data.get("signedDocumentIds")
        ):
            return 0
        created = 0
        requested = set(data["signedDocumentIds"])
        version = int(onboarding["version"])
        existing = {
            item.get("signature", {}).get("templateCode")
            for item in self.store.documents
            if item.get("signature", {}).get("onboardingVersion") == version
        }
        for template in SIGNED_TEMPLATES:
            if template["code"] not in requested or template["code"] in existing:
                continue
            document_id = deterministic_document_id(
                f"{auth.tenant_id}:{auth.student_id}:{template['code']}:{version}"
            )
            signed_at = str(onboarding["completedAt"])
            pdf = create_signed_pdf(
                template["title"],
                str(data["signatureFullName"]),
                signed_at,
                f"onboarding.{version}.{template['code']}.{auth.student_id}",
            )
            sha256 = hashlib.sha256(pdf).hexdigest()
            storage_key = f"{auth.tenant_id}/{auth.student_id}/signed-onboarding/{document_id}.pdf"
            await self.storage.put(storage_key, pdf, "application/pdf", sha256)
            self.store.save_signed_document(
                auth,
                {
                    "id": document_id,
                    "templateCode": template["code"],
                    "onboardingVersion": version,
                    "title": template["title"],
                    "fileName": template["fileName"],
                    "sizeBytes": len(pdf),
                    "sha256": sha256,
                    "signerName": data["signatureFullName"],
                    "signatureMethod": data["signatureMethod"],
                    "signedAt": signed_at,
                },
                storage_key,
            )
            created += 1
        return created


def _optional_str(value: object) -> str | None:
    return str(value) if value not in (None, "") else None


_ONE_PIXEL_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb00430001010101010101010101010101010101"
    "01010101010101010101010101010101010101010101010101010101010101010101010101010101"
    "0101ffc0000b080001000101011100ffc40014000100000000000000000000000000000000ffc40014"
    "100100000000000000000000000000000000ffda0008010100003f00ffd9"
)
