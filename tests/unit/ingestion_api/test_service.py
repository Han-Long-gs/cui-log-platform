"""ingestion_api.service against a mock Session: what each function asks the session to do."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from cui_db import LogEvent
from cui_schemas.schemas import LogEntry
from ingestion_api.constants import DEV_TENANT_ID
from ingestion_api.service import check_db_connection, insert_log_events
from sqlalchemy import exc


def insert(session: MagicMock, *events: dict[str, Any]) -> list[LogEvent]:
    """Run insert_log_events on the given event dicts; assert one add_all call and return what it received."""
    insert_log_events([LogEntry.model_validate(event) for event in events], session)
    session.add_all.assert_called_once()
    return list(session.add_all.call_args.args[0])


def test_check_db_connection_executes_select_1(session: MagicMock) -> None:
    """The check sends exactly one statement, SELECT 1, through the session."""
    check_db_connection(session)
    session.execute.assert_called_once()
    assert str(session.execute.call_args.args[0]) == "SELECT 1"


@pytest.mark.parametrize("error", [exc.OperationalError("SELECT 1", {}, Exception()), exc.TimeoutError()])
def test_check_db_connection_propagates_errors(session: MagicMock, error: Exception) -> None:
    """Errors from the session reach the caller instead of being swallowed."""
    session.execute.side_effect = error
    with pytest.raises(type(error)):
        check_db_connection(session)


def test_insert_sets_dev_tenant_even_if_request_sends_one(
    make_event: Callable[..., dict[str, Any]], session: MagicMock
) -> None:
    """Every event gets DEV_TENANT_ID, including one whose request data carried another tenant_id."""
    logs = insert(session, make_event(), make_event(tenant_id=str(uuid.uuid4())))
    assert [log.tenant_id for log in logs] == [DEV_TENANT_ID, DEV_TENANT_ID]


def test_insert_copies_fields_and_stamps_ingested_at(
    make_event: Callable[..., dict[str, Any]], session: MagicMock
) -> None:
    """Every payload field is copied unchanged and ingested_at is an aware time taken during the call."""
    event = make_event(level="error", trace_id=str(uuid.uuid4()), custom={"user_id": 42})
    payload = LogEntry.model_validate(event)
    before = datetime.now(UTC)
    (log,) = insert(session, event)
    after = datetime.now(UTC)
    assert log.event_id == payload.event_id
    assert log.service_name == payload.service_name
    assert log.environment == payload.environment
    assert log.level == "ERROR"
    assert log.message == payload.message
    assert log.occurred_at == payload.occurred_at
    assert log.trace_id == payload.trace_id
    assert log.custom == {"user_id": 42}
    assert before <= log.ingested_at <= after


def test_insert_does_not_flush_or_commit(make_event: Callable[..., dict[str, Any]], session: MagicMock) -> None:
    """Flushing and committing are left to whoever owns the session."""
    insert(session, make_event())
    session.flush.assert_not_called()
    session.commit.assert_not_called()
