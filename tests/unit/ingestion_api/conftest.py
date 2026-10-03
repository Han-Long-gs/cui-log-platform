"""Shared fixtures for ingestion API unit tests. Nothing here touches a real database."""

import uuid
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest
from fastapi.testclient import TestClient
from ingestion_api.db import get_session
from ingestion_api.main import app
from sqlalchemy.orm import Session


@pytest.fixture
def make_event() -> Callable[..., dict[str, Any]]:
    """Return a factory for valid log event dicts; keyword arguments override fields."""

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
def session() -> MagicMock:
    """A mock Session with the real Session's method signatures."""
    return create_autospec(Session, instance=True)


@pytest.fixture
def client(session: MagicMock) -> Generator[TestClient]:
    """A TestClient whose requests get the mock session; the app lifespan (which needs the database) is not run."""
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()
