"""worker.service against the real database: one batch becomes rows of log_events, in one statement and one
transaction, under the tenant named by the message; events the tenant already has are skipped."""

import uuid
from collections.abc import Callable

import pytest
from cui_db import DEV_TENANT_ID, LogEvent
from cui_messaging import BatchMessage
from cui_schemas.schemas import LogEntry
from sqlalchemy import exc
from worker import db
from worker.service import ingest_batch_to_pg

OTHER_TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")


def with_new_event_ids(batch: BatchMessage) -> BatchMessage:
    """Return a copy of the batch whose entries all carry fresh event ids."""
    entries = [entry.model_copy(update={"event_id": uuid.uuid4()}) for entry in batch.log_entries]
    return batch.model_copy(update={"log_entries": entries})


# --- what gets written ---


def test_writes_one_row_per_entry(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_events: Callable[[], list[LogEvent]]
) -> None:
    """Every entry of the batch is stored, in the order it was sent."""
    batch = make_batch(entries=3)
    ingest_batch_to_pg(db.session_factory, batch)
    assert [row.event_id for row in stored_events()] == [entry.event_id for entry in batch.log_entries]


def test_stores_every_field_of_the_entry(
    batch_tenant: None,
    make_batch: Callable[..., BatchMessage],
    make_event: Callable[..., dict],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """Each column holds the value of the entry field with the same name."""
    entry = LogEntry.model_validate(
        make_event(
            service_name="rag-agent",
            environment="prod",
            level="error",
            message="boom",
            occurred_at="2026-10-01T08:30:00+02:00",
            trace_id=str(uuid.uuid4()),
            custom={"user": 7, "tags": ["a", "b"]},
        )
    )
    ingest_batch_to_pg(db.session_factory, make_batch().model_copy(update={"log_entries": [entry]}))
    (row,) = stored_events()
    assert row.event_id == entry.event_id
    assert row.service_name == "rag-agent"
    assert row.environment == "prod"
    assert row.level == "ERROR"
    assert row.message == "boom"
    assert row.occurred_at == entry.occurred_at
    assert row.trace_id == entry.trace_id
    assert row.custom == {"user": 7, "tags": ["a", "b"]}


def test_optional_fields_are_stored_as_null(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_events: Callable[[], list[LogEvent]]
) -> None:
    """An entry without trace_id or custom is stored with NULL in both columns."""
    ingest_batch_to_pg(db.session_factory, make_batch())
    (row,) = stored_events()
    assert row.trace_id is None
    assert row.custom is None


def test_rows_belong_to_the_tenant_named_by_the_message(
    add_tenant: Callable[..., None],
    make_batch: Callable[..., BatchMessage],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """tenant_id comes from the message, whichever other tenants exist (the dev tenant included)."""
    add_tenant(make_batch().tenant_id)
    add_tenant(DEV_TENANT_ID)
    add_tenant(OTHER_TENANT)
    batch = make_batch(entries=2).model_copy(update={"tenant_id": OTHER_TENANT})
    ingest_batch_to_pg(db.session_factory, batch)
    assert [row.tenant_id for row in stored_events()] == [OTHER_TENANT, OTHER_TENANT]


def test_ingested_at_is_when_the_api_received_the_batch(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_events: Callable[[], list[LogEvent]]
) -> None:
    """Every row of a batch carries the message's received_at, not the time the worker wrote it."""
    batch = make_batch(entries=3)
    ingest_batch_to_pg(db.session_factory, batch)
    assert [row.ingested_at for row in stored_events()] == [batch.received_at] * 3


# --- one statement, one transaction ---


def test_a_full_batch_is_one_insert_statement(
    batch_tenant: None,
    make_batch: Callable[..., BatchMessage],
    stored_events: Callable[[], list[LogEvent]],
    log_event_inserts: list[str],
) -> None:
    """200 entries are stored by a single INSERT, not one per entry."""
    ingest_batch_to_pg(db.session_factory, make_batch(entries=200))
    assert len(stored_events()) == 200
    assert len(log_event_inserts) == 1


def test_a_rejected_batch_stores_nothing(
    make_batch: Callable[..., BatchMessage], stored_events: Callable[[], list[LogEvent]]
) -> None:
    """A batch for a tenant that does not exist fails on the foreign key and leaves no row behind."""
    with pytest.raises(exc.IntegrityError, match="tenant_id"):
        ingest_batch_to_pg(db.session_factory, make_batch(entries=3))
    assert stored_events() == []


def test_the_connection_goes_back_to_the_pool(batch_tenant: None, make_batch: Callable[..., BatchMessage]) -> None:
    """After a batch is written no connection stays checked out."""
    ingest_batch_to_pg(db.session_factory, make_batch())
    assert db.engine.pool.checkedout() == 0


# --- duplicates ---


def test_the_same_batch_twice_is_stored_once(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_events: Callable[[], list[LogEvent]]
) -> None:
    """A redelivered batch succeeds and adds no rows."""
    batch = make_batch(entries=3)
    ingest_batch_to_pg(db.session_factory, batch)
    ingest_batch_to_pg(db.session_factory, batch)
    assert [row.event_id for row in stored_events()] == [entry.event_id for entry in batch.log_entries]


def test_only_new_events_of_a_partly_duplicate_batch_are_stored(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_events: Callable[[], list[LogEvent]]
) -> None:
    """Entries the tenant already has are skipped; the rest of the batch is still written."""
    first = make_batch(entries=2)
    new = with_new_event_ids(make_batch(entries=2))
    retry = first.model_copy(update={"log_entries": first.log_entries + new.log_entries})
    ingest_batch_to_pg(db.session_factory, first)
    ingest_batch_to_pg(db.session_factory, retry)
    assert [row.event_id for row in stored_events()] == [entry.event_id for entry in retry.log_entries]


def test_an_event_id_repeated_inside_one_batch_is_stored_once(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_events: Callable[[], list[LogEvent]]
) -> None:
    """Two entries with the same event id in one batch produce one row."""
    batch = make_batch(entries=1)
    doubled = batch.model_copy(update={"log_entries": batch.log_entries * 2})
    ingest_batch_to_pg(db.session_factory, doubled)
    assert [row.event_id for row in stored_events()] == [batch.log_entries[0].event_id]


def test_the_same_event_id_under_another_tenant_is_not_a_duplicate(
    add_tenant: Callable[..., None],
    make_batch: Callable[..., BatchMessage],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """Deduplication is per tenant: one tenant's event id never hides another tenant's event."""
    batch = make_batch()
    add_tenant(batch.tenant_id)
    add_tenant(OTHER_TENANT)
    ingest_batch_to_pg(db.session_factory, batch)
    ingest_batch_to_pg(db.session_factory, batch.model_copy(update={"tenant_id": OTHER_TENANT}))
    assert [row.tenant_id for row in stored_events()] == [batch.tenant_id, OTHER_TENANT]
