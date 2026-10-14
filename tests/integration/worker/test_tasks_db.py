"""worker.tasks against the real database: running the task stores the batch and its ingest_batches row through the
worker's own engine, also when the message comes twice, the write fails or the message is invalid."""

import uuid
from collections.abc import Callable

import pytest
from cui_db import IngestBatch, LogEvent
from cui_messaging import INGEST_BATCH_ARG_NAME, BatchMessage
from pydantic import ValidationError
from worker import tasks


def test_task_stores_the_batch(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_events: Callable[[], list[LogEvent]]
) -> None:
    """A message as it arrives from the broker ends up as rows of log_events."""
    batch = make_batch(entries=3)
    tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: batch.model_dump(mode="json")})
    assert [row.event_id for row in stored_events()] == [entry.event_id for entry in batch.log_entries]


def test_task_stores_nothing_from_an_invalid_message(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_events: Callable[[], list[LogEvent]]
) -> None:
    """One invalid entry fails the whole message before anything is written."""
    payload = make_batch(entries=3).model_dump(mode="json")
    payload["log_entries"][2]["level"] = "verbose"
    with pytest.raises(ValidationError, match="level"):
        tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})
    assert stored_events() == []


def test_task_leaves_the_batch_persisted(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """The ingest_batches row of a stored batch ends as PERSISTED."""
    batch = make_batch()
    tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: batch.model_dump(mode="json")})
    assert stored_batch(batch.batch_id).status == "PERSISTED"


def test_the_same_message_twice_is_processed_once(
    batch_tenant: None,
    make_batch: Callable[..., BatchMessage],
    stored_batch: Callable[..., IngestBatch | None],
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """A redelivery after the commit changes nothing: same rows, same timestamps as the first run."""
    payload = make_batch(entries=3).model_dump(mode="json")
    tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})
    first = stored_batch(uuid.UUID(payload["batch_id"]))
    tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})
    second = stored_batch(uuid.UUID(payload["batch_id"]))
    assert len(stored_events()) == 3
    assert second.started_at == first.started_at
    assert second.persisted_at == first.persisted_at


def test_a_failed_write_leaves_the_batch_failed_and_no_rows(
    batch_tenant: None,
    make_batch: Callable[..., BatchMessage],
    stored_batch: Callable[..., IngestBatch | None],
    stored_events: Callable[[], list[LogEvent]],
    fail_statement: Callable[[str], None],
) -> None:
    """The task fails, the row says FAILED with the error type, and the half-done write is rolled back."""
    batch = make_batch(entries=3)
    fail_statement("UPDATE ingest_batches")  # the PERSISTED update, after the INSERT ran
    with pytest.raises(RuntimeError):
        tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: batch.model_dump(mode="json")})
    row = stored_batch(batch.batch_id)
    assert row.status == "FAILED"
    assert row.error_message == "RuntimeError"
    assert stored_events() == []


def test_an_invalid_message_leaves_a_failed_row(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """With its ids present, a message that fails validation is still traceable by its batch id."""
    payload = make_batch(entries=2).model_dump(mode="json")
    payload["log_entries"][0]["level"] = "verbose"
    with pytest.raises(ValidationError):
        tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})
    row = stored_batch(uuid.UUID(payload["batch_id"]))
    assert row.status == "FAILED"
    assert row.event_count == 2
    assert row.error_message == "ValidationError"


def test_an_invalid_message_of_an_unknown_tenant_fails_with_its_validation_error(
    make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """The FAILED row is refused by the foreign key; the task still reports the ValidationError, not that."""
    payload = make_batch().model_dump(mode="json")
    payload["log_entries"][0]["level"] = "verbose"
    with pytest.raises(ValidationError):
        tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})
    assert stored_batch(uuid.UUID(payload["batch_id"])) is None
