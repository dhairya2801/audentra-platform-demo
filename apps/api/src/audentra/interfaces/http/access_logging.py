"""Uvicorn access-log safeguards for OAuth callback credentials."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from uvicorn.config import LOGGING_CONFIG


def redact_oauth_callback_path(value: object) -> object:
    """Remove callback query values before Uvicorn formats an access record.

    OAuth authorization codes and state values arrive in the query string. They
    are intentionally retained in the ASGI request for route handling, but the
    process access log must see only the callback path.
    """

    if not isinstance(value, str):
        return value
    parsed = urlsplit(value)
    path = parsed.path
    callback_prefixes = (
        "/v1/auth/staff/sso/",
        "/v1/staff/mail/oauth/",
    )
    if path.endswith("/callback") and path.startswith(callback_prefixes):
        return path
    return value


class OAuthCallbackAccessLogFilter(logging.Filter):
    """Redact code/state/error query parameters from Uvicorn access records."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Uvicorn's access formatter receives
        # (client_addr, method, full_path, http_version, status_code).
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            values = list(record.args)
            values[2] = redact_oauth_callback_path(values[2])
            record.args = tuple(values)
        return True


def install_access_log_redaction() -> None:
    """Protect direct ``uvicorn module:app`` launches as well as ``audentra-api``.

    The production entry point supplies ``uvicorn_log_config`` below.  Adding the
    filter to the named logger at application construction also covers the
    documented reload command, which delegates logging configuration to Uvicorn's
    CLI rather than to our entry point.
    """

    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, OAuthCallbackAccessLogFilter) for item in access_logger.filters):
        access_logger.addFilter(OAuthCallbackAccessLogFilter())


def uvicorn_log_config() -> dict[str, Any]:
    """Return Uvicorn's normal log configuration with callback redaction."""

    config: dict[str, Any] = deepcopy(LOGGING_CONFIG)
    filter_name = "audentra_oauth_callback_redaction"
    config.setdefault("filters", {})[filter_name] = {
        "()": "audentra.interfaces.http.access_logging.OAuthCallbackAccessLogFilter",
    }
    access_handler = config["handlers"]["access"]
    filters = list(access_handler.get("filters", []))
    if filter_name not in filters:
        filters.append(filter_name)
    access_handler["filters"] = filters
    return config
