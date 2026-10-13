"""worker.tasks against the real database: running the task stores the batch through the worker's own engine."""

from collections.abc import Callable

import pytest
from cui_db import LogEvent
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
