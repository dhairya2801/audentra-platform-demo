"""Deterministic, tenant-isolated staff-workspace preview state.

The canonical PostgreSQL staff slice owns work items, student preferences, and
document decisions.  The richer product-design workspace is still preview-only;
this adapter keeps that state bounded, versioned, and explicitly unavailable in
production while allowing the real FastAPI process to drive the complete portal.
"""

from __future__ import annotations

import asyncio
import copy
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import yaml  # type: ignore[import-untyped]

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError

JsonDict = dict[str, Any]
ConfigurationKind = Literal["journeys", "campus_life", "academics"]

_FIXTURE_TIME = "2026-07-24T00:00:00.000Z"
_CONFIGURATION_FILES: dict[ConfigurationKind, str] = {
    "journeys": "journeys.yaml",
    "campus_life": "campus-life.yaml",
    "academics": "academics.yaml",
}
_CONFIGURATION_NAMES: dict[ConfigurationKind, str] = {
    "journeys": "journeys",
    "campus_life": "campus_life",
    "academics": "academics",
}
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

_FALLBACK_YAML: dict[ConfigurationKind, str] = {
    "journeys": """schema_version: 1
tenant: aster
configuration: journeys
flows:
  - id: offer_onboarding
    title: Offer onboarding
    kind: onboarding
    status: published
    tasks:
      - id: review_offer
        student_step: offer
        title: Review your offer
        description: Confirm the admitted program, term, and campus.
        task_type: approval
        owner: Admissions
        required: true
        points: 20
  - id: enrollment_checklist
    title: Enrollment checklist
    kind: enrollment
    status: published
    tasks:
      - id: verify_profile
        title: Verify your profile
        description: Confirm the student's enrollment profile.
        task_type: form
        owner: Admissions
        required: true
        points: 20
""",
    "campus_life": """schema_version: 1
tenant: aster
configuration: campus_life
events:
  - id: first-year-research-showcase
    title: First-Year Research Showcase
    description: Meet faculty mentors and discover research opportunities.
    starts_at: 2027-09-02T16:00:00.000Z
    ends_at: 2027-09-02T18:00:00.000Z
    location: Innovation Hall
    category: academic
    featured: true
    accent: blue
    visual_theme: discovery
    registration_url: null
""",
    "academics": """schema_version: 1
tenant: aster
configuration: academics
catalog_version: 2027-2028.v1
courses:
  - id: cs-101
    code: CS 101
    title: Programming Fundamentals
    description: Problem solving, algorithms, and introductory software development.
    credits: 4
    level: 100
    prerequisites: []
    instructor_names: [Dr. Maya Patel]
    meeting_pattern: Mon/Wed 10:00 AM
    availability_label: Fall and Spring
""",
}


@dataclass(slots=True)
class _TenantPreviewState:
    configurations: dict[ConfigurationKind, JsonDict]
    knowledge_base: list[JsonDict]
    core_plays: list[JsonDict]
    inquiries: list[JsonDict]
    outreach_runs: list[JsonDict]
    club_overrides: dict[str, JsonDict]
    created_clubs: list[JsonDict]
    cohort: list[JsonDict]
    cohort_seed: JsonDict


