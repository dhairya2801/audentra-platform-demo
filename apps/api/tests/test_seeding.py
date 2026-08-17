from __future__ import annotations

import asyncio
import hashlib
import inspect
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
import yaml  # type: ignore[import-untyped]

from audentra.infrastructure.postgres.managed_configuration_repository import (
    academic_courses,
    campus_events,
    materialized_journey_tasks,
)
from audentra.infrastructure.seeding.cli import build_parser, execute_seed, selected_mode
from audentra.infrastructure.seeding.fixture import load_demo_fixture
from audentra.infrastructure.seeding.media import (
    MediaStorage,
    PortalMediaAsset,
    PortalMediaChecksumError,
    seed_portal_media,
)
from audentra.infrastructure.seeding.profile import seed_profile
from audentra.infrastructure.seeding.relational import (
    _CANONICAL_REQUIREMENTS,
    _DEMO_STAFF,
    _DEMO_WORK_ITEMS,
    _EXTRA_STUDENTS,
    _ONBOARDING_STEPS,
    _SYNTHETIC_STAFF,
    ASTER_TENANT_ID,
    HARVARD_TENANT_ID,
    ColumnSpec,
    _convert_value,
    _ensure_student_journey_for_accepted_offer,
    _foreign_key_order,
    _managed_configuration_root,
    _managed_configuration_tenants,
    _upsert_statement,
    provision_demo_managed_configurations,
    seed_relational_data,
)
from audentra.infrastructure.seeding.safety import SeedEnvironmentError, seed_environment
from audentra.infrastructure.seeding.synthetic_university import (
    SYNTHETIC_TENANT_ID,
    SYNTHETIC_TENANT_SLUG,
)
from audentra.infrastructure.storage.s3 import ObjectNotFoundError, S3ObjectMetadata


class FakeMediaStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.head_calls: list[str] = []
        self.put_calls: list[str] = []

    async def head(self, key: str) -> S3ObjectMetadata:
        self.head_calls.append(key)
        stored = self.objects.get(key)
        if stored is None:
            raise ObjectNotFoundError(key)
        body, digest = stored
        return S3ObjectMetadata(key, len(body), "image/jpeg", digest, None)

    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        sha256: str | None = None,
    ) -> str:
        assert content_type == "image/jpeg"
        digest = hashlib.sha256(body).hexdigest()
        assert sha256 == digest
        self.put_calls.append(key)
        self.objects[key] = (body, digest)
        return digest


def test_fixture_is_clean_legacy_inventory_with_expected_demo_ids() -> None:
    tables = load_demo_fixture()
    indexed = {table.name: table.rows for table in tables}

    assert len(tables) == 46
    assert sum(len(rows) for rows in indexed.values()) == 264
    assert len(indexed["document_record"]) == 1
    assert len(indexed["student_appointment"]) == 1
    assert len(indexed["tenant_reward_rule"]) == 34
    assert {row["id"] for row in indexed["tenant"]} == {
        "00000000-0000-7000-8000-000000000001",
        "00000000-0000-7000-8000-000000000002",
    }
    assert indexed["student"][0]["id"] == "00000000-0000-7000-8000-000000000101"
    assert indexed["admission_offer"][0]["status"] == "offered"


