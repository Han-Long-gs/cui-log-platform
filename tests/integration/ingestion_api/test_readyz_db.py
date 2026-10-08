"""Sync-mode GET /readyz against a real database, and against a port where nothing is listening."""

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ingestion_api import db
from ingestion_api.service import check_db_connection
from sqlalchemy import Engine, create_engine, exc
from sqlalchemy.orm import Session


@pytest.fixture
def unreachable_engine() -> Generator[Engine]:
    """An engine with the test database's settings but port 1, where no server is listening."""
    engine = create_engine(db.engine.url.set(port=1))
    yield engine
    engine.dispose()


@pytest.fixture
def unreachable_client(client: TestClient, sync_app: FastAPI, unreachable_engine: Engine) -> Generator[TestClient]:
    """The lifespan client, with every request's session bound to the unreachable engine."""

    def unreachable_session() -> Generator[Session]:
        with Session(unreachable_engine) as session:
            yield session

    sync_app.dependency_overrides[db.get_session] = unreachable_session
    yield client
    sync_app.dependency_overrides.clear()


def test_readyz_returns_200_with_real_db(client: TestClient) -> None:
    """With the test database up, the readiness check succeeds."""
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == "ingestion-api service to db is healthy"


def test_unreachable_db_raises_real_operational_error(unreachable_engine: Engine) -> None:
    """Connecting to a port with no server makes the driver raise OperationalError, the error /readyz catches."""
    with Session(unreachable_engine) as session, pytest.raises(exc.OperationalError):
        check_db_connection(session)


def test_readyz_returns_503_when_db_unreachable(unreachable_client: TestClient) -> None:
    """When the database cannot be reached, /readyz answers 503 with the generic body."""
    resp = unreachable_client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == "Database is unavailable"
