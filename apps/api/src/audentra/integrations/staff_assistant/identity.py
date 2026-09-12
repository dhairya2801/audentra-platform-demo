"""Who is asking: the signed-in staff member as a first-class fact.

Every staff turn is asked *by someone* with a role, a component, a manager,
possibly a team, a caseload and a queue. Until now the assistant knew only an
opaque actor id, so "my work", "my team" and "am I over my cap" had nothing
to bind to. The identity is loaded once per turn from the same bounded
profile read the Staff Portal's *My desk* uses, and is offered to the
classifier (self-reference), the tools (``staffId`` binding for "me"), the
composer (an honest "you are signed in as …") and the trace.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]

# Roles whose holders manage people; a "my team" question from them is a
# team question, from anyone else it is a department question.
MANAGER_ROLE_CODES = frozenset({"vp", "director", "associate_director", "manager"})
ADVISER_ROLE_CODES = frozenset({"academic_adviser", "transfer_adviser"})


@dataclass(frozen=True)
class StaffIdentity:
    id: str
    name: str
    title: str | None
    role_code: str
    component: str
    employment_status: str
    manager_name: str | None
    direct_reports: int
    primary_advisees: int
    caseload_cap: int | None
    open_items: int
    overdue_items: int
    profile: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_manager(self) -> bool:
        return self.direct_reports > 0 or self.role_code in MANAGER_ROLE_CODES

    @property
    def is_adviser(self) -> bool:
        return self.role_code in ADVISER_ROLE_CODES or self.primary_advisees > 0

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else ""

    def describe(self) -> str:
        """One line for prompts and evidence: who is signed in."""

        parts = [f"Signed-in staff member: {self.name}"]
        if self.title:
            parts.append(f"({self.title})")
        parts.append(f"in {self.component}")
        if self.manager_name:
            parts.append(f"reporting to {self.manager_name}")
        if self.direct_reports:
            parts.append(f"with {self.direct_reports} direct report(s)")
        return (
            " ".join(parts)
            + f". Current assigned workload: {self.open_items} open, "
            + f"{self.overdue_items} overdue (all due dates; not just items due today)."
        )

    def as_trace(self) -> JsonDict:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "roleCode": self.role_code,
            "component": self.component,
            "employmentStatus": self.employment_status,
            "isManager": self.is_manager,
            "directReports": self.direct_reports,
            "primaryAdvisees": self.primary_advisees,
        }


def identity_from_profile(profile: Mapping[str, Any] | None) -> StaffIdentity | None:
    if not profile or not profile.get("id"):
        return None
    caseload: Mapping[str, Any] = (
        profile["caseload"] if isinstance(profile.get("caseload"), Mapping) else {}
    )
    work: Mapping[str, Any] = profile["work"] if isinstance(profile.get("work"), Mapping) else {}
    manager: Mapping[str, Any] = (
        profile["manager"] if isinstance(profile.get("manager"), Mapping) else {}
    )
    cap = caseload.get("cap")
    return StaffIdentity(
        id=str(profile["id"]),
        name=str(profile.get("name") or ""),
        title=str(profile["title"]) if profile.get("title") else None,
        role_code=str(profile.get("roleCode") or "staff"),
        component=str(profile.get("component") or ""),
        employment_status=str(profile.get("employmentStatus") or "active"),
        manager_name=str(manager["name"]) if manager.get("name") else None,
        direct_reports=int(profile.get("directReports") or 0),
        primary_advisees=int(caseload.get("primaryAdvisees") or 0),
        caseload_cap=int(cap) if cap else None,
        open_items=int(work.get("open") or 0),
        overdue_items=int(work.get("overdue") or 0),
        profile=dict(profile),
    )
