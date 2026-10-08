"""POST /v1/logs against the real database: what gets committed, and what gets rolled back."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from cui_db import LogEvent
from fastapi.testclient import TestClient
from ingestion_api import db
from ingestion_api.constants import DEV_TENANT_ID, MAX_EVENTS_PER_BATCH
from sqlalchemy import text


def test_valid_batch_is_persisted(
    client: TestClient,
    make_event: Callable[..., dict[str, Any]],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """A valid batch is committed: every event is stored under the dev tenant with the fields it was sent with."""
    trace_id = str(uuid.uuid4())
    first = make_event(level="error", message="payment failed", trace_id=trace_id, custom={"order": 7})
    second = make_event()
    resp = client.post("/v1/logs", json={"log_entries": [first, second]})
    assert resp.status_code == 201

    stored = stored_events()
    assert [str(log.event_id) for log in stored] == [first["event_id"], second["event_id"]]
    log = stored[0]
    assert log.tenant_id == DEV_TENANT_ID
    assert log.service_name == "music-api"
    assert log.environment == "dev"
    assert log.level == "ERROR"
    assert log.message == "payment failed"
    assert str(log.trace_id) == trace_id
    assert log.custom == {"order": 7}


def test_max_size_batch_is_persisted(
    client: TestClient,
    make_event: Callable[..., dict[str, Any]],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """A batch of exactly MAX_EVENTS_PER_BATCH events is stored in full."""
    resp = client.post("/v1/logs", json={"log_entries": [make_event() for _ in range(MAX_EVENTS_PER_BATCH)]})
    assert resp.status_code == 201
    assert len(stored_events()) == MAX_EVENTS_PER_BATCH


def test_occurred_at_keeps_the_same_instant(
    client: TestClient,
    make_event: Callable[..., dict[str, Any]],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """A timestamp sent with a -07:00 offset reads back as the same moment in time."""
    client.post("/v1/logs", json={"log_entries": [make_event(occurred_at="2026-10-03T10:00:00-07:00")]})
    (log,) = stored_events()
    assert log.occurred_at == datetime(2026, 10, 3, 17, 0, tzinfo=UTC)


def test_missing_custom_is_stored_as_sql_null(client: TestClient, make_event: Callable[..., dict[str, Any]]) -> None:
    """An event without custom is stored as SQL NULL, not as the JSON value null."""
    without_custom = make_event()
    with_custom = make_event(custom={"k": "v"})
    client.post("/v1/logs", json={"log_entries": [without_custom, with_custom]})

    with db.engine.connect() as conn:
        rows = conn.execute(text("SELECT event_id::text, custom IS NULL FROM log_events")).all()
    is_null = dict(rows)
    assert is_null[without_custom["event_id"]] is True
    assert is_null[with_custom["event_id"]] is False


def test_duplicate_event_id_in_one_batch_stores_nothing(
    client: TestClient,
    make_event: Callable[..., dict[str, Any]],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """Two events with the same event_id fail the whole batch: 500 and not a single row is committed."""
    duplicate = make_event()
    resp = client.post("/v1/logs", json={"log_entries": [make_event(), duplicate, duplicate]})
    assert resp.status_code == 500
    assert stored_events() == []


def test_duplicate_across_requests_keeps_the_first_batch(
    client: TestClient,
    make_event: Callable[..., dict[str, Any]],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """A later batch repeating an earlier event_id fails on its own; the earlier batch stays as it was."""
    first, second = make_event(), make_event()
    assert client.post("/v1/logs", json={"log_entries": [first, second]}).status_code == 201

    resp = client.post("/v1/logs", json={"log_entries": [make_event(), first]})
    assert resp.status_code == 500
    assert [str(log.event_id) for log in stored_events()] == [first["event_id"], second["event_id"]]


def test_invalid_request_stores_nothing(
    client: TestClient,
    make_event: Callable[..., dict[str, Any]],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """A request that fails validation is rejected with 422 and writes no rows."""
    resp = client.post("/v1/logs", json={"log_entries": [make_event(), make_event(level="verbose")]})
    assert resp.status_code == 422
    assert stored_events() == []
