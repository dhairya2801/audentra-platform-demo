"""An opt-in reset for one disposable, single-process demo API.

The read-only template lives on the same PostgreSQL server. Local development
never opts in by default. No per-user databases or automatic login resets.
"""

from __future__ import annotations

import asyncio
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
    enabled = bool(settings.demo_reset_template)
    if enabled:
        reset_database_names(settings)
    app.state.demo_resetting = False
    app.state.demo_active_writes = 0
    lock = asyncio.Lock()

    if enabled:

        @app.middleware("http")
        async def maintenance(request: Request, call_next):  # type: ignore[no-untyped-def]
            if app.state.demo_resetting and request.url.path != RESET_PATH:
                return JSONResponse(
                    {"error": {"message": "The demo is resetting. Please sign in again shortly."}},
                    status_code=503,
                )
            writing = (
                request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path != RESET_PATH
            )
            if writing:
                app.state.demo_active_writes += 1
            try:
                return await call_next(request)
            finally:
                if writing:
                    app.state.demo_active_writes -= 1

    @app.get(RESET_PATH)
    async def status(auth: AuthContext = Depends(get_auth_context)) -> dict[str, bool]:  # noqa: B008
        return {
            "enabled": enabled
            and auth.actor_type == "staff"
            and auth.authentication_method == "demo"
            and auth.tenant_id == settings.demo_tenant_id
        }

    @app.post(RESET_PATH)
    async def reset(
        request: Request,
        body: dict[str, str],
        auth: AuthContext = Depends(get_auth_context),  # noqa: B008
    ) -> JSONResponse:
        if not enabled:
            raise ApiError(404, "NOT_FOUND", "Not found")
        if (
            auth.actor_type != "staff"
            or auth.authentication_method != "demo"
            or auth.tenant_id != settings.demo_tenant_id
        ):
            raise ApiError(403, "DEMO_RESET_FORBIDDEN", "Sign in as a demo staff member to reset")
        if request.headers.get("origin") not in settings.web_origins:
            raise ApiError(403, "DEMO_RESET_ORIGIN", "Reset from the demo portal")
        if body != {"confirmation": "RESET DEMO"}:
            raise ApiError(400, "DEMO_RESET_CONFIRMATION", "Confirm the demo reset")
        if lock.locked():
            raise ApiError(409, "DEMO_RESET_BUSY", "A demo reset is already running")
        if app.state.demo_active_writes:
            raise ApiError(409, "DEMO_DATABASE_BUSY", "Wait for the current demo action to finish")
        async with lock:
            app.state.demo_resetting = True
            try:
                task = asyncio.create_task(
                    restore_demo_database(
                        settings, app.state.runtime_resources.close, restart_runtime
                    )
                )
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Finish recovery even if the operator closes the browser tab.
                    await task
                    raise
            except ApiError:
                raise
            except Exception as error:
                # Database errors can contain connection details; don't return them.
                logger.error("Demo reset failed (%s)", type(error).__name__)
                raise ApiError(
                    503,
                    "DEMO_RESET_FAILED",
                    "Reset failed. Ask the demo operator to check the service.",
                ) from None
            finally:
                app.state.demo_resetting = False
        response = JSONResponse({"reset": True})
        for cookie in (
            "vv_staff_session",
            "vv_session",
            "vv_demo_session",
            "vv_demo_student",
            "vv_delegate_session",
        ):
            response.delete_cookie(cookie, path="/")
        return response