def test_supplement_inventory_is_deterministic_tenant_safe_and_complete() -> None:
    requirement_codes = [definition.code for definition in _CANONICAL_REQUIREMENTS]
    requirement_ids = [definition.definition_suffix for definition in _CANONICAL_REQUIREMENTS]
    instance_ids = [definition.instance_suffix for definition in _CANONICAL_REQUIREMENTS]

    assert len(requirement_codes) == len(set(requirement_codes)) == 8
    assert len(requirement_ids) == len(set(requirement_ids)) == 8
    assert len(instance_ids) == len(set(instance_ids)) == 8
    assert len(_ONBOARDING_STEPS) == len(set(_ONBOARDING_STEPS)) == 8

    scenarios_by_tenant = {
        tenant_id: [item for item in _EXTRA_STUDENTS if item.tenant_id == tenant_id]
        for tenant_id in (ASTER_TENANT_ID, HARVARD_TENANT_ID)
    }
    assert {tenant_id: len(items) for tenant_id, items in scenarios_by_tenant.items()} == {
        ASTER_TENANT_ID: 2,
        HARVARD_TENANT_ID: 2,
    }
    assert len({item.student_id for item in _EXTRA_STUDENTS}) == 4
    assert all(item.offer_status == "accepted" for item in _EXTRA_STUDENTS)

    assert {
        tenant_id: sum(staff.tenant_id == tenant_id for staff in _DEMO_STAFF)
        for tenant_id in (ASTER_TENANT_ID, HARVARD_TENANT_ID)
    } == {ASTER_TENANT_ID: 3, HARVARD_TENANT_ID: 3}
    assert len({staff.staff_id for staff in _DEMO_STAFF}) == 6
    assert {
        tenant_id: sum(item.tenant_id == tenant_id for item in _DEMO_WORK_ITEMS)
        for tenant_id in (ASTER_TENANT_ID, HARVARD_TENANT_ID)
    } == {ASTER_TENANT_ID: 2, HARVARD_TENANT_ID: 3}
    assert len({item.item_id for item in _DEMO_WORK_ITEMS}) == 5


def test_demo_requirement_reconciliation_updates_deterministic_ids() -> None:
    source = inspect.getsource(_ensure_student_journey_for_accepted_offer)

    assert "SELECT id" in source
    assert "existing_requirement = existing_result.first()" in source
    assert "if existing_requirement is not None" in source
    assert "ON CONFLICT (id) DO UPDATE SET" in source
    assert "requirement_definition_version_id=" in source
    assert "version=student_requirement.version + 1" in source
    assert "IS DISTINCT FROM" in source


def test_packaged_tenant_configurations_match_seeded_workflow_inventory() -> None:
    root = Path(__file__).resolve().parents[1] / "assets" / "config" / "tenants"
    expected_requirement_codes = {item.code for item in _CANONICAL_REQUIREMENTS}

    for tenant_slug in ("aster", "harvard"):
        tenant_root = root / tenant_slug
        journeys = yaml.safe_load((tenant_root / "journeys.yaml").read_text(encoding="utf-8"))
        academics = yaml.safe_load((tenant_root / "academics.yaml").read_text(encoding="utf-8"))
        campus_life = yaml.safe_load((tenant_root / "campus-life.yaml").read_text(encoding="utf-8"))

        assert journeys["tenant"] == tenant_slug
        assert journeys["configuration"] == "journeys"
        flows = {flow["kind"]: flow for flow in journeys["flows"]}
        assert set(flows) == {"onboarding", "enrollment"}
        assert [task["student_step"] for task in flows["onboarding"]["tasks"]] == list(
            _ONBOARDING_STEPS
        )
        assert {task["id"] for task in flows["enrollment"]["tasks"]} == (expected_requirement_codes)
        assert sum(len(flow["tasks"]) for flow in flows.values()) == 16
        assert len(materialized_journey_tasks(journeys)) == 8

        assert academics["tenant"] == tenant_slug
        assert academics["configuration"] == "academics"
        assert len(academics["courses"]) >= 6
        assert len({course["code"] for course in academics["courses"]}) == len(academics["courses"])
        assert len(academic_courses(academics)) == len(academics["courses"])

        assert campus_life["tenant"] == tenant_slug
        assert campus_life["configuration"] == "campus_life"
        assert len(campus_life["events"]) >= 3
        assert all(event["starts_at"] < event["ends_at"] for event in campus_life["events"])
        assert len(campus_events(campus_life)) == len(campus_life["events"])


