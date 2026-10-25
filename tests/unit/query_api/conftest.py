"""Shared fixtures for query API unit tests. Nothing here touches a real database."""

import inspect
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from cui_schemas.constants import LEVELS
from fastapi.testclient import TestClient
from httpx2 import Response
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


def args_by_name(mock: MagicMock, real: Callable[..., Any]) -> Callable[[], dict[str, Any]]:
    """Return a function giving the arguments of the single call to mock by real's parameter names,
    whether the router passed them positionally or by keyword. Unpassed parameters get their defaults."""

    def args() -> dict[str, Any]:
        mock.assert_called_once()
        bound = inspect.signature(real).bind(*mock.call_args.args, **mock.call_args.kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)

    return args


@pytest.fixture
def fetch_log_events_args(fetch_log_events: MagicMock) -> Callable[[], dict[str, Any]]:
    """The arguments of the single fetch_log_events call, by parameter name."""
    return args_by_name(fetch_log_events, service.fetch_log_events)


@pytest.fixture
def count_log_events_by_level() -> Generator[MagicMock]:
    """Mock count_log_events_by_level where the router looks it up; it returns zero for every level unless a test
    sets return_value."""
    zeros = dict.fromkeys(LEVELS, 0)
    with patch.object(router, "count_log_events_by_level", autospec=True, return_value=zeros) as mock:
        yield mock


@pytest.fixture
def count_log_events_by_level_args(count_log_events_by_level: MagicMock) -> Callable[[], dict[str, Any]]:
    """The arguments of the single count_log_events_by_level call, by parameter name."""
    return args_by_name(count_log_events_by_level, service.count_log_events_by_level)


@pytest.fixture
def error_locs() -> Callable[[Response], list[list[Any]]]:
    """error_locs(resp): assert resp is a 422 in the app's format (exact keys, unlike FastAPI's default) and return
    each error's loc."""

    def locs(resp: Response) -> list[list[Any]]:
        assert resp.status_code == 422
        body = resp.json()
        assert body.keys() == {"error", "detail"}
        assert body["error"] == "Validation Error"
        assert body["detail"], "422 response must list at least one error"
        for entry in body["detail"]:
            assert entry.keys() == {"loc", "msg"}
        return [entry["loc"] for entry in body["detail"]]

    return locs
