"""Object-storage adapters used by the API and background worker."""

from __future__ import annotations

from typing import Protocol

from .gcs import GcsObjectStorage, GcsStorageSettings
from .s3 import (
    ObjectNotFoundError,
    S3ObjectMetadata,
    S3ObjectStorage,
    S3StorageSettings,
    StorageObjectTooLargeError,
)

StorageSettings = S3StorageSettings | GcsStorageSettings


class ObjectStorage(Protocol):
    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        sha256: str | None = None,
    ) -> str: ...

    async def get(self, key: str) -> bytes: ...

    async def head(self, key: str) -> S3ObjectMetadata: ...

    async def delete(self, key: str) -> None: ...

    async def close(self) -> None: ...


def create_object_storage(settings: StorageSettings) -> ObjectStorage:
    if isinstance(settings, GcsStorageSettings):
        return GcsObjectStorage(settings)
    return S3ObjectStorage(settings)


__all__ = [
    "GcsObjectStorage",
    "GcsStorageSettings",
    "ObjectNotFoundError",
    "ObjectStorage",
    "S3ObjectMetadata",
    "S3ObjectStorage",
    "S3StorageSettings",
    "StorageObjectTooLargeError",
    "StorageSettings",
    "create_object_storage",
]
