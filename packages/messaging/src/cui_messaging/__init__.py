"""Shared messaging contract: Celery app factory, publish / health-check helpers, message schema and errors."""

from .celery_app import (
    check_broker_connection,
    make_celery_app,
    send_batch_msg,
    INGEST_BATCH_ARG_NAME,
    INGEST_BATCH_TASK_NAME,
)
from .exceptions import BrokerDownError
from .schemas import BatchMessage

__all__ = [
    "make_celery_app",
    "send_batch_msg",
    "check_broker_connection",
    "BatchMessage",
    "BrokerDownError",
    "INGEST_BATCH_ARG_NAME",
    "INGEST_BATCH_TASK_NAME",
]
