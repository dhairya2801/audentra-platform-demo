"""Staff identity, adviser relationships, availability and person-bound booking.

Everything here is tenant-scoped in SQL. The staff side answers "who am I,
who reports to me, which students are mine, what is on my calendar"; the
student side answers "who is my adviser, when can I see them" and performs
bookings that respect the adviser's working hours, absences and existing
appointments. The slot rules live in :mod:`audentra.domain.scheduling`; this
module only loads the facts and persists the outcome.
"""

# ruff: noqa: S608 -- the only interpolations are the module-level column lists.
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, ConflictError, NotFoundError
from audentra.domain.scheduling import (
    APPOINTMENT_TYPES,
    ASSIGNMENT_ROLE_FOR_TYPE,
    MAX_AVAILABILITY_WINDOW,
    AvailabilityRule,
    BusyInterval,
    OpenSlot,
    blocking_reason,
    derive_open_slots,
    find_slot,
    resolve_zone,
)
from audentra.infrastructure.postgres.portal_repository import (
    PostgresPortalRepository,
    _iso,
    _utc_now,
)

JsonDict = dict[str, Any]

ASSIGNMENT_ROLES: tuple[str, ...] = (
    "primary_advisor",
    "admissions_counselor",
    "financial_aid_counselor",
    "international_adviser",
    "housing_coordinator",
)
TYPE_FOR_ASSIGNMENT_ROLE = {role: kind for kind, role in ASSIGNMENT_ROLE_FOR_TYPE.items()}
DEFAULT_SLOT_MINUTES = 30
DEFAULT_LOOKAHEAD = timedelta(days=14)
STALE_WORK_AFTER = timedelta(days=10)
# A meeting that ended yesterday is not "never closed out"; staff get three
# days to record an outcome before it counts against them.
OUTCOME_GRACE = timedelta(days=3)
STAFF_APPOINTMENT_STATUSES = frozenset({"cancelled", "completed", "no_show"})

_STAFF_COLUMNS = """
    m.id, m.tenant_id, m.display_name, m.email_normalized, m.component, m.active,
    m.external_ref, m.title, m.role_code, m.manager_id, m.employment_status,
    m.employment_type, m.started_at, m.ended_at, m.leave_until, m.timezone,
    m.office_location, m.caseload_cap, m.student_facing, m.appointment_types
"""

_APPOINTMENT_COLUMNS = """
    a.id, a.type, a.starts_at, a.ends_at, a.notes, a.status, a.created_at,
    a.modality, a.location, a.booked_via, a.cancelled_at, a.cancel_reason,
    a.rescheduled_to_id, a.outcome_note, a.version, a.staff_member_id,
    a.student_id,
    m.display_name AS staff_name, m.title AS staff_title, m.component AS staff_component,
    m.employment_status AS staff_employment_status, m.email_normalized AS staff_email
"""


