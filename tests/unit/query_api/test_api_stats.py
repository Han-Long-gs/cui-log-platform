"""GET /api/v1/stats over HTTP, with count_log_events_by_level mocked.

Checks what the HTTP layer adds on top of test_schemas.py: the response shape, the app's 422 format, which arguments
reach count_log_events_by_level (tenant from the server, not the request), and that parameters /stats does not take
are ignored.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from cui_db import DEV_TENANT_ID
from fastapi.testclient import TestClient
from httpx2 import Response

URL = "/api/v1/stats"

# The error_locs fixture (conftest.py).
ErrorLocs = Callable[[Response], list[list[Any]]]


# ---------- 200 ----------


def test_counts_from_the_service_are_returned_under_counts(
    client: TestClient, count_log_events_by_level: MagicMock
) -> None:
    """The per-level numbers from the service come back unchanged under "counts"."""
    counts = {"DEBUG": 0, "INFO": 120, "WARNING": 7, "ERROR": 3, "CRITICAL": 1}
    count_log_events_by_level.return_value = counts
    resp = client.get(URL)
    assert resp.status_code == 200
    assert resp.json()["counts"] == counts


def test_response_has_exactly_the_counts_key(client: TestClient, count_log_events_by_level: MagicMock) -> None:
    """Level counts sit inside "counts", so fields added later can't be mistaken for a level."""
    resp = client.get(URL)
    assert resp.json().keys() == {"counts"}


# ---------- what reaches count_log_events_by_level ----------


def test_no_params_passes_no_filters(
    client: TestClient, count_log_events_by_level_args: Callable[[], dict[str, Any]]
) -> None:
    """Without parameters every filter is None: count the whole tenant."""
    client.get(URL)
    args = count_log_events_by_level_args()
    assert args["service_name"] is None
    assert args["environment"] is None
    assert args["start"] is None
    assert args["end"] is None


def test_params_reach_the_service_parsed(
    client: TestClient, count_log_events_by_level_args: Callable[[], dict[str, Any]]
) -> None:
    """Filters are passed through and times are parsed to aware datetimes."""
    client.get(
        URL,
        params={
            "service_name": "api",
            "environment": "prod",
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-02T00:00:00Z",
        },
    )
    args = count_log_events_by_level_args()
    assert args["service_name"] == "api"
    assert args["environment"] == "prod"
    assert args["start"] == datetime(2026, 10, 1, tzinfo=UTC)
    assert args["end"] == datetime(2026, 10, 2, tzinfo=UTC)


def test_empty_filters_reach_the_service_as_none(
    client: TestClient, count_log_events_by_level_args: Callable[[], dict[str, Any]]
) -> None:
    """?service_name=&environment= is 200 and filters on nothing, the same as not sending them."""
    resp = client.get(URL, params={"service_name": "", "environment": ""})
    assert resp.status_code == 200
    args = count_log_events_by_level_args()
    assert args["service_name"] is None
    assert args["environment"] is None


def test_tenant_comes_from_the_server(
    client: TestClient, count_log_events_by_level_args: Callable[[], dict[str, Any]]
) -> None:
    """Invariant: the tenant is decided server-side; a tenant_id parameter is ignored."""
    resp = client.get(URL, params={"tenant_id": str(uuid.uuid4())})
    assert resp.status_code == 200
    assert count_log_events_by_level_args()["tenant_id"] == DEV_TENANT_ID


def test_service_gets_the_request_session(
    client: TestClient, session: MagicMock, count_log_events_by_level_args: Callable[[], dict[str, Any]]
) -> None:
    """count_log_events_by_level runs on the session from the get_session dependency."""
    client.get(URL)
    assert count_log_events_by_level_args()["session"] is session


def test_level_cursor_and_limit_are_ignored(client: TestClient, count_log_events_by_level: MagicMock) -> None:
    """/logs parameters that /stats does not take are dropped like any unknown parameter, even invalid values."""
    resp = client.get(URL, params={"level": "verbose", "cursor": "not base64!!", "limit": "0"})
    assert resp.status_code == 200
    count_log_events_by_level.assert_called_once()


# ---------- 422 ----------


def test_start_after_end_is_422(
    client: TestClient, count_log_events_by_level: MagicMock, error_locs: ErrorLocs
) -> None:
    """start > end comes back in the app's 422 format, and the service is not called."""
    resp = client.get(URL, params={"start": "2026-10-02T00:00:00Z", "end": "2026-10-01T00:00:00Z"})
    error_locs(resp)
    count_log_events_by_level.assert_not_called()


def test_naive_start_is_422_with_query_loc(
    client: TestClient, count_log_events_by_level: MagicMock, error_locs: ErrorLocs
) -> None:
    """A start without a timezone is reported at ["query", "start"]."""
    resp = client.get(URL, params={"start": "2026-10-01T12:00:00"})
    assert error_locs(resp) == [["query", "start"]]
    count_log_events_by_level.assert_not_called()
