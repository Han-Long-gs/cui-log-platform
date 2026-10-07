from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import exc
from sqlalchemy.orm import Session

from .db import get_session
from .schemas import IngestRequest, IngestResponse
from .service import check_db_connection, insert_log_events

sync_router = APIRouter()

# function scope:start the dependency before the path operation function that handles the request,
# end the dependency after the path operation function ends,
# but before the response is sent back to the client.
SessionDep = Annotated[Session, Depends(get_session, scope="function")]


@sync_router.post("/v1/logs", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_logs(body: IngestRequest, session: SessionDep) -> IngestResponse:
    """Insert a batch of 1-200 log events (body at most 1 MiB) in one transaction; respond 201 after the commit.
    Too large a body -> 413; an invalid event or batch size -> 422; nothing is written in either case.
    A database error (e.g. duplicate event_id) rolls back the whole batch and returns 500."""
    insert_log_events(body.log_entries, session)

    return IngestResponse(message="succeed")


@sync_router.get("/readyz", status_code=status.HTTP_200_OK)
def readiness_check(session: SessionDep) -> JSONResponse:
    """Run SELECT 1 against Postgres: 200 if it succeeds, 503 if the database is unreachable or the pool times out.
    The error is printed server-side; the response body never contains its details."""
    try:
        check_db_connection(session)
        return JSONResponse("ingestion-api service to db is healthy")
    except (exc.OperationalError, exc.TimeoutError) as ex:
        print(repr(ex))
        return JSONResponse("Database is unavailable", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
