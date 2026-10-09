import importlib
import uuid
from collections.abc import AsyncGenerator, Callable, Generator
from typing import Any, Literal

import pytest
import pytest_asyncio
from cui_config import settings
from cui_db import Base
from fastapi import FastAPI
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@pytest.fixture(scope="session")
def db_schema() -> Generator[None]:
    """Own schema setup and teardown for both DB suites; refuse databases not named *test."""
    url = make_url(settings.database_url)
    if not url.database or not url.database.endswith("test"):
        raise RuntimeError(f"refuse to run tests against {url.database}")
    engine = create_engine(url.set(drivername="postgresql+psycopg"), poolclass=pool.NullPool)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def db_engine(db_schema: None) -> AsyncGenerator[AsyncEngine]:
    """Provide the async engine after schema setup and dispose it before schema teardown."""
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Yield an AsyncSession inside an open transaction on the shared async engine."""

    session_maker = async_sessionmaker(bind=db_engine, expire_on_commit=True)
    async with session_maker() as session:
        async with session.begin():
            yield session


@pytest.fixture
def make_event() -> Callable[..., dict[str, Any]]:
    """Return a factory for valid ingestion log event dicts; keyword arguments override fields."""

    def factory(**overrides: Any) -> dict[str, Any]:
        return {
            "event_id": str(uuid.uuid4()),
            "service_name": "music-api",
            "environment": "dev",
            "level": "INFO",
            "message": "hello",
            "occurred_at": "2026-10-03T10:00:00Z",
        } | overrides

    return factory


@pytest.fixture
def build_app(monkeypatch: pytest.MonkeyPatch) -> Generator[Callable[[Literal["sync", "queue"]], FastAPI]]:
    """Return a function that rebuilds ingestion_api.main.app for the given ingest mode.
    The mode is chosen when main is imported, so the module is reloaded; it is reloaded again with the real settings
    afterwards."""
    from ingestion_api import main

    def build(mode: Literal["sync", "queue"]) -> FastAPI:
        monkeypatch.setattr(settings, "ingest_mode", mode)
        monkeypatch.setattr(settings, "allow_ingest_sync", mode == "sync")
        return importlib.reload(main).app

    yield build
    monkeypatch.undo()
    importlib.reload(main)


@pytest.fixture
def sync_app(build_app: Callable[[Literal["sync", "queue"]], FastAPI]) -> FastAPI:
    """The ingestion app built in sync mode (writes to Postgres, answers 201)."""
    return build_app("sync")


@pytest.fixture
def queue_app(build_app: Callable[[Literal["sync", "queue"]], FastAPI]) -> FastAPI:
    """The ingestion app built in queue mode (publishes to the broker, answers 202)."""
    return build_app("queue")
