"""Demo identities and stored originals. Never executes mock workflow commands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError

from .work_board_sql import PROJECT_SQL

if TYPE_CHECKING:
    from .staff_repository import PostgresStaffRepository


class DemoTaskBoardProjection:
    def __init__(self, staff: PostgresStaffRepository) -> None:
        self.staff = staff

    async def read(self, auth: AuthContext, *, assigned_fallback: bool = False) -> dict[str, Any]:
        if auth.actor_type != "staff":
            raise ApiError(403, "STAFF_REQUIRED", "Staff access required")
        async with self.staff._engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('audentra.tenant_id', :tenant, true)"),
                {"tenant": auth.tenant_id},
            )
            params = {
                "tenant": auth.tenant_id,
                "actor": auth.actor_id,
                "fallback": assigned_fallback,
            }
            # The normal board has no curated membership; reuse the exact same
            # linked-record projection for canonically assigned work.
            membership_sql = "public.staff_demo_board_card"
            if assigned_fallback:
                project_sql = PROJECT_SQL.replace("item.", "assigned.")
                membership_sql = f"""(SELECT assigned.tenant_id,
                    assigned.assignee_id AS staff_member_id,
                    assigned.id AS work_item_id, assigned.key AS template_key,
                    NULL::text AS preview_template_key, {project_sql} AS board_id,
                    assigned.created_at AS position, NULL::timestamptz AS archived_at,
                    'canonical'::text AS scenario_version
                    FROM public.staff_work_item assigned
                    WHERE assigned.tenant_id=CAST(:tenant AS uuid)
                    AND assigned.assignee_id=CAST(:actor AS uuid))"""  # noqa: S608 — code-owned SQL
            member = (
                (
                    await connection.execute(
                        text("""SELECT id, display_name, title, component FROM public.staff_member
                    WHERE tenant_id=CAST(:tenant AS uuid) AND id=CAST(:actor AS uuid)
                      AND active=true"""),
                        params,
                    )
                )
                .mappings()
                .first()
            )
            if member is None:
                raise ApiError(403, "STAFF_REQUIRED", "An active staff profile is required")
            assignees = (
                (
                    await connection.execute(
                        text("""
                SELECT id,display_name FROM staff_member WHERE tenant_id=CAST(:tenant AS uuid)
                  AND active AND component=:component ORDER BY display_name
            """),
                        {**params, "component": member["component"]},
                    )
                )
                .mappings()
                .all()
            )
            rows = (
                (
                    await connection.execute(
                        text(f"""
                        SELECT COALESCE(c.preview_template_key,c.template_key) AS template_key,
                        c.board_id,c.position,c.scenario_version,
                        document.documents, activity.entries AS activity, requirements.entries
                        AS requirements,
                        conversations.entries AS conversations,
                        w.id,w.key,w.title,w.version,w.student_id,w.created_at,
                        w.priority,w.due_at,w.status,w.description,w.next_step,w.follow_up_at,
                        w.updated_at,w.work_type,w.assignee_id,
                        s.external_ref,p.first_name,p.last_name,s.class_year,
                        COALESCE(NULLIF(profile.preferred_name,''),p.first_name) AS preferred_name,
                        COALESCE(onboarding.payload->>'universityProgramName',
                          offer_program.name,'Program not assigned') AS program,
                        u.email,u.admit_term
                    FROM {membership_sql} c
                    JOIN public.staff_work_item w ON w.id=c.work_item_id
                      AND w.tenant_id=c.tenant_id
                      AND EXISTS(SELECT 1 FROM staff_member viewer
                      WHERE viewer.id=c.staff_member_id
                      AND viewer.tenant_id=w.tenant_id AND viewer.active
                      AND (w.assignee_id=viewer.id OR w.component=viewer.component))
                    JOIN public.student s ON s.id=w.student_id AND s.tenant_id=w.tenant_id
                    JOIN public.person p ON p.id=s.person_id AND p.tenant_id=s.tenant_id
                    LEFT JOIN public.student_profile profile ON profile.student_id=s.id
                      AND profile.tenant_id=s.tenant_id
                    LEFT JOIN public.student_onboarding onboarding ON onboarding.student_id=s.id
                      AND onboarding.tenant_id=s.tenant_id
                    LEFT JOIN university.student u ON u.id=s.id::text AND u.tenant_id=s.tenant_id
                    LEFT JOIN LATERAL (
                      SELECT program.name FROM public.admission_offer offer
                      JOIN public.program program ON program.id=offer.program_id
                        AND program.tenant_id=offer.tenant_id
                      WHERE offer.student_id=s.id AND offer.tenant_id=s.tenant_id
                      ORDER BY offer.created_at DESC,offer.id LIMIT 1
                    ) offer_program ON true
                    LEFT JOIN LATERAL (
                      SELECT jsonb_agg(jsonb_build_object(
                        'id',d.id,'fileName',d.file_name,'mimeType',d.mime_type,
                        'sizeBytes',d.size_bytes,'category',d.category,'uploadedAt',b.received_at,
                        'requirementId',d.requirement_id,'status',d.status,'extraction',d.extraction,
                        'updatedAt',d.updated_at,
                        'decisions',(SELECT COALESCE(jsonb_agg(jsonb_build_object(
                          'id',r.id,'decision',r.decision,'note',r.student_message,
                          'reviewerName',r.reviewer_display_name,'decidedAt',r.decided_at)
                          ORDER BY r.decided_at DESC),'[]'::jsonb) FROM document_review_decision r
                          WHERE r.tenant_id=d.tenant_id AND r.document_id=d.id),
                        'contentPath','/v1/staff/documents/' || d.id || '/content'
                      ) ORDER BY b.received_at DESC,d.id) AS documents
                      FROM staff_demo_document b JOIN document_record d
                        ON d.id=b.document_id AND d.tenant_id=b.tenant_id
                      WHERE b.work_item_id=w.id AND b.tenant_id=w.tenant_id
                        AND d.student_id=w.student_id AND d.superseded_at IS NULL
                    ) document ON true
                    LEFT JOIN LATERAL (
                      SELECT jsonb_agg(jsonb_build_object('id',l.id,'actor',l.actor_name,
                        'actorType',l.actor_type,'action',l.action,'message',l.message,
                        'createdAt',l.occurred_at) ORDER BY l.occurred_at DESC,l.id) AS entries
                      FROM staff_work_log l WHERE l.tenant_id=w.tenant_id AND l.work_item_id=w.id
                    ) activity ON true
                    LEFT JOIN LATERAL (
                      SELECT jsonb_agg(jsonb_build_object('id',r.id,'title',d.title,'status',
                      r.status,
                        'version',r.version,'code',d.code,'dueAt',r.due_at,
                        'category',d.input_config->>'document_category')
                        ORDER BY r.created_at,r.id)
                        AS entries
                      FROM staff_work_item_link l JOIN student_requirement r
                        ON r.id=l.entity_id AND r.tenant_id=l.tenant_id AND EXISTS(SELECT 1
                        FROM enrollment_journey j WHERE j.id=r.journey_id
                          AND j.tenant_id=r.tenant_id AND j.student_id=w.student_id)
                      JOIN requirement_definition_version d
                        ON d.id=r.requirement_definition_version_id AND d.tenant_id=r.tenant_id
                      WHERE l.work_item_id=w.id AND l.tenant_id=w.tenant_id AND
                      l.entity_type='requirement'
                    ) requirements ON true
                    LEFT JOIN LATERAL (
                      SELECT jsonb_agg(jsonb_build_object('id',i.id,'version',i.version,
                      'status',i.status,
                        'expiresAt',i.expires_at,'expired',i.expires_at<=now() OR
                        i.archived_at IS NOT NULL,
                        'messages',COALESCE((SELECT jsonb_agg(m.value ORDER BY m.at,m.id) FROM (
                          SELECT i.id,i.created_at AS
                          at,jsonb_build_object('id',i.id,'direction','student',
                            'authorName',p.first_name || ' ' || p.last_name,'body',i.message,
                            'createdAt',i.created_at) AS value WHERE i.initiator_type='student'
                          UNION ALL
                          SELECT r.id,r.created_at,jsonb_build_object('id',r.id,'direction','staff',
                            'authorName',s.display_name,'body',r.response_note,'createdAt',
                            r.created_at)
                          FROM student_inquiry_reply r JOIN staff_member s
                            ON s.id=r.staff_member_id AND s.tenant_id=r.tenant_id
                          WHERE r.tenant_id=i.tenant_id AND r.inquiry_id=i.id AND r.notify_student
                          UNION ALL
                          SELECT r.id,r.created_at,jsonb_build_object('id',r.id,'direction',
                          'student',
                            'authorName',p.first_name || ' ' || p.last_name,'body',r.body,
                            'createdAt',r.created_at)
                          FROM student_inquiry_student_reply r WHERE r.tenant_id=i.tenant_id
                          AND r.inquiry_id=i.id
                        ) m),'[]'::jsonb)) ORDER BY i.created_at DESC,i.id DESC) AS entries
                      FROM staff_work_item_link l JOIN student_inquiry i
                        ON i.id=l.entity_id AND i.tenant_id=l.tenant_id AND
                        i.student_id=w.student_id
                      WHERE l.tenant_id=w.tenant_id AND l.work_item_id=w.id AND
                      l.entity_type='inquiry'
                    ) conversations ON true
                    WHERE c.archived_at IS NULL AND c.tenant_id=CAST(:tenant AS uuid)
                      AND c.staff_member_id=CAST(:actor AS uuid)
                      AND (:fallback OR EXISTS (SELECT 1 FROM public.student_staff_assignment a
                        WHERE a.tenant_id=s.tenant_id AND a.student_id=s.id
                          AND a.staff_member_id=c.staff_member_id
                          AND a.role='primary_advisor' AND a.ended_at IS NULL))
                    ORDER BY c.position,w.key"""),  # noqa: S608 — code-owned SQL
                        params,
                    )
                )
                .mappings()
                .all()
            )
        if not rows and not assigned_fallback:
            raise ApiError(
                404, "DEMO_BOARD_NOT_CONFIGURED", "This staff demo board is not configured"
            )
        cards = []
        for row in rows:
            from audentra.domain.document_evidence import review_checks

            documents = row["documents"] or []
            for document in documents:
                document["checks"] = review_checks(
                    document.get("extraction") or {},
                    document["category"],
                    f"{row['first_name']} {row['last_name']}",
                )
            cards.append(
                {
                    "id": str(row["id"]),
                    "key": row["key"],
                    "title": row["title"],
                    "templateKey": row["template_key"],
                    "board": row["board_id"],
                    "version": row["version"],
                    "priority": row["priority"],
                    "dueAt": row["due_at"].isoformat() if row["due_at"] else None,
                    "status": row["status"],
                    "description": row["description"],
                    "nextStep": row["next_step"],
                    "followUpAt": row["follow_up_at"].isoformat() if row["follow_up_at"] else None,
                    "updatedAt": row["updated_at"].isoformat(),
                    "workType": row["work_type"],
                    "assigneeId": str(row["assignee_id"]) if row["assignee_id"] else None,
                    "createdAt": row["created_at"].isoformat(),
                    "documents": row["documents"] or [],
                    "activity": row["activity"] or [],
                    "requirements": row["requirements"] or [],
                    "conversations": row["conversations"] or [],
                    "student": {
                        "id": str(row["student_id"]),
                        "externalRef": row["external_ref"],
                        "name": f"{row['first_name']} {row['last_name']}",
                        "preferredName": row["preferred_name"],
                        "program": row["program"],
                        "classYear": row["class_year"],
                        "admitTerm": row["admit_term"],
                        "email": row["email"],
                    },
                }
            )
        return {
            "scenarioVersion": rows[0]["scenario_version"] if rows else "canonical",
            "staff": {
                "id": str(member["id"]),
                "name": member["display_name"],
                "title": member["title"],
                "component": member["component"],
            },
            "assignees": [{"id": str(m["id"]), "name": m["display_name"]} for m in assignees],
            "cards": cards,
            "total": len(cards),
            "studentCount": len({card["student"]["id"] for card in cards}),
        }
