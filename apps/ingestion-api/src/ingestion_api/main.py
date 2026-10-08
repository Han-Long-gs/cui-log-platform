"""
Endpoints
ref: https://fastapi.tiangolo.com/tutorial/dependencies/#share-annotated-dependencies
    https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/
    https://fastapi.tiangolo.com/tutorial/handling-errors/
    https://starlette.dev/middleware/#requestbodylimitmiddleware
    https://fastapi.tiangolo.com/advanced/middleware/
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from config import settings

from .constants import MAX_BODY_BYTES
from .queue_router import queue_router
from .sync_router import sync_router

app = FastAPI()

# register the starlette middleware to the FastAPI to restrict the body size to 1MB before FastAPI reads it to its mem
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_BODY_BYTES)


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, ex: RequestValidationError) -> JSONResponse:
    """Return 422 with one {"loc", "msg"} entry per request validation error."""
    message = {"error": "Validation Error", "detail": []}
    errors = ex.errors()
    for error in errors:
        message["detail"].append({"loc": error["loc"], "msg": error["msg"]})
    return JSONResponse(message, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)


@app.get("/healthz", status_code=status.HTTP_200_OK)
async def liveness_check() -> JSONResponse:
    """Return 200 to show the process is up and serving HTTP; does not touch the database."""
    return JSONResponse("ingestion-api service is up and run!")


if settings.ingest_mode == "queue":
    app.include_router(queue_router)
elif settings.ingest_mode == "sync":
    app.include_router(sync_router)
