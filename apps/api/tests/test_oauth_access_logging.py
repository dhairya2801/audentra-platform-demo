from __future__ import annotations

import logging

from audentra.interfaces.http.access_logging import (
    OAuthCallbackAccessLogFilter,
    install_access_log_redaction,
    redact_oauth_callback_path,
    uvicorn_log_config,
)


def test_oauth_callback_access_log_filter_removes_code_state_and_error_values() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %s',
        (
            "127.0.0.1:12345",
            "GET",
            "/v1/auth/staff/sso/google/callback?code=secret-code&state=secret-state&error=x",
            "1.1",
            303,
        ),
        None,
    )

    assert OAuthCallbackAccessLogFilter().filter(record)
    assert isinstance(record.args, tuple)
    assert record.args[2] == "/v1/auth/staff/sso/google/callback"
    assert "secret-code" not in record.getMessage()
    assert "secret-state" not in record.getMessage()


def test_only_oauth_callback_paths_are_redacted() -> None:
    assert (
        redact_oauth_callback_path("/v1/staff/mail/oauth/microsoft/callback?code=mail-code")
        == "/v1/staff/mail/oauth/microsoft/callback"
    )
    assert redact_oauth_callback_path("/v1/staff/mailboxes?cursor=public") == (
        "/v1/staff/mailboxes?cursor=public"
    )


def test_production_uvicorn_log_config_installs_the_access_filter() -> None:
    config = uvicorn_log_config()
    assert "audentra_oauth_callback_redaction" in config["handlers"]["access"]["filters"]
    assert config["filters"]["audentra_oauth_callback_redaction"]["()"].endswith(
        "OAuthCallbackAccessLogFilter"
    )


def test_direct_uvicorn_launch_uses_the_named_access_logger_filter() -> None:
    logger = logging.getLogger("uvicorn.access")
    original_filters = list(logger.filters)
    try:
        logger.filters.clear()
        install_access_log_redaction()
        install_access_log_redaction()
        assert sum(isinstance(item, OAuthCallbackAccessLogFilter) for item in logger.filters) == 1
    finally:
        logger.filters[:] = original_filters
