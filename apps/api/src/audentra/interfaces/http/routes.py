"""Compatibility-first FastAPI routes for the Audentra platform API."""

import asyncio
import json
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from audentra.contracts.requests import (
    ActivityEventBatchRequest,
    AskEdwardRequest,
    AskStaffEdwardRequest,
    CancelEdwardActionRequest,
    CancelStudentAppointmentRequest,
    CompleteFerpaAuthorizationRequest,
    CompleteStaffInteractionRequest,
    CompleteStudentOnboardingRequest,
    ConfirmEdwardActionRequest,
    ConfirmStudentDocumentExtractionRequest,
    CreateAssistantConversationRequest,
    CreateAssistantVoiceSessionRequest,
    CreateDepositPaymentRequest,
    CreateStaffActionRuleRequest,
    CreateStaffClubRequest,
    CreateStaffCorePlayRequest,
    CreateStaffKnowledgeCardRequest,
    CreateStaffWorkCommentRequest,
    CreateStaffWorkItemRequest,
    CreateStudentAppointmentRequest,
    CreateStudentDocumentRequest,
    CreateStudentHelpRequest,
    CreateStudentInquiryMessageRequest,
    DecideStudentExperienceUpdateRequest,
    DeferStudentExperienceUpdatesRequest,
    DraftStaffManagedConfigurationRequest,
    EdwardFeedbackRequest,
    FerpaLinkCommandRequest,
    PreviewStaffEdwardRequest,
    RecordStaffCommunicationRequest,
    RegisterCampusEventRequest,
    RequestStaffAiRefreshRequest,
    RescheduleStudentAppointmentRequest,
    RetryStaffCallTranscriptionRequest,
    ReviewStaffDocumentRequest,
    SaveStaffOutreachDraftRequest,
    SelectPaymentPlanRequest,
    SimulateStaffOutreachRequest,
    StartStaffInteractionRequest,
    SubmitAssistantVoiceTurnRequest,
    SubmitStudentRequirementResponseRequest,
    UpdateFerpaAccessRequest,
    UpdateStaffActionRuleRequest,
    UpdateStaffAppointmentRequest,
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
    UpdateTenantPortalConfigurationRequest,
)
from audentra.contracts.responses import ApiErrorEnvelope
from audentra.core.assistant_execution import (
    ASSISTANT_EXECUTION_MODE_HEADER,
    DEFAULT_ASSISTANT_EXECUTION,
    READ_PLANNER_HEADER,
    ResolvedAssistantExecutionMode,
    lab_execution_controls_enabled,
    resolve_assistant_execution_mode,
)
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError
from audentra.core.ports import BinaryPayload, FileUpload, PlatformService, ServiceCall
from audentra.integrations.assistant.trace import get_assistant_trace_recorder

from .dependencies import (
    AuthDependency,
    IdempotencyDependency,
    ServiceDependency,
    VoiceAgentTokenDependency,
    VoiceSessionServiceDependency,
    WorkerTokenDependency,
    get_settings,
)

MAXIMUM_DOCUMENT_BYTES = 10_485_760
MAXIMUM_PORTAL_MEDIA_BYTES = 5_242_880
MAXIMUM_CALL_RECORDING_BYTES = 10_485_760
ALLOWED_DOCUMENT_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
ALLOWED_PORTAL_MEDIA_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_CALL_RECORDING_MIME_TYPES = {
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "video/mp4",
    "video/webm",
}
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
    assistant_execution: ResolvedAssistantExecutionMode = DEFAULT_ASSISTANT_EXECUTION,
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
            assistant_execution=assistant_execution,
        )
    )


