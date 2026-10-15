"""worker.service against the real database: the ingest_batches row of a batch goes PROCESSING -> PERSISTED or FAILED,
each step in the transaction it belongs to; a redelivered or retried batch is picked up again (with the attempt's
retry_count) unless it is already persisted; recording a failure never raises."""

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from cui_db import IngestBatch, LogEvent
from cui_messaging import BatchMessage
from sqlalchemy import exc
from worker import db
from worker.service import handle_invalid_batch, ingest_batch_to_pg, mark_batch_failed, mark_batch_processing

UNKNOWN_TENANT = "33333333-3333-3333-3333-333333333333"


class Window:
    """The time span of a with-block, to check that a timestamp was taken inside it."""

    start: datetime
    end: datetime


@contextmanager
def window() -> Iterator[Window]:
    """Record the current time before and after the block."""
    span = Window()
    span.start = datetime.now(UTC)
    yield span
    span.end = datetime.now(UTC)


# --- mark_batch_processing ---


def test_a_new_batch_gets_a_processing_row(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """Every column comes from the message, except started_at, which is when the worker began."""
    batch = make_batch(entries=3)
    with window() as span:
        assert mark_batch_processing(db.session_factory, batch, 0) is True
    row = stored_batch(batch.batch_id)
    assert row.tenant_id == batch.tenant_id
    assert row.event_count == 3
    assert row.status == "PROCESSING"
    assert row.received_at == batch.received_at
    assert span.start <= row.started_at <= span.end
    assert row.persisted_at is None
    assert row.failed_at is None
    assert row.error_message is None
    assert row.retry_count == 0


def test_processing_is_committed_before_the_write(
    batch_tenant: None,
    make_batch: Callable[..., BatchMessage],
    stored_batch: Callable[..., IngestBatch | None],
    fail_statement: Callable[[str], None],
) -> None:
    """A write that fails afterwards does not take the PROCESSING row down with it."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 0)
    fail_statement("INSERT INTO log_events")
    with pytest.raises(RuntimeError):
        ingest_batch_to_pg(db.session_factory, batch)
    assert stored_batch(batch.batch_id).status == "PROCESSING"


def test_a_redelivered_processing_batch_starts_again(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """The worker died before the write committed: the row is reused and started_at becomes the new start."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 0)
    first_start = stored_batch(batch.batch_id).started_at
    assert mark_batch_processing(db.session_factory, batch, 0) is True
    row = stored_batch(batch.batch_id)
    assert row.status == "PROCESSING"
    assert row.started_at > first_start


def test_a_redelivered_failed_batch_loses_its_old_failure(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """A PROCESSING row never carries the failure of an earlier attempt."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 0)
    mark_batch_failed(db.session_factory, batch, "OperationalError")
    mark_batch_processing(db.session_factory, batch, 0)
    row = stored_batch(batch.batch_id)
    assert row.status == "PROCESSING"
    assert row.failed_at is None
    assert row.error_message is None


def test_a_redelivery_keeps_what_the_first_delivery_stored(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """Only status and the attempt's timestamps are updated; tenant, size and received_at stay as first stored."""
    batch = make_batch(entries=3)
    mark_batch_processing(db.session_factory, batch, 0)
    changed = batch.model_copy(update={"received_at": datetime(2030, 1, 1, tzinfo=UTC), "log_entries": []})
    mark_batch_processing(db.session_factory, changed, 0)
    row = stored_batch(batch.batch_id)
    assert row.received_at == batch.received_at
    assert row.event_count == 3


def test_the_retry_count_is_stored(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """A batch first written on its third retry says so."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 3)
    assert stored_batch(batch.batch_id).retry_count == 3


def test_a_retry_overwrites_the_retry_count(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """The row of an earlier attempt takes the new attempt's count."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 0)
    mark_batch_processing(db.session_factory, batch, 4)
    assert stored_batch(batch.batch_id).retry_count == 4


def test_a_redelivered_attempt_does_not_add_to_the_retry_count(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """The count is assigned, not incremented: the broker redelivering the same attempt writes the same value."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 2)
    mark_batch_processing(db.session_factory, batch, 2)
    assert stored_batch(batch.batch_id).retry_count == 2


def test_a_persisted_batch_is_not_touched_again(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """The worker died after the write committed: the answer is 'do not process', and the first timestamps stay."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 0)
    ingest_batch_to_pg(db.session_factory, batch)
    before = stored_batch(batch.batch_id)
    assert mark_batch_processing(db.session_factory, batch, 0) is False
    after = stored_batch(batch.batch_id)
    assert after.status == "PERSISTED"
    assert after.started_at == before.started_at
    assert after.persisted_at == before.persisted_at


def test_a_batch_of_an_unknown_tenant_gets_no_row(
    make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """ingest_batches.tenant_id is a foreign key too: the first step already fails."""
    batch = make_batch()
    with pytest.raises(exc.IntegrityError):
        mark_batch_processing(db.session_factory, batch, 0)
    assert stored_batch(batch.batch_id) is None


# --- ingest_batch_to_pg: PERSISTED ---


def test_a_written_batch_is_persisted(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """After the write the row says PERSISTED, with the time the write happened."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 0)
    with window() as span:
        ingest_batch_to_pg(db.session_factory, batch)
    row = stored_batch(batch.batch_id)
    assert row.status == "PERSISTED"
    assert span.start <= row.persisted_at <= span.end
    assert row.started_at <= row.persisted_at


def test_persisted_and_the_rows_commit_together(
    batch_tenant: None,
    make_batch: Callable[..., BatchMessage],
    stored_batch: Callable[..., IngestBatch | None],
    stored_events: Callable[[], list[LogEvent]],
    fail_statement: Callable[[str], None],
) -> None:
    """If marking PERSISTED fails after the INSERT ran, the inserted rows are rolled back with it."""
    batch = make_batch(entries=3)
    mark_batch_processing(db.session_factory, batch, 0)
    fail_statement("UPDATE ingest_batches")
    with pytest.raises(RuntimeError):
        ingest_batch_to_pg(db.session_factory, batch)
    assert stored_events() == []
    assert stored_batch(batch.batch_id).status == "PROCESSING"


# --- mark_batch_failed ---


def test_a_failed_batch_records_when_and_why(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """FAILED with the time and the given message; the start time of the attempt is kept."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 0)
    started_at = stored_batch(batch.batch_id).started_at
    with window() as span:
        mark_batch_failed(db.session_factory, batch, "OperationalError")
    row = stored_batch(batch.batch_id)
    assert row.status == "FAILED"
    assert span.start <= row.failed_at <= span.end
    assert row.error_message == "OperationalError"
    assert row.started_at == started_at
    assert row.persisted_at is None


def test_marking_a_batch_without_a_row_failed_changes_nothing(
    make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """mark_batch_processing never wrote the row (e.g. unknown tenant): the update finds nothing and creates nothing."""
    batch = make_batch()
    mark_batch_failed(db.session_factory, batch, "IntegrityError")
    assert stored_batch(batch.batch_id) is None


def test_a_failure_to_mark_failed_is_logged_not_raised(
    batch_tenant: None,
    make_batch: Callable[..., BatchMessage],
    stored_batch: Callable[..., IngestBatch | None],
    fail_statement: Callable[..., None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The caller is already giving up on the batch; an error here must not replace the one that made it fail."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 0)
    fail_statement("UPDATE ingest_batches")
    with caplog.at_level(logging.ERROR, logger="worker.service"):
        mark_batch_failed(db.session_factory, batch, "OperationalError")
    assert f"failed to mark ingest batch {batch.batch_id} as FAILED" in caplog.text
    assert stored_batch(batch.batch_id).status == "PROCESSING"


# --- handle_invalid_batch ---


def test_an_invalid_batch_is_recorded_from_raw_strings(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """The ids and received_at arrive as unvalidated strings; Postgres parses them into the row."""
    raw = make_batch(entries=4).model_dump(mode="json")
    with window() as span:
        handle_invalid_batch(db.session_factory, raw)
    row = stored_batch(uuid.UUID(raw["batch_id"]))
    assert row.tenant_id == uuid.UUID(raw["tenant_id"])
    assert row.received_at == datetime.fromisoformat(raw["received_at"])
    assert row.event_count == 4
    assert row.status == "FAILED"
    assert span.start <= row.failed_at <= span.end
    assert row.error_message == "ValidationError"
    assert row.started_at is None


def test_an_invalid_batch_whose_entries_are_not_a_list_has_0_events(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """event_count falls back to 0 when log_entries cannot be counted."""
    raw = make_batch().model_dump(mode="json") | {"log_entries": "not a list"}
    handle_invalid_batch(db.session_factory, raw)
    assert stored_batch(uuid.UUID(raw["batch_id"])).event_count == 0


@pytest.mark.parametrize("field", ["batch_id", "tenant_id", "received_at"])
def test_an_invalid_batch_missing_an_id_field_is_not_recorded(
    batch_tenant: None,
    make_batch: Callable[..., BatchMessage],
    stored_batch: Callable[..., IngestBatch | None],
    field: str,
) -> None:
    """Without all three there is no row to write; nothing is attempted."""
    raw = make_batch().model_dump(mode="json")
    del raw[field]
    handle_invalid_batch(db.session_factory, raw)
    assert stored_batch(make_batch().batch_id) is None


def test_an_invalid_batch_of_an_unknown_tenant_is_logged_not_raised(
    make_batch: Callable[..., BatchMessage],
    stored_batch: Callable[..., IngestBatch | None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The tenant id was never validated; the foreign key stops it, and the refusal only goes to the log."""
    raw = make_batch().model_dump(mode="json") | {"tenant_id": UNKNOWN_TENANT}
    with caplog.at_level(logging.ERROR, logger="worker.service"):
        handle_invalid_batch(db.session_factory, raw)
    assert stored_batch(uuid.UUID(raw["batch_id"])) is None
    assert f"failed to record invalid ingest batch '{raw['batch_id']}'" in caplog.text


@pytest.mark.parametrize("field", ["batch_id", "tenant_id", "received_at"])
def test_an_invalid_batch_with_a_malformed_value_is_not_recorded(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], field: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A value Postgres cannot parse as a uuid / timestamp is rejected by the database; nothing is raised."""
    raw = make_batch().model_dump(mode="json") | {field: "not-a-value"}
    with caplog.at_level(logging.ERROR, logger="worker.service"):
        handle_invalid_batch(db.session_factory, raw)
    assert "failed to record invalid ingest batch" in caplog.text


def test_an_invalid_message_cannot_overwrite_a_stored_batch(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], stored_batch: Callable[..., IngestBatch | None]
) -> None:
    """A bad message reusing the id of a persisted batch is refused and the stored row stays as it was."""
    batch = make_batch()
    mark_batch_processing(db.session_factory, batch, 0)
    ingest_batch_to_pg(db.session_factory, batch)
    handle_invalid_batch(db.session_factory, batch.model_dump(mode="json"))
    assert stored_batch(batch.batch_id).status == "PERSISTED"


def test_an_unvalidated_batch_id_cannot_forge_or_flood_the_log(
    batch_tenant: None, make_batch: Callable[..., BatchMessage], caplog: pytest.LogCaptureFixture
) -> None:
    """The raw batch_id is logged repr'd (a newline stays a literal \\n) and cut to 40 characters."""
    forged = "x\n[ERROR] tenant deleted" + "a" * 1000
    raw = make_batch().model_dump(mode="json") | {"batch_id": forged}
    with caplog.at_level(logging.ERROR, logger="worker.service"):
        handle_invalid_batch(db.session_factory, raw)
    message = caplog.records[-1].getMessage()
    assert "\n" not in message
    assert "\\n[ERROR]" in message
    assert len(message) <= len("failed to record invalid ingest batch ") + 40