class PreviewStaffWorkspaceRepository:
    """Concurrency-safe preview state composed with canonical PostgreSQL reads."""

    def __init__(
        self,
        *,
        enabled: bool,
        configuration_root: str | Path | None = None,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._enabled = enabled
        self._configuration_root = self._resolve_configuration_root(configuration_root)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory
        self._states: dict[str, _TenantPreviewState] = {}
        self._lock = asyncio.Lock()

    async def get_workspace(
        self,
        auth: AuthContext,
        *,
        action_center: Mapping[str, Any],
        student: Mapping[str, Any],
        campus_life: Mapping[str, Any],
        canonical_inquiries: Sequence[Mapping[str, Any]] = (),
        canonical_cohort: Sequence[Mapping[str, Any]] = (),
        canonical_knowledge: Sequence[Mapping[str, Any]] | None = None,
        canonical_core_plays: Sequence[Mapping[str, Any]] | None = None,
    ) -> JsonDict:
        self._require_preview_staff(auth)
        async with self._lock:
            state = self._state(auth)
            snapshot = copy.deepcopy(state)

        staff = _mapping_list(action_center.get("staff"))
        current_staff = next(
            (member for member in staff if str(member.get("id")) == auth.actor_id),
            staff[0] if staff else None,
        )
        if current_staff is None:
            raise NotFoundError(
                "STAFF_PREVIEW_NOT_CONFIGURED", "No staff preview identity is configured"
            )

        current_student = cast(JsonDict, copy.deepcopy(student))
        cohort = snapshot.cohort
        _project_current_student(cohort, current_student, staff)
        _merge_canonical_cohort(cohort, canonical_cohort)
        items = _mapping_list(action_center.get("items"))
        if cohort and items:
            cohort[0]["recommendedAction"]["taskId"] = str(items[0]["id"])
        personal_students = [
            item
            for item in cohort
            if item["assignedStaffId"] == current_staff["id"]
            and item["recommendedAction"]["recommendedToday"]
        ]
        personal_students.sort(key=lambda item: int(item["risk"]["score"]), reverse=True)
        personal_task_ids = {
            item["recommendedAction"]["taskId"]
            for item in personal_students
            if item["recommendedAction"]["taskId"]
        }
        personal_tasks = [item for item in items if item.get("id") in personal_task_ids]

        configurations = {
            kind: _public_configuration(configuration)
            for kind, configuration in snapshot.configurations.items()
        }
        journey_blueprint = _journey_blueprint(snapshot.configurations["journeys"])
        academic_catalog = _academic_catalog(snapshot.configurations["academics"])
        merged_campus = _merge_campus_life(snapshot, campus_life)
        knowledge_base = (
            copy.deepcopy(snapshot.knowledge_base)
            if canonical_knowledge is None
            else [copy.deepcopy(dict(item)) for item in canonical_knowledge]
        )
        core_plays = (
            copy.deepcopy(snapshot.core_plays)
            if canonical_core_plays is None
            else [copy.deepcopy(dict(item)) for item in canonical_core_plays]
        )
        inquiry_by_id = {str(item["id"]): item for item in snapshot.inquiries}
        inquiry_by_id.update(
            {
                str(item["id"]): copy.deepcopy(dict(item))
                for item in canonical_inquiries
                if item.get("id")
            }
        )
        inquiries = _resolve_inquiry_assignees(list(inquiry_by_id.values()), staff)
        current_student["syntheticTestRecord"] = False
        current_student["operation"] = next(
            (entry for entry in cohort if entry["id"] == auth.student_id), None
        )
        generated_at = _iso(self._clock())
        return {
            "currentStaff": current_staff,
            "actionCenter": copy.deepcopy(action_center),
            "personalActionCenter": {
                "staff": current_staff,
                "students": personal_students,
                "tasks": personal_tasks,
                "counts": {
                    "studentsToday": len(personal_students),
                    "critical": sum(
                        item["risk"]["band"] == "critical" for item in personal_students
                    ),
                    "highRisk": sum(item["risk"]["band"] == "high" for item in personal_students),
                    "inProgress": sum(
                        item.get("status") == "in_progress" for item in personal_tasks
                    ),
                    "completed": sum(item.get("status") == "done" for item in personal_tasks),
                },
                "generatedAt": generated_at,
            },
            "cohort": cohort,
            "cohortSeed": snapshot.cohort_seed,
            "student": current_student,
            "knowledgeBase": knowledge_base,
            "corePlays": core_plays,
            "inquiries": inquiries,
            "journeyBlueprint": journey_blueprint,
            "academicCatalog": academic_catalog,
            "configurations": {
                "journeys": configurations["journeys"],
                "campusLife": configurations["campus_life"],
                "academics": configurations["academics"],
            },
            "campusLife": merged_campus,
            "portalInventory": _portal_inventory(
                journey_blueprint,
                academic_catalog,
                merged_campus,
                inquiries,
                knowledge_base,
            ),
            "outreachRuns": snapshot.outreach_runs,
            "capabilities": {
                "sharedStudentEdits": True,
                "campusContentEdits": True,
                "knowledgeBaseEdits": True,
                "corePlayEdits": True,
                "inquiryReplies": True,
                "externalOutreach": "simulation_only",
                "staffEdward": "preview_only",
                "managedYaml": True,
            },
            "generatedAt": generated_at,
        }

    async def get_managed_configuration(self, auth: AuthContext, kind: str) -> JsonDict:
        self._require_preview_staff(auth)
        normalized = _configuration_kind(kind)
        async with self._lock:
            return _public_configuration(self._state(auth).configurations[normalized])

    async def sync_managed_configurations(
        self,
        auth: AuthContext,
        configurations: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Refresh preview projections from the durable published documents.

        The staff workspace still composes several preview-only panels, but its
        journey, event, and course editors must use the same versions that are
        persisted and materialized for students.
        """

        self._require_preview_staff(auth)
        async with self._lock:
            state = self._state(auth)
            for raw_kind, configuration in configurations.items():
                kind = _configuration_kind(raw_kind)
                next_configuration = copy.deepcopy(dict(configuration))
                if "document" not in next_configuration:
                    next_configuration["document"] = _parse_configuration(
                        kind, str(next_configuration.get("yaml") or "")
                    )
                next_configuration.setdefault("kind", kind)
                next_configuration.setdefault("fileName", _CONFIGURATION_FILES[kind])
                state.configurations[kind] = next_configuration

    async def update_managed_configuration(
        self, auth: AuthContext, kind: str, update: Mapping[str, Any]
    ) -> JsonDict:
        self._require_preview_staff(auth)
        normalized = _configuration_kind(kind)
        async with self._lock:
            state = self._state(auth)
            current = state.configurations[normalized]
            _check_version(current, update)
            yaml_text = str(update["yaml"])
            document = _parse_configuration(normalized, yaml_text)
            current.update(
                {
                    "version": int(current["version"]) + 1,
                    "yaml": yaml_text,
                    "document": document,
                    "recordCount": _record_count(normalized, document),
                    "updatedAt": _iso(self._clock()),
                    "updatedBy": "Staff preview user",
                    "changeSummary": update.get("changeSummary")
                    or "Published from the staff workspace",
                }
            )
            return _public_configuration(current)

    async def draft_managed_configuration(
        self, auth: AuthContext, draft: Mapping[str, Any]
    ) -> JsonDict:
        self._require_preview_staff(auth)
        kind = _configuration_kind(str(draft["kind"]))
        async with self._lock:
            current = copy.deepcopy(self._state(auth).configurations[kind])
        _check_version(current, draft)
        instruction = str(draft["instruction"]).strip()
        document = cast(JsonDict, current["document"])
        changes, warnings = _apply_configuration_instruction(kind, document, instruction)
        yaml_text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        return {
            "kind": kind,
            "expectedVersion": int(current["version"]),
            "yaml": yaml_text,
            "summary": changes[0] if changes else "Prepared a reviewable configuration draft.",
            "changes": changes,
            "warnings": warnings,
            "executionMode": "draft_requires_confirmation",
        }

    async def create_knowledge_card(
        self, auth: AuthContext, payload: Mapping[str, Any]
    ) -> JsonDict:
        self._require_preview_staff(auth)
        async with self._lock:
            card = {
                "id": str(self._uuid_factory()),
                **copy.deepcopy(dict(payload)),
                "owner": "Staff preview user",
                "version": 1,
                "updatedAt": _iso(self._clock()),
            }
            self._state(auth).knowledge_base.insert(0, card)
            return copy.deepcopy(card)

    async def update_knowledge_card(
        self, auth: AuthContext, card_id: str, payload: Mapping[str, Any]
    ) -> JsonDict:
        self._require_preview_staff(auth)
        async with self._lock:
            cards = self._state(auth).knowledge_base
            card = _find(cards, card_id, "STAFF_KNOWLEDGE_CARD_NOT_FOUND", "Knowledge card")
            _check_version(card, payload)
            owner = card["owner"]
            card.clear()
            card.update(
                {
                    "id": card_id,
                    **_without_expected_version(payload),
                    "owner": owner,
                    "version": int(payload["expectedVersion"]) + 1,
                    "updatedAt": _iso(self._clock()),
                }
            )
            return copy.deepcopy(card)

    async def create_core_play(self, auth: AuthContext, payload: Mapping[str, Any]) -> JsonDict:
        self._require_preview_staff(auth)
        async with self._lock:
            play = {
                "id": str(self._uuid_factory()),
                **copy.deepcopy(dict(payload)),
                "owner": "Staff preview user",
                "version": 1,
                "updatedAt": _iso(self._clock()),
            }
            self._state(auth).core_plays.insert(0, play)
            return copy.deepcopy(play)

    async def update_core_play(
        self, auth: AuthContext, play_id: str, payload: Mapping[str, Any]
    ) -> JsonDict:
        self._require_preview_staff(auth)
        async with self._lock:
            plays = self._state(auth).core_plays
            play = _find(plays, play_id, "STAFF_CORE_PLAY_NOT_FOUND", "Core play")
            _check_version(play, payload)
            owner = play["owner"]
            play.clear()
            play.update(
                {
                    "id": play_id,
                    **_without_expected_version(payload),
                    "owner": owner,
                    "version": int(payload["expectedVersion"]) + 1,
                    "updatedAt": _iso(self._clock()),
                }
            )
            return copy.deepcopy(play)

    async def update_inquiry(
        self,
        auth: AuthContext,
        inquiry_id: str,
        payload: Mapping[str, Any],
        *,
        staff: Sequence[Mapping[str, Any]],
    ) -> JsonDict:
        self._require_preview_staff(auth)
        async with self._lock:
            inquiry = _find(
                self._state(auth).inquiries,
                inquiry_id,
                "STAFF_INQUIRY_NOT_FOUND",
                "Inquiry",
            )
            _check_version(inquiry, payload)
            assignee_id = payload.get("assigneeId")
            if assignee_id is not None and not any(
                str(member.get("id")) == str(assignee_id) for member in staff
            ):
                raise BadRequestError("STAFF_ASSIGNEE_NOT_FOUND", "Choose an active staff assignee")
            inquiry.update(
                {
                    "status": payload["status"],
                    "assigneeId": str(assignee_id) if assignee_id else None,
                    "version": int(inquiry["version"]) + 1,
                    "updatedAt": _iso(self._clock()),
                }
            )
            return _public_inquiry(inquiry, staff)

    async def create_club(self, auth: AuthContext, payload: Mapping[str, Any]) -> JsonDict:
        self._require_preview_staff(auth)
        async with self._lock:
            club = _new_club(
                str(self._uuid_factory()), payload, version=1, updated_at=_iso(self._clock())
            )
            self._state(auth).created_clubs.insert(0, club)
            return copy.deepcopy(club)

    async def update_club(
        self,
        auth: AuthContext,
        club_id: str,
        payload: Mapping[str, Any],
        *,
        campus_life: Mapping[str, Any],
    ) -> JsonDict:
        self._require_preview_staff(auth)
        async with self._lock:
            state = self._state(auth)
            current = state.club_overrides.get(club_id)
            if current is None:
                current = next(
                    (club for club in state.created_clubs if club["id"] == club_id), None
                )
            if current is None:
                current = next(
                    (
                        copy.deepcopy(club)
                        for club in _mapping_list(campus_life.get("clubs"))
                        if str(club.get("id")) == club_id
                    ),
                    None,
                )
                if current is not None:
                    current["version"] = int(current.get("version", 1))
            if current is None:
                raise NotFoundError("STAFF_CLUB_NOT_FOUND", "The campus club was not found")
            _check_version(current, payload)
            updated = {
                **current,
                **_without_expected_version(payload),
                "version": int(current["version"]) + 1,
                "updatedAt": _iso(self._clock()),
            }
            state.club_overrides[club_id] = updated
            state.created_clubs = [
                updated if club["id"] == club_id else club for club in state.created_clubs
            ]
            return copy.deepcopy(updated)

    async def simulate_outreach(self, auth: AuthContext, payload: Mapping[str, Any]) -> JsonDict:
        self._require_preview_staff(auth)
        async with self._lock:
            run = {
                "id": str(self._uuid_factory()),
                **copy.deepcopy(dict(payload)),
                "status": "simulation_only",
                "createdBy": "Staff preview user",
                "createdAt": _iso(self._clock()),
            }
            self._state(auth).outreach_runs.insert(0, run)
            return copy.deepcopy(run)

    async def preview_edward(self, auth: AuthContext, payload: Mapping[str, Any]) -> JsonDict:
        self._require_preview_staff(auth)
        message = str(payload["message"]).strip()
        lowered = message.lower()
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
                "I prepared a preview plan from the bounded staff workspace. "
                "No student record, configuration, or external channel was changed."
            ),
            "plan": plan,
            "dataSources": ["staff_action_center", "student_record", "tenant_configuration"],
            "executionMode": "preview_only",
        }

    def _state(self, auth: AuthContext) -> _TenantPreviewState:
        state = self._states.get(auth.tenant_id)
        if state is None:
            state = _seed_state(auth, self._configuration_root)
            self._states[auth.tenant_id] = state
        return state

    def _require_preview_staff(self, auth: AuthContext) -> None:
        if not self._enabled:
            raise NotFoundError(
                "STAFF_PREVIEW_DISABLED",
                "The extended staff preview is disabled in this environment",
            )
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_ACCESS_REQUIRED", "Staff access is required")

    @staticmethod
    def _resolve_configuration_root(configured: str | Path | None) -> Path | None:
        if configured is not None:
            candidate = Path(configured).expanduser().resolve()
            return candidate if candidate.is_dir() else None
        relative_roots = (
            Path("assets") / "config" / "tenants",
            Path("apps") / "api" / "assets" / "config" / "tenants",
            Path("config") / "tenants",
        )
        starts = (Path.cwd().resolve(), Path(__file__).resolve())
        for start in starts:
            for parent in (start, *start.parents):
                for relative_root in relative_roots:
                    candidate = parent / relative_root
                    if candidate.is_dir():
                        return candidate
        return None


def _seed_state(auth: AuthContext, configuration_root: Path | None) -> _TenantPreviewState:
    tenant_slug = auth.tenant_slug or "aster"
    configurations: dict[ConfigurationKind, JsonDict] = {}
    for kind, file_name in _CONFIGURATION_FILES.items():
        path = configuration_root / tenant_slug / file_name if configuration_root else None
        yaml_text = (
            path.read_text(encoding="utf-8") if path and path.is_file() else _FALLBACK_YAML[kind]
        )
        document = _parse_configuration(kind, yaml_text)
        configurations[kind] = {
            "kind": kind,
            "fileName": file_name,
            "version": 1,
            "yaml": yaml_text,
            "document": document,
            "recordCount": _record_count(kind, document),
            "updatedAt": _FIXTURE_TIME,
            "updatedBy": "Preview fixture",
        }
    return _TenantPreviewState(
        configurations=configurations,
        knowledge_base=_seed_knowledge_base(),
        core_plays=_seed_core_plays(),
        inquiries=_seed_inquiries(),
        outreach_runs=[],
        club_overrides={},
        created_clubs=[],
        cohort=_seed_cohort(auth),
        cohort_seed={
            "synthetic": True,
            "count": 400,
            "purpose": "Deterministic staff workflow and scale testing only",
            "generatedAt": _FIXTURE_TIME,
            "tenantSlug": tenant_slug,
        },
    )


def _seed_knowledge_base() -> list[JsonDict]:
    return [
        {
            "id": "00000000-0000-7000-8000-000000000931",
            "title": "Enrollment deposit policy",
            "summary": "Approved guidance for deposit deadlines, waivers, and escalation.",
            "body": "Verify the offer deadline before discussing extensions or waivers.",
            "category": "Enrollment",
            "audience": "internal",
            "status": "published",
            "owner": "Admissions Operations",
            "version": 1,
            "updatedAt": _FIXTURE_TIME,
        },
        {
            "id": "00000000-0000-7000-8000-000000000932",
            "title": "Transcript review expectations",
            "summary": "What students and reviewers should expect after an upload.",
            "body": "Extracted fields remain suggestions until a reviewer confirms a decision.",
            "category": "Documents",
            "audience": "student",
            "status": "published",
            "owner": "Registrar",
            "version": 1,
            "updatedAt": _FIXTURE_TIME,
        },
        {
            "id": "00000000-0000-7000-8000-000000000933",
            "title": "Housing follow-up guide",
            "summary": "Routing notes for undecided and off-campus students.",
            "body": "Use housing and accommodation preferences to select the advising queue.",
            "category": "Housing",
            "audience": "internal",
            "status": "draft",
            "owner": "Student Life",
            "version": 1,
            "updatedAt": _FIXTURE_TIME,
        },
    ]


def _seed_core_plays() -> list[JsonDict]:
    return [
        {
            "id": "00000000-0000-7000-8000-000000000941",
            "title": "Deposit deadline rescue",
            "description": "A coordinated sequence for an approaching deposit deadline.",
            "trigger": "Deposit due within 72 hours and requirement incomplete",
            "audience": "Admitted students with incomplete deposits",
            "steps": [
                "Verify the student has an active offer",
                "Check for an approved waiver or extension",
                "Draft a reminder with the secure payment link",
            ],
            "status": "active",
            "owner": "Admissions Operations",
            "version": 1,
            "updatedAt": _FIXTURE_TIME,
        },
        {
            "id": "00000000-0000-7000-8000-000000000942",
            "title": "Missing document recovery",
            "description": "A follow-up path for blocking enrollment documents.",
            "trigger": "Blocking document is rejected or seven days overdue",
            "audience": "Students with blocking document requirements",
            "steps": ["Confirm the rejection reason", "Draft resubmission instructions"],
            "status": "draft",
            "owner": "Registrar",
            "version": 1,
            "updatedAt": _FIXTURE_TIME,
        },
    ]


def _seed_inquiries() -> list[JsonDict]:
    return [
        {
            "id": "00000000-0000-7000-8000-000000000951",
            "student": {
                "id": "00000000-0000-7000-8000-000000000101",
                "name": "Alex Morgan",
                "preferredName": "Alex",
                "programName": "Computer Science",
                "classYear": 2027,
            },
            "topicCode": "documents",
            "subject": "Which transcript should I upload?",
            "message": "Should I upload both dual-enrollment transcripts?",
            "status": "new",
            "priority": "high",
            "assigneeId": None,
            "createdAt": "2026-07-24T10:30:00.000Z",
            "updatedAt": "2026-07-24T10:30:00.000Z",
            "version": 1,
        }
    ]


def _seed_cohort(auth: AuthContext) -> list[JsonDict]:
    first_names = ("Alex", "Maya", "Noah", "Jordan", "Avery", "Sam", "Riley", "Taylor")
    last_names = ("Morgan", "Chen", "Williams", "Ellis", "Patel", "Lee", "Garcia", "Brown")
    programs = ("Computer Science", "Mechanical Engineering", "Business Administration")
    categories = (
        "financial",
        "academic",
        "belonging",
        "administrative",
        "engagement",
    )
    cohort: list[JsonDict] = []
    for index in range(400):
        student_id = auth.student_id if index == 0 else f"61000000-0000-7000-8000-{index:012d}"
        score = 95 - (index * 17 % 88)
        band = (
            "critical"
            if score >= 85
            else "high"
            if score >= 65
            else "medium"
            if score >= 35
            else "low"
        )
        category = categories[index % len(categories)]
        assigned = (
            "00000000-0000-7000-8000-000000000901"
            if index < 30
            else "00000000-0000-7000-8000-000000000902"
        )
        occurred_at = datetime(2026, 7, 24, 10, tzinfo=UTC) - timedelta(hours=index % 72)
        first = first_names[index % len(first_names)]
        last = last_names[(index // len(first_names)) % len(last_names)]
        cohort.append(
            {
                "id": student_id,
                "name": f"{first} {last}",
                "preferredName": first,
                "programName": programs[index % len(programs)],
                "classYear": 2027 + index % 3,
                "assignedStaffId": assigned,
                "syntheticSeed": True,
                "journey": {
                    "stage": ("Onboarding", "Documents", "Deposit", "Ready")[index % 4],
                    "completedTasks": index % 8,
                    "totalTasks": 8,
                    "lastActivityAt": _iso(occurred_at),
                },
                "risk": {
                    "score": score,
                    "band": band,
                    "category": category,
                    "meltLikelihoodPercent": score,
                    "recoveryLikelihoodPercent": max(10, 100 - score // 2),
                    "reason": f"Recent {category} signals require a staff check-in.",
                    "signals": ["Enrollment task inactivity", "Deadline approaching"],
                    "modelVersion": "deterministic-preview-v1",
                    "evaluatedAt": _FIXTURE_TIME,
                },
                "recommendedAction": {
                    "title": "Review the next enrollment step",
                    "rationale": "A focused staff check-in can remove the current blocker.",
                    "channel": ("email", "sms", "voice")[index % 3],
                    "expectedImpact": "Restore forward progress this week",
                    "taskId": None,
                    "recommendedToday": index < 30,
                },
                "communicationHistory": [
                    {
                        "id": f"communication-{index}-1",
                        "channel": "portal",
                        "direction": "outbound",
                        "summary": "Enrollment reminder available in the portal",
                        "outcome": "opened" if index % 2 == 0 else "no_response",
                        "occurredAt": _iso(occurred_at),
                    }
                ],
            }
        )
    return cohort


def _project_current_student(
    cohort: list[JsonDict], student: JsonDict, staff: Sequence[Mapping[str, Any]]
) -> None:
    if not cohort:
        return
    summary = cast(Mapping[str, Any], student.get("student", {}))
    first = cohort[0]
    first.update(
        {
            "id": str(summary.get("id", first["id"])),
            "name": str(summary.get("name", first["name"])),
            "preferredName": str(summary.get("preferredName", first["preferredName"])),
            "programName": str(summary.get("programName", first["programName"])),
            "classYear": int(summary.get("classYear", first["classYear"])),
            "assignedStaffId": str(staff[0]["id"]) if staff else first["assignedStaffId"],
        }
    )
    if len(staff) > 1:
        for entry in cohort[30:]:
            entry["assignedStaffId"] = str(staff[1]["id"])


def _merge_canonical_cohort(
    cohort: list[JsonDict], canonical_cohort: Sequence[Mapping[str, Any]]
) -> None:
    """Add real students to the preview roster without inventing risk scores."""

    by_id = {str(entry["id"]): entry for entry in cohort}
    for canonical_value in canonical_cohort:
        canonical = copy.deepcopy(dict(canonical_value))
        student_id = str(canonical.get("id") or "")
        if not student_id:
            continue
        current = by_id.get(student_id)
        if current is None:
            cohort.append(canonical)
            by_id[student_id] = canonical
            continue
        for key in (
            "name",
            "preferredName",
            "programName",
            "classYear",
            "assignedStaffId",
            "journey",
            "syntheticSeed",
        ):
            if key in canonical:
                current[key] = canonical[key]
        recommendation = cast(Mapping[str, Any], canonical.get("recommendedAction", {}))
        if recommendation.get("taskId"):
            current["recommendedAction"] = copy.deepcopy(dict(recommendation))


def _configuration_kind(value: str) -> ConfigurationKind:
    if value not in _CONFIGURATION_FILES:
        raise BadRequestError("INVALID_STAFF_CONFIGURATION", "Choose a managed configuration")
    return value


def _parse_configuration(kind: ConfigurationKind, yaml_text: str) -> JsonDict:
    try:
        value = yaml.safe_load(yaml_text)
    except yaml.YAMLError as error:
        raise BadRequestError("INVALID_MANAGED_YAML", "The managed YAML is not valid") from error
    if not isinstance(value, dict):
        raise BadRequestError("INVALID_MANAGED_YAML", "The managed YAML must be an object")
    document = {str(key): item for key, item in value.items()}
    if document.get("configuration") != _CONFIGURATION_NAMES[kind]:
        raise BadRequestError(
            "MANAGED_CONFIGURATION_KIND_MISMATCH",
            "The YAML configuration kind does not match this resource",
        )
    key = "flows" if kind == "journeys" else "events" if kind == "campus_life" else "courses"
    if not isinstance(document.get(key), list):
        raise BadRequestError("INVALID_MANAGED_YAML", f"The {key} collection is required")
    return document


def _record_count(kind: ConfigurationKind, document: Mapping[str, Any]) -> int:
    if kind == "journeys":
        return sum(
            len(flow.get("tasks", []))
            for flow in _mapping_list(document.get("flows"))
            if isinstance(flow.get("tasks"), list)
        )
    key = "events" if kind == "campus_life" else "courses"
    return len(_mapping_list(document.get(key)))


def _public_configuration(configuration: Mapping[str, Any]) -> JsonDict:
    return {
        str(key): copy.deepcopy(value)
        for key, value in configuration.items()
        if key != "document" and value is not None
    }


def _check_version(current: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    expected = int(payload["expectedVersion"])
    if int(current["version"]) != expected:
        raise ConflictError("VERSION_CONFLICT", "This staff record changed in another session")


def _without_expected_version(payload: Mapping[str, Any]) -> JsonDict:
    return {
        str(key): copy.deepcopy(value) for key, value in payload.items() if key != "expectedVersion"
    }


def _find(items: list[JsonDict], identifier: str, code: str, label: str) -> JsonDict:
    item = next((candidate for candidate in items if candidate.get("id") == identifier), None)
    if item is None:
        raise NotFoundError(code, f"{label} was not found")
    return item


def _apply_configuration_instruction(
    kind: ConfigurationKind, document: JsonDict, instruction: str
) -> tuple[list[str], list[str]]:
    changes: list[str] = []
    warnings: list[str] = []
    if kind == "journeys":
        points = re.search(r"\b(\d{1,4})\s+points?\b", instruction, re.IGNORECASE)
        addition = re.search(
            r"\badd\s+[\"'](.+?)[\"']\s+to\s+(onboarding|enrollment)\b",
            instruction,
            re.IGNORECASE,
        )
        if addition:
            task_title, flow_kind = addition.groups()
            normalized_kind = flow_kind.lower()
            flow = next(
                (
                    candidate
                    for candidate in _mutable_mapping_list(document.get("flows"))
                    if str(candidate.get("kind", "")).lower() == normalized_kind
                ),
                None,
            )
            if flow is None:
                raise BadRequestError(
                    "INVALID_MANAGED_YAML",
                    f"The {normalized_kind} journey flow is not configured",
                )
            tasks = cast(list[Any], flow.setdefault("tasks", []))
            task_id = re.sub(r"[^a-z0-9]+", "_", task_title.lower()).strip("_")
            if any(isinstance(task, Mapping) and str(task.get("id")) == task_id for task in tasks):
                warnings.append(f"{task_title} already exists in {normalized_kind}.")
            else:
                lowered = instruction.lower()
                task_type = (
                    "multiple_select"
                    if "multiple selection" in lowered or "multiple select" in lowered
                    else "single_select"
                    if "single selection" in lowered or "single select" in lowered
                    else "upload_file"
                    if "upload" in lowered
                    else "payment"
                    if "payment" in lowered or "pay " in lowered
                    else "form"
                )
                selection_options: list[str] | None = None
                selection_ready = True
                if task_type in {"single_select", "multiple_select"}:
                    if "meal plan" in task_title.lower():
                        selection_options = [
                            "Unlimited dining",
                            "14 meals per week",
                            "10 meals per week",
                            "Commuter plan",
                        ]
                    else:
                        option_match = re.search(
                            r"\boptions?\s*(?:are|:)?\s*(.+?)(?:\.|$)",
                            instruction,
                            re.IGNORECASE,
                        )
                        if option_match:
                            selection_options = [
                                value.strip(" \t\"'")
                                for value in re.split(
                                    r"\s*,\s*|\s+and\s+",
                                    option_match.group(1),
                                )
                                if value.strip(" \t\"'")
                            ]
                    if not selection_options or len(set(selection_options)) < 2:
                        selection_ready = False
                        warnings.append(
                            f"{task_title} needs at least two explicit option values "
                            "before it can be published."
                        )
                if selection_ready:
                    tasks.append(
                        {
                            "id": task_id,
                            "title": task_title,
                            "description": (
                                f"Complete {task_title.lower()} for your student journey."
                            ),
                            "task_type": task_type,
                            "submission_type": (
                                "document"
                                if task_type == "upload_file"
                                else "payment"
                                if task_type == "payment"
                                else "form"
                            ),
                            "owner": "Enrollment Operations",
                            "required": "optional" not in lowered,
                            "points": int(points.group(1)) if points else 0,
                            "depends_on": [],
                            **(
                                {"options": selection_options}
                                if selection_options is not None
                                else {}
                            ),
                            **(
                                {"maximum_selections": len(selection_options)}
                                if task_type == "multiple_select" and selection_options is not None
                                else {}
                            ),
                        }
                    )
                    changes.append(f"Added {task_title} to {normalized_kind}.")
        title_match = re.search(
            r"(?:change|set)\s+(.+?)\s+(?:to|at)\s+\d+\s+points", instruction, re.IGNORECASE
        )
        if points and not addition:
            tasks = [
                task
                for flow in _mutable_mapping_list(document.get("flows"))
                for task in _mutable_mapping_list(flow.get("tasks"))
            ]
            needle = title_match.group(1).strip(" \"'") if title_match else ""
            target = next(
                (
                    task
                    for task in tasks
                    if not needle or needle.lower() in str(task.get("title", "")).lower()
                ),
                None,
            )
            if target is not None:
                target["points"] = int(points.group(1))
                changes.append(
                    f"Set {target.get('title', 'the journey task')} to {points.group(1)} points."
                )
    elif kind == "campus_life":
        event = re.search(
            r"add an event called [\"'](.+?)[\"'] on (\d{4}-\d{2}-\d{2}) at (.+?)[.!]?$",
            instruction,
            re.IGNORECASE,
        )
        if event:
            event_title, day, location = event.groups()
            slug = re.sub(r"[^a-z0-9]+", "-", event_title.lower()).strip("-")
            cast(list[Any], document["events"]).append(
                {
                    "id": slug,
                    "title": event_title,
                    "description": f"Tenant-authored event: {event_title}.",
                    "starts_at": f"{day}T17:00:00.000Z",
                    "ends_at": f"{day}T19:00:00.000Z",
                    "location": location.strip(),
                    "category": "social",
                    "featured": False,
                    "accent": "blue",
                    "visual_theme": "community",
                    "registration_url": None,
                }
            )
            changes.append(f"Added {event_title} to the campus-life event draft.")
    elif kind == "academics":
        course = re.search(
            r"add course ([A-Z]{2,8}\s*\d{2,4}) called [\"'](.+?)[\"'](?: for (\d+) credits?)?",
            instruction,
            re.IGNORECASE,
        )
        if course:
            code, course_title, credits = course.groups()
            normalized_code = re.sub(r"\s+", " ", code.upper()).strip()
            level_match = re.search(r"\d+", normalized_code)
            if level_match is None:  # Defensive; the course expression requires digits.
                raise BadRequestError("INVALID_COURSE_CODE", "The course code is invalid")
            cast(list[Any], document["courses"]).append(
                {
                    "id": re.sub(r"[^a-z0-9]+", "-", normalized_code.lower()).strip("-"),
                    "code": normalized_code,
                    "title": course_title,
                    "description": f"Tenant-authored catalog course: {course_title}.",
                    "credits": int(credits or 3),
                    "level": int(level_match.group()) // 100 * 100,
                    "prerequisites": [],
                    "instructor_names": [],
                    "meeting_pattern": None,
                    "availability_label": "Tenant catalog",
                }
            )
            changes.append(f"Added {normalized_code} {course_title} to the catalog draft.")
    if not changes and not warnings:
        warnings.append(
            "The preview could not safely translate this instruction; review the unchanged YAML."
        )
    return changes, warnings


def _journey_blueprint(configuration: Mapping[str, Any]) -> list[JsonDict]:
    version = int(configuration["version"])
    document = cast(Mapping[str, Any], configuration["document"])
    result: list[JsonDict] = []
    for flow in _mapping_list(document.get("flows")):
        tasks = _mapping_list(flow.get("tasks"))
        for order, task in enumerate(tasks, start=1):
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
    courses: list[JsonDict] = []
    for course in _mapping_list(document.get("courses")):
        prerequisites = [
            {
                "courseCode": item.get("course_code"),
                "minimumGrade": item.get("minimum_grade"),
            }
            for item in _mapping_list(course.get("prerequisites"))
        ]
        courses.append(
            {
                "id": str(course.get("id")),
                "code": str(course.get("code")),
                "title": str(course.get("title")),
                "description": str(course.get("description", "")),
                "credits": int(course.get("credits", 0)),
                "level": int(course.get("level", 0)),
                "availabilityLabel": course.get("availability_label"),
                "instructorNames": list(course.get("instructor_names", [])),
                "meetingPattern": course.get("meeting_pattern"),
                "resources": copy.deepcopy(course.get("resources", [])),
                "relatedVideos": [
                    {
                        "id": video.get("id"),
                        "title": video.get("title"),
                        "description": video.get("description"),
                        "url": video.get("url"),
                        "provider": "YouTube",
                        "sourceLabel": video.get("source_label"),
                    }
                    for video in _mapping_list(course.get("related_videos"))
                ],
                "prerequisites": prerequisites,
            }
        )
    return {"version": str(document.get("catalog_version", "unavailable")), "courses": courses}


def _merge_campus_life(state: _TenantPreviewState, base: Mapping[str, Any]) -> JsonDict:
    clubs: list[JsonDict] = []
    for item in _mapping_list(base.get("clubs")):
        identifier = str(item.get("id"))
        club = copy.deepcopy(state.club_overrides.get(identifier, item))
        club.setdefault("version", 1)
        club.setdefault("updatedAt", str(base.get("generatedAt", _FIXTURE_TIME)))
        clubs.append(club)
    known = {str(item["id"]) for item in clubs}
    clubs.extend(copy.deepcopy(item) for item in state.created_clubs if item["id"] not in known)
    configuration = state.configurations["campus_life"]
    document = cast(Mapping[str, Any], configuration["document"])
    configured_events = [_campus_event(item) for item in _mapping_list(document.get("events"))]
    events = configured_events or copy.deepcopy(base.get("events", []))
    return {"events": events, "clubs": clubs, "generatedAt": _iso(datetime.now(UTC))}


def _campus_event(item: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(item.get("id")),
        "title": str(item.get("title")),
        "description": str(item.get("description", "")),
        "startsAt": _yaml_timestamp(item.get("starts_at")),
        "endsAt": _yaml_timestamp(item.get("ends_at")),
        "location": str(item.get("location", "")),
        "category": str(item.get("category", "social")),
        "featured": bool(item.get("featured", False)),
        "accent": str(item.get("accent", "blue")),
        "visualTheme": str(item.get("visual_theme", "community")),
        "imageUrl": item.get("image_url"),
        "imageAlt": item.get("image_alt"),
        "imageAttribution": item.get("image_attribution"),
        "imageSourceUrl": item.get("image_source_url"),
        "advertisementStartsAt": (
            _yaml_timestamp(item.get("advertisement_starts_at"))
            if item.get("advertisement_starts_at") is not None
            else None
        ),
        "advertisementEndsAt": (
            _yaml_timestamp(item.get("advertisement_ends_at"))
            if item.get("advertisement_ends_at") is not None
            else None
        ),
        "registrationUrl": item.get("registration_url"),
        "version": int(item.get("version", 1)),
        "registrationStatus": None,
    }


def _new_club(
    identifier: str, payload: Mapping[str, Any], *, version: int, updated_at: str
) -> JsonDict:
    return {
        "id": identifier,
        **copy.deepcopy(dict(payload)),
        "nextActivity": None,
        "imageUrl": payload.get("imageUrl") or "/media/clubs/code-collective.jpg",
        "imageAlt": f"Students participating in {payload['name']}",
        "imageAttribution": "Tenant-authored staff preview",
        "imageSourceUrl": "",
        "socialLinks": [],
        "longDescription": payload["description"],
        "meetingSchedule": None,
        "events": [],
        "version": version,
        "updatedAt": updated_at,
    }


def _resolve_inquiry_assignees(
    inquiries: Sequence[Mapping[str, Any]], staff: Sequence[Mapping[str, Any]]
) -> list[JsonDict]:
    return [_public_inquiry(item, staff) for item in inquiries]


def _public_inquiry(inquiry: Mapping[str, Any], staff: Sequence[Mapping[str, Any]]) -> JsonDict:
    assignee_id = inquiry.get("assigneeId")
    assignee = next(
        (copy.deepcopy(member) for member in staff if str(member.get("id")) == assignee_id),
        None,
    )
    return {
        **{str(key): copy.deepcopy(value) for key, value in inquiry.items() if key != "assigneeId"},
        "assignee": assignee,
    }


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
            "Student intake and consent stages.",
            sum(item["kind"] == "onboarding" for item in journeys),
            "partially_editable",
        ),
        _inventory(
            "enrollment",
            "Enrollment",
            "Enrollment checklist templates.",
            sum(item["kind"] == "enrollment" for item in journeys),
            "editable",
        ),
        _inventory(
            "classrooms",
            "Classrooms",
            "Programs, courses, and resources.",
            len(cast(Sequence[Any], academics["courses"])),
            "editable",
        ),
        _inventory(
            "campus_life",
            "Campus life",
            "University events and clubs.",
            len(cast(Sequence[Any], campus["events"])) + len(cast(Sequence[Any], campus["clubs"])),
            "editable",
        ),
        _inventory("financials", "Financials", "Aid and payment-plan content.", 0, "planned"),
        _inventory(
            "messages",
            "Messages",
            "Official notices and student inquiries.",
            len(inquiries),
            "editable",
        ),
        _inventory(
            "help",
            "Help and guidance",
            "Student-safe knowledge cards.",
            sum(item.get("audience") == "student" for item in knowledge),
            "editable",
        ),
    ]


def _inventory(identifier: str, label: str, description: str, count: int, state: str) -> JsonDict:
    return {
        "id": identifier,
        "label": label,
        "description": description,
        "recordCount": count,
        "managementState": state,
    }


def _mapping_list(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [
        {str(key): item for key, item in candidate.items()}
        for candidate in value
        if isinstance(candidate, dict)
    ]


def _mutable_mapping_list(value: object) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [cast(JsonDict, candidate) for candidate in value if isinstance(candidate, dict)]


def _yaml_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return _iso(value)
    return str(value)


def _iso(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
