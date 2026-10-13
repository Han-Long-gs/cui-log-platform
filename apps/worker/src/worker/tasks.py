"""Tasks Registry
ref:https://docs.celeryq.dev/en/stable/userguide/tasks.html
    https://docs.celeryq.dev/en/stable/userguide/workers.html
    https://docs.celeryq.dev/en/stable/userguide/optimizing.html#optimizing-prefetch-limit
"""

from celery.utils.log import get_task_logger
from cui_config import settings
from cui_messaging import INGEST_BATCH_TASK_NAME, BatchMessage, make_celery_app

from .db import session_factory
from .service import ingest_batch_to_pg

celery_app = make_celery_app(broker_url=settings.broker_url)
logger = get_task_logger(__name__)


@celery_app.task(name=INGEST_BATCH_TASK_NAME)
def ingest_batch(batch_message: dict) -> None:
    """Validate one ingest batch message from the broker and log its batch id, tenant id and entry count.
    Raises pydantic.ValidationError if the message does not match BatchMessage.
    The parameter name must equal cui_messaging.INGEST_BATCH_ARG_NAME: the publisher sends the batch under it."""
    batch = BatchMessage.model_validate(batch_message)
    logger.info(
        f"\n---WORKING ON TASK---\n"
        f"batch_id: {batch.batch_id}, tenant_id: {batch.tenant_id}, num_entries: {len(batch.log_entries)}"
    )
    ingest_batch_to_pg(session_factory, batch)
