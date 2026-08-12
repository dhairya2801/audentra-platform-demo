"""Portal lifecycle, durable document, retry, and signed-PDF parity."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from audentra.application.platform_service import InMemoryPlatformService
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.core.ports import BinaryPayload, FileUpload, ServiceCall
from audentra.infrastructure.memory.store import DEMO_IDS


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def student_auth() -> AuthContext:
    return AuthContext(
        tenant_id=DEMO_IDS["tenant_id"],
        student_id=DEMO_IDS["student_id"],
        actor_id=DEMO_IDS["person_id"],
        actor_type="student",
    )


def call(
    operation: str,
    *,
    payload: dict[str, Any] | None = None,
    path: dict[str, str] | None = None,
    upload: FileUpload | None = None,
) -> ServiceCall:
    return ServiceCall(
        operation=operation,
        auth=student_auth(),
        request_id=f"request.{uuid4()}",
        payload=payload or {},
        path_params=path or {},
        upload=upload,
    )


def accept_offer(service: InMemoryPlatformService, key: str = "portal.offer.accept.0001") -> None:
    run(
        service.dispatch(
            call(
                "admission.accept_offer",
                payload={"idempotencyKey": key},
                path={"offer_id": DEMO_IDS["offer_id"]},
            )
        )
    )


def test_ordered_onboarding_deposit_completion_and_signed_pdfs() -> None:
    service = InMemoryPlatformService()
    bootstrap = run(service.dispatch(call("student.get_bootstrap")))
    assert bootstrap["onboarding"] == {
        "required": True,
        "status": "in_progress",
        "currentStep": "offer",
        "version": 1,
    }

    with pytest.raises(ApiError) as raised:
        run(
            service.dispatch(
                call(
                    "student.update_onboarding",
                    payload={
                        "expectedVersion": 1,
                        "currentStep": "offer",
                        "data": {},
                        "skip": True,
                    },
                )
            )
        )
    assert raised.value.code == "ONBOARDING_STEP_REQUIRED"

    accept_offer(service)
    steps = [
        ("offer", {}),
        (
            "about_you",
            {
                "firstName": "Alex",
                "lastName": "Morgan",
                "preferredName": "Alex",
                "personalEmail": "alex.morgan@example.com",
                "mobilePhone": "+15550102027",
                "citizenshipStatus": "us_citizen",
                "communicationPreference": "email",
                "residencyStatus": "domestic",
                "residencyVerificationPath": "home_address_review",
                "streetAddress": "18 Willow Street",
                "city": "Cambridge",
                "stateOrProvince": "MA",
                "postalCode": "02139",
                "country": "United States",
            },
        ),
        ("housing", {"housingPreference": "undecided"}),
        ("campus_life", {"campusInterests": ["student_clubs"]}),
        (
            "emergency_contacts",
            {
                "emergencyContacts": [
                    {
                        "fullName": "Jordan Morgan",
                        "relationship": "parent",
                        "mobilePhone": "+15550100300",
                    }
                ]
            },
        ),
        ("family_permissions", {"familyPermissions": []}),
        (
            "review_and_sign",
            {
                "signatureFullName": "Alex Morgan",
                "signatureMethod": "typed",
                "signatureConsent": True,
                "signedDocumentIds": ["ferpa_release", "enrollment_acknowledgment"],
            },
        ),
    ]
    version = 1
    for step, data in steps:
        onboarding = run(
            service.dispatch(
                call(
                    "student.update_onboarding",
                    payload={"expectedVersion": version, "currentStep": step, "data": data},
                )
            )
        )
        version = onboarding["version"]
    assert version == 8
    assert onboarding["currentStep"] == "deposit"

    deposit_request = call(
        "student.create_deposit",
        payload={"offerId": DEMO_IDS["offer_id"], "idempotencyKey": "portal.deposit.0001"},
    )
    first_deposit = run(service.dispatch(deposit_request))
    assert run(service.dispatch(deposit_request)) == first_deposit
    assert first_deposit["amountCents"] == 50_000

    onboarding = run(
        service.dispatch(
            call(
                "student.update_onboarding",
                payload={
                    "expectedVersion": version,
                    "currentStep": "deposit",
                    "data": {"depositChoice": "pay_now"},
                },
            )
        )
    )
    complete_call = call(
        "student.complete_onboarding",
        payload={
            "expectedVersion": onboarding["version"],
            "idempotencyKey": "onboarding.done.0001",
        },
    )
    completed = run(service.dispatch(complete_call))
    assert run(service.dispatch(complete_call)) == completed
    assert completed["status"] == "completed"
    assert completed["version"] == 10
    assert run(service.dispatch(call("student.get_bootstrap")))["initialRoute"] == "/dashboard"

    documents = run(service.dispatch(call("student.list_documents")))
    signed = [item for item in documents["items"] if item.get("signature")]
    assert len(signed) == 2
    assert all(item["status"] == "accepted" for item in signed)
    content = run(
        service.dispatch(
            call("student.get_document_content", path={"document_id": signed[0]["id"]})
        )
    )
    assert isinstance(content, BinaryPayload)
    assert content.media_type == "application/pdf"
    assert content.data.startswith(b"%PDF-")
    assert b"Electronically signed by Alex Morgan" in content.data


def test_upload_process_download_confirm_and_replay() -> None:
    service = InMemoryPlatformService()
    pdf = b"%PDF-1.7\nAster FERPA test\n%%EOF\n"
    request = call(
        "student.upload_document",
        payload={"idempotencyKey": "portal.document.upload.0001"},
        upload=FileUpload(
            file_name="release.pdf",
            mime_type="application/pdf",
            content=pdf,
            category="consent",
            upload_bundle_id="bundle.0001",
        ),
    )
    uploaded = run(service.dispatch(request))
    assert run(service.dispatch(request)) == uploaded
    assert uploaded["status"] == "processing"
    assert len(uploaded["sha256"]) == 64

    processed = run(
        service.dispatch(
            call(
                "internal.process_document_extraction",
                path={"document_id": uploaded["id"]},
            )
        )
    )
    assert processed["status"] == "needs_review"
    assert processed["extraction"]["documentType"] == "ferpa"
    assert service.ai.extraction_calls == 1

    content = run(
        service.dispatch(call("student.get_document_content", path={"document_id": uploaded["id"]}))
    )
    assert isinstance(content, BinaryPayload)
    assert content.data == pdf

    confirmed = run(
        service.dispatch(
            call(
                "student.confirm_document_extraction",
                path={"document_id": uploaded["id"]},
                payload={
                    "acceptedFieldKeys": ["student_name"],
                    "idempotencyKey": "portal.document.confirm.0001",
                },
            )
        )
    )
    assert confirmed["status"] == "under_review"
    assert confirmed["extraction"]["acceptedFieldKeys"] == ["student_name"]


def test_storage_failure_replay_and_transient_or_terminal_parse_retry() -> None:
    service = InMemoryPlatformService()
    pdf = b"%PDF-1.7\nretryable upload\n%%EOF\n"
    upload = FileUpload(
        file_name="retry.pdf", mime_type="application/pdf", content=pdf, category="transcript"
    )
    request = call(
        "student.upload_document",
        payload={"idempotencyKey": "portal.document.storage-retry.0001"},
        upload=upload,
    )
    service.storage.fail_puts = 1
    with pytest.raises(ApiError) as raised:
        run(service.dispatch(request))
    assert raised.value.status_code == 503
    assert raised.value.code == "DOCUMENT_STORAGE_UNAVAILABLE"
    recovered_upload = run(service.dispatch(request))
    assert recovered_upload["status"] == "processing"

    service.ai.queue_extraction(
        RuntimeError("OpenRouter returned HTTP 503: bearer secret-must-not-leak"),
        RuntimeError("OpenRouter returned HTTP 503: bearer secret-must-not-leak"),
    )
    failed = run(
        service.dispatch(
            call(
                "internal.process_document_extraction",
                path={"document_id": recovered_upload["id"]},
            )
        )
    )
    assert failed["status"] == "uploaded"
    assert failed["extraction"]["failureCode"] == "provider_unavailable"
    assert failed["extraction"]["retryable"] is True
    assert "secret-must-not-leak" not in str(failed)

    retry_call = call(
        "student.retry_document_extraction",
        path={"document_id": failed["id"]},
        payload={"idempotencyKey": "portal.document.retry.0001"},
    )
    retried = run(service.dispatch(retry_call))
    assert retried["status"] == "processing"
    assert run(service.dispatch(retry_call)) == retried
    recovered = run(
        service.dispatch(
            call(
                "internal.process_document_extraction",
                path={"document_id": failed["id"]},
            )
        )
    )
    assert recovered["status"] == "needs_review"
    assert run(service.dispatch(retry_call)) == recovered

    unsupported_request = call(
        "student.upload_document",
        payload={"idempotencyKey": "portal.document.unsupported.0001"},
        upload=FileUpload(
            file_name="unsupported.pdf",
            mime_type="application/pdf",
            content=pdf,
            category="identity",
        ),
    )
    unsupported = run(service.dispatch(unsupported_request))
    service.ai.queue_extraction(RuntimeError("HTTP 415 unsupported parser capability"))
    terminal = run(
        service.dispatch(
            call(
                "internal.process_document_extraction",
                path={"document_id": unsupported["id"]},
            )
        )
    )
    assert terminal["extraction"]["failureCode"] == "unsupported_capability"
    assert terminal["extraction"]["retryable"] is False
    terminal_retry = call(
        "student.retry_document_extraction",
        path={"document_id": terminal["id"]},
        payload={"idempotencyKey": "portal.document.unsupported-retry.0001"},
    )
    assert run(service.dispatch(terminal_retry)) == terminal


def test_messages_requirements_profile_appointments_help_and_edward() -> None:
    service = InMemoryPlatformService()
    accept_offer(service)
    requirements = run(service.dispatch(call("student.list_requirements")))
    requirement = requirements["items"][0]
    by_slug = run(
        service.dispatch(
            call("student.get_requirement", path={"requirement_id": requirement["slug"]})
        )
    )
    assert by_slug == requirement

    messages = run(service.dispatch(call("student.list_messages")))
    read_call = call("student.mark_message_read", path={"message_id": messages["items"][0]["id"]})
    first_read = run(service.dispatch(read_call))
    assert run(service.dispatch(read_call)) == first_read

    profile = run(service.dispatch(call("student.get_profile")))
    updated = run(
        service.dispatch(
            call(
                "student.update_profile",
                payload={
                    "expectedVersion": profile["version"],
                    "preferredName": "Alexis",
                    "communicationPreference": "sms",
                },
            )
        )
    )
    assert updated["version"] == 2
    with pytest.raises(ApiError) as stale:
        run(
            service.dispatch(
                call(
                    "student.update_profile",
                    payload={"expectedVersion": profile["version"], "preferredName": "Stale"},
                )
            )
        )
    assert stale.value.code == "VERSION_CONFLICT"

    appointment_call = call(
        "student.create_appointment",
        payload={
            "type": "financial_aid",
            "startsAt": "2030-08-10T14:00:00.000Z",
            "notes": "Deposit questions",
            "idempotencyKey": "appointment.0001",
        },
    )
    appointment = run(service.dispatch(appointment_call))
    assert run(service.dispatch(appointment_call)) == appointment
    assert appointment["status"] == "scheduled"

    help_content = run(service.dispatch(call("student.get_help")))
    assert help_content["articles"]
    edward = run(
        service.dispatch(
            call(
                "student.ask_edward",
                payload={
                    "message": "Where is my document?",
                    "pageContext": "/dashboard",
                    "history": [],
                },
            )
        )
    )
    # The assistant pipeline answers deterministically (no provider tokens)
    # from the checklist and document reads of this turn.
    assert edward["provider"] == "guided"
    assert edward["usage"] is None
    sources = {receipt["source"] for receipt in edward["contextReceipts"]}
    assert {"onboarding", "documents"} <= sources
    assert edward["blocks"], "the assistant response carries presentation blocks"
