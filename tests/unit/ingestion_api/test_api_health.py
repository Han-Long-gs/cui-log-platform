"""GET /healthz and GET /readyz in both ingest modes, with check_db_connection / check_broker_connection mocked."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from cui_messaging import BrokerDownError
from fastapi.testclient import TestClient
from ingestion_api import queue_router, sync_router
from sqlalchemy import exc

SECRET = "db.internal.example:5432 password=hunter2"


@pytest.fixture
def db_check() -> Generator[MagicMock]:
    """Mock check_db_connection where sync_router looks it up; it succeeds unless a test sets side_effect."""
    with patch.object(sync_router, "check_db_connection", autospec=True) as mock:
        yield mock


def test_healthz_returns_200_without_db_check(client: TestClient, db_check: MagicMock) -> None:
    """/healthz answers 200 even when the database is down, and never runs the check."""
    db_check.side_effect = exc.OperationalError("SELECT 1", {}, Exception(SECRET))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == "ingestion-api service is up and run!"
    db_check.assert_not_called()


def test_readyz_returns_200_when_db_check_succeeds(client: TestClient, db_check: MagicMock, session: MagicMock) -> None:
    """/readyz runs the check once with the request's session and answers 200."""
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == "ingestion-api service to db is healthy"
    db_check.assert_called_once_with(session)


@pytest.mark.parametrize(
    "error",
    [exc.OperationalError("SELECT 1", {}, Exception(SECRET)), exc.TimeoutError(f"QueuePool timed out {SECRET}")],
    ids=["operational_error", "pool_timeout"],
)
def test_readyz_returns_503_when_db_unavailable(client: TestClient, db_check: MagicMock, error: Exception) -> None:
    """An unreachable database or an exhausted pool gives 503 with a generic body that leaks no details."""
    db_check.side_effect = error
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == "Database is unavailable"
    assert SECRET not in resp.text


def test_readyz_lets_other_db_errors_propagate(client: TestClient, db_check: MagicMock) -> None:
    """Errors that do not mean "database unavailable" are not turned into 503."""
    db_check.side_effect = exc.ProgrammingError("SELECT 1", {}, Exception("syntax error"))
    with pytest.raises(exc.ProgrammingError):
        client.get("/readyz")


# --- queue mode ---


def test_queue_healthz_returns_200_without_broker_check(queue_client: TestClient, broker_check: MagicMock) -> None:
    """/healthz is the same in queue mode: 200 even when the broker is down, and the broker is never checked."""
    broker_check.side_effect = BrokerDownError("Broker is down")
    resp = queue_client.get("/healthz")
    assert resp.status_code == 200
    broker_check.assert_not_called()


def test_queue_readyz_returns_200_when_broker_reachable(
    queue_client: TestClient, broker_check: MagicMock, db_check: MagicMock
) -> None:
    """/readyz checks the broker with the router's Celery app, answers 200, and does not check the database."""
    resp = queue_client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == "ingestion-api service to broker is healthy"
    broker_check.assert_called_once_with(queue_router.celery_app)
    db_check.assert_not_called()


def test_queue_readyz_returns_503_when_broker_down(queue_client: TestClient, broker_check: MagicMock) -> None:
    """An unreachable broker gives 503 with a generic body that leaks no details."""
    error = BrokerDownError("Broker is down")
    error.__cause__ = Exception(SECRET)
    broker_check.side_effect = error
    resp = queue_client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == "Messaging broker is unreachable"
    assert SECRET not in resp.text
