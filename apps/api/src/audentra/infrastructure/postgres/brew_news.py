"""Bounded publisher RSS retrieval. Public editorial content is never AI instructions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

FEED_URL = "https://www.highereddive.com/feeds/news/"


class SummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.image: str | None = None

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        src = dict(attrs).get("src")
        if (
            tag == "img"
            and src
            and urlsplit(src).scheme == "https"
            and urlsplit(src).hostname == "imgproxy.divecdn.com"
        ):
            self.image = src


def parse_feed(raw: bytes, now: datetime) -> list[dict[str, Any]]:
    if len(raw) > 2_000_000 or b"\x00" in raw:
        raise ValueError("Unsupported feed encoding or size")
    decoded = raw.decode("utf-8")
    if "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
        raise ValueError("Unsupported feed document")
    root = ElementTree.fromstring(decoded)  # noqa: S314 -- bounded XML; DTD/entity declarations rejected above.
    articles: dict[str, dict[str, Any]] = {}
    for item in root.findall("./channel/item")[:100]:
        title = (item.findtext("title") or "").strip()[:300]
        url = urlsplit((item.findtext("link") or "").strip())
        if not title or url.scheme != "https" or url.hostname != "www.highereddive.com":
            continue
        if url.path.startswith("/spons/"):  # Sponsored items are not independent reporting.
            continue
        canonical = urlunsplit(("https", url.netloc, url.path, "", ""))
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(UTC)
        except (ValueError, TypeError, OverflowError):
            continue
        if published > now + timedelta(minutes=10):
            continue
        summary = SummaryParser()
        summary.feed(item.findtext("description") or "")
        articles[canonical] = {
            "id": hashlib.sha256(canonical.encode()).hexdigest()[:24],
            "title": title,
            "url": canonical,
            "source": "Higher Ed Dive",
            "publishedAt": published.isoformat(),
            "summary": unescape(" ".join(" ".join(summary.parts).split()))[:500],
            "imageUrl": summary.image,
        }
    return sorted(articles.values(), key=lambda r: r["publishedAt"], reverse=True)[:20]


async def retrieve_feed(
    etag: str | None, modified: str | None
) -> tuple[int, bytes, dict[str, str]]:
    headers = {
        "User-Agent": "Audentra-MorningBrew/1.0 (RSS reader)",
        "Accept": "application/rss+xml,application/xml",
    }
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    async with (
        httpx.AsyncClient(timeout=10, follow_redirects=False) as client,
        client.stream("GET", FEED_URL, headers=headers) as response,
    ):
        if response.status_code == 304:
            return 304, b"", dict(response.headers)
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > 2_000_000:
                raise ValueError("Feed exceeds size limit")
        return response.status_code, bytes(body), dict(response.headers)
