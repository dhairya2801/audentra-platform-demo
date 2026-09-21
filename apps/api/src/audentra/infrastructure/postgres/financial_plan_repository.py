"""Tenant-bound planning persistence; financial facts come from university repositories."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.domain.financial_plan import financial_plan, simulate_plan, validate_inputs

if TYPE_CHECKING:
    from .university_repository import PostgresUniversityRepository


class FinancialPlanService:
    def __init__(self, university: PostgresUniversityRepository) -> None:
        self.university = university

    async def read(self, auth: AuthContext, term_id: str = "2026FA") -> dict[str, Any]:
        from .university_repository import _rows

        account = await self.university.record(auth, "account")
        data: dict[str, Any] = {}
        async with self.university.engine.begin() as c:
            await c.execute(
                text("SELECT set_config('audentra.tenant_id',:tenant,true)"),
                {"tenant": auth.tenant_id},
            )
            data["terms"] = await _rows(c, auth, "SELECT * FROM term ORDER BY starts_on")
            if term_id not in {r["id"] for r in data["terms"]}:
                raise ApiError(400, "INVALID_TERM", "Choose an institutional term")
            for key, sql in {
                "advisers": (
                    "SELECT s.name,s.email,o.name AS office_name FROM assignment a "
                    "JOIN staff s ON s.id=a.staff_id JOIN office o ON o.id=s.office_id "
                    "WHERE a.student_id=:sid AND a.ends_at IS NULL AND s.office_id='FA'"
                ),
                "planning": (
                    "SELECT * FROM financial_plan_input WHERE student_id=:sid AND term_id=:term"
                ),
                "catalog": "SELECT * FROM rate_catalog ORDER BY kind,id",
                "mealEnrollments": (
                    "SELECT * FROM meal_enrollment WHERE student_id=:sid AND term_id=:term"
                ),
                "insuranceCoverage": (
                    "SELECT * FROM insurance_coverage WHERE student_id=:sid AND term_id=:term"
                ),
                "paymentAgreements": (
                    "SELECT * FROM payment_agreement WHERE student_id=:sid AND term_id=:term"
                ),
                "installments": (
                    "SELECT i.* FROM payment_installment i JOIN payment_agreement a ON "
                    "a.id=i.agreement_id WHERE a.student_id=:sid AND a.term_id=:term"
                ),
                "exceptions": (
                    "SELECT e.*,o.name AS office_name,s.name AS approver_name "
                    "FROM exception e JOIN office o ON o.id=e.office_id "
                    "LEFT JOIN staff s ON s.id=e.approver_id "
                    "WHERE e.student_id=:sid AND e.term_id=:term "
                    "AND e.office_id IN ('SA','FA','SHS') ORDER BY e.starts_at"
                ),
                "termAwards": (
                    "SELECT t.*,a.fund_id,a.status,f.name,f.source,f.posts_to_account "
                    "FROM award_term t JOIN award a ON a.id=t.award_id "
                    "JOIN fund f ON f.id=a.fund_id "
                    "WHERE a.student_id=:sid AND t.term_id=:term ORDER BY f.name"
                ),
                "loanTerms": (
                    "SELECT l.* FROM loan_terms l JOIN award a ON a.fund_id=l.fund_id "
                    "JOIN term t ON t.id=:term WHERE a.student_id=:sid "
                    "AND l.effective_from<=t.starts_on AND l.effective_until>=t.starts_on "
                    "ORDER BY l.fund_id"
                ),
                "scenarios": (
                    "SELECT * FROM financial_scenario WHERE student_id=:sid AND term_id=:term"
                    " ORDER BY id"
                ),
            }.items():
                data[key] = await _rows(c, auth, sql, sid=auth.student_id, term=term_id)
            data["planning"] = next(iter(data["planning"]), None)
        return financial_plan(account, data, term_id)

    async def simulate(self, auth: AuthContext, payload: dict[str, Any]) -> dict[str, Any]:
        plan = await self.read(auth, str(payload.get("termId", "2026FA")))
        try:
            return simulate_plan(plan, payload)
        except ValueError as error:
            raise ApiError(400, "INVALID_SCENARIO", str(error)) from error

    async def save_inputs(
        self, auth: AuthContext, payload: dict[str, Any], key: str | None, request_id: str
    ) -> dict[str, Any]:
        if auth.actor_type != "student":
            raise ApiError(
                403, "STUDENT_REQUIRED", "Only the student can change personal planning assumptions"
            )
        if not key or len(key) > 128:
            raise ApiError(400, "IDEMPOTENCY_REQUIRED", "Supply a bounded idempotency key")
        if (
            set(payload) != {"termId", "expectedVersion", "inputs"}
            or type(payload.get("expectedVersion")) is not int
            or payload["expectedVersion"] < 0
        ):
            raise ApiError(400, "INVALID_PLAN_INPUT", "Supply termId, expectedVersion and inputs")
        try:
            inputs = validate_inputs(payload["inputs"])
        except ValueError as error:
            raise ApiError(400, "INVALID_PLAN_INPUT", str(error)) from error
        # Validate actor/term using the same public read before any mutation.
        await self.read(auth, str(payload["termId"]))
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        params = {
            "tenant": auth.tenant_id,
            "actor": auth.actor_id,
            "sid": auth.student_id,
            "term": payload["termId"],
            "key": key,
            "hash": digest,
            "inputs": json.dumps(inputs, sort_keys=True),
        }
        async with self.university.engine.begin() as c:
            await c.execute(text("SELECT set_config('audentra.tenant_id',:tenant,true)"), params)
            # Serialize first creation as well as subsequent versions and exact-key retries.
            # Browser sessions identify the person, while legacy demo headers
            # identify the student. Verify either against the same owned row.
            owned = await c.scalar(
                text(
                    "SELECT id FROM public.student WHERE tenant_id=:tenant AND id=CAST(:sid "
                    "AS uuid) AND (person_id=CAST(:actor AS uuid) OR "
                    "id=CAST(:actor AS uuid)) FOR UPDATE"
                ),
                params,
            )
            if owned is None:
                raise ApiError(
                    403,
                    "STUDENT_REQUIRED",
                    "Only the student can change personal planning assumptions",
                )
            receipt = (
                (
                    await c.execute(
                        text(
                            (
                                "SELECT payload_hash,result FROM university.planning_receipt WHERE "
                                "tenant_id=:tenant AND actor_id=CAST(:actor AS uuid) AND "
                                "idempotency_key=:key"
                            )
                        ),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            if receipt:
                if receipt["payload_hash"] != digest:
                    raise ApiError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "This key was used for different planning inputs",
                    )
                return dict(receipt["result"])
            version = await c.scalar(
                text(
                    "SELECT version FROM university.financial_plan_input WHERE "
                    "tenant_id=:tenant AND student_id=:sid AND term_id=:term FOR UPDATE"
                ),
                params,
            )
            if (version or 0) != payload["expectedVersion"]:
                raise ApiError(
                    409, "VERSION_CONFLICT", "The plan changed; refresh before saving your inputs"
                )
            params["version"] = (version or 0) + 1
            await c.execute(
                text(
                    "INSERT INTO university.financial_plan_input(tenant_id,student_id,term_id"
                    ",version,inputs_json,updated_at,provenance) "
                    "VALUES(:tenant,:sid,:term,:version,:inputs,to_char(now() AT TIME ZONE "
                    "'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),'student_entered') ON "
                    "CONFLICT(tenant_id,student_id,term_id) DO UPDATE SET version=EXCLUDED.ve"
                    "rsion,inputs_json=EXCLUDED.inputs_json,updated_at=EXCLUDED.updated_at"
                ),
                params,
            )
            result = {
                "receiptId": str(uuid4()),
                "version": params["version"],
                "termId": payload["termId"],
                "inputs": inputs,
                "provenance": "student_entered",
                "ledgerChanged": False,
            }
            params.update(
                result=json.dumps(result), receipt=result["receiptId"], request=request_id
            )
            await c.execute(
                text(
                    "INSERT INTO university.planning_receipt(tenant_id,actor_id,idempotency_k"
                    "ey,payload_hash,result) VALUES(:tenant,CAST(:actor AS "
                    "uuid),:key,:hash,CAST(:result AS jsonb))"
                ),
                params,
            )
            await c.execute(
                text(
                    "INSERT INTO audit_event(id,tenant_id,actor_type,actor_id,student_id,acti"
                    "on,resource_type,resource_id,authorization_basis,request_id,correlation_"
                    "id,metadata) VALUES(CAST(:receipt AS uuid),:tenant,'student',CAST(:actor"
                    " AS uuid),CAST(:sid AS "
                    "uuid),'financial_plan.inputs_saved','student',CAST(:sid AS "
                    "uuid),'self',:request,:request,CAST(:result AS jsonb))"
                ),
                params,
            )
            return result
