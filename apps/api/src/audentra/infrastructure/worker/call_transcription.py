"""Durable, independently leased speech-to-text processing for staff calls."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

OPENROUTER_TRANSCRIPTION_URL = "https://openrouter.ai/api/v1/audio/transcriptions"

_AUDIO_FORMATS = {
    "audio/flac": "flac",
    "audio/m4a": "m4a",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/webm": "webm",
    "audio/x-m4a": "m4a",
    "video/mp4": "mp4",
    "video/webm": "webm",
}


class RecordingStorage(Protocol):
    async def get(self, key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ClaimedRecording:
    id: str
    tenant_id: str
    interaction_id: str
    work_item_id: str
    student_id: str
    file_name: str
    mime_type: str
    storage_key: str
    sha256: str
    attempts: int
    max_attempts: int


class CallTranscriptionRunner:
    """Transcribe stored audio without blocking API, outbox, or AI loops."""

    def __init__(
        self,
        engine: AsyncEngine,
        storage: RecordingStorage,
        http: httpx.AsyncClient,
        *,
        api_key: str,
        model: str = "openai/whisper-large-v3",
        app_url: str = "http://localhost:3000",
        app_name: str = "Audentra Student Portal",
        worker_id: str = "call-transcription",
        batch_size: int = 4,
        concurrency: int = 2,
        lease_seconds: int = 300,
        timeout_seconds: float = 120.0,
        uuid_factory: Callable[[], UUID] = uuid4,
        logger: logging.Logger | None = None,
    ) -> None:
        if not 1 <= batch_size <= 20:
            raise ValueError("batch_size must be between 1 and 20")
        if not 1 <= concurrency <= batch_size:
            raise ValueError("concurrency must be between 1 and batch_size")
        self._engine = engine
        self._storage = storage
        self._http = http
        self._api_key = api_key.strip()
        self._model = model
        self._app_url = app_url
        self._app_name = app_name
        self._worker_id = worker_id
        self._batch_size = batch_size
        self._concurrency = concurrency
        self._lease_seconds = lease_seconds
        self._timeout_seconds = timeout_seconds
        self._uuid_factory = uuid_factory
        self._logger = logger or logging.getLogger(__name__)

    async def run_once(self) -> int:
        recordings = await self._claim()
        if not recordings:
            return 0
        semaphore = asyncio.Semaphore(self._concurrency)

        async def process(recording: ClaimedRecording) -> bool:
            async with semaphore:
                return await self._process(recording)

        results = await asyncio.gather(*(process(recording) for recording in recordings))
        return sum(results)

    async def _claim(self) -> list[ClaimedRecording]:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    WITH due AS (
                      SELECT id
                      FROM public.staff_call_recording
                      WHERE attempts < max_attempts
                        AND (
                          status = 'queued'
                          OR (
                            status = 'failed_retryable'
                            AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                          )
                          OR (
                            status = 'transcribing'
                            AND lease_expires_at IS NOT NULL
                            AND lease_expires_at <= NOW()
                          )
                          OR (status = 'pending_configuration' AND :configured)
                        )
                      ORDER BY created_at, id
                      LIMIT :batch_size
                      FOR UPDATE SKIP LOCKED
                    )
                    UPDATE public.staff_call_recording recording
                    SET status = 'transcribing', attempts = attempts + 1,
                        lease_owner = :worker_id,
                        lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                        next_attempt_at = NULL,
                        last_error_code = NULL, last_error_message = NULL,
                        version = version + 1, updated_at = NOW()
                    FROM due
                    WHERE recording.id = due.id
                    RETURNING recording.id, recording.tenant_id,
                              recording.interaction_id, recording.work_item_id,
                              recording.student_id, recording.file_name,
                              recording.mime_type, recording.storage_key,
                              recording.sha256, recording.attempts,
                              recording.max_attempts
                    """
                ),
                {
                    "configured": bool(self._api_key),
                    "batch_size": self._batch_size,
                    "worker_id": self._worker_id,
                    "lease_seconds": self._lease_seconds,
                },
            )
            return [
                ClaimedRecording(
                    id=str(row["id"]),
                    tenant_id=str(row["tenant_id"]),
                    interaction_id=str(row["interaction_id"]),
                    work_item_id=str(row["work_item_id"]),
                    student_id=str(row["student_id"]),
                    file_name=str(row["file_name"]),
                    mime_type=str(row["mime_type"]),
                    storage_key=str(row["storage_key"]),
                    sha256=str(row["sha256"]),
                    attempts=int(row["attempts"]),
                    max_attempts=int(row["max_attempts"]),
                )
                for row in result.mappings().all()
            ]

    async def _process(self, recording: ClaimedRecording) -> bool:
        if not self._api_key:
            await self._pending_configuration(recording)
            return False
        try:
            audio = await self._storage.get(recording.storage_key)
            if hashlib.sha256(audio).hexdigest() != recording.sha256:
                raise RuntimeError("Stored call recording checksum mismatch")
            audio_format = _AUDIO_FORMATS.get(recording.mime_type)
            if audio_format is None:
                raise RuntimeError("Stored call recording has an unsupported format")
            response = await self._http.post(
                OPENROUTER_TRANSCRIPTION_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": self._app_url,
                    "X-Title": self._app_name,
                },
                json={
                    "model": self._model,
                    "input_audio": {
                        "data": base64.b64encode(audio).decode("ascii"),
                        "format": audio_format,
                    },
                    "temperature": 0,
                },
                timeout=self._timeout_seconds,
            )
            if response.status_code in {401, 403}:
                await self._pending_configuration(recording, "OpenRouter credentials were rejected")
                return False
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise RuntimeError("OpenRouter returned an invalid transcription response")
            transcript = _bounded_text(payload.get("text"), 200_000)
            if not transcript:
                raise RuntimeError("OpenRouter returned an empty call transcript")
            await self._persist_success(recording, transcript, payload)
            return True
        except Exception as error:
            self._logger.exception(
                "call_transcription_failed",
                extra={"recording_id": recording.id, "tenant_id": recording.tenant_id},
            )
            await self._persist_failure(recording, error)
            return False

    async def _persist_success(
        self,
        recording: ClaimedRecording,
        transcript: str,
        payload: Mapping[str, Any],
    ) -> None:
        transcript_id = self._uuid_factory()
        usage = payload.get("usage")
        usage_mapping = usage if isinstance(usage, Mapping) else {}
        duration = _optional_float(payload.get("duration")) or _optional_float(
            usage_mapping.get("seconds")
        )
        language = _bounded_text(payload.get("language"), 16) or None
        segments = _transcription_segments(payload.get("segments"))
        async with self._engine.begin() as connection:
            await self._lock_lease(connection, recording)
            version_result = await connection.execute(
                text(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM public.staff_call_transcript_revision
                    WHERE tenant_id = :tenant_id AND recording_id = :recording_id
                    """
                ),
                {"tenant_id": recording.tenant_id, "recording_id": recording.id},
            )
            transcript_version = int(version_result.scalar_one())
            await connection.execute(
                text(
                    """
                    UPDATE public.staff_call_transcript_revision
                    SET is_current = false
                    WHERE tenant_id = :tenant_id AND recording_id = :recording_id
                      AND is_current = true
                    """
                ),
                {"tenant_id": recording.tenant_id, "recording_id": recording.id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO public.staff_call_transcript_revision (
                      id, tenant_id, recording_id, version, transcript, language,
                      duration_seconds, segments, provider, model, is_current,
                      generated_at
                    ) VALUES (
                      :id, :tenant_id, :recording_id, :version, :transcript,
                      :language, :duration_seconds, CAST(:segments AS jsonb),
                      'openrouter', :model,
                      true, NOW()
                    )
                    """
                ),
                {
                    "id": transcript_id,
                    "tenant_id": recording.tenant_id,
                    "recording_id": recording.id,
                    "version": transcript_version,
                    "transcript": transcript,
                    "language": language,
                    "duration_seconds": duration,
                    "segments": _json(segments),
                    "model": self._model,
                },
            )
            interaction_result = await connection.execute(
                text(
                    """
                    SELECT source_version
                    FROM public.staff_interaction
                    WHERE tenant_id = :tenant_id AND id = :interaction_id
                    FOR UPDATE
                    """
                ),
                {
                    "tenant_id": recording.tenant_id,
                    "interaction_id": recording.interaction_id,
                },
            )
            interaction = interaction_result.mappings().first()
            if interaction is None:
                raise RuntimeError("The call interaction no longer exists")
            source_sequence = int(interaction["source_version"]) + 1
            await connection.execute(
                text(
                    """
                    INSERT INTO public.communication_event (
                      id, tenant_id, student_id, channel, direction, subject,
                      body_excerpt, metadata, resolution_status, occurred_at,
                      created_at, interaction_id, source_type, source_id,
                      source_sequence, delivery_status
                    ) VALUES (
                      :id, :tenant_id, :student_id, 'voice', 'outbound',
                      'Call recording transcript', :body,
                      CAST(:metadata AS jsonb), 'unresolved', NOW(), NOW(),
                      :interaction_id, 'call_transcript_revision',
                      :transcript_id, :source_sequence, 'recorded'
                    )
                    """
                ),
                {
                    "id": self._uuid_factory(),
                    "tenant_id": recording.tenant_id,
                    "student_id": recording.student_id,
                    "body": _bounded_text(transcript, 12_000),
                    "metadata": _json(
                        {
                            "recordingId": recording.id,
                            "transcriptVersion": transcript_version,
                            "speakerDirection": "mixed",
                        }
                    ),
                    "interaction_id": recording.interaction_id,
                    "transcript_id": transcript_id,
                    "source_sequence": source_sequence,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE public.staff_interaction
                    SET source_version = :source_sequence,
                        status = 'enrichment_pending', quiet_until = NOW(),
                        last_activity_at = NOW(), version = version + 1,
                        updated_at = NOW()
                    WHERE tenant_id = :tenant_id AND id = :interaction_id
                    """
                ),
                {
                    "tenant_id": recording.tenant_id,
                    "interaction_id": recording.interaction_id,
                    "source_sequence": source_sequence,
                },
            )
            await connection.execute(
                text(
                    """
                    UPDATE public.staff_call_recording
                    SET status = 'ready', attempts = 0, lease_owner = NULL,
                        lease_expires_at = NULL, next_attempt_at = NULL,
                        last_error_code = NULL,
                        last_error_message = NULL, transcribed_at = NOW(),
                        version = version + 1, updated_at = NOW()
                    WHERE id = :recording_id AND tenant_id = :tenant_id
                      AND status = 'transcribing' AND lease_owner = :worker_id
                    """
                ),
                {
                    "recording_id": recording.id,
                    "tenant_id": recording.tenant_id,
                    "worker_id": self._worker_id,
                },
            )
            await self._queue_enrichment(connection, recording, source_sequence)
            await self._insert_system_log(
                connection,
                recording,
                "Call transcription completed and outcome enrichment was queued.",
            )
            await self._notify(
                connection,
                recording,
                kind="call_transcription_ready",
                title="Call transcript ready",
                body="The stored call recording was transcribed and is ready for review.",
                dedupe_suffix=f"ready:{transcript_version}",
            )

    async def _queue_enrichment(
        self,
        connection: AsyncConnection,
        recording: ClaimedRecording,
        source_version: int,
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO public.action_center_ai_job (
                  id, tenant_id, purpose, dedupe_key, student_id, work_item_id,
                  interaction_id, status, requested_source_version,
                  covered_source_version, not_before, attempts, max_attempts,
                  created_at, updated_at
                ) VALUES (
                  :id, :tenant_id, 'interaction_enrichment', :dedupe_key,
                  :student_id, :work_item_id, :interaction_id, 'pending',
                  :source_version, 0, NOW(), 0, 5, NOW(), NOW()
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
                  not_before = NOW(),
                  attempts = CASE
                    WHEN action_center_ai_job.status = 'dead_letter' THEN 0
                    ELSE action_center_ai_job.attempts
                  END,
                  completed_at = NULL, last_error_code = NULL,
                  last_error_message = NULL, updated_at = NOW()
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": recording.tenant_id,
                "dedupe_key": f"interaction:{recording.interaction_id}",
                "student_id": recording.student_id,
                "work_item_id": recording.work_item_id,
                "interaction_id": recording.interaction_id,
                "source_version": source_version,
            },
        )

    async def _pending_configuration(
        self,
        recording: ClaimedRecording,
        message: str = "OPENROUTER_API_KEY is not configured for speech to text",
    ) -> None:
        async with self._engine.begin() as connection:
            await self._lock_lease(connection, recording)
            await connection.execute(
                text(
                    """
                    UPDATE public.staff_call_recording
                    SET status = 'pending_configuration', attempts = 0,
                        lease_owner = NULL, lease_expires_at = NULL,
                        next_attempt_at = NULL,
                        last_error_code = 'provider_not_configured',
                        last_error_message = :message, version = version + 1,
                        updated_at = NOW()
                    WHERE id = :recording_id AND tenant_id = :tenant_id
                      AND status = 'transcribing' AND lease_owner = :worker_id
                    """
                ),
                {
                    "recording_id": recording.id,
                    "tenant_id": recording.tenant_id,
                    "worker_id": self._worker_id,
                    "message": message,
                },
            )

    async def _persist_failure(
        self,
        recording: ClaimedRecording,
        error: Exception,
    ) -> None:
        dead_letter = recording.attempts >= recording.max_attempts
        retry_delay_seconds = min(300, 5 * (2 ** max(0, recording.attempts - 1)))
        async with self._engine.begin() as connection:
            await self._lock_lease(connection, recording)
            await connection.execute(
                text(
                    """
                    UPDATE public.staff_call_recording
                    SET status = :status, lease_owner = NULL,
                        lease_expires_at = NULL,
                        next_attempt_at = CASE
                          WHEN :dead_letter THEN NULL
                          ELSE NOW() + make_interval(secs => :retry_delay_seconds)
                        END,
                        last_error_code = :error_code,
                        last_error_message = :error_message,
                        version = version + 1, updated_at = NOW()
                    WHERE id = :recording_id AND tenant_id = :tenant_id
                      AND status = 'transcribing' AND lease_owner = :worker_id
                    """
                ),
                {
                    "status": "dead_letter" if dead_letter else "failed_retryable",
                    "dead_letter": dead_letter,
                    "retry_delay_seconds": retry_delay_seconds,
                    "recording_id": recording.id,
                    "tenant_id": recording.tenant_id,
                    "worker_id": self._worker_id,
                    "error_code": type(error).__name__[:80],
                    "error_message": _bounded_text(str(error), 500) or "Call transcription failed",
                },
            )
            await self._notify(
                connection,
                recording,
                kind="call_transcription_failed",
                title="Call transcript needs attention",
                body=(
                    "Automatic transcription exhausted its retries. The original audio is safe; "
                    "review it and retry from the Action Center."
                    if dead_letter
                    else (
                        "Automatic transcription failed. The original audio is safe and can be "
                        "retried."
                    )
                ),
                dedupe_suffix=f"failure:{recording.attempts}",
            )

    async def _lock_lease(
        self,
        connection: AsyncConnection,
        recording: ClaimedRecording,
    ) -> Mapping[str, Any]:
        result = await connection.execute(
            text(
                """
                SELECT status, lease_owner
                FROM public.staff_call_recording
                WHERE id = :recording_id AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {"recording_id": recording.id, "tenant_id": recording.tenant_id},
        )
        current = result.mappings().first()
        if (
            current is None
            or current["status"] != "transcribing"
            or current["lease_owner"] != self._worker_id
        ):
            raise RuntimeError("The call transcription lease was lost")
        return dict(current)

    async def _insert_system_log(
        self,
        connection: AsyncConnection,
        recording: ClaimedRecording,
        message: str,
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO public.staff_work_log (
                  id, tenant_id, work_item_id, actor_type, actor_id,
                  actor_name, action, message, occurred_at
                ) VALUES (
                  :id, :tenant_id, :work_item_id, 'system', NULL, 'Speech to text',
                  'call_transcription_updated', :message, NOW()
                )
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": recording.tenant_id,
                "work_item_id": recording.work_item_id,
                "message": message,
            },
        )

    async def _notify(
        self,
        connection: AsyncConnection,
        recording: ClaimedRecording,
        *,
        kind: str,
        title: str,
        body: str,
        dedupe_suffix: str,
    ) -> None:
        await connection.execute(
            text(
                """
                INSERT INTO public.staff_notification (
                  id, tenant_id, staff_member_id, team_component, kind,
                  title, body, resource_type, resource_id, dedupe_key,
                  created_at
                )
                SELECT :id, item.tenant_id, item.assignee_id,
                       CASE WHEN item.assignee_id IS NULL THEN item.component ELSE NULL END,
                       :kind, :title, :body, 'staff_work_item', item.id,
                       :dedupe_key, NOW()
                FROM public.staff_work_item item
                WHERE item.tenant_id = :tenant_id AND item.id = :work_item_id
                ON CONFLICT (tenant_id, dedupe_key) DO NOTHING
                """
            ),
            {
                "id": self._uuid_factory(),
                "tenant_id": recording.tenant_id,
                "work_item_id": recording.work_item_id,
                "kind": kind,
                "title": title,
                "body": body,
                "dedupe_key": f"call-recording:{recording.id}:{dedupe_suffix}"[:240],
            },
        )


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _transcription_segments(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    segments: list[dict[str, object]] = []
    for candidate in value[:500]:
        if not isinstance(candidate, Mapping):
            continue
        text_value = _bounded_text(candidate.get("text"), 2_000)
        if not text_value:
            continue
        segments.append(
            {
                "start": _optional_float(candidate.get("start")),
                "end": _optional_float(candidate.get("end")),
                "text": text_value,
            }
        )
    return segments


def _bounded_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\x00", "").strip()[:limit]


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
