"""The portal and Edward must read the same underlying state.

These tests drive the same in-memory platform composition the portal-facing
preview serves: portal projections come from `student.*` dispatch operations,
Edward's view comes from the tool results captured in the per-turn
`AssistantTurnTrace`. For every representative synthetic state, what the
student would see on a portal page and what Edward's tools returned must
agree — and a mutation made through a portal operation must be visible to
Edward on the very next turn.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from audentra.application.platform_service import InMemoryPlatformService
from audentra.core.auth import AuthContext
from audentra.core.ports import ServiceCall
from audentra.domain.student_state import AID_DOCUMENT_SATISFIED_STATUSES
from audentra.infrastructure.memory.eval_personas import apply_persona
from audentra.infrastructure.memory.store import DEMO_IDS, InMemoryPlatformStore
from audentra.integrations.assistant.trace import get_assistant_trace_recorder

AUTH = AuthContext(
    tenant_id=DEMO_IDS["tenant_id"],
    student_id=DEMO_IDS["student_id"],
    actor_id=DEMO_IDS["person_id"],
    actor_type="student",
)

_OPEN = {"ready", "in_progress", "blocked", "submitted", "under_review", "rejected"}


def _service(persona: str) -> InMemoryPlatformService:
    store = InMemoryPlatformStore()
    apply_persona(store, persona)
    return InMemoryPlatformService(store=store)


def _dispatch(
    service: InMemoryPlatformService,
    operation: str,
    payload: dict[str, Any] | None = None,
    request_id: str = "req",
    path_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        asyncio.run(
            service.dispatch(
                ServiceCall(
                    operation=operation,
                    auth=AUTH,
                    request_id=request_id,
                    payload=payload or {},
                    path_params=path_params or {},
                )
            )
        ),
    )


def _ask(service: InMemoryPlatformService, message: str, request_id: str) -> dict[str, Any]:
    return _dispatch(
        service,
        "student.ask_edward",
        {"message": message, "pageContext": "/enrollment"},
        request_id=request_id,
    )


def _tool_result(request_id: str, tool: str) -> dict[str, Any]:
    trace = get_assistant_trace_recorder().get(request_id)
    assert trace is not None, f"no trace for {request_id}"
    call = next((c for c in trace["toolCalls"] if c["tool"] == tool), None)
    assert call is not None, (
        f"{tool} was not read; executed: {[c['tool'] for c in trace['toolCalls']]}"
    )
    assert call["status"] == "available"
    result = call.get("result")
    assert isinstance(result, dict)
    return result


def _checklist_statuses(items: list[dict[str, Any]]) -> dict[str, str]:
    return {str(item["code"]): str(item["status"]) for item in items}


def _assert_checklist_agreement(service: InMemoryPlatformService, request_id: str) -> None:
    """The core invariant: Edward's checklist read equals the portal's."""

    portal = _dispatch(service, "student.list_requirements")
    edward = _tool_result(request_id, "getOnboardingChecklist")
    assert _checklist_statuses(edward["items"]) == _checklist_statuses(portal["items"])


def test_transcript_under_review_agrees_between_portal_and_edward() -> None:
    service = _service("transcript_under_review")

    portal_documents = _dispatch(service, "student.list_documents")
    portal_statuses = {item["category"]: item["status"] for item in portal_documents["items"]}
    assert portal_statuses.get("transcript") == "under_review"

    response = _ask(service, "Did you receive my transcript?", "agree-transcript")
    edward_documents = _tool_result("agree-transcript", "getDocumentStatuses")
    edward_statuses = {item["category"]: item["status"] for item in edward_documents["items"]}
    assert edward_statuses == portal_statuses
    _assert_checklist_agreement(service, "agree-transcript")
    assert "under review" in response["message"].lower()


def test_pending_deposit_payment_agrees() -> None:
    service = _service("payment_pending")

    portal_payments = _dispatch(service, "student.list_payments")
    assert any(
        item["type"] == "enrollment_deposit" and item["status"] == "pending"
        for item in portal_payments["items"]
    )

    _ask(service, "Is my deposit paid?", "agree-pending")
    account = _tool_result("agree-pending", "getStudentAccountSummary")
    assert account["depositPaid"] is False
    assert account["depositPaymentPending"] is True


def test_blocked_housing_agrees() -> None:
    service = _service("new_admit")

    portal = _dispatch(service, "student.list_requirements")
    assert _checklist_statuses(portal["items"])["housing_preference"] == "blocked"

    _ask(service, "Can I apply for housing?", "agree-housing")
    eligibility = _tool_result("agree-housing", "getStudentHousingEligibility")
    assert eligibility["eligibility"] == "blocked"
    gate_codes = {gate["code"] for gate in eligibility["gates"]}
    assert "enrollment_deposit_posted" in gate_codes
    _assert_checklist_agreement(service, "agree-housing")


def test_incomplete_financial_aid_agrees() -> None:
    service = _service("aid_verification_outstanding")

    portal = _dispatch(service, "student.get_financials")
    portal_open = {
        item["code"]: item["status"]
        for item in portal["requiredDocuments"]
        if item["status"] not in AID_DOCUMENT_SATISFIED_STATUSES
    }
    assert "verification_worksheet" in portal_open

    _ask(service, "Why is my financial aid incomplete?", "agree-aid")
    aid = _tool_result("agree-aid", "getFinancialAidStatus")
    edward_open = {item["code"]: item["status"] for item in aid["openRequirements"]}
    assert edward_open == portal_open


def test_multiple_blockers_agree() -> None:
    service = _service("deadline_passed")

    portal = _dispatch(service, "student.list_requirements")
    portal_open_blocking = {
        item["code"] for item in portal["items"] if item["blocking"] and item["status"] in _OPEN
    }
    assert len(portal_open_blocking) >= 3

    _ask(service, "Why can't I register for classes?", "agree-blockers")
    registration = _tool_result("agree-blockers", "getRegistrationStatus")
    gate_codes = {gate["code"] for gate in registration["gates"]}
    # Every registration gate corresponds to open portal state: either the
    # unpaid-deposit offer record or an open blocking requirement.
    mapped = {
        "enrollment_deposit_posted": "enrollment_deposit",
        "final_transcript": "official_transcript",
        "immunization_cleared": "immunization_record",
    }
    for code in gate_codes:
        assert mapped.get(code, code) in portal_open_blocking
    _assert_checklist_agreement(service, "agree-blockers")


def test_portal_mutation_is_visible_to_edward_on_the_next_turn() -> None:
    """Complete the housing step through the portal operation, then ask."""

    service = _service("nearly_complete")

    _ask(service, "What do I still need to do?", "mutate-before")
    before = _tool_result("mutate-before", "getOnboardingChecklist")
    assert _checklist_statuses(before["items"])["housing_preference"] != "completed"

    onboarding_version = _dispatch(service, "student.get_housing_plan")["version"]
    _dispatch(
        service,
        "student.update_housing_plan",
        {
            "expectedVersion": onboarding_version,
            "preference": "on_campus",
            "residenceOption": "residence_hall",
        },
    )

    portal = _dispatch(service, "student.list_requirements")
    assert _checklist_statuses(portal["items"])["housing_preference"] == "completed"

    _ask(service, "What do I still need to do?", "mutate-after")
    edward = _tool_result("mutate-after", "getOnboardingChecklist")
    assert _checklist_statuses(edward["items"])["housing_preference"] == "completed"
    _assert_checklist_agreement(service, "mutate-after")


def test_enrollment_position_agrees_with_the_dashboard() -> None:
    """Program, term, campus, class year and progress are all reachable."""

    service = _service("new_admit")
    dashboard = _dispatch(service, "student.get_dashboard")

    _ask(service, "What term am I starting?", "agree-enrollment")
    state = _tool_result("agree-enrollment", "getEnrollmentState")
    assert state["admission"]["programName"] == dashboard["offer"]["programName"]
    assert state["admission"]["termName"] == dashboard["offer"]["termName"]
    assert state["admission"]["campusName"] == dashboard["offer"]["campusName"]
    assert state["admission"]["offerStatus"] == dashboard["offer"]["status"]
    assert state["student"]["classYear"] == dashboard["student"]["classYear"]
    assert state["journey"]["completionPercent"] == dashboard["journey"]["completionPercent"]


def test_onboarding_answers_agree_with_the_onboarding_record() -> None:
    """Answers saved through the wizard are readable on the next turn."""

    service = _service("new_admit")
    _dispatch(
        service,
        "admission.accept_offer",
        {"idempotencyKey": "accept-1"},
        path_params={"offerId": DEMO_IDS["offer_id"]},
    )
    for step, data in (
        ("offer", {}),
        (
            "about_you",
            {
                "firstName": "Alex",
                "lastName": "Morgan",
                "preferredName": "Alex",
                "personalEmail": "alex.morgan@example.com",
                "mobilePhone": "+1 555 010 4471",
                "communicationPreference": "email",
                "residencyStatus": "domestic",
                "citizenshipStatus": "permanent_resident",
                "streetAddress": "12 Wren Court",
                "city": "Boston",
            },
        ),
        ("housing", {"housingPreference": "on_campus"}),
        ("campus_life", {"campusInterests": ["intramural_sports"]}),
        (
            "emergency_contacts",
            {"emergencyContacts": [{"name": "Sam Rivera", "relationship": "sibling"}]},
        ),
    ):
        _dispatch(
            service,
            "student.update_onboarding",
            {
                "expectedVersion": _dispatch(service, "student.get_onboarding")["version"],
                "currentStep": step,
                "data": data,
            },
        )
    portal = _dispatch(service, "student.get_onboarding")
    assert portal["data"]["citizenshipStatus"] == "permanent_resident"

    _ask(service, "What address do you have on file for me?", "agree-personal")
    responses = _tool_result("agree-personal", "getOnboardingResponses")
    assert responses["citizenshipStatus"] == portal["data"]["citizenshipStatus"]
    assert "12 Wren Court" in responses["mailingAddress"]
    assert [contact["name"] for contact in responses["emergencyContacts"]] == ["Sam Rivera"]


def test_academic_standing_agrees_with_the_financials_page() -> None:
    service = _service("new_admit")
    portal = _dispatch(service, "student.get_financials")

    _ask(service, "What is my GPA?", "agree-standing")
    standing = _tool_result("agree-standing", "getAcademicStanding")
    sap = standing["satisfactoryAcademicProgress"]
    assert sap["cumulativeGpa"] == portal["sap"]["cumulativeGpa"]
    assert sap["minimumGpa"] == portal["sap"]["minimumGpa"]
    assert sap["status"] == portal["sap"]["status"]


def test_profile_detail_agrees_with_the_profile_page() -> None:
    """Contact fields are redacted in the trace but present in the read.

    `sanitize_trace_value` masks phone/email keys, so the trace can only
    confirm the non-contact fields agree; that the contact keys are *present*
    is what proves the read is no longer the three-field summary it was.
    """

    service = _service("new_admit")
    portal = _dispatch(service, "student.get_profile")

    _ask(service, "Hi", "agree-profile")
    profile = _tool_result("agree-profile", "getStudentProfile")
    for key in ("preferredName", "pronouns", "communicationPreference"):
        assert profile[key] == portal.get(key)
    for key in ("email", "mobilePhone", "emailVerified", "phoneVerified"):
        assert key in profile


def test_a_posted_deposit_is_reported_as_paid() -> None:
    """The divergence class this work exists to make impossible."""

    service = _service("deposit_posted")
    payments = _dispatch(service, "student.list_payments")
    assert any(
        item["type"] == "enrollment_deposit" and item["status"] == "succeeded"
        for item in payments["items"]
    )

    response = _ask(service, "Is my deposit paid?", "agree-deposit-paid")
    account = _tool_result("agree-deposit-paid", "getStudentAccountSummary")
    assert account["depositPaid"] is True
    assert account["depositState"]["known"] is True
    # The same projection reaches every tool that carries deposit state.
    enrollment = _tool_result("agree-deposit-paid", "getEnrollmentState")
    assert enrollment["depositState"] == account["depositState"]
    assert "not been paid" not in response["message"].lower()

    _ask(service, "Why can't I register for classes?", "agree-deposit-paid-gates")
    registration = _tool_result("agree-deposit-paid-gates", "getRegistrationStatus")
    assert "enrollment_deposit_posted" not in {gate["code"] for gate in registration["gates"]}


def test_everything_complete_state_agrees() -> None:
    """nearly_complete plus the housing step done: nothing blocking remains."""

    service = _service("nearly_complete")
    onboarding_version = _dispatch(service, "student.get_housing_plan")["version"]
    _dispatch(
        service,
        "student.update_housing_plan",
        {
            "expectedVersion": onboarding_version,
            "preference": "on_campus",
            "residenceOption": "residence_hall",
        },
    )

    portal = _dispatch(service, "student.list_requirements")
    open_blocking = {
        item["code"] for item in portal["items"] if item["blocking"] and item["status"] in _OPEN
    }
    assert open_blocking == set()

    response = _ask(service, "Why can't I register for classes?", "agree-complete")
    registration = _tool_result("agree-complete", "getRegistrationStatus")
    assert registration["gates"] == []
    assert registration["eligible"] is True
    assert "nothing" in response["message"].lower() or "no " in response["message"].lower()
    _assert_checklist_agreement(service, "agree-complete")
