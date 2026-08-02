"""Compatibility-first FastAPI routes for the Audentra platform API."""

import json
from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request, Response
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from audentra.contracts.requests import (
    ActivityEventBatchRequest,
    AskEdwardRequest,
    CompleteStudentOnboardingRequest,
    ConfirmStudentDocumentExtractionRequest,
    CreateDepositPaymentRequest,
    CreateStaffClubRequest,
    CreateStaffCorePlayRequest,
    CreateStaffKnowledgeCardRequest,
    CreateStudentAppointmentRequest,
    CreateStudentDocumentRequest,
    CreateStudentHelpRequest,
    DecideStudentExperienceUpdateRequest,
    DraftStaffManagedConfigurationRequest,
    PreviewStaffEdwardRequest,
    ReviewStaffDocumentRequest,
    SelectPaymentPlanRequest,
    SimulateStaffOutreachRequest,
    UpdateStaffClubRequest,
    UpdateStaffCorePlayRequest,
    UpdateStaffInquiryRequest,
    UpdateStaffKnowledgeCardRequest,
    UpdateStaffManagedConfigurationRequest,
    UpdateStaffStudentPreferencesRequest,
    UpdateStaffWorkItemRequest,
    UpdateStudentHousingPlanRequest,
    UpdateStudentOnboardingRequest,
    UpdateStudentProfileRequest,
)
from audentra.contracts.responses import ApiErrorEnvelope
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError
from audentra.core.ports import BinaryPayload, FileUpload, PlatformService, ServiceCall

from .dependencies import (
    AuthDependency,
    IdempotencyDependency,
    ServiceDependency,
    WorkerTokenDependency,
)

MAXIMUM_DOCUMENT_BYTES = 10_485_760
ALLOWED_DOCUMENT_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
ALLOWED_DOCUMENT_CATEGORIES = {
    "identity",
    "residency",
    "transcript",
    "financial_aid",
    "health",
    "consent",
    "other",
}
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status_code: {"model": ApiErrorEnvelope}
    for status_code in (400, 401, 403, 404, 409, 413, 415, 500, 503)
}

router = APIRouter(responses=ERROR_RESPONSES)


async def _dispatch(
    *,
    service: PlatformService,
    request: Request,
    operation: str,
    auth: AuthContext | None = None,
    payload: Mapping[str, Any] | None = None,
    path_params: Mapping[str, str] | None = None,
    query_params: Mapping[str, str] | None = None,
    upload: FileUpload | None = None,
    idempotency_key: str | None = None,
) -> object:
    return await service.dispatch(
        ServiceCall(
            operation=operation,
            auth=auth,
            request_id=request.state.request_id,
            payload=payload or {},
            path_params=path_params or {},
            query_params=query_params or {},
            upload=upload,
            idempotency_key=idempotency_key,
        )
    )


def _uuid(value: UUID) -> str:
    return str(value)


def _binary_response(payload: object) -> Response:
    if not isinstance(payload, BinaryPayload):
        raise ApiError(
            500,
            "INVALID_BINARY_RESPONSE",
            "The platform service did not return a binary document",
        )
    safe_file_name = payload.file_name.replace('"', "").replace("\r", "").replace("\n", "")
    return Response(
        content=payload.data,
        media_type=payload.media_type,
        headers={
            "content-disposition": f'inline; filename="{safe_file_name}"',
            "cache-control": payload.cache_control,
        },
    )


def _valid_file_signature(content: bytes, mime_type: str) -> bool:
    if mime_type == "application/pdf":
        return content.startswith(b"%PDF-")
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    return content.startswith(b"\x89PNG\r\n\x1a\n")


def _parse_optional_uuid(value: str | None, *, code: str, message: str) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as error:
        raise BadRequestError(code, message) from error


