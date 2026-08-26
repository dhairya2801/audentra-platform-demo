# ruff: noqa: S608 -- schema identifiers are validated before interpolation.
"""One SQL translation of the canonical cohort vocabulary.

`domain/student_cohort.py` owns what a cohort *means*. This module owns the
only place that meaning becomes SQL. Staff Edward's cohort reads and the
Morning Brew aggregates both build their predicates here, so the briefing that
says "7 deposited students have overdue requirements" and the assistant answer
to "who are those 7?" cannot drift apart: they are the same WHERE clause.

Nothing here executes anything or knows about authentication. It emits SQL
fragments and bind parameters; the repositories that use it add the tenant
filter, which is the one predicate a caller may never supply.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text

from audentra.domain.student_cohort import DUE_SOON_HORIZON_DAYS, CohortFilter

JsonDict = dict[str, Any]

SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

OPEN_WORK_STATUSES: tuple[str, ...] = ("todo", "in_progress", "follow_up_required", "blocked")
DONE_REQUIREMENT_STATUSES: tuple[str, ...] = ("completed", "waived", "not_applicable")
IN_REVIEW_REQUIREMENT_STATUSES: tuple[str, ...] = ("submitted", "under_review")

_DOCUMENT_STATUS_GROUPS: dict[str, tuple[str, ...]] = {
    "submitted": ("uploaded", "processing"),
    "under_review": ("needs_review", "under_review"),
    "accepted": ("accepted",),
    "rejected": ("rejected",),
}


def text_bind_expanding(name: str, values: tuple[str, ...]):  # type: ignore[no-untyped-def]
    """An expanding IN bind parameter for raw text() statements."""

    return bindparam(name, value=list(values), expanding=True)


def quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class CohortSql:
    """Schema-bound SQL for the canonical cohort vocabulary."""

    def __init__(self, schema: str = "public") -> None:
        if not SQL_IDENTIFIER.fullmatch(schema):
            raise ValueError("schema is not a safe PostgreSQL identifier")
        self._schema = schema

    @property
    def schema(self) -> str:
        return self._schema

    def table(self, name: str) -> str:
        return f"{self._schema}.{name}"

    # ------------------------------------------------------------------
    # Shared column expressions
    # ------------------------------------------------------------------

    def deposit_bucket(self) -> str:
        """Canonical deposit state over the payment ledger.

        The current PostgreSQL constraint has no in-flight statuses, but the
        shared vocabulary keeps the pending bucket explicit for the provider
        contract that introduces them. Paid always wins over an older pending
        attempt.
        """

        payment = self.table("payment_transaction")
        base = (
            "pay.tenant_id = student.tenant_id AND pay.student_id = student.id"
            " AND pay.type = 'enrollment_deposit'"
        )
        return (
            f"CASE WHEN EXISTS (SELECT 1 FROM {payment} AS pay"
            f" WHERE {base} AND pay.status = 'succeeded') THEN 'paid'"
            f" WHEN EXISTS (SELECT 1 FROM {payment} AS pay"
            f" WHERE {base} AND pay.status IN ('pending', 'processing', 'submitted'))"
            f" THEN 'pending' ELSE 'unpaid' END"
        )

    def housing_bucket(self) -> str:
        """The housing step bucketed into the `HOUSING_STATES` vocabulary.

        One definition serves both the filter and the grouping. When these were
        written separately, `housingState=blocked` selected nothing while the
        grouping happily reported raw statuses like `in_progress` — two answers
        to the same question.
        """

        done = quoted(DONE_REQUIREMENT_STATUSES)
        status = (
            f"(SELECT req.status FROM {self.table('enrollment_journey')} AS jr"
            f" JOIN {self.table('student_requirement')} AS req"
            f"   ON req.journey_id = jr.id AND req.tenant_id = jr.tenant_id"
            f"  AND req.retired_at IS NULL"
            f" JOIN {self.table('requirement_definition_version')} AS rdv"
            f"   ON rdv.id = req.requirement_definition_version_id"
            f"  AND rdv.tenant_id = req.tenant_id"
            f" WHERE jr.tenant_id = student.tenant_id AND jr.student_id = student.id"
            f"   AND rdv.code = 'housing_preference' LIMIT 1)"
        )
        return (
            f"CASE WHEN {status} IS NULL THEN 'no_step'"
            f" WHEN {status} = 'blocked' THEN 'blocked'"
            f" WHEN {status} IN ({done}) THEN 'selected'"
            f" ELSE 'actionable' END"
        )

    def latest_offer_status(self) -> str:
        offer = self.table("admission_offer")
        return (
            f"(SELECT o.status FROM {offer} AS o"
            f" WHERE o.tenant_id = student.tenant_id AND o.student_id = student.id"
            f" ORDER BY o.created_at DESC, o.id DESC LIMIT 1)"
        )

    def onboarding_status(self) -> str:
        onboarding = self.table("student_onboarding")
        return (
            f"COALESCE((SELECT ob.status FROM {onboarding} AS ob"
            f" WHERE ob.tenant_id = student.tenant_id AND ob.student_id = student.id),"
            f" 'not_started')"
        )

    def requirement_progress(self) -> str:
        journey = self.table("enrollment_journey")
        requirement = self.table("student_requirement")
        definition = self.table("requirement_definition_version")
        done = quoted(DONE_REQUIREMENT_STATUSES)
        return f"""
            SELECT
              COUNT(requirement.id)::integer AS total_count,
              COUNT(requirement.id) FILTER (
                WHERE requirement.status IN ({done})
              )::integer AS completed_count,
              COUNT(requirement.id) FILTER (
                WHERE definition.blocking = 1
                  AND requirement.status NOT IN ({done})
              )::integer AS open_blocking_count,
              MIN(requirement.due_at) FILTER (
                WHERE requirement.status NOT IN ({done})
              ) AS next_due_at
            FROM {journey} AS journey
            LEFT JOIN {requirement} AS requirement
              ON requirement.tenant_id = journey.tenant_id
             AND requirement.journey_id = journey.id
             AND requirement.retired_at IS NULL
            LEFT JOIN {definition} AS definition
              ON definition.id = requirement.requirement_definition_version_id
             AND definition.tenant_id = requirement.tenant_id
            WHERE journey.tenant_id = student.tenant_id
              AND journey.student_id = student.id
        """

    def from_clause(self) -> str:
        return f"""
            FROM {self.table("student")} AS student
            JOIN {self.table("person")} AS person
              ON person.id = student.person_id AND person.tenant_id = student.tenant_id
            LEFT JOIN {self.table("student_profile")} AS profile
              ON profile.student_id = student.id AND profile.tenant_id = student.tenant_id
        """

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def predicates(self, cohort: CohortFilter, *, prefix: str = "") -> tuple[list[str], JsonDict]:
        """Translate a validated filter into SQL predicates over canonical rows.

        Every branch here reads the same tables the portal renders from. The
        list, the count, and every grouping share this one function, so a staff
        user can never be told "42 students" by one call and shown 39 by the
        next.

        `prefix` namespaces the generated bind parameters so several different
        cohorts can be counted inside one statement — which is how Morning Brew
        reads its whole funnel at a single instant.
        """

        onboarding = self.table("student_onboarding")
        offer = self.table("admission_offer")
        program = self.table("program")
        journey = self.table("enrollment_journey")
        requirement = self.table("student_requirement")
        definition = self.table("requirement_definition_version")
        document = self.table("document_record")
        aid_document = self.table("financial_document_requirement")
        work_item = self.table("staff_work_item")
        credential = self.table("credential_account")
        done = quoted(DONE_REQUIREMENT_STATUSES)
        key = f"{prefix}cohort"

        clauses: list[str] = []
        params: JsonDict = {}

        if cohort.query:
            tokens = [token for token in re.split(r"\s+", cohort.query.strip()) if token][:5]
            for index, token in enumerate(tokens):
                name = f"{key}_token_{index}"
                params[name] = f"%{escape_like(token)}%"
                clauses.append(
                    f"(person.first_name ILIKE :{name} ESCAPE '\\'"
                    f" OR person.last_name ILIKE :{name} ESCAPE '\\'"
                    f" OR COALESCE(profile.preferred_name, person.preferred_name, '')"
                    f" ILIKE :{name} ESCAPE '\\'"
                    f" OR EXISTS (SELECT 1 FROM {credential} AS account"
                    f"   WHERE account.tenant_id = student.tenant_id"
                    f"     AND account.student_id = student.id"
                    f"     AND account.email_normalized ILIKE :{name} ESCAPE '\\'))"
                )

        if cohort.class_year is not None:
            params[f"{key}_class_year"] = cohort.class_year
            clauses.append(f"student.class_year = :{key}_class_year")

        if cohort.program:
            params[f"{key}_program"] = f"%{escape_like(cohort.program)}%"
            clauses.append(
                f"EXISTS (SELECT 1 FROM {offer} AS o JOIN {program} AS p"
                f"   ON p.id = o.program_id AND p.tenant_id = o.tenant_id"
                f" WHERE o.tenant_id = student.tenant_id AND o.student_id = student.id"
                f"   AND p.name ILIKE :{key}_program ESCAPE '\\')"
            )

        if cohort.offer_status:
            params[f"{key}_offer_status"] = cohort.offer_status
            # The *current* offer is the newest one; an older superseded offer
            # must not make a student match a status they have moved past.
            clauses.append(f"{self.latest_offer_status()} = :{key}_offer_status")

        if cohort.deposit_state:
            params[f"{key}_deposit_state"] = cohort.deposit_state
            clauses.append(f"{self.deposit_bucket()} = :{key}_deposit_state")

        if cohort.onboarding_status:
            params[f"{key}_onboarding_status"] = cohort.onboarding_status
            clauses.append(f"{self.onboarding_status()} = :{key}_onboarding_status")

        if cohort.requirement_code or cohort.requirement_state:
            state = cohort.requirement_state or "open"
            requirement_clauses = [
                "req.tenant_id = student.tenant_id",
                "jr.student_id = student.id",
                "req.retired_at IS NULL",
            ]
            if cohort.requirement_code:
                params[f"{key}_requirement_code"] = cohort.requirement_code.lower()
                requirement_clauses.append(f"LOWER(rdv.code) = :{key}_requirement_code")
            if state == "open":
                requirement_clauses.append(f"req.status NOT IN ({done})")
            elif state == "blocked":
                requirement_clauses.append("req.status = 'blocked'")
            elif state == "in_review":
                requirement_clauses.append(
                    f"req.status IN ({quoted(IN_REVIEW_REQUIREMENT_STATUSES)})"
                )
            elif state == "complete":
                requirement_clauses.append(f"req.status IN ({done})")
            elif state == "overdue":
                requirement_clauses.append(
                    f"req.status NOT IN ({done}) AND req.due_at IS NOT NULL AND req.due_at < NOW()"
                )
            elif state == "due_soon":
                requirement_clauses.append(
                    f"req.status NOT IN ({done}) AND req.due_at IS NOT NULL"
                    f" AND req.due_at >= NOW()"
                    f" AND req.due_at < NOW() + INTERVAL '{DUE_SOON_HORIZON_DAYS} days'"
                )
            clauses.append(
                f"EXISTS (SELECT 1 FROM {journey} AS jr"
                f" JOIN {requirement} AS req ON req.journey_id = jr.id"
                f"   AND req.tenant_id = jr.tenant_id"
                f" JOIN {definition} AS rdv ON rdv.id = req.requirement_definition_version_id"
                f"   AND rdv.tenant_id = req.tenant_id"
                f" WHERE jr.tenant_id = student.tenant_id"
                f"   AND {' AND '.join(requirement_clauses)})"
            )

        if cohort.document_category or cohort.document_state:
            state = cohort.document_state or "missing"
            document_clauses = [
                "doc.tenant_id = student.tenant_id",
                "doc.student_id = student.id",
            ]
            if cohort.document_category:
                params[f"{key}_document_category"] = cohort.document_category.lower()
                document_clauses.append(f"LOWER(doc.category) = :{key}_document_category")
            if state == "missing":
                # A rejected upload leaves the requirement unmet, so a student
                # whose only file was rejected still counts as missing it.
                document_clauses.append("doc.status NOT IN ('placeholder', 'rejected')")
                clauses.append(
                    f"NOT EXISTS (SELECT 1 FROM {document} AS doc"
                    f" WHERE {' AND '.join(document_clauses)})"
                )
            else:
                document_clauses.append(f"doc.status IN ({quoted(_DOCUMENT_STATUS_GROUPS[state])})")
                clauses.append(
                    f"EXISTS (SELECT 1 FROM {document} AS doc"
                    f" WHERE {' AND '.join(document_clauses)})"
                )

        if cohort.aid_document_state:
            state = cohort.aid_document_state
            if state == "outstanding":
                predicate: str | None = "aid.status <> 'verified'"
            elif state == "in_review":
                predicate = "aid.status IN ('submitted', 'under_review')"
            elif state == "verified":
                # "Aid documents are verified" means the file is complete,
                # not merely that one verified row exists beside another row
                # that still requires action.
                clauses.append(
                    f"EXISTS (SELECT 1 FROM {aid_document} AS aid"
                    f" WHERE aid.tenant_id = student.tenant_id"
                    f"   AND aid.student_id = student.id)"
                )
                clauses.append(
                    f"NOT EXISTS (SELECT 1 FROM {aid_document} AS aid"
                    f" WHERE aid.tenant_id = student.tenant_id"
                    f"   AND aid.student_id = student.id"
                    f"   AND aid.status <> 'verified')"
                )
                predicate = None
            else:
                params[f"{key}_aid_state"] = state
                predicate = f"aid.status = :{key}_aid_state"
            if predicate is not None:
                clauses.append(
                    f"EXISTS (SELECT 1 FROM {aid_document} AS aid"
                    f" WHERE aid.tenant_id = student.tenant_id AND aid.student_id = student.id"
                    f"   AND {predicate})"
                )

        if cohort.housing_state:
            params[f"{key}_housing_state"] = cohort.housing_state
            clauses.append(f"{self.housing_bucket()} = :{key}_housing_state")

        if cohort.assigned_staff_id:
            params[f"{key}_assignee"] = UUID(cohort.assigned_staff_id)
            clauses.append(
                f"EXISTS (SELECT 1 FROM {work_item} AS wi"
                f" WHERE wi.tenant_id = student.tenant_id AND wi.student_id = student.id"
                f"   AND wi.assignee_id = :{key}_assignee"
                f"   AND wi.status IN :open_statuses)"
            )

        if cohort.has_open_work_item is not None:
            exists = (
                f"EXISTS (SELECT 1 FROM {work_item} AS wi"
                f" WHERE wi.tenant_id = student.tenant_id AND wi.student_id = student.id"
                f"   AND wi.status IN :open_statuses)"
            )
            clauses.append(exists if cohort.has_open_work_item else f"NOT {exists}")

        if cohort.has_overdue_requirement is not None:
            exists = self.overdue_requirement_exists()
            clauses.append(exists if cohort.has_overdue_requirement else f"NOT {exists}")

        if cohort.has_open_blocking_requirement is not None:
            exists = self.open_blocking_requirement_exists()
            clauses.append(exists if cohort.has_open_blocking_requirement else f"NOT {exists}")

        assignment = self.table("student_staff_assignment")
        member = self.table("staff_member")
        current_adviser = (
            f"SELECT 1 FROM {assignment} AS adv"
            f" WHERE adv.tenant_id = student.tenant_id AND adv.student_id = student.id"
            f"   AND adv.role = 'primary_advisor' AND adv.ended_at IS NULL"
        )
        if cohort.primary_adviser_id:
            params[f"{key}_primary_adviser"] = UUID(cohort.primary_adviser_id)
            clauses.append(
                f"EXISTS ({current_adviser} AND adv.staff_member_id = :{key}_primary_adviser)"
            )
        if cohort.adviser_state == "none":
            clauses.append(f"NOT EXISTS ({current_adviser})")
        elif cohort.adviser_state == "assigned":
            clauses.append(f"EXISTS ({current_adviser})")
        elif cohort.adviser_state:
            params[f"{key}_adviser_state"] = cohort.adviser_state
            clauses.append(
                f"EXISTS ({current_adviser}"
                f" AND EXISTS (SELECT 1 FROM {member} AS am WHERE am.id = adv.staff_member_id"
                f"   AND am.tenant_id = adv.tenant_id"
                f"   AND am.employment_status = :{key}_adviser_state))"
            )

        for field_name, column in (
            ("residency_status", "residencyStatus"),
            ("citizenship_status", "citizenshipStatus"),
        ):
            value = getattr(cohort, field_name)
            if not value:
                continue
            name = f"{key}_{field_name}"
            params[name] = value.lower()
            # Onboarding answers are the canonical home for these; the portal
            # renders them from the same jsonb payload.
            clauses.append(
                f"LOWER(COALESCE((SELECT ob.payload ->> '{column}' FROM {onboarding} AS ob"
                f" WHERE ob.tenant_id = student.tenant_id AND ob.student_id = student.id), ''))"
                f" = :{name}"
            )

        return clauses, params

    def overdue_requirement_exists(self) -> str:
        journey = self.table("enrollment_journey")
        requirement = self.table("student_requirement")
        done = quoted(DONE_REQUIREMENT_STATUSES)
        return (
            f"EXISTS (SELECT 1 FROM {journey} AS jr"
            f" JOIN {requirement} AS req ON req.journey_id = jr.id"
            f"   AND req.tenant_id = jr.tenant_id AND req.retired_at IS NULL"
            f" WHERE jr.tenant_id = student.tenant_id AND jr.student_id = student.id"
            f"   AND req.status NOT IN ({done})"
            f"   AND req.due_at IS NOT NULL AND req.due_at < NOW())"
        )

    def open_blocking_requirement_exists(self) -> str:
        """Exactly the condition `requirement_progress.open_blocking_count > 0`
        tests, expressed as a predicate so a cohort can select on it.

        Reusing the per-student progress derivation is what lets the briefing
        say "9 deposited students are still blocked" and lets the assistant
        list those nine from the same rule."""

        journey = self.table("enrollment_journey")
        requirement = self.table("student_requirement")
        definition = self.table("requirement_definition_version")
        done = quoted(DONE_REQUIREMENT_STATUSES)
        return (
            f"EXISTS (SELECT 1 FROM {journey} AS jr"
            f" JOIN {requirement} AS req ON req.journey_id = jr.id"
            f"   AND req.tenant_id = jr.tenant_id AND req.retired_at IS NULL"
            f" JOIN {definition} AS rdv ON rdv.id = req.requirement_definition_version_id"
            f"   AND rdv.tenant_id = req.tenant_id"
            f" WHERE jr.tenant_id = student.tenant_id AND jr.student_id = student.id"
            f"   AND rdv.blocking = 1 AND req.status NOT IN ({done}))"
        )

    def statement(self, sql: str) -> Any:
        """Bind the expanding parameters a generated statement happens to use."""

        statement = text(sql)
        if ":open_statuses" in sql:
            statement = statement.bindparams(
                text_bind_expanding("open_statuses", OPEN_WORK_STATUSES)
            )
        return statement
