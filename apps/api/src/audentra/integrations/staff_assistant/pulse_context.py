"""Explain an explicitly labeled screen snapshot, never promote it to a live fact."""

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


def describe_displayed_pulse(message: str, context: Mapping[str, Any] | None) -> str | None:
    if not context or context.get("dataOrigin") != "demo":
        return None
    metric = context.get("displayedPulse")
    if not isinstance(metric, Mapping):
        return None
    if re.search(
        r"\b(actual|live|database|today|students?|person|who|send|create|invite)\b", message, re.I
    ):
        return None
    if not re.search(
        r"\b(this|these|shown|displayed|pulse|comparison|goal|target|pace|figures?|numbers?)\b",
        message,
        re.I,
    ):
        return None
    try:
        as_of = datetime.fromisoformat(str(context.get("displayedAsOf")).replace("Z", "+00:00"))
        displayed_at = as_of.astimezone(ZoneInfo("America/New_York")).strftime(
            "%b %d, %Y, %I:%M %p ET"
        )
    except ValueError:
        displayed_at = "the pinned demo day"
    lines = [
        f"The demo Pulse card you opened shows {metric['metric']}: {metric['value']}.",
        f"Period: {metric['period']}. Displayed as of {displayed_at}. {metric['definition']}",
    ]
    if metric.get("target"):
        lines.append(f"Displayed goal: {metric['target']}.")
    if metric.get("comparison"):
        lines.append(f"Selected comparison: {metric['comparison']}.")
    if metric.get("forecast"):
        lines.append(
            f"Illustrative current-pace projection: {metric['forecast']}. "
            "This is not a guaranteed outcome."
        )
    lines.append("Selected topics: " + ", ".join(metric.get("topics", [])) + ".")
    lines.append(
        "These are the provisional figures on your screen, not current institutional records. "
        "This snapshot does not establish a cause. Ask explicitly for live data to read "
        "authorized records separately."
    )
    return "\n\n".join(lines)
