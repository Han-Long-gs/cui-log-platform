"""worker.tasks: the ingest_batch task is registered under the agreed name, takes what the API publishes, validates it
again and drives the batch through the service functions: mark PROCESSING with the retry count, write; retry a
transient error with backoff, mark FAILED on a permanent one or when the retries run out; skip a batch that is already
persisted; hand an invalid message to handle_invalid_batch. Nothing here needs a broker or a database: the four
worker.service functions are mocks (their own tests are in tests/integration/worker). Retries are run with
Task.apply(), which executes them eagerly in this process and ignores the countdown."""

import importlib
import json
import logging
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock, call, create_autospec

import pytest
from celery import Celery
from celery.exceptions import Retry
from celery.result import EagerResult
from cui_config import settings
from cui_messaging import INGEST_BATCH_ARG_NAME, INGEST_BATCH_TASK_NAME, BatchMessage, send_batch_msg
from pydantic import ValidationError
from sqlalchemy import exc
from worker import service as worker_service
from worker import tasks

BROKER_URL = "amqp://u:p@test-broker:5672//"


@pytest.fixture
def payload(make_batch: Callable[..., BatchMessage]) -> dict[str, Any]:
    """A three-entry batch the way it arrives from the broker: plain JSON types."""
    return make_batch(entries=3).model_dump(mode="json")


SERVICE_FUNCTIONS = ["mark_batch_processing", "ingest_batch_to_pg", "mark_batch_failed", "handle_invalid_batch"]


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


def apply(payload: dict[str, Any], retries: int = 0) -> EagerResult:
    """Run the task inside a Celery request whose retry count is `retries`; a retry runs again at once, in-process."""
    return tasks.ingest_batch.apply(kwargs={INGEST_BATCH_ARG_NAME: payload}, retries=retries)


def operational_error() -> exc.OperationalError:
    """What SQLAlchemy raises when Postgres cannot be reached or drops the connection."""
    return exc.OperationalError("INSERT INTO log_events ...", {}, Exception("server closed the connection"))


def integrity_error() -> exc.IntegrityError:
    """What SQLAlchemy raises when a constraint rejects the row, e.g. an unknown tenant."""
    return exc.IntegrityError("INSERT INTO ingest_batches ...", {}, Exception("foreign key violation"))


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
        call.mark_batch_processing(tasks.session_factory, batch, 0),
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
    """A permanent error in the first step stops the task before the write, and the task fails with it."""
    service.mark_batch_processing.side_effect = integrity_error()
    with pytest.raises(exc.IntegrityError):
        run(payload)
    service.ingest_batch_to_pg.assert_not_called()


def test_a_permanent_error_marking_processing_marks_the_batch_failed(
    payload: dict[str, Any], service: MagicMock
) -> None:
    """The same give-up path as a failed write; if the row was never written, the update simply finds nothing."""
    service.mark_batch_processing.side_effect = integrity_error()
    with pytest.raises(exc.IntegrityError):
        run(payload)
    service.mark_batch_failed.assert_called_once_with(
        tasks.session_factory, BatchMessage.model_validate(payload), "IntegrityError"
    )


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


# --- ingest_batch: an invalid message ---


@pytest.mark.parametrize("field", ["batch_id", "tenant_id", "received_at"])
def test_task_rejects_a_message_missing_an_id_field(payload: dict[str, Any], service: MagicMock, field: str) -> None:
    """Without all three fields validation fails; only handle_invalid_batch runs (it finds nothing to write)."""
    del payload[field]
    with pytest.raises(ValidationError, match=field):
        run(payload)
    assert service.mock_calls == [call.handle_invalid_batch(tasks.session_factory, payload)]


def test_task_rejects_a_message_with_an_invalid_entry(payload: dict[str, Any], service: MagicMock) -> None:
    """Every log entry is validated again too: one bad entry fails the whole batch before any processing."""
    payload["log_entries"][1]["level"] = "verbose"
    with pytest.raises(ValidationError, match="level"):
        run(payload)
    service.mark_batch_processing.assert_not_called()
    service.ingest_batch_to_pg.assert_not_called()


def test_an_invalid_message_goes_to_handle_invalid_batch(payload: dict[str, Any], service: MagicMock) -> None:
    """The raw message, unchanged, is what handle_invalid_batch reads the ids from."""
    payload["log_entries"][1]["level"] = "verbose"
    with pytest.raises(ValidationError):
        run(payload)
    service.handle_invalid_batch.assert_called_once_with(tasks.session_factory, payload)


