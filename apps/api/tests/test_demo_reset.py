from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import httpx
import pytest

from audentra.bootstrap import demo_reset
from audentra.bootstrap.api import create_production_app
from audentra.bootstrap.settings import RuntimeSettings
from audentra.core.auth import AuthContext
from audentra.core.errors import ApiError
from audentra.interfaces.http.dependencies import get_auth_context


def settings_for(name: str = "audentra_demo_test") -> RuntimeSettings:
    return RuntimeSettings.from_environment(
        {
            "AUDENTRA_ENV": "test",
            "DATABASE_URL": f"postgresql://localhost/{name}",
            "DEMO_RESET_TEMPLATE": name + "_baseline",
            "DEMO_STUDENT_ALLOWLIST": "SYN-000061",
            "DEMO_STAFF_ALLOWLIST": "AU-55ff7e408818",
            "BROWSER_AUTH_REQUIRED": "true",
            "WEB_ORIGIN": "http://portal.test",
        }
    )


def test_reset_requires_a_separate_restricted_demo_database() -> None:
    settings = settings_for()
    assert demo_reset.reset_database_names(settings) == (
        "audentra_demo_test",
        "audentra_demo_test_baseline",
    )
    for unsafe in (
        replace(settings, environment="production"),
        replace(settings, browser_auth_required=False),
        replace(settings, auth_mode="oidc"),
        replace(settings, database_url="postgresql://localhost/audentra_university_vnext"),
        replace(settings, demo_reset_template="other_baseline"),
        replace(settings, demo_personas=type(settings.demo_personas).open()),
    ):
        with pytest.raises(ValueError, match="dedicated"):
            demo_reset.reset_database_names(unsafe)


def test_reset_route_authorization_confirmation_and_disabled_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        settings = settings_for()
        app = create_production_app(settings)
        auth = AuthContext(settings.demo_tenant_id, "student", "staff", "staff")
        app.dependency_overrides[get_auth_context] = lambda: auth
        app.state.runtime_resources = SimpleNamespace(close=AsyncMock(), engine=object())
        restore = AsyncMock(return_value={"reset": True, "requirementsReset": 9, "cardsRemoved": 3})
        monkeypatch.setattr(
            "audentra.infrastructure.postgres.demo_document_reset.reset_documents", restore
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://portal.test"
        ) as client:
            assert (await client.get(demo_reset.RESET_PATH)).json() == {"enabled": True}
            body = {"confirmation": "RESET DEMO"}
            headers = {"Origin": "http://portal.test"}
            for denied in (
                replace(auth, actor_type="student"),
                replace(auth, authentication_method="credentials"),
                replace(auth, tenant_id="another-tenant"),
            ):
                auth = denied
                assert (await client.get(demo_reset.RESET_PATH)).json() == {"enabled": False}
                response = await client.post(demo_reset.RESET_PATH, json=body, headers=headers)
                assert response.status_code == 403
            auth = AuthContext(settings.demo_tenant_id, "student", "staff", "staff")
            assert (await client.post(demo_reset.RESET_PATH, json=body)).status_code == 403
            assert (
                await client.post(demo_reset.RESET_PATH, json={}, headers=headers)
            ).status_code == 400
            response = await client.post(demo_reset.RESET_PATH, json=body, headers=headers)
            assert response.status_code == 200
            assert response.json()["requirementsReset"] == 9
            assert "set-cookie" not in response.headers
            restore.assert_awaited_once()
        local = create_production_app(replace(settings, environment="production"))
        local.dependency_overrides[get_auth_context] = lambda: auth
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=local), base_url="http://portal.test"
        ) as client:
            assert (await client.get(demo_reset.RESET_PATH)).json() == {"enabled": False}
            assert (
                await client.post(demo_reset.RESET_PATH, json=body, headers=headers)
            ).status_code == 404

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.parametrize("fail_restart", [False, True])
def test_real_database_reset_and_failed_restart_rollback(fail_restart: bool) -> None:
    admin_url = os.getenv("AUDENTRA_DEMO_RESET_TEST_ADMIN_URL")
    if not admin_url:
        pytest.skip("Set AUDENTRA_DEMO_RESET_TEST_ADMIN_URL to an isolated local PostgreSQL server")
    parsed = urlsplit(admin_url)
    assert parsed.hostname in {"localhost", "127.0.0.1"}
    assert parsed.path == "/postgres"

    async def scenario() -> None:
        name = "audentra_demo_test_" + uuid4().hex[:12]
        baseline = name + "_baseline"
        db = await asyncpg.connect(admin_url)
        url = urlunsplit(parsed._replace(path="/" + name))
        settings = replace(settings_for(name), database_url=url)
        target = None
        try:
            await db.execute(f'CREATE DATABASE "{baseline}"')
            source = await asyncpg.connect(urlunsplit(parsed._replace(path="/" + baseline)))
            await source.execute(
                "CREATE TABLE state (value text); INSERT INTO state VALUES ('start')"
            )
            await source.close()
            await db.execute(f'ALTER DATABASE "{baseline}" ALLOW_CONNECTIONS false')
            await db.execute(f'CREATE DATABASE "{name}" TEMPLATE "{baseline}"')
            target = await asyncpg.connect(
                url, server_settings={"application_name": "audentra-api"}
            )
            await target.execute("UPDATE state SET value='changed'")
            closed = False
            restarts = 0

            async def close() -> None:
                nonlocal closed
                assert target is not None
                await target.close()
                closed = True

            async def restart() -> None:
                nonlocal restarts
                restarts += 1
                assert closed
                if fail_restart and restarts == 1:
                    raise RuntimeError("restart failure")
                check = await asyncpg.connect(url)
                try:
                    assert await check.fetchval("SELECT value FROM state") == (
                        "changed" if fail_restart else "start"
                    )
                finally:
                    await check.close()

            # A second client (worker/operator) prevents destructive reset.
            other = await asyncpg.connect(url)
            try:
                with pytest.raises(ApiError, match="Stop workers"):
                    await demo_reset.restore_demo_database(settings, close, restart)
                assert not closed
            finally:
                await other.close()
            if fail_restart:
                with pytest.raises(RuntimeError, match="restart failure"):
                    await demo_reset.restore_demo_database(settings, close, restart)
            else:
                await demo_reset.restore_demo_database(settings, close, restart)
            assert restarts == (2 if fail_restart else 1)
            assert not await db.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=ANY($1))",
                [name + "_next", name + "_previous"],
            )
        finally:
            if target is not None:
                await target.close()
            for cleanup in (name, baseline, name + "_next", name + "_previous"):
                await db.execute(f'DROP DATABASE IF EXISTS "{cleanup}" WITH (FORCE)')
            await db.close()

    asyncio.run(scenario())
