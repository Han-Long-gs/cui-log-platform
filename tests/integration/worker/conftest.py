"""Fixtures for worker integration tests, which run against the real test database through the worker's engine."""

from collections.abc import Callable, Generator
from datetime import UTC, datetime

import pytest
from cui_db import LogEvent, Tenant
from cui_messaging import BatchMessage
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session
from worker import db


@pytest.fixture(scope="session", autouse=True)
def dispose_worker_engine(db_schema: None) -> Generator[None]:
    """Use the shared schema, and close the worker engine's pooled connections before the schema is dropped."""
    yield
    db.engine.dispose()


def truncate() -> None:
    """Delete every tenant and log event, along with rows that reference a tenant."""
    with db.engine.begin() as conn:
        conn.execute(text("TRUNCATE tenants, log_events RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def empty_tables() -> Generator[None]:
    """Empty the tables before and after each test, so counts start from zero and nothing leaks into other tests."""
    truncate()
    yield
    truncate()


@pytest.fixture
def add_tenant() -> Callable[..., None]:
    """Return a function that inserts a tenant row with the given id."""

    def add(tenant_id: object) -> None:
        with db.session_factory.begin() as session:
            session.add(
                Tenant(id=tenant_id, name=f"tenant-{tenant_id}", retention_days=30, created_at=datetime.now(UTC))
            )

    return add


@pytest.fixture
def batch_tenant(make_batch: Callable[..., BatchMessage], add_tenant: Callable[..., None]) -> None:
    """The tenant named by make_batch() messages exists, so their rows satisfy the foreign key."""
    add_tenant(make_batch().tenant_id)


@pytest.fixture
def stored_events() -> Callable[[], list[LogEvent]]:
    """Return a function that reads every row of log_events, ordered by id."""

    def fetch() -> list[LogEvent]:
        with Session(db.engine) as session:
            return list(session.scalars(select(LogEvent).order_by(LogEvent.id)))

    return fetch


@pytest.fixture
def log_event_inserts() -> Generator[list[str]]:
    """Collect every INSERT INTO log_events statement the worker engine sends while the test runs."""
    seen: list[str] = []

    def record(conn: object, cursor: object, statement: str, *rest: object) -> None:
        if statement.startswith("INSERT INTO log_events"):
            seen.append(statement)

    event.listen(db.engine, "before_cursor_execute", record)
    yield seen
    event.remove(db.engine, "before_cursor_execute", record)
