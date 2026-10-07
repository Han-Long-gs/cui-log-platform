import uuid
from datetime import UTC, datetime

from cui_messaging import BatchMessage, make_celery_app, send_batch_msg
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from config import settings

from .constants import DEV_TENANT_ID
from .schemas import IngestRequest, IngestResponse

queue_router = APIRouter()
celery_app = make_celery_app(broker_url=settings.broker_url)


@queue_router.post("/v1/logs", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest_logs(body: IngestRequest) -> IngestResponse:
    """send a batch message of 1-200 log events (body at most 1 MiB); respond 202 after queued.
    Too large a body -> 413; an invalid event or batch size -> 422; nothing is queued in either case.
    A database error (e.g. duplicate event_id) rolls back the whole batch and returns 500."""
    message = BatchMessage(
        batch_id=uuid.uuid4(), tenant_id=DEV_TENANT_ID, received_at=datetime.now(UTC), log_entries=body.log_entries
    )
    send_batch_msg(app=celery_app, msg=message)
    return


@queue_router.get("/readyz", status_code=status.HTTP_200_OK)
def readiness_check() -> JSONResponse:
    pass
