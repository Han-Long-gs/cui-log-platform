"""GET /api/v1/logs over HTTP, with fetch_log_events mocked.

Checks what the HTTP layer adds on top of test_schemas.py: the response shape, the app's 422 format, which arguments
reach fetch_log_events (tenant from the server, not the request; the cursor already decoded), that next_cursor is
passed through, and that unknown parameters are ignored.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from cui_db import DEV_TENANT_ID, LogEvent
from fastapi.testclient import TestClient
from httpx2 import Response
from query_api.constants import DEFAULT_LIMIT
from query_api.service import LogPage, encode_cursor

URL = "/api/v1/logs"


def error_locs(resp: Response) -> list[list[Any]]:
    """Assert resp is a 422 in the app's format (exact keys, unlike FastAPI's default) and return each error's loc."""
    assert resp.status_code == 422
    body = resp.json()
    assert body.keys() == {"error", "detail"}
    assert body["error"] == "Validation Error"
    assert body["detail"], "422 response must list at least one error"
    for entry in body["detail"]:
        assert entry.keys() == {"loc", "msg"}
    return [entry["loc"] for entry in body["detail"]]


def stored_event(**overrides: Any) -> LogEvent:
    """A LogEvent as fetch_log_events would return it."""
    fields: dict[str, Any] = {
        "id": 7,
        "event_id": uuid.uuid4(),
        "tenant_id": DEV_TENANT_ID,
        "service_name": "api",
        "environment": "prod",
        "level": "ERROR",
        "message": "boom",
        "occurred_at": datetime(2026, 10, 1, 12, tzinfo=UTC),
        "ingested_at": datetime(2026, 10, 1, 12, 0, 5, tzinfo=UTC),
        "trace_id": None,
        "custom": {"order": 7},
    }
    return LogEvent(**(fields | overrides))


def page(*events: LogEvent, next_cursor: str | None = None) -> LogPage:
    """What fetch_log_events returns: the events of one page and the cursor of the next one."""
    return LogPage(next_cursor=next_cursor, items=list(events))


# ---------- 200 ----------


def test_no_events_returns_200_with_empty_items(client: TestClient, fetch_log_events: MagicMock) -> None:
    """No matching events is not an error: 200, empty items, next_cursor null."""
    resp = client.get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body.keys() == {"items", "next_cursor"}
    assert body["items"] == []
    assert body["next_cursor"] is None


def test_events_are_serialized_in_the_order_fetch_log_events_returns(
    client: TestClient, fetch_log_events: MagicMock
) -> None:
    """The router keeps fetch_log_events' order (newest first is the service's job) and serializes each event."""
    fetch_log_events.return_value = page(stored_event(id=9, message="newer"), stored_event(id=3, message="older"))
    resp = client.get(URL)
    assert resp.status_code == 200
    assert [item["message"] for item in resp.json()["items"]] == ["newer", "older"]


def test_item_has_exactly_the_public_fields(client: TestClient, fetch_log_events: MagicMock) -> None:
    """Each item carries the public fields only; tenant_id / event_id / ingested_at never leave the server."""
    fetch_log_events.return_value = page(stored_event())
    item = client.get(URL).json()["items"][0]
    assert item.keys() == {"id", "service_name", "environment", "level", "message", "occurred_at", "trace_id", "custom"}
    assert item["id"] == 7
    assert item["custom"] == {"order": 7}
    assert datetime.fromisoformat(item["occurred_at"]) == datetime(2026, 10, 1, 12, tzinfo=UTC)


def test_next_cursor_is_passed_through(client: TestClient, fetch_log_events: MagicMock) -> None:
    """The router returns the page's next_cursor unchanged; computing it is the service's job."""
    fetch_log_events.return_value = page(stored_event(), next_cursor="opaque-cursor")
    assert client.get(URL).json()["next_cursor"] == "opaque-cursor"


# ---------- what reaches fetch_log_events ----------


def test_no_params_passes_no_filters_and_default_limit(
    client: TestClient, fetch_log_events_args: Callable[[], dict[str, Any]]
) -> None:
    """Without parameters every filter is None and limit is DEFAULT_LIMIT."""
    client.get(URL)
    args = fetch_log_events_args()
    assert args["service_name"] is None
    assert args["environment"] is None
    assert args["level"] is None
    assert args["start"] is None
    assert args["end"] is None
    assert args["cursor"] is None
    assert args["limit"] == DEFAULT_LIMIT


