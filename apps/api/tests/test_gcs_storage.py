from __future__ import annotations

import asyncio
import hashlib

import pytest
from google.api_core.exceptions import Forbidden, NotFound

from audentra.infrastructure.storage.gcs import GcsObjectStorage, GcsStorageSettings
from audentra.infrastructure.storage.s3 import (
    ObjectNotFoundError,
    S3ObjectMetadata,
    StorageError,
    StorageObjectTooLargeError,
)


class FakeBlob:
    def __init__(self, *, body: bytes = b"", size: int | None = None) -> None:
        self.body = body
        self.size = len(body) if size is None else size
        self.content_type: str | None = "application/pdf"
        self.metadata: object = {"sha256": hashlib.sha256(body).hexdigest()}
        self.etag: str | None = "etag"
        self.uploads: list[dict[str, object]] = []
        self.downloads: list[dict[str, object]] = []

    def upload_from_string(self, body: bytes, **kwargs: object) -> None:
        self.body = body
        self.size = len(body)
        self.uploads.append({"body": body, **kwargs})

    def download_as_bytes(self, **kwargs: object) -> bytes:
        self.downloads.append(dict(kwargs))
        end = kwargs.get("end")
        return self.body if not isinstance(end, int) else self.body[: end + 1]


class FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, FakeBlob] = {}
        self.deleted: list[str] = []
        self.failure: BaseException | None = None

    def blob(self, key: str) -> FakeBlob:
        return self.objects.setdefault(key, FakeBlob())

    def get_blob(self, key: str) -> FakeBlob | None:
        if self.failure is not None:
            raise self.failure
        return self.objects.get(key)

    def delete_blob(self, key: str) -> None:
        self.deleted.append(key)
        if self.failure is not None:
            raise self.failure
        self.objects.pop(key, None)


class FakeClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self._bucket = bucket
        self.bucket_names: list[str] = []
        self.closed = False

    def bucket(self, name: str) -> FakeBucket:
        self.bucket_names.append(name)
        return self._bucket

    def close(self) -> None:
        self.closed = True


def _storage(bucket: FakeBucket, *, limit: int = 16) -> tuple[GcsObjectStorage, FakeClient]:
    client = FakeClient(bucket)
    return (
        GcsObjectStorage(
            GcsStorageSettings(bucket="documents", project_id="audentra", max_object_bytes=limit),
            client=client,
        ),
        client,
    )


def test_gcs_put_preserves_content_integrity_metadata() -> None:
    bucket = FakeBucket()
    storage, client = _storage(bucket)
    body = b"document"
    digest = hashlib.sha256(body).hexdigest()

    result = asyncio.run(
        storage.put("tenant/document.pdf", body, content_type="application/pdf", sha256=digest)
    )

    assert result == digest
    blob = bucket.objects["tenant/document.pdf"]
    assert blob.metadata == {"sha256": digest}
    assert blob.uploads == [{"body": body, "content_type": "application/pdf", "checksum": "auto"}]
    assert client.bucket_names == ["documents"]


def test_gcs_rejects_unsafe_or_oversized_values_before_the_provider() -> None:
    storage, _client = _storage(FakeBucket())

    with pytest.raises(ValueError):
        asyncio.run(
            storage.put("tenant/../document.pdf", b"document", content_type="application/pdf")
        )
    with pytest.raises(StorageObjectTooLargeError):
        asyncio.run(
            storage.put("document.pdf", b"0123456789abcdefg", content_type="application/pdf")
        )


def test_gcs_get_reads_one_extra_byte_and_enforces_the_limit() -> None:
    bucket = FakeBucket()
    bucket.objects["document.pdf"] = FakeBlob(body=b"0123456789abcdefg", size=1)
    storage, _client = _storage(bucket)

    with pytest.raises(StorageObjectTooLargeError):
        asyncio.run(storage.get("document.pdf"))

    assert bucket.objects["document.pdf"].downloads == [{"start": 0, "end": 16}]


def test_gcs_head_delete_and_close_keep_s3_compatible_semantics() -> None:
    bucket = FakeBucket()
    body = b"document"
    bucket.objects["tenant/document.pdf"] = FakeBlob(body=body)
    storage, client = _storage(bucket)

    metadata = asyncio.run(storage.head("tenant/document.pdf"))
    asyncio.run(storage.delete("tenant/document.pdf"))
    asyncio.run(storage.delete("tenant/document.pdf"))
    asyncio.run(storage.close())

    assert metadata == S3ObjectMetadata(
        key="tenant/document.pdf",
        content_length=len(body),
        content_type="application/pdf",
        sha256=hashlib.sha256(body).hexdigest(),
        etag="etag",
    )
    assert bucket.deleted == ["tenant/document.pdf", "tenant/document.pdf"]
    assert client.closed is True


def test_gcs_maps_not_found_and_provider_failures_without_sdk_leaks() -> None:
    bucket = FakeBucket()
    storage, _client = _storage(bucket)
    with pytest.raises(ObjectNotFoundError):
        asyncio.run(storage.get("missing.pdf"))

    bucket.failure = Forbidden("denied")  # type: ignore[no-untyped-call]
    with pytest.raises(StorageError, match="object-storage request failed") as raised:
        asyncio.run(storage.head("document.pdf"))
    assert isinstance(raised.value.__cause__, Forbidden)


def test_gcs_maps_explicit_not_found_from_the_provider() -> None:
    bucket = FakeBucket()
    bucket.failure = NotFound("missing")  # type: ignore[no-untyped-call]
    storage, _client = _storage(bucket)

    with pytest.raises(ObjectNotFoundError):
        asyncio.run(storage.head("document.pdf"))
