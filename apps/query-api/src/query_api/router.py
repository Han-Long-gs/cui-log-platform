"""
ref:https://fastapi.tiangolo.com/tutorial/query-param-models/
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from cui_db import DEV_TENANT_ID, ensure_dev_tenant
from fastapi import APIRouter, Depends, FastAPI, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import exc
from sqlalchemy.orm import Session

from .db import dispose_engine, get_session, session_factory
from .schemas import QueryParams, QueryResponse, StatsParams, StatsResponse
from .service import check_db_connection, count_log_events_by_level, fetch_log_events


@asynccontextmanager
async def router_lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Ensure the dev tenant exists on startup and dispose the engine (closing pooled DB connections) on shutdown."""
    ensure_dev_tenant(session_factory=session_factory)
    yield
    dispose_engine()


router = APIRouter(lifespan=router_lifespan)

# function scope:start the dependency before the path operation function that handles the request,
# end the dependency after the path operation function ends,
# but before the response is sent back to the client.
SessionDep = Annotated[Session, Depends(get_session, scope="function")]


@router.get("/healthz", status_code=status.HTTP_200_OK)
async def liveness_check() -> JSONResponse:
    """Return 200 while the process serves HTTP; does not touch the database."""
    return JSONResponse("query-api service is up and run!")


@router.get("/readyz", status_code=status.HTTP_200_OK)
def readiness_check(session: SessionDep) -> JSONResponse:
    """Run SELECT 1 against Postgres: 200 if it succeeds, 503 if the database is unreachable or the pool times out.
    The error is printed server-side; the response body never contains its details."""
    try:
        check_db_connection(session)
        return JSONResponse("query-api service to db is healthy")
    except (exc.OperationalError, exc.TimeoutError) as ex:
        print(repr(ex))
        return JSONResponse("Database is unavailable", status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


@router.get("/api/v1/logs", status_code=status.HTTP_200_OK)
def fetch_logs(params: Annotated[QueryParams, Query()], session: SessionDep) -> QueryResponse:
    """Return one page of the dev tenant's logs matching the query parameters, newest first.
    Unknown query parameters are ignored; invalid ones are rejected with 422."""
    log_page = fetch_log_events(
        session=session,
        tenant_id=DEV_TENANT_ID,
        service_name=params.service_name,
        environment=params.environment,
        level=params.level,
        start=params.start,
        end=params.end,
        cursor=params.cursor,
        limit=params.limit,
    )
    return QueryResponse(next_cursor=log_page.next_cursor, items=log_page.items)


@router.get("/api/v1/stats", status_code=status.HTTP_200_OK)
def fetch_stats(params: Annotated[StatsParams, Query()], session: SessionDep) -> StatsResponse:
    """Return the dev tenant's event counts per level for the given filters.
    Unknown query parameters are ignored; invalid ones are rejected with 422."""
    level_counts = count_log_events_by_level(
        session=session,
        tenant_id=DEV_TENANT_ID,
        service_name=params.service_name,
        environment=params.environment,
        start=params.start,
        end=params.end,
    )
    return StatsResponse(counts=level_counts)
