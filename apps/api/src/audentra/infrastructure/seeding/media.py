"""Checksum-first portal-media upload service."""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from audentra.infrastructure.storage.s3 import (
    ObjectNotFoundError,
    S3ObjectMetadata,
)

from .safety import assert_seed_environment

ASTER_TENANT_ID = "00000000-0000-7000-8000-000000000001"


@dataclass(frozen=True, slots=True)
class PortalMediaAsset:
    relative_path: str
    storage_key: str
    sha256: str


PORTAL_MEDIA_ASSETS: tuple[PortalMediaAsset, ...] = (
    PortalMediaAsset(
        "housing/aster-residence-hall-room.jpg",
        f"tenants/{ASTER_TENANT_ID}/portal-media/housing/aster-residence-hall-room.jpg",
        "5d72de56956d1cac2cac29d85d732a1fca8b9b86cf882a33f0be6b6d664fb05e",
    ),
    PortalMediaAsset(
        "housing/aster-apartments-room.jpg",
        f"tenants/{ASTER_TENANT_ID}/portal-media/housing/aster-apartments-room.jpg",
        "a836c4fcaa9fd9a5926694a806a23a710048f27bba4454dfb138954ee2858449",
    ),
    PortalMediaAsset(
        "housing/student-village-room.jpg",
        f"tenants/{ASTER_TENANT_ID}/portal-media/housing/student-village-room.jpg",
        "da850065779e2ad3f51a5ee748f939159215b87a684667a6fdceefe93fd3d7d3",
    ),
    PortalMediaAsset(
        "clubs/robotics.jpg",
        f"tenants/{ASTER_TENANT_ID}/portal-media/clubs/robotics.jpg",
        "e5f1a809ff85bfb326bb098b1a9109cb68cce8c15314a4d8c336ee5cfaaa4296",
    ),
    PortalMediaAsset(
        "clubs/code-collective.jpg",
        f"tenants/{ASTER_TENANT_ID}/portal-media/clubs/code-collective.jpg",
        "9fc235b4d5662ff3e10f7e761f67c19cfb229efad60e578f951befc21ba49270",
    ),
    PortalMediaAsset(
        "clubs/women-in-business.jpg",
        f"tenants/{ASTER_TENANT_ID}/portal-media/clubs/women-in-business.jpg",
        "d85e5278b3a53fcf7b9936dad89f29e30febb0f8ece128c8b0e4d0bb487fdeed",
    ),
    PortalMediaAsset(
        "clubs/outdoor-aster.jpg",
        f"tenants/{ASTER_TENANT_ID}/portal-media/clubs/outdoor-aster.jpg",
        "0cd40b90c9a7306af3909de672e45704399745a416cf72549f849fed2dde42c3",
    ),
)


class MediaStorage(Protocol):
    async def head(self, key: str) -> S3ObjectMetadata: ...

    async def put(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str,
        sha256: str | None = None,
    ) -> str: ...


class PortalMediaChecksumError(RuntimeError):
    """A checked-in media file no longer matches its reviewed manifest."""


@dataclass(frozen=True, slots=True)
class MediaSeedReport:
    assets: int
    uploaded: int
    unchanged: int


async def seed_portal_media(
    storage: MediaStorage,
    *,
    environment: str,
    media_root: Path | None = None,
    assets: Sequence[PortalMediaAsset] = PORTAL_MEDIA_ASSETS,
) -> MediaSeedReport:
    """Verify every local object before making any remote write."""

    assert_seed_environment(environment)
    root = resolve_media_root(media_root)
    verified = await asyncio.gather(
        *(_read_verified(_asset_path(root, asset), asset) for asset in assets)
    )
    uploaded = 0
    unchanged = 0
    for asset, body in zip(assets, verified, strict=True):
        try:
            existing = await storage.head(asset.storage_key)
        except ObjectNotFoundError:
            existing = None
        if (
            existing is not None
            and existing.sha256 == asset.sha256
            and existing.content_length == len(body)
        ):
            unchanged += 1
            continue
        await storage.put(
            asset.storage_key,
            body,
            content_type="image/jpeg",
            sha256=asset.sha256,
        )
        uploaded += 1
    return MediaSeedReport(assets=len(assets), uploaded=uploaded, unchanged=unchanged)


def resolve_media_root(explicit: Path | None = None) -> Path:
    configured = explicit or (
        Path(os.environ["PORTAL_MEDIA_DIR"]).expanduser()
        if os.environ.get("PORTAL_MEDIA_DIR", "").strip()
        else None
    )
    candidates = tuple(
        path
        for path in (
            configured,
            Path.cwd() / "assets" / "portal-media",
            Path(__file__).resolve().parents[4] / "assets" / "portal-media",
        )
        if path is not None
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise FileNotFoundError("Portal media directory was not found; set PORTAL_MEDIA_DIR")


def _asset_path(root: Path, asset: PortalMediaAsset) -> Path:
    path = (root / Path(asset.relative_path)).resolve()
    if root not in path.parents:
        raise PortalMediaChecksumError("Portal media manifest contains an unsafe path")
    return path


async def _read_verified(path: Path, asset: PortalMediaAsset) -> bytes:
    body = await asyncio.to_thread(path.read_bytes)
    digest = hashlib.sha256(body).hexdigest()
    if digest != asset.sha256:
        raise PortalMediaChecksumError(f"Portal media checksum mismatch: {path}")
    return body