async def _read_document_upload(request: Request) -> FileUpload:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ApiError(415, "MULTIPART_REQUIRED", "Content-Type must be multipart/form-data")

    try:
        form = await request.form(
            max_files=2,
            max_fields=4,
            max_part_size=MAXIMUM_DOCUMENT_BYTES,
        )
    except (MultiPartException, StarletteHTTPException) as error:
        detail = str(getattr(error, "detail", error)).lower()
        if "file" in detail and "too many" in detail:
            raise BadRequestError(
                "ONE_DOCUMENT_REQUIRED", "Upload exactly one document file"
            ) from error
        if "field" in detail and "too many" in detail:
            raise BadRequestError(
                "UNEXPECTED_UPLOAD_FIELD",
                "Only file, category, requirementId, and uploadBundleId fields are accepted",
            ) from error
        raise ApiError(
            413,
            "DOCUMENT_TOO_LARGE",
            "Upload one document no larger than 10 MB",
        ) from error

    try:
        file_part: UploadFile | None = None
        fields: dict[str, str] = {}
        allowed_fields = {"category", "requirementId", "uploadBundleId"}
        for field_name, part in form.multi_items():
            if isinstance(part, UploadFile):
                if field_name != "file" or file_part is not None:
                    raise BadRequestError(
                        "ONE_DOCUMENT_REQUIRED", "Upload exactly one document file"
                    )
                file_part = part
                continue
            if field_name not in allowed_fields:
                raise BadRequestError(
                    "UNEXPECTED_UPLOAD_FIELD",
                    "Only file, category, requirementId, and uploadBundleId fields are accepted",
                )
            if field_name in fields:
                raise BadRequestError(
                    "DUPLICATE_UPLOAD_FIELD",
                    f"The {field_name} upload field may appear only once",
                )
            fields[field_name] = str(part)

        if file_part is None:
            raise BadRequestError("FILE_REQUIRED", "Choose a document file to upload")

        mime_type = file_part.content_type or "application/octet-stream"
        if mime_type not in ALLOWED_DOCUMENT_MIME_TYPES:
            raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "Use a PDF, JPEG, or PNG document")
        content = await file_part.read(MAXIMUM_DOCUMENT_BYTES + 1)
        if len(content) < 1 or len(content) > MAXIMUM_DOCUMENT_BYTES:
            raise ApiError(
                413,
                "DOCUMENT_TOO_LARGE",
                "Documents must be no larger than 10 MB",
            )
        if not _valid_file_signature(content, mime_type):
            raise ApiError(
                415,
                "FILE_SIGNATURE_MISMATCH",
                "The file contents do not match the selected PDF, JPEG, or PNG type",
            )

        category = fields.get("category") or None
        if category is not None and category not in ALLOWED_DOCUMENT_CATEGORIES:
            raise BadRequestError("INVALID_DOCUMENT_CATEGORY", "Choose a valid document category")
        requirement_id = _parse_optional_uuid(
            fields.get("requirementId") or None,
            code="INVALID_REQUIREMENT_ID",
            message="The document requirement is invalid",
        )
        upload_bundle_id = _parse_optional_uuid(
            fields.get("uploadBundleId") or None,
            code="INVALID_UPLOAD_BUNDLE_ID",
            message="The document upload bundle is invalid",
        )
        return FileUpload(
            file_name=file_part.filename or "document",
            mime_type=mime_type,
            content=content,
            category=category,
            requirement_id=requirement_id,
            upload_bundle_id=upload_bundle_id,
        )
    finally:
        await form.close()


@router.get("/health", status_code=200, response_model=None)
async def liveness(request: Request, service: ServiceDependency) -> object:
    return await _dispatch(service=service, request=request, operation="health.liveness")


@router.get("/health/ready", status_code=200, response_model=None)
async def readiness(request: Request, service: ServiceDependency) -> object:
    return await _dispatch(service=service, request=request, operation="health.readiness")


@router.get("/v1/student/dashboard", status_code=200, response_model=None)
async def get_dashboard(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_dashboard", auth=auth
    )


@router.get("/v1/student/academics", status_code=200, response_model=None)
async def get_academics(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_academics", auth=auth
    )


@router.get("/v1/catalog/courses", status_code=200, response_model=None)
async def search_courses(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    query: Annotated[str, Query()] = "",
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="catalog.search_courses",
        auth=auth,
        query_params={"query": query},
    )


@router.get("/v1/student/financials", status_code=200, response_model=None)
async def get_financials(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_financials", auth=auth
    )


