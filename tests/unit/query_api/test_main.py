"""The app's lifespan with the database calls mocked: dev tenant on startup, engine disposal on shutdown."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from query_api import db, router
from query_api.main import app


def test_startup_ensures_the_dev_tenant_and_shutdown_disposes_the_engine() -> None:
    """Entering the app creates the dev tenant once; leaving it disposes the engine, and not before."""
    with (
        patch.object(router, "ensure_dev_tenant", autospec=True) as ensure,
        patch.object(router, "dispose_engine", autospec=True) as dispose,
    ):
        with TestClient(app):
            ensure.assert_called_once_with(session_factory=db.session_factory)
            dispose.assert_not_called()
        dispose.assert_called_once()
