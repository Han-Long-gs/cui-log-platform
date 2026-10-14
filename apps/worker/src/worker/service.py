"""Service Layer of celery worker
ref:https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#postgresql-insert-on-conflict
    https://docs.sqlalchemy.org/en/21/orm/session_api.html#sqlalchemy.orm.Session.execute
    https://docs.sqlalchemy.org/en/21/orm/queryguide/dml.html#orm-bulk-insert-statements
"""

from datetime import UTC, datetime

from cui_db import IngestBatch, LogEvent
from cui_messaging import BatchMessage
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker


def mark_batch_processing(session_factory: sessionmaker[Session], batch: BatchMessage) -> bool:
    """upsert the ingest batch that is processing"""
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
            error_message=None,
        )
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[IngestBatch.id],
            set_={
                "status": insert_stmt.excluded.status,
                "started_at": insert_stmt.excluded.started_at,
                "failed_at": insert_stmt.excluded.failed_at,
                "error_message": insert_stmt.excluded.error_message,
            },
        )
        session.execute(upsert_stmt)
    return True


def record_invalid_batch(
    session_factory: sessionmaker[Session],
    batch_id: str,
    tenant_id: str,
    received_at: str,
    event_count: str,
    error_msg: str,
) -> None:
    """insert the ingest batch that failed the validation"""
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


def mark_batch_failed(session_factory: sessionmaker[Session], batch: BatchMessage, error_msg: str) -> None:
    """update the batch status to failed"""
    with session_factory.begin() as session:
        update_stmt = (
            update(IngestBatch)
            .where(IngestBatch.id == batch.batch_id)
            .values(status="FAILED", failed_at=datetime.now(UTC), error_message=error_msg)
        )
        session.execute(update_stmt)


def ingest_batch_to_pg(session_factory: sessionmaker[Session], batch: BatchMessage) -> None:
    """Insert one log_events row per entry of the batch in a single statement and transaction, then commit.
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
