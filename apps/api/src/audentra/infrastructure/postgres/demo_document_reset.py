"""Atomic reset of explicitly configured demo evidence; audit and originals survive."""

from dataclasses import replace
from typing import Any

from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError

from .demo_task_board_commands import lock_demo_student
from .portal_repository import PostgresPortalRepository


async def reset_documents(
    portal: PostgresPortalRepository, auth: AuthContext, student_ref: str, request_id: str
) -> dict[str, Any]:
    async with portal.engine.begin() as db:
        student = await db.scalar(
            text("SELECT id FROM student WHERE tenant_id=:tenant AND external_ref=:ref"),
            {"tenant": auth.tenant_id, "ref": student_ref},
        )
        if not student:
            raise ApiError(
                404, "DEMO_STUDENT_NOT_FOUND", "The configured demo student was not found"
            )
        await lock_demo_student(db, auth.tenant_id, str(student))
        p: dict[str, Any] = {"tenant": auth.tenant_id, "student": str(student)}
        requirements = list(
            (
                await db.execute(
                    text(
                        (
                            "SELECT requirement_id FROM demo_document_requirement WHERE "
                            "tenant_id=:tenant AND student_id=:student"
                        )
                    ),
                    p,
                )
            ).scalars()
        )
        if not requirements:
            raise ApiError(
                409, "DEMO_DOCUMENTS_NOT_CONFIGURED", "No demo document requirements are configured"
            )
        p["requirements"] = requirements
        # A shared student lock serializes reservation, attachment, completion and reset.
        # Rejected originals cannot be reclaimed by a delayed worker or retried after reset.
        await db.execute(
            text("""UPDATE document_record SET status='rejected',
            superseded_at=now(),updated_at=now()
            WHERE tenant_id=:tenant AND student_id=:student AND requirement_id=ANY(:requirements)
              AND superseded_at IS NULL"""),
            p,
        )
        work_ids = list(
            (
                await db.execute(
                    text("""SELECT DISTINCT w.id FROM staff_work_item w
          WHERE w.tenant_id=:tenant AND w.student_id=:student AND w.work_type='document_review'
            AND w.status <> 'cancelled'
            AND (EXISTS(SELECT 1 FROM staff_work_item_link l WHERE l.tenant_id=w.tenant_id AND
                    l.work_item_id=w.id
              AND l.entity_type='requirement' AND l.entity_id=ANY(:requirements))
              OR EXISTS(SELECT 1 FROM document_record d WHERE d.tenant_id=w.tenant_id AND
                    d.student_id=w.student_id
                AND d.requirement_id=ANY(:requirements) AND (d.id=w.source_id OR EXISTS(
                  SELECT 1 FROM staff_demo_document b WHERE b.tenant_id=w.tenant_id AND
                    b.work_item_id=w.id AND b.document_id=d.id))))"""),
                    p,
                )
            ).scalars()
        )
        if work_ids:
            p["work"] = work_ids
            await db.execute(
                text(
                    "UPDATE staff_demo_board_card SET archived_at=now() WHERE "
                    "tenant_id=:tenant AND work_item_id=ANY(:work)"
                ),
                p,
            )
            await db.execute(
                text("""UPDATE staff_work_item SET status='cancelled',cancelled_at=now(),
                terminal_reason='Demo document reset; evidence
                    retained',version=version+1,updated_at=now()
                WHERE tenant_id=:tenant AND id=ANY(:work)"""),
                p,
            )
        await db.execute(
            text("""UPDATE student_requirement SET status='ready',progress_percent=0,
            version=version+1,updated_at=now()
            WHERE tenant_id=:tenant AND id=ANY(:requirements)"""),
            p,
        )
        await db.execute(
            text("""UPDATE financial_document_requirement SET status='not_started',document_id=NULL,
            version=version+1,updated_at=now() WHERE tenant_id=:tenant AND student_id=:student
            AND code='verification_worksheet' AND EXISTS(SELECT 1 FROM demo_document_requirement
              WHERE tenant_id=:tenant AND student_id=:student AND expected_type='financial_aid'
            )"""),
            p,
        )
        subject = replace(auth, student_id=str(student))
        await portal._insert_audit(
            db,
            subject,
            "demo.documents_reset",
            "student",
            str(student),
            request_id,
            {
                "requirementIds": [str(x) for x in requirements],
                "workItemIds": [str(x) for x in work_ids],
            },
        )
        await portal._insert_student_realtime_event(
            db,
            auth=subject,
            event_type="student.requirements.updated",
            resource_type="student",
            resource_id=str(student),
            payload={"invalidate": ["requirements", "documents", "bootstrap", "dashboard"]},
        )
        await portal._insert_realtime_event(
            db,
            tenant_id=auth.tenant_id,
            event_type="staff.work_item.updated",
            resource_type="student",
            resource_id=str(student),
            work_item_id=None,
            target={"tenantWide": True},
            payload={"invalidate": ["workspace", "documents", "students"]},
        )
        return {
            "reset": True,
            "requirementsReset": len(requirements),
            "cardsRemoved": len(work_ids),
        }
