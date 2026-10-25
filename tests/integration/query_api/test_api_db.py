"""GET /api/v1/logs, /api/v1/stats and /readyz end to end: HTTP → router → service → the real test database.

One path through every layer; filter and boundary cases live in test_service_db.py.
"""

from collections.abc import Callable
from datetime import datetime

from fastapi.testclient import TestClient
from query_api.main import app


def test_logs_endpoint_reads_the_dev_tenant_from_the_database(at: Callable[..., datetime]) -> None:
    """A filtered request returns the dev tenant's rows newest first, serialized with their stored values."""
    resp = TestClient(app).get("/api/v1/logs", params={"level": "error", "service_name": "api", "environment": "prod"})
    assert resp.status_code == 200
    body = resp.json()
    assert [item["message"] for item in body["items"]] == ["e8", "e1"]
    assert body["next_cursor"] is None
    newest = body["items"][0]
    assert newest["service_name"] == "api"
    assert newest["environment"] == "prod"
    assert newest["level"] == "ERROR"
    assert datetime.fromisoformat(newest["occurred_at"]) == at(9, day=2)
    assert isinstance(newest["id"], int)


def test_following_next_cursor_over_http_reads_every_row_once() -> None:
    """A client that sends back next_cursor until it is null sees each dev row exactly once, newest first."""
    client = TestClient(app)
    seen: list[list[str]] = []
    params = {"limit": "3"}
    for _ in range(10):
        resp = client.get("/api/v1/logs", params=params)
        assert resp.status_code == 200
        body = resp.json()
        seen.append([item["message"] for item in body["items"]])
        if body["next_cursor"] is None:
            break
        params = {"limit": "3", "cursor": body["next_cursor"]}
    assert seen == [["e8", "e7", "e6"], ["e5", "e4", "e3"], ["e2", "e1"]]


def test_stats_endpoint_reads_the_dev_tenant_from_the_database() -> None:
    """A filtered request counts the dev tenant's rows per level, every level present."""
    resp = TestClient(app).get("/api/v1/stats", params={"service_name": "api", "environment": "prod"})
    assert resp.status_code == 200
    assert resp.json() == {"counts": {"DEBUG": 0, "INFO": 1, "WARNING": 0, "ERROR": 2, "CRITICAL": 1}}


def test_readyz_is_200_against_the_real_database() -> None:
    """The database check passes when Postgres is up."""
    assert TestClient(app).get("/readyz").status_code == 200
