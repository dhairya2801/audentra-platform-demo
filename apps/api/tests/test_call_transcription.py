from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from audentra.infrastructure.worker.call_transcription import (
    OPENROUTER_TRANSCRIPTION_URL,
    CallTranscriptionRunner,
    ClaimedRecording,
    RecordingStorage,
    _bounded_text,
    _optional_float,
    _transcription_segments,
)


class StubStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.keys: list[str] = []

    async def get(self, key: str) -> bytes:
        self.keys.append(key)
        return self.content


class StubResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", OPENROUTER_TRANSCRIPTION_URL)
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("transcription failed", request=request, response=response)

    def json(self) -> object:
        return self._payload


class StubHttp:
    def __init__(self, response: StubResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> StubResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


class ProbeRunner(CallTranscriptionRunner):
    def __init__(
        self,
        storage: StubStorage,
        http: StubHttp,
        *,
        api_key: str,
    ) -> None:
        super().__init__(
            cast(AsyncEngine, object()),
            cast(RecordingStorage, storage),
            cast(httpx.AsyncClient, http),
            api_key=api_key,
            app_url="https://portal.example.edu",
            app_name="Audentra test",
        )
        self.successes: list[tuple[ClaimedRecording, str, Mapping[str, Any]]] = []
        self.configuration_messages: list[str] = []
        self.failures: list[Exception] = []

    async def _persist_success(
        self,
        recording: ClaimedRecording,
        transcript: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.successes.append((recording, transcript, payload))

    async def _pending_configuration(
        self,
        recording: ClaimedRecording,
        message: str = "OPENROUTER_API_KEY is not configured for speech to text",
    ) -> None:
        del recording
        self.configuration_messages.append(message)

    async def _persist_failure(
        self,
        recording: ClaimedRecording,
        error: Exception,
    ) -> None:
        del recording
        self.failures.append(error)


def _recording(audio: bytes) -> ClaimedRecording:
    return ClaimedRecording(
        id="recording-1",
        tenant_id="tenant-1",
        interaction_id="interaction-1",
        work_item_id="work-1",
        student_id="student-1",
        file_name="call.webm",
        mime_type="audio/webm",
        storage_key="tenant-1/call.webm",
        sha256=hashlib.sha256(audio).hexdigest(),
        attempts=1,
        max_attempts=5,
    )


def test_openrouter_transcription_sends_consent_stored_audio_and_keeps_response() -> None:
    audio = b"\x1a\x45\xdf\xa3synthetic-audio"
    storage = StubStorage(audio)
    http = StubHttp(
        StubResponse(
            200,
            {
                "text": "The student will upload the transcript tomorrow.",
                "language": "en",
                "segments": [{"start": 0, "end": 2, "text": "The student will upload."}],
            },
        )
    )
    runner = ProbeRunner(storage, http, api_key="openrouter-test-key")

    processed = asyncio.run(runner._process(_recording(audio)))

    assert processed is True
    assert storage.keys == ["tenant-1/call.webm"]
    assert len(http.calls) == 1
    request = http.calls[0]
    assert request["url"] == OPENROUTER_TRANSCRIPTION_URL
    assert request["headers"]["Authorization"] == "Bearer openrouter-test-key"
    assert request["headers"]["HTTP-Referer"] == "https://portal.example.edu"
    assert request["json"] == {
        "model": "openai/whisper-large-v3",
        "input_audio": {
            "data": base64.b64encode(audio).decode("ascii"),
            "format": "webm",
        },
        "temperature": 0,
    }
    assert runner.successes[0][1] == "The student will upload the transcript tomorrow."
    assert runner.failures == []


def test_missing_openrouter_key_preserves_recording_for_later_configuration() -> None:
    audio = b"\x1a\x45\xdf\xa3synthetic-audio"
    storage = StubStorage(audio)
    http = StubHttp(StubResponse(200, {"text": "unused"}))
    runner = ProbeRunner(storage, http, api_key="")

    processed = asyncio.run(runner._process(_recording(audio)))

    assert processed is False
    assert storage.keys == []
    assert http.calls == []
    assert runner.configuration_messages == [
        "OPENROUTER_API_KEY is not configured for speech to text"
    ]
    assert runner.failures == []


def test_rejected_openrouter_credentials_move_recording_to_configuration_state() -> None:
    audio = b"\x1a\x45\xdf\xa3synthetic-audio"
    storage = StubStorage(audio)
    http = StubHttp(StubResponse(401, {"error": "unauthorized"}))
    runner = ProbeRunner(storage, http, api_key="rejected-key")

    processed = asyncio.run(runner._process(_recording(audio)))

    assert processed is False
    assert runner.configuration_messages == ["OpenRouter credentials were rejected"]
    assert runner.failures == []


def test_checksum_mismatch_never_sends_audio_and_stays_retryable() -> None:
    expected_audio = b"\x1a\x45\xdf\xa3expected"
    storage = StubStorage(b"\x1a\x45\xdf\xa3changed")
    http = StubHttp(StubResponse(200, {"text": "unused"}))
    runner = ProbeRunner(storage, http, api_key="openrouter-test-key")

    processed = asyncio.run(runner._process(_recording(expected_audio)))

    assert processed is False
    assert http.calls == []
    assert len(runner.failures) == 1
    assert str(runner.failures[0]) == "Stored call recording checksum mismatch"


class _DbMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _DbResult:
    def __init__(self, rows: list[dict[str, object]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _DbMappings:
        return _DbMappings(self._rows)

    def scalar_one(self) -> object:
        assert len(self._rows) == 1 and len(self._rows[0]) == 1
        return next(iter(self._rows[0].values()))


class _DbConnection:
    def __init__(self, *, claim_rows: list[dict[str, object]] | None = None) -> None:
        self.claim_rows = claim_rows or []
        self.executions: list[tuple[str, dict[str, object]]] = []

    async def execute(
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> _DbResult:
        sql = str(statement)
        values = dict(parameters or {})
        self.executions.append((sql, values))
        if "WITH due AS" in sql:
            return _DbResult(self.claim_rows)
        if "SELECT status, lease_owner" in sql:
            return _DbResult([{"status": "transcribing", "lease_owner": "worker-1"}])
        if "SELECT COALESCE(MAX(version), 0) + 1" in sql:
            return _DbResult([{"version": 2}])
        if "SELECT source_version" in sql and "FROM public.staff_interaction" in sql:
            return _DbResult([{"source_version": 3}])
        return _DbResult()


class _DbContext(AbstractAsyncContextManager[_DbConnection]):
    def __init__(self, connection: _DbConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _DbConnection:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _DbEngine:
    def __init__(self, connection: _DbConnection) -> None:
        self.connection = connection

    def begin(self) -> _DbContext:
        return _DbContext(self.connection)


class _RunOnceRunner(CallTranscriptionRunner):
    def __init__(self, recordings: list[ClaimedRecording]) -> None:
        super().__init__(
            cast(AsyncEngine, object()),
            cast(RecordingStorage, StubStorage(b"audio")),
            cast(httpx.AsyncClient, StubHttp(StubResponse(200, {"text": "unused"}))),
            api_key="key",
            concurrency=2,
        )
        self.recordings = recordings
        self.processed: list[str] = []

    async def _claim(self) -> list[ClaimedRecording]:
        return self.recordings

    async def _process(self, recording: ClaimedRecording) -> bool:
        self.processed.append(recording.id)
        return recording.id != "failed"


def _database_runner(connection: _DbConnection) -> CallTranscriptionRunner:
    return CallTranscriptionRunner(
        cast(AsyncEngine, _DbEngine(connection)),
        cast(RecordingStorage, StubStorage(b"audio")),
        cast(httpx.AsyncClient, StubHttp(StubResponse(200, {"text": "unused"}))),
        api_key="openrouter-test-key",
        worker_id="worker-1",
        uuid_factory=uuid4,
    )


def test_successful_transcript_persistence_keeps_audio_evidence_and_queues_one_ai_job() -> None:
    connection = _DbConnection()
    runner = _database_runner(connection)
    recording = _recording(b"audio")

    asyncio.run(
        runner._persist_success(
            recording,
            "Advisor: The transcript is ready.\nStudent: Thank you.",
            {
                "language": "en",
                "duration": 22.5,
                "segments": [
                    {"start": 0, "end": 2.5, "text": "The transcript is ready."},
                    {"start": True, "end": None, "text": "Thank you."},
                    {"start": 3, "end": 4, "text": "   "},
                ],
            },
        )
    )

    statements = [sql for sql, _ in connection.executions]
    assert any("INSERT INTO public.staff_call_transcript_revision" in sql for sql in statements)
    assert any("INSERT INTO public.communication_event" in sql for sql in statements)
    assert any("INSERT INTO public.action_center_ai_job" in sql for sql in statements)
    assert any("INSERT INTO public.staff_work_log" in sql for sql in statements)
    assert any("INSERT INTO public.staff_notification" in sql for sql in statements)
    communication = next(
        values
        for sql, values in connection.executions
        if "INSERT INTO public.communication_event" in sql
    )
    assert communication["source_sequence"] == 4


@pytest.mark.parametrize(
    ("attempts", "expected_status", "dead_letter"),
    [(1, "failed_retryable", False), (5, "dead_letter", True)],
)
def test_transcription_failure_is_retryable_then_dead_letters_without_deleting_audio(
    attempts: int,
    expected_status: str,
    dead_letter: bool,
) -> None:
    connection = _DbConnection()
    runner = _database_runner(connection)
    recording = replace(_recording(b"audio"), attempts=attempts)

    asyncio.run(runner._persist_failure(recording, RuntimeError("temporary provider failure")))

    update = next(
        values
        for sql, values in connection.executions
        if "UPDATE public.staff_call_recording" in sql and "next_attempt_at" in sql
    )
    assert update["status"] == expected_status
    assert update["dead_letter"] is dead_letter
    assert update["error_message"] == "temporary provider failure"
    assert any("INSERT INTO public.staff_notification" in sql for sql, _ in connection.executions)


def test_pending_configuration_releases_lease_and_preserves_recording() -> None:
    connection = _DbConnection()
    runner = _database_runner(connection)

    asyncio.run(runner._pending_configuration(_recording(b"audio"), "Configure speech to text"))

    update = next(
        values
        for sql, values in connection.executions
        if "UPDATE public.staff_call_recording" in sql
    )
    assert update["message"] == "Configure speech to text"


def test_claim_maps_durable_recording_without_loading_audio() -> None:
    audio = b"audio"
    connection = _DbConnection(
        claim_rows=[
            {
                "id": "recording-1",
                "tenant_id": "tenant-1",
                "interaction_id": "interaction-1",
                "work_item_id": "work-1",
                "student_id": "student-1",
                "file_name": "call.webm",
                "mime_type": "audio/webm",
                "storage_key": "tenant-1/call.webm",
                "sha256": hashlib.sha256(audio).hexdigest(),
                "attempts": 2,
                "max_attempts": 5,
            }
        ]
    )
    runner = _database_runner(connection)

    claimed = asyncio.run(runner._claim())

    assert claimed == [
        ClaimedRecording(
            id="recording-1",
            tenant_id="tenant-1",
            interaction_id="interaction-1",
            work_item_id="work-1",
            student_id="student-1",
            file_name="call.webm",
            mime_type="audio/webm",
            storage_key="tenant-1/call.webm",
            sha256=hashlib.sha256(audio).hexdigest(),
            attempts=2,
            max_attempts=5,
        )
    ]
    claim_values = connection.executions[0][1]
    assert claim_values["configured"] is True
    assert claim_values["lease_seconds"] == 300


def test_run_once_processes_a_bounded_batch_concurrently_and_counts_successes() -> None:
    successful = _recording(b"audio")
    failed = replace(successful, id="failed")
    runner = _RunOnceRunner([successful, failed])

    assert asyncio.run(runner.run_once()) == 1
    assert runner.processed == ["recording-1", "failed"]
    assert asyncio.run(_RunOnceRunner([]).run_once()) == 0


def test_transcription_helpers_bound_provider_data_and_runner_limits() -> None:
    assert _optional_float(True) is None
    assert _optional_float("1.2") is None
    assert _optional_float(1) == 1.0
    assert _transcription_segments("bad") == []
    assert _transcription_segments([None, {"start": 0, "end": 2, "text": " hello \x00"}]) == [
        {"start": 0.0, "end": 2.0, "text": "hello"}
    ]
    assert _bounded_text(None, 5) == ""
    assert _bounded_text("  abcdef  ", 3) == "abc"
    with pytest.raises(ValueError, match="batch_size"):
        CallTranscriptionRunner(
            cast(AsyncEngine, object()),
            cast(RecordingStorage, StubStorage(b"")),
            cast(httpx.AsyncClient, object()),
            api_key="key",
            batch_size=0,
        )
    with pytest.raises(ValueError, match="concurrency"):
        CallTranscriptionRunner(
            cast(AsyncEngine, object()),
            cast(RecordingStorage, StubStorage(b"")),
            cast(httpx.AsyncClient, object()),
            api_key="key",
            batch_size=1,
            concurrency=2,
        )
