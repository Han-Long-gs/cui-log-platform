"""worker.tasks: the ingest_batch task is registered under the agreed name, takes what the API publishes, validates it
again and drives the batch through the service functions: mark PROCESSING, write, mark FAILED on error; skip a batch
that is already persisted; record a FAILED row for an invalid message when its ids are present. Nothing here needs a
broker or a database: the four worker.service functions are mocks (their own tests are in tests/integration/worker)."""

import importlib
import json
import logging
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock, call, create_autospec

import pytest
from celery import Celery
from cui_config import settings
from cui_messaging import INGEST_BATCH_ARG_NAME, INGEST_BATCH_TASK_NAME, BatchMessage, send_batch_msg
from pydantic import ValidationError
from worker import service as worker_service
from worker import tasks

BROKER_URL = "amqp://u:p@test-broker:5672//"


@pytest.fixture
def payload(make_batch: Callable[..., BatchMessage]) -> dict[str, Any]:
    """A three-entry batch the way it arrives from the broker: plain JSON types."""
    return make_batch(entries=3).model_dump(mode="json")


SERVICE_FUNCTIONS = ["mark_batch_processing", "ingest_batch_to_pg", "mark_batch_failed", "record_invalid_batch"]


@pytest.fixture(autouse=True)
def service(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the service functions the task calls, so no test in this file touches the database.
    All mocks hang off one parent, so mock_calls shows their order; mark_batch_processing answers "go on"."""
    parent = MagicMock()
    for name in SERVICE_FUNCTIONS:
        mock = create_autospec(getattr(worker_service, name))
        parent.attach_mock(mock, name)
        monkeypatch.setattr(tasks, name, mock)
    parent.mark_batch_processing.return_value = True
    return parent


def run(payload: dict[str, Any]) -> None:
    """Call the task the way Celery does, with the message under the agreed keyword."""
    tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})


@pytest.fixture
def tasks_with_broker(monkeypatch: pytest.MonkeyPatch) -> Generator[Any]:
    """worker.tasks re-imported with BROKER_URL in the settings; re-imported with the real settings afterwards.
    The Celery app is built when the module is imported, so changing the setting needs a reload."""
    monkeypatch.setattr(settings, "broker_url", BROKER_URL)
    yield importlib.reload(tasks)
    monkeypatch.undo()
    importlib.reload(tasks)


# --- the worker's Celery app ---


def test_worker_app_connects_to_the_configured_broker(tasks_with_broker: Any) -> None:
    """The broker URL comes from the settings."""
    assert tasks_with_broker.celery_app.conf.broker_url == BROKER_URL


def test_worker_app_is_built_by_the_shared_factory() -> None:
    """The ack settings are set in cui_messaging.make_celery_app, so they only apply if the worker uses it."""
    conf = tasks.celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1


def test_task_is_registered_under_the_agreed_name() -> None:
    """A message naming a task the worker has not registered is discarded."""
    assert tasks.ingest_batch.name == INGEST_BATCH_TASK_NAME
    assert INGEST_BATCH_TASK_NAME in tasks.celery_app.tasks


# --- ingest_batch: a valid message ---


def test_task_accepts_what_the_api_publishes(make_batch: Callable[..., BatchMessage]) -> None:
    """The task name and keyword arguments sent by send_batch_msg fit the task after a trip through JSON."""
    publisher = create_autospec(Celery, instance=True)
    send_batch_msg(publisher, make_batch())
    published = publisher.send_task.call_args
    assert published.args == (tasks.ingest_batch.name,)
    tasks.ingest_batch(**json.loads(json.dumps(published.kwargs["kwargs"])))


def test_task_logs_which_batch_it_received(payload: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    """One log line carries the batch id, the tenant id and the number of entries."""
    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        run(payload)
    assert f"batch_id: {payload['batch_id']}" in caplog.text
    assert f"tenant_id: {payload['tenant_id']}" in caplog.text
    assert "num_entries: 3" in caplog.text


def test_task_marks_the_batch_processing_then_writes_it(payload: dict[str, Any], service: MagicMock) -> None:
    """Both steps get the validated batch and the worker's session factory, in this order, and nothing else runs."""
    run(payload)
    batch = BatchMessage.model_validate(payload)
    assert service.mock_calls == [
        call.mark_batch_processing(tasks.session_factory, batch),
        call.ingest_batch_to_pg(tasks.session_factory, batch),
    ]


def test_task_skips_a_batch_that_is_already_persisted(payload: dict[str, Any], service: MagicMock) -> None:
    """A redelivered batch whose write already committed is not written again, and the task succeeds."""
    service.mark_batch_processing.return_value = False
    run(payload)
    service.ingest_batch_to_pg.assert_not_called()
    service.mark_batch_failed.assert_not_called()


def test_task_logs_the_skipped_batch(
    payload: dict[str, Any], service: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """The log line is the only trace that a redelivery happened."""
    service.mark_batch_processing.return_value = False
    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        run(payload)
    assert f"{payload['batch_id']} is already persisted" in caplog.text


def test_task_does_not_write_when_marking_processing_fails(payload: dict[str, Any], service: MagicMock) -> None:
    """If the first step cannot reach the database there is no row to mark FAILED: the error goes straight out."""
    service.mark_batch_processing.side_effect = ConnectionError("database is down")
    with pytest.raises(ConnectionError):
        run(payload)
    service.ingest_batch_to_pg.assert_not_called()
    service.mark_batch_failed.assert_not_called()


# --- ingest_batch: the write fails ---


def test_task_fails_with_the_write_error(payload: dict[str, Any], service: MagicMock) -> None:
    """An error from the write is not swallowed: the task fails with it."""
    service.ingest_batch_to_pg.side_effect = RuntimeError("database is down")
    with pytest.raises(RuntimeError, match="database is down"):
        run(payload)


def test_a_failed_write_marks_the_batch_failed_with_the_error_type(payload: dict[str, Any], service: MagicMock) -> None:
    """error_message is the exception's class name only: its text can hold the batch's log bodies."""
    service.ingest_batch_to_pg.side_effect = RuntimeError("message='card 4111 1111'")
    with pytest.raises(RuntimeError):
        run(payload)
    service.mark_batch_failed.assert_called_once_with(
        tasks.session_factory, BatchMessage.model_validate(payload), "RuntimeError"
    )


def test_a_failed_write_is_logged_without_the_error_text(
    payload: dict[str, Any], service: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """The log names the batch and the error type, never the exception text."""
    service.ingest_batch_to_pg.side_effect = RuntimeError("message='card 4111 1111'")
    with caplog.at_level(logging.INFO, logger=tasks.logger.name), pytest.raises(RuntimeError):
        run(payload)
    assert payload["batch_id"] in caplog.text
    assert "RuntimeError" in caplog.text
    assert "card 4111 1111" not in caplog.text


def test_the_write_error_survives_a_failure_to_mark_the_batch_failed(
    payload: dict[str, Any], service: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Marking FAILED usually fails for the same reason the write did; the task still reports the write's error."""
    service.ingest_batch_to_pg.side_effect = RuntimeError("write failed")
    service.mark_batch_failed.side_effect = ConnectionError("database is down")
    with caplog.at_level(logging.INFO, logger=tasks.logger.name), pytest.raises(RuntimeError, match="write failed"):
        run(payload)
    assert f"failed to mark ingest batch {payload['batch_id']} as FAILED" in caplog.text


# --- ingest_batch: an invalid message ---


@pytest.mark.parametrize("field", ["batch_id", "tenant_id", "received_at"])
def test_task_rejects_a_message_missing_an_id_field(payload: dict[str, Any], service: MagicMock, field: str) -> None:
    """Without all three fields the worker validates, fails and writes nothing at all."""
    del payload[field]
    with pytest.raises(ValidationError, match=field):
        run(payload)
    assert service.mock_calls == []


def test_task_rejects_a_message_with_an_invalid_entry(payload: dict[str, Any], service: MagicMock) -> None:
    """Every log entry is validated again too: one bad entry fails the whole batch before any processing."""
    payload["log_entries"][1]["level"] = "verbose"
    with pytest.raises(ValidationError, match="level"):
        run(payload)
    service.mark_batch_processing.assert_not_called()
    service.ingest_batch_to_pg.assert_not_called()


def test_an_invalid_message_with_its_ids_is_recorded_as_failed(payload: dict[str, Any], service: MagicMock) -> None:
    """The raw ids, received_at and entry count go to record_invalid_batch, with the error type as the reason."""
    payload["log_entries"][1]["level"] = "verbose"
    with pytest.raises(ValidationError):
        run(payload)
    service.record_invalid_batch.assert_called_once_with(
        tasks.session_factory,
        payload["batch_id"],
        payload["tenant_id"],
        payload["received_at"],
        3,
        "ValidationError",
    )


def test_an_invalid_message_whose_entries_are_not_a_list_has_0_events(
    payload: dict[str, Any], service: MagicMock
) -> None:
    """event_count falls back to 0 when log_entries cannot be counted."""
    payload["log_entries"] = "not a list"
    with pytest.raises(ValidationError):
        run(payload)
    assert service.record_invalid_batch.call_args.args[4] == 0


def test_the_validation_error_survives_a_failure_to_record_the_batch(
    payload: dict[str, Any], service: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """If the FAILED row cannot be written (e.g. unknown tenant) the task still fails with the ValidationError."""
    payload["log_entries"][1]["level"] = "verbose"
    service.record_invalid_batch.side_effect = ConnectionError("foreign key violation")
    with caplog.at_level(logging.INFO, logger=tasks.logger.name), pytest.raises(ValidationError):
        run(payload)
    assert f"failed to record invalid ingest batch {payload['batch_id']}" in caplog.text


def test_an_invalid_message_is_logged_without_its_content(
    payload: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """The worker's own log line about a rejected message does not repeat the message."""
    payload["log_entries"][1] |= {"level": "verbose", "message": "card 4111 1111"}
    with caplog.at_level(logging.INFO, logger=tasks.logger.name), pytest.raises(ValidationError):
        run(payload)
    assert "failed to validate the batch message" in caplog.text
    assert "card 4111 1111" not in caplog.text
