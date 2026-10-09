"""Audited corrections of extracted values on the current original."""

from __future__ import annotations

import copy
import json
import math
import re
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError

from .demo_task_board_commands import lock_demo_card
from .document_access import require_document_access

if TYPE_CHECKING:
    from .staff_repository import PostgresStaffRepository


async def correct_extraction(
    staff: PostgresStaffRepository,
    auth: AuthContext,
    document_id: str,
    payload: dict[str, Any],
    request_id: str,
    key: str | None,
) -> dict[str, object]:
    async def handler(connection: AsyncConnection) -> dict[str, object]:
        await require_document_access(connection, auth, document_id)
        work = await lock_demo_card(connection, auth, payload["workItemId"])
        if (
            str(work["source_id"]) != document_id
            or work["version"] != payload["expectedWorkItemVersion"]
        ):
            raise ApiError(409, "VERSION_CONFLICT", "Review the latest document and task version")
        doc = (
            (
                await connection.execute(
                    text(
                        "SELECT extraction,status FROM document_record WHERE tenant_id=:tenant "
                        "AND id=:id FOR UPDATE"
                    ),
                    {"tenant": auth.tenant_id, "id": document_id},
                )
            )
            .mappings()
            .one()
        )
        if (
            doc["status"] not in {"under_review", "needs_review"}
            or not doc["extraction"]
            or doc["extraction"].get("status") not in {"completed", "pending_staff"}
        ):
            raise ApiError(
                409, "EXTRACTION_NOT_REVIEWABLE", "Wait for extraction or request a replacement"
            )
        extraction = copy.deepcopy(dict(doc["extraction"]))
        allowed = {"studentName", "institutionName", "issueDate", "academicTerm"} | {
            f["key"] for f in extraction.get("fields", [])
        }
        allowed |= {
            "date_of_birth",
            "transcript_type",
            "program",
            "attendance_dates",
            "gpa",
            "gpa_scale",
            "total_credits",
            "document_subtype",
            "nationality",
            "issuing_country",
            "passport_number",
            "expiry_date",
            "vaccination_records",
            "exemption",
            "titer_result",
            "tax_year",
            "household_size",
            "household_income",
            "student_income",
        }
        for index, _course in enumerate(extraction.get("courses", [])):
            allowed.update(
                f"course_{index}_{k}" for k in ("term", "sourceCode", "title", "credits", "grade")
            )
        values = payload["values"]
        if (
            (not values and not payload.get("decisions"))
            or set(values) - allowed
            or any(not isinstance(v, str) or len(v) > 500 for v in values.values())
        ):
            raise ApiError(400, "INVALID_CORRECTION", "Correct only displayed extracted fields")
        for field, value in values.items():
            extraction.get("fieldDecisions", {}).pop(field, None)
            if field in {"studentName", "institutionName", "issueDate", "academicTerm"}:
                extraction[field] = value
            elif match := re.fullmatch(
                r"course_(\d+)_(term|sourceCode|title|credits|grade)", field
            ):
                if match[2] == "credits":
                    try:
                        number = float(value) if value.strip() else None
                        if number is not None and (
                            not math.isfinite(number) or not 0 <= number <= 500
                        ):
                            raise ValueError()
                    except ValueError as error:
                        raise ApiError(
                            400, "INVALID_CREDITS", "Enter credits between 0 and 500"
                        ) from error
                    extraction["courses"][int(match[1])][match[2]] = number
                else:
                    extraction["courses"][int(match[1])][match[2]] = value
            else:
                item = next(
                    (f for f in extraction.setdefault("fields", []) if f["key"] == field), None
                )
                if item is None:
                    item = {"key": field, "label": field.replace("_", " ").title()}
                    extraction["fields"].append(item)
                item["value"] = value
                item["confidence"] = 1.0
                item["source"] = "staff"
                item.pop("sourcePage", None)
        from audentra.domain.conference_documents import validate_evidence

        expected = await connection.scalar(
            text(
                "SELECT expected_type FROM demo_document_requirement cfg JOIN document_record d "
                "ON d.tenant_id=cfg.tenant_id AND d.requirement_id=cfg.requirement_id "
                "WHERE d.tenant_id=:tenant AND d.id=:id"
            ),
            {"tenant": auth.tenant_id, "id": document_id},
        )
        if expected:
            extraction["validation"] = validate_evidence(extraction, str(expected))
        for field, decision in payload.get("decisions", {}).items():
            if field not in allowed or (
                decision["action"] == "correction" and not decision["note"].strip()
            ):
                raise ApiError(
                    400,
                    "INVALID_FIELD_DECISION",
                    "Choose a displayed field and give a correction reason",
                )
            decisions = extraction.setdefault("fieldDecisions", {})
            if decision["action"] == "undo":
                decisions.pop(field, None)
            else:
                decisions[field] = {**decision, "who": auth.actor_id}
        extraction["staffCorrected"] = True
        # Original provider result remains in audit evidence; retry cannot overwrite review.
        await connection.execute(
            text(
                "UPDATE document_record SET extraction=CAST(:value AS "
                "jsonb),updated_at=now() WHERE tenant_id=:tenant AND id=:id"
            ),
            {"value": json.dumps(extraction), "tenant": auth.tenant_id, "id": document_id},
        )
        await connection.execute(
            text(
                "UPDATE staff_work_item SET version=version+1,updated_at=now() WHERE "
                "tenant_id=:tenant AND id=:id"
            ),
            {"tenant": auth.tenant_id, "id": work["id"]},
        )
        await staff._insert_work_log(
            connection,
            auth=auth,
            work_item_id=str(work["id"]),
            actor_name=await staff._staff_name(connection, auth, auth.actor_id),
            action="commented",
            message="Extraction corrected: " + payload["note"],
        )
        await staff._insert_audit(
            connection,
            auth=auth,
            request_id=request_id,
            action="document.extraction_corrected",
            resource_type="document_record",
            resource_id=document_id,
            metadata={"before": doc["extraction"], "after": extraction, "reason": payload["note"]},
        )
        await staff._insert_realtime_event(
            connection,
            tenant_id=auth.tenant_id,
            event_type="staff.work_item.updated",
            resource_type="document",
            resource_id=document_id,
            work_item_id=str(work["id"]),
            staff_member_id=auth.actor_id,
            team_component=None,
            tenant_wide=False,
            payload={"invalidate": ["documents", "workspace"]},
        )
        await staff._insert_student_realtime_event(
            connection,
            tenant_id=auth.tenant_id,
            student_id=str(work["student_id"]),
            event_type="student.document.updated",
            resource_type="document",
            resource_id=document_id,
            payload={"invalidate": ["documents", "requirements"]},
        )
        return {"saved": True, "workItemVersion": work["version"] + 1}

    return await staff._run_idempotent(
        auth=auth,
        idempotency_key=key or request_id,
        operation="document.correct_extraction",
        request_payload={"documentId": document_id, **payload},
        response_status=200,
        handler=handler,
    )
