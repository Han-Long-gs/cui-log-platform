"""
Endpoints
ref: https://fastapi.tiangolo.com/tutorial/dependencies/#share-annotated-dependencies
    https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/
    https://fastapi.tiangolo.com/tutorial/handling-errors/
    https://starlette.dev/middleware/#requestbodylimitmiddleware
    https://fastapi.tiangolo.com/advanced/middleware/
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from .constants import MAX_BODY_BYTES
from .db import dispose_engine, ensure_dev_tenant, get_session
from .schemas import IngestRequest, IngestResponse
from .service import insert_log_events


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Ensure the dev tenant exists on startup and dispose the engine (closing pooled DB connections) on shutdown."""
    ensure_dev_tenant()
    yield
    dispose_engine()


app = FastAPI(lifespan=lifespan)

# register the starlette middleware to the FastAPI to restrict the body size to 1MB before FastAPI reads it to its mem
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_BODY_BYTES)

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


@app.post("/v1/logs", response_model=IngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_logs(body: IngestRequest, session: SessionDep) -> IngestResponse:
    """Insert a batch of 1-200 log events (body at most 1 MiB) in one transaction; respond 201 after the commit.
    Too large a body -> 413; an invalid event or batch size -> 422; nothing is written in either case.
    A database error (e.g. duplicate event_id) rolls back the whole batch and returns 500."""
    insert_log_events(body.log_entries, session)

    return IngestResponse(message="succeed")
