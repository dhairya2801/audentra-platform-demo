"""Staff action-center and official document-decision compatibility."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from audentra.application.platform_service import InMemoryPlatformService
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.core.ports import FileUpload, ServiceCall
from audentra.infrastructure.memory.store import DEMO_IDS


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def staff_auth(*, actor_type: str = "staff", tenant_id: str | None = None) -> AuthContext:
    return AuthContext(
        tenant_id=tenant_id or DEMO_IDS["tenant_id"],
        student_id=DEMO_IDS["student_id"],
        actor_id=DEMO_IDS["staff_advisor_id"],
        actor_type=actor_type,  # type: ignore[arg-type]
    )


def call(
    operation: str,
    *,
    identity: AuthContext | None = None,
    payload: dict[str, Any] | None = None,
    path: dict[str, str] | None = None,
    upload: FileUpload | None = None,
) -> ServiceCall:
    return ServiceCall(
        operation=operation,
        auth=identity or staff_auth(),
        request_id=f"request.{uuid4()}",
        payload=payload or {},
        path_params=path or {},
        upload=upload,
    )


def test_action_center_requires_staff_and_updates_optimistic_work_item() -> None:
    service = InMemoryPlatformService()
    student_identity = staff_auth(actor_type="student")
    with pytest.raises(ApiError) as denied:
        run(service.dispatch(call("staff.get_action_center", identity=student_identity)))
    assert denied.value.status_code == 403
    assert denied.value.code == "STAFF_ACCESS_REQUIRED"

    center = run(service.dispatch(call("staff.get_action_center")))
    item = next(x for x in center["items"] if x["id"] == DEMO_IDS["staff_onboarding_work_item_id"])
    updated = run(
        service.dispatch(
            call(
                "staff.update_work_item",
                path={"work_item_id": item["id"]},
                payload={
                    "expectedVersion": item["version"],
                    "status": "in_progress",
                    "escalated": True,
                    "note": "Advisor review started.",
                },
            )
        )
    )
    assert updated["status"] == "in_progress"
    assert updated["escalated"] is True
    assert updated["version"] == 2
    assert [entry["action"] for entry in updated["history"][:3]] == [
        "commented",
        "escalated",
        "status_changed",
    ]

    with pytest.raises(ApiError) as stale:
        run(
            service.dispatch(
                call(
                    "staff.update_work_item",
                    path={"work_item_id": item["id"]},
                    payload={"expectedVersion": 1, "status": "done"},
                )
            )
        )
    assert stale.value.code == "VERSION_CONFLICT"


def test_staff_student_preferences_are_atomically_versioned_and_tenant_safe() -> None:
    service = InMemoryPlatformService()
    record = run(
        service.dispatch(call("staff.get_student", path={"student_id": DEMO_IDS["student_id"]}))
    )
    updated = run(
        service.dispatch(
            call(
                "staff.update_student_preferences",
                path={"student_id": DEMO_IDS["student_id"]},
                payload={
                    "expectedOnboardingVersion": record["onboarding"]["version"],
                    "expectedProfileVersion": record["profile"]["version"],
                    "communicationPreference": "sms",
                    "housingPreference": "on_campus",
                    "accommodationInterest": "housing",
                    "residencyVerificationPath": "advisor_review",
                    "notifyStudent": True,
                    "note": "Preferences confirmed with the student.",
                },
            )
        )
    )
    assert updated["profile"]["communicationPreference"] == "sms"
    assert updated["profile"]["version"] == 2
    assert updated["onboarding"]["data"]["housingPreference"] == "on_campus"
    assert updated["onboarding"]["version"] == 2
    assert (
        run(
            service.dispatch(
                call(
                    "student.list_messages",
                    identity=AuthContext(
                        tenant_id=DEMO_IDS["tenant_id"],
                        student_id=DEMO_IDS["student_id"],
                        actor_id=DEMO_IDS["person_id"],
                        actor_type="student",
                    ),
                )
            )
        )["unreadCount"]
        == 3
    )

    foreign = staff_auth(tenant_id="00000000-0000-7000-8000-000000009999")
    with pytest.raises(ApiError) as isolated:
        run(service.dispatch(call("staff.get_action_center", identity=foreign)))
    assert isolated.value.status_code == 404


def test_document_review_completes_work_item_and_notifies_student() -> None:
    service = InMemoryPlatformService()
    student = AuthContext(
        tenant_id=DEMO_IDS["tenant_id"],
        student_id=DEMO_IDS["student_id"],
        actor_id=DEMO_IDS["person_id"],
        actor_type="student",
    )
    upload_call = ServiceCall(
        operation="student.upload_document",
        auth=student,
        request_id="request.upload",
        payload={"idempotencyKey": "staff.document.upload.0001"},
        upload=FileUpload(
            file_name="review-me.pdf",
            mime_type="application/pdf",
            content=b"%PDF-1.7\nreview me\n%%EOF\n",
            category="consent",
        ),
    )
    uploaded = run(service.dispatch(upload_call))
    processed = run(
        service.dispatch(
            ServiceCall(
                operation="internal.process_document_extraction",
                auth=student,
                request_id="request.worker",
                path_params={"document_id": uploaded["id"]},
            )
        )
    )
    assert processed["status"] == "needs_review"

    center = run(service.dispatch(call("staff.get_action_center")))
    item = next(
        x for x in center["items"] if x.get("source") == {"type": "document", "id": uploaded["id"]}
    )
    result = run(
        service.dispatch(
            call(
                "staff.review_document",
                path={"document_id": uploaded["id"]},
                payload={
                    "workItemId": item["id"],
                    "expectedWorkItemVersion": item["version"],
                    "decision": "accepted",
                    "note": "The stored original matches the student record.",
                    "notifyStudent": True,
                },
            )
        )
    )
    assert result["document"]["status"] == "accepted"
    assert result["workItem"]["status"] == "done"
    assert result["workItem"]["version"] == 2
    assert result["notification"]["subject"] == "review-me.pdf was accepted"

    student_documents = run(
        service.dispatch(
            ServiceCall(
                operation="student.list_documents",
                auth=student,
                request_id="request.documents",
            )
        )
    )
    assert (
        next(x for x in student_documents["items"] if x["id"] == uploaded["id"])["status"]
        == "accepted"
    )


@pytest.mark.parametrize(
    ("decision", "subject_suffix"),
    [("accepted", "… was accepted"), ("rejected", "… needs changes")],
)
def test_long_document_name_keeps_original_and_bounds_derived_staff_labels(
    decision: str, subject_suffix: str
) -> None:
    service = InMemoryPlatformService()
    student = AuthContext(
        tenant_id=DEMO_IDS["tenant_id"],
        student_id=DEMO_IDS["student_id"],
        actor_id=DEMO_IDS["person_id"],
        actor_type="student",
    )
    file_name = f"{'ü' * 251}.pdf"
    uploaded = run(
        service.dispatch(
            ServiceCall(
                operation="student.upload_document",
                auth=student,
                request_id="request.long-document-upload",
                payload={"idempotencyKey": "staff.document.long-name.0001"},
                upload=FileUpload(
                    file_name=file_name,
                    mime_type="application/pdf",
                    content=b"%PDF-1.7\nlong-name boundary\n%%EOF\n",
                    category="consent",
                ),
            )
        )
    )
    processed = run(
        service.dispatch(
            ServiceCall(
                operation="internal.process_document_extraction",
                auth=student,
                request_id="request.long-document-worker",
                path_params={"document_id": uploaded["id"]},
            )
        )
    )
    assert processed["fileName"] == file_name
    assert len(processed["fileName"]) == 255
    assert processed["status"] == "needs_review"

    center = run(service.dispatch(call("staff.get_action_center")))
    item = next(
        candidate
        for candidate in center["items"]
        if candidate.get("source") == {"type": "document", "id": uploaded["id"]}
    )
    assert len(item["title"]) == 240
    assert item["title"].startswith("Review ")
    assert item["title"].endswith("…")

    result = run(
        service.dispatch(
            call(
                "staff.review_document",
                path={"document_id": uploaded["id"]},
                payload={
                    "workItemId": item["id"],
                    "expectedWorkItemVersion": item["version"],
                    "decision": decision,
                    "note": "The bounded-label fixture matches the stored original.",
                    "notifyStudent": True,
                },
            )
        )
    )
    subject = result["notification"]["subject"]
    assert len(subject) == 240
    assert subject.endswith(subject_suffix)
    assert result["document"]["status"] == decision
    assert result["document"]["fileName"] == file_name
