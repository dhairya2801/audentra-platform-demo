"""Cloud Storage adapter using Application Default Credentials.

The Cloud Run runtime service account obtains credentials from the metadata
server.  This deliberately avoids the static S3/HMAC credentials required by
the legacy compatibility adapter.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import storage as google_storage  # type: ignore[import-untyped]

from .s3 import (
    DEFAULT_MAX_OBJECT_BYTES,
    ObjectNotFoundError,
    S3ObjectMetadata,
    StorageError,
    StorageObjectTooLargeError,
    _optional_string,
    _validate_key,
)


@dataclass(frozen=True, slots=True)
class GcsStorageSettings:
    """Configuration for a Cloud Storage bucket owned by the GCP project."""

    bucket: str
    project_id: str | None = None
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES

    def __post_init__(self) -> None:
        if not self.bucket.strip():
            raise ValueError("object-storage bucket is required")
        if self.max_object_bytes < 1:
            raise ValueError("max_object_bytes must be positive")


class GcsObjectStorage:
    """Bounded Cloud Storage facade with the same document-safety semantics as S3."""

    def __init__(
        self,
        settings: GcsStorageSettings,
        *,
        client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or google_storage.Client(project=settings.project_id)
        self._bucket = self._client.bucket(settings.bucket)

    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        sha256: str | None = None,
    ) -> str:
        normalized_key = _validate_key(key)
        if not body or len(body) > self._settings.max_object_bytes:
            raise StorageObjectTooLargeError(
                f"object must be between 1 and {self._settings.max_object_bytes} bytes"
            )
        digest = hashlib.sha256(body).hexdigest()
        if sha256 is not None and sha256.lower() != digest:
            raise ValueError("provided sha256 does not match object bytes")
        blob = self._bucket.blob(normalized_key)
        blob.metadata = {"sha256": digest}
        try:
            await asyncio.to_thread(
                blob.upload_from_string,
                body,
                content_type=content_type,
                checksum="auto",
            )
        except GoogleAPICallError as error:
            raise StorageError(f"object-storage request failed for {normalized_key!r}") from error
        return digest

    async def get(self, key: str) -> bytes:
        normalized_key = _validate_key(key)
        blob = await self._get_blob(normalized_key)
        declared_length = int(blob.size or 0)
        if declared_length > self._settings.max_object_bytes:
            raise StorageObjectTooLargeError(
                f"object {normalized_key!r} exceeds the configured read limit"
            )
        try:
            # ``end`` is inclusive.  Reading one extra byte ensures an absent
            # or stale size cannot bypass the same bound used on uploads.
            data = cast(
                bytes,
                await asyncio.to_thread(
                    blob.download_as_bytes,
                    start=0,
                    end=self._settings.max_object_bytes,
                ),
            )
        except NotFound as error:
            raise ObjectNotFoundError(f"object {normalized_key!r} was not found") from error
        except GoogleAPICallError as error:
            raise StorageError(f"object-storage request failed for {normalized_key!r}") from error
        if len(data) > self._settings.max_object_bytes:
            raise StorageObjectTooLargeError(
                f"object {normalized_key!r} exceeds the configured read limit"
            )
        return data

    async def head(self, key: str) -> S3ObjectMetadata:
        normalized_key = _validate_key(key)
        blob = await self._get_blob(normalized_key)
        metadata = blob.metadata
        metadata_mapping = metadata if isinstance(metadata, Mapping) else {}
        return S3ObjectMetadata(
            key=normalized_key,
            content_length=int(blob.size or 0),
            content_type=_optional_string(blob.content_type),
            sha256=_optional_string(metadata_mapping.get("sha256")),
            etag=_optional_string(blob.etag),
        )

    async def delete(self, key: str) -> None:
        normalized_key = _validate_key(key)
        try:
            await asyncio.to_thread(self._bucket.delete_blob, normalized_key)
        except NotFound:
            # Preserve S3's idempotent delete behavior.
            return
        except GoogleAPICallError as error:
            raise StorageError(f"object-storage request failed for {normalized_key!r}") from error

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)

    async def _get_blob(self, key: str) -> Any:
        try:
            blob = await asyncio.to_thread(self._bucket.get_blob, key)
        except NotFound as error:
            raise ObjectNotFoundError(f"object {key!r} was not found") from error
        except GoogleAPICallError as error:
            raise StorageError(f"object-storage request failed for {key!r}") from error
        if blob is None:
            raise ObjectNotFoundError(f"object {key!r} was not found")
        return blob
