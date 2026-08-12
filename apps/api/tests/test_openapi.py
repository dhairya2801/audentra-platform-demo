from __future__ import annotations

import ast
import inspect

from audentra.interfaces.http import routes
from audentra.interfaces.http.app import create_app

EXPECTED_OPERATIONS = {
    ("get", "/health"): 200,
    ("get", "/health/ready"): 200,
    ("get", "/v1/student/dashboard"): 200,
    ("get", "/v1/student/academics"): 200,
    ("get", "/v1/catalog/courses"): 200,
    ("get", "/v1/student/financials"): 200,
    ("post", "/v1/student/financials/payment-plan"): 200,
    ("get", "/v1/student/campus-life"): 200,
    ("post", "/v1/admission-offers/{offerId}/accept"): 200,
    ("post", "/v1/activity-events/batch"): 202,
    ("get", "/v1/student/bootstrap"): 200,
    ("post", "/v1/student/experience-updates/defer"): 200,
    ("post", "/v1/student/experience-updates/{id}/decision"): 200,
    ("get", "/v1/student/onboarding"): 200,
    ("put", "/v1/student/onboarding"): 200,
    ("post", "/v1/student/onboarding/complete"): 200,
    ("get", "/v1/student/housing-plan"): 200,
    ("patch", "/v1/student/housing-plan"): 200,
    ("get", "/v1/student/requirements"): 200,
    ("get", "/v1/student/requirements/{id}"): 200,
    ("get", "/v1/student/messages"): 200,
    ("post", "/v1/student/messages/{id}/read"): 200,
    ("get", "/v1/student/documents"): 200,
    ("post", "/v1/student/documents"): 201,
    ("post", "/v1/student/documents/upload"): 201,
    ("get", "/v1/student/documents/{id}/content"): 200,
    ("get", "/v1/student/documents/{id}/profile-photo"): 200,
    ("post", "/v1/student/documents/{id}/confirm-extraction"): 200,
    ("post", "/v1/student/documents/{id}/retry-extraction"): 200,
    ("post", "/v1/student/internal/document-extractions/{id}"): 200,
    ("post", "/v1/student/internal/document-extraction-reservations/{id}"): 200,
    ("post", "/v1/student/assistant/messages"): 200,
    ("get", "/v1/student/appointments"): 200,
    ("post", "/v1/student/appointments"): 201,
    ("get", "/v1/student/payments"): 200,
    ("post", "/v1/student/payments/deposit"): 200,
    ("get", "/v1/student/profile"): 200,
    ("patch", "/v1/student/profile"): 200,
    ("get", "/v1/student/help"): 200,
    ("get", "/v1/staff/action-center"): 200,
    ("patch", "/v1/staff/work-items/{id}"): 200,
    ("get", "/v1/staff/students/{id}"): 200,
    ("patch", "/v1/staff/students/{id}/preferences"): 200,
    ("post", "/v1/staff/documents/{id}/decision"): 201,
}

EXPECTED_DISPATCH_OPERATIONS = {
    "health.liveness",
    "health.readiness",
    "student.get_dashboard",
    "student.get_academics",
    "catalog.search_courses",
    "student.get_financials",
    "student.select_payment_plan",
    "student.get_campus_life",
    "admission.accept_offer",
    "activity.ingest_batch",
    "student.get_bootstrap",
    "student.defer_experience_updates",
    "student.decide_experience_update",
    "student.get_onboarding",
    "student.update_onboarding",
    "student.complete_onboarding",
    "student.get_housing_plan",
    "student.update_housing_plan",
    "student.list_requirements",
    "student.get_requirement",
    "student.list_messages",
    "student.mark_message_read",
    "student.list_documents",
    "student.create_document",
    "student.upload_document",
    "student.get_document_content",
    "student.get_document_profile_photo",
    "student.confirm_document_extraction",
    "student.retry_document_extraction",
    "internal.process_document_extraction",
    "internal.recover_document_extraction_reservation",
    "student.ask_edward",
    "student.list_appointments",
    "student.create_appointment",
    "student.list_payments",
    "student.create_deposit",
    "student.get_profile",
    "student.update_profile",
    "student.get_help",
    "staff.get_action_center",
    "staff.update_work_item",
    "staff.get_student",
    "staff.update_student_preferences",
    "staff.review_document",
}

EXPECTED_EXTENDED_STAFF_OPERATIONS = {
    ("get", "/v1/staff/workspace"): 200,
    ("get", "/v1/staff/configurations/{kind}"): 200,
    ("put", "/v1/staff/configurations/{kind}"): 200,
    ("post", "/v1/staff/edward/configuration-draft"): 200,
    ("post", "/v1/staff/knowledge-base"): 201,
    ("patch", "/v1/staff/knowledge-base/{id}"): 200,
    ("post", "/v1/staff/core-plays"): 201,
    ("patch", "/v1/staff/core-plays/{id}"): 200,
    ("patch", "/v1/staff/inquiries/{id}"): 200,
    ("post", "/v1/staff/campus-life/clubs"): 201,
    ("patch", "/v1/staff/campus-life/clubs/{id}"): 200,
    ("post", "/v1/staff/outreach/simulate"): 201,
    ("post", "/v1/staff/edward/preview"): 200,
}

