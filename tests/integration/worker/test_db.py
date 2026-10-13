"""worker.db against the real database: the main-process startup handler creates the dev tenant and keeps no
connection for child processes to inherit."""

import uuid

from cui_db import DEV_TENANT_ID, Tenant
from sqlalchemy import select
from sqlalchemy.orm import Session
from worker import db


def tenant_ids() -> list[uuid.UUID]:
    """Return the id of every row in tenants."""
    with Session(db.engine) as session:
        return list(session.scalars(select(Tenant.id)))


def test_startup_creates_the_dev_tenant() -> None:
    """Starting the worker inserts the dev tenant into an empty tenants table."""
    db.create_dev_tenant()
    assert tenant_ids() == [DEV_TENANT_ID]


def test_second_startup_does_not_duplicate_the_dev_tenant() -> None:
    """Starting the worker again when the dev tenant exists succeeds and leaves a single row."""
    db.create_dev_tenant()
    db.create_dev_tenant()
    assert tenant_ids() == [DEV_TENANT_ID]


def test_startup_leaves_no_pooled_connection() -> None:
    """The main process closes its connection after creating the dev tenant, so a forked child inherits none."""
    db.create_dev_tenant()
    assert db.engine.pool.checkedin() == 0
    assert db.engine.pool.checkedout() == 0
