from __future__ import annotations

import asyncio
import hashlib
from typing import Any, cast

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from audentra.infrastructure.storage.s3 import (
    ObjectNotFoundError,
    S3ObjectMetadata,
    S3ObjectStorage,
    S3StorageSettings,
    StorageError,
    StorageObjectTooLargeError,
)

TEST_STORAGE_SECRET = "storage-secret"  # noqa: S105 -- inert test credential


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "request failed"}},
        "GetObject",
    )


class FakeBody:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.read_amounts: list[int | None] = []
        self.closed = False

    def read(self, amount: int | None = None) -> bytes:
        self.read_amounts.append(amount)
        return self.content if amount is None else self.content[:amount]

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.get_response: dict[str, object] | BaseException = {}
        self.head_response: dict[str, object] | BaseException = {}
        self.closed = False

    def put_object(self, **kwargs: object) -> None:
        self.put_calls.append(dict(kwargs))

    def get_object(self, **kwargs: object) -> dict[str, object]:
        self.get_calls.append(dict(kwargs))
        if isinstance(self.get_response, BaseException):
            raise self.get_response
        return dict(self.get_response)

    def head_object(self, **kwargs: object) -> dict[str, object]:
        self.head_calls.append(dict(kwargs))
        if isinstance(self.head_response, BaseException):
            raise self.head_response
        return dict(self.head_response)

    def delete_object(self, **kwargs: object) -> None:
        self.delete_calls.append(dict(kwargs))

    def close(self) -> None:
        self.closed = True


def _storage(client: FakeS3Client, *, limit: int = 16) -> S3ObjectStorage:
    return S3ObjectStorage(
        S3StorageSettings(bucket="documents", max_object_bytes=limit),
        client=client,
    )


