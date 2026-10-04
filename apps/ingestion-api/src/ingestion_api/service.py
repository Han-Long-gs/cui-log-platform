"""Business logic
ref:https://docs.sqlalchemy.org/en/20/core/sqlelement.html#sqlalchemy.sql.expression.text
"""

from datetime import UTC, datetime

from cui_db import LogEvent
from cui_schemas.schemas import LogEntry
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import DEV_TENANT_ID


def insert_log_events(logs: list[LogEntry], session: Session) -> None:
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


def check_db_connection(session: Session) -> None:
    """Execute SELECT 1 on the session; raises a SQLAlchemy error if the database cannot be reached."""
    query = text("SELECT 1")
    session.execute(query)
