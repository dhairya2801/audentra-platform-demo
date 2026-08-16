"""The signed cookie that names which demo student a browser is acting as.

Development sign-in already works without this: `vv_demo_session` proves the
caller may use the tenant's demo identity, and the tenant resolves that to its
first student. That is enough for a fourteen-student fixture and useless for a
three-thousand-student one, where the whole point is to open a *chosen*
student's portal.

So a second cookie carries the choice. It is signed rather than trusted:

* The value is `<student uuid>.<hmac>`, where the HMAC covers the tenant *and*
  the student. A cookie minted for one university therefore cannot be replayed
  at another even if both are demo tenants on the same host.
* The key is the deployment's demo session token — the same secret that already
  gates `vv_demo_session`, so the cookie grants nothing that the demo session
  did not already grant.
* Signature verification is a precondition, never the authorization. The
  request handler still looks the student up inside the authenticated tenant,
  so a forged-but-valid signature over a student who does not belong there
  still fails.

None of this makes the mechanism safe for production, and it is not meant to
be: every route that mints or reads the cookie is disabled outside
development, preview, and test.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final
from uuid import UUID

DEMO_STUDENT_COOKIE: Final = "vv_demo_student"

_SEPARATOR: Final = "."
_MAXIMUM_LENGTH: Final = 128


def _signature(secret: str, tenant_id: str, student_id: str) -> str:
    message = f"{tenant_id}:{student_id}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


def issue_demo_student_cookie(secret: str, tenant_id: str, student_id: str) -> str:
    """Mint the cookie value for a student the caller has already resolved."""

    return f"{student_id}{_SEPARATOR}{_signature(secret, tenant_id, student_id)}"


def read_demo_student_cookie(secret: str, tenant_id: str, value: str | None) -> str | None:
    """Return the student id a well-formed, correctly signed cookie names.

    Returns None for anything else — absent, malformed, wrong signature, or
    signed for a different tenant. The caller treats None as "no student was
    chosen", not as an error, so a stale cookie degrades to the tenant's
    default demo identity instead of locking the browser out.
    """

    if not value or len(value) > _MAXIMUM_LENGTH or _SEPARATOR not in value:
        return None
    student_id, _, signature = value.partition(_SEPARATOR)
    try:
        # Canonicalize before signing: "ABC-..." and "abc-..." are the same
        # student, and only one spelling was ever signed.
        student_id = str(UUID(student_id))
    except ValueError:
        return None
    expected = _signature(secret, tenant_id, student_id)
    if not hmac.compare_digest(signature, expected):
        return None
    return student_id
