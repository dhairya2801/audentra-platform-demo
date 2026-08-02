from __future__ import annotations

import asyncio
import hashlib
import os
from uuid import uuid4

import pytest

from audentra.infrastructure.storage.s3 import (
    ObjectNotFoundError,
    S3ObjectStorage,
    S3StorageSettings,
)

pytestmark = pytest.mark.integration


def test_real_s3_put_head_get_delete_round_trip() -> None:
    endpoint = os.getenv("AUDENTRA_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("AUDENTRA_TEST_S3_ENDPOINT is not configured")

    async def scenario() -> None:
        storage = S3ObjectStorage(
            S3StorageSettings(
                endpoint_url=endpoint,
                region="us-east-1",
                bucket=os.getenv("AUDENTRA_TEST_S3_BUCKET", "vv-documents"),
                access_key_id=os.getenv("AUDENTRA_TEST_S3_ACCESS_KEY", "vv_minio"),
                secret_access_key=os.getenv("AUDENTRA_TEST_S3_SECRET_KEY", "vv_minio_password"),
                force_path_style=True,
            )
        )
        key = f"integration-tests/{uuid4()}.txt"
        body = b"audentra-fastapi-storage-round-trip"
        digest = hashlib.sha256(body).hexdigest()
        try:
            assert (
                await storage.put(
                    key,
                    body,
                    content_type="text/plain",
                    sha256=digest,
                )
                == digest
            )
            metadata = await storage.head(key)
            assert metadata.content_length == len(body)
            assert metadata.content_type == "text/plain"
            assert metadata.sha256 == digest
            assert await storage.get(key) == body
            await storage.delete(key)
            with pytest.raises(ObjectNotFoundError):
                await storage.get(key)
        finally:
            # Deleting a missing S3 object is idempotent and keeps failed tests isolated.
            await storage.delete(key)
            await storage.close()

    asyncio.run(scenario())
