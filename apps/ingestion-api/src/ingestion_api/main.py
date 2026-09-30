"""
Endpoints
ref: https://fastapi.tiangolo.com/tutorial/dependencies/#share-annotated-dependencies
    https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/
    https://fastapi.tiangolo.com/tutorial/handling-errors/
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, status
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


@app.post("/v1/logs", response_model=LogResponse, status_code=status.HTTP_201_CREATED)
def ingest_logs(logs: list[LogRequest], session: SessionDep) -> LogResponse:
    """Insert a batch of log events in a single transaction and respond after it is committed.
    Returns 201 when every event is committed; if any event fails the whole batch is rolled back and 500 is returned."""
    insert_log_events(logs, session)

    return LogResponse(message="succeed")