@pytest.mark.parametrize(
    "kwargs",
    (
        {"bucket": ""},
        {"bucket": "documents", "max_object_bytes": 0},
        {"bucket": "documents", "access_key_id": "key"},
        {"bucket": "documents", "secret_access_key": "secret"},
    ),
)
def test_storage_settings_reject_incomplete_or_unbounded_configuration(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        S3StorageSettings(**kwargs)


def test_default_client_receives_endpoint_credentials_and_path_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    client = FakeS3Client()

    def create_client(**kwargs: object) -> FakeS3Client:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(boto3, "client", create_client)
    storage = S3ObjectStorage(
        S3StorageSettings(
            bucket="documents",
            region="eu-west-1",
            endpoint_url="http://minio:9000",
            access_key_id="access",
            secret_access_key=TEST_STORAGE_SECRET,
            force_path_style=True,
        )
    )

    assert storage is not None
    assert captured["service_name"] == "s3"
    assert captured["region_name"] == "eu-west-1"
    assert captured["endpoint_url"] == "http://minio:9000"
    assert captured["aws_access_key_id"] == "access"
    assert captured["aws_secret_access_key"] == TEST_STORAGE_SECRET
    assert cast(Any, captured["config"]).s3 == {"addressing_style": "path"}


def test_put_hashes_content_and_forwards_integrity_metadata() -> None:
    client = FakeS3Client()
    storage = _storage(client)
    content = b"document"
    digest = hashlib.sha256(content).hexdigest()

    result = asyncio.run(
        storage.put(
            " tenant/student/document.pdf ",
            content,
            content_type="application/pdf",
            sha256=digest.upper(),
        )
    )

    assert result == digest
    assert client.put_calls == [
        {
            "Bucket": "documents",
            "Key": "tenant/student/document.pdf",
            "Body": content,
            "ContentType": "application/pdf",
            "Metadata": {"sha256": digest},
        }
    ]


@pytest.mark.parametrize("content", [b"", b"0123456789abcdefg"])
def test_put_rejects_empty_or_oversized_content_before_network(content: bytes) -> None:
    client = FakeS3Client()
    with pytest.raises(StorageObjectTooLargeError):
        asyncio.run(_storage(client).put("document.pdf", content, content_type="application/pdf"))
    assert client.put_calls == []


def test_put_rejects_mismatched_caller_digest() -> None:
    client = FakeS3Client()
    with pytest.raises(ValueError, match="sha256"):
        asyncio.run(
            _storage(client).put(
                "document.pdf",
                b"document",
                content_type="application/pdf",
                sha256="0" * 64,
            )
        )
    assert client.put_calls == []


@pytest.mark.parametrize(
    "key",
    ["", "/root", "tenant//document", "tenant/../document", "tenant\\document", "a\x00b"],
)
def test_operations_reject_unsafe_object_keys(key: str) -> None:
    client = FakeS3Client()
    with pytest.raises(ValueError):
        asyncio.run(_storage(client).get(key))
    assert client.get_calls == []


def test_get_reads_one_extra_byte_and_always_closes_stream() -> None:
    client = FakeS3Client()
    body = FakeBody(b"document")
    client.get_response = {"ContentLength": 8, "Body": body}

    result = asyncio.run(_storage(client).get("document.pdf"))

    assert result == b"document"
    assert body.read_amounts == [17]
    assert body.closed is True
    assert client.get_calls == [{"Bucket": "documents", "Key": "document.pdf"}]


def test_get_closes_declared_oversized_stream_without_reading() -> None:
    client = FakeS3Client()
    body = FakeBody(b"not-read")
    client.get_response = {"ContentLength": 17, "Body": body}

    with pytest.raises(StorageObjectTooLargeError):
        asyncio.run(_storage(client).get("document.pdf"))

    assert body.read_amounts == []
    assert body.closed is True


def test_get_detects_oversize_when_content_length_is_missing_or_wrong() -> None:
    client = FakeS3Client()
    body = FakeBody(b"0123456789abcdefg")
    client.get_response = {"ContentLength": 1, "Body": body}

    with pytest.raises(StorageObjectTooLargeError):
        asyncio.run(_storage(client).get("document.pdf"))

    assert body.read_amounts == [17]
    assert body.closed is True


def test_get_rejects_response_without_stream() -> None:
    client = FakeS3Client()
    client.get_response = {"ContentLength": 4}
    with pytest.raises(StorageError, match="no response body"):
        asyncio.run(_storage(client).get("document.pdf"))


@pytest.mark.parametrize("code", ["NoSuchKey", "NotFound", "404"])
def test_get_maps_provider_not_found_codes(code: str) -> None:
    client = FakeS3Client()
    client.get_response = _client_error(code)

    with pytest.raises(ObjectNotFoundError, match=r"document\.pdf"):
        asyncio.run(_storage(client).get("document.pdf"))


def test_head_maps_generic_provider_error_without_leaking_sdk_error() -> None:
    client = FakeS3Client()
    client.head_response = _client_error("AccessDenied")

    with pytest.raises(StorageError, match=r"object-storage request failed") as raised:
        asyncio.run(_storage(client).head("document.pdf"))

    assert isinstance(raised.value.__cause__, ClientError)


def test_head_delete_and_close_expose_sanitized_metadata_and_release_pool() -> None:
    client = FakeS3Client()
    client.head_response = {
        "ContentLength": 123,
        "ContentType": "application/pdf",
        "Metadata": {"sha256": "digest"},
        "ETag": '"etag"',
    }
    storage = _storage(client)

    metadata = asyncio.run(storage.head("tenant/document.pdf"))
    asyncio.run(storage.delete("tenant/document.pdf"))
    asyncio.run(storage.close())

    assert metadata == S3ObjectMetadata(
        key="tenant/document.pdf",
        content_length=123,
        content_type="application/pdf",
        sha256="digest",
        etag='"etag"',
    )
    assert client.delete_calls == [{"Bucket": "documents", "Key": "tenant/document.pdf"}]
    assert client.closed is True


def test_head_tolerates_absent_or_malformed_optional_metadata() -> None:
    client = FakeS3Client()
    client.head_response = {
        "ContentLength": 0,
        "ContentType": 123,
        "Metadata": "not-a-map",
        "ETag": "",
    }

    metadata = asyncio.run(_storage(client).head("document.pdf"))

    assert metadata.content_type is None
    assert metadata.sha256 is None
    assert metadata.etag is None


def test_close_accepts_sdk_clients_without_close_method() -> None:
    class ClientWithoutClose:
        pass

    storage = S3ObjectStorage(
        S3StorageSettings(bucket="documents"),
        client=ClientWithoutClose(),
    )
    asyncio.run(storage.close())
