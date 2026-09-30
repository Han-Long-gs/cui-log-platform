"""
Endpoints
ref: https://fastapi.tiangolo.com/tutorial/dependencies/#share-annotated-dependencies
    https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/
    https://fastapi.tiangolo.com/tutorial/handling-errors/
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .db import dispose_engine, ensure_dev_tenant, get_session
from .schemas import LogRequest, LogResponse
from .service import insert_log_events


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Ensure the dev tenant exists on startup and dispose the engine (closing pooled DB connections) on shutdown."""
    ensure_dev_tenant()
    yield
    dispose_engine()


app = FastAPI(lifespan=lifespan)

# function scope:start the dependency before the path operation function that handles the request,
# end the dependency after the path operation function ends,
# but before the response is sent back to the client.
SessionDep = Annotated[Session, Depends(get_session, scope="function")]


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, ex: RequestValidationError) -> JSONResponse:
    """Return 422 with one {"loc", "msg"} entry per request validation error."""
    message = {"error": "Validation Error", "detail": []}
    errors = ex.errors()
    for error in errors:
        message["detail"].append({"loc": error["loc"], "msg": error["msg"]})
    return JSONResponse(message, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)


@app.post("/v1/logs", response_model=LogResponse, status_code=status.HTTP_201_CREATED)
def ingest_logs(logs: list[LogRequest], session: SessionDep) -> LogResponse:
    """Insert a batch of log events in a single transaction and respond after it is committed.
    An invalid event rejects the whole request with 422 before anything is written;
    a database error (e.g. duplicate event_id) rolls back the whole batch and returns 500."""
    insert_log_events(logs, session)

    return LogResponse(message="succeed")
