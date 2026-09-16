"""Small, scoped commands for the progressively connected staff demonstration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError

if TYPE_CHECKING:
    from .staff_repository import PostgresStaffRepository


async def lock_demo_student(connection: AsyncConnection, tenant: str, student: str) -> None:
    # All demo uploads, reviews and participant messages take this before row locks.
    await connection.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended("
            ":tenant || '|demo-student|' || :student,0))"
        ),
        {"tenant": tenant, "student": student},
    )


async def lock_demo_card(
    connection: AsyncConnection, auth: AuthContext, work: str
) -> dict[str, Any]:
    if auth.actor_type != "staff":
        raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
    params = {"tenant": auth.tenant_id, "staff": auth.actor_id, "work": work}
    await connection.execute(text("SELECT set_config('audentra.tenant_id',:tenant,true)"), params)
    student = await connection.scalar(
        text("""
        SELECT w.student_id FROM staff_demo_board_card c JOIN staff_work_item w
          ON w.id=c.work_item_id AND w.tenant_id=c.tenant_id
        WHERE c.tenant_id=CAST(:tenant AS uuid) AND c.staff_member_id=CAST(:staff AS uuid)
          AND w.assignee_id=c.staff_member_id AND w.id=CAST(:work AS uuid)
    """),
        params,
    )
    if student is None:
        raise ApiError(404, "DEMO_CARD_NOT_FOUND", "This task is no longer assigned to you")
    await lock_demo_student(connection, auth.tenant_id, str(student))
    row = (
        (
            await connection.execute(
                text("""
        SELECT w.* FROM staff_demo_board_card c JOIN staff_work_item w
          ON w.id=c.work_item_id AND w.tenant_id=c.tenant_id
        JOIN student_staff_assignment a ON a.tenant_id=w.tenant_id AND a.student_id=w.student_id
          AND a.staff_member_id=c.staff_member_id AND a.role='primary_advisor' AND a.ended_at
          IS NULL
        JOIN staff_member s ON s.id=a.staff_member_id AND s.tenant_id=a.tenant_id AND s.active
        WHERE c.tenant_id=CAST(:tenant AS uuid) AND c.staff_member_id=CAST(:staff AS uuid)
          AND w.assignee_id=c.staff_member_id AND w.id=CAST(:work AS uuid)
        FOR UPDATE OF w FOR SHARE OF a,s
    """),
                params,
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ApiError(404, "DEMO_CARD_NOT_FOUND", "This task is no longer assigned to you")
    return dict(row)


class DemoTaskBoardCommands:
    def __init__(self, staff: PostgresStaffRepository) -> None:
        self.staff = staff

    async def write(
        self,
        auth: AuthContext,
        work: str,
        payload: dict[str, Any],
        request_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        self.staff._require_staff(auth)
        body = str(payload["body"]).strip()
        if not body:
            raise ApiError(400, "MESSAGE_REQUIRED", "Enter a message")
        kind = payload["kind"]

        async def handler(connection: AsyncConnection) -> dict[str, object]:
            card = await lock_demo_card(connection, auth, work)
            if int(card["version"]) != payload["expectedVersion"]:
                raise ApiError(
                    409,
                    "VERSION_CONFLICT",
                    "This task changed. Review the latest details and retry.",
                )
            student = str(card["student_id"])
            params: dict[str, Any] = {
                "tenant": auth.tenant_id,
                "staff": auth.actor_id,
                "student": student,
                "work": work,
                "body": body,
                "subject": str(card["title"])[:240],
            }
            inquiry_id = None
            if kind == "message":
                inquiry = (
                    (
                        await connection.execute(
                            text("""
                    SELECT i.*, (i.expires_at<=now() OR i.archived_at IS NOT NULL) AS expired
                    FROM staff_work_item_link l JOIN student_inquiry i
                      ON i.id=l.entity_id AND i.tenant_id=l.tenant_id
                    WHERE l.tenant_id=CAST(:tenant AS uuid) AND l.work_item_id=CAST(:work AS uuid)
                      AND l.entity_type='inquiry' AND i.student_id=CAST(:student AS uuid)
                    ORDER BY i.created_at DESC,i.id DESC LIMIT 1 FOR UPDATE OF i
                """),
                            params,
                        )
                    )
                    .mappings()
                    .first()
                )
                if (
                    inquiry is not None
                    and inquiry["expired"]
                    and not payload.get("startNewConversation")
                ):
                    raise ApiError(
                        409,
                        "SUPPORT_CONVERSATION_EXPIRED",
                        "This conversation expired. Start a new conversation to send your message.",
                    )
                if (
                    inquiry is not None
                    and not inquiry["expired"]
                    and payload.get("startNewConversation")
                ):
                    raise ApiError(
                        409,
                        "CONVERSATION_ACTIVE",
                        "This conversation is still active. Reply in this thread.",
                    )
                if inquiry is None or inquiry["expired"]:
                    inquiry_id = str(uuid4())
                    params["inquiry"] = inquiry_id
                    await connection.execute(
                        text("""
                        INSERT INTO student_inquiry(id,tenant_id,student_id,topic_code,
                        subject,message,
                          status,priority,assignee_id,initiator_type,last_message_at,expires_at)
                        VALUES(CAST(:inquiry AS uuid),CAST(:tenant AS uuid),CAST(:student AS uuid),
                          'support',:subject,:body,'waiting_on_student','medium',CAST(:staff
                          AS uuid),
                          'staff',now(),now()+interval '5 days')
                    """),
                        params,
                    )
                    await connection.execute(
                        text("""
                        INSERT INTO staff_work_item_link(id,tenant_id,work_item_id,
                        entity_type,entity_id,relationship)
                        VALUES(gen_random_uuid(),CAST(:tenant AS uuid),CAST(:work AS
                        uuid),'inquiry',
                          CAST(:inquiry AS uuid),'task_conversation')
                    """),
                        params,
                    )
                else:
                    inquiry_id = str(inquiry["id"])
                    params["inquiry"] = inquiry_id
                    await connection.execute(
                        text("""
                        UPDATE student_inquiry SET
                        status='waiting_on_student',assignee_id=CAST(:staff AS uuid),
                          version=version+1,last_message_at=now(),expires_at=now()+interval '5
                          days',updated_at=now()
                        WHERE tenant_id=CAST(:tenant AS uuid) AND id=CAST(:inquiry AS uuid)
                    """),
                        params,
                    )
                notification = await self.staff._insert_student_message(
                    connection,
                    auth=auth,
                    student_id=student,
                    subject=params["subject"],
                    body=body,
                    href=f"/help?conversation={inquiry_id}",
                )
                params["notification"] = str(notification["id"])
                await connection.execute(
                    text("""
                    INSERT INTO student_inquiry_reply(id,tenant_id,inquiry_id,student_id,
                    staff_member_id,
                      response_note,notify_student,student_message_id)
                    VALUES(gen_random_uuid(),CAST(:tenant AS uuid),CAST(:inquiry AS
                    uuid),CAST(:student AS uuid),
                      CAST(:staff AS uuid),:body,true,CAST(:notification AS uuid))
                """),
                    params,
                )
                actor_name = await self.staff._staff_name(connection, auth, auth.actor_id)
                await connection.execute(
                    text("""
                    UPDATE student_message SET sender_name=:name WHERE tenant_id=CAST(:tenant
                    AS uuid)
                      AND id=CAST(:notification AS uuid)
                """),
                    {**params, "name": actor_name},
                )
                interaction = await connection.scalar(
                    text("""
                    SELECT id FROM staff_interaction WHERE tenant_id=CAST(:tenant AS uuid)
                      AND work_item_id=CAST(:work AS uuid) ORDER BY created_at DESC,id DESC
                      LIMIT 1 FOR UPDATE
                """),
                    params,
                )
                params["interaction"] = str(interaction or uuid4())
                params["request_key"] = str(uuid4())
                if interaction is None:
                    await connection.execute(
                        text("""
                        INSERT INTO staff_interaction(id,tenant_id,student_id,work_item_id,
                        objective,
                          status,selected_channel,created_by,request_key)
                        VALUES(CAST(:interaction AS uuid),CAST(:tenant AS uuid),CAST(:student
                        AS uuid),
                          CAST(:work AS uuid),:subject,'collecting','portal',CAST(:staff AS
                          uuid),:request_key)
                    """),
                        params,
                    )
                sequence = await connection.scalar(
                    text("""
                    UPDATE staff_interaction SET source_version=source_version+1,version=version+1,
                      last_activity_at=now(),updated_at=now(),status='collecting'
                    WHERE tenant_id=CAST(:tenant AS uuid) AND id=CAST(:interaction AS uuid)
                    RETURNING source_version
                """),
                    params,
                )
                params["sequence"] = sequence
                await connection.execute(
                    text("""
                    INSERT INTO communication_event(id,tenant_id,student_id,interaction_id,
                    channel,direction,
                      subject,body_excerpt,metadata,resolution_status,occurred_at,source_type,
                      source_id,
                      source_sequence,request_key,delivery_status)
                    VALUES(gen_random_uuid(),CAST(:tenant AS uuid),CAST(:student AS
                    uuid),CAST(:interaction AS uuid),
                      'portal','outbound',:subject,:body,'{}','resolved',now(),
                      'staff_recorded_communication',
                      CAST(:notification AS uuid),:sequence,:request_key,'delivered')
                """),
                    params,
                )
            version = int(card["version"]) + 1
            await connection.execute(
                text("""
                UPDATE staff_work_item SET version=version+1,updated_at=now()
                WHERE tenant_id=CAST(:tenant AS uuid) AND id=CAST(:work AS uuid)
            """),
                params,
            )
            await self.staff._insert_work_log(
                connection,
                auth=auth,
                work_item_id=work,
                actor_name=await self.staff._staff_name(connection, auth, auth.actor_id),
                action="communication_recorded" if kind == "message" else "commented",
                message=body,
            )
            await self.staff._insert_audit(
                connection,
                auth=auth,
                request_id=request_id,
                action=f"staff.demo_{kind}",
                resource_type="staff_work_item",
                resource_id=work,
                metadata={"studentId": student, "inquiryId": inquiry_id},
            )
            await self.staff._insert_outbox(
                connection,
                auth=auth,
                request_id=request_id,
                event_name="staff.demo_task_updated.v1",
                aggregate_type="staff_work_item",
                aggregate_id=work,
                aggregate_version=version,
                data={"studentId": student, "workItemId": work, "inquiryId": inquiry_id},
            )
            return {"workItemId": work, "version": version, "inquiryId": inquiry_id}

        return await self.staff._run_idempotent(
            auth=auth,
            idempotency_key=idempotency_key or request_id,
            operation="staff.demo_task_write",
            request_payload={"workItemId": work, **payload},
            response_status=201,
            handler=handler,
        )
