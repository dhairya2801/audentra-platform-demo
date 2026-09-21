"""Tenant-bound v3 reads shared by the portals and Edward.

The SQLite build is seed input only. Every runtime read here uses the same
PostgreSQL transaction and actor as the rest of the product. No model supplies
SQL, tenant identity, or student identity. Policies are retrieved as evidence,
never interpreted as instructions or as permission to perform a write.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError, BadRequestError, NotFoundError
from audentra.domain.document_review import public_review_decision

JsonDict = dict[str, Any]


def account_amount_totals(rows: list[JsonDict], grouping: str) -> list[JsonDict]:
    """Exact cents grouped by term AND lifecycle state; never count future aid as posted."""
    totals: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row["term_id"]), str(row[grouping]))
        totals[key] = totals.get(key, 0) + int(row["amount_cents"])
    return [
        {"term_id": term, grouping: state, "amount_cents": amount}
        for (term, state), amount in sorted(totals.items())
    ]


TABLES = (
    "meta",
    "student",
    "program",
    "term",
    "application",
    "office",
    "staff",
    "staff_absence",
    "staff_availability",
    "staff_calendar_event",
    "assignment",
    "course",
    "section",
    "enrollment",
    "prerequisite",
    "requirement",
    "transfer_credit",
    "sap_evaluation",
    "waitlist",
    "document",
    "document_revision",
    "fund",
    "award",
    "disbursement",
    "payment",
    "ledger",
    "hold",
    "residence",
    "bed",
    "housing",
    "consent",
    "exception",
    "workflow",
    "workflow_step",
    "step_dependency",
    "communication",
    "appointment",
    "event",
    "policy",
    "policy_link",
    "calendar",
    "account_balance",
    "current_load",
    "policy_section",
    "runtime_link",
    "academic_progress",
    "source_issue",
    "rate_catalog",
    "financial_plan_input",
    "financial_scenario",
    "meal_enrollment",
    "insurance_coverage",
    "payment_agreement",
    "payment_installment",
    "loan_terms",
    "award_term",
)
DOMAINS = (
    "overview",
    "academics",
    "account",
    "relationships",
    "documents",
    "history",
    "financial_plan",
)


def _scope(sql: str) -> str:
    """Qualify code-owned SQL through tenant-filtered CTEs, including every join.

    RLS is a second boundary, not a substitute for these predicates. Identifiers
    come solely from this code-owned allowlist; values are always bound.
    """
    used = [name for name in TABLES if re.search(rf"\b(?:FROM|JOIN)\s+{name}\b", sql, re.I)]
    ctes = ",".join(
        f"{name} AS (SELECT * FROM university.{name} WHERE tenant_id=:tenant_id)"  # noqa: S608
        for name in used
    )
    return f"WITH {ctes} {sql}" if ctes else sql


async def _rows(c: AsyncConnection, auth: AuthContext, sql: str, **params: Any) -> list[JsonDict]:
    result = await c.execute(text(_scope(sql)), {**params, "tenant_id": auth.tenant_id})
    return [dict(r) for r in result.mappings()]


def _cutoff(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise BadRequestError(
            "INVALID_TIME_CUTOFF", "Use an ISO timestamp with timezone"
        ) from error


def _with_local_dates(record: JsonDict) -> JsonDict:
    # The claim guard must see the same institutional date conversion as the
    # student. Midnight UTC is often the preceding day in New York.
    stamps = re.findall(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})", json.dumps(record, default=str)
    )
    record["localDates"] = sorted(
        {
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone(ZoneInfo("America/New_York"))
            .date()
            .isoformat()
            for value in stamps
        }
    )
    record["timezone"] = "America/New_York"
    return record


class PostgresUniversityRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.tenant_ids: frozenset[str] = frozenset()
        self.clocks: dict[str, str] = {}

    async def load_tenants(self) -> None:
        clocks: dict[str, str] = {}
        async with self.engine.begin() as c:
            tenants = (await c.execute(text("SELECT id FROM public.tenant"))).scalars().all()
            for tenant in tenants:
                await c.execute(
                    text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                    {"tenant": str(tenant)},
                )
                value = await c.scalar(
                    text(
                        "SELECT value FROM university.meta WHERE tenant_id=:tenant AND key='clock'"
                    ),
                    {"tenant": tenant},
                )
                if value:
                    clocks[str(tenant)] = str(value)
        self.clocks = clocks
        self.tenant_ids = frozenset(clocks)

    def is_enabled(self, auth: AuthContext) -> bool:
        return auth.tenant_id in self.tenant_ids and not auth.is_delegate

    async def enabled(self, auth: AuthContext) -> bool:
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": auth.tenant_id},
            )
            return bool(await _rows(c, auth, "SELECT value FROM meta WHERE key='version'"))

    async def clock(self, auth: AuthContext) -> str:
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": auth.tenant_id},
            )
            found = await _rows(c, auth, "SELECT value FROM meta WHERE key='clock'")
        return str(found[0]["value"]) if found else datetime.now(UTC).isoformat()

    async def _student(self, c: AsyncConnection, auth: AuthContext) -> JsonDict:
        if auth.is_delegate:
            raise ApiError(
                403,
                "UNIVERSITY_SCOPE_REQUIRED",
                "University dossiers require a student or staff session",
            )
        found = await _rows(
            c,
            auth,
            (
                "SELECT s.*,p.name AS program_name,p.degree_credits,p.department "
                "FROM student s JOIN program p ON p.id=s.program_id WHERE "
                "s.id=:sid"
            ),
            sid=auth.student_id,
        )
        if not found:
            raise NotFoundError(
                "UNIVERSITY_STUDENT_NOT_FOUND", "Student not found in this university"
            )
        # Preference writes retain their existing canonical domain and guardrails.
        profile = (
            (
                await c.execute(
                    text(
                        (
                            "SELECT "
                            "preferred_name,pronouns,mobile_phone,communication_preference "
                            "FROM public.student_profile WHERE tenant_id=:tenant AND "
                            "student_id=:sid"
                        )
                    ),
                    {"tenant": auth.tenant_id, "sid": auth.student_id},
                )
            )
            .mappings()
            .first()
        )
        return {**found[0], **(dict(profile) if profile else {})}

    async def record(
        self,
        auth: AuthContext,
        domain: str = "overview",
        *,
        known_at: str | None = None,
        effective_at: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> JsonDict:
        if domain == "financial_plan":
            from .financial_plan_repository import FinancialPlanService

            return await FinancialPlanService(self).read(auth)
        if entity_type is not None and entity_type not in {
            "application",
            "appointment",
            "disbursement",
            "document",
            "enrollment",
            "hold",
            "housing",
            "ledger",
            "payment",
            "sap_evaluation",
            "transfer_credit",
            "waitlist",
            "workflow",
            "profile",
            "consent",
            "exception",
        }:
            raise BadRequestError(
                "INVALID_UNIVERSITY_EVENT_TYPE",
                "Choose a documented event type; external grade corrections use transfer_credit, "
                "institutional course grades use enrollment. Omit the filter to search all types.",
            )
        if domain not in DOMAINS:
            raise BadRequestError(
                "INVALID_UNIVERSITY_DOMAIN", "Choose a documented university domain"
            )
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": auth.tenant_id},
            )
            student = await self._student(c, auth)
            meta = {
                r["key"]: r["value"]
                for r in await _rows(
                    c, auth, "SELECT key,value FROM meta WHERE key IN ('clock','version','seed')"
                )
            }
            now = str(meta["clock"])
            result: JsonDict = {
                "student": student,
                "domain": domain,
                "snapshotAt": now,
                "build": meta,
                "semantics": (
                    "Current snapshot. Only history events use both effective and "
                    "recorded cutoffs. Money fields ending in _cents are integer USD "
                    "cents."
                ),
            }

            async def read(sql: str, **params: Any) -> list[JsonDict]:
                return await _rows(c, auth, sql, sid=auth.student_id, now=now, **params)

            if domain == "overview":
                result["applications"] = await read(
                    "SELECT * FROM application WHERE student_id=:sid ORDER BY submitted_at DESC"
                )
                result["holds"] = await read(
                    "SELECT h.*,o.name AS office_name,o.email FROM hold h JOIN office "
                    "o ON o.id=h.office_id WHERE student_id=:sid ORDER BY placed_at "
                    "DESC"
                )
                result["loads"] = await read("SELECT * FROM current_load WHERE student_id=:sid")
                result["balances"] = await read(
                    "SELECT * FROM account_balance WHERE student_id=:sid"
                )
                result["deadlines"] = await read(
                    (
                        "SELECT * FROM calendar WHERE starts_at>=:now AND (audience IN "
                        "('student','all','new_students') OR (audience='international' AND"
                        " :international) OR (audience='transfer' AND :transfer) OR "
                        "(audience='on_campus' AND EXISTS(SELECT 1 FROM housing WHERE "
                        "student_id=:sid AND status IN ('assigned','waitlisted') AND "
                        "ends_at IS NULL))) ORDER BY starts_at LIMIT 15"
                    ),
                    international=student["residency"] == "international",
                    transfer=student["admit_type"] == "transfer",
                )
            elif domain == "academics":
                result["attempts"] = await read(
                    "SELECT e.*,c.id AS "
                    "course_id,c.code,c.title,c.credits,s.term_id,s.weekday,s.start_minute,s.end_minute,s.room,s.modality"
                    " FROM enrollment e JOIN section s ON s.id=e.section_id JOIN "
                    "course c ON c.id=s.course_id WHERE e.student_id=:sid ORDER BY "
                    "s.term_id DESC,c.code"
                )
                result["transfers"] = await read(
                    "SELECT t.*,c.code,c.title FROM transfer_credit t JOIN course c ON"
                    " c.id=t.course_id WHERE student_id=:sid"
                )
                result["sap"] = await read(
                    "SELECT * FROM sap_evaluation WHERE student_id=:sid ORDER BY evaluated_at DESC"
                )
                result["requirements"] = await read(
                    (
                        "SELECT r.*,c.code,c.title,c.level,c.description AS "
                        "course_description FROM requirement r LEFT JOIN course c ON "
                        "c.id=r.course_id WHERE program_id=:program ORDER BY "
                        "recommended_term,r.id"
                    ),
                    program=student["program_id"],
                )
                result["programs"] = await read("SELECT * FROM program ORDER BY name")
                result["prerequisites"] = await read(
                    (
                        "SELECT p.*,c.code,req.code AS required_code FROM prerequisite p "
                        "JOIN course c ON c.id=p.course_id JOIN course req ON "
                        "req.id=p.required_course_id WHERE EXISTS(SELECT 1 FROM "
                        "requirement r WHERE r.course_id=p.course_id AND "
                        "r.program_id=:program)"
                    ),
                    program=student["program_id"],
                )
                result["loads"] = await read("SELECT * FROM current_load WHERE student_id=:sid")
                result["waitlists"] = await read(
                    "SELECT w.*,c.code FROM waitlist w JOIN section s ON "
                    "s.id=w.section_id JOIN course c ON c.id=s.course_id WHERE "
                    "student_id=:sid"
                )
                result["exceptions"] = await read("SELECT * FROM exception WHERE student_id=:sid")
                result["policyReferences"] = {
                    "courseWaitlist": "registration-policy",
                    "courseDrop": "withdrawal-and-refund-policy",
                    "internationalCourseLoad": "f1-enrollment-and-reduced-course-load",
                    "courseLoad": "course-load-and-overload",
                }
                result["courseImpacts"] = []
                for attempt in result["attempts"]:
                    if attempt["status"] != "enrolled":
                        continue
                    load = next(
                        (r for r in result["loads"] if r["term_id"] == attempt["term_id"]), None
                    )
                    if load is None:
                        continue
                    registered = sum(
                        r["credits"]
                        for r in result["attempts"]
                        if r["status"] == "enrolled" and r["term_id"] == attempt["term_id"]
                    )
                    after = registered - attempt["credits"]
                    approvals = [
                        r
                        for r in result["exceptions"]
                        if r["status"] == "approved"
                        and r["starts_at"] <= now < r["ends_at"]
                        and r.get("term_id") == attempt["term_id"]
                    ]
                    result["courseImpacts"].append(
                        {
                            "course": attempt["code"],
                            "term": attempt["term_id"],
                            "beforeCredits": registered,
                            "afterCredits": after,
                            "belowStandardFullTime": after < 12,
                            "requiresPriorDSOApproval": student["residency"] == "international"
                            and after < 12
                            and not any(
                                r["kind"] == "reduced_course_load"
                                and r.get("minimum_credits") is not None
                                and after >= r["minimum_credits"]
                                for r in approvals
                            ),
                            "reducedLoadApprovals": [
                                {
                                    "id": r["id"],
                                    "policyId": r["policy_id"],
                                    "office": r["office_id"],
                                    "minimumCredits": r.get("minimum_credits"),
                                    "withinApprovedFloor": after >= r["minimum_credits"]
                                    if r.get("minimum_credits") is not None
                                    else None,
                                }
                                for r in approvals
                                if r["kind"] == "reduced_course_load"
                            ],
                            "aidEligibility": (
                                "Separate review required; ISS approval "
                                "does not preserve aid automatically."
                            ),
                            "execution": "Read-only counterfactual; no enrollment change was made.",
                        }
                    )
                result["limitations"] = [
                    "Unresolved core/elective distributions cannot certify graduation.",
                    (
                        "SAP counts Aster attempts; transfer-inclusive SAP, repeat "
                        "forgiveness and successful appeal/probation are not modeled."
                    ),
                ]
            elif domain == "account":
                # Student-visible progress deliberately excludes workflow evidence,
                # staff notes, communications and internal identifiers. Submission
                # completion does not imply that these downstream stages completed.
                result["financialAidRequirements"] = await read(
                    "SELECT d.title,r.status,d.responsible_office AS office_name, "
                    "CASE WHEN r.status IN ('ready','in_progress','rejected') "
                    "THEN 'Student submission is not complete; check required evidence' "
                    "WHEN r.status='under_review' THEN 'Awaiting university review' "
                    "WHEN r.status IN ('completed','waived','not_applicable') "
                    "THEN 'No outstanding student submission for this requirement' "
                    "ELSE 'Check prerequisite or requirement details' END AS meaning "
                    "FROM public.student_requirement r JOIN public.enrollment_journey j "
                    "ON j.tenant_id=r.tenant_id AND j.id=r.journey_id "
                    "JOIN public.requirement_definition_version d "
                    "ON d.tenant_id=r.tenant_id AND d.id=r.requirement_definition_version_id "
                    "WHERE r.tenant_id=:tenant_id AND j.student_id=CAST(:sid AS uuid) "
                    "AND r.retired_at IS NULL AND d.code='financial_aid_verification' "
                    "ORDER BY r.id"
                )
                result["serviceProgress"] = await read(
                    "SELECT w.title AS case_title,w.status AS case_status,step.title,"
                    "step.status,o.name AS office_name FROM workflow w "
                    "JOIN workflow_step step ON step.workflow_id=w.id "
                    "JOIN office o ON o.id=step.office_id "
                    "WHERE w.student_id=:sid AND w.kind='verification' "
                    "AND w.status NOT IN ('resolved','cancelled') ORDER BY step.id"
                )
                for key, table, order in [
                    ("ledger", "ledger", "posted_at"),
                    ("payments", "payment", "submitted_at"),
                    ("balances", "account_balance", "term_id"),
                    ("holds", "hold", "placed_at"),
                ]:
                    result[key] = await read(
                        f"SELECT * FROM {table} WHERE student_id=:sid ORDER BY {order},student_id"  # noqa: S608 — fixed tuple above
                    )
                result["awards"] = await read(
                    "SELECT a.*,f.name,f.source,f.posts_to_account FROM award a JOIN "
                    "fund f ON f.id=a.fund_id WHERE student_id=:sid ORDER BY f.name"
                )
                result["disbursements"] = await read(
                    "SELECT d.*,a.fund_id,f.name FROM disbursement d JOIN award a ON "
                    "a.id=d.award_id JOIN fund f ON f.id=a.fund_id WHERE "
                    "a.student_id=:sid ORDER BY scheduled_at,d.id"
                )
                result["sap"] = await read(
                    "SELECT * FROM sap_evaluation WHERE student_id=:sid ORDER BY evaluated_at DESC"
                )
                result["totals"] = {
                    "ledgerByTermAndKind": account_amount_totals(result["ledger"], "kind"),
                    "aidByTermAndStatus": account_amount_totals(result["disbursements"], "status"),
                    "paymentsByTermAndStatus": account_amount_totals(result["payments"], "status"),
                }
                result["semantics"] = (
                    "Balance is SUM(posted ledger). Negative means credit owed, not "
                    "refund settled. A refund ledger entry records accounting, not bank delivery; "
                    "refund settlement is not recorded. Annual accepted aid, future installments, "
                    "pending/failed payments and employment awards do not reduce "
                    "posted balance. Term charges are not annual COA."
                )
                result["refundSettlementStatus"] = "not_recorded"
                result["balanceAfterAnticipatedAid"] = [
                    {
                        "term_id": row["term_id"],
                        "postedBalanceCents": row["balance_cents"],
                        "anticipatedAidCents": anticipated,
                        "estimatedRemainingCents": max(0, row["balance_cents"] - anticipated),
                    }
                    for row in result["balances"]
                    for anticipated in [
                        sum(
                            d["amount_cents"]
                            for d in result["disbursements"]
                            if d["term_id"] == row["term_id"]
                            and d["status"] in ("scheduled", "held")
                        )
                    ]
                ]
            elif domain == "relationships":
                result["assignments"] = await read(
                    "SELECT a.*,s.name,s.email,s.office_id,s.status,o.name AS "
                    "office_name,o.location FROM assignment a JOIN staff s ON "
                    "s.id=a.staff_id JOIN office o ON o.id=s.office_id WHERE "
                    "student_id=:sid ORDER BY a.role,a.starts_at"
                )
                result["coverage"] = await read(
                    "SELECT a.*,s.name AS covering_name,s.email AS covering_email FROM"
                    " staff_absence a JOIN staff s ON s.id=a.covering_staff_id JOIN "
                    "assignment x ON x.staff_id=a.staff_id WHERE x.student_id=:sid AND"
                    " x.ends_at IS NULL AND a.starts_at<=:now AND a.ends_at>:now"
                )
                for key, table in [
                    ("consents", "consent"),
                    ("exceptions", "exception"),
                    ("appointments", "appointment"),
                ]:
                    result[key] = await read(f"SELECT * FROM {table} WHERE student_id=:sid")  # noqa: S608 — fixed tuple above
                result["housing"] = await read(
                    "SELECT h.*,r.name AS residence_name,b.room,b.accessible FROM "
                    "housing h LEFT JOIN bed b ON b.id=h.bed_id LEFT JOIN residence r "
                    "ON r.id=b.residence_id WHERE student_id=:sid"
                )
                result["communications"] = await read(
                    (
                        "SELECT * FROM communication WHERE student_id=:sid AND (:staff OR "
                        "(audience='student' AND delivery='delivered')) ORDER BY sent_at "
                        "DESC"
                    ),
                    staff=auth.actor_type == "staff",
                )
                inbox = await c.execute(
                    text("""
                    SELECT id,subject,body,sender_name,kind,href,sent_at,read_at
                    FROM public.student_message
                    WHERE tenant_id=CAST(:tenant AS uuid) AND student_id=CAST(:student AS uuid)
                    ORDER BY sent_at DESC,id LIMIT 50
                    """),
                    {"tenant": auth.tenant_id, "student": auth.student_id},
                )
                result["portalInbox"] = [dict(row) for row in inbox.mappings()]
                result["portalInboxSemantics"] = (
                    "Latest 50 canonical portal inbox messages, using operational sent_at "
                    "timestamps rather than the fixed institutional snapshot clock. "
                    "A portal message is delivered to this inbox; it does not prove external "
                    "email delivery, case completion, or satisfaction of a required step. "
                    "Internal staff notes are excluded."
                )
                result["portalAuthorizations"] = [
                    dict(r)
                    for r in (
                        await c.execute(
                            text(
                                "SELECT d.id,d.full_name,d.scopes,d.active,a.status AS "
                                "authorization_status,d.updated_at "
                                "FROM public.ferpa_delegate d "
                                "JOIN public.ferpa_authorization a "
                                "ON a.id=d.authorization_id AND "
                                "a.tenant_id=d.tenant_id WHERE d.tenant_id=:tenant AND "
                                "d.student_id=:sid"
                            ),
                            {"tenant": auth.tenant_id, "sid": auth.student_id},
                        )
                    ).mappings()
                ]
                result["portalMessages"] = [
                    dict(r)
                    for r in (
                        await c.execute(
                            text(
                                "SELECT id,subject,body,sender_name,sent_at,read_at FROM "
                                "public.student_message WHERE tenant_id=:tenant AND "
                                "student_id=:sid ORDER BY sent_at DESC LIMIT 20"
                            ),
                            {"tenant": auth.tenant_id, "sid": auth.student_id},
                        )
                    ).mappings()
                ]
                if auth.actor_type == "staff":
                    result["workflows"] = await read(
                        "SELECT w.*,s.name AS owner_name FROM workflow w JOIN staff s ON "
                        "s.id=w.owner_id WHERE student_id=:sid"
                    )
                    result["steps"] = await read(
                        "SELECT step.* FROM workflow_step step JOIN workflow w ON "
                        "w.id=step.workflow_id WHERE w.student_id=:sid"
                    )
                    result["dependencies"] = await read(
                        "SELECT d.* FROM step_dependency d JOIN workflow_step step ON "
                        "step.id=d.step_id JOIN workflow w ON w.id=step.workflow_id WHERE "
                        "w.student_id=:sid"
                    )
            elif domain == "documents":
                result["documents"] = await read(
                    "SELECT d.*,o.name AS office_name FROM document d JOIN office o ON"
                    " o.id=d.office_id WHERE student_id=:sid ORDER BY category"
                )
                result["revisions"] = await read(
                    "SELECT r.* FROM document_revision r JOIN document d ON "
                    "d.id=r.document_id WHERE d.student_id=:sid ORDER BY recorded_at "
                    "DESC"
                )
                decisions = await c.execute(
                    text("""
                    SELECT id, document_id, decision, reason_code, reason_label,
                      student_message, reviewer_display_name, source, decided_at
                    FROM public.document_review_decision
                    WHERE tenant_id=CAST(:tenant AS uuid) AND student_id=CAST(:student AS uuid)
                    ORDER BY decided_at,id
                """),
                    {"tenant": auth.tenant_id, "student": auth.student_id},
                )
                result["reviewDecisionSemantics"] = (
                    "staff_review records an official decision on one submission. "
                    "legacy_backfill is a prior portal status snapshot without an original "
                    "reviewer or guidance in that snapshot. Consult the university document "
                    "revisions for any separately recorded reasons; a snapshot does not "
                    "supersede revision evidence."
                )
                result["reviewDecisions"] = [
                    {**public_review_decision(dict(row)), "documentId": str(row["document_id"])}
                    for row in decisions.mappings()
                ]
            elif domain == "history":
                known_at = _cutoff(known_at)
                effective_at = _cutoff(effective_at)
                result["entityType"] = entity_type
                result["entityId"] = entity_id
                result["knownAt"] = known_at or now
                result["effectiveAt"] = effective_at or now
                count = await read(
                    "SELECT count(*)::integer AS count FROM event WHERE student_id=:sid "
                    "AND recorded_at<=:known AND effective_at<=:effective "
                    "AND (:staff OR visibility='student') "
                    "AND (CAST(:kind AS text) IS NULL OR entity_type=:kind) "
                    "AND (CAST(:entity AS text) IS NULL OR entity_id=:entity)",
                    known=known_at or now,
                    effective=effective_at or now,
                    staff=auth.actor_type == "staff",
                    kind=entity_type,
                    entity=entity_id,
                )
                result["totalMatchingEvents"] = count[0]["count"]
                result["hasMoreEvents"] = count[0]["count"] > 100
                result["events"] = await read(
                    (
                        "SELECT * FROM event WHERE student_id=:sid AND recorded_at<=:known"
                        " AND effective_at<=:effective AND (:staff OR "
                        "visibility='student') "
                        "AND (CAST(:kind AS text) IS NULL OR entity_type=:kind) "
                        "AND (CAST(:entity AS text) IS NULL OR entity_id=:entity) "
                        "ORDER BY recorded_at DESC,effective_at "
                        "DESC,id LIMIT 100"
                    ),
                    known=known_at or now,
                    effective=effective_at or now,
                    staff=auth.actor_type == "staff",
                    kind=entity_type,
                    entity=entity_id,
                )
                result["semantics"] = (
                    "Events bounded by BOTH recorded_at and effective_at. Student "
                    "header is current identity only. This is not reconstruction of "
                    "every historical snapshot. At most 100 events; newest recorded "
                    "first."
                )
            return _with_local_dates(result)

    async def operations(self, auth: AuthContext) -> JsonDict:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": auth.tenant_id},
            )
            identity = await _rows(
                c,
                auth,
                (
                    "SELECT s.* FROM staff s JOIN runtime_link l ON l.world_id=s.id "
                    "AND l.kind='staff' WHERE l.runtime_id=:actor"
                ),
                actor=auth.actor_id,
            )
            if not identity:
                raise ApiError(403, "STAFF_REQUIRED", "No university staff identity")
            sid = identity[0]["id"]
            result: JsonDict = {"staff": identity[0]}
            for key, sql in {
                "availability": (
                    "SELECT * FROM staff_availability WHERE staff_id=:sid "
                    "ORDER BY weekday,start_minute"
                ),
                "calendar": (
                    "SELECT * FROM staff_calendar_event WHERE staff_id=:sid ORDER BY starts_at"
                ),
                "absences": (
                    "SELECT a.*,s.name AS covering_name FROM staff_absence a JOIN "
                    "staff s ON s.id=a.covering_staff_id WHERE staff_id=:sid ORDER BY "
                    "starts_at"
                ),
                "appointments": (
                    "SELECT a.*,s.name AS student_name,s.external_ref FROM appointment"
                    " a JOIN student s ON s.id=a.student_id WHERE staff_id=:sid ORDER "
                    "BY starts_at DESC LIMIT 50"
                ),
                "cases": (
                    "SELECT w.*,s.name AS student_name,s.external_ref FROM workflow w "
                    "JOIN student s ON s.id=w.student_id WHERE owner_id=:sid ORDER BY "
                    "due_at LIMIT 100"
                ),
                "caseload": (
                    "SELECT role,count(*)::integer AS count FROM assignment WHERE "
                    "staff_id=:sid AND ends_at IS NULL GROUP BY role"
                ),
                "offices": "SELECT * FROM office ORDER BY name",
            }.items():
                result[key] = await _rows(c, auth, sql, sid=sid)
            result["tasks"] = [
                dict(r)
                for r in (
                    await c.execute(
                        text(
                            "SELECT id,title,status,priority,due_at FROM public.staff_work_item "
                            "WHERE tenant_id=:tenant AND assignee_id=:actor "
                            "AND status NOT IN ('done','cancelled') "
                            "ORDER BY due_at NULLS LAST LIMIT 30"
                        ),
                        {"tenant": auth.tenant_id, "actor": auth.actor_id},
                    )
                ).mappings()
            ]
            result["taskCounts"] = [
                dict(r)
                for r in (
                    await c.execute(
                        text(
                            "SELECT status,count(*)::integer AS count FROM public.staff_work_item "
                            "WHERE tenant_id=:tenant AND assignee_id=:actor GROUP BY status"
                        ),
                        {"tenant": auth.tenant_id, "actor": auth.actor_id},
                    )
                ).mappings()
            ]
            result["snapshotAt"] = (
                await _rows(c, auth, "SELECT value FROM meta WHERE key='clock'")
            )[0]["value"]
            return _with_local_dates(result)

    async def policies(
        self, auth: AuthContext, query: str, *, at: str | None = None, known_at: str | None = None
    ) -> JsonDict:
        at, known_at = _cutoff(at), _cutoff(known_at)
        if auth.is_delegate:
            raise ApiError(403, "UNIVERSITY_SCOPE_REQUIRED", "Use student or staff access")
        if not query.strip() or len(query) > 500:
            raise BadRequestError("INVALID_SEARCH", "Search must contain 1-500 characters")
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": auth.tenant_id},
            )
            meta = await _rows(c, auth, "SELECT value FROM meta WHERE key='clock'")
            now = str(meta[0]["value"])
            # OR terms improve recall for long natural-language questions. Rank
            # passages, not whole documents; exact code/title boosts preserve
            # institutional vocabulary. Metadata gates run BEFORE ranking.
            words = re.findall(r"[A-Za-z0-9]+", query.lower())[:30]
            stop = {
                "i",
                "my",
                "the",
                "a",
                "an",
                "is",
                "are",
                "can",
                "what",
                "how",
                "do",
                "does",
                "for",
                "to",
                "of",
                "and",
                "with",
                "about",
                "me",
                "it",
                "if",
                "this",
            }
            aliases = {
                "bill": "billing payment",
                "money": "payment disbursement",
                "parent": "ferpa consent",
                "drop": "registration withdrawal",
                "leave": "leave coverage",
                "late": "deadline extension",
                "visa": "f1 international",
                "room": "housing accommodation",
            }
            words = [w for w in words if w not in stop]
            expanded = " ".join([*words, *(aliases[w] for w in words if w in aliases)])
            terms = " | ".join(re.findall(r"[a-z0-9]+", expanded)) or "university"
            # Disambiguate a course seat offer from an admission/award offer
            # using the tenant's actual course catalog, never a persona fixture.
            course_offer = False
            if {"offer", "waitlist", "seat"} & set(words):
                courses = await _rows(c, auth, "SELECT code FROM course")
                course_offer = any(
                    re.search(r"\b" + re.escape(row["code"]) + r"\b", query, re.IGNORECASE)
                    for row in courses
                )
            contextual_code = "registration-policy" if course_offer else ""
            found = await _rows(
                c,
                auth,
                (
                    "SELECT "
                    "p.id,p.code,p.version,p.title,p.owner_id,p.audience,p.authority,p.effective_from,p.effective_until,p.published_at,p.source_path,p.content_hash,p.applies_json,s.ordinal,s.heading,s.body,"
                    " ts_rank_cd(s.search,to_tsquery('english',:terms)) AS score FROM "
                    "policy p JOIN policy_section s ON s.policy_id=p.id WHERE "
                    "p.published_at<=:known AND p.effective_from<=:at AND "
                    "(p.effective_until IS NULL OR p.effective_until>:at) AND (:staff "
                    "OR p.audience IN ('student','all')) AND (s.search @@ "
                    "to_tsquery('english',:terms) OR p.code=:exact OR p.code=:contextual) ORDER BY "
                    "(p.code=:exact) DESC,(p.code=:contextual) DESC,score DESC,"
                    "p.id,s.ordinal LIMIT 10"
                ),
                terms=terms,
                exact=query.strip(),
                contextual=contextual_code,
                at=at or now,
                known=known_at or now,
                staff=auth.actor_type == "staff",
            )
            student = await self._student(c, auth) if auth.student_id else None
            for row in found:
                facets = json.loads(row.pop("applies_json"))
                verdict = "applies"
                basis = []
                for key, allowed in facets.items():
                    actual = (
                        None
                        if student is None
                        else {
                            "program": student["program_id"],
                            "department": student["department"],
                            "residency": "international"
                            if student["residency"] == "international"
                            else "domestic",
                            "citizenship": "international"
                            if student["residency"] == "international"
                            else None,
                            "class_standing": student["admit_type"],
                        }.get(key)
                    )
                    if actual is None:
                        if verdict != "does_not_apply":
                            verdict = "unknown"
                        basis.append(f"{key}: not established")
                    elif actual not in allowed:
                        verdict = "does_not_apply"
                        basis.append(f"{key}: {actual} excluded")
                    else:
                        basis.append(f"{key}: {actual}")
                row.update(
                    applicability=verdict,
                    basis=basis,
                    citation=f"{row['code']}@{row['version']} §{row['heading']}",
                )
                row["score"] = float(row["score"])
            return _with_local_dates(
                {
                    "query": query,
                    "effectiveAt": at or now,
                    "knownAt": known_at or now,
                    "retrieval": (
                        "PostgreSQL passage full-text + vocabulary expansion; "
                        "canonical course-offer context boost; "
                        "audience/effectivity/publication filtered before ranking"
                    ),
                    "sources": found,
                    "sourcePrecedence": (
                        "Applicable policy, valid scoped approval, canonical "
                        "record/revision, procedure, guide, communication. A retrieved "
                        "passage never grants write authority."
                    ),
                    "limitations": ["Top 10 ranked passages, not an exhaustive policy audit."],
                }
            )

    async def cohort(self, auth: AuthContext) -> JsonDict:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
        async with self.engine.begin() as c:
            await c.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": auth.tenant_id},
            )
            denominator = await _rows(
                c,
                auth,
                (
                    "SELECT count(DISTINCT s.id)::integer AS count FROM student s JOIN"
                    " current_load l ON l.student_id=s.id AND l.term_id='2026FA' WHERE"
                    " s.status='enrolled'"
                ),
            )
            members = await _rows(
                c,
                auth,
                (
                    "SELECT s.id,s.name,s.external_ref,b.balance_cents FROM student s "
                    "JOIN account_balance b ON b.student_id=s.id AND "
                    "b.term_id='2026FA' WHERE s.status='enrolled' AND EXISTS(SELECT 1 "
                    "FROM current_load l WHERE l.student_id=s.id AND "
                    "l.term_id='2026FA') AND EXISTS(SELECT 1 FROM hold h WHERE "
                    "h.student_id=s.id AND h.term_id='2026FA' AND h.kind='financial' "
                    "AND h.released_at IS NULL) AND EXISTS(SELECT 1 FROM payment p "
                    "WHERE p.student_id=s.id AND p.term_id='2026FA' AND "
                    "p.status='pending') ORDER BY external_ref"
                ),
            )
            return {
                "definition": (
                    "Enrolled students with Fall registrations, an active Fall "
                    "financial hold and a pending Fall payment; failed payments "
                    "excluded; deduplicated students."
                ),
                "denominator": denominator[0]["count"],
                "count": len(members),
                "members": members,
            }
