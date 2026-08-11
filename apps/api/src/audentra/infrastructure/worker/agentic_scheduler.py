"""Bounded scheduled workflows for inbound communication and engagement signals."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.domain.agentic_workflows import InboundTriageCandidate, decide_inbound_action

_PENDING_INBOX_LIMIT = 25
_ACTION_RULE_CANDIDATE_LIMIT = 250
_INBOX_BODY_LIMIT = 12_000
_ACTIONABLE_TERMS = (
    "help",
    "question",
    "urgent",
    "deadline",
    "deposit",
    "payment",
    "invoice",
    "document",
    "transcript",
    "passport",
    "identity",
    "upload",
    "housing",
    "health",
    "review",
)
_HELP_TERMS = ("help", "question", "please", "can you", "how do i")


class AgenticWorkflowScheduler:
    """Run safe, repeatable scheduled workflows against durable DB records.

    Email/vendor adapters only need to insert ``inbox_event`` rows. This runner
    performs deterministic identity resolution and triage, records the run and
    tool evidence, and creates a reversible staff task. It never changes a
    requirement, payment, profile, or other official student record.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        uuid_factory: Callable[[], UUID] = uuid4,
        logger: logging.Logger | None = None,
        inbox_limit: int = _PENDING_INBOX_LIMIT,
    ) -> None:
        if not 1 <= inbox_limit <= 100:
            raise ValueError("inbox_limit must be between 1 and 100")
        self._engine = engine
        self._uuid_factory = uuid_factory
        self._logger = logger or logging.getLogger(__name__)
        self._inbox_limit = inbox_limit

    async def run_once(self) -> int:
        processed = await self._process_inbox_events()
        scanned = await self._scan_engagement()
        matched = await self._run_action_rules()
        lifecycle_actions = await self._run_due_work_item_actions()
        archived_conversations = await self._archive_inactive_support_conversations()
        return processed + scanned + matched + lifecycle_actions + archived_conversations

    async def _archive_inactive_support_conversations(self) -> int:
        """Remove quiet support threads from active inboxes without losing evidence.

        The conversation rows, participant messages, delivery records, and work
        logs remain intact.  Only the active-inbox projection is retired after
        the agreed five-day inactivity period, so staff can still audit or
        recover the history without a destructive background delete.
        """

        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    WITH expired AS (
                      SELECT inquiry.id
                      FROM public.student_inquiry AS inquiry
                      WHERE inquiry.archived_at IS NULL
                        AND inquiry.status IN ('new', 'open', 'waiting_on_student', 'resolved')
                        AND inquiry.expires_at <= NOW()
                      ORDER BY inquiry.expires_at, inquiry.id
                      LIMIT 100
                      FOR UPDATE SKIP LOCKED
                    )
                    UPDATE public.student_inquiry AS inquiry
                    SET status = 'archived',
                        archived_at = NOW(),
                        version = inquiry.version + 1,
                        updated_at = NOW()
                    FROM expired
                    WHERE inquiry.id = expired.id
                    RETURNING inquiry.id, inquiry.tenant_id, inquiry.student_id
                    """
                )
            )
            archived = [dict(row) for row in result.mappings().all()]
            for inquiry in archived:
                work_item_result = await connection.execute(
                    text(
                        """
                        SELECT item.id, item.assignee_id, item.component
                        FROM public.staff_work_item AS item
                        WHERE item.tenant_id = :tenant_id
                          AND (
                            (item.source_type = 'message' AND item.source_id = :inquiry_id)
                            OR EXISTS (
                              SELECT 1
                              FROM public.staff_work_item_link AS link
                              WHERE link.tenant_id = item.tenant_id
                                AND link.work_item_id = item.id
                                AND link.entity_type = 'inquiry'
                                AND link.entity_id = :inquiry_id
                            )
                          )
                        ORDER BY item.created_at DESC, item.id DESC
                        LIMIT 1
                        """
                    ),
                    {
                        "tenant_id": inquiry["tenant_id"],
                        "inquiry_id": inquiry["id"],
                    },
                )
                work_item = work_item_result.mappings().first()
                if work_item is None:
                    await self._emit_support_conversation_archive_to_student(connection, inquiry)
                    continue
                await connection.execute(
                    text(
                        """
                        INSERT INTO public.staff_work_log (
                          id, tenant_id, work_item_id, actor_type, actor_id,
                          actor_name, action, message, occurred_at
                        ) VALUES (
                          :id, :tenant_id, :work_item_id, 'system', NULL,
                          'Audentra scheduler', 'status_changed', :message, NOW()
                        )
                        """
                    ),
                    {
                        "id": self._uuid_factory(),
                        "tenant_id": inquiry["tenant_id"],
                        "work_item_id": work_item["id"],
                        "message": (
                            "The support conversation left active inboxes after five days "
                            "without a participant message. Its full history is retained."
                        ),
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO public.staff_realtime_event (
                          id, tenant_id, event_type, resource_type, resource_id,
                          work_item_id, staff_member_id, team_component, tenant_wide,
                          payload, created_at
                        ) VALUES (
                          :id, :tenant_id, 'staff.inquiry.archived',
                          'student_inquiry', :inquiry_id, :work_item_id,
                          :staff_member_id, :team_component, false,
                          CAST(:payload AS jsonb), NOW()
                        )
                        """
                    ),
                    {
                        "id": self._uuid_factory(),
                        "tenant_id": inquiry["tenant_id"],
                        "inquiry_id": inquiry["id"],
                        "work_item_id": work_item["id"],
                        "staff_member_id": work_item["assignee_id"],
                        "team_component": (
                            None if work_item["assignee_id"] is not None else work_item["component"]
                        ),
                        "payload": _json(
                            {
                                "inquiryId": str(inquiry["id"]),
                                "workItemId": str(work_item["id"]),
                                "invalidate": ["workspace", "inquiries"],
                            }
                        ),
                    },
                )
                await self._emit_support_conversation_archive_to_student(connection, inquiry)
        return len(archived)

    async def _emit_support_conversation_archive_to_student(
        self,
        connection: Any,
        inquiry: dict[str, Any],
    ) -> None:
        """Invalidate the student's support view after its active thread expires."""

        await connection.execute(
            text(
                """
                INSERT INTO public.student_realtime_event (
                  id, tenant_id, student_id, event_type, resource_type,
                  resource_id, payload, created_at
                ) VALUES (
                  :id, :tenant_id, :student_id, 'student.inquiry.archived',
                  'student_inquiry', :inquiry_id,
                  CAST(:payload AS jsonb), NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": inquiry["tenant_id"],
                "student_id": inquiry["student_id"],
                "inquiry_id": inquiry["id"],
                "payload": _json(
                    {
                        "inquiryId": str(inquiry["id"]),
                        "href": f"/help?conversation={inquiry['id']}",
                        "invalidate": ["help", "messages", "bootstrap"],
                    }
                ),
            },
        )

    async def _run_due_work_item_actions(self) -> int:
        """Promote due follow-ups and escalate expired blockers/SLA work without AI."""

        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    WITH due AS (
                      SELECT item.id,
                             CASE
                               WHEN item.status = 'blocked'
                                AND item.blocker_review_at <= NOW()
                                 THEN 'blocked_review_due'
                               WHEN item.due_at <= NOW() AND item.escalated = false
                                 THEN 'sla_overdue'
                               ELSE 'follow_up_due'
                             END AS reason
                      FROM public.staff_work_item AS item
                      WHERE item.status NOT IN ('done', 'cancelled')
                        AND (
                          (
                            item.status = 'blocked'
                            AND item.blocker_review_at IS NOT NULL
                            AND item.blocker_review_at <= NOW()
                          )
                          OR (
                            item.due_at IS NOT NULL
                            AND item.due_at <= NOW()
                            AND item.escalated = false
                          )
                          OR (
                            item.status = 'follow_up_required'
                            AND item.follow_up_at IS NOT NULL
                            AND item.follow_up_at <= NOW()
                          )
                        )
                      ORDER BY
                        COALESCE(item.blocker_review_at, item.due_at, item.follow_up_at),
                        item.id
                      LIMIT 100
                      FOR UPDATE SKIP LOCKED
                    )
                    UPDATE public.staff_work_item AS item
                    SET status = CASE
                          WHEN due.reason = 'follow_up_due' THEN 'todo'
                          ELSE item.status
                        END,
                        attempt_count = CASE
                          WHEN due.reason = 'follow_up_due' THEN item.attempt_count + 1
                          ELSE item.attempt_count
                        END,
                        follow_up_at = CASE
                          WHEN due.reason = 'follow_up_due' THEN NULL
                          ELSE item.follow_up_at
                        END,
                        blocker_review_at = CASE
                          WHEN due.reason = 'blocked_review_due' THEN NULL
                          ELSE item.blocker_review_at
                        END,
                        escalated = CASE
                          WHEN due.reason IN ('blocked_review_due', 'sla_overdue') THEN true
                          ELSE item.escalated
                        END,
                        version = item.version + 1,
                        updated_at = NOW()
                    FROM due
                    WHERE item.id = due.id
                    RETURNING item.id, item.tenant_id, item.assignee_id,
                              item.component, item.title, item.version, due.reason
                    """
                )
            )
            due_items = [dict(row) for row in result.mappings().all()]
            for item in due_items:
                reason = str(item["reason"])
                if reason == "follow_up_due":
                    action = "status_changed"
                    title = "Scheduled follow-up is due"
                    message = (
                        "Scheduled follow-up returned this action to To Do. Reach out using "
                        "the selected channel and record the result."
                    )
                elif reason == "blocked_review_due":
                    action = "escalated"
                    title = "Blocked action needs review"
                    message = (
                        "The blocker review time passed. This action is escalated for team "
                        "or leader attention."
                    )
                else:
                    action = "escalated"
                    title = "Action SLA is overdue"
                    message = (
                        "The due time passed. This action is escalated for team or leader "
                        "attention."
                    )
                await connection.execute(
                    text(
                        """
                        INSERT INTO public.staff_work_log (
                          id, tenant_id, work_item_id, actor_type, actor_id,
                          actor_name, action, message, occurred_at
                        ) VALUES (
                          :id, :tenant_id, :work_item_id, 'system', NULL,
                          'Audentra scheduler', :action, :message, NOW()
                        )
                        """
                    ),
                    {
                        "id": self._uuid_factory(),
                        "tenant_id": item["tenant_id"],
                        "work_item_id": item["id"],
                        "action": action,
                        "message": message,
                    },
                )
                notification_result = await connection.execute(
                    text(
                        """
                        INSERT INTO public.staff_notification (
                          id, tenant_id, staff_member_id, team_component, kind,
                          title, body, resource_type, resource_id, dedupe_key,
                          created_at
                        ) VALUES (
                          :id, :tenant_id, :staff_member_id, :team_component,
                          :kind, :title, :body, 'staff_work_item', :resource_id,
                          :dedupe_key, NOW()
                        )
                        ON CONFLICT (tenant_id, dedupe_key) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "id": self._uuid_factory(),
                        "tenant_id": item["tenant_id"],
                        "staff_member_id": item["assignee_id"],
                        "team_component": (
                            None if item["assignee_id"] is not None else item["component"]
                        ),
                        "kind": reason,
                        "title": title,
                        "body": f"{item['title']}: {message}"[:2_000],
                        "resource_id": item["id"],
                        "dedupe_key": (f"work-lifecycle:{item['id']}:{reason}:{item['version']}")[
                            :240
                        ],
                    },
                )
                notification = notification_result.mappings().first()
                if notification is not None:
                    await connection.execute(
                        text(
                            """
                            INSERT INTO public.staff_realtime_event (
                              id, tenant_id, event_type, resource_type, resource_id,
                              work_item_id, staff_member_id, team_component, tenant_wide,
                              payload, created_at
                            ) VALUES (
                              :id, :tenant_id, 'staff.notification.created',
                              'staff_notification', :notification_id, :work_item_id,
                              :staff_member_id, :team_component, false,
                              CAST(:payload AS jsonb), NOW()
                            )
                            """
                        ),
                        {
                            "id": self._uuid_factory(),
                            "tenant_id": item["tenant_id"],
                            "notification_id": notification["id"],
                            "work_item_id": item["id"],
                            "staff_member_id": item["assignee_id"],
                            "team_component": (
                                None if item["assignee_id"] is not None else item["component"]
                            ),
                            "payload": _json(
                                {
                                    "notificationId": str(notification["id"]),
                                    "workItemId": str(item["id"]),
                                    "kind": reason,
                                    "title": title,
                                    "body": f"{item['title']}: {message}"[:2_000],
                                    "invalidate": ["notifications", "workspace"],
                                }
                            ),
                        },
                    )
        return len(due_items)

    async def _run_action_rules(self) -> int:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT id, tenant_id, code, name, signal_type, flow_kind,
                           requirement_code, lookahead_days, inactivity_days,
                           cadence_minutes, component, priority, action_type,
                           title_template, description_template
                    FROM public.staff_action_rule
                    WHERE enabled = true
                      AND (
                        last_evaluated_at IS NULL
                        OR last_evaluated_at <= NOW() - make_interval(mins => cadence_minutes)
                      )
                    ORDER BY COALESCE(last_evaluated_at, '-infinity'::timestamptz), id
                    LIMIT 20
                    """
                )
            )
            rules = [dict(row) for row in result.mappings().all()]

        matched = 0
        for rule in rules:
            candidates = await self._action_rule_candidates(rule)
            for candidate in candidates:
                if await self._create_rule_work_item(rule, candidate):
                    matched += 1
            if len(candidates) < _ACTION_RULE_CANDIDATE_LIMIT:
                async with self._engine.begin() as connection:
                    await connection.execute(
                        text(
                            """
                            UPDATE public.staff_action_rule
                            SET last_evaluated_at = NOW()
                            WHERE tenant_id = :tenant_id AND id = :rule_id
                              AND enabled = true
                            """
                        ),
                        {"tenant_id": rule["tenant_id"], "rule_id": rule["id"]},
                    )
        return matched

    async def _action_rule_candidates(self, rule: dict[str, Any]) -> list[dict[str, Any]]:
        if rule["signal_type"] == "requirement_due":
            query = """
                SELECT requirement.id AS subject_id, student.id AS student_id,
                       COALESCE(profile.preferred_name, person.preferred_name,
                                person.first_name) AS student_name,
                       definition.code AS requirement_code,
                       definition.title AS requirement_title,
                       requirement.due_at,
                       GREATEST(
                         0,
                         CEIL(EXTRACT(EPOCH FROM (requirement.due_at - NOW())) / 86400.0)
                       )::integer AS days_remaining,
                       'requirement:' || requirement.id::text || ':' ||
                         requirement.due_at::date::text AS window_key
                FROM public.student_requirement requirement
                JOIN public.enrollment_journey journey
                  ON journey.tenant_id = requirement.tenant_id
                 AND journey.id = requirement.journey_id
                JOIN public.student student
                  ON student.tenant_id = journey.tenant_id
                 AND student.id = journey.student_id
                JOIN public.person person
                  ON person.tenant_id = student.tenant_id
                 AND person.id = student.person_id
                LEFT JOIN public.student_profile profile
                  ON profile.tenant_id = student.tenant_id
                 AND profile.student_id = student.id
                JOIN public.requirement_definition_version definition
                  ON definition.tenant_id = requirement.tenant_id
                 AND definition.id = requirement.requirement_definition_version_id
                WHERE requirement.tenant_id = :tenant_id
                  AND requirement.retired_at IS NULL
                  AND requirement.status NOT IN ('completed', 'waived', 'not_applicable')
                  AND requirement.due_at >= NOW()
                  AND requirement.due_at < NOW() + make_interval(days => :lookahead_days + 1)
                  AND (
                    CAST(:flow_kind AS varchar) IS NULL
                    OR definition.flow_kind = CAST(:flow_kind AS varchar)
                  )
                  AND (
                    CAST(:requirement_code AS varchar) IS NULL
                    OR definition.code = CAST(:requirement_code AS varchar)
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.staff_action_rule_execution execution
                    WHERE execution.tenant_id = requirement.tenant_id
                      AND execution.rule_id = :rule_id
                      AND execution.student_id = student.id
                      AND execution.window_key = 'requirement:' || requirement.id::text || ':' ||
                        requirement.due_at::date::text
                  )
                ORDER BY requirement.due_at, student.id
                LIMIT :limit
            """
            parameters = {
                "tenant_id": rule["tenant_id"],
                "rule_id": rule["id"],
                "lookahead_days": int(rule["lookahead_days"] or 0),
                "flow_kind": rule["flow_kind"],
                "requirement_code": rule["requirement_code"],
                "limit": _ACTION_RULE_CANDIDATE_LIMIT,
            }
        else:
            query = """
                SELECT NULL::uuid AS subject_id, student.id AS student_id,
                       COALESCE(profile.preferred_name, person.preferred_name,
                                person.first_name) AS student_name,
                       NULL::varchar AS requirement_code,
                       NULL::varchar AS requirement_title,
                       NULL::timestamptz AS due_at,
                       NULL::integer AS days_remaining,
                       'inactive:' || COALESCE(
                         to_char(activity.last_meaningful_at AT TIME ZONE 'UTC',
                                 'YYYY-MM-DD"T"HH24:MI:SS.US'),
                         'never'
                       ) AS window_key
                FROM public.student student
                JOIN public.person person
                  ON person.tenant_id = student.tenant_id
                 AND person.id = student.person_id
                LEFT JOIN public.student_profile profile
                  ON profile.tenant_id = student.tenant_id
                 AND profile.student_id = student.id
                LEFT JOIN LATERAL (
                  SELECT MAX(event.occurred_at) FILTER (
                    WHERE event.event_name NOT LIKE '%viewed%'
                      AND event.event_name NOT LIKE '%visited%'
                  ) AS last_meaningful_at
                  FROM public.activity_event event
                  WHERE event.tenant_id = student.tenant_id
                    AND event.student_id = student.id
                ) activity ON true
                WHERE student.tenant_id = :tenant_id
                  AND (
                    CAST(:flow_kind AS varchar) IS NULL
                    OR (
                      CAST(:flow_kind AS varchar) = 'enrollment'
                      AND EXISTS (
                        SELECT 1 FROM public.enrollment_journey journey
                        WHERE journey.tenant_id = student.tenant_id
                          AND journey.student_id = student.id
                          AND journey.status NOT IN ('completed', 'cancelled')
                      )
                    )
                    OR (
                      CAST(:flow_kind AS varchar) = 'onboarding'
                      AND EXISTS (
                        SELECT 1 FROM public.student_onboarding onboarding
                        WHERE onboarding.tenant_id = student.tenant_id
                          AND onboarding.student_id = student.id
                          AND onboarding.status <> 'completed'
                      )
                    )
                  )
                  AND (
                    activity.last_meaningful_at IS NULL
                    OR activity.last_meaningful_at <=
                       NOW() - make_interval(days => :inactivity_days)
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM public.staff_action_rule_execution execution
                    WHERE execution.tenant_id = student.tenant_id
                      AND execution.rule_id = :rule_id
                      AND execution.student_id = student.id
                      AND execution.window_key = 'inactive:' || COALESCE(
                        to_char(activity.last_meaningful_at AT TIME ZONE 'UTC',
                                'YYYY-MM-DD"T"HH24:MI:SS.US'),
                        'never'
                      )
                  )
                ORDER BY activity.last_meaningful_at NULLS FIRST, student.id
                LIMIT :limit
            """
            parameters = {
                "tenant_id": rule["tenant_id"],
                "rule_id": rule["id"],
                "inactivity_days": int(rule["inactivity_days"] or 1),
                "flow_kind": rule["flow_kind"],
                "limit": _ACTION_RULE_CANDIDATE_LIMIT,
            }
        async with self._engine.connect() as connection:
            result = await connection.execute(text(query), parameters)
            return [dict(row) for row in result.mappings().all()]

    async def _create_rule_work_item(
        self,
        rule: dict[str, Any],
        candidate: dict[str, Any],
    ) -> bool:
        execution_id = self._uuid_factory()
        work_item_id = self._uuid_factory()
        student_name = str(candidate["student_name"])
        context = {
            "studentName": student_name,
            "requirementTitle": str(candidate.get("requirement_title") or "requirement"),
            "requirementCode": str(candidate.get("requirement_code") or ""),
            "dueDate": _iso(_as_utc(candidate.get("due_at"))) or "not set",
            "daysRemaining": str(candidate.get("days_remaining") or 0),
            "inactivityDays": str(rule.get("inactivity_days") or 0),
        }
        title = _render_rule_template(str(rule["title_template"]), context, 240)
        description = _render_rule_template(str(rule["description_template"]), context, 2_000)
        evidence = {
            "signalType": str(rule["signal_type"]),
            "ruleCode": str(rule["code"]),
            "requirementCode": candidate.get("requirement_code"),
            "dueAt": _iso(_as_utc(candidate.get("due_at"))),
            "daysRemaining": candidate.get("days_remaining"),
            "inactivityDays": rule.get("inactivity_days"),
        }
        async with self._engine.begin() as connection:
            execution_result = await connection.execute(
                text(
                    """
                    INSERT INTO public.staff_action_rule_execution (
                      id, tenant_id, rule_id, student_id, subject_id,
                      window_key, evidence, matched_at
                    ) VALUES (
                      :id, :tenant_id, :rule_id, :student_id, :subject_id,
                      :window_key, CAST(:evidence AS jsonb), NOW()
                    )
                    ON CONFLICT (tenant_id, rule_id, student_id, window_key)
                    DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": execution_id,
                    "tenant_id": rule["tenant_id"],
                    "rule_id": rule["id"],
                    "student_id": candidate["student_id"],
                    "subject_id": candidate.get("subject_id"),
                    "window_key": candidate["window_key"],
                    "evidence": _json(evidence),
                },
            )
            if execution_result.first() is None:
                return False
            assignee_result = await connection.execute(
                text(
                    """
                    SELECT id FROM public.staff_member
                    WHERE tenant_id = :tenant_id AND active = true
                      AND component = :component
                    ORDER BY display_name, id
                    LIMIT 1
                    """
                ),
                {"tenant_id": rule["tenant_id"], "component": rule["component"]},
            )
            assignee = assignee_result.mappings().first()
            due_at = candidate.get("due_at") or datetime.now(UTC) + timedelta(days=1)
            await connection.execute(
                text(
                    """
                    INSERT INTO public.staff_work_item (
                      id, tenant_id, student_id, key, title, description,
                      status, priority, work_type, component, due_at, escalated,
                      assignee_id, source_type, source_id, version, action_type,
                      created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :student_id, :key, :title, :description,
                      'todo', :priority, 'enrollment', :component, :due_at, false,
                      :assignee_id, 'scheduled_rule', :source_id, 1, :action_type,
                      NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": work_item_id,
                    "tenant_id": rule["tenant_id"],
                    "student_id": candidate["student_id"],
                    "key": f"RULE-{str(work_item_id).replace('-', '')[:12].upper()}",
                    "title": title,
                    "description": description,
                    "priority": rule["priority"],
                    "component": rule["component"],
                    "due_at": due_at,
                    "assignee_id": assignee["id"] if assignee is not None else None,
                    "source_id": execution_id,
                    "action_type": rule["action_type"],
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE public.staff_action_rule_execution
                    SET work_item_id = :work_item_id
                    WHERE tenant_id = :tenant_id AND id = :execution_id
                    """
                ),
                {
                    "tenant_id": rule["tenant_id"],
                    "execution_id": execution_id,
                    "work_item_id": work_item_id,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO public.staff_work_log (
                      id, tenant_id, work_item_id, actor_type, actor_id,
                      actor_name, action, message, occurred_at
                    ) VALUES (
                      :id, :tenant_id, :work_item_id, 'system', NULL, 'System',
                      'scheduled_rule_matched', :message, NOW()
                    )
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": rule["tenant_id"],
                    "work_item_id": work_item_id,
                    "message": f"Scheduled rule {rule['name']} matched current student facts.",
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO public.staff_notification (
                      id, tenant_id, staff_member_id, team_component, kind,
                      title, body, resource_type, resource_id, dedupe_key,
                      created_at
                    ) VALUES (
                      :id, :tenant_id, :staff_member_id, :team_component,
                      'scheduled_action', :title, :body, 'staff_work_item',
                      :resource_id, :dedupe_key, NOW()
                    )
                    ON CONFLICT (tenant_id, dedupe_key) DO NOTHING
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": rule["tenant_id"],
                    "staff_member_id": assignee["id"] if assignee is not None else None,
                    "team_component": (None if assignee is not None else rule["component"]),
                    "title": title,
                    "body": f"{student_name}: {description}"[:2_000],
                    "resource_id": work_item_id,
                    "dedupe_key": f"scheduled-rule:{execution_id}",
                },
            )
        return True

    async def _process_inbox_events(self) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    WITH pending AS (
                      SELECT id
                      FROM public.inbox_event
                      WHERE status = 'received'
                         OR (
                           status = 'processing'
                           AND updated_at < NOW() - INTERVAL '15 minutes'
                         )
                      ORDER BY occurred_at, id
                      LIMIT :limit
                      FOR UPDATE SKIP LOCKED
                    )
                    UPDATE public.inbox_event AS event
                    SET status = 'processing', updated_at = NOW(), failure_code = NULL
                    FROM pending
                    WHERE event.id = pending.id
                    RETURNING event.id, event.tenant_id, event.provider,
                              event.external_message_id, event.sender_address,
                              event.subject, event.body_excerpt, event.occurred_at
                    """
                ),
                {"limit": self._inbox_limit},
            )
            events = [dict(row) for row in result.mappings().all()]

        processed = 0
        for event in events:
            try:
                await self._triage_inbox_event(event)
                processed += 1
            except Exception:
                self._logger.exception(
                    "agentic_inbox_triage_failed",
                    extra={
                        "inbox_event_id": str(event["id"]),
                        "tenant_id": str(event["tenant_id"]),
                    },
                )
                await self._mark_inbox_failed(event["id"])
        return processed

    async def _triage_inbox_event(self, event: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        tenant_id = str(event["tenant_id"])
        inbox_id = str(event["id"])
        sender = str(event.get("sender_address") or "").strip().lower()
        subject = _bounded_text(str(event.get("subject") or ""), 500)
        body = _bounded_text(str(event.get("body_excerpt") or ""), _INBOX_BODY_LIMIT)
        searchable = f"{subject} {body}".lower()
        correlation_id = f"inbox:{inbox_id}"[:160]

        async with self._engine.begin() as connection:
            student_result = await connection.execute(
                text(
                    """
                    SELECT account.student_id
                    FROM public.credential_account AS account
                    WHERE account.tenant_id = :tenant_id
                      AND account.email_normalized = :sender
                      AND account.status = 'active'
                    """
                ),
                {"tenant_id": tenant_id, "sender": sender},
            )
            student_row = student_result.mappings().first()
            student_id = str(student_row["student_id"]) if student_row is not None else None
            communication_id = self._uuid_factory()
            communication_result = await connection.execute(
                text(
                    """
                    INSERT INTO public.communication_event (
                      id, tenant_id, inbox_event_id, student_id, channel, direction,
                      external_thread_id, subject, body_excerpt, metadata,
                      resolution_status, occurred_at
                    )
                    SELECT :id, event.tenant_id, event.id, CAST(:student_id AS uuid),
                           'email', 'inbound',
                           event.external_thread_id, event.subject, event.body_excerpt,
                           CAST(:metadata AS jsonb),
                           CASE
                             WHEN CAST(:student_id AS uuid) IS NULL THEN 'ambiguous'
                             ELSE 'unresolved'
                           END,
                           event.occurred_at
                    FROM public.inbox_event AS event
                    WHERE event.id = :inbox_event_id
                    ON CONFLICT (inbox_event_id) DO UPDATE
                    SET student_id = EXCLUDED.student_id
                    RETURNING id
                    """
                ),
                {
                    "id": communication_id,
                    "tenant_id": tenant_id,
                    "inbox_event_id": inbox_id,
                    "student_id": student_id,
                    "metadata": _json({"classificationMode": "deterministic-v1"}),
                },
            )
            communication_row = communication_result.mappings().first()
            if communication_row is None:
                raise RuntimeError(f"Communication event was not created for {inbox_id}")
            communication_id = communication_row["id"]

            run_id = self._uuid_factory()
            await connection.execute(
                text(
                    """
                    INSERT INTO public.agent_run (
                      id, tenant_id, feature, trigger_type, trigger_event_id,
                      actor_type, student_id, provider, model, status,
                      correlation_id, created_at, started_at
                    ) VALUES (
                      :id, :tenant_id, 'inbound_communication_triage', 'integration',
                      :trigger_event_id, 'system', :student_id, 'deterministic',
                      'rules-v1', 'running', :correlation_id, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": run_id,
                    "tenant_id": tenant_id,
                    "trigger_event_id": inbox_id,
                    "student_id": student_id,
                    "correlation_id": correlation_id,
                },
            )

            due_at, blocking, existing_work_item_id, existing_inquiry_id = (None, False, None, None)
            if student_id is not None:
                due_at, blocking = await self._student_deadline_facts(
                    connection, tenant_id, student_id
                )
                existing_work_item_id = await self._existing_work_item(
                    connection, tenant_id, student_id
                )
                existing_inquiry_id = await self._existing_inquiry(
                    connection, tenant_id, student_id
                )

            candidate = InboundTriageCandidate(
                student_resolved=student_id is not None,
                actionable=any(term in searchable for term in _ACTIONABLE_TERMS),
                existing_work_item_id=existing_work_item_id,
                existing_inquiry_id=existing_inquiry_id,
                due_at=due_at,
                blocking=blocking,
                explicit_help_request=any(term in searchable for term in _HELP_TERMS),
                priority_evidence=("requirement_deadline",) if due_at is not None else (),
                requires_human_review=True,
            )
            decision = decide_inbound_action(candidate, now=now)
            target_id: str | None = None
            if decision.action == "append_to_existing_task" and existing_work_item_id is not None:
                target_id = existing_work_item_id
                await self._append_work_item_context(
                    connection,
                    tenant_id,
                    existing_work_item_id,
                    communication_id,
                    body,
                    decision.priority,
                )
            elif (
                decision.action == "append_to_existing_inquiry" and existing_inquiry_id is not None
            ):
                await self._touch_inquiry(
                    connection, tenant_id, existing_inquiry_id, decision.priority
                )
                target_id = await self._create_staff_task(
                    connection,
                    tenant_id=tenant_id,
                    student_id=student_id,
                    inbox_id=inbox_id,
                    communication_id=str(communication_id),
                    subject=subject or "Inbound student communication",
                    body=body,
                    priority=decision.priority,
                    searchable=searchable,
                    create_inquiry=False,
                )
            elif decision.action == "create_staff_task" or decision.action == "create_inquiry":
                target_id = await self._create_staff_task(
                    connection,
                    tenant_id=tenant_id,
                    student_id=student_id,
                    inbox_id=inbox_id,
                    communication_id=str(communication_id),
                    subject=subject or "Inbound student communication",
                    body=body,
                    priority=decision.priority,
                    searchable=searchable,
                    create_inquiry=decision.action == "create_inquiry",
                )

            if target_id is not None and student_id is not None:
                await self._attach_communication_to_interaction(
                    connection,
                    tenant_id=tenant_id,
                    student_id=student_id,
                    work_item_id=target_id,
                    communication_id=str(communication_id),
                    inbox_id=inbox_id,
                    objective=subject or "Respond to inbound student communication",
                )

            result = {
                "action": decision.action,
                "priority": decision.priority,
                "reasonCode": decision.reason_code,
                "requiresHumanReview": decision.requires_human_review,
                "studentResolved": student_id is not None,
                "targetId": target_id,
            }
            await connection.execute(
                text(
                    """
                    INSERT INTO public.agent_tool_call (
                      id, tenant_id, agent_run_id, sequence, tool_name,
                      authorization_scope, arguments_hash, result_status, created_at
                    ) VALUES (
                      :id, :tenant_id, :agent_run_id, 1, 'inbox.triage',
                      'tenant:communication:triage', :arguments_hash, 'succeeded', NOW()
                    )
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": tenant_id,
                    "agent_run_id": run_id,
                    "arguments_hash": _hash_payload({"inboxEventId": inbox_id, "subject": subject}),
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE public.agent_run
                    SET status = 'succeeded', result = CAST(:result AS jsonb), completed_at = NOW()
                    WHERE id = :id AND tenant_id = :tenant_id
                    """
                ),
                {"id": run_id, "tenant_id": tenant_id, "result": _json(result)},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO public.agent_action_proposal (
                      id, tenant_id, agent_run_id, student_id, action_type,
                      target_type, target_id, payload, rationale, status, executed_at
                    ) VALUES (
                      :id, :tenant_id, :agent_run_id, :student_id, :action_type,
                      :target_type, :target_id, CAST(:payload AS jsonb), :rationale,
                      CAST(:status AS varchar(24)),
                      CASE
                        WHEN CAST(:status AS varchar(24)) = 'executed' THEN NOW()
                        ELSE NULL
                      END
                    )
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": tenant_id,
                    "agent_run_id": run_id,
                    "student_id": student_id,
                    "action_type": decision.action,
                    "target_type": "staff_work_item" if target_id is not None else None,
                    "target_id": target_id,
                    "payload": _json(result),
                    "rationale": decision.reason_code,
                    "status": "executed" if target_id is not None else "rejected",
                },
            )
            resolution = (
                "ambiguous"
                if decision.action == "human_triage"
                else ("ignored" if decision.action == "record_only" else "resolved")
            )
            await connection.execute(
                text(
                    """
                    UPDATE public.communication_event
                    SET resolution_status = :resolution
                    WHERE tenant_id = :tenant_id AND id = :communication_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "communication_id": communication_id,
                    "resolution": resolution,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE public.inbox_event
                    SET status = CASE
                                   WHEN :resolution = 'ambiguous' THEN 'needs_triage'
                                   ELSE 'processed'
                                 END,
                        updated_at = NOW(), failure_code = NULL
                    WHERE tenant_id = :tenant_id AND id = :inbox_id
                    """
                ),
                {"tenant_id": tenant_id, "inbox_id": inbox_id, "resolution": resolution},
            )

    async def _create_staff_task(
        self,
        connection: Any,
        *,
        tenant_id: str,
        student_id: str | None,
        inbox_id: str,
        communication_id: str,
        subject: str,
        body: str,
        priority: str,
        searchable: str,
        create_inquiry: bool,
    ) -> str | None:
        if student_id is None:
            return None
        component = _component_for(searchable)
        assignee_result = await connection.execute(
            text(
                """
                SELECT id FROM public.staff_member
                WHERE tenant_id = :tenant_id AND active = true
                ORDER BY CASE WHEN component = :component THEN 0 ELSE 1 END, display_name, id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "component": component},
        )
        assignee = assignee_result.mappings().first()
        if create_inquiry:
            inquiry_result = await connection.execute(
                text(
                    """
                    INSERT INTO public.student_inquiry (
                      id, tenant_id, student_id, topic_code, subject, message,
                      status, priority, assignee_id, version
                    ) VALUES (
                      :id, :tenant_id, :student_id, :topic_code, :subject, :message,
                      'new', :priority, :assignee_id, 1
                    ) RETURNING id
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": tenant_id,
                    "student_id": student_id,
                    "topic_code": _topic_for(searchable),
                    "subject": _bounded_text(subject, 240),
                    "message": _bounded_text(body or subject, 500),
                    "priority": priority,
                    "assignee_id": assignee["id"] if assignee is not None else None,
                },
            )
            _ = inquiry_result.mappings().first()

        work_item_id = self._uuid_factory()
        insert_result = await connection.execute(
            text(
                """
                INSERT INTO public.staff_work_item (
                  id, tenant_id, student_id, key, title, description, status, priority,
                  work_type, component, due_at, escalated, assignee_id,
                  source_type, source_id, version
                ) VALUES (
                  :id, :tenant_id, :student_id, :key, :title, :description, 'todo', :priority,
                  'communication', :component, NULL, false, :assignee_id, 'message', :source_id, 1
                )
                ON CONFLICT (tenant_id, source_type, source_id)
                WHERE source_type IS NOT NULL AND source_id IS NOT NULL
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": work_item_id,
                "tenant_id": tenant_id,
                "student_id": student_id,
                "key": f"INBOX-{inbox_id.replace('-', '')[:12].upper()}",
                "title": _bounded_text(subject, 240),
                "description": _bounded_text(body or "Review the inbound communication.", 4_000),
                "priority": priority,
                "component": component,
                "assignee_id": assignee["id"] if assignee is not None else None,
                "source_id": inbox_id,
            },
        )
        inserted = insert_result.mappings().first()
        if inserted is None:
            existing = await connection.execute(
                text(
                    """
                    SELECT id FROM public.staff_work_item
                    WHERE tenant_id = :tenant_id
                      AND source_type = 'message' AND source_id = :source_id
                    """
                ),
                {"tenant_id": tenant_id, "source_id": inbox_id},
            )
            existing_row = existing.mappings().first()
            return str(existing_row["id"]) if existing_row is not None else None
        work_item_id = inserted["id"]
        await connection.execute(
            text(
                """
                INSERT INTO public.staff_work_item_link (
                  id, tenant_id, work_item_id, entity_type, entity_id, relationship
                ) VALUES
                  (:link_id, :tenant_id, :work_item_id, 'communication',
                   :communication_id, 'source'),
                  (:inbox_link_id, :tenant_id, :work_item_id, 'inbox_event', :inbox_id, 'source')
                ON CONFLICT (tenant_id, work_item_id, entity_type, entity_id) DO NOTHING
                """
            ),
            {
                "link_id": self._uuid_factory(),
                "inbox_link_id": self._uuid_factory(),
                "tenant_id": tenant_id,
                "work_item_id": work_item_id,
                "communication_id": communication_id,
                "inbox_id": inbox_id,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO public.staff_work_log (
                  id, tenant_id, work_item_id, actor_type, actor_id, actor_name,
                  action, message, occurred_at
                ) VALUES (
                  :id, :tenant_id, :work_item_id, 'system', NULL, 'Audentra workflow',
                  'created', 'Created from inbound communication triage.', NOW()
                )
                """
            ),
            {"id": self._uuid_factory(), "tenant_id": tenant_id, "work_item_id": work_item_id},
        )
        return str(work_item_id)

    async def _attach_communication_to_interaction(
        self,
        connection: Any,
        *,
        tenant_id: str,
        student_id: str,
        work_item_id: str,
        communication_id: str,
        inbox_id: str,
        objective: str,
    ) -> None:
        interaction_result = await connection.execute(
            text(
                """
                SELECT id, source_version
                FROM public.staff_interaction
                WHERE tenant_id = :tenant_id AND work_item_id = :work_item_id
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"tenant_id": tenant_id, "work_item_id": work_item_id},
        )
        interaction = interaction_result.mappings().first()
        if interaction is None:
            interaction_id = self._uuid_factory()
            source_version = 1
            await connection.execute(
                text(
                    """
                    INSERT INTO public.staff_interaction (
                      id, tenant_id, student_id, work_item_id, objective,
                      status, selected_channel, source_version,
                      covered_source_version, version, quiet_until,
                      last_activity_at, created_by, request_key, created_at, updated_at
                    ) VALUES (
                      :id, :tenant_id, :student_id, :work_item_id, :objective,
                      'enrichment_pending', 'email', :source_version,
                      0, 1, NOW() + interval '5 minutes', NOW(), NULL,
                      :request_key, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": interaction_id,
                    "tenant_id": tenant_id,
                    "student_id": student_id,
                    "work_item_id": work_item_id,
                    "objective": _bounded_text(objective, 1_000),
                    "source_version": source_version,
                    "request_key": f"inbox-interaction:{inbox_id}",
                },
            )
        else:
            interaction_id = interaction["id"]
            source_version = int(interaction["source_version"]) + 1
            await connection.execute(
                text(
                    """
                    UPDATE public.staff_interaction
                    SET status = 'enrichment_pending', selected_channel = 'email',
                        source_version = :source_version,
                        quiet_until = LEAST(
                          NOW() + interval '5 minutes',
                          created_at + interval '15 minutes'
                        ),
                        last_activity_at = NOW(), version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND id = :interaction_id
                    """
                ),
                {
                    "source_version": source_version,
                    "tenant_id": tenant_id,
                    "interaction_id": interaction_id,
                },
            )

        await connection.execute(
            text(
                """
                UPDATE public.communication_event
                SET interaction_id = :interaction_id,
                    source_type = 'inbox_event',
                    source_id = :inbox_id,
                    source_sequence = :source_sequence,
                    request_key = :request_key,
                    delivery_status = 'received'
                WHERE tenant_id = :tenant_id AND id = :communication_id
                """
            ),
            {
                "interaction_id": interaction_id,
                "inbox_id": inbox_id,
                "source_sequence": source_version,
                "request_key": f"inbox:{inbox_id}",
                "tenant_id": tenant_id,
                "communication_id": communication_id,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO public.action_center_ai_job (
                  id, tenant_id, purpose, dedupe_key, student_id,
                  work_item_id, interaction_id, status,
                  requested_source_version, covered_source_version,
                  not_before, attempts, max_attempts, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, 'interaction_enrichment', :dedupe_key,
                  :student_id, :work_item_id, :interaction_id, 'pending',
                  :source_version, 0, NOW() + interval '5 minutes', 0, 5,
                  NOW(), NOW()
                )
                ON CONFLICT (tenant_id, purpose, dedupe_key)
                DO UPDATE SET
                  requested_source_version = GREATEST(
                    action_center_ai_job.requested_source_version,
                    EXCLUDED.requested_source_version
                  ),
                  status = CASE
                    WHEN action_center_ai_job.status = 'running' THEN 'running'
                    ELSE 'pending'
                  END,
                  not_before = LEAST(
                    GREATEST(
                      action_center_ai_job.not_before,
                      EXCLUDED.not_before
                    ),
                    action_center_ai_job.created_at + interval '15 minutes'
                  ),
                  completed_at = NULL,
                  last_error_code = NULL,
                  last_error_message = NULL,
                  updated_at = NOW()
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": tenant_id,
                "dedupe_key": f"interaction:{interaction_id}",
                "student_id": student_id,
                "work_item_id": work_item_id,
                "interaction_id": interaction_id,
                "source_version": source_version,
            },
        )

    async def _touch_inquiry(
        self, connection: Any, tenant_id: str, inquiry_id: str, priority: str
    ) -> None:
        await connection.execute(
            text(
                """
                UPDATE public.student_inquiry
                SET priority = CASE
                                 WHEN priority = 'urgent' OR :priority = 'urgent' THEN 'urgent'
                                 WHEN priority = 'high' OR :priority = 'high' THEN 'high'
                                 WHEN priority = 'medium' OR :priority = 'medium' THEN 'medium'
                                 ELSE 'low'
                               END,
                    updated_at = NOW(), version = version + 1
                WHERE tenant_id = :tenant_id AND id = :inquiry_id
                """
            ),
            {"tenant_id": tenant_id, "inquiry_id": inquiry_id, "priority": priority},
        )

    async def _append_work_item_context(
        self,
        connection: Any,
        tenant_id: str,
        work_item_id: str,
        communication_id: str,
        body: str,
        priority: str,
    ) -> None:
        await connection.execute(
            text(
                """
                UPDATE public.staff_work_item
                SET priority = CASE
                                 WHEN priority = 'urgent' OR :priority = 'urgent' THEN 'urgent'
                                 WHEN priority = 'high' OR :priority = 'high' THEN 'high'
                                 WHEN priority = 'medium' OR :priority = 'medium' THEN 'medium'
                                 ELSE 'low'
                               END,
                    updated_at = NOW(), version = version + 1
                WHERE tenant_id = :tenant_id AND id = :work_item_id
                """
            ),
            {
                "tenant_id": tenant_id,
                "work_item_id": work_item_id,
                "priority": priority,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO public.staff_work_item_link (
                  id, tenant_id, work_item_id, entity_type, entity_id, relationship
                ) VALUES (:id, :tenant_id, :work_item_id, 'communication',
                          :communication_id, 'update')
                ON CONFLICT (tenant_id, work_item_id, entity_type, entity_id) DO NOTHING
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": tenant_id,
                "work_item_id": work_item_id,
                "communication_id": communication_id,
            },
        )
        await connection.execute(
            text(
                """
                INSERT INTO public.staff_work_log (
                  id, tenant_id, work_item_id, actor_type, actor_id, actor_name,
                  action, message, occurred_at
                ) VALUES (
                  :id, :tenant_id, :work_item_id, 'system', NULL, 'Audentra workflow',
                  'commented', :message, NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": tenant_id,
                "work_item_id": work_item_id,
                "message": _bounded_text(body or "Received an additional student message.", 4_000),
            },
        )

    async def _student_deadline_facts(
        self, connection: Any, tenant_id: str, student_id: str
    ) -> tuple[Any, bool]:
        result = await connection.execute(
            text(
                """
                SELECT MIN(due_at) FILTER (WHERE due_at IS NOT NULL) AS due_at,
                       COUNT(*) FILTER (WHERE status = 'blocked') > 0 AS blocking
                FROM public.student_requirement
                WHERE tenant_id = :tenant_id
                  AND journey_id IN (
                    SELECT id FROM public.enrollment_journey
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                  )
                  AND status NOT IN ('completed', 'waived', 'not_applicable')
                """
            ),
            {"tenant_id": tenant_id, "student_id": student_id},
        )
        row = result.mappings().first()
        return (row["due_at"], bool(row["blocking"])) if row is not None else (None, False)

    async def _existing_work_item(
        self, connection: Any, tenant_id: str, student_id: str
    ) -> str | None:
        result = await connection.execute(
            text(
                """
                SELECT id FROM public.staff_work_item
                WHERE tenant_id = :tenant_id AND student_id = :student_id
                  AND status IN ('todo', 'in_progress', 'follow_up_required')
                  AND work_type = 'communication'
                ORDER BY updated_at DESC, id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "student_id": student_id},
        )
        row = result.mappings().first()
        return str(row["id"]) if row is not None else None

    async def _existing_inquiry(
        self, connection: Any, tenant_id: str, student_id: str
    ) -> str | None:
        result = await connection.execute(
            text(
                """
                SELECT id FROM public.student_inquiry
                WHERE tenant_id = :tenant_id AND student_id = :student_id
                  AND status IN ('new', 'open', 'waiting_on_student')
                ORDER BY updated_at DESC, id
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "student_id": student_id},
        )
        row = result.mappings().first()
        return str(row["id"]) if row is not None else None

    async def _mark_inbox_failed(self, inbox_id: object) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    UPDATE public.inbox_event
                    SET status = 'failed', failure_code = 'triage_failed', updated_at = NOW()
                    WHERE id = :id AND status = 'processing'
                    """
                ),
                {"id": inbox_id},
            )

    async def _scan_engagement(self) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT s.tenant_id, s.id AS student_id,
                           activity.last_active_at, activity.last_meaningful_action_at,
                           activity.current_step_code, activity.recent_upload_failures,
                           activity.help_requested, requirements.completion_percentage,
                           requirements.blocking_requirement_count, requirements.next_deadline,
                           inquiries.open_support_case_count
                    FROM public.student AS s
                    LEFT JOIN LATERAL (
                      SELECT MAX(a.occurred_at) AS last_active_at,
                             MAX(a.occurred_at) FILTER (
                               WHERE a.event_name NOT LIKE '%viewed%'
                                 AND a.event_name NOT LIKE '%visited%'
                             ) AS last_meaningful_action_at,
                             (ARRAY_AGG(
                               COALESCE(a.properties ->> 'stepCode', a.event_name)
                               ORDER BY a.occurred_at DESC
                             ))[1] AS current_step_code,
                             COUNT(*) FILTER (
                               WHERE a.event_name LIKE '%upload%failed%'
                                 AND a.occurred_at >= NOW() - INTERVAL '30 days'
                             )::integer AS recent_upload_failures,
                             BOOL_OR(
                               a.event_name LIKE '%help%'
                               AND a.occurred_at >= NOW() - INTERVAL '30 days'
                             ) AS help_requested
                      FROM public.activity_event AS a
                      WHERE a.tenant_id = s.tenant_id
                        AND a.student_id = s.id
                        AND a.occurred_at >= NOW() - INTERVAL '30 days'
                    ) AS activity ON true
                    LEFT JOIN LATERAL (
                      SELECT ROUND(
                               100.0 * COUNT(*) FILTER (WHERE r.status IN ('completed', 'waived'))
                               / NULLIF(COUNT(*) FILTER (WHERE r.status <> 'not_applicable'), 0)
                             )::smallint AS completion_percentage,
                             COUNT(*) FILTER (WHERE r.status = 'blocked')::integer
                               AS blocking_requirement_count,
                             MIN(r.due_at) FILTER (
                               WHERE r.due_at IS NOT NULL
                                 AND r.status NOT IN ('completed', 'waived', 'not_applicable')
                             ) AS next_deadline
                      FROM public.enrollment_journey AS j
                      JOIN public.student_requirement AS r
                        ON r.tenant_id = j.tenant_id AND r.journey_id = j.id
                      WHERE j.tenant_id = s.tenant_id AND j.student_id = s.id
                    ) AS requirements ON true
                    LEFT JOIN LATERAL (
                      SELECT COUNT(*)::integer AS open_support_case_count
                      FROM public.student_inquiry AS inquiry
                      WHERE inquiry.tenant_id = s.tenant_id
                        AND inquiry.student_id = s.id
                        AND inquiry.status <> 'resolved'
                    ) AS inquiries ON true
                    LEFT JOIN public.student_engagement_snapshot AS snapshot
                      ON snapshot.tenant_id = s.tenant_id
                     AND snapshot.student_id = s.id
                    WHERE snapshot.projected_at IS NULL
                       OR snapshot.projected_at < NOW() - INTERVAL '1 day'
                    """
                )
            )
            students = [dict(row) for row in result.mappings().all()]

        scanned = 0
        for student in students:
            await self._persist_engagement_snapshot(student)
            scanned += 1
        return scanned

    async def _persist_engagement_snapshot(self, student: dict[str, Any]) -> None:
        tenant_id = str(student["tenant_id"])
        student_id = str(student["student_id"])
        now = datetime.now(UTC)
        last_meaningful = _as_utc(student.get("last_meaningful_action_at"))
        next_deadline = _as_utc(student.get("next_deadline"))
        blocking = int(student.get("blocking_requirement_count") or 0)
        help_requested = bool(student.get("help_requested"))
        recent_upload_failures = int(student.get("recent_upload_failures") or 0)
        days_to_deadline = (next_deadline - now).days if next_deadline is not None else None
        inactive = last_meaningful is None or (now - last_meaningful).days >= 7
        intervention = (
            (inactive and blocking > 0)
            or (days_to_deadline is not None and days_to_deadline <= 7)
            or help_requested
        )
        reasons: list[str] = []
        if inactive:
            reasons.append("inactive")
        if blocking > 0:
            reasons.append("blocking_requirement")
        if days_to_deadline is not None and days_to_deadline <= 7:
            reasons.append("deadline_within_7_days")
        if help_requested:
            reasons.append("help_requested")
        priority = (
            "urgent"
            if days_to_deadline is not None and days_to_deadline <= 2
            else ("high" if intervention else "low")
        )
        run_id = self._uuid_factory()
        candidate_id = self._uuid_factory()
        dedupe_key = f"engagement:{now.date().isoformat()}"
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO public.student_engagement_snapshot (
                      tenant_id, student_id, snapshot_version, last_active_at,
                      last_meaningful_action_at, current_step_code, completion_percentage,
                      blocking_requirement_count, next_deadline, days_to_next_deadline,
                      recent_upload_failures, help_requested, open_support_case_count,
                      signals, projected_at
                    ) VALUES (
                      :tenant_id, :student_id, 1, :last_active_at,
                      :last_meaningful_action_at, :current_step_code, :completion_percentage,
                      :blocking_requirement_count, :next_deadline, :days_to_next_deadline,
                      :recent_upload_failures, :help_requested, :open_support_case_count,
                      CAST(:signals AS jsonb), NOW()
                    )
                    ON CONFLICT (tenant_id, student_id) DO UPDATE SET
                      snapshot_version = student_engagement_snapshot.snapshot_version + 1,
                      last_active_at = EXCLUDED.last_active_at,
                      last_meaningful_action_at = EXCLUDED.last_meaningful_action_at,
                      current_step_code = EXCLUDED.current_step_code,
                      completion_percentage = EXCLUDED.completion_percentage,
                      blocking_requirement_count = EXCLUDED.blocking_requirement_count,
                      next_deadline = EXCLUDED.next_deadline,
                      days_to_next_deadline = EXCLUDED.days_to_next_deadline,
                      recent_upload_failures = EXCLUDED.recent_upload_failures,
                      help_requested = EXCLUDED.help_requested,
                      open_support_case_count = EXCLUDED.open_support_case_count,
                      signals = EXCLUDED.signals,
                      projected_at = NOW()
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "student_id": student_id,
                    "last_active_at": student.get("last_active_at"),
                    "last_meaningful_action_at": student.get("last_meaningful_action_at"),
                    "current_step_code": student.get("current_step_code"),
                    "completion_percentage": student.get("completion_percentage"),
                    "blocking_requirement_count": blocking,
                    "next_deadline": student.get("next_deadline"),
                    "days_to_next_deadline": days_to_deadline,
                    "recent_upload_failures": recent_upload_failures,
                    "help_requested": help_requested,
                    "open_support_case_count": int(student.get("open_support_case_count") or 0),
                    "signals": _json({"reasons": reasons, "priority": priority}),
                },
            )
            if intervention:
                await connection.execute(
                    text(
                        """
                        INSERT INTO public.intervention_candidate (
                          id, tenant_id, student_id, trigger_code, dedupe_key,
                          priority, reason_codes, evidence, status
                        ) VALUES (
                          :id, :tenant_id, :student_id, 'engagement_scan', :dedupe_key,
                          :priority, CAST(:reason_codes AS jsonb), CAST(:evidence AS jsonb), 'new'
                        )
                        ON CONFLICT (tenant_id, student_id, dedupe_key) DO NOTHING
                        """
                    ),
                    {
                        "id": candidate_id,
                        "tenant_id": tenant_id,
                        "student_id": student_id,
                        "dedupe_key": dedupe_key,
                        "priority": priority,
                        "reason_codes": _json(reasons),
                        "evidence": _json(
                            {
                                "lastMeaningfulActionAt": _iso(last_meaningful),
                                "nextDeadline": _iso(next_deadline),
                                "blockingRequirementCount": blocking,
                                "recentUploadFailures": recent_upload_failures,
                                "helpRequested": help_requested,
                            }
                        ),
                    },
                )
            await connection.execute(
                text(
                    """
                    INSERT INTO public.agent_run (
                      id, tenant_id, feature, trigger_type, actor_type, student_id,
                      provider, model, status, result, correlation_id, started_at, completed_at
                    ) VALUES (
                      :id, :tenant_id, 'engagement_scan', 'scheduled', 'system', :student_id,
                      'deterministic', 'rules-v1', 'succeeded', CAST(:result AS jsonb),
                      :correlation_id, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": run_id,
                    "tenant_id": tenant_id,
                    "student_id": student_id,
                    "result": _json({"intervention": intervention, "reasons": reasons}),
                    "correlation_id": f"engagement:{student_id}:{now.date().isoformat()}",
                },
            )


def _bounded_text(value: str, limit: int) -> str:
    return value[:limit]


def _render_rule_template(template: str, context: dict[str, str], limit: int) -> str:
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace("{" + key + "}", value)
    return " ".join(rendered.split())[:limit]


def _component_for(searchable: str) -> str:
    if any(term in searchable for term in ("payment", "deposit", "invoice", "financial", "aid")):
        return "Financial Aid"
    if any(
        term in searchable for term in ("document", "transcript", "passport", "identity", "upload")
    ):
        return "Registrar"
    if any(term in searchable for term in ("health", "vaccine", "immun")):
        return "Student Health"
    return "Admissions"


def _topic_for(searchable: str) -> str:
    if any(term in searchable for term in ("payment", "deposit", "invoice", "financial", "aid")):
        return "payments"
    if any(
        term in searchable for term in ("document", "transcript", "passport", "identity", "upload")
    ):
        return "documents"
    return "support"


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _hash_payload(value: object) -> str:
    import hashlib

    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
