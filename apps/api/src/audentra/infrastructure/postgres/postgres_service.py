"""Production application service composed from PostgreSQL repositories and adapters."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import text

from audentra.application.morning_brew import build_morning_brew
from audentra.application.staff_workspace import (
    compose_staff_workspace,
    draft_managed_configuration,
    preview_edward,
    simulate_outreach,
)
from audentra.core.assistant_execution import (
    DEFAULT_ASSISTANT_EXECUTION,
    ResolvedAssistantExecutionMode,
    model_hook,
)
from audentra.core.auth import AuthContext
from audentra.core.delegate_authorization import require_delegate_scope
from audentra.core.errors import ApiError, BadRequestError, NotFoundError, UnauthorizedError
from audentra.core.ports import BinaryPayload, ServiceCall
from audentra.domain.action_center import (
    DEFAULT_QUERY,
    ActionCenterQuery,
    parse_action_center_query,
)
from audentra.domain.documents import (
    can_retry_extraction,
    classify_extraction_failure,
    deterministic_document_id,
    document_type_for_category,
    failed_extraction,
    validate_document_upload,
)
from audentra.domain.edward_action_catalog import CAPABILITY_BY_ACTION
from audentra.domain.edward_actions import (
    SemanticActionRequest,
    amend_pending_action,
    has_question_sentence,
    named_day,
    parse_staff_action,
    parse_student_action,
    preference_fields_incomplete,
    question_sentences,
    recognize_with_model,
    repeats_action_for_another,
    untrusted_action_framing,
)
from audentra.domain.student_state import derive_deposit_state
from audentra.infrastructure.documents.processing import (
    NormalizedImageRegion,
    SignatureBox,
    SigningInput,
    create_signed_onboarding_pdf,
    extract_student_document_image_region,
)
from audentra.infrastructure.postgres.knowledge_repository import (
    PostgresInstitutionKnowledgeRepository,
    SearchQuery,
)
from audentra.infrastructure.storage.s3 import ObjectNotFoundError, StorageError
from audentra.integrations import edward_action_responses as action_responses
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
from audentra.integrations.staff_assistant.classify import (
    STAFF_REQUEST_TYPES,
    cohort_filter_from_text,
)
from audentra.integrations.staff_assistant.normalize import normalize_staff_request
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

from .advising_repository import PostgresAdvisingRepository
from .edward_action_gateway import EdwardActionGateway, StaffEmailActions
from .edward_feedback_repository import PostgresEdwardFeedbackRepository
from .ferpa_repository import PostgresFerpaRepository
from .managed_configuration_repository import PostgresManagedConfigurationRepository
from .model_usage_ledger import record_assistant_usage
from .morning_brew_repository import PostgresMorningBrewRepository
from .platform_repository import PostgresPlatformRepository
from .portal_repository import PostgresPortalRepository
from .staff_assistant_repository import PostgresStaffAssistantRepository
from .staff_operations_repository import PostgresStaffOperationsRepository
from .staff_repository import PostgresStaffRepository
from .tenant_repository import PostgresTenantRepository
from .university_repository import PostgresUniversityRepository

JsonDict = dict[str, Any]
LOGGER = logging.getLogger(__name__)
# The first page of the reader's own queue shipped inside the workspace; the
# Action Center pages the rest through GET /v1/staff/action-center?assignee=me.
PERSONAL_QUEUE_PAGE_LIMIT = 50
_LOCAL_FERPA_LINK_SECRET = "local-development-ferpa-delegate-link-secret-v1"  # noqa: S105

SIGNED_TEMPLATES = (
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
# Tenants with their own reviewed template artwork. Every other university signs
# the standard set below, so signing is never withheld from a student.
SIGNED_TEMPLATE_TENANT_PREFIXES = {
    ASTER_TENANT_ID: "aster",
    HARVARD_TENANT_ID: "harvard",
}
DEFAULT_SIGNED_TEMPLATE_PREFIX = "aster"

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


def _pipeline_already_asked(result: Any) -> bool:
    """True when the read turn ended in a question the action clarifier
    would only make worse.

    An ambiguous or unknown student name produces a candidate list with
    student references in it. Replacing that with "Which student is the
    follow-up for?" throws away the disambiguators the person needs.
    """

    if getattr(result, "resolved_student_id", None):
        return False
    message = str(getattr(result, "message", "") or "")
    return "?" in message and bool(
        re.search(r"which one|did you mean|couldn't find|SYN-\d", message, re.I)
    )


def _continued_action(
    message: str,
    state: Mapping[str, Any],
    *,
    actor: str,
) -> tuple[SemanticActionRequest | None, str | None]:
    """Carry an action forward when the message only carries the change.

    Two shapes, both ordinary in a working conversation and both previously
    lost to the read pipeline:

    * **Amendment** — "actually make it urgent". The verb and the target are in
      the previous turn; only the correction is here. A confirmation card is
      immutable by design, so this produces a *fresh* proposal for the same
      action against the same target, which the gateway re-resolves and
      re-authorizes from scratch. Only a pending, unconfirmed intent can be
      amended: once something is committed the honest answer is a new change.
    * **Repetition** — "and one for Greta Oakenshaw too". The same action, a
      newly named person. The target is *not* inherited here; the person named
      this turn is resolved normally, which is what keeps a repetition from
      quietly acting on the previous student.

    Returns the request and, for an amendment only, the student to inherit.
    """

    pending = [item for item in _sequence(state.get("pending")) if isinstance(item, Mapping)]
    receipts = [item for item in _sequence(state.get("receipts")) if isinstance(item, Mapping)]
    last_field: str | None = None
    pending_fields: dict[str, Any] = {}
    if pending:
        last_action = str(pending[-1].get("action") or "")
        inherited = pending[-1].get("targetStudentId")
        raw_pending_fields = pending[-1].get("fields")
        if isinstance(raw_pending_fields, Mapping):
            pending_fields = dict(raw_pending_fields)
        # "Right — use 020 7946 0222 instead" names a value and no field; the
        # field is whichever one the pending proposal already carries.
        if len(pending_fields) == 1:
            last_field = next(iter(pending_fields))
    elif receipts:
        last_action = str(receipts[-1].get("action") or "")
        inherited = None
        result = receipts[-1].get("result")
        changes = _sequence(result.get("changes")) if isinstance(result, Mapping) else ()
        last_field = next(
            (
                str(item["field"])
                for item in changes
                if isinstance(item, Mapping) and item.get("field")
            ),
            None,
        )
    else:
        return None, None
    if not last_action:
        return None, None

    if repeats_action_for_another(message):
        if last_action not in {
            "operations.follow_up.create",
            "operations.work_item.update",
            "student.preferences.update",
        }:
            return None, None
        fields = (
            _follow_up_fields_for(message) if last_action == "operations.follow_up.create" else {}
        )
        repeated_request = SemanticActionRequest(
            cast(Any, last_action), fields, 0.85, source="continuation"
        )
        return repeated_request, None

    amended = amend_pending_action(
        message, cast(Any, last_action), actor=cast(Any, actor), last_field=last_field
    )
    if amended is None:
        return None, None
    if pending and pending_fields:
        # Corrections accumulate: "make it urgent", then "and due thursday",
        # must end urgent AND due thursday. Each amendment is still a fresh
        # proposal — only the starting fields come from the one on the table.
        merged = {**pending_fields, **amended.fields}
        amended = SemanticActionRequest(
            amended.action, merged, amended.confidence, source="continuation"
        )
    # An amendment to a *pending* proposal keeps its target; one that follows a
    # committed change is a new change to the same record, which the actor's
    # own identity already resolves.
    return amended, (str(inherited) if pending and inherited else None)


#: The clarifying questions the write plane itself asks, matched against the
#: previous assistant turn so the user's next message can be read as the
#: answer. Each sentinel is a stable substring of exactly one server-authored
#: question; prose from the read plane never contains them.
_CLARIFY_BLOCKER_SENTINEL = "What is it waiting on?"
_CLARIFY_DATE_SENTINELS = (
    "needs a date and a next step",
    "Choose a date for work that moves to follow-up",
)
_CLARIFY_CANCEL_SENTINEL = "what should the record say the reason was"
_CLARIFY_STUDENT_SENTINELS = (
    "Which student is the follow-up for?",
    "Who should the email go to?",
    "which one do you mean",
)

_ANY_WORK_ITEM_KEY = re.compile(r"\b([A-Za-z]{2,6}-\d{2,6})\b")


def _last_key_anywhere(history: Sequence[Mapping[str, Any]]) -> str | None:
    """The most recent work-item key in the conversation, noun or no noun.

    `_recent_work_item_key` requires a task noun beside the key so "that
    task" cannot inherit an id from prose. The clarify loop is stricter
    context: Edward's own question already bound the item, and the answer to
    "what is it waiting on?" ("AST-00006 is stuck…" two turns up) rarely
    repeats a noun."""

    for item in reversed(history):
        found = _ANY_WORK_ITEM_KEY.findall(str(item.get("content") or ""))
        if found:
            return str(found[-1]).upper()
    return None


def _clarify_loop_continuation(
    message: str, history: Sequence[Mapping[str, Any]]
) -> tuple[SemanticActionRequest, str | None, bool] | None:
    """Interpret this turn as the answer to Edward's own last question.

    Returns the reconstructed request, a work-item key recovered from the
    conversation when the question was about one, and whether the turn is a
    disambiguation pick whose resolved student should be trusted as the
    action target. Everything still crosses the gateway: this establishes a
    *request*, never authority.
    """

    text = message.strip()
    if not text or len(text) > 300 or untrusted_action_framing(text):
        return None
    last_assistant = next(
        (item for item in reversed(history) if str(item.get("role")) == "assistant"), None
    )
    if last_assistant is None:
        return None
    question = str(last_assistant.get("content") or "")

    if _CLARIFY_BLOCKER_SENTINEL in question:
        fields: JsonDict = {"status": "blocked", "nextStep": text[:200]}
        return (
            SemanticActionRequest(
                "operations.work_item.update", fields, 0.9, source="continuation"
            ),
            _last_key_anywhere(history),
            False,
        )
    if any(sentinel in question for sentinel in _CLARIFY_DATE_SENTINELS):
        fields = {"status": "follow_up_required", "nextStep": text[:200]}
        day = named_day(text)
        if day:
            fields["followUp"] = day
        return (
            SemanticActionRequest(
                "operations.work_item.update", fields, 0.9, source="continuation"
            ),
            _last_key_anywhere(history),
            False,
        )
    if _CLARIFY_CANCEL_SENTINEL in question:
        fields = {"status": "cancelled", "nextStep": text[:200]}
        return (
            SemanticActionRequest(
                "operations.work_item.update", fields, 0.9, source="continuation"
            ),
            _last_key_anywhere(history),
            False,
        )
    if any(sentinel in question for sentinel in _CLARIFY_STUDENT_SENTINELS):
        # The action lives in the user turn that triggered the question; the
        # student lives in this one. Re-derive the action from that turn and
        # let normal resolution bind whoever this turn names or picks.
        for item in reversed(history):
            if str(item.get("role")) != "user":
                continue
            content = str(item.get("content") or "")
            if content.strip() == text:
                continue
            prior = parse_staff_action(content)
            if prior is not None and prior.action in {
                "operations.follow_up.create",
                "communications.email.prepare",
            }:
                return (
                    SemanticActionRequest(
                        prior.action, dict(prior.fields), 0.85, source="continuation"
                    ),
                    None,
                    True,
                )
            break
    return None


def _follow_up_fields_for(message: str) -> JsonDict:
    """Field values a repeated follow-up should carry from its own sentence."""

    repeated = parse_staff_action(f"create a follow-up {message}")
    return dict(repeated.fields) if repeated is not None else {}


class _ActionClarified(Exception):
    """Internal signal: the turn asked a question instead of proposing."""


def _recognition_source(
    request: SemanticActionRequest | None, recognizer_usage: Mapping[str, Any] | None
) -> str | None:
    """Which recognition stage settled this turn, for the trace.

    "pattern" and "continuation" come from the request itself; "model" means
    tier 1 recognized it; "model_none" means tier 1 was actually called (a
    provider round trip happened) and found nothing. None means no stage
    produced anything and no provider was consulted — the turn was never
    action-shaped, or recognition was blocked before any tier ran.
    """

    if request is not None:
        return request.source
    if recognizer_usage is not None:
        return "model_none"
    return None


def _record_recognizer_call(
    trace: AssistantTurnTrace,
    usage: Mapping[str, Any] | None,
    request: SemanticActionRequest | None,
) -> None:
    """Meter the model recognition tier like every other model call.

    A tier that is invisible in the trace is a tier nobody can attribute a
    regression or a cost to, and this one runs on turns the read planner never
    sees.
    """

    if usage is None:
        return
    trace.add_model_call(
        operation="action_recognizer",
        attempt=1,
        duration_ms=float(usage.get("durationMs") or 0.0),
        outcome="recognized" if request is not None else "no_action",
        provider=usage.get("provider"),
        model=usage.get("model"),
        usage=usage.get("usage") if isinstance(usage.get("usage"), Mapping) else None,
        detail=request.action if request is not None else None,
    )


def _edward_action_unavailable_response(error: ApiError) -> JsonDict:
    """Render an expected policy or resolution denial without claiming a write."""

    message = error.message
    return {
        "message": message,
        "blocks": [{"type": "text", "text": message}],
        "provider": "guided",
        "model": None,
        "usage": None,
        "actionIntents": [],
        "actionReceipts": [],
        "actionError": {"code": error.code, "message": message},
    }


def _recent_work_item_key(history: Sequence[Mapping[str, Any]]) -> str | None:
    """Resolve "that task" from recent server history without inheriting an ID blindly.

    A key in the *user's own* words ("show me AST-00533" → "set it in
    progress") is their reference and safe to carry. A key inside Edward's
    prose still needs a task noun beside it, so an answer that happens to
    list keys cannot silently become the target of the next verb.
    """

    for item in reversed(history):
        content = str(item.get("content") or "")
        normalized = normalize_staff_request(content)
        key = normalized.work_item_key
        if key and (
            str(item.get("role")) == "user"
            or re.search(r"\b(?:task|work item|item|case|ticket)\b", content, re.I)
        ):
            return key
    return None


def _action_may_inherit_student(
    message: str, action: str, *, has_server_draft: bool = False
) -> bool:
    """Require current-turn referential language before using a carried student."""

    if re.search(
        r"\b(?:her|him|their|that student|this student|the same student|for them)\b",
        message,
        re.I,
    ):
        return True
    return bool(
        action == "communications.email.prepare"
        and has_server_draft
        # A server-persisted draft names its own recipient. "Send that",
        # "prepare it", "get that ready to go" are all the same request about
        # the same draft, and asking "who should the email go to?" when the
        # answer is written on the draft is a round trip for nothing.
        and re.search(r"\b(?:that|this|the|it)\b|\bsend it\b|\bready to go\b", message, re.I)
    )


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
    morning_brew: PostgresMorningBrewRepository | None = None
    ferpa: PostgresFerpaRepository | None = None
    edward_feedback: PostgresEdwardFeedbackRepository | None = None
    advising: PostgresAdvisingRepository | None = None
    staff_operations: PostgresStaffOperationsRepository | None = None
    # The approved institutional knowledge corpus (policies, calendar,
    # offices). Optional: a tenant without a corpus answers institutional
    # questions with the honest refusal, exactly as before.
    knowledge: PostgresInstitutionKnowledgeRepository | None = None
    university: PostgresUniversityRepository | None = None


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


class FerpaSignedDocumentGenerator(Protocol):
    async def create_ferpa(
        self,
        *,
        auth: AuthContext,
        authorization: Mapping[str, Any],
        signature: Mapping[str, Any],
        reservation: Mapping[str, Any],
        storage: ObjectStorage,
    ) -> JsonDict: ...


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
        tenant_prefix = SIGNED_TEMPLATE_TENANT_PREFIXES.get(
            auth.tenant_id, DEFAULT_SIGNED_TEMPLATE_PREFIX
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

    async def create_ferpa(
        self,
        *,
        auth: AuthContext,
        authorization: Mapping[str, Any],
        signature: Mapping[str, Any],
        reservation: Mapping[str, Any],
        storage: ObjectStorage,
    ) -> JsonDict:
        signer_name = str(signature.get("signerName") or "").strip()
        method = str(signature.get("signatureMethod") or "typed")
        if (
            signature.get("accepted") is not True
            or not signer_name
            or method not in {"typed", "drawn"}
        ):
            raise BadRequestError("FERPA_SIGNATURE_REQUIRED", "Provide a valid FERPA signature")
        signed_at = str(reservation["signedAt"])
        authorization_id = str(authorization["id"])
        document_id = str(reservation["documentId"])
        storage_key = str(reservation["storageKey"])
        try:
            pdf = await storage.get(storage_key)
        except ObjectNotFoundError:
            tenant_prefix = SIGNED_TEMPLATE_TENANT_PREFIXES.get(
                auth.tenant_id, DEFAULT_SIGNED_TEMPLATE_PREFIX
            )
            pdf = await create_signed_onboarding_pdf(
                SigningInput(
                    template_bytes=self._read_template(f"{tenant_prefix}-ferpa-release.pdf"),
                    signer_name=signer_name,
                    signature_method=cast(Any, method),
                    signature_image_data=(
                        str(signature["signatureImageData"])
                        if method == "drawn" and signature.get("signatureImageData")
                        else None
                    ),
                    signed_at=signed_at,
                    audit_receipt=f"ferpa.{authorization_id}.{auth.student_id}",
                    signature_box=SignatureBox(x=0.098, y=0.488, width=0.53, height=0.054),
                )
            )
            digest = hashlib.sha256(pdf).hexdigest()
            await storage.put(storage_key, pdf, content_type="application/pdf", sha256=digest)
        digest = hashlib.sha256(pdf).hexdigest()
        return {
            "id": document_id,
            "tenantId": auth.tenant_id,
            "studentId": auth.student_id,
            "title": "FERPA Information Release",
            "fileName": "ferpa-information-release-signed.pdf",
            "sizeBytes": len(pdf),
            "storageProvider": "s3",
            "storageKey": storage_key,
            "sha256": digest,
            "signerName": signer_name,
            "signatureMethod": method,
            "signedAt": signed_at,
        }


class PostgresPlatformService:
    """Dispatch canonical and preview operations without coupling them to FastAPI."""

    def __init__(
        self,
        repository: PostgresRepositoryBundle,
        storage: ObjectStorage,
        ai: StudentAI,
        signed_documents: SignedDocumentGenerator,
        worker_token: str,
        *,
        ferpa_link_secret: str = _LOCAL_FERPA_LINK_SECRET,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.ai = ai
        self.signed_documents = signed_documents
        self.worker_token = worker_token
        self.ferpa_link_secret = ferpa_link_secret
        self.edward_actions = (
            EdwardActionGateway(
                repository.portal.engine,
                repository.portal,
                repository.staff,
                repository.staff_assistant,
            )
            if repository.staff_assistant is not None
            else None
        )

    def configure_edward_staff_email(self, staff_email: StaffEmailActions) -> None:
        """Attach mail after bootstrap constructs the independently routed service."""

        if self.repository.staff_assistant is not None:
            self.edward_actions = EdwardActionGateway(
                self.repository.portal.engine,
                self.repository.portal,
                self.repository.staff,
                self.repository.staff_assistant,
                staff_email=staff_email,
            )

    def _edward_actions(self) -> EdwardActionGateway:
        if self.edward_actions is None:
            raise ApiError(503, "EDWARD_ACTIONS_UNAVAILABLE", "Edward actions are unavailable")
        return self.edward_actions

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
            return await tenant.get_active_by_id(self._path(call, "tenantId"))
        if operation == "internal.get_assistant_trace":
            feedback = self.repository.edward_feedback
            if feedback is None:
                return None
            return await feedback.get_trace(self._path(call, "traceId"))
        if operation == "internal.list_assistant_feedback":
            feedback = self._feedback_repo()
            filters = dict(call.payload)
            return await feedback.list_feedback(
                assistant_kind=cast(str | None, filters.get("assistantKind")),
                rating=cast(str | None, filters.get("rating")),
                has_written=cast(bool | None, filters.get("hasWritten")),
                date_from=cast(str | None, filters.get("from")),
                date_to=cast(str | None, filters.get("to")),
                search=cast(str | None, filters.get("search")),
                limit=int(filters.get("limit") or 50),
                offset=int(filters.get("offset") or 0),
            )

        auth = self._auth(call)
        if operation == "staff.work_board":
            from .work_board_repository import WorkBoardProjection

            return await WorkBoardProjection(self.repository.staff).read(
                auth,
                int(call.query_params.get("offset") or 0),
                call.query_params.get("project"),
                filters={
                    k: v for k, v in call.query_params.items() if k not in {"offset", "project"}
                },
            )
        if operation in {
            "student.financial_plan",
            "student.save_financial_plan",
            "student.simulate_financial_plan",
        }:
            from .financial_plan_repository import FinancialPlanService

            university = self.repository.university
            if university is None or not university.is_enabled(auth):
                raise ApiError(404, "UNIVERSITY_NOT_AVAILABLE", "No university record is available")
            plan = FinancialPlanService(university)
            if operation == "student.simulate_financial_plan":
                return await plan.simulate(auth, dict(call.payload))
            if operation == "student.save_financial_plan":
                return await plan.save_inputs(
                    auth, dict(call.payload), call.idempotency_key, call.request_id
                )
            return await plan.read(auth, str(call.query_params.get("termId") or "2026FA"))
        if operation in {"student.university", "staff.student_university", "staff.university"}:
            university = self.repository.university
            if university is None or not university.is_enabled(auth):
                raise ApiError(404, "UNIVERSITY_NOT_AVAILABLE", "No university record is available")
            if operation.startswith("staff.") and auth.actor_type != "staff":
                raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
            if operation == "staff.university":
                return await university.operations(auth)
            bound = (
                replace(auth, student_id=self._path(call, "id"))
                if operation == "staff.student_university"
                else auth
            )
            return await university.record(
                bound, str(call.query_params.get("domain") or "overview")
            )
        payload = dict(call.payload)
        key = call.idempotency_key
        portal = self.repository.portal
        platform = self.repository.platform
        staff = self.repository.staff

        if operation in {"student.get_edward_action", "staff.get_edward_action"}:
            return await self._edward_actions().get_intent(auth, self._path(call, "intentId", "id"))
        if operation in {"student.confirm_edward_action", "staff.confirm_edward_action"}:
            return await self._edward_actions().confirm(
                auth,
                self._path(call, "intentId", "id"),
                expected_version=int(payload["expectedVersion"]),
                content_sha256=str(payload["contentSha256"]),
                request_id=call.request_id,
            )
        if operation in {"student.cancel_edward_action", "staff.cancel_edward_action"}:
            return await self._edward_actions().cancel(
                auth,
                self._path(call, "intentId", "id"),
                int(payload["expectedVersion"]),
            )

        if operation == "student.get_dashboard":
            return await self._delegate_dashboard(auth, platform.get_student_dashboard(auth))
        if operation == "student.get_academics":
            return self._delegate_academics(auth, await portal.get_student_academics(auth))
        if operation == "catalog.search_courses":
            return await portal.search_catalog_courses(
                auth, str(call.query_params.get("query", ""))
            )
        if operation == "student.get_financials":
            return self._delegate_financials(auth, await portal.get_student_financials(auth))
        if operation == "student.select_payment_plan":
            return await portal.select_financial_payment_plan(
                auth, str(payload["planId"]), self._key(key), call.request_id
            )
        if operation == "student.get_campus_life":
            return self._delegate_campus_life(auth, await portal.get_campus_life(auth))
        if operation == "student.register_campus_event":
            require_delegate_scope(auth, "campus_life")
            return await portal.register_campus_event(
                auth,
                self._path(call, "eventId", "id"),
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "admission.accept_offer":
            if auth.is_delegate:
                raise ApiError(
                    403,
                    "DELEGATE_ROUTE_NOT_ALLOWED",
                    "Parent and guardian onboarding access is view-only",
                )
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
            bootstrap["actor"] = (
                {
                    "type": "delegate",
                    "delegateId": auth.actor_id,
                    "name": auth.delegate_name,
                    "relationship": auth.delegate_relationship,
                    "studentId": auth.student_id,
                    "studentName": auth.subject_student_name,
                    "scopes": sorted(auth.delegate_scopes),
                }
                if auth.is_delegate
                else {"type": "student"}
            )
            if auth.is_delegate:
                bootstrap.pop("rewards", None)
                if "enrollment" not in auth.delegate_scopes:
                    onboarding = _mapping(bootstrap.get("onboarding"))
                    bootstrap["onboarding"] = {
                        "required": bool(onboarding.get("required")),
                        "status": "restricted",
                    }
                if "messages" not in auth.delegate_scopes:
                    bootstrap["unreadMessageCount"] = 0
                if "dashboard" not in auth.delegate_scopes:
                    bootstrap["experienceUpdates"] = []
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
            return await self._delegate_onboarding(auth, portal.get_student_onboarding(auth))
        if operation == "student.update_onboarding":
            self._guard_delegate_onboarding_payload(auth, payload)
            return await portal.update_student_onboarding(auth, payload, call.request_id)
        if operation == "student.complete_onboarding":
            self._guard_delegate_onboarding_payload(auth, payload)
            result = await portal.complete_student_onboarding(
                auth, payload, self._key(key), call.request_id
            )
            await self._ensure_signed(auth, result, call.request_id)
            return result
        if operation == "student.get_housing_plan":
            return await portal.get_student_housing_plan(auth)
        if operation == "student.update_housing_plan":
            require_delegate_scope(auth, "enrollment")
            return await portal.update_student_housing_plan(auth, payload, call.request_id)
        if operation == "student.list_requirements":
            return await self._delegate_requirement_list(
                auth, portal.get_student_requirements(auth)
            )
        if operation == "student.get_requirement":
            identifier = self._path(call, "requirementId", "id", "requirement_id")
            if auth.is_delegate:
                await self._ferpa().requirement_flow(auth, identifier)
            requirement = await portal.get_student_requirement(auth, identifier)
            return self._delegate_requirement_projection(auth, requirement)
        if operation == "student.list_requirement_appointments":
            identifier = self._path(call, "requirementId", "id", "requirement_id")
            await self._authorize_requirement_appointment(auth, identifier, mutation=False)
            appointments = (
                await self.repository.advising.get_student_appointments(auth)
                if self.repository.advising is not None
                else await portal.get_student_appointments(auth)
            )
            active_items = [
                dict(_mapping(item))
                for item in _sequence(appointments.get("items"))
                if _mapping(item).get("status") in {"scheduled", "rescheduled"}
            ]
            return {"items": active_items, "total": len(active_items)}
        if operation == "student.create_requirement_appointment":
            identifier = self._path(call, "requirementId", "id", "requirement_id")
            await self._authorize_requirement_appointment(auth, identifier, mutation=True)
            if self.repository.advising is not None:
                return await self.repository.advising.create_student_appointment(
                    auth, payload, self._key(key), call.request_id
                )
            return await portal.create_student_appointment(
                auth, payload, self._key(key), call.request_id
            )
        if operation == "student.update_requirement_profile":
            identifier = self._path(call, "requirementId", "id", "requirement_id")
            await self._authorize_requirement_profile(auth, identifier)
            return await portal.update_student_profile(auth, payload, call.request_id)
        if operation == "student.submit_requirement_response":
            identifier = self._path(call, "requirementId", "id", "requirement_id")
            context = await self._ferpa().requirement_context(auth, identifier)
            interaction_type = str(context["interactionType"])
            if interaction_type == "ferpa":
                raise ApiError(
                    403 if auth.is_delegate else 409,
                    (
                        "FERPA_STUDENT_CONTROL_REQUIRED"
                        if auth.is_delegate
                        else "REQUIREMENT_SPECIALIZED_SUBMISSION_REQUIRED"
                    ),
                    "Use the student FERPA completion flow for this task",
                )
            if auth.is_delegate and context.get("flowKind") == "onboarding":
                raise ApiError(
                    403,
                    "DELEGATE_ROUTE_NOT_ALLOWED",
                    "Parent and guardian onboarding access is view-only",
                )
            return await portal.submit_student_requirement_response(
                auth,
                identifier,
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "student.get_ferpa_authorization":
            return {"authorization": await self._ferpa().get_current(auth)}
        if operation == "student.complete_ferpa_authorization":
            requirement_id = self._path(call, "requirementId", "id", "requirement_id")
            idempotency_key = self._key(key)
            preflight = await self._ferpa().preflight_completion(
                auth,
                requirement_id,
                payload,
                idempotency_key,
            )
            cached = preflight.get("cachedResponse")
            if isinstance(cached, Mapping):
                return dict(cached)
            current = _mapping(preflight.get("authorization"))
            signature = _mapping(payload.get("signature"))
            signed_document: JsonDict | None = None
            reservation = _mapping(preflight.get("signingReservation"))
            if reservation.get("status") == "stored":
                required = (
                    "documentId",
                    "signedAt",
                    "storageKey",
                    "sha256",
                    "sizeBytes",
                    "title",
                    "fileName",
                    "signerName",
                    "signatureMethod",
                )
                if any(reservation.get(field) is None for field in required):
                    raise ApiError(
                        500,
                        "FERPA_SIGNING_RESERVATION_INVALID",
                        "The prepared FERPA document is incomplete",
                    )
                signed_document = {
                    "id": reservation["documentId"],
                    "tenantId": auth.tenant_id,
                    "studentId": auth.student_id,
                    "title": reservation["title"],
                    "fileName": reservation["fileName"],
                    "sizeBytes": reservation["sizeBytes"],
                    "storageProvider": "s3",
                    "storageKey": reservation["storageKey"],
                    "sha256": reservation["sha256"],
                    "signerName": reservation["signerName"],
                    "signatureMethod": reservation["signatureMethod"],
                    "signedAt": reservation["signedAt"],
                }
                return await self._ferpa().complete(
                    auth,
                    requirement_id,
                    payload,
                    signed_document,
                    idempotency_key,
                    call.request_id,
                )
            if signature and not bool(preflight.get("hasSignedEvidence")):
                if not reservation.get("id"):
                    raise ApiError(
                        500,
                        "FERPA_SIGNING_RESERVATION_MISSING",
                        "The FERPA signing attempt could not be reserved",
                    )
                claimed = await self._ferpa().claim_signing_reservation(
                    auth, str(reservation["id"])
                )
                signed_document = await cast(
                    FerpaSignedDocumentGenerator, self.signed_documents
                ).create_ferpa(
                    auth=auth,
                    authorization=current,
                    signature=signature,
                    reservation=claimed,
                    storage=self.storage,
                )
                await self._ferpa().mark_signing_reservation_stored(
                    auth,
                    str(reservation["id"]),
                    signed_document,
                )
                return await self._ferpa().complete(
                    auth,
                    requirement_id,
                    payload,
                    signed_document,
                    idempotency_key,
                    call.request_id,
                )
            elif not bool(preflight.get("hasSignedEvidence")):
                raise BadRequestError(
                    "FERPA_SIGNATURE_REQUIRED",
                    "The FERPA document must be signed before completion",
                )
            return await self._ferpa().complete(
                auth,
                requirement_id,
                payload,
                signed_document,
                idempotency_key,
                call.request_id,
            )
        if operation == "student.update_ferpa_access":
            return await self._ferpa().update_access(
                auth,
                self._path(call, "authorizationId", "id"),
                payload,
                call.request_id,
            )
        if operation == "student.issue_ferpa_delegate_link":
            authorization_id = self._path(call, "authorizationId")
            delegate_id = self._path(call, "delegateId")
            idempotency_key = self._key(key)
            token = self._delegate_link_token(
                auth,
                authorization_id,
                delegate_id,
                idempotency_key,
            )
            result = await self._ferpa().issue_link(
                auth,
                authorization_id,
                delegate_id,
                expected_version=_integer(payload.get("expectedVersion")),
                token=token,
                idempotency_key=idempotency_key,
                request_id=call.request_id,
            )
            result["url"] = f"/delegate#token={token}"
            return result
        if operation == "student.revoke_ferpa_delegate_link":
            return await self._ferpa().revoke_link(
                auth,
                self._path(call, "authorizationId"),
                self._path(call, "delegateId"),
                expected_version=_integer(payload.get("expectedVersion")),
                request_id=call.request_id,
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
            if not auth.is_delegate:
                await self._ensure_signed(
                    auth, await portal.get_student_onboarding(auth), call.request_id
                )
            documents = await portal.get_student_documents(auth)
            return await self._delegate_document_list(auth, documents)
        if operation == "student.create_document":
            require_delegate_scope(auth, "documents")
            return await portal.create_student_document(
                auth, payload, self._key(key), call.request_id
            )
        if operation == "student.upload_document":
            return await self._upload_document(auth, call)
        if operation == "student.get_document_content":
            document_id = self._path(call, "documentId", "id", "document_id")
            await self._authorize_document_action(auth, document_id)
            return await self._get_document_content(
                auth,
                document_id,
                cache_control="private, no-store",
            )
        if operation == "student.get_document_profile_photo":
            return await self._get_document_profile_photo(
                auth, self._path(call, "documentId", "id", "document_id")
            )
        if operation == "student.confirm_document_extraction":
            document_id = self._path(call, "documentId", "id", "document_id")
            await self._authorize_document_action(auth, document_id, mutation=True)
            return await portal.confirm_student_document_extraction(
                auth,
                document_id,
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "student.retry_document_extraction":
            document_id = self._path(call, "documentId", "id", "document_id")
            if payload:
                raise BadRequestError(
                    "RETRY_EXTRACTION_BODY_NOT_ALLOWED",
                    "Document extraction retry does not accept a request body",
                )
            await self._authorize_document_action(auth, document_id, mutation=True)
            return await self._retry_document_extraction(
                auth,
                document_id,
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
            return await self._ask_edward(
                auth, payload, call.request_id, execution=call.assistant_execution
            )
        if operation == "student.submit_edward_feedback":
            trace_id = str(payload.get("traceId") or "")
            return await self._feedback_repo().submit_student_feedback(
                auth,
                assistant_message_id=self._path(call, "assistantMessageId", "id"),
                trace_id=trace_id,
                payload=payload,
                trace_payload=get_assistant_trace_recorder().get(trace_id),
            )
        if operation == "student.create_assistant_conversation":
            page = _assistant_page_context(payload.get("pageContext"))
            if auth.is_delegate:
                return {
                    "id": str(uuid4()),
                    "status": "active",
                    "messages": [],
                    "createdAt": self._timestamp(),
                    "ephemeral": True,
                }
            return await portal.create_assistant_conversation(
                auth, page_path=page[0], page_label=page[1]
            )
        if operation == "student.get_assistant_conversation_messages":
            if auth.is_delegate:
                return {
                    "conversationId": self._path(call, "conversationId", "id"),
                    "messages": [],
                    "ephemeral": True,
                }
            return await portal.get_assistant_conversation_messages(
                auth, self._path(call, "conversationId", "id")
            )
        if operation == "student.list_appointments":
            advising = self.repository.advising
            if advising is None:
                return await portal.get_student_appointments(auth)
            return await advising.get_student_appointments(auth)
        if operation == "student.create_appointment":
            require_delegate_scope(auth, "appointments")
            advising = self.repository.advising
            if advising is None:
                return await portal.create_student_appointment(
                    auth, payload, self._key(key), call.request_id
                )
            return await advising.create_student_appointment(
                auth, payload, self._key(key), call.request_id
            )
        if operation == "student.get_appointment_availability":
            return await self._advising().get_student_availability(
                auth,
                appointment_type=str(call.query_params.get("type") or ""),
                staff_member_id=call.query_params.get("staffMemberId") or None,
                window_from=call.query_params.get("from") or None,
                window_to=call.query_params.get("to") or None,
            )
        if operation == "student.cancel_appointment":
            require_delegate_scope(auth, "appointments")
            return await self._advising().cancel_student_appointment(
                auth, self._path(call, "appointmentId", "id"), payload, call.request_id
            )
        if operation == "student.reschedule_appointment":
            require_delegate_scope(auth, "appointments")
            return await self._advising().reschedule_student_appointment(
                auth,
                self._path(call, "appointmentId", "id"),
                payload,
                self._key(key),
                call.request_id,
            )
        if operation == "student.get_advising":
            return await self._advising().get_student_advising(auth)
        if operation == "student.list_payments":
            return await portal.get_student_payments(auth)
        if operation == "student.create_deposit":
            if auth.is_delegate and not ({"payments", "enrollment"} & auth.delegate_scopes):
                require_delegate_scope(auth, "payments")
            return await portal.create_deposit_payment(
                auth, payload, self._key(key), call.request_id
            )
        if operation == "student.get_profile":
            return await portal.get_student_profile(auth)
        if operation == "student.update_profile":
            require_delegate_scope(auth, "profile")
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
            # The workspace carries the first page of open work plus the
            # board-wide counts; the task board queries further pages and
            # filters itself. "My" open items are read separately so the
            # personal action center does not depend on which page came back.
            action_center, personal_board = await asyncio.gather(
                staff.get_action_center(auth, DEFAULT_QUERY),
                staff.get_work_queue(
                    auth,
                    ActionCenterQuery(
                        assignee="me", sort="attention", limit=PERSONAL_QUEUE_PAGE_LIMIT
                    ),
                ),
            )
            (
                student,
                campus_life,
                inquiries,
                cohort,
                managed_content,
            ) = await asyncio.gather(
                staff.get_student_record(auth, auth.student_id),
                portal.get_campus_life(auth),
                portal.list_staff_help_requests(auth),
                staff.get_student_roster(auth),
                staff.get_managed_content(auth),
            )
            return compose_staff_workspace(
                auth,
                action_center=action_center,
                personal_items=cast(Sequence[Mapping[str, Any]], personal_board.get("items", [])),
                personal_page=cast(Mapping[str, Any], personal_board.get("page", {})),
                student=student,
                campus_life=campus_life,
                inquiries=cast(Sequence[Mapping[str, Any]], inquiries),
                cohort=cast(Sequence[Mapping[str, Any]], cohort),
                managed_content=managed_content,
                configurations=configurations,
                generated_at=self._timestamp(),
            )
        if operation == "staff.search_students":
            return await staff.search_students(
                auth,
                query=call.query_params.get("query") or None,
                student_id=call.query_params.get("studentId") or None,
                limit=_integer(call.query_params.get("limit")) or 50,
            )
        if operation == "staff.get_me":
            return await self._advising().get_staff_me(auth)
        if operation == "staff.get_caseload":
            return await self._advising().get_staff_caseload(
                auth, call.query_params.get("role") or None
            )
        if operation == "staff.get_appointments":
            return await self._advising().get_staff_appointments(
                auth,
                staff_member_id=call.query_params.get("staffMemberId") or None,
                window_from=call.query_params.get("from") or None,
                window_to=call.query_params.get("to") or None,
            )
        if operation == "staff.update_appointment":
            return await self._advising().update_staff_appointment(
                auth, self._path(call, "appointmentId", "id"), payload, call.request_id
            )
        if operation == "staff.get_morning_brew":
            brew = self.repository.morning_brew
            if brew is None:
                raise ApiError(
                    503,
                    "MORNING_BREW_UNAVAILABLE",
                    "The Morning Brew repository is not provisioned",
                )
            return await build_morning_brew(auth, brew)
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
            return await staff.get_action_center(auth, parse_action_center_query(call.query_params))
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
            return await self._ask_staff_edward(
                auth, payload, call.request_id, execution=call.assistant_execution
            )
        if operation == "staff.submit_edward_feedback":
            trace_id = str(payload.get("traceId") or "")
            return await self._feedback_repo().submit_staff_feedback(
                auth,
                assistant_message_id=self._path(call, "assistantMessageId", "id"),
                trace_id=trace_id,
                payload=payload,
                trace_payload=get_assistant_trace_recorder().get(trace_id),
            )
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
        if operation == "staff.document_review_options":
            return await staff.get_document_review_options(auth)
        if operation == "staff.review_document":
            return await staff.review_document(
                auth,
                self._path(call, "documentId", "id", "document_id"),
                payload,
                call.request_id,
                call.idempotency_key,
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

    def _ferpa(self) -> PostgresFerpaRepository:
        if self.repository.ferpa is None:
            raise ApiError(
                503,
                "FERPA_REPOSITORY_UNAVAILABLE",
                "FERPA authorization is not configured",
            )
        return self.repository.ferpa

    @staticmethod
    def _guard_delegate_onboarding_payload(auth: AuthContext, payload: Mapping[str, Any]) -> None:
        del payload
        if not auth.is_delegate:
            return
        raise ApiError(
            403,
            "DELEGATE_ROUTE_NOT_ALLOWED",
            "Parent and guardian onboarding access is view-only",
        )

    @staticmethod
    def _delegate_requirement_projection(auth: AuthContext, requirement: JsonDict) -> JsonDict:
        if not auth.is_delegate or requirement.get("interactionType") != "ferpa":
            return requirement
        projected = dict(requirement)
        projected["inputConfig"] = {}
        projected.pop("response", None)
        projected["ferpa"] = {
            "studentManaged": True,
            "delegateId": auth.actor_id,
            "relationship": auth.delegate_relationship,
            "scopes": sorted(auth.delegate_scopes),
            "completed": requirement.get("status") == "completed",
        }
        return projected

    async def _delegate_requirement_list(
        self,
        auth: AuthContext,
        pending: Awaitable[JsonDict],
    ) -> JsonDict:
        requirements = await pending
        if not auth.is_delegate:
            return requirements
        allowed_flows = (
            {"onboarding", "enrollment"} if "enrollment" in auth.delegate_scopes else set()
        )
        items = [
            self._delegate_requirement_projection(auth, dict(_mapping(item)))
            for item in _sequence(requirements.get("items"))
            if _mapping(item).get("flowKind") in allowed_flows
        ]
        return {"items": items, "total": len(items)}

    async def _delegate_dashboard(
        self,
        auth: AuthContext,
        pending: Awaitable[JsonDict],
    ) -> JsonDict:
        dashboard = await pending
        if not auth.is_delegate:
            return dashboard

        # Dashboard is a compound projection. Cross-page consumers may call it,
        # but each embedded section stays bound to the page scope that owns it.
        if "dashboard" in auth.delegate_scopes:
            dashboard["delegateRestricted"] = True
            return dashboard
        if not ({"enrollment", "payments"} & auth.delegate_scopes):
            dashboard.pop("offer", None)
        if "enrollment" not in auth.delegate_scopes:
            dashboard.pop("journey", None)
        dashboard["unreadMessageCount"] = 0
        dashboard["delegateRestricted"] = True
        return dashboard

    @staticmethod
    def _delegate_financials(auth: AuthContext, financials: JsonDict) -> JsonDict:
        if (
            not auth.is_delegate
            or "financials" in auth.delegate_scopes
            or "dashboard" not in auth.delegate_scopes
        ):
            return financials
        return {
            key: financials[key]
            for key in (
                "academicYear",
                "acceptedAidCents",
                "paymentsCents",
                "remainingBalanceCents",
                "requiredDocuments",
                "paymentPlans",
                "paymentSchedule",
                "generatedAt",
            )
            if key in financials
        }

    @staticmethod
    def _delegate_academics(auth: AuthContext, academics: JsonDict) -> JsonDict:
        if (
            not auth.is_delegate
            or {"classrooms", "enrollment"} & auth.delegate_scopes
            or "dashboard" not in auth.delegate_scopes
        ):
            return academics
        return {
            key: academics[key]
            for key in ("selectedProgram", "exemptionRecommendations", "generatedAt")
            if key in academics
        }

    @staticmethod
    def _delegate_campus_life(auth: AuthContext, campus_life: JsonDict) -> JsonDict:
        if (
            not auth.is_delegate
            or "campus_life" in auth.delegate_scopes
            or "dashboard" not in auth.delegate_scopes
        ):
            return campus_life
        return {key: campus_life[key] for key in ("events", "generatedAt") if key in campus_life}

    async def _delegate_onboarding(
        self,
        auth: AuthContext,
        pending: Awaitable[JsonDict],
    ) -> JsonDict:
        onboarding = await pending
        if not auth.is_delegate:
            return onboarding
        data = dict(_mapping(onboarding.get("data")))
        for field in (
            "familyPermissions",
            "signatureFullName",
            "signatureMethod",
            "signatureImageData",
            "signatureConsent",
            "signedDocumentIds",
        ):
            data.pop(field, None)
        onboarding["data"] = data
        if self.repository.ferpa is not None:
            authorization = await self.repository.ferpa.get_current(auth)
            onboarding["ferpa"] = {
                "status": (
                    str(authorization.get("status")) if authorization is not None else "unavailable"
                ),
                "studentManaged": True,
            }
        return onboarding

    def _delegate_link_token(
        self,
        auth: AuthContext,
        authorization_id: str,
        delegate_id: str,
        idempotency_key: str,
    ) -> str:
        material = ":".join(
            (
                auth.tenant_id,
                auth.student_id,
                auth.actor_id,
                authorization_id,
                delegate_id,
                idempotency_key,
            )
        ).encode("utf-8")
        digest = hmac.new(self.ferpa_link_secret.encode("utf-8"), material, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _feedback_repo(self) -> PostgresEdwardFeedbackRepository:
        if self.repository.edward_feedback is None:
            raise ApiError(
                503,
                "EDWARD_FEEDBACK_UNAVAILABLE",
                "Edward feedback is not configured on this host",
            )
        return self.repository.edward_feedback

    async def _record_assistant_trace(self, trace: AssistantTurnTrace) -> None:
        """Record once, then durably store the same sanitized trace payload."""

        try:
            payload = trace.to_dict()
        except Exception:  # pragma: no cover - same defensive boundary as the recorder
            get_assistant_trace_recorder().record(trace)
            return
        get_assistant_trace_recorder().record_payload(payload)
        if self.repository.edward_feedback is None:
            return
        try:
            await self.repository.edward_feedback.save_trace(payload)
        except Exception:
            # Observability persistence must never turn a valid Edward answer
            # into a failed chat request. Feedback submission can still create
            # a minimal trace from the immutable transcript pair later.
            LOGGER.exception(
                "assistant durable trace persistence failed trace_id=%s", trace.trace_id
            )

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

    async def _student_advising_primitive(self, auth: AuthContext) -> JsonDict:
        advising = self.repository.advising
        if advising is None:
            return {"primaryAdviser": None, "advisers": [], "gaps": []}
        return await advising.get_student_advising(auth)

    def _advising(self) -> PostgresAdvisingRepository:
        advising = self.repository.advising
        if advising is None:
            raise ApiError(
                503,
                "ADVISING_UNAVAILABLE",
                "The advising repository is not provisioned",
            )
        return advising

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

    async def _authorize_requirement_appointment(
        self,
        auth: AuthContext,
        requirement_id: str,
        *,
        mutation: bool,
    ) -> JsonDict:
        context = await self._ferpa().requirement_context(auth, requirement_id)
        if context.get("interactionType") != "scheduling":
            raise ApiError(
                409,
                "REQUIREMENT_SCHEDULING_REQUIRED",
                "Appointments can be managed here only for a scheduling requirement",
            )
        if mutation and context.get("status") not in {
            "ready",
            "help_requested",
            "in_progress",
            "rejected",
        }:
            raise ApiError(
                409,
                "STUDENT_REQUIREMENT_NOT_ACTIONABLE",
                "This scheduling requirement cannot accept an appointment now",
            )
        if auth.is_delegate and mutation and context.get("flowKind") == "onboarding":
            raise ApiError(
                403,
                "DELEGATE_ROUTE_NOT_ALLOWED",
                "Parent and guardian onboarding access is view-only",
            )
        return context

    async def _authorize_requirement_profile(
        self,
        auth: AuthContext,
        requirement_id: str,
    ) -> JsonDict:
        context = await self._ferpa().requirement_context(auth, requirement_id)
        if (
            context.get("flowKind") != "enrollment"
            or context.get("code") != "profile_verification"
            or context.get("interactionType") != "form"
        ):
            raise ApiError(
                409,
                "REQUIREMENT_PROFILE_UPDATE_REQUIRED",
                "Profile changes here require the active enrollment profile task",
            )
        if context.get("status") not in {
            "ready",
            "help_requested",
            "in_progress",
            "rejected",
        }:
            raise ApiError(
                409,
                "STUDENT_REQUIREMENT_NOT_ACTIONABLE",
                "This profile requirement cannot accept changes now",
            )
        return context

    async def _authorize_document_action(
        self,
        auth: AuthContext,
        document_id: str,
        *,
        mutation: bool = False,
    ) -> JsonDict | None:
        if self.repository.ferpa is None:
            if auth.is_delegate:
                self._ferpa()
            return None
        context = await self._ferpa().document_requirement_context(auth, document_id)
        if context is None:
            if auth.is_delegate:
                require_delegate_scope(auth, "documents")
            return None
        if context.get("interactionType") == "ferpa":
            raise ApiError(
                403 if auth.is_delegate else 409,
                (
                    "FERPA_STUDENT_CONTROL_REQUIRED"
                    if auth.is_delegate
                    else "REQUIREMENT_SPECIALIZED_SUBMISSION_REQUIRED"
                ),
                "FERPA evidence is managed only through the canonical e-signature flow",
            )
        if auth.is_delegate:
            if "documents" in auth.delegate_scopes:
                return context
            if mutation and context.get("flowKind") == "onboarding":
                require_delegate_scope(auth, "documents")
                return context
            require_delegate_scope(auth, "enrollment")
        return context

    async def _delegate_document_list(self, auth: AuthContext, documents: JsonDict) -> JsonDict:
        if not auth.is_delegate or "documents" in auth.delegate_scopes:
            return documents
        visible: list[JsonDict] = []
        for raw in _sequence(documents.get("items")):
            document = dict(_mapping(raw))
            requirement_id = document.get("requirementId")
            if not isinstance(requirement_id, str):
                continue
            document_id = document.get("id")
            if not isinstance(document_id, str):
                continue
            context = await self._ferpa().document_requirement_context(auth, document_id)
            if context is None or context.get("interactionType") == "ferpa":
                continue
            if "enrollment" in auth.delegate_scopes:
                visible.append(document)
        return {**documents, "items": visible, "total": len(visible)}

    async def _delegate_document_read(
        self,
        auth: AuthContext,
        pending: Awaitable[JsonDict],
    ) -> JsonDict:
        return await self._delegate_document_list(auth, await pending)

    async def _upload_document(self, auth: AuthContext, call: ServiceCall) -> JsonDict:
        upload = call.upload
        if upload is None:
            raise BadRequestError("FILE_REQUIRED", "Choose a document file to upload")
        if auth.is_delegate:
            if upload.requirement_id is not None:
                context = await self._ferpa().requirement_context(
                    auth,
                    upload.requirement_id,
                    enforce_delegate_scope=False,
                )
                if context.get("interactionType") == "ferpa":
                    raise ApiError(
                        403,
                        "FERPA_STUDENT_CONTROL_REQUIRED",
                        "FERPA accepts only the student's canonical e-signature",
                    )
                if "documents" in auth.delegate_scopes:
                    pass
                elif context.get("flowKind") == "onboarding":
                    require_delegate_scope(auth, "documents")
                else:
                    require_delegate_scope(auth, "enrollment")
            else:
                require_delegate_scope(auth, "documents")
        elif upload.requirement_id is not None and self.repository.ferpa is not None:
            interaction_type = await self._ferpa().requirement_flow(auth, upload.requirement_id)
            if interaction_type == "ferpa":
                raise ApiError(
                    409,
                    "REQUIREMENT_SPECIALIZED_SUBMISSION_REQUIRED",
                    "FERPA accepts only the canonical e-signature flow",
                )
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
        reference: JsonDict | None
        try:
            reference = await self.repository.portal.get_student_document_content_reference(
                auth, document_id
            )
        except NotFoundError:
            reference = await self._ferpa().get_document_reference(auth, document_id)
            if reference is None:
                raise
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
        primitives: dict[str, Callable[..., Any]] = {}

        def granted(*scopes: str) -> bool:
            return not auth.is_delegate or bool(set(scopes) & auth.delegate_scopes)

        if granted("profile", "enrollment"):
            primitives["profile"] = lambda: portal.get_student_profile(auth)
        if granted("enrollment"):
            primitives["requirements"] = lambda: self._delegate_requirement_list(
                auth, portal.get_student_requirements(auth)
            )
        if granted("documents", "enrollment"):
            primitives["documents"] = lambda: self._delegate_document_read(
                auth, portal.get_student_documents(auth)
            )
        if granted("payments", "enrollment"):
            primitives["payments"] = lambda: portal.get_student_payments(auth)
        if granted("financials"):
            primitives["financials"] = lambda: portal.get_student_financials(auth)
        if granted("dashboard", "enrollment", "payments"):
            primitives["dashboard"] = lambda: self._delegate_dashboard(
                auth, platform.get_student_dashboard(auth)
            )
        if granted("enrollment"):
            primitives["onboarding"] = lambda: self._delegate_onboarding(
                auth, portal.get_student_onboarding(auth)
            )
        if granted("enrollment"):
            primitives["housing_plan"] = lambda: portal.get_student_housing_plan(auth)
        if granted("appointments", "enrollment"):
            primitives["appointments"] = lambda: (
                self.repository.advising.get_student_appointments(auth)
                if self.repository.advising is not None
                else portal.get_student_appointments(auth)
            )
            if self.repository.advising is not None:
                advising_repo = self.repository.advising
                primitives["advising"] = lambda: advising_repo.get_student_advising(auth)
        if granted("help"):
            primitives["help"] = lambda: portal.get_student_help(auth)
        if granted("classrooms", "enrollment"):
            primitives["academics"] = lambda: portal.get_student_academics(auth)
        if granted("campus_life"):
            primitives["campus_life"] = lambda: portal.get_campus_life(auth)
        if granted("messages"):
            primitives["messages"] = lambda: portal.get_student_messages(auth)
        knowledge = self.repository.knowledge
        if knowledge is not None and granted("help", "enrollment"):
            primitives["institution_knowledge"] = lambda query: self._student_knowledge_search(
                auth, knowledge, query
            )
        university = self.repository.university
        if university is not None and university.is_enabled(auth):
            primitives["university_record"] = lambda **kwargs: university.record(auth, **kwargs)
            primitives["institution_knowledge"] = lambda query: university.policies(auth, query)
        return AssistantToolHost(cast(Any, primitives))

    @staticmethod
    async def _student_knowledge_search(
        auth: AuthContext, knowledge: PostgresInstitutionKnowledgeRepository, query: str
    ) -> JsonDict:
        """Approved knowledge for the signed-in student: student audience only,
        applicability computed from that student's own record."""

        facets = (
            await knowledge.facets_for_student(auth.tenant_id, auth.student_id)
            if auth.student_id
            else None
        )
        return await knowledge.search(
            auth.tenant_id, SearchQuery(text=query, audience="student"), facets=facets
        )

    @staticmethod
    def _action_conversation_response(message: str, state: Mapping[str, Any]) -> JsonDict | None:
        """Answer a question about what this conversation already changed.

        These three questions — did it happen, undo it, forget it — used to
        fall into the read pipeline, which answered about the student instead.
        They are answerable, and only from server state: receipts prove what
        was committed, pending intents prove what was not, and the difference
        is exactly what the person is asking about.
        """

        receipts = [item for item in _sequence(state.get("receipts")) if isinstance(item, Mapping)]
        pending = [item for item in _sequence(state.get("pending")) if isinstance(item, Mapping)]
        if action_responses.is_undo_request(message):
            return action_responses.undo_answer(receipts)
        if action_responses.is_abandon_request(message) and pending:
            return action_responses.abandon_answer(pending)
        if action_responses.is_recall_question(message):
            return action_responses.recall_answer(receipts, pending=pending)
        return None

    async def _single_caseload_name(self, auth: AuthContext, message: str) -> str | None:
        """A unique on-caseload student named by first name alone, or None.

        Bounded: at most three capitalised tokens are searched, only exact
        first/preferred-name matches count, and only when exactly one student
        across every token is on the asker's caseload. Anything else keeps
        the clarifying question. The gateway still re-resolves and
        re-authorizes whatever this returns.
        """

        skip = {
            "The",
            "This",
            "That",
            "These",
            "Those",
            "Please",
            "Can",
            "Could",
            "Would",
            "Will",
            "What",
            "When",
            "Where",
            "Which",
            "Who",
            "How",
            "Why",
            "Does",
            "Did",
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
            "Create",
            "Make",
            "Queue",
            "Add",
            "Set",
            "Put",
            "Log",
            "Flag",
            "Chase",
            "Edward",
            "Action",
            "Center",
        }
        tokens = [
            token for token in re.findall(r"\b([A-Z][a-z]{2,20})\b", message) if token not in skip
        ][:3]
        if not tokens:
            return None
        matches: set[str] = set()
        repo = self._staff_assistant_repo()
        for token in tokens:
            try:
                found = await repo.search_students(auth, query=token, limit=25)
            except Exception:
                return None
            items = list(found.get("items", []))
            if len(items) >= 25:
                # A full page means the roster holds more of this name than
                # the page shows; a unique-looking match could be an artifact
                # of truncation. Keep the question.
                return None
            for item in items:
                if not item.get("onCaseload"):
                    continue
                first = str(item.get("name", "")).split(" ")[0].lower()
                preferred = str(item.get("preferredName", "")).lower()
                if token.lower() in {first, preferred}:
                    matches.add(str(item["id"]))
            if len(matches) > 1:
                return None
        return next(iter(matches)) if len(matches) == 1 else None

    async def _recognize_with_model(
        self,
        message: str,
        *,
        actor: str,
        capabilities: Sequence[str] = (),
        request_id: str,
        tenant_id: str,
        allows_model_calls: bool = True,
    ) -> tuple[SemanticActionRequest | None, JsonDict | None]:
        """Tier 1: bounded model recognition, for turns the patterns missed.

        Reached only after the deterministic tier and the conversation
        continuation have both found nothing, so an amendment to a proposal
        already on the table is never re-derived from a sentence that names
        neither the action nor the target. What it returns is a *request*,
        which the gateway then resolves, authorizes and previews exactly as it
        does a pattern-matched one. A model failure degrades to "not
        recognized", never to an error.
        """

        # `deterministic` mode promises a turn with no provider call at all,
        # and that promise is what makes the mode useful as a control. The
        # recognizer is a model call like any other, so it is gated by the
        # same switch rather than quietly exempt from it.
        recognizer = getattr(self.ai, "recognize_edward_action", None)
        if recognizer is None or not allows_model_calls:
            return None, None
        usage: JsonDict | None = None

        async def complete(**kwargs: Any) -> Mapping[str, Any] | None:
            nonlocal usage
            parsed = cast(Mapping[str, Any] | None, await recognizer(**kwargs))
            if isinstance(parsed, Mapping):
                usage = {
                    "operation": "action_recognizer",
                    "provider": parsed.get("provider"),
                    "model": parsed.get("model"),
                    "usage": parsed.get("usage"),
                }
            return parsed

        started = time.perf_counter()
        recognized = await recognize_with_model(
            message,
            actor=cast(Any, actor),
            capabilities=capabilities,
            complete=complete,
            tenant_id=tenant_id,
            request_id=request_id,
        )
        if usage is not None:
            usage["durationMs"] = round((time.perf_counter() - started) * 1_000)
        return recognized, usage

    async def _ask_edward(
        self,
        auth: AuthContext,
        payload: Mapping[str, Any],
        request_id: str,
        *,
        execution: ResolvedAssistantExecutionMode = DEFAULT_ASSISTANT_EXECUTION,
    ) -> JsonDict:
        turn_started = time.perf_counter()
        message = str(payload.get("message", ""))
        page_path, page_label = _assistant_page_context(payload.get("pageContext"))
        conversation_id = payload.get("conversationId")
        client_message_id = payload.get("clientMessageId")
        # Recognition happens before the conversation is created because a
        # recognized action needs a durable conversation to bind the intent to,
        # and everything else must not create one.
        injection_reason = None if auth.is_delegate else untrusted_action_framing(message)
        semantic_action: SemanticActionRequest | None = None
        recognizer_usage: JsonDict | None = None
        student_conversation_state: JsonDict = {"receipts": [], "pending": []}
        # Recognition order is deliberate: patterns, then this conversation's
        # own state, then the model. A correction to a proposal already on the
        # table must not be re-derived by a tier that cannot see the table.
        if not auth.is_delegate and injection_reason is None:
            semantic_action = parse_student_action(message)
            if semantic_action is None and conversation_id is not None:
                student_conversation_state = await self._edward_actions().conversation_actions(
                    auth, str(conversation_id)
                )
                semantic_action, _ = _continued_action(
                    message, student_conversation_state, actor="student"
                )
            if semantic_action is None:
                semantic_action, recognizer_usage = await self._recognize_with_model(
                    message,
                    actor="student",
                    request_id=request_id,
                    tenant_id=auth.tenant_id,
                    allows_model_calls=execution.mode.allows_model_calls,
                )
            elif (
                semantic_action.action == "student.preferences.update"
                and semantic_action.source == "pattern"
                and semantic_action.fields
                and preference_fields_incomplete(message, semantic_action.fields)
            ):
                # Tier 0 parsed part of a multi-field change; tier 1 fills the
                # fields the patterns could not anchor ("change it to Lucy").
                modelled, recognizer_usage = await self._recognize_with_model(
                    message,
                    actor="student",
                    request_id=request_id,
                    tenant_id=auth.tenant_id,
                    allows_model_calls=execution.mode.allows_model_calls,
                )
                # Only genuine values: a model echoing the request ("my name")
                # must not become the preview.
                if modelled is not None and modelled.action == semantic_action.action:
                    modelled_fields = {
                        name: value
                        for name, value in modelled.fields.items()
                        if not (
                            isinstance(value, str)
                            and re.fullmatch(
                                r"(?:my |the |your )?(?:name|pronouns|number)", value.strip(), re.I
                            )
                        )
                    }
                    modelled = SemanticActionRequest(
                        modelled.action,
                        modelled_fields,
                        modelled.confidence,
                        source=modelled.source,
                    )
                    semantic_action = SemanticActionRequest(
                        semantic_action.action,
                        {**modelled.fields, **semantic_action.fields},
                        semantic_action.confidence,
                        source="pattern+model",
                    )
        trace_action_requested = semantic_action.action if semantic_action is not None else None
        trace_recognition_source = _recognition_source(semantic_action, recognizer_usage)
        if semantic_action is not None and conversation_id is None:
            created = await self.repository.portal.create_assistant_conversation(
                auth, page_path=page_path, page_label=page_label
            )
            conversation_id = created["id"]
        persist = not auth.is_delegate and (
            conversation_id is not None or client_message_id is not None
        )
        trace = AssistantTurnTrace(
            trace_id=request_id,
            tenant_id=auth.tenant_id,
            student_id=auth.student_id,
            conversation_id=str(conversation_id) if conversation_id else None,
            input_mode=str(payload.get("inputMode") or "text"),
            user_message=message,
            execution_mode=execution.mode.value,
            ignored_execution_mode_request=execution.ignored_request,
            action_requested=trace_action_requested,
            action_recognition_source=trace_recognition_source,
        )
        trace.started_from(turn_started)
        _record_recognizer_call(trace, recognizer_usage, semantic_action)

        if not auth.is_delegate and isinstance(client_message_id, str) and client_message_id:
            replay = await self.repository.portal.find_assistant_exchange_by_client_id(
                auth, client_message_id
            )
            if replay is not None:
                trace.path = "idempotent_replay"
                trace.final_message = str(replay.get("message") or "")
                await self._record_assistant_trace(trace)
                return replay

        guarded = guarded_response(message)
        conversation_state = student_conversation_state
        if (
            not conversation_state["receipts"]
            and not conversation_state["pending"]
            and conversation_id is not None
            and not auth.is_delegate
        ):
            conversation_state = await self._edward_actions().conversation_actions(
                auth, str(conversation_id)
            )
        action_recall = self._action_conversation_response(message, conversation_state)
        if guarded is not None:
            response = dict(guarded)
            trace.path = "pre_pipeline_safety_gate"
            trace.response_source = "deterministic"
            trace.provider = str(response.get("provider") or "guided")
        elif injection_reason is not None and not (
            injection_reason == "quoted_content"
            and re.search(
                r"\bis (?:that|this|it)(?: (?:statement|claim|advice|message|note))? "
                r"(?:reliable|true|correct|accurate)\s*\?",
                message,
                re.I,
            )
        ):
            response = action_responses.injection_refusal(injection_reason)
            trace.path = "action_untrusted_framing"
            trace.response_source = "deterministic"
            trace.failure_codes.append(f"untrusted_{injection_reason}")
        elif action_recall is not None:
            response = action_recall
            trace.path = "action_conversation_recall"
            trace.response_source = "deterministic"
        elif (
            semantic_action is not None
            and (
                clarify := action_responses.clarification(
                    semantic_action.action, semantic_action.fields, message=message
                )
            )
            is not None
        ):
            # Understood, one value short. A question is the whole answer; a
            # refusal here would throw away everything the student just said.
            response = clarify
            trace.path = "action_clarification"
            trace.response_source = "deterministic"
            trace.action_proposed = semantic_action.action
            trace.action_policy_result = "clarify"
        elif semantic_action is not None:
            assert conversation_id is not None
            try:
                intent = await self._edward_actions().propose_student(
                    auth,
                    semantic_action,
                    conversation_id=str(conversation_id),
                    trace_id=request_id,
                    page_path=page_path,
                )
                action_message = action_responses.proposal_message(
                    semantic_action.action, _mapping(intent.get("preview"))
                )
                # A compound turn — "What's my preferred name right now? change
                # it to Naddy" — owes the question an answer alongside the
                # card. The read half runs deterministically (no model calls,
                # ~2 ms) on only the question sentences, because the full
                # message would classify as the very write the card already
                # carries. A read failure never blocks the proposal.
                read_question = (
                    question_sentences(message) if has_question_sentence(message) else ""
                )
                if read_question:
                    try:
                        # The read half gets the same model hooks as a plain
                        # read turn (hybrid planning): a question the regex
                        # classifier cannot place goes to the read loop rather
                        # than to the broad safe fallback.
                        read_pipeline = AssistantPipeline(
                            self._assistant_host(auth),
                            model_composer=model_hook(
                                execution.mode, self._assistant_composer(auth, request_id)
                            ),
                            model_planner=model_hook(
                                execution.mode, self._assistant_planner(auth, request_id)
                            ),
                            read_loop_step=model_hook(
                                execution.mode, self._read_loop_step(auth, request_id, "student")
                            ),
                            read_planner="hybrid",
                        )
                        read_result = await read_pipeline.execute(
                            message=read_question,
                            history=await self.repository.portal.get_recent_assistant_history(
                                auth, str(conversation_id)
                            ),
                            page_path=page_path,
                            page_label=page_label,
                            trace=None,
                        )
                        read_prose = str(read_result.message or "").strip()
                        if read_prose:
                            action_message = f"{read_prose}\n\n{action_message}"
                    except Exception:  # noqa: S110 - the card must still land
                        pass
                response = {
                    "message": action_message,
                    "blocks": [{"type": "text", "text": action_message}],
                    "provider": "guided",
                    "model": None,
                    "usage": None,
                    "suggestedActions": [],
                    "contextReceipts": [],
                    "widgets": [],
                    "actionIntents": [intent],
                    "actionReceipts": [],
                }
                trace.path = "action_proposal"
                trace.response_source = "deterministic"
                trace.classification = {
                    "requestType": "action_request",
                    "action": semantic_action.action,
                    "confidence": semantic_action.confidence,
                }
                trace.action_proposed = semantic_action.action
                trace.action_policy_result = "allowed"
                trace.action_intent_id = str(intent["id"])
                trace.action_confirmation_mode = str(intent["confirmationMode"])
                trace.action_authorization_capability = intent.get("authorizationCapability")
                preview = _mapping(intent.get("preview"))
                cohort = _mapping(preview.get("cohort"))
                trace.action_blast_radius = int(cohort.get("count") or 1)
                trace.action_provenance = [
                    dict(item)
                    for item in _sequence(intent.get("provenance"))
                    if isinstance(item, Mapping)
                ]
            except ApiError as error:
                response = action_responses.denial(error.code, error.message)
                trace.path = "action_proposal_denied"
                trace.response_source = "deterministic"
                trace.failure_codes.append(error.code)
                trace.action_policy_result = "denied"
                trace.action_denial_reason = error.code
        elif (boundary := action_responses.boundary("student", message)) is not None:
            # Understood, and genuinely out of reach. Naming the real limit and
            # the real route beats both a stock refusal and a read answer to a
            # question the student did not ask.
            response = boundary
            university = self.repository.university
            if (
                university is not None
                and university.is_enabled(auth)
                and re.search(r"\bholds?\b", message, re.I)
            ):
                account = await university.record(auth, "account")
                balance = sum(
                    row["amount_cents"] for row in account["ledger"] if row["term_id"] == "2026FA"
                )
                active = [row for row in account["holds"] if not row["released_at"]]
                pending = [row for row in account["payments"] if row["status"] == "pending"]
                answer = (
                    "Hold release is not an Edward action in this version. "
                    f"Your posted Fall 2026 balance is ${balance / 100:,.2f}; "
                    f"there are {len(active)} active holds and {len(pending)} pending payments. "
                    "Pending payments do not reduce that posted balance. Student Accounts "
                    "must verify settlement and record a financial hold release separately; "
                    "other holds require their owning office. No hold was changed."
                )
                response = {
                    **response,
                    "message": answer,
                    "blocks": [{"type": "text", "text": answer, "fallbackText": answer}],
                    "contextReceipts": [{"source": "university"}],
                }
                trace.add_tool_call(
                    tool="getUniversityAccount",
                    status="available",
                    duration_ms=None,
                    result=account,
                    round_name="action-boundary",
                )
            trace.path = "action_boundary"
            trace.response_source = "deterministic"
        elif action_responses.is_capability_question(message):
            response = action_responses.capability_answer(
                "student",
                (),
                reads=(
                    "I can read your enrollment record — your checklist, documents, "
                    "deadlines, financial aid, housing, registration and account."
                ),
            )
            trace.path = "action_capability_answer"
            trace.response_source = "deterministic"
        else:
            history: Sequence[Mapping[str, Any]]
            if conversation_id is not None and not auth.is_delegate:
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
                model_composer=model_hook(
                    execution.mode, self._assistant_composer(auth, request_id)
                ),
                model_planner=model_hook(execution.mode, self._assistant_planner(auth, request_id)),
                read_loop_step=model_hook(
                    execution.mode, self._read_loop_step(auth, request_id, "student")
                ),
                **self._read_loop_settings(execution, auth),
            )
            result = await pipeline.execute(
                message=message,
                history=history,
                page_path=page_path,
                page_label=page_label,
                trace=trace,
            )
            await record_assistant_usage(
                self.repository.portal.engine,
                tenant_id=auth.tenant_id,
                feature="student_assistant",
                actor_type="student",
                actor_id=None,
                student_id=auth.student_id,
                provider=result.provider,
                model=result.model,
                usage=result.usage,
                request_id=request_id,
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
            if auth.is_delegate:
                response["suggestedActions"] = self._delegate_edward_actions(
                    auth, _sequence(response.get("suggestedActions"))
                )
            response["contextReceipts"] = result.context_receipts

        # A student who says they are stuck has asked a question and, without
        # saying so, asked for help. Answer the question, then say the route
        # exists — it is the highest-value thing Edward can add to a read.
        support_offer = (
            action_responses.offers_support_route(message)
            if not auth.is_delegate and not response.get("actionIntents")
            else None
        )
        if support_offer and support_offer not in str(response.get("message") or ""):
            response = dict(response)
            response["message"] = f"{response.get('message', '').rstrip()} {support_offer}".strip()
            blocks = list(_sequence(response.get("blocks")))
            response["blocks"] = [
                *blocks,
                {"type": "text", "text": support_offer, "fallbackText": support_offer},
            ]

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
                    "actionIntents": response.get("actionIntents"),
                    "actionReceipts": response.get("actionReceipts"),
                },
                request_id=request_id,
            )
            response.update(stored)
            trace.conversation_id = str(stored.get("conversationId") or "") or trace.conversation_id
            trace.user_message_id = str(stored.get("userMessageId") or "") or None
            trace.assistant_message_id = str(stored.get("assistantMessageId") or "") or None
        response["requestId"] = request_id
        trace.final_message = str(response.get("message") or "")
        trace.response_blocks = list(response.get("blocks") or [])
        trace.add_stage(
            "presentation",
            0,
            blockTypes=[b.get("type") for b in trace.response_blocks],
            source=trace.response_source,
            construction="semantic_projection"
            if any(b.get("type") == "answer" for b in trace.response_blocks)
            else "existing_response_blocks",
        )
        await self._record_assistant_trace(trace)
        return response

    async def _edward_action_authority(
        self, auth: AuthContext, message: str
    ) -> EdwardActionAuthority:
        """Server-side authority over action widgets, loaded only when an
        action intent appears in the message."""

        wants_deposit = bool(_EDWARD_DEPOSIT_ACTION.search(message))
        wants_document = bool(_EDWARD_DOCUMENT_ACTION.search(message))
        wants_appointment = bool(_EDWARD_APPOINTMENT_ACTION.search(message))
        deposit_scoped = not auth.is_delegate or bool(
            {"payments", "enrollment"} & auth.delegate_scopes
        )
        document_scoped = not auth.is_delegate or "documents" in auth.delegate_scopes
        appointment_scoped = not auth.is_delegate or "appointments" in auth.delegate_scopes
        offer_id = ""
        deposit_amount = 0
        deposit_paid = False
        if wants_deposit and deposit_scoped:
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
        if wants_document and document_scoped:
            document_upload_category = (
                "transcript" if "transcript" in message.lower() else "financial_aid"
            )
        appointment_type = None
        if wants_appointment and appointment_scoped:
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

    @staticmethod
    def _delegate_edward_actions(
        auth: AuthContext,
        actions: Sequence[object],
    ) -> list[JsonDict]:
        required: tuple[tuple[str, frozenset[str]], ...] = (
            ("/dashboard", frozenset({"dashboard"})),
            ("/enrollment", frozenset({"enrollment"})),
            ("/financials", frozenset({"financials"})),
            ("/classrooms", frozenset({"classrooms", "enrollment"})),
            ("/campus-life", frozenset({"campus_life"})),
            ("/documents", frozenset({"documents"})),
            ("/messages", frozenset({"messages"})),
            ("/appointments", frozenset({"appointments"})),
            ("/payments", frozenset({"payments"})),
            ("/profile", frozenset({"profile", "enrollment"})),
            ("/help", frozenset({"help"})),
            ("/edward", frozenset({"edward"})),
        )
        visible: list[JsonDict] = []
        for raw in actions:
            action = _mapping(raw)
            href = str(action.get("href") or "")
            scopes = next(
                (
                    allowed
                    for prefix, allowed in required
                    if href == prefix or href.startswith(f"{prefix}/")
                ),
                frozenset(),
            )
            if scopes & auth.delegate_scopes:
                visible.append(dict(action))
        return visible

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

    def _read_loop_step(self, auth: AuthContext, request_id: str, actor: str) -> Any:
        """The model step for the read loop, bound to this turn's identity."""

        step = getattr(self.ai, "run_read_loop_step", None)
        if step is None:
            return None

        async def run(*, messages: Any, schema: Any) -> Mapping[str, Any] | None:
            return cast(
                Mapping[str, Any] | None,
                await step(
                    messages=messages,
                    schema=schema,
                    actor=actor,
                    tenant_id=auth.tenant_id,
                    subject_id=auth.student_id if actor == "student" else auth.actor_id,
                    request_id=request_id,
                ),
            )

        return run

    def _read_loop_settings(
        self, execution: ResolvedAssistantExecutionMode, auth: AuthContext | None = None
    ) -> dict[str, Any]:
        """Planner mode and round budget for one turn: the Lab header wins,
        else the deployment default from the gateway settings."""

        planner = execution.read_planner
        university = self.repository.university
        if auth is not None and university is not None and university.is_enabled(auth):
            return {
                "read_planner": planner.value if planner is not None else "model",
                "read_loop_max_rounds": 4,
                "now": lambda: datetime.fromisoformat(
                    university.clocks[auth.tenant_id].replace("Z", "+00:00")
                ),
            }
        default = getattr(self.ai, "default_read_planner", "deterministic")
        rounds = getattr(self.ai, "read_loop_max_rounds", 3)
        return {
            "read_planner": planner.value if planner is not None else str(default),
            "read_loop_max_rounds": int(rounds),
        }

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

        async def student_by_external_ref(external_ref: str) -> Mapping[str, Any]:
            found = await assistant.get_student_by_external_ref(auth, external_ref)
            return found or {}

        return StaffAssistantToolHost(
            {
                "search_students": lambda **kwargs: assistant.search_students(auth, **kwargs),
                "search_students_fuzzy": (
                    lambda query, limit=5: assistant.search_students_fuzzy(
                        auth, query=query, limit=limit
                    )
                ),
                "student_by_external_ref": student_by_external_ref,
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
                "student_appointments": lambda student_id: (
                    self.repository.advising.get_student_appointments(student_auth(student_id))
                    if self.repository.advising is not None
                    else portal.get_student_appointments(student_auth(student_id))
                ),
                "student_advising": lambda student_id: self._student_advising_primitive(
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
                "work_queue": lambda query=None: staff.get_work_queue(
                    auth, parse_action_center_query(query)
                ),
                # Counts over the same query vocabulary, never a page.
                "work_queue_summary": lambda query=None, group_by=None, limit=12: (
                    staff.summarize_work_queue(
                        auth, parse_action_center_query(query), group_by=group_by, limit=limit
                    )
                ),
                "work_item_by_key": lambda key: staff.find_work_item_by_key(auth, key),
                # The Staff Portal's own briefing composer — Edward reads the
                # same briefing the staff member can already see, rather than
                # recomputing a second, divergent one.
                "morning_brew": lambda: self._morning_brew_for_assistant(auth),
                "work_item_detail": lambda work_item_id: staff.get_work_item_detail(
                    auth, work_item_id, ensure_document_work_items=False
                ),
                "inquiries": inquiries,
                "inquiry_thread": lambda inquiry_id: portal.get_staff_inquiry_thread(
                    auth, inquiry_id
                ),
                "guidance": lambda: assistant.get_staff_guidance(auth),
                **self._staff_knowledge_primitives(auth),
                "action_rules": lambda: staff.get_action_rules(auth),
                "mailbox_messages": (
                    lambda query="", limit=10: assistant.get_authorized_mailbox_messages(
                        auth, query=query, limit=limit
                    )
                ),
                **self._staff_operations_primitives(auth),
                **self._university_staff_primitives(auth),
            },
            staff_member_id=auth.actor_id,
        )

    def _staff_knowledge_primitives(self, auth: AuthContext) -> dict[str, Any]:
        """Approved knowledge for staff: every audience, applicability against
        the resolved student when the turn is about one. Absent when the tenant
        has no corpus so the tool reports honest unavailability."""

        knowledge = self.repository.knowledge
        if knowledge is None:
            return {}

        async def viewer_terms() -> tuple[str, ...]:
            if not auth.actor_id:
                return ()
            try:
                component = await knowledge.viewer_component(auth.tenant_id, str(auth.actor_id))
            except Exception:
                return ()
            return (component,) if component else ()

        async def read(
            query: str,
            student_id: str | None = None,
            audience: str | None = None,
            kind: str | None = None,
            office: str | None = None,
            limit: int | None = None,
        ) -> JsonDict:
            facets = (
                await knowledge.facets_for_student(auth.tenant_id, student_id)
                if student_id
                else None
            )
            kinds: tuple[str, ...] = (kind,) if kind else ()
            # Staff always read every audience: a student-facing rule and its
            # internal procedure belong together, and a model narrowing the
            # audience would hide the procedure. Fewer than three documents is
            # too few to say "nothing else applies".
            del audience
            return await knowledge.search(
                auth.tenant_id,
                SearchQuery(
                    text=query,
                    audience="staff",
                    limit=max(3, int(limit or 4)),
                    kinds=kinds,
                    office=office,
                    viewer_terms=await viewer_terms(),
                ),
                facets=facets,
            )

        return {"institution_knowledge": read}

    def _staff_operations_primitives(self, auth: AuthContext) -> dict[str, Any]:
        """Bounded staff directory / inquiry / department reads.

        Queue reads are not here: they go through the staff repository's
        Action Center query layer (``work_queue`` / ``work_queue_summary``).
        Present only when the operations repository is provisioned; the tool
        layer reports a missing primitive as an honest unavailability.
        """

        operations = self.repository.staff_operations
        advising = self.repository.advising
        if operations is None:
            return {}
        primitives: dict[str, Any] = {
            "staff_profile": lambda staff_member_id: operations.staff_profile(
                auth, staff_member_id
            ),
            "search_staff": lambda **kwargs: operations.search_staff(auth, **kwargs),
            "list_components": lambda: operations.list_components(auth),
            "inquiry_summary": lambda filters, group_by=None, limit=10: (
                operations.summarize_inquiries(
                    auth, filters=filters, group_by=group_by, limit=limit
                )
            ),
            "inquiry_search": lambda filters, limit=10, sort="oldest": operations.search_inquiries(
                auth, filters=filters, limit=limit, sort=sort
            ),
            "component_summary": lambda component: operations.component_summary(auth, component),
        }
        if advising is not None:
            bound_advising = advising

            async def staff_team(staff_member_id: str) -> Mapping[str, Any] | None:
                return await bound_advising.team_for(auth, staff_member_id)

            async def staff_caseload(
                staff_member_id: str, role: str | None = None
            ) -> Mapping[str, Any] | None:
                return await bound_advising.caseload_for(auth, staff_member_id, role=role)

            async def staff_appointments(
                staff_member_id: str,
                window_from: str | None = None,
                window_to: str | None = None,
            ) -> Mapping[str, Any] | None:
                return await bound_advising.appointments_for(
                    auth, staff_member_id, window_from=window_from, window_to=window_to
                )

            primitives.update(
                {
                    "staff_team": staff_team,
                    "staff_caseload": staff_caseload,
                    "staff_appointments": staff_appointments,
                }
            )
        return primitives

    def _university_staff_primitives(self, auth: AuthContext) -> dict[str, Any]:
        university = self.repository.university
        if university is None or not university.is_enabled(auth):
            return {}
        return {
            "university_record": lambda student_id, **kwargs: university.record(
                replace(auth, student_id=student_id), **kwargs
            ),
            "university_operations": lambda: university.operations(auth),
            "university_work_board": lambda: self._university_work_board(auth),
            "university_cohort": lambda: university.cohort(auth),
            "university_policies": lambda query, student_id=None, **kwargs: university.policies(
                replace(auth, student_id=student_id or ""), query, **kwargs
            ),
        }

    async def _university_work_board(self, auth: AuthContext) -> JsonDict:
        from .work_board_repository import WorkBoardProjection

        return await WorkBoardProjection(self.repository.staff).read(auth)

    async def _morning_brew_for_assistant(self, auth: AuthContext) -> Mapping[str, Any]:
        """The canonical Morning Brew, or an honest unavailability."""

        brew = self.repository.morning_brew
        if brew is None:
            raise NotFoundError(
                "MORNING_BREW_UNAVAILABLE",
                "The morning briefing is not available on this deployment",
            )
        return await build_morning_brew(auth, brew)

    async def _ask_staff_edward(
        self,
        auth: AuthContext,
        payload: Mapping[str, Any],
        request_id: str,
        *,
        execution: ResolvedAssistantExecutionMode = DEFAULT_ASSISTANT_EXECUTION,
    ) -> JsonDict:
        if auth.actor_type != "staff":
            raise UnauthorizedError("Staff authentication is required")
        turn_started = time.perf_counter()
        repo = self._staff_assistant_repo()
        message = str(payload.get("message", ""))
        conversation_id = payload.get("conversationId")
        client_message_id = payload.get("clientMessageId")
        injection_reason = untrusted_action_framing(message)
        staff_capabilities = await self._edward_actions().capabilities_for(auth)
        semantic_action: SemanticActionRequest | None = None
        recognizer_usage: JsonDict | None = None
        staff_conversation_state: JsonDict = {"receipts": [], "pending": []}
        staff_inherited_student_id: str | None = None
        clarify_forced_work_item_key: str | None = None
        clarify_pick_student = False
        # Patterns, then this conversation's own state, then the answer to a
        # question Edward itself just asked, then the model — the same order
        # the student path uses, and for the same reason.
        if injection_reason is None:
            semantic_action = parse_staff_action(message)
            if semantic_action is None and conversation_id is not None:
                staff_conversation_state = await self._edward_actions().conversation_actions(
                    auth, str(conversation_id)
                )
                semantic_action, staff_inherited_student_id = _continued_action(
                    message, staff_conversation_state, actor="staff"
                )
            if semantic_action is None and conversation_id is not None:
                # "What is it waiting on?" → "the registrar hasn't sent the
                # file". The reply to Edward's own clarifying question names
                # neither an action nor a target; both are in the question it
                # answers. Without this the reply fell into the read plane and
                # the exchange dead-ended one step from done.
                recent_for_clarify = await repo.get_recent_history(auth, str(conversation_id))
                continuation = _clarify_loop_continuation(
                    message, list(recent_for_clarify.get("history", []))
                )
                if continuation is not None:
                    semantic_action, clarify_forced_work_item_key, clarify_pick_student = (
                        continuation
                    )
            if semantic_action is None:
                semantic_action, recognizer_usage = await self._recognize_with_model(
                    message,
                    actor="staff",
                    capabilities=staff_capabilities,
                    request_id=request_id,
                    tenant_id=auth.tenant_id,
                    allows_model_calls=execution.mode.allows_model_calls,
                )
        if semantic_action is not None and conversation_id is None:
            created = await repo.create_conversation(auth)
            conversation_id = created["id"]
        persist = conversation_id is not None or client_message_id is not None
        trace = AssistantTurnTrace(
            trace_id=request_id,
            tenant_id=auth.tenant_id,
            assistant_kind="staff",
            actor_type="staff",
            staff_member_id=auth.actor_id,
            conversation_id=str(conversation_id) if conversation_id else None,
            user_message=message,
            execution_mode=execution.mode.value,
            ignored_execution_mode_request=execution.ignored_request,
            action_requested=semantic_action.action if semantic_action else None,
            action_recognition_source=_recognition_source(semantic_action, recognizer_usage),
        )
        trace.started_from(turn_started)
        _record_recognizer_call(trace, recognizer_usage, semantic_action)

        if isinstance(client_message_id, str) and client_message_id:
            replay = await repo.find_exchange_by_client_id(auth, client_message_id)
            if replay is not None:
                trace.path = "idempotent_replay"
                trace.final_message = str(replay.get("message") or "")
                await self._record_assistant_trace(trace)
                return replay

        guarded = guarded_staff_response(message)
        conversation_state = staff_conversation_state
        if (
            not conversation_state["receipts"]
            and not conversation_state["pending"]
            and conversation_id is not None
        ):
            conversation_state = await self._edward_actions().conversation_actions(
                auth, str(conversation_id)
            )
        inherited_student_id = staff_inherited_student_id
        action_recall = self._action_conversation_response(message, conversation_state)
        if (
            guarded is None
            and injection_reason is not None
            and not (
                injection_reason == "quoted_content"
                and re.search(
                    r"\bis (?:that|this|it)(?: (?:statement|claim|advice|message|note))? "
                    r"(?:reliable|true|correct|accurate)\s*\?",
                    message,
                    re.I,
                )
            )
        ):
            guarded = action_responses.injection_refusal(injection_reason)
            trace.failure_codes.append(f"untrusted_{injection_reason}")
        elif guarded is None and action_recall is not None:
            guarded = action_recall
        elif guarded is None and semantic_action is None:
            # Understood but out of reach, or a question about what Edward can
            # do at all. Both used to answer "I'm read-only in this version",
            # which stopped being true the moment the write plane shipped.
            boundary = action_responses.boundary("staff", message)
            if boundary is not None:
                guarded = boundary
            elif action_responses.is_capability_question(message):
                guarded = action_responses.capability_answer(
                    "staff",
                    staff_capabilities,
                    reads=(
                        "I can read your students' enrollment state, your work queue, "
                        "communications, inquiries and attention signals."
                    ),
                )
        resolved_student_id: str | None = None
        referent_action = "keep"
        active_student_id: str | None = None
        active_cohort_filter: Mapping[str, Any] | None = None
        prior_cohort_filter: Mapping[str, Any] | None = None
        if guarded is not None:
            response = dict(guarded)
            kind = _mapping(response.get("actionResponse")).get("kind")
            trace.path = f"action_{kind}" if kind else "pre_pipeline_safety_gate"
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
                raw_prior_cohort = recent.get("activeCohortFilter")
                prior_cohort_filter = (
                    raw_prior_cohort if isinstance(raw_prior_cohort, Mapping) else None
                )
                trace.history_source = "server"
            pipeline = StaffAssistantPipeline(
                self._staff_assistant_host(auth),
                # A recognised write request is resolved by the deterministic
                # classifier and Action Gateway. Running model planning here
                # would add cost and latency to a result that is discarded,
                # while also allowing untrusted prose to influence reads used
                # for action target binding. Normal read/reason turns retain
                # the optional model-assisted path.
                model_composer=(
                    None
                    if semantic_action is not None
                    else model_hook(
                        execution.mode, self._staff_assistant_composer(auth, request_id)
                    )
                ),
                model_planner=(
                    None
                    if semantic_action is not None
                    else model_hook(execution.mode, self._staff_assistant_planner(auth, request_id))
                ),
                read_loop_step=(
                    None
                    if semantic_action is not None
                    else model_hook(execution.mode, self._read_loop_step(auth, request_id, "staff"))
                ),
                **self._read_loop_settings(execution, auth),
            )
            result = await pipeline.execute(
                message=message,
                history=history,
                context_student_id=context_student_id,
                trace=trace,
                action_is_supported=semantic_action is not None,
            )
            resolved_student_id = result.resolved_student_id
            referent_action = result.referent_action
            active_student_id = result.next_referent_student_id
            active_cohort_filter = (
                result.classification.cohort_filter if result.classification is not None else None
            )
            trace.student_id = resolved_student_id
            await record_assistant_usage(
                self.repository.portal.engine,
                tenant_id=auth.tenant_id,
                feature="staff_assistant",
                actor_type="staff",
                actor_id=auth.actor_id,
                student_id=resolved_student_id,
                provider=result.provider,
                model=result.model,
                usage=result.usage,
                request_id=request_id,
            )
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
            if semantic_action is not None:
                action_filter = cohort_filter_from_text(message) or prior_cohort_filter
                normalized = normalize_staff_request(message, history=history)
                draft = (
                    await repo.get_recent_email_draft(auth, str(conversation_id))
                    if semantic_action.action == "communications.email.prepare"
                    and conversation_id is not None
                    else None
                )
                explicit_student = bool(
                    normalized.candidate_student_name
                    or normalized.candidate_student_id
                    or (normalized.reference_token and normalized.work_item_key is None)
                )
                allows_inheritance = _action_may_inherit_student(
                    message,
                    semantic_action.action,
                    has_server_draft=draft is not None,
                )
                # The read pipeline can resolve a carried referent for prose,
                # but action target binding is stricter: current-turn entity
                # language or an explicit anaphor is required.
                action_student_id = (
                    resolved_student_id if explicit_student or allows_inheritance else None
                )
                if action_student_id is None and inherited_student_id is not None:
                    # An amendment to a pending proposal keeps that proposal's
                    # target: "actually make it urgent" is about the follow-up
                    # already on the table, and re-deriving the student from a
                    # sentence that names nobody would lose it.
                    action_student_id = inherited_student_id
                if (
                    action_student_id is None
                    and context_student_id is not None
                    and allows_inheritance
                ):
                    action_student_id = str(context_student_id)
                if action_student_id is None and clarify_pick_student and resolved_student_id:
                    # The turn answers Edward's own "which student?" — the
                    # pipeline resolved whoever it named or picked, and that
                    # resolution is the whole point of the exchange.
                    action_student_id = resolved_student_id
                if (
                    action_student_id is None
                    and semantic_action.action
                    in {
                        "operations.follow_up.create",
                        "communications.email.prepare",
                    }
                    # Only when NO full name was in play. When one was and it
                    # is ambiguous or out of scope, the question/denial it
                    # produced is the answer — a first-name fallback here once
                    # bound "Fiona Larkspur" to Fiona Calderwood, which is the
                    # exact silent-wrong-target failure the gateway exists to
                    # prevent.
                    and normalized.candidate_student_name is None
                    and normalized.candidate_student_id is None
                    and normalized.reference_token is None
                    and not _pipeline_already_asked(result)
                ):
                    # "Yusuf's been quiet — put something on my plate": one
                    # first name, one caseload. A bounded roster search that
                    # accepts only a UNIQUE on-caseload first-name match; two
                    # Milos still get the question.
                    action_student_id = await self._single_caseload_name(auth, message)
                work_item_key = normalized.work_item_key
                if work_item_key is None and re.search(
                    r"\b(?:that|this|it|previous)\b", message, re.I
                ):
                    work_item_key = _recent_work_item_key(history)
                    if work_item_key is None and conversation_id is not None:
                        work_item_key = await self._edward_actions().recent_work_item_key(
                            auth, str(conversation_id)
                        )
                if work_item_key is None and clarify_forced_work_item_key is not None:
                    # The answer to "what is it waiting on?" rarely repeats the
                    # key; the question already bound it.
                    work_item_key = clarify_forced_work_item_key
                # Capability is checked before anything is clarified: an
                # adviser without the bulk permission cannot be helped by
                # describing a cohort, and asking them to is a worse answer
                # than saying which permission is missing.
                required_capability = CAPABILITY_BY_ACTION.get(semantic_action.action)
                capability_missing = bool(
                    required_capability and required_capability not in staff_capabilities
                )
                clarify = (
                    None
                    if capability_missing
                    else action_responses.clarification(
                        semantic_action.action,
                        semantic_action.fields,
                        message=message,
                        has_student=(
                            action_student_id is not None
                            or semantic_action.action
                            not in {"operations.follow_up.create", "communications.email.prepare"}
                        ),
                        has_work_item=(
                            work_item_key is not None
                            or semantic_action.action != "operations.work_item.update"
                        ),
                        has_cohort=(
                            bool(action_filter)
                            or semantic_action.action != "operations.cohort.create_follow_ups"
                        ),
                        has_draft=(
                            draft is not None
                            or semantic_action.action != "communications.email.prepare"
                        ),
                    )
                )
                if (
                    semantic_action.action
                    in {"operations.follow_up.create", "communications.email.prepare"}
                    and (listed := action_responses.several_students_named(message)) is not None
                ):
                    response.update(listed)
                    trace.path = "action_clarification"
                    trace.response_source = "deterministic"
                    trace.action_proposed = semantic_action.action
                    trace.action_policy_result = "clarify"
                    semantic_action = None
                elif clarify is not None and _pipeline_already_asked(result):
                    # The read pipeline already asked a better question than a
                    # generic one — an ambiguous name deserves the candidate
                    # list it produced, not "which student?" with no options.
                    trace.path = "action_clarification"
                    trace.response_source = "deterministic"
                    trace.action_proposed = semantic_action.action
                    trace.action_policy_result = "clarify"
                    semantic_action = None
                elif clarify is not None:
                    # One question instead of a policy error the staff member
                    # cannot act on. "Create a follow-up" with no student is a
                    # complete intent missing one noun.
                    response.update(clarify)
                    trace.path = "action_clarification"
                    trace.response_source = "deterministic"
                    trace.action_proposed = semantic_action.action
                    trace.action_policy_result = "clarify"
                    semantic_action = None
                try:
                    if semantic_action is None:
                        raise _ActionClarified
                    intent = await self._edward_actions().propose_staff(
                        auth,
                        semantic_action,
                        conversation_id=str(conversation_id),
                        trace_id=request_id,
                        resolved_student_id=action_student_id,
                        cohort_filter=action_filter,
                        draft=draft,
                        work_item_key=work_item_key,
                    )
                    action_message = (
                        "I prepared a server-verified preview. Nothing has happened yet — "
                        "review the target, scope, and exact effect below."
                    )
                    # A compound turn owes its question an answer beside the
                    # card. The deterministic pipeline already ran for target
                    # resolution; its prose is the read half, and it is kept
                    # only when the turn actually asked something — a bare
                    # command stays a bare card.
                    read_prose = str(result.message or "").strip()
                    if read_prose and has_question_sentence(message):
                        action_message = f"{read_prose}\n\n{action_message}"
                    response.update(
                        {
                            "message": action_message,
                            "blocks": [{"type": "text", "text": action_message}],
                            "provider": "guided",
                            "model": None,
                            "usage": None,
                            "actionIntents": [intent],
                            "actionReceipts": [],
                        }
                    )
                    trace.path = "action_proposal"
                    trace.response_source = "deterministic"
                    trace.classification = {
                        "requestType": "action_request",
                        "action": semantic_action.action,
                        "confidence": semantic_action.confidence,
                    }
                    trace.action_proposed = semantic_action.action
                    trace.action_policy_result = "allowed"
                    trace.action_intent_id = str(intent["id"])
                    trace.action_confirmation_mode = str(intent["confirmationMode"])
                    trace.action_authorization_capability = intent.get("authorizationCapability")
                    preview = _mapping(intent.get("preview"))
                    cohort = _mapping(preview.get("cohort"))
                    trace.action_blast_radius = int(cohort.get("count") or 1)
                    trace.action_provenance = [
                        dict(item)
                        for item in _sequence(intent.get("provenance"))
                        if isinstance(item, Mapping)
                    ]
                except _ActionClarified:
                    pass
                except ApiError as error:
                    denial_payload = action_responses.denial(
                        error.code,
                        error.message,
                        student_name=result.resolved_student_name,
                    )
                    # "Who's got AST-00001, and can you mark it done?" — the
                    # scope denial is right and the read half is still owed.
                    # The pipeline's own answer (which names the owner) rides
                    # in front of the denial when the turn asked a question.
                    read_prose = str(result.message or "").strip()
                    if read_prose and has_question_sentence(message):
                        merged_text = f"{read_prose}\n\n{denial_payload['message']}"
                        denial_payload = dict(denial_payload)
                        denial_payload["message"] = merged_text
                        denial_payload["blocks"] = [
                            {"type": "text", "text": merged_text, "fallbackText": merged_text}
                        ]
                    response.update(denial_payload)
                    trace.path = "action_proposal_denied"
                    trace.response_source = "deterministic"
                    trace.failure_codes.append(error.code)
                    trace.action_policy_result = "denied"
                    trace.action_denial_reason = error.code

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
                    "actionIntents": response.get("actionIntents"),
                    "actionReceipts": response.get("actionReceipts"),
                },
                referenced_student_id=resolved_student_id,
                request_id=request_id,
                referent_action=referent_action,
                active_student_id=active_student_id,
                active_cohort_filter=active_cohort_filter,
                cohort_action=(
                    "set"
                    if active_cohort_filter
                    else "clear"
                    if resolved_student_id is not None
                    else "keep"
                ),
            )
            response.update(stored)
            trace.conversation_id = str(stored.get("conversationId") or "") or trace.conversation_id
            trace.user_message_id = str(stored.get("userMessageId") or "") or None
            trace.assistant_message_id = str(stored.get("assistantMessageId") or "") or None
        response["requestId"] = request_id
        trace.final_message = str(response.get("message") or "")
        trace.response_blocks = list(response.get("blocks") or [])
        trace.add_stage(
            "presentation",
            0,
            blockTypes=[b.get("type") for b in trace.response_blocks],
            source=trace.response_source,
            construction="semantic_projection"
            if any(b.get("type") == "answer" for b in trace.response_blocks)
            else "existing_response_blocks",
        )
        await self._record_assistant_trace(trace)
        return response

    def _staff_assistant_planner(
        self, auth: AuthContext, request_id: str
    ) -> StaffModelPlanner | None:
        planner = getattr(self.ai, "plan_staff_tool_reads", None)
        if planner is None:
            return None

        async def plan(
            *,
            message: str,
            student_resolved: bool = False,
            context: Mapping[str, Any] | None = None,
        ) -> Mapping[str, Any] | None:
            return cast(
                Mapping[str, Any] | None,
                await planner(
                    message=message,
                    allowed_request_types=STAFF_REQUEST_TYPES,
                    available_tools=STAFF_TOOL_DESCRIPTIONS,
                    student_resolved=student_resolved,
                    context=context,
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
