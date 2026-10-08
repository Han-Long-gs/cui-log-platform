"""Fixtures for ingestion API integration tests, which run against the real test database through the app's engine."""

from collections.abc import Callable, Generator

import pytest
from cui_db import LogEvent
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ingestion_api import db
from sqlalchemy import select, text
from sqlalchemy.orm import Session


@pytest.fixture(scope="session", autouse=True)
def dispose_app_engine(db_schema: None) -> Generator[None]:
    """Use the shared schema, and close the app engine's pooled connections before the schema is dropped."""
    yield
    db.engine.dispose()


def truncate() -> None:
    """Delete every tenant and log event, along with rows that reference a tenant."""
    with db.engine.begin() as conn:
        conn.execute(text("TRUNCATE tenants, log_events RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def empty_tables() -> Generator[None]:
    """Empty the tables before and after each test, so counts start from zero and nothing leaks into other tests."""
    truncate()
    yield
    truncate()


@pytest.fixture
def client(sync_app: FastAPI) -> Generator[TestClient]:
    """A sync-mode TestClient that runs the lifespan (creates the dev tenant on enter, disposes the engine on exit).
    Server errors come back as 500 responses instead of being raised in the test."""
    with TestClient(sync_app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def stored_events() -> Callable[[], list[LogEvent]]:
    """Return a function that reads every row of log_events, ordered by id."""

    def fetch() -> list[LogEvent]:
        with Session(db.engine) as session:
            return list(session.scalars(select(LogEvent).order_by(LogEvent.id)))

    return fetch
