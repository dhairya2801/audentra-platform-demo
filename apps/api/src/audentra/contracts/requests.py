"""Strict Pydantic v2 models for the existing NestJS request surface."""

import unicodedata
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
)

from .base import StrictRequest

ShortText = Annotated[StrictStr, StringConstraints(min_length=1, max_length=120)]
NameText = Annotated[StrictStr, StringConstraints(min_length=1, max_length=160)]
LongText = Annotated[StrictStr, StringConstraints(min_length=1, max_length=500)]
OperationalCode = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$"),
]
StaffCommunicationChannel = Literal["email", "sms", "voice", "portal"]
PhoneE164 = Annotated[StrictStr, StringConstraints(pattern=r"^\+[1-9][0-9]{7,14}$")]
SessionToken = Annotated[
    StrictStr,
    StringConstraints(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]

OnboardingStep = Literal[
    "offer",
    "about_you",
    "housing",
    "campus_life",
    "emergency_contacts",
    "family_permissions",
    "review_and_sign",
    "deposit",
]
HousingPreference = Literal["on_campus", "off_campus", "commuting", "undecided", "family"]
HousingResidence = Literal["aster_residence_hall", "aster_apartments", "student_village"]
DocumentCategory = Literal[
    "identity", "residency", "transcript", "financial_aid", "health", "consent", "other"
]


def _ensure_email(value: str) -> str:
    if len(value) > 254 or value.count("@") != 1:
        raise ValueError("must be a valid email address")
    local, domain = value.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        raise ValueError("must be a valid email address")
    return value


def _ensure_iso8601(value: str) -> str:
    if "T" not in value:
        raise ValueError("must be an ISO 8601 date-time")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("must be an ISO 8601 date-time") from error
    return value


class EmptyBody(StrictRequest):
    pass


def _normalize_email(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a valid email address")
    return _ensure_email(unicodedata.normalize("NFKC", value).strip().lower())


class StartGuidedOnboardingRequest(StrictRequest):
    completed_onboarding: StrictBool = False


class StudentSignUpRequest(StrictRequest):
    email: StrictStr
    phone: PhoneE164
    password: Annotated[StrictStr, StringConstraints(min_length=12, max_length=128)]

    _normalize_email_value = field_validator("email", mode="before")(_normalize_email)

    @field_validator("password")
    @classmethod
    def _strong_password(cls, value: str) -> str:
        if not any(character.isalpha() for character in value) or not any(
            character.isdigit() for character in value
        ):
            raise ValueError("must include at least one letter and one number")
        return value


class StudentSignInRequest(StrictRequest):
    email: StrictStr
    password: Annotated[StrictStr, StringConstraints(min_length=1, max_length=128)]

    _normalize_email_value = field_validator("email", mode="before")(_normalize_email)


class StaffSignInRequest(StudentSignInRequest):
    pass


class StaffSignUpRequest(StrictRequest):
    email: StrictStr
    password: Annotated[StrictStr, StringConstraints(min_length=12, max_length=128)]
    institution_access_code: Annotated[
        StrictStr,
        StringConstraints(min_length=16, max_length=256),
    ]

    _normalize_email_value = field_validator("email", mode="before")(_normalize_email)

    @field_validator("password")
    @classmethod
    def _strong_password(cls, value: str) -> str:
        if not any(character.isalpha() for character in value) or not any(
            character.isdigit() for character in value
        ):
            raise ValueError("must include at least one letter and one number")
        return value


ActivityEventName = Literal[
    "ui.portal_session_started.v1",
    "ui.dashboard_viewed.v1",
    "ui.admission_offer_viewed.v1",
    "ui.admission_decision_started.v1",
    "ui.enrollment_started.v1",
    "ui.enrollment_step_viewed.v1",
    "ui.portal_section_viewed.v1",
    "ui.enrollment_task_viewed.v1",
    "ui.enrollment_task_abandoned.v1",
    "ui.financial_aid_viewed.v1",
    "ui.course_catalog_searched.v1",
    "ui.course_viewed.v1",
    "ui.exemption_reviewed.v1",
    "ui.campus_event_viewed.v1",
    "ui.club_viewed.v1",
    "ui.edward_context_receipts_received.v1",
    "ui.edward_tool_invoked.v1",
    "ui.edward_action_widget_viewed.v1",
    "ui.edward_action_completed.v1",
    "ui.help_opened.v1",
]


class ActivityEventRequest(StrictRequest):
    event_id: UUID
    event_name: ActivityEventName
    occurred_at: StrictStr
    session_id: SessionToken
    page_instance_id: SessionToken
    correlation_id: SessionToken | None = None
    properties: dict[StrictStr, StrictStr | StrictInt | StrictFloat | StrictBool | None]

    _validate_occurred_at = field_validator("occurred_at")(_ensure_iso8601)


class ActivityEventBatchRequest(StrictRequest):
    events: list[ActivityEventRequest] = Field(min_length=1, max_length=100)


class RegisterCampusEventRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)


class OnboardingEmergencyContactRequest(StrictRequest):
    full_name: NameText
    relationship: Literal["parent", "guardian", "partner", "sibling", "relative", "friend", "other"]
    mobile_phone: PhoneE164


class OnboardingFamilyPermissionRequest(StrictRequest):
    full_name: NameText
    relationship: Literal["parent", "guardian", "partner", "sponsor", "other"]
    email: StrictStr
    scopes: list[Annotated[StrictStr, StringConstraints(max_length=80)]] = Field(max_length=7)
    purpose: Literal["education_and_expenses", "academic_planning", "billing_and_aid", "other"]
    expires: Literal["end_first_year", "end_enrollment", "registrar_date"]

    _validate_email = field_validator("email")(_ensure_email)


class StudentOnboardingDataRequest(StrictRequest):
    first_name: ShortText | None = None
    last_name: ShortText | None = None
    preferred_name: ShortText | None = None
    personal_email: StrictStr | None = None
    mobile_phone: PhoneE164 | None = None
    citizenship_status: (
        Literal["us_citizen", "permanent_resident", "eligible_noncitizen", "international"] | None
    ) = None
    communication_preference: Literal["email", "sms"] | None = None
    residency_status: Literal["domestic", "international"] | None = None
    residency_verification_path: (
        Literal["home_address_review", "document_upload", "advisor_review"] | None
    ) = None
    street_address: Annotated[StrictStr, StringConstraints(min_length=1, max_length=180)] | None = (
        None
    )
    address_line2: Annotated[StrictStr, StringConstraints(max_length=180)] | None = None
    city: ShortText | None = None
    state_or_province: ShortText | None = None
    postal_code: Annotated[StrictStr, StringConstraints(min_length=1, max_length=32)] | None = None
    country: Annotated[StrictStr, StringConstraints(min_length=2, max_length=120)] | None = None
    support_needs: list[Annotated[StrictStr, StringConstraints(max_length=80)]] | None = Field(
        default=None, max_length=12
    )
    accommodation_interest: Literal["not_now", "housing", "academic", "both"] | None = None
    housing_preference: HousingPreference | None = None
    housing_residence_option: HousingResidence | None = None
    housing_residence_preferences: list[HousingResidence] | None = Field(default=None, max_length=3)
    insurance_interest: Literal["not_now", "learn_more", "tuition", "housing", "both"] | None = None
    housing_room_type: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    bathroom_preference: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    roommate_matching: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    known_roommate_name: (
        Annotated[StrictStr, StringConstraints(min_length=2, max_length=160)] | None
    ) = None
    known_roommate_email: StrictStr | None = None
    sleep_schedule: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    study_habits: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    room_noise: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    cleanliness: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    guest_preference: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    temperature_preference: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    smoke_vape_compatibility: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    substance_free_housing: StrictBool | None = None
    gender_inclusive_housing: StrictBool | None = None
    accessible_housing_information: StrictBool | None = None
    living_learning_communities: (
        list[Annotated[StrictStr, StringConstraints(max_length=80)]] | None
    ) = Field(default=None, max_length=6)
    off_campus_status: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    off_campus_resources: list[Annotated[StrictStr, StringConstraints(max_length=80)]] | None = (
        Field(default=None, max_length=7)
    )
    commute_mode: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    commute_duration: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    commuter_resources: list[Annotated[StrictStr, StringConstraints(max_length=80)]] | None = Field(
        default=None, max_length=8
    )
    campus_interests: list[Annotated[StrictStr, StringConstraints(max_length=80)]] | None = Field(
        default=None, max_length=12
    )
    social_comfort: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    first_month_goals: list[Annotated[StrictStr, StringConstraints(max_length=80)]] | None = Field(
        default=None, max_length=7
    )
    emergency_contacts: list[OnboardingEmergencyContactRequest] | None = Field(
        default=None, max_length=4
    )
    family_permissions: list[OnboardingFamilyPermissionRequest] | None = Field(
        default=None, max_length=4
    )
    signature_full_name: (
        Annotated[StrictStr, StringConstraints(min_length=2, max_length=240)] | None
    ) = None
    signature_method: Literal["typed", "drawn"] | None = None
    signature_image_data: Annotated[StrictStr, StringConstraints(max_length=100_000)] | None = None
    signature_consent: StrictBool | None = None
    signed_document_ids: list[Annotated[StrictStr, StringConstraints(max_length=80)]] | None = (
        Field(default=None, max_length=12)
    )
    deposit_choice: Literal["pay_now", "pay_later", "waiver_or_deferral"] | None = None
    custom_fields: (
        dict[
            Annotated[
                StrictStr,
                StringConstraints(
                    min_length=2,
                    max_length=80,
                    pattern=r"^[a-z][a-z0-9_]*$",
                ),
            ],
            StrictStr | StrictBool | list[StrictStr],
        ]
        | None
    ) = Field(default=None, max_length=40)

    @field_validator("personal_email", "known_roommate_email")
    @classmethod
    def validate_optional_emails(cls, value: str | None) -> str | None:
        return None if value is None else _ensure_email(value)

    @field_validator("custom_fields")
    @classmethod
    def validate_custom_fields(
        cls,
        value: dict[str, str | bool | list[str]] | None,
    ) -> dict[str, str | bool | list[str]] | None:
        if value is None:
            return None
        for answer in value.values():
            if isinstance(answer, str) and len(answer) > 2_000:
                raise ValueError("custom field answers must be no longer than 2,000 characters")
            if isinstance(answer, list) and (
                len(answer) > 25 or any(len(item) > 200 for item in answer)
            ):
                raise ValueError("custom field selections exceed the supported limits")
        return value


class UpdateStudentOnboardingRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    current_step: OnboardingStep
    data: StudentOnboardingDataRequest
    skip: StrictBool | None = None


class CompleteStudentOnboardingRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)


class UpdateStudentHousingPlanRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    preference: HousingPreference
    residence_option: HousingResidence | None = None
    residence_preferences: list[HousingResidence] | None = Field(default=None, max_length=3)
    room_type: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    bathroom_preference: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    roommate_matching: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    known_roommate_name: (
        Annotated[StrictStr, StringConstraints(min_length=2, max_length=160)] | None
    ) = None
    known_roommate_email: StrictStr | None = None
    sleep_schedule: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    study_habits: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    room_noise: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    cleanliness: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    guest_preference: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    temperature_preference: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    smoke_vape_compatibility: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    substance_free_housing: StrictBool | None = None
    gender_inclusive_housing: StrictBool | None = None
    accessible_housing_information: StrictBool | None = None
    living_learning_communities: (
        list[Annotated[StrictStr, StringConstraints(max_length=80)]] | None
    ) = Field(default=None, max_length=6)

    @field_validator("known_roommate_email")
    @classmethod
    def validate_known_roommate_email(cls, value: str | None) -> str | None:
        return None if value is None else _ensure_email(value)


class DecideStudentExperienceUpdateRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    action: Literal["handle_now", "later"]


class StudentExperienceUpdateVersionRequest(StrictRequest):
    id: UUID
    expected_version: StrictInt = Field(ge=1)


