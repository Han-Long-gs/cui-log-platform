"""Celery App
ref:https://docs.celeryq.dev/en/stable/getting-started/first-steps-with-celery.html
    https://docs.celeryq.dev/en/stable/getting-started/next-steps.html#using-celery-in-your-application
"""

from celery import Celery

from .schemas import BatchMessage

INGEST_BATCH_TASK_NAME = "ingest_batch"
INGEST_BATCH_ARG_NAME = "batch_message"


def make_celery_app(broker_url: str) -> Celery:
    """Build a Celery app with the settings both producer and consumer agree on"""
    app = Celery("cui", broker=broker_url)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
    )
    return app


def send_batch_msg(app: Celery, msg: BatchMessage):
    """send batch message to the broker"""
    app.send_task(INGEST_BATCH_TASK_NAME, kwargs={INGEST_BATCH_ARG_NAME: msg.model_dump(mode="json")})
