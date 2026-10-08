"""Celery App
ref:https://docs.celeryq.dev/en/stable/getting-started/first-steps-with-celery.html
    https://docs.celeryq.dev/en/stable/getting-started/next-steps.html#using-celery-in-your-application
    https://github.com/celery/celery/issues/5410
    https://docs.celeryq.dev/en/stable/userguide/configuration.html
    celery->app->defaults.py->NAMESPACES for args
    https://docs.celeryq.dev/en/stable/userguide/calling.html#message-sending-retry
"""

from celery import Celery
from kombu import exceptions as exc

from .exceptions import BrokerDownError
from .schemas import BatchMessage

INGEST_BATCH_TASK_NAME = "ingest_batch"
INGEST_BATCH_ARG_NAME = "batch_message"


def make_celery_app(broker_url: str) -> Celery:
    """Build a Celery app with the serializer, timeout, publisher-confirm and retry settings.
    Producer and consumer both build their app here so the two sides agree."""
    app = Celery("cui", broker=broker_url)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        broker_connection_timeout=2,
        broker_transport_options={
            "confirm_publish": True  # wait for ack from broker
        },
        task_publish_retry_policy={"max_retries": 1, "interval_start": 0, "interval_step": 0.2, "interval_max": 1},
    )
    return app


def send_batch_msg(app: Celery, msg: BatchMessage) -> None:
    """Publish msg as an ingest_batch task and wait for the broker's confirm.
    Raises BrokerDownError if the broker cannot be reached or does not confirm in time."""
    try:
        app.send_task(
            INGEST_BATCH_TASK_NAME, kwargs={INGEST_BATCH_ARG_NAME: msg.model_dump(mode="json")}, confirm_timeout=3
        )
    except exc.OperationalError as ex:
        raise BrokerDownError("Broker is down") from ex


def check_broker_connection(app: Celery) -> None:
    """Open and release one connection to the broker without retrying; raises BrokerDownError if it fails."""
    try:
        with app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=0)
    except exc.OperationalError as ex:
        raise BrokerDownError("Broker is down") from ex