def test_params_reach_fetch_log_events_parsed_and_normalized(
    client: TestClient, fetch_log_events_args: Callable[[], dict[str, Any]]
) -> None:
    """Filters are passed through; level is normalized; times are parsed; limit is an int."""
    client.get(
        URL,
        params={
            "service_name": "api",
            "environment": "prod",
            "level": "error",
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-02T00:00:00Z",
            "limit": "5",
        },
    )
    args = fetch_log_events_args()
    assert args["service_name"] == "api"
    assert args["environment"] == "prod"
    assert args["level"] == "ERROR"
    assert args["start"] == datetime(2026, 10, 1, tzinfo=UTC)
    assert args["end"] == datetime(2026, 10, 2, tzinfo=UTC)
    assert args["limit"] == 5


def test_plus_offset_survives_url_encoding(
    client: TestClient, fetch_log_events_args: Callable[[], dict[str, Any]]
) -> None:
    """A '+08:00' offset sent encoded (%2B) arrives as the right instant."""
    client.get(URL, params={"start": "2026-10-01T20:00:00+08:00"})
    assert fetch_log_events_args()["start"] == datetime(2026, 10, 1, 12, tzinfo=UTC)


def test_tenant_comes_from_the_server(client: TestClient, fetch_log_events_args: Callable[[], dict[str, Any]]) -> None:
    """Invariant: the tenant is decided server-side; a tenant_id parameter is ignored."""
    resp = client.get(URL, params={"tenant_id": str(uuid.uuid4())})
    assert resp.status_code == 200
    assert fetch_log_events_args()["tenant_id"] == DEV_TENANT_ID


def test_fetch_log_events_gets_the_request_session(
    client: TestClient, session: MagicMock, fetch_log_events_args: Callable[[], dict[str, Any]]
) -> None:
    """fetch_log_events runs on the session from the get_session dependency."""
    client.get(URL)
    assert fetch_log_events_args()["session"] is session


def test_cursor_reaches_fetch_log_events_decoded(
    client: TestClient, fetch_log_events_args: Callable[[], dict[str, Any]]
) -> None:
    """The service gets (occurred_at, id), not the opaque string."""
    at = datetime(2026, 10, 1, 12, tzinfo=UTC)
    client.get(URL, params={"cursor": encode_cursor(at, 9)})
    assert fetch_log_events_args()["cursor"] == (at, 9)


def test_empty_cursor_is_the_first_page(
    client: TestClient, fetch_log_events_args: Callable[[], dict[str, Any]]
) -> None:
    """?cursor= is 200 and reaches the service as no cursor."""
    resp = client.get(URL, params={"cursor": ""})
    assert resp.status_code == 200
    assert fetch_log_events_args()["cursor"] is None


def test_unknown_params_are_ignored(client: TestClient, fetch_log_events: MagicMock) -> None:
    """Parameters the API does not know are dropped, not rejected."""
    assert client.get(URL, params={"foo": "bar"}).status_code == 200


# ---------- 422 ----------


def test_invalid_level_is_422_with_query_loc(client: TestClient, fetch_log_events: MagicMock) -> None:
    """A bad level is reported at ["query", "level"] in the app's format, and fetch_log_events is not called."""
    resp = client.get(URL, params={"level": "verbose"})
    assert error_locs(resp) == [["query", "level"]]
    fetch_log_events.assert_not_called()


def test_limit_out_of_range_is_422(client: TestClient, fetch_log_events: MagicMock) -> None:
    """limit=101 is reported at ["query", "limit"]."""
    resp = client.get(URL, params={"limit": "101"})
    assert error_locs(resp) == [["query", "limit"]]
    fetch_log_events.assert_not_called()


def test_naive_start_is_422(client: TestClient, fetch_log_events: MagicMock) -> None:
    """A start without a timezone is reported at ["query", "start"]."""
    resp = client.get(URL, params={"start": "2026-10-01T12:00:00"})
    assert error_locs(resp) == [["query", "start"]]
    fetch_log_events.assert_not_called()


def test_start_after_end_is_422(client: TestClient, fetch_log_events: MagicMock) -> None:
    """start > end comes back in the app's 422 format."""
    resp = client.get(URL, params={"start": "2026-10-02T00:00:00Z", "end": "2026-10-01T00:00:00Z"})
    error_locs(resp)
    fetch_log_events.assert_not_called()


def test_malformed_cursor_is_422_with_query_loc(client: TestClient, fetch_log_events: MagicMock) -> None:
    """A cursor that does not decode is reported at ["query", "cursor"] (a 422, not a 500)."""
    resp = client.get(URL, params={"cursor": "not base64!!"})
    assert error_locs(resp) == [["query", "cursor"]]
    fetch_log_events.assert_not_called()


def test_every_error_is_listed(client: TestClient, fetch_log_events: MagicMock) -> None:
    """Two bad parameters give two entries."""
    resp = client.get(URL, params={"level": "verbose", "limit": "0"})
    assert sorted(error_locs(resp)) == [["query", "level"], ["query", "limit"]]
