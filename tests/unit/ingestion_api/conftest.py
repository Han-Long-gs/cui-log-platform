"""Shared fixtures for ingestion API unit tests. Nothing here touches a real database."""

from collections.abc import Generator
from unittest.mock import MagicMock, create_autospec

import pytest
from fastapi.testclient import TestClient
from ingestion_api.db import get_session
from ingestion_api.main import app
from sqlalchemy.orm import Session


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
