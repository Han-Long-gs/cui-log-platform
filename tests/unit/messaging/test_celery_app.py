"""cui_messaging.celery_app with Celery mocked: the shared config, what gets published, and error translation."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest
from celery import Celery
from cui_messaging import BatchMessage, BrokerDownError, check_broker_connection, make_celery_app, send_batch_msg
from cui_messaging.celery_app import INGEST_BATCH_ARG_NAME, INGEST_BATCH_TASK_NAME
from cui_schemas.schemas import LogEntry
from kombu import exceptions as kombu_exc

BROKER_URL = "amqp://u:p@broker:5672//"


@pytest.fixture
def message(make_event: Callable[..., dict[str, Any]]) -> BatchMessage:
    """A valid one-entry batch."""
    return BatchMessage(
        batch_id="11111111-1111-1111-1111-111111111111",
        tenant_id="22222222-2222-2222-2222-222222222222",
        received_at=datetime(2026, 10, 8, 4, 26, tzinfo=UTC),
        log_entries=[LogEntry.model_validate(make_event())],
    )


@pytest.fixture
def celery_app() -> MagicMock:
    """A mock Celery app with the real method signatures."""
    return create_autospec(Celery, instance=True)


# --- make_celery_app ---


def test_app_uses_the_given_broker() -> None:
    """The broker URL comes from the caller, not from the package."""
    assert make_celery_app(BROKER_URL).conf.broker_url == BROKER_URL


def test_app_serializes_json_only() -> None:
    """Tasks are sent as JSON, and only JSON is accepted."""
    conf = make_celery_app(BROKER_URL).conf
    assert conf.task_serializer == "json"
    assert conf.accept_content == ["json"]


def test_app_waits_for_publisher_confirms() -> None:
    """Publisher confirms are on, so a publish returns only after the broker has the message."""
    assert make_celery_app(BROKER_URL).conf.broker_transport_options["confirm_publish"] is True


def test_app_bounds_connection_time() -> None:
    """A single connection attempt gives up after 2 seconds."""
    assert make_celery_app(BROKER_URL).conf.broker_connection_timeout == 2


def test_app_retries_a_failed_publish_once() -> None:
    """A failed publish is retried exactly once, so a dead broker turns into an error within seconds."""
    assert make_celery_app(BROKER_URL).conf.task_publish_retry_policy["max_retries"] == 1


def test_app_has_no_misspelled_settings() -> None:
    """Every setting changed from the defaults is a real Celery setting (conf.update accepts any key silently)."""
    changed = make_celery_app(BROKER_URL).conf.changes
    unknown = [key for key in changed if key not in Celery().conf.keys()]
    assert unknown == []


# --- send_batch_msg ---


def test_send_publishes_ingest_batch_task(celery_app: MagicMock, message: BatchMessage) -> None:
    """One task is sent, by name, so the API never imports worker code."""
    send_batch_msg(celery_app, message)
    celery_app.send_task.assert_called_once()
    assert celery_app.send_task.call_args.args == (INGEST_BATCH_TASK_NAME,)


def test_send_puts_the_message_under_the_agreed_argument_name(celery_app: MagicMock, message: BatchMessage) -> None:
    """The worker receives the batch as its single keyword argument, already in JSON types."""
    send_batch_msg(celery_app, message)
    task_kwargs = celery_app.send_task.call_args.kwargs["kwargs"]
    assert task_kwargs == {INGEST_BATCH_ARG_NAME: message.model_dump(mode="json")}
    assert json.loads(json.dumps(task_kwargs)) == task_kwargs


def test_send_bounds_the_wait_for_the_confirm(celery_app: MagicMock, message: BatchMessage) -> None:
    """confirm_timeout is a publish option, not part of the task arguments."""
    send_batch_msg(celery_app, message)
    call = celery_app.send_task.call_args
    assert call.kwargs["confirm_timeout"] == 3
    assert "confirm_timeout" not in call.kwargs["kwargs"]


def test_send_translates_broker_errors(celery_app: MagicMock, message: BatchMessage) -> None:
    """kombu's OperationalError becomes BrokerDownError, keeping the original as the cause."""
    original = kombu_exc.OperationalError("[Errno 61] Connection refused")
    celery_app.send_task.side_effect = original
    with pytest.raises(BrokerDownError) as caught:
        send_batch_msg(celery_app, message)
    assert caught.value.__cause__ is original


def test_send_lets_other_errors_propagate(celery_app: MagicMock, message: BatchMessage) -> None:
    """Only "broker unavailable" errors are translated."""
    celery_app.send_task.side_effect = kombu_exc.EncodeError("cannot serialize")
    with pytest.raises(kombu_exc.EncodeError):
        send_batch_msg(celery_app, message)


# --- check_broker_connection ---


def connection(celery_app: MagicMock) -> MagicMock:
    """The connection object a `with app.connection_for_write() as conn` block receives."""
    return celery_app.connection_for_write.return_value.__enter__.return_value


def test_check_connects_once_without_retrying(celery_app: MagicMock) -> None:
    """The probe tries exactly once; retrying is the caller's job (it polls)."""
    check_broker_connection(celery_app)
    connection(celery_app).ensure_connection.assert_called_once_with(max_retries=0)


def test_check_releases_the_connection(celery_app: MagicMock) -> None:
    """The connection is released after the check."""
    check_broker_connection(celery_app)
    celery_app.connection_for_write.return_value.__exit__.assert_called_once()


def test_check_translates_broker_errors_and_still_releases(celery_app: MagicMock) -> None:
    """A failed connect becomes BrokerDownError (cause kept), and the connection is still released."""
    original = kombu_exc.OperationalError("timed out")
    connection(celery_app).ensure_connection.side_effect = original
    with pytest.raises(BrokerDownError) as caught:
        check_broker_connection(celery_app)
    assert caught.value.__cause__ is original
    celery_app.connection_for_write.return_value.__exit__.assert_called_once()