def test_the_demo_campus_configuration_declares_the_housing_prerequisite() -> None:
    """The demo campus is Aster's document set plus one deliberate difference.

    Room selection waits on the deposit there, and `reconcile_journey_routes`
    recomputes every blocked step from `depends_on` alone — so the prerequisite
    has to be in the published YAML, not only in the seeder that wrote the
    first `blocked` status.
    """

    root = Path(__file__).resolve().parents[1] / "assets" / "config" / "tenants"
    demo = yaml.safe_load((root / "aster-demo" / "journeys.yaml").read_text(encoding="utf-8"))
    aster = yaml.safe_load((root / "aster" / "journeys.yaml").read_text(encoding="utf-8"))

    assert demo["tenant"] == "aster-demo"
    demo_tasks = {
        task["id"]: task
        for flow in demo["flows"]
        if flow["kind"] == "enrollment"
        for task in flow["tasks"]
    }
    aster_tasks = {
        task["id"]: task
        for flow in aster["flows"]
        if flow["kind"] == "enrollment"
        for task in flow["tasks"]
    }
    assert set(demo_tasks) == set(aster_tasks) == {item.code for item in _CANONICAL_REQUIREMENTS}
    assert demo_tasks["housing_preference"]["depends_on"] == ["enrollment_deposit"]
    assert "depends_on" not in aster_tasks["housing_preference"]

    # Everything else is the same graph, so the demo campus is not quietly a
    # different product.
    for code, task in demo_tasks.items():
        if code == "housing_preference":
            continue
        assert task == aster_tasks[code]


def test_the_synthetic_seed_profile_only_adds_the_demo_campus() -> None:
    """`compact` is the default, and the larger profile is strictly additive."""

    assert seed_profile({}) == "compact"
    assert _managed_configuration_tenants("compact") == ("aster", "harvard")
    assert _managed_configuration_tenants("synthetic_university") == (
        "aster",
        "harvard",
        SYNTHETIC_TENANT_SLUG,
    )
    # The demo campus staff are its own; nothing here can land in a preview
    # tenant, and nothing from a preview tenant can land in it.
    assert {staff.tenant_id for staff in _SYNTHETIC_STAFF} == {SYNTHETIC_TENANT_ID}
    assert SYNTHETIC_TENANT_ID not in {staff.tenant_id for staff in _DEMO_STAFF}
    assert SYNTHETIC_TENANT_ID not in {item.tenant_id for item in _DEMO_WORK_ITEMS}


def test_seed_environment_is_explicit_and_production_fails_closed() -> None:
    assert seed_environment({"AUDENTRA_ENV": "development"}) == "development"
    assert seed_environment({"AUDENTRA_ENV": "preview"}) == "preview"
    assert seed_environment({"NODE_ENV": "test"}) == "test"
    with pytest.raises(SeedEnvironmentError, match="disabled in production"):
        seed_environment({"AUDENTRA_ENV": "production"})
    with pytest.raises(SeedEnvironmentError, match="explicitly"):
        seed_environment({})


def test_cli_requires_exactly_one_explicit_mode() -> None:
    parser = build_parser()
    assert selected_mode(parser.parse_args(["--data"])) == "data"
    assert selected_mode(parser.parse_args(["--media"])) == "media"
    assert selected_mode(parser.parse_args(["--all"])) == "all"
    with pytest.raises(SystemExit):
        parser.parse_args([])
    with pytest.raises(SystemExit):
        parser.parse_args(["--data", "--media"])


def test_execute_seed_rejects_production_before_composing_resources() -> None:
    with pytest.raises(SeedEnvironmentError, match="disabled in production"):
        asyncio.run(execute_seed("all", environment={"AUDENTRA_ENV": "production"}))


def test_relational_seed_rejects_production_before_using_engine() -> None:
    with pytest.raises(SeedEnvironmentError, match="disabled in production"):
        asyncio.run(
            seed_relational_data(
                cast("object", None),  # type: ignore[arg-type]
                environment="production",
            )
        )


def test_managed_configuration_root_resolves_runtime_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_root = Path(__file__).resolve().parents[1] / "assets" / "config" / "tenants"
    monkeypatch.chdir(tenant_root.parents[2])
    monkeypatch.delenv("MANAGED_CONFIGURATION_ROOT", raising=False)

    assert _managed_configuration_root() == tenant_root