class DeferStudentExperienceUpdatesRequest(StrictRequest):
    """Atomically defer every update presented in one student portal visit."""

    updates: list[StudentExperienceUpdateVersionRequest] = Field(min_length=1, max_length=20)


class SubmitStudentRequirementResponseRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    response: dict[str, object]


class CreateStudentDocumentRequest(StrictRequest):
    file_name: Annotated[StrictStr, StringConstraints(min_length=1, max_length=255)]
    mime_type: Literal["application/pdf", "image/jpeg", "image/png"]
    size_bytes: StrictInt = Field(ge=1, le=10_485_760)
    category: DocumentCategory

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if not value.strip() or any(char in value for char in ("/", "\\")):
            raise ValueError("must be a safe file name")
        if any(ord(char) < 32 for char in value):
            raise ValueError("must be a safe file name")
        return value


class ConfirmStudentDocumentExtractionRequest(StrictRequest):
    accepted_field_keys: list[
        Annotated[StrictStr, StringConstraints(min_length=1, max_length=80)]
    ] = Field(max_length=24)


class SelectPaymentPlanRequest(StrictRequest):
    plan_id: UUID


class EdwardChatMessageRequest(StrictRequest):
    role: Literal["user", "assistant"]
    content: Annotated[StrictStr, StringConstraints(min_length=1, max_length=1_200)]


