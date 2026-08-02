"""Bounded async facade over boto3's synchronous S3 client."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

DEFAULT_MAX_OBJECT_BYTES = 10 * 1024 * 1024
_OBJECT_KEY_PATTERN = re.compile(r"^[^\\\x00-\x1f]{1,1024}$")


class StorageError(RuntimeError):
    """Base error for object-storage operations."""


class ObjectNotFoundError(StorageError):
    """The requested object does not exist."""


class StorageObjectTooLargeError(StorageError):
    """An object exceeds the configured in-memory safety bound."""


class _StreamingBody(Protocol):
    def read(self, amount: int | None = None) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class S3StorageSettings:
    bucket: str
    region: str = "us-east-1"
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    force_path_style: bool = False
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES

    def __post_init__(self) -> None:
        if not self.bucket.strip():
            raise ValueError("object-storage bucket is required")
        if self.max_object_bytes < 1:
            raise ValueError("max_object_bytes must be positive")
        credentials = (self.access_key_id, self.secret_access_key)
        if (credentials[0] is None) is not (credentials[1] is None):
            raise ValueError("object-storage credentials must be configured together")


@dataclass(frozen=True, slots=True)
class S3ObjectMetadata:
    key: str
    content_length: int
    content_type: str | None
    sha256: str | None
    etag: str | None


class S3ObjectStorage:
    """Store small documents without ever running boto3 on the event loop."""

    def __init__(
        self,
        settings: S3StorageSettings,
        *,
        client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._client = client if client is not None else self._create_client(settings)

    @staticmethod
    def _create_client(settings: S3StorageSettings) -> Any:
        options: dict[str, object] = {
            "service_name": "s3",
            "region_name": settings.region,
        }
        if settings.endpoint_url:
            options["endpoint_url"] = settings.endpoint_url
        if settings.access_key_id is not None:
            options["aws_access_key_id"] = settings.access_key_id
            options["aws_secret_access_key"] = settings.secret_access_key
        if settings.force_path_style:
            from botocore.config import Config  # type: ignore[import-untyped]

            options["config"] = Config(s3={"addressing_style": "path"})
        return boto3.client(**options)

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
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._settings.bucket,
            Key=normalized_key,
            Body=body,
            ContentType=content_type,
            Metadata={"sha256": digest},
        )
        return digest

    async def get(self, key: str) -> bytes:
        normalized_key = _validate_key(key)
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._settings.bucket,
                Key=normalized_key,
            )
        except ClientError as error:
            self._raise_mapped_error(error, normalized_key)

        declared_length = int(response.get("ContentLength", 0))
        if declared_length > self._settings.max_object_bytes:
            body = cast(_StreamingBody | None, response.get("Body"))
            if body is not None:
                await asyncio.to_thread(body.close)
            raise StorageObjectTooLargeError(
                f"object {normalized_key!r} exceeds the configured read limit"
            )
        stream = cast(_StreamingBody | None, response.get("Body"))
        if stream is None:
            raise StorageError(f"object {normalized_key!r} has no response body")
        try:
            # Read one extra byte so an absent or incorrect Content-Length cannot
            # bypass the same bound applied to uploads.
            data = await asyncio.to_thread(stream.read, self._settings.max_object_bytes + 1)
        finally:
            await asyncio.to_thread(stream.close)
        if len(data) > self._settings.max_object_bytes:
            raise StorageObjectTooLargeError(
                f"object {normalized_key!r} exceeds the configured read limit"
            )
        return data

    async def head(self, key: str) -> S3ObjectMetadata:
        normalized_key = _validate_key(key)
        try:
            response = await asyncio.to_thread(
                self._client.head_object,
                Bucket=self._settings.bucket,
                Key=normalized_key,
            )
        except ClientError as error:
            self._raise_mapped_error(error, normalized_key)
        metadata = response.get("Metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        return S3ObjectMetadata(
            key=normalized_key,
            content_length=int(response.get("ContentLength", 0)),
            content_type=_optional_string(response.get("ContentType")),
            sha256=_optional_string(metadata_dict.get("sha256")),
            etag=_optional_string(response.get("ETag")),
        )

    async def delete(self, key: str) -> None:
        normalized_key = _validate_key(key)
        await asyncio.to_thread(
            self._client.delete_object,
            Bucket=self._settings.bucket,
            Key=normalized_key,
        )

    async def close(self) -> None:
        """Release the SDK connection pool without blocking the event loop."""

        close = getattr(self._client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)

    @staticmethod
    def _raise_mapped_error(error: ClientError, key: str) -> NoReturn:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code in {"NoSuchKey", "NotFound", "404"}:
            raise ObjectNotFoundError(f"object {key!r} was not found") from error
        raise StorageError(f"object-storage request failed for {key!r}") from error


def _validate_key(key: str) -> str:
    value = key.strip()
    if not _OBJECT_KEY_PATTERN.fullmatch(value) or value.startswith("/"):
        raise ValueError("object key is invalid")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("object key contains an invalid path segment")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
