"""cui_db.engine against the real database: ensure_dev_tenant creates the dev tenant row exactly once."""

from collections.abc import Generator

import pytest
from cui_config import settings
from cui_db import DEV_TENANT_ID, Tenant, ensure_dev_tenant, make_engine
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def engine(db_schema: None) -> Generator[Engine]:
    """An engine built by make_engine on the test database, with tenants emptied before and after the test."""
    engine = make_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tenants RESTART IDENTITY CASCADE"))
    yield engine
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tenants RESTART IDENTITY CASCADE"))
    engine.dispose()


def tenants(engine: Engine) -> list[Tenant]:
    """Return every row of tenants."""
    with Session(engine) as session:
        return list(session.scalars(select(Tenant)))


def test_make_engine_connects_to_the_configured_database(engine: Engine) -> None:
    """The engine built from the settings URL reaches the database named in it."""
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT current_database()")) == engine.url.database


def test_ensure_dev_tenant_inserts_the_dev_tenant(engine: Engine) -> None:
    """On an empty table one row with the dev tenant id is committed."""
    ensure_dev_tenant(sessionmaker(engine))
    (tenant,) = tenants(engine)
    assert tenant.id == DEV_TENANT_ID
    assert tenant.created_at is not None


def test_ensure_dev_tenant_does_nothing_when_it_exists(engine: Engine) -> None:
    """A second call succeeds and leaves the existing row alone."""
    ensure_dev_tenant(sessionmaker(engine))
    (before,) = tenants(engine)
    ensure_dev_tenant(sessionmaker(engine))
    (after,) = tenants(engine)
    assert after.id == DEV_TENANT_ID
    assert after.created_at == before.created_at
