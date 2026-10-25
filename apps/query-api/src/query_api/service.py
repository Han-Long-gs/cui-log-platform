import base64
import uuid
from dataclasses import dataclass
from datetime import datetime

from cui_db import LogEvent
from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session


@dataclass
class LogPage:
    """One page of log events, newest first, and the cursor of the next page (None when this is the last page)."""

    next_cursor: str | None
    items: list[LogEvent]


def encode_cursor(occurred_at: datetime, row_id: int) -> str:
    """Encode (occurred_at, id) as an opaque URL-safe base64 cursor string."""
    raw_str = occurred_at.isoformat() + "|" + str(row_id)
    raw_bytes = raw_str.encode()
    urlsafe_bytes = base64.urlsafe_b64encode(raw_bytes)
    return urlsafe_bytes.decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """Decode a cursor made by encode_cursor back to (occurred_at, id).
    Raise ValueError if it is not base64 of '<ISO time with timezone>|<integer id>'."""
    try:
        standard_b64_str = cursor.replace("-", "+").replace("_", "/")
        decoded_cursor_str = base64.b64decode(standard_b64_str, validate=True).decode()

        cursor_parts = decoded_cursor_str.split("|")
        if len(cursor_parts) != 2:
            raise ValueError("Too few or too many cursor parts")

        timestamp = datetime.fromisoformat(cursor_parts[0])
        if timestamp.tzinfo is None:
            raise ValueError("Timezone was unset")
        row_id = int(cursor_parts[1])
        return (timestamp, row_id)
    except ValueError as ex:
        raise ValueError("Cursor cannot be decoded") from ex


def fetch_log_events(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    service_name: str | None,
    environment: str | None,
    level: str | None,
    start: datetime | None,
    end: datetime | None,
    cursor: tuple[datetime, int] | None,
    limit: int,
) -> LogPage:
    """Return at most limit events of tenant_id matching every non-None filter, ordered by (occurred_at, id) DESC.
    start is inclusive, end exclusive; cursor returns only rows strictly after that position in this order.
    next_cursor is set only when more rows follow."""
    # init return values
    next_cursor = None
    items = []

    # edge case: start == end, return early to save a roundtrip to db
    if start == end and start is not None:
        return LogPage(next_cursor=next_cursor, items=items)

    # build iterable to go into where()
    where_clause = [LogEvent.tenant_id == tenant_id]
    if service_name:
        where_clause.append(LogEvent.service_name == service_name)
    if environment:
        where_clause.append(LogEvent.environment == environment)
    if level:
        where_clause.append(LogEvent.level == level)
    if start:
        where_clause.append(LogEvent.occurred_at >= start)
    if end:
        where_clause.append(LogEvent.occurred_at < end)
    if cursor:
        last_row_timestamp, last_row_id = cursor
        where_clause.append(
            or_(
                and_(LogEvent.occurred_at == last_row_timestamp, LogEvent.id < last_row_id),
                LogEvent.occurred_at < last_row_timestamp,
            )
        )

    stmt = (
        select(LogEvent).where(*where_clause).order_by(LogEvent.occurred_at.desc(), LogEvent.id.desc()).limit(limit + 1)
    )
    rows = session.execute(stmt).scalars().all()

    # build next cursor iff the next page exists
    if len(rows) > limit:
        next_cursor = encode_cursor(
            occurred_at=rows[-2].occurred_at, row_id=rows[-2].id
        )  # validation layer ensures limit > 0 -> len(rows) >= 2
        items = rows[:-1]
    else:
        items = rows
    return LogPage(next_cursor=next_cursor, items=items)


def check_db_connection(session: Session) -> None:
    """Execute SELECT 1 on the session; raises a SQLAlchemy error if the database cannot be reached."""
    query = text("SELECT 1")
    session.execute(query)
