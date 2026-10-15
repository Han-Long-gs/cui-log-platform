"""Worker database writes: the ingest_batches status row of a batch and its log_events rows.
ref:https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#postgresql-insert-on-conflict
    https://docs.sqlalchemy.org/en/21/orm/session_api.html#sqlalchemy.orm.Session.execute
    https://docs.sqlalchemy.org/en/21/orm/queryguide/dml.html#orm-bulk-insert-statements
"""

import logging
from datetime import UTC, datetime
from typing import Any

from cui_db import IngestBatch, LogEvent
from cui_messaging import BatchMessage
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def mark_batch_processing(session_factory: sessionmaker[Session], batch: BatchMessage, retry_count: int) -> bool:
    """Upsert the batch's ingest_batches row as PROCESSING with started_at now and the given retry_count, clearing an
    earlier failure, and commit. retry_count is assigned, so a redelivered message writes the same value.
    Returns False without writing if the batch is already persisted (persisted_at set), True otherwise.
    Raises a SQLAlchemy error if the write fails (e.g. database unreachable, unknown tenant)."""
    with session_factory.begin() as session:
        # check the batch is not persisted
        # (edge case: worker died right after write to db and before ack -> task requeued)
        exist_batch = session.get(IngestBatch, batch.batch_id)
        if exist_batch is not None and exist_batch.persisted_at is not None:
            return False

        insert_stmt = insert(IngestBatch).values(
            id=batch.batch_id,
            tenant_id=batch.tenant_id,
            event_count=len(batch.log_entries),
            status="PROCESSING",
            received_at=batch.received_at,
            started_at=datetime.now(UTC),
            failed_at=None,
            retry_count=retry_count,
            error_message=None,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[IngestBatch.id],
            set_={
                "status": insert_stmt.excluded.status,
                "started_at": insert_stmt.excluded.started_at,
                "failed_at": insert_stmt.excluded.failed_at,
                "error_message": insert_stmt.excluded.error_message,
                "retry_count": insert_stmt.excluded.retry_count,
            },
        )
        session.execute(upsert_stmt)
    return True


def handle_invalid_batch(session_factory: sessionmaker[Session], batch_message: dict[str, Any]) -> None:
    """Record a message that failed validation: if its raw batch_id, tenant_id and received_at are present, insert a
    FAILED ingest_batches row (event_count is the length of log_entries, 0 if it is not a list). Never raises."""
    batch_id = batch_message.get("batch_id")
    tenant_id = batch_message.get("tenant_id")
    received_at = batch_message.get("received_at")
    log_entries = batch_message.get("log_entries")
    required_fields_present = batch_id and tenant_id and received_at

    if required_fields_present:
        event_count = 0
        if isinstance(log_entries, list):
            event_count = len(log_entries)
        _record_invalid_batch(session_factory, batch_id, tenant_id, received_at, event_count, "ValidationError")


def _record_invalid_batch(
    session_factory: sessionmaker[Session],
    batch_id: str,
    tenant_id: str,
    received_at: str,
    event_count: int,
    error_msg: str,
) -> None:
    """Insert a FAILED ingest_batches row for a message that failed validation, from its raw values, and commit.
    The database parses the ids and received_at. Never raises: if the database rejects the values or cannot be
    reached, logs the (unvalidated, so repr'd and truncated) batch_id instead."""
    try:
        with session_factory.begin() as session:
            insert_stmt = insert(IngestBatch).values(
                id=batch_id,
                tenant_id=tenant_id,
                event_count=event_count,
                status="FAILED",
                received_at=received_at,
                failed_at=datetime.now(UTC),
                error_message=error_msg,
            )
            session.execute(insert_stmt)
    except Exception:
        logger.error("failed to record invalid ingest batch %.40r", batch_id)


def mark_batch_failed(session_factory: sessionmaker[Session], batch: BatchMessage, error_msg: str) -> None:
    """Set the batch's ingest_batches row to FAILED with failed_at now and the given error message, and commit.
    Updates nothing if the row does not exist. Never raises: a failed write is only logged, so it cannot hide the
    error that made the batch fail."""
    try:
        with session_factory.begin() as session:
            update_stmt = (
                update(IngestBatch)
                .where(IngestBatch.id == batch.batch_id)
                .values(status="FAILED", failed_at=datetime.now(UTC), error_message=error_msg)
            )
            session.execute(update_stmt)
    except Exception:
        logger.error("failed to mark ingest batch %s as FAILED", batch.batch_id)


def ingest_batch_to_pg(session_factory: sessionmaker[Session], batch: BatchMessage) -> None:
    """Insert one log_events row per entry and set the batch's ingest_batches row to PERSISTED in one transaction.
    Rows get the batch's tenant_id and received_at (as ingested_at); events the tenant already has are skipped.
    Raises a SQLAlchemy error and writes nothing if the database rejects the batch (e.g. unknown tenant)."""
    with session_factory.begin() as session:
        log_events = []
        for entry in batch.log_entries:
            log_events.append(
                {
                    "event_id": entry.event_id,
                    "tenant_id": batch.tenant_id,
                    "service_name": entry.service_name,
                    "environment": entry.environment,
                    "level": entry.level,
                    "message": entry.message,
                    "occurred_at": entry.occurred_at,
                    "ingested_at": batch.received_at,
                    "trace_id": entry.trace_id,
                    "custom": entry.custom,
                }
            )
        session.execute(insert(LogEvent).on_conflict_do_nothing(constraint="uix_event"), log_events)

        update_stmt = (
            update(IngestBatch)
            .where(IngestBatch.id == batch.batch_id)
            .values(status="PERSISTED", persisted_at=datetime.now(UTC))
        )
        session.execute(update_stmt)
