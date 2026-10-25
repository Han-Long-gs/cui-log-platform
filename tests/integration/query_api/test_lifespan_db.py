"""The app's lifespan against the real database: the dev tenant exists after startup, the engine is disposed on
shutdown."""

import uuid
from unittest.mock import patch

from cui_db import DEV_TENANT_ID, Tenant
from fastapi.testclient import TestClient
from query_api import db, router
from query_api.main import app
from sqlalchemy import select, text
from sqlalchemy.orm import Session


def tenant_ids() -> list[uuid.UUID]:
    """Return the id of every row in tenants."""
    with Session(db.engine) as session:
        return list(session.scalars(select(Tenant.id)))


def test_startup_creates_the_dev_tenant_when_missing() -> None:
    """With an empty tenants table, starting the app inserts exactly the dev tenant."""
    with db.engine.begin() as conn:
        conn.execute(text("TRUNCATE tenants, log_events RESTART IDENTITY CASCADE"))
    with TestClient(app):
        assert tenant_ids() == [DEV_TENANT_ID]


def test_shutdown_disposes_the_engine() -> None:
    """The real dispose_engine runs when the app shuts down, not before."""
    with patch.object(router, "dispose_engine", wraps=db.dispose_engine) as dispose:
        with TestClient(app):
            dispose.assert_not_called()
        dispose.assert_called_once()