def _assistant_execution_mode(request: Request) -> ResolvedAssistantExecutionMode:
    """Resolve the Lab's execution-mode header for one assistant turn.

    Honoured only where assistant trace debugging is enabled and the
    deployment is not production; elsewhere the header is ignored and the turn
    takes the ordinary path.
    """

    settings = request.app.state.http_settings
    return resolve_assistant_execution_mode(
        request.headers.get(ASSISTANT_EXECUTION_MODE_HEADER),
        lab_controls_enabled=lab_execution_controls_enabled(
            environment=settings.environment,
            assistant_trace_debug_enabled=settings.assistant_trace_debug_enabled,
        ),
        read_planner_raw=request.headers.get(READ_PLANNER_HEADER),
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
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"


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


async def _read_portal_media_upload(request: Request) -> FileUpload:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ApiError(415, "MULTIPART_REQUIRED", "Content-Type must be multipart/form-data")
    try:
        form = await request.form(
            max_files=1,
            max_fields=0,
            max_part_size=MAXIMUM_PORTAL_MEDIA_BYTES,
        )
    except (MultiPartException, StarletteHTTPException) as error:
        raise ApiError(
            413,
            "MEDIA_TOO_LARGE",
            "Upload one event image no larger than 5 MB",
        ) from error
    try:
        items = form.multi_items()
        if len(items) != 1 or items[0][0] != "file" or not isinstance(items[0][1], UploadFile):
            raise BadRequestError("ONE_MEDIA_FILE_REQUIRED", "Upload exactly one event image")
        file_part = items[0][1]
        mime_type = file_part.content_type or "application/octet-stream"
        if mime_type not in ALLOWED_PORTAL_MEDIA_MIME_TYPES:
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Use a JPEG, PNG, or WebP image")
        content = await file_part.read(MAXIMUM_PORTAL_MEDIA_BYTES + 1)
        if len(content) < 1 or len(content) > MAXIMUM_PORTAL_MEDIA_BYTES:
            raise ApiError(413, "MEDIA_TOO_LARGE", "Event images must be no larger than 5 MB")
        if not _valid_file_signature(content, mime_type):
            raise ApiError(
                415,
                "FILE_SIGNATURE_MISMATCH",
                "The file contents do not match the selected image type",
            )
        return FileUpload(
            file_name=file_part.filename or "event-image",
            mime_type=mime_type,
            content=content,
        )
    finally:
        await form.close()


def _valid_audio_signature(content: bytes, mime_type: str) -> bool:
    if mime_type in {"audio/webm", "video/webm"}:
        return content.startswith(b"\x1a\x45\xdf\xa3")
    if mime_type == "audio/ogg":
        return content.startswith(b"OggS")
    if mime_type == "audio/flac":
        return content.startswith(b"fLaC")
    if mime_type == "audio/wav":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WAVE"
    if mime_type == "audio/mpeg":
        return content.startswith(b"ID3") or (
            len(content) >= 2 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0
        )
    return len(content) >= 12 and content[4:8] == b"ftyp"


async def _read_call_recording_upload(request: Request) -> FileUpload:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ApiError(415, "MULTIPART_REQUIRED", "Content-Type must be multipart/form-data")
    try:
        form = await request.form(
            max_files=1,
            max_fields=1,
            max_part_size=MAXIMUM_CALL_RECORDING_BYTES,
        )
    except (MultiPartException, StarletteHTTPException) as error:
        raise ApiError(
            413,
            "CALL_RECORDING_TOO_LARGE",
            "Upload one call recording no larger than 10 MB",
        ) from error
    try:
        file_part: UploadFile | None = None
        consent_confirmed = False
        for field_name, part in form.multi_items():
            if isinstance(part, UploadFile):
                if field_name != "file" or file_part is not None:
                    raise BadRequestError(
                        "ONE_CALL_RECORDING_REQUIRED",
                        "Upload exactly one call recording",
                    )
                file_part = part
            elif field_name == "consentConfirmed":
                consent_confirmed = str(part).strip().lower() == "true"
            else:
                raise BadRequestError(
                    "UNEXPECTED_UPLOAD_FIELD",
                    "Only file and consentConfirmed fields are accepted",
                )
        if file_part is None:
            raise BadRequestError("FILE_REQUIRED", "Choose a call recording to upload")
        if not consent_confirmed:
            raise BadRequestError(
                "RECORDING_CONSENT_REQUIRED",
                "Confirm that recording and transcription consent was obtained",
            )
        mime_type = (file_part.content_type or "application/octet-stream").split(";", 1)[0]
        if mime_type not in ALLOWED_CALL_RECORDING_MIME_TYPES:
            raise ApiError(
                415,
                "UNSUPPORTED_CALL_RECORDING_TYPE",
                "Use a FLAC, MP3, MP4, M4A, OGG, WAV, or WebM recording",
            )
        content = await file_part.read(MAXIMUM_CALL_RECORDING_BYTES + 1)
        if len(content) < 1 or len(content) > MAXIMUM_CALL_RECORDING_BYTES:
            raise ApiError(
                413,
                "CALL_RECORDING_TOO_LARGE",
                "Call recordings must be no larger than 10 MB",
            )
        if not _valid_audio_signature(content, mime_type):
            raise ApiError(
                415,
                "CALL_RECORDING_SIGNATURE_MISMATCH",
                "The recording contents do not match the selected audio type",
            )
        return FileUpload(
            file_name=file_part.filename or "call-recording",
            mime_type=mime_type,
            content=content,
        )
    finally:
        await form.close()


@router.get("/health", status_code=200, response_model=None)
async def liveness(request: Request, service: ServiceDependency) -> object:
    return await _dispatch(service=service, request=request, operation="health.liveness")


@router.get("/health/ready", status_code=200, response_model=None)
async def readiness(request: Request, service: ServiceDependency) -> object:
    return await _dispatch(service=service, request=request, operation="health.readiness")


@router.get("/v1/media/{file}", status_code=200, response_model=None)
async def get_portal_media(
    file_name: Annotated[str, Path(alias="file", max_length=64)],
    request: Request,
    service: ServiceDependency,
) -> Response:
    result = await _dispatch(
        service=service,
        request=request,
        operation="public.get_portal_media",
        path_params={"mediaFile": file_name},
    )
    return _binary_response(result)


@router.get("/v1/tenant/bootstrap", status_code=200, response_model=None)
async def get_tenant_bootstrap(request: Request, service: ServiceDependency) -> object:
    settings = get_settings(request)
    tenant_id = settings.oidc_tenant_id if settings.auth_mode == "oidc" else settings.demo_tenant_id
    if tenant_id is None:
        raise ApiError(
            503,
            "OIDC_TENANT_NOT_CONFIGURED",
            "The institutional sign-in tenant is not configured",
        )
    return await _dispatch(
        service=service,
        request=request,
        operation="public.get_tenant_bootstrap",
        path_params={"tenantId": tenant_id},
    )


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


@router.post(
    "/v1/student/campus-life/events/{eventId}/register",
    status_code=200,
    response_model=None,
)
async def register_campus_event(
    event_id: Annotated[UUID, Path(alias="eventId")],
    body: RegisterCampusEventRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.register_campus_event",
        auth=auth,
        path_params={"eventId": _uuid(event_id)},
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
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
    "/v1/student/experience-updates/defer",
    status_code=200,
    response_model=None,
)
async def defer_student_experience_updates(
    body: DeferStudentExperienceUpdatesRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    """Save a grouped reminder without exposing partial client-side writes."""

    return await _dispatch(
        service=service,
        request=request,
        operation="student.defer_experience_updates",
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


@router.get(
    "/v1/student/requirements/{id}/appointments",
    status_code=200,
    response_model=None,
)
async def list_requirement_appointments(
    requirement_id: Annotated[str, Path(alias="id", min_length=1, max_length=128)],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.list_requirement_appointments",
        auth=auth,
        path_params={"requirementId": requirement_id},
    )


@router.post(
    "/v1/student/requirements/{id}/appointments",
    status_code=201,
    response_model=None,
)
async def create_requirement_appointment(
    requirement_id: Annotated[str, Path(alias="id", min_length=1, max_length=128)],
    body: CreateStudentAppointmentRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.create_requirement_appointment",
        auth=auth,
        path_params={"requirementId": requirement_id},
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.patch(
    "/v1/student/requirements/{id}/profile",
    status_code=200,
    response_model=None,
)
async def update_requirement_profile(
    requirement_id: Annotated[str, Path(alias="id", min_length=1, max_length=128)],
    body: UpdateStudentProfileRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.update_requirement_profile",
        auth=auth,
        path_params={"requirementId": requirement_id},
        payload=body.public_payload(),
    )


@router.post(
    "/v1/student/requirements/{id}/responses",
    status_code=200,
    response_model=None,
)
async def submit_requirement_response(
    requirement_id: Annotated[str, Path(alias="id", min_length=1, max_length=128)],
    body: SubmitStudentRequirementResponseRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.submit_requirement_response",
        auth=auth,
        path_params={"requirementId": requirement_id},
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.get(
    "/v1/student/ferpa-authorizations/current",
    status_code=200,
    response_model=None,
)
async def get_current_ferpa_authorization(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.get_ferpa_authorization",
        auth=auth,
    )


@router.post(
    "/v1/student/requirements/{id}/ferpa/complete",
    status_code=200,
    response_model=None,
)
async def complete_ferpa_authorization(
    requirement_id: Annotated[str, Path(alias="id", min_length=1, max_length=128)],
    body: CompleteFerpaAuthorizationRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.complete_ferpa_authorization",
        auth=auth,
        path_params={"requirementId": requirement_id},
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.patch(
    "/v1/student/ferpa-authorizations/{id}/access",
    status_code=200,
    response_model=None,
)
async def update_ferpa_access(
    authorization_id: Annotated[UUID, Path(alias="id")],
    body: UpdateFerpaAccessRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.update_ferpa_access",
        auth=auth,
        path_params={"authorizationId": _uuid(authorization_id)},
        payload=body.public_payload(),
    )


@router.post(
    "/v1/student/ferpa-authorizations/{id}/delegates/{delegateId}/link",
    status_code=200,
    response_model=None,
)
async def issue_ferpa_delegate_link(
    authorization_id: Annotated[UUID, Path(alias="id")],
    delegate_id: Annotated[UUID, Path(alias="delegateId")],
    body: FerpaLinkCommandRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.issue_ferpa_delegate_link",
        auth=auth,
        path_params={
            "authorizationId": _uuid(authorization_id),
            "delegateId": _uuid(delegate_id),
        },
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/v1/student/ferpa-authorizations/{id}/delegates/{delegateId}/link/revoke",
    status_code=200,
    response_model=None,
)
async def revoke_ferpa_delegate_link(
    authorization_id: Annotated[UUID, Path(alias="id")],
    delegate_id: Annotated[UUID, Path(alias="delegateId")],
    body: FerpaLinkCommandRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.revoke_ferpa_delegate_link",
        auth=auth,
        path_params={
            "authorizationId": _uuid(authorization_id),
            "delegateId": _uuid(delegate_id),
        },
        payload=body.public_payload(),
    )


@router.get("/v1/student/messages", status_code=200, response_model=None)
async def list_messages(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.list_messages", auth=auth
    )


def _student_sse_message(event: Mapping[str, object]) -> str:
    cursor_value = event["cursor"]
    if not isinstance(cursor_value, int):
        raise TypeError("Student realtime event cursor must be an integer")
    event_type = str(event["type"]).replace("\r", "").replace("\n", "")
    return (
        f"id: {cursor_value}\n"
        f"event: {event_type}\n"
        f"data: {json.dumps(dict(event), separators=(',', ':'), default=str)}\n\n"
    )


@router.get("/v1/student/events", status_code=200, response_model=None)
async def stream_student_events(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    cursor = after
    header_cursor = request.headers.get("last-event-id")
    if cursor is None and header_cursor:
        if not header_cursor.isascii() or not header_cursor.isdecimal():
            raise BadRequestError(
                "INVALID_EVENT_CURSOR",
                "Last-Event-ID must be a non-negative numeric cursor",
            )
        cursor = int(header_cursor)

    async def events() -> Any:
        bootstrap = cursor is None or cursor == 0
        current = None if bootstrap else cursor
        heartbeat_at = time.monotonic()
        yield "retry: 2000\n\n"
        ready_sent = False
        while not await request.is_disconnected():
            batch = await service.dispatch(
                ServiceCall(
                    operation="student.get_realtime_events",
                    auth=auth,
                    request_id=request.state.request_id,
                    payload={"afterCursor": current, "limit": 100},
                )
            )
            if not isinstance(batch, Mapping):
                raise ApiError(
                    500,
                    "INVALID_EVENT_STREAM_RESPONSE",
                    "The platform service returned an invalid event stream response",
                )
            raw_events = batch.get("events")
            if isinstance(raw_events, list):
                for raw_event in raw_events:
                    if not isinstance(raw_event, Mapping):
                        continue
                    current = int(raw_event["cursor"])
                    yield _student_sse_message(raw_event)
                    heartbeat_at = time.monotonic()
            if current is None and batch.get("cursor") is not None:
                current = int(batch["cursor"])
            if bootstrap and not ready_sent and current is not None:
                yield _student_sse_message(
                    {
                        "cursor": current,
                        "type": "student.stream.ready",
                        "resourceType": "student",
                        "resourceId": auth.student_id,
                        "data": {"invalidate": ["messages", "bootstrap"]},
                    }
                )
                ready_sent = True
                heartbeat_at = time.monotonic()
            now = time.monotonic()
            if now - heartbeat_at >= 15:
                yield f": heartbeat {current or 0}\n\n"
                heartbeat_at = now
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
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
        assistant_execution=_assistant_execution_mode(request),
    )


@router.get("/v1/student/assistant/action-intents/{id}", status_code=200, response_model=None)
async def get_student_edward_action(
    intent_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.get_edward_action",
        auth=auth,
        path_params={"intentId": _uuid(intent_id)},
    )


@router.post(
    "/v1/student/assistant/action-intents/{id}/confirm",
    status_code=200,
    response_model=None,
)
async def confirm_student_edward_action(
    intent_id: Annotated[UUID, Path(alias="id")],
    body: ConfirmEdwardActionRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.confirm_edward_action",
        auth=auth,
        payload=body.public_payload(),
        path_params={"intentId": _uuid(intent_id)},
    )


@router.post(
    "/v1/student/assistant/action-intents/{id}/cancel",
    status_code=200,
    response_model=None,
)
async def cancel_student_edward_action(
    intent_id: Annotated[UUID, Path(alias="id")],
    body: CancelEdwardActionRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.cancel_edward_action",
        auth=auth,
        payload=body.public_payload(),
        path_params={"intentId": _uuid(intent_id)},
    )


@router.patch(
    "/v1/student/assistant/messages/{id}/feedback",
    status_code=200,
    response_model=None,
)
async def submit_student_edward_feedback(
    assistant_message_id: Annotated[UUID, Path(alias="id")],
    body: EdwardFeedbackRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.submit_edward_feedback",
        auth=auth,
        path_params={"assistantMessageId": _uuid(assistant_message_id)},
        payload=body.public_payload(),
    )


def _require_assistant_trace_debug(request: Request) -> None:
    if not request.app.state.http_settings.assistant_trace_debug_enabled:
        raise ApiError(404, "NOT_FOUND", "Not found")


@router.get(
    "/internal/assistant/traces",
    status_code=200,
    response_model=None,
    include_in_schema=False,
)
async def list_assistant_traces(
    request: Request,
    _worker_token: WorkerTokenDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> object:
    _require_assistant_trace_debug(request)
    return {"traces": get_assistant_trace_recorder().list(limit)}


@router.get(
    "/internal/assistant/traces/{traceId}",
    status_code=200,
    response_model=None,
    include_in_schema=False,
)
async def get_assistant_trace(
    trace_id: Annotated[str, Path(alias="traceId", max_length=128)],
    request: Request,
    service: ServiceDependency,
    _worker_token: WorkerTokenDependency,
) -> object:
    _require_assistant_trace_debug(request)
    trace = get_assistant_trace_recorder().get(trace_id)
    if trace is None:
        try:
            durable = await _dispatch(
                service=service,
                request=request,
                operation="internal.get_assistant_trace",
                path_params={"traceId": trace_id},
            )
        except ApiError as error:
            if error.code not in {
                "PLATFORM_OPERATION_NOT_IMPLEMENTED",
                "PLATFORM_SERVICE_UNAVAILABLE",
            }:
                raise
            durable = None
        trace = durable if isinstance(durable, dict) else None
    if trace is None:
        raise ApiError(
            404,
            "ASSISTANT_TRACE_NOT_FOUND",
            "No assistant trace with that ID is available",
        )
    return trace


@router.get(
    "/internal/assistant/feedback",
    status_code=200,
    response_model=None,
    include_in_schema=False,
)
async def list_assistant_feedback(
    request: Request,
    service: ServiceDependency,
    _worker_token: WorkerTokenDependency,
    assistant_kind: Annotated[
        Literal["student", "staff"] | None,
        Query(alias="assistantKind"),
    ] = None,
    rating: Annotated[
        Literal["positive", "negative", "unrated"] | None,
        Query(),
    ] = None,
    has_written: Annotated[bool | None, Query(alias="hasWritten")] = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> object:
    _require_assistant_trace_debug(request)
    return await _dispatch(
        service=service,
        request=request,
        operation="internal.list_assistant_feedback",
        payload={
            "assistantKind": assistant_kind,
            "rating": rating,
            "hasWritten": has_written,
            "from": date_from.isoformat() if date_from is not None else None,
            "to": date_to.isoformat() if date_to is not None else None,
            "search": search,
            "limit": limit,
            "offset": offset,
        },
    )


@router.get(
    "/internal/assistant/dev/personas",
    status_code=200,
    response_model=None,
    include_in_schema=False,
)
async def list_assistant_dev_personas(
    request: Request,
    service: ServiceDependency,
    _worker_token: WorkerTokenDependency,
) -> object:
    _require_assistant_trace_debug(request)
    from audentra.infrastructure.memory.eval_personas import PERSONA_LABELS, PERSONAS

    store = getattr(service, "store", None)
    supported = store is not None and hasattr(store, "assistant_messages")
    profile = getattr(store, "profile", {}) if supported else {}
    return {
        "supported": bool(supported),
        "active": getattr(service, "active_eval_persona", None),
        "student": {
            "preferredName": profile.get("preferredName"),
            "studentId": profile.get("studentId"),
        }
        if supported
        else None,
        "personas": [{"name": name, "label": PERSONA_LABELS.get(name, name)} for name in PERSONAS],
    }


@router.get(
    "/internal/assistant/dev/tools",
    status_code=200,
    response_model=None,
    include_in_schema=False,
)
async def list_assistant_dev_tools(
    request: Request,
    _worker_token: WorkerTokenDependency,
) -> object:
    """The tool catalogues exactly as the planners see them, for the Lab.

    Descriptions come from the same tables the model planner and read loop
    are prompted with, so what the Lab explains about a tool is what Edward
    was told about it. Staff tools add their information class and validated
    argument schema; student tools take no arguments (identity is bound
    server-side).
    """

    _require_assistant_trace_debug(request)
    from audentra.integrations.assistant.planner import TOOL_DESCRIPTIONS
    from audentra.integrations.staff_assistant.catalog import (
        STAFF_TOOL_ARGUMENTS,
        STAFF_TOOL_DESCRIPTIONS,
        STAFF_TOOL_INFORMATION_CLASS,
        STAFF_TOOL_NAMES,
    )

    return {
        "student": [
            {"name": name, "description": description, "arguments": None}
            for name, description in TOOL_DESCRIPTIONS.items()
        ],
        "staff": [
            {
                "name": name,
                "description": STAFF_TOOL_DESCRIPTIONS.get(name, ""),
                "informationClass": STAFF_TOOL_INFORMATION_CLASS.get(name),
                "arguments": {
                    argument: dict(spec)
                    for argument, spec in STAFF_TOOL_ARGUMENTS.get(name, {}).items()
                },
            }
            for name in STAFF_TOOL_NAMES
        ],
    }


@router.post(
    "/internal/assistant/dev/personas/{name}",
    status_code=200,
    response_model=None,
    include_in_schema=False,
)
async def activate_assistant_dev_persona(
    name: Annotated[str, Path(max_length=64)],
    request: Request,
    service: ServiceDependency,
    _worker_token: WorkerTokenDependency,
) -> object:
    _require_assistant_trace_debug(request)
    from audentra.infrastructure.memory.eval_personas import (
        PERSONA_LABELS,
        PERSONAS,
        apply_persona,
    )
    from audentra.infrastructure.memory.store import InMemoryPlatformStore

    if not hasattr(service, "store"):
        raise ApiError(
            409,
            "PERSONA_SWITCH_UNSUPPORTED",
            "Persona switching is only available on the in-memory development host",
        )
    if name not in PERSONAS:
        raise ApiError(404, "PERSONA_NOT_FOUND", "Unknown evaluation persona")
    store = InMemoryPlatformStore()
    apply_persona(store, name)
    service.store = store
    service.active_eval_persona = name  # type: ignore[attr-defined]
    return {
        "active": name,
        "label": PERSONA_LABELS.get(name, name),
        "student": {
            "preferredName": store.profile.get("preferredName"),
            "studentId": store.profile.get("studentId"),
        },
    }


@router.post("/v1/student/assistant/conversations", status_code=201, response_model=None)
async def create_assistant_conversation(
    body: CreateAssistantConversationRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.create_assistant_conversation",
        auth=auth,
        payload=body.public_payload(),
    )


@router.get(
    "/v1/student/assistant/conversations/{id}/messages",
    status_code=200,
    response_model=None,
)
async def get_assistant_conversation_messages(
    conversation_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.get_assistant_conversation_messages",
        auth=auth,
        path_params={"conversationId": _uuid(conversation_id)},
    )


@router.post("/v1/student/assistant/voice-sessions", status_code=201, response_model=None)
async def create_assistant_voice_session(
    body: CreateAssistantVoiceSessionRequest,
    request: Request,
    auth: AuthDependency,
    voice_sessions: VoiceSessionServiceDependency,
) -> object:
    return await voice_sessions.create(auth, body.public_payload(), request.state.request_id)


@router.post(
    "/v1/student/assistant/voice-sessions/{id}/token",
    status_code=200,
    response_model=None,
)
async def reconnect_assistant_voice_session(
    voice_session_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    auth: AuthDependency,
    voice_sessions: VoiceSessionServiceDependency,
) -> object:
    return await voice_sessions.reconnect(auth, _uuid(voice_session_id), request.state.request_id)


# The three internal routes below are the voice agent's whole surface. They
# authenticate with the shared voice-agent bearer credential, never a student
# session: the student identity is bound server-side by the session record.


@router.get(
    "/internal/assistant/voice-sessions/{id}",
    status_code=200,
    response_model=None,
)
async def get_internal_voice_session(
    voice_session_id: Annotated[UUID, Path(alias="id")],
    _agent_token: VoiceAgentTokenDependency,
    voice_sessions: VoiceSessionServiceDependency,
) -> object:
    return await voice_sessions.get_internal(_uuid(voice_session_id))


@router.post(
    "/internal/assistant/voice-sessions/{id}/turns",
    status_code=200,
    response_model=None,
)
async def submit_internal_voice_turn(
    voice_session_id: Annotated[UUID, Path(alias="id")],
    body: SubmitAssistantVoiceTurnRequest,
    request: Request,
    _agent_token: VoiceAgentTokenDependency,
    voice_sessions: VoiceSessionServiceDependency,
) -> object:
    return await voice_sessions.submit_turn(
        _uuid(voice_session_id), body.public_payload(), request.state.request_id
    )


@router.post(
    "/internal/assistant/voice-sessions/{id}/end",
    status_code=200,
    response_model=None,
)
async def end_internal_voice_session(
    voice_session_id: Annotated[UUID, Path(alias="id")],
    _agent_token: VoiceAgentTokenDependency,
    voice_sessions: VoiceSessionServiceDependency,
) -> object:
    return await voice_sessions.end_internal(_uuid(voice_session_id))


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


@router.get("/v1/student/appointments/availability", status_code=200, response_model=None)
async def get_appointment_availability(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    type: Annotated[str, Query(max_length=40)],
    staff_member_id: Annotated[str | None, Query(alias="staffMemberId", max_length=64)] = None,
    window_from: Annotated[str | None, Query(alias="from", max_length=40)] = None,
    window_to: Annotated[str | None, Query(alias="to", max_length=40)] = None,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.get_appointment_availability",
        auth=auth,
        query_params={
            "type": type,
            "staffMemberId": staff_member_id or "",
            "from": window_from or "",
            "to": window_to or "",
        },
    )


@router.post(
    "/v1/student/appointments/{appointmentId}/cancel", status_code=200, response_model=None
)
async def cancel_appointment(
    appointment_id: Annotated[UUID, Path(alias="appointmentId")],
    body: CancelStudentAppointmentRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.cancel_appointment",
        auth=auth,
        payload=body.public_payload(),
        path_params={"appointmentId": _uuid(appointment_id)},
    )


@router.post(
    "/v1/student/appointments/{appointmentId}/reschedule", status_code=200, response_model=None
)
async def reschedule_appointment(
    appointment_id: Annotated[UUID, Path(alias="appointmentId")],
    body: RescheduleStudentAppointmentRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.reschedule_appointment",
        auth=auth,
        payload=body.public_payload(),
        path_params={"appointmentId": _uuid(appointment_id)},
        idempotency_key=idempotency_key,
    )


@router.get("/v1/student/advising", status_code=200, response_model=None)
async def get_student_advising(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="student.get_advising", auth=auth
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


@router.post(
    "/v1/student/help/requests/{id}/messages",
    status_code=200,
    response_model=None,
)
async def create_student_inquiry_message(
    inquiry_id: Annotated[UUID, Path(alias="id")],
    body: CreateStudentInquiryMessageRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="student.create_inquiry_message",
        auth=auth,
        payload=body.public_payload(),
        path_params={"inquiryId": _uuid(inquiry_id)},
        idempotency_key=idempotency_key,
    )


@router.get("/v1/staff/workspace", status_code=200, response_model=None)
async def get_staff_workspace(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="staff.get_workspace", auth=auth
    )


@router.get("/v1/staff/me", status_code=200, response_model=None)
async def get_staff_me(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(service=service, request=request, operation="staff.get_me", auth=auth)


@router.get("/v1/staff/caseload", status_code=200, response_model=None)
async def get_staff_caseload(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    role: Annotated[str | None, Query(max_length=40)] = None,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_caseload",
        auth=auth,
        query_params={"role": role or ""},
    )


@router.get("/v1/staff/appointments", status_code=200, response_model=None)
async def get_staff_appointments(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    staff_member_id: Annotated[str | None, Query(alias="staffMemberId", max_length=64)] = None,
    window_from: Annotated[str | None, Query(alias="from", max_length=40)] = None,
    window_to: Annotated[str | None, Query(alias="to", max_length=40)] = None,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_appointments",
        auth=auth,
        query_params={
            "staffMemberId": staff_member_id or "",
            "from": window_from or "",
            "to": window_to or "",
        },
    )


@router.patch("/v1/staff/appointments/{appointmentId}", status_code=200, response_model=None)
async def update_staff_appointment(
    appointment_id: Annotated[UUID, Path(alias="appointmentId")],
    body: UpdateStaffAppointmentRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_appointment",
        auth=auth,
        payload=body.public_payload(),
        path_params={"appointmentId": _uuid(appointment_id)},
    )


@router.get("/v1/staff/morning-brew", status_code=200, response_model=None)
async def get_staff_morning_brew(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service, request=request, operation="staff.get_morning_brew", auth=auth
    )


@router.get("/v1/staff/tenant-configuration", status_code=200, response_model=None)
async def get_staff_tenant_configuration(
    request: Request, service: ServiceDependency, auth: AuthDependency
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_tenant_configuration",
        auth=auth,
    )


@router.patch("/v1/staff/tenant-configuration", status_code=200, response_model=None)
async def update_staff_tenant_configuration(
    body: UpdateTenantPortalConfigurationRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_tenant_configuration",
        auth=auth,
        payload=body.public_payload(),
    )


@router.post("/v1/staff/media", status_code=201, response_model=None)
async def upload_staff_portal_media(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    upload = await _read_portal_media_upload(request)
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.upload_portal_media",
        auth=auth,
        upload=upload,
        payload={"publicBaseUrl": str(request.base_url).rstrip("/")},
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


@router.get("/v1/staff/inquiries/{id}/thread", status_code=200, response_model=None)
async def get_staff_inquiry_thread(
    inquiry_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_inquiry_thread",
        auth=auth,
        path_params={"inquiryId": _uuid(inquiry_id)},
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
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    status: Annotated[str | None, Query(max_length=24)] = None,
    priority: Annotated[str | None, Query(max_length=16)] = None,
    component: Annotated[str | None, Query(max_length=120)] = None,
    assignee: Annotated[str | None, Query(max_length=64)] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    due: Annotated[str | None, Query(max_length=16)] = None,
    stale: Annotated[str | None, Query(max_length=8)] = None,
    owner_risk: Annotated[str | None, Query(alias="ownerRisk", max_length=8)] = None,
    escalated: Annotated[str | None, Query(max_length=8)] = None,
    action_type: Annotated[str | None, Query(alias="actionType", max_length=64)] = None,
    work_type: Annotated[str | None, Query(alias="workType", max_length=64)] = None,
    student_id: Annotated[str | None, Query(alias="studentId", max_length=64)] = None,
    in_progress_days: Annotated[str | None, Query(alias="inProgressDays", max_length=6)] = None,
    sort: Annotated[str | None, Query(max_length=16)] = None,
    limit: Annotated[str | None, Query(max_length=6)] = None,
    offset: Annotated[str | None, Query(max_length=8)] = None,
) -> object:
    """One bounded page of the board. Defaults to open work, 50 items."""

    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_action_center",
        auth=auth,
        query_params={
            key: value
            for key, value in {
                "status": status,
                "priority": priority,
                "component": component,
                "assignee": assignee,
                "search": search,
                "due": due,
                "stale": stale,
                "ownerRisk": owner_risk,
                "escalated": escalated,
                "actionType": action_type,
                "workType": work_type,
                "studentId": student_id,
                "inProgressDays": in_progress_days,
                "sort": sort,
                "limit": limit,
                "offset": offset,
            }.items()
            if value is not None
        },
    )


@router.post("/v1/staff/work-items", status_code=201, response_model=None)
async def create_work_item(
    body: CreateStaffWorkItemRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.create_work_item",
        auth=auth,
        payload=body.public_payload(),
        idempotency_key=idempotency_key,
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


def _staff_sse_message(event: Mapping[str, object]) -> str:
    cursor_value = event["cursor"]
    if not isinstance(cursor_value, int):
        raise TypeError("Staff realtime event cursor must be an integer")
    cursor = cursor_value
    event_type = str(event["type"]).replace("\r", "").replace("\n", "")
    return (
        f"id: {cursor}\n"
        f"event: {event_type}\n"
        f"data: {json.dumps(dict(event), separators=(',', ':'), default=str)}\n\n"
    )


@router.get("/v1/staff/events", status_code=200, response_model=None)
async def stream_staff_events(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    cursor = after
    header_cursor = request.headers.get("last-event-id")
    if cursor is None and header_cursor:
        if not header_cursor.isascii() or not header_cursor.isdecimal():
            raise BadRequestError(
                "INVALID_EVENT_CURSOR",
                "Last-Event-ID must be a non-negative numeric cursor",
            )
        cursor = int(header_cursor)

    async def events() -> Any:
        bootstrap = cursor is None or cursor == 0
        current = None if bootstrap else cursor
        heartbeat_at = time.monotonic()
        yield "retry: 2000\n\n"
        ready_sent = False
        while not await request.is_disconnected():
            batch = await service.dispatch(
                ServiceCall(
                    operation="staff.get_realtime_events",
                    auth=auth,
                    request_id=request.state.request_id,
                    payload={"afterCursor": current, "limit": 100},
                )
            )
            if not isinstance(batch, Mapping):
                raise ApiError(
                    500,
                    "INVALID_EVENT_STREAM_RESPONSE",
                    "The platform service returned an invalid event stream response",
                )
            raw_events = batch.get("events")
            if isinstance(raw_events, list):
                for raw_event in raw_events:
                    if not isinstance(raw_event, Mapping):
                        continue
                    current = int(raw_event["cursor"])
                    yield _staff_sse_message(raw_event)
                    heartbeat_at = time.monotonic()
            if current is None and batch.get("cursor") is not None:
                current = int(batch["cursor"])
            if bootstrap and not ready_sent and current is not None:
                yield _staff_sse_message(
                    {
                        "cursor": current,
                        "type": "staff.stream.ready",
                        "resourceType": "tenant",
                        "resourceId": auth.tenant_id,
                        "workItemId": None,
                        "data": {"invalidate": ["workspace", "notifications", "messages"]},
                    }
                )
                ready_sent = True
                heartbeat_at = time.monotonic()
            now = time.monotonic()
            if now - heartbeat_at >= 15:
                yield f": heartbeat {current or 0}\n\n"
                heartbeat_at = now
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/v1/staff/work-items/{id}", status_code=200, response_model=None)
async def get_work_item_detail(
    work_item_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_work_item_detail",
        auth=auth,
        path_params={"workItemId": _uuid(work_item_id)},
    )


@router.post("/v1/staff/work-items/{id}/comments", status_code=201, response_model=None)
async def create_work_item_comment(
    work_item_id: Annotated[UUID, Path(alias="id")],
    body: CreateStaffWorkCommentRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.create_work_comment",
        auth=auth,
        payload=body.public_payload(),
        path_params={"workItemId": _uuid(work_item_id)},
        idempotency_key=idempotency_key,
    )


@router.put("/v1/staff/work-items/{id}/outreach-draft", status_code=200, response_model=None)
async def save_outreach_draft(
    work_item_id: Annotated[UUID, Path(alias="id")],
    body: SaveStaffOutreachDraftRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.save_outreach_draft",
        auth=auth,
        payload=body.public_payload(),
        path_params={"workItemId": _uuid(work_item_id)},
        idempotency_key=idempotency_key,
    )


@router.post("/v1/staff/work-items/{id}/interactions", status_code=201, response_model=None)
async def start_work_item_interaction(
    work_item_id: Annotated[UUID, Path(alias="id")],
    body: StartStaffInteractionRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.start_interaction",
        auth=auth,
        payload=body.public_payload(),
        path_params={"workItemId": _uuid(work_item_id)},
        idempotency_key=idempotency_key,
    )


@router.post(
    "/v1/staff/interactions/{id}/communications",
    status_code=201,
    response_model=None,
)
async def record_interaction_communication(
    interaction_id: Annotated[UUID, Path(alias="id")],
    body: RecordStaffCommunicationRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.record_interaction_communication",
        auth=auth,
        payload=body.public_payload(),
        path_params={"interactionId": _uuid(interaction_id)},
        idempotency_key=idempotency_key,
    )


@router.post(
    "/v1/staff/interactions/{id}/recordings",
    status_code=201,
    response_model=None,
)
async def upload_interaction_recording(
    interaction_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    idempotency_key: IdempotencyDependency,
) -> object:
    upload = await _read_call_recording_upload(request)
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.upload_call_recording",
        auth=auth,
        upload=upload,
        path_params={"interactionId": _uuid(interaction_id)},
        idempotency_key=idempotency_key,
    )


@router.get("/v1/staff/call-recordings/{id}/content", status_code=200)
async def get_call_recording_content(
    recording_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> Response:
    result = await _dispatch(
        service=service,
        request=request,
        operation="staff.get_call_recording_content",
        auth=auth,
        path_params={"recordingId": _uuid(recording_id)},
    )
    return _binary_response(result)


@router.post(
    "/v1/staff/call-recordings/{id}/retry",
    status_code=202,
    response_model=None,
)
async def retry_call_recording_transcription(
    recording_id: Annotated[UUID, Path(alias="id")],
    body: RetryStaffCallTranscriptionRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.retry_call_transcription",
        auth=auth,
        payload=body.public_payload(),
        path_params={"recordingId": _uuid(recording_id)},
    )


@router.post(
    "/v1/staff/interactions/{id}/complete",
    status_code=200,
    response_model=None,
)
async def complete_interaction(
    interaction_id: Annotated[UUID, Path(alias="id")],
    body: CompleteStaffInteractionRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.complete_interaction",
        auth=auth,
        payload=body.public_payload(),
        path_params={"interactionId": _uuid(interaction_id)},
    )


@router.post("/v1/staff/work-items/{id}/ai-refresh", status_code=202, response_model=None)
async def request_work_item_ai_refresh(
    work_item_id: Annotated[UUID, Path(alias="id")],
    body: RequestStaffAiRefreshRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.request_ai_refresh",
        auth=auth,
        payload=body.public_payload(),
        path_params={"workItemId": _uuid(work_item_id)},
    )


@router.get("/v1/staff/action-rules", status_code=200, response_model=None)
async def get_staff_action_rules(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_action_rules",
        auth=auth,
    )


@router.get("/v1/staff/notifications", status_code=200, response_model=None)
async def get_staff_notifications(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_notifications",
        auth=auth,
    )


@router.post(
    "/v1/staff/notifications/{id}/read",
    status_code=200,
    response_model=None,
)
async def mark_staff_notification_read(
    notification_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.mark_notification_read",
        auth=auth,
        path_params={"notificationId": _uuid(notification_id)},
    )


@router.post("/v1/staff/action-rules", status_code=201, response_model=None)
async def create_staff_action_rule(
    body: CreateStaffActionRuleRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.create_action_rule",
        auth=auth,
        payload=body.public_payload(),
    )


@router.patch("/v1/staff/action-rules/{id}", status_code=200, response_model=None)
async def update_staff_action_rule(
    rule_id: Annotated[UUID, Path(alias="id")],
    body: UpdateStaffActionRuleRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.update_action_rule",
        auth=auth,
        payload=body.public_payload(),
        path_params={"ruleId": _uuid(rule_id)},
    )


@router.post("/v1/staff/assistant/messages", status_code=200, response_model=None)
async def ask_staff_edward(
    body: AskStaffEdwardRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.ask_edward",
        auth=auth,
        payload=body.public_payload(),
        assistant_execution=_assistant_execution_mode(request),
    )


@router.get("/v1/staff/assistant/action-intents/{id}", status_code=200, response_model=None)
async def get_staff_edward_action(
    intent_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_edward_action",
        auth=auth,
        path_params={"intentId": _uuid(intent_id)},
    )


@router.post(
    "/v1/staff/assistant/action-intents/{id}/confirm",
    status_code=200,
    response_model=None,
)
async def confirm_staff_edward_action(
    intent_id: Annotated[UUID, Path(alias="id")],
    body: ConfirmEdwardActionRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.confirm_edward_action",
        auth=auth,
        payload=body.public_payload(),
        path_params={"intentId": _uuid(intent_id)},
    )


@router.post(
    "/v1/staff/assistant/action-intents/{id}/cancel",
    status_code=200,
    response_model=None,
)
async def cancel_staff_edward_action(
    intent_id: Annotated[UUID, Path(alias="id")],
    body: CancelEdwardActionRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.cancel_edward_action",
        auth=auth,
        payload=body.public_payload(),
        path_params={"intentId": _uuid(intent_id)},
    )


@router.patch(
    "/v1/staff/assistant/messages/{id}/feedback",
    status_code=200,
    response_model=None,
)
async def submit_staff_edward_feedback(
    assistant_message_id: Annotated[UUID, Path(alias="id")],
    body: EdwardFeedbackRequest,
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.submit_edward_feedback",
        auth=auth,
        path_params={"assistantMessageId": _uuid(assistant_message_id)},
        payload=body.public_payload(),
    )


@router.post("/v1/staff/assistant/conversations", status_code=201, response_model=None)
async def create_staff_assistant_conversation(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.create_assistant_conversation",
        auth=auth,
    )


@router.get(
    "/v1/staff/assistant/conversations/{id}/messages",
    status_code=200,
    response_model=None,
)
async def get_staff_assistant_conversation_messages(
    conversation_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.get_assistant_conversation_messages",
        auth=auth,
        path_params={"conversationId": _uuid(conversation_id)},
    )


@router.get("/v1/staff/students", status_code=200, response_model=None)
async def search_staff_students(
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
    query: Annotated[str | None, Query(max_length=120)] = None,
    student_id: Annotated[str | None, Query(alias="studentId", max_length=64)] = None,
    limit: Annotated[str | None, Query(max_length=6)] = None,
) -> object:
    """One bounded page of the tenant roster, searched server-side (max 200)."""

    return await _dispatch(
        service=service,
        request=request,
        operation="staff.search_students",
        auth=auth,
        query_params={
            key: value
            for key, value in {"query": query, "studentId": student_id, "limit": limit}.items()
            if value is not None
        },
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
    idempotency_key: IdempotencyDependency,
) -> object:
    return await _dispatch(
        service=service,
        request=request,
        operation="staff.review_document",
        auth=auth,
        payload=body.public_payload(),
        path_params={"documentId": _uuid(document_id)},
        idempotency_key=idempotency_key,
    )


@router.get("/v1/staff/documents/{id}/content", status_code=200, response_model=None)
async def get_staff_document_content(
    document_id: Annotated[UUID, Path(alias="id")],
    request: Request,
    service: ServiceDependency,
    auth: AuthDependency,
) -> Response:
    result = await _dispatch(
        service=service,
        request=request,
        operation="staff.get_document_content",
        auth=auth,
        path_params={"documentId": _uuid(document_id)},
    )
    return _binary_response(result)


@router.get("/v1/student/university", response_model=None)
async def student_university(
    request: Request, auth: AuthDependency, service: ServiceDependency, domain: str = "overview"
) -> object:
    return await _dispatch(
        service=service,
        operation="student.university",
        request=request,
        auth=auth,
        query_params={"domain": domain},
    )


@router.get("/v1/staff/students/{id}/university", response_model=None)
async def staff_student_university(
    request: Request,
    id: UUID,
    auth: AuthDependency,
    service: ServiceDependency,
    domain: str = "overview",
) -> object:
    return await _dispatch(
        service=service,
        operation="staff.student_university",
        request=request,
        auth=auth,
        path_params={"id": str(id)},
        query_params={"domain": domain},
    )


@router.get("/v1/staff/university", response_model=None)
async def staff_university(
    request: Request, auth: AuthDependency, service: ServiceDependency
) -> object:
    return await _dispatch(
        service=service, operation="staff.university", request=request, auth=auth
    )


@router.get("/v1/student/financial-plan", response_model=None)
async def get_financial_plan(
    request: Request, auth: AuthDependency, service: ServiceDependency, term_id: str = "2026FA"
) -> object:
    return await _dispatch(
        service=service,
        operation="student.financial_plan",
        request=request,
        auth=auth,
        query_params={"termId": term_id},
    )


@router.put("/v1/student/financial-plan/inputs", response_model=None)
async def save_financial_plan_inputs(
    request: Request,
    auth: AuthDependency,
    service: ServiceDependency,
    idempotency_key: IdempotencyDependency,
    body: dict[str, object],
) -> object:
    return await _dispatch(
        service=service,
        operation="student.save_financial_plan",
        request=request,
        auth=auth,
        payload=body,
        idempotency_key=idempotency_key,
    )


@router.get("/v1/staff/work-board", response_model=None)
async def get_work_board(
    request: Request,
    auth: AuthDependency,
    service: ServiceDependency,
    offset: int = 0,
    project: str | None = None,
) -> object:
    return await _dispatch(
        service=service,
        operation="staff.work_board",
        request=request,
        auth=auth,
        query_params={
            **dict(request.query_params),
            "offset": str(offset),
            **({"project": project} if project else {}),
        },
    )


@router.post("/v1/student/financial-plan/simulate", response_model=None)
async def simulate_financial_plan(
    request: Request,
    auth: AuthDependency,
    service: ServiceDependency,
    body: dict[str, object],
) -> object:
    return await _dispatch(
        service=service,
        operation="student.simulate_financial_plan",
        request=request,
        auth=auth,
        payload=body,
    )


@router.get("/v1/staff/document-review/options", response_model=None)
async def document_review_options(
    request: Request, auth: AuthDependency, service: ServiceDependency
) -> object:
    return await _dispatch(
        service=service, operation="staff.document_review_options", request=request, auth=auth
    )
