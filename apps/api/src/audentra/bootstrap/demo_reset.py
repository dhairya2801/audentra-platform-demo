"""An opt-in reset for one disposable, single-process demo API.

The read-only template lives on the same PostgreSQL server. Local development
never opts in by default. No per-user databases or automatic login resets.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import asyncpg  # type: ignore[import-untyped]
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.interfaces.http.dependencies import get_auth_context

logger = logging.getLogger(__name__)
RESET_PATH = "/v1/staff/demo/reset"


def reset_database_names(settings: RuntimeSettings) -> tuple[str, str]:
    name = urlsplit(settings.database_url).path.removeprefix("/")
    if (
        settings.environment not in {"development", "preview", "test"}
        or settings.auth_mode != "demo"
        or not settings.browser_auth_required
        or not settings.demo_personas.restricted
        or not re.fullmatch(r"audentra_demo_[a-z0-9_]{1,28}", name)
        or settings.demo_reset_template != name + "_baseline"
    ):
        raise ValueError(
            "Demo reset requires a restricted, authenticated demo, a dedicated audentra_demo_* "
            "database, and DEMO_RESET_TEMPLATE=<database>_baseline; never a production database"
        )
    return name, settings.demo_reset_template


async def restore_demo_database(
    settings: RuntimeSettings,
    close_runtime: Callable[[], Awaitable[None]],
    restart_runtime: Callable[[], Awaitable[None]],
) -> None:
    name, template = reset_database_names(settings)
    # Names are configuration, validated above, never request input.
    replacement, previous = name + "_next", name + "_previous"
    parsed = urlsplit(settings.database_url.replace("postgresql+asyncpg://", "postgresql://"))
    control_url = urlunsplit(parsed._replace(path="/postgres"))
    db = await asyncpg.connect(control_url, command_timeout=90)
    swapped = False
    closed = False
    created = False
    try:
        if not await db.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", "demo-reset:" + name):
            raise ApiError(409, "DEMO_RESET_BUSY", "A demo reset is already running")
        baseline = await db.fetchrow(
            "SELECT datallowconn, "
            "datdba=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS owned "
            "FROM pg_database WHERE datname=$1",
            template,
        )
        if baseline is None or baseline["datallowconn"] or not baseline["owned"]:
            raise ApiError(
                409, "DEMO_BASELINE_UNAVAILABLE", "Prepare and freeze the demo baseline first"
            )
        if await db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
            "WHERE datname=$1 AND application_name<>$2)",
            name,
            settings.database.application_name,
        ):
            raise ApiError(
                409,
                "DEMO_DATABASE_BUSY",
                "Stop workers and other database clients before resetting this demo",
            )
        if await db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=ANY($1))",
            [replacement, previous],
        ):
            raise ApiError(
                409, "DEMO_RESET_RECOVERY_REQUIRED", "A previous reset needs operator inspection"
            )
        await db.execute(
            f'CREATE DATABASE "{replacement}" TEMPLATE "{template}" ALLOW_CONNECTIONS false'
        )
        created = True
        closed = True
        await close_runtime()
        await db.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        await db.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1", name
        )
        async with db.transaction():
            await db.execute(f'ALTER DATABASE "{name}" RENAME TO "{previous}"')
            await db.execute(f'ALTER DATABASE "{replacement}" RENAME TO "{name}"')
            await db.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        swapped = True
        await restart_runtime()
    except Exception:
        if swapped:
            await db.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
            await db.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1", name
            )
            async with db.transaction():
                await db.execute(f'ALTER DATABASE "{name}" RENAME TO "{replacement}"')
                await db.execute(f'ALTER DATABASE "{previous}" RENAME TO "{name}"')
                await db.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        elif closed:
            await db.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS true')
        if closed:
            await restart_runtime()
        if created:
            await db.execute(f'DROP DATABASE "{replacement}"')
        raise
    else:
        # The new API is ready before removing the previous disposable state.
        try:
            await db.execute(f'DROP DATABASE "{previous}"')
        except Exception as error:
            logger.error(
                "Demo reset succeeded; previous database cleanup failed (%s)", type(error).__name__
            )
    finally:
        await db.close()


def install_demo_reset(
    app: FastAPI, settings: RuntimeSettings, restart_runtime: Callable[[], Awaitable[None]]
) -> None:
    # The visible demo control no longer restores a database or revokes sessions.
    enabled = (
        settings.environment in {"development", "preview", "test"}
        and settings.auth_mode == "demo"
        and settings.demo_personas.restricted
    )

    def authorize(auth: AuthContext) -> None:
        if not enabled:
            raise ApiError(404, "NOT_FOUND", "Not found")
        if (
            auth.actor_type != "staff"
            or auth.authentication_method != "demo"
            or auth.tenant_id != settings.demo_tenant_id
        ):
            raise ApiError(403, "DEMO_RESET_FORBIDDEN", "Sign in as a demo staff member to reset")

    @app.get(RESET_PATH)
    async def status(auth: AuthContext = Depends(get_auth_context)) -> dict[str, bool]:  # noqa: B008
        return {
            "enabled": bool(
                enabled
                and auth.actor_type == "staff"
                and auth.authentication_method == "demo"
                and auth.tenant_id == settings.demo_tenant_id
            )
        }

    @app.post(RESET_PATH)
    async def reset(
        request: Request,
        body: dict[str, str],
        auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    ) -> JSONResponse:
        authorize(auth)
        if request.headers.get("origin") not in settings.web_origins:
            raise ApiError(403, "DEMO_RESET_ORIGIN", "Reset from the demo portal")
        if body != {"confirmation": "RESET DEMO"}:
            raise ApiError(400, "DEMO_RESET_CONFIRMATION", "Confirm the demo reset")
        from audentra.infrastructure.postgres.demo_document_reset import reset_documents
        from audentra.infrastructure.postgres.portal_repository import PostgresPortalRepository

        result = await reset_documents(
            PostgresPortalRepository(app.state.runtime_resources.engine),
            auth,
            settings.demo_personas.default_student_ref or "",
            request.headers.get("x-request-id", "demo-document-reset"),
        )
        return JSONResponse(result)