class PostgresAdvisingRepository:
    def __init__(
        self,
        engine: AsyncEngine,
        portal: PostgresPortalRepository,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._portal = portal
        self._clock = clock

    # ------------------------------------------------------------------ staff

    async def get_staff_me(self, auth: AuthContext) -> JsonDict:
        _require_staff(auth)
        now = self._clock()
        async with self._engine.connect() as connection:
            me = await self._staff_row(connection, auth.tenant_id, auth.actor_id)
            if me is None:
                raise NotFoundError(
                    "STAFF_IDENTITY_NOT_PROVISIONED",
                    "The authenticated staff identity is not provisioned for this tenant",
                )
            manager = (
                await self._staff_row(connection, auth.tenant_id, str(me["manager_id"]))
                if me.get("manager_id")
                else None
            )
            team = await self._team_rows(connection, auth.tenant_id, auth.actor_id, now)
            reports = [entry for entry in team if entry["level"] == 1]
            caseload = await self._caseload_counts(connection, auth.tenant_id, [auth.actor_id])
            work = await self._work_counts(connection, auth.tenant_id, auth.actor_id, now)
            calendar = await self._calendar_summary(
                connection, auth.tenant_id, me, now, include_pattern=True
            )
            today = await self._appointments_between(
                connection,
                auth.tenant_id,
                auth.actor_id,
                _day_start(now, str(me["timezone"])),
                _day_start(now, str(me["timezone"])) + timedelta(days=1),
            )
            component_summary = (
                await self._component_summary(connection, auth.tenant_id, me, team, now)
                if team
                else None
            )
        mine = caseload.get(auth.actor_id, {})
        primary = int(mine.get("primary_advisor", 0))
        cap = me.get("caseload_cap")
        return {
            "staff": _map_staff(me),
            "manager": _map_staff_brief(manager) if manager else None,
            "directReports": reports,
            # Everyone under this person, direct reports first (level 1), then
            # their reports: a director sees the adviser who left even when
            # that adviser reported to an assistant director.
            "team": team,
            "caseload": {
                "byRole": {role: int(mine.get(role, 0)) for role in ASSIGNMENT_ROLES},
                "primaryAdvisees": primary,
                "cap": cap,
                "utilization": round(primary / cap, 3) if cap else None,
                "overCap": bool(cap and primary > cap),
            },
            "work": work,
            "availability": calendar,
            "appointmentsToday": today,
            "componentSummary": component_summary,
            "generatedAt": _iso(now),
        }

    async def get_staff_caseload(self, auth: AuthContext, role: str | None) -> JsonDict:
        _require_staff(auth)
        if role is not None and role not in ASSIGNMENT_ROLES:
            raise BadRequestError("VALIDATION_ERROR", "Unknown assignment role")
        now = self._clock()
        async with self._engine.connect() as connection:
            me = await self._staff_row(connection, auth.tenant_id, auth.actor_id)
            if me is None:
                raise NotFoundError(
                    "STAFF_IDENTITY_NOT_PROVISIONED",
                    "The authenticated staff identity is not provisioned for this tenant",
                )
            rows = await self._caseload_rows(connection, auth.tenant_id, auth.actor_id, role, now)
        items = [_map_caseload_row(row, now) for row in rows]
        by_role: dict[str, int] = {}
        for item in items:
            by_role[item["role"]] = by_role.get(item["role"], 0) + 1
        primary = by_role.get("primary_advisor", 0)
        cap = me.get("caseload_cap")
        advising = [
            item["advising"]["status"] for item in items if item["role"] == "primary_advisor"
        ]
        return {
            "staff": _map_staff_brief(me),
            "items": items,
            "total": len(items),
            "summary": {
                "byRole": by_role,
                "primaryAdvisees": primary,
                "cap": cap,
                "utilization": round(primary / cap, 3) if cap else None,
                "overCap": bool(cap and primary > cap),
                "advising": {
                    "completed": advising.count("completed"),
                    "scheduled": advising.count("scheduled"),
                    "missed": advising.count("missed"),
                    "none": advising.count("none"),
                },
                "withOpenWork": sum(1 for item in items if item["work"]["open"] > 0),
                "withOverdueWork": sum(1 for item in items if item["work"]["overdue"] > 0),
            },
            "generatedAt": _iso(now),
        }

    async def get_staff_appointments(
        self,
        auth: AuthContext,
        *,
        staff_member_id: str | None,
        window_from: str | None,
        window_to: str | None,
    ) -> JsonDict:
        _require_staff(auth)
        now = self._clock()
        target = staff_member_id or auth.actor_id
        start, end = _window(window_from, window_to, now, default_days=7, past_days=7)
        async with self._engine.connect() as connection:
            member = await self._staff_row(connection, auth.tenant_id, target)
            if member is None:
                raise NotFoundError("STAFF_MEMBER_NOT_FOUND", "The staff member was not found")
            items = await self._appointments_between(connection, auth.tenant_id, target, start, end)
            summary = await self._calendar_summary(
                connection, auth.tenant_id, member, now, include_pattern=False
            )
        return {
            "staff": _map_staff_brief(member),
            "from": _iso(start),
            "to": _iso(end),
            "items": items,
            "total": len(items),
            "counts": {
                "scheduled": sum(1 for item in items if item["status"] == "scheduled"),
                "completed": sum(1 for item in items if item["status"] == "completed"),
                "cancelled": sum(1 for item in items if item["status"] == "cancelled"),
                "noShow": sum(1 for item in items if item["status"] == "no_show"),
                "awaitingOutcome": sum(
                    1
                    for item in items
                    if item["status"] == "scheduled"
                    and datetime.fromisoformat(item["startsAt"].replace("Z", "+00:00")) < now
                ),
            },
            "availability": summary,
        }

    async def update_staff_appointment(
        self,
        auth: AuthContext,
        appointment_id: str,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> JsonDict:
        _require_staff(auth)
        status = str(payload.get("status") or "")
        if status not in STAFF_APPOINTMENT_STATUSES:
            raise BadRequestError(
                "VALIDATION_ERROR", "Status must be cancelled, completed or no_show"
            )
        expected_version = payload.get("expectedVersion")
        async with self._engine.begin() as connection:
            row = await self._appointment_row(connection, auth.tenant_id, appointment_id)
            if row is None:
                raise NotFoundError("APPOINTMENT_NOT_FOUND", "The appointment was not found")
            if not await self._may_manage(connection, auth, row.get("staff_member_id")):
                raise ApiError(
                    403,
                    "APPOINTMENT_NOT_OWNED",
                    "Only the staff member on the appointment or their manager may update it",
                )
            if row["status"] != "scheduled":
                raise ConflictError(
                    "APPOINTMENT_NOT_ACTIVE", "Only a scheduled appointment can be updated"
                )
            if expected_version is not None and int(expected_version) != int(row["version"]):
                raise ConflictError(
                    "APPOINTMENT_VERSION_CONFLICT", "The appointment changed; reload and retry"
                )
            await connection.execute(
                text(
                    """
                    UPDATE student_appointment
                    SET status=CAST(:status AS varchar),
                        cancelled_at=CASE WHEN CAST(:status AS varchar)='cancelled'
                                          THEN NOW() ELSE cancelled_at END,
                        cancel_reason=CASE WHEN CAST(:status AS varchar)='cancelled'
                                           THEN CAST(:reason AS varchar)
                                           ELSE cancel_reason END,
                        outcome_note=COALESCE(CAST(:outcome_note AS text), outcome_note),
                        version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:id
                    """
                ),
                {
                    "status": status,
                    "reason": _bounded(payload.get("reason"), 240),
                    "outcome_note": _bounded(payload.get("outcomeNote"), 2000),
                    "tenant_id": UUID(auth.tenant_id),
                    "id": UUID(str(row["id"])),
                },
            )
            student_auth = _student_scope(auth, str(row["student_id"]))
            await self._portal._insert_audit(
                connection,
                student_auth,
                f"student_appointment.{status}",
                "student_appointment",
                str(row["id"]),
                request_id,
                {"by": "staff", "staffMemberId": auth.actor_id},
            )
            await self._portal._insert_outbox(
                connection,
                student_auth,
                f"student.appointment_{status}.v1",
                "student_appointment",
                str(row["id"]),
                int(row["version"]) + 1,
                request_id,
                {"studentId": str(row["student_id"]), "staffMemberId": auth.actor_id},
            )
            updated = await self._appointment_row(connection, auth.tenant_id, appointment_id)
        assert updated is not None
        return _map_appointment(updated, include_student=True)

    # ---------------------------------------------------------------- student

    async def get_student_advising(self, auth: AuthContext) -> JsonDict:
        _require_student_or_delegate(auth)
        now = self._clock()
        async with self._engine.connect() as connection:
            assignments = await self._student_assignments(connection, auth)
            for entry in assignments:
                member = entry["staff_row"]
                entry["availability"] = await self._calendar_summary(
                    connection, auth.tenant_id, member, now, include_pattern=False
                )
            advising = await self._advising_status(
                connection, auth.tenant_id, auth.student_id, "academic_advising", now
            )
        advisers = [_map_assignment(entry) for entry in assignments]
        primary = next((entry for entry in advisers if entry["role"] == "primary_advisor"), None)
        gaps: list[JsonDict] = []
        if primary is None:
            gaps.append(
                {
                    "code": "no_primary_adviser",
                    "message": "No academic adviser has been assigned to you yet.",
                }
            )
        else:
            status = primary["staff"]["employmentStatus"]
            if status == "departed":
                gaps.append(
                    {
                        "code": "adviser_departed",
                        "message": (
                            f"{primary['staff']['name']} is no longer with the university; "
                            "a new adviser has not been assigned yet."
                        ),
                    }
                )
            elif status == "on_leave":
                until = primary["staff"].get("leaveUntil")
                gaps.append(
                    {
                        "code": "adviser_on_leave",
                        "message": (
                            f"{primary['staff']['name']} is on leave"
                            + (f" until {until}" if until else "")
                            + "."
                        ),
                    }
                )
            elif primary["availability"]["nextOpenSlotAt"] is None:
                gaps.append(
                    {
                        "code": "adviser_no_open_slots",
                        "message": (
                            f"{primary['staff']['name']} has no open appointment slots "
                            "in the next two weeks."
                        ),
                    }
                )
        return {
            "primaryAdviser": primary,
            "advisers": advisers,
            "advising": advising,
            "gaps": gaps,
            "generatedAt": _iso(now),
        }

    async def get_student_availability(
        self,
        auth: AuthContext,
        *,
        appointment_type: str,
        staff_member_id: str | None,
        window_from: str | None,
        window_to: str | None,
    ) -> JsonDict:
        _require_student_or_delegate(auth)
        if appointment_type not in APPOINTMENT_TYPES:
            raise BadRequestError("VALIDATION_ERROR", "Unknown appointment type")
        now = self._clock()
        start, end = _window(window_from, window_to, now, default_days=14, past_days=0)
        start = max(start, now)
        async with self._engine.connect() as connection:
            candidates: list[tuple[JsonDict, str | None]] = []
            if staff_member_id is not None:
                member = await self._staff_row(connection, auth.tenant_id, staff_member_id)
                if member is None:
                    raise NotFoundError("STAFF_MEMBER_NOT_FOUND", "The staff member was not found")
                relationship = await self._relationship(connection, auth, str(member["id"]))
                candidates.append((member, relationship))
            else:
                role = ASSIGNMENT_ROLE_FOR_TYPE.get(appointment_type)
                assigned = None
                if role is not None:
                    assigned = await self._assigned_staff(connection, auth, role)
                if assigned is not None:
                    candidates.append((assigned, role))
                else:
                    for member in await self._staff_offering(
                        connection, auth.tenant_id, appointment_type
                    ):
                        candidates.append((member, None))
            staff_entries: list[JsonDict] = []
            for member, relationship in candidates:
                entry = _map_staff_brief(member)
                entry["relationship"] = relationship
                reason = _unbookable_reason(member, appointment_type)
                slots: list[OpenSlot] = []
                if reason is None:
                    slots = await self._open_slots(
                        connection,
                        auth.tenant_id,
                        member,
                        start,
                        end,
                        now,
                        appointment_type,
                    )
                    if not slots:
                        reason = "no_open_slots"
                entry["reason"] = reason
                entry["slots"] = [_map_slot(slot) for slot in slots]
                entry["nextOpenSlotAt"] = _iso(slots[0].starts_at) if slots else None
                staff_entries.append(entry)
        return {
            "type": appointment_type,
            "from": _iso(start),
            "to": _iso(end),
            "staff": staff_entries,
            "generatedAt": _iso(now),
        }

    async def get_student_appointments(self, auth: AuthContext) -> JsonDict:
        _require_student_or_delegate(auth)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    f"""
                    SELECT {_APPOINTMENT_COLUMNS}
                    FROM student_appointment a
                    LEFT JOIN staff_member m
                      ON m.id=a.staff_member_id AND m.tenant_id=a.tenant_id
                    WHERE a.tenant_id=:tenant_id AND a.student_id=:student_id
                    ORDER BY a.starts_at, a.id
                    """
                ),
                {"tenant_id": UUID(auth.tenant_id), "student_id": UUID(auth.student_id)},
            )
            rows = [dict(row) for row in result.mappings().all()]
        items = [_map_appointment(row) for row in rows]
        return {"items": items, "total": len(items)}

    async def create_student_appointment(
        self,
        auth: AuthContext,
        payload: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        _require_student_or_delegate(auth)
        starts_at = _parse_starts_at(payload.get("startsAt"))
        appointment_type = str(payload.get("type") or "")
        if appointment_type not in APPOINTMENT_TYPES:
            raise BadRequestError("VALIDATION_ERROR", "Unknown appointment type")
        now = self._clock()
        if starts_at <= now:
            raise BadRequestError(
                "APPOINTMENT_MUST_BE_FUTURE", "Appointment time must be in the future"
            )

        async def handler(connection: AsyncConnection) -> JsonDict:
            return await self._book(
                connection,
                auth,
                appointment_type=appointment_type,
                starts_at=starts_at,
                staff_member_id=_optional_str(payload.get("staffMemberId")),
                modality=_optional_str(payload.get("modality")),
                notes=_bounded(payload.get("notes"), 500),
                now=now,
                request_id=request_id,
                exclude_appointment_id=None,
            )

        return await self._portal._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_appointment.create",
            dict(payload),
            201,
            handler,
        )

    async def cancel_student_appointment(
        self,
        auth: AuthContext,
        appointment_id: str,
        payload: Mapping[str, Any],
        request_id: str,
    ) -> JsonDict:
        _require_student_or_delegate(auth)
        async with self._engine.begin() as connection:
            row = await self._appointment_row(
                connection, auth.tenant_id, appointment_id, student_id=auth.student_id
            )
            if row is None:
                raise NotFoundError("APPOINTMENT_NOT_FOUND", "The appointment was not found")
            if row["status"] != "scheduled":
                raise ConflictError(
                    "APPOINTMENT_NOT_ACTIVE", "Only a scheduled appointment can be cancelled"
                )
            await connection.execute(
                text(
                    """
                    UPDATE student_appointment
                    SET status='cancelled', cancelled_at=NOW(), cancel_reason=:reason,
                        version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:id AND student_id=:student_id
                    """
                ),
                {
                    "reason": _bounded(payload.get("reason"), 240),
                    "tenant_id": UUID(auth.tenant_id),
                    "id": UUID(str(row["id"])),
                    "student_id": UUID(auth.student_id),
                },
            )
            await self._portal._insert_audit(
                connection,
                auth,
                "student_appointment.cancelled",
                "student_appointment",
                str(row["id"]),
                request_id,
                {"reason": _bounded(payload.get("reason"), 240)},
            )
            await self._portal._insert_outbox(
                connection,
                auth,
                "student.appointment_cancelled.v1",
                "student_appointment",
                str(row["id"]),
                int(row["version"]) + 1,
                request_id,
                {
                    "studentId": auth.student_id,
                    "staffMemberId": _optional_str(row.get("staff_member_id")),
                },
            )
            updated = await self._appointment_row(connection, auth.tenant_id, appointment_id)
        assert updated is not None
        return _map_appointment(updated)

    async def reschedule_student_appointment(
        self,
        auth: AuthContext,
        appointment_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        request_id: str,
    ) -> JsonDict:
        _require_student_or_delegate(auth)
        starts_at = _parse_starts_at(payload.get("startsAt"))
        now = self._clock()
        if starts_at <= now:
            raise BadRequestError(
                "APPOINTMENT_MUST_BE_FUTURE", "Appointment time must be in the future"
            )

        async def handler(connection: AsyncConnection) -> JsonDict:
            row = await self._appointment_row(
                connection, auth.tenant_id, appointment_id, student_id=auth.student_id
            )
            if row is None:
                raise NotFoundError("APPOINTMENT_NOT_FOUND", "The appointment was not found")
            if row["status"] != "scheduled":
                raise ConflictError(
                    "APPOINTMENT_NOT_ACTIVE", "Only a scheduled appointment can be rescheduled"
                )
            replacement = await self._book(
                connection,
                auth,
                appointment_type=str(row["type"]),
                starts_at=starts_at,
                staff_member_id=_optional_str(payload.get("staffMemberId"))
                or _optional_str(row.get("staff_member_id")),
                modality=_optional_str(payload.get("modality"))
                or _optional_str(row.get("modality")),
                notes=_bounded(payload.get("notes"), 500) or _optional_str(row.get("notes")),
                now=now,
                request_id=request_id,
                exclude_appointment_id=str(row["id"]),
            )
            await connection.execute(
                text(
                    """
                    UPDATE student_appointment
                    SET status='rescheduled', rescheduled_to_id=:new_id,
                        version=version+1, updated_at=NOW()
                    WHERE tenant_id=:tenant_id AND id=:id AND student_id=:student_id
                    """
                ),
                {
                    "new_id": UUID(replacement["id"]),
                    "tenant_id": UUID(auth.tenant_id),
                    "id": UUID(str(row["id"])),
                    "student_id": UUID(auth.student_id),
                },
            )
            await self._portal._insert_audit(
                connection,
                auth,
                "student_appointment.rescheduled",
                "student_appointment",
                str(row["id"]),
                request_id,
                {"rescheduledToId": replacement["id"]},
            )
            replacement["rescheduledFromId"] = str(row["id"])
            return replacement

        return await self._portal._run_idempotent(
            auth,
            idempotency_key,
            request_id,
            "student_appointment.reschedule",
            {"appointmentId": appointment_id, **dict(payload)},
            200,
            handler,
        )

    # ------------------------------------------------------------- internals

    async def _book(
        self,
        connection: AsyncConnection,
        auth: AuthContext,
        *,
        appointment_type: str,
        starts_at: datetime,
        staff_member_id: str | None,
        modality: str | None,
        notes: str | None,
        now: datetime,
        request_id: str,
        exclude_appointment_id: str | None,
    ) -> JsonDict:
        member: JsonDict | None = None
        if staff_member_id is not None:
            member = await self._staff_row(connection, auth.tenant_id, staff_member_id)
            if member is None:
                raise NotFoundError("STAFF_MEMBER_NOT_FOUND", "The staff member was not found")
        else:
            role = ASSIGNMENT_ROLE_FOR_TYPE.get(appointment_type)
            if role is not None:
                member = await self._assigned_staff(connection, auth, role)

        ends_at = starts_at + timedelta(minutes=DEFAULT_SLOT_MINUTES)
        location: str | None = None
        if member is not None:
            reason = _unbookable_reason(member, appointment_type)
            if reason == "departed":
                raise ConflictError(
                    "STAFF_MEMBER_DEPARTED",
                    f"{member['display_name']} is no longer with the university",
                )
            if reason == "on_leave":
                raise ConflictError(
                    "STAFF_MEMBER_ON_LEAVE",
                    f"{member['display_name']} is on leave and cannot be booked",
                )
            if reason == "does_not_offer_type":
                raise ConflictError(
                    "STAFF_DOES_NOT_OFFER_TYPE",
                    f"{member['display_name']} does not take "
                    f"{appointment_type.replace('_', ' ')} appointments",
                )
            rules, time_off, busy = await self._calendar_inputs(
                connection,
                auth.tenant_id,
                str(member["id"]),
                starts_at - timedelta(days=1),
                starts_at + timedelta(days=1),
                exclude_appointment_id=exclude_appointment_id,
            )
            if not rules:
                raise ConflictError(
                    "STAFF_NO_AVAILABILITY",
                    f"{member['display_name']} has no published availability",
                )
            slot = find_slot(
                starts_at,
                rules=rules,
                time_off=time_off,
                appointments=busy,
                timezone=str(member["timezone"]),
                now=now,
                appointment_type=appointment_type,
            )
            if slot is None:
                code = blocking_reason(
                    starts_at,
                    rules=rules,
                    time_off=time_off,
                    appointments=busy,
                    timezone=str(member["timezone"]),
                    appointment_type=appointment_type,
                )
                raise ConflictError(
                    code, _BLOCKING_MESSAGES[code].format(name=member["display_name"])
                )
            ends_at = slot.ends_at
            location = slot.location
            if slot.modality != "either":
                modality = slot.modality
            elif modality not in {"in_person", "virtual"}:
                modality = "in_person"
        elif modality not in {"in_person", "virtual"}:
            modality = None

        appointment_id = str(uuid4())
        try:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO student_appointment (
                      id, tenant_id, student_id, type, starts_at, ends_at, notes, status,
                      staff_member_id, modality, location, booked_via
                    ) SELECT :id, s.tenant_id, s.id, :type, :starts_at, :ends_at, :notes,
                             'scheduled', :staff_member_id, :modality, :location, 'student_portal'
                    FROM student s
                    WHERE s.tenant_id=:tenant_id AND s.id=:student_id
                    RETURNING id
                    """
                ),
                {
                    "id": appointment_id,
                    "type": appointment_type,
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "notes": notes,
                    "staff_member_id": UUID(str(member["id"])) if member else None,
                    "modality": modality,
                    "location": location,
                    "tenant_id": auth.tenant_id,
                    "student_id": auth.student_id,
                },
            )
        except IntegrityError as error:
            raise ConflictError(
                "APPOINTMENT_SLOT_TAKEN", "That time was booked by someone else just now"
            ) from error
        if result.first() is None:
            raise NotFoundError("STUDENT_NOT_FOUND", "The authenticated student was not found")
        await self._portal._insert_audit(
            connection,
            auth,
            "student_appointment.scheduled",
            "student_appointment",
            appointment_id,
            request_id,
            {"type": appointment_type, "staffMemberId": str(member["id"]) if member else None},
        )
        await self._portal._insert_outbox(
            connection,
            auth,
            "student.appointment_scheduled.v1",
            "student_appointment",
            appointment_id,
            1,
            request_id,
            {
                "studentId": auth.student_id,
                "startsAt": _iso(starts_at),
                "staffMemberId": str(member["id"]) if member else None,
            },
        )
        row = await self._appointment_row(connection, auth.tenant_id, appointment_id)
        assert row is not None
        return _map_appointment(row)

    async def _staff_row(
        self, connection: AsyncConnection, tenant_id: str, staff_member_id: str
    ) -> JsonDict | None:
        try:
            identifier = UUID(staff_member_id)
        except ValueError:
            return None
        result = await connection.execute(
            text(
                f"""
                SELECT {_STAFF_COLUMNS}
                FROM staff_member m
                WHERE m.tenant_id=:tenant_id AND m.id=:id
                """
            ),
            {"tenant_id": UUID(tenant_id), "id": identifier},
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _staff_offering(
        self, connection: AsyncConnection, tenant_id: str, appointment_type: str
    ) -> list[JsonDict]:
        result = await connection.execute(
            text(
                f"""
                SELECT {_STAFF_COLUMNS}
                FROM staff_member m
                WHERE m.tenant_id=:tenant_id AND m.active=true AND m.student_facing=true
                  AND m.employment_status='active'
                  AND :type = ANY(m.appointment_types)
                ORDER BY m.display_name, m.id
                LIMIT 25
                """
            ),
            {"tenant_id": UUID(tenant_id), "type": appointment_type},
        )
        return [dict(row) for row in result.mappings().all()]

    async def _assigned_staff(
        self, connection: AsyncConnection, auth: AuthContext, role: str
    ) -> JsonDict | None:
        result = await connection.execute(
            text(
                f"""
                SELECT {_STAFF_COLUMNS}
                FROM student_staff_assignment a
                JOIN staff_member m ON m.id=a.staff_member_id AND m.tenant_id=a.tenant_id
                WHERE a.tenant_id=:tenant_id AND a.student_id=:student_id
                  AND a.role=:role AND a.ended_at IS NULL
                """
            ),
            {
                "tenant_id": UUID(auth.tenant_id),
                "student_id": UUID(auth.student_id),
                "role": role,
            },
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _relationship(
        self, connection: AsyncConnection, auth: AuthContext, staff_member_id: str
    ) -> str | None:
        result = await connection.execute(
            text(
                """
                SELECT role FROM student_staff_assignment
                WHERE tenant_id=:tenant_id AND student_id=:student_id
                  AND staff_member_id=:staff_member_id AND ended_at IS NULL
                ORDER BY CASE WHEN role='primary_advisor' THEN 0 ELSE 1 END
                LIMIT 1
                """
            ),
            {
                "tenant_id": UUID(auth.tenant_id),
                "student_id": UUID(auth.student_id),
                "staff_member_id": UUID(staff_member_id),
            },
        )
        value = result.scalar_one_or_none()
        return str(value) if value is not None else None

    async def _student_assignments(
        self, connection: AsyncConnection, auth: AuthContext
    ) -> list[JsonDict]:
        result = await connection.execute(
            text(
                f"""
                SELECT a.role, a.assigned_at, a.source, {_STAFF_COLUMNS}
                FROM student_staff_assignment a
                JOIN staff_member m ON m.id=a.staff_member_id AND m.tenant_id=a.tenant_id
                WHERE a.tenant_id=:tenant_id AND a.student_id=:student_id AND a.ended_at IS NULL
                ORDER BY CASE a.role WHEN 'primary_advisor' THEN 0 ELSE 1 END, a.role
                """
            ),
            {"tenant_id": UUID(auth.tenant_id), "student_id": UUID(auth.student_id)},
        )
        entries: list[JsonDict] = []
        for row in result.mappings().all():
            data = dict(row)
            entries.append(
                {
                    "role": str(data.pop("role")),
                    "assigned_at": data.pop("assigned_at"),
                    "source": data.pop("source"),
                    "staff_row": data,
                }
            )
        return entries

    async def _appointment_row(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        appointment_id: str,
        *,
        student_id: str | None = None,
    ) -> JsonDict | None:
        try:
            identifier = UUID(appointment_id)
        except ValueError:
            return None
        result = await connection.execute(
            text(
                f"""
                SELECT {_APPOINTMENT_COLUMNS},
                       person.first_name AS student_first_name,
                       person.last_name AS student_last_name,
                       COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                         AS student_preferred_name,
                       student.external_ref AS student_external_ref
                FROM student_appointment a
                JOIN student ON student.id=a.student_id AND student.tenant_id=a.tenant_id
                JOIN person ON person.id=student.person_id AND person.tenant_id=student.tenant_id
                LEFT JOIN student_profile profile
                  ON profile.student_id=student.id AND profile.tenant_id=student.tenant_id
                LEFT JOIN staff_member m ON m.id=a.staff_member_id AND m.tenant_id=a.tenant_id
                WHERE a.tenant_id=:tenant_id AND a.id=:id
                  AND (CAST(:student_id AS uuid) IS NULL OR a.student_id=:student_id)
                FOR UPDATE OF a
                """
            ),
            {
                "tenant_id": UUID(tenant_id),
                "id": identifier,
                "student_id": UUID(student_id) if student_id else None,
            },
        )
        row = result.mappings().first()
        return dict(row) if row is not None else None

    async def _may_manage(
        self, connection: AsyncConnection, auth: AuthContext, staff_member_id: object
    ) -> bool:
        if staff_member_id is None:
            return True
        if str(staff_member_id) == auth.actor_id:
            return True
        result = await connection.execute(
            text(
                """
                SELECT 1 FROM staff_member
                WHERE tenant_id=:tenant_id AND id=:id AND manager_id=:manager_id
                """
            ),
            {
                "tenant_id": UUID(auth.tenant_id),
                "id": UUID(str(staff_member_id)),
                "manager_id": UUID(auth.actor_id),
            },
        )
        return result.first() is not None

    async def _calendar_inputs(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        staff_member_id: str,
        window_start: datetime,
        window_end: datetime,
        *,
        exclude_appointment_id: str | None = None,
    ) -> tuple[list[AvailabilityRule], list[BusyInterval], list[BusyInterval]]:
        inputs = await self._calendar_inputs_many(
            connection,
            tenant_id,
            [staff_member_id],
            window_start,
            window_end,
            exclude_appointment_id=exclude_appointment_id,
        )
        return inputs[staff_member_id]

    async def _calendar_inputs_many(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        staff_member_ids: Iterable[str],
        window_start: datetime,
        window_end: datetime,
        *,
        exclude_appointment_id: str | None = None,
    ) -> dict[str, tuple[list[AvailabilityRule], list[BusyInterval], list[BusyInterval]]]:
        ids = [UUID(value) for value in staff_member_ids]
        out: dict[str, tuple[list[AvailabilityRule], list[BusyInterval], list[BusyInterval]]] = {
            str(value): ([], [], []) for value in ids
        }
        if not ids:
            return out
        params: dict[str, Any] = {"tenant_id": UUID(tenant_id), "ids": ids}
        rule_result = await connection.execute(
            text(
                """
                SELECT staff_member_id, weekday, start_minute, end_minute, slot_minutes,
                       modality, location, appointment_types
                FROM staff_availability
                WHERE tenant_id=:tenant_id AND staff_member_id = ANY(:ids)
                ORDER BY weekday, start_minute
                """
            ),
            params,
        )
        for row in rule_result.mappings().all():
            out[str(row["staff_member_id"])][0].append(
                AvailabilityRule(
                    weekday=int(row["weekday"]),
                    start_minute=int(row["start_minute"]),
                    end_minute=int(row["end_minute"]),
                    slot_minutes=int(row["slot_minutes"]),
                    modality=str(row["modality"]),
                    location=row.get("location"),
                    appointment_types=tuple(row.get("appointment_types") or ()),
                )
            )
        off_result = await connection.execute(
            text(
                """
                SELECT staff_member_id, starts_at, ends_at
                FROM staff_time_off
                WHERE tenant_id=:tenant_id AND staff_member_id = ANY(:ids)
                  AND blocks_bookings=true
                  AND ends_at > :window_start AND starts_at < :window_end
                """
            ),
            {**params, "window_start": window_start, "window_end": window_end},
        )
        for row in off_result.mappings().all():
            out[str(row["staff_member_id"])][1].append(
                BusyInterval(_aware(row["starts_at"]), _aware(row["ends_at"]))
            )
        busy_result = await connection.execute(
            text(
                """
                SELECT staff_member_id, starts_at,
                       COALESCE(ends_at, starts_at + INTERVAL '30 minutes') AS ends_at
                FROM student_appointment
                WHERE tenant_id=:tenant_id AND staff_member_id = ANY(:ids)
                  AND status='scheduled'
                  AND COALESCE(ends_at, starts_at + INTERVAL '30 minutes') > :window_start
                  AND starts_at < :window_end
                  AND (CAST(:exclude AS uuid) IS NULL OR id <> :exclude)
                """
            ),
            {
                **params,
                "window_start": window_start,
                "window_end": window_end,
                "exclude": UUID(exclude_appointment_id) if exclude_appointment_id else None,
            },
        )
        for row in busy_result.mappings().all():
            out[str(row["staff_member_id"])][2].append(
                BusyInterval(_aware(row["starts_at"]), _aware(row["ends_at"]))
            )
        return out

    async def _open_slots(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        member: Mapping[str, Any],
        window_start: datetime,
        window_end: datetime,
        now: datetime,
        appointment_type: str | None,
    ) -> list[OpenSlot]:
        rules, time_off, busy = await self._calendar_inputs(
            connection, tenant_id, str(member["id"]), window_start, window_end
        )
        return derive_open_slots(
            rules=rules,
            time_off=time_off,
            appointments=busy,
            timezone=str(member["timezone"]),
            window_start=window_start,
            window_end=window_end,
            now=now,
            appointment_type=appointment_type,
        )

    async def _calendar_summary(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        member: Mapping[str, Any],
        now: datetime,
        *,
        include_pattern: bool,
    ) -> JsonDict:
        window_end = now + DEFAULT_LOOKAHEAD
        rules, time_off, busy = await self._calendar_inputs(
            connection, tenant_id, str(member["id"]), now, window_end
        )
        bookable = _unbookable_reason(member, None) is None
        slots = (
            derive_open_slots(
                rules=rules,
                time_off=time_off,
                appointments=busy,
                timezone=str(member["timezone"]),
                window_start=now,
                window_end=window_end,
                now=now,
            )
            if bookable
            else []
        )
        summary: JsonDict = {
            "bookable": bookable and bool(rules),
            "reason": None
            if bookable and rules
            else (_unbookable_reason(member, None) or "no_availability"),
            "nextOpenSlotAt": _iso(slots[0].starts_at) if slots else None,
            "openSlotsNext14Days": len(slots),
            "bookedNext14Days": len(busy),
            "timezone": str(member["timezone"]),
        }
        if include_pattern:
            summary["weekly"] = [
                {
                    "weekday": rule.weekday,
                    "startMinute": rule.start_minute,
                    "endMinute": rule.end_minute,
                    "slotMinutes": rule.slot_minutes,
                    "modality": rule.modality,
                    "location": rule.location,
                    "appointmentTypes": list(rule.appointment_types),
                }
                for rule in rules
            ]
            off_result = await connection.execute(
                text(
                    """
                    SELECT id, starts_at, ends_at, kind, note, blocks_bookings
                    FROM staff_time_off
                    WHERE tenant_id=:tenant_id AND staff_member_id=:id
                      AND ends_at > :since
                    ORDER BY starts_at
                    LIMIT 50
                    """
                ),
                {
                    "tenant_id": UUID(tenant_id),
                    "id": UUID(str(member["id"])),
                    "since": now - timedelta(days=30),
                },
            )
            summary["timeOff"] = [
                {
                    "id": str(row["id"]),
                    "startsAt": _iso(row["starts_at"]),
                    "endsAt": _iso(row["ends_at"]),
                    "kind": str(row["kind"]),
                    "note": row.get("note"),
                    "blocksBookings": bool(row["blocks_bookings"]),
                    "current": _aware(row["starts_at"]) <= now < _aware(row["ends_at"]),
                }
                for row in off_result.mappings().all()
            ]
        return summary

    async def _appointments_between(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        staff_member_id: str,
        start: datetime,
        end: datetime,
    ) -> list[JsonDict]:
        result = await connection.execute(
            text(
                f"""
                SELECT {_APPOINTMENT_COLUMNS},
                       person.first_name AS student_first_name,
                       person.last_name AS student_last_name,
                       COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                         AS student_preferred_name,
                       student.external_ref AS student_external_ref
                FROM student_appointment a
                JOIN student ON student.id=a.student_id AND student.tenant_id=a.tenant_id
                JOIN person ON person.id=student.person_id AND person.tenant_id=student.tenant_id
                LEFT JOIN student_profile profile
                  ON profile.student_id=student.id AND profile.tenant_id=student.tenant_id
                LEFT JOIN staff_member m ON m.id=a.staff_member_id AND m.tenant_id=a.tenant_id
                WHERE a.tenant_id=:tenant_id AND a.staff_member_id=:staff_member_id
                  AND a.starts_at >= :start AND a.starts_at < :end
                ORDER BY a.starts_at, a.id
                """
            ),
            {
                "tenant_id": UUID(tenant_id),
                "staff_member_id": UUID(staff_member_id),
                "start": start,
                "end": end,
            },
        )
        return [
            _map_appointment(dict(row), include_student=True) for row in result.mappings().all()
        ]

    async def _team_rows(
        self, connection: AsyncConnection, tenant_id: str, manager_id: str, now: datetime
    ) -> list[JsonDict]:
        result = await connection.execute(
            text(
                f"""
                SELECT {_STAFF_COLUMNS},
                  (SELECT COUNT(*) FROM student_staff_assignment a
                    WHERE a.tenant_id=m.tenant_id AND a.staff_member_id=m.id
                      AND a.role='primary_advisor' AND a.ended_at IS NULL) AS advisees,
                  (SELECT COUNT(*) FROM student_staff_assignment a
                    WHERE a.tenant_id=m.tenant_id AND a.staff_member_id=m.id
                      AND a.ended_at IS NULL) AS assignments,
                  (SELECT COUNT(*) FROM staff_work_item w
                    WHERE w.tenant_id=m.tenant_id AND w.assignee_id=m.id
                      AND w.status NOT IN ('done','cancelled')) AS open_items,
                  (SELECT COUNT(*) FROM staff_work_item w
                    WHERE w.tenant_id=m.tenant_id AND w.assignee_id=m.id
                      AND w.status NOT IN ('done','cancelled')
                      AND w.due_at IS NOT NULL AND w.due_at < :now) AS overdue_items,
                  (SELECT COUNT(*) FROM staff_work_item w
                    WHERE w.tenant_id=m.tenant_id AND w.assignee_id=m.id
                      AND w.status='in_progress' AND w.updated_at < :stale_before) AS stale_items,
                  (SELECT COUNT(*) FROM student_appointment ap
                    WHERE ap.tenant_id=m.tenant_id AND ap.staff_member_id=m.id
                      AND ap.status='scheduled' AND ap.starts_at < :outcome_before)
                    AS awaiting_outcome,
                  tree.level, tree.reports_to
                FROM (
                  WITH RECURSIVE tree AS (
                    SELECT r.id, 1 AS level, r.manager_id AS reports_to
                    FROM staff_member r
                    WHERE r.tenant_id=:tenant_id AND r.manager_id=:manager_id
                    UNION ALL
                    SELECT r.id, tree.level + 1, r.manager_id
                    FROM staff_member r
                    JOIN tree ON r.manager_id=tree.id
                    WHERE r.tenant_id=:tenant_id AND tree.level < 4
                  )
                  SELECT * FROM tree
                ) AS tree
                JOIN staff_member m ON m.id=tree.id AND m.tenant_id=:tenant_id
                ORDER BY tree.level, m.employment_status, m.display_name, m.id
                """
            ),
            {
                "tenant_id": UUID(tenant_id),
                "manager_id": UUID(manager_id),
                "now": now,
                "stale_before": now - STALE_WORK_AFTER,
                "outcome_before": now - OUTCOME_GRACE,
            },
        )
        rows = [dict(row) for row in result.mappings().all()]
        calendars = await self._calendar_inputs_many(
            connection,
            tenant_id,
            [str(row["id"]) for row in rows],
            now,
            now + DEFAULT_LOOKAHEAD,
        )
        team: list[JsonDict] = []
        for row in rows:
            rules, time_off, busy = calendars[str(row["id"])]
            bookable = _unbookable_reason(row, None) is None
            slots = (
                derive_open_slots(
                    rules=rules,
                    time_off=time_off,
                    appointments=busy,
                    timezone=str(row["timezone"]),
                    window_start=now,
                    window_end=now + DEFAULT_LOOKAHEAD,
                    now=now,
                )
                if bookable
                else []
            )
            advisees = int(row["advisees"])
            cap = row.get("caseload_cap")
            entry = _map_staff_brief(row)
            entry.update(
                {
                    "level": int(row["level"]),
                    "reportsTo": str(row["reports_to"]) if row.get("reports_to") else None,
                    "caseload": {
                        "primaryAdvisees": advisees,
                        "assignments": int(row["assignments"]),
                        "cap": cap,
                        "utilization": round(advisees / cap, 3) if cap else None,
                        "overCap": bool(cap and advisees > cap),
                    },
                    "work": {
                        "open": int(row["open_items"]),
                        "overdue": int(row["overdue_items"]),
                        "staleInProgress": int(row["stale_items"]),
                        "appointmentsAwaitingOutcome": int(row["awaiting_outcome"]),
                    },
                    "availability": {
                        "bookable": bookable and bool(rules),
                        "reason": None
                        if bookable and rules
                        else (_unbookable_reason(row, None) or "no_availability"),
                        "nextOpenSlotAt": _iso(slots[0].starts_at) if slots else None,
                        "openSlotsNext14Days": len(slots),
                        "bookedNext14Days": len(busy),
                    },
                    "flags": _team_flags(row, advisees, cap, len(slots)),
                }
            )
            team.append(entry)
        return team

    async def _component_summary(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        me: Mapping[str, Any],
        reports: list[JsonDict],
        now: datetime,
    ) -> JsonDict:
        report_ids = [UUID(entry["id"]) for entry in reports]
        result = await connection.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM student_staff_assignment a
                     JOIN staff_member m ON m.id=a.staff_member_id AND m.tenant_id=a.tenant_id
                    WHERE a.tenant_id=:tenant_id AND a.ended_at IS NULL
                      AND a.role='primary_advisor' AND a.staff_member_id = ANY(:ids)
                      AND m.employment_status='departed') AS with_departed_adviser,
                  (SELECT COUNT(*) FROM student_staff_assignment a
                     JOIN staff_member m ON m.id=a.staff_member_id AND m.tenant_id=a.tenant_id
                    WHERE a.tenant_id=:tenant_id AND a.ended_at IS NULL
                      AND a.role='primary_advisor' AND a.staff_member_id = ANY(:ids)
                      AND m.employment_status='on_leave') AS with_adviser_on_leave,
                  (SELECT COUNT(*) FROM student s
                    WHERE s.tenant_id=:tenant_id
                      AND EXISTS (SELECT 1 FROM admission_offer o
                                   WHERE o.tenant_id=s.tenant_id AND o.student_id=s.id
                                     AND o.status='accepted')
                      AND NOT EXISTS (SELECT 1 FROM student_staff_assignment a
                                       WHERE a.tenant_id=s.tenant_id AND a.student_id=s.id
                                         AND a.role='primary_advisor' AND a.ended_at IS NULL))
                    AS accepted_without_primary_adviser,
                  (SELECT COUNT(*) FROM staff_work_item w
                    WHERE w.tenant_id=:tenant_id AND w.component=:component
                      AND w.status NOT IN ('done','cancelled') AND w.assignee_id IS NULL)
                    AS unassigned_component_items,
                  (SELECT COUNT(*) FROM staff_work_item w
                    WHERE w.tenant_id=:tenant_id AND w.component=:component
                      AND w.status NOT IN ('done','cancelled')
                      AND w.due_at IS NOT NULL AND w.due_at < :now) AS overdue_component_items
                """
            ),
            {
                "tenant_id": UUID(tenant_id),
                "ids": report_ids,
                "component": str(me["component"]),
                "now": now,
            },
        )
        row = result.mappings().one()
        advisees = sum(int(entry["caseload"]["primaryAdvisees"]) for entry in reports)
        cap = sum(int(entry["caseload"]["cap"] or 0) for entry in reports)
        return {
            "component": str(me["component"]),
            "members": len(reports),
            "membersOnLeave": sum(
                1 for entry in reports if entry["employmentStatus"] == "on_leave"
            ),
            "membersDeparted": sum(
                1 for entry in reports if entry["employmentStatus"] == "departed"
            ),
            "membersOverCap": sum(1 for entry in reports if entry["caseload"]["overCap"]),
            "primaryAdvisees": advisees,
            "caseloadCap": cap or None,
            "studentsWithDepartedAdviser": int(row["with_departed_adviser"]),
            "studentsWithAdviserOnLeave": int(row["with_adviser_on_leave"]),
            "acceptedStudentsWithoutPrimaryAdviser": int(row["accepted_without_primary_adviser"]),
            "unassignedComponentItems": int(row["unassigned_component_items"]),
            "overdueComponentItems": int(row["overdue_component_items"]),
        }

    async def _caseload_counts(
        self, connection: AsyncConnection, tenant_id: str, staff_ids: list[str]
    ) -> dict[str, dict[str, int]]:
        result = await connection.execute(
            text(
                """
                SELECT staff_member_id, role, COUNT(*) AS n
                FROM student_staff_assignment
                WHERE tenant_id=:tenant_id AND staff_member_id = ANY(:ids) AND ended_at IS NULL
                GROUP BY staff_member_id, role
                """
            ),
            {"tenant_id": UUID(tenant_id), "ids": [UUID(value) for value in staff_ids]},
        )
        counts: dict[str, dict[str, int]] = {}
        for row in result.mappings().all():
            counts.setdefault(str(row["staff_member_id"]), {})[str(row["role"])] = int(row["n"])
        return counts

    async def _work_counts(
        self, connection: AsyncConnection, tenant_id: str, staff_member_id: str, now: datetime
    ) -> JsonDict:
        result = await connection.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status NOT IN ('done','cancelled')) AS open_items,
                  COUNT(*) FILTER (WHERE status NOT IN ('done','cancelled')
                                     AND due_at IS NOT NULL AND due_at < :now) AS overdue_items,
                  COUNT(*) FILTER (WHERE status NOT IN ('done','cancelled') AND priority='urgent')
                    AS urgent_items,
                  COUNT(*) FILTER (WHERE status NOT IN ('done','cancelled') AND escalated)
                    AS escalated_items,
                  COUNT(*) FILTER (WHERE status='in_progress' AND updated_at < :stale_before)
                    AS stale_items,
                  COUNT(*) FILTER (WHERE status='done' AND completed_at >= :week_ago)
                    AS completed_last_7_days
                FROM staff_work_item
                WHERE tenant_id=:tenant_id AND assignee_id=:staff_member_id
                """
            ),
            {
                "tenant_id": UUID(tenant_id),
                "staff_member_id": UUID(staff_member_id),
                "now": now,
                "stale_before": now - STALE_WORK_AFTER,
                "week_ago": now - timedelta(days=7),
            },
        )
        row = result.mappings().one()
        awaiting = await connection.execute(
            text(
                """
                SELECT COUNT(*) FROM student_appointment
                WHERE tenant_id=:tenant_id AND staff_member_id=:staff_member_id
                  AND status='scheduled' AND starts_at < :outcome_before
                """
            ),
            {
                "tenant_id": UUID(tenant_id),
                "staff_member_id": UUID(staff_member_id),
                "outcome_before": now - OUTCOME_GRACE,
            },
        )
        return {
            "open": int(row["open_items"]),
            "overdue": int(row["overdue_items"]),
            "urgent": int(row["urgent_items"]),
            "escalated": int(row["escalated_items"]),
            "staleInProgress": int(row["stale_items"]),
            "completedLast7Days": int(row["completed_last_7_days"]),
            "appointmentsAwaitingOutcome": int(awaiting.scalar_one()),
        }

    async def _caseload_rows(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        staff_member_id: str,
        role: str | None,
        now: datetime,
    ) -> list[JsonDict]:
        result = await connection.execute(
            text(
                """
                SELECT a.role, a.assigned_at, a.source, a.note,
                       s.id AS student_id, s.external_ref, s.class_year,
                       person.first_name, person.last_name,
                       COALESCE(profile.preferred_name, person.preferred_name, person.first_name)
                         AS preferred_name,
                       offer.status AS offer_status, program.name AS program_name,
                       journey.status AS journey_status,
                       req.total_count, req.completed_count,
                       work.open_count, work.overdue_count,
                       appt.last_completed_at, appt.next_starts_at, appt.next_id,
                       appt.last_missed_at
                FROM student_staff_assignment a
                JOIN student s ON s.id=a.student_id AND s.tenant_id=a.tenant_id
                JOIN person ON person.id=s.person_id AND person.tenant_id=s.tenant_id
                LEFT JOIN student_profile profile
                  ON profile.student_id=s.id AND profile.tenant_id=s.tenant_id
                LEFT JOIN LATERAL (
                  SELECT o.status, o.program_id FROM admission_offer o
                  WHERE o.tenant_id=s.tenant_id AND o.student_id=s.id
                  ORDER BY o.created_at DESC LIMIT 1
                ) AS offer ON true
                LEFT JOIN program ON program.id=offer.program_id AND program.tenant_id=s.tenant_id
                LEFT JOIN LATERAL (
                  SELECT j.status FROM enrollment_journey j
                  WHERE j.tenant_id=s.tenant_id AND j.student_id=s.id
                  ORDER BY j.created_at DESC LIMIT 1
                ) AS journey ON true
                LEFT JOIN LATERAL (
                  SELECT COUNT(r.id)::integer AS total_count,
                         COUNT(r.id) FILTER (
                           WHERE r.status IN ('completed','waived','not_applicable')
                         )::integer AS completed_count
                  FROM enrollment_journey j
                  LEFT JOIN student_requirement r
                    ON r.tenant_id=j.tenant_id AND r.journey_id=j.id AND r.retired_at IS NULL
                  WHERE j.tenant_id=s.tenant_id AND j.student_id=s.id
                ) AS req ON true
                LEFT JOIN LATERAL (
                  SELECT COUNT(*)::integer AS open_count,
                         COUNT(*) FILTER (WHERE w.due_at IS NOT NULL AND w.due_at < :now)::integer
                           AS overdue_count
                  FROM staff_work_item w
                  WHERE w.tenant_id=s.tenant_id AND w.student_id=s.id
                    AND w.status NOT IN ('done','cancelled')
                ) AS work ON true
                LEFT JOIN LATERAL (
                  SELECT MAX(ap.starts_at) FILTER (WHERE ap.status='completed')
                           AS last_completed_at,
                         MAX(ap.starts_at) FILTER (WHERE ap.status='no_show') AS last_missed_at,
                         MIN(ap.starts_at) FILTER (
                           WHERE ap.status='scheduled' AND ap.starts_at >= :now
                         ) AS next_starts_at,
                         (SELECT ap2.id FROM student_appointment ap2
                           WHERE ap2.tenant_id=s.tenant_id AND ap2.student_id=s.id
                             AND ap2.type=:appointment_type AND ap2.status='scheduled'
                             AND ap2.starts_at >= :now
                           ORDER BY ap2.starts_at LIMIT 1) AS next_id
                  FROM student_appointment ap
                  WHERE ap.tenant_id=s.tenant_id AND ap.student_id=s.id
                    AND ap.type=:appointment_type
                ) AS appt ON true
                WHERE a.tenant_id=:tenant_id AND a.staff_member_id=:staff_member_id
                  AND a.ended_at IS NULL
                  AND (CAST(:role AS varchar) IS NULL OR a.role=:role)
                ORDER BY a.role, person.last_name, person.first_name, s.id
                """
            ),
            {
                "tenant_id": UUID(tenant_id),
                "staff_member_id": UUID(staff_member_id),
                "role": role,
                "now": now,
                "appointment_type": "academic_advising",
            },
        )
        return [dict(row) for row in result.mappings().all()]

    async def _advising_status(
        self,
        connection: AsyncConnection,
        tenant_id: str,
        student_id: str,
        appointment_type: str,
        now: datetime,
    ) -> JsonDict:
        result = await connection.execute(
            text(
                f"""
                SELECT {_APPOINTMENT_COLUMNS}
                FROM student_appointment a
                LEFT JOIN staff_member m ON m.id=a.staff_member_id AND m.tenant_id=a.tenant_id
                WHERE a.tenant_id=:tenant_id AND a.student_id=:student_id AND a.type=:type
                ORDER BY a.starts_at, a.id
                """
            ),
            {
                "tenant_id": UUID(tenant_id),
                "student_id": UUID(student_id),
                "type": appointment_type,
            },
        )
        rows = [dict(row) for row in result.mappings().all()]
        completed = [row for row in rows if row["status"] == "completed"]
        upcoming = [
            row for row in rows if row["status"] == "scheduled" and _aware(row["starts_at"]) >= now
        ]
        missed = [row for row in rows if row["status"] == "no_show"]
        if completed:
            status = "completed"
        elif upcoming:
            status = "scheduled"
        elif missed:
            status = "missed"
        else:
            status = "none"
        return {
            "type": appointment_type,
            "status": status,
            "lastCompletedAt": _iso(completed[-1]["starts_at"]) if completed else None,
            "nextAppointment": _map_appointment(upcoming[0]) if upcoming else None,
            "missedCount": len(missed),
        }


# ------------------------------------------------------------------ mapping


_BLOCKING_MESSAGES = {
    "APPOINTMENT_SLOT_TAKEN": "{name} already has an appointment at that time",
    "STAFF_UNAVAILABLE": "{name} is away at that time",
    "OUTSIDE_WORKING_HOURS": "That time is outside {name}'s appointment hours",
    "APPOINTMENT_OFF_GRID": "That time does not match one of {name}'s appointment slots",
}


def _team_flags(row: Mapping[str, Any], advisees: int, cap: object, open_slots: int) -> list[str]:
    flags: list[str] = []
    status = str(row["employment_status"])
    if status == "departed" and advisees:
        flags.append("departed_with_caseload")
    elif status == "departed":
        flags.append("departed")
    if status == "on_leave" and advisees:
        flags.append("on_leave_with_caseload")
    elif status == "on_leave":
        flags.append("on_leave")
    if cap and advisees > int(str(cap)):
        flags.append("over_cap")
    if status == "active" and open_slots == 0 and bool(row.get("student_facing")):
        flags.append("no_open_slots")
    if int(row["stale_items"]) >= 3 or int(row["awaiting_outcome"]) >= 5:
        flags.append("falling_behind")
    if cap and status == "active" and advisees < 0.5 * int(str(cap)) and open_slots >= 40:
        flags.append("spare_capacity")
    return flags


def _unbookable_reason(member: Mapping[str, Any], appointment_type: str | None) -> str | None:
    status = str(member.get("employment_status") or "active")
    if status == "departed" or not bool(member.get("active", True)):
        return "departed"
    if status == "on_leave":
        return "on_leave"
    if appointment_type is not None:
        offered = list(member.get("appointment_types") or [])
        if offered and appointment_type not in offered:
            return "does_not_offer_type"
        if not bool(member.get("student_facing", True)):
            return "does_not_offer_type"
    return None


def _map_staff_brief(row: Mapping[str, Any]) -> JsonDict:
    return {
        "id": str(row["id"]),
        "name": str(row["display_name"]),
        "email": str(row["email_normalized"]),
        "component": str(row["component"]),
        "title": row.get("title"),
        "roleCode": str(row.get("role_code") or "staff"),
        "externalRef": row.get("external_ref"),
        "employmentStatus": str(row.get("employment_status") or "active"),
        "leaveUntil": _iso(row["leave_until"]) if row.get("leave_until") else None,
        "endedAt": _iso(row["ended_at"]) if row.get("ended_at") else None,
    }


def _map_staff(row: Mapping[str, Any]) -> JsonDict:
    brief = _map_staff_brief(row)
    brief.update(
        {
            "active": bool(row["active"]),
            "employmentType": str(row.get("employment_type") or "full_time"),
            "startedAt": _iso(row["started_at"]) if row.get("started_at") else None,
            "timezone": str(row.get("timezone") or "UTC"),
            "officeLocation": row.get("office_location"),
            "caseloadCap": row.get("caseload_cap"),
            "studentFacing": bool(row.get("student_facing", True)),
            "appointmentTypes": list(row.get("appointment_types") or []),
            "managerId": str(row["manager_id"]) if row.get("manager_id") else None,
        }
    )
    return brief


def _map_assignment(entry: Mapping[str, Any]) -> JsonDict:
    staff = _map_staff_brief(entry["staff_row"])
    staff["officeLocation"] = entry["staff_row"].get("office_location")
    return {
        "role": str(entry["role"]),
        "assignedAt": _iso(entry["assigned_at"]),
        "source": str(entry["source"]),
        "staff": staff,
        "availability": entry.get("availability")
        or {"bookable": False, "reason": "no_availability", "nextOpenSlotAt": None},
    }


def _map_slot(slot: OpenSlot) -> JsonDict:
    return {
        "startsAt": _iso(slot.starts_at),
        "endsAt": _iso(slot.ends_at),
        "modality": slot.modality,
        "location": slot.location,
    }


def _map_appointment(row: Mapping[str, Any], *, include_student: bool = False) -> JsonDict:
    staff = None
    if row.get("staff_member_id"):
        staff = {
            "id": str(row["staff_member_id"]),
            "name": str(row.get("staff_name") or ""),
            "title": row.get("staff_title"),
            "component": row.get("staff_component"),
            "email": row.get("staff_email"),
            "employmentStatus": str(row.get("staff_employment_status") or "active"),
        }
    data: JsonDict = {
        "id": str(row["id"]),
        "type": row["type"],
        "startsAt": _iso(row["starts_at"]),
        "endsAt": _iso(row["ends_at"]) if row.get("ends_at") else None,
        "notes": row.get("notes"),
        "status": row["status"],
        "createdAt": _iso(row["created_at"]),
        "modality": row.get("modality"),
        "location": row.get("location"),
        "bookedVia": row.get("booked_via") or "student_portal",
        "cancelledAt": _iso(row["cancelled_at"]) if row.get("cancelled_at") else None,
        "cancelReason": row.get("cancel_reason"),
        "rescheduledToId": str(row["rescheduled_to_id"]) if row.get("rescheduled_to_id") else None,
        "outcomeNote": row.get("outcome_note"),
        "version": int(row.get("version") or 1),
        "staff": staff,
    }
    if include_student:
        data["student"] = {
            "id": str(row["student_id"]),
            "name": (
                f"{row.get('student_first_name', '')} {row.get('student_last_name', '')}"
            ).strip(),
            "preferredName": row.get("student_preferred_name"),
            "externalRef": row.get("student_external_ref"),
        }
    return data


def _map_caseload_row(row: Mapping[str, Any], now: datetime) -> JsonDict:
    next_starts = row.get("next_starts_at")
    if row.get("last_completed_at"):
        advising_status = "completed"
    elif next_starts:
        advising_status = "scheduled"
    elif row.get("last_missed_at"):
        advising_status = "missed"
    else:
        advising_status = "none"
    total = int(row.get("total_count") or 0)
    completed = int(row.get("completed_count") or 0)
    return {
        "role": str(row["role"]),
        "assignedAt": _iso(row["assigned_at"]),
        "source": str(row["source"]),
        "note": row.get("note"),
        "student": {
            "id": str(row["student_id"]),
            "name": f"{row['first_name']} {row['last_name']}",
            "preferredName": str(row["preferred_name"]),
            "externalRef": row.get("external_ref"),
            "classYear": row.get("class_year"),
            "programName": row.get("program_name") or "Program not assigned",
        },
        "offerStatus": row.get("offer_status"),
        "journeyStatus": row.get("journey_status"),
        "requirements": {
            "completed": completed,
            "total": total,
            "percent": round(100 * completed / total) if total else None,
        },
        "advising": {
            "status": advising_status,
            "lastCompletedAt": _iso(row["last_completed_at"])
            if row.get("last_completed_at")
            else None,
            "nextAppointmentAt": _iso(next_starts) if next_starts else None,
            "nextAppointmentId": str(row["next_id"]) if row.get("next_id") else None,
        },
        "work": {
            "open": int(row.get("open_count") or 0),
            "overdue": int(row.get("overdue_count") or 0),
        },
    }


# ------------------------------------------------------------------ helpers


def _require_staff(auth: AuthContext) -> None:
    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_ACCESS_REQUIRED", "This route requires a staff identity")


def _require_student_or_delegate(auth: AuthContext) -> None:
    if auth.actor_type not in {"student", "delegate"}:
        raise ApiError(403, "STUDENT_ACCESS_REQUIRED", "Student access is required")


def _student_scope(auth: AuthContext, student_id: str) -> AuthContext:
    return replace(auth, student_id=student_id)


def _parse_starts_at(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise BadRequestError("VALIDATION_ERROR", "Appointment time is invalid") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_optional(value: str | None, fallback: datetime) -> datetime:
    if value is None or not str(value).strip():
        return fallback
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise BadRequestError(
            "VALIDATION_ERROR", "A window boundary is not a valid date-time"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _window(
    window_from: str | None,
    window_to: str | None,
    now: datetime,
    *,
    default_days: int,
    past_days: int,
) -> tuple[datetime, datetime]:
    start = _parse_optional(window_from, now - timedelta(days=past_days))
    end = _parse_optional(window_to, start + timedelta(days=default_days + past_days))
    if end <= start:
        raise BadRequestError("VALIDATION_ERROR", "The window must end after it starts")
    if end - start > MAX_AVAILABILITY_WINDOW:
        raise BadRequestError("VALIDATION_ERROR", "The window may cover at most 62 days")
    return start, end


def _day_start(now: datetime, timezone: str) -> datetime:
    zone = resolve_zone(timezone)
    local = now.astimezone(zone)
    return datetime(local.year, local.month, local.day, tzinfo=zone).astimezone(UTC)


def _aware(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    raise TypeError("expected a datetime")


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _bounded(value: object, limit: int) -> str | None:
    text_value = _optional_str(value)
    if text_value is None:
        return None
    return text_value[:limit]
