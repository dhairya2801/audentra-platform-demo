"""Deterministic in-memory adapters used by parity tests and local demos."""

from .adapters import FakeDocumentStorage, FakeStudentAI
from .store import DEMO_IDS, InMemoryPlatformStore

__all__ = ["DEMO_IDS", "FakeDocumentStorage", "FakeStudentAI", "InMemoryPlatformStore"]
