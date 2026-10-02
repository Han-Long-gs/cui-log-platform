"""Business logic"""

from datetime import UTC, datetime

from cui_db import LogEvent
from sqlalchemy.orm import Session

from .db import DEV_TENANT_ID
from .schemas import LogEventPayload


def insert_log_events(logs: list[LogEventPayload], session: Session) -> None:
    """Convert each log into a LogEvent and add it to the session without flushing or committing.
    Sets tenant_id to DEV_TENANT_ID and ingested_at to the current UTC time for each event."""
    orm_logs = []
    for log in logs:
        orm_log = LogEvent(
            event_id=log.event_id,
            tenant_id=DEV_TENANT_ID,
            service_name=log.service_name,
            environment=log.environment,
            level=log.level,
            message=log.message,
            occurred_at=log.occurred_at,
            ingested_at=datetime.now(UTC),
            trace_id=log.trace_id,
            custom=log.custom,
        )
        orm_logs.append(orm_log)
    session.add_all(orm_logs)
