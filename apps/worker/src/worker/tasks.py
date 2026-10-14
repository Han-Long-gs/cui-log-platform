"""Celery app and tasks of the worker.
ref:https://docs.celeryq.dev/en/stable/userguide/tasks.html
    https://docs.celeryq.dev/en/stable/userguide/workers.html
    https://docs.celeryq.dev/en/stable/userguide/optimizing.html#optimizing-prefetch-limit
"""

from celery.utils.log import get_task_logger
from cui_config import settings
from cui_messaging import INGEST_BATCH_TASK_NAME, BatchMessage, make_celery_app
from pydantic import ValidationError

from .db import session_factory
from .service import ingest_batch_to_pg, mark_batch_failed, mark_batch_processing, record_invalid_batch

celery_app = make_celery_app(broker_url=settings.broker_url)
logger = get_task_logger(__name__)


@celery_app.task(name=INGEST_BATCH_TASK_NAME)
def ingest_batch(batch_message: dict) -> None:
    """Validate one batch message, mark it PROCESSING, write it, or mark it FAILED; skip a batch already persisted.
    Invalid message: records a FAILED row if its ids are present, then raises ValidationError; a failed write raises.
    The parameter name must equal cui_messaging.INGEST_BATCH_ARG_NAME: the publisher sends the batch under it."""
    try:
        batch = BatchMessage.model_validate(batch_message)
    except ValidationError:
        logger.error("failed to validate the batch message")

        batch_id = batch_message.get("batch_id")
        tenant_id = batch_message.get("tenant_id")
        received_at = batch_message.get("received_at")
        log_entries = batch_message.get("log_entries")
        required_fields_present = batch_id and tenant_id and received_at

        if required_fields_present:
            event_count = 0
            if isinstance(log_entries, list):
                event_count = len(log_entries)
            try:
                record_invalid_batch(session_factory, batch_id, tenant_id, received_at, event_count, "ValidationError")
            except Exception:
                logger.error(f"failed to record invalid ingest batch {batch_id}")
        raise

    logger.info(
        f"\n---WORKING ON TASK---\n"
        f"batch_id: {batch.batch_id}, tenant_id: {batch.tenant_id}, num_entries: {len(batch.log_entries)}"
    )
    should_persist = mark_batch_processing(session_factory, batch)
    if not should_persist:
        logger.info(f"ingest batch {batch.batch_id} is already persisted. skipping...")
        return

    try:
        ingest_batch_to_pg(session_factory, batch)
    except Exception as ex:
        ex_class_name = type(ex).__name__
        logger.error(f"failed to ingest batch {batch.batch_id}, %s", ex_class_name)
        try:
            mark_batch_failed(session_factory, batch, ex_class_name)
        except Exception:
            logger.error(f"failed to mark ingest batch {batch.batch_id} as FAILED")
        raise
