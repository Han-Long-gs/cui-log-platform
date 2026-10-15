"""Celery app and tasks of the worker.
ref:https://docs.celeryq.dev/en/stable/userguide/tasks.html
    https://docs.celeryq.dev/en/stable/userguide/workers.html
    https://docs.celeryq.dev/en/stable/userguide/optimizing.html#optimizing-prefetch-limit
    https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
    https://stackoverflow.com/questions/54899320/what-is-the-meaning-of-bind-true-keyword-in-celery
"""

from celery.utils.log import get_task_logger
from cui_config import settings
from cui_messaging import INGEST_BATCH_TASK_NAME, BatchMessage, make_celery_app
from pydantic import ValidationError
from sqlalchemy import exc

from .db import session_factory
from .service import handle_invalid_batch, ingest_batch_to_pg, mark_batch_failed, mark_batch_processing

TRANSIENT_ERRORS = (exc.TimeoutError, exc.OperationalError)
MAX_RETRIES = 10
MAX_BACKOFF = 10

celery_app = make_celery_app(broker_url=settings.broker_url)
logger = get_task_logger(__name__)


@celery_app.task(
    name=INGEST_BATCH_TASK_NAME,
    bind=True,
    autoretry_for=TRANSIENT_ERRORS,
    max_retries=MAX_RETRIES,
    retry_backoff=True,
    retry_backoff_max=MAX_BACKOFF,
    retry_jitter=True,
)
def ingest_batch(self, batch_message: dict) -> None:
    """Validate one batch message, mark it PROCESSING (with the current retry count); skip a batch already persisted.
    Invalid message: records a FAILED row if its ids are present, then raises ValidationError (no retry).
    Transient error (TRANSIENT_ERRORS): re-raised, and Celery's autoretry republishes the task with exponential backoff.
    Permanent error, or a transient one on the last allowed retry: marks the batch FAILED, then re-raises.
    The parameter name must equal cui_messaging.INGEST_BATCH_ARG_NAME: the publisher sends the batch under it."""
    try:
        batch = BatchMessage.model_validate(batch_message)
        logger.info(
            f"\n---WORKING ON TASK---\n"
            f"batch_id: {batch.batch_id}, tenant_id: {batch.tenant_id}, num_entries: {len(batch.log_entries)}"
        )
        should_persist = mark_batch_processing(session_factory, batch, self.request.retries)
        if not should_persist:
            logger.info(f"ingest batch {batch.batch_id} is already persisted. skipping...")
            return
        ingest_batch_to_pg(session_factory, batch)
    except ValidationError:
        logger.error("failed to validate the batch message")
        handle_invalid_batch(session_factory, batch_message)
        raise
    except Exception as ex:
        if not isinstance(ex, TRANSIENT_ERRORS) or self.request.retries >= MAX_RETRIES:
            ex_class_name = type(ex).__name__
            logger.error(f"failed to ingest batch {batch.batch_id}, %s", ex_class_name)
            mark_batch_failed(session_factory, batch, ex_class_name)
        raise
