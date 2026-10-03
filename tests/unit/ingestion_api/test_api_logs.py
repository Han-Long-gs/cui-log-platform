"""POST /v1/logs over HTTP with a mock session: 201 on success, the app's own 422 format, 413 for large bodies.

Field-level edge cases live in test_schemas.py; this file only checks what the HTTP layer adds.
"""

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from httpx2 import Response
from ingestion_api.constants import MAX_BODY_BYTES


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


def test_valid_batch_returns_201_and_adds_events(
    make_event: Callable[..., dict[str, Any]], client: TestClient, session: MagicMock
) -> None:
    """A valid batch is handed to the session in one add_all call and answered with 201."""
    resp = client.post("/v1/logs", json={"log_entries": [make_event(), make_event()]})
    assert resp.status_code == 201
    assert resp.json() == {"message": "succeed"}
    session.add_all.assert_called_once()
    assert len(session.add_all.call_args.args[0]) == 2


def test_invalid_level_reports_loc_and_value(make_event: Callable[..., dict[str, Any]], client: TestClient) -> None:
    """A bad level is reported with its full path, and the message names the bad value."""
    resp = client.post("/v1/logs", json={"log_entries": [make_event(level="verbose")]})
    assert error_locs(resp) == [["body", "log_entries", 0, "level"]]
    assert "verbose" in resp.json()["detail"][0]["msg"]


def test_loc_points_at_offending_entry(make_event: Callable[..., dict[str, Any]], client: TestClient) -> None:
    """The loc index is that of the invalid entry, not the first one."""
    entries = [make_event(), make_event(occurred_at="2026-10-03T10:00:00"), make_event()]
    resp = client.post("/v1/logs", json={"log_entries": entries})
    assert error_locs(resp) == [["body", "log_entries", 1, "occurred_at"]]


def test_all_errors_reported_together(make_event: Callable[..., dict[str, Any]], client: TestClient) -> None:
    """Every error in the request is listed in one response."""
    resp = client.post("/v1/logs", json={"log_entries": [make_event(level="verbose", occurred_at=1727712000)]})
    assert sorted(error_locs(resp)) == [["body", "log_entries", 0, "level"], ["body", "log_entries", 0, "occurred_at"]]


def test_missing_body_returns_422(client: TestClient, session: MagicMock) -> None:
    """A request without a body is rejected and nothing is added to the session."""
    assert error_locs(client.post("/v1/logs")) == [["body"]]
    session.add_all.assert_not_called()


def test_oversized_body_returns_413(make_event: Callable[..., dict[str, Any]], client: TestClient) -> None:
    """A body larger than MAX_BODY_BYTES is rejected with 413 before validation."""
    body = json.dumps({"log_entries": [make_event(message="x" * MAX_BODY_BYTES)]})
    resp = client.post("/v1/logs", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413
