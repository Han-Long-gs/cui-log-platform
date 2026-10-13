"""worker.tasks: the ingest_batch task is registered under the agreed name, takes what the API publishes,
validates it again, logs which batch it got and hands it to the write function. Nothing here needs a broker or a
database: worker.service.ingest_batch_to_pg is replaced by a mock (its own tests are in tests/integration/worker)."""

import importlib
import json
import logging
from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest
from celery import Celery
from cui_config import settings
from cui_messaging import INGEST_BATCH_ARG_NAME, INGEST_BATCH_TASK_NAME, BatchMessage, send_batch_msg
from pydantic import ValidationError
from worker import tasks
from worker.service import ingest_batch_to_pg

BROKER_URL = "amqp://u:p@test-broker:5672//"


@pytest.fixture
def payload(make_batch: Callable[..., BatchMessage]) -> dict[str, Any]:
    """A three-entry batch the way it arrives from the broker: plain JSON types."""
    return make_batch(entries=3).model_dump(mode="json")


@pytest.fixture(autouse=True)
def write_batch(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the function the task writes with, so no test in this file touches the database."""
    mock = create_autospec(ingest_batch_to_pg)
    monkeypatch.setattr(tasks, "ingest_batch_to_pg", mock)
    return mock


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


# --- ingest_batch ---


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
        tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})
    assert f"batch_id: {payload['batch_id']}" in caplog.text
    assert f"tenant_id: {payload['tenant_id']}" in caplog.text
    assert "num_entries: 3" in caplog.text


def test_task_writes_the_validated_batch(payload: dict[str, Any], write_batch: MagicMock) -> None:
    """The batch is written once, as a BatchMessage, through the worker's session factory."""
    tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})
    write_batch.assert_called_once_with(tasks.session_factory, BatchMessage.model_validate(payload))


def test_task_fails_when_the_write_fails(payload: dict[str, Any], write_batch: MagicMock) -> None:
    """An error from the write is not swallowed: the task fails with it."""
    write_batch.side_effect = RuntimeError("database is down")
    with pytest.raises(RuntimeError, match="database is down"):
        tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})


def test_task_rejects_a_message_without_a_tenant(payload: dict[str, Any], write_batch: MagicMock) -> None:
    """The worker validates the message again instead of trusting whatever is in the queue."""
    del payload["tenant_id"]
    with pytest.raises(ValidationError, match="tenant_id"):
        tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})
    write_batch.assert_not_called()


def test_task_rejects_a_message_with_an_invalid_entry(payload: dict[str, Any], write_batch: MagicMock) -> None:
    """Every log entry is validated again too: one bad entry fails the whole batch."""
    payload["log_entries"][1]["level"] = "verbose"
    with pytest.raises(ValidationError, match="level"):
        tasks.ingest_batch(**{INGEST_BATCH_ARG_NAME: payload})
    write_batch.assert_not_called()
