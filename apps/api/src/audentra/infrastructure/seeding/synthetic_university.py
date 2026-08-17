# ruff: noqa: S608 -- every interpolated identifier is a module-local constant.
"""Imports the synthetic university into the canonical PostgreSQL domain model.

The generator in ``tools/demo-api/src/synthetic-university`` describes a whole
fictional institution in its own vocabulary: checklist tasks, holds, an account
ledger, housing assignments, registration gates. Audentra's schema describes a
different, smaller set of things: offers, journeys, requirements, documents,
aid, payments. This module is the translation between them, and it is the only
place that translation happens.

Three rules shape everything here.

**PostgreSQL stays canonical.** Nothing is imported that the schema cannot
express. Where the generator has a concept the product does not model — account
holds, room assignments, registration windows — the concept is either
translated into the canonical fact it implies or dropped, never smuggled into a
JSONB column so a downstream reader can "discover" it. Dropped concepts are
counted in :class:`SyntheticUniverseReport` so the gap stays visible.

**Vocabulary is checked, not assumed.** Every enumerated value written here is
mapped through an explicit table whose keys are the generator's values and
whose values are the schema's. An unmapped value raises rather than reaching
the database, because the failure mode we are avoiding is a fixture that
describes states production could never hold.

**The import is deterministic and idempotent.** Identifiers the generator
supplies are reused; identifiers it does not supply are derived with
:func:`uuid5` from the record they belong to. Re-running the import converges
on the same rows rather than accumulating new ones.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .fixture import SeedFixtureError
from .synthetic_university_asset import (
    ASSET_RELATIVE_PATH,
    ASSET_SHA256,
    ASSET_SIZE_BYTES,
    UNIVERSE_COLLECTION_COUNTS,
    UNIVERSE_GENERATED_FOR,
    UNIVERSE_SEED,
    UNIVERSE_STUDENT_COUNT,
    UNIVERSE_VERSION,
)

# --------------------------------------------------------------------------
# Identity of the demo tenant
# --------------------------------------------------------------------------

#: The synthetic population lives here and nowhere else. Keeping it out of the
#: two preview tenants is what lets the compact fixture stay a fixture: an
#: integration test that counts Aster students still counts fourteen.
SYNTHETIC_TENANT_ID: Final = "00000000-0000-7000-8000-000000000003"
SYNTHETIC_TENANT_SLUG: Final = "aster-demo"
SYNTHETIC_TENANT_NAME: Final = "Aster University"
SYNTHETIC_NAMESPACE: Final = "30000000"
SYNTHETIC_CAMPUS_ID: Final = "30000000-0000-7000-8000-000000000110"
#: Identifier of the bootstrap journey definition. Only used until the tenant
#: publishes `journeys.yaml`; after that the active definition is read from the
#: database, because the published graph is the one students hang off.
SYNTHETIC_JOURNEY_DEFINITION_ID: Final = "30000000-0000-7000-8000-000000000301"

#: Deposit charged by the generator, in cents. Mirrors ENROLLMENT_DEPOSIT_USD.
SYNTHETIC_DEPOSIT_CENTS: Final = 50_000

#: Aid year every financial row is filed under. Mirrors the generator's AID_YEAR.
SYNTHETIC_ACADEMIC_YEAR: Final = "2026-2027"

_UUID_NAMESPACE_PREFIX: Final = "audentra-synthetic-university"

#: Work items are a staff-facing queue, not a per-student record. Importing one
#: for every student with an open blocker would produce a board no human could
#: read and would enqueue thousands of Action Center AI jobs behind the
#: `staff_work_item` insert trigger. A realistic board is a few hundred rows.
MAX_WORK_ITEMS: Final = 300

_BATCH_SIZE: Final = 1_000


# --------------------------------------------------------------------------
# Vocabulary translation
# --------------------------------------------------------------------------

#: Generator document category -> `document_record.category`.
#: The last two have no canonical category of their own; `other` is the honest
#: answer rather than filing immigration paperwork under `identity`.
_DOCUMENT_CATEGORIES: Final[Mapping[str, str]] = {
    "transcript": "transcript",
    "immunization": "health",
    "photo_id": "identity",
    "residency_affidavit": "residency",
    "verification_worksheet": "financial_aid",
    "tax_return_transcript": "financial_aid",
    "i20_support": "other",
    "english_proficiency": "other",
}

#: Generator document status -> `document_record.status`, or None when no
#: document row should exist at all. A never-submitted or waived document is
#: the absence of a record, not a record in a special state.
_DOCUMENT_STATUSES: Final[Mapping[str, str | None]] = {
    "NOT_SUBMITTED": None,
    "WAIVED": None,
    "UPLOADED": "uploaded",
    "UNDER_REVIEW": "under_review",
    "ACCEPTED": "accepted",
    "REJECTED": "rejected",
    # PostgreSQL has no `needs_resubmission`. `rejected` is the true statement:
    # the office looked at it and did not accept it.
    "NEEDS_RESUBMISSION": "rejected",
}

#: Generator document status -> (`student_requirement.status`, progress).
_DOCUMENT_REQUIREMENT_STATES: Final[Mapping[str, tuple[str, int]]] = {
    "NOT_SUBMITTED": ("ready", 0),
    "UPLOADED": ("submitted", 60),
    "UNDER_REVIEW": ("under_review", 80),
    "ACCEPTED": ("completed", 100),
    "REJECTED": ("rejected", 0),
    "NEEDS_RESUBMISSION": ("rejected", 0),
    "WAIVED": ("waived", 100),
}

#: Generator FAFSA state -> `financial_document_requirement.status` for the
#: FAFSA row itself.
_FAFSA_DOCUMENT_STATUSES: Final[Mapping[str, str]] = {
    "not_received": "not_started",
    "received": "submitted",
    "selected_for_verification": "under_review",
    "verification_complete": "verified",
    "rejected": "action_required",
}

#: Generator verification-requirement state -> the same column.
_VERIFICATION_DOCUMENT_STATUSES: Final[Mapping[str, str]] = {
    "outstanding": "action_required",
    "submitted": "under_review",
    "satisfied": "verified",
}

#: Generator SAP state -> `student_sap_status.status`. The schema has no
#: `suspension`; `not_meeting` is the bucket a suspended student falls in.
_SAP_STATUSES: Final[Mapping[str, str]] = {
    "meeting": "meeting",
    "warning": "warning",
    "probation": "probation",
    "suspension": "not_meeting",
}

#: Generator award status -> `student_financial_award.status`. Identical
#: vocabularies, asserted rather than assumed.
_AWARD_STATUSES: Final[Mapping[str, str]] = {
    "offered": "offered",
    "accepted": "accepted",
    "declined": "declined",
    "pending": "pending",
}

_AWARD_TYPES: Final[frozenset[str]] = frozenset({"grant", "scholarship", "loan", "work_study"})
_AWARD_SOURCES: Final[frozenset[str]] = frozenset({"federal", "state", "institutional", "private"})

#: Which canonical requirement each generator document backs, when the document
#: is the evidence for that requirement. Documents with no entry are filed
#: against the student but not against a requirement.
_DOCUMENT_REQUIREMENT_CODES: Final[Mapping[str, str]] = {
    "transcript": "official_transcript",
    "photo_id": "identity_document",
    "immunization": "immunization_record",
    "verification_worksheet": "financial_aid_verification",
    "tax_return_transcript": "financial_aid_verification",
}

#: Requirement statuses that count as "the student is finished with this".
_DONE_STATUSES: Final[frozenset[str]] = frozenset({"completed", "waived", "not_applicable"})

#: The eight canonical requirement codes, in the order the tenant defines them,
#: with the dependencies the schema declares. Kept here so the importer can
#: gate on prerequisites without a round trip per student.
_REQUIREMENT_CODES: Final[tuple[str, ...]] = (
    "profile_verification",
    "identity_document",
    "official_transcript",
    "financial_aid_verification",
    "immunization_record",
    "housing_preference",
    "enrollment_deposit",
    "orientation_registration",
)

#: Prior credits attributed to a student's aid-year SAP evaluation. The
#: generator supplies a GPA and a completion rate but no credit count, and a
#: completion rate over zero attempted credits is arithmetic nonsense — so the
#: importer supplies the denominator, by admit type, and the report says so.
_ATTEMPTED_CREDITS_BY_ADMIT_TYPE: Final[Mapping[str, int]] = {
    "first_year": 24,
    "transfer": 60,
    "international": 24,
}


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RequirementDefinition:
    """One canonical requirement definition, as the demo tenant stores it."""

    definition_id: UUID
    due_offset_days: int | None


@dataclass(slots=True)
class SyntheticUniverseReport:
    """What the import actually did, in numbers a report can quote."""

    students_generated: int = 0
    students_imported: int = 0
    students_rejected: int = 0
    rejections: list[str] = field(default_factory=list)
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    row_counts: dict[str, int] = field(default_factory=dict)
    dropped_records: dict[str, int] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    load_seconds: float = 0.0
    map_seconds: float = 0.0
    write_seconds: float = 0.0

    @property
    def total_rows(self) -> int:
        return sum(self.row_counts.values())

    @property
    def total_seconds(self) -> float:
        return self.load_seconds + self.map_seconds + self.write_seconds


class SyntheticUniverseError(SeedFixtureError):
    """The packaged universe is missing, corrupt, or internally inconsistent."""


# --------------------------------------------------------------------------
# Asset loading
# --------------------------------------------------------------------------


def _asset_root(explicit: Path | None = None) -> Path:
    # An explicit root is authoritative. Falling back to the packaged asset
    # would make a caller that pointed somewhere else silently read the wrong
    # universe, which is the opposite of what asking for a root means.
    candidates = (
        [explicit]
        if explicit is not None
        else [
            Path("/app/assets"),
            Path.cwd() / "assets",
            Path(__file__).resolve().parents[4] / "assets",
        ]
    )
    for candidate in candidates:
        if (candidate / ASSET_RELATIVE_PATH).is_file():
            return candidate
    searched = ", ".join(str(candidate / ASSET_RELATIVE_PATH) for candidate in candidates)
    raise SyntheticUniverseError(
        f"The synthetic university archive was not found; searched: {searched}"
    )


def load_synthetic_universe(asset_root: Path | None = None) -> dict[str, Any]:
    """Decode and verify the packaged universe.

    The digest is checked against the *compressed* bytes before anything is
    decompressed, so a truncated or swapped archive fails immediately rather
    than after producing a plausible-looking half-population.
    """

    path = _asset_root(asset_root) / ASSET_RELATIVE_PATH
    raw = path.read_bytes()
    if len(raw) != ASSET_SIZE_BYTES:
        raise SyntheticUniverseError(
            f"The synthetic university archive is {len(raw)} bytes; "
            f"{ASSET_SIZE_BYTES} were expected"
        )
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ASSET_SHA256:
        raise SyntheticUniverseError(
            "The synthetic university archive checksum does not match; regenerate it with "
            "`node tools/export-synthetic-university.mjs`"
        )
    try:
        universe = json.loads(gzip.decompress(raw))
    except (OSError, ValueError) as error:  # gzip and json both surface as these
        raise SyntheticUniverseError("The synthetic university archive cannot be decoded") from (
            error
        )
    if not isinstance(universe, dict):
        raise SyntheticUniverseError("The synthetic university archive must decode to an object")

    meta = universe.get("meta")
    if not isinstance(meta, dict):
        raise SyntheticUniverseError("The synthetic university archive has no meta block")
    expected_meta = {
        "version": UNIVERSE_VERSION,
        "seed": UNIVERSE_SEED,
        "studentCount": UNIVERSE_STUDENT_COUNT,
        "generatedFor": UNIVERSE_GENERATED_FOR,
    }
    for key, expected in expected_meta.items():
        if meta.get(key) != expected:
            raise SyntheticUniverseError(
                f"The synthetic university archive declares {key}={meta.get(key)!r}; "
                f"{expected!r} was expected"
            )
    for name, expected_count in UNIVERSE_COLLECTION_COUNTS.items():
        collection = universe.get(name)
        if not isinstance(collection, list) or len(collection) != expected_count:
            actual = len(collection) if isinstance(collection, list) else "missing"
            raise SyntheticUniverseError(
                f"The synthetic university archive has {actual} {name}; "
                f"{expected_count} were expected"
            )
    return universe


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _uuid(kind: str, *parts: str) -> UUID:
    """A stable identifier for a row the generator does not name itself."""

    return uuid5(NAMESPACE_URL, ":".join((_UUID_NAMESPACE_PREFIX, kind, *parts)))


def _instant(value: object) -> datetime:
    if not isinstance(value, str):
        raise SyntheticUniverseError(f"Expected an ISO instant; received {value!r}")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _optional_instant(value: object) -> datetime | None:
    return None if value is None else _instant(value)


def _cents(amount: object) -> int:
    if not isinstance(amount, int | float):
        raise SyntheticUniverseError(f"Expected a numeric amount; received {amount!r}")
    return round(float(amount) * 100)


def _group_by_student(records: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["studentId"]), []).append(record)
    return grouped


def _class_year(term_start: datetime, admit_type: str) -> int:
    """Expected graduation year: four years for an entering class, two for a
    transfer admit who arrives with credit."""

    return term_start.year + (2 if admit_type == "transfer" else 4)


def _deterministic_size_bytes(identifier: str) -> int:
    """A stable, plausible file size. `document_record` requires 1..10 MiB and
    a constant everywhere would make every demo document look identical."""

    digest = hashlib.sha256(identifier.encode("utf-8")).digest()
    return 40_000 + int.from_bytes(digest[:4], "big") % 360_000


def _sha256_of(identifier: str) -> str:
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Tables:
    """Row buffers, one per canonical table, filled in dependency order."""

    person: list[dict[str, Any]] = field(default_factory=list)
    student: list[dict[str, Any]] = field(default_factory=list)
    student_profile: list[dict[str, Any]] = field(default_factory=list)
    student_onboarding: list[dict[str, Any]] = field(default_factory=list)
    student_portal_projection: list[dict[str, Any]] = field(default_factory=list)
    admission_offer: list[dict[str, Any]] = field(default_factory=list)
    enrollment_journey: list[dict[str, Any]] = field(default_factory=list)
    student_requirement: list[dict[str, Any]] = field(default_factory=list)
    document_record: list[dict[str, Any]] = field(default_factory=list)
    financial_document_requirement: list[dict[str, Any]] = field(default_factory=list)
    student_financial_award: list[dict[str, Any]] = field(default_factory=list)
    student_financial_summary: list[dict[str, Any]] = field(default_factory=list)
    student_sap_status: list[dict[str, Any]] = field(default_factory=list)
    payment_transaction: list[dict[str, Any]] = field(default_factory=list)
    student_message: list[dict[str, Any]] = field(default_factory=list)
    student_appointment: list[dict[str, Any]] = field(default_factory=list)
    staff_member: list[dict[str, Any]] = field(default_factory=list)
    staff_work_item: list[dict[str, Any]] = field(default_factory=list)
    staff_work_log: list[dict[str, Any]] = field(default_factory=list)
    program: list[dict[str, Any]] = field(default_factory=list)
    academic_term: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class _WorkItemCandidate:
    priority_rank: int
    external_ref: str
    student_id: UUID
    title: str
    description: str
    component: str
    action_type: str
    priority: str
    source_type: str
    source_id: UUID
    due_at: datetime | None


def _requirement_states(
    *,
    tasks: Mapping[str, Mapping[str, Any]],
    document_statuses: Mapping[str, str],
    fafsa_state: str | None,
    verification_states: Sequence[str],
) -> dict[str, tuple[str, int]]:
    """Derive the eight canonical requirement states for one admitted student.

    The generator's checklist and the canonical requirement list overlap but do
    not coincide — the generator tracks "did you file a FAFSA", the schema
    tracks "is financial-aid verification complete" — so each canonical code is
    derived from whichever generator facts actually determine it, and a
    dependency gate runs afterwards over the result.
    """

    def task_status(code: str) -> str:
        task = tasks.get(code)
        return str(task["status"]) if task is not None else "not_started"

    def from_document(category: str) -> tuple[str, int]:
        status = document_statuses.get(category)
        if status is None:
            return ("ready", 0)
        return _DOCUMENT_REQUIREMENT_STATES[status]

    states: dict[str, tuple[str, int]] = {}

    # Accepting the offer is what confirms the applicant's identity record.
    states["profile_verification"] = (
        ("completed", 100) if task_status("accept_offer") == "complete" else ("ready", 0)
    )
    states["identity_document"] = from_document("photo_id")
    states["official_transcript"] = from_document("transcript")
    states["immunization_record"] = from_document("immunization")

    # Verification, not filing. A student selected for verification has filed a
    # FAFSA and still has work to do; the checklist's "complete_fafsa" would
    # call that finished, which is exactly the state staff need to see open.
    if fafsa_state is None:
        states["financial_aid_verification"] = ("ready", 0)
    elif fafsa_state == "not_received":
        states["financial_aid_verification"] = ("in_progress", 25)
    elif fafsa_state == "rejected":
        states["financial_aid_verification"] = ("rejected", 0)
    elif fafsa_state == "selected_for_verification":
        if verification_states and all(state == "satisfied" for state in verification_states):
            states["financial_aid_verification"] = ("completed", 100)
        elif any(state == "outstanding" for state in verification_states):
            states["financial_aid_verification"] = ("in_progress", 40)
        else:
            states["financial_aid_verification"] = ("under_review", 70)
    else:
        states["financial_aid_verification"] = ("completed", 100)

    deposit = task_status("pay_enrollment_deposit")
    states["enrollment_deposit"] = {
        "complete": ("completed", 100),
        "in_progress": ("in_progress", 50),
        "not_started": ("ready", 0),
        "waived": ("waived", 100),
    }[deposit]

    # The generator closes housing until the deposit posts. That is a real
    # prerequisite even though the schema does not declare it, so it becomes a
    # `blocked` status rather than an unexplained `ready` the student cannot use.
    housing = task_status("apply_for_housing")
    if states["enrollment_deposit"][0] not in _DONE_STATUSES:
        states["housing_preference"] = ("blocked", 0)
    else:
        states["housing_preference"] = {
            "complete": ("completed", 100),
            "in_progress": ("in_progress", 50),
            "not_started": ("ready", 0),
            "waived": ("waived", 100),
        }[housing]

    orientation = task_status("register_for_orientation")
    if orientation == "complete":
        states["orientation_registration"] = ("completed", 100)
    elif states["enrollment_deposit"][0] not in _DONE_STATUSES:
        states["orientation_registration"] = ("blocked", 0)
    else:
        states["orientation_registration"] = ("ready", 0)

    # Schema-declared dependencies win over any derived `ready`.
    dependencies = {
        "identity_document": ("profile_verification",),
        "official_transcript": ("profile_verification",),
        "immunization_record": ("profile_verification",),
    }
    for code, prerequisites in dependencies.items():
        status, progress = states[code]
        if status in _DONE_STATUSES:
            continue
        if any(states[prerequisite][0] not in _DONE_STATUSES for prerequisite in prerequisites):
            states[code] = ("blocked", 0)
        else:
            states[code] = (status, progress)
    return states


def _onboarding_state(
    *,
    deposit_state: str,
    housing_selected: bool,
) -> tuple[str, str, tuple[str, ...]]:
    """(status, current_step, completed_steps) for the onboarding wizard."""

    all_steps = (
        "offer",
        "about_you",
        "housing",
        "campus_life",
        "emergency_contacts",
        "family_permissions",
        "review_and_sign",
        "deposit",
    )
    if deposit_state == "complete":
        return ("completed", "deposit", all_steps)
    if deposit_state == "in_progress":
        return ("in_progress", "deposit", all_steps[:-1])
    if housing_selected:
        return ("in_progress", "campus_life", all_steps[:3])
    return ("in_progress", "housing", all_steps[:2])


def _onboarding_payload(
    student: Mapping[str, Any],
    *,
    housing_application: Mapping[str, Any] | None,
    housing_assignment: Mapping[str, Any] | None,
    completed: bool,
) -> dict[str, Any]:
    """Wizard answers consistent with the state written alongside them.

    Only fields the wizard itself owns are written. Generator concepts with no
    wizard field — a room label, a meal plan code — are recorded where the
    wizard already has somewhere to put them and dropped where it does not.
    """

    residency = str(student["residency"])
    preference = str(housing_application["preference"]) if housing_application is not None else None
    payload: dict[str, Any] = {
        "housingPreference": (
            "on_campus"
            if preference == "residential"
            else "off_campus"
            if preference == "commuter"
            else "undecided"
        ),
        "skippedSteps": [],
        "residencyStatus": "international" if residency == "international" else "domestic",
        "citizenshipStatus": ("international" if residency == "international" else "us_citizen"),
    }
    if not completed:
        return payload
    first_name = str(student["firstName"])
    last_name = str(student["lastName"])
    payload.update(
        {
            "firstName": first_name,
            "lastName": last_name,
            "preferredName": str(student["preferredName"]),
            "personalEmail": str(student["email"]),
            "communicationPreference": "email",
        }
    )
    if housing_application is not None and preference == "residential":
        room_type = housing_application.get("roomTypePreference")
        payload["housingResidenceOption"] = "residence_hall"
        if isinstance(room_type, str) and room_type != "no_preference":
            payload["housingRoomType"] = room_type
    if housing_assignment is not None:
        payload["housingAssignmentLabel"] = str(housing_assignment["roomLabel"])
        payload["mealPlanCode"] = str(housing_assignment["mealPlanCode"])
    return payload


class _Mapper:
    """Turns one decoded universe into canonical rows for one tenant."""

    def __init__(
        self,
        universe: Mapping[str, Any],
        *,
        tenant_id: str,
        journey_definition_id: UUID,
    ) -> None:
        self._universe = universe
        self._tenant = UUID(tenant_id)
        self._campus = UUID(SYNTHETIC_CAMPUS_ID)
        self._journey_definition = journey_definition_id
        self._now = _instant(universe["meta"]["generatedFor"])
        self.tables = _Tables()
        self.report = SyntheticUniverseReport()
        self._requirement_definitions: dict[str, RequirementDefinition] = {}
        self._work_items: list[_WorkItemCandidate] = []

    # -- reference data ---------------------------------------------------

    def with_requirement_definitions(
        self, definitions: Mapping[str, RequirementDefinition]
    ) -> _Mapper:
        missing = [code for code in _REQUIREMENT_CODES if code not in definitions]
        if missing:
            raise SyntheticUniverseError(
                "The demo tenant is missing requirement definitions: " + ", ".join(missing)
            )
        self._requirement_definitions = dict(definitions)
        return self

    def map_reference_data(self) -> None:
        for term in self._universe["terms"]:
            starts_at = _instant(term["startsAt"])
            self.tables.academic_term.append(
                {
                    "id": UUID(str(term["id"])),
                    "tenant_id": self._tenant,
                    "name": str(term["name"]),
                    "starts_on": starts_at.date(),
                }
            )
        for program in self._universe["programs"]:
            self.tables.program.append(
                {
                    "id": UUID(str(program["id"])),
                    "tenant_id": self._tenant,
                    "name": str(program["name"]),
                    "code": str(program["code"]),
                    "degree": str(program["degreeType"]),
                    "total_credits": int(program["requiredCredits"]),
                    "description": (
                        f"{program['degreeType']} in {program['name']}, offered by the "
                        f"{program['department']} department."
                    ),
                    "source_label": "Synthetic university generator",
                    "source_url": None,
                    "source_status": "synthetic_preview",
                }
            )
        for advisor in self._universe["advisors"]:
            self.tables.staff_member.append(
                {
                    "id": UUID(str(advisor["id"])),
                    "tenant_id": self._tenant,
                    "display_name": f"{advisor['firstName']} {advisor['lastName']}",
                    "email_normalized": str(advisor["email"]).lower(),
                    "component": str(advisor["department"]),
                    "active": True,
                }
            )

    # -- population -------------------------------------------------------

    def map_population(self) -> None:
        universe = self._universe
        programs_by_code = {str(row["code"]): UUID(str(row["id"])) for row in universe["programs"]}
        terms_by_code = {str(row["code"]): row for row in universe["terms"]}
        coa_by_key = {
            f"{row['cohort']}:{row['residency']}": row for row in universe["costOfAttendance"]
        }
        applications = {str(row["studentId"]): row for row in universe["applications"]}
        tasks_by_student = _group_by_student(universe["checklistTasks"])
        documents_by_student = _group_by_student(universe["documents"])
        fafsa_by_student = {str(row["studentId"]): row for row in universe["fafsaRecords"]}
        verification_by_student = _group_by_student(universe["verificationRequirements"])
        awards_by_student = _group_by_student(universe["aidAwards"])
        sap_by_student = {str(row["studentId"]): row for row in universe["sapStatus"]}
        ledger_by_student = _group_by_student(universe["accountLedger"])
        housing_applications = {
            str(row["studentId"]): row for row in universe["housingApplications"]
        }
        housing_assignments = {str(row["studentId"]): row for row in universe["housingAssignments"]}

        dropped: dict[str, int] = {
            "holds": len(universe["holds"]),
            "registrationEligibility": len(universe["registrationEligibility"]),
            "internationalRequirements": len(universe["internationalRequirements"]),
            "disbursements": len(universe["disbursements"]),
            "housingAssignments": 0,
            "accountLedgerLines": len(universe["accountLedger"]),
            "documentStatusHistoryEntries": 0,
            "orientationRegistrations": len(universe["orientationRegistrations"]),
            "courses": len(universe.get("courses", ())),
            "sections": len(universe.get("sections", ())),
            "residenceHalls": len(universe["residenceHalls"]),
            "mealPlans": len(universe["mealPlans"]),
        }

        self.report.students_generated = len(universe["students"])

        for student in universe["students"]:
            student_key = str(student["id"])
            application = applications.get(student_key)
            if application is None:
                self._reject(student, "no application record")
                continue
            decision = str(application["decision"])
            if decision != "admitted":
                # Audentra models enrollment, and every path that creates a
                # `student` row creates an admission offer with it. A denied or
                # waitlisted applicant has no offer, and importing one produces
                # an account whose dashboard and financial pages 404 — a state
                # production has never held. The applicant is skipped rather
                # than given a fabricated offer to make the page render.
                self._reject(student, f"applicant decision is {decision}, not admitted")
                continue
            program_id = programs_by_code.get(str(student["programCode"]))
            term = terms_by_code.get(str(student["cohort"]))
            if program_id is None or term is None:
                self._reject(student, "program or term does not resolve")
                continue
            documents = documents_by_student.get(student_key, ())
            dropped["documentStatusHistoryEntries"] += sum(
                len(document.get("statusHistory") or ()) for document in documents
            )
            try:
                self._map_student(
                    student,
                    application=application,
                    program_id=program_id,
                    term=term,
                    tasks={
                        str(task["taskCode"]): task
                        for task in tasks_by_student.get(student_key, ())
                    },
                    documents=documents,
                    fafsa=fafsa_by_student.get(student_key),
                    verifications=verification_by_student.get(student_key, ()),
                    awards=awards_by_student.get(student_key, ()),
                    sap=sap_by_student.get(student_key),
                    ledger=ledger_by_student.get(student_key, ()),
                    housing_application=housing_applications.get(student_key),
                    housing_assignment=housing_assignments.get(student_key),
                    cost_of_attendance=coa_by_key.get(
                        f"{student['cohort']}:{student['residency']}"
                    ),
                )
            except SyntheticUniverseError as error:
                self._reject(student, str(error))
                continue
            self.report.students_imported += 1
            if housing_assignments.get(student_key) is not None:
                dropped["housingAssignments"] += 1

        self._map_work_items()
        self.report.dropped_records = dropped
        self.report.row_counts = {
            name: len(getattr(self.tables, name))
            for name in _TABLE_ORDER
            if getattr(self.tables, name)
        }

    def _reject(self, student: Mapping[str, Any], reason: str) -> None:
        self.report.students_rejected += 1
        self.report.rejection_reasons[reason] = self.report.rejection_reasons.get(reason, 0) + 1
        if len(self.report.rejections) < 25:
            self.report.rejections.append(f"{student.get('externalRef', student['id'])}: {reason}")

    # -- one student ------------------------------------------------------

    def _map_student(
        self,
        student: Mapping[str, Any],
        *,
        application: Mapping[str, Any],
        program_id: UUID,
        term: Mapping[str, Any],
        tasks: Mapping[str, Mapping[str, Any]],
        documents: Sequence[Mapping[str, Any]],
        fafsa: Mapping[str, Any] | None,
        verifications: Sequence[Mapping[str, Any]],
        awards: Sequence[Mapping[str, Any]],
        sap: Mapping[str, Any] | None,
        ledger: Sequence[Mapping[str, Any]],
        housing_application: Mapping[str, Any] | None,
        housing_assignment: Mapping[str, Any] | None,
        cost_of_attendance: Mapping[str, Any] | None,
    ) -> None:
        tables = self.tables
        student_id = UUID(str(student["id"]))
        person_id = _uuid("person", str(student["id"]))
        external_ref = str(student["externalRef"])
        term_start = _instant(term["startsAt"])
        decided_at = _instant(application["decidedAt"])

        tables.person.append(
            {
                "id": person_id,
                "tenant_id": self._tenant,
                "preferred_name": str(student["preferredName"]),
                "first_name": str(student["firstName"]),
                "last_name": str(student["lastName"]),
            }
        )
        tables.student.append(
            {
                "id": student_id,
                "tenant_id": self._tenant,
                "person_id": person_id,
                "class_year": _class_year(term_start, str(student["admitType"])),
                "external_ref": external_ref,
            }
        )
        tables.student_profile.append(
            {
                "tenant_id": self._tenant,
                "student_id": student_id,
                "preferred_name": str(student["preferredName"]),
                "communication_preference": "email",
                "version": 1,
            }
        )

        document_statuses = {
            str(document["category"]): str(document["status"]) for document in documents
        }
        verification_states = [str(row["status"]) for row in verifications]
        fafsa_state = str(fafsa["status"]) if fafsa is not None else None

        requirement_ids: dict[str, UUID] = {}

        # Every applicant that reaches here was admitted, so the offer, the
        # journey, and the requirement set always exist.
        accept_task = tasks.get("accept_offer")
        accepted_at = _optional_instant(
            accept_task.get("completedAt") if accept_task is not None else None
        ) or (decided_at + timedelta(days=7))
        offer_id = _uuid("offer", str(student["id"]))
        tables.admission_offer.append(
            {
                "id": offer_id,
                "tenant_id": self._tenant,
                "student_id": student_id,
                "program_id": program_id,
                "academic_term_id": UUID(str(term["id"])),
                "campus_id": self._campus,
                "response_deadline": _instant(application["depositDeadline"]).date(),
                "deposit_amount_cents": SYNTHETIC_DEPOSIT_CENTS,
                "status": "accepted",
                "accepted_at": accepted_at,
                "version": 2,
                "created_at": decided_at,
                "updated_at": accepted_at,
            }
        )

        states = _requirement_states(
            tasks=tasks,
            document_statuses=document_statuses,
            fafsa_state=fafsa_state,
            verification_states=verification_states,
        )
        blocking_open = any(
            status not in _DONE_STATUSES
            for code, (status, _) in states.items()
            if code != "housing_preference"
        )
        journey_id = _uuid("journey", str(student["id"]))
        tables.enrollment_journey.append(
            {
                "id": journey_id,
                "tenant_id": self._tenant,
                "student_id": student_id,
                "offer_id": offer_id,
                "journey_definition_version_id": self._journey_definition,
                "status": "in_progress" if blocking_open else "completed",
                "version": 1,
                "created_at": accepted_at,
                "updated_at": self._now,
            }
        )
        for code in _REQUIREMENT_CODES:
            status, progress = states[code]
            requirement_id = _uuid("requirement", str(student["id"]), code)
            requirement_ids[code] = requirement_id
            definition = self._requirement_definitions[code]
            # The generator dates its own checklist against the term, which
            # is more informative than a flat offset — but two canonical
            # requirements have no checklist counterpart, and those fall
            # back to the tenant's declared offset from acceptance.
            task = tasks.get(_TASK_FOR_REQUIREMENT.get(code, ""))
            if task is not None:
                due_at = _optional_instant(task.get("dueAt"))
            elif definition.due_offset_days is not None:
                due_at = accepted_at + timedelta(days=definition.due_offset_days)
            else:
                due_at = None
            tables.student_requirement.append(
                {
                    "id": requirement_id,
                    "tenant_id": self._tenant,
                    "journey_id": journey_id,
                    "requirement_definition_version_id": definition.definition_id,
                    "status": status,
                    "due_at": due_at,
                    "progress_percent": progress,
                    "version": 1,
                    "created_at": accepted_at,
                    "updated_at": self._now,
                }
            )

        # -- documents ----------------------------------------------------
        for document in documents:
            generator_status = str(document["status"])
            canonical_status = _DOCUMENT_STATUSES[generator_status]
            if canonical_status is None:
                continue
            category = str(document["category"])
            document_id = UUID(str(document["id"]))
            history = document.get("statusHistory") or ()
            created_at = _instant(history[0]["at"]) if history else decided_at
            updated_at = _instant(history[-1]["at"]) if history else decided_at
            requirement_code = _DOCUMENT_REQUIREMENT_CODES.get(category)
            tables.document_record.append(
                {
                    "id": document_id,
                    "tenant_id": self._tenant,
                    "student_id": student_id,
                    "file_name": f"{category}-{str(student['lastName']).lower()}.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": _deterministic_size_bytes(str(document["id"])),
                    "category": _DOCUMENT_CATEGORIES[category],
                    "status": canonical_status,
                    "storage_provider": "local_placeholder",
                    "storage_key": f"synthetic/{student['id']}/{document['id']}.pdf",
                    "sha256": _sha256_of(str(document["id"])),
                    "processing_mode": "agentic",
                    "requirement_id": (
                        requirement_ids.get(requirement_code) if requirement_code else None
                    ),
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )
            if generator_status in {"REJECTED", "NEEDS_RESUBMISSION"}:
                self._work_items.append(
                    _WorkItemCandidate(
                        priority_rank=0,
                        external_ref=external_ref,
                        student_id=student_id,
                        title=f"Re-review {document['label']} for {student['firstName']}",
                        description=(
                            f"{document['label']} was returned by "
                            f"{document['responsibleOffice']}. Confirm the reason and send "
                            "resubmission instructions."
                        ),
                        component="Registrar",
                        action_type="document_review",
                        priority="high",
                        source_type="document",
                        source_id=document_id,
                        due_at=self._now + timedelta(days=3),
                    )
                )

        # -- financial aid documents --------------------------------------
        if fafsa is not None:
            tables.financial_document_requirement.append(
                {
                    "id": _uuid("fafsa", str(student["id"])),
                    "tenant_id": self._tenant,
                    "student_id": student_id,
                    "code": "fafsa",
                    "title": "Free Application for Federal Student Aid",
                    "description": (f"FAFSA on file for the {SYNTHETIC_ACADEMIC_YEAR} aid year."),
                    "status": _FAFSA_DOCUMENT_STATUSES[str(fafsa["status"])],
                    "due_at": None,
                    "document_id": None,
                    "version": 1,
                    "created_at": _optional_instant(fafsa.get("receivedAt")) or decided_at,
                    "updated_at": self._now,
                }
            )
        for requirement in verifications:
            tables.financial_document_requirement.append(
                {
                    "id": UUID(str(requirement["id"])),
                    "tenant_id": self._tenant,
                    "student_id": student_id,
                    "code": str(requirement["code"]),
                    "title": str(requirement["label"]),
                    "description": (
                        f"Federal verification requirement for the "
                        f"{SYNTHETIC_ACADEMIC_YEAR} aid year."
                    ),
                    "status": _VERIFICATION_DOCUMENT_STATUSES[str(requirement["status"])],
                    "due_at": _optional_instant(requirement.get("dueAt")),
                    "document_id": None,
                    "version": 1,
                    "created_at": decided_at,
                    "updated_at": self._now,
                }
            )

        # -- awards -------------------------------------------------------
        for award in awards:
            award_type = str(award["awardType"])
            source = str(award["source"])
            if award_type not in _AWARD_TYPES or source not in _AWARD_SOURCES:
                raise SyntheticUniverseError(
                    f"award {award['id']} has unmapped type/source {award_type}/{source}"
                )
            accepted = award.get("acceptedAmountUsd")
            tables.student_financial_award.append(
                {
                    "id": UUID(str(award["id"])),
                    "tenant_id": self._tenant,
                    "student_id": student_id,
                    "academic_year": str(award["aidYear"]),
                    "source": source,
                    "name": str(award["name"]),
                    "type": award_type,
                    "offered_amount_cents": _cents(award["offeredAmountUsd"]),
                    "accepted_amount_cents": 0 if accepted is None else _cents(accepted),
                    "status": _AWARD_STATUSES[str(award["status"])],
                    "requires_action": bool(
                        str(award["status"]) in {"offered", "pending"}
                        or award.get("requiresPromissoryNote")
                        or award.get("requiresEntranceCounseling")
                    ),
                    "created_at": _instant(award["offeredAt"]),
                    "updated_at": self._now,
                }
            )

        # -- money --------------------------------------------------------
        if cost_of_attendance is not None:
            external_payments = sum(
                abs(_cents(entry["amountUsd"]))
                for entry in ledger
                if str(entry["code"]) == "student_payment"
            )
            tables.student_financial_summary.append(
                {
                    "tenant_id": self._tenant,
                    "student_id": student_id,
                    "academic_year": SYNTHETIC_ACADEMIC_YEAR,
                    "cost_of_attendance_cents": _cents(cost_of_attendance["totalUsd"]),
                    "external_payments_cents": external_payments,
                    "version": 1,
                }
            )

        deposit_entry = next(
            (entry for entry in ledger if str(entry["code"]) == "enrollment_deposit"),
            None,
        )
        if deposit_entry is not None:
            tables.payment_transaction.append(
                {
                    "id": _uuid("deposit", str(student["id"])),
                    "tenant_id": self._tenant,
                    "student_id": student_id,
                    "offer_id": offer_id,
                    "type": "enrollment_deposit",
                    "amount_cents": abs(_cents(deposit_entry["amountUsd"])),
                    "status": "succeeded",
                    "processor": "dummy",
                    "processor_reference": f"syn_{deposit_entry['id']}",
                    "created_at": _instant(deposit_entry["postedAt"]),
                    "updated_at": _instant(deposit_entry["postedAt"]),
                }
            )

        # -- academic progress --------------------------------------------
        if sap is not None:
            attempted = _ATTEMPTED_CREDITS_BY_ADMIT_TYPE.get(str(student["admitType"]), 24)
            tables.student_sap_status.append(
                {
                    "tenant_id": self._tenant,
                    "student_id": student_id,
                    "academic_year": SYNTHETIC_ACADEMIC_YEAR,
                    "status": _SAP_STATUSES[str(sap["status"])],
                    "cumulative_gpa": Decimal(str(sap["gpa"])),
                    "minimum_gpa": Decimal("2.000"),
                    "completion_rate_percent": Decimal(
                        str(round(float(sap["completionRate"]) * 100, 2))
                    ),
                    "minimum_completion_rate_percent": Decimal("67.00"),
                    "attempted_credits": Decimal(attempted),
                    "maximum_attempted_credits": Decimal(180),
                    "calculated_at": _instant(sap["evaluatedAt"]),
                    "version": 1,
                }
            )

        # -- onboarding ----------------------------------------------------
        deposit_state = states["enrollment_deposit"][0]
        deposit_step = (
            "complete"
            if deposit_state in _DONE_STATUSES
            else "in_progress"
            if deposit_state == "in_progress"
            else "not_started"
        )
        onboarding_status, current_step, completed_steps = _onboarding_state(
            deposit_state=deposit_step,
            housing_selected=housing_application is not None,
        )
        payload = _onboarding_payload(
            student,
            housing_application=housing_application,
            housing_assignment=housing_assignment,
            completed=onboarding_status == "completed",
        )
        completed_at = (
            _instant(deposit_entry["postedAt"])
            if onboarding_status == "completed" and deposit_entry is not None
            else accepted_at
            if onboarding_status == "completed"
            else None
        )
        tables.student_onboarding.append(
            {
                "tenant_id": self._tenant,
                "student_id": student_id,
                "status": onboarding_status,
                "current_step": current_step,
                "completed_steps": list(completed_steps),
                "payload": json.dumps(payload),
                "version": len(completed_steps) + 1,
                "completed_at": completed_at,
            }
        )
        tables.student_portal_projection.append(
            {
                "tenant_id": self._tenant,
                "student_id": student_id,
                "projection_version": 1,
                "dashboard": "{}",
                "source_updated_at": self._now,
            }
        )

        # -- inbox and appointments ---------------------------------------
        tables.student_message.append(
            {
                "id": _uuid("welcome-message", str(student["id"])),
                "tenant_id": self._tenant,
                "student_id": student_id,
                "subject": f"Welcome to Aster, {student['preferredName']}",
                "body": (
                    "Your enrollment portal has your current checklist, your financial "
                    "summary, and everything Aster still needs from you."
                ),
                "sender_name": "Enrollment Services",
                "sent_at": decided_at,
                "kind": "general",
                "href": None,
            }
        )
        if tasks.get("meet_academic_advisor", {}).get("status") == "complete":
            completed_task_at = _optional_instant(tasks["meet_academic_advisor"].get("completedAt"))
            tables.student_appointment.append(
                {
                    "id": _uuid("advising-appointment", str(student["id"])),
                    "tenant_id": self._tenant,
                    "student_id": student_id,
                    "type": "enrollment_support",
                    "starts_at": completed_task_at or (self._now - timedelta(days=14)),
                    "notes": "Academic advising meeting completed.",
                    "status": "completed",
                }
            )

        # -- deadline risk --------------------------------------------------
        response_deadline = _instant(application["depositDeadline"])
        if deposit_entry is None and response_deadline < self._now:
            self._work_items.append(
                _WorkItemCandidate(
                    priority_rank=1,
                    external_ref=external_ref,
                    student_id=student_id,
                    title=f"Deposit deadline passed for {student['firstName']}",
                    description=(
                        "The enrollment deposit deadline has passed with no payment on "
                        "file. Confirm whether the student still intends to enroll."
                    ),
                    component="Admissions",
                    action_type="deadline_risk",
                    priority="urgent",
                    source_type="requirement",
                    source_id=requirement_ids["enrollment_deposit"],
                    due_at=self._now + timedelta(days=1),
                )
            )

    # -- staff board ------------------------------------------------------

    def _map_work_items(self) -> None:
        staff = self.tables.staff_member
        if not staff:
            return
        candidates = sorted(
            self._work_items,
            key=lambda candidate: (candidate.priority_rank, candidate.external_ref),
        )[:MAX_WORK_ITEMS]
        for index, candidate in enumerate(candidates):
            assignee = staff[index % len(staff)]
            item_id = _uuid("work-item", str(candidate.source_id))
            self.tables.staff_work_item.append(
                {
                    "id": item_id,
                    "tenant_id": self._tenant,
                    "student_id": candidate.student_id,
                    "key": f"SYN-{index + 1:04d}",
                    "title": candidate.title[:240],
                    "description": candidate.description,
                    "status": "todo",
                    "priority": candidate.priority,
                    "work_type": (
                        "document_review"
                        if candidate.action_type == "document_review"
                        else "enrollment"
                    ),
                    "component": candidate.component,
                    "due_at": candidate.due_at,
                    "escalated": False,
                    "assignee_id": assignee["id"],
                    "source_type": candidate.source_type,
                    "source_id": candidate.source_id,
                    "version": 1,
                    "action_type": candidate.action_type,
                }
            )
            self.tables.staff_work_log.append(
                {
                    "id": _uuid("work-log", str(item_id)),
                    "tenant_id": self._tenant,
                    "work_item_id": item_id,
                    "actor_type": "system",
                    "actor_id": None,
                    "actor_name": "Enrollment workflow",
                    "action": "created",
                    "message": "Created from the synthetic university demo population.",
                    "occurred_at": self._now,
                }
            )


#: Requirement code -> the generator checklist task whose due date it inherits.
_TASK_FOR_REQUIREMENT: Final[Mapping[str, str]] = {
    "profile_verification": "accept_offer",
    "official_transcript": "submit_final_transcript",
    "financial_aid_verification": "complete_fafsa",
    "immunization_record": "submit_immunization_record",
    "housing_preference": "apply_for_housing",
    "enrollment_deposit": "pay_enrollment_deposit",
    "orientation_registration": "register_for_orientation",
}


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

#: Tables in foreign-key order, with the conflict target that makes the write
#: idempotent. `staff_member`, `program`, and `academic_term` come first
#: because students point at them.
_TABLE_ORDER: Final[tuple[str, ...]] = (
    "academic_term",
    "program",
    "staff_member",
    "person",
    "student",
    "student_profile",
    "admission_offer",
    "enrollment_journey",
    "student_requirement",
    "document_record",
    "financial_document_requirement",
    "student_financial_award",
    "student_financial_summary",
    "student_sap_status",
    "payment_transaction",
    "student_onboarding",
    "student_portal_projection",
    "student_message",
    "student_appointment",
    "staff_work_item",
    "staff_work_log",
)

_CONFLICT_TARGETS: Final[Mapping[str, str]] = {
    "academic_term": "(id)",
    "program": "(id)",
    "staff_member": "(id)",
    "person": "(id)",
    "student": "(id)",
    "student_profile": "(tenant_id, student_id)",
    "admission_offer": "(id)",
    "enrollment_journey": "(id)",
    "student_requirement": "(id)",
    "document_record": "(id)",
    "financial_document_requirement": "(id)",
    "student_financial_award": "(id)",
    "student_financial_summary": "(tenant_id, student_id, academic_year)",
    "student_sap_status": "(tenant_id, student_id, academic_year)",
    "payment_transaction": "(id)",
    "student_onboarding": "(tenant_id, student_id)",
    "student_portal_projection": "(tenant_id, student_id)",
    "student_message": "(id)",
    "student_appointment": "(id)",
    "staff_work_item": "(id)",
    "staff_work_log": "(id)",
}

#: Columns that must be cast on the way in because the driver cannot infer the
#: PostgreSQL type from a Python list or a JSON string.
_COLUMN_CASTS: Final[Mapping[tuple[str, str], str]] = {
    ("student_onboarding", "completed_steps"): "text[]",
    ("student_onboarding", "payload"): "jsonb",
    ("student_portal_projection", "dashboard"): "jsonb",
}


def _insert_statement(table: str, columns: Sequence[str]) -> str:
    values = ", ".join(
        (
            f"CAST(:{column} AS {_COLUMN_CASTS[(table, column)]})"
            if (table, column) in _COLUMN_CASTS
            else f":{column}"
        )
        for column in columns
    )
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values}) "
        f"ON CONFLICT {_CONFLICT_TARGETS[table]} DO NOTHING"
    )


async def _write_table(
    connection: AsyncConnection,
    table: str,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    if not rows:
        return
    columns = tuple(rows[0].keys())
    statement = text(_insert_statement(table, columns))
    for start in range(0, len(rows), _BATCH_SIZE):
        batch = [dict(row) for row in rows[start : start + _BATCH_SIZE]]
        await connection.execute(statement, batch)


# --------------------------------------------------------------------------
# Post-write invariants
# --------------------------------------------------------------------------

#: Statements that must each return zero rows after the import. These are the
#: mistakes the Edward parity work found in hand-written fixtures: a payment
#: with no compatible offer, a requirement with no journey, a status the schema
#: allows but the domain cannot mean.
_INVARIANT_QUERIES: Final[tuple[tuple[str, str], ...]] = (
    (
        "deposit without an accepted offer",
        """
        SELECT count(*) FROM payment_transaction AS pay
        JOIN admission_offer AS offer ON offer.id = pay.offer_id
        WHERE pay.tenant_id = :tenant_id AND offer.status <> 'accepted'
        """,
    ),
    (
        "deposit whose offer belongs to another student",
        """
        SELECT count(*) FROM payment_transaction AS pay
        JOIN admission_offer AS offer ON offer.id = pay.offer_id
        WHERE pay.tenant_id = :tenant_id
          AND (offer.student_id <> pay.student_id OR offer.tenant_id <> pay.tenant_id)
        """,
    ),
    (
        "requirement outside its journey's tenant",
        """
        SELECT count(*) FROM student_requirement AS req
        JOIN enrollment_journey AS journey ON journey.id = req.journey_id
        WHERE req.tenant_id = :tenant_id AND journey.tenant_id <> req.tenant_id
        """,
    ),
    (
        "journey for an offer that was never accepted",
        """
        SELECT count(*) FROM enrollment_journey AS journey
        JOIN admission_offer AS offer ON offer.id = journey.offer_id
        WHERE journey.tenant_id = :tenant_id
          AND (offer.status <> 'accepted' OR offer.accepted_at IS NULL)
        """,
    ),
    (
        "housing requirement complete while the deposit is not",
        """
        SELECT count(*) FROM student_requirement AS housing
        JOIN requirement_definition_version AS housing_def
          ON housing_def.id = housing.requirement_definition_version_id
        JOIN student_requirement AS deposit ON deposit.journey_id = housing.journey_id
        JOIN requirement_definition_version AS deposit_def
          ON deposit_def.id = deposit.requirement_definition_version_id
        WHERE housing.tenant_id = :tenant_id
          AND housing_def.code = 'housing_preference'
          AND deposit_def.code = 'enrollment_deposit'
          AND housing.status IN ('completed', 'waived')
          AND deposit.status NOT IN ('completed', 'waived')
        """,
    ),
    (
        "document filed against another student's requirement",
        """
        SELECT count(*) FROM document_record AS doc
        JOIN student_requirement AS req ON req.id = doc.requirement_id
        JOIN enrollment_journey AS journey ON journey.id = req.journey_id
        WHERE doc.tenant_id = :tenant_id AND journey.student_id <> doc.student_id
        """,
    ),
    (
        "student outside the demo tenant's person records",
        """
        SELECT count(*) FROM student
        JOIN person ON person.id = student.person_id
        WHERE student.tenant_id = :tenant_id AND person.tenant_id <> student.tenant_id
        """,
    ),
    (
        "work item assigned to staff in another tenant",
        """
        SELECT count(*) FROM staff_work_item AS item
        JOIN staff_member AS staff ON staff.id = item.assignee_id
        WHERE item.tenant_id = :tenant_id AND staff.tenant_id <> item.tenant_id
        """,
    ),
    (
        "onboarding marked complete with no completion instant",
        """
        SELECT count(*) FROM student_onboarding
        WHERE tenant_id = :tenant_id AND status = 'completed' AND completed_at IS NULL
        """,
    ),
    (
        "aid award accepting more than was offered",
        """
        SELECT count(*) FROM student_financial_award
        WHERE tenant_id = :tenant_id AND accepted_amount_cents > offered_amount_cents
        """,
    ),
)


async def verify_synthetic_invariants(
    connection: AsyncConnection,
    *,
    tenant_id: str = SYNTHETIC_TENANT_ID,
) -> list[str]:
    """Assert the imported population against the domain, not the schema.

    PostgreSQL already refuses a bad foreign key or an out-of-range status.
    These queries look for the combinations it happily accepts and the product
    can never mean.
    """

    violations: list[str] = []
    for label, query in _INVARIANT_QUERIES:
        result = await connection.execute(text(query), {"tenant_id": UUID(tenant_id)})
        count = int(result.scalar_one())
        if count:
            violations.append(f"{label}: {count}")
    return violations


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


async def import_synthetic_university(
    connection: AsyncConnection,
    *,
    tenant_id: str = SYNTHETIC_TENANT_ID,
    journey_definition_id: UUID,
    requirement_definitions: Mapping[str, RequirementDefinition],
    asset_root: Path | None = None,
    universe: Mapping[str, Any] | None = None,
) -> SyntheticUniverseReport:
    """Converge the whole synthetic population into canonical tables.

    Runs inside the caller's transaction so a failed import leaves no partial
    university behind.
    """

    started = time.perf_counter()
    decoded = load_synthetic_universe(asset_root) if universe is None else dict(universe)
    load_seconds = time.perf_counter() - started

    started = time.perf_counter()
    mapper = _Mapper(
        decoded,
        tenant_id=tenant_id,
        journey_definition_id=journey_definition_id,
    ).with_requirement_definitions(requirement_definitions)
    mapper.map_reference_data()
    mapper.map_population()
    map_seconds = time.perf_counter() - started

    started = time.perf_counter()
    for table in _TABLE_ORDER:
        await _write_table(connection, table, getattr(mapper.tables, table))
    write_seconds = time.perf_counter() - started

    report = mapper.report
    report.load_seconds = load_seconds
    report.map_seconds = map_seconds
    report.write_seconds = write_seconds
    report.violations = await verify_synthetic_invariants(connection, tenant_id=tenant_id)
    if report.violations:
        raise SyntheticUniverseError(
            "The imported synthetic university violates domain invariants: "
            + "; ".join(report.violations)
        )
    return report


__all__ = [
    "SYNTHETIC_ACADEMIC_YEAR",
    "SYNTHETIC_CAMPUS_ID",
    "SYNTHETIC_JOURNEY_DEFINITION_ID",
    "SYNTHETIC_NAMESPACE",
    "SYNTHETIC_TENANT_ID",
    "SYNTHETIC_TENANT_NAME",
    "SYNTHETIC_TENANT_SLUG",
    "RequirementDefinition",
    "SyntheticUniverseError",
    "SyntheticUniverseReport",
    "import_synthetic_university",
    "load_synthetic_universe",
    "verify_synthetic_invariants",
]