@router.post("/v1/student/financials/payment-plan", status_code=200, response_model=None)
async def select_payment_plan(
    body: SelectPaymentPlanRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.select_payment_plan",
        auth=auth,
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.get("/v1/student/campus-life", status_code=200, response_model=None)
async def get_campus_life(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_campus_life", auth=auth
    )


@router.post("/v1/admission-offers/{offerId}/accept", status_code=200, response_model=None)
async def accept_offer(
    offer_id: Annotated[UUID, Path(alias="offerId")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="admission.accept_offer",
        auth=auth,
        path_params={"offerId": _uuid(offer_id)},
        idempotency_key=idempotency_key,
    )


@router.post("/v1/activity-events/batch", status_code=202, response_model=None)
async def ingest_activity_batch(
    body: ActivityEventBatchRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="activity.ingest_batch",
        auth=auth,
        payload=body.public_payload(),
    )


@router.get("/v1/student/bootstrap", status_code=200, response_model=None)
async def get_bootstrap(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_bootstrap", auth=auth
    )


@router.get("/v1/student/onboarding", status_code=200, response_model=None)
async def get_onboarding(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_onboarding", auth=auth
    )


@router.put("/v1/student/onboarding", status_code=200, response_model=None)
async def update_onboarding(
    body: UpdateStudentOnboardingRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.update_onboarding",
        auth=auth,
        payload=body.public_payload(),
    )


@router.post("/v1/student/onboarding/complete", status_code=200, response_model=None)
async def complete_onboarding(
    body: CompleteStudentOnboardingRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.complete_onboarding",
        auth=auth,
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.get("/v1/student/housing-plan", status_code=200, response_model=None)
async def get_housing_plan(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_housing_plan", auth=auth
    )


@router.patch("/v1/student/housing-plan", status_code=200, response_model=None)
async def update_housing_plan(
    body: UpdateStudentHousingPlanRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.update_housing_plan",
        auth=auth,
        payload=body.public_payload(),
    )


@router.post(
    "/v1/student/experience-updates/{id}/decision",
    status_code=200,
    response_model=None,
)
async def decide_student_experience_update(
    update_id: Annotated[UUID, Path(alias="id")],
    body: DecideStudentExperienceUpdateRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.decide_experience_update",
        auth=auth,
        path_params={"updateId": _uuid(update_id)},
        payload=body.public_payload(),
    )


@router.get("/v1/student/requirements", status_code=200, response_model=None)
async def list_requirements(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.list_requirements", auth=auth
    )


@router.get("/v1/student/requirements/{id}", status_code=200, response_model=None)
async def get_requirement(
    requirement_id: Annotated[str, Path(alias="id", min_length=1, max_length=128)],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.get_requirement",
        auth=auth,
        path_params={"requirementId": requirement_id},
    )


@router.get("/v1/student/messages", status_code=200, response_model=None)
async def list_messages(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.list_messages", auth=auth
    )


@router.post("/v1/student/messages/{id}/read", status_code=200, response_model=None)
async def mark_message_read(
    message_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.mark_message_read",
        auth=auth,
        path_params={"messageId": _uuid(message_id)},
    )


@router.get("/v1/student/documents", status_code=200, response_model=None)
async def list_documents(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.list_documents", auth=auth
    )


@router.post("/v1/student/documents", status_code=201, response_model=None)
async def create_document(
    body: CreateStudentDocumentRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.create_document",
        auth=auth,
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/v1/student/documents/upload",
    status_code=201,
    response_model=None,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "category": {
                                "type": "string",
                                "enum": sorted(ALLOWED_DOCUMENT_CATEGORIES),
                            },
                            "requirementId": {"type": "string", "format": "uuid"},
                            "uploadBundleId": {"type": "string", "format": "uuid"},
                        },
                    }
                }
            },
        }
    },
)
async def upload_document(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    upload = await _read_document_upload(request)
    return await _dispatch(
        service=service,
        request=request,
        operation="student.upload_document",
        auth=auth,
        upload=upload,
        idempotency_key=idempotency_key,
    )


@router.get("/v1/student/documents/{id}/content", status_code=200, response_model=None)
async def get_document_content(
    document_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> Response:
    result = await _dispatch(
        service=service,
        request=request,
        operation="student.get_document_content",
        auth=auth,
        path_params={"documentId": _uuid(document_id)},
    )
    return _binary_response(result)


@router.get("/v1/student/documents/{id}/profile-photo", status_code=200, response_model=None)
async def get_document_profile_photo(
    document_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> Response:
    result = await _dispatch(
        service=service,
        request=request,
        operation="student.get_document_profile_photo",
        auth=auth,
        path_params={"documentId": _uuid(document_id)},
    )
    if not isinstance(result, BinaryPayload):
        raise ApiError(
            500,
            "INVALID_BINARY_RESPONSE",
            "The platform service did not return a binary profile photo",
        )
    return Response(
        content=result.data,
        media_type="image/jpeg",
        headers={
            "content-disposition": (f'inline; filename="profile-photo-{_uuid(document_id)}.jpg"'),
            "cache-control": "private, max-age=300",
        },
    )


@router.post("/v1/student/documents/{id}/confirm-extraction", status_code=200, response_model=None)
async def confirm_document_extraction(
    document_id: Annotated[UUID, Path(alias="id")],
    body: ConfirmStudentDocumentExtractionRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.confirm_document_extraction",
        auth=auth,
        payload=body.public_payload(),
        path_params={"documentId": _uuid(document_id)},
        idempotency_key=idempotency_key,
    )


@router.post(
    "/v1/student/documents/{id}/retry-extraction",
    status_code=200,
    response_model=None,
    openapi_extra={
        "requestBody": {
            "required": False,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "maxProperties": 0,
                    }
                }
            },
        }
    },
)
async def retry_document_extraction(
    document_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    raw_body = await request.body()
    if raw_body.strip():
        try:
            parsed_body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise BadRequestError(
                "RETRY_EXTRACTION_BODY_NOT_ALLOWED",
                "Document extraction retry does not accept a request body",
            ) from error
        if parsed_body != {}:
            raise BadRequestError(
                "RETRY_EXTRACTION_BODY_NOT_ALLOWED",
                "Document extraction retry does not accept a request body",
            )
    return await _dispatch(
        service=service,
        request=request,
        operation="student.retry_document_extraction",
        auth=auth,
        path_params={"documentId": _uuid(document_id)},
        idempotency_key=idempotency_key,
    )


@router.post("/v1/student/internal/document-extractions/{id}", status_code=200, response_model=None)
async def process_document_extraction(
    document_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    _worker_token: WorkerTokenDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="internal.process_document_extraction",
        auth=auth,
        path_params={"documentId": _uuid(document_id)},
    )


@router.post(
    "/v1/student/internal/document-extraction-reservations/{id}",
    status_code=200,
    response_model=None,
)
async def recover_document_extraction_reservation(
    document_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    _worker_token: WorkerTokenDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="internal.recover_document_extraction_reservation",
        auth=auth,
        path_params={"documentId": _uuid(document_id)},
    )


@router.post("/v1/student/assistant/messages", status_code=200, response_model=None)
async def ask_edward(
    body: AskEdwardRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.ask_edward",
        auth=auth,
        payload=body.public_payload(),
    )


@router.get("/v1/student/appointments", status_code=200, response_model=None)
async def list_appointments(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.list_appointments", auth=auth
    )


@router.post("/v1/student/appointments", status_code=201, response_model=None)
async def create_appointment(
    body: CreateStudentAppointmentRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.create_appointment",
        auth=auth,
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.get("/v1/student/payments", status_code=200, response_model=None)
async def list_payments(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.list_payments", auth=auth
    )


@router.post("/v1/student/payments/deposit", status_code=200, response_model=None)
async def create_deposit(
    body: CreateDepositPaymentRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.create_deposit",
        auth=auth,
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.get("/v1/student/profile", status_code=200, response_model=None)
async def get_profile(request: Request, service: ServiceDependency, auth: AuthDependency) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_profile", auth=auth
    )


@router.patch("/v1/student/profile", status_code=200, response_model=None)
async def update_profile(
    body: UpdateStudentProfileRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.update_profile",
        auth=auth,
        payload=body.public_payload(),
    )


@router.get("/v1/student/help", status_code=200, response_model=None)
async def get_help(request: Request, service: ServiceDependency, auth: AuthDependency) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_help", auth=auth
    )


@router.post("/v1/demo/help-requests", status_code=200, response_model=None)
async def create_help_request(
    body: CreateStudentHelpRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.create_help_request",
        auth=auth,
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.get("/v1/staff/workspace", status_code=200, response_model=None)
async def get_staff_workspace(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="staff.get_workspace", auth=auth
    )


@router.get("/v1/staff/configurations/{kind}", status_code=200, response_model=None)
async def get_staff_managed_configuration(
    kind: Annotated[str, Path(pattern=r"^(journeys|campus_life|academics)$")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_managed_configuration",
        auth=auth,
        path_params={"kind": kind},
    )


@router.put("/v1/staff/configurations/{kind}", status_code=200, response_model=None)
async def update_staff_managed_configuration(
    kind: Annotated[str, Path(pattern=r"^(journeys|campus_life|academics)$")],
    body: UpdateStaffManagedConfigurationRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_managed_configuration",
        auth=auth,
        path_params={"kind": kind},
        payload=body.public_payload(),
    )


@router.post("/v1/staff/edward/configuration-draft", status_code=200, response_model=None)
async def draft_staff_managed_configuration(
    body: DraftStaffManagedConfigurationRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.draft_managed_configuration",
        auth=auth,
        payload=body.public_payload(),
    )


@router.post("/v1/staff/knowledge-base", status_code=201, response_model=None)
async def create_staff_knowledge_card(
    body: CreateStaffKnowledgeCardRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.create_knowledge_card",
        auth=auth,
        payload=body.public_payload(),
    )


@router.patch("/v1/staff/knowledge-base/{id}", status_code=200, response_model=None)
async def update_staff_knowledge_card(
    card_id: Annotated[UUID, Path(alias="id")],
    body: UpdateStaffKnowledgeCardRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_knowledge_card",
        auth=auth,
        path_params={"cardId": _uuid(card_id)},
        payload=body.public_payload(),
    )


@router.post("/v1/staff/core-plays", status_code=201, response_model=None)
async def create_staff_core_play(
    body: CreateStaffCorePlayRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.create_core_play",
        auth=auth,
        payload=body.public_payload(),
    )


@router.patch("/v1/staff/core-plays/{id}", status_code=200, response_model=None)
async def update_staff_core_play(
    play_id: Annotated[UUID, Path(alias="id")],
    body: UpdateStaffCorePlayRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_core_play",
        auth=auth,
        path_params={"playId": _uuid(play_id)},
        payload=body.public_payload(),
    )


@router.patch("/v1/staff/inquiries/{id}", status_code=200, response_model=None)
async def update_staff_inquiry(
    inquiry_id: Annotated[UUID, Path(alias="id")],
    body: UpdateStaffInquiryRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_inquiry",
        auth=auth,
        path_params={"inquiryId": _uuid(inquiry_id)},
        payload=body.public_payload(),
    )


@router.post("/v1/staff/campus-life/clubs", status_code=201, response_model=None)
async def create_staff_club(
    body: CreateStaffClubRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.create_club",
        auth=auth,
        payload=body.public_payload(),
    )


@router.patch("/v1/staff/campus-life/clubs/{id}", status_code=200, response_model=None)
async def update_staff_club(
    club_id: Annotated[UUID, Path(alias="id")],
    body: UpdateStaffClubRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_club",
        auth=auth,
        path_params={"clubId": _uuid(club_id)},
        payload=body.public_payload(),
    )


@router.post("/v1/staff/outreach/simulate", status_code=201, response_model=None)
async def simulate_staff_outreach(
    body: SimulateStaffOutreachRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.simulate_outreach",
        auth=auth,
        payload=body.public_payload(),
    )


@router.post("/v1/staff/edward/preview", status_code=200, response_model=None)
async def preview_staff_edward(
    body: PreviewStaffEdwardRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.preview_edward",
        auth=auth,
        payload=body.public_payload(),
    )


@router.get("/v1/staff/action-center", status_code=200, response_model=None)
async def get_action_center(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="staff.get_action_center", auth=auth
    )


@router.patch("/v1/staff/work-items/{id}", status_code=200, response_model=None)
async def update_work_item(
    work_item_id: Annotated[UUID, Path(alias="id")],
    body: UpdateStaffWorkItemRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_work_item",
        auth=auth,
        payload=body.public_payload(),
        path_params={"workItemId": _uuid(work_item_id)},
    )


@router.get("/v1/staff/students/{id}", status_code=200, response_model=None)
async def get_staff_student(
    student_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_student",
        auth=auth,
        path_params={"studentId": _uuid(student_id)},
    )


@router.patch("/v1/staff/students/{id}/preferences", status_code=200, response_model=None)
async def update_student_preferences(
    student_id: Annotated[UUID, Path(alias="id")],
    body: UpdateStaffStudentPreferencesRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_student_preferences",
        auth=auth,
        payload=body.public_payload(),
        path_params={"studentId": _uuid(student_id)},
    )


@router.post("/v1/staff/documents/{id}/decision", status_code=201, response_model=None)
async def review_document(
    document_id: Annotated[UUID, Path(alias="id")],
    body: ReviewStaffDocumentRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.review_document",
        auth=auth,
        payload=body.public_payload(),
        path_params={"documentId": _uuid(document_id)},
    )
