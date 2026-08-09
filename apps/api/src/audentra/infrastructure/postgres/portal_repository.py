# ruff: noqa: S608 -- only the module-owned DOCUMENT_SELECT constant is interpolated.
"""Async SQLAlchemy Core port of the legacy PostgreSQL portal store.

The repository deliberately owns transaction boundaries for commands.  State,
audit, outbox, rewards, and idempotency records therefore commit or roll back
together exactly as they did in the Nest implementation.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError
from audentra.domain.onboarding import (
    ABOUT_YOU_REQUIRED_FIELDS,
    ONBOARDING_STEPS,
    is_skippable_onboarding_step,
    validate_onboarding_step,
)
from audentra.infrastructure.messaging.envelope import DomainEventActor, DomainEventEnvelope
from audentra.infrastructure.messaging.outbox import OutboxRepository, OutboxRepositoryConfig

JsonDict = dict[str, Any]
IdempotentHandler = Callable[[AsyncConnection], Awaitable[JsonDict]]

REQUIREMENT_SLUGS = {
    "profile_verification": "profile-verification",
    "identity_document": "identity-document-upload",
    "official_transcript": "transcript-upload",
    "financial_aid_verification": "financial-aid-verification",
    "immunization_record": "immunization-upload",
    "enrollment_deposit": "enrollment-deposit",
}
DOCUMENT_CATEGORIES = {
    "identity_document": "identity",
    "official_transcript": "transcript",
    "financial_aid_verification": "financial_aid",
    "immunization_record": "health",
}
PROCESSING_MODES = {
    "identity": "agentic",
    "transcript": "agentic",
    "health": "agentic",
    "residency": "agentic",
    "financial_aid": "classification_only",
    "consent": "manual_review",
    "other": "agentic",
}
_ABOUT_YOU_CONFIGURABLE_FIELDS = frozenset(
    {
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
    }
)
_ABOUT_YOU_FIELD_BINDINGS = {
    "first_name": "firstName",
    "last_name": "lastName",
    "preferred_name": "preferredName",
    "personal_email": "personalEmail",
    "mobile_phone": "mobilePhone",
    "citizenship_status": "citizenshipStatus",
    "street_address": "streetAddress",
    "city": "city",
    "state_or_province": "stateOrProvince",
    "postal_code": "postalCode",
    "country": "country",
    "residency_verification_path": "residencyVerificationPath",
}
_HOUSING_PLAN_FIELD_BINDINGS = {
    "residencePreferences": "housingResidencePreferences",
    "roomType": "housingRoomType",
    "bathroomPreference": "bathroomPreference",
    "roommateMatching": "roommateMatching",
    "knownRoommateName": "knownRoommateName",
    "knownRoommateEmail": "knownRoommateEmail",
    "sleepSchedule": "sleepSchedule",
    "studyHabits": "studyHabits",
    "roomNoise": "roomNoise",
    "cleanliness": "cleanliness",
    "guestPreference": "guestPreference",
    "temperaturePreference": "temperaturePreference",
    "smokeVapeCompatibility": "smokeVapeCompatibility",
    "substanceFreeHousing": "substanceFreeHousing",
    "genderInclusiveHousing": "genderInclusiveHousing",
    "accessibleHousingInformation": "accessibleHousingInformation",
    "livingLearningCommunities": "livingLearningCommunities",
}
_ONBOARDING_SCREEN_DEFAULTS: dict[str, tuple[str, str, str]] = {
    "offer": (
        "Offer",
        "Your place at Aster",
        "Begin by confirming the admission decision that brought you here.",
    ),
    "about_you": (
        "About you",
        "Identity & home address",
        "Add the personal details and permanent address Aster needs to prepare your "
        "student record.",
    ),
    "housing": (
        "Housing",
        "One personalized story",
        "Tell us where you imagine starting your Aster experience.",
    ),
    "campus_life": (
        "Campus life",
        "Clubs, people & support",
        "Choose the communities and support you want to hear about.",
    ),
    "emergency_contacts": (
        "Emergency contacts",
        "People in your corner",
        "Enter one or more people Aster may contact in an emergency.",
    ),
    "family_permissions": (
        "Family permissions",
        "Your privacy, your choice",
        "Choose whether anyone else may discuss parts of your student record.",
    ),
    "review_and_sign": (
        "Review & sign",
        "Review and sign",
        "Review your answers and sign the enrollment and privacy acknowledgements.",
    ),
    "deposit": (
        "Deposit",
        "Review your deposit",
        "Confirm the enrollment-deposit amount and continue to the enrollment checklist.",
    ),
}
DOCUMENT_SELECT = """
id, requirement_id, file_name, mime_type, size_bytes, category,
processing_mode, status, storage_key, sha256, extraction, created_at
"""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


def _json_default(value: object) -> str:
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        timestamp = value if value.tzinfo else value.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    raise RuntimeError("Database returned an invalid timestamp")


def _nullable_iso(value: object) -> str | None:
    return None if value is None else _iso(value)


def _add_calendar_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _timestamp(value: object) -> datetime:
    """Normalize an API timestamp before binding it to a PostgreSQL timestamptz."""

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise RuntimeError("Application produced an invalid timestamp")


def _mapping(value: object) -> JsonDict:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return {str(key): item for key, item in parsed.items()}
    return {}


def _list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def _map_onboarding(row: Mapping[str, Any]) -> JsonDict:
    return {
        "studentId": str(row["student_id"]),
        "status": row["status"],
        "currentStep": row["current_step"],
        "completedSteps": _list(row["completed_steps"]),
        "data": _mapping(row["payload"]),
        "version": int(row["version"]),
        "completedAt": _nullable_iso(row["completed_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


def _onboarding_screen_configurations(document: Mapping[str, Any]) -> JsonDict:
    configurations: JsonDict = {}
    flows = document.get("flows")
    if not isinstance(flows, list):
        return configurations
    for flow in flows:
        if not isinstance(flow, Mapping) or flow.get("kind") != "onboarding":
            continue
        tasks = flow.get("tasks")
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, Mapping):
                continue
            step = task.get("student_step")
            if not isinstance(step, str) or step not in ONBOARDING_STEPS:
                continue
            raw_input = task.get("input")
            input_config = raw_input if isinstance(raw_input, Mapping) else {}
            default_label, default_title, default_description = _ONBOARDING_SCREEN_DEFAULTS[step]
            configuration: JsonDict = {
                "label": str(input_config.get("screen_label") or default_label),
                "title": str(input_config.get("screen_title") or default_title),
                "description": str(input_config.get("screen_description") or default_description),
            }
            raw_fields = input_config.get("fields")
            if isinstance(raw_fields, list):
                configuration["fields"] = [
                    dict(field) for field in raw_fields if isinstance(field, Mapping)
                ]
            if step == "about_you":
                raw_required = input_config.get(
                    "required_fields", input_config.get("requiredFields")
                )
                required_fields = (
                    [
                        str(field)
                        for field in raw_required
                        if isinstance(field, str) and field in _ABOUT_YOU_CONFIGURABLE_FIELDS
                    ]
                    if isinstance(raw_required, list)
                    else list(ABOUT_YOU_REQUIRED_FIELDS)
                )
                configuration.update(
                    {
                        "requiredFields": required_fields,
                        "identityQuickUpload": input_config.get(
                            "identity_quick_upload",
                            input_config.get("identityQuickUpload", True),
                        )
                        is not False,
                    }
                )
            configurations[step] = configuration
    return configurations


def _map_housing(row: Mapping[str, Any], residences: list[JsonDict] | None = None) -> JsonDict:
    payload = _mapping(row["payload"])
    preference = payload.get("housingPreference")
    if preference not in {"on_campus", "off_campus", "commuting", "undecided", "family"}:
        preference = None
    residence = payload.get("housingResidenceOption")
    if residence not in {"aster_residence_hall", "aster_apartments", "student_village"}:
        residence = None
    mapped: JsonDict = {
        "preference": preference,
        "residenceOption": residence,
        "residencePreferences": _list(payload.get("housingResidencePreferences")),
        "roomType": payload.get("housingRoomType"),
        "bathroomPreference": payload.get("bathroomPreference"),
        "roommateMatching": payload.get("roommateMatching"),
        "knownRoommateName": payload.get("knownRoommateName"),
        "knownRoommateEmail": payload.get("knownRoommateEmail"),
        "sleepSchedule": payload.get("sleepSchedule"),
        "studyHabits": payload.get("studyHabits"),
        "roomNoise": payload.get("roomNoise"),
        "cleanliness": payload.get("cleanliness"),
        "guestPreference": payload.get("guestPreference"),
        "temperaturePreference": payload.get("temperaturePreference"),
        "smokeVapeCompatibility": payload.get("smokeVapeCompatibility"),
        "substanceFreeHousing": payload.get("substanceFreeHousing"),
        "genderInclusiveHousing": payload.get("genderInclusiveHousing"),
        "accessibleHousingInformation": payload.get("accessibleHousingInformation"),
        "livingLearningCommunities": _list(payload.get("livingLearningCommunities")),
        "residences": residences or [],
        "version": int(row["version"]),
        "updatedAt": _iso(row["updated_at"]),
    }
    return mapped


def _requirement_code(identifier: str) -> str:
    reverse = {slug: code for code, slug in REQUIREMENT_SLUGS.items()}
    return reverse.get(identifier, identifier.lower().replace("-", "_"))


def _interaction_type_for_submission(submission_type: str) -> str:
    return {
        "none": "information",
        "document": "upload_file",
        "payment": "payment",
        "appointment": "scheduling",
    }.get(submission_type, "form")


def _invalid_requirement_response(message: str) -> BadRequestError:
    return BadRequestError("INVALID_REQUIREMENT_RESPONSE", message)


def _bounded_response_value(
    value: object,
    *,
    depth: int = 0,
    counter: list[int] | None = None,
) -> object:
    if depth > 5:
        raise _invalid_requirement_response("The response is nested too deeply")
    nodes = counter if counter is not None else [0]
    nodes[0] += 1
    if nodes[0] > 200:
        raise _invalid_requirement_response("The response contains too many values")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > 2_000:
            raise _invalid_requirement_response("Response text cannot exceed 2,000 characters")
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _invalid_requirement_response("Response numbers must be finite")
        return value
    if isinstance(value, Mapping):
        if len(value) > 100:
            raise _invalid_requirement_response("A response object has too many fields")
        result: JsonDict = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 100:
                raise _invalid_requirement_response("Response field names are invalid")
            result[raw_key] = _bounded_response_value(
                item,
                depth=depth + 1,
                counter=nodes,
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) > 100:
            raise _invalid_requirement_response("A response list has too many values")
        return [_bounded_response_value(item, depth=depth + 1, counter=nodes) for item in value]
    raise _invalid_requirement_response("The response contains an unsupported value")


def _strict_response_object(value: object) -> JsonDict:
    if not isinstance(value, Mapping):
        raise _invalid_requirement_response("response must be an object")
    bounded = _bounded_response_value(value)
    if not isinstance(bounded, dict):  # pragma: no cover - guarded above
        raise _invalid_requirement_response("response must be an object")
    if len(_json(bounded).encode("utf-8")) > 50_000:
        raise _invalid_requirement_response("The response is too large")
    return bounded


def _response_keys(
    response: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional_keys = optional or set()
    missing = required - response.keys()
    extra = response.keys() - required - optional_keys
    if missing or extra:
        raise _invalid_requirement_response(
            "Response fields do not match this task's interaction type"
        )


def _string_options(value: object) -> list[str]:
    return [str(item) for item in _list(value) if isinstance(item, str)]


def _validate_configured_values(values: JsonDict, fields_value: object) -> JsonDict:
    fields = [field for field in _list(fields_value) if isinstance(field, Mapping)]
    if not fields:
        return values
    known_ids = {str(field.get("id")) for field in fields if field.get("id")}
    if set(values) - known_ids:
        raise _invalid_requirement_response("The form contains an unknown field")
    normalized: JsonDict = {}
    for field_value in fields:
        field = _mapping(field_value)
        field_id = str(field.get("id") or "")
        if not field_id:
            raise _invalid_requirement_response("The published form contains an invalid field")
        when = _mapping(field.get("when"))
        applies = not when or values.get(str(when.get("field"))) == when.get("equals")
        present = field_id in values and values[field_id] not in (None, "", [])
        if not applies:
            if field_id in values:
                raise _invalid_requirement_response(
                    f"Field {field_id} is not applicable to this response"
                )
            continue
        if field.get("required") is True and not present:
            raise _invalid_requirement_response(f"Field {field_id} is required")
        if not present:
            continue
        value = values[field_id]
        field_type = str(field.get("field_type") or field.get("fieldType") or "text")
        if field_type in {"text", "email", "phone", "date"}:
            if not isinstance(value, str):
                raise _invalid_requirement_response(f"Field {field_id} must be text")
            if field_type == "email" and (
                value.count("@") != 1 or "." not in value.rsplit("@", 1)[1]
            ):
                raise _invalid_requirement_response(f"Field {field_id} must be an email")
            if field_type == "date":
                try:
                    date.fromisoformat(value)
                except ValueError as error:
                    raise _invalid_requirement_response(
                        f"Field {field_id} must be an ISO date"
                    ) from error
        elif field_type == "checkbox":
            if not isinstance(value, bool):
                raise _invalid_requirement_response(f"Field {field_id} must be true or false")
        elif field_type == "single_select":
            options = _string_options(field.get("options"))
            if not isinstance(value, str) or value not in options:
                raise _invalid_requirement_response(f"Field {field_id} has an invalid option")
        elif field_type == "multiple_select":
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) for item in value)
            ):
                raise _invalid_requirement_response(f"Field {field_id} must be a selection list")
            selected = [str(item) for item in value]
            options = _string_options(field.get("options"))
            maximum = field.get("maximum_selections", field.get("maximumSelections"))
            if len(selected) != len(set(selected)) or any(item not in options for item in selected):
                raise _invalid_requirement_response(f"Field {field_id} has invalid selections")
            if (
                isinstance(maximum, int)
                and not isinstance(maximum, bool)
                and len(selected) > maximum
            ):
                raise _invalid_requirement_response(f"Field {field_id} has too many selections")
        else:
            raise _invalid_requirement_response(
                f"Field {field_id} has an unsupported published type"
            )
        normalized[field_id] = value
    return normalized


def _normalize_requirement_response(
    interaction_type: str,
    input_config: Mapping[str, Any],
    value: object,
) -> JsonDict:
    response = _strict_response_object(value)
    if interaction_type == "information":
        _response_keys(response, required={"acknowledged"})
        if response["acknowledged"] is not True:
            raise _invalid_requirement_response("Information tasks must be acknowledged")
        return {"acknowledged": True}
    if interaction_type == "approval":
        _response_keys(response, required={"approved"})
        if response["approved"] is not True:
            raise _invalid_requirement_response("Approval tasks require explicit approval")
        return {"approved": True}
    if interaction_type in {"form", "selection_flow"}:
        _response_keys(response, required={"values"})
        if not isinstance(response["values"], dict):
            raise _invalid_requirement_response("Form values must be an object")
        fields = input_config.get(
            "fields" if interaction_type == "form" else "flow",
            [],
        )
        return {"values": _validate_configured_values(response["values"], fields)}
    if interaction_type == "single_select":
        _response_keys(response, required={"selectedOption"})
        selected = response["selectedOption"]
        if not isinstance(selected, str) or selected not in _string_options(
            input_config.get("options")
        ):
            raise _invalid_requirement_response("Choose one of the published options")
        return {"selectedOption": selected}
    if interaction_type == "multiple_select":
        _response_keys(response, required={"selectedOptions"})
        selected_value = response["selectedOptions"]
        if (
            not isinstance(selected_value, list)
            or not selected_value
            or not all(isinstance(item, str) for item in selected_value)
        ):
            raise _invalid_requirement_response("Choose one or more published options")
        selected = [str(item) for item in selected_value]
        options = _string_options(input_config.get("options"))
        maximum = input_config.get("maximumSelections")
        if len(selected) != len(set(selected)) or any(item not in options for item in selected):
            raise _invalid_requirement_response("The response contains invalid selections")
        if isinstance(maximum, int) and not isinstance(maximum, bool) and len(selected) > maximum:
            raise _invalid_requirement_response("The response contains too many selections")
        return {"selectedOptions": selected}
    if interaction_type == "signature":
        _response_keys(
            response,
            required={"accepted", "signerName"},
            optional={"signatureMethod"},
        )
        signer_name = response["signerName"]
        method = response.get("signatureMethod", "typed")
        if response["accepted"] is not True:
            raise _invalid_requirement_response("The signature must be explicitly accepted")
        if not isinstance(signer_name, str) or not signer_name.strip() or len(signer_name) > 160:
            raise _invalid_requirement_response("A valid signer name is required")
        if method not in {"typed", "drawn"}:
            raise _invalid_requirement_response("The signature method is invalid")
        return {
            "accepted": True,
            "signerName": signer_name.strip(),
            "signatureMethod": method,
        }
    if interaction_type == "scheduling":
        _response_keys(response, required={"appointmentId"})
        appointment_id = response["appointmentId"]
        try:
            normalized_id = str(UUID(str(appointment_id)))
        except ValueError as error:
            raise _invalid_requirement_response("A valid appointmentId is required") from error
        return {"appointmentId": normalized_id}
    raise ConflictError(
        "REQUIREMENT_SPECIALIZED_SUBMISSION_REQUIRED",
        "This task uses a specialized document or payment submission flow",
    )


def _map_requirement(row: Mapping[str, Any]) -> JsonDict:
    code = str(row["code"])
    item: JsonDict = {
        "id": str(row["id"]),
        "slug": REQUIREMENT_SLUGS.get(code, code.lower().replace("_", "-")),
        "journeyId": str(row["journey_id"]),
        "code": code,
        "title": row["title"],
        "description": row["description"],
        "status": row["status"],
        "version": int(row.get("version") or 1),
        "blocking": bool(row["blocking"]),
        "priority": int(row.get("priority") or 0),
        "dueAt": _nullable_iso(row.get("due_at")),
        "progressPercent": int(row["progress_percent"]),
        "submissionType": row["submission_type"],
        "flowKind": row.get("flow_kind") or "enrollment",
        "interactionType": row.get("interaction_type")
        or _interaction_type_for_submission(str(row["submission_type"])),
        "inputConfig": _mapping(row.get("input_config") or {}),
        "order": int(row.get("display_order") or 0),
        "documentCategory": DOCUMENT_CATEGORIES.get(code),
        "responsibleOffice": row["responsible_office"],
        "dependencyCodes": _list(row["depends_on_codes"]),
    }
    points = int(row.get("reward_points") or 0)
    if points > 0:
        item["reward"] = {"points": points, "earned": row.get("reward_earned") is True}
    if row.get("response_id") is not None:
        item["response"] = {
            "id": str(row["response_id"]),
            "interactionType": row.get("response_interaction_type") or item["interactionType"],
            "data": _mapping(row.get("response_data") or {}),
            "version": int(row.get("response_version") or 1),
            "submittedAt": _iso(row["response_submitted_at"]),
        }
    return item


def _map_message(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "subject": row["subject"],
        "body": row["body"],
        "senderName": row["sender_name"],
        "kind": row.get("kind") or "general",
        "href": row.get("href"),
        "sentAt": _iso(row["sent_at"]),
        "readAt": _nullable_iso(row["read_at"]),
    }


def _map_document(row: Mapping[str, Any]) -> JsonDict:
    item: JsonDict = {
        "id": str(row["id"]),
        "fileName": row["file_name"],
        "mimeType": row["mime_type"],
        "sizeBytes": int(row["size_bytes"]),
        "category": row["category"],
        "processingMode": row["processing_mode"],
        "status": row["status"],
        "createdAt": _iso(row["created_at"]),
    }
    if row.get("requirement_id"):
        item["requirementId"] = str(row["requirement_id"])
    if row.get("storage_key"):
        item["contentUrl"] = f"/v1/student/documents/{row['id']}/content"
    if row.get("sha256"):
        item["sha256"] = str(row["sha256"])
    if row.get("extraction"):
        item["extraction"] = _mapping(row["extraction"])
    return item


def _map_signed_document(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "fileName": row["file_name"],
        "mimeType": "application/pdf",
        "sizeBytes": int(row["size_bytes"]),
        "category": "other",
        "processingMode": "generated",
        "status": "accepted",
        "contentUrl": f"/v1/student/documents/{row['id']}/content",
        "sha256": str(row["sha256"]),
        "signature": {
            "templateCode": row["template_code"],
            "title": row["title"],
            "signerName": row["signer_name"],
            "method": row["signature_method"],
            "signedAt": _iso(row["signed_at"]),
            "onboardingVersion": int(row["onboarding_version"]),
        },
        "createdAt": _iso(row["created_at"]),
    }


def _map_profile(row: Mapping[str, Any]) -> JsonDict:
    item: JsonDict = {
        "studentId": str(row["student_id"]),
        "preferredName": row["preferred_name"],
        "pronouns": row.get("pronouns"),
        "mobilePhone": row.get("mobile_phone"),
        "communicationPreference": row["communication_preference"],
        "version": int(row["version"]),
        "updatedAt": _iso(row["updated_at"]),
    }
    if row.get("first_name"):
        item["firstName"] = row["first_name"]
    if row.get("last_name"):
        item["lastName"] = row["last_name"]
    if row.get("email"):
        item.update(
            {
                "email": row["email"],
                "emailVerified": row.get("email_verified") is True,
                "phoneVerified": row.get("phone_verified") is True,
            }
        )
    return item


def _map_appointment(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "type": row["type"],
        "startsAt": _iso(row["starts_at"]),
        "notes": row.get("notes"),
        "status": row["status"],
        "createdAt": _iso(row["created_at"]),
    }


def _map_payment(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "offerId": str(row["offer_id"]),
        "type": "enrollment_deposit",
        "amountCents": int(row["amount_cents"]),
        "status": row["status"],
        "processor": "dummy",
        "processorReference": row["processor_reference"],
        "createdAt": _iso(row["created_at"]),
    }


def _map_help_request(row: Mapping[str, Any]) -> JsonDict:
    item = {
        "id": str(row["id"]),
        "topicCode": row["topic_code"],
        "subject": row["subject"],
        "message": row["message"],
        "status": row["status"],
        "priority": row["priority"],
        "assigneeId": str(row["assignee_id"]) if row.get("assignee_id") else None,
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
        "version": int(row["version"]),
    }
    if row.get("requirement_id") is not None:
        item["requirementId"] = str(row["requirement_id"])
    if row.get("work_item_id") is not None:
        item["workItemId"] = str(row["work_item_id"])
    return item


def _map_source(row: Mapping[str, Any]) -> JsonDict | None:
    if not row.get("source_label") or not row.get("source_url") or not row.get("source_status"):
        return None
    return {
        "label": row["source_label"],
        "url": row["source_url"],
        "dataStatus": row["source_status"],
    }


def _map_course(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "code": row["code"],
        "title": row["title"],
        "description": row["description"],
        "credits": float(row["credits"]),
        "level": int(row["level"]),
        "prerequisites": _list(row.get("prerequisites")),
        "availabilityLabel": row.get("availability_label"),
        "instructorNames": _list(row.get("instructor_names")),
        "meetingPattern": row.get("meeting_pattern"),
        "resources": _list(row.get("resources")),
        "source": _map_source(row),
    }


def _map_program(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "code": row["code"],
        "name": row["name"],
        "degree": row["degree"],
        "totalCredits": int(row["total_credits"]),
        "description": row["description"],
        "source": _map_source(row),
    }


class PostgresPortalRepository:
    """Tenant-scoped SQLAlchemy Core repository for every portal store method."""

    def __init__(self, engine: AsyncEngine, outbox: OutboxRepository | None = None) -> None:
        self.engine = engine
        self.outbox = outbox or OutboxRepository(
            engine, OutboxRepositoryConfig(worker_id="audentra-api")
        )

    async def _all(self, statement: str, params: Mapping[str, Any]) -> list[JsonDict]:
        async with self.engine.connect() as connection:
            result = await connection.execute(text(statement), dict(params))
            return [dict(row) for row in result.mappings().all()]

    async def _one(self, statement: str, params: Mapping[str, Any]) -> JsonDict | None:
        rows = await self._all(statement, params)
        return rows[0] if rows else None

    async def get_student_bootstrap(self, auth: AuthContext) -> JsonDict:
        async with self.engine.begin() as connection:
            await self._reconcile_authoritative_rewards(connection, auth)
        row = await self._one(
            """
            SELECT s.id AS student_id,
                   COALESCE(sp.preferred_name, p.preferred_name, p.first_name) AS preferred_name,
                   p.first_name || ' ' || p.last_name AS full_name,
                   so.status, so.current_step, so.version,
                   COALESCE(
                     (SELECT definition.onboarding_required
                      FROM enrollment_journey journey
                      JOIN journey_definition_version definition
                        ON definition.id=journey.journey_definition_version_id
                       AND definition.tenant_id=journey.tenant_id
                      WHERE journey.tenant_id=s.tenant_id
                        AND journey.student_id=s.id
                        AND journey.status<>'cancelled'
                      ORDER BY journey.created_at DESC
                      LIMIT 1),
                     (SELECT definition.onboarding_required
                      FROM journey_definition_version definition
                      WHERE definition.tenant_id=s.tenant_id
                        AND definition.active=1
                      ORDER BY definition.version DESC
                      LIMIT 1),
                     true
                   ) AS configured_onboarding_required,
                   (SELECT COUNT(*)::integer FROM student_message message
                    WHERE message.tenant_id = s.tenant_id
                      AND message.student_id = s.id AND message.read_at IS NULL)
                     AS unread_message_count
            FROM student s
            JOIN person p ON p.id = s.person_id AND p.tenant_id = s.tenant_id
            JOIN student_onboarding so
              ON so.student_id = s.id AND so.tenant_id = s.tenant_id
            LEFT JOIN student_profile sp
              ON sp.student_id = s.id AND sp.tenant_id = s.tenant_id
            WHERE s.tenant_id = :tenant_id AND s.id = :student_id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if row is None:
            raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
        required = row["configured_onboarding_required"] is True and row["status"] != "completed"
        response: JsonDict = {
            "authenticated": True,
            "student": {
                "id": str(row["student_id"]),
                "preferredName": row["preferred_name"],
                "fullName": row["full_name"],
            },
            "onboarding": {
                "required": required,
                "status": row["status"],
                "currentStep": row["current_step"],
                "version": int(row["version"]),
            },
            "unreadMessageCount": int(row["unread_message_count"]),
            "initialRoute": "/onboarding" if required else "/dashboard",
            "generatedAt": _iso(_utc_now()),
        }
        rewards = await self._get_reward_summary(auth)
        if rewards:
            response["rewards"] = rewards
        return response

    async def get_student_onboarding(self, auth: AuthContext) -> JsonDict:
        row = await self._one(
            """
            SELECT student_id, status, current_step, completed_steps, payload,
                   version, completed_at, updated_at
            FROM student_onboarding
            WHERE tenant_id = :tenant_id AND student_id = :student_id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if row is None:
            raise NotFoundError("STUDENT_ONBOARDING_NOT_FOUND", "Student onboarding was not found")
        mapped = _map_onboarding(row)
        configuration_row = await self._one(
            """
            SELECT version, document
            FROM staff_managed_configuration_version
            WHERE tenant_id=:tenant_id AND kind='journeys' AND active=true
            ORDER BY version DESC LIMIT 1
            """,
            {"tenant_id": auth.tenant_id},
        )
        if configuration_row is not None:
            mapped["configurationVersion"] = int(configuration_row["version"])
            mapped["screenConfigurations"] = _onboarding_screen_configurations(
                _mapping(configuration_row["document"])
            )
        return mapped

    async def get_student_housing_plan(self, auth: AuthContext) -> JsonDict:
        onboarding = await self._one(
            """
            SELECT student_id, status, current_step, completed_steps, payload,
                   version, completed_at, updated_at
            FROM student_onboarding
            WHERE tenant_id = :tenant_id AND student_id = :student_id
            LIMIT 1
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if onboarding is None:
            raise NotFoundError("STUDENT_ONBOARDING_NOT_FOUND", "Student onboarding was not found")
        rows = await self._all(
            """
            SELECT residence.id, residence.code, residence.name, residence.description,
                   residence.amenities, media.public_path, media.alt_text,
                   media.attribution, media.source_url
            FROM housing_residence_option residence
            JOIN media_asset media ON media.id = residence.media_asset_id
              AND media.tenant_id = residence.tenant_id AND media.active = true
            WHERE residence.tenant_id = :tenant_id AND residence.active = true
            ORDER BY residence.display_order, residence.id
            """,
            {"tenant_id": auth.tenant_id},
        )
        residences = [
            {
                "id": str(row["id"]),
                "value": row["code"],
                "name": row["name"],
                "description": row["description"],
                "amenities": _list(row["amenities"]),
                "imageUrl": row["public_path"],
                "imageAlt": row["alt_text"],
                "attribution": row["attribution"],
                "sourceUrl": row["source_url"],
            }
            for row in rows
        ]
        return _map_housing(onboarding, residences)

    async def update_student_housing_plan(
        self, auth: AuthContext, update: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            current = await self._locked_onboarding(connection, auth)
            if int(current["version"]) != update["expectedVersion"]:
                raise ConflictError(
                    "VERSION_CONFLICT", "Your housing plan changed in another session"
                )
            residence = (
                update.get("residenceOption") if update["preference"] == "on_campus" else None
            )
            payload = _mapping(current["payload"])
            payload.update(
                {"housingPreference": update["preference"], "housingResidenceOption": residence}
            )
            changed_fields = ["preference", "residenceOption"]
            for api_field, payload_field in _HOUSING_PLAN_FIELD_BINDINGS.items():
                if api_field in update:
                    payload[payload_field] = update[api_field]
                    changed_fields.append(api_field)
            result = await connection.execute(
                text(
                    """
                    UPDATE student_onboarding SET payload = CAST(:payload AS jsonb),
                      version = version + 1, updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                      AND version = :expected_version
                    RETURNING student_id, status, current_step, completed_steps, payload,
                              version, completed_at, updated_at
                    """
                ),
                {
                    "payload": _json(payload),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "expected_version": update["expectedVersion"],
                },
            )
            row = result.mappings().first()
            if row is None:
                raise ConflictError(
                    "VERSION_CONFLICT", "Your housing plan changed in another session"
                )
            updated = dict(row)
            await self._complete_requirement(connection, auth, "housing_preference")
            await self._insert_audit(
                connection,
                auth,
                "student_housing_plan.updated",
                "student_onboarding",
                auth.student_id,
                request_id,
                {
                    "preference": update["preference"],
                    "residenceOption": residence,
                    "changedFields": changed_fields,
                    "version": int(updated["version"]),
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.housing_plan_updated.v1",
                "student_onboarding",
                auth.student_id,
                int(updated["version"]),
                request_id,
                {
                    "studentId": auth.student_id,
                    "preference": update["preference"],
                    "residenceOption": residence,
                },
            )
            return _map_housing(updated)

    async def update_student_onboarding(
        self, auth: AuthContext, update: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            current = await self._locked_onboarding(connection, auth)
            if current["status"] == "completed":
                raise ConflictError(
                    "ONBOARDING_ALREADY_COMPLETED", "Completed onboarding cannot be changed"
                )
            if int(current["version"]) != update["expectedVersion"]:
                raise ConflictError("VERSION_CONFLICT", "Onboarding changed in another session")
            target = str(update["currentStep"])
            active = str(current["current_step"])
            completed = [str(item) for item in _list(current["completed_steps"])]
            if (
                target not in ONBOARDING_STEPS
                or active not in ONBOARDING_STEPS
                or (target != active and target not in completed)
                or ONBOARDING_STEPS.index(target) > ONBOARDING_STEPS.index(active)
            ):
                raise ConflictError(
                    "ONBOARDING_STEP_OUT_OF_ORDER",
                    f"The next required onboarding step is {active}",
                )
            expected_prior = list(ONBOARDING_STEPS[: ONBOARDING_STEPS.index(active)])
            if completed != expected_prior:
                raise ApiError(
                    500,
                    "ONBOARDING_STATE_INVALID",
                    "The stored onboarding sequence is inconsistent",
                )
            skip = update.get("skip") is True
            if skip and not is_skippable_onboarding_step(target):
                raise BadRequestError(
                    "ONBOARDING_STEP_REQUIRED",
                    "This onboarding step is required before you can continue",
                )
            submitted = dict(update.get("data", {}))
            submitted.pop("skippedSteps", None)
            merged = {**_mapping(current["payload"]), **submitted}
            skipped = list(_mapping(current["payload"]).get("skippedSteps", []))
            if skip and target not in skipped:
                skipped.append(target)
            if not skip:
                skipped = [step for step in skipped if step != target]
                await self._validate_onboarding_step(connection, auth, target, merged)
            merged["skippedSteps"] = skipped
            synchronized_profile_version: int | None = None
            if target == "about_you" and all(
                merged.get(field)
                for field in (
                    "firstName",
                    "lastName",
                    "preferredName",
                    "mobilePhone",
                    "communicationPreference",
                )
            ):
                await connection.execute(
                    text(
                        """
                        UPDATE person p SET first_name=:first_name, last_name=:last_name,
                          preferred_name=:preferred_name, updated_at=NOW()
                        FROM student s WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                          AND p.tenant_id=s.tenant_id AND p.id=s.person_id
                        """
                    ),
                    {
                        "first_name": merged["firstName"],
                        "last_name": merged["lastName"],
                        "preferred_name": merged["preferredName"],
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
                profile_result = await connection.execute(
                    text(
                        """
                        UPDATE student_profile SET preferred_name=:preferred_name,
                          mobile_phone=:mobile_phone,
                          communication_preference=:communication_preference,
                          version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                        RETURNING version
                        """
                    ),
                    {
                        "preferred_name": merged["preferredName"],
                        "mobile_phone": merged["mobilePhone"],
                        "communication_preference": merged["communicationPreference"],
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
                profile_row = profile_result.mappings().first()
                synchronized_profile_version = (
                    int(profile_row["version"]) if profile_row is not None else None
                )
                await self._complete_requirement(connection, auth, "profile_verification")
            advancing = target == active
            next_step = (
                ONBOARDING_STEPS[min(ONBOARDING_STEPS.index(active) + 1, len(ONBOARDING_STEPS) - 1)]
                if advancing
                else active
            )
            next_completed = [*completed, active] if advancing else completed
            result = await connection.execute(
                text(
                    """
                    UPDATE student_onboarding SET status='in_progress', current_step=:next_step,
                      completed_steps=CAST(:completed_steps AS text[]),
                      payload=CAST(:payload AS jsonb), version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                    RETURNING student_id, status, current_step, completed_steps, payload,
                              version, completed_at, updated_at
                    """
                ),
                {
                    "next_step": next_step,
                    "completed_steps": next_completed,
                    "payload": _json(merged),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise ApiError(500, "ONBOARDING_UPDATE_FAILED", "Onboarding could not be saved")
            updated = dict(row)
            await self._insert_audit(
                connection,
                auth,
                "student_onboarding.step_completed"
                if advancing
                else "student_onboarding.step_updated",
                "student_onboarding",
                auth.student_id,
                request_id,
                {
                    "step": target,
                    "skipped": skip,
                    "version": int(updated["version"]),
                    "profileSynchronized": synchronized_profile_version is not None,
                },
            )
            if synchronized_profile_version is not None:
                await self._insert_outbox(
                    connection,
                    auth,
                    "student.profile_updated.v1",
                    "student_profile",
                    auth.student_id,
                    synchronized_profile_version,
                    request_id,
                    {
                        "studentId": auth.student_id,
                        "source": "onboarding.about_you",
                        "changedFields": [
                            "firstName",
                            "lastName",
                            "preferredName",
                            "mobilePhone",
                            "communicationPreference",
                        ],
                    },
                )
            return _map_onboarding(updated)

    async def complete_student_onboarding(
        self,
        auth: AuthContext,
        update: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            current = await self._locked_onboarding(connection, auth)
            if current["status"] == "completed":
                return _map_onboarding(current)
            if int(current["version"]) != update["expectedVersion"]:
                raise ConflictError("VERSION_CONFLICT", "Onboarding changed in another session")
            completed = [str(item) for item in _list(current["completed_steps"])]
            payload = _mapping(current["payload"])
            if completed != list(ONBOARDING_STEPS) or any(
                not is_skippable_onboarding_step(str(step))
                for step in payload.get("skippedSteps", [])
            ):
                raise ConflictError(
                    "ONBOARDING_INCOMPLETE",
                    "Every required onboarding step must be completed in order",
                )
            result = await connection.execute(
                text(
                    """
                    UPDATE student_onboarding SET status='completed', completed_at=NOW(),
                      version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                    RETURNING student_id, status, current_step, completed_steps, payload,
                              version, completed_at, updated_at
                    """
                ),
                {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
            )
            row = result.mappings().first()
            if row is None:
                raise ApiError(
                    500,
                    "ONBOARDING_COMPLETION_FAILED",
                    "Onboarding could not be completed",
                )
            mapped = _map_onboarding(dict(row))
            await self._award_rewards(
                connection,
                auth,
                "onboarding_completed",
                "onboarding",
                auth.student_id,
                {},
            )
            completed_journeys = await connection.execute(
                text(
                    """
                    UPDATE enrollment_journey journey
                    SET status='completed', version=journey.version+1, updated_at=NOW()
                    WHERE journey.tenant_id=:tenant_id
                      AND journey.student_id=:student_id
                      AND journey.status NOT IN ('completed','cancelled')
                      AND NOT EXISTS (
                        SELECT 1 FROM student_requirement requirement
                        WHERE requirement.tenant_id=journey.tenant_id
                          AND requirement.journey_id=journey.id
                          AND requirement.retired_at IS NULL
                          AND requirement.status NOT IN (
                            'not_applicable','completed','waived','expired'
                          )
                      )
                    RETURNING journey.id
                    """
                ),
                {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
            )
            if completed_journeys.mappings().first() is not None:
                await self._insert_student_message(
                    connection,
                    auth,
                    subject="Enrollment complete",
                    body="All required enrollment tasks are complete.",
                    kind="enrollment_completed",
                    href="/dashboard",
                )
            await self._insert_audit(
                connection,
                auth,
                "student_onboarding.completed",
                "student_onboarding",
                auth.student_id,
                request_id,
                {"version": mapped["version"]},
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.onboarding_completed.v1",
                "student_onboarding",
                auth.student_id,
                int(mapped["version"]),
                request_id,
                {"studentId": auth.student_id},
            )
            await self._insert_student_message(
                connection,
                auth,
                subject="Onboarding complete",
                body="Your onboarding checklist is complete. You can continue from your dashboard.",
                kind="onboarding",
                href="/dashboard",
            )
            return mapped

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_onboarding.complete",
            update,
            200,
            handler,
        )

    async def claim_student_document_processing(
        self,
        auth: AuthContext,
        document_id: str,
        *,
        retry: bool = False,
        request_id: str | None = None,
        retry_idempotency_key: str | None = None,
    ) -> bool:
        processing: JsonDict = {
            "status": "processing",
            "documentType": "other",
            "summary": (
                "The original file is safely stored. Edward is preparing a reviewable record."
            ),
            "studentName": None,
            "institutionName": None,
            "issueDate": None,
            "academicTerm": None,
            "fields": [],
            "courses": [],
            "warnings": [],
            "model": None,
            "provider": "local",
            "processedAt": None,
            "verifiedAt": None,
        }
        if retry:
            if not retry_idempotency_key:
                raise BadRequestError(
                    "IDEMPOTENCY_KEY_REQUIRED", "The Idempotency-Key header is required"
                )
            operation = f"student_document.extraction.retry:{document_id}"
            request_hash = self._request_hash(auth, {"documentId": document_id})
            lock_key = f"{auth.tenant_id}:{auth.actor_id}:{operation}:{retry_idempotency_key}"
            async with self.engine.begin() as connection:
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": lock_key},
                )
                existing = await connection.execute(
                    text(
                        """
                        SELECT request_hash, response_body FROM idempotency_record
                        WHERE tenant_id=:tenant_id AND actor_id=:actor_id
                          AND operation=:operation AND idempotency_key=:idempotency_key
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "actor_id": auth.actor_id,
                        "operation": operation,
                        "idempotency_key": retry_idempotency_key,
                    },
                )
                replay = existing.mappings().first()
                if replay is not None:
                    if replay["request_hash"] != request_hash:
                        raise ConflictError(
                            "IDEMPOTENCY_KEY_REUSED",
                            "This idempotency key was already used for a different request",
                        )
                    return False
                result = await connection.execute(
                    text(
                        """
                        UPDATE document_record SET status='processing',
                          extraction=CAST(:processing AS jsonb), updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND id=:document_id AND status IN ('uploaded','needs_review')
                          AND extraction IS NOT NULL AND (
                            extraction->>'status'='pending_configuration' OR (
                              extraction->>'status'='failed'
                              AND COALESCE(extraction->'retryable','true'::jsonb)<>'false'::jsonb
                            )
                          )
                        RETURNING id
                        """
                    ),
                    {
                        "processing": _json(processing),
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "document_id": document_id,
                    },
                )
                if result.mappings().first() is None:
                    return False
                await connection.execute(
                    text(
                        """
                        INSERT INTO idempotency_record (
                          tenant_id, actor_id, operation, idempotency_key, request_hash,
                          response_status, response_body, created_at, expires_at
                        ) VALUES (
                          :tenant_id, :actor_id, :operation, :idempotency_key,
                          :request_hash, 202, CAST(:response_body AS jsonb), NOW(),
                          NOW()+INTERVAL '24 hours'
                        )
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "actor_id": auth.actor_id,
                        "operation": operation,
                        "idempotency_key": retry_idempotency_key,
                        "request_hash": request_hash,
                        "response_body": _json({"documentId": document_id, "status": "processing"}),
                    },
                )
                correlation = request_id or "document-extraction-retry"
                await self._insert_audit(
                    connection,
                    auth,
                    "document.extraction_retry_started",
                    "document_record",
                    document_id,
                    correlation,
                    {},
                )
                await self._insert_outbox(
                    connection,
                    auth,
                    "document.extraction_retry_started.v1",
                    "document_record",
                    document_id,
                    2,
                    correlation,
                    {"studentId": auth.student_id},
                )
                await self._insert_outbox(
                    connection,
                    auth,
                    "document.extraction_requested.v1",
                    "document_record",
                    document_id,
                    3,
                    correlation,
                    {"studentId": auth.student_id, "retry": True},
                )
                return True

        async with self.engine.begin() as connection:
            correlation = request_id or "document-extraction-request"
            result = await connection.execute(
                text(
                    """
                    UPDATE document_record SET status='under_review', updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id AND status='uploaded' AND extraction IS NULL
                      AND processing_mode='manual_review'
                    RETURNING requirement_id, category
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            manual = result.mappings().first()
            if manual is not None:
                if manual["requirement_id"]:
                    await connection.execute(
                        text(
                            """
                            UPDATE student_requirement SET status='under_review',
                              progress_percent=80, updated_at=NOW()
                            WHERE tenant_id=:tenant_id AND id=:requirement_id
                              AND status<>'completed'
                            """
                        ),
                        {
                            "tenant_id": auth.tenant_id,
                            "requirement_id": manual["requirement_id"],
                        },
                    )
                if manual["category"] == "financial_aid":
                    await connection.execute(
                        text(
                            """
                            UPDATE financial_document_requirement
                            SET status='under_review', document_id=:document_id,
                              version=version+1, updated_at=NOW()
                            WHERE tenant_id=:tenant_id AND student_id=:student_id
                              AND code='verification_worksheet'
                            """
                        ),
                        {
                            "document_id": document_id,
                            "tenant_id": auth.tenant_id,
                            "student_id": auth.student_id,
                        },
                    )
                await self._insert_audit(
                    connection,
                    auth,
                    "document.stored_for_review",
                    "document_record",
                    document_id,
                    correlation,
                    {
                        "storageConfirmed": True,
                        "processingMode": "manual_review",
                        "category": manual["category"],
                    },
                )
                await self._insert_outbox(
                    connection,
                    auth,
                    "document.stored_for_review.v1",
                    "document_record",
                    document_id,
                    2,
                    correlation,
                    {
                        "studentId": auth.student_id,
                        "category": manual["category"],
                        "requirementId": None
                        if manual["requirement_id"] is None
                        else str(manual["requirement_id"]),
                    },
                )
                return False
            result = await connection.execute(
                text(
                    """
                    UPDATE document_record SET status='processing',
                      extraction=CAST(:processing AS jsonb), updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id AND status='uploaded' AND extraction IS NULL
                      AND processing_mode IN ('agentic','classification_only')
                    RETURNING id
                    """
                ),
                {
                    "processing": _json(processing),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            if result.mappings().first() is None:
                return False
            await self._insert_audit(
                connection,
                auth,
                "document.extraction_queued",
                "document_record",
                document_id,
                correlation,
                {"storageConfirmed": True},
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.extraction_requested.v1",
                "document_record",
                document_id,
                2,
                correlation,
                {"studentId": auth.student_id, "retry": False},
            )
            return True

    async def release_student_document_processing(
        self, auth: AuthContext, document_id: str, request_id: str
    ) -> None:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE document_record SET status='uploaded', extraction=NULL,
                      updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id AND status='processing'
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            if result.mappings().first() is None:
                raise ConflictError(
                    "DOCUMENT_PROCESSING_STATE_CHANGED",
                    "The document processing state changed before the upload could be retried",
                )
            await self._insert_audit(
                connection,
                auth,
                "document.storage_failed",
                "document_record",
                document_id,
                request_id,
                {"retryable": True},
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.storage_failed.v1",
                "document_record",
                document_id,
                2,
                request_id,
                {"studentId": auth.student_id, "retryable": True},
            )

    async def complete_student_document_extraction(
        self,
        auth: AuthContext,
        document_id: str,
        extraction: Mapping[str, Any],
        request_id: str,
        retry_idempotency_key: str | None = None,
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            extraction_data = dict(extraction)
            completed = extraction_data.get("status") == "completed"
            # A failed extraction is still a reviewable stored original. Keep it
            # out of the upload queue and expose an explicit retry/human-review
            # state without deleting or replacing the original object.
            status = "needs_review"
            inferred = self._category_for_document_type(str(extraction_data.get("documentType")))
            result = await connection.execute(
                text(
                    f"""
                    UPDATE document_record SET status=:status,
                      category=CASE WHEN category='other' THEN :inferred ELSE category END,
                      extraction=CAST(:extraction AS jsonb), updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id AND status='processing'
                    RETURNING {DOCUMENT_SELECT}
                    """
                ),
                {
                    "status": status,
                    "inferred": inferred,
                    "extraction": _json(extraction_data),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise ConflictError(
                    "DOCUMENT_PROCESSING_STATE_CHANGED",
                    "The document processing state changed before completion",
                )
            updated = dict(row)
            classification_matches = (
                not updated.get("requirement_id")
                or self._category_for_document_type(str(extraction_data.get("documentType")))
                == updated["category"]
            )
            linked_requirement: Mapping[str, Any] | None = None
            candidate_requirement_ids: list[str] = []
            if updated.get("requirement_id") and (not completed or classification_matches):
                candidate_requirement_ids.append(str(updated["requirement_id"]))
            if completed and not updated.get("requirement_id"):
                candidate_requirement_ids.extend(
                    str(match.get("targetId"))
                    for match in _list(extraction_data.get("contextMatches"))
                    if isinstance(match, Mapping)
                    and match.get("targetType") == "requirement"
                    and match.get("status") == "sufficient"
                    and isinstance(match.get("targetId"), str)
                )
            for requirement_id in dict.fromkeys(candidate_requirement_ids):
                requirement_result = await connection.execute(
                    text(
                        """
                        SELECT sr.id, sr.status, rdv.code, rdv.title
                        FROM student_requirement sr
                        JOIN enrollment_journey journey
                          ON journey.id=sr.journey_id AND journey.tenant_id=sr.tenant_id
                        JOIN requirement_definition_version rdv
                          ON rdv.id=sr.requirement_definition_version_id
                         AND rdv.tenant_id=sr.tenant_id
                        WHERE sr.tenant_id=:tenant_id
                          AND journey.student_id=:student_id
                          AND sr.id=:requirement_id
                          AND sr.retired_at IS NULL
                          AND rdv.submission_type='document'
                          AND rdv.interaction_type='upload_file'
                        LIMIT 1 FOR UPDATE OF sr
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "requirement_id": requirement_id,
                    },
                )
                candidate = requirement_result.mappings().first()
                if candidate is not None:
                    linked_requirement = dict(candidate)
                    break

            requirement_transitioned = False
            if linked_requirement is not None:
                linked_requirement_id = str(linked_requirement["id"])
                active_help_result = await connection.execute(
                    text(
                        """
                        SELECT 1
                        FROM student_inquiry
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND requirement_id=:requirement_id
                          AND status IN ('new','open','waiting_on_student')
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "requirement_id": linked_requirement_id,
                    },
                )
                has_active_help = active_help_result.mappings().first() is not None
                requirement_status = (
                    "under_review" if completed or not has_active_help else "help_requested"
                )
                requirement_update = await connection.execute(
                    text(
                        """
                        UPDATE student_requirement SET status=:status,
                          progress_percent=LEAST(80, GREATEST(progress_percent, 80)),
                          version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND id=:requirement_id
                          AND status IN (
                            'ready','help_requested','in_progress','rejected','under_review'
                          )
                          AND status IS DISTINCT FROM :status
                        RETURNING id
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "requirement_id": linked_requirement_id,
                        "status": requirement_status,
                    },
                )
                requirement_transitioned = requirement_update.mappings().first() is not None
                for raw_match in _list(extraction_data.get("contextMatches")):
                    if isinstance(raw_match, dict):
                        raw_match["applied"] = (
                            raw_match.get("targetType") == "requirement"
                            and raw_match.get("targetId") == linked_requirement_id
                            and requirement_transitioned
                        )
                linked_document = await connection.execute(
                    text(
                        f"""
                        UPDATE document_record SET
                          requirement_id=COALESCE(requirement_id, :requirement_id),
                          extraction=CAST(:extraction AS jsonb), updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND id=:document_id
                        RETURNING {DOCUMENT_SELECT}
                        """
                    ),
                    {
                        "requirement_id": linked_requirement_id,
                        "extraction": _json(extraction_data),
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "document_id": document_id,
                    },
                )
                linked_row = linked_document.mappings().first()
                if linked_row is not None:
                    updated = dict(linked_row)
                if requirement_transitioned:
                    await self._insert_student_message(
                        connection,
                        auth,
                        subject=(
                            f"{linked_requirement['title']} submitted for review"
                            if completed
                            else f"{linked_requirement['title']} needs staff review"
                        ),
                        body=(
                            (
                                "Your document matched this enrollment task. Your submission is "
                                "complete for now and the enrollment team will make the official "
                                "decision."
                            )
                            if completed
                            else (
                                "Your original file is safely stored, but automatic parsing did "
                                "not finish. Staff review has been requested; you may also retry "
                                "the stored file without uploading it again."
                            )
                        ),
                        kind=(
                            "requirement_under_review" if completed else "document_review_requested"
                        ),
                        href=(
                            "/enrollment/requirements/"
                            + REQUIREMENT_SLUGS.get(
                                str(linked_requirement["code"]),
                                str(linked_requirement["code"]).replace("_", "-"),
                            )
                        ),
                    )
            automatic_transcript = (
                updated["category"] == "transcript"
                and completed
                and classification_matches
                and extraction_data.get("documentType") == "transcript"
            )
            if automatic_transcript:
                await connection.execute(
                    text(
                        """
                        UPDATE document_record SET status='under_review', updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND id=:document_id
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "document_id": document_id,
                    },
                )
                updated["status"] = "under_review"
                await self._persist_transcript_credits(
                    connection, auth, document_id, extraction_data
                )
                await self._project_course_exemptions(
                    connection, auth, document_id, extraction_data
                )
                await self._insert_outbox(
                    connection,
                    auth,
                    "student.transcript_credits_imported.v1",
                    "document_record",
                    document_id,
                    3,
                    request_id,
                    {
                        "studentId": auth.student_id,
                        "courseCount": len(_list(extraction_data.get("courses"))),
                        "projection": "automatic",
                    },
                )
            if extraction_data.get("immunizationCompliance"):
                await self._persist_immunization_evaluation(
                    connection, auth, document_id, extraction_data
                )
            if updated["category"] == "financial_aid" and completed and classification_matches:
                await connection.execute(
                    text(
                        """
                        UPDATE financial_document_requirement SET status='under_review',
                          document_id=:document_id, version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND code='verification_worksheet'
                        """
                    ),
                    {
                        "document_id": document_id,
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
            recovered_help_count = 0
            recovered_review_count = 0
            if completed and classification_matches and linked_requirement is not None:
                recovered = await self._resolve_requirement_help_after_success(
                    connection,
                    auth=auth,
                    requirement_id=str(linked_requirement["id"]),
                    replacement_document_id=document_id,
                    request_id=request_id,
                )
                recovered_help_count = int(recovered["helpRequestsResolved"])
                recovered_review_count = int(recovered["reviewItemsResolved"])
            work_item_result = await self._ensure_document_review_work_item(
                connection,
                auth,
                updated,
                parse_failure=not completed,
                failure_code=(
                    str(extraction_data.get("failureCode"))
                    if extraction_data.get("failureCode") is not None
                    else None
                ),
                request_id=request_id,
            )
            work_item_created = bool(work_item_result["created"])
            await self._insert_audit(
                connection,
                auth,
                "document.extraction_completed",
                "document_record",
                document_id,
                request_id,
                {
                    "status": extraction_data.get("status"),
                    "provider": extraction_data.get("provider"),
                    "model": extraction_data.get("model"),
                    "extractedFieldCount": len(_list(extraction_data.get("fields"))),
                    "failureCode": extraction_data.get("failureCode"),
                    "retryable": extraction_data.get("retryable"),
                    "automaticallyProjectedTranscript": automatic_transcript,
                    "requirementTransitionedToReview": requirement_transitioned,
                    "staffWorkItemCreated": work_item_created,
                    "helpRequestsAutoResolved": recovered_help_count,
                    "parseReviewItemsAutoResolved": recovered_review_count,
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.extraction_completed.v1",
                "document_record",
                document_id,
                2,
                request_id,
                {
                    "studentId": auth.student_id,
                    "category": updated["category"],
                    "extractionStatus": extraction_data.get("status"),
                    "failureCode": extraction_data.get("failureCode"),
                    "retryable": extraction_data.get("retryable"),
                    "requirementId": (
                        str(updated["requirement_id"])
                        if updated.get("requirement_id") is not None
                        else None
                    ),
                    "staffReviewQueued": True,
                    "helpRequestsAutoResolved": recovered_help_count,
                    "parseReviewItemsAutoResolved": recovered_review_count,
                },
            )
            document = _map_document(updated)
            if retry_idempotency_key:
                await connection.execute(
                    text(
                        """
                        UPDATE idempotency_record SET response_status=200,
                          response_body=CAST(:response_body AS jsonb)
                        WHERE tenant_id=:tenant_id AND actor_id=:actor_id
                          AND operation=:operation AND idempotency_key=:idempotency_key
                        """
                    ),
                    {
                        "response_body": _json(document),
                        "tenant_id": auth.tenant_id,
                        "actor_id": auth.actor_id,
                        "operation": f"student_document.extraction.retry:{document_id}",
                        "idempotency_key": retry_idempotency_key,
                    },
                )
            return document

    async def get_student_document(self, auth: AuthContext, document_id: str) -> JsonDict:
        row = await self._one(
            f"""
            SELECT {DOCUMENT_SELECT} FROM document_record
            WHERE tenant_id=:tenant_id AND student_id=:student_id AND id=:document_id
            LIMIT 1
            """,
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "document_id": document_id,
            },
        )
        if row is None:
            raise NotFoundError("STUDENT_DOCUMENT_NOT_FOUND", "The document was not found")
        return _map_document(row)

    async def get_student_document_content_reference(
        self, auth: AuthContext, document_id: str
    ) -> JsonDict:
        row = await self._one(
            """
            SELECT storage_key, file_name, mime_type FROM (
              SELECT storage_key, file_name, mime_type, 1 AS priority
              FROM document_record WHERE tenant_id=:tenant_id
                AND student_id=:student_id AND id=:document_id
                AND storage_key IS NOT NULL
              UNION ALL
              SELECT storage_key, file_name, mime_type, 2 AS priority
              FROM student_signed_document WHERE tenant_id=:tenant_id
                AND student_id=:student_id AND id=:document_id
            ) reference ORDER BY priority LIMIT 1
            """,
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "document_id": document_id,
            },
        )
        if row is None:
            raise NotFoundError(
                "STUDENT_DOCUMENT_CONTENT_NOT_FOUND",
                "The uploaded document content was not found",
            )
        return {
            "storageKey": str(row["storage_key"]),
            "fileName": row["file_name"],
            "mimeType": row["mime_type"],
        }

    async def confirm_student_document_extraction(
        self,
        auth: AuthContext,
        document_id: str,
        confirmation: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            result = await connection.execute(
                text(
                    f"""
                    SELECT {DOCUMENT_SELECT} FROM document_record
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id FOR UPDATE
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_DOCUMENT_NOT_FOUND", "The document was not found")
            current = dict(row)
            extraction = _mapping(current.get("extraction"))
            if extraction.get("status") != "completed":
                raise ConflictError(
                    "DOCUMENT_EXTRACTION_NOT_READY",
                    "Document extraction is not ready for review",
                )
            available = {
                str(field.get("key"))
                for field in _list(extraction.get("fields"))
                if isinstance(field, dict)
            }
            accepted = list(dict.fromkeys(confirmation.get("acceptedFieldKeys", [])))
            if any(key not in available for key in accepted):
                raise BadRequestError(
                    "UNKNOWN_EXTRACTED_FIELD",
                    "One or more extracted fields do not belong to this document",
                )
            extraction.update({"acceptedFieldKeys": accepted, "verifiedAt": _iso(_utc_now())})
            result = await connection.execute(
                text(
                    f"""
                    UPDATE document_record SET status='under_review',
                      extraction=CAST(:extraction AS jsonb), updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:document_id RETURNING {DOCUMENT_SELECT}
                    """
                ),
                {
                    "extraction": _json(extraction),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                },
            )
            updated_row = result.mappings().first()
            if updated_row is None:
                raise ConflictError(
                    "DOCUMENT_PROCESSING_STATE_CHANGED",
                    "The document review state changed before confirmation",
                )
            projection = self._safe_profile_projection(_list(extraction.get("fields")), accepted)
            if projection:
                profile_result = await connection.execute(
                    text(
                        """
                        UPDATE student_profile SET
                          preferred_name=CASE WHEN :has_preferred
                            THEN :preferred ELSE preferred_name END,
                          pronouns=CASE WHEN :has_pronouns THEN :pronouns ELSE pronouns END,
                          mobile_phone=CASE WHEN :has_mobile THEN :mobile ELSE mobile_phone END,
                          communication_preference=CASE WHEN :has_preference
                            THEN :preference ELSE communication_preference END,
                          version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                        RETURNING student_id, preferred_name, pronouns, mobile_phone,
                          communication_preference, version, updated_at
                        """
                    ),
                    {
                        "has_preferred": "preferredName" in projection,
                        "preferred": projection.get("preferredName", ""),
                        "has_pronouns": "pronouns" in projection,
                        "pronouns": projection.get("pronouns"),
                        "has_mobile": "mobilePhone" in projection,
                        "mobile": projection.get("mobilePhone"),
                        "has_preference": "communicationPreference" in projection,
                        "preference": projection.get("communicationPreference", "email"),
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
                profile = profile_result.mappings().first()
                if profile is not None:
                    if "preferredName" in projection:
                        await connection.execute(
                            text(
                                """
                                UPDATE person p SET preferred_name=:preferred_name,
                                  updated_at=NOW() FROM student s
                                WHERE s.person_id=p.id AND s.tenant_id=p.tenant_id
                                  AND s.tenant_id=:tenant_id AND s.id=:student_id
                                """
                            ),
                            {
                                "preferred_name": profile["preferred_name"],
                                "tenant_id": auth.tenant_id,
                                "student_id": auth.student_id,
                            },
                        )
                    await self._insert_outbox(
                        connection,
                        auth,
                        "student.profile_updated.v1",
                        "student_profile",
                        auth.student_id,
                        int(profile["version"]),
                        request_id,
                        {
                            "studentId": auth.student_id,
                            "changedFields": list(projection),
                            "source": "confirmed_document_extraction",
                        },
                    )
            if extraction.get("documentType") == "transcript" and _list(extraction.get("courses")):
                await self._persist_transcript_credits(connection, auth, document_id, extraction)
                await self._project_course_exemptions(connection, auth, document_id, extraction)
                await self._insert_outbox(
                    connection,
                    auth,
                    "student.transcript_credits_imported.v1",
                    "document_record",
                    document_id,
                    3,
                    request_id,
                    {
                        "studentId": auth.student_id,
                        "courseCount": len(_list(extraction.get("courses"))),
                    },
                )
            await self._insert_audit(
                connection,
                auth,
                "document.extraction_confirmed",
                "document_record",
                document_id,
                request_id,
                {"acceptedFieldKeys": accepted, "projectedProfileFields": list(projection)},
            )
            return _map_document(dict(updated_row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            f"student_document.extraction.confirm:{document_id}",
            confirmation,
            200,
            handler,
        )

    async def get_student_requirements(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT sr.id, sr.journey_id, rdv.code, rdv.title, rdv.description,
                   sr.status, sr.version, rdv.blocking, sr.due_at, sr.progress_percent,
                   rdv.submission_type, rdv.responsible_office, rdv.depends_on_codes,
                   rdv.flow_kind, rdv.interaction_type, rdv.input_config,
                   rdv.priority, rdv.display_order,
                   submitted_response.id AS response_id,
                   submitted_response.interaction_type AS response_interaction_type,
                   submitted_response.response_data,
                   submitted_response.version AS response_version,
                   submitted_response.submitted_at AS response_submitted_at,
                   reward.reward_points, reward.reward_earned
            FROM student_requirement sr
            JOIN enrollment_journey j
              ON j.id=sr.journey_id AND j.tenant_id=sr.tenant_id
            JOIN requirement_definition_version evidence_definition
              ON evidence_definition.id=sr.requirement_definition_version_id
             AND evidence_definition.tenant_id=sr.tenant_id
            JOIN journey_requirement_definition current_link
              ON current_link.journey_definition_version_id=j.journey_definition_version_id
            JOIN requirement_definition_version rdv
              ON rdv.id=current_link.requirement_definition_version_id
             AND rdv.tenant_id=sr.tenant_id
             AND rdv.code=evidence_definition.code
            LEFT JOIN LATERAL (
              SELECT response.id, response.interaction_type, response.response_data,
                     response.version, response.submitted_at
              FROM student_requirement_response response
              WHERE response.tenant_id=sr.tenant_id
                AND response.requirement_id=sr.id
              ORDER BY response.version DESC
              LIMIT 1
            ) submitted_response ON true
            LEFT JOIN LATERAL (
              SELECT COALESCE(SUM(rr.points),0)::integer AS reward_points,
                     CASE WHEN COUNT(rr.id)=0 THEN false ELSE BOOL_AND(EXISTS (
                       SELECT 1 FROM student_reward_ledger ledger
                       WHERE ledger.tenant_id=sr.tenant_id
                         AND ledger.student_id=:student_id
                         AND ledger.reward_rule_id=rr.id
                         AND ledger.source_key=sr.id::text
                     )) END AS reward_earned
              FROM tenant_reward_rule rr
              WHERE rr.tenant_id=sr.tenant_id
                AND rr.trigger_type='requirement_completed'
                AND rr.trigger_key=rdv.code AND rr.enabled=true
                AND (rr.starts_at IS NULL OR rr.starts_at<=NOW())
                AND (rr.ends_at IS NULL OR rr.ends_at>NOW())
            ) reward ON true
            WHERE sr.tenant_id=:tenant_id AND j.student_id=:student_id
              AND sr.retired_at IS NULL
            ORDER BY CASE rdv.flow_kind WHEN 'onboarding' THEN 0 ELSE 1 END,
                     rdv.priority DESC, rdv.display_order, sr.created_at
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [_map_requirement(row) for row in rows]
        return {"items": items, "total": len(items)}

    async def get_student_requirement(self, auth: AuthContext, identifier: str) -> JsonDict:
        row = await self._one(
            """
            SELECT sr.id, sr.journey_id, rdv.code, rdv.title, rdv.description,
                   sr.status, sr.version, rdv.blocking, sr.due_at, sr.progress_percent,
                   rdv.submission_type, rdv.responsible_office, rdv.depends_on_codes,
                   rdv.flow_kind, rdv.interaction_type, rdv.input_config,
                   rdv.priority, rdv.display_order,
                   submitted_response.id AS response_id,
                   submitted_response.interaction_type AS response_interaction_type,
                   submitted_response.response_data,
                   submitted_response.version AS response_version,
                   submitted_response.submitted_at AS response_submitted_at,
                   reward.reward_points, reward.reward_earned
            FROM student_requirement sr
            JOIN enrollment_journey j
              ON j.id=sr.journey_id AND j.tenant_id=sr.tenant_id
            JOIN requirement_definition_version evidence_definition
              ON evidence_definition.id=sr.requirement_definition_version_id
             AND evidence_definition.tenant_id=sr.tenant_id
            JOIN journey_requirement_definition current_link
              ON current_link.journey_definition_version_id=j.journey_definition_version_id
            JOIN requirement_definition_version rdv
              ON rdv.id=current_link.requirement_definition_version_id
             AND rdv.tenant_id=sr.tenant_id
             AND rdv.code=evidence_definition.code
            LEFT JOIN LATERAL (
              SELECT response.id, response.interaction_type, response.response_data,
                     response.version, response.submitted_at
              FROM student_requirement_response response
              WHERE response.tenant_id=sr.tenant_id
                AND response.requirement_id=sr.id
              ORDER BY response.version DESC
              LIMIT 1
            ) submitted_response ON true
            LEFT JOIN LATERAL (
              SELECT COALESCE(SUM(rr.points),0)::integer AS reward_points,
                     CASE WHEN COUNT(rr.id)=0 THEN false ELSE BOOL_AND(EXISTS (
                       SELECT 1 FROM student_reward_ledger ledger
                       WHERE ledger.tenant_id=sr.tenant_id
                         AND ledger.student_id=:student_id
                         AND ledger.reward_rule_id=rr.id
                         AND ledger.source_key=sr.id::text
                     )) END AS reward_earned
              FROM tenant_reward_rule rr
              WHERE rr.tenant_id=sr.tenant_id
                AND rr.trigger_type='requirement_completed'
                AND rr.trigger_key=rdv.code AND rr.enabled=true
                AND (rr.starts_at IS NULL OR rr.starts_at<=NOW())
                AND (rr.ends_at IS NULL OR rr.ends_at>NOW())
            ) reward ON true
            WHERE sr.tenant_id=:tenant_id AND j.student_id=:student_id
              AND sr.retired_at IS NULL
              AND (sr.id::text=:identifier OR rdv.code=:requirement_code)
            LIMIT 1
            """,
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "identifier": identifier,
                "requirement_code": _requirement_code(identifier),
            },
        )
        if row is None:
            raise NotFoundError("STUDENT_REQUIREMENT_NOT_FOUND", "The requirement was not found")
        mapped = _map_requirement(row)
        if row["code"] != "immunization_record":
            return mapped
        policy = await self.get_immunization_policy_context(auth)
        if policy:
            version = policy["policyVersion"]
            mapped["immunizationPolicy"] = {
                "id": version["id"],
                "code": version["code"],
                "version": version["version"],
                "name": version["name"],
                "effectiveFrom": version["effectiveFrom"],
                "effectiveUntil": version["effectiveUntil"],
                "requirements": [
                    {
                        key: rule[key]
                        for key in (
                            "id",
                            "code",
                            "name",
                            "description",
                            "required",
                            "doseCount",
                            "validityDays",
                        )
                    }
                    for rule in policy["requirements"]
                ],
            }
        return mapped

    async def submit_student_requirement_response(
        self,
        auth: AuthContext,
        identifier: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        expected_version = payload.get("expectedVersion")
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 1
        ):
            raise BadRequestError("VALIDATION_ERROR", "expectedVersion must be a positive integer")

        async def handler(connection: AsyncConnection) -> JsonDict:
            result = await connection.execute(
                text(
                    """
                    SELECT sr.id, sr.journey_id, sr.status, sr.version, sr.due_at,
                           sr.progress_percent, rdv.id AS current_definition_id,
                           rdv.code, rdv.title, rdv.description, rdv.blocking,
                           rdv.submission_type, rdv.responsible_office,
                           rdv.depends_on_codes, rdv.flow_kind, rdv.interaction_type,
                           rdv.input_config, rdv.priority, rdv.display_order
                    FROM student_requirement sr
                    JOIN enrollment_journey journey
                      ON journey.id=sr.journey_id AND journey.tenant_id=sr.tenant_id
                    JOIN requirement_definition_version evidence_definition
                      ON evidence_definition.id=sr.requirement_definition_version_id
                     AND evidence_definition.tenant_id=sr.tenant_id
                    JOIN journey_requirement_definition current_link
                      ON current_link.journey_definition_version_id=
                         journey.journey_definition_version_id
                    JOIN requirement_definition_version rdv
                      ON rdv.id=current_link.requirement_definition_version_id
                     AND rdv.tenant_id=sr.tenant_id
                     AND rdv.code=evidence_definition.code
                    WHERE sr.tenant_id=:tenant_id AND journey.student_id=:student_id
                      AND sr.retired_at IS NULL
                      AND (sr.id::text=:identifier OR rdv.code=:requirement_code)
                    LIMIT 1
                    FOR UPDATE OF sr
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "identifier": identifier,
                    "requirement_code": _requirement_code(identifier),
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError(
                    "STUDENT_REQUIREMENT_NOT_FOUND",
                    "The requirement was not found",
                )
            current = dict(row)
            if int(current["version"]) != expected_version:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This requirement changed in another session",
                )
            status = str(current["status"])
            if status == "blocked":
                raise ConflictError(
                    "STUDENT_REQUIREMENT_BLOCKED",
                    "Complete the prerequisite tasks before responding",
                )
            if status in {
                "not_applicable",
                "submitted",
                "under_review",
                "completed",
                "waived",
                "expired",
            }:
                raise ConflictError(
                    "STUDENT_REQUIREMENT_NOT_ACTIONABLE",
                    "This requirement cannot accept a response in its current status",
                )
            interaction_type = str(current["interaction_type"])
            input_config = _mapping(current["input_config"])
            if (
                interaction_type == "signature"
                and input_config.get("signatureProvider") == "docusign"
            ):
                raise ConflictError(
                    "DOCUSIGN_EXECUTION_NOT_CONFIGURED",
                    "DocuSign execution is not configured for this environment",
                )
            response_data = _normalize_requirement_response(
                interaction_type,
                input_config,
                payload.get("response"),
            )
            if interaction_type == "scheduling":
                appointment = await connection.execute(
                    text(
                        """
                        SELECT 1 FROM student_appointment
                        WHERE id=:appointment_id AND tenant_id=:tenant_id
                          AND student_id=:student_id
                          AND status IN ('scheduled','rescheduled')
                        LIMIT 1
                        """
                    ),
                    {
                        "appointment_id": response_data["appointmentId"],
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
                if appointment.first() is None:
                    raise _invalid_requirement_response(
                        "The selected appointment is not active for this student"
                    )

            updated = await connection.execute(
                text(
                    """
                    UPDATE student_requirement
                    SET status='completed', progress_percent=100,
                        version=version+1, updated_at=NOW()
                    WHERE id=:requirement_id AND tenant_id=:tenant_id
                      AND version=:expected_version AND retired_at IS NULL
                    RETURNING version, updated_at
                    """
                ),
                {
                    "requirement_id": current["id"],
                    "tenant_id": auth.tenant_id,
                    "expected_version": expected_version,
                },
            )
            updated_row = updated.mappings().first()
            if updated_row is None:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This requirement changed in another session",
                )
            response_version_result = await connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(version),0)+1
                    FROM student_requirement_response
                    WHERE tenant_id=:tenant_id AND requirement_id=:requirement_id
                    """
                ),
                {"tenant_id": auth.tenant_id, "requirement_id": current["id"]},
            )
            response_version = int(response_version_result.scalar_one())
            response_id = str(uuid4())
            inserted_response = await connection.execute(
                text(
                    """
                    INSERT INTO student_requirement_response (
                      id, tenant_id, student_id, requirement_id,
                      requirement_definition_version_id, interaction_type,
                      response_data, version, submitted_at, created_at
                    ) VALUES (
                      :id, :tenant_id, :student_id, :requirement_id,
                      :definition_id, :interaction_type, CAST(:response_data AS jsonb),
                      :version, NOW(), NOW()
                    )
                    RETURNING submitted_at
                    """
                ),
                {
                    "id": response_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "requirement_id": current["id"],
                    "definition_id": current["current_definition_id"],
                    "interaction_type": interaction_type,
                    "response_data": _json(response_data),
                    "version": response_version,
                },
            )
            response_row = inserted_response.mappings().one()
            await self._award_rewards(
                connection,
                auth,
                "requirement_completed",
                str(current["code"]),
                str(current["id"]),
                {},
            )
            await self._insert_student_message(
                connection,
                auth,
                subject=f"{current['title']!s} complete",
                body="Your response was saved and this enrollment task is now complete.",
                kind="requirement_completed",
                href=(
                    "/enrollment/requirements/"
                    + REQUIREMENT_SLUGS.get(
                        str(current["code"]), str(current["code"]).replace("_", "-")
                    )
                ),
            )
            await connection.execute(
                text(
                    """
                    UPDATE student_requirement candidate SET status='ready',
                      version=candidate.version+1, updated_at=NOW()
                    FROM requirement_definition_version definition,
                         enrollment_journey journey
                    WHERE candidate.tenant_id=:tenant_id
                      AND candidate.journey_id=journey.id
                      AND journey.student_id=:student_id
                      AND candidate.requirement_definition_version_id=definition.id
                      AND candidate.retired_at IS NULL
                      AND candidate.status='blocked'
                      AND NOT EXISTS (
                        SELECT 1 FROM unnest(definition.depends_on_codes) dependency(code)
                        WHERE NOT EXISTS (
                          SELECT 1 FROM student_requirement prerequisite
                          JOIN requirement_definition_version prerequisite_definition
                            ON prerequisite_definition.id=
                               prerequisite.requirement_definition_version_id
                           AND prerequisite_definition.tenant_id=prerequisite.tenant_id
                          WHERE prerequisite.tenant_id=:tenant_id
                            AND prerequisite.journey_id=candidate.journey_id
                            AND prerequisite.retired_at IS NULL
                            AND prerequisite_definition.code=dependency.code
                            AND prerequisite.status IN (
                              'completed','waived','not_applicable'
                            )
                        )
                      )
                    """
                ),
                {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
            )
            await connection.execute(
                text(
                    """
                    UPDATE student_experience_update
                    SET status='acknowledged', acknowledged_at=NOW(),
                        version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND requirement_id=:requirement_id
                      AND status IN ('pending','deferred')
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "requirement_id": current["id"],
                },
            )
            completed_journey = await connection.execute(
                text(
                    """
                    UPDATE enrollment_journey journey
                    SET status='completed', version=journey.version+1, updated_at=NOW()
                    WHERE journey.id=:journey_id AND journey.tenant_id=:tenant_id
                      AND journey.status NOT IN ('completed','cancelled')
                      AND (
                        EXISTS (
                          SELECT 1 FROM journey_definition_version definition
                          WHERE definition.id=journey.journey_definition_version_id
                            AND definition.tenant_id=journey.tenant_id
                            AND NOT definition.onboarding_required
                        )
                        OR EXISTS (
                          SELECT 1 FROM student_onboarding onboarding
                          WHERE onboarding.tenant_id=journey.tenant_id
                            AND onboarding.student_id=journey.student_id
                            AND onboarding.status='completed'
                        )
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM student_requirement requirement
                        WHERE requirement.tenant_id=journey.tenant_id
                          AND requirement.journey_id=journey.id
                          AND requirement.retired_at IS NULL
                          AND requirement.status NOT IN (
                            'not_applicable','completed','waived','expired'
                          )
                      )
                    RETURNING journey.id
                    """
                ),
                {"journey_id": current["journey_id"], "tenant_id": auth.tenant_id},
            )
            if completed_journey.mappings().first() is not None:
                await self._insert_student_message(
                    connection,
                    auth,
                    subject="Enrollment complete",
                    body="All required enrollment tasks are complete.",
                    kind="enrollment_completed",
                    href="/dashboard",
                )
            updated_version = int(updated_row["version"])
            await self._insert_audit(
                connection,
                auth,
                "student_requirement.responded",
                "student_requirement",
                str(current["id"]),
                request_id,
                {
                    "interactionType": interaction_type,
                    "requirementVersion": updated_version,
                    "responseVersion": response_version,
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.requirement_responded.v1",
                "student_requirement",
                str(current["id"]),
                updated_version,
                request_id,
                {
                    "studentId": auth.student_id,
                    "journeyId": str(current["journey_id"]),
                    "requirementCode": str(current["code"]),
                    "interactionType": interaction_type,
                },
            )
            mapped_row = {
                **current,
                "status": "completed",
                "version": updated_version,
                "progress_percent": 100,
                "response_id": response_id,
                "response_interaction_type": interaction_type,
                "response_data": response_data,
                "response_version": response_version,
                "response_submitted_at": response_row["submitted_at"],
            }
            return _map_requirement(mapped_row)

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            f"student_requirement.response:{identifier}",
            payload,
            200,
            handler,
        )

    async def get_student_messages(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT id, subject, body, sender_name, kind, href, sent_at, read_at
            FROM student_message
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY sent_at DESC, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [_map_message(row) for row in rows]
        return {"items": items, "unreadCount": sum(x["readAt"] is None for x in items)}

    async def mark_student_message_read(
        self, auth: AuthContext, message_id: str, request_id: str
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, subject, body, sender_name, kind, href, sent_at, read_at
                    FROM student_message
                    WHERE tenant_id=:tenant_id AND student_id=:student_id AND id=:message_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "message_id": message_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_MESSAGE_NOT_FOUND", "The message was not found")
            current = dict(row)
            if current["read_at"] is not None:
                return _map_message(current)
            result = await connection.execute(
                text(
                    """
                    UPDATE student_message SET read_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id AND id=:message_id
                    RETURNING id, subject, body, sender_name, kind, href, sent_at, read_at
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "message_id": message_id,
                },
            )
            updated = result.mappings().first()
            if updated is None:
                raise ApiError(
                    500, "MESSAGE_UPDATE_FAILED", "The message could not be marked as read"
                )
            await self._insert_audit(
                connection,
                auth,
                "student_message.read",
                "student_message",
                message_id,
                request_id,
                {},
            )
            return _map_message(dict(updated))

    async def get_student_documents(self, auth: AuthContext) -> JsonDict:
        uploads = await self._all(
            f"""
            SELECT {DOCUMENT_SELECT} FROM document_record
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY created_at DESC, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        signed = await self._all(
            """
            SELECT id, template_code, onboarding_version, title, file_name, mime_type,
                   size_bytes, storage_key, sha256, signer_name, signature_method,
                   signed_at, created_at
            FROM student_signed_document
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY signed_at DESC, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [*map(_map_document, uploads), *map(_map_signed_document, signed)]
        items.sort(key=lambda item: (item["createdAt"], item["id"]), reverse=True)
        return {"items": items, "total": len(items)}

    async def save_student_signed_document(
        self, auth: AuthContext, document: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO student_signed_document (
                      id, tenant_id, student_id, template_code, onboarding_version,
                      title, file_name, mime_type, size_bytes, storage_provider,
                      storage_key, sha256, signer_name, signature_method, signed_at
                    ) VALUES (
                      :id, :tenant_id, :student_id, :template_code, :onboarding_version,
                      :title, :file_name, 'application/pdf', :size_bytes, 's3',
                      :storage_key, :sha256, :signer_name, :signature_method, :signed_at
                    ) ON CONFLICT (tenant_id, student_id, template_code, onboarding_version)
                    DO NOTHING
                    RETURNING id, template_code, onboarding_version, title, file_name,
                      mime_type, size_bytes, storage_key, sha256, signer_name,
                      signature_method, signed_at, created_at
                    """
                ),
                {
                    "id": document["id"],
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "template_code": document["templateCode"],
                    "onboarding_version": document["onboardingVersion"],
                    "title": document["title"],
                    "file_name": document["fileName"],
                    "size_bytes": document["sizeBytes"],
                    "storage_key": document["storageKey"],
                    "sha256": document["sha256"],
                    "signer_name": document["signerName"],
                    "signature_method": document["signatureMethod"],
                    "signed_at": _timestamp(document["signedAt"]),
                },
            )
            row = result.mappings().first()
            if row is None:
                existing = await connection.execute(
                    text(
                        """
                        SELECT id, template_code, onboarding_version, title, file_name,
                          mime_type, size_bytes, storage_key, sha256, signer_name,
                          signature_method, signed_at, created_at
                        FROM student_signed_document
                        WHERE tenant_id=:tenant_id AND student_id=:student_id
                          AND template_code=:template_code
                          AND onboarding_version=:onboarding_version
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "template_code": document["templateCode"],
                        "onboarding_version": document["onboardingVersion"],
                    },
                )
                row = existing.mappings().first()
            else:
                await self._insert_audit(
                    connection,
                    auth,
                    "student_signed_document.created",
                    "student_signed_document",
                    str(row["id"]),
                    request_id,
                    {
                        "templateCode": row["template_code"],
                        "onboardingVersion": int(row["onboarding_version"]),
                    },
                )
            if row is None:
                raise ApiError(
                    500,
                    "SIGNED_DOCUMENT_WRITE_FAILED",
                    "The signed document could not be saved",
                )
            return _map_signed_document(dict(row))

    async def get_course_exemption_context(
        self, auth: AuthContext, _courses: Sequence[Mapping[str, Any]]
    ) -> JsonDict | None:
        base = await self._one(
            """
            SELECT p.id AS program_id, p.code AS program_code, p.name AS program_name,
                   ccv.id AS catalog_version_id, ccv.code AS catalog_code,
                   ccv.effective_from, ccv.updated_at
            FROM admission_offer ao
            JOIN program p ON p.id=ao.program_id AND p.tenant_id=ao.tenant_id
            JOIN course_catalog_version ccv
              ON ccv.tenant_id=ao.tenant_id AND ccv.status='active'
            WHERE ao.tenant_id=:tenant_id AND ao.student_id=:student_id
            ORDER BY ccv.effective_from DESC, ccv.updated_at DESC LIMIT 1
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if base is None:
            return None
        common = {
            "tenant_id": auth.tenant_id,
            "catalog_version_id": base["catalog_version_id"],
        }
        courses = await self._all(
            """
            SELECT id, code, title, credits FROM catalog_course
            WHERE tenant_id=:tenant_id AND catalog_version_id=:catalog_version_id
              AND active=true ORDER BY code
            """,
            common,
        )
        requirements = await self._all(
            """
            SELECT id, course_id, category, required, recommended_term
            FROM program_requirement WHERE tenant_id=:tenant_id
              AND program_id=:program_id AND catalog_version_id=:catalog_version_id
            ORDER BY recommended_term, id
            """,
            {**common, "program_id": base["program_id"]},
        )
        prerequisites = await self._all(
            """
            SELECT course_id, prerequisite_course_id, minimum_grade
            FROM course_prerequisite WHERE tenant_id=:tenant_id
              AND catalog_version_id=:catalog_version_id
            ORDER BY course_id, prerequisite_course_id
            """,
            common,
        )
        rules = await self._all(
            """
            SELECT id, code, version, source_type, source_code, minimum_score,
                   minimum_grade, minimum_credits, target_course_id, confidence
            FROM course_equivalency_rule WHERE tenant_id=:tenant_id
              AND catalog_version_id=:catalog_version_id AND active=true
            ORDER BY code, version DESC
            """,
            common,
        )
        highest = max((int(rule["version"]) for rule in rules), default=0)
        return {
            "program": {
                "id": str(base["program_id"]),
                "code": base["program_code"],
                "name": base["program_name"],
            },
            "catalogVersion": {
                "id": str(base["catalog_version_id"]),
                "code": base["catalog_code"],
                "effectiveFrom": str(base["effective_from"]),
                "updatedAt": _iso(base["updated_at"]),
            },
            "policyVersion": f"{base['catalog_code']}:rules-v{highest}",
            "catalogCourses": [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "title": row["title"],
                    "credits": float(row["credits"]),
                }
                for row in courses
            ],
            "programRequirements": [
                {
                    "id": str(row["id"]),
                    "courseId": str(row["course_id"]),
                    "category": row["category"],
                    "required": bool(row["required"]),
                    "recommendedTerm": int(row["recommended_term"]),
                }
                for row in requirements
            ],
            "prerequisites": [
                {
                    "courseId": str(row["course_id"]),
                    "prerequisiteCourseId": str(row["prerequisite_course_id"]),
                    "minimumGrade": row["minimum_grade"],
                }
                for row in prerequisites
            ],
            "equivalencyRules": [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "version": int(row["version"]),
                    "sourceType": row["source_type"],
                    "sourceCode": row["source_code"],
                    "minimumScore": None
                    if row["minimum_score"] is None
                    else float(row["minimum_score"]),
                    "minimumGrade": row["minimum_grade"],
                    "minimumCredits": None
                    if row["minimum_credits"] is None
                    else float(row["minimum_credits"]),
                    "targetCourseId": str(row["target_course_id"]),
                    "confidence": float(row["confidence"]),
                }
                for row in rules
            ],
        }

    async def get_immunization_policy_context(self, auth: AuthContext) -> JsonDict | None:
        policy = await self._one(
            """
            SELECT id, code, version, name, effective_from, effective_until, updated_at
            FROM immunization_policy_version
            WHERE tenant_id=:tenant_id AND status='published'
              AND effective_from<=CURRENT_DATE
              AND (effective_until IS NULL OR effective_until>=CURRENT_DATE)
            ORDER BY version DESC LIMIT 1
            """,
            {"tenant_id": auth.tenant_id},
        )
        if policy is None:
            return None
        rules = await self._all(
            """
            SELECT id, code, name, description, required, dose_count, validity_days,
                   applies_when, evidence_criteria
            FROM immunization_requirement_rule
            WHERE tenant_id=:tenant_id AND policy_version_id=:policy_id AND active=true
            ORDER BY display_order, code
            """,
            {"tenant_id": auth.tenant_id, "policy_id": policy["id"]},
        )
        return {
            "policyVersion": {
                "id": str(policy["id"]),
                "code": policy["code"],
                "version": int(policy["version"]),
                "name": policy["name"],
                "effectiveFrom": str(policy["effective_from"]),
                "effectiveUntil": None
                if policy["effective_until"] is None
                else str(policy["effective_until"]),
                "updatedAt": _iso(policy["updated_at"]),
            },
            "requirements": [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "name": row["name"],
                    "description": row["description"],
                    "required": bool(row["required"]),
                    "doseCount": row["dose_count"],
                    "validityDays": row["validity_days"],
                    "appliesWhen": _mapping(row["applies_when"]),
                    "evidenceCriteria": _mapping(row["evidence_criteria"]),
                }
                for row in rules
            ],
        }

    async def create_student_document(
        self,
        auth: AuthContext,
        document: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            document_id = str(uuid4())
            result = await connection.execute(
                text(
                    f"""
                    INSERT INTO document_record (
                      id, tenant_id, student_id, file_name, mime_type, size_bytes,
                      category, processing_mode, status, storage_provider
                    ) SELECT :id, s.tenant_id, s.id, :file_name, :mime_type,
                      :size_bytes, :category, :processing_mode, 'placeholder',
                      'local_placeholder'
                    FROM student s WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                    RETURNING {DOCUMENT_SELECT}
                    """
                ),
                {
                    "id": document_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "file_name": document["fileName"],
                    "mime_type": document["mimeType"],
                    "size_bytes": document["sizeBytes"],
                    "category": document["category"],
                    "processing_mode": PROCESSING_MODES.get(str(document["category"]), "agentic"),
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
            await self._insert_audit(
                connection,
                auth,
                "document.placeholder_created",
                "document_record",
                document_id,
                request_id,
                {
                    "category": document["category"],
                    "mimeType": document["mimeType"],
                    "sizeBytes": document["sizeBytes"],
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.placeholder_created.v1",
                "document_record",
                document_id,
                1,
                request_id,
                {"studentId": auth.student_id, "category": document["category"]},
            )
            return _map_document(dict(row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_document.create",
            document,
            201,
            handler,
        )

    async def reserve_student_document_upload(
        self,
        auth: AuthContext,
        document: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
        requirement_id: str | None = None,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            category = str(document["category"])
            if requirement_id:
                # Student rows retain their historical evidence definition after a
                # journey publication. Authorize against the current definition that
                # get_student_requirement projects to the portal.
                result = await connection.execute(
                    text(
                        """
                        SELECT current_definition.code, sr.status
                        FROM student_requirement sr
                        JOIN enrollment_journey j
                          ON j.id=sr.journey_id AND j.tenant_id=sr.tenant_id
                        JOIN requirement_definition_version evidence_definition
                          ON evidence_definition.id=sr.requirement_definition_version_id
                         AND evidence_definition.tenant_id=sr.tenant_id
                        JOIN journey_requirement_definition current_link
                          ON current_link.journey_definition_version_id=
                             j.journey_definition_version_id
                        JOIN requirement_definition_version current_definition
                          ON current_definition.id=
                             current_link.requirement_definition_version_id
                         AND current_definition.tenant_id=sr.tenant_id
                         AND current_definition.code=evidence_definition.code
                        WHERE sr.tenant_id=:tenant_id AND j.student_id=:student_id
                          AND sr.id=:requirement_id
                          AND current_definition.submission_type='document'
                          AND current_definition.interaction_type='upload_file'
                          AND sr.retired_at IS NULL
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "requirement_id": requirement_id,
                    },
                )
                requirement = result.mappings().first()
                if requirement is None:
                    raise NotFoundError(
                        "DOCUMENT_REQUIREMENT_NOT_FOUND",
                        "The document requirement was not found",
                    )
                if requirement["status"] == "blocked":
                    raise ConflictError(
                        "DOCUMENT_REQUIREMENT_BLOCKED",
                        "Complete the prerequisite enrollment tasks before uploading "
                        "this document.",
                    )
                category = DOCUMENT_CATEGORIES.get(str(requirement["code"]), category)
            document_id = str(uuid4())
            extension = {
                "application/pdf": ".pdf",
                "image/jpeg": ".jpg",
                "image/png": ".png",
            }[str(document["mimeType"])]
            storage_key = f"{auth.tenant_id}/{auth.student_id}/{document_id}{extension}"
            result = await connection.execute(
                text(
                    f"""
                    INSERT INTO document_record (
                      id, tenant_id, student_id, requirement_id, file_name, mime_type,
                      size_bytes, category, processing_mode, status, storage_provider,
                      storage_key, sha256
                    ) SELECT :id, s.tenant_id, s.id, :requirement_id, :file_name,
                      :mime_type, :size_bytes, :category, :processing_mode, 'uploaded',
                      's3', :storage_key, :sha256
                    FROM student s WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                    RETURNING {DOCUMENT_SELECT}
                    """
                ),
                {
                    "id": document_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "requirement_id": requirement_id,
                    "file_name": document["fileName"],
                    "mime_type": document["mimeType"],
                    "size_bytes": document["sizeBytes"],
                    "category": category,
                    "processing_mode": PROCESSING_MODES.get(category, "agentic"),
                    "storage_key": storage_key,
                    "sha256": document["sha256"],
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
            await self._insert_audit(
                connection,
                auth,
                "document.upload_reserved",
                "document_record",
                document_id,
                request_id,
                {
                    "category": category,
                    "requirementId": requirement_id,
                    "mimeType": document["mimeType"],
                    "sizeBytes": document["sizeBytes"],
                    "sha256": document["sha256"],
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.upload_reserved.v1",
                "document_record",
                document_id,
                1,
                request_id,
                {
                    "studentId": auth.student_id,
                    "category": category,
                    "requirementId": requirement_id,
                },
            )
            return _map_document(dict(row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_document.upload.reserve",
            {"document": dict(document), "requirementId": requirement_id},
            201,
            handler,
        )

    async def get_student_appointments(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT id, type, starts_at, notes, status, created_at
            FROM student_appointment
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY starts_at, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [_map_appointment(row) for row in rows]
        return {"items": items, "total": len(items)}

    async def create_student_appointment(
        self,
        auth: AuthContext,
        appointment: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        try:
            starts_at = datetime.fromisoformat(str(appointment["startsAt"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as error:
            raise BadRequestError("VALIDATION_ERROR", "Appointment time is invalid") from error
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=UTC)
        if starts_at <= _utc_now():
            raise BadRequestError(
                "APPOINTMENT_MUST_BE_FUTURE", "Appointment time must be in the future"
            )

        async def handler(connection: AsyncConnection) -> JsonDict:
            appointment_id = str(uuid4())
            result = await connection.execute(
                text(
                    """
                    INSERT INTO student_appointment (
                      id, tenant_id, student_id, type, starts_at, notes, status
                    ) SELECT :id, s.tenant_id, s.id, :type, :starts_at, :notes, 'scheduled'
                    FROM student s
                    WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                    RETURNING id, type, starts_at, notes, status, created_at
                    """
                ),
                {
                    "id": appointment_id,
                    "type": appointment["type"],
                    "starts_at": starts_at,
                    "notes": appointment.get("notes"),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
            await self._insert_audit(
                connection,
                auth,
                "student_appointment.scheduled",
                "student_appointment",
                appointment_id,
                request_id,
                {"type": appointment["type"]},
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.appointment_scheduled.v1",
                "student_appointment",
                appointment_id,
                1,
                request_id,
                {"studentId": auth.student_id, "startsAt": _iso(starts_at)},
            )
            return _map_appointment(dict(row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_appointment.create",
            appointment,
            201,
            handler,
        )

    async def get_student_payments(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT id, offer_id, amount_cents, status, processor_reference, created_at
            FROM payment_transaction
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY created_at DESC, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        items = [_map_payment(row) for row in rows]
        return {"items": items, "total": len(items)}

    async def create_deposit_payment(
        self,
        auth: AuthContext,
        payment: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            offer_result = await connection.execute(
                text(
                    """
                    SELECT deposit_amount_cents FROM admission_offer
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:offer_id AND status='accepted'
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "offer_id": payment["offerId"],
                },
            )
            offer = offer_result.mappings().first()
            if offer is None:
                raise ConflictError(
                    "ACCEPTED_OFFER_REQUIRED",
                    "An accepted admission offer is required before paying a deposit",
                )
            existing_result = await connection.execute(
                text(
                    """
                    SELECT id, offer_id, amount_cents, status, processor_reference, created_at
                    FROM payment_transaction
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND offer_id=:offer_id AND type='enrollment_deposit'
                      AND status='succeeded'
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "offer_id": payment["offerId"],
                },
            )
            existing = existing_result.mappings().first()
            if existing is not None:
                return _map_payment(dict(existing))
            payment_id = str(uuid4())
            processor_reference = f"dummy_{payment_id.replace('-', '')}"
            result = await connection.execute(
                text(
                    """
                    INSERT INTO payment_transaction (
                      id, tenant_id, student_id, offer_id, type, amount_cents,
                      status, processor, processor_reference
                    ) VALUES (
                      :id, :tenant_id, :student_id, :offer_id, 'enrollment_deposit',
                      :amount_cents, 'succeeded', 'dummy', :processor_reference
                    )
                    RETURNING id, offer_id, amount_cents, status,
                              processor_reference, created_at
                    """
                ),
                {
                    "id": payment_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "offer_id": payment["offerId"],
                    "amount_cents": int(offer["deposit_amount_cents"]),
                    "processor_reference": processor_reference,
                },
            )
            row = result.mappings().first()
            if row is None:
                raise ApiError(500, "PAYMENT_CREATE_FAILED", "The deposit could not be recorded")
            await self._complete_requirement(connection, auth, "enrollment_deposit")
            await self._insert_audit(
                connection,
                auth,
                "payment.deposit_succeeded",
                "payment_transaction",
                payment_id,
                request_id,
                {
                    "offerId": payment["offerId"],
                    "amountCents": int(offer["deposit_amount_cents"]),
                    "processor": "dummy",
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "payment.deposit_succeeded.v1",
                "payment_transaction",
                payment_id,
                1,
                request_id,
                {
                    "studentId": auth.student_id,
                    "offerId": payment["offerId"],
                    "amountCents": int(offer["deposit_amount_cents"]),
                },
            )
            return _map_payment(dict(row))

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_payment.deposit",
            payment,
            200,
            handler,
        )

    async def get_student_profile(self, auth: AuthContext) -> JsonDict:
        row = await self._one(
            """
            SELECT sp.student_id, sp.preferred_name, p.first_name, p.last_name,
                   ca.email_normalized AS email,
                   ca.email_verified_at IS NOT NULL AS email_verified,
                   ca.phone_verified_at IS NOT NULL AS phone_verified,
                   sp.pronouns, sp.mobile_phone, sp.communication_preference,
                   sp.version, sp.updated_at
            FROM student_profile sp
            JOIN student s ON s.id=sp.student_id AND s.tenant_id=sp.tenant_id
            JOIN person p ON p.id=s.person_id AND p.tenant_id=s.tenant_id
            LEFT JOIN credential_account ca
              ON ca.student_id=sp.student_id AND ca.tenant_id=sp.tenant_id
             AND ca.status='active'
            WHERE sp.tenant_id=:tenant_id AND sp.student_id=:student_id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if row is None:
            raise NotFoundError("STUDENT_PROFILE_NOT_FOUND", "The student profile was not found")
        return _map_profile(row)

    async def update_student_profile(
        self, auth: AuthContext, update: Mapping[str, Any], request_id: str
    ) -> JsonDict:
        fields = ("preferredName", "pronouns", "mobilePhone", "communicationPreference")
        changed = [field for field in fields if field in update]
        if not changed:
            raise BadRequestError(
                "PROFILE_UPDATE_EMPTY", "At least one profile field must be supplied"
            )
        async with self.engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE student_profile SET
                      preferred_name=CASE WHEN :has_preferred
                        THEN :preferred ELSE preferred_name END,
                      pronouns=CASE WHEN :has_pronouns THEN :pronouns ELSE pronouns END,
                      mobile_phone=CASE WHEN :has_mobile THEN :mobile ELSE mobile_phone END,
                      communication_preference=CASE WHEN :has_preference
                        THEN :preference ELSE communication_preference END,
                      version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND version=:expected_version
                    RETURNING student_id, preferred_name, pronouns, mobile_phone,
                              communication_preference, version, updated_at
                    """
                ),
                {
                    "has_preferred": "preferredName" in update,
                    "preferred": update.get("preferredName", ""),
                    "has_pronouns": "pronouns" in update,
                    "pronouns": update.get("pronouns"),
                    "has_mobile": "mobilePhone" in update,
                    "mobile": update.get("mobilePhone"),
                    "has_preference": "communicationPreference" in update,
                    "preference": update.get("communicationPreference", "email"),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "expected_version": update["expectedVersion"],
                },
            )
            row = result.mappings().first()
            if row is None:
                current = await connection.execute(
                    text(
                        """SELECT version FROM student_profile
                           WHERE tenant_id=:tenant_id AND student_id=:student_id"""
                    ),
                    {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
                )
                if current.mappings().first() is None:
                    raise NotFoundError(
                        "STUDENT_PROFILE_NOT_FOUND", "The student profile was not found"
                    )
                raise ConflictError("VERSION_CONFLICT", "The profile changed in another session")
            updated = dict(row)
            if "preferredName" in update:
                await connection.execute(
                    text(
                        """
                        UPDATE person p SET preferred_name=:preferred_name, updated_at=NOW()
                        FROM student s WHERE s.person_id=p.id AND s.tenant_id=p.tenant_id
                          AND s.tenant_id=:tenant_id AND s.id=:student_id
                        """
                    ),
                    {
                        "preferred_name": updated["preferred_name"],
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                    },
                )
            await self._complete_requirement(connection, auth, "profile_verification")
            await self._insert_audit(
                connection,
                auth,
                "student_profile.updated",
                "student_profile",
                auth.student_id,
                request_id,
                {"changedFields": changed, "version": int(updated["version"])},
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.profile_updated.v1",
                "student_profile",
                auth.student_id,
                int(updated["version"]),
                request_id,
                {"studentId": auth.student_id, "changedFields": changed},
            )
            return _map_profile(updated)

    async def get_student_academics(self, auth: AuthContext) -> JsonDict:
        selected = await self._one(
            """
            SELECT p.id, p.code, p.name, p.degree, p.total_credits, p.description,
                   p.source_label, p.source_url, p.source_status,
                   ccv.id AS catalog_id, ccv.code AS catalog_code
            FROM admission_offer ao
            JOIN program p ON p.id=ao.program_id AND p.tenant_id=ao.tenant_id
            JOIN course_catalog_version ccv
              ON ccv.tenant_id=ao.tenant_id AND ccv.status='active'
            WHERE ao.tenant_id=:tenant_id AND ao.student_id=:student_id
            ORDER BY ao.created_at DESC, ccv.effective_from DESC LIMIT 1
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if selected is None:
            raise NotFoundError(
                "STUDENT_ACADEMICS_NOT_FOUND",
                "No academic plan is available for this student",
            )
        available_rows = await self._all(
            """
            SELECT id, code, name, degree, total_credits, description,
                   source_label, source_url, source_status
            FROM program WHERE tenant_id=:tenant_id ORDER BY name
            """,
            {"tenant_id": auth.tenant_id},
        )
        course_rows = await self._all(
            """
            SELECT cc.id, cc.code, cc.title, cc.description, cc.credits, cc.level,
                   cc.availability_label, cc.instructor_names, cc.meeting_pattern,
                   cc.resources, COALESCE(cc.source_url, ccv.source_url) AS source_url,
                   ccv.source_label, ccv.source_status,
                   COALESCE(json_agg(json_build_object(
                     'courseCode', prerequisite.code,
                     'minimumGrade', cp.minimum_grade
                   )) FILTER (WHERE prerequisite.id IS NOT NULL), '[]'::json) AS prerequisites
            FROM catalog_course cc
            JOIN course_catalog_version ccv
              ON ccv.id=cc.catalog_version_id AND ccv.tenant_id=cc.tenant_id
            LEFT JOIN course_prerequisite cp
              ON cp.course_id=cc.id AND cp.tenant_id=cc.tenant_id
             AND cp.catalog_version_id=cc.catalog_version_id
            LEFT JOIN catalog_course prerequisite ON prerequisite.id=cp.prerequisite_course_id
            WHERE cc.tenant_id=:tenant_id AND cc.catalog_version_id=:catalog_id
              AND cc.active=true
            GROUP BY cc.id, ccv.id ORDER BY cc.code
            """,
            {"tenant_id": auth.tenant_id, "catalog_id": selected["catalog_id"]},
        )
        courses = [_map_course(row) for row in course_rows]
        course_by_id = {course["id"]: course for course in courses}
        requirement_rows = await self._all(
            """
            SELECT course_id, category, recommended_term FROM program_requirement
            WHERE tenant_id=:tenant_id AND program_id=:program_id
              AND catalog_version_id=:catalog_id AND required=true
            ORDER BY recommended_term, id
            """,
            {
                "tenant_id": auth.tenant_id,
                "program_id": selected["id"],
                "catalog_id": selected["catalog_id"],
            },
        )
        credit_rows = await self._all(
            """
            SELECT id, source_type, source_code, title, grade_or_score, credits,
                   institution_name, source_document_id
            FROM student_transcript_credit
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY created_at, id
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        recommendation_rows = await self._all(
            """
            SELECT cer.id, cer.transcript_credit_id, target.code AS target_course_code,
                   target.title AS target_course_title, rule.code AS rule_code,
                   cer.rationale, cer.confidence, cer.status
            FROM course_exemption_recommendation cer
            JOIN catalog_course target ON target.id=cer.target_course_id
            JOIN course_equivalency_rule rule ON rule.id=cer.equivalency_rule_id
            WHERE cer.tenant_id=:tenant_id AND cer.student_id=:student_id
              AND cer.program_id=:program_id AND cer.status<>'superseded'
            ORDER BY target.code, cer.created_at
            """,
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "program_id": selected["id"],
            },
        )
        transcript_credits = [
            {
                "id": str(row["id"]),
                "sourceType": row["source_type"],
                "sourceCode": row.get("source_code"),
                "title": row["title"],
                "gradeOrScore": row.get("grade_or_score"),
                "credits": None if row.get("credits") is None else float(row["credits"]),
                "institutionName": row.get("institution_name"),
                "sourceDocumentId": (
                    str(row["source_document_id"]) if row.get("source_document_id") else None
                ),
            }
            for row in credit_rows
        ]
        recommendations = [
            {
                "id": str(row["id"]),
                "transcriptCreditId": str(row["transcript_credit_id"]),
                "targetCourseCode": row["target_course_code"],
                "targetCourseTitle": row["target_course_title"],
                "ruleCode": row["rule_code"],
                "rationale": row["rationale"],
                "confidence": float(row["confidence"]),
                "status": row["status"],
                "requiresStaffReview": True,
            }
            for row in recommendation_rows
        ]
        recommendation_by_code = {item["targetCourseCode"]: item for item in recommendations}
        approved = {
            item["targetCourseCode"] for item in recommendations if item["status"] == "approved"
        }
        plan: list[JsonDict] = []
        for requirement in requirement_rows:
            course = course_by_id.get(str(requirement["course_id"]))
            if course is None:
                continue
            recommendation = recommendation_by_code.get(str(course["code"]))
            prerequisites = _list(course.get("prerequisites"))
            prerequisite_codes = [
                str(item["courseCode"])
                for item in prerequisites
                if isinstance(item, dict) and item.get("courseCode")
            ]
            missing = [code for code in prerequisite_codes if code not in approved]
            status = (
                "exempted"
                if recommendation and recommendation["status"] == "approved"
                else "exemption_suggested"
                if recommendation
                else "blocked"
                if missing
                else "eligible"
            )
            plan.append(
                {
                    "course": course,
                    "category": requirement["category"],
                    "recommendedTerm": int(requirement["recommended_term"]),
                    "status": status,
                    "satisfiedPrerequisiteCodes": [
                        code for code in prerequisite_codes if code in approved
                    ],
                    "missingPrerequisiteCodes": missing,
                }
            )
        exempted = sum(
            float(item["course"]["credits"]) for item in plan if item["status"] == "exempted"
        )
        required = int(selected["total_credits"])
        return {
            "selectedProgram": _map_program(selected),
            "availablePrograms": [_map_program(row) for row in available_rows],
            "transcriptCredits": transcript_credits,
            "exemptionRecommendations": recommendations,
            "plan": plan,
            "progress": {
                "completedCredits": 0,
                "exemptedCredits": exempted,
                "requiredCredits": required,
                "percent": round(exempted / required * 100) if required else 0,
            },
            "catalogVersion": selected["catalog_code"],
            "generatedAt": _iso(_utc_now()),
        }

    async def search_catalog_courses(self, auth: AuthContext, query: str) -> JsonDict:
        normalized = query.strip()[:120]
        rows = await self._all(
            """
            SELECT cc.id, cc.code, cc.title, cc.description, cc.credits, cc.level,
                   cc.availability_label, cc.instructor_names, cc.meeting_pattern,
                   cc.resources, COALESCE(cc.source_url, ccv.source_url) AS source_url,
                   ccv.source_label, ccv.source_status, ccv.code AS catalog_code,
                   COALESCE(json_agg(json_build_object(
                     'courseCode', prerequisite.code,
                     'minimumGrade', cp.minimum_grade
                   )) FILTER (WHERE prerequisite.id IS NOT NULL), '[]'::json) AS prerequisites
            FROM catalog_course cc
            JOIN course_catalog_version ccv
              ON ccv.id=cc.catalog_version_id AND ccv.tenant_id=cc.tenant_id
             AND ccv.status='active'
            LEFT JOIN course_prerequisite cp
              ON cp.course_id=cc.id AND cp.tenant_id=cc.tenant_id
             AND cp.catalog_version_id=cc.catalog_version_id
            LEFT JOIN catalog_course prerequisite ON prerequisite.id=cp.prerequisite_course_id
            WHERE cc.tenant_id=:tenant_id AND cc.active=true
              AND (:query='' OR cc.code ILIKE :pattern OR cc.title ILIKE :pattern
                   OR cc.description ILIKE :pattern)
            GROUP BY cc.id, ccv.id ORDER BY cc.code LIMIT 60
            """,
            {"tenant_id": auth.tenant_id, "query": normalized, "pattern": f"%{normalized}%"},
        )
        items = [_map_course(row) for row in rows]
        return {
            "items": items,
            "total": len(items),
            "catalogVersion": rows[0]["catalog_code"] if rows else "unavailable",
        }

    async def get_student_financials(self, auth: AuthContext) -> JsonDict:
        summary = await self._one(
            """
            SELECT sfs.academic_year, sfs.cost_of_attendance_cents,
                   sfs.external_payments_cents,
                   COALESCE((SELECT SUM(pt.amount_cents) FROM payment_transaction pt
                     WHERE pt.tenant_id=sfs.tenant_id AND pt.student_id=sfs.student_id
                       AND pt.status='succeeded'), 0) AS portal_payments_cents
            FROM student_financial_summary sfs
            WHERE sfs.tenant_id=:tenant_id AND sfs.student_id=:student_id
            ORDER BY sfs.academic_year DESC LIMIT 1
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if summary is None:
            raise NotFoundError(
                "STUDENT_FINANCIALS_NOT_FOUND",
                "No financial record is available for this student",
            )
        common = {
            "tenant_id": auth.tenant_id,
            "student_id": auth.student_id,
            "academic_year": summary["academic_year"],
        }
        award_rows = await self._all(
            """
            SELECT id, source, name, type, offered_amount_cents,
                   accepted_amount_cents, status, requires_action
            FROM student_financial_award
            WHERE tenant_id=:tenant_id AND student_id=:student_id
              AND academic_year=:academic_year ORDER BY type, name
            """,
            common,
        )
        document_rows = await self._all(
            """
            SELECT id, code, title, description, status, due_at, document_id
            FROM financial_document_requirement
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY due_at NULLS LAST, code
            """,
            common,
        )
        plan_rows = await self._all(
            """
            SELECT id, name, installment_count, enrollment_fee_cents, status
            FROM student_payment_plan
            WHERE tenant_id=:tenant_id AND student_id=:student_id
              AND academic_year=:academic_year AND status<>'cancelled'
            ORDER BY installment_count
            """,
            common,
        )
        sap = await self._one(
            """
            SELECT status, cumulative_gpa, minimum_gpa, completion_rate_percent,
                   minimum_completion_rate_percent, attempted_credits,
                   maximum_attempted_credits
            FROM student_sap_status
            WHERE tenant_id=:tenant_id AND student_id=:student_id
              AND academic_year=:academic_year
            """,
            common,
        )
        offer = await self._one(
            """
            SELECT offer.id, offer.response_deadline, offer.deposit_amount_cents,
                   EXISTS (
                     SELECT 1 FROM payment_transaction payment
                     WHERE payment.tenant_id=offer.tenant_id
                       AND payment.student_id=offer.student_id
                       AND payment.offer_id=offer.id
                       AND payment.type='enrollment_deposit'
                       AND payment.status='succeeded'
                   ) AS deposit_paid
            FROM admission_offer offer
            WHERE offer.tenant_id=:tenant_id AND offer.student_id=:student_id
              AND offer.status IN ('offered','accepted')
            ORDER BY CASE offer.status WHEN 'accepted' THEN 0 ELSE 1 END,
                     offer.created_at DESC
            LIMIT 1
            """,
            common,
        )
        if sap is None:
            raise NotFoundError(
                "STUDENT_SAP_NOT_FOUND",
                "No satisfactory academic progress record is available",
            )
        awards = [
            {
                "id": str(row["id"]),
                "source": row["source"],
                "name": row["name"],
                "type": row["type"],
                "offeredAmountCents": int(row["offered_amount_cents"]),
                "acceptedAmountCents": int(row["accepted_amount_cents"]),
                "status": row["status"],
                "requiresAction": bool(row["requires_action"]),
            }
            for row in award_rows
        ]
        accepted_aid = sum(
            item["acceptedAmountCents"] for item in awards if item["type"] != "work_study"
        )
        pending_aid = sum(
            item["offeredAmountCents"]
            for item in awards
            if item["type"] != "work_study" and item["status"] in {"offered", "pending"}
        )
        payments = int(summary["external_payments_cents"]) + int(summary["portal_payments_cents"])
        remaining = max(0, int(summary["cost_of_attendance_cents"]) - accepted_aid - payments)
        payment_schedule: list[JsonDict] = []
        unpaid_deposit = 0
        schedule_anchor: date | None = None
        if offer is not None:
            schedule_anchor = offer["response_deadline"]
            deposit_paid = offer["deposit_paid"] is True
            deposit_amount = int(offer["deposit_amount_cents"])
            unpaid_deposit = 0 if deposit_paid else deposit_amount
            payment_schedule.append(
                {
                    "id": f"{offer['id']}:enrollment_deposit",
                    "kind": "deposit",
                    "label": "Enrollment deposit",
                    "amountCents": deposit_amount,
                    "enrollmentFeeCents": 0,
                    "dueAt": _iso(schedule_anchor),
                    "status": "paid" if deposit_paid else "due",
                    "projected": False,
                }
            )
        enrolled_plan = next((row for row in plan_rows if row["status"] == "enrolled"), None)
        if enrolled_plan is not None and schedule_anchor is not None:
            count = int(enrolled_plan["installment_count"])
            installment_total = max(0, remaining - unpaid_deposit)
            base_amount, remainder_cents = divmod(installment_total, count)
            for installment_index in range(count):
                payment_schedule.append(
                    {
                        "id": f"{enrolled_plan['id']}:installment:{installment_index + 1}",
                        "kind": "installment",
                        "label": (
                            f"{enrolled_plan['name']} installment "
                            f"{installment_index + 1} of {count}"
                        ),
                        "amountCents": base_amount
                        + (1 if installment_index < remainder_cents else 0),
                        "enrollmentFeeCents": (
                            int(enrolled_plan["enrollment_fee_cents"])
                            if installment_index == 0
                            else 0
                        ),
                        "dueAt": _iso(_add_calendar_months(schedule_anchor, installment_index + 1)),
                        "status": "projected",
                        "projected": True,
                    }
                )
        return {
            "academicYear": str(summary["academic_year"]).replace("-", "\N{EN DASH}", 1),
            "costOfAttendanceCents": int(summary["cost_of_attendance_cents"]),
            "acceptedAidCents": accepted_aid,
            "pendingAidCents": pending_aid,
            "paymentsCents": payments,
            "remainingBalanceCents": remaining,
            "awards": awards,
            "requiredDocuments": [
                {
                    "id": str(row["id"]),
                    "code": row["code"],
                    "title": row["title"],
                    "description": row["description"],
                    "status": row["status"],
                    "dueAt": _nullable_iso(row.get("due_at")),
                    "documentId": (
                        None if row.get("document_id") is None else str(row["document_id"])
                    ),
                    "href": (
                        f"/documents?document={row['document_id']}"
                        if row.get("document_id") is not None
                        else "/financials"
                        if row["code"] == "award_acceptance"
                        else "/enrollment/requirements/financial-aid-verification"
                    ),
                }
                for row in document_rows
            ],
            "paymentPlans": [
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "installmentCount": int(row["installment_count"]),
                    "installmentAmountCents": math.ceil(remaining / int(row["installment_count"])),
                    "enrollmentFeeCents": int(row["enrollment_fee_cents"]),
                    "status": row["status"],
                }
                for row in plan_rows
            ],
            "paymentSchedule": payment_schedule,
            "sap": {
                "status": sap["status"],
                "cumulativeGpa": float(sap["cumulative_gpa"]),
                "minimumGpa": float(sap["minimum_gpa"]),
                "completionRatePercent": float(sap["completion_rate_percent"]),
                "minimumCompletionRatePercent": float(sap["minimum_completion_rate_percent"]),
                "attemptedCredits": float(sap["attempted_credits"]),
                "maximumAttemptedCredits": float(sap["maximum_attempted_credits"]),
            },
            "generatedAt": _iso(_utc_now()),
        }

    async def select_financial_payment_plan(
        self,
        auth: AuthContext,
        plan_id: str,
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        async def handler(connection: AsyncConnection) -> JsonDict:
            result = await connection.execute(
                text(
                    """
                    SELECT id, academic_year FROM student_payment_plan
                    WHERE id=:plan_id AND tenant_id=:tenant_id
                      AND student_id=:student_id AND status<>'cancelled'
                    FOR UPDATE
                    """
                ),
                {
                    "plan_id": plan_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                },
            )
            plan = result.mappings().first()
            if plan is None:
                raise NotFoundError(
                    "PAYMENT_PLAN_NOT_FOUND", "The selected payment plan was not found"
                )
            await connection.execute(
                text(
                    """
                    UPDATE student_payment_plan
                    SET status=CASE WHEN id=:plan_id THEN 'enrolled' ELSE 'available' END,
                        enrolled_at=CASE WHEN id=:plan_id THEN NOW() ELSE NULL END,
                        version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND academic_year=:academic_year AND status<>'cancelled'
                    """
                ),
                {
                    "plan_id": str(plan["id"]),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "academic_year": plan["academic_year"],
                },
            )
            await self._insert_audit(
                connection,
                auth,
                "student_financial.payment_plan_selected",
                "student_payment_plan",
                str(plan["id"]),
                request_id,
                {"academicYear": plan["academic_year"]},
            )
            await self._insert_outbox(
                connection,
                auth,
                "student_financial.payment_plan_selected.v1",
                "student_payment_plan",
                str(plan["id"]),
                1,
                request_id,
                {"studentId": auth.student_id},
            )
            return {"planId": str(plan["id"]), "status": "enrolled"}

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_financial.payment_plan.select",
            {"planId": plan_id},
            200,
            handler,
        )

    async def get_campus_life(self, auth: AuthContext) -> JsonDict:
        event_rows = await self._all(
            """
            SELECT id, title, description, starts_at, ends_at, location,
                   category, featured, accent, source_label, source_url,
                   source_status, registration_url, visual_theme, image_url,
                   image_alt, image_attribution, image_source_url,
                   advertisement_starts_at, advertisement_ends_at
            FROM campus_event WHERE tenant_id=:tenant_id AND active=true
              AND (
                :include_scheduled
                OR (
                  (advertisement_starts_at IS NULL OR advertisement_starts_at <= NOW())
                  AND (advertisement_ends_at IS NULL OR advertisement_ends_at >= NOW())
                )
              )
            ORDER BY featured DESC, starts_at, id LIMIT 30
            """,
            {
                "tenant_id": auth.tenant_id,
                "include_scheduled": auth.actor_type == "staff",
            },
        )
        club_rows = await self._all(
            """
            SELECT club.id, club.name, club.category, club.description,
                   club.contact_name, club.contact_role, club.contact_channel,
                   club.latest_update, club.next_activity, club.source_label,
                   club.source_url, club.source_status, club.social_links,
                   club.long_description, club.meeting_schedule, club.membership_open,
                   COALESCE(media.public_path, '/media/clubs/code-collective.jpg') AS image_url,
                   COALESCE(media.alt_text, 'Students collaborating in a campus club') AS image_alt,
                   COALESCE(media.attribution, 'Default Aster club image') AS image_attribution,
                   COALESCE(media.source_url, '') AS image_source_url
            FROM student_club club
            LEFT JOIN media_asset media ON media.id=club.media_asset_id
              AND media.tenant_id=club.tenant_id AND media.active=true
            WHERE club.tenant_id=:tenant_id AND club.active=true
            ORDER BY club.name LIMIT 100
            """,
            {"tenant_id": auth.tenant_id},
        )
        club_event_rows = await self._all(
            """
            SELECT id, club_id, title, description, starts_at, ends_at, location,
                   category, registration_url FROM student_club_event
            WHERE tenant_id=:tenant_id AND active=true
            ORDER BY starts_at, id LIMIT 500
            """,
            {"tenant_id": auth.tenant_id},
        )
        events = [
            {
                "id": str(row["id"]),
                "title": row["title"],
                "description": row["description"],
                "startsAt": _iso(row["starts_at"]),
                "endsAt": _iso(row["ends_at"]),
                "location": row["location"],
                "category": row["category"],
                "featured": bool(row["featured"]),
                "accent": row["accent"],
                **({"visualTheme": row["visual_theme"]} if row.get("visual_theme") else {}),
                "imageUrl": row.get("image_url"),
                "imageAlt": row.get("image_alt"),
                "imageAttribution": row.get("image_attribution"),
                "imageSourceUrl": row.get("image_source_url"),
                "advertisementStartsAt": _nullable_iso(row.get("advertisement_starts_at")),
                "advertisementEndsAt": _nullable_iso(row.get("advertisement_ends_at")),
                "source": _map_source(row),
                "registrationUrl": row.get("registration_url"),
            }
            for row in event_rows
        ]
        clubs: list[JsonDict] = []
        for row in club_rows:
            clubs.append(
                {
                    "id": str(row["id"]),
                    "name": row["name"],
                    "category": row["category"],
                    "description": row["description"],
                    "contactName": row["contact_name"],
                    "contactRole": row["contact_role"],
                    "contactChannel": row["contact_channel"],
                    "latestUpdate": row["latest_update"],
                    "nextActivity": row.get("next_activity"),
                    "imageUrl": row["image_url"],
                    "imageAlt": row["image_alt"],
                    "imageAttribution": row["image_attribution"],
                    "imageSourceUrl": row["image_source_url"],
                    "source": _map_source(row),
                    "socialLinks": _list(row.get("social_links")),
                    "longDescription": row.get("long_description"),
                    "meetingSchedule": row.get("meeting_schedule"),
                    "membershipOpen": bool(row["membership_open"]),
                    "events": [
                        {
                            "id": str(event["id"]),
                            "title": event["title"],
                            "description": event["description"],
                            "startsAt": _iso(event["starts_at"]),
                            "endsAt": _iso(event["ends_at"]),
                            "location": event["location"],
                            "category": event["category"],
                            "registrationUrl": event.get("registration_url"),
                        }
                        for event in club_event_rows
                        if str(event["club_id"]) == str(row["id"])
                    ],
                }
            )
        return {"events": events, "clubs": clubs, "generatedAt": _iso(_utc_now())}

    async def get_student_help(self, auth: AuthContext) -> JsonDict:
        rows = await self._all(
            """
            SELECT id, category, question, answer FROM help_article
            WHERE tenant_id=:tenant_id AND active=true ORDER BY sort_order, id
            """,
            {"tenant_id": auth.tenant_id},
        )
        inquiry_rows = await self._all(
            """
            SELECT id, topic_code, subject, message, status, priority,
                   assignee_id, requirement_id, version, created_at, updated_at
            FROM student_inquiry
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            ORDER BY updated_at DESC, id DESC
            LIMIT 50
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        async with self.engine.connect() as connection:
            requests = [
                {
                    **_map_help_request(row),
                    "messages": await self._student_inquiry_messages(
                        connection,
                        tenant_id=auth.tenant_id,
                        student_id=auth.student_id,
                        inquiry_id=str(row["id"]),
                        initial_body=str(row["message"]),
                        initial_created_at=row["created_at"],
                    ),
                }
                for row in inquiry_rows
            ]
        return {
            "articles": [
                {
                    "id": str(row["id"]),
                    "category": row["category"],
                    "question": row["question"],
                    "answer": row["answer"],
                }
                for row in rows
            ],
            "requests": requests,
            "support": {
                "email": "enrollment-support@vv.example",
                "phone": "+1 555 010 2027",
                "hours": "Monday-Friday, 09:00-17:00",
            },
        }

    async def create_student_help_request(
        self,
        auth: AuthContext,
        payload: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        topic_code = str(payload["topicCode"])
        message = str(payload["message"])
        requirement_value = payload.get("requirementId")
        requirement_id = str(requirement_value) if requirement_value is not None else None
        priority = "high" if topic_code == "support" else "medium"

        async def handler(connection: AsyncConnection) -> JsonDict:
            subject = f"Student question about {topic_code.replace('_', ' ')}"
            requirement: Mapping[str, Any] | None = None
            if requirement_id is not None:
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": f"requirement-help:{auth.tenant_id}:{requirement_id}"},
                )
                requirement_result = await connection.execute(
                    text(
                        """
                        SELECT requirement.id, requirement.status, requirement.version,
                               definition.title, definition.responsible_office,
                               definition.flow_kind
                        FROM student_requirement requirement
                        JOIN enrollment_journey journey
                          ON journey.id=requirement.journey_id
                         AND journey.tenant_id=requirement.tenant_id
                        JOIN requirement_definition_version definition
                          ON definition.id=requirement.requirement_definition_version_id
                         AND definition.tenant_id=requirement.tenant_id
                        WHERE requirement.tenant_id=:tenant_id
                          AND requirement.id=:requirement_id
                          AND journey.student_id=:student_id
                          AND requirement.retired_at IS NULL
                        FOR UPDATE OF requirement
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "requirement_id": requirement_id,
                    },
                )
                requirement_row = requirement_result.mappings().first()
                if requirement_row is None:
                    raise NotFoundError(
                        "STUDENT_REQUIREMENT_NOT_FOUND",
                        "This enrollment or onboarding task is no longer available",
                    )
                requirement = dict(requirement_row)
                if str(requirement["status"]) in {
                    "not_applicable",
                    "completed",
                    "waived",
                    "expired",
                }:
                    raise ConflictError(
                        "REQUIREMENT_HELP_NOT_AVAILABLE",
                        "Help cannot be requested for a completed or inactive task",
                    )
                active_result = await connection.execute(
                    text(
                        """
                        SELECT inquiry.id, inquiry.topic_code, inquiry.subject,
                               inquiry.message, inquiry.status, inquiry.priority,
                               inquiry.assignee_id, inquiry.requirement_id,
                               inquiry.version, inquiry.created_at, inquiry.updated_at,
                               item.id AS work_item_id
                        FROM student_inquiry inquiry
                        LEFT JOIN staff_work_item item
                          ON item.tenant_id=inquiry.tenant_id
                         AND item.source_type='message'
                         AND item.source_id=inquiry.id
                        WHERE inquiry.tenant_id=:tenant_id
                          AND inquiry.student_id=:student_id
                          AND inquiry.requirement_id=:requirement_id
                          AND inquiry.status IN ('new','open','waiting_on_student')
                        ORDER BY inquiry.created_at, inquiry.id
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "requirement_id": requirement_id,
                    },
                )
                active_row = active_result.mappings().first()
                if active_row is not None:
                    active = dict(active_row)
                    response = _map_help_request(active)
                    response["workItemId"] = (
                        str(active["work_item_id"])
                        if active.get("work_item_id") is not None
                        else None
                    )
                    response["messages"] = await self._student_inquiry_messages(
                        connection,
                        tenant_id=auth.tenant_id,
                        student_id=auth.student_id,
                        inquiry_id=str(active["id"]),
                        initial_body=str(active["message"]),
                        initial_created_at=active["created_at"],
                    )
                    return response

                subject = f"Help with {requirement['title']}"

            target = await self._resolve_staff_triage_target(
                connection,
                tenant_id=auth.tenant_id,
                preferred_component=(
                    str(requirement["responsible_office"])
                    if requirement is not None
                    else "Enrollment Support"
                ),
            )
            inquiry_id = str(uuid4())
            result = await connection.execute(
                text(
                    """
                    INSERT INTO student_inquiry (
                      id, tenant_id, student_id, topic_code, subject, message,
                      status, priority, assignee_id, requirement_id,
                      status_before_help, version, created_at, updated_at
                    )
                    SELECT :id, student.tenant_id, student.id, :topic_code, :subject,
                           :message, 'new', :priority, :assignee_id, :requirement_id,
                           :status_before_help, 1, NOW(), NOW()
                    FROM student
                    WHERE student.tenant_id=:tenant_id AND student.id=:student_id
                    RETURNING id, topic_code, subject, message, status, priority,
                              assignee_id, requirement_id, version, created_at, updated_at
                    """
                ),
                {
                    "id": inquiry_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "topic_code": topic_code,
                    "subject": subject,
                    "message": message,
                    "priority": priority,
                    "assignee_id": target.get("staffMemberId"),
                    "requirement_id": requirement_id,
                    "status_before_help": (
                        str(requirement["status"]) if requirement is not None else None
                    ),
                },
            )
            row = result.mappings().first()
            if row is None:
                raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
            if requirement is not None:
                await connection.execute(
                    text(
                        """
                        UPDATE student_requirement
                        SET status='help_requested', version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND id=:requirement_id
                          AND status NOT IN (
                            'not_applicable','completed','waived','expired'
                          )
                        """
                    ),
                    {"tenant_id": auth.tenant_id, "requirement_id": requirement_id},
                )
            work_item_id = str(uuid4())
            work_key = f"INQ-{inquiry_id.replace('-', '')[:8].upper()}"
            work_result = await connection.execute(
                text(
                    """
                    INSERT INTO staff_work_item (
                      id, tenant_id, student_id, key, title, description,
                      status, priority, work_type, component, due_at, escalated,
                      assignee_id, source_type, source_id, version, action_type,
                      selected_channel, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :student_id, :key, :title, :description,
                      'todo', :priority, 'communication', :component,
                      NOW() + interval '1 day', false, :assignee_id,
                      'message', :source_id, 1,
                      'communication_response', 'portal', NOW(), NOW()
                    )
                    ON CONFLICT (tenant_id, source_type, source_id)
                    WHERE source_type IS NOT NULL AND source_id IS NOT NULL
                    DO UPDATE SET updated_at = staff_work_item.updated_at
                    RETURNING id
                    """
                ),
                {
                    "id": work_item_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "key": work_key,
                    "title": subject,
                    "description": message,
                    "priority": priority,
                    "component": target["workComponent"],
                    "assignee_id": target.get("staffMemberId"),
                    "source_id": inquiry_id,
                },
            )
            work_item = work_result.mappings().first()
            if work_item is None:
                raise RuntimeError("The inquiry work item could not be created")
            work_item_id = str(work_item["id"])
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_work_item_link (
                      id, tenant_id, work_item_id, entity_type, entity_id,
                      relationship, created_at
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'inquiry', :inquiry_id,
                      'source', NOW()
                    )
                    ON CONFLICT (tenant_id, work_item_id, entity_type, entity_id)
                    DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "work_item_id": work_item_id,
                    "inquiry_id": inquiry_id,
                },
            )
            if requirement_id is not None:
                await connection.execute(
                    text(
                        """
                        INSERT INTO staff_work_item_link (
                          id, tenant_id, work_item_id, entity_type, entity_id,
                          relationship, created_at
                        ) VALUES (
                          :id, :tenant_id, :work_item_id, 'requirement',
                          :requirement_id, 'help_requested_for', NOW()
                        )
                        ON CONFLICT (tenant_id, work_item_id, entity_type, entity_id)
                        DO NOTHING
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "tenant_id": auth.tenant_id,
                        "work_item_id": work_item_id,
                        "requirement_id": requirement_id,
                    },
                )
            interaction_id = str(uuid4())
            interaction_result = await connection.execute(
                text(
                    """
                    INSERT INTO staff_interaction (
                      id, tenant_id, student_id, work_item_id, objective, status,
                      selected_channel, source_version, covered_source_version,
                      version, quiet_until, last_activity_at, request_key,
                      created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :student_id, :work_item_id, :objective,
                      'enrichment_pending', 'portal', 1, 0, 1,
                      NOW() + interval '5 minutes', NOW(), :request_key,
                      NOW(), NOW()
                    )
                    RETURNING id
                    """
                ),
                {
                    "id": interaction_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "work_item_id": work_item_id,
                    "objective": subject,
                    "request_key": f"portal-inquiry:{inquiry_id}",
                },
            )
            interaction = interaction_result.mappings().first()
            if interaction is None:
                raise RuntimeError("The inquiry interaction could not be created")
            interaction_id = str(interaction["id"])
            communication_id = str(uuid4())
            await connection.execute(
                text(
                    """
                    INSERT INTO communication_event (
                      id, tenant_id, student_id, channel, direction, subject,
                      body_excerpt, metadata, resolution_status, occurred_at,
                      created_at, interaction_id, source_type, source_id,
                      source_sequence, delivery_status
                    ) VALUES (
                      :id, :tenant_id, :student_id, 'portal', 'inbound', :subject,
                      :body, :metadata, 'unresolved', NOW(), NOW(), :interaction_id,
                      'student_inquiry', :source_id, 1, 'received'
                    )
                    ON CONFLICT (tenant_id, source_type, source_id)
                    WHERE source_type IS NOT NULL AND source_id IS NOT NULL
                    DO NOTHING
                    """
                ),
                {
                    "id": communication_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "subject": subject,
                    "body": message,
                    "metadata": json.dumps({"topicCode": topic_code}, separators=(",", ":")),
                    "interaction_id": interaction_id,
                    "source_id": inquiry_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO action_center_ai_job (
                      id, tenant_id, purpose, dedupe_key, student_id, work_item_id,
                      interaction_id, status, requested_source_version,
                      covered_source_version, not_before, attempts, max_attempts,
                      created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, 'interaction_enrichment', :dedupe_key,
                      :student_id, :work_item_id, :interaction_id, 'pending',
                      1, 0, NOW() + interval '5 minutes', 0, 5, NOW(), NOW()
                    )
                    ON CONFLICT (tenant_id, purpose, dedupe_key) DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "dedupe_key": f"interaction:{interaction_id}",
                    "student_id": auth.student_id,
                    "work_item_id": work_item_id,
                    "interaction_id": interaction_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_work_log (
                      id, tenant_id, work_item_id, actor_type, actor_id,
                      actor_name, action, message, occurred_at
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'system', NULL,
                      'Audentra workflow', 'created',
                      :message, NOW()
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "work_item_id": work_item_id,
                    "message": (
                        "Created from a student help request linked to an enrollment or "
                        "onboarding requirement."
                        if requirement_id is not None
                        else "Created from a student portal inquiry."
                    ),
                },
            )
            notification_id = await self._insert_staff_notification(
                connection,
                auth=auth,
                target=target,
                work_item_id=work_item_id,
                kind="new_student_inquiry",
                title="New student inquiry",
                body=f"{work_key}: {subject}",
                dedupe_key=f"work-item:{work_item_id}:attention",
            )
            response = _map_help_request(dict(row))
            response["workItemId"] = work_item_id
            response["messages"] = [
                {
                    "id": inquiry_id,
                    "direction": "student",
                    "body": message,
                    "authorName": "You",
                    "deliveryStatus": "received",
                    "createdAt": _iso(row["created_at"]),
                }
            ]
            await self._insert_audit(
                connection,
                auth,
                "student.help_request_created",
                "student_inquiry",
                inquiry_id,
                request_id,
                {
                    "topicCode": topic_code,
                    "status": "new",
                    "priority": priority,
                    "requirementId": requirement_id,
                    "workItemId": work_item_id,
                    "notificationId": notification_id,
                    "triageComponent": target["workComponent"],
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.help_request_created.v1",
                "student_inquiry",
                inquiry_id,
                1,
                request_id,
                {
                    "studentId": auth.student_id,
                    "topicCode": topic_code,
                    "status": "new",
                    "priority": priority,
                    "requirementId": requirement_id,
                    "workItemId": work_item_id,
                    "notificationId": notification_id,
                },
            )
            return response

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "help.request.create",
            {
                "topicCode": topic_code,
                "message": message,
                "requirementId": requirement_id,
            },
            200,
            handler,
        )

    async def create_student_inquiry_message(
        self,
        auth: AuthContext,
        inquiry_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        expected_version = int(payload["expectedVersion"])
        body = str(payload["body"]).strip()

        async def handler(connection: AsyncConnection) -> JsonDict:
            inquiry_result = await connection.execute(
                text(
                    """
                    SELECT inquiry.id, inquiry.topic_code, inquiry.subject,
                           inquiry.message, inquiry.status, inquiry.priority,
                           inquiry.assignee_id, inquiry.requirement_id,
                           inquiry.status_before_help, inquiry.version,
                           inquiry.created_at, inquiry.updated_at,
                           definition.responsible_office
                    FROM student_inquiry inquiry
                    LEFT JOIN student_requirement requirement
                      ON requirement.tenant_id=inquiry.tenant_id
                     AND requirement.id=inquiry.requirement_id
                    LEFT JOIN requirement_definition_version definition
                      ON definition.tenant_id=requirement.tenant_id
                     AND definition.id=requirement.requirement_definition_version_id
                    WHERE inquiry.tenant_id=:tenant_id
                      AND inquiry.student_id=:student_id
                      AND inquiry.id=:inquiry_id
                    FOR UPDATE OF inquiry
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "inquiry_id": inquiry_id,
                },
            )
            inquiry = inquiry_result.mappings().first()
            if inquiry is None:
                raise NotFoundError(
                    "STUDENT_INQUIRY_NOT_FOUND",
                    "This support conversation is no longer available",
                )
            if int(inquiry["version"]) != expected_version:
                raise ConflictError(
                    "VERSION_CONFLICT",
                    "This conversation changed in another session. Refresh before sending.",
                )
            target = await self._resolve_staff_triage_target(
                connection,
                tenant_id=auth.tenant_id,
                assignee_id=(
                    str(inquiry["assignee_id"]) if inquiry.get("assignee_id") is not None else None
                ),
                preferred_component=(
                    str(inquiry["responsible_office"])
                    if inquiry.get("responsible_office") is not None
                    else "Enrollment Support"
                ),
            )

            reply_id = str(uuid4())
            await connection.execute(
                text(
                    """
                    INSERT INTO student_inquiry_student_reply (
                      id, tenant_id, inquiry_id, student_id, request_key,
                      body, created_at
                    ) VALUES (
                      :id, :tenant_id, :inquiry_id, :student_id, :request_key,
                      :body, NOW()
                    )
                    """
                ),
                {
                    "id": reply_id,
                    "tenant_id": auth.tenant_id,
                    "inquiry_id": inquiry_id,
                    "student_id": auth.student_id,
                    "request_key": idempotency_key,
                    "body": body,
                },
            )
            updated_result = await connection.execute(
                text(
                    """
                    UPDATE student_inquiry
                    SET status='open', version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND id=:inquiry_id
                    RETURNING id, topic_code, subject, message, status, priority,
                              assignee_id, requirement_id, version, created_at, updated_at
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "inquiry_id": inquiry_id,
                },
            )
            updated = cast(Mapping[str, Any], updated_result.mappings().one())
            if inquiry.get("requirement_id") is not None:
                await connection.execute(
                    text(
                        """
                        UPDATE student_requirement
                        SET status='help_requested', version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND id=:requirement_id
                          AND status NOT IN (
                            'not_applicable','completed','waived','expired'
                          )
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "requirement_id": str(inquiry["requirement_id"]),
                    },
                )

            work_result = await connection.execute(
                text(
                    """
                    SELECT id, key, status, assignee_id, version
                    FROM staff_work_item
                    WHERE tenant_id=:tenant_id
                      AND (
                        (source_type='message' AND source_id=:inquiry_id)
                        OR EXISTS (
                          SELECT 1 FROM staff_work_item_link link
                          WHERE link.tenant_id=staff_work_item.tenant_id
                            AND link.work_item_id=staff_work_item.id
                            AND link.entity_type='inquiry'
                            AND link.entity_id=:inquiry_id
                        )
                      )
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"tenant_id": auth.tenant_id, "inquiry_id": inquiry_id},
            )
            existing_work = work_result.mappings().first()
            terminal = existing_work is None or str(existing_work["status"]) in {
                "done",
                "cancelled",
            }
            if terminal:
                work_item_id = str(uuid4())
                await connection.execute(
                    text(
                        """
                        INSERT INTO staff_work_item (
                          id, tenant_id, student_id, key, title, description,
                          status, priority, work_type, component, due_at,
                          escalated, assignee_id, source_type, source_id,
                          version, action_type,
                          selected_channel, created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :student_id, :key, :title, :description,
                          'in_progress', :priority, 'communication',
                          :component, NOW() + interval '1 day', false,
                          :assignee_id, 'message', :source_id, 1,
                          'communication_response',
                          'portal', NOW(), NOW()
                        )
                        """
                    ),
                    {
                        "id": work_item_id,
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "key": f"MSG-{reply_id.replace('-', '')[:8].upper()}",
                        "title": f"Student replied: {updated['subject']}",
                        "description": body,
                        "priority": updated["priority"],
                        "component": target["workComponent"],
                        "assignee_id": target.get("staffMemberId"),
                        "source_id": reply_id,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO staff_work_item_link (
                          id, tenant_id, work_item_id, entity_type,
                          entity_id, relationship, created_at
                        ) VALUES (
                          :id, :tenant_id, :work_item_id, 'inquiry',
                          :inquiry_id, 'reopened_from', NOW()
                        )
                        ON CONFLICT (
                          tenant_id, work_item_id, entity_type, entity_id
                        ) DO NOTHING
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "tenant_id": auth.tenant_id,
                        "work_item_id": work_item_id,
                        "inquiry_id": inquiry_id,
                    },
                )
                if inquiry.get("requirement_id") is not None:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO staff_work_item_link (
                              id, tenant_id, work_item_id, entity_type,
                              entity_id, relationship, created_at
                            ) VALUES (
                              :id, :tenant_id, :work_item_id, 'requirement',
                              :requirement_id, 'help_requested_for', NOW()
                            )
                            ON CONFLICT (
                              tenant_id, work_item_id, entity_type, entity_id
                            ) DO NOTHING
                            """
                        ),
                        {
                            "id": str(uuid4()),
                            "tenant_id": auth.tenant_id,
                            "work_item_id": work_item_id,
                            "requirement_id": str(inquiry["requirement_id"]),
                        },
                    )
                await connection.execute(
                    text(
                        """
                        INSERT INTO staff_work_log (
                          id, tenant_id, work_item_id, actor_type, actor_id,
                          actor_name, action, message, occurred_at
                        ) VALUES (
                          :id, :tenant_id, :work_item_id, 'student', :student_id,
                          'Student portal', 'created',
                          'Reopened from a student reply to a closed conversation.', NOW()
                        )
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "tenant_id": auth.tenant_id,
                        "work_item_id": work_item_id,
                        "student_id": auth.student_id,
                    },
                )
            else:
                assert existing_work is not None
                work_item_id = str(existing_work["id"])
                await connection.execute(
                    text(
                        """
                        UPDATE staff_work_item
                        SET status='in_progress', selected_channel='portal',
                            component=:component,
                            assignee_id=COALESCE(assignee_id, :assignee_id),
                            follow_up_at=NULL, completed_at=NULL, cancelled_at=NULL,
                            terminal_reason=NULL, version=version+1, updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND id=:work_item_id
                        """
                    ),
                    {
                        "tenant_id": auth.tenant_id,
                        "work_item_id": work_item_id,
                        "component": target["workComponent"],
                        "assignee_id": target.get("staffMemberId"),
                    },
                )

            interaction_result = await connection.execute(
                text(
                    """
                    SELECT id, source_version, completed_at
                    FROM staff_interaction
                    WHERE tenant_id=:tenant_id AND work_item_id=:work_item_id
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                    """
                ),
                {"tenant_id": auth.tenant_id, "work_item_id": work_item_id},
            )
            interaction = interaction_result.mappings().first()
            if interaction is None or interaction["completed_at"] is not None:
                interaction_id = str(uuid4())
                source_version = 1
                await connection.execute(
                    text(
                        """
                        INSERT INTO staff_interaction (
                          id, tenant_id, student_id, work_item_id, objective,
                          status, selected_channel, source_version,
                          covered_source_version, version, quiet_until,
                          last_activity_at, request_key, created_at, updated_at
                        ) VALUES (
                          :id, :tenant_id, :student_id, :work_item_id, :objective,
                          'enrichment_pending', 'portal', 1, 0, 1,
                          NOW() + interval '5 minutes', NOW(), :request_key,
                          NOW(), NOW()
                        )
                        """
                    ),
                    {
                        "id": interaction_id,
                        "tenant_id": auth.tenant_id,
                        "student_id": auth.student_id,
                        "work_item_id": work_item_id,
                        "objective": str(updated["subject"]),
                        "request_key": f"student-reply:{reply_id}",
                    },
                )
            else:
                interaction_id = str(interaction["id"])
                source_version = int(interaction["source_version"]) + 1
                await connection.execute(
                    text(
                        """
                        UPDATE staff_interaction
                        SET status=CASE
                              WHEN covered_source_version > 0 THEN 'stale'
                              ELSE 'enrichment_pending'
                            END,
                            source_version=:source_version,
                            quiet_until=NOW() + interval '5 minutes',
                            last_activity_at=NOW(), version=version+1,
                            updated_at=NOW()
                        WHERE tenant_id=:tenant_id AND id=:interaction_id
                        """
                    ),
                    {
                        "source_version": source_version,
                        "tenant_id": auth.tenant_id,
                        "interaction_id": interaction_id,
                    },
                )

            communication_id = str(uuid4())
            await connection.execute(
                text(
                    """
                    INSERT INTO communication_event (
                      id, tenant_id, student_id, channel, direction,
                      subject, body_excerpt, metadata, resolution_status,
                      occurred_at, created_at, interaction_id, source_type,
                      source_id, source_sequence, request_key, delivery_status
                    ) VALUES (
                      :id, :tenant_id, :student_id, 'portal', 'inbound',
                      :subject, :body, CAST(:metadata AS jsonb), 'unresolved',
                      NOW(), NOW(), :interaction_id,
                      'student_inquiry_student_reply', :source_id,
                      :source_sequence, :request_key, 'received'
                    )
                    """
                ),
                {
                    "id": communication_id,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "subject": str(updated["subject"]),
                    "body": body,
                    "metadata": json.dumps({"inquiryId": inquiry_id}, separators=(",", ":")),
                    "interaction_id": interaction_id,
                    "source_id": reply_id,
                    "source_sequence": source_version,
                    "request_key": f"student-reply:{reply_id}",
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO action_center_ai_job (
                      id, tenant_id, purpose, dedupe_key, student_id,
                      work_item_id, interaction_id, status,
                      requested_source_version, covered_source_version,
                      not_before, attempts, max_attempts, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, 'interaction_enrichment', :dedupe_key,
                      :student_id, :work_item_id, :interaction_id, 'pending',
                      :source_version, 0, NOW() + interval '5 minutes',
                      0, 5, NOW(), NOW()
                    )
                    ON CONFLICT (tenant_id, purpose, dedupe_key)
                    DO UPDATE SET
                      requested_source_version=GREATEST(
                        action_center_ai_job.requested_source_version,
                        EXCLUDED.requested_source_version
                      ),
                      status=CASE
                        WHEN action_center_ai_job.status='running' THEN 'running'
                        ELSE 'pending'
                      END,
                      not_before=CASE
                        WHEN action_center_ai_job.status IN (
                          'succeeded', 'dead_letter', 'cancelled'
                        ) THEN EXCLUDED.not_before
                        ELSE LEAST(
                          GREATEST(action_center_ai_job.not_before, EXCLUDED.not_before),
                          action_center_ai_job.created_at + interval '15 minutes'
                        )
                      END,
                      attempts=CASE
                        WHEN action_center_ai_job.status='dead_letter' THEN 0
                        ELSE action_center_ai_job.attempts
                      END,
                      created_at=CASE
                        WHEN action_center_ai_job.status IN (
                          'succeeded', 'dead_letter', 'cancelled'
                        ) THEN NOW()
                        ELSE action_center_ai_job.created_at
                      END,
                      completed_at=NULL, last_error_code=NULL,
                      last_error_message=NULL, updated_at=NOW()
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "dedupe_key": f"interaction:{interaction_id}",
                    "student_id": auth.student_id,
                    "work_item_id": work_item_id,
                    "interaction_id": interaction_id,
                    "source_version": source_version,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_work_log (
                      id, tenant_id, work_item_id, actor_type, actor_id,
                      actor_name, action, message, occurred_at
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'student', :student_id,
                      'Student portal', 'communication_recorded',
                      'Student replied in the portal.', NOW()
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "work_item_id": work_item_id,
                    "student_id": auth.student_id,
                },
            )
            notification_id = await self._insert_staff_notification(
                connection,
                auth=auth,
                target=target,
                work_item_id=work_item_id,
                kind="student_inquiry_reply",
                title="Student replied",
                body=f"{updated['subject']}: a new portal message is ready for review.",
                dedupe_key=f"inquiry-reply:{reply_id}:attention",
            )
            version = int(updated["version"])
            await self._insert_audit(
                connection,
                auth,
                "student.inquiry_message_created",
                "student_inquiry",
                inquiry_id,
                request_id,
                {
                    "submittedVersion": expected_version,
                    "committedVersion": version,
                    "reopened": str(inquiry["status"]) == "resolved",
                },
            )
            await self._insert_outbox(
                connection,
                auth,
                "student.inquiry_message_created.v1",
                "student_inquiry",
                inquiry_id,
                version,
                request_id,
                {
                    "studentId": auth.student_id,
                    "messageId": reply_id,
                    "workItemId": work_item_id,
                    "notificationId": notification_id,
                },
            )
            response = _map_help_request(updated)
            response["workItemId"] = work_item_id
            response["messages"] = await self._student_inquiry_messages(
                connection,
                tenant_id=auth.tenant_id,
                student_id=auth.student_id,
                inquiry_id=inquiry_id,
                initial_body=str(updated["message"]),
                initial_created_at=updated["created_at"],
            )
            return response

        return await self._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "help.request.message.create",
            {
                "inquiryId": inquiry_id,
                "expectedVersion": expected_version,
                "body": body,
            },
            200,
            handler,
        )

    async def _student_inquiry_messages(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: str,
        student_id: str,
        inquiry_id: str,
        initial_body: str,
        initial_created_at: object,
    ) -> list[JsonDict]:
        staff_result = await connection.execute(
            text(
                """
                SELECT reply.id, reply.response_note AS body,
                       staff.display_name AS author_name, reply.created_at
                FROM student_inquiry_reply AS reply
                JOIN staff_member AS staff
                  ON staff.tenant_id=reply.tenant_id
                 AND staff.id=reply.staff_member_id
                WHERE reply.tenant_id=:tenant_id
                  AND reply.student_id=:student_id
                  AND reply.inquiry_id=:inquiry_id
                  AND reply.notify_student=true
                ORDER BY reply.created_at, reply.id
                """
            ),
            {
                "tenant_id": tenant_id,
                "student_id": student_id,
                "inquiry_id": inquiry_id,
            },
        )
        student_result = await connection.execute(
            text(
                """
                SELECT id, body, created_at
                FROM student_inquiry_student_reply
                WHERE tenant_id=:tenant_id AND student_id=:student_id
                  AND inquiry_id=:inquiry_id
                ORDER BY created_at, id
                """
            ),
            {
                "tenant_id": tenant_id,
                "student_id": student_id,
                "inquiry_id": inquiry_id,
            },
        )
        messages: list[JsonDict] = [
            {
                "id": inquiry_id,
                "direction": "student",
                "body": initial_body,
                "authorName": "You",
                "deliveryStatus": "received",
                "createdAt": _iso(initial_created_at),
            }
        ]
        messages.extend(
            {
                "id": str(row["id"]),
                "direction": "staff",
                "body": str(row["body"]),
                "authorName": str(row["author_name"]),
                "deliveryStatus": "delivered",
                "createdAt": _iso(row["created_at"]),
            }
            for row in staff_result.mappings().all()
        )
        messages.extend(
            {
                "id": str(row["id"]),
                "direction": "student",
                "body": str(row["body"]),
                "authorName": "You",
                "deliveryStatus": "received",
                "createdAt": _iso(row["created_at"]),
            }
            for row in student_result.mappings().all()
        )
        return sorted(messages, key=lambda message: str(message["createdAt"]))

    async def list_staff_help_requests(self, auth: AuthContext) -> list[JsonDict]:
        rows = await self._all(
            """
            SELECT inquiry.id, inquiry.topic_code, inquiry.subject, inquiry.message,
                   inquiry.status, inquiry.priority, inquiry.assignee_id,
                   inquiry.version, inquiry.created_at, inquiry.updated_at,
                   student.id AS student_id, student.class_year,
                   person.first_name, person.last_name,
                   COALESCE(profile.preferred_name, person.preferred_name,
                            person.first_name) AS preferred_name,
                   latest_program.name AS program_name
            FROM student_inquiry inquiry
            JOIN student
              ON student.id=inquiry.student_id
             AND student.tenant_id=inquiry.tenant_id
            JOIN person
              ON person.id=student.person_id
             AND person.tenant_id=student.tenant_id
            LEFT JOIN student_profile profile
              ON profile.student_id=student.id
             AND profile.tenant_id=student.tenant_id
            LEFT JOIN LATERAL (
              SELECT program.name
              FROM admission_offer offer
              JOIN program
                ON program.id=offer.program_id
               AND program.tenant_id=offer.tenant_id
              WHERE offer.tenant_id=inquiry.tenant_id
                AND offer.student_id=inquiry.student_id
              ORDER BY offer.created_at DESC, offer.id DESC
              LIMIT 1
            ) latest_program ON true
            WHERE inquiry.tenant_id=:tenant_id
            ORDER BY inquiry.updated_at DESC, inquiry.id
            LIMIT 200
            """,
            {"tenant_id": auth.tenant_id},
        )
        return [
            {
                **_map_help_request(row),
                "student": {
                    "id": str(row["student_id"]),
                    "name": f"{row['first_name']} {row['last_name']}",
                    "preferredName": row["preferred_name"],
                    "programName": row.get("program_name") or "Program not available",
                    "classYear": int(row["class_year"]),
                },
            }
            for row in rows
        ]

    async def _locked_onboarding(self, connection: AsyncConnection, auth: AuthContext) -> JsonDict:
        result = await connection.execute(
            text(
                """
                SELECT student_id, status, current_step, completed_steps, payload,
                       version, completed_at, updated_at
                FROM student_onboarding
                WHERE tenant_id=:tenant_id AND student_id=:student_id
                FOR UPDATE
                """
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        row = result.mappings().first()
        if row is None:
            raise NotFoundError("STUDENT_ONBOARDING_NOT_FOUND", "Student onboarding was not found")
        return dict(row)

    async def _validate_onboarding_step(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        step: str,
        data: Mapping[str, Any],
    ) -> None:
        required_fields = ABOUT_YOU_REQUIRED_FIELDS
        required_custom_fields: tuple[str, ...] = ()
        if step == "about_you":
            configuration_result = await connection.execute(
                text(
                    """
                    SELECT document
                    FROM staff_managed_configuration_version
                    WHERE tenant_id=:tenant_id AND kind='journeys' AND active=true
                    ORDER BY version DESC LIMIT 1
                    """
                ),
                {"tenant_id": auth.tenant_id},
            )
            configuration_row = configuration_result.mappings().first()
            if configuration_row is not None:
                configuration = _onboarding_screen_configurations(
                    _mapping(configuration_row["document"])
                ).get("about_you", {})
                authored_required = _mapping(configuration).get("requiredFields")
                if isinstance(authored_required, list):
                    required_fields = tuple(
                        str(field)
                        for field in authored_required
                        if isinstance(field, str) and field in _ABOUT_YOU_CONFIGURABLE_FIELDS
                    )
                authored_fields = _mapping(configuration).get("fields")
                if isinstance(authored_fields, list):
                    required_custom_fields = tuple(
                        str(field["id"])
                        for field in authored_fields
                        if isinstance(field, Mapping)
                        and field.get("required") is True
                        and isinstance(field.get("id"), str)
                        and field["id"] not in _ABOUT_YOU_FIELD_BINDINGS
                    )
        validate_onboarding_step(
            step,
            data,
            about_you_required_fields=required_fields,
            about_you_required_custom_fields=required_custom_fields,
        )
        if step == "offer":
            result = await connection.execute(
                text(
                    """
                    SELECT 1 FROM admission_offer
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND status='accepted' LIMIT 1
                    """
                ),
                {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
            )
            if result.first() is None:
                raise ConflictError(
                    "ACCEPTED_OFFER_REQUIRED",
                    "Accept the admission offer before completing this step",
                )
        if step == "deposit" and data.get("depositChoice") == "pay_now":
            result = await connection.execute(
                text(
                    """
                    SELECT 1 FROM payment_transaction
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND type='enrollment_deposit' AND status='succeeded' LIMIT 1
                    """
                ),
                {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
            )
            if result.first() is None:
                raise ConflictError(
                    "DEPOSIT_REQUIRED",
                    "Complete the enrollment deposit before saving this step",
                )

    def _request_hash(self, auth: AuthContext, payload: object) -> str:
        encoded = _json(
            {
                "tenantId": auth.tenant_id,
                "studentId": auth.student_id,
                "requestPayload": payload,
            }
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def _run_idempotent(
        self,
        auth: AuthContext,
        idempotency_key: str,
        request_id: str,
        operation: str,
        request_payload: object,
        response_status: int,
        handler: IdempotentHandler,
    ) -> JsonDict:
        del request_id  # request lineage is recorded by the command handler.
        request_hash = self._request_hash(auth, request_payload)
        lock_key = f"{auth.tenant_id}:{auth.actor_id}:{operation}:{idempotency_key}"
        async with self.engine.begin() as connection:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
            result = await connection.execute(
                text(
                    """
                    SELECT request_hash, response_body FROM idempotency_record
                    WHERE tenant_id=:tenant_id AND actor_id=:actor_id
                      AND operation=:operation AND idempotency_key=:idempotency_key
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "actor_id": auth.actor_id,
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                },
            )
            existing = result.mappings().first()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise ConflictError(
                        "IDEMPOTENCY_KEY_REUSED",
                        "This idempotency key was already used for a different request",
                    )
                response = existing["response_body"]
                return _mapping(response)
            response = await handler(connection)
            await connection.execute(
                text(
                    """
                    INSERT INTO idempotency_record (
                      tenant_id, actor_id, operation, idempotency_key, request_hash,
                      response_status, response_body, created_at, expires_at
                    ) VALUES (
                      :tenant_id, :actor_id, :operation, :idempotency_key, :request_hash,
                      :response_status, CAST(:response_body AS jsonb), NOW(),
                      NOW()+INTERVAL '24 hours'
                    )
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "actor_id": auth.actor_id,
                    "operation": operation,
                    "idempotency_key": idempotency_key,
                    "request_hash": request_hash,
                    "response_status": response_status,
                    "response_body": _json(response),
                },
            )
            return response

    async def _resolve_staff_triage_target(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: str,
        assignee_id: str | None = None,
        preferred_component: str | None = None,
    ) -> JsonDict:
        """Resolve only reachable tenant staff targets, with deterministic fallback."""

        if assignee_id is not None:
            assignee_result = await connection.execute(
                text(
                    """
                    SELECT id, component
                    FROM staff_member
                    WHERE tenant_id=:tenant_id AND id=:staff_member_id AND active=true
                    """
                ),
                {"tenant_id": tenant_id, "staff_member_id": assignee_id},
            )
            assignee = assignee_result.mappings().first()
            if assignee is not None:
                return {
                    "staffMemberId": str(assignee["id"]),
                    "teamComponent": None,
                    "tenantWide": False,
                    "workComponent": str(assignee["component"]),
                }

        work_component: str | None = None
        if preferred_component:
            component_result = await connection.execute(
                text(
                    """
                    SELECT component
                    FROM staff_member
                    WHERE tenant_id=:tenant_id AND active=true
                      AND lower(component)=lower(:component)
                    ORDER BY component, id
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "component": preferred_component},
            )
            component = component_result.mappings().first()
            if component is not None:
                work_component = str(component["component"])

        admissions_result = await connection.execute(
            text(
                """
                SELECT component
                FROM staff_member
                WHERE tenant_id=:tenant_id AND active=true
                  AND lower(component)='admissions'
                ORDER BY component, id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )
        admissions = admissions_result.mappings().first()
        if work_component is None and admissions is not None:
            work_component = str(admissions["component"])

        fallback_result = await connection.execute(
            text(
                """
                SELECT id, component
                FROM staff_member
                WHERE tenant_id=:tenant_id AND active=true
                ORDER BY component, display_name, id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        )
        fallback = fallback_result.mappings().first()
        if work_component is None and fallback is not None:
            work_component = str(fallback["component"])

        # Unassigned triage must be visible to every active staff member in the
        # tenant. Ownership still uses a deterministic component, but the alert
        # cannot be component-scoped: a tenant may have no member in the
        # configured/preferred component, and the currently signed-in triager
        # may legitimately belong to another team.
        return {
            "staffMemberId": None,
            "teamComponent": None,
            "tenantWide": True,
            "workComponent": work_component or preferred_component or "Tenant Triage",
        }

    async def _insert_staff_notification(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        target: Mapping[str, Any],
        work_item_id: str,
        kind: str,
        title: str,
        body: str,
        dedupe_key: str,
    ) -> str:
        notification_id = str(uuid4())
        inserted_result = await connection.execute(
            text(
                """
                INSERT INTO staff_notification (
                  id, tenant_id, staff_member_id, team_component, tenant_wide,
                  kind, title, body, resource_type, resource_id, dedupe_key,
                  created_at
                ) VALUES (
                  :id, :tenant_id, :staff_member_id, :team_component, :tenant_wide,
                  :kind, :title, :body, 'staff_work_item', :work_item_id,
                  :dedupe_key, NOW()
                )
                ON CONFLICT (tenant_id, dedupe_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": notification_id,
                "tenant_id": auth.tenant_id,
                "staff_member_id": target.get("staffMemberId"),
                "team_component": target.get("teamComponent"),
                "tenant_wide": bool(target.get("tenantWide")),
                "kind": kind,
                "title": title,
                "body": body[:2_000],
                "work_item_id": work_item_id,
                "dedupe_key": dedupe_key[:240],
            },
        )
        inserted = inserted_result.mappings().first()
        if inserted is None:
            existing_result = await connection.execute(
                text(
                    """
                    SELECT id FROM staff_notification
                    WHERE tenant_id=:tenant_id AND dedupe_key=:dedupe_key
                    """
                ),
                {"tenant_id": auth.tenant_id, "dedupe_key": dedupe_key[:240]},
            )
            existing = existing_result.mappings().first()
            if existing is None:
                raise RuntimeError("The staff notification could not be persisted")
            return str(existing["id"])
        notification_id = str(inserted["id"])
        await self._insert_realtime_event(
            connection,
            tenant_id=auth.tenant_id,
            event_type="staff.notification.created",
            resource_type="staff_notification",
            resource_id=notification_id,
            work_item_id=work_item_id,
            target=target,
            payload={
                "notificationId": notification_id,
                "workItemId": work_item_id,
                "kind": kind,
                "invalidate": ["notifications", "workspace", "messages"],
            },
        )
        return notification_id

    async def _insert_realtime_event(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: str,
        event_type: str,
        resource_type: str,
        resource_id: str,
        work_item_id: str | None,
        target: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO staff_realtime_event (
                  id, tenant_id, event_type, resource_type, resource_id,
                  work_item_id, staff_member_id, team_component, tenant_wide,
                  payload, created_at
                ) VALUES (
                  :id, :tenant_id, :event_type, :resource_type, :resource_id,
                  :work_item_id, :staff_member_id, :team_component, :tenant_wide,
                  CAST(:payload AS jsonb), NOW()
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": tenant_id,
                "event_type": event_type,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "work_item_id": work_item_id,
                "staff_member_id": target.get("staffMemberId"),
                "team_component": target.get("teamComponent"),
                "tenant_wide": bool(target.get("tenantWide")),
                "payload": _json(payload),
            },
        )

    async def _insert_audit(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO audit_event (
                  id, tenant_id, actor_type, actor_id, student_id, action,
                  resource_type, resource_id, authorization_basis, request_id,
                  correlation_id, metadata
                ) VALUES (
                  :id, :tenant_id, :actor_type, :actor_id, :student_id, :action,
                  :resource_type, :resource_id, 'student_self_service', :request_id,
                  :request_id, CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": auth.tenant_id,
                "actor_type": auth.actor_type,
                "actor_id": auth.actor_id,
                "student_id": auth.student_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "request_id": request_id,
                "metadata": _json(metadata),
            },
        )

    async def _insert_outbox(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        event_name: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        request_id: str,
        data: Mapping[str, Any],
    ) -> None:
        safe_data = json.loads(_json(data))
        event_id = str(uuid4())
        await self.outbox.enqueue(
            connection,
            DomainEventEnvelope(
                event_id=event_id,
                event_name=event_name,
                occurred_at=_utc_now(),
                tenant_id=auth.tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                actor=DomainEventActor(type=auth.actor_type, id=auth.actor_id),
                correlation_id=request_id,
                causation_id=str(uuid4()),
                data=safe_data,
            ),
        )

    async def _insert_student_message(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        *,
        subject: str,
        body: str,
        kind: str = "general",
        href: str | None = None,
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO student_message (
                  id, tenant_id, student_id, subject, body, sender_name,
                  kind, href, sent_at, read_at, created_at
                ) VALUES (
                  :id, :tenant_id, :student_id, :subject, :body,
                  'Enrollment Team', :kind, :href, NOW(), NULL, NOW()
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "subject": subject,
                "body": body,
                "kind": kind,
                "href": href,
            },
        )

    async def _resolve_requirement_help_after_success(
        self,
        connection: AsyncConnection,
        *,
        auth: AuthContext,
        requirement_id: str,
        replacement_document_id: str,
        request_id: str,
    ) -> JsonDict:
        """Resolve active help/review work after a valid replacement is parsed."""

        resolution_message = "Student successfully uploaded/parsed replacement document"
        inquiry_result = await connection.execute(
            text(
                """
                SELECT id
                FROM student_inquiry
                WHERE tenant_id=:tenant_id AND student_id=:student_id
                  AND requirement_id=:requirement_id
                  AND status IN ('new','open','waiting_on_student')
                ORDER BY created_at, id
                FOR UPDATE
                """
            ),
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "requirement_id": requirement_id,
            },
        )
        active_inquiry_ids = {str(row["id"]) for row in inquiry_result.mappings().all()}
        work_item_result = await connection.execute(
            text(
                """
                SELECT item.id, item.assignee_id, item.component,
                       CASE WHEN item.source_type='message' AND EXISTS (
                         SELECT 1 FROM student_inquiry inquiry
                         WHERE inquiry.tenant_id=item.tenant_id
                           AND inquiry.id=item.source_id
                           AND inquiry.student_id=:student_id
                           AND inquiry.requirement_id=:requirement_id
                           AND inquiry.status IN ('new','open','waiting_on_student')
                       ) THEN true ELSE false END AS help_item
                FROM staff_work_item item
                WHERE item.tenant_id=:tenant_id
                  AND item.student_id=:student_id
                  AND item.status NOT IN ('done','cancelled')
                  AND (
                    (
                      item.source_type='message'
                      AND EXISTS (
                        SELECT 1 FROM student_inquiry inquiry
                        WHERE inquiry.tenant_id=item.tenant_id
                          AND inquiry.id=item.source_id
                          AND inquiry.student_id=:student_id
                          AND inquiry.requirement_id=:requirement_id
                          AND inquiry.status IN ('new','open','waiting_on_student')
                      )
                    )
                    OR (
                      item.blocker_code='document_parse_failure'
                      AND EXISTS (
                        SELECT 1 FROM staff_work_item_link link
                        WHERE link.tenant_id=item.tenant_id
                          AND link.work_item_id=item.id
                          AND link.entity_type='requirement'
                          AND link.entity_id=:requirement_id
                      )
                    )
                  )
                ORDER BY item.created_at, item.id
                FOR UPDATE
                """
            ),
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "requirement_id": requirement_id,
            },
        )
        work_items = [dict(row) for row in work_item_result.mappings().all()]

        resolved_inquiry_count = 0
        if active_inquiry_ids:
            resolved_result = await connection.execute(
                text(
                    """
                    UPDATE student_inquiry
                    SET status='resolved', resolved_at=NOW(),
                        resolution_reason='replacement_document_parsed',
                        status_before_help=NULL, version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND student_id=:student_id
                      AND requirement_id=:requirement_id
                      AND status IN ('new','open','waiting_on_student')
                    RETURNING id
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "requirement_id": requirement_id,
                },
            )
            resolved_inquiry_count = len(resolved_result.mappings().all())

        resolved_review_count = 0
        for item in work_items:
            work_item_id = str(item["id"])
            updated_result = await connection.execute(
                text(
                    """
                    UPDATE staff_work_item
                    SET status='done', outcome_code='document_uploaded',
                        resolution_code='replacement_document_parsed',
                        next_step=:message, blocker_code=NULL,
                        blocker_detail=NULL, blocker_review_at=NULL,
                        completed_at=NOW(), cancelled_at=NULL,
                        version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:work_item_id
                      AND status NOT IN ('done','cancelled')
                    RETURNING version
                    """
                ),
                {
                    "tenant_id": auth.tenant_id,
                    "work_item_id": work_item_id,
                    "message": resolution_message,
                },
            )
            updated_item = updated_result.mappings().first()
            if updated_item is None:
                continue
            if not bool(item["help_item"]):
                resolved_review_count += 1
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_work_log (
                      id, tenant_id, work_item_id, actor_type, actor_id,
                      actor_name, action, message, occurred_at
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'student', :actor_id,
                      'Student self-service', 'status_changed', :message, NOW()
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "work_item_id": work_item_id,
                    "actor_id": auth.actor_id,
                    "message": resolution_message,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_work_item_link (
                      id, tenant_id, work_item_id, entity_type, entity_id,
                      relationship, created_at
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'document',
                      :document_id, 'resolved_by_replacement', NOW()
                    )
                    ON CONFLICT (tenant_id, work_item_id, entity_type, entity_id)
                    DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "work_item_id": work_item_id,
                    "document_id": replacement_document_id,
                },
            )
            target = await self._resolve_staff_triage_target(
                connection,
                tenant_id=auth.tenant_id,
                assignee_id=(
                    str(item["assignee_id"]) if item.get("assignee_id") is not None else None
                ),
                preferred_component=str(item["component"]),
            )
            notification_id = await self._insert_staff_notification(
                connection,
                auth=auth,
                target=target,
                work_item_id=work_item_id,
                kind="student_document_recovered",
                title="Student document issue resolved",
                body=resolution_message,
                dedupe_key=(f"work-item:{work_item_id}:replacement:{replacement_document_id}"),
            )
            work_item_version = int(updated_item["version"])
            event_data = {
                "workItemId": work_item_id,
                "studentId": auth.student_id,
                "requirementId": requirement_id,
                "replacementDocumentId": replacement_document_id,
                "notificationId": notification_id,
                "message": resolution_message,
            }
            await self._insert_audit(
                connection,
                auth,
                "staff_work_item.auto_resolved_by_document",
                "staff_work_item",
                work_item_id,
                request_id,
                event_data,
            )
            await self._insert_outbox(
                connection,
                auth,
                "staff.work_item_auto_resolved_by_document.v1",
                "staff_work_item",
                work_item_id,
                work_item_version,
                request_id,
                event_data,
            )

        return {
            "helpRequestsResolved": resolved_inquiry_count,
            "reviewItemsResolved": resolved_review_count,
        }

    async def _ensure_document_review_work_item(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document: Mapping[str, Any],
        *,
        parse_failure: bool,
        failure_code: str | None,
        request_id: str,
    ) -> JsonDict:
        """Materialize one durable staff task for a reviewable document.

        The unique source index also protects the lazy staff-action-center
        reconciler, so either path may run first without creating duplicates.
        """

        category = str(document.get("category") or "other")
        preferred_component, priority = (
            ("Financial Aid", "urgent")
            if category == "financial_aid"
            else (("Student Health", "high") if category == "health" else ("Registrar", "high"))
        )
        target = await self._resolve_staff_triage_target(
            connection,
            tenant_id=auth.tenant_id,
            preferred_component=preferred_component,
        )
        document_id = str(document["id"])
        existing_result = await connection.execute(
            text(
                """
                SELECT status
                FROM staff_work_item
                WHERE tenant_id=:tenant_id AND source_type='document'
                  AND source_id=:document_id
                FOR UPDATE
                """
            ),
            {"tenant_id": auth.tenant_id, "document_id": document_id},
        )
        existing = existing_result.mappings().first()
        reopened = bool(
            parse_failure
            and existing is not None
            and str(existing["status"]) in {"done", "cancelled"}
        )
        work_item_id = str(uuid4())
        inserted = await connection.execute(
            text(
                """
                INSERT INTO staff_work_item (
                  id, tenant_id, student_id, key, title, description,
                  status, priority, work_type, component, due_at,
                  escalated, assignee_id, source_type, source_id, version,
                  action_type, blocker_code, blocker_detail
                ) VALUES (
                  :id, :tenant_id, :student_id, :key, :title, :description,
                  'todo', :priority, 'document_review', :component,
                  NOW() + INTERVAL '2 days', false, :assignee_id,
                  'document', :document_id, 1, 'document_review',
                  :blocker_code, :blocker_detail
                )
                ON CONFLICT (tenant_id, source_type, source_id)
                WHERE source_type IS NOT NULL AND source_id IS NOT NULL
                DO UPDATE SET
                  title = CASE
                    WHEN :parse_failure THEN EXCLUDED.title
                    ELSE staff_work_item.title
                  END,
                  description = CASE
                    WHEN :parse_failure THEN EXCLUDED.description
                    ELSE staff_work_item.description
                  END,
                  status = CASE
                    WHEN :parse_failure
                     AND staff_work_item.status IN ('done','cancelled') THEN 'todo'
                    ELSE staff_work_item.status
                  END,
                  priority = CASE
                    WHEN :parse_failure THEN EXCLUDED.priority
                    ELSE staff_work_item.priority
                  END,
                  component = CASE
                    WHEN :parse_failure THEN EXCLUDED.component
                    ELSE staff_work_item.component
                  END,
                  assignee_id = CASE
                    WHEN :parse_failure THEN COALESCE(
                      staff_work_item.assignee_id, EXCLUDED.assignee_id
                    )
                    ELSE staff_work_item.assignee_id
                  END,
                  due_at = CASE
                    WHEN :parse_failure THEN EXCLUDED.due_at
                    ELSE staff_work_item.due_at
                  END,
                  blocker_code = CASE
                    WHEN :parse_failure THEN :blocker_code
                    ELSE staff_work_item.blocker_code
                  END,
                  blocker_detail = CASE
                    WHEN :parse_failure THEN :blocker_detail
                    ELSE staff_work_item.blocker_detail
                  END,
                  completed_at = CASE
                    WHEN :parse_failure
                     AND staff_work_item.status IN ('done','cancelled') THEN NULL
                    ELSE staff_work_item.completed_at
                  END,
                  cancelled_at = CASE
                    WHEN :parse_failure
                     AND staff_work_item.status IN ('done','cancelled') THEN NULL
                    ELSE staff_work_item.cancelled_at
                  END,
                  terminal_reason = CASE
                    WHEN :parse_failure
                     AND staff_work_item.status IN ('done','cancelled') THEN NULL
                    ELSE staff_work_item.terminal_reason
                  END,
                  outcome_code = CASE
                    WHEN :parse_failure
                     AND staff_work_item.status IN ('done','cancelled') THEN NULL
                    ELSE staff_work_item.outcome_code
                  END,
                  resolution_code = CASE
                    WHEN :parse_failure
                     AND staff_work_item.status IN ('done','cancelled') THEN NULL
                    ELSE staff_work_item.resolution_code
                  END,
                  next_step = CASE
                    WHEN :parse_failure
                     AND staff_work_item.status IN ('done','cancelled') THEN NULL
                    ELSE staff_work_item.next_step
                  END,
                  version = CASE
                    WHEN :parse_failure THEN staff_work_item.version + 1
                    ELSE staff_work_item.version
                  END,
                  updated_at = CASE
                    WHEN :parse_failure THEN NOW() ELSE staff_work_item.updated_at
                  END
                RETURNING id, version, (xmax = 0) AS inserted
                """
            ),
            {
                "id": work_item_id,
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "key": f"DOC-{document_id.replace('-', '')[:8].upper()}",
                "title": (
                    f"Human review needed: {document['file_name']}"
                    if parse_failure
                    else f"Review {document['file_name']}"
                ),
                "description": (
                    "The original is safely stored, but automatic parsing did not complete. "
                    "Review the original and either record the result or retry extraction."
                    if parse_failure
                    else (
                        "Verify the stored original, extracted evidence, and proposed record "
                        "matches."
                    )
                ),
                "priority": priority,
                "component": target["workComponent"],
                "assignee_id": target.get("staffMemberId"),
                "document_id": document_id,
                "blocker_code": "document_parse_failure" if parse_failure else None,
                "blocker_detail": (
                    f"Automatic parsing failed ({failure_code or 'unknown'}); original retained."
                    if parse_failure
                    else None
                ),
                "parse_failure": parse_failure,
            },
        )
        work_item = inserted.mappings().first()
        if work_item is None:
            raise RuntimeError("The document review work item could not be persisted")
        work_item_id = str(work_item["id"])
        created = bool(work_item["inserted"])
        requirement_value = document.get("requirement_id")
        if requirement_value is not None:
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_work_item_link (
                      id, tenant_id, work_item_id, entity_type, entity_id,
                      relationship, created_at
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'requirement',
                      :requirement_id, :relationship, NOW()
                    )
                    ON CONFLICT (tenant_id, work_item_id, entity_type, entity_id)
                    DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "work_item_id": work_item_id,
                    "requirement_id": str(requirement_value),
                    "relationship": ("parse_failure" if parse_failure else "document_submission"),
                },
            )
        if created or parse_failure:
            await connection.execute(
                text(
                    """
                    INSERT INTO staff_work_log (
                      id, tenant_id, work_item_id, actor_type, actor_id,
                      actor_name, action, message, occurred_at
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'system', NULL,
                      'Audentra workflow', :action, :message, NOW()
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "work_item_id": work_item_id,
                    "action": "created" if created else "status_changed",
                    "message": (
                        (
                            "Automatic document parsing failed; the original was retained "
                            "and human review was requested."
                            if created
                            else (
                                "A document parsing retry failed; the original was retained "
                                + (
                                    "and the human-review task was reopened."
                                    if reopened
                                    else "and the existing human-review task was refreshed."
                                )
                            )
                        )
                        if parse_failure
                        else "Created when the student document entered staff review."
                    ),
                },
            )

        notification_id: str | None = None
        if created or parse_failure:
            notification_id = await self._insert_staff_notification(
                connection,
                auth=auth,
                target=target,
                work_item_id=work_item_id,
                kind=("document_parse_review" if parse_failure else "document_review_ready"),
                title=(
                    "Document parsing needs human review"
                    if parse_failure
                    else "Student document ready for review"
                ),
                body=(
                    f"{document['file_name']} could not be parsed automatically. "
                    "The original is safe and available for review or retry."
                    if parse_failure
                    else f"{document['file_name']} is ready for staff review."
                ),
                dedupe_key=(
                    f"work-item:{work_item_id}:parse-failure:{request_id}"
                    if parse_failure
                    else f"work-item:{work_item_id}:attention"
                ),
            )

        if parse_failure:
            details = {
                "documentId": document_id,
                "studentId": auth.student_id,
                "workItemId": work_item_id,
                "requirementId": document.get("requirement_id"),
                "notificationId": notification_id,
                "failureCode": failure_code,
                "originalRetained": True,
                "workItemCreated": created,
                "workItemReopened": reopened,
            }
            await self._insert_audit(
                connection,
                auth,
                "document.human_review_requested",
                "document_record",
                document_id,
                request_id,
                details,
            )
            await self._insert_outbox(
                connection,
                auth,
                "document.human_review_requested.v1",
                "document_record",
                document_id,
                1,
                request_id,
                details,
            )

        return {
            "created": created,
            "reopened": reopened,
            "workItemId": work_item_id,
            "notificationId": notification_id,
        }

    async def _complete_requirement(
        self, connection: AsyncConnection, auth: AuthContext, requirement_code: str
    ) -> None:
        result = await connection.execute(
            text(
                """
                UPDATE student_requirement sr SET status='completed', progress_percent=100,
                  version=sr.version+1, updated_at=NOW()
                FROM requirement_definition_version rdv, enrollment_journey journey
                WHERE sr.tenant_id=:tenant_id AND sr.journey_id=journey.id
                  AND journey.student_id=:student_id
                  AND sr.requirement_definition_version_id=rdv.id
                  AND rdv.code=:requirement_code
                  AND sr.retired_at IS NULL
                  AND sr.status NOT IN ('completed','waived','not_applicable')
                RETURNING sr.id
                """
            ),
            {
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "requirement_code": requirement_code,
            },
        )
        completed_rows = list(result.mappings().all())
        for row in completed_rows:
            await self._award_rewards(
                connection,
                auth,
                "requirement_completed",
                requirement_code,
                str(row["id"]),
                {},
            )
        if completed_rows:
            if requirement_code == "enrollment_deposit":
                subject = "Enrollment deposit received"
                body = "Your enrollment deposit was received and the task is complete."
                kind = "payment"
                href = "/financials"
            else:
                subject = f"{requirement_code.replace('_', ' ').title()} complete"
                body = "This enrollment task has been marked complete."
                kind = "requirement_completed"
                href = "/enrollment/requirements/" + REQUIREMENT_SLUGS.get(
                    requirement_code, requirement_code.replace("_", "-")
                )
            await self._insert_student_message(
                connection,
                auth,
                subject=subject,
                body=body,
                kind=kind,
                href=href,
            )
        await connection.execute(
            text(
                """
                UPDATE student_requirement candidate SET status='ready',
                  version=candidate.version+1, updated_at=NOW()
                FROM requirement_definition_version definition,
                     enrollment_journey journey
                WHERE candidate.tenant_id=:tenant_id
                  AND candidate.journey_id=journey.id AND journey.student_id=:student_id
                  AND candidate.requirement_definition_version_id=definition.id
                  AND candidate.retired_at IS NULL
                  AND candidate.status='blocked'
                  AND NOT EXISTS (
                    SELECT 1 FROM unnest(definition.depends_on_codes) dependency(code)
                    WHERE NOT EXISTS (
                      SELECT 1 FROM student_requirement prerequisite
                      JOIN requirement_definition_version prerequisite_definition
                        ON prerequisite_definition.id=prerequisite.requirement_definition_version_id
                       AND prerequisite_definition.tenant_id=prerequisite.tenant_id
                      WHERE prerequisite.tenant_id=:tenant_id
                        AND prerequisite.journey_id=candidate.journey_id
                        AND prerequisite_definition.code=dependency.code
                        AND prerequisite.retired_at IS NULL
                        AND prerequisite.status IN ('completed','waived','not_applicable')
                    )
                  )
                """
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        completed_journeys = await connection.execute(
            text(
                """
                UPDATE enrollment_journey journey
                SET status='completed', version=journey.version+1, updated_at=NOW()
                WHERE journey.tenant_id=:tenant_id
                  AND journey.student_id=:student_id
                  AND journey.status NOT IN ('completed','cancelled')
                  AND (
                    EXISTS (
                      SELECT 1 FROM journey_definition_version definition
                      WHERE definition.id=journey.journey_definition_version_id
                        AND definition.tenant_id=journey.tenant_id
                        AND NOT definition.onboarding_required
                    )
                    OR EXISTS (
                      SELECT 1 FROM student_onboarding onboarding
                      WHERE onboarding.tenant_id=journey.tenant_id
                        AND onboarding.student_id=journey.student_id
                        AND onboarding.status='completed'
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM student_requirement requirement
                    WHERE requirement.tenant_id=journey.tenant_id
                      AND requirement.journey_id=journey.id
                      AND requirement.retired_at IS NULL
                      AND requirement.status NOT IN (
                        'not_applicable','completed','waived','expired'
                      )
                  )
                RETURNING journey.id
                """
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if completed_journeys.mappings().first() is not None:
            await self._insert_student_message(
                connection,
                auth,
                subject="Enrollment complete",
                body="All required enrollment tasks are complete.",
                kind="enrollment_completed",
                href="/dashboard",
            )

    async def _award_rewards(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        trigger_type: str,
        trigger_key: str,
        source_key: str,
        properties: Mapping[str, Any],
    ) -> int:
        result = await connection.execute(
            text(
                """
                SELECT id, points, max_awards_per_student FROM tenant_reward_rule
                WHERE tenant_id=:tenant_id AND trigger_type=:trigger_type
                  AND trigger_key=:trigger_key AND enabled=true
                  AND (starts_at IS NULL OR starts_at<=NOW())
                  AND (ends_at IS NULL OR ends_at>NOW())
                  AND CAST(:properties AS jsonb) @> trigger_properties
                ORDER BY display_order, id FOR UPDATE
                """
            ),
            {
                "tenant_id": auth.tenant_id,
                "trigger_type": trigger_type,
                "trigger_key": trigger_key,
                "properties": _json(properties),
            },
        )
        awarded = 0
        for rule in result.mappings().all():
            inserted = await connection.execute(
                text(
                    """
                    INSERT INTO student_reward_ledger (
                      id, tenant_id, student_id, reward_rule_id, source_type,
                      source_key, points, metadata, awarded_at
                    ) SELECT :id, :tenant_id, :student_id, :rule_id, :trigger_type,
                      :source_key, :points, CAST(:metadata AS jsonb), NOW()
                    WHERE (SELECT COUNT(*) FROM student_reward_ledger existing
                      WHERE existing.tenant_id=:tenant_id
                        AND existing.student_id=:student_id
                        AND existing.reward_rule_id=:rule_id) < :maximum
                    ON CONFLICT (tenant_id, student_id, reward_rule_id, source_key)
                    DO NOTHING RETURNING points
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "rule_id": rule["id"],
                    "trigger_type": trigger_type,
                    "source_key": source_key,
                    "points": int(rule["points"]),
                    "metadata": _json({"triggerKey": trigger_key, "properties": properties}),
                    "maximum": int(rule["max_awards_per_student"]),
                },
            )
            awarded += sum(int(row["points"]) for row in inserted.mappings().all())
        return awarded

    async def _reconcile_authoritative_rewards(
        self, connection: AsyncConnection, auth: AuthContext
    ) -> None:
        onboarding = await connection.execute(
            text(
                """SELECT status FROM student_onboarding
                   WHERE tenant_id=:tenant_id AND student_id=:student_id"""
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        row = onboarding.mappings().first()
        if row is not None and row["status"] == "completed":
            await self._award_rewards(
                connection, auth, "onboarding_completed", "onboarding", auth.student_id, {}
            )
        requirements = await connection.execute(
            text(
                """
                SELECT sr.id, rdv.code FROM student_requirement sr
                JOIN enrollment_journey journey
                  ON journey.id=sr.journey_id AND journey.tenant_id=sr.tenant_id
                JOIN requirement_definition_version rdv
                  ON rdv.id=sr.requirement_definition_version_id
                 AND rdv.tenant_id=sr.tenant_id
                WHERE sr.tenant_id=:tenant_id AND journey.student_id=:student_id
                  AND sr.status='completed'
                """
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        for requirement in requirements.mappings().all():
            await self._award_rewards(
                connection,
                auth,
                "requirement_completed",
                str(requirement["code"]),
                str(requirement["id"]),
                {},
            )

    async def _get_reward_summary(self, auth: AuthContext) -> JsonDict | None:
        row = await self._one(
            """
            SELECT program.point_name, program.points_per_usd,
                   COALESCE(SUM(ledger.points), 0)::bigint AS lifetime_points
            FROM tenant_reward_program program
            LEFT JOIN student_reward_ledger ledger
              ON ledger.tenant_id=program.tenant_id AND ledger.student_id=:student_id
            WHERE program.tenant_id=:tenant_id AND program.enabled=true
            GROUP BY program.point_name, program.points_per_usd
            """,
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
        if row is None:
            return None
        lifetime = int(row["lifetime_points"])
        points_per_usd = int(row["points_per_usd"])
        return {
            "pointName": row["point_name"],
            "pointsPerUsd": points_per_usd,
            "lifetimePoints": lifetime,
            "bookstoreCreditCents": math.floor(lifetime * 100 / points_per_usd),
        }

    @staticmethod
    def _category_for_document_type(document_type: str) -> str:
        return {
            "transcript": "transcript",
            "identity": "identity",
            "financial_aid": "financial_aid",
            "ferpa": "consent",
            "immunization": "health",
            "residency": "residency",
            "other": "other",
        }.get(document_type, "other")

    @staticmethod
    def _safe_profile_projection(
        fields: Sequence[Any], accepted_field_keys: Sequence[str]
    ) -> JsonDict:
        accepted = set(accepted_field_keys)
        projection: JsonDict = {}
        for item in fields:
            if not isinstance(item, dict) or item.get("key") not in accepted:
                continue
            value = str(item.get("value", "")).strip()
            if not value:
                continue
            key = item["key"]
            if key == "preferred_name" and len(value) <= 120:
                projection["preferredName"] = value
            elif key == "pronouns" and len(value) <= 80:
                projection["pronouns"] = value
            elif (
                key == "mobile_phone"
                and 7 <= len(value) <= 32
                and all(character.isdigit() or character in "+ ()-" for character in value)
            ):
                projection["mobilePhone"] = value
            elif key == "communication_preference" and value.lower() in {"email", "sms"}:
                projection["communicationPreference"] = value.lower()
        return projection

    async def _persist_transcript_credits(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document_id: str,
        extraction: Mapping[str, Any],
    ) -> None:
        for item in _list(extraction.get("courses")):
            if not isinstance(item, dict) or not item.get("title"):
                continue
            source_label = f"{item.get('sourceCode') or ''} {item['title']}".lower()
            source_type = (
                "ap" if "ap " in source_label else "ib" if "ib " in source_label else "transcript"
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO student_transcript_credit (
                      id, tenant_id, student_id, source_document_id, source_type,
                      source_code, title, grade_or_score, credits, institution_name,
                      evidence, reviewed_at
                    ) SELECT CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                      CAST(:student_id AS uuid), CAST(:document_id AS uuid),
                      CAST(:source_type AS varchar), CAST(:source_code AS varchar),
                      CAST(:title AS varchar), CAST(:grade_or_score AS varchar),
                      CAST(:credits AS numeric), CAST(:institution_name AS varchar),
                      CAST(:evidence AS jsonb), NOW()
                    WHERE NOT EXISTS (
                      SELECT 1 FROM student_transcript_credit credit
                      WHERE credit.tenant_id=CAST(:tenant_id AS uuid)
                        AND credit.student_id=CAST(:student_id AS uuid)
                        AND credit.source_document_id=CAST(:document_id AS uuid)
                        AND COALESCE(credit.source_code,'')=
                          COALESCE(CAST(:source_code AS varchar),'')
                        AND credit.title=CAST(:title AS varchar)
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                    "source_type": source_type,
                    "source_code": item.get("sourceCode"),
                    "title": item["title"],
                    "grade_or_score": item.get("score", item.get("grade")),
                    "credits": item.get("credits"),
                    "institution_name": extraction.get("institutionName"),
                    "evidence": _json(
                        {
                            "term": item.get("term"),
                            "confidence": item.get("confidence"),
                            "projection": "automatic_transcript_extraction",
                        }
                    ),
                },
            )

    async def _project_course_exemptions(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document_id: str,
        extraction: Mapping[str, Any],
    ) -> None:
        evaluation = extraction.get("courseExemptionEvaluation")
        if not isinstance(evaluation, dict):
            return
        for item in _list(evaluation.get("decisions")):
            if (
                not isinstance(item, dict)
                or item.get("status") not in {"matched", "needs_review"}
                or not item.get("targetCourseId")
                or not item.get("equivalencyRuleId")
            ):
                continue
            status = "suggested" if item["status"] == "matched" else "needs_review"
            await connection.execute(
                text(
                    """
                    INSERT INTO course_exemption_recommendation (
                      id, tenant_id, student_id, program_id, catalog_version_id,
                      transcript_credit_id, target_course_id, equivalency_rule_id,
                      status, confidence, rationale
                    ) SELECT :id, credit.tenant_id, credit.student_id, offer.program_id,
                      rule.catalog_version_id, credit.id, rule.target_course_id, rule.id,
                      :status, :confidence, :rationale
                    FROM student_transcript_credit credit
                    JOIN admission_offer offer ON offer.tenant_id=credit.tenant_id
                      AND offer.student_id=credit.student_id
                    JOIN course_equivalency_rule rule ON rule.tenant_id=credit.tenant_id
                      AND rule.id=:rule_id AND rule.target_course_id=:target_course_id
                      AND rule.catalog_version_id=:catalog_version_id AND rule.active=true
                    WHERE credit.tenant_id=:tenant_id AND credit.student_id=:student_id
                      AND credit.source_document_id=:document_id
                      AND COALESCE(credit.source_code,'')=
                        COALESCE(CAST(:source_code AS varchar),'')
                      AND credit.title=:source_title
                    ON CONFLICT (
                      tenant_id, student_id, transcript_credit_id,
                      target_course_id, equivalency_rule_id
                    ) DO UPDATE SET
                      status=CASE WHEN course_exemption_recommendation.status
                        IN ('approved','denied') THEN course_exemption_recommendation.status
                        ELSE EXCLUDED.status END,
                      confidence=EXCLUDED.confidence, rationale=EXCLUDED.rationale,
                      version=course_exemption_recommendation.version+1, updated_at=NOW()
                    """
                ),
                {
                    "id": str(uuid4()),
                    "status": status,
                    "confidence": item.get("confidence", 0),
                    "rationale": item.get("rationale", ""),
                    "rule_id": item["equivalencyRuleId"],
                    "target_course_id": item["targetCourseId"],
                    "catalog_version_id": evaluation.get("catalogVersionId"),
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                    "document_id": document_id,
                    "source_code": item.get("sourceCode"),
                    "source_title": item.get("sourceTitle", ""),
                },
            )

    async def _persist_immunization_evaluation(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        document_id: str,
        extraction: Mapping[str, Any],
    ) -> None:
        evaluation = extraction.get("immunizationCompliance")
        if not isinstance(evaluation, dict):
            return
        generated_at = evaluation.get("generatedAt")
        await connection.execute(
            text(
                """
                INSERT INTO student_immunization_evaluation (
                  id, tenant_id, student_id, source_document_id,
                  policy_version_id, result, generated_at
                ) SELECT :id, :tenant_id, :student_id, :document_id, policy.id,
                  CAST(:result AS jsonb), :generated_at
                FROM immunization_policy_version policy
                WHERE policy.id=:policy_version_id AND policy.tenant_id=:tenant_id
                  AND policy.status='published'
                ON CONFLICT (
                  tenant_id, student_id, source_document_id, policy_version_id
                ) DO UPDATE SET result=EXCLUDED.result, generated_at=EXCLUDED.generated_at
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": auth.tenant_id,
                "student_id": auth.student_id,
                "document_id": document_id,
                "policy_version_id": evaluation.get("policyVersionId"),
                "result": _json(evaluation),
                "generated_at": (
                    datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
                    if generated_at
                    else _utc_now()
                ),
            },
        )
        await connection.execute(
            text(
                """
                UPDATE student_portal_projection SET projection_version=projection_version+1,
                  source_updated_at=NOW()
                WHERE tenant_id=:tenant_id AND student_id=:student_id
                """
            ),
            {"tenant_id": auth.tenant_id, "student_id": auth.student_id},
        )
