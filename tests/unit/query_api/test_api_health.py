"""/healthz and /readyz over HTTP, with the database check mocked (test_service.py tests the check itself)."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import exc

SECRET = "postgresql://cui:hunter2@db.internal:5432/cui"


def test_healthz_is_200_without_touching_the_database(client: TestClient, db_check: MagicMock) -> None:
    """Liveness only says the process serves HTTP."""
    assert client.get("/healthz").status_code == 200
    db_check.assert_not_called()


def test_readyz_is_200_when_the_database_answers(client: TestClient, db_check: MagicMock, session: MagicMock) -> None:
    """Readiness runs the database check on the request's session."""
    assert client.get("/readyz").status_code == 200
    db_check.assert_called_once_with(session)


def test_readyz_is_503_when_the_database_is_unreachable(client: TestClient, db_check: MagicMock) -> None:
    """An OperationalError (connection refused, auth failed) means not ready."""
    db_check.side_effect = exc.OperationalError("SELECT 1", {}, Exception(SECRET))
    assert client.get("/readyz").status_code == 503


def test_readyz_is_503_when_the_pool_times_out(client: TestClient, db_check: MagicMock) -> None:
    """A pool TimeoutError (every connection busy) also means not ready."""
    db_check.side_effect = exc.TimeoutError()
    assert client.get("/readyz").status_code == 503


def test_readyz_does_not_leak_the_error(client: TestClient, db_check: MagicMock) -> None:
    """The 503 body never contains the database error (it can include the connection URL)."""
    db_check.side_effect = exc.OperationalError("SELECT 1", {}, Exception(SECRET))
    assert "hunter2" not in client.get("/readyz").text
