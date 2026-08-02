"""Async SQLAlchemy engine construction with explicit pool bounds."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


@dataclass(frozen=True, slots=True)
class DatabaseEngineOptions:
    """Connection-pool settings applied per API or worker process."""

    pool_size: int = 10
    max_overflow: int = 0
    pool_timeout_seconds: float = 5.0
    pool_recycle_seconds: int = 1_800
    statement_timeout_ms: int = 15_000
    application_name: str = "audentra-api"
    echo: bool = False

    def __post_init__(self) -> None:
        if self.pool_size < 1:
            raise ValueError("pool_size must be positive")
        if self.max_overflow < 0:
            raise ValueError("max_overflow cannot be negative")
        if self.pool_timeout_seconds <= 0:
            raise ValueError("pool_timeout_seconds must be positive")
        if self.statement_timeout_ms < 1:
            raise ValueError("statement_timeout_ms must be positive")
        if not self.application_name.strip():
            raise ValueError("application_name is required")


def normalize_database_url(database_url: str) -> str:
    """Normalize a conventional PostgreSQL URL for SQLAlchemy's asyncpg dialect."""

    value = database_url.strip()
    if not value:
        raise ValueError("DATABASE_URL is required")
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if value.startswith("postgresql+asyncpg://"):
        return value
    raise ValueError("DATABASE_URL must use PostgreSQL")


def create_database_engine(
    database_url: str,
    options: DatabaseEngineOptions | None = None,
) -> AsyncEngine:
    """Create an async engine without multiplying connections implicitly."""

    config = options or DatabaseEngineOptions()
    return create_async_engine(
        normalize_database_url(database_url),
        echo=config.echo,
        pool_pre_ping=True,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_timeout=config.pool_timeout_seconds,
        pool_recycle=config.pool_recycle_seconds,
        connect_args={
            "server_settings": {
                "application_name": config.application_name,
                "statement_timeout": str(config.statement_timeout_ms),
            }
        },
    )
