"""Service Layer of celery worker
ref:https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#postgresql-insert-on-conflict
    https://docs.sqlalchemy.org/en/21/orm/session_api.html#sqlalchemy.orm.Session.execute
    https://docs.sqlalchemy.org/en/21/orm/queryguide/dml.html#orm-bulk-insert-statements
"""

from cui_db import LogEvent
from cui_messaging import BatchMessage
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker


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
