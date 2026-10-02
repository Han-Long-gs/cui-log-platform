"""
Handles the creation of the db engine and db ops
ref: https://docs.sqlalchemy.org/en/21/orm/session_basics.html
"""

import uuid
from collections.abc import Generator
from datetime import UTC, datetime

from cui_db import Tenant
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from config import settings

DEV_TENANT_ID = uuid.UUID("dab739d3-52a2-4efa-ae31-6afc1031f062")

sync_conn_url = make_url(settings.database_url).set(drivername="postgresql+psycopg")
engine = create_engine(sync_conn_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
session_factory = sessionmaker(engine)


def get_session() -> Generator[Session]:
    """creates a new session and yield after receiving the request,
    execute the cleanup following the yield before response"""
    with session_factory.begin() as session:
        yield session
    # commits the transaction, closes the session


def ensure_dev_tenant() -> None:
    """Insert the dev tenant row (DEV_TENANT_ID) into tenants if it does not exist; do nothing if it already exists."""
    with session_factory.begin() as session:
        if session.get(Tenant, DEV_TENANT_ID):
            return
        dev_tenant = Tenant(id=DEV_TENANT_ID, name="Test Tenant", retention_days=10, created_at=datetime.now(UTC))
        session.add(dev_tenant)
    # commits the transaction, closes the session


def dispose_engine() -> None:
    """dispose the db engine"""
    engine.dispose()
