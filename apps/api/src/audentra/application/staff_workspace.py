"""Stateless staff-workspace composition from canonical repository projections."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import yaml  # type: ignore[import-untyped]

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, ConflictError, NotFoundError
from audentra.infrastructure.postgres.managed_configuration_repository import (
    academic_courses,
    draft_managed_configuration_document,
)

JsonDict = dict[str, Any]

_INTERACTION_TYPES = {
    "information": "information",
    "approval": "approval",
    "form": "form",
    "single_select": "single_select",
    "multiple_select": "multiple_select",
    "selection_flow": "selection_flow",
    "upload_file": "upload_file",
    "file_upload": "upload_file",
    "signature": "signature",
    "e_signature": "signature",
    "esignature": "signature",
    "docusign": "signature",
    "docu_sign": "signature",
    "payment": "payment",
    "scheduling": "scheduling",
}
_SUBMISSION_TYPES = {
    "information": "none",
    "approval": "form",
    "form": "form",
    "single_select": "form",
    "multiple_select": "form",
    "selection_flow": "form",
    "upload_file": "document",
    "signature": "form",
    "payment": "payment",
    "scheduling": "appointment",
}


_ATTENTION_ORDER = {"none": 0, "watch": 1, "attention": 2, "urgent": 3}


def _attention_level(item: Mapping[str, Any]) -> str:
    return str(cast(Mapping[str, Any], item.get("attention", {})).get("level", "none"))


def _attention_rank(item: Mapping[str, Any]) -> tuple[int, int]:
    """Strongest attention level first, then the most open work."""

    return (
        _ATTENTION_ORDER.get(_attention_level(item), 0),
        int(item.get("openWorkItems", 0) or 0),
    )


_OPEN_WORK_STATUSES = {"todo", "in_progress", "follow_up_required", "blocked"}
_PERSONAL_COUNT_KEYS = (
    "open",
    "overdue",
    "dueToday",
    "urgent",
    "escalated",
    "todo",
    "inProgress",
    "followUpRequired",
    "blocked",
    "stale",
    "students",
)


def _personal_counts(mine: Mapping[str, Any]) -> dict[str, int]:
    """The reader's own counts, straight from the board's SQL scope counts."""

    return {key: int(mine.get(key, 0) or 0) for key in _PERSONAL_COUNT_KEYS}


def _personal_counts_from_items(
    tasks: Sequence[Mapping[str, Any]], student_ids: set[str]
) -> dict[str, int]:
    """Fallback for stores that serve no scope counts: count the page itself."""

    def signal(task: Mapping[str, Any], name: str) -> bool:
        return bool(cast(Mapping[str, Any], task.get("signals") or {}).get(name))

    open_tasks = [task for task in tasks if str(task.get("status")) in _OPEN_WORK_STATUSES]
    return {
        "open": len(open_tasks),
        "overdue": sum(signal(task, "overdue") for task in open_tasks),
        "dueToday": 0,
        "urgent": sum(task.get("priority") == "urgent" for task in open_tasks),
        "escalated": sum(bool(task.get("escalated")) for task in open_tasks),
        "todo": sum(task.get("status") == "todo" for task in open_tasks),
        "inProgress": sum(task.get("status") == "in_progress" for task in open_tasks),
        "followUpRequired": sum(task.get("status") == "follow_up_required" for task in open_tasks),
        "blocked": sum(task.get("status") == "blocked" for task in open_tasks),
        "stale": sum(signal(task, "stale") for task in open_tasks),
        "students": len(student_ids),
    }


def compose_staff_workspace(
    auth: AuthContext,
    *,
    action_center: Mapping[str, Any],
    student: Mapping[str, Any],
    campus_life: Mapping[str, Any],
    inquiries: Sequence[Mapping[str, Any]],
    cohort: Sequence[Mapping[str, Any]],
    managed_content: Mapping[str, Any],
    configurations: Mapping[str, Mapping[str, Any]],
    generated_at: str,
    personal_items: Sequence[Mapping[str, Any]] | None = None,
    personal_page: Mapping[str, Any] | None = None,
) -> JsonDict:
    """Build one response exclusively from durable reads and pure projections.

    ``personal_items`` is the first page of the reader's own open work in
    attention order and ``personal_page`` its page envelope; both come from
    the same board read the Task Board uses (``assignee=me``), never from the
    roster, so the personal Action Center shows every item the reader owns.
    """

    _require_staff(auth)
    staff = _mapping_list(action_center.get("staff"))
    current_staff = next(
        (member for member in staff if str(member.get("id")) == auth.actor_id),
        None,
    )
    if current_staff is None:
        raise NotFoundError(
            "STAFF_IDENTITY_NOT_PROVISIONED",
            "The authenticated staff identity is not provisioned for this tenant",
        )

    canonical_cohort = [copy.deepcopy(dict(item)) for item in cohort]
    items = _mapping_list(action_center.get("items"))
    if personal_items is not None:
        personal_tasks = [copy.deepcopy(dict(item)) for item in personal_items]
    else:
        personal_tasks = [
            item
            for item in items
            if str(cast(Mapping[str, Any], item.get("assignee") or {}).get("id")) == auth.actor_id
            and str(item.get("status")) in _OPEN_WORK_STATUSES
        ]
    personal_student_ids = {
        str(cast(Mapping[str, Any], task.get("student") or {}).get("id")) for task in personal_tasks
    }
    # Roster rows for the students in the reader's queue (when the roster
    # page carries them), strongest attention first. The queue itself is
    # complete; this is context for it.
    personal_students = [
        item for item in canonical_cohort if str(item.get("id")) in personal_student_ids
    ]
    personal_students.sort(key=_attention_rank, reverse=True)
    scopes = cast(Mapping[str, Any], action_center.get("scopes") or {})
    mine_counts = cast(Mapping[str, Any], scopes.get("mine") or {})
    personal_counts = (
        _personal_counts(mine_counts)
        if mine_counts
        else _personal_counts_from_items(personal_tasks, personal_student_ids)
    )
    page = dict(personal_page or {})
    personal_queue = {
        "total": int(page.get("total", personal_counts["open"]) or 0),
        "limit": int(page.get("limit", len(personal_tasks)) or 0),
        "hasMore": bool(page.get("hasMore", False)),
        "sort": "attention",
    }

    journey_configuration = _configuration(configurations, "journeys")
    campus_configuration = _configuration(configurations, "campus_life")
    academics_configuration = _configuration(configurations, "academics")
    journey_blueprint = _journey_blueprint(journey_configuration)
    academic_catalog = _academic_catalog(academics_configuration)
    knowledge = _mapping_list(managed_content.get("knowledgeBase"))
    core_plays = _mapping_list(managed_content.get("corePlays"))
    resolved_inquiries = _resolve_inquiry_assignees(inquiries, staff)
    durable_campus = copy.deepcopy(dict(campus_life))
    durable_student = copy.deepcopy(dict(student))
    durable_student["syntheticTestRecord"] = False
    durable_student["operation"] = next(
        (item for item in canonical_cohort if str(item.get("id")) == auth.student_id),
        None,
    )

    return {
        "currentStaff": copy.deepcopy(current_staff),
        "actionCenter": copy.deepcopy(dict(action_center)),
        "personalActionCenter": {
            "staff": copy.deepcopy(current_staff),
            "students": personal_students,
            "tasks": personal_tasks,
            "queue": personal_queue,
            "counts": personal_counts,
            "generatedAt": generated_at,
        },
        "cohort": canonical_cohort,
        "cohortSeed": {
            "synthetic": False,
            "count": len(canonical_cohort),
            "purpose": "Canonical PostgreSQL staff roster",
            "generatedAt": generated_at,
            "tenantSlug": auth.tenant_slug or "",
        },
        "student": durable_student,
        "knowledgeBase": knowledge,
        "corePlays": core_plays,
        "inquiries": resolved_inquiries,
        "journeyBlueprint": journey_blueprint,
        "academicCatalog": academic_catalog,
        "configurations": {
            "journeys": _public_configuration(journey_configuration),
            "campusLife": _public_configuration(campus_configuration),
            "academics": _public_configuration(academics_configuration),
        },
        "campusLife": durable_campus,
        "portalInventory": _portal_inventory(
            journey_blueprint,
            academic_catalog,
            durable_campus,
            resolved_inquiries,
            knowledge,
        ),
        # Outreach simulation is deliberately stateless. No run history is
        # invented or retained in the API process.
        "outreachRuns": [],
        "capabilities": {
            "sharedStudentEdits": True,
            "campusContentEdits": True,
            "knowledgeBaseEdits": True,
            "corePlayEdits": True,
            "inquiryReplies": True,
            "externalOutreach": "simulation_only",
            "staffEdward": "preview_only",
            "managedYaml": "import_only",
            "managedDocument": True,
        },
        "generatedAt": generated_at,
    }


def draft_managed_configuration(
    auth: AuthContext,
    current: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> JsonDict:
    """Prepare a non-persistent draft from the current PostgreSQL document."""

    _require_staff(auth)
    expected = int(payload.get("expectedVersion") or 0)
    version = int(current["version"])
    if version != expected:
        raise ConflictError("VERSION_CONFLICT", "This managed configuration changed")
    kind = str(current["kind"])
    document = copy.deepcopy(dict(cast(Mapping[str, Any], current["document"])))
    changes, warnings = draft_managed_configuration_document(
        kind, document, str(payload.get("instruction") or "").strip()
    )
    return {
        "kind": kind,
        "expectedVersion": version,
        "document": document,
        "yaml": yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        "summary": changes[0] if changes else "Prepared a reviewable configuration draft.",
        "changes": changes,
        "warnings": warnings,
        "executionMode": "draft_requires_confirmation",
        "persisted": False,
    }


def simulate_outreach(
    auth: AuthContext,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> JsonDict:
    """Return an explicit, non-persistent simulation result."""

    _require_staff(auth)
    created_at = now or datetime.now(UTC)
    return {
        "id": str(uuid4()),
        **copy.deepcopy(dict(payload)),
        "status": "simulation_only",
        "createdBy": auth.actor_id,
        "createdAt": _iso(created_at),
        "persisted": False,
        "notice": "This simulation is not stored and will not appear in workspace history.",
    }


def preview_edward(auth: AuthContext, payload: Mapping[str, Any]) -> JsonDict:
    """Build a bounded plan without changing tenant or student state."""

    _require_staff(auth)
    lowered = str(payload.get("message") or "").strip().lower()
    plan: list[JsonDict] = [
        {
            "label": "Read the relevant staff workspace context",
            "capability": "read_student_data",
            "status": "available",
        }
    ]
    if any(term in lowered for term in ("journey", "configuration", "course", "event")):
        plan.append(
            {
                "label": "Prepare a versioned tenant-configuration draft",
                "capability": "update_journey",
                "status": "needs_confirmation",
            }
        )
    if any(term in lowered for term in ("message", "reply", "email", "sms")):
        plan.append(
            {
                "label": "Draft a student-safe message for staff review",
                "capability": "draft_message",
                "status": "needs_confirmation",
            }
        )
    if any(term in lowered for term in ("outreach", "campaign", "cohort", "call")):
        plan.append(
            {
                "label": "Simulate the requested cohort outreach",
                "capability": "launch_outreach",
                "status": "simulation_only",
            }
        )
    return {
        "message": (
            "I prepared a bounded staff plan. No student record, configuration, "
            "or external channel was changed."
        ),
        "plan": plan,
        "dataSources": ["staff_action_center", "student_record", "tenant_configuration"],
        "executionMode": "preview_only",
        "persisted": False,
    }


def _configuration(configurations: Mapping[str, Mapping[str, Any]], kind: str) -> Mapping[str, Any]:
    configuration = configurations.get(kind)
    if configuration is None or not isinstance(configuration.get("document"), Mapping):
        raise NotFoundError(
            "MANAGED_CONFIGURATION_NOT_PROVISIONED",
            f"No PostgreSQL managed configuration exists for {kind}",
        )
    return configuration


def _public_configuration(configuration: Mapping[str, Any]) -> JsonDict:
    return {str(key): copy.deepcopy(value) for key, value in configuration.items()}


def _journey_blueprint(configuration: Mapping[str, Any]) -> list[JsonDict]:
    version = int(configuration["version"])
    document = cast(Mapping[str, Any], configuration["document"])
    result: list[JsonDict] = []
    for flow in _mapping_list(document.get("flows")):
        for order, task in enumerate(_mapping_list(flow.get("tasks")), start=1):
            authored_task_type = str(task.get("task_type", "information")).lower()
            task_type = _INTERACTION_TYPES.get(authored_task_type, authored_task_type)
            raw_input = task.get("input")
            input_config = copy.deepcopy(dict(raw_input)) if isinstance(raw_input, Mapping) else {}
            if task.get("flow") is not None:
                input_config["flow"] = copy.deepcopy(task["flow"])
            if task.get("options") is not None:
                input_config["options"] = copy.deepcopy(task["options"])
            result.append(
                {
                    "id": str(task.get("id", f"task-{order}")),
                    "kind": str(flow.get("kind", "enrollment")),
                    "flowId": str(flow.get("id", "flow")),
                    "flowTitle": str(flow.get("title", "Journey")),
                    "title": str(task.get("title", "Untitled task")),
                    "description": str(task.get("description", "")),
                    "owner": str(task.get("owner", "Enrollment Operations")),
                    "required": bool(task.get("required", False)),
                    "active": task.get("active", True) is True,
                    "published": flow.get("status") == "published",
                    "priority": int(task.get("priority", 0)),
                    "order": order,
                    "dueOffsetDays": task.get("due_days_after_acceptance"),
                    "taskType": task_type,
                    "submissionType": _SUBMISSION_TYPES.get(task_type, "none"),
                    "inputConfig": input_config,
                    "points": int(task.get("points", 0)),
                    "studentStep": task.get("student_step"),
                    "dependsOn": list(task.get("depends_on", [])),
                    "flow": copy.deepcopy(task.get("flow", [])),
                    "configurationVersion": version,
                }
            )
    return result


def _academic_catalog(configuration: Mapping[str, Any]) -> JsonDict:
    document = cast(Mapping[str, Any], configuration["document"])
    courses = []
    for course in academic_courses(document):
        courses.append(
            {
                "id": str(course["sourceId"]),
                **{
                    str(key): copy.deepcopy(value)
                    for key, value in course.items()
                    if key not in {"sourceId", "sourceUrl"}
                },
                "source": (
                    {
                        "label": "Tenant-managed catalog",
                        "url": course["sourceUrl"],
                        "dataStatus": "tenant_authored",
                    }
                    if course.get("sourceUrl")
                    else None
                ),
            }
        )
    return {"version": str(document.get("catalog_version") or "unavailable"), "courses": courses}


def _resolve_inquiry_assignees(
    inquiries: Sequence[Mapping[str, Any]], staff: Sequence[Mapping[str, Any]]
) -> list[JsonDict]:
    by_id = {str(member.get("id")): member for member in staff}
    result: list[JsonDict] = []
    for inquiry in inquiries:
        item = copy.deepcopy(dict(inquiry))
        assignee_id = item.pop("assigneeId", None)
        item["assignee"] = (
            copy.deepcopy(dict(by_id[str(assignee_id)]))
            if assignee_id is not None and str(assignee_id) in by_id
            else None
        )
        result.append(item)
    return result


def _portal_inventory(
    journeys: Sequence[Mapping[str, Any]],
    academics: Mapping[str, Any],
    campus: Mapping[str, Any],
    inquiries: Sequence[Mapping[str, Any]],
    knowledge: Sequence[Mapping[str, Any]],
) -> list[JsonDict]:
    return [
        _inventory(
            "onboarding",
            "Onboarding",
            sum(item.get("kind") == "onboarding" for item in journeys),
            "partially_editable",
        ),
        _inventory(
            "enrollment",
            "Enrollment",
            sum(item.get("kind") == "enrollment" for item in journeys),
            "editable",
        ),
        _inventory(
            "classrooms", "Classrooms", len(_mapping_list(academics.get("courses"))), "editable"
        ),
        _inventory(
            "campus_life",
            "Campus life",
            len(_mapping_list(campus.get("events"))) + len(_mapping_list(campus.get("clubs"))),
            "editable",
        ),
        _inventory("financials", "Financials", 0, "planned"),
        _inventory("messages", "Messages", len(inquiries), "editable"),
        _inventory(
            "help",
            "Help and guidance",
            sum(item.get("audience") == "student" for item in knowledge),
            "editable",
        ),
    ]


def _inventory(identifier: str, label: str, count: int, state: str) -> JsonDict:
    descriptions = {
        "onboarding": "Student intake and consent stages.",
        "enrollment": "Enrollment checklist templates.",
        "classrooms": "Programs, courses, and resources.",
        "campus_life": "University events and clubs.",
        "financials": "Aid and payment-plan content.",
        "messages": "Official notices and student inquiries.",
        "help": "Student-safe knowledge cards.",
    }
    return {
        "id": identifier,
        "label": label,
        "description": descriptions[identifier],
        "recordCount": count,
        "managementState": state,
    }


def _mapping_list(value: object) -> list[JsonDict]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [copy.deepcopy(dict(item)) for item in value if isinstance(item, Mapping)]


def _require_staff(auth: AuthContext) -> None:
    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_ACCESS_REQUIRED", "Staff access is required")


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
