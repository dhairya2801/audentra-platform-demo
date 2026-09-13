"""Student-safe decision evidence shared by portal and university reads.

Internal staff notes are deliberately never selected or projected here.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def _timestamp(value: Any) -> str:
    stamp = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return stamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def public_review_decision(row: Mapping[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": str(row["id"]),
        "decision": ("accepted" if str(row["decision"]) == "accepted" else "changes_requested"),
        "decidedAt": _timestamp(row["decided_at"]),
        "reasonCode": str(row["reason_code"]) if row.get("reason_code") is not None else None,
        "reasonLabel": (str(row["reason_label"]) if row.get("reason_label") is not None else None),
        "note": (str(row["student_message"]) if row.get("student_message") is not None else None),
        "reviewerName": str(row["reviewer_display_name"]),
        "source": str(row["source"]),
    }
    if row.get("source") == "legacy_backfill":
        item["synthetic"] = True
    if row.get("source") == "ai_auto":
        item["automated"] = True
    return item
