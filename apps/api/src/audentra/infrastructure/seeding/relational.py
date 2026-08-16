# ruff: noqa: S608 -- every interpolated identifier is fixture-manifest/schema validated.
"""Transactional PostgreSQL demo-data seeding independent of any web framework."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
)

from .fixture import FixtureTable, SeedFixtureError, load_demo_fixture
from .safety import assert_seed_environment

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_SEED_LOCK_NAME = "audentra.demo-seed.v1"
ASTER_TENANT_ID = "00000000-0000-7000-8000-000000000001"
ASTER_STUDENT_ID = "00000000-0000-7000-8000-000000000101"
HARVARD_TENANT_ID = "00000000-0000-7000-8000-000000000002"
HARVARD_PERSON_ID = "80000000-0000-7000-8000-000000000100"
HARVARD_STUDENT_ID = "80000000-0000-7000-8000-000000000101"
HARVARD_CAMPUS_ID = "80000000-0000-7000-8000-000000000110"
HARVARD_TERM_ID = "80000000-0000-7000-8000-000000000120"
HARVARD_OFFER_ID = "80000000-0000-7000-8000-000000000201"
HARVARD_STAFF_ID = "80000000-0000-7000-8000-000000000901"
_MANAGED_CONFIGURATION_FILES = {
    "journeys": "journeys.yaml",
    "campus_life": "campus-life.yaml",
    "academics": "academics.yaml",
}
_RESET_PRESERVED_TABLES = frozenset(
    {
        "staff_core_play",
        "staff_knowledge_card",
        "tenant",
        "tenant_portal_configuration",
        "staff_managed_configuration_version",
    }
)


def _managed_configuration_root(explicit: Path | None = None) -> Path:
    configured = os.getenv("MANAGED_CONFIGURATION_ROOT")
    candidates = tuple(
        dict.fromkeys(
            candidate.resolve()
            for candidate in (
                explicit,
                Path(configured) if configured else None,
                Path.cwd() / "assets" / "config" / "tenants",
                Path("/workspace/apps/api/assets/config/tenants"),
                Path(__file__).resolve().parents[4] / "assets" / "config" / "tenants",
            )
            if candidate is not None
        )
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise SeedFixtureError(f"Managed configuration seed root was not found; searched: {searched}")


@dataclass(frozen=True, slots=True)
class DemoRequirementDefinition:
    code: str
    title: str
    description: str
    blocking: bool
    display_order: int
    depends_on_codes: tuple[str, ...]
    due_offset_days: int
    submission_type: str
    responsible_office: str
    definition_suffix: str
    instance_suffix: str


@dataclass(frozen=True, slots=True)
class DemoStudentScenario:
    namespace: str
    tenant_id: str
    program_id: str
    campus_id: str
    term_id: str
    first_name: str
    last_name: str
    preferred_name: str
    class_year: int
    offer_status: str
    onboarding_status: str
    current_step: str
    completed_steps: tuple[str, ...]
    onboarding_payload: Mapping[str, object]
    accepted_at: datetime | None
    requirement_states: Mapping[str, tuple[str, int]]

    @property
    def person_id(self) -> str:
        return _demo_uuid(self.namespace, "000000000100")

    @property
    def student_id(self) -> str:
        return _demo_uuid(self.namespace, "000000000101")

    @property
    def offer_id(self) -> str:
        return _demo_uuid(self.namespace, "000000000201")

    @property
    def journey_id(self) -> str:
        return _demo_uuid(self.namespace, "000000000501")


@dataclass(frozen=True, slots=True)
class DemoStaffSeed:
    tenant_id: str
    staff_id: str
    display_name: str
    email: str
    component: str


@dataclass(frozen=True, slots=True)
class DemoWorkItemSeed:
    tenant_id: str
    item_id: str
    key: str
    student_id: str
    assignee_id: str
    title: str
    description: str
    status: str
    priority: str
    component: str
    due_at: datetime


_CANONICAL_REQUIREMENTS = (
    DemoRequirementDefinition(
        "profile_verification",
        "Verify your profile",
        "Confirm your personal and contact information before continuing.",
        True,
        10,
        (),
        7,
        "form",
        "Enrollment Services",
        "000000000401",
        "000000000601",
    ),
    DemoRequirementDefinition(
        "identity_document",
        "Provide identity documentation",
        "Upload an accepted identity document for institutional review.",
        True,
        20,
        ("profile_verification",),
        14,
        "document",
        "Registrar",
        "000000000402",
        "000000000602",
    ),
    DemoRequirementDefinition(
        "official_transcript",
        "Submit your official transcript",
        "Upload your transcript for document review and potential course-credit matching.",
        True,
        30,
        ("profile_verification",),
        14,
        "document",
        "Registrar",
        "000000000304",
        "000000000604",
    ),
    DemoRequirementDefinition(
        "financial_aid_verification",
        "Complete financial-aid verification",
        "Submit the requested verification worksheet and review your aid package.",
        True,
        40,
        (),
        10,
        "document",
        "Financial Aid",
        "000000000305",
        "000000000605",
    ),
    DemoRequirementDefinition(
        "immunization_record",
        "Provide immunization records",
        "Upload the required health clearance documentation before arrival.",
        True,
        50,
        ("profile_verification",),
        30,
        "document",
        "Student Health",
        "000000000306",
        "000000000606",
    ),
    DemoRequirementDefinition(
        "housing_preference",
        "Confirm housing plans",
        "Tell the university whether you plan to live on campus or elsewhere.",
        False,
        60,
        (),
        18,
        "form",
        "Housing & Residence Life",
        "000000000307",
        "000000000607",
    ),
    DemoRequirementDefinition(
        "enrollment_deposit",
        "Pay your enrollment deposit",
        "Complete the enrollment deposit through the approved payment flow.",
        True,
        70,
        (),
        21,
        "payment",
        "Student Accounts",
        "000000000403",
        "000000000603",
    ),
    DemoRequirementDefinition(
        "orientation_registration",
        "Register for orientation",
        "Choose an orientation session after your deposit and core records are complete.",
        True,
        80,
        ("enrollment_deposit", "identity_document"),
        35,
        "form",
        "New Student Programs",
        "000000000308",
        "000000000608",
    ),
)

_ONBOARDING_STEPS = (
    "offer",
    "about_you",
    "housing",
    "campus_life",
    "emergency_contacts",
    "family_permissions",
    "review_and_sign",
    "deposit",
)


def _onboarding_payload(
    *,
    first_name: str,
    last_name: str,
    completed: bool,
    housing_preference: str,
    skipped_steps: tuple[str, ...] = (),
) -> dict[str, object]:
    """Build answers consistent with the wizard status written by the seed."""

    payload: dict[str, object] = {
        "housingPreference": housing_preference,
        "skippedSteps": list(skipped_steps),
    }
    if not completed:
        return payload
    slug = f"{first_name}.{last_name}".lower()
    guardian = f"Robin {last_name}"
    guardian_email = f"robin.{last_name.lower()}@example.com"
    payload.update(
        {
            "firstName": first_name,
            "lastName": last_name,
            "preferredName": first_name,
            "personalEmail": f"{slug}@example.com",
            "mobilePhone": "+1 555 010 4471",
            "communicationPreference": "email",
            "citizenshipStatus": "us_citizen",
            "residencyStatus": "domestic",
            "residencyVerificationPath": "home_address_review",
            "streetAddress": "418 Larkspur Lane",
            "city": "Cambridge",
            "stateOrProvince": "MA",
            "postalCode": "02139",
            "country": "United States",
            "housingResidenceOption": "residence_hall",
            "housingRoomType": "double",
            "bathroomPreference": "shared_suite",
            "roommateMatching": "match_me",
            "sleepSchedule": "early_riser",
            "studyHabits": "quiet_room",
            "cleanliness": "tidy",
            "guestPreference": "occasional_guests",
            "substanceFreeHousing": True,
            "accommodationInterest": "not_now",
            "insuranceInterest": "learn_more",
            "campusInterests": ["undergraduate_research", "intramural_sports"],
            "socialComfort": "small_groups",
            "firstMonthGoals": ["meet_my_advisor", "join_one_club"],
            "emergencyContacts": [
                {
                    "name": guardian,
                    "relationship": "parent",
                    "phone": "+1 555 010 8823",
                    "email": guardian_email,
                    "isPrimary": True,
                }
            ],
            "familyPermissions": [
                {
                    "name": guardian,
                    "relationship": "parent",
                    "email": guardian_email,
                    "scopes": ["billing", "enrollment_progress"],
                }
            ],
            "signatureFullName": f"{first_name} {last_name}",
            "signatureMethod": "typed",
            "signatureConsent": True,
        }
    )
    return payload


_PRIMARY_REQUIREMENT_STATES: Mapping[str, tuple[str, int]] = {
    "financial_aid_verification": ("in_progress", 35),
    "housing_preference": ("in_progress", 50),
}

_EARLY_REQUIREMENT_STATES: Mapping[str, tuple[str, int]] = {
    "profile_verification": ("completed", 100),
    "identity_document": ("ready", 0),
    "official_transcript": ("ready", 0),
    "financial_aid_verification": ("in_progress", 35),
    "immunization_record": ("ready", 0),
    "housing_preference": ("in_progress", 50),
    "enrollment_deposit": ("ready", 0),
    "orientation_registration": ("blocked", 0),
}

_ADVANCED_REQUIREMENT_STATES: Mapping[str, tuple[str, int]] = {
    "profile_verification": ("completed", 100),
    "identity_document": ("completed", 100),
    "official_transcript": ("under_review", 80),
    "financial_aid_verification": ("completed", 100),
    "immunization_record": ("in_progress", 60),
    "housing_preference": ("completed", 100),
    "enrollment_deposit": ("completed", 100),
    "orientation_registration": ("ready", 0),
}

_EXTRA_STUDENTS = (
    DemoStudentScenario(
        namespace="10000000",
        tenant_id=ASTER_TENANT_ID,
        program_id="00000000-0000-7000-8000-000000000130",
        campus_id="00000000-0000-7000-8000-000000000110",
        term_id="00000000-0000-7000-8000-000000000120",
        first_name="Maya",
        last_name="Chen",
        preferred_name="Maya",
        class_year=2027,
        offer_status="accepted",
        onboarding_status="in_progress",
        current_step="housing",
        completed_steps=("offer", "about_you"),
        onboarding_payload=_onboarding_payload(
            first_name="Maya",
            last_name="Chen",
            completed=False,
            housing_preference="undecided",
        ),
        accepted_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        requirement_states=_EARLY_REQUIREMENT_STATES,
    ),
    DemoStudentScenario(
        namespace="11000000",
        tenant_id=ASTER_TENANT_ID,
        program_id="00000000-0000-7000-8000-000000000130",
        campus_id="00000000-0000-7000-8000-000000000110",
        term_id="00000000-0000-7000-8000-000000000120",
        first_name="Jordan",
        last_name="Ellis",
        preferred_name="Jordan",
        class_year=2027,
        offer_status="accepted",
        onboarding_status="completed",
        current_step="deposit",
        completed_steps=_ONBOARDING_STEPS,
        onboarding_payload=_onboarding_payload(
            first_name="Jordan",
            last_name="Ellis",
            completed=True,
            housing_preference="on_campus",
        ),
        accepted_at=datetime(2026, 7, 18, 16, 30, tzinfo=UTC),
        requirement_states=_ADVANCED_REQUIREMENT_STATES,
    ),
    DemoStudentScenario(
        namespace="81000000",
        tenant_id=HARVARD_TENANT_ID,
        program_id="80000000-0000-7000-8000-000000000101",
        campus_id=HARVARD_CAMPUS_ID,
        term_id=HARVARD_TERM_ID,
        first_name="Maya",
        last_name="Chen",
        preferred_name="Maya",
        class_year=2027,
        offer_status="accepted",
        onboarding_status="in_progress",
        current_step="housing",
        completed_steps=("offer", "about_you"),
        onboarding_payload=_onboarding_payload(
            first_name="Maya",
            last_name="Chen",
            completed=False,
            housing_preference="undecided",
        ),
        accepted_at=datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        requirement_states=_EARLY_REQUIREMENT_STATES,
    ),
    DemoStudentScenario(
        namespace="82000000",
        tenant_id=HARVARD_TENANT_ID,
        program_id="80000000-0000-7000-8000-000000000101",
        campus_id=HARVARD_CAMPUS_ID,
        term_id=HARVARD_TERM_ID,
        first_name="Jordan",
        last_name="Ellis",
        preferred_name="Jordan",
        class_year=2027,
        offer_status="accepted",
        onboarding_status="completed",
        current_step="deposit",
        completed_steps=_ONBOARDING_STEPS,
        onboarding_payload=_onboarding_payload(
            first_name="Jordan",
            last_name="Ellis",
            completed=True,
            housing_preference="on_campus",
        ),
        accepted_at=datetime(2026, 7, 18, 16, 30, tzinfo=UTC),
        requirement_states=_ADVANCED_REQUIREMENT_STATES,
    ),
)

_DEMO_STAFF = (
    DemoStaffSeed(
        ASTER_TENANT_ID,
        "00000000-0000-7000-8000-000000000901",
        "Priya Shah",
        "priya.shah@aster.example.edu",
        "Admissions",
    ),
    DemoStaffSeed(
        ASTER_TENANT_ID,
        "00000000-0000-7000-8000-000000000902",
        "Marcus Lee",
        "marcus.lee@aster.example.edu",
        "Registrar",
    ),
    DemoStaffSeed(
        ASTER_TENANT_ID,
        "00000000-0000-7000-8000-000000000903",
        "Elena Torres",
        "elena.torres@aster.example.edu",
        "Student Life",
    ),
    DemoStaffSeed(
        HARVARD_TENANT_ID,
        HARVARD_STAFF_ID,
        "Priya Shah",
        "priya.shah@harvard.example.edu",
        "Admissions",
    ),
    DemoStaffSeed(
        HARVARD_TENANT_ID,
        "80000000-0000-7000-8000-000000000902",
        "Marcus Lee",
        "marcus.lee@harvard.example.edu",
        "Registrar",
    ),
    DemoStaffSeed(
        HARVARD_TENANT_ID,
        "80000000-0000-7000-8000-000000000903",
        "Elena Torres",
        "elena.torres@harvard.example.edu",
        "Student Life",
    ),
)

_DEMO_WORK_ITEMS = (
    DemoWorkItemSeed(
        ASTER_TENANT_ID,
        "10000000-0000-7000-8000-000000000911",
        "AEN-201",
        "10000000-0000-7000-8000-000000000101",
        "00000000-0000-7000-8000-000000000902",
        "Resolve Maya's identity-document blocker",
        "Confirm the accepted identity-document path and unblock Maya's transcript tasks.",
        "todo",
        "high",
        "Registrar",
        datetime(2027, 7, 28, 17, 0, tzinfo=UTC),
    ),
    DemoWorkItemSeed(
        ASTER_TENANT_ID,
        "11000000-0000-7000-8000-000000000911",
        "AEN-202",
        "11000000-0000-7000-8000-000000000101",
        "00000000-0000-7000-8000-000000000903",
        "Confirm Jordan's orientation registration",
        "Jordan is nearly enrollment-ready and needs a final orientation selection.",
        "in_progress",
        "medium",
        "Student Life",
        datetime(2027, 8, 2, 17, 0, tzinfo=UTC),
    ),
    DemoWorkItemSeed(
        HARVARD_TENANT_ID,
        "80000000-0000-7000-8000-000000000911",
        "HEN-301",
        HARVARD_STUDENT_ID,
        HARVARD_STAFF_ID,
        "Welcome Alex to Harvard enrollment",
        "Review the active offer and be ready to answer enrollment questions.",
        "todo",
        "high",
        "Admissions",
        datetime(2027, 7, 27, 17, 0, tzinfo=UTC),
    ),
    DemoWorkItemSeed(
        HARVARD_TENANT_ID,
        "81000000-0000-7000-8000-000000000911",
        "HEN-302",
        "81000000-0000-7000-8000-000000000101",
        "80000000-0000-7000-8000-000000000902",
        "Review Maya's enrollment documents",
        "Maya completed profile verification and can now submit the remaining documents.",
        "todo",
        "urgent",
        "Registrar",
        datetime(2027, 7, 26, 17, 0, tzinfo=UTC),
    ),
    DemoWorkItemSeed(
        HARVARD_TENANT_ID,
        "82000000-0000-7000-8000-000000000911",
        "HEN-303",
        "82000000-0000-7000-8000-000000000101",
        "80000000-0000-7000-8000-000000000903",
        "Confirm Jordan's campus arrival plan",
        "Jordan is nearly ready and needs a final arrival and orientation check-in.",
        "in_progress",
        "medium",
        "Student Life",
        datetime(2027, 8, 2, 17, 0, tzinfo=UTC),
    ),
)


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    name: str
    data_type: str
    udt_name: str


@dataclass(frozen=True, slots=True)
class RelationalSeedReport:
    tables: int
    rows: int


async def seed_relational_data(
    engine: AsyncEngine,
    *,
    environment: str,
) -> RelationalSeedReport:
    """Converge deterministic demo rows inside one locked transaction."""

    assert_seed_environment(environment)
    fixture = load_demo_fixture()
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": _SEED_LOCK_NAME},
        )
        columns, primary_keys, dependencies = await _load_schema(connection, fixture)
        for table in _foreign_key_order(fixture, dependencies):
            await _upsert_table(
                connection,
                table,
                columns[table.name],
                primary_keys[table.name],
            )
        await _ensure_demo_seed_supplements(connection, completed_onboarding=False)
    await provision_demo_managed_configurations(engine)
    return RelationalSeedReport(
        tables=len(fixture),
        rows=sum(len(table.rows) for table in fixture),
    )


async def reset_relational_data(
    engine: AsyncEngine,
    *,
    environment: str,
    completed_onboarding: bool = False,
    preserve_managed_configurations: bool = True,
) -> RelationalSeedReport:
    """Atomically restore the complete deterministic browser fixture.

    This intentionally destructive operation is guarded by the same strict
    development/test environment check as the CLI seeder and is only exposed
    through the explicitly named guided-demo endpoint.
    """

    assert_seed_environment(environment)
    fixture = load_demo_fixture()
    preserved_configurations: list[dict[str, object]] = []
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": _SEED_LOCK_NAME},
        )
        columns, primary_keys, dependencies = await _load_schema(connection, fixture)
        if preserve_managed_configurations:
            existing_configurations = await connection.execute(
                text(
                    """
                    SELECT tenant_id, kind, document, created_by
                    FROM staff_managed_configuration_version
                    WHERE active=true
                    ORDER BY tenant_id, kind
                    """
                )
            )
            preserved_configurations = [
                dict(row) for row in existing_configurations.mappings().all()
            ]
        reset_tables = await connection.execute(
            text(
                """
                SELECT tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname='public' AND tablename<>'vv_schema_migration'
                ORDER BY tablename
                """
            )
        )
        preserved_tables = (
            _RESET_PRESERVED_TABLES
            if preserve_managed_configurations
            else _RESET_PRESERVED_TABLES - {"staff_managed_configuration_version"}
        )
        table_names = [
            str(row["tablename"])
            for row in reset_tables.mappings().all()
            if str(row["tablename"]) not in preserved_tables
        ]
        if not table_names or any(not _IDENTIFIER.fullmatch(name) for name in table_names):
            raise SeedFixtureError("The development reset found an unsafe table inventory")
        table_list = ", ".join(f'public."{name}"' for name in table_names)
        await connection.execute(text(f"TRUNCATE TABLE {table_list}"))
        for table in _foreign_key_order(fixture, dependencies):
            await _upsert_table(
                connection,
                table,
                columns[table.name],
                primary_keys[table.name],
            )
        await _ensure_demo_seed_supplements(
            connection,
            completed_onboarding=completed_onboarding,
        )
    repository = PostgresManagedConfigurationRepository(engine)
    for configuration in preserved_configurations:
        tenant_id = str(configuration["tenant_id"])
        tenant_slug = "harvard" if tenant_id == HARVARD_TENANT_ID else "aster"
        await repository.rematerialize_active(
            AuthContext(
                tenant_id=tenant_id,
                student_id=(
                    HARVARD_STUDENT_ID if tenant_id == HARVARD_TENANT_ID else ASTER_STUDENT_ID
                ),
                actor_id=str(configuration["created_by"]),
                actor_type="staff",
                tenant_slug=tenant_slug,
            ),
            str(configuration["kind"]),
        )
    return RelationalSeedReport(
        tables=len(fixture),
        rows=sum(len(table.rows) for table in fixture),
    )


async def ensure_harvard_demo_student(
    engine: AsyncEngine,
    *,
    environment: str,
) -> None:
    """Ensure the secondary tenant has an isolated demo identity for browser sessions."""

    assert_seed_environment(environment)
    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": f"{_SEED_LOCK_NAME}.harvard"},
        )
        await _ensure_tenant_workflow(
            connection,
            tenant_id=HARVARD_TENANT_ID,
            namespace="80000000",
        )
        await _ensure_harvard_ai_runtime(connection)
        await _ensure_harvard_demo_student(connection, False)
        for scenario in _EXTRA_STUDENTS:
            if scenario.tenant_id == HARVARD_TENANT_ID:
                await _ensure_demo_student_scenario(connection, scenario)
        await _ensure_demo_staff_and_work(connection, tenant_id=HARVARD_TENANT_ID)
        await _ensure_default_action_rules(connection, (HARVARD_TENANT_ID,))


async def provision_demo_managed_configurations(
    engine: AsyncEngine,
    *,
    tenant_slugs: tuple[str, ...] = ("aster", "harvard"),
    configuration_root: Path | None = None,
) -> None:
    """Import packaged demo documents once through the canonical publication path."""

    root = _managed_configuration_root(configuration_root)
    repository = PostgresManagedConfigurationRepository(engine)
    identities = {
        "aster": (ASTER_TENANT_ID, "00000000-0000-7000-8000-000000000901"),
        "harvard": (HARVARD_TENANT_ID, HARVARD_STAFF_ID),
    }
    for tenant_slug in tenant_slugs:
        tenant_id, staff_id = identities[tenant_slug]
        auth = AuthContext(
            tenant_id=tenant_id,
            student_id=(ASTER_STUDENT_ID if tenant_slug == "aster" else HARVARD_STUDENT_ID),
            actor_id=staff_id,
            actor_type="staff",
            tenant_slug=tenant_slug,
        )
        for kind, file_name in _MANAGED_CONFIGURATION_FILES.items():
            source = root / tenant_slug / file_name
            try:
                raw_document = yaml.safe_load(
                    await asyncio.to_thread(source.read_text, encoding="utf-8")
                )
            except (OSError, yaml.YAMLError) as error:
                raise SeedFixtureError(
                    f"The managed configuration seed could not read {source}"
                ) from error
            if not isinstance(raw_document, dict):
                raise SeedFixtureError(f"The managed configuration seed is invalid: {source}")
            await repository.provision_if_missing(
                auth,
                kind,
                raw_document,
                f"demo-managed-config:{tenant_slug}:{kind}",
                rematerialize_existing=True,
            )


def _demo_uuid(namespace: str, suffix: str) -> str:
    return f"{namespace}-0000-7000-8000-{suffix}"


def _seed_interaction_type(submission_type: str) -> str:
    return {
        "none": "information",
        "document": "upload_file",
        "payment": "payment",
        "appointment": "scheduling",
    }.get(submission_type, "form")


async def _ensure_demo_seed_supplements(
    connection: AsyncConnection,
    *,
    completed_onboarding: bool,
) -> None:
    await _ensure_harvard_reference_rows(connection)
    await _ensure_tenant_workflow(
        connection,
        tenant_id=ASTER_TENANT_ID,
        namespace="00000000",
    )
    await _ensure_tenant_workflow(
        connection,
        tenant_id=HARVARD_TENANT_ID,
        namespace="80000000",
    )
    await _ensure_harvard_ai_runtime(connection)
    await _ensure_harvard_demo_student(connection, completed_onboarding)

    if completed_onboarding:
        await _complete_primary_demo_student(
            connection,
            tenant_id=ASTER_TENANT_ID,
            student_id=ASTER_STUDENT_ID,
            offer_id="00000000-0000-7000-8000-000000000201",
            journey_id="00000000-0000-7000-8000-000000000501",
            requirement_namespace="00000000",
        )
    else:
        await _ensure_student_journey_for_accepted_offer(
            connection,
            tenant_id=ASTER_TENANT_ID,
            student_id=ASTER_STUDENT_ID,
            offer_id="00000000-0000-7000-8000-000000000201",
            journey_id="00000000-0000-7000-8000-000000000501",
            requirement_namespace="00000000",
            requirement_states=_PRIMARY_REQUIREMENT_STATES,
        )

    for scenario in _EXTRA_STUDENTS:
        await _ensure_demo_student_scenario(connection, scenario)
    await _ensure_demo_staff_and_work(connection)
    await _ensure_default_action_rules(
        connection,
        (ASTER_TENANT_ID, HARVARD_TENANT_ID),
    )


async def _ensure_default_action_rules(
    connection: AsyncConnection,
    tenant_ids: tuple[str, ...],
) -> None:
    """Seed fresh tenants without overwriting staff-customized rule settings."""

    for tenant_id in tenant_ids:
        await connection.execute(
            text(
                """
                INSERT INTO staff_action_rule (
                  id, tenant_id, code, name, description, enabled, signal_type,
                  flow_kind, requirement_code, lookahead_days, cadence_minutes,
                  component, priority, action_type, title_template,
                  description_template, version
                ) VALUES (
                  :id, :tenant_id, 'transcript-due-soon', 'Transcript due soon',
                  'Create staff work when an incomplete official transcript is due soon.',
                  true, 'requirement_due', 'enrollment', 'official_transcript',
                  3, 60, 'Admissions', 'high', 'deadline_risk',
                  'Follow up: transcript due soon',
                  'The official transcript is incomplete and due within 3 days.', 1
                )
                ON CONFLICT (tenant_id, code) DO NOTHING
                """
            ),
            {
                "id": uuid5(
                    NAMESPACE_URL,
                    f"audentra:{tenant_id}:staff-action-rule:transcript-due-soon",
                ),
                "tenant_id": UUID(tenant_id),
            },
        )


async def _ensure_harvard_ai_runtime(connection: AsyncConnection) -> None:
    """Clone the published demo AI runtime into the isolated Harvard tenant."""

    tenant_parameters = {
        "source_tenant_id": UUID(ASTER_TENANT_ID),
        "target_tenant_id": UUID(HARVARD_TENANT_ID),
    }
    await connection.execute(
        text(
            """
            INSERT INTO ai_prompt_template_version (
              id, tenant_id, operation, version, name, system_prompt,
              user_prompt_template, template_variables, status, created_by,
              published_at, created_at
            )
            SELECT
              replace(id::text, '60000000-', '80000000-')::uuid,
              :target_tenant_id, operation, version, name, system_prompt,
              user_prompt_template, template_variables, status, NULL,
              published_at, created_at
            FROM ai_prompt_template_version
            WHERE tenant_id=:source_tenant_id
            ON CONFLICT (id) DO UPDATE SET
              name=EXCLUDED.name,
              system_prompt=EXCLUDED.system_prompt,
              user_prompt_template=EXCLUDED.user_prompt_template,
              template_variables=EXCLUDED.template_variables,
              status=EXCLUDED.status,
              published_at=EXCLUDED.published_at
            """
        ),
        tenant_parameters,
    )
    await connection.execute(
        text(
            """
            INSERT INTO ai_context_policy_version (
              id, tenant_id, operation, version, name, context_policy, status,
              created_by, published_at, created_at
            )
            SELECT
              replace(id::text, '61000000-', '81000000-')::uuid,
              :target_tenant_id, operation, version, name, context_policy, status,
              NULL, published_at, created_at
            FROM ai_context_policy_version
            WHERE tenant_id=:source_tenant_id
            ON CONFLICT (id) DO UPDATE SET
              name=EXCLUDED.name,
              context_policy=EXCLUDED.context_policy,
              status=EXCLUDED.status,
              published_at=EXCLUDED.published_at
            """
        ),
        tenant_parameters,
    )
    await connection.execute(
        text(
            """
            INSERT INTO ai_output_schema_version (
              id, tenant_id, operation, version, name, output_schema, status,
              created_by, published_at, created_at
            )
            SELECT
              replace(id::text, '62000000-', '82000000-')::uuid,
              :target_tenant_id, operation, version, name, output_schema, status,
              NULL, published_at, created_at
            FROM ai_output_schema_version
            WHERE tenant_id=:source_tenant_id
            ON CONFLICT (id) DO UPDATE SET
              name=EXCLUDED.name,
              output_schema=EXCLUDED.output_schema,
              status=EXCLUDED.status,
              published_at=EXCLUDED.published_at
            """
        ),
        tenant_parameters,
    )
    await connection.execute(
        text(
            """
            INSERT INTO ai_operation_config (
              tenant_id, operation, prompt_template_version_id,
              context_policy_version_id, output_schema_version_id, provider,
              model, max_output_tokens, temperature_milli, config_revision,
              updated_at
            )
            SELECT
              :target_tenant_id,
              operation,
              replace(prompt_template_version_id::text, '60000000-', '80000000-')::uuid,
              replace(context_policy_version_id::text, '61000000-', '81000000-')::uuid,
              CASE
                WHEN output_schema_version_id IS NULL THEN NULL
                ELSE replace(
                  output_schema_version_id::text,
                  '62000000-',
                  '82000000-'
                )::uuid
              END,
              provider,
              model,
              max_output_tokens,
              temperature_milli,
              config_revision,
              updated_at
            FROM ai_operation_config
            WHERE tenant_id=:source_tenant_id
            ON CONFLICT (tenant_id, operation) DO UPDATE SET
              prompt_template_version_id=EXCLUDED.prompt_template_version_id,
              context_policy_version_id=EXCLUDED.context_policy_version_id,
              output_schema_version_id=EXCLUDED.output_schema_version_id,
              provider=EXCLUDED.provider,
              model=EXCLUDED.model,
              max_output_tokens=EXCLUDED.max_output_tokens,
              temperature_milli=EXCLUDED.temperature_milli,
              config_revision=EXCLUDED.config_revision,
              updated_at=EXCLUDED.updated_at
            """
        ),
        tenant_parameters,
    )


async def _ensure_harvard_reference_rows(connection: AsyncConnection) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO campus (id, tenant_id, name)
            VALUES (:campus_id, :tenant_id, 'Cambridge Campus')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"campus_id": UUID(HARVARD_CAMPUS_ID), "tenant_id": UUID(HARVARD_TENANT_ID)},
    )
    await connection.execute(
        text(
            """
            INSERT INTO academic_term (id, tenant_id, name, starts_on)
            VALUES (:term_id, :tenant_id, 'Fall 2027', DATE '2027-09-01')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"term_id": UUID(HARVARD_TERM_ID), "tenant_id": UUID(HARVARD_TENANT_ID)},
    )