def test_normal_seed_replays_existing_managed_documents_without_new_publication() -> None:
    source = inspect.getsource(seed_relational_data)
    provision_source = inspect.getsource(provision_demo_managed_configurations)

    assert "provision_demo_managed_configurations(" in source
    assert "rematerialize_existing=True" in provision_source


def test_upsert_statement_is_insert_only_and_uses_typed_json_cast() -> None:
    columns = {
        "id": ColumnSpec("id", "uuid", "uuid"),
        "payload": ColumnSpec("payload", "jsonb", "jsonb"),
    }
    sql, binds = _upsert_statement("example", ("id", "payload"), columns, ("id",))

    assert 'INSERT INTO public."example"' in sql
    assert "CAST(:value_1 AS jsonb)" in sql
    assert 'ON CONFLICT ("id") DO NOTHING' in sql
    assert binds == {"id": "value_0", "payload": "value_1"}


def test_seed_tables_are_stably_topologically_sorted() -> None:
    fixture = load_demo_fixture()
    by_name = {table.name: table for table in fixture}
    ordered = _foreign_key_order(
        (by_name["student_club"], by_name["media_asset"], by_name["tenant"]),
        {
            "student_club": frozenset({"tenant", "media_asset"}),
            "media_asset": frozenset({"tenant"}),
            "tenant": frozenset(),
        },
    )

    assert [table.name for table in ordered] == ["tenant", "media_asset", "student_club"]


def test_fixture_value_conversion_preserves_postgres_boundary_types() -> None:
    uuid_value = _convert_value(
        "00000000-0000-7000-8000-000000000001", ColumnSpec("id", "uuid", "uuid")
    )
    timestamp = _convert_value(
        "2026-07-24T00:00:00+00:00",
        ColumnSpec("created_at", "timestamp with time zone", "timestamptz"),
    )
    date_value = _convert_value("2027-09-01", ColumnSpec("starts_on", "date", "date"))
    numeric = _convert_value("3.500", ColumnSpec("credits", "numeric", "numeric"))
    payload = _convert_value({"b": 2, "a": 1}, ColumnSpec("payload", "jsonb", "jsonb"))

    assert isinstance(uuid_value, UUID)
    assert isinstance(timestamp, datetime) and timestamp.tzinfo is not None
    assert date_value == date(2027, 9, 1)
    assert numeric == Decimal("3.500")
    assert payload == '{"a":1,"b":2}'


def test_media_seed_preflights_checksum_then_uploads_and_skips_unchanged(
    tmp_path: Path,
) -> None:
    body = b"reviewed-jpeg-bytes"
    digest = hashlib.sha256(body).hexdigest()
    (tmp_path / "housing").mkdir()
    (tmp_path / "housing" / "room.jpg").write_bytes(body)
    asset = PortalMediaAsset("housing/room.jpg", "tenant/media/room.jpg", digest)
    storage = FakeMediaStorage()

    first = asyncio.run(
        seed_portal_media(
            storage,
            environment="test",
            media_root=tmp_path,
            assets=(asset,),
        )
    )
    second = asyncio.run(
        seed_portal_media(
            storage,
            environment="test",
            media_root=tmp_path,
            assets=(asset,),
        )
    )

    assert first.uploaded == 1 and first.unchanged == 0
    assert second.uploaded == 0 and second.unchanged == 1
    assert storage.put_calls == [asset.storage_key]


def test_media_checksum_failure_makes_no_storage_calls(tmp_path: Path) -> None:
    (tmp_path / "clubs").mkdir()
    (tmp_path / "clubs" / "one.jpg").write_bytes(b"changed")
    asset = PortalMediaAsset("clubs/one.jpg", "tenant/media/one.jpg", "0" * 64)
    storage = FakeMediaStorage()

    with pytest.raises(PortalMediaChecksumError, match="checksum mismatch"):
        asyncio.run(
            seed_portal_media(
                cast(MediaStorage, storage),
                environment="development",
                media_root=tmp_path,
                assets=(asset,),
            )
        )
    assert storage.head_calls == []
    assert storage.put_calls == []
