"""The whole write path: POST /v1/logs -> test RabbitMQ -> `celery worker` subprocess -> Postgres.
Each test starts its own worker, so a test can also publish while no worker is running."""

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cui_db import DEV_TENANT_ID, IngestBatch, LogEvent
from cui_messaging import INGEST_BATCH_ARG_NAME, INGEST_BATCH_TASK_NAME
from worker import tasks

if TYPE_CHECKING:
    from tests.integration.pipeline.conftest import WorkerProcess

Rows = list[IngestBatch]
WaitForBatches = Callable[[Callable[[Rows], bool]], Rows]


def all_persisted(count: int) -> Callable[[Rows], bool]:
    """Condition: there are count rows and every one is PERSISTED."""
    return lambda rows: len(rows) == count and all(row.status == "PERSISTED" for row in rows)


def test_a_posted_batch_is_stored_as_one_persisted_batch(
    worker: WorkerProcess,
    post_events: Callable[[list[dict[str, Any]]], str],
    make_event: Callable[..., dict[str, Any]],
    wait_for_batches: WaitForBatches,
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """One request -> one message -> one task -> one ingest_batches row (PERSISTED, no retries) and one log_events
    row per event, stored under the dev tenant."""
    worker.start()
    events = [make_event(), make_event(), make_event()]

    batch_id = post_events(events)
    [batch] = wait_for_batches(all_persisted(1))

    assert str(batch.id) == batch_id
    assert batch.retry_count == 0
    assert batch.event_count == 3
    stored = stored_events()
    assert sorted(str(row.event_id) for row in stored) == sorted(event["event_id"] for event in events)
    for row in stored:
        assert row.tenant_id == DEV_TENANT_ID


def test_the_same_events_posted_twice_are_stored_once(
    worker: WorkerProcess,
    post_events: Callable[[list[dict[str, Any]]], str],
    make_event: Callable[..., dict[str, Any]],
    wait_for_batches: WaitForBatches,
    stored_events: Callable[[], list[LogEvent]],
) -> None:
    """Two requests with the same event_ids are two batches, both PERSISTED, but each event has one log_events row."""
    worker.start()
    events = [make_event(), make_event(), make_event()]

    first_id = post_events(events)
    second_id = post_events(events)
    batches = wait_for_batches(all_persisted(2))

    assert first_id != second_id
    assert sorted(str(batch.id) for batch in batches) == sorted([first_id, second_id])
    assert len(stored_events()) == 3


def test_batches_wait_in_the_queue_until_a_worker_starts(
    worker: WorkerProcess,
    post_events: Callable[[list[dict[str, Any]]], str],
    make_event: Callable[..., dict[str, Any]],
    wait_for_batches: WaitForBatches,
    stored_batches: Callable[[], Rows],
    stored_events: Callable[[], list[LogEvent]],
    queued_messages: Callable[[], int],
) -> None:
    """With no worker running, accepted batches stay in the queue and nothing is written; once a worker starts it
    consumes them all."""
    for _ in range(3):
        post_events([make_event()])

    assert queued_messages() == 3
    assert stored_batches() == []

    worker.start()
    wait_for_batches(all_persisted(3))

    assert queued_messages() == 0
    assert len(stored_events()) == 3


def test_an_invalid_message_is_marked_failed_and_not_retried(
    worker: WorkerProcess, wait_for_batches: WaitForBatches, stored_events: Callable[[], list[LogEvent]]
) -> None:
    """A message that skips the API's validation (invalid level) gets a FAILED row with the ValidationError, and the
    worker receives it exactly once."""
    worker.start()
    batch_id = str(uuid.uuid4())
    message = {
        "batch_id": batch_id,
        "tenant_id": str(DEV_TENANT_ID),
        "received_at": "2026-10-15T10:00:00-07:00",
        "log_entries": [{"level": "verbose"}],
    }

    result = tasks.celery_app.send_task(INGEST_BATCH_TASK_NAME, kwargs={INGEST_BATCH_ARG_NAME: message})
    [batch] = wait_for_batches(lambda rows: len(rows) == 1 and rows[0].status == "FAILED")

    assert str(batch.id) == batch_id
    assert batch.error_message == "ValidationError"
    assert stored_events() == []
    worker.wait_for_log(f"ingest_batch[{result.id}] raised unexpected")
    log = worker.log()
    assert log.count(f"ingest_batch[{result.id}] received") == 1
    assert f"ingest_batch[{result.id}] retry" not in log
