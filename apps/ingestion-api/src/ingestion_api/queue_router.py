"""Routes for queue mode: publish each batch to the broker and never touch the database."""

import uuid
from datetime import UTC, datetime

from cui_config import settings
from cui_messaging import BatchMessage, BrokerDownError, check_broker_connection, make_celery_app, send_batch_msg
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from .constants import DEV_TENANT_ID
from .schemas import IngestRequest, IngestResponse

queue_router = APIRouter()
celery_app = make_celery_app(broker_url=settings.broker_url)


@queue_router.post("/v1/logs", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
def ingest_logs(body: IngestRequest) -> IngestResponse:
    """Publish a batch of 1-200 log events (body at most 1 MiB) and respond 202 once the broker confirms it.
    Too large a body -> 413; an invalid event or batch size -> 422; nothing is published in either case.
    Broker unreachable or no confirm in time -> 503; never touches the database."""
    batch_id = uuid.uuid4()
    message = BatchMessage(
        batch_id=batch_id, tenant_id=DEV_TENANT_ID, received_at=datetime.now(UTC), log_entries=body.log_entries
    )
    try:
        send_batch_msg(app=celery_app, msg=message)
        return IngestResponse(batch_id=batch_id, message="log entries batch accepted")
    except BrokerDownError as ex:
        print(repr(ex.__cause__))
        return JSONResponse("Messaging broker is unreachable", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


@queue_router.get("/readyz", status_code=status.HTTP_200_OK)
def readiness_check() -> JSONResponse:
    """Open one AMQP connection to the broker without retrying: 200 if it succeeds, 503 if it fails.
    The cause is printed server-side; the response body never contains its details."""
    try:
        check_broker_connection(celery_app)
        return JSONResponse("ingestion-api service to broker is healthy")
    except BrokerDownError as ex:
        print(repr(ex.__cause__))
        return JSONResponse("Messaging broker is unreachable", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