async def _ensure_tenant_workflow(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    namespace: str,
) -> None:
    journey_definition_id = _demo_uuid(namespace, "000000000301")
    await connection.execute(
        text(
            """
            INSERT INTO journey_definition_version (
              id, tenant_id, code, version, active, onboarding_required
            ) VALUES (
              :id, :tenant_id, 'standard_undergraduate_enrollment', 1, 1, true
            )
            ON CONFLICT (id) DO UPDATE SET onboarding_required=true
            """
        ),
        {"id": UUID(journey_definition_id), "tenant_id": UUID(tenant_id)},
    )
    for definition in _CANONICAL_REQUIREMENTS:
        definition_id = _demo_uuid(namespace, definition.definition_suffix)
        await connection.execute(
            text(
                """
                INSERT INTO requirement_definition_version (
                  id, tenant_id, code, title, description, blocking,
                  display_order, depends_on_codes, due_offset_days, version,
                  submission_type, responsible_office, flow_kind,
                  interaction_type, input_config
                ) VALUES (
                  :id, :tenant_id, :code, :title, :description, :blocking,
                  :display_order, CAST(:depends_on_codes AS text[]),
                  :due_offset_days, 1, :submission_type, :responsible_office,
                  'enrollment', :interaction_type, '{}'::jsonb
                )
                ON CONFLICT (id) DO UPDATE SET
                  flow_kind=EXCLUDED.flow_kind,
                  interaction_type=EXCLUDED.interaction_type,
                  input_config=EXCLUDED.input_config
                """
            ),
            {
                "id": UUID(definition_id),
                "tenant_id": UUID(tenant_id),
                "code": definition.code,
                "title": definition.title,
                "description": definition.description,
                "blocking": int(definition.blocking),
                "display_order": definition.display_order,
                "depends_on_codes": list(definition.depends_on_codes),
                "due_offset_days": definition.due_offset_days,
                "submission_type": definition.submission_type,
                "interaction_type": _seed_interaction_type(definition.submission_type),
                "responsible_office": definition.responsible_office,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO journey_requirement_definition (
                  journey_definition_version_id, requirement_definition_version_id
                ) VALUES (:journey_definition_id, :requirement_definition_id)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "journey_definition_id": UUID(journey_definition_id),
                "requirement_definition_id": UUID(definition_id),
            },
        )


