"""Stored uploads enter configured demo boards without fabricating extraction evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.domain.documents import bounded_document_label

if TYPE_CHECKING:
    from .portal_repository import PostgresPortalRepository


async def attach_demo_document(
    portal: PostgresPortalRepository, auth: AuthContext, document_id: str, request_id: str
) -> bool:
    """Called only after storage.put succeeds. False retains the normal processing path."""
    portal._require_student_or_delegate(auth)
    async with portal.engine.begin() as connection:
        params: dict[str, Any] = {
            "tenant": auth.tenant_id,
            "student": auth.student_id,
            "document": document_id,
        }
        await connection.execute(
            text("SELECT set_config('audentra.tenant_id', :tenant, true)"), params
        )
        from .demo_task_board_commands import lock_demo_student

        await lock_demo_student(connection, auth.tenant_id, auth.student_id)
        target = (
            await connection.execute(
                text("""
            SELECT a.staff_member_id FROM student_staff_assignment a
            JOIN staff_member s ON s.id=a.staff_member_id AND s.tenant_id=a.tenant_id AND s.active
            WHERE a.tenant_id=CAST(:tenant AS uuid) AND a.student_id=CAST(:student AS uuid)
              AND a.role='primary_advisor' AND a.ended_at IS NULL
              AND EXISTS(SELECT 1 FROM staff_demo_board_card c WHERE c.tenant_id=a.tenant_id
                AND c.staff_member_id=a.staff_member_id)
            FOR SHARE OF a,s
        """),
                params,
            )
        ).scalar_one_or_none()
        if target is None:
            return False
        params["staff"] = str(target)
        # Serialize slot allocation and resubmissions across the whole board.
        await connection.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended("
                ":tenant || '|demo-upload|' || :staff,0))"
            ),
            params,
        )
        doc = (
            (
                await connection.execute(
                    text("""
            SELECT * FROM document_record WHERE tenant_id=CAST(:tenant AS uuid)
              AND student_id=CAST(:student AS uuid) AND id=CAST(:document AS uuid) FOR UPDATE
        """),
                    params,
                )
            )
            .mappings()
            .first()
        )
        if doc is None:
            raise ApiError(404, "DOCUMENT_NOT_FOUND", "Document not found")
        if (
            await connection.execute(
                text("""
            SELECT 1 FROM staff_demo_document WHERE tenant_id=CAST(:tenant AS uuid)
              AND document_id=CAST(:document AS uuid)
        """),
                params,
            )
        ).scalar_one_or_none():
            return True
        if doc["status"] != "uploaded" or doc["extraction"] is not None:
            return False
        board = "fa-docs" if doc["category"] == "financial_aid" else "en-docs"
        params.update(
            {
                "board": board,
                "requirement": str(doc["requirement_id"]) if doc["requirement_id"] else None,
                "category": doc["category"],
            }
        )
        # A requirement has one active card; standalone uploads are separate submissions.
        work = None
        if doc["requirement_id"]:
            work = await connection.scalar(
                text("""
                SELECT w.id FROM staff_demo_board_card c
                JOIN staff_work_item w ON w.id=c.work_item_id AND w.tenant_id=c.tenant_id
                JOIN staff_work_item_link l ON l.work_item_id=w.id AND l.tenant_id=w.tenant_id
                WHERE c.tenant_id=CAST(:tenant AS uuid) AND c.staff_member_id=CAST(:staff AS uuid)
                  AND w.assignee_id=c.staff_member_id AND w.student_id=CAST(:student AS uuid)
                  AND l.entity_type='requirement' AND l.entity_id=CAST(:requirement AS uuid)
                ORDER BY c.position LIMIT 1
            """),
                params,
            )
        if doc["requirement_id"] and work is None:
            work = (
                await connection.execute(
                    text("""
                SELECT c.work_item_id FROM staff_demo_board_card c
                JOIN staff_work_item w ON w.id=c.work_item_id AND w.tenant_id=c.tenant_id
                JOIN staff_demo_document b ON b.work_item_id=w.id AND b.tenant_id=w.tenant_id
                JOIN document_record d ON d.id=b.document_id AND d.tenant_id=b.tenant_id
                WHERE c.tenant_id=CAST(:tenant AS uuid) AND c.staff_member_id=CAST(:staff AS uuid)
                  AND w.assignee_id=c.staff_member_id AND w.student_id=CAST(:student AS uuid)
                  AND d.requirement_id=CAST(:requirement AS uuid)
                ORDER BY b.received_at DESC LIMIT 1
            """),
                    params,
                )
            ).scalar_one_or_none()
        if work is None:
            work = (
                await connection.execute(
                    text("""
                SELECT c.work_item_id FROM staff_demo_board_card c
                JOIN staff_work_item w ON w.id=c.work_item_id AND w.tenant_id=c.tenant_id
                WHERE c.tenant_id=CAST(:tenant AS uuid) AND c.staff_member_id=CAST(:staff AS uuid)
                  AND w.assignee_id=c.staff_member_id AND w.student_id=CAST(:student AS uuid)
                  AND c.board_id=:board AND w.source_id IS NULL
                  AND NOT EXISTS(SELECT 1 FROM staff_work_item_link l WHERE l.tenant_id=w.tenant_id
                    AND l.work_item_id=w.id AND l.entity_type='requirement')
                  AND NOT EXISTS(SELECT 1 FROM staff_demo_document b
                    WHERE b.work_item_id=w.id AND b.tenant_id=w.tenant_id)
                ORDER BY (w.status='done'),c.position LIMIT 1
            """),
                    params,
                )
            ).scalar_one_or_none()
        params.update(
            {
                "work": str(work or uuid4()),
                "title": bounded_document_label(doc["file_name"], prefix="Review "),
                "component": "Financial Aid" if board == "fa-docs" else "Enrollment",
            }
        )
        if work is None:
            params["key"] = "DOC-" + str(int(str(params["work"]).replace("-", "")[:16], 16))
            await connection.execute(
                text("""
                INSERT INTO staff_work_item(id,tenant_id,student_id,key,title,description,
                  status,priority,work_type,action_type,component,assignee_id)
                VALUES(CAST(:work AS uuid),CAST(:tenant AS uuid),CAST(:student AS uuid),:key,
                  :title,'Original uploaded; parsing remains simulated; awaiting staff review.',
                  'todo','medium','document_review','document_review',
                  :component,CAST(:staff AS uuid))
            """),
                params,
            )
            await connection.execute(
                text("""
                INSERT INTO staff_demo_board_card(tenant_id,work_item_id,staff_member_id,
                  template_key,preview_template_key,board_id,position,scenario_version)
                SELECT CAST(:tenant AS uuid),CAST(:work AS uuid),CAST(:staff AS uuid),
                  :key,CASE WHEN :board='fa-docs' THEN 'FIN-151' ELSE 'ENR-184' END,
                  :board,max(position)+1,min(scenario_version)
                FROM staff_demo_board_card WHERE tenant_id=CAST(:tenant AS uuid)
                  AND staff_member_id=CAST(:staff AS uuid)
            """),
                params,
            )
        await connection.execute(
            text("""
            UPDATE staff_work_item SET title=:title,source_type='document',
              source_id=CAST(:document AS uuid),
              status='todo',completed_at=NULL,cancelled_at=NULL,terminal_reason=NULL,
              version=version+1,updated_at=now(),due_at=now()+interval '2 days',
              description='Original uploaded; parsing remains simulated; awaiting staff review.'
            WHERE id=CAST(:work AS uuid) AND tenant_id=CAST(:tenant AS uuid)
        """),
            params,
        )
        await connection.execute(
            text("""
            INSERT INTO staff_demo_document(tenant_id,document_id,work_item_id,received_at)
            VALUES(CAST(:tenant AS uuid),CAST(:document AS uuid),
              CAST(:work AS uuid),clock_timestamp())
        """),
            params,
        )
        for entity, identifier in [
            ("document", document_id),
            ("requirement", params["requirement"]),
        ]:
            if identifier:
                await connection.execute(
                    text("""
                    INSERT INTO staff_work_item_link(id,tenant_id,work_item_id,
                      entity_type,entity_id,relationship)
                    VALUES(CAST(:id AS uuid),CAST(:tenant AS uuid),CAST(:work AS uuid),:entity,
                      CAST(:identifier AS uuid),'document_submission') ON CONFLICT DO NOTHING
                """),
                    {**params, "id": str(uuid4()), "entity": entity, "identifier": identifier},
                )
        await connection.execute(
            text("""
            UPDATE document_record SET status='under_review',processing_mode='manual_review',
              updated_at=now()
            WHERE tenant_id=CAST(:tenant AS uuid) AND id=CAST(:document AS uuid)
        """),
            params,
        )
        await connection.execute(
            text("""
            UPDATE student_requirement SET status='under_review',progress_percent=80,
              version=version+1,updated_at=now()
            WHERE tenant_id=CAST(:tenant AS uuid) AND id=CAST(:requirement AS uuid)
              AND status NOT IN ('completed','waived','not_applicable')
        """),
            params,
        )
        await connection.execute(
            text("""
            INSERT INTO staff_work_log(id,tenant_id,work_item_id,
              actor_type,actor_id,actor_name,action,message)
            SELECT CAST(:id AS uuid),CAST(:tenant AS uuid),CAST(:work AS uuid),'student',s.id,
              p.first_name || ' ' || p.last_name,'status_changed',:message
            FROM student s JOIN person p ON p.id=s.person_id AND p.tenant_id=s.tenant_id
            WHERE s.id=CAST(:student AS uuid) AND s.tenant_id=CAST(:tenant AS uuid)
        """),
            {
                **params,
                "id": str(uuid4()),
                "message": f"Uploaded {doc['file_name']}. Original stored; review pending.",
            },
        )
        payload = {
            "studentId": auth.student_id,
            "workItemId": params["work"],
            "documentId": document_id,
            "assigneeId": params["staff"],
            "storageConfirmed": True,
        }
        await portal._insert_audit(
            connection,
            auth,
            "document.demo_board_attached",
            "document_record",
            document_id,
            request_id,
            payload,
        )
        await portal._insert_outbox(
            connection,
            auth,
            "document.stored_for_review.v1",
            "document_record",
            document_id,
            2,
            request_id,
            payload,
        )
        return True
