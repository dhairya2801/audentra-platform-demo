# ruff: noqa: S608 -- the only interpolated SQL fragment is a module-owned static filter.
"""Durable, bounded Action Center AI enrichment jobs.

The runner never treats an AI projection as canonical enrollment state. It
claims a versioned source snapshot, releases the database transaction while the
provider runs, and then independently commits the interaction outcome and
student-summary projections. New evidence arriving during a call is preserved
and schedules another pass rather than being overwritten.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_TECHNICAL_IDENTIFIER_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.IGNORECASE
)


class ActionCenterEnrichmentGateway(Protocol):
    async def enrich_action_center(
        self,
        *,
        context: Mapping[str, Any],
        tenant_id: str,
        student_id: str,
        request_id: str,
        attempt: int = 1,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ClaimedEnrichmentJob:
    id: str
    tenant_id: str
    purpose: str
    student_id: str
    work_item_id: str | None
    interaction_id: str | None
    processing_source_version: int
    attempts: int


@dataclass(frozen=True, slots=True)
class EnrichmentSnapshot:
    context: dict[str, Any]
    source_ids: tuple[str, ...]
    source_revision: int
    base_summary_version: int


class ActionCenterEnrichmentRunner:
    """Claim and process a small concurrent batch of Action Center AI work."""

    def __init__(
        self,
        engine: AsyncEngine,
        gateway: ActionCenterEnrichmentGateway,
        *,
        worker_id: str,
        uuid_factory: Callable[[], UUID] = uuid4,
        logger: logging.Logger | None = None,
        batch_size: int = 6,
        concurrency: int = 3,
        lease_seconds: int = 180,
    ) -> None:
        if not 1 <= batch_size <= 50:
            raise ValueError("batch_size must be between 1 and 50")
        if not 1 <= concurrency <= 10:
            raise ValueError("concurrency must be between 1 and 10")
        if not 60 <= lease_seconds <= 900:
            raise ValueError("lease_seconds must be between 60 and 900")
        self._engine = engine
        self._gateway = gateway
        self._worker_id = worker_id[:160]
        self._uuid_factory = uuid_factory
        self._logger = logger or logging.getLogger(__name__)
        self._batch_size = batch_size
        self._concurrency = concurrency
        self._lease_seconds = lease_seconds

    async def run_once(self) -> int:
        jobs = await self._claim_jobs()
        if not jobs:
            return 0
        semaphore = asyncio.Semaphore(self._concurrency)

        async def process(job: ClaimedEnrichmentJob) -> bool:
            async with semaphore:
                return await self._process(job)

        outcomes = await asyncio.gather(*(process(job) for job in jobs))
        return sum(outcomes)

    async def _claim_jobs(self) -> list[ClaimedEnrichmentJob]:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    WITH due AS (
                      SELECT id
                      FROM public.action_center_ai_job
                      WHERE attempts < max_attempts
                        AND (
                          (
                            status IN ('pending', 'failed_retryable')
                            AND not_before <= NOW()
                          )
                          OR (
                            status = 'running'
                            AND lease_expires_at < NOW()
                          )
                        )
                      ORDER BY not_before, created_at, id
                      LIMIT :batch_size
                      FOR UPDATE SKIP LOCKED
                    )
                    UPDATE public.action_center_ai_job AS job
                    SET status = 'running',
                        processing_source_version = requested_source_version,
                        attempts = attempts + 1,
                        lease_owner = :worker_id,
                        lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                        last_error_code = NULL,
                        last_error_message = NULL,
                        updated_at = NOW()
                    FROM due
                    WHERE job.id = due.id
                    RETURNING job.id, job.tenant_id, job.purpose, job.student_id,
                              job.work_item_id, job.interaction_id,
                              job.processing_source_version, job.attempts
                    """
                ),
                {
                    "batch_size": self._batch_size,
                    "worker_id": self._worker_id,
                    "lease_seconds": self._lease_seconds,
                },
            )
            return [
                ClaimedEnrichmentJob(
                    id=str(row["id"]),
                    tenant_id=str(row["tenant_id"]),
                    purpose=str(row["purpose"]),
                    student_id=str(row["student_id"]),
                    work_item_id=(
                        str(row["work_item_id"]) if row["work_item_id"] is not None else None
                    ),
                    interaction_id=(
                        str(row["interaction_id"]) if row["interaction_id"] is not None else None
                    ),
                    processing_source_version=int(row["processing_source_version"]),
                    attempts=int(row["attempts"]),
                )
                for row in result.mappings().all()
            ]

    async def _process(self, job: ClaimedEnrichmentJob) -> bool:
        run_id = str(self._uuid_factory())
        try:
            await self._start_agent_run(job, run_id)
            snapshot = await self._load_snapshot(job)
            result = await self._gateway.enrich_action_center(
                context=snapshot.context,
                tenant_id=job.tenant_id,
                student_id=job.student_id,
                request_id=run_id,
                attempt=job.attempts,
            )
            await self._persist_success(job, run_id, snapshot, result)
            return True
        except Exception as error:
            self._logger.exception(
                "action_center_enrichment_failed",
                extra={
                    "job_id": job.id,
                    "tenant_id": job.tenant_id,
                    "purpose": job.purpose,
                },
            )
            await self._persist_failure(job, run_id, error)
            return False

    async def _start_agent_run(self, job: ClaimedEnrichmentJob, run_id: str) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    INSERT INTO public.agent_run (
                      id, tenant_id, feature, trigger_type, trigger_event_id,
                      actor_type, student_id, snapshot_version, prompt_version,
                      output_schema_version, status, correlation_id,
                      created_at, started_at
                    ) VALUES (
                      :id, :tenant_id, 'action_center_enrichment', 'event',
                      :trigger_event_id, 'system', :student_id, :snapshot_version,
                      'action-center-enrichment-v1', 'action-center-enrichment-v1',
                      'running', :correlation_id, NOW(), NOW()
                    )
                    """
                ),
                {
                    "id": run_id,
                    "tenant_id": job.tenant_id,
                    "trigger_event_id": job.id,
                    "student_id": job.student_id,
                    "snapshot_version": max(1, job.processing_source_version),
                    "correlation_id": f"action-center:{job.id}:{job.attempts}"[:160],
                },
            )

    async def _load_snapshot(self, job: ClaimedEnrichmentJob) -> EnrichmentSnapshot:
        async with self._engine.begin() as connection:
            student_result = await connection.execute(
                text(
                    """
                    SELECT student.id,
                           COALESCE(profile.preferred_name, person.preferred_name,
                                    person.first_name) AS preferred_name,
                           person.first_name, person.last_name, student.class_year,
                           profile.communication_preference,
                           latest_offer.program_name, latest_offer.offer_status,
                           journey.status AS journey_status,
                           journey.version AS journey_version,
                           onboarding.status AS onboarding_status,
                           onboarding.current_step AS onboarding_current_step,
                           onboarding.version AS onboarding_version,
                           GREATEST(
                             student.updated_at,
                             person.updated_at,
                             COALESCE(profile.updated_at, student.updated_at),
                             COALESCE(journey.updated_at, student.updated_at),
                             COALESCE(onboarding.updated_at, student.updated_at)
                           ) AS source_updated_at
                    FROM public.student AS student
                    JOIN public.person AS person
                      ON person.tenant_id = student.tenant_id
                     AND person.id = student.person_id
                    LEFT JOIN public.student_profile AS profile
                      ON profile.tenant_id = student.tenant_id
                     AND profile.student_id = student.id
                    LEFT JOIN LATERAL (
                      SELECT program.name AS program_name, offer.status AS offer_status
                      FROM public.admission_offer AS offer
                      JOIN public.program AS program
                        ON program.tenant_id = offer.tenant_id
                       AND program.id = offer.program_id
                      WHERE offer.tenant_id = student.tenant_id
                        AND offer.student_id = student.id
                      ORDER BY offer.created_at DESC, offer.id DESC
                      LIMIT 1
                    ) AS latest_offer ON true
                    LEFT JOIN LATERAL (
                      SELECT candidate.status, candidate.version, candidate.updated_at
                      FROM public.enrollment_journey AS candidate
                      WHERE candidate.tenant_id = student.tenant_id
                        AND candidate.student_id = student.id
                      ORDER BY candidate.updated_at DESC, candidate.id DESC
                      LIMIT 1
                    ) AS journey ON true
                    LEFT JOIN public.student_onboarding AS onboarding
                      ON onboarding.tenant_id = student.tenant_id
                     AND onboarding.student_id = student.id
                    WHERE student.tenant_id = :tenant_id
                      AND student.id = :student_id
                    """
                ),
                {"tenant_id": job.tenant_id, "student_id": job.student_id},
            )
            student = student_result.mappings().first()
            if student is None:
                raise RuntimeError("The student no longer exists")

            task_result = await connection.execute(
                text(
                    """
                    SELECT id, key, title, description, status, priority, work_type,
                           action_type, component, due_at, escalated, selected_channel,
                           attempt_count, follow_up_at, blocker_code, blocker_detail,
                           blocker_review_at, outcome_code, resolution_code, next_step,
                           terminal_reason, version, created_at, updated_at
                    FROM public.staff_work_item
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                    ORDER BY
                      CASE WHEN status IN ('done', 'cancelled') THEN 1 ELSE 0 END,
                      updated_at DESC, id DESC
                    LIMIT 50
                    """
                ),
                {"tenant_id": job.tenant_id, "student_id": job.student_id},
            )
            tasks = [dict(row) for row in task_result.mappings().all()]

            requirement_result = await connection.execute(
                text(
                    """
                    SELECT requirement.id, definition.code, definition.title,
                           definition.flow_kind, definition.blocking,
                           requirement.status, requirement.progress_percent,
                           requirement.due_at, requirement.version,
                           requirement.retired_at, requirement.retired_reason,
                           requirement.updated_at
                    FROM public.student_requirement AS requirement
                    JOIN public.enrollment_journey AS journey
                      ON journey.tenant_id = requirement.tenant_id
                     AND journey.id = requirement.journey_id
                    JOIN public.requirement_definition_version AS definition
                      ON definition.tenant_id = requirement.tenant_id
                     AND definition.id = requirement.requirement_definition_version_id
                    WHERE requirement.tenant_id = :tenant_id
                      AND journey.student_id = :student_id
                    ORDER BY definition.flow_kind, definition.display_order,
                             requirement.created_at, requirement.id
                    LIMIT 150
                    """
                ),
                {"tenant_id": job.tenant_id, "student_id": job.student_id},
            )
            requirements = [dict(row) for row in requirement_result.mappings().all()]

            document_result = await connection.execute(
                text(
                    """
                    SELECT id, file_name, category, status, processing_mode,
                           requirement_id, extraction, created_at, updated_at
                    FROM public.document_record
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 40
                    """
                ),
                {"tenant_id": job.tenant_id, "student_id": job.student_id},
            )
            documents = [dict(row) for row in document_result.mappings().all()]

            communication_parameters: dict[str, object] = {
                "tenant_id": job.tenant_id,
                "student_id": job.student_id,
                "source_version": job.processing_source_version,
            }
            communication_filter = ""
            if job.interaction_id is not None:
                communication_filter = """
                  AND communication.interaction_id = :interaction_id
                  AND communication.source_sequence <= :source_version
                """
                communication_parameters["interaction_id"] = job.interaction_id
            communication_result = await connection.execute(
                text(
                    f"""
                    SELECT communication.id, communication.interaction_id,
                           communication.channel, communication.direction,
                           communication.subject, communication.body_excerpt,
                           communication.delivery_status,
                           communication.source_sequence,
                           communication.occurred_at
                    FROM public.communication_event AS communication
                    WHERE communication.tenant_id = :tenant_id
                      AND communication.student_id = :student_id
                      {communication_filter}
                    ORDER BY communication.occurred_at, communication.id
                    LIMIT 120
                    """
                ),
                communication_parameters,
            )
            communications = [dict(row) for row in communication_result.mappings().all()]

            outcome_result = await connection.execute(
                text(
                    """
                    SELECT outcome.id, outcome.interaction_id, outcome.summary,
                           outcome.channel_results, outcome.outcome_code,
                           outcome.resolution_code, outcome.next_step,
                           outcome.follow_up_required, outcome.generated_at
                    FROM public.interaction_outcome_revision AS outcome
                    WHERE outcome.tenant_id = :tenant_id
                      AND outcome.student_id = :student_id
                      AND outcome.is_current = true
                    ORDER BY outcome.generated_at DESC, outcome.id DESC
                    LIMIT 30
                    """
                ),
                {"tenant_id": job.tenant_id, "student_id": job.student_id},
            )
            outcomes = [dict(row) for row in outcome_result.mappings().all()]

            summary_result = await connection.execute(
                text(
                    """
                    SELECT version, summary, key_facts, risks, next_steps,
                           source_revision, generated_at
                    FROM public.student_summary_revision
                    WHERE tenant_id = :tenant_id AND student_id = :student_id
                      AND is_current = true
                    FOR UPDATE
                    """
                ),
                {"tenant_id": job.tenant_id, "student_id": job.student_id},
            )
            previous_summary = summary_result.mappings().first()
            base_summary_version = int(previous_summary["version"]) if previous_summary else 0
            await connection.execute(
                text(
                    """
                    UPDATE public.action_center_ai_job
                    SET base_summary_version = NULLIF(:base_summary_version, 0),
                        updated_at = NOW()
                    WHERE id = :job_id AND tenant_id = :tenant_id
                      AND status = 'running' AND lease_owner = :worker_id
                    """
                ),
                {
                    "base_summary_version": base_summary_version,
                    "job_id": job.id,
                    "tenant_id": job.tenant_id,
                    "worker_id": self._worker_id,
                },
            )

        task = next((item for item in tasks if str(item["id"]) == job.work_item_id), None)
        source_ids = tuple(
            dict.fromkeys(
                [
                    *(str(item["id"]) for item in tasks),
                    *(str(item["id"]) for item in requirements),
                    *(str(item["id"]) for item in documents),
                    *(str(item["id"]) for item in communications),
                    *(str(item["id"]) for item in outcomes),
                ]
            )
        )[:300]
        updated_values = [
            student.get("source_updated_at"),
            *(item.get("updated_at") for item in tasks),
            *(item.get("updated_at") for item in requirements),
            *(item.get("updated_at") for item in documents),
            *(item.get("occurred_at") for item in communications),
            *(item.get("generated_at") for item in outcomes),
        ]
        source_revision = max(
            (_revision(value) for value in updated_values),
            default=max(1, job.processing_source_version),
        )
        task_contexts = [context for item in tasks if (context := _task_context(item)) is not None]
        bounded_tasks = _budgeted_evidence(task_contexts, max_items=50, character_budget=24_000)
        requirement_contexts = [_requirement_context(item) for item in requirements]
        bounded_requirements = _budgeted_evidence(
            requirement_contexts,
            max_items=150,
            character_budget=20_000,
        )
        document_contexts = [_document_context(item) for item in documents]
        document_contexts.sort(
            key=lambda item: str(item.get("category") or "") == "transcript",
            reverse=True,
        )
        bounded_documents = _budgeted_evidence(
            document_contexts,
            max_items=40,
            character_budget=60_000,
        )
        communication_contexts = [_communication_context(item) for item in communications]
        bounded_communications = _budgeted_evidence(
            communication_contexts,
            max_items=120,
            character_budget=70_000,
            newest_first=True,
        )
        outcome_contexts = [_outcome_context(item) for item in outcomes]
        bounded_outcomes = _budgeted_evidence(
            outcome_contexts,
            max_items=30,
            character_budget=16_000,
        )
        channel_counts: dict[str, int] = {}
        for communication in communication_contexts:
            channel = str(communication.get("channel") or "unknown")
            channel_counts[channel] = channel_counts.get(channel, 0) + 1

        context = {
            "generatedAt": datetime.now(UTC).isoformat(),
            "student": {
                "displayName": str(student["preferred_name"]),
                "classYear": student["class_year"],
                "program": student["program_name"],
                "communicationPreference": student["communication_preference"],
                "offerStatus": student["offer_status"],
                "journeyStatus": student["journey_status"],
                "onboardingStatus": student["onboarding_status"],
                "onboardingCurrentStep": student["onboarding_current_step"],
            },
            "task": _task_context(task),
            "allTasks": bounded_tasks,
            "requirements": bounded_requirements,
            "documents": bounded_documents,
            "communications": bounded_communications,
            "priorOutcomes": bounded_outcomes,
            "contextCoverage": {
                "tasks": _coverage(len(task_contexts), len(bounded_tasks)),
                "requirements": _coverage(len(requirement_contexts), len(bounded_requirements)),
                "documents": _coverage(len(document_contexts), len(bounded_documents)),
                "communications": {
                    **_coverage(len(communication_contexts), len(bounded_communications)),
                    "channelCounts": channel_counts,
                },
                "priorOutcomes": _coverage(len(outcome_contexts), len(bounded_outcomes)),
            },
            "previousStudentSummary": (
                {
                    "version": previous_summary["version"],
                    "summary": _ai_safe_text(previous_summary["summary"], 900),
                    "keyFacts": _ai_safe_text_list(previous_summary["key_facts"], 8, 300),
                    "risks": _ai_safe_text_list(previous_summary["risks"], 6, 300),
                    "nextSteps": _ai_safe_text_list(previous_summary["next_steps"], 6, 300),
                }
                if previous_summary is not None
                else None
            ),
            "sourceRevision": source_revision,
        }
        return EnrichmentSnapshot(
            context=context,
            source_ids=source_ids,
            source_revision=source_revision,
            base_summary_version=base_summary_version,
        )

    async def _persist_success(
        self,
        job: ClaimedEnrichmentJob,
        run_id: str,
        snapshot: EnrichmentSnapshot,
        result: Mapping[str, Any],
    ) -> None:
        provider = _bounded_text(result.get("provider"), 48) or "unknown"
        model = _bounded_text(result.get("model"), 160) or "unknown"
        prompt_version = _bounded_text(result.get("promptVersion"), 160) or "v1"
        outcome_summary = _required_text(result.get("outcomeSummary"), 1_600)
        student_summary = _required_text(result.get("studentSummary"), 1_600)
        source_ids_json = _json(list(snapshot.source_ids))
        async with self._engine.begin() as connection:
            job_result = await connection.execute(
                text(
                    """
                    SELECT requested_source_version, processing_source_version,
                           status, lease_owner, work_item_id, interaction_id
                    FROM public.action_center_ai_job
                    WHERE id = :job_id AND tenant_id = :tenant_id
                    FOR UPDATE
                    """
                ),
                {"job_id": job.id, "tenant_id": job.tenant_id},
            )
            current_job = job_result.mappings().first()
            if (
                current_job is None
                or current_job["status"] != "running"
                or current_job["lease_owner"] != self._worker_id
                or int(current_job["processing_source_version"] or 0)
                != job.processing_source_version
            ):
                raise RuntimeError("The Action Center enrichment lease was lost")

            if job.purpose == "interaction_enrichment" and job.interaction_id is not None:
                await self._persist_outcome(
                    connection,
                    job=job,
                    run_id=run_id,
                    result=result,
                    summary=outcome_summary,
                    source_ids_json=source_ids_json,
                    provider=provider,
                    model=model,
                    prompt_version=prompt_version,
                )

            if job.purpose == "task_insight" and job.work_item_id is not None:
                await self._persist_task_insight(
                    connection,
                    job=job,
                    run_id=run_id,
                    snapshot=snapshot,
                    result=result,
                    source_ids_json=source_ids_json,
                    provider=provider,
                    model=model,
                    prompt_version=prompt_version,
                )

            summary_written = await self._persist_student_summary(
                connection,
                job=job,
                run_id=run_id,
                snapshot=snapshot,
                result=result,
                summary=student_summary,
                source_ids_json=source_ids_json,
                provider=provider,
                model=model,
                prompt_version=prompt_version,
            )
            requested_source_version = int(current_job["requested_source_version"])
            has_newer_source = requested_source_version > job.processing_source_version
            summary_retry_required = not summary_written and job.purpose == "student_summary"
            await connection.execute(
                text(
                    """
                    UPDATE public.action_center_ai_job
                    SET status = CASE
                          WHEN :has_newer_source OR :summary_retry_required THEN 'pending'
                          ELSE 'succeeded'
                        END,
                        covered_source_version = GREATEST(
                          covered_source_version, :processing_source_version
                        ),
                        processing_source_version = NULL,
                        attempts = CASE
                          WHEN :has_newer_source OR :summary_retry_required THEN attempts
                          ELSE 0
                        END,
                        not_before = CASE
                          WHEN :summary_retry_required THEN NOW() + interval '5 seconds'
                          ELSE GREATEST(not_before, NOW())
                        END,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        completed_at = CASE
                          WHEN :has_newer_source OR :summary_retry_required THEN NULL
                          ELSE NOW()
                        END,
                        updated_at = NOW()
                    WHERE id = :job_id AND tenant_id = :tenant_id
                    """
                ),
                {
                    "has_newer_source": has_newer_source,
                    "summary_retry_required": summary_retry_required,
                    "processing_source_version": job.processing_source_version,
                    "job_id": job.id,
                    "tenant_id": job.tenant_id,
                },
            )
            await self._complete_agent_run(
                connection,
                job=job,
                run_id=run_id,
                result=result,
                snapshot=snapshot,
                provider=provider,
                model=model,
                prompt_version=prompt_version,
            )

    async def _persist_task_insight(
        self,
        connection: AsyncConnection,
        *,
        job: ClaimedEnrichmentJob,
        run_id: str,
        snapshot: EnrichmentSnapshot,
        result: Mapping[str, Any],
        source_ids_json: str,
        provider: str,
        model: str,
        prompt_version: str,
    ) -> None:
        assert job.work_item_id is not None
        work_item_result = await connection.execute(
            text(
                """
                SELECT version
                FROM public.staff_work_item
                WHERE tenant_id = :tenant_id AND id = :work_item_id
                FOR UPDATE
                """
            ),
            {"tenant_id": job.tenant_id, "work_item_id": job.work_item_id},
        )
        if work_item_result.mappings().first() is None:
            raise RuntimeError("The work item no longer exists")

        version_result = await connection.execute(
            text(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM public.task_insight_revision
                WHERE tenant_id = :tenant_id AND work_item_id = :work_item_id
                """
            ),
            {"tenant_id": job.tenant_id, "work_item_id": job.work_item_id},
        )
        insight_version = int(version_result.scalar_one())
        await connection.execute(
            text(
                """
                UPDATE public.task_insight_revision
                SET is_current = false
                WHERE tenant_id = :tenant_id AND work_item_id = :work_item_id
                  AND is_current = true
                """
            ),
            {"tenant_id": job.tenant_id, "work_item_id": job.work_item_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO public.task_insight_revision (
                  id, tenant_id, work_item_id, student_id, version, summary,
                  why_this_matters, objective, success_definition,
                  suggested_approach, suggested_channel, source_ids,
                  source_revision, provider, model, prompt_version,
                  agent_run_id, is_current, generated_at
                ) VALUES (
                  :id, :tenant_id, :work_item_id, :student_id, :version,
                  :summary, :why_this_matters, :objective, :success_definition,
                  :suggested_approach, :suggested_channel,
                  CAST(:source_ids AS jsonb), :source_revision, :provider,
                  :model, :prompt_version, :agent_run_id, true, NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": job.tenant_id,
                "work_item_id": job.work_item_id,
                "student_id": job.student_id,
                "version": insight_version,
                "summary": _required_text(result.get("taskSummary"), 1_200),
                "why_this_matters": _required_text(result.get("whyThisMatters"), 800),
                "objective": _required_text(result.get("taskObjective"), 800),
                "success_definition": _required_text(result.get("successDefinition"), 800),
                "suggested_approach": _required_text(result.get("suggestedApproach"), 1_200),
                "suggested_channel": _communication_channel(result.get("suggestedChannel")),
                "source_ids": source_ids_json,
                "source_revision": snapshot.source_revision,
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "agent_run_id": run_id,
            },
        )
        await self._insert_system_log(
            connection,
            job,
            "ai_task_insight_updated",
            "AI task summary and suggested approach updated from current evidence.",
        )

    async def _persist_outcome(
        self,
        connection: AsyncConnection,
        *,
        job: ClaimedEnrichmentJob,
        run_id: str,
        result: Mapping[str, Any],
        summary: str,
        source_ids_json: str,
        provider: str,
        model: str,
        prompt_version: str,
    ) -> None:
        assert job.interaction_id is not None
        interaction_result = await connection.execute(
            text(
                """
                SELECT source_version, covered_source_version, completed_at
                FROM public.staff_interaction
                WHERE tenant_id = :tenant_id AND id = :interaction_id
                FOR UPDATE
                """
            ),
            {"tenant_id": job.tenant_id, "interaction_id": job.interaction_id},
        )
        interaction = interaction_result.mappings().first()
        if interaction is None:
            raise RuntimeError("The interaction no longer exists")
        version_result = await connection.execute(
            text(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM public.interaction_outcome_revision
                WHERE tenant_id = :tenant_id AND interaction_id = :interaction_id
                """
            ),
            {"tenant_id": job.tenant_id, "interaction_id": job.interaction_id},
        )
        outcome_version = int(version_result.scalar_one())
        await connection.execute(
            text(
                """
                UPDATE public.interaction_outcome_revision
                SET is_current = false
                WHERE tenant_id = :tenant_id AND interaction_id = :interaction_id
                  AND is_current = true
                """
            ),
            {"tenant_id": job.tenant_id, "interaction_id": job.interaction_id},
        )
        confidence = result.get("confidence")
        confidence_milli = None
        if isinstance(confidence, int | float):
            confidence_milli = round(min(1.0, max(0.0, float(confidence))) * 1_000)
        await connection.execute(
            text(
                """
                INSERT INTO public.interaction_outcome_revision (
                  id, tenant_id, interaction_id, work_item_id, student_id,
                  version, finality, summary, channel_results, conversation_signals,
                  outcome_code,
                  resolution_code, next_step, follow_up_required, source_ids,
                  covered_source_version, confidence_milli, provider, model,
                  prompt_version, agent_run_id, is_current, generated_at
                ) VALUES (
                  :id, :tenant_id, :interaction_id, :work_item_id, :student_id,
                  :version, :finality, :summary, CAST(:channel_results AS jsonb),
                  CAST(:conversation_signals AS jsonb), :outcome_code,
                  :resolution_code, :next_step,
                  :follow_up_required, CAST(:source_ids AS jsonb),
                  :covered_source_version, :confidence_milli, :provider, :model,
                  :prompt_version, :agent_run_id, true, NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": job.tenant_id,
                "interaction_id": job.interaction_id,
                "work_item_id": job.work_item_id,
                "student_id": job.student_id,
                "version": outcome_version,
                "finality": "final" if interaction["completed_at"] is not None else "provisional",
                "summary": summary,
                "channel_results": _json(_channel_results(result.get("channelResults"))),
                "conversation_signals": _json(
                    _conversation_signals(result.get("conversationSignals"))
                ),
                "outcome_code": _code(result.get("outcomeCode")),
                "resolution_code": _code(result.get("resolutionCode")),
                "next_step": _bounded_text(result.get("nextStep"), 800) or None,
                "follow_up_required": bool(result.get("followUpRequired")),
                "source_ids": source_ids_json,
                "covered_source_version": job.processing_source_version,
                "confidence_milli": confidence_milli,
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "agent_run_id": run_id,
            },
        )
        has_newer_source = int(interaction["source_version"]) > job.processing_source_version
        await connection.execute(
            text(
                """
                UPDATE public.staff_interaction
                SET covered_source_version = GREATEST(
                      covered_source_version, :covered_source_version
                    ),
                    status = CASE
                      WHEN source_version > :covered_source_version THEN 'stale'
                      WHEN completed_at IS NOT NULL THEN 'completed'
                      ELSE 'provisional'
                    END,
                    version = version + 1,
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id AND id = :interaction_id
                """
            ),
            {
                "covered_source_version": job.processing_source_version,
                "tenant_id": job.tenant_id,
                "interaction_id": job.interaction_id,
            },
        )
        if job.work_item_id is not None:
            await self._insert_system_log(
                connection,
                job,
                "ai_outcome_updated",
                (
                    "AI outcome updated; newer communication is waiting to be included."
                    if has_newer_source
                    else "AI outcome updated from the latest communication evidence."
                ),
            )

    async def _persist_student_summary(
        self,
        connection: AsyncConnection,
        *,
        job: ClaimedEnrichmentJob,
        run_id: str,
        snapshot: EnrichmentSnapshot,
        result: Mapping[str, Any],
        summary: str,
        source_ids_json: str,
        provider: str,
        model: str,
        prompt_version: str,
    ) -> bool:
        # A SELECT ... FOR UPDATE against an empty revision stream does not lock
        # anything. Lock the canonical student row first so concurrent task and
        # interaction jobs for one student serialize before choosing a summary
        # version. The second job can then detect its stale base version and
        # enqueue one deduplicated student-summary rebuild instead of colliding
        # on version 1.
        student_result = await connection.execute(
            text(
                """
                SELECT id
                FROM public.student
                WHERE tenant_id = :tenant_id AND id = :student_id
                FOR UPDATE
                """
            ),
            {"tenant_id": job.tenant_id, "student_id": job.student_id},
        )
        if student_result.mappings().first() is None:
            raise RuntimeError("The student no longer exists")

        current_result = await connection.execute(
            text(
                """
                SELECT version
                FROM public.student_summary_revision
                WHERE tenant_id = :tenant_id AND student_id = :student_id
                  AND is_current = true
                FOR UPDATE
                """
            ),
            {"tenant_id": job.tenant_id, "student_id": job.student_id},
        )
        current = current_result.mappings().first()
        current_version = int(current["version"]) if current is not None else 0
        if current_version != snapshot.base_summary_version:
            await self._queue_student_summary_rebuild(connection, job)
            return False
        next_version = current_version + 1
        await connection.execute(
            text(
                """
                UPDATE public.student_summary_revision
                SET is_current = false
                WHERE tenant_id = :tenant_id AND student_id = :student_id
                  AND is_current = true
                """
            ),
            {"tenant_id": job.tenant_id, "student_id": job.student_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO public.student_summary_revision (
                  id, tenant_id, student_id, version, summary, key_facts,
                  risks, next_steps, source_ids, source_revision, provider,
                  model, prompt_version, agent_run_id, is_current, generated_at
                ) VALUES (
                  :id, :tenant_id, :student_id, :version, :summary,
                  CAST(:key_facts AS jsonb), CAST(:risks AS jsonb),
                  CAST(:next_steps AS jsonb), CAST(:source_ids AS jsonb),
                  :source_revision, :provider, :model, :prompt_version,
                  :agent_run_id, true, NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": job.tenant_id,
                "student_id": job.student_id,
                "version": next_version,
                "summary": summary,
                "key_facts": _json(_text_list(result.get("keyFacts"), 8, 300)),
                "risks": _json(_text_list(result.get("risks"), 6, 300)),
                "next_steps": _json(_text_list(result.get("nextSteps"), 6, 300)),
                "source_ids": source_ids_json,
                "source_revision": snapshot.source_revision,
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "agent_run_id": run_id,
            },
        )
        if job.work_item_id is not None:
            await self._insert_system_log(
                connection,
                job,
                "student_summary_updated",
                "Canonical student summary updated from current enrollment and outreach evidence.",
            )
            await self._insert_update_notification(connection, job, next_version)
        return True

    async def _queue_student_summary_rebuild(
        self, connection: AsyncConnection, job: ClaimedEnrichmentJob
    ) -> None:
        if job.purpose == "student_summary":
            return
        await connection.execute(
            text(
                """
                INSERT INTO public.action_center_ai_job (
                  id, tenant_id, purpose, dedupe_key, student_id, status,
                  requested_source_version, covered_source_version,
                  not_before, attempts, max_attempts, created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, 'student_summary', :dedupe_key, :student_id,
                  'pending', 1, 0, NOW() + interval '5 seconds', 0, 5, NOW(), NOW()
                )
                ON CONFLICT (tenant_id, purpose, dedupe_key)
                DO UPDATE SET
                  requested_source_version = action_center_ai_job.requested_source_version + 1,
                  status = CASE
                    WHEN action_center_ai_job.status = 'running' THEN 'running'
                    ELSE 'pending'
                  END,
                  not_before = NOW() + interval '5 seconds',
                  completed_at = NULL, updated_at = NOW()
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": job.tenant_id,
                "dedupe_key": f"student:{job.student_id}",
                "student_id": job.student_id,
            },
        )

    async def _complete_agent_run(
        self,
        connection: AsyncConnection,
        *,
        job: ClaimedEnrichmentJob,
        run_id: str,
        result: Mapping[str, Any],
        snapshot: EnrichmentSnapshot,
        provider: str,
        model: str,
        prompt_version: str,
    ) -> None:
        snapshot_hash = hashlib.sha256(_json(snapshot.context).encode("utf-8")).hexdigest()
        await connection.execute(
            text(
                """
                UPDATE public.agent_run
                SET provider = :provider, model = :model,
                    prompt_version = :prompt_version,
                    snapshot_hash = :snapshot_hash,
                    status = 'succeeded', result = CAST(:result AS jsonb),
                    completed_at = NOW()
                WHERE id = :run_id AND tenant_id = :tenant_id
                """
            ),
            {
                "provider": provider,
                "model": model,
                "prompt_version": prompt_version,
                "snapshot_hash": snapshot_hash,
                "result": _json(result),
                "run_id": run_id,
                "tenant_id": job.tenant_id,
            },
        )
        usage = result.get("usage")
        if isinstance(usage, Mapping):
            await connection.execute(
                text(
                    """
                    INSERT INTO public.model_usage (
                      id, tenant_id, agent_run_id, provider, model,
                      input_tokens, output_tokens, created_at
                    ) VALUES (
                      :id, :tenant_id, :agent_run_id, :provider, :model,
                      :input_tokens, :output_tokens, NOW()
                    )
                    ON CONFLICT (agent_run_id) DO NOTHING
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": job.tenant_id,
                    "agent_run_id": run_id,
                    "provider": provider,
                    "model": model,
                    "input_tokens": _optional_nonnegative_int(usage.get("inputTokens")),
                    "output_tokens": _optional_nonnegative_int(usage.get("outputTokens")),
                },
            )

    async def _persist_failure(
        self, job: ClaimedEnrichmentJob, run_id: str, error: Exception
    ) -> None:
        error_code = type(error).__name__[:80]
        error_message = _bounded_text(str(error), 500) or "Action Center enrichment failed"
        retry_delay = min(300, 5 * (2 ** max(0, job.attempts - 1)))
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    UPDATE public.action_center_ai_job
                    SET status = CASE
                          WHEN attempts >= max_attempts THEN 'dead_letter'
                          ELSE 'failed_retryable'
                        END,
                        processing_source_version = NULL,
                        not_before = NOW() + make_interval(secs => :retry_delay),
                        lease_owner = NULL, lease_expires_at = NULL,
                        last_error_code = :error_code,
                        last_error_message = :error_message,
                        completed_at = CASE
                          WHEN attempts >= max_attempts THEN NOW() ELSE NULL
                        END,
                        updated_at = NOW()
                    WHERE id = :job_id AND tenant_id = :tenant_id
                      AND status = 'running' AND lease_owner = :worker_id
                    RETURNING status
                    """
                ),
                {
                    "retry_delay": retry_delay,
                    "error_code": error_code,
                    "error_message": error_message,
                    "job_id": job.id,
                    "tenant_id": job.tenant_id,
                    "worker_id": self._worker_id,
                },
            )
            failed_job = result.mappings().first()
            if failed_job is not None and job.interaction_id is not None:
                await connection.execute(
                    text(
                        """
                        UPDATE public.staff_interaction
                        SET status = 'failed_retryable', version = version + 1,
                            updated_at = NOW()
                        WHERE tenant_id = :tenant_id AND id = :interaction_id
                        """
                    ),
                    {"tenant_id": job.tenant_id, "interaction_id": job.interaction_id},
                )
            await connection.execute(
                text(
                    """
                    UPDATE public.agent_run
                    SET status = 'failed', failure_code = :failure_code,
                        result = CAST(:result AS jsonb), completed_at = NOW()
                    WHERE id = :run_id AND tenant_id = :tenant_id
                    """
                ),
                {
                    "failure_code": error_code[:64],
                    "result": _json({"error": error_message}),
                    "run_id": run_id,
                    "tenant_id": job.tenant_id,
                },
            )

    async def _insert_system_log(
        self,
        connection: AsyncConnection,
        job: ClaimedEnrichmentJob,
        action: str,
        message: str,
    ) -> None:
        if job.work_item_id is None:
            return
        await connection.execute(
            text(
                """
                INSERT INTO public.staff_work_log (
                  id, tenant_id, work_item_id, actor_type, actor_id,
                  actor_name, action, message, occurred_at
                ) VALUES (
                  :id, :tenant_id, :work_item_id, 'system', NULL,
                  'Audentra AI', :action, :message, NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": job.tenant_id,
                "work_item_id": job.work_item_id,
                "action": action,
                "message": message,
            },
        )

    async def _insert_update_notification(
        self,
        connection: AsyncConnection,
        job: ClaimedEnrichmentJob,
        summary_version: int,
    ) -> None:
        if job.work_item_id is None:
            return
        target_result = await connection.execute(
            text(
                """
                SELECT item.assignee_id, item.component, item.key,
                       member.id AS active_assignee_id
                FROM public.staff_work_item item
                LEFT JOIN public.staff_member member
                  ON member.tenant_id=item.tenant_id
                 AND member.id=item.assignee_id
                 AND member.active=true
                WHERE item.tenant_id = :tenant_id AND item.id = :work_item_id
                """
            ),
            {"tenant_id": job.tenant_id, "work_item_id": job.work_item_id},
        )
        target = target_result.mappings().first()
        if target is None:
            return
        notification_id = self._uuid_factory()
        notification_result = await connection.execute(
            text(
                """
                INSERT INTO public.staff_notification (
                  id, tenant_id, staff_member_id, team_component, tenant_wide, kind,
                  title, body, resource_type, resource_id, dedupe_key, created_at
                ) VALUES (
                  :id, :tenant_id, :staff_member_id, NULL, :tenant_wide,
                  'ai_update_available', 'New AI update available',
                  :body, 'staff_work_item', :work_item_id, :dedupe_key, NOW()
                )
                ON CONFLICT (tenant_id, dedupe_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": notification_id,
                "tenant_id": job.tenant_id,
                "staff_member_id": target["active_assignee_id"],
                "tenant_wide": target["active_assignee_id"] is None,
                "body": (
                    f"Outcome and student summary updates are ready for {target['key']}. "
                    "Refresh when you are ready; your unsaved work will not be replaced."
                ),
                "work_item_id": job.work_item_id,
                # Reuse the immediate attention notification rather than
                # creating an AI duplicate in the notification center.
                "dedupe_key": f"work-item:{job.work_item_id}:attention",
            },
        )
        notification = notification_result.mappings().first()
        if notification is None:
            existing_result = await connection.execute(
                text(
                    """
                    SELECT id FROM public.staff_notification
                    WHERE tenant_id=:tenant_id AND dedupe_key=:dedupe_key
                    """
                ),
                {
                    "tenant_id": job.tenant_id,
                    "dedupe_key": f"work-item:{job.work_item_id}:attention",
                },
            )
            existing = existing_result.mappings().first()
            if existing is None:
                raise RuntimeError("The Action Center update notification could not be found")
            notification_id = existing["id"]
        else:
            notification_id = notification["id"]

        # Even when the durable notification was deduplicated, emit a fresh
        # invalidation record for the newly generated AI revision.
        await connection.execute(
            text(
                """
                INSERT INTO public.staff_realtime_event (
                  id, tenant_id, event_type, resource_type, resource_id,
                  work_item_id, staff_member_id, team_component, tenant_wide,
                  payload, created_at
                ) VALUES (
                  :id, :tenant_id, 'staff.ai_update.available',
                  'staff_notification', :notification_id, :work_item_id,
                  :staff_member_id, NULL, :tenant_wide,
                  CAST(:payload AS jsonb), NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": job.tenant_id,
                "notification_id": notification_id,
                "work_item_id": job.work_item_id,
                "staff_member_id": target["active_assignee_id"],
                "tenant_wide": target["active_assignee_id"] is None,
                "payload": _json(
                    {
                        "notificationId": str(notification_id),
                        "workItemId": job.work_item_id,
                        "workItemKey": str(target["key"]),
                        "purpose": job.purpose,
                        "summaryVersion": summary_version,
                        "sourceVersion": job.processing_source_version,
                        "invalidate": ["notifications", "workspace", "action-center"],
                    }
                ),
            },
        )


def _task_context(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "key": value["key"],
        "title": value["title"],
        "description": _bounded_text(value.get("description"), 2_000),
        "status": value["status"],
        "priority": value["priority"],
        "type": value["work_type"],
        "actionType": value["action_type"],
        "component": value["component"],
        "dueAt": _iso(value.get("due_at")),
        "escalated": value["escalated"],
        "selectedChannel": value["selected_channel"],
        "attemptCount": value["attempt_count"],
        "followUpAt": _iso(value.get("follow_up_at")),
        "blockerCode": value["blocker_code"],
        "blockerDetail": _bounded_text(value.get("blocker_detail"), 500),
        "outcomeCode": value["outcome_code"],
        "resolutionCode": value["resolution_code"],
        "nextStep": _bounded_text(value.get("next_step"), 800),
        "terminalReason": value["terminal_reason"],
        "version": value["version"],
        "updatedAt": _iso(value.get("updated_at")),
    }


def _requirement_context(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "code": value["code"],
        "title": value["title"],
        "flow": value["flow_kind"],
        "blocking": bool(value["blocking"]),
        "status": value["status"],
        "progress": value["progress_percent"],
        "dueAt": _iso(value.get("due_at")),
        "retiredAt": _iso(value.get("retired_at")),
        "retiredReason": value["retired_reason"],
        "version": value["version"],
    }


def _document_context(value: Mapping[str, Any]) -> dict[str, Any]:
    extraction = _json_object(value.get("extraction"))
    return {
        "fileName": _bounded_text(value.get("file_name"), 255),
        "category": value["category"],
        "status": value["status"],
        "processingMode": value["processing_mode"],
        "extraction": {
            "documentType": extraction.get("documentType"),
            "summary": _bounded_text(extraction.get("summary"), 1_500),
            "institutionName": _bounded_text(extraction.get("institutionName"), 180),
            "academicTerm": _bounded_text(extraction.get("academicTerm"), 120),
            "fields": _bounded_evidence_records(
                extraction.get("fields"),
                max_items=30,
                character_budget=8_000,
            ),
            "courses": _bounded_evidence_records(
                extraction.get("courses"),
                max_items=80,
                character_budget=40_000,
            ),
            "warnings": _text_list(extraction.get("warnings"), 12, 500),
        },
        "updatedAt": _iso(value.get("updated_at")),
    }


def _communication_context(value: Mapping[str, Any]) -> dict[str, Any]:
    channel = str(value["channel"])
    return {
        "channel": channel,
        "direction": value["direction"],
        "subject": _bounded_text(value.get("subject"), 500),
        "body": _bounded_text(
            value.get("body_excerpt"),
            12_000 if channel == "voice" else 3_000,
        ),
        "deliveryStatus": value["delivery_status"],
        "sourceSequence": value["source_sequence"],
        "occurredAt": _iso(value.get("occurred_at")),
    }


def _outcome_context(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "summary": _bounded_text(value.get("summary"), 1_600),
        "channelResults": _json_list(value.get("channel_results"))[:8],
        "conversationSignals": _json_object(value.get("conversation_signals")),
        "outcomeCode": value["outcome_code"],
        "resolutionCode": value["resolution_code"],
        "nextStep": _bounded_text(value.get("next_step"), 800),
        "followUpRequired": value["follow_up_required"],
        "generatedAt": _iso(value.get("generated_at")),
    }


def _channel_results(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    results: list[dict[str, str]] = []
    for item in value[:8]:
        if not isinstance(item, Mapping):
            continue
        channel = str(item.get("channel") or "")
        description = _bounded_text(item.get("result"), 500)
        if channel in {"email", "sms", "voice", "portal"} and description:
            results.append({"channel": channel, "result": description})
    return results


def _conversation_signals(value: object) -> dict[str, object]:
    source = value if isinstance(value, Mapping) else {}

    def metric(name: str) -> dict[str, object]:
        raw = source.get(name)
        item = raw if isinstance(raw, Mapping) else {}
        label = _bounded_text(item.get("label"), 80) or "Not enough evidence"
        score_value = item.get("score")
        score = (
            min(1.0, max(0.0, float(score_value)))
            if isinstance(score_value, int | float) and not isinstance(score_value, bool)
            else None
        )
        return {"label": label, "score": score}

    return {
        "sentiment": metric("sentiment"),
        "engagement": metric("engagement"),
        "intent": _bounded_text(source.get("intent"), 160) or "Not enough evidence",
        "likelihoodToProgress": metric("likelihoodToProgress"),
    }


def _text_list(value: object, limit: int, character_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    values = [_bounded_text(item, character_limit) for item in value[:limit]]
    return list(dict.fromkeys(item for item in values if item))


def _json_list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _coverage(total: int, included: int) -> dict[str, int]:
    return {
        "total": total,
        "included": included,
        "omitted": max(0, total - included),
    }


def _budgeted_evidence(
    values: list[dict[str, Any]],
    *,
    max_items: int,
    character_budget: int,
    newest_first: bool = False,
) -> list[dict[str, Any]]:
    candidates = list(reversed(values)) if newest_first else values
    selected: list[dict[str, Any]] = []
    used = 2
    for value in candidates:
        if len(selected) >= max_items:
            break
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        cost = len(encoded) + (1 if selected else 0)
        if used + cost > character_budget:
            continue
        selected.append(value)
        used += cost
    return list(reversed(selected)) if newest_first else selected


def _bounded_evidence_records(
    value: object,
    *,
    max_items: int,
    character_budget: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _json_list(value):
        normalized = _bounded_json_value(item)
        if isinstance(normalized, dict):
            records.append(normalized)
        else:
            records.append({"value": normalized})
    return _budgeted_evidence(
        records,
        max_items=max_items,
        character_budget=character_budget,
    )


def _bounded_json_value(value: object, *, depth: int = 0) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return _bounded_text(value, 500)
    if depth >= 3:
        return _bounded_text(str(value), 500)
    if isinstance(value, Mapping):
        return {
            _bounded_text(str(key), 80): _bounded_json_value(item, depth=depth + 1)
            for key, item in list(value.items())[:24]
        }
    if isinstance(value, list):
        return [_bounded_json_value(item, depth=depth + 1) for item in value[:24]]
    return _bounded_text(str(value), 500)


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _required_text(value: object, limit: int) -> str:
    result = _bounded_text(value, limit)
    if not result:
        raise ValueError("The enrichment result omitted required summary text")
    return result


def _bounded_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\x00", "").split())[:limit]


def _ai_safe_text(value: object, limit: int) -> str:
    """Keep prior AI projections useful without leaking technical record IDs back to a model."""

    return _TECHNICAL_IDENTIFIER_PATTERN.sub("[redacted identifier]", _bounded_text(value, limit))


def _ai_safe_text_list(value: object, limit: int, character_limit: int) -> list[str]:
    return list(
        dict.fromkeys(
            item
            for item in (
                _ai_safe_text(candidate, character_limit) for candidate in _json_list(value)[:limit]
            )
            if item
        )
    )


def _code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace(" ", "_")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    if not normalized or len(normalized) > 48 or any(char not in allowed for char in normalized):
        return None
    return normalized


def _communication_channel(value: object) -> str | None:
    return str(value) if value in {"email", "sms", "voice", "portal"} else None


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed >= 0 else None


def _revision(value: object) -> int:
    if not isinstance(value, datetime):
        return 0
    timestamp = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return max(1, int(timestamp.timestamp() * 1_000_000))


def _iso(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    timestamp = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return timestamp.isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