async def _ensure_harvard_demo_student(
    connection: AsyncConnection,
    completed_onboarding: bool,
) -> None:
    await _ensure_harvard_reference_rows(connection)
    accepted_at = datetime(2026, 7, 24, 12, 0, tzinfo=UTC) if completed_onboarding else None
    scenario = DemoStudentScenario(
        namespace="80000000",
        tenant_id=HARVARD_TENANT_ID,
        program_id="80000000-0000-7000-8000-000000000101",
        campus_id=HARVARD_CAMPUS_ID,
        term_id=HARVARD_TERM_ID,
        first_name="Alex",
        last_name="Morgan",
        preferred_name="Alex",
        class_year=2027,
        offer_status="accepted" if completed_onboarding else "offered",
        onboarding_status="completed" if completed_onboarding else "in_progress",
        current_step="deposit" if completed_onboarding else "offer",
        completed_steps=_ONBOARDING_STEPS if completed_onboarding else (),
        onboarding_payload=_onboarding_payload(
            first_name="Alex",
            last_name="Morgan",
            completed=completed_onboarding,
            housing_preference="on_campus" if completed_onboarding else "undecided",
            skipped_steps=("deposit",) if completed_onboarding else (),
        ),
        accepted_at=accepted_at,
        requirement_states=_PRIMARY_REQUIREMENT_STATES,
    )
    await _ensure_demo_student_scenario(connection, scenario)
    if completed_onboarding:
        await _complete_primary_demo_student(
            connection,
            tenant_id=HARVARD_TENANT_ID,
            student_id=HARVARD_STUDENT_ID,
            offer_id=HARVARD_OFFER_ID,
            journey_id="80000000-0000-7000-8000-000000000501",
            requirement_namespace="80000000",
        )


