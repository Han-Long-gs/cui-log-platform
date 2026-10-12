"""Tasks Registry
ref:https://docs.celeryq.dev/en/stable/userguide/tasks.html
    https://docs.celeryq.dev/en/stable/userguide/workers.html
    https://docs.celeryq.dev/en/stable/userguide/optimizing.html#optimizing-prefetch-limit
"""

from cui_messaging import make_celery_app, INGEST_BATCH_TASK_NAME, BatchMessage
from cui_config import settings
from celery.utils.log import get_task_logger

celery_app = make_celery_app(broker_url=settings.broker_url)
logger = get_task_logger(__name__)


@celery_app.task(name=INGEST_BATCH_TASK_NAME)
def ingest_batch(batch_message: dict) -> None:
    """note: the arg's name has to match the INGEST_BATCH_ARG_NAME defined in the cui_messaging"""
    batch = BatchMessage.model_validate(batch_message)
    logger.info(
        f"WORKING ON TASK - batch_id: {batch.batch_id}, tenant_id: {batch.tenant_id}, num_entries: {len(batch.log_entries)}"
    )
