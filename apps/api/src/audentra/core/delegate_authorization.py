"""Default-deny page-scope policy for parent and guardian delegates."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .auth import AuthContext, PortalScope
from .errors import ApiError


@dataclass(frozen=True, slots=True)
class DelegateRoutePolicy:
    pattern: re.Pattern[str]
    scopes: frozenset[PortalScope]
    methods: frozenset[str] | None = None


def _policy(
    pattern: str,
    *scopes: PortalScope,
    methods: tuple[str, ...] | None = None,
) -> DelegateRoutePolicy:
    return DelegateRoutePolicy(
        re.compile(pattern),
        frozenset(scopes),
        frozenset(methods) if methods is not None else None,
    )


# Every authenticated non-staff route is listed deliberately. A newly-added
# route is unavailable to delegates until its owning product page is reviewed.
_ROUTE_POLICIES = (
    _policy(r"^/v1/student/bootstrap$", *()),
    _policy(
        r"^/v1/student/dashboard$",
        "dashboard",
        "enrollment",
        "payments",
    ),
    _policy(r"^/v1/student/academics$", "classrooms", "enrollment", "dashboard"),
    _policy(r"^/v1/catalog/courses$", "classrooms", "enrollment"),
    _policy(r"^/v1/student/financials$", "financials", "dashboard", methods=("GET",)),
    _policy(r"^/v1/student/financials/payment-plan$", "financials"),
    _policy(
        r"^/v1/student/campus-life$",
        "campus_life",
        "dashboard",
        methods=("GET",),
    ),
    _policy(
        r"^/v1/student/campus-life/events/[^/]+/register$",
        "campus_life",
    ),
    _policy(r"^/v1/activity-events/batch$", *()),
    # Onboarding data is rendered inside My Enrollment for delegates; it is
    # never a separately grantable or navigable parent page.
    _policy(r"^/v1/student/onboarding$", "enrollment", methods=("GET",)),
    _policy(
        r"^/v1/student/housing-plan$",
        "enrollment",
        methods=("GET",),
    ),
    _policy(r"^/v1/student/housing-plan$", "enrollment", methods=("PATCH",)),
    _policy(r"^/v1/student/experience-updates(?:/.*)?$", "dashboard"),
    _policy(r"^/v1/student/requirements(?:/[^/]+)?$", "enrollment"),
    _policy(
        r"^/v1/student/requirements/[^/]+/responses$",
        methods=("POST",),
    ),
    _policy(r"^/v1/student/messages(?:/[^/]+/read)?$", "messages"),
    _policy(r"^/v1/student/documents/upload$", "documents", "enrollment"),
    _policy(
        r"^/v1/student/documents/[^/]+/content$",
        "documents",
        "enrollment",
        methods=("GET",),
    ),
    _policy(
        r"^/v1/student/documents/[^/]+/(?:confirm-extraction|retry-extraction)$",
        "documents",
        "enrollment",
        methods=("POST",),
    ),
    _policy(r"^/v1/student/documents/[^/]+/profile-photo$", "documents", "profile"),
    _policy(
        r"^/v1/student/documents$",
        "documents",
        "enrollment",
        methods=("GET",),
    ),
    _policy(r"^/v1/student/documents(?:/.*)?$", "documents"),
    _policy(r"^/v1/student/assistant(?:/.*)?$", "edward"),
    _policy(r"^/v1/student/appointments$", "appointments"),
    _policy(
        r"^/v1/student/requirements/[^/]+/appointments$",
        "enrollment",
        methods=("GET",),
    ),
    _policy(
        r"^/v1/student/requirements/[^/]+/appointments$",
        "enrollment",
        methods=("POST",),
    ),
    _policy(
        r"^/v1/student/requirements/[^/]+/profile$",
        "enrollment",
        methods=("PATCH",),
    ),
    _policy(r"^/v1/student/appointments(?:/.*)?$", "appointments"),
    _policy(
        r"^/v1/student/payments/deposit$",
        "payments",
        "enrollment",
        methods=("POST",),
    ),
    _policy(
        r"^/v1/student/payments$",
        "payments",
        "enrollment",
        methods=("GET",),
    ),
    _policy(r"^/v1/student/payments(?:/.*)?$", "payments"),
    _policy(
        r"^/v1/student/profile$",
        "profile",
        "enrollment",
        methods=("GET",),
    ),
    _policy(r"^/v1/student/profile$", "profile", methods=("PATCH",)),
    _policy(r"^/v1/student/help(?:/.*)?$", "help"),
    _policy(r"^/v1/demo/help-requests$", "help"),
)


def authorize_delegate_route(auth: AuthContext, method: str, path: str) -> None:
    """Authorize one delegate request against an explicit route-to-page map."""

    if not auth.is_delegate:
        return
    if "/ferpa" in path or path.startswith("/v1/student/internal/"):
        raise ApiError(
            403,
            "FERPA_STUDENT_CONTROL_REQUIRED",
            "Only the student may sign FERPA or manage parent and guardian access",
        )
    if path.startswith("/v1/student/assistant/voice-"):
        raise ApiError(
            403,
            "DELEGATE_ROUTE_NOT_ALLOWED",
            "Voice sessions are not available through parent or guardian access",
        )
    normalized_method = method.upper()
    for policy in _ROUTE_POLICIES:
        if not policy.pattern.fullmatch(path):
            continue
        if policy.methods is not None and normalized_method not in policy.methods:
            continue
        if not policy.scopes or policy.scopes.intersection(auth.delegate_scopes):
            return
        raise ApiError(
            403,
            "DELEGATE_SCOPE_REQUIRED",
            "This parent or guardian link does not grant access to that portal page",
        )
    raise ApiError(
        403,
        "DELEGATE_ROUTE_NOT_ALLOWED",
        "This operation is not available through parent or guardian access",
    )


def require_delegate_scope(auth: AuthContext, scope: PortalScope) -> None:
    if auth.is_delegate and scope not in auth.delegate_scopes:
        raise ApiError(
            403,
            "DELEGATE_SCOPE_REQUIRED",
            "This parent or guardian link does not grant access to that portal page",
        )


def require_student_ferpa_control(auth: AuthContext) -> None:
    if auth.actor_type != "student":
        raise ApiError(
            403,
            "FERPA_STUDENT_CONTROL_REQUIRED",
            "Only the student may sign FERPA or manage parent and guardian access",
        )
