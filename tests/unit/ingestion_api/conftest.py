"""Shared fixtures for ingestion API unit tests. Nothing here touches a real database or broker."""

from collections.abc import Generator
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ingestion_api import queue_router
from ingestion_api.db import get_session
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> MagicMock:
    """A mock Session with the real Session's method signatures."""
    return create_autospec(Session, instance=True)


@pytest.fixture
def client(sync_app: FastAPI, session: MagicMock) -> Generator[TestClient]:
    """A sync-mode TestClient whose requests get the mock session; the lifespan (needs the database) is not run."""
    sync_app.dependency_overrides[get_session] = lambda: session
    yield TestClient(sync_app)
    sync_app.dependency_overrides.clear()


@pytest.fixture
def queue_client(queue_app: FastAPI) -> TestClient:
    """A queue-mode TestClient. Combine with `publish` / `broker_check` so no real broker is contacted."""
    return TestClient(queue_app)


@pytest.fixture
def publish() -> Generator[MagicMock]:
    """Mock send_batch_msg where queue_router looks it up; it succeeds unless a test sets side_effect."""
    with patch.object(queue_router, "send_batch_msg", autospec=True) as mock:
        yield mock


@pytest.fixture
def broker_check() -> Generator[MagicMock]:
    """Mock check_broker_connection where queue_router looks it up; it succeeds unless a test sets side_effect."""
    with patch.object(queue_router, "check_broker_connection", autospec=True) as mock:
        yield mock
