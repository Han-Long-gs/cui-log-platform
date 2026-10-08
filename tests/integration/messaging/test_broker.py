"""check_broker_connection against the real broker, and against a port where nothing is listening."""

import time

import pytest
from cui_messaging import BrokerDownError, check_broker_connection, make_celery_app
from kombu import exceptions as kombu_exc

from config import settings

UNREACHABLE_URL = "amqp://guest:guest@127.0.0.1:1//"


def test_check_succeeds_with_real_broker() -> None:
    """With the test broker up, the AMQP handshake completes and nothing is raised."""
    check_broker_connection(make_celery_app(settings.broker_url))


def test_check_fails_fast_when_nothing_listens() -> None:
    """A refused connection raises BrokerDownError (cause: kombu's OperationalError) well within the probe's budget."""
    started = time.monotonic()
    with pytest.raises(BrokerDownError) as caught:
        check_broker_connection(make_celery_app(UNREACHABLE_URL))
    assert time.monotonic() - started < 3
    assert isinstance(caught.value.__cause__, kombu_exc.OperationalError)