def test_an_invalid_message_is_logged_without_its_content(
    payload: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """The worker's own log line about a rejected message does not repeat the message."""
    payload["log_entries"][1] |= {"level": "verbose", "message": "card 4111 1111"}
    with caplog.at_level(logging.INFO, logger=tasks.logger.name), pytest.raises(ValidationError):
        run(payload)
    assert "failed to validate the batch message" in caplog.text
    assert "card 4111 1111" not in caplog.text


# --- ingest_batch: retries ---


def test_retry_settings_match_the_decision() -> None:
    """docs/decisions.md: retry OperationalError and pool TimeoutError only, at most 10 times, backoff 1 s doubling up
    to 10 s with full jitter."""
    task = tasks.ingest_batch
    assert task.autoretry_for == tasks.TRANSIENT_ERRORS
    assert task.max_retries == tasks.MAX_RETRIES == 10
    assert task.retry_backoff is True
    assert task.retry_backoff_max == tasks.MAX_BACKOFF == 10
    assert task.retry_jitter is True


@pytest.mark.parametrize(
    "error",
    [
        exc.OperationalError("SELECT 1", {}, Exception("connection refused")),
        exc.TimeoutError("QueuePool limit reached"),
    ],
)
def test_transient_errors(error: Exception) -> None:
    """Postgres unreachable / connection dropped, and no free connection in the pool."""
    assert isinstance(error, tasks.TRANSIENT_ERRORS)


@pytest.mark.parametrize(
    "error",
    [
        exc.IntegrityError("INSERT", {}, Exception("foreign key violation")),
        exc.DataError("INSERT", {}, Exception("invalid input syntax for type uuid")),
        exc.ProgrammingError("INSERT", {}, Exception("relation does not exist")),
        exc.InterfaceError("INSERT", {}, Exception("the cursor is closed")),
        KeyError("batch_id"),
        RuntimeError("bug"),
    ],
)
def test_permanent_errors(error: Exception) -> None:
    """Retrying the same batch would fail the same way: rejected data, a wrong query, a misused driver, a bug."""
    assert not isinstance(error, tasks.TRANSIENT_ERRORS)


def test_a_transient_write_error_is_retried(payload: dict[str, Any], service: MagicMock) -> None:
    """The write fails once, the retry succeeds: the task ends successfully and nothing is marked FAILED."""
    service.ingest_batch_to_pg.side_effect = [operational_error(), None]
    result = apply(payload)
    assert result.successful()
    assert service.ingest_batch_to_pg.call_count == 2
    service.mark_batch_failed.assert_not_called()


def test_a_transient_error_marking_processing_is_retried(payload: dict[str, Any], service: MagicMock) -> None:
    """Postgres being down shows up first in mark_batch_processing; that step is retried too."""
    service.mark_batch_processing.side_effect = [operational_error(), True]
    result = apply(payload)
    assert result.successful()
    service.ingest_batch_to_pg.assert_called_once()


def test_each_attempt_marks_processing_with_its_retry_count(payload: dict[str, Any], service: MagicMock) -> None:
    """retry_count comes from the request: 0 on the first run, 1 and 2 on the retries."""
    service.ingest_batch_to_pg.side_effect = [operational_error(), operational_error(), None]
    apply(payload)
    batch = BatchMessage.model_validate(payload)
    assert service.mark_batch_processing.call_args_list == [
        call(tasks.session_factory, batch, 0),
        call(tasks.session_factory, batch, 1),
        call(tasks.session_factory, batch, 2),
    ]


def test_a_transient_error_is_not_marked_failed_while_retries_remain(
    payload: dict[str, Any], service: MagicMock
) -> None:
    """Waiting for a retry is still PROCESSING (decision 2B)."""
    service.ingest_batch_to_pg.side_effect = [operational_error(), None]
    apply(payload, retries=tasks.MAX_RETRIES - 1)
    service.mark_batch_failed.assert_not_called()


def test_a_transient_error_on_the_last_retry_marks_the_batch_failed(
    payload: dict[str, Any], service: MagicMock
) -> None:
    """With the retries used up the batch is given up on: FAILED with the error type, and the task fails with it."""
    service.ingest_batch_to_pg.side_effect = operational_error()
    result = apply(payload, retries=tasks.MAX_RETRIES)
    assert result.failed()
    assert isinstance(result.result, exc.OperationalError)
    service.mark_batch_failed.assert_called_once_with(
        tasks.session_factory, BatchMessage.model_validate(payload), "OperationalError"
    )


def test_a_batch_that_keeps_failing_runs_max_retries_plus_one_times(
    payload: dict[str, Any], service: MagicMock
) -> None:
    """The first run and 10 retries; then it stops."""
    service.ingest_batch_to_pg.side_effect = operational_error()
    result = apply(payload)
    assert result.failed()
    assert service.ingest_batch_to_pg.call_count == tasks.MAX_RETRIES + 1
    service.mark_batch_failed.assert_called_once()


def test_a_permanent_error_is_not_retried(payload: dict[str, Any], service: MagicMock) -> None:
    """One attempt, FAILED, and the task fails with the original error."""
    service.ingest_batch_to_pg.side_effect = integrity_error()
    result = apply(payload)
    assert result.failed()
    assert isinstance(result.result, exc.IntegrityError)
    service.ingest_batch_to_pg.assert_called_once()
    service.mark_batch_failed.assert_called_once()


def test_an_invalid_message_is_not_retried(payload: dict[str, Any], service: MagicMock) -> None:
    """Validation fails the same way every time."""
    payload["log_entries"][0]["level"] = "verbose"
    result = apply(payload)
    assert result.failed()
    assert isinstance(result.result, ValidationError)
    service.handle_invalid_batch.assert_called_once()


@pytest.mark.parametrize(("retries", "ceiling"), [(0, 1), (1, 2), (3, 8), (4, 10), (9, 10)])
def test_the_retry_delay_doubles_up_to_the_cap(
    payload: dict[str, Any], service: MagicMock, monkeypatch: pytest.MonkeyPatch, retries: int, ceiling: int
) -> None:
    """countdown is drawn from 0..min(10, 2**retries) (full jitter), and the retry carries the original error."""
    service.ingest_batch_to_pg.side_effect = operational_error()
    retry = MagicMock(side_effect=Retry())
    monkeypatch.setattr(tasks.ingest_batch, "retry", retry)
    apply(payload, retries=retries)
    assert 0 <= retry.call_args.kwargs["countdown"] <= ceiling
    assert isinstance(retry.call_args.kwargs["exc"], exc.OperationalError)
