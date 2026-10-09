"""Server-side authorization for original bytes and review actions."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError


async def require_document_access(
    connection: AsyncConnection, auth: AuthContext, document_id: str
) -> None:
    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
    allowed = await connection.scalar(
        text("""
        SELECT 1 FROM document_record d JOIN staff_member m
          ON m.tenant_id=d.tenant_id AND m.id=CAST(:actor AS uuid) AND m.active
        WHERE d.tenant_id=CAST(:tenant AS uuid) AND d.id=CAST(:document AS uuid)
          AND EXISTS(SELECT 1 FROM staff_work_item w WHERE w.tenant_id=d.tenant_id
            AND w.student_id=d.student_id AND (w.source_id=d.id OR EXISTS(
              SELECT 1 FROM staff_work_item_link l WHERE l.tenant_id=w.tenant_id
                AND l.work_item_id=w.id AND l.entity_type='document' AND l.entity_id=d.id))
            AND (w.assignee_id=m.id OR w.component=m.component))
    """),
        {"tenant": auth.tenant_id, "actor": auth.actor_id, "document": document_id},
    )
    if not allowed:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "Document not found in your authorized work")


async def require_work_access(connection: AsyncConnection, auth: AuthContext, work_id: str) -> None:
    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
    allowed = await connection.scalar(
        text("""
        SELECT 1 FROM staff_work_item w JOIN staff_member m
          ON m.tenant_id=w.tenant_id AND m.id=CAST(:actor AS uuid) AND m.active
        WHERE w.tenant_id=CAST(:tenant AS uuid) AND w.id=CAST(:work AS uuid)
          AND (w.assignee_id=m.id OR w.component=m.component)
    """),
        {"tenant": auth.tenant_id, "actor": auth.actor_id, "work": work_id},
    )
    if not allowed:
        raise ApiError(404, "STAFF_WORK_ITEM_NOT_FOUND", "Task not found in your authorized work")


async def require_student_access(
    connection: AsyncConnection, auth: AuthContext, student_id: str
) -> None:
    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
    allowed = await connection.scalar(
        text("""
        SELECT 1 FROM staff_member m WHERE m.tenant_id=CAST(:tenant AS uuid)
          AND m.id=CAST(:actor AS uuid) AND m.active AND (
            EXISTS(SELECT 1 FROM student_staff_assignment a WHERE a.tenant_id=m.tenant_id
             
                    AND a.staff_member_id=m.id AND a.student_id=CAST(:student AS uuid)
              AND a.ended_at IS NULL)
            OR EXISTS(SELECT 1 FROM staff_work_item w WHERE w.tenant_id=m.tenant_id
             
                    AND w.student_id=CAST(:student AS uuid)
              AND (w.assignee_id=m.id OR w.component=m.component)))
    """),
        {"tenant": auth.tenant_id, "actor": auth.actor_id, "student": student_id},
    )
    if not allowed:
        raise ApiError(
            404, "STAFF_STUDENT_NOT_FOUND", "Student not found in your authorized caseload"
        )
