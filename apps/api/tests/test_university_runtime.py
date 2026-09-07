"""Canonical university reads and writes against an explicitly isolated v3 DB."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from sqlalchemy import text

from audentra.bootstrap.api import build_api_runtime
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError
from audentra.domain.edward_actions import parse_student_action
from audentra.infrastructure.postgres.university_repository import _cutoff
from audentra.infrastructure.seeding.synthetic_university import SYNTHETIC_TENANT_ID
from audentra.integrations.assistant.guard import guard_grounded_answer


def test_cutoffs_are_instants_and_require_timezones() -> None:
    assert _cutoff("2026-09-01T23:59:59-04:00") == "2026-09-02T03:59:59Z"
    with pytest.raises(BadRequestError):
        _cutoff("2026-09-01")


def test_date_evidence_permits_its_year_but_no_other_year() -> None:
    evidence = ["The term starts at 2026-08-28T04:00:00Z"]
    assert guard_grounded_answer(
        answer="The term starts in 2026.", evidence_texts=evidence
    ).accepted
    assert not guard_grounded_answer(
        answer="The term starts in 2027.", evidence_texts=evidence
    ).accepted


def test_policy_day_first_dates_and_hold_release_boundary() -> None:
    from audentra.domain.edward_action_catalog import boundary_for

    assert guard_grounded_answer(
        answer="The deadline is September 11, 2026.", evidence_texts=["Add/drop: 11 September 2026"]
    ).accepted
    assert not guard_grounded_answer(
        answer="The deadline is September 12, 2026.", evidence_texts=["Add/drop: 11 September 2026"]
    ).accepted
    boundary = boundary_for("student", "I paid this morning. Remove my financial hold.")
    assert boundary is not None and boundary.code == "hold_release"


def _url() -> str:
    value = os.getenv("AUDENTRA_UNIVERSITY_TEST_DATABASE_URL", "")
    if not value:
        pytest.skip("Set AUDENTRA_UNIVERSITY_TEST_DATABASE_URL to a seeded isolated database")
    parsed = urlparse(value)
    if parsed.hostname not in ("127.0.0.1", "localhost") or not parsed.path.startswith(
        "/audentra_university_test"
    ):
        raise ValueError("University tests require an explicitly named local test database")
    return value


@pytest.mark.postgres
@pytest.mark.integration
def test_runtime_preserves_evidence_scope_and_confirmed_write_invariants() -> None:
    async def scenario() -> None:
        runtime = await build_api_runtime(
            RuntimeSettings.from_environment({"DATABASE_URL": _url()})
        )
        service: Any = runtime.service
        university = service.repository.university
        portal = service.repository.portal
        try:
            async with runtime.engine.connect() as c:
                students = (
                    (
                        await c.execute(
                            text(
                                "SELECT id FROM student WHERE tenant_id=:tenant "
                                "ORDER BY external_ref LIMIT 30"
                            ),
                            {"tenant": SYNTHETIC_TENANT_ID},
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(students) == 30
                assert (
                    await c.scalar(
                        text("SELECT count(*) FROM university.student WHERE tenant_id=:tenant"),
                        {"tenant": SYNTHETIC_TENANT_ID},
                    )
                    == 3000
                )
                # No duplicated appointments introduced by the import triggers.
                assert (
                    await c.scalar(
                        text("SELECT count(*) FROM university.appointment WHERE tenant_id=:tenant"),
                        {"tenant": SYNTHETIC_TENANT_ID},
                    )
                    == 1774
                )
            auth = AuthContext(SYNTHETIC_TENANT_ID, str(students[0]), str(students[0]), "student")
            for sid in students:
                actor = replace(auth, student_id=str(sid), actor_id=str(sid))
                account = await university.record(actor, "account")
                financial = await portal.get_student_financials(actor)
                posted = sum(
                    r["amount_cents"] for r in account["ledger"] if r["term_id"] == "2026FA"
                )
                assert financial["remainingBalanceCents"] == posted
                academic = await portal.get_student_academics(actor)
                assert academic["selectedProgram"]["name"] == account["student"]["program_name"]
                assert len(academic["availablePrograms"]) == 14
                profile = await portal.get_student_profile(actor)
                assert profile["email"] == account["student"]["email"]
            with pytest.raises(ApiError):
                await university.record(replace(auth, tenant_id=str(uuid4())))
            with pytest.raises(ApiError):
                await university.record(replace(auth, actor_type="delegate"))
            with pytest.raises(ApiError):
                await university.operations(auth)
            policy = await university.policies(auth, "payment settlement")
            assert policy["sources"]
            assert all(s["audience"] != "internal" for s in policy["sources"])
            assert all(s["content_hash"] and s["citation"] for s in policy["sources"])
            history_auth = replace(
                auth,
                student_id="957e7792-507b-4781-bb7c-d4f4c4b2a6ff",
                actor_id="957e7792-507b-4781-bb7c-d4f4c4b2a6ff",
            )
            with pytest.raises(BadRequestError):
                await university.record(history_auth, "history", entity_type="grade")
            prior = await university.record(
                history_auth,
                "history",
                entity_type="transfer_credit",
                known_at="2026-09-01T23:59:59-04:00",
            )
            current = await university.record(
                history_auth, "history", entity_type="transfer_credit"
            )
            assert prior["totalMatchingEvents"] == 1 and prior["events"][0]["to_state"] == "F"
            assert current["totalMatchingEvents"] == 2
            assert current["events"][0]["to_state"] == "B"
            from audentra.integrations.assistant.read_loop import bound_result

            assert len(bound_result(current)["events"]) == 2
            bounded = bound_result(
                {
                    "domain": "overview",
                    "snapshotAt": "2026-09-08T16:00:00Z",
                    "deadline": "2026-09-12T03:59:00Z",
                }
            )
            assert bounded["deadline"] == "2026-09-11T23:59:00-04:00"
            before = await portal.get_student_profile(auth)
            conversation = await portal.create_assistant_conversation(auth)
            request = parse_student_action("Change my preferred name to Rowan.")
            assert request is not None
            gateway = service.edward_actions
            intent = await gateway.propose_student(
                auth, request, conversation_id=str(conversation["id"]), trace_id=str(uuid4())
            )
            assert (await university.record(auth))["student"]["preferred_name"] == before[
                "preferredName"
            ]
            with pytest.raises(ConflictError):
                await gateway.confirm(
                    auth,
                    str(intent["id"]),
                    expected_version=int(intent["version"]),
                    content_sha256="a" * 64,
                    request_id=str(uuid4()),
                )
            receipt = await gateway.confirm(
                auth,
                str(intent["id"]),
                expected_version=int(intent["version"]),
                content_sha256=str(intent["contentSha256"]),
                request_id=str(uuid4()),
            )
            replay = await gateway.confirm(
                auth,
                str(intent["id"]),
                expected_version=int(intent["version"]),
                content_sha256=str(intent["contentSha256"]),
                request_id=str(uuid4()),
            )
            assert receipt == replay
            assert receipt["status"] == "succeeded" and receipt["auditEventIds"]
            assert (await university.record(auth))["student"]["preferred_name"] == "Rowan"
            after = await portal.get_student_profile(auth)
            await portal.update_student_profile(
                auth,
                {"preferredName": before["preferredName"], "expectedVersion": after["version"]},
                str(uuid4()),
            )
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.postgres
@pytest.mark.integration
def test_rls_and_transactional_document_deposit_and_case_guards() -> None:
    async def scenario() -> None:
        from sqlalchemy.exc import IntegrityError

        from audentra.infrastructure.db.engine import create_database_engine

        engine = create_database_engine(_url())
        tenant = SYNTHETIC_TENANT_ID
        try:
            async with engine.connect() as c:
                transaction = await c.begin()
                try:
                    await c.execute(text("CREATE ROLE audentra_university_rls_probe NOLOGIN"))
                    await c.execute(
                        text("GRANT USAGE ON SCHEMA university TO audentra_university_rls_probe")
                    )
                    await c.execute(
                        text(
                            "GRANT SELECT ON ALL TABLES IN SCHEMA university "
                            "TO audentra_university_rls_probe"
                        )
                    )
                    await c.execute(text("SET LOCAL ROLE audentra_university_rls_probe"))
                    assert await c.scalar(text("SELECT count(*) FROM university.student")) == 0
                    assert (
                        await c.scalar(text("SELECT count(*) FROM university.account_balance")) == 0
                    )
                    await c.execute(
                        text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                        {"tenant": tenant},
                    )
                    assert await c.scalar(text("SELECT count(*) FROM university.student")) == 3000
                    await c.execute(
                        text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                        {"tenant": str(uuid4())},
                    )
                    assert await c.scalar(text("SELECT count(*) FROM university.student")) == 0
                    assert (
                        await c.scalar(text("SELECT count(*) FROM university.account_balance")) == 0
                    )
                    await c.execute(text("RESET ROLE"))
                    await c.execute(
                        text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                        {"tenant": tenant},
                    )
                    doc = (
                        (
                            await c.execute(
                                text(
                                    "SELECT w.id,w.version,(SELECT max(revision) FROM "
                                    "university.document_revision r WHERE r.tenant_id=w.tenant_id "
                                    "AND r.document_id=w.id) AS last_revision "
                                    "FROM university.document w "
                                    "JOIN document_record d ON d.id::text=w.id "
                                    "AND d.tenant_id=w.tenant_id "
                                    "WHERE w.tenant_id=:tenant AND w.status='ACCEPTED' LIMIT 1"
                                ),
                                {"tenant": tenant},
                            )
                        )
                        .mappings()
                        .one()
                    )
                    await c.execute(
                        text(
                            "UPDATE document_record SET status='rejected' "
                            "WHERE tenant_id=:tenant AND id=CAST(:id AS uuid)"
                        ),
                        {"tenant": tenant, "id": doc["id"]},
                    )
                    assert (
                        await c.scalar(
                            text(
                                "SELECT status FROM university.document "
                                "WHERE tenant_id=:tenant AND id=:id"
                            ),
                            {"tenant": tenant, "id": doc["id"]},
                        )
                        == "REJECTED"
                    )
                    assert (
                        await c.scalar(
                            text(
                                "SELECT max(revision) FROM university.document_revision "
                                "WHERE tenant_id=:tenant AND document_id=:id"
                            ),
                            {"tenant": tenant, "id": doc["id"]},
                        )
                        == doc["last_revision"] + 1
                    )
                    case = await c.scalar(
                        text(
                            "SELECT l.runtime_id FROM university.runtime_link l "
                            "JOIN university.workflow_step s ON s.tenant_id=l.tenant_id "
                            "AND s.workflow_id=l.world_id WHERE l.tenant_id=:tenant "
                            "AND l.kind='workflow' AND s.status='blocked' LIMIT 1"
                        ),
                        {"tenant": tenant},
                    )
                    with pytest.raises(IntegrityError):
                        async with c.begin_nested():
                            await c.execute(
                                text(
                                    "UPDATE staff_work_item SET status='done',version=version+1 "
                                    "WHERE tenant_id=:tenant AND id=:id"
                                ),
                                {"tenant": tenant, "id": case},
                            )
                    offer = (
                        (
                            await c.execute(
                                text(
                                    "SELECT a.id,a.student_id FROM admission_offer a "
                                    "WHERE a.tenant_id=:tenant AND a.status='accepted' "
                                    "AND NOT EXISTS("
                                    "SELECT 1 FROM payment_transaction p "
                                    "WHERE p.tenant_id=a.tenant_id "
                                    "AND p.offer_id=a.id AND p.status='succeeded') LIMIT 1"
                                ),
                                {"tenant": tenant},
                            )
                        )
                        .mappings()
                        .one()
                    )
                    payment_id = uuid4()
                    await c.execute(
                        text(
                            "INSERT INTO payment_transaction(id,tenant_id,student_id,offer_id,type,"
                            "amount_cents,status,processor,processor_reference) VALUES "
                            "(:id,:tenant,:student,:offer,'enrollment_deposit',50000,'succeeded',"
                            "'dummy',:reference)"
                        ),
                        {
                            "id": payment_id,
                            "tenant": tenant,
                            "student": offer["student_id"],
                            "offer": offer["id"],
                            "reference": f"university-test:{payment_id}",
                        },
                    )
                    assert (
                        await c.scalar(
                            text(
                                "SELECT sum(amount_cents) FROM university.ledger "
                                "WHERE tenant_id=:tenant AND payment_id=:payment"
                            ),
                            {"tenant": tenant, "payment": str(payment_id)},
                        )
                        == -50000
                    )
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.postgres
@pytest.mark.integration
def test_new_submission_revises_unmet_evidence_and_review_follows_current_file() -> None:
    async def scenario() -> None:
        from audentra.infrastructure.db.engine import create_database_engine

        engine = create_database_engine(_url())
        try:
            async with engine.connect() as c:
                transaction = await c.begin()
                try:
                    tenant = SYNTHETIC_TENANT_ID
                    await c.execute(
                        text("SELECT set_config('audentra.tenant_id',:t,true)"), {"t": tenant}
                    )
                    doc = (
                        (
                            await c.execute(
                                text("""
                        SELECT w.id,w.student_id,w.category,
                          (SELECT max(revision) FROM university.document_revision r
                           WHERE r.tenant_id=w.tenant_id AND r.document_id=w.id) AS last_revision
                        FROM university.document w
                        JOIN document_record d ON d.tenant_id=w.tenant_id AND d.id::text=w.id
                        WHERE w.tenant_id=:t AND w.category='transcript'
                          AND w.status IN (
                            'NOT_SUBMITTED','REJECTED','EXPIRED','NEEDS_RESUBMISSION')
                        ORDER BY w.id LIMIT 1
                    """),
                                {"t": tenant},
                            )
                        )
                        .mappings()
                        .one()
                    )
                    values = {"t": tenant, "student": doc["student_id"], "id": uuid4()}
                    count_before = await c.scalar(
                        text("SELECT count(*) FROM university.document WHERE tenant_id=:t"), values
                    )
                    await c.execute(
                        text("""
                        INSERT INTO document_record(id,tenant_id,student_id,file_name,mime_type,
                          size_bytes,category,status,storage_provider)
                        VALUES(:id,:t,CAST(:student AS uuid),'test.pdf','application/pdf',100,
                          'transcript','uploaded','local_placeholder')
                    """),
                        values,
                    )
                    assert (
                        await c.scalar(
                            text("SELECT count(*) FROM university.document WHERE tenant_id=:t"),
                            values,
                        )
                        == count_before
                    )
                    assert (
                        await c.scalar(
                            text(
                                "SELECT status FROM university.document "
                                "WHERE tenant_id=:t AND id=:doc"
                            ),
                            {**values, "doc": doc["id"]},
                        )
                        == "UPLOADED"
                    )
                    # Reviewing the new file must reach the original requirement's current head.
                    await c.execute(
                        text(
                            "UPDATE document_record SET status='accepted' "
                            "WHERE tenant_id=:t AND id=:id"
                        ),
                        values,
                    )
                    assert (
                        await c.scalar(
                            text(
                                "SELECT status FROM university.document "
                                "WHERE tenant_id=:t AND id=:doc"
                            ),
                            {**values, "doc": doc["id"]},
                        )
                        == "ACCEPTED"
                    )
                    # An old file's review cannot overwrite its replacement's decision.
                    await c.execute(
                        text(
                            "UPDATE document_record SET status='rejected' "
                            "WHERE tenant_id=:t AND id=CAST(:doc AS uuid)"
                        ),
                        {**values, "doc": doc["id"]},
                    )
                    assert (
                        await c.scalar(
                            text(
                                "SELECT status FROM university.document "
                                "WHERE tenant_id=:t AND id=:doc"
                            ),
                            {**values, "doc": doc["id"]},
                        )
                        == "ACCEPTED"
                    )
                    assert (
                        await c.scalar(
                            text(
                                "SELECT max(revision) FROM university.document_revision "
                                "WHERE tenant_id=:t AND document_id=:doc"
                            ),
                            {**values, "doc": doc["id"]},
                        )
                        == doc["last_revision"] + 2
                    )
                    for requirement_code, category, office in [
                        ("immunization_record", "immunization", "SHS"),
                        ("financial_aid_verification", "financial_aid_submission", "FA"),
                    ]:
                        requirement = (
                            (
                                await c.execute(
                                    text("""
                            SELECT r.id,j.student_id FROM student_requirement r
                            JOIN enrollment_journey j ON j.tenant_id=r.tenant_id
                              AND j.id=r.journey_id
                            JOIN requirement_definition_version d
                              ON d.tenant_id=r.tenant_id
                              AND d.id=r.requirement_definition_version_id
                            WHERE r.tenant_id=:t AND d.code=:code AND r.retired_at IS NULL LIMIT 1
                        """),
                                    {"t": tenant, "code": requirement_code},
                                )
                            )
                            .mappings()
                            .one()
                        )
                        new_id = uuid4()
                        await c.execute(
                            text("""
                            INSERT INTO document_record(id,tenant_id,student_id,requirement_id,
                              file_name,mime_type,size_bytes,category,status,storage_provider)
                            VALUES(:id,:t,:student,:requirement,'placeholder.pdf','application/pdf',1,
                              'other','placeholder','local_placeholder')
                        """),
                            {
                                "id": new_id,
                                "t": tenant,
                                "student": requirement["student_id"],
                                "requirement": requirement["id"],
                            },
                        )
                        row = (
                            (
                                await c.execute(
                                    text("""
                            SELECT category,status,office_id FROM university.document w
                            WHERE tenant_id=:t AND (id=:id OR id IN (
                              SELECT world_id FROM university.runtime_link WHERE tenant_id=:t
                                AND kind='document' AND runtime_id=CAST(:id AS uuid)))
                        """),
                                    {"t": tenant, "id": str(new_id)},
                                )
                            )
                            .mappings()
                            .one()
                        )
                        assert row["category"] == category and row["office_id"] == office
                        assert row["status"] == "NOT_SUBMITTED"
                        if category == "financial_aid_submission":
                            from sqlalchemy.exc import IntegrityError

                            params = {
                                "t": tenant,
                                "student": str(requirement["student_id"]),
                                "requirement": requirement["id"],
                            }
                            await c.execute(
                                text("""
                                UPDATE university.document SET status='UNDER_REVIEW'
                                WHERE tenant_id=:t AND student_id=:student
                                  AND category IN ('verification_worksheet','tax_return_transcript')
                            """),
                                params,
                            )
                            await c.execute(
                                text("""
                                UPDATE university.document SET status='ACCEPTED'
                                WHERE tenant_id=:t AND student_id=:student
                                  AND category='verification_worksheet'
                            """),
                                params,
                            )
                            status = await c.scalar(
                                text("SELECT university.required_document_status(:t,:requirement)"),
                                params,
                            )
                            assert status == "under_review"
                            await c.execute(
                                text("""
                                UPDATE student_requirement SET status='under_review'
                                WHERE tenant_id=:t AND id=:requirement
                            """),
                                params,
                            )
                            with pytest.raises(IntegrityError):
                                async with c.begin_nested():
                                    await c.execute(
                                        text("""
                                        UPDATE student_requirement SET status='completed'
                                        WHERE tenant_id=:t AND id=:requirement
                                    """),
                                        params,
                                    )

                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