class AssistantPageContextRequest(StrictRequest):
    path: Annotated[StrictStr, StringConstraints(max_length=240)]
    label: Annotated[StrictStr, StringConstraints(max_length=240)]


class AskEdwardRequest(StrictRequest):
    message: Annotated[StrictStr, StringConstraints(min_length=1, max_length=2_000)]
    page_context: (
        Annotated[StrictStr, StringConstraints(max_length=240)] | AssistantPageContextRequest
    )
    history: list[EdwardChatMessageRequest] | None = Field(default=None, max_length=8)
    conversation_id: UUID | None = None
    client_message_id: (
        Annotated[
            StrictStr,
            StringConstraints(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
        ]
        | None
    ) = None
    input_mode: Literal["text", "voice"] | None = None


class CreateAssistantConversationRequest(StrictRequest):
    page_context: AssistantPageContextRequest


class CreateStudentAppointmentRequest(StrictRequest):
    type: Literal["admissions_counseling", "financial_aid", "enrollment_support"]
    starts_at: StrictStr
    notes: Annotated[StrictStr, StringConstraints(max_length=500)] | None = None

    _validate_starts_at = field_validator("starts_at")(_ensure_iso8601)


class CreateStudentHelpRequest(StrictRequest):
    topic_code: Literal["getting_started", "documents", "payments", "support"]
    message: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]
    requirement_id: UUID | None = None


class CreateStudentInquiryMessageRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    body: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    ]


class CreateDepositPaymentRequest(StrictRequest):
    offer_id: UUID


class UpdateStudentProfileRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    preferred_name: ShortText | None = None
    pronouns: Annotated[StrictStr, StringConstraints(max_length=80)] | None = None
    mobile_phone: (
        Annotated[StrictStr, StringConstraints(pattern=r"^\+?[0-9 ()-]{7,32}$")] | None
    ) = None
    communication_preference: Literal["email", "sms"] | None = None


class UpdateStaffWorkItemRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    status: (
        Literal[
            "todo",
            "in_progress",
            "follow_up_required",
            "blocked",
            "done",
            "cancelled",
        ]
        | None
    ) = None
    assignee_id: UUID | None = None
    escalated: StrictBool | None = None
    selected_channel: StaffCommunicationChannel | None = None
    follow_up_at: datetime | None = None
    blocker_code: OperationalCode | None = None
    blocker_detail: (
        Annotated[StrictStr, StringConstraints(min_length=1, max_length=1000)] | None
    ) = None
    blocker_review_at: datetime | None = None
    outcome_code: OperationalCode | None = None
    resolution_code: OperationalCode | None = None
    next_step: Annotated[StrictStr, StringConstraints(min_length=1, max_length=1000)] | None = None
    terminal_reason: OperationalCode | None = None
    note: Annotated[StrictStr, StringConstraints(min_length=1, max_length=500)] | None = None


class CreateStaffWorkItemRequest(StrictRequest):
    student_id: UUID
    flow_kind: Literal["enrollment", "onboarding"]
    requirement_id: UUID | None = None
    title: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=240),
    ]
    description: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    ]
    component: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
    ]
    assignee_id: UUID | None = None
    priority: Literal["low", "medium", "high", "urgent"]
    status: Literal["todo", "in_progress", "follow_up_required", "blocked"] = "todo"
    due_at: datetime | None = None
    action_type: (
        Literal[
            "enrollment_follow_up",
            "onboarding_assistance",
            "document_review",
            "missing_information",
            "external_verification",
            "deadline_risk",
            "staff_decision",
            "communication_response",
            "blocked_dependency",
        ]
        | None
    ) = None

    @field_validator("due_at")
    @classmethod
    def _due_at_must_include_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("must include a timezone offset")
        return value


class CreateStaffWorkCommentRequest(StrictRequest):
    expected_work_item_version: StrictInt = Field(ge=1)
    body: Annotated[StrictStr, StringConstraints(min_length=1, max_length=2000)]
    mention_ids: list[UUID] = Field(default_factory=list, max_length=20)


