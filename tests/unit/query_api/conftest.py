"""Shared fixtures for query API unit tests. Nothing here touches a real database."""

import inspect
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from fastapi.testclient import TestClient
from query_api import router, service
from query_api.db import get_session
from query_api.main import app
from query_api.service import LogPage
from sqlalchemy.orm import Session


@pytest.fixture
def session() -> MagicMock:
    """A mock Session with the real Session's method signatures."""
    return create_autospec(Session, instance=True)


@pytest.fixture
def client(session: MagicMock) -> Generator[TestClient]:
    """A TestClient whose requests get the mock session; the lifespan (if any) is not run."""
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def fetch_log_events() -> Generator[MagicMock]:
    """Mock fetch_log_events where the router looks it up; it returns an empty last page unless a test sets
    return_value."""
    empty = LogPage(next_cursor=None, items=[])
    with patch.object(router, "fetch_log_events", autospec=True, return_value=empty) as mock:
        yield mock


@pytest.fixture
def db_check() -> Generator[MagicMock]:
    """Mock check_db_connection where the router looks it up; it succeeds unless a test sets side_effect."""
    with patch.object(router, "check_db_connection", autospec=True) as mock:
        yield mock


@pytest.fixture
def fetch_log_events_args(fetch_log_events: MagicMock) -> Callable[[], dict[str, Any]]:
    """Return a function giving the arguments of the single fetch_log_events call by parameter name,
    whether the router passed them positionally or by keyword. Unpassed parameters get their defaults."""

    def args() -> dict[str, Any]:
        fetch_log_events.assert_called_once()
        bound = inspect.signature(service.fetch_log_events).bind(
            *fetch_log_events.call_args.args, **fetch_log_events.call_args.kwargs
        )
        bound.apply_defaults()
        return dict(bound.arguments)

    return args
