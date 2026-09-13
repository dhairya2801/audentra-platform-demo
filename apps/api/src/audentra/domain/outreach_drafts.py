"""Staff-only saved composition; a draft is not delivered institutional evidence."""

from collections.abc import Mapping
from typing import Any


def project_outreach_draft(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "workItemId": str(row["work_item_id"]),
        "studentId": str(row["student_id"]),
        "channel": "portal",
        "subject": row["subject"],
        "body": str(row["body"]),
        "status": str(row["status"]),
        "version": int(row["version"]),
        "createdByStaffId": str(row["created_by"]),
        "updatedByStaffId": str(row["updated_by"]),
        "communicationId": str(row["communication_id"]) if row["communication_id"] else None,
        "updatedAt": row["updated_at"].isoformat(),
        "semantics": (
            "Staff-authored composition. Only status=sent with a linked communication "
            "records portal delivery; case completion is separate."
        ),
    }