class StartStaffInteractionRequest(StrictRequest):
    expected_work_item_version: StrictInt = Field(ge=1)
    channel: StaffCommunicationChannel
    objective: Annotated[StrictStr, StringConstraints(min_length=1, max_length=1000)]


class RecordStaffCommunicationRequest(StrictRequest):
    expected_interaction_version: StrictInt = Field(ge=1)
    channel: StaffCommunicationChannel
    direction: Literal["inbound", "outbound"]
    subject: Annotated[StrictStr, StringConstraints(min_length=1, max_length=500)] | None = None
    body: Annotated[StrictStr, StringConstraints(min_length=1, max_length=12000)]
    occurred_at: datetime | None = None


class CompleteStaffInteractionRequest(StrictRequest):
    expected_interaction_version: StrictInt = Field(ge=1)
    expected_work_item_version: StrictInt = Field(ge=1)
    outcome_code: OperationalCode
    resolution_code: OperationalCode
    next_step: Annotated[StrictStr, StringConstraints(min_length=1, max_length=1000)] | None = None
    follow_up_at: datetime | None = None


class RequestStaffAiRefreshRequest(StrictRequest):
    expected_work_item_version: StrictInt = Field(ge=1)
    scope: Literal["interaction", "student_summary", "task_insight", "both"]
    interaction_id: UUID | None = None


class RetryStaffCallTranscriptionRequest(StrictRequest):
    expected_recording_version: StrictInt = Field(ge=1)


StaffActionRuleSignal = Literal["requirement_due", "student_inactive"]
StaffWorkPriority = Literal["low", "medium", "high", "urgent"]
StaffWorkActionType = Literal[
    "enrollment_follow_up",
    "onboarding_assistance",
    "document_review",
    "missing_information",
    "external_verification",
    "deadline_risk",
    "staff_decision",
    "communication_response",
    "blocked_dependency",
]


class CreateStaffActionRuleRequest(StrictRequest):
    code: Annotated[
        StrictStr,
        StringConstraints(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9-]*$"),
    ]
    name: NameText
    description: Annotated[StrictStr, StringConstraints(min_length=1, max_length=1000)]
    enabled: StrictBool = True
    signal_type: StaffActionRuleSignal
    flow_kind: Literal["enrollment", "onboarding"] | None = None
    requirement_code: OperationalCode | None = None
    lookahead_days: StrictInt | None = Field(default=None, ge=0, le=365)
    inactivity_days: StrictInt | None = Field(default=None, ge=1, le=365)
    cadence_minutes: StrictInt = Field(ge=5, le=1440)
    component: NameText
    priority: StaffWorkPriority
    action_type: StaffWorkActionType
    title_template: Annotated[StrictStr, StringConstraints(min_length=1, max_length=240)]
    description_template: Annotated[StrictStr, StringConstraints(min_length=1, max_length=2000)]


class UpdateStaffActionRuleRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    name: NameText | None = None
    description: Annotated[StrictStr, StringConstraints(min_length=1, max_length=1000)] | None = (
        None
    )
    enabled: StrictBool | None = None
    flow_kind: Literal["enrollment", "onboarding"] | None = None
    requirement_code: OperationalCode | None = None
    lookahead_days: StrictInt | None = Field(default=None, ge=0, le=365)
    inactivity_days: StrictInt | None = Field(default=None, ge=1, le=365)
    cadence_minutes: StrictInt | None = Field(default=None, ge=5, le=1440)
    component: NameText | None = None
    priority: StaffWorkPriority | None = None
    action_type: StaffWorkActionType | None = None
    title_template: Annotated[StrictStr, StringConstraints(min_length=1, max_length=240)] | None = (
        None
    )
    description_template: (
        Annotated[StrictStr, StringConstraints(min_length=1, max_length=2000)] | None
    ) = None


class UpdateStaffStudentPreferencesRequest(StrictRequest):
    expected_onboarding_version: StrictInt = Field(ge=1)
    expected_profile_version: StrictInt = Field(ge=1)
    communication_preference: Literal["email", "sms"]
    housing_preference: HousingPreference
    accommodation_interest: Literal["not_now", "housing", "academic", "both"]
    residency_verification_path: Literal["home_address_review", "document_upload", "advisor_review"]
    notify_student: StrictBool
    note: Annotated[StrictStr, StringConstraints(min_length=1, max_length=500)] | None = None


