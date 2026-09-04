"""Coherence enrichment of an existing synthetic-university tenant.

The synthetic population (students, staff, awards, requirements) was generated
before the institutional world existed. Four things in it contradict the
approved corpus, and each is corrected here, idempotently, against an already
seeded database:

1. **Housing residences.** The tenant listed three generic options while
   students' onboarding answers name rooms in the six real halls (ALD, BIR,
   CED, DUN, ELM, FER). The six halls replace the three, reusing the tenant's
   existing housing photographs, and each student's residence answer is
   re-pointed from the room label ("FER-327B" → Fernhollow House).
2. **International students' aid.** Federal and state funds were awarded to
   students on a visa, who are not eligible for them. Their federal/state
   awards are re-sourced to the institutional funds the corpus defines (Aster
   Global Scholarship, International Campus Employment award, the
   partner-lender International Student Loan), preserving amounts, statuses
   and therefore every remaining-balance figure.
3. **SAP maximum timeframe.** Every student carried a flat 180 attempted-credit
   maximum; the policy is 150 % of the program's published length, so the
   figure now follows the program (120 → 180 … 130 → 195).
4. **Managed content.** The expanded course catalog and campus-life content in
   the tenant's YAML are republished through the canonical managed-content
   publication path, exactly as a staff member would from the portal.

Everything is scoped to one tenant and runs in one transaction per step.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import yaml  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.infrastructure.postgres.managed_configuration_repository import (
    PostgresManagedConfigurationRepository,
)

_NAMESPACE = UUID("2c8a0d5e-4b7f-4c0e-8f7a-1d2e3f4a5b6c")

# code, name, style, beds, description, amenities, photograph to reuse (the
# tenant's existing housing image with the closest style).
RESIDENCE_HALLS: tuple[tuple[str, str, str, int, str, tuple[str, ...], str], ...] = (
    (
        "alder_hall",
        "Alder Hall",
        "traditional",
        320,
        "Traditional corridor-style hall for first-year students; home of the First-Year "
        "Exploration living-learning community. Doubles, a limited number of singles, and "
        "temporary triples in high-demand years.",
        ("shared bathrooms", "floor lounges", "laundry", "peer mentors", "card access"),
        "aster_residence_hall",
    ),
    (
        "birchwood_commons",
        "Birchwood Commons",
        "suite",
        260,
        "Suite-style hall for first- and second-year students — four singles or two doubles "
        "around a shared bathroom and lounge — with Birchwood Dining Hall on the ground floor.",
        ("suite bathroom", "suite lounge", "dining hall downstairs", "laundry", "study rooms"),
        "student_village",
    ),
    (
        "cedarcroft_house",
        "Cedarcroft House",
        "traditional",
        180,
        "A smaller traditional hall for first-year students. Housing & Residence Life "
        "(Cedarcroft Commons) and the campus mail room are on the ground floor.",
        ("shared bathrooms", "housing office downstairs", "mail room", "laundry", "kitchenette"),
        "aster_residence_hall",
    ),
    (
        "dunmore_hall",
        "Dunmore Hall",
        "suite",
        240,
        "Suite-style hall for second-year and transfer students; home of the Wellness and "
        "Recovery substance-free community.",
        ("suite bathroom", "suite lounge", "quiet floors", "laundry", "fitness room"),
        "student_village",
    ),
    (
        "elmridge_commons",
        "Elmridge Commons",
        "apartment",
        200,
        "Four-bedroom apartments with a kitchen and living room for third- and fourth-year "
        "and transfer students. Any meal plan, or none.",
        ("full kitchen", "living room", "in-unit laundry", "parking", "card access"),
        "aster_apartments",
    ),
    (
        "fernhollow_house",
        "Fernhollow House",
        "apartment",
        150,
        "Two- and four-bedroom apartments open to all years; home of the Design Studio and "
        "Global Village living-learning communities, with 24-hour studio access.",
        ("kitchen", "24-hour studio", "living-learning floors", "laundry", "card access"),
        "aster_apartments",
    ),
)

_HALL_BY_PREFIX = {
    "ALD": "alder_hall",
    "BIR": "birchwood_commons",
    "CED": "cedarcroft_house",
    "DUN": "dunmore_hall",
    "ELM": "elmridge_commons",
    "FER": "fernhollow_house",
}
_LABEL = re.compile(r"^([A-Z]{3})-")

# Federal/state fund name -> (international fund name, source, type)
_INTERNATIONAL_FUNDS: dict[str, tuple[str, str, str]] = {
    "Federal Pell Grant": ("Aster Global Scholarship", "institutional", "grant"),
    "Federal Supplemental Educational Opportunity Grant": (
        "Aster Global Scholarship",
        "institutional",
        "grant",
    ),
    "State Access Grant": ("Aster Global Scholarship", "institutional", "grant"),
    "Federal Direct Subsidised Loan": ("International Student Loan", "private", "loan"),
    "Federal Direct Unsubsidised Loan": ("International Student Loan", "private", "loan"),
    "Federal Work-Study": ("International Campus Employment", "institutional", "work_study"),
    # Need-based Aster Access is packaged from the FAFSA, which visa holders
    # cannot file; their institutional need-based award is the Global fund.
    "Aster Access Scholarship": ("Aster Global Scholarship", "institutional", "grant"),
    # The targets map to themselves so a re-run merges duplicates it created.
    "Aster Global Scholarship": ("Aster Global Scholarship", "institutional", "grant"),
    "International Student Loan": ("International Student Loan", "private", "loan"),
    "International Campus Employment": (
        "International Campus Employment",
        "institutional",
        "work_study",
    ),
}
# Students who answered the generic pre-enrichment options without a room
# label: map the legacy value to the hall that option described.
_LEGACY_RESIDENCE = {
    "residence_hall": "alder_hall",
    "aster_residence_hall": "alder_hall",
    "aster_apartments": "elmridge_commons",
    "student_village": "birchwood_commons",
}


@dataclass(slots=True)
class EnrichmentReport:
    residences_written: int = 0
    residence_answers_repointed: int = 0
    awards_resourced: int = 0
    awards_merged: int = 0
    sap_rows_updated: int = 0
    offer_deadlines_updated: int = 0
    clubs_written: int = 0
    managed_published: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _stable(tenant_id: str, kind: str, key: str) -> UUID:
    return uuid5(_NAMESPACE, f"{tenant_id}:{kind}:{key}")


async def enrich_tenant(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    tenant_slug: str,
    staff_actor_id: str,
    configuration_root: Path | None = None,
    steps: Sequence[str] = ("housing", "aid", "sap", "offers", "clubs", "managed"),
) -> EnrichmentReport:
    report = EnrichmentReport()
    moment = datetime.now(UTC)
    if "housing" in steps:
        async with engine.begin() as connection:
            await _enrich_housing(connection, tenant_id, moment, report)
    if "clubs" in steps:
        async with engine.begin() as connection:
            await _enrich_clubs(connection, tenant_id, moment, report)
    if "aid" in steps:
        async with engine.begin() as connection:
            await _enrich_international_aid(connection, tenant_id, moment, report)
    if "sap" in steps:
        async with engine.begin() as connection:
            await _enrich_sap_maximums(connection, tenant_id, moment, report)
    if "offers" in steps:
        async with engine.begin() as connection:
            await _enrich_offer_deadlines(connection, tenant_id, moment, report)
    if "managed" in steps:
        await _republish_managed_content(
            engine, tenant_id, tenant_slug, staff_actor_id, configuration_root, report
        )
    return report


# ---------------------------------------------------------------------------
# 1. Housing
# ---------------------------------------------------------------------------


async def _enrich_housing(
    connection: AsyncConnection, tenant_id: str, moment: datetime, report: EnrichmentReport
) -> None:
    media = {
        str(row["code"]): row["media_asset_id"]
        for row in (
            await connection.execute(
                text(
                    "SELECT code, media_asset_id FROM housing_residence_option "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings()
    }
    # The generic options' photographs are the only housing images the tenant
    # owns; each hall reuses the one that matches its style.
    fallback_media = next(iter(media.values()), None)
    if fallback_media is None:
        report.notes.append("no housing photographs on the tenant; halls skipped")
        return
    for order, (code, name, style, beds, description, amenities, photo) in enumerate(
        RESIDENCE_HALLS, start=1
    ):
        await connection.execute(
            text(
                """
                INSERT INTO housing_residence_option (
                  id, tenant_id, code, name, description, amenities, media_asset_id,
                  display_order, active, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :code, :name, :description, CAST(:amenities AS jsonb),
                  :media, :order, true, :now, :now
                )
                ON CONFLICT (id) DO UPDATE SET
                  name = EXCLUDED.name, description = EXCLUDED.description,
                  amenities = EXCLUDED.amenities, media_asset_id = EXCLUDED.media_asset_id,
                  display_order = EXCLUDED.display_order, active = true,
                  updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "id": _stable(tenant_id, "residence", code),
                "tenant_id": tenant_id,
                "code": code,
                "name": name,
                "description": f"{description} {style.capitalize()} style, {beds} beds.",
                "amenities": _json(list(amenities)),
                "media": media.get(photo, fallback_media),
                "order": order,
                "now": moment,
            },
        )
        report.residences_written += 1
    # Retire the generic options (never delete: onboarding answers may name them).
    await connection.execute(
        text(
            """
            UPDATE housing_residence_option SET active = false, updated_at = :now
            WHERE tenant_id = :tenant_id AND active = true
              AND NOT (code = ANY(CAST(:codes AS text[])))
            """
        ),
        {"tenant_id": tenant_id, "codes": [hall[0] for hall in RESIDENCE_HALLS], "now": moment},
    )
    # Re-point each student's residence answer from the room label prefix.
    rows = (
        (
            await connection.execute(
                text(
                    """
                SELECT student_id, payload->>'housingAssignmentLabel' AS label,
                       payload->>'housingResidenceOption' AS residence
                FROM student_onboarding
                WHERE tenant_id = :tenant_id
                  AND (payload ? 'housingAssignmentLabel' OR payload ? 'housingResidenceOption')
                """
                ),
                {"tenant_id": tenant_id},
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        match = _LABEL.match(str(row["label"] or ""))
        hall = _HALL_BY_PREFIX.get(match.group(1)) if match else None
        if hall is None:
            hall = _LEGACY_RESIDENCE.get(str(row["residence"] or ""))
        if hall is None or hall == row["residence"]:
            continue
        await connection.execute(
            text(
                """
                UPDATE student_onboarding
                SET payload = jsonb_set(
                      payload, '{housingResidenceOption}', to_jsonb(CAST(:hall AS text))
                    ),
                    updated_at = :now
                WHERE tenant_id = :tenant_id AND student_id = :student_id
                """
            ),
            {"hall": hall, "now": moment, "tenant_id": tenant_id, "student_id": row["student_id"]},
        )
        report.residence_answers_repointed += 1


# ---------------------------------------------------------------------------
# 1b. Clubs (the tenant had none; the corpus and the Campus Life page expect some)
# ---------------------------------------------------------------------------

# name, category, description, long description, meeting schedule, contact
# role, next activity, image (an existing tenant club photograph, or None)
CLUBS: tuple[tuple[str, str, str, str, str, str, str, str | None], ...] = (
    (
        "Aster Robotics",
        "academic",
        "Builds and competes with autonomous robots; open to every major.",
        "Weekly build sessions in the Innovation Hall makerspace, a first-year mentoring track, "
        "and two intercollegiate competitions a year. No experience needed.",
        "Tue 19:00, Innovation Hall makerspace",
        "Club president",
        "Involvement Fair table, 2 September",
        "/media/clubs/robotics.jpg",
    ),
    (
        "Code Collective",
        "academic",
        "Peer programming, hackathons and interview practice for Computing and Data Science "
        "students.",
        "Runs the autumn hackathon with the Career Center, weekly problem-solving sessions and "
        "a beginners' track aligned with CS 101.",
        "Wed 18:00, Innovation Hall 210",
        "Club president",
        "Autumn hackathon, 17 to 18 October",
        "/media/clubs/code-collective.jpg",
    ),
    (
        "Women in Business",
        "professional",
        "Mentoring, speaker series and case competitions for students in Business and Accounting.",
        "Partners with the Department of Business on the spring case competition and runs a "
        "mentoring circle with alumnae.",
        "Thu 17:30, Student Union 300",
        "Club president",
        "Speaker series opens 24 September",
        "/media/clubs/women-in-business.jpg",
    ),
    (
        "Outdoor Aster",
        "recreation",
        "Day hikes, weekend trips and gear lending for students of every experience level.",
        "Runs a beginners' hike each Saturday of September and lends tents and packs from the "
        "Recreation Center.",
        "Sat 09:00, Gatehouse (trips depart)",
        "Trip coordinator",
        "First-year hike, 5 September",
        "/media/clubs/outdoor-aster.jpg",
    ),
    (
        "International Students Association",
        "cultural",
        "Community, cultural nights and practical support for students from abroad and their "
        "friends.",
        "Works with International Student Services on check-in week, hosts the Global Village "
        "dinners in Fernhollow House and a monthly cultural night.",
        "Fri 18:00, Global Center 1F lounge",
        "Club president",
        "Welcome dinner, 22 August (check-in week)",
        None,
    ),
    (
        "Aster Pre-Health Society",
        "academic",
        "For Nursing, Biology and Chemistry students heading to health professions.",
        "Application workshops, shadowing placements arranged with the Department of Health "
        "Sciences, and the spring health-professions panel.",
        "Mon 18:00, Wellness Center seminar room",
        "Club president",
        "Shadowing sign-up, 15 September",
        None,
    ),
    (
        "Design Collective",
        "arts",
        "BFA Design students and anyone who makes things; runs the Fernhollow studio nights.",
        "Open studio nights in Fernhollow House, the spring student exhibition and portfolio "
        "reviews for internal-transfer applicants.",
        "Thu 19:00, Fernhollow House studio",
        "Studio lead",
        "Portfolio review night, 8 October",
        None,
    ),
    (
        "Economics Society",
        "academic",
        "Debates, guest lectures and the annual policy competition for Social Sciences students.",
        "Hosts a faculty lecture each month and fields a team for the intercollegiate policy "
        "competition in March.",
        "Tue 17:00, Larkin Library 2F seminar room",
        "Club president",
        "First debate, 16 September",
        None,
    ),
    (
        "Aster Chamber Singers",
        "arts",
        "Auditioned choir performing the Winter Concert and Commencement.",
        "Rehearses twice weekly; auditions in the first two weeks of each term.",
        "Mon/Wed 19:30, Student Union recital room",
        "Director",
        "Auditions, 1 to 11 September",
        None,
    ),
    (
        "Commuter Student Network",
        "community",
        "Support and space for students living off campus or with family.",
        "Commuter lounge in the Student Union, carpool matching and a voice on parking and "
        "shuttle matters through Student Life.",
        "Wed 12:00, Student Union commuter lounge",
        "Network chair",
        "Commuter breakfast, 3 September",
        None,
    ),
    (
        "Transfer Student Alliance",
        "community",
        "Peer support for transfer students, from credit evaluation to finding a study group.",
        "Meets with the Transfer Success Advisers each term and runs the transfer mentor program "
        "launched at Transfer Orientation.",
        "Thu 12:30, Advising Centre lounge",
        "Alliance chair",
        "Transfer Orientation lunch, 27 August",
        None,
    ),
    (
        "Aster Service Corps",
        "service",
        "Community service placements with local partners; counts towards Work-Study "
        "community service.",
        "Weekly placements at partner organisations and the Spring Festival service day.",
        "Sat 10:00, Student Union 300",
        "Service coordinator",
        "Placement sign-up, 9 September",
        None,
    ),
)


async def _enrich_clubs(
    connection: AsyncConnection, tenant_id: str, moment: datetime, report: EnrichmentReport
) -> None:
    images = {
        str(row["public_path"]): row["id"]
        for row in (
            await connection.execute(
                text(
                    "SELECT id, public_path FROM media_asset WHERE tenant_id = :tenant_id "
                    "AND purpose = 'student_club' AND active = true"
                ),
                {"tenant_id": tenant_id},
            )
        ).mappings()
    }
    contact = (
        await connection.execute(
            text(
                "SELECT display_name FROM staff_member WHERE tenant_id = :tenant_id "
                "AND component = 'Student Life' AND active = true "
                "ORDER BY CASE role_code WHEN 'student_life_coordinator' THEN 0 ELSE 1 END, "
                "display_name LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        )
    ).first()
    contact_name = str(contact[0]) if contact else "Office of Student Life"
    for (
        name,
        category,
        description,
        long_description,
        schedule,
        role,
        next_activity,
        image,
    ) in CLUBS:
        await connection.execute(
            text(
                """
                INSERT INTO student_club (
                  id, tenant_id, name, category, description, contact_name, contact_role,
                  contact_channel, latest_update, next_activity, active, media_asset_id,
                  source_label, source_status, social_links, long_description,
                  meeting_schedule, membership_open, version, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, :name, :category, :description, :contact_name, :contact_role,
                  :contact_channel, :latest_update, :next_activity, true, :media,
                  'Office of Student Life', 'tenant_authored', '[]'::jsonb, :long_description,
                  :schedule, true, 1, :now, :now
                )
                ON CONFLICT (tenant_id, name) DO UPDATE SET
                  category = EXCLUDED.category, description = EXCLUDED.description,
                  contact_name = EXCLUDED.contact_name, contact_role = EXCLUDED.contact_role,
                  contact_channel = EXCLUDED.contact_channel,
                  latest_update = EXCLUDED.latest_update, next_activity = EXCLUDED.next_activity,
                  active = true, media_asset_id = EXCLUDED.media_asset_id,
                  long_description = EXCLUDED.long_description,
                  meeting_schedule = EXCLUDED.meeting_schedule, updated_at = EXCLUDED.updated_at
                """
            ),
            {
                "id": _stable(tenant_id, "club", name),
                "tenant_id": tenant_id,
                "name": name,
                "category": category,
                "description": description,
                "contact_name": contact_name,
                "contact_role": f"{role} (registered through the Office of Student Life)",
                "contact_channel": "studentlife@synthetic.aster.example",
                "latest_update": (
                    f"Registered for 2026-2027 with the Office of Student Life. {description}"
                ),
                "next_activity": next_activity,
                "media": images.get(image) if image else None,
                "long_description": long_description,
                "schedule": schedule,
                "now": moment,
            },
        )
        report.clubs_written += 1


# ---------------------------------------------------------------------------
# 2. International students' aid
# ---------------------------------------------------------------------------


async def _enrich_international_aid(
    connection: AsyncConnection, tenant_id: str, moment: datetime, report: EnrichmentReport
) -> None:
    rows = (
        (
            await connection.execute(
                text(
                    """
                SELECT a.id, a.student_id, a.academic_year, a.name, a.offered_amount_cents,
                       a.accepted_amount_cents, a.status, a.requires_action
                FROM student_financial_award a
                JOIN student_onboarding o
                  ON o.student_id = a.student_id AND o.tenant_id = a.tenant_id
                WHERE a.tenant_id = :tenant_id
                  AND o.payload->>'residencyStatus' = 'international'
                  AND a.name = ANY(CAST(:names AS text[]))
                ORDER BY a.student_id, a.name
                """
                ),
                {"tenant_id": tenant_id, "names": list(_INTERNATIONAL_FUNDS)},
            )
        )
        .mappings()
        .all()
    )
    # Group by (student, year, target fund) so two federal grants become one
    # Global Scholarship row with the amounts summed.
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        target = _INTERNATIONAL_FUNDS.get(str(row["name"]))
        if target is None:
            continue
        key = (str(row["student_id"]), str(row["academic_year"]), target[0])
        groups.setdefault(key, []).append(dict(row))
    status_rank = {"accepted": 3, "pending": 2, "offered": 1, "declined": 0}
    for (_student_id, _year, fund), awards in groups.items():
        source, kind = _INTERNATIONAL_FUNDS[str(awards[0]["name"])][1:]
        if len(awards) == 1 and str(awards[0]["name"]) == fund:
            continue  # already in its target shape
        offered = sum(int(a["offered_amount_cents"] or 0) for a in awards)
        accepted_values = [
            a["accepted_amount_cents"] for a in awards if a["accepted_amount_cents"] is not None
        ]
        accepted = sum(int(v) for v in accepted_values) if accepted_values else None
        status = max((str(a["status"]) for a in awards), key=lambda s: status_rank.get(s, -1))
        requires_action = any(bool(a["requires_action"]) for a in awards)
        keep, *merge = awards
        await connection.execute(
            text(
                """
                UPDATE student_financial_award
                SET name = :name, source = :source, type = :type,
                    offered_amount_cents = :offered, accepted_amount_cents = :accepted,
                    status = :status, requires_action = :requires_action, updated_at = :now
                WHERE id = :id AND tenant_id = :tenant_id
                """
            ),
            {
                "name": fund,
                "source": source,
                "type": kind,
                "offered": offered,
                "accepted": accepted,
                "status": status,
                "requires_action": requires_action,
                "now": moment,
                "id": keep["id"],
                "tenant_id": tenant_id,
            },
        )
        report.awards_resourced += 1
        for extra in merge:
            await connection.execute(
                text(
                    "DELETE FROM student_financial_award WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": extra["id"], "tenant_id": tenant_id},
            )
            report.awards_merged += 1


# ---------------------------------------------------------------------------
# 3. SAP maximum timeframe
# ---------------------------------------------------------------------------


async def _enrich_sap_maximums(
    connection: AsyncConnection, tenant_id: str, moment: datetime, report: EnrichmentReport
) -> None:
    result = await connection.execute(
        text(
            """
            UPDATE student_sap_status s
            SET maximum_attempted_credits = ROUND(p.total_credits * 1.5),
                updated_at = :now
            FROM admission_offer ao
            JOIN program p ON p.id = ao.program_id AND p.tenant_id = ao.tenant_id
            WHERE ao.student_id = s.student_id AND ao.tenant_id = s.tenant_id
              AND s.tenant_id = :tenant_id
              AND p.total_credits IS NOT NULL
              AND s.maximum_attempted_credits <> ROUND(p.total_credits * 1.5)
            """
        ),
        {"tenant_id": tenant_id, "now": moment},
    )
    report.sap_rows_updated = int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# 3b. Offer response deadlines
# ---------------------------------------------------------------------------


async def _enrich_offer_deadlines(
    connection: AsyncConnection, tenant_id: str, moment: datetime, report: EnrichmentReport
) -> None:
    """Spring 2027 offers carried the Fall response date (1 September 2026).

    Spring admits respond by 15 December 2026 under the calendar; the Fall
    value is left alone because the frozen evaluation banks derive facts
    from it.
    """

    result = await connection.execute(
        text(
            """
            UPDATE admission_offer ao
            SET response_deadline = DATE '2026-12-15', updated_at = :now
            FROM academic_term t
            WHERE t.id = ao.academic_term_id AND t.tenant_id = ao.tenant_id
              AND ao.tenant_id = :tenant_id AND t.name = 'Spring 2027'
              AND ao.response_deadline <> DATE '2026-12-15'
            """
        ),
        {"tenant_id": tenant_id, "now": moment},
    )
    report.offer_deadlines_updated = int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# 4. Managed content republication
# ---------------------------------------------------------------------------


async def _republish_managed_content(
    engine: AsyncEngine,
    tenant_id: str,
    tenant_slug: str,
    staff_actor_id: str,
    configuration_root: Path | None,
    report: EnrichmentReport,
) -> None:
    from audentra.infrastructure.seeding.relational import (
        _demo_configuration_student,
        _managed_configuration_root,
    )

    root = _managed_configuration_root(configuration_root) / tenant_slug
    repository = PostgresManagedConfigurationRepository(engine)
    auth = AuthContext(
        tenant_id=tenant_id,
        student_id=_demo_configuration_student(tenant_id),
        actor_id=staff_actor_id,
        actor_type="staff",
        tenant_slug=tenant_slug,
    )
    for kind, file_name in (("academics", "academics.yaml"), ("campus_life", "campus-life.yaml")):
        source = root / file_name
        if not source.exists():
            report.notes.append(f"{file_name} not found; {kind} not republished")
            continue
        yaml_text = source.read_text(encoding="utf-8")
        document = yaml.safe_load(yaml_text)
        current = await repository.get(auth, kind)
        if str(current.get("yaml") or "") == yaml_text or current.get("document") == _normalised(
            document
        ):
            report.notes.append(f"{kind}: unchanged (version {current.get('version')})")
            continue
        try:
            published = await repository.publish(
                auth,
                kind,
                {
                    "yaml": yaml_text,
                    "expectedVersion": int(current["version"]),
                    "changeSummary": (
                        "Synthetic-university enrichment: republished from tenant YAML"
                    ),
                },
                f"enrich-{tenant_slug}-{kind}",
            )
        except ApiError as error:
            report.notes.append(f"{kind}: publish failed — {error}")
            continue
        report.managed_published.append(f"{kind} v{published.get('version')}")


def _normalised(document: Any) -> Any:
    return yaml.safe_load(yaml.safe_dump(document, sort_keys=True))


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> None:
    import argparse

    from audentra.bootstrap.settings import RuntimeSettings
    from audentra.infrastructure.db.engine import create_database_engine
    from audentra.infrastructure.seeding.relational import SYNTHETIC_STAFF_ID
    from audentra.infrastructure.seeding.synthetic_university import (
        SYNTHETIC_TENANT_ID,
        SYNTHETIC_TENANT_SLUG,
    )

    parser = argparse.ArgumentParser(
        description="Bring an existing synthetic-university tenant into line with the corpus"
    )
    parser.add_argument("--tenant", default=SYNTHETIC_TENANT_SLUG)
    parser.add_argument(
        "--steps",
        default="housing,aid,sap,offers,clubs,managed",
        help="comma-separated subset of housing,aid,sap,offers,clubs,managed",
    )
    parser.add_argument("--root", type=Path, help="override the tenant configuration root")
    arguments = parser.parse_args(argv)
    steps = tuple(part.strip() for part in arguments.steps.split(",") if part.strip())

    async def run() -> EnrichmentReport:
        settings = RuntimeSettings.from_environment(os.environ)
        engine = create_database_engine(settings.database_url, settings.database)
        try:
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text("SELECT id FROM tenant WHERE slug = :slug"), {"slug": arguments.tenant}
                    )
                ).first()
            if row is None:
                raise SystemExit(
                    f"audentra-enrich-university: tenant {arguments.tenant!r} not found"
                )
            tenant_id = str(row[0])
            staff_id = (
                SYNTHETIC_STAFF_ID
                if tenant_id == SYNTHETIC_TENANT_ID
                else await staff_actor_for(engine, tenant_id)
            )
            return await enrich_tenant(
                engine,
                tenant_id=tenant_id,
                tenant_slug=arguments.tenant,
                staff_actor_id=staff_id,
                configuration_root=arguments.root,
                steps=steps,
            )
        finally:
            await engine.dispose()

    report = asyncio.run(run())
    print(
        f"Residences written: {report.residences_written}; residence answers re-pointed: "
        f"{report.residence_answers_repointed}; awards re-sourced: {report.awards_resourced} "
        f"({report.awards_merged} merged); SAP rows updated: {report.sap_rows_updated}; "
        f"clubs written: {report.clubs_written}; "
        f"offer deadlines updated: {report.offer_deadlines_updated}; "
        f"managed content published: {', '.join(report.managed_published) or 'none'}"
    )
    for note in report.notes:
        print(f"  note: {note}")


async def staff_actor_for(engine: AsyncEngine, tenant_id: str) -> str:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT id FROM staff_member WHERE tenant_id = :tenant_id AND active = true "
                    "ORDER BY created_at LIMIT 1"
                ),
                {"tenant_id": tenant_id},
            )
        ).first()
    if row is None:
        raise SystemExit("audentra-enrich-university: no staff member to publish as")
    return str(row[0])


if __name__ == "__main__":
    main()
