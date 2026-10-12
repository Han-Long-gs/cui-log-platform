"""
Handles the creation of the db engine and db ops
ref: https://docs.sqlalchemy.org/en/21/orm/session_basics.html
"""

from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from .constants import DEV_TENANT_ID
from .models import Tenant


def make_engine(url: str) -> Engine:
    """creates a db engine"""
    sync_conn_url = make_url(url).set(drivername="postgresql+psycopg")
    engine = create_engine(sync_conn_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    return engine


def ensure_dev_tenant(session_factory: sessionmaker) -> None:
    """Insert the dev tenant row (DEV_TENANT_ID) into tenants if it does not exist; do nothing if it already exists."""
    with session_factory.begin() as session:
        if session.get(Tenant, DEV_TENANT_ID):
            return
        dev_tenant = Tenant(id=DEV_TENANT_ID, name="Test Tenant", retention_days=10, created_at=datetime.now(UTC))
        session.add(dev_tenant)
    # commits the transaction, closes the session