class ReviewStaffDocumentRequest(StrictRequest):
    work_item_id: UUID
    expected_work_item_version: StrictInt = Field(ge=1)
    decision: Literal["accepted", "rejected"]
    note: Annotated[StrictStr, StringConstraints(min_length=3, max_length=500)]
    notify_student: StrictBool


StaffManagedConfigurationKind = Literal["journeys", "campus_life", "academics"]
StaffManagedContentStatus = Literal["draft", "published", "archived"]


class UpdateStaffManagedConfigurationRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    yaml: Annotated[StrictStr, StringConstraints(min_length=1, max_length=250_000)]
    change_summary: Annotated[StrictStr, StringConstraints(max_length=500)] | None = None


class DraftStaffManagedConfigurationRequest(StrictRequest):
    kind: StaffManagedConfigurationKind
    expected_version: StrictInt = Field(ge=1)
    instruction: Annotated[StrictStr, StringConstraints(min_length=3, max_length=2_000)]


class CreateStaffKnowledgeCardRequest(StrictRequest):
    title: Annotated[StrictStr, StringConstraints(min_length=2, max_length=120)]
    summary: Annotated[StrictStr, StringConstraints(min_length=3, max_length=240)]
    body: Annotated[StrictStr, StringConstraints(min_length=3, max_length=2_000)]
    category: Annotated[StrictStr, StringConstraints(min_length=2, max_length=60)]
    audience: Literal["internal", "student"]
    status: StaffManagedContentStatus


class UpdateStaffKnowledgeCardRequest(CreateStaffKnowledgeCardRequest):
    expected_version: StrictInt = Field(ge=1)


class CreateStaffCorePlayRequest(StrictRequest):
    title: Annotated[StrictStr, StringConstraints(min_length=2, max_length=120)]
    description: Annotated[StrictStr, StringConstraints(min_length=3, max_length=500)]
    trigger: Annotated[StrictStr, StringConstraints(min_length=3, max_length=240)]
    audience: Annotated[StrictStr, StringConstraints(min_length=3, max_length=240)]
    steps: list[Annotated[StrictStr, StringConstraints(min_length=2, max_length=240)]] = Field(
        min_length=1, max_length=12
    )
    status: Literal["draft", "active", "archived"]


class UpdateStaffCorePlayRequest(CreateStaffCorePlayRequest):
    expected_version: StrictInt = Field(ge=1)


class UpdateStaffInquiryRequest(StrictRequest):
    expected_version: StrictInt = Field(ge=1)
    status: Literal["new", "open", "waiting_on_student", "resolved"]
    assignee_id: UUID | None = None
    response_note: (
        Annotated[StrictStr, StringConstraints(min_length=1, max_length=1_000)] | None
    ) = None
    notify_student: StrictBool


class CreateStaffClubRequest(StrictRequest):
    name: Annotated[StrictStr, StringConstraints(min_length=2, max_length=180)]
    category: Annotated[StrictStr, StringConstraints(min_length=2, max_length=100)]
    description: Annotated[StrictStr, StringConstraints(min_length=3, max_length=2_000)]
    latest_update: Annotated[StrictStr, StringConstraints(min_length=3, max_length=1_000)]
    contact_name: Annotated[StrictStr, StringConstraints(min_length=2, max_length=180)]
    contact_role: Annotated[StrictStr, StringConstraints(min_length=2, max_length=120)]
    contact_channel: Annotated[StrictStr, StringConstraints(min_length=3, max_length=200)]
    membership_open: StrictBool
    image_url: Annotated[StrictStr, StringConstraints(min_length=1, max_length=500)] | None = None


class UpdateStaffClubRequest(CreateStaffClubRequest):
    expected_version: StrictInt = Field(ge=1)
    image_url: None = None


class SimulateStaffOutreachRequest(StrictRequest):
    title: Annotated[StrictStr, StringConstraints(min_length=2, max_length=180)]
    audience: Annotated[StrictStr, StringConstraints(min_length=3, max_length=1_000)]
    channel: Literal["email", "sms", "voice"]
    requested_count: StrictInt = Field(ge=1, le=10_000)


class PreviewStaffEdwardRequest(StrictRequest):
    message: Annotated[StrictStr, StringConstraints(min_length=1, max_length=2_000)]
