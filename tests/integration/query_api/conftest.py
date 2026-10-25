"""Fixtures for query API integration tests: a known data set in the real test database, read through the app's engine.

The data set is e1–e8 for the dev tenant plus rows for a second tenant placed at the same instants, so any missing
tenant filter shows up as extra rows.
Tests can't import from this file (no packages under tests/), so everything they need is a fixture.
"""

import uuid
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from cui_db import DEV_TENANT_ID, LogEvent, Tenant
from query_api import db
from query_api.constants import DEFAULT_LIMIT
from query_api.service import LogPage, count_log_events_by_level, decode_cursor, fetch_log_events
from sqlalchemy import text
from sqlalchemy.orm import Session

OTHER_TENANT_ID = uuid.UUID("0b1e7c2a-5d1f-4c8e-9a51-3f0d8e2b6c47")

# (message, service_name, environment, level, occurred_at)
Event = tuple[str, str, str, str, datetime]


def at_utc(hour: int, day: int = 1) -> datetime:
    """An instant on 2026-10-<day> at <hour>:00 UTC."""
    return datetime(2026, 10, day, hour, tzinfo=UTC)


# Inserted in this order so ids ascend e1 → e8. e4 and e5 share an instant: newest first puts e5 (larger id) first.
DEV_EVENTS: list[Event] = [
    ("e1", "api", "prod", "ERROR", at_utc(9)),
    ("e2", "api", "prod", "INFO", at_utc(10)),
    ("e3", "api", "dev", "ERROR", at_utc(11)),
    ("e4", "worker", "prod", "ERROR", at_utc(12)),
    ("e5", "worker", "prod", "WARNING", at_utc(12)),
    ("e6", "worker", "dev", "DEBUG", at_utc(13)),
    ("e7", "api", "prod", "CRITICAL", at_utc(14)),
    ("e8", "api", "prod", "ERROR", at_utc(9, day=2)),
]

# Same services, environments and levels as the dev tenant, at instants the dev tenant also uses.
OTHER_EVENTS: list[Event] = [
    ("other-1", "api", "prod", "ERROR", at_utc(12)),
    ("other-2", "worker", "prod", "WARNING", at_utc(12)),
    ("other-3", "api", "dev", "ERROR", at_utc(9, day=2)),
]


def truncate() -> None:
    """Delete every tenant and log event, along with rows that reference a tenant."""
    with db.engine.begin() as conn:
        conn.execute(text("TRUNCATE tenants, log_events RESTART IDENTITY CASCADE"))


def insert(tenant_id: uuid.UUID, events: list[Event]) -> None:
    """Insert events one flush at a time, so ids follow the list order."""
    with Session(db.engine) as session, session.begin():
        for message, service_name, environment, level, occurred_at in events:
            session.add(
                LogEvent(
                    event_id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    service_name=service_name,
                    environment=environment,
                    level=level,
                    message=message,
                    occurred_at=occurred_at,
                    ingested_at=datetime.now(UTC),
                )
            )
            session.flush()


@pytest.fixture(scope="session", autouse=True)
def dispose_app_engine(db_schema: None) -> Generator[None]:
    """Use the shared schema, and close the app engine's pooled connections before the schema is dropped."""
    yield
    db.engine.dispose()


@pytest.fixture(autouse=True)
def seeded() -> Generator[None]:
    """Two tenants with the known events, rebuilt for every test and removed afterwards."""
    truncate()
    with Session(db.engine) as session, session.begin():
        session.add(Tenant(id=DEV_TENANT_ID, name="dev", retention_days=10, created_at=datetime.now(UTC)))
        session.add(Tenant(id=OTHER_TENANT_ID, name="other", retention_days=10, created_at=datetime.now(UTC)))
    insert(DEV_TENANT_ID, DEV_EVENTS)
    insert(OTHER_TENANT_ID, OTHER_EVENTS)
    yield
    truncate()


@pytest.fixture
def at() -> Callable[..., datetime]:
    """at(hour, day=1): an instant on 2026-10-<day> at <hour>:00 UTC, the clock the data set uses."""
    return at_utc


@pytest.fixture
def other_tenant_id() -> uuid.UUID:
    """The second tenant's id."""
    return OTHER_TENANT_ID


@pytest.fixture
def insert_events() -> Callable[[uuid.UUID, list[Event]], None]:
    """insert_events(tenant_id, events): add more rows on top of the data set, ids in list order."""
    return insert


# fetch_log_events has no defaults (the schema fills them in), so tests start from "no filter, default limit"
# and override only what they care about.
NO_FILTERS: dict[str, Any] = {
    "service_name": None,
    "environment": None,
    "level": None,
    "start": None,
    "end": None,
    "cursor": None,
    "limit": DEFAULT_LIMIT,
}
MAX_PAGES = 20


def fetch_page(tenant_id: uuid.UUID = DEV_TENANT_ID, **overrides: Any) -> LogPage:
    """Call fetch_log_events on a real session with every argument passed explicitly: NO_FILTERS plus overrides."""
    with Session(db.engine, expire_on_commit=False) as session:
        page = fetch_log_events(session, tenant_id, **(NO_FILTERS | overrides))
        session.expunge_all()
        return page


@pytest.fixture
def fetch() -> Callable[..., LogPage]:
    """fetch(tenant_id=dev tenant, **overrides): one LogPage; arguments not given are NO_FILTERS."""
    return fetch_page


@pytest.fixture
def messages() -> Callable[..., list[str]]:
    """messages(tenant_id=dev tenant, **overrides): the messages of one page, in the order received."""

    def page_messages(tenant_id: uuid.UUID = DEV_TENANT_ID, **overrides: Any) -> list[str]:
        return [event.message for event in fetch_page(tenant_id, **overrides).items]

    return page_messages


@pytest.fixture
def pages() -> Callable[..., list[list[str]]]:
    """pages(tenant_id=dev tenant, **overrides): walk every page, first without a cursor, then the decoded
    next_cursor of each page until it is None; returns the messages of each page. Fails after MAX_PAGES pages, so a
    cursor that never moves can't loop forever."""

    def walk(tenant_id: uuid.UUID = DEV_TENANT_ID, **overrides: Any) -> list[list[str]]:
        result: list[list[str]] = []
        cursor = None
        for _ in range(MAX_PAGES):
            page = fetch_page(tenant_id, **(overrides | {"cursor": cursor}))
            result.append([event.message for event in page.items])
            if page.next_cursor is None:
                return result
            cursor = decode_cursor(page.next_cursor)
        pytest.fail(f"still a next_cursor after {MAX_PAGES} pages: {result}")

    return walk


# count_log_events_by_level has no defaults either.
NO_COUNT_FILTERS: dict[str, Any] = {
    "service_name": None,
    "environment": None,
    "start": None,
    "end": None,
}


@pytest.fixture
def count() -> Callable[..., dict[str, int]]:
    """count(tenant_id=dev tenant, **overrides): count_log_events_by_level on a real session with every argument
    passed explicitly; arguments not given are NO_COUNT_FILTERS."""

    def count_levels(tenant_id: uuid.UUID = DEV_TENANT_ID, **overrides: Any) -> dict[str, int]:
        with Session(db.engine) as session:
            return count_log_events_by_level(session, tenant_id, **(NO_COUNT_FILTERS | overrides))

    return count_levels
