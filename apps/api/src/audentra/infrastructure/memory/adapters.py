"""Controllable fake object storage and AI providers."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from typing import Any


class FakeDocumentStorage:
    """An opaque object store with injectable transient failures."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_puts = 0
        self.fail_gets = 0

    async def put(self, key: str, content: bytes, _content_type: str, _sha256: str) -> None:
        if self.fail_puts:
            self.fail_puts -= 1
            raise OSError("Simulated object storage outage")
        self.objects[key] = bytes(content)

    async def get(self, key: str) -> bytes:
        if self.fail_gets:
            self.fail_gets -= 1
            raise OSError("Simulated object storage outage")
        try:
            return bytes(self.objects[key])
        except KeyError as error:
            raise OSError("Missing test object") from error


class FakeStudentAI:
    """A deterministic provider whose extraction outcomes can be queued."""

    def __init__(self) -> None:
        self.extraction_calls = 0
        self.extraction_outcomes: deque[dict[str, Any] | BaseException] = deque()
        self.edward_response: dict[str, Any] = {
            "message": "Open Documents to review your upload.",
            "provider": "openrouter",
            "model": "test/edward",
            "usage": {"promptTokens": 50, "completionTokens": 8, "totalTokens": 58},
            "suggestedActions": [{"label": "Open documents", "href": "/documents"}],
            "contextReceipts": [{"source": "dashboard"}],
            "widgets": [],
        }

    def queue_extraction(self, *outcomes: dict[str, Any] | BaseException) -> None:
        self.extraction_outcomes.extend(outcomes)

    async def extract_document(self, **_input: object) -> dict[str, Any]:
        self.extraction_calls += 1
        if self.extraction_outcomes:
            outcome = self.extraction_outcomes.popleft()
            if isinstance(outcome, BaseException):
                raise outcome
            return deepcopy(outcome)
        return {
            "status": "completed",
            "documentType": "ferpa",
            "summary": "A student records release authorization.",
            "studentName": "Alex Morgan",
            "institutionName": "Aster University",
            "issueDate": None,
            "academicTerm": None,
            "fields": [
                {
                    "key": "student_name",
                    "label": "Student name",
                    "value": "Alex Morgan",
                    "confidence": 0.98,
                }
            ],
            "courses": [],
            "visualRegions": [],
            "warnings": [],
            "model": "test/document-model",
            "provider": "openrouter",
            "processedAt": "2026-07-24T12:00:00.000Z",
            "verifiedAt": None,
        }

    async def ask_edward(self, **_input: object) -> dict[str, Any]:
        return deepcopy(self.edward_response)
