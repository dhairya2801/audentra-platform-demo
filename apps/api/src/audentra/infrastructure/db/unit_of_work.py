"""Small explicit transaction boundary for SQLAlchemy Core repositories."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction


class AsyncUnitOfWork:
    """Own exactly one connection and transaction for an application command."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._transaction: AsyncTransaction | None = None
        self.connection: AsyncConnection | None = None
        self._completed = False

    async def __aenter__(self) -> Self:
        if self.connection is not None:
            raise RuntimeError("unit of work cannot be entered twice")
        self.connection = await self._engine.connect()
        self._transaction = await self.connection.begin()
        return self

    async def commit(self) -> None:
        transaction = self._require_transaction()
        await transaction.commit()
        self._completed = True

    async def rollback(self) -> None:
        transaction = self._require_transaction()
        if transaction.is_active:
            await transaction.rollback()
        self._completed = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if not self._completed:
                await self.rollback()
        finally:
            if self.connection is not None:
                await self.connection.close()
            self.connection = None
            self._transaction = None

    def _require_transaction(self) -> AsyncTransaction:
        if self._transaction is None or self.connection is None:
            raise RuntimeError("unit of work is not active")
        return self._transaction


class UnitOfWorkFactory:
    """Injectable factory that keeps the engine out of application modules."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    def __call__(self) -> AsyncUnitOfWork:
        return AsyncUnitOfWork(self._engine)