async def _ensure_demo_student_scenario(
    connection: AsyncConnection,
    scenario: DemoStudentScenario,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO person (id, tenant_id, preferred_name, first_name, last_name)
            VALUES (:id, :tenant_id, :preferred_name, :first_name, :last_name)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": UUID(scenario.person_id),
            "tenant_id": UUID(scenario.tenant_id),
            "preferred_name": scenario.preferred_name,
            "first_name": scenario.first_name,
            "last_name": scenario.last_name,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO student (id, tenant_id, person_id, class_year)
            VALUES (:id, :tenant_id, :person_id, :class_year)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": UUID(scenario.student_id),
            "tenant_id": UUID(scenario.tenant_id),
            "person_id": UUID(scenario.person_id),
            "class_year": scenario.class_year,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO admission_offer (
              id, tenant_id, student_id, program_id, academic_term_id, campus_id,
              response_deadline, deposit_amount_cents, status, accepted_at, version
            ) VALUES (
              :id, :tenant_id, :student_id, :program_id, :term_id, :campus_id,
              DATE '2027-08-15', :deposit_amount_cents, :status, :accepted_at, :version
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": UUID(scenario.offer_id),
            "tenant_id": UUID(scenario.tenant_id),
            "student_id": UUID(scenario.student_id),
            "program_id": UUID(scenario.program_id),
            "term_id": UUID(scenario.term_id),
            "campus_id": UUID(scenario.campus_id),
            "deposit_amount_cents": (85000 if scenario.tenant_id == HARVARD_TENANT_ID else 50000),
            "status": scenario.offer_status,
            "accepted_at": scenario.accepted_at,
            "version": 2 if scenario.offer_status == "accepted" else 1,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO student_onboarding (
              tenant_id, student_id, status, current_step, completed_steps,
              payload, version, completed_at
            ) VALUES (
              :tenant_id, :student_id, :status, :current_step,
              CAST(:completed_steps AS text[]), CAST(:payload AS jsonb),
              :version, :completed_at
            )
            ON CONFLICT (tenant_id, student_id) DO NOTHING
            """
        ),
        {
            "tenant_id": UUID(scenario.tenant_id),
            "student_id": UUID(scenario.student_id),
            "status": scenario.onboarding_status,
            "current_step": scenario.current_step,
            "completed_steps": list(scenario.completed_steps),
            "payload": json.dumps(scenario.onboarding_payload),
            "version": len(scenario.completed_steps) + 1,
            "completed_at": (
                scenario.accepted_at if scenario.onboarding_status == "completed" else None
            ),
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO student_profile (
              tenant_id, student_id, preferred_name, communication_preference, version
            ) VALUES (:tenant_id, :student_id, :preferred_name, 'email', 1)
            ON CONFLICT (tenant_id, student_id) DO NOTHING
            """
        ),
        {
            "tenant_id": UUID(scenario.tenant_id),
            "student_id": UUID(scenario.student_id),
            "preferred_name": scenario.preferred_name,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO student_portal_projection (
              tenant_id, student_id, projection_version, dashboard, source_updated_at
            ) VALUES (:tenant_id, :student_id, :version, '{}'::jsonb, NOW())
            ON CONFLICT (tenant_id, student_id) DO NOTHING
            """
        ),
        {
            "tenant_id": UUID(scenario.tenant_id),
            "student_id": UUID(scenario.student_id),
            "version": 2 if scenario.offer_status == "accepted" else 1,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO student_financial_summary (
              tenant_id, student_id, academic_year, cost_of_attendance_cents,
              external_payments_cents, version
            ) VALUES (
              :tenant_id, :student_id, '2027-2028', :cost, 0, 1
            )
            ON CONFLICT (tenant_id, student_id, academic_year) DO NOTHING
            """
        ),
        {
            "tenant_id": UUID(scenario.tenant_id),
            "student_id": UUID(scenario.student_id),
            "cost": 8500000 if scenario.tenant_id == HARVARD_TENANT_ID else 6500000,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO student_sap_status (
              tenant_id, student_id, academic_year, status, cumulative_gpa,
              minimum_gpa, completion_rate_percent,
              minimum_completion_rate_percent, attempted_credits,
              maximum_attempted_credits, version
            ) VALUES (
              :tenant_id, :student_id, '2027-2028', 'meeting', 0, 2,
              100, 67, 0, 180, 1
            )
            ON CONFLICT (tenant_id, student_id, academic_year) DO NOTHING
            """
        ),
        {"tenant_id": UUID(scenario.tenant_id), "student_id": UUID(scenario.student_id)},
    )
    await connection.execute(
        text(
            """
            INSERT INTO student_message (
              id, tenant_id, student_id, subject, body, sender_name, sent_at
            ) VALUES (
              :id, :tenant_id, :student_id, :subject, :body,
              'Enrollment Services', :sent_at
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": UUID(_demo_uuid(scenario.namespace, "000000000701")),
            "tenant_id": UUID(scenario.tenant_id),
            "student_id": UUID(scenario.student_id),
            "subject": f"Welcome, {scenario.preferred_name}",
            "body": "Your enrollment portal contains your current checklist and campus updates.",
            "sent_at": datetime(2026, 7, 24, 9, 0, tzinfo=UTC),
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO student_appointment (
              id, tenant_id, student_id, type, starts_at, notes, status
            ) VALUES (
              :id, :tenant_id, :student_id, 'enrollment_support',
              :starts_at, 'Demo enrollment planning appointment', 'scheduled'
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": UUID(_demo_uuid(scenario.namespace, "000000000702")),
            "tenant_id": UUID(scenario.tenant_id),
            "student_id": UUID(scenario.student_id),
            "starts_at": datetime(2027, 8, 5, 14, 0, tzinfo=UTC),
        },
    )
    await _ensure_student_journey_for_accepted_offer(
        connection,
        tenant_id=scenario.tenant_id,
        student_id=scenario.student_id,
        offer_id=scenario.offer_id,
        journey_id=scenario.journey_id,
        requirement_namespace=scenario.namespace,
        requirement_states=scenario.requirement_states,
    )


async def _complete_primary_demo_student(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    student_id: str,
    offer_id: str,
    journey_id: str,
    requirement_namespace: str,
) -> None:
    accepted_at = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    await connection.execute(
        text(
            """
            UPDATE admission_offer
            SET status='accepted', accepted_at=:accepted_at, version=2,
                updated_at=:accepted_at
            WHERE tenant_id=:tenant_id AND student_id=:student_id
              AND id=:offer_id
            """
        ),
        {
            "accepted_at": accepted_at,
            "tenant_id": UUID(tenant_id),
            "student_id": UUID(student_id),
            "offer_id": UUID(offer_id),
        },
    )
    await connection.execute(
        text(
            """
            UPDATE student_onboarding
            SET status='completed', current_step='deposit',
                completed_steps=CAST(:completed_steps AS text[]),
                payload=CAST(:payload AS jsonb), version=10,
                completed_at=:accepted_at, updated_at=:accepted_at
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            """
        ),
        {
            "completed_steps": list(_ONBOARDING_STEPS),
            "payload": json.dumps(
                _onboarding_payload(
                    first_name="Alex",
                    last_name="Morgan",
                    completed=True,
                    housing_preference="on_campus",
                    skipped_steps=("deposit",),
                )
            ),
            "accepted_at": accepted_at,
            "tenant_id": UUID(tenant_id),
            "student_id": UUID(student_id),
        },
    )
    await connection.execute(
        text(
            """
            UPDATE student_portal_projection
            SET projection_version=GREATEST(projection_version, 2),
                source_updated_at=:accepted_at, projected_at=:accepted_at
            WHERE tenant_id=:tenant_id AND student_id=:student_id
            """
        ),
        {
            "accepted_at": accepted_at,
            "tenant_id": UUID(tenant_id),
            "student_id": UUID(student_id),
        },
    )
    await _ensure_student_journey_for_accepted_offer(
        connection,
        tenant_id=tenant_id,
        student_id=student_id,
        offer_id=offer_id,
        journey_id=journey_id,
        requirement_namespace=requirement_namespace,
        requirement_states=_PRIMARY_REQUIREMENT_STATES,
    )


async def _ensure_student_journey_for_accepted_offer(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    student_id: str,
    offer_id: str,
    journey_id: str,
    requirement_namespace: str,
    requirement_states: Mapping[str, tuple[str, int]],
) -> None:
    offer_result = await connection.execute(
        text(
            """
            SELECT status, accepted_at FROM admission_offer
            WHERE tenant_id=:tenant_id AND student_id=:student_id AND id=:offer_id
            """
        ),
        {
            "tenant_id": UUID(tenant_id),
            "student_id": UUID(student_id),
            "offer_id": UUID(offer_id),
        },
    )
    offer = offer_result.mappings().first()
    if offer is None or offer["status"] != "accepted" or offer["accepted_at"] is None:
        return
    accepted_at = offer["accepted_at"]

    journey_result = await connection.execute(
        text(
            """
            SELECT id, journey_definition_version_id FROM enrollment_journey
            WHERE tenant_id=:tenant_id AND offer_id=:offer_id
            """
        ),
        {"tenant_id": UUID(tenant_id), "offer_id": UUID(offer_id)},
    )
    journey = journey_result.mappings().first()
    if journey is None:
        definition_result = await connection.execute(
            text(
                """
                SELECT id FROM journey_definition_version
                WHERE tenant_id=:tenant_id AND active=1
                ORDER BY version DESC, id LIMIT 1
                """
            ),
            {"tenant_id": UUID(tenant_id)},
        )
        definition = definition_result.mappings().first()
        if definition is None:
            raise SeedFixtureError(
                f"Demo tenant {tenant_id} has no active enrollment journey definition"
            )
        definition_id = str(definition["id"])
        await connection.execute(
            text(
                """
                INSERT INTO enrollment_journey (
                  id, tenant_id, student_id, offer_id, journey_definition_version_id,
                  status, version, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :student_id, :offer_id, :definition_id,
                  'in_progress', 1, :accepted_at, :accepted_at
                )
                ON CONFLICT (tenant_id, offer_id) DO NOTHING
                """
            ),
            {
                "id": UUID(journey_id),
                "tenant_id": UUID(tenant_id),
                "student_id": UUID(student_id),
                "offer_id": UUID(offer_id),
                "definition_id": UUID(definition_id),
                "accepted_at": accepted_at,
            },
        )
        journey_result = await connection.execute(
            text(
                """
                SELECT id, journey_definition_version_id FROM enrollment_journey
                WHERE tenant_id=:tenant_id AND offer_id=:offer_id
                """
            ),
            {"tenant_id": UUID(tenant_id), "offer_id": UUID(offer_id)},
        )
        journey = journey_result.mappings().one()

    actual_journey_id = str(journey["id"])
    definition_id = str(journey["journey_definition_version_id"])
    definitions_result = await connection.execute(
        text(
            """
            SELECT definition.id, definition.code, definition.depends_on_codes,
                   definition.due_offset_days
            FROM journey_requirement_definition link
            JOIN requirement_definition_version definition
              ON definition.id=link.requirement_definition_version_id
             AND definition.tenant_id=:tenant_id
            WHERE link.journey_definition_version_id=:definition_id
            ORDER BY definition.display_order, definition.id
            """
        ),
        {"tenant_id": UUID(tenant_id), "definition_id": UUID(definition_id)},
    )
    canonical = {item.code: item for item in _CANONICAL_REQUIREMENTS}
    for definition in definitions_result.mappings().all():
        code = str(definition["code"])
        known = canonical.get(code)
        requirement_id = (
            _demo_uuid(requirement_namespace, known.instance_suffix)
            if known is not None
            else str(
                uuid5(
                    NAMESPACE_URL,
                    f"audentra-demo:{tenant_id}:{student_id}:{definition_id}:{code}",
                )
            )
        )
        dependencies = tuple(str(item) for item in (definition["depends_on_codes"] or ()))
        status, progress = requirement_states.get(
            code,
            ("blocked", 0) if dependencies else ("ready", 0),
        )
        due_offset = definition["due_offset_days"]
        due_at = None if due_offset is None else accepted_at + timedelta(days=int(due_offset))
        parameters = {
            "id": UUID(requirement_id),
            "tenant_id": UUID(tenant_id),
            "journey_id": UUID(actual_journey_id),
            "definition_id": definition["id"],
            "status": status,
            "due_at": due_at,
            "progress": progress,
            "accepted_at": accepted_at,
        }
        existing_result = await connection.execute(
            text(
                """
                SELECT id
                FROM student_requirement
                WHERE tenant_id=:tenant_id
                  AND journey_id=:journey_id
                  AND requirement_definition_version_id=:definition_id
                """
            ),
            parameters,
        )
        existing_requirement = existing_result.first()
        if existing_requirement is not None:
            await connection.execute(
                text(
                    """
                    UPDATE student_requirement SET
                      status=:status,
                      due_at=:due_at,
                      progress_percent=:progress,
                      version=version + 1,
                      updated_at=:accepted_at
                    WHERE id=:existing_id
                      AND (status, due_at, progress_percent) IS DISTINCT FROM
                          (:status, :due_at, :progress)
                    """
                ),
                {**parameters, "existing_id": existing_requirement[0]},
            )
            continue
        await connection.execute(
            text(
                """
                INSERT INTO student_requirement (
                  id, tenant_id, journey_id, requirement_definition_version_id,
                  status, due_at, progress_percent, version, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :journey_id, :definition_id,
                  :status, :due_at, :progress, 1, :accepted_at, :accepted_at
                )
                ON CONFLICT (id) DO UPDATE SET
                  tenant_id=EXCLUDED.tenant_id,
                  journey_id=EXCLUDED.journey_id,
                  requirement_definition_version_id=
                    EXCLUDED.requirement_definition_version_id,
                  status=EXCLUDED.status,
                  due_at=EXCLUDED.due_at,
                  progress_percent=EXCLUDED.progress_percent,
                  version=student_requirement.version + 1,
                  updated_at=EXCLUDED.updated_at
                WHERE (
                  student_requirement.tenant_id,
                  student_requirement.journey_id,
                  student_requirement.requirement_definition_version_id,
                  student_requirement.status,
                  student_requirement.due_at,
                  student_requirement.progress_percent
                ) IS DISTINCT FROM (
                  EXCLUDED.tenant_id,
                  EXCLUDED.journey_id,
                  EXCLUDED.requirement_definition_version_id,
                  EXCLUDED.status,
                  EXCLUDED.due_at,
                  EXCLUDED.progress_percent
                )
                """
            ),
            parameters,
        )


async def _ensure_demo_staff_and_work(
    connection: AsyncConnection,
    *,
    tenant_id: str | None = None,
) -> None:
    for staff in _DEMO_STAFF:
        if tenant_id is not None and staff.tenant_id != tenant_id:
            continue
        await connection.execute(
            text(
                """
                INSERT INTO staff_member (
                  id, tenant_id, display_name, email_normalized, component, active
                ) VALUES (
                  :id, :tenant_id, :display_name, :email, :component, true
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": UUID(staff.staff_id),
                "tenant_id": UUID(staff.tenant_id),
                "display_name": staff.display_name,
                "email": staff.email,
                "component": staff.component,
            },
        )
    for item in _DEMO_WORK_ITEMS:
        if tenant_id is not None and item.tenant_id != tenant_id:
            continue
        await connection.execute(
            text(
                """
                INSERT INTO staff_work_item (
                  id, tenant_id, student_id, key, title, description, status,
                  priority, work_type, component, due_at, escalated, assignee_id,
                  source_type, source_id, version
                ) VALUES (
                  :id, :tenant_id, :student_id, :key, :title, :description, :status,
                  :priority, 'enrollment', :component, :due_at, false, :assignee_id,
                  'onboarding', :student_id, 1
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": UUID(item.item_id),
                "tenant_id": UUID(item.tenant_id),
                "student_id": UUID(item.student_id),
                "key": item.key,
                "title": item.title,
                "description": item.description,
                "status": item.status,
                "priority": item.priority,
                "component": item.component,
                "due_at": item.due_at,
                "assignee_id": UUID(item.assignee_id),
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
                  'Enrollment workflow', 'created',
                  'Created from the deterministic enrollment demo scenario.',
                  :occurred_at
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": UUID(_demo_uuid(item.item_id[:8], "000000000921")),
                "tenant_id": UUID(item.tenant_id),
                "work_item_id": UUID(item.item_id),
                "occurred_at": datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
            },
        )


async def _load_schema(
    connection: AsyncConnection,
    fixture: tuple[FixtureTable, ...],
) -> tuple[
    dict[str, dict[str, ColumnSpec]],
    dict[str, tuple[str, ...]],
    dict[str, frozenset[str]],
]:
    names = tuple(table.name for table in fixture)
    if not names or any(not _IDENTIFIER.fullmatch(name) for name in names):
        raise SeedFixtureError("The demo fixture contains an unsafe table identifier")
    in_list = ", ".join(f"'{name}'" for name in names)
    column_result = await connection.execute(
        text(
            f"""
            SELECT table_name, column_name, data_type, udt_name, is_generated
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name IN ({in_list})
            ORDER BY table_name, ordinal_position
            """
        )
    )
    columns: dict[str, dict[str, ColumnSpec]] = {name: {} for name in names}
    for row in column_result.mappings():
        table_name = str(row["table_name"])
        column_name = str(row["column_name"])
        if row["is_generated"] != "NEVER":
            continue
        columns[table_name][column_name] = ColumnSpec(
            name=column_name,
            data_type=str(row["data_type"]),
            udt_name=str(row["udt_name"]),
        )

    key_result = await connection.execute(
        text(
            f"""
            SELECT kcu.table_name, kcu.column_name, kcu.ordinal_position
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_catalog = tc.constraint_catalog
             AND kcu.constraint_schema = tc.constraint_schema
             AND kcu.constraint_name = tc.constraint_name
            WHERE tc.table_schema = 'public'
              AND tc.constraint_type = 'PRIMARY KEY'
              AND kcu.table_name IN ({in_list})
            ORDER BY kcu.table_name, kcu.ordinal_position
            """
        )
    )
    keys: dict[str, list[str]] = {name: [] for name in names}
    for row in key_result.mappings():
        keys[str(row["table_name"])].append(str(row["column_name"]))

    dependency_result = await connection.execute(
        text(
            f"""
            SELECT DISTINCT tc.table_name, ccu.table_name AS foreign_table_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_catalog = tc.constraint_catalog
             AND ccu.constraint_schema = tc.constraint_schema
             AND ccu.constraint_name = tc.constraint_name
            WHERE tc.table_schema = 'public'
              AND tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name IN ({in_list})
            """
        )
    )
    dependencies: dict[str, set[str]] = {name: set() for name in names}
    fixture_names = set(names)
    for row in dependency_result.mappings():
        table_name = str(row["table_name"])
        foreign_table = str(row["foreign_table_name"])
        if foreign_table in fixture_names and foreign_table != table_name:
            dependencies[table_name].add(foreign_table)

    missing_tables = [name for name in names if not columns[name]]
    missing_keys = [name for name in names if not keys[name]]
    if missing_tables or missing_keys:
        details = ", ".join((*missing_tables, *missing_keys))
        raise SeedFixtureError(
            f"Seed schema is missing required migrated tables or keys: {details}"
        )
    return (
        columns,
        {name: tuple(value) for name, value in keys.items()},
        {name: frozenset(value) for name, value in dependencies.items()},
    )


def _foreign_key_order(
    fixture: tuple[FixtureTable, ...],
    dependencies: dict[str, frozenset[str]],
) -> tuple[FixtureTable, ...]:
    remaining = list(fixture)
    completed: set[str] = set()
    ordered: list[FixtureTable] = []
    while remaining:
        ready = [table for table in remaining if dependencies[table.name] <= completed]
        if not ready:
            names = ", ".join(table.name for table in remaining)
            raise SeedFixtureError(f"Seed tables contain an unresolved foreign-key cycle: {names}")
        for table in ready:
            ordered.append(table)
            completed.add(table.name)
            remaining.remove(table)
    return tuple(ordered)


async def _upsert_table(
    connection: AsyncConnection,
    table: FixtureTable,
    schema_columns: dict[str, ColumnSpec],
    primary_key: tuple[str, ...],
) -> None:
    if not table.rows:
        return
    names = tuple(table.rows[0])
    expected = set(names)
    if (
        not names
        or any(set(row) != expected for row in table.rows)
        or any(not _IDENTIFIER.fullmatch(name) for name in names)
    ):
        raise SeedFixtureError(f"Demo fixture table {table.name!r} has inconsistent columns")
    missing_columns = sorted(expected.difference(schema_columns))
    if missing_columns:
        raise SeedFixtureError(
            f"Seed table {table.name!r} requires missing columns: {', '.join(missing_columns)}"
        )
    if not set(primary_key).issubset(expected):
        raise SeedFixtureError(f"Seed table {table.name!r} does not contain its primary key")

    statement, bind_names = _upsert_statement(table.name, names, schema_columns, primary_key)
    parameters = [
        {bind_names[name]: _convert_value(row[name], schema_columns[name]) for name in names}
        for row in table.rows
    ]
    await connection.execute(text(statement), parameters)


def _upsert_statement(
    table: str,
    names: tuple[str, ...],
    columns: dict[str, ColumnSpec],
    primary_key: tuple[str, ...],
) -> tuple[str, dict[str, str]]:
    if not _IDENTIFIER.fullmatch(table):
        raise SeedFixtureError("Unsafe seed table identifier")
    bind_names = {name: f"value_{index}" for index, name in enumerate(names)}
    quoted_columns = ", ".join(f'"{name}"' for name in names)
    values = ", ".join(
        (
            f"CAST(:{bind_names[name]} AS {columns[name].udt_name})"
            if columns[name].data_type in {"json", "jsonb"}
            else f":{bind_names[name]}"
        )
        for name in names
    )
    conflict = ", ".join(f'"{name}"' for name in primary_key)
    return (
        f'INSERT INTO public."{table}" ({quoted_columns}) VALUES ({values}) '
        f"ON CONFLICT ({conflict}) DO NOTHING",
        bind_names,
    )


def _convert_value(value: object, column: ColumnSpec) -> Any:
    if value is None:
        return None
    if column.data_type in {"json", "jsonb"}:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if column.data_type == "uuid":
        return UUID(str(value))
    if column.data_type == "timestamp with time zone":
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if column.data_type == "timestamp without time zone":
        return datetime.fromisoformat(str(value))
    if column.data_type == "date":
        return date.fromisoformat(str(value))
    if column.data_type in {"numeric", "decimal"}:
        return Decimal(str(value))
    if column.data_type == "bytea":
        if not isinstance(value, dict) or not isinstance(value.get("$bytes"), str):
            raise SeedFixtureError(f"Seed bytea value for {column.name!r} is invalid")
        return base64.b64decode(value["$bytes"], validate=True)
    return value
