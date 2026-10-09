"""Spreadsheet-specific boundaries: external news and displayed demo facts."""

from datetime import UTC, datetime
from typing import Any

import pytest

from audentra.infrastructure.postgres.brew_news import parse_feed
from audentra.integrations.staff_assistant.pulse_context import describe_displayed_pulse

NOW = datetime(2026, 10, 5, 18, tzinfo=UTC)


def item(path: str = "/news/example/123/", date: str = "Mon, 05 Oct 2026 12:00:00 GMT") -> str:
    return f"""<item><title>Publisher report</title><link>https://www.highereddive.com{path}</link>
    <pubDate>{date}</pubDate><description><![CDATA[<img src="https://imgproxy.divecdn.com/test.jpg"/>
    <p>A &amp; B report on enrollment.</p>]]></description></item>"""


def test_news_dates_deduplication_and_safe_images() -> None:
    articles = parse_feed(
        (
            "<rss><channel>"
            + item()
            + item("?tracking=yes")
            + item()
            + item("/spons/advertisement/")
            + item("/news/future/", "Fri, 09 Oct 2026 12:00:00 GMT")
            + item("/news/undated/", "")
            + "</channel></rss>"
        ).encode(),
        NOW,
    )
    assert len(articles) == 2
    assert articles[0]["publishedAt"] == "2026-10-05T12:00:00+00:00"
    assert "<" not in articles[0]["summary"]
    assert "A & B" in articles[0]["summary"]
    assert articles[0]["imageUrl"] == "https://imgproxy.divecdn.com/test.jpg"
    duplicate = (
        "<rss><channel>" + item() + item("/news/example/123/?tracking=yes") + "</channel></rss>"
    )
    assert len(parse_feed(duplicate.encode(), NOW)) == 1


def test_news_rejects_untrusted_urls_and_xml_entities() -> None:
    raw = (
        "<rss><channel>"
        + item().replace("www.highereddive.com", "attacker.example")
        + "</channel></rss>"
    ).encode()
    assert parse_feed(raw, NOW) == []
    with pytest.raises(ValueError):
        parse_feed(b'<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss/>', NOW)
    raw = (
        "<rss><channel>"
        + item().replace("imgproxy.divecdn.com", "attacker.example")
        + "</channel></rss>"
    ).encode()
    assert parse_feed(raw, NOW)[0]["imageUrl"] is None


def snapshot() -> dict[str, Any]:
    return {
        "dataOrigin": "demo",
        "displayedAsOf": "2025-05-20T11:30:00Z",
        "displayedPulse": {
            "metric": "Applications",
            "value": "14,782",
            "target": "20,000",
            "period": "Fall 2025",
            "comparison": "vs yesterday: +458 (+3.2%)",
            "forecast": "16,040 by June 30",
            "definition": "Applications in the selected cohort.",
            "topics": ["admissions"],
        },
    }


def test_pulse_response_matches_displayed_snapshot_without_claiming_live_evidence() -> None:
    answer = describe_displayed_pulse("Explain this Pulse card", snapshot())
    assert answer is not None
    for expected in ("14,782", "20,000", "+458 (+3.2%)", "16,040", "Fall 2025", "admissions"):
        assert expected in answer
    assert "not current institutional records" in answer
    assert "does not establish a cause" in answer


@pytest.mark.parametrize(
    "question",
    [
        "Show live applications",
        "Who is this student?",
        "Create a task",
        "Explain tuition",
        "Send this message",
    ],
)
def test_other_edward_questions_use_existing_pipeline(question: str) -> None:
    assert describe_displayed_pulse(question, snapshot()) is None
