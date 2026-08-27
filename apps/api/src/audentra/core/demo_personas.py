"""Which synthetic people a deployment lets a browser open the demo portals as.

Development sign-in (`/v1/auth/demo/sign-in-as`, `/v1/auth/demo/staff/…`)
opens the portal as *any* student or staff member of a demo-enabled tenant.
That is what a developer wants against a three-thousand-student fixture and
exactly what a release candidate must not offer: a public demo should show
one representative student and a handful of staff chairs, and a caller who
edits the request body must not be able to open anyone else.

This module is that policy, as configuration rather than as a second
authentication system:

* Empty allowlists (the development default) change nothing — every demo
  route keeps its broad behaviour.
* Non-empty allowlists name the institution references (`SYN-000061`,
  `SYN-ADV-001`, …) that may be opened. The adapter that resolves demo
  identities enforces them on every path that mints or resolves a demo
  identity — sign-in by reference, the tenant's default demo student, the
  staff directory, the per-request student cookie and the resolution of a
  demo staff session — and the HTTP layer checks the same object again.
* Deployed demo configurations (`AUDENTRA_ENV=preview` with `AUTH_MODE=demo`)
  must set both lists; the settings loader refuses to start otherwise.

References compare case-insensitively, so a tenant's `SYN-000061` and a typed
`syn-000061` are the same person; a UUID is never an allowlist entry because
the entry names a person of the tenant, not a row.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

STUDENT_ALLOWLIST_VARIABLE: Final = "DEMO_STUDENT_ALLOWLIST"
STAFF_ALLOWLIST_VARIABLE: Final = "DEMO_STAFF_ALLOWLIST"

_MAXIMUM_ENTRIES: Final = 32
_MAXIMUM_REFERENCE_LENGTH: Final = 64


def _normalise(reference: str | None) -> str | None:
    if reference is None:
        return None
    candidate = reference.strip().upper()
    return candidate or None


def _parse(raw: str | None, *, variable: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    entries: list[str] = []
    for part in raw.split(","):
        reference = _normalise(part)
        if reference is None:
            continue
        if len(reference) > _MAXIMUM_REFERENCE_LENGTH:
            raise ValueError(f"{variable} entries are institution references of at most 64 chars")
        if reference not in entries:
            entries.append(reference)
    if len(entries) > _MAXIMUM_ENTRIES:
        raise ValueError(f"{variable} names a demo, not a directory: at most 32 people")
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class DemoPersonaAllowlist:
    """The demo people a deployment exposes; empty means every demo identity."""

    students: tuple[str, ...] = ()
    staff: tuple[str, ...] = ()

    @classmethod
    def open(cls) -> DemoPersonaAllowlist:
        return cls()

    @classmethod
    def of(cls, *, students: Iterable[str], staff: Iterable[str]) -> DemoPersonaAllowlist:
        return cls(
            students=_parse(",".join(students), variable=STUDENT_ALLOWLIST_VARIABLE),
            staff=_parse(",".join(staff), variable=STAFF_ALLOWLIST_VARIABLE),
        )

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> DemoPersonaAllowlist:
        students = _parse(
            values.get(STUDENT_ALLOWLIST_VARIABLE), variable=STUDENT_ALLOWLIST_VARIABLE
        )
        staff = _parse(values.get(STAFF_ALLOWLIST_VARIABLE), variable=STAFF_ALLOWLIST_VARIABLE)
        allowlist = cls(students=students, staff=staff)
        if bool(allowlist.students) != bool(allowlist.staff):
            raise ValueError(
                f"{STUDENT_ALLOWLIST_VARIABLE} and {STAFF_ALLOWLIST_VARIABLE} are set together: "
                "a restricted demo names its student and its staff, an open one names neither"
            )
        return allowlist

    @property
    def restricted(self) -> bool:
        return bool(self.students or self.staff)

    @property
    def default_student_ref(self) -> str | None:
        """The student "sign in as the demo student" opens under a restriction."""

        return self.students[0] if self.students else None

    def allows_student(self, external_ref: str | None) -> bool:
        if not self.restricted:
            return True
        reference = _normalise(external_ref)
        return reference is not None and reference in self.students

    def allows_staff(self, external_ref: str | None) -> bool:
        if not self.restricted:
            return True
        reference = _normalise(external_ref)
        return reference is not None and reference in self.staff
