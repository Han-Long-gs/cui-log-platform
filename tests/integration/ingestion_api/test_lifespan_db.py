"""The sync-mode lifespan against the real database: dev tenant on startup, engine disposal on shutdown."""

import uuid
from unittest.mock import patch

from cui_db import Tenant
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ingestion_api import db, sync_router
from ingestion_api.constants import DEV_TENANT_ID
from sqlalchemy import select
from sqlalchemy.orm import Session


def tenant_ids() -> list[uuid.UUID]:
    """Return the id of every row in tenants."""
    with Session(db.engine) as session:
        return list(session.scalars(select(Tenant.id)))


def test_startup_creates_dev_tenant(client: TestClient) -> None:
    """Starting the app inserts the dev tenant into an empty tenants table."""
    assert tenant_ids() == [DEV_TENANT_ID]


def test_second_startup_does_not_duplicate_dev_tenant(client: TestClient, sync_app: FastAPI) -> None:
    """Starting the app again when the dev tenant already exists succeeds and leaves a single row."""
    with TestClient(sync_app):
        pass
    assert tenant_ids() == [DEV_TENANT_ID]


def test_shutdown_disposes_engine(sync_app: FastAPI) -> None:
    """The engine is disposed when the app shuts down, not before."""
    with patch.object(sync_router, "dispose_engine", wraps=db.dispose_engine) as dispose:
        with TestClient(sync_app):
            dispose.assert_not_called()
        dispose.assert_called_once()