EXPECTED_EXTENDED_STAFF_DISPATCH_OPERATIONS = {
    "staff.get_workspace",
    "staff.get_managed_configuration",
    "staff.update_managed_configuration",
    "staff.draft_managed_configuration",
    "staff.create_knowledge_card",
    "staff.update_knowledge_card",
    "staff.create_core_play",
    "staff.update_core_play",
    "staff.update_inquiry",
    "staff.create_club",
    "staff.update_club",
    "staff.simulate_outreach",
    "staff.preview_edward",
}

EXPECTED_ACTION_CENTER_OPERATIONS = {
    ("post", "/v1/student/help/requests/{id}/messages"): 200,
    ("post", "/v1/staff/work-items"): 201,
    ("get", "/v1/staff/work-items/{id}"): 200,
    ("post", "/v1/staff/work-items/{id}/comments"): 201,
    ("post", "/v1/staff/work-items/{id}/interactions"): 201,
    ("post", "/v1/staff/interactions/{id}/communications"): 201,
    ("post", "/v1/staff/interactions/{id}/recordings"): 201,
    ("get", "/v1/staff/call-recordings/{id}/content"): 200,
    ("post", "/v1/staff/call-recordings/{id}/retry"): 202,
    ("post", "/v1/staff/interactions/{id}/complete"): 200,
    ("post", "/v1/staff/work-items/{id}/ai-refresh"): 202,
    ("get", "/v1/staff/action-rules"): 200,
    ("post", "/v1/staff/action-rules"): 201,
    ("patch", "/v1/staff/action-rules/{id}"): 200,
    ("get", "/v1/staff/notifications"): 200,
    ("post", "/v1/staff/notifications/{id}/read"): 200,
}

EXPECTED_ACTION_CENTER_DISPATCH_OPERATIONS = {
    "student.create_inquiry_message",
    "staff.create_work_item",
    "staff.get_work_item_detail",
    "staff.create_work_comment",
    "staff.start_interaction",
    "staff.record_interaction_communication",
    "staff.upload_call_recording",
    "staff.get_call_recording_content",
    "staff.retry_call_transcription",
    "staff.complete_interaction",
    "staff.request_ai_refresh",
    "staff.get_action_rules",
    "staff.create_action_rule",
    "staff.update_action_rule",
    "staff.get_notifications",
    "staff.mark_notification_read",
}


def test_openapi_preserves_nest_routes_and_registers_extended_staff_preview() -> None:
    schema = create_app().openapi()
    methods = {"get", "post", "put", "patch", "delete"}
    actual = {
        (method, path): min(int(code) for code in operation["responses"] if code.startswith("2"))
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
        if method in methods
    }

    expected = (
        EXPECTED_OPERATIONS | EXPECTED_EXTENDED_STAFF_OPERATIONS | EXPECTED_ACTION_CENTER_OPERATIONS
    )
    assert {operation: actual.get(operation) for operation in expected} == expected


def test_all_routes_dispatch_unique_known_application_operations() -> None:
    syntax_tree = ast.parse(inspect.getsource(routes))
    dispatched = [
        keyword.value.value
        for call in ast.walk(syntax_tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_dispatch"
        for keyword in call.keywords
        if keyword.arg == "operation"
        and isinstance(keyword.value, ast.Constant)
        and isinstance(keyword.value.value, str)
    ]

    assert len(dispatched) == len(set(dispatched))
    assert set(dispatched) >= (
        EXPECTED_DISPATCH_OPERATIONS
        | EXPECTED_EXTENDED_STAFF_DISPATCH_OPERATIONS
        | EXPECTED_ACTION_CENTER_DISPATCH_OPERATIONS
    )


def test_openapi_documents_400_envelope_instead_of_fastapi_422() -> None:
    schema = create_app().openapi()

    for path_item in schema["paths"].values():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert "400" in operation["responses"]
            assert "422" not in operation["responses"]
            assert operation["responses"]["400"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ApiErrorEnvelope"
            }


def test_openapi_request_models_are_strict_and_camel_case() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    profile = schemas["UpdateStudentProfileRequest"]
    onboarding = schemas["UpdateStudentOnboardingRequest"]
    experience_deferral = schemas["DeferStudentExperienceUpdatesRequest"]

    assert profile["additionalProperties"] is False
    assert "expectedVersion" in profile["properties"]
    assert "expected_version" not in profile["properties"]
    assert onboarding["additionalProperties"] is False
    assert "currentStep" in onboarding["properties"]
    assert experience_deferral["additionalProperties"] is False
    assert experience_deferral["required"] == ["updates"]


def test_openapi_multipart_schema_includes_upload_bundle_id() -> None:
    operation = create_app().openapi()["paths"]["/v1/student/documents/upload"]["post"]
    upload_schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]

    assert upload_schema["additionalProperties"] is False
    assert upload_schema["required"] == ["file"]
    assert "uploadBundleId" in upload_schema["properties"]
